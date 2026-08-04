"""
bbv001_reclassificacao.py — Ferramenta tradicional do PPR (Reclassificação de Lançamentos).

Contrato gradus-platform (ver dummy_repo):
  - main(**inputs) recebe os inputs pelos códigos dos InputFields.
    Arquivos chegam JÁ ABERTOS (file-like binário), não como caminho.
  - Retorna um dict {código_output: valor}. Para arquivos, o valor é um BytesIO
    e o nome vai em "<código>__nome". O wrapper sobe os arquivos ao S3 e faz o callback.

Inputs  (InputField.code): base_fechamento, depara_custo, classe_valor_conta,
                           estrutura_contas, depara_grupos (V2/R4 — OBRIGATÓRIO, de-para
                           GRUPO→Conta que substitui os 11 overrides hardcoded),
                           estrutura_entidades_cc (opcional).
                           A etapa de reclassificação chama, via engine.reclassifier_bridge,
                           a ferramenta reclassificador_predicao_bbv001 (API do PPR), que já
                           usa seus próprios arquivos default de modelo/parâmetros — não são
                           mais inputs desta ferramenta. base_reclassificada (opcional) é só
                           um override manual: se informado, pula a chamada à API e usa o
                           arquivo fornecido diretamente.
Outputs (OutputField.code): base_final (arquivo), base_reclassificador (arquivo),
                            auditoria (tabela), auditoria_status (tabela — V3, PENDENTE
                            cadastro de OutputField no admin do PPR, ver main()),
                            valor_por_pacote (tabela),
                            warnings (tabela — V2, opcional cadastrar),
                            log_execucao (texto_longo), excecoes (arquivo, 5 abas na V2).

V2: o engine em ./engine ganhou controles transversais (engine/controls.py) —
validação de inputs/cadastros, censo de duplicatas início×fim (ERRO bloqueante),
equação de conservação entrada×saída e avisos estruturados ao usuário. As mudanças
de comportamento aprovadas vs a v1/Alteryx estão em docs/SPEC_V2.md do projeto.
Erros de negócio sobem como RuntimeError com mensagem acionável (o PPR exibe no
status da execução).

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
    "depara_grupos":         "depara_grupos.xlsx",          # V2/R4 — obrigatório
    "estrutura_entidades_cc": "estrutura_entidades_cc.xlsx",
}

# V2/D1 — inputs obrigatórios (validação upfront ANTES de rodar o engine) + nome
# amigável para a mensagem de erro que o usuário do PPR vai ler.
REQUIRED_INPUTS = {
    "base_fechamento":    "Base de Fechamento (mensal)",
    "depara_custo":       "De-Para Custo (Cobrança)",
    "classe_valor_conta": "Classe de Valor × Conta Contábil",
    "estrutura_contas":   "Estrutura de Contas",
    "depara_grupos":      "De-Para de Grupos → Conta (novo na v2)",
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


def _fmt_valor_br(value):
    """Formata um float como string no padrão BR: milhar '.', decimal ',', 2 casas
    (ex.: 1234567.891 -> "1.234.567,89"). Sem depender de locale (pt_BR pode não
    estar instalado na imagem Docker)."""
    return f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _read_bytesio(name):
    p = os.path.join(OUT_DIR, name)
    if not os.path.exists(p):
        return None
    b = io.BytesIO()
    with open(p, "rb") as fh:
        b.write(fh.read())
    b.seek(0)
    return b


def _montar_excel_excecoes(exc: dict, aviso_rows: list) -> io.BytesIO:
    """
    Monta o excel de exceções (5 abas na V2: Contas Contábeis, Centros de Custo,
    Correção Automática de Conta, Bloqueio Prefixo Conta, Avisos) a partir do
    dict devolvido por engine.transforms.build_exceptions() + a lista de
    avisos do RunControls.

    Extraída de main() pra ser testável isoladamente (V2/R9, 2026-07-20) — main()
    lida com o contrato de inputs/outputs file-like do PPR, o que tornaria difícil
    testar só a montagem do Excel sem essa separação.
    """
    import pandas as pd

    cols = ["Código", "Descrição", "Valor"]
    cols_r9 = ["Codigo Interno", "Tipo", "Conta Contabil", "Nome da Conta", "Conta destino",
               "Nome conta contábil", "Nome da Classe de Valor", "Valor", "Centro de Custo",
               "Nome do Centro de Custo", "Motivo", "Conta OM", "Ação Recomendada"]
    cols_r10 = ["Codigo Interno", "Tipo", "Nome da Classe de Valor", "Valor",
               "Conta destino original", "Conta destino corrigida", "Conta OM"]

    df_contas = exc.get("contas")
    df_cc = exc.get("centros_custo")
    df_bloqueio = exc.get("bloqueio_prefixo")
    df_correcao = exc.get("correcao_automatica")
    if df_contas is None:
        df_contas = pd.DataFrame(columns=cols)
    if df_cc is None:
        df_cc = pd.DataFrame(columns=cols)
    if df_bloqueio is None:
        df_bloqueio = pd.DataFrame(columns=cols_r9)
    if df_correcao is None:
        df_correcao = pd.DataFrame(columns=cols_r10)

    df_avisos = pd.DataFrame(
        aviso_rows, columns=["etapa", "severidade", "codigo", "mensagem", "registros", "valor"]
    ).rename(columns={"etapa": "Etapa", "severidade": "Severidade", "codigo": "Código",
                      "mensagem": "Mensagem", "registros": "Registros", "valor": "Valor"})

    exc_io = io.BytesIO()
    with pd.ExcelWriter(exc_io, engine="openpyxl") as xw:
        df_contas.to_excel(xw, sheet_name="Contas Contábeis", index=False)
        df_cc.to_excel(xw, sheet_name="Centros de Custo", index=False)
        df_correcao.to_excel(xw, sheet_name="Correção Automática de Conta", index=False)
        df_bloqueio.to_excel(xw, sheet_name="Bloqueio Prefixo Conta", index=False)
        df_avisos.to_excel(xw, sheet_name="Avisos", index=False)
    exc_io.seek(0)
    return exc_io


def _monta_auditoria(base_completa):
    """V3 — auditoria sobre o UNIVERSO COMPLETO: por Tipo (com 'Não classificado')
    e por Status. Conta 'ID Lançamento', a chave verdadeira do lançamento.
    """
    por_tipo, por_status = [], []
    if base_completa is None or len(base_completa) == 0:
        return por_tipo, por_status
    g = (base_completa.groupby("Tipo")
                      .agg(registros=("ID Lançamento", "count"),
                           soma_valor=("Valor", "sum"))
                      .reset_index())
    for _, r in g.iterrows():
        por_tipo.append({"tipo": str(r["Tipo"]),
                         "registros": int(r["registros"]),
                         "soma_valor": _fmt_valor_br(float(r["soma_valor"]))})
    s = (base_completa.groupby("Status")
                      .agg(registros=("ID Lançamento", "count"),
                           soma_valor=("Valor", "sum"))
                      .reset_index())
    for _, r in s.iterrows():
        por_status.append({"status": str(r["Status"]),
                           "registros": int(r["registros"]),
                           "soma_valor": _fmt_valor_br(float(r["soma_valor"]))})
    return por_tipo, por_status


def main(base_fechamento=None, depara_custo=None, classe_valor_conta=None,
         estrutura_contas=None, base_reclassificada=None, estrutura_entidades_cc=None,
         depara_grupos=None):
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
        "depara_grupos":        depara_grupos,          # V2/R4
        "estrutura_entidades_cc": estrutura_entidades_cc,
    }
    gravados = {}
    for role, fobj in incoming.items():
        gravados[role] = _write_input(fobj, os.path.join(IN_DIR, CANON[role]))

    # V2/D1 — validação upfront: obrigatório ausente vira erro acionável AGORA,
    # não FileNotFoundError críptico no meio do engine.
    faltando = [nome for role, nome in REQUIRED_INPUTS.items() if not gravados.get(role)]
    if faltando:
        raise RuntimeError(
            "[INPUT_OBRIGATORIO_AUSENTE] Os seguintes inputs obrigatórios não foram "
            f"enviados (ou vieram vazios): {faltando}. Envie os arquivos e execute novamente."
        )

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

    # 3) Env apontando para os dirs de trabalho + roda o engine
    os.environ["BBV001_INPUT_DIR"] = IN_DIR
    os.environ["BBV001_OUTPUT_DIR"] = OUT_DIR
    os.environ["BBV001_BASE_FILE"] = CANON["base_fechamento"]

    import pipeline
    from controls import BloqueioError
    try:
        result = pipeline.run_pipeline(base_reclassificada_override=df_reclass_override)
    except BloqueioError as exc:
        # V2 — erro de NEGÓCIO com mensagem acionável (input inválido, GRUPO não
        # cadastrado, duplicação fabricada, conservação violada). Propaga como
        # RuntimeError: o PPR marca a execução como falha e exibe a mensagem ao
        # usuário no status — nenhuma base_final é publicada.
        logging.getLogger(__name__).error(str(exc))
        raise RuntimeError(str(exc)) from exc

    # 4) Auditoria — V3: sobre a base completa, por Tipo e por Status
    audit_rows, audit_status_rows = _monta_auditoria(result.get("base_completa"))

    # 4b) Valor por Pacote, antes × depois da reclassificação (output tipo=tabela)
    pacote_rows = []
    pacote_report = result.get("pacote_report")
    if pacote_report is not None:
        for _, r in pacote_report.iterrows():
            pacote_rows.append({
                "pacote": str(r["Pacote"]),
                "valor_antes_reclassf": _fmt_valor_br(float(r["Valor antes Reclassf."])),
                "valor_pos_reclassf": _fmt_valor_br(float(r["Valor pós Reclassf."])),
            })

    # 4c) V2 — avisos estruturados (aba 'Avisos' + tabela `warnings` + resumo no log)
    controls = result.get("controls")
    aviso_rows = controls.to_rows() if controls is not None else []
    resumo = controls.resumo_log() if controls is not None else ""

    # 5) Monta os parâmetros de saída
    parametros_saida = {
        "auditoria": json.dumps(audit_rows, ensure_ascii=False),
        # V3 — quebra por Status (FORA_DE_ESCOPO/FALHA_INTERNA/CADASTRO_BLOQUEANTE/
        # CADASTRO_PENDENTE/DESTINO_SUSPEITO/DADO_INVALIDO/OK — ordem de precedência, config.STATUS_
        # PRECEDENCIA) do mesmo universo completo acima. PENDENCIA: este OutputField
        # ainda não existe no admin do PPR — precisa ser cadastrado pelo time de
        # analytics antes desta chave ter efeito na plataforma (mesma natureza da
        # pendência que `depara_grupos` teve no R4 e o campo de modo do R9 teve no R10).
        "auditoria_status": json.dumps(audit_status_rows, ensure_ascii=False),
        "valor_por_pacote": json.dumps(pacote_rows, ensure_ascii=False),
        # V2 — tabela de avisos (OutputField `warnings`, opcional cadastrar no PPR;
        # o mesmo conteúdo SEMPRE sai na aba 'Avisos' do excel de exceções e no
        # resumo do topo do log, garantindo visibilidade sem mudança de admin)
        "warnings": json.dumps(aviso_rows, ensure_ascii=False),
        # V2 — resumo da conciliação + avisos no TOPO do log (o detalhe segue abaixo)
        "log_execucao": resumo + buf.getvalue(),
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

    # 6) Excel de exceções — V2: 5 abas (Contas Contábeis, Centros de Custo,
    #    Correção Automática de Conta, Bloqueio Prefixo Conta, Avisos)
    exc = result.get("exceptions", {}) or {}
    exc_io = _montar_excel_excecoes(exc, aviso_rows)
    parametros_saida["excecoes"] = exc_io
    parametros_saida["excecoes__nome"] = EXCECOES_NAME

    return parametros_saida
