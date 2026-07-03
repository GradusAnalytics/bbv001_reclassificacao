"""
bbv001_reclassificacao.py — Ferramenta tradicional do PPR (Reclassificação de Lançamentos).

Contrato gradus-platform (ver dummy_repo):
  - main(**inputs) recebe os inputs pelos códigos dos InputFields.
    Arquivos chegam JÁ ABERTOS (file-like binário), não como caminho.
  - Retorna um dict {código_output: valor}. Para arquivos, o valor é um BytesIO
    e o nome vai em "<código>__nome". O wrapper sobe os arquivos ao S3 e faz o callback.

Inputs  (InputField.code): base_fechamento, depara_custo, classe_valor_conta,
                           estrutura_contas, estrutura_entidades_cc (opcional).
                           A etapa de reclassificação chama, via engine.reclassifier_bridge,
                           a ferramenta reclassificador_predicao_bbv001 (API do PPR), que já
                           usa seus próprios arquivos default de modelo/parâmetros — não são
                           mais inputs desta ferramenta. base_reclassificada (opcional) é só
                           um override manual: se informado, pula a chamada à API e usa o
                           arquivo fornecido diretamente.
Outputs (OutputField.code): base_final (arquivo), base_reclassificador (arquivo),
                            auditoria (tabela), log_execucao (texto_longo).

O engine BBV001 fica em ./engine e NÃO é alterado — paridade com o Alteryx.

NOTA (conversão xlsb): alguns inputs chegam em .xlsb "fora do padrão" (ex.: exportações
de ERP) que openpyxl/calamine/pyxlsb não leem corretamente — pyxlsb inclusive lê só a
primeira coluna e descarta o resto silenciosamente. Esses bytes são normalizados para
.xlsx via LibreOffice headless (ver _normalize_to_xlsx_bytes) antes de qualquer leitura.
Requer o pacote libreoffice-calc na imagem Docker.
"""
import io
import os
import sys
import json
import shutil
import zipfile
import logging
import tempfile
import subprocess
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "engine"))

# Nome do input → nome de arquivo canônico esperado pelo engine (config.py)
CANON = {
    "base_fechamento":      "base_fechamento.xlsx",
    "depara_custo":         "DeXPara_Custo_Gradus.xlsx",
    "classe_valor_conta":   "BBV001-260509-Classe de Valor x Conta Contábil-v1 MU.xlsx",
    "estrutura_contas":      "20260509 - 12h30 - Estrutura de contas.xlsx",
    "estrutura_entidades_cc": "estrutura_entidades_cc.xlsx",
}
WORK = "/tmp/bbv001"
IN_DIR = os.path.join(WORK, "inputs")
OUT_DIR = os.path.join(WORK, "outputs")
FINAL_NAME = "BBV001-Base_consolidada_final.xlsx"
RECL_NAME = "BBV001-Base_para_reclassificador.xlsx"
EXCECOES_NAME = "BBV001-Excecoes_nao_cadastrados.xlsx"


