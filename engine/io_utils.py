"""
io_utils.py — Read inputs and write outputs for the BBV001 pipeline.

One function per Alteryx Input tool. Reading and the trivial first Select
(rename / column-type change) are bundled together where it makes sense.
"""
import logging
import zipfile
from pathlib import Path
from typing import Optional

import pandas as pd

from config import (
    INPUT_FILES,
    REQUIRED_COLUMNS,
    OUTPUT_RECLASSIFIER,
    OUTPUT_FINAL_CONSOLIDATED,
    OUTPUT_DIR,
)
from controls import BloqueioError
from helpers import log_step

logger = logging.getLogger(__name__)

# Assinatura do container binário OLE2/CFB usado por formatos legados do Office
# (.xls e alguns .xlsb). main() sempre grava os inputs com extensão .xlsx fixa (ver
# CANON em bbv001_reclassificacao.py), então a extensão do arquivo não é confiável
# para saber o formato real — é preciso inspecionar os bytes.
_OLE2_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _detecta_xlsb(path) -> bool:
    """V2/§3.10 — detecta .xlsb pelo CONTEÚDO, cobrindo as duas variantes reais:
    (a) container OLE2/CFB (assinatura D0CF11E0...); (b) container ZIP ("PK") com
    `xl/workbook.bin` no lugar do `xl/workbook.xml` — é o formato dos .xlsb de ERP
    deste cliente, que a v1 NÃO detectava (premissa OLE2 errada) e acabava lendo via
    pyxlsb, que devolve só a 1ª coluna em silêncio (evidência: _qa_resultados_20260708)."""
    try:
        with open(path, "rb") as fh:
            header = fh.read(8)
    except OSError:
        return False
    if header.startswith(_OLE2_SIGNATURE):
        return True
    if header[:2] == b"PK":
        try:
            with zipfile.ZipFile(path) as z:
                return "xl/workbook.bin" in z.namelist()
        except zipfile.BadZipFile:
            return False
    return False


def _read_excel(path, **kwargs):
    """Wrapper de pd.read_excel. V2/§3.10 (decisão 2026-07-09): .xlsb é RECUSADO com
    erro acionável — nenhum leitor Python disponível lê os .xlsb deste ERP de forma
    íntegra (pyxlsb perde todas as colunas menos a 1ª, silenciosamente; calamine dá
    panic). Na plataforma isso nunca dispara: o main() converte xlsb→xlsx via
    LibreOffice ANTES de gravar os inputs."""
    if _detecta_xlsb(path):
        raise BloqueioError(
            "FORMATO_XLSB",
            f"O arquivo '{Path(path).name}' está em formato .xlsb, que não pode ser lido "
            f"de forma confiável fora da plataforma. Abra o arquivo no Excel e salve como "
            f".xlsx (Pasta de Trabalho do Excel) antes de enviar.")
    return pd.read_excel(path, **kwargs)


def _valida_colunas(df: pd.DataFrame, input_key: str, path, sheet) -> pd.DataFrame:
    """V2/D1 — validação pós-leitura de colunas mínimas (cinto-e-suspensório da
    validação upfront do main). Falta de coluna vira erro acionável, não KeyError
    críptico no meio da cascata."""
    required = REQUIRED_COLUMNS.get(input_key, [])
    faltando = [c for c in required if c not in df.columns]
    if faltando:
        raise BloqueioError(
            "COLUNAS_FALTANDO",
            f"O input '{input_key}' (arquivo '{Path(path).name}', aba '{sheet}') não tem "
            f"as colunas obrigatórias: {faltando}. Colunas encontradas: "
            f"{list(df.columns)[:20]}. Verifique se o arquivo/aba corretos foram enviados "
            f"e se o template não mudou.")
    return df


def _read_excel_with_header_marker(path, sheet, marker: str, max_scan: int = 15) -> pd.DataFrame:
    """
    Some sheets carry title/banner rows above the real table header (e.g. the
    'Base' and 'Unico CV' sheets start with 'Titulo2'/'Titulo3' and blank rows, so a
    plain read_excel mislabels the columns as 'Unnamed: N'). Scan the first `max_scan`
    rows for the cell equal to `marker` and use that row as the header.
    """
    probe = _read_excel(path, sheet_name=sheet, header=None, nrows=max_scan)
    header_row = None
    for i in range(len(probe)):
        if probe.iloc[i].astype(str).str.strip().eq(marker).any():
            header_row = i
            break
    if header_row is None:
        raise ValueError(
            f"Header marker {marker!r} not found in the first {max_scan} rows of "
            f"sheet {sheet!r} ({path}). The template may have changed."
        )
    return _read_excel(path, sheet_name=sheet, header=header_row)


