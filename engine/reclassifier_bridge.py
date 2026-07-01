"""
reclassifier_bridge.py — Chama a etapa de reclassificação manual via API do PPR.

Substitui o processo manual de "Run 1 -> baixa base_reclassificador -> roda
reclassificador_predicao por fora -> sobe base_reclassificada -> Run 2": esta ponte
traduz a base_reclassificador (schema RECLASSIFIER_OUTPUT_SCHEMA) para o formato
esperado pela ferramenta reclassificador_predicao já publicada no PPR, dispara a
execução via API pública (docs/api_implementation_plan.md do repo
PlataformaPrototipagem), espera o resultado por polling e devolve o
base_reclassificada já parseado em DataFrame.

Credenciais AWS (aws_access_key_id / aws_secret_access_key) são lidas do ambiente do
processo — mesmas variáveis que o dispatcher generic_tool do PPR já injeta em toda
execução tipo_execucao=ecs (mesmo padrão usado pelo ecs_handler.py legado de
reclassificador_predicao/reclassificador_metricas para ler/escrever no S3).
"""
import io
import logging
import time
import uuid

import boto3
import pandas as pd
import requests

import config as cfg

logger = logging.getLogger(__name__)

# Tool 122 (BBV001) -> Base esperada pelo reclassificador_predicao (Param_Reclassificador)
RENAME_TO_PREDICAO = {
    "Codigo Interno": "Chave única",
    "Conta Contabil": "Cód Conta Contábil",
    "Centro de Custo": "Código Centro de Custo",
}


class ReclassifierBridgeError(RuntimeError):
    """Erro na chamada da etapa de reclassificação via API do PPR."""


def build_predicao_base(df_for_reclass: pd.DataFrame) -> io.BytesIO:
    """Traduz o schema do Tool 122 (base_reclassificador) para a 'Base' que o
    reclassificador_predicao espera, aplicando apenas as 3 renomeações necessárias.
    Colunas extras (Conta destino, Como tratar?, FINALIZACAO, GRUPO) são mantidas —
    o reclassificador_predicao só lê as colunas que precisa, o resto é ignorado.
    """
    df = df_for_reclass.rename(columns=RENAME_TO_PREDICAO)
    buf = io.BytesIO()
    df.to_excel(buf, sheet_name="Base", index=False)
    buf.seek(0)
    return buf


def _s3_client():
    return boto3.client(
        "s3",
        aws_access_key_id=cfg.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=cfg.AWS_SECRET_ACCESS_KEY,
    )


def _read_bytes(file_obj) -> bytes:
    if hasattr(file_obj, "read"):
        file_obj.seek(0)
        return file_obj.read()
    if isinstance(file_obj, (bytes, bytearray)):
        return bytes(file_obj)
    raise ReclassifierBridgeError(
        f"Tipo de arquivo não suportado para upload: {type(file_obj)!r}"
    )


def _upload_bytes(data: bytes, key: str) -> str:
    _s3_client().put_object(Bucket=cfg.BBV001_S3_BUCKET, Key=key, Body=data)
    logger.info(f"[bridge] Upload S3: s3://{cfg.BBV001_S3_BUCKET}/{key} ({len(data):,} bytes)")
    return key


def _post_exec(files: dict) -> str:
    url = f"{cfg.PPR_API_BASE_URL.rstrip('/')}/ferramentas/api/exec/"
    payload = {
        "auth_token": cfg.PPR_API_TOKEN,
        "ferramenta": cfg.RECLASSIFICADOR_PREDICAO_TOOLNAME,
        "inputs": {},
        "files": files,
    }
    resp = requests.post(url, json=payload, timeout=30)
    if resp.status_code != 202:
        raise ReclassifierBridgeError(
            f"Falha ao disparar {cfg.RECLASSIFICADOR_PREDICAO_TOOLNAME} via API do PPR "
            f"(HTTP {resp.status_code}): {resp.text[:500]}"
        )
    body = resp.json()
    execution_id = body.get("execution_id")
    if not execution_id:
        raise ReclassifierBridgeError(f"Resposta do PPR sem execution_id: {body}")
    logger.info(f"[bridge] Execução disparada: {execution_id}")
    return execution_id