def _is_xlsb(data):
    """xlsb e xlsx são ambos ZIP (magic 'PK'); o que distingue é o interior:
    xlsb contém xl/workbook.bin, xlsx contém xl/workbook.xml."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return "xl/workbook.bin" in z.namelist()
    except zipfile.BadZipFile:
        return False


def _xlsb_to_xlsx_bytes(data):
    """Converte bytes de um .xlsb em bytes de .xlsx via LibreOffice headless.
    Necessário porque os parsers .xlsb do Python (openpyxl/calamine/pyxlsb)
    falham ou corrompem silenciosamente arquivos fora do padrão."""
    binario = shutil.which("libreoffice") or shutil.which("soffice")
    if not binario:
        raise RuntimeError(
            "LibreOffice não encontrado. Instale 'libreoffice-calc' na imagem Docker."
        )
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.xlsb"
        src.write_bytes(data)
        # UserInstallation isolado: resolve HOME não-gravável e conversões concorrentes
        subprocess.run(
            [binario, "-env:UserInstallation=file://" + tmp + "/prof",
             "--headless", "--convert-to", "xlsx", "--outdir", tmp, str(src)],
            check=True, capture_output=True, timeout=180,
        )
        out = Path(tmp) / "in.xlsx"
        if not out.exists():
            raise RuntimeError("Conversão LibreOffice não gerou o .xlsx esperado.")
        return out.read_bytes()


def _normalize_to_xlsx_bytes(data):
    """Se os bytes forem .xlsb, converte para .xlsx; senão devolve intactos."""
    return _xlsb_to_xlsx_bytes(data) if _is_xlsb(data) else data


def _read_bytes(file_obj):
    """Extrai bytes de um input (file-like / bytes / caminho). None se vazio."""
    if file_obj is None:
        return None
    if hasattr(file_obj, "read"):
        data = file_obj.read()
    elif isinstance(file_obj, (bytes, bytearray)):
        data = bytes(file_obj)
    elif isinstance(file_obj, str) and os.path.exists(file_obj):
        with open(file_obj, "rb") as fh:
            data = fh.read()
    else:
        return None
    return data or None


def _write_input(file_obj, dest):
    """Grava o input em dest, normalizando xlsb→xlsx quando necessário.
    Retorna True se gravou."""
    data = _read_bytes(file_obj)
    if data is None:
        return False
    data = _normalize_to_xlsx_bytes(data)  # xlsb "fantasiado" de .xlsx vira xlsx real
    with open(dest, "wb") as out:
        out.write(data)
    return True


def _read_bytesio(name):
    p = os.path.join(OUT_DIR, name)
    if not os.path.exists(p):
        return None
    b = io.BytesIO()
    with open(p, "rb") as fh:
        b.write(fh.read())
    b.seek(0)
    return b


def main(base_fechamento=None, depara_custo=None, classe_valor_conta=None,
         estrutura_contas=None, base_reclassificada=None, estrutura_entidades_cc=None):
    import pandas as pd

    # 1) Diretórios limpos por execução
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(IN_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)

    incoming = {
        "base_fechamento":      base_fechamento,
        "depara_custo":         depara_custo,
        "classe_valor_conta":   classe_valor_conta,
        "estrutura_contas":     estrutura_contas,
        "estrutura_entidades_cc": estrutura_entidades_cc,
    }
    for role, fobj in incoming.items():
        _write_input(fobj, os.path.join(IN_DIR, CANON[role]))

    # base_reclassificada agora é só o override manual (pula a chamada à API do PPR
    # em reclassifier_bridge quando informado) — lido direto em memória, não escrito
    # em disco, já que pipeline.run_pipeline() espera um DataFrame.
    # Normaliza xlsb→xlsx também aqui, pois este input não passa por _write_input.
    df_reclass_override = None
    if base_reclassificada is not None:
        _raw = _read_bytes(base_reclassificada)
        if _raw is not None:
            _xlsx = _normalize_to_xlsx_bytes(_raw)
            # Não fixamos o nome da aba: pegamos sempre a primeira (índice 0),
            # pois xlsb/xlsx desta origem têm nomes de aba variáveis.
            df_reclass_override = pd.read_excel(io.BytesIO(_xlsx), sheet_name=0)

    # 2) Captura do log (log_step do engine)
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%H:%M:%S"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)

    # 3) Env apontando para os dirs de trabalho + roda o engine (intacto)
    os.environ["BBV001_INPUT_DIR"] = IN_DIR
    os.environ["BBV001_OUTPUT_DIR"] = OUT_DIR
    os.environ["BBV001_BASE_FILE"] = CANON["base_fechamento"]

    import pipeline
    result = pipeline.run_pipeline(base_reclassificada_override=df_reclass_override)

    # 4) Auditoria por Tipo (output tipo=tabela → JSON array de linhas)
    audit_rows = []
    unioned = result.get("_intermediates", {}).get("unioned")
    if unioned is not None and "Tipo" in unioned.columns:
        g = (unioned.groupby("Tipo")
                    .agg(registros=("Codigo Interno", "count"),
                         soma_valor=("Valor", "sum"))
                    .reset_index())
        for _, r in g.iterrows():
            audit_rows.append({
                "tipo": str(r["Tipo"]),
                "registros": int(r["registros"]),
                "soma_valor": round(float(r["soma_valor"]), 2),
            })

    # 5) Monta os parâmetros de saída
    parametros_saida = {
        "auditoria": json.dumps(audit_rows, ensure_ascii=False),
        "log_execucao": buf.getvalue(),
        # A etapa de reclassificação sempre roda agora (override manual ou via API a
        # reclassificador_predicao_bbv001, que já tem seus próprios defaults) — "Run 1"
        # sem reclassificação não existe mais.
        "run_mode": "2",
    }
    final_io = _read_bytesio(FINAL_NAME)
    if final_io is not None:
        parametros_saida["base_final"] = final_io
        parametros_saida["base_final__nome"] = FINAL_NAME
    recl_io = _read_bytesio(RECL_NAME)
    if recl_io is not None:
        parametros_saida["base_reclassificador"] = recl_io
        parametros_saida["base_reclassificador__nome"] = RECL_NAME

    # 6) Excel de exceções (2 abas): Contas Contábeis e Centros de Custo
    cols = ["Código", "Descrição", "Valor"]
    exc = result.get("exceptions", {}) or {}
    df_contas = exc.get("contas")
    df_cc = exc.get("centros_custo")
    if df_contas is None:
        df_contas = pd.DataFrame(columns=cols)
    if df_cc is None:
        df_cc = pd.DataFrame(columns=cols)
    exc_io = io.BytesIO()
    with pd.ExcelWriter(exc_io, engine="openpyxl") as xw:
        df_contas.to_excel(xw, sheet_name="Contas Contábeis", index=False)
        df_cc.to_excel(xw, sheet_name="Centros de Custo", index=False)
    exc_io.seek(0)
    parametros_saida["excecoes"] = exc_io
    parametros_saida["excecoes__nome"] = EXCECOES_NAME

    return parametros_saida