# -------------------------------------------------------------------------
# Inputs
# -------------------------------------------------------------------------
def read_base_fechamento() -> pd.DataFrame:
    """Tool 4 + Tool 87 (rename Valor do Lancamento → Valor)."""
    cfg = INPUT_FILES["base_fechamento"]
    df = _read_excel(cfg["path"], sheet_name=cfg["sheet"])
    df = df.rename(columns={"Valor do Lancamento": "Valor"})
    # V2/D1 — validar ANTES de tocar qualquer coluna. A ordem importa: a coerção de
    # 'Conta Contabil' logo abaixo indexa a coluna pelo nome, então se o usuário
    # enviar o arquivo/aba errado (cenário mais provável na virada de mês) ele
    # receberia `KeyError: 'Conta Contabil'` cru do pandas em vez do erro de negócio
    # acionável que o D1 promete (qual input, qual aba, quais colunas faltam).
    # Foi assim que se descobriu que 'real junho bv.xlsx' não era base de fechamento
    # (Task 13, achado A1/A2). Vem depois do rename porque REQUIRED_COLUMNS exige
    # 'Valor', o nome já renomeado. Regressão coberta por
    # tests/test_v3_colunas_faltando_antes_da_coercao.py.
    _valida_colunas(df, "base_fechamento", cfg["path"], cfg["sheet"])
    # V3 — ID Lançamento: posição da linha na base bruta (1..N), imutável.
    # É a ÚNICA chave verdadeira por lançamento: "Codigo Interno" não serve porque
    # duplicata nativa existe e é válida (dono, 2026-07-08; MAPA_PROCESSO G9).
    # Nome deliberadamente diferente de "RecordID", que já existe no Tool 110
    # significando outra coisa (cumcount por Codigo Interno).
    df.insert(0, "ID Lançamento", range(1, len(df) + 1))
    # Conta Contabil cells come back as full-precision Python ints (25-digit account codes
    # stored as numbers in Excel). Keep them as strings: as ints they survive the pipeline
    # in memory but get cast to float64 on the Excel write, losing precision
    # (8172700000000000010000000 → 8172699999999999715835904). This column also feeds
    # 'Conta destino' for the two Match paths (Tools 93/94), so the fix covers both.
    df["Conta Contabil"] = df["Conta Contabil"].apply(
        lambda v: v if (isinstance(v, str) or (isinstance(v, float) and pd.isna(v))) else str(v)
    )
    log_step(logger, "4", "Read Base Fechamento + rename Valor", df)
    return df


def read_depara_custo() -> pd.DataFrame:
    """Tool 10. V2/R3: DATA_BASE agora é coluna obrigatória (resolução por recência)."""
    cfg = INPUT_FILES["depara_custo"]
    df = _read_excel(cfg["path"], sheet_name=cfg["sheet"])
    _valida_colunas(df, "depara_custo", cfg["path"], cfg["sheet"])  # V2/D1
    log_step(logger, "10", "Read De-Para Custo", df)
    return df


def read_classe_valor_conta() -> pd.DataFrame:
    """Tool 47 — sheet 'Base' (real header below a few title rows)."""
    cfg = INPUT_FILES["classe_valor_conta"]
    df = _read_excel_with_header_marker(cfg["path"], cfg["sheet_base"], "Nome classe de valor")
    _valida_colunas(df, "classe_valor_conta_base", cfg["path"], cfg["sheet_base"])  # V2/D1
    log_step(logger, "47", "Read Classe Valor x Conta (Base)", df)
    return df


def read_unico_cv() -> pd.DataFrame:
    """Tool 48 — sheet 'Unico CV' (real header below a few title rows)."""
    cfg = INPUT_FILES["classe_valor_conta"]
    df = _read_excel_with_header_marker(cfg["path"], cfg["sheet_unico_cv"], "Classe de valor")
    _valida_colunas(df, "classe_valor_conta_unico_cv", cfg["path"], cfg["sheet_unico_cv"])  # V2/D1
    log_step(logger, "48", "Read Unico CV", df)
    return df