def _poll_status(execution_id: str) -> dict:
    url = f"{cfg.PPR_API_BASE_URL.rstrip('/')}/ferramentas/api/exec/{execution_id}/"
    deadline = time.monotonic() + cfg.RECLASSIFIER_BRIDGE_TIMEOUT_S
    while True:
        resp = requests.get(url, params={"auth_token": cfg.PPR_API_TOKEN}, timeout=30)
        if resp.status_code != 200:
            raise ReclassifierBridgeError(
                f"Falha ao consultar status da execução {execution_id} "
                f"(HTTP {resp.status_code}): {resp.text[:500]}"
            )
        body = resp.json()
        status = body.get("status")
        if status == "success":
            logger.info(f"[bridge] Execução {execution_id} concluída com sucesso.")
            return body
        if status == "error":
            raise ReclassifierBridgeError(
                f"Execução {execution_id} de {cfg.RECLASSIFICADOR_PREDICAO_TOOLNAME} "
                f"falhou: {body.get('status_text')}"
            )
        if time.monotonic() > deadline:
            raise ReclassifierBridgeError(
                f"Timeout ({cfg.RECLASSIFIER_BRIDGE_TIMEOUT_S}s) esperando a execução "
                f"{execution_id} de {cfg.RECLASSIFICADOR_PREDICAO_TOOLNAME} terminar. "
                f"Último status: {status} / {body.get('status_text')}"
            )
        time.sleep(cfg.RECLASSIFIER_BRIDGE_POLL_INTERVAL_S)


def _download_output(status_body: dict, code: str = "output") -> pd.DataFrame:
    outputs = status_body.get("outputs") or []
    output = next((o for o in outputs if o.get("code") == code), None)
    if output is None or not output.get("arquivo"):
        raise ReclassifierBridgeError(
            f"Output '{code}' (base_reclassificada) não encontrado na resposta do PPR: "
            f"{outputs}"
        )
    resp = requests.get(output["arquivo"], timeout=60)
    if resp.status_code != 200:
        raise ReclassifierBridgeError(
            f"Falha ao baixar output '{code}' (HTTP {resp.status_code}) de "
            f"{output['arquivo']}"
        )
    return pd.read_excel(io.BytesIO(resp.content))


def run_predicao_via_api(
    reclassifier_base_df: pd.DataFrame,
    parametros_reclassificador_file,
    modelo_reclassificador_file,
) -> pd.DataFrame:
    """Dispara reclassificador_predicao via API do PPR e devolve base_reclassificada
    já parseada em DataFrame (colunas mínimas garantidas: 'Chave única' e
    'Conta ajustada', consumidas por transforms.integrate_reclassified).
    """
    if not cfg.PPR_API_TOKEN:
        raise ReclassifierBridgeError(
            "PPR_API_TOKEN não configurado — necessário para chamar "
            f"{cfg.RECLASSIFICADOR_PREDICAO_TOOLNAME} via API do PPR."
        )
    if parametros_reclassificador_file is None or modelo_reclassificador_file is None:
        raise ReclassifierBridgeError(
            "parametros_reclassificador e modelo_reclassificador são obrigatórios "
            "para a etapa de reclassificação via API (ou informe base_reclassificada "
            "manualmente para pular esta etapa)."
        )

    run_id = uuid.uuid4()
    scratch_prefix = f"tools/{cfg.RECLASSIFICADOR_PREDICAO_TOOLNAME}/_bridge/{run_id}"

    base_key = _upload_bytes(
        _read_bytes(build_predicao_base(reclassifier_base_df)),
        f"{scratch_prefix}/base.xlsx",
    )
    param_key = _upload_bytes(
        _read_bytes(parametros_reclassificador_file),
        f"{scratch_prefix}/parametros.xlsx",
    )
    modelo_key = _upload_bytes(
        _read_bytes(modelo_reclassificador_file),
        f"{scratch_prefix}/modelo.pkl",
    )

    execution_id = _post_exec(
        files={"arquivo": base_key, "arquivo_param": param_key, "reclassificador": modelo_key}
    )
    status_body = _poll_status(execution_id)
    return _download_output(status_body, code="output")