def read_estrutura_contas() -> pd.DataFrame:
    """Tool 61."""
    cfg = INPUT_FILES["estrutura_contas"]
    df = _read_excel(cfg["path"], sheet_name=cfg["sheet"])
    _valida_colunas(df, "estrutura_contas", cfg["path"], cfg["sheet"])  # V2/D1
    log_step(logger, "61", "Read Estrutura de Contas", df)
    return df


def read_depara_grupos() -> pd.DataFrame:
    """V2/R4 (NOVO) — de-para GRUPO → Conta OM / Conta Contábil. Input OBRIGATÓRIO;
    substitui os 11 overrides hardcoded do Tool 86. Template: header na linha 1,
    colunas Grupo · Conta OM · Conta Contábil (seed em
    aux_files/gera_seed_depara_grupos.py).

    V3.1 (2026-07-30): a aba é lida por NOME (`INPUT_FILES[...]["sheet"]`, default
    `De-Para Grupos`), com fallback para a primeira aba do arquivo. Deixou de ser
    "1 aba": o arquivo pode trazer todos os cadastros, um por aba, e o mesmo arquivo
    ser enviado em todos os campos — só a Base de Fechamento continua separada
    (ela e a aba `Base` do classe_valor_conta exigiriam o mesmo nome de aba)."""
    cfg = INPUT_FILES["depara_grupos"]
    path: Path = cfg["path"]
    if not path.exists():
        raise BloqueioError(
            "INPUT_OBRIGATORIO_AUSENTE",
            "O input 'depara_grupos' (De-Para de Grupos de Cobrança → Conta) é obrigatório "
            "e não foi enviado. Use o template de 3 colunas (Grupo · Conta OM · Conta "
            "Contábil) — sem ele não é possível classificar o caminho Cobrança.")
    # V3.1 — aba por nome, caindo para a 1ª aba quando o nome não existe no arquivo
    aba_esperada = cfg["sheet"]
    aba = aba_esperada
    fallback_usado = False
    try:
        df = _read_excel(path, sheet_name=aba)
    except ValueError:
        fallback_usado = True
        aba = cfg.get("sheet_fallback", 0)
        df = _read_excel(path, sheet_name=aba)
        logger.info(
            f"[R4] Aba {aba_esperada!r} não existe em '{path.name}' — lendo a primeira "
            f"aba do arquivo. Nomear a aba permite enviar todos os cadastros num "
            f"arquivo só.")
    # V3.1 (revisão final) — se a validação de colunas falhar DEPOIS do fallback, o
    # `logger.info` acima nunca chega ao usuário: um BloqueioError aborta o main()
    # antes de montar o log de execução (`bbv001_reclassificacao.py`). Por isso o
    # rótulo de aba passado à validação, sozinho, tem de dizer que houve fallback,
    # qual era a aba esperada e que ela não foi encontrada — sem isso a mensagem
    # citava só a aba efetivamente lida ("aba '0'"), que não diz nada ao usuário.
    aba_label = aba
    if fallback_usado:
        aba_label = (
            f"{aba} (a ferramenta tentou a 1ª aba do arquivo porque a aba esperada "
            f"{aba_esperada!r} não foi encontrada)"
        )
    _valida_colunas(df, "depara_grupos", path, aba_label)  # V2/D1
    # Cadastro mantido pelo usuário: strip nas 3 colunas de texto (contrato do
    # template — evita não-match por espaço acidental; não afeta a paridade dos
    # joins portados do Alteryx, que seguem byte-a-byte).
    for col in ("Grupo", "Conta OM", "Conta Contábil"):
        df[col] = df[col].apply(lambda v: v.strip() if isinstance(v, str) else v)
    log_step(logger, "R4", "Read De-Para Grupos (novo input obrigatório)", df)
    return df


def read_base_reclassificada() -> Optional[pd.DataFrame]:
    """
    Tool 124. Returns None when the file does not exist —
    e.g., on the first run that GENERATES the reclassifier input.
    On the second run (which CONSUMES the reclassifier output), the file must exist.
    """
    cfg = INPUT_FILES["base_reclassificada"]
    path: Path = cfg["path"]
    if not path.exists():
        logger.warning(
            f"[Tool 124] base_reclassificada not found at {path}. "
            f"Assuming 1st-run mode (no reclassifier output yet). "
            f"Reclassificador path will return an empty frame."
        )
        return None
    df = _read_excel(path, sheet_name=cfg["sheet"])
    log_step(logger, "124", "Read base_reclassificada", df)
    return df


def read_estrutura_entidades_cc() -> Optional[pd.DataFrame]:
    """
    NOVO (Tool 200) — cadastro de Entidades x Centros de Custo.

    Input OPCIONAL e tolerante a falhas: qualquer problema de leitura apenas emite
    WARNING e retorna None (a aba 'Centros de Custo' do relatório fica vazia) — nunca
    derruba a execução, pois as demais saídas não dependem deste cadastro.

    .xlsx real: lido via openpyxl com data_only=True (pega o valor já calculado das
    fórmulas). .xlsb (detectado pelos magic bytes, não pela extensão): lido via
    pyxlsb — atenção que pyxlsb NÃO recalcula fórmulas, só devolve o valor bruto
    armazenado no arquivo; se o cadastro usar fórmulas, confira se os valores saíram
    corretos (emite um WARNING adicional nesse caso).
    """
    cfg = INPUT_FILES["estrutura_entidades_cc"]
    path: Path = cfg["path"]
    if not path.exists():
        logger.warning(
            f"[Tool 200] cadastro de Centros de Custo não encontrado em {path}. "
            f"A aba 'Centros de Custo' do relatório ficará vazia."
        )
        return None
    try:
        if _detecta_xlsb(path):
            # V2/§3.10: sem leitor confiável de .xlsb fora da plataforma (pyxlsb
            # devolve só a 1ª coluna em silêncio). Input é OPCIONAL → warning e aba
            # vazia, em vez de derrubar a execução.
            logger.warning(
                "[Tool 200] cadastro de CC recebido em .xlsb — formato não pode ser lido "
                "de forma confiável; salve como .xlsx e reenvie. A aba 'Centros de Custo' "
                "do relatório de exceções ficará vazia nesta execução."
            )
            return None
        else:
            from openpyxl import load_workbook
            wb = load_workbook(path, read_only=True, data_only=True)
            sheet = cfg["sheet"] if cfg["sheet"] in wb.sheetnames else wb.sheetnames[0]
            rows = list(wb[sheet].values)
            wb.close()
            if not rows:
                logger.warning("[Tool 200] cadastro de CC vazio. Aba 'Centros de Custo' ficará vazia.")
                return None
            header = [str(h) if h is not None else f"col{i}" for i, h in enumerate(rows[0])]
            df = pd.DataFrame(rows[1:], columns=header)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"[Tool 200] não foi possível ler o cadastro de CC ({exc}). "
            f"A aba 'Centros de Custo' ficará vazia."
        )
        return None
    log_step(logger, "200", "Read Estrutura de Entidades x CC", df)
    return df


# V2/R4: get_manual_overrides() (Tool 86, 11 linhas hardcoded) foi REMOVIDA —
# o de-para de grupos agora entra pelo input obrigatório read_depara_grupos() acima.


# -------------------------------------------------------------------------
# Outputs
# -------------------------------------------------------------------------
def write_reclassifier_base(df: pd.DataFrame) -> Path:
    """Tool 123 — base para o processo de reclassificação."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_excel(OUTPUT_RECLASSIFIER, sheet_name="Base", index=False)
    log_step(logger, "123", f"Wrote reclassifier base → {OUTPUT_RECLASSIFIER.name}", df)
    return OUTPUT_RECLASSIFIER


def write_final_consolidated(df_aba1: pd.DataFrame, df_aba2: pd.DataFrame) -> Path:
    """Tool 114 equivalent — base consolidada para carga no Matrix.

    V3 (2026-07-27): 2 abas no mesmo schema — 'Consolidado' (universo gerencial,
    sem as Classes de Valor excluídas) e 'Fora de Escopo' (só elas). O nome da
    aba 1 é o mesmo da v2 para não quebrar quem já consome o arquivo.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FINAL_CONSOLIDATED, engine="openpyxl") as xw:
        df_aba1.to_excel(xw, sheet_name="Consolidado", index=False)
        df_aba2.to_excel(xw, sheet_name="Fora de Escopo", index=False)
    log_step(logger, "114", f"Wrote final consolidated → {OUTPUT_FINAL_CONSOLIDATED.name} "
                            f"(2 abas: {len(df_aba1):,} + {len(df_aba2):,})", df_aba1)
    return OUTPUT_FINAL_CONSOLIDATED
