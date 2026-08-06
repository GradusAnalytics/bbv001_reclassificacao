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
    COLUNAS_ALIAS,
    HEADER_MARKERS,
    OUTPUT_RECLASSIFIER,
    OUTPUT_FINAL_CONSOLIDATED,
    OUTPUT_DIR,
)
from controls import BloqueioError, coerce_conta_str
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


def _read_excel_with_header_marker(path, sheet, marker, max_scan: int = 15) -> pd.DataFrame:
    """
    Some sheets carry title/banner rows above the real table header (e.g. the
    'Base' and 'Unico CV' sheets start with 'Titulo2'/'Titulo3' and blank rows, so a
    plain read_excel mislabels the columns as 'Unnamed: N'). Scan the first `max_scan`
    rows for a cell equal to `marker` and use that row as the header.

    V3.3 — `marker` aceita uma string OU uma lista de strings: o arquivo mestre de
    cadastros escreve 'Nome da classe de valor' onde o anterior escrevia 'Nome classe
    de valor', e a coluna do marker é justamente uma das renomeadas.
    """
    markers = [marker] if isinstance(marker, str) else list(marker)
    probe = _read_excel(path, sheet_name=sheet, header=None, nrows=max_scan)
    header_row = None
    for i in range(len(probe)):
        celulas = probe.iloc[i].astype(str).str.strip()
        if celulas.isin(markers).any():
            header_row = i
            break
    if header_row is None:
        raise ValueError(
            f"Header marker {markers!r} not found in the first {max_scan} rows of "
            f"sheet {sheet!r} ({path}). The template may have changed."
        )
    return _read_excel(path, sheet_name=sheet, header=header_row)


def _aplica_aliases(df: pd.DataFrame, input_key: str) -> pd.DataFrame:
    """
    V3.3 — renomeia para o nome canônico as colunas que o arquivo mestre de cadastros
    escreve de outra forma (`config.COLUNAS_ALIAS`).

    Só renomeia quando o nome canônico AINDA NÃO existe no DataFrame: se o arquivo
    trouxer as duas grafias, a canônica ganha e nenhuma coluna é sobrescrita em
    silêncio.
    """
    alias = COLUNAS_ALIAS.get(input_key, {})
    renomear = {de: para for de, para in alias.items()
                if de in df.columns and para not in df.columns}
    if renomear:
        df = df.rename(columns=renomear)
        logger.info(f"[{input_key}] colunas renomeadas por alias (V3.3): {renomear}")
    return df


def _read_excel_primeira_aba(path):
    """
    V4 (dono, 2026-08-04) — lê a 1ª aba do arquivo (índice 0), sem checar nome.
    Usado por base_fechamento e De-Para de Custo: os dois deixaram de exigir nome
    de aba fixo ('Base'/'Planilha1' exatos, respectivamente).

    Devolve (df, nome_da_aba) — o NOME resolvido, não o índice, para que mensagens
    de erro downstream (_valida_colunas) citem a aba de verdade se as colunas
    obrigatórias faltarem.
    """
    df = _read_excel(path, sheet_name=0)
    nome = pd.ExcelFile(path).sheet_names[0]
    return df, nome


def valida_cadastros_auxiliares() -> None:
    """
    V4 (dono, 2026-08-04) — confere, ANTES de qualquer leitura de cadastro, que as 5
    abas obrigatórias existem no arquivo `cadastros_auxiliares`. Substitui o
    fallback "cai na 1ª aba" que `depara_grupos`/`estrutura_entidades_cc` tinham
    como arquivos avulsos (v3.1/v3.3): dentro de 1 arquivo com 5 abas, cair na 1ª
    aba por nome não encontrado leria dados de OUTRO cadastro em silêncio — pior
    que abortar com erro acionável.

    nrows=0 mantém a checagem barata (só estrutura, sem ler nenhuma linha de dado)
    e reaproveita a detecção de .xlsb / erro de abertura já centralizada em
    _read_excel.
    """
    cfg = INPUT_FILES["cadastros_auxiliares"]
    path: Path = cfg["path"]
    if not path.exists():
        raise BloqueioError(
            "INPUT_OBRIGATORIO_AUSENTE",
            "O input 'cadastros_auxiliares' (Allowlist · Arbitragem · Estrutura de "
            "Contas · Estrutura completa de Entidades · Grupos Cobrança, num "
            "arquivo só) é obrigatório e não foi enviado. Envie o arquivo e "
            "execute novamente.")
    todas_as_abas = _read_excel(path, sheet_name=None, nrows=0)
    esperadas = {k: v for k, v in cfg.items() if k.startswith("sheet_")}
    faltando = [nome for nome in esperadas.values() if nome not in todas_as_abas]
    if faltando:
        raise BloqueioError(
            "ABA_AUXILIAR_FALTANDO",
            f"O arquivo 'cadastros_auxiliares' ('{path.name}') não tem a(s) aba(s) "
            f"obrigatória(s): {faltando}. Abas encontradas: "
            f"{sorted(todas_as_abas)}. As 5 abas esperadas são: "
            f"{sorted(esperadas.values())}.")


# -------------------------------------------------------------------------
# Inputs
# -------------------------------------------------------------------------
def read_base_fechamento() -> pd.DataFrame:
    """Tool 4 + Tool 87 (rename Valor do Lancamento → Valor).

    V4 (dono, 2026-08-04) — lê a 1ª aba do arquivo, sem checar nome (antes exigia
    a aba 'Base' exata).
    """
    cfg = INPUT_FILES["base_fechamento"]
    df, aba = _read_excel_primeira_aba(cfg["path"])
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
    _valida_colunas(df, "base_fechamento", cfg["path"], aba)
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
    # V3.2 (dono, 2026-07-30, Step 5 do plano — ESCOPO ALÉM das 3 mudanças pedidas,
    # ver relatório da task): trocado o lambda inline por coerce_conta_str, que já é
    # o padrão usado no resto do projeto. O lambda antigo tratava só str e NaN-float
    # como "já ok"; qualquer outro float (a coluna inteira vira float64 se QUALQUER
    # linha tiver Conta Contabil nula) caía no `else str(v)` e produzia notação
    # científica ("8.1727e+24") em vez do número por extenso — bug latente na coluna
    # mais crítica da ferramenta. coerce_conta_str trata o caso float corretamente.
    df["Conta Contabil"] = coerce_conta_str(df["Conta Contabil"])
    # V3.2 (dono, 2026-07-30) — 'Centro de Custo' viaja como TEXTO. Coagir aqui, na
    # leitura, cobre de uma vez os TRÊS consumidores: o arquivo de carga, a base do
    # reclassificador (onde a coluna vira 'Código Centro de Custo', ver
    # reclassifier_bridge.py) e as abas de exceção. Coagir só na escrita deixaria dois
    # deles recebendo número. Não há risco de precisão aqui (9 dígitos cabem em int64);
    # o ganho é consistência de tipo — o Matrix lê essa coluna como texto.
    # Usa coerce_conta_str e NÃO um lambda inline equivalente ao que 'Conta Contabil'
    # tinha antes desta task: se a coluna tiver qualquer nulo, o pandas a devolve
    # float64 e aquele padrão produziria "220344.0".
    df["Centro de Custo"] = coerce_conta_str(df["Centro de Custo"])
    log_step(logger, "4", "Read Base Fechamento + rename Valor", df)
    return df


def read_depara_custo() -> pd.DataFrame:
    """Tool 10. V2/R3: DATA_BASE agora é coluna obrigatória (resolução por recência).

    V4 (dono, 2026-08-04) — lê a 1ª aba do arquivo, sem checar nome (antes exigia
    a aba 'Planilha1' exata).
    """
    cfg = INPUT_FILES["depara_custo"]
    df, aba = _read_excel_primeira_aba(cfg["path"])
    _valida_colunas(df, "depara_custo", cfg["path"], aba)
    log_step(logger, "10", "Read De-Para Custo", df)
    return df


def read_classe_valor_conta() -> pd.DataFrame:
    """Tool 47 — sheet 'Allowlist' (era 'Base'; renomeada na v4, dono 2026-08-04 —
    a aba é uma allowlist de pares [Classe de Valor, Conta Contábil] válidos, não
    uma "base"), header abaixo de linhas de banner.

    V3.3 — marker por lista e aliases de coluna: o arquivo mestre de cadastros usa
    'Nome da classe de valor' / 'Cód conta contábil permitida' / 'Cód Classe de
    Valor' onde o layout antigo usava 'Nome classe de valor' / 'Cód conta
    contábil' / 'Número att'. Os dois são legíveis.

    V4 — path e nome de aba vêm de INPUT_FILES['cadastros_auxiliares'] (antes,
    INPUT_FILES['classe_valor_conta']['sheet_base'], arquivo próprio).
    """
    cfg = INPUT_FILES["cadastros_auxiliares"]
    df = _read_excel_with_header_marker(
        cfg["path"], cfg["sheet_allowlist"], HEADER_MARKERS["classe_valor_conta_base"])
    df = _aplica_aliases(df, "classe_valor_conta_base")            # V3.3 — antes da validação
    _valida_colunas(df, "classe_valor_conta_base", cfg["path"], cfg["sheet_allowlist"])
    log_step(logger, "47", "Read Allowlist (Classe Valor x Conta)", df)
    return df


def read_unico_cv() -> pd.DataFrame:
    """Tool 48 — sheet 'Arbitragem' (era 'Unico CV'; renomeada na v4, dono
    2026-08-04 — nome que o próprio arquivo do cliente já usa no banner interno
    da aba, e que bate com o Tipo 'Arbitrado classe' que a ferramenta produz),
    header abaixo de linhas de banner.

    V4 — path e nome de aba vêm de INPUT_FILES['cadastros_auxiliares'].
    """
    cfg = INPUT_FILES["cadastros_auxiliares"]
    df = _read_excel_with_header_marker(
        cfg["path"], cfg["sheet_arbitragem"], HEADER_MARKERS["classe_valor_conta_unico_cv"])
    _valida_colunas(df, "classe_valor_conta_unico_cv", cfg["path"], cfg["sheet_arbitragem"])
    log_step(logger, "48", "Read Arbitragem (Unico CV)", df)
    return df


def read_estrutura_contas() -> pd.DataFrame:
    """Tool 61. V4 (dono, 2026-08-04) — path e nome de aba vêm de
    INPUT_FILES['cadastros_auxiliares'] (era arquivo próprio)."""
    cfg = INPUT_FILES["cadastros_auxiliares"]
    df = _read_excel(cfg["path"], sheet_name=cfg["sheet_estrutura"])
    _valida_colunas(df, "estrutura_contas", cfg["path"], cfg["sheet_estrutura"])
    log_step(logger, "61", "Read Estrutura de Contas", df)
    return df


def read_depara_grupos() -> pd.DataFrame:
    """V2/R4 — de-para GRUPO → Conta OM / Conta Contábil. Input OBRIGATÓRIO;
    substitui os 11 overrides hardcoded do Tool 86. Template: header na linha 1,
    colunas Grupo · Conta OM · Conta Contábil (seed em
    aux_files/gera_seed_depara_grupos.py).

    V4 (dono, 2026-08-04) — path e nome de aba ('Grupos Cobrança') vêm de
    INPUT_FILES['cadastros_auxiliares']. O fallback "cai na 1ª aba" da v3.1/v3.3
    foi REMOVIDO (ver valida_cadastros_auxiliares, chamada antes desta função no
    pipeline) — dentro de 1 arquivo com 5 abas nomeadas, cair na 1ª aba leria
    dados de outro cadastro em silêncio.
    """
    cfg = INPUT_FILES["cadastros_auxiliares"]
    df = _read_excel(cfg["path"], sheet_name=cfg["sheet_grupos"])
    _valida_colunas(df, "depara_grupos", cfg["path"], cfg["sheet_grupos"])
    # Cadastro mantido pelo usuário: strip nas 3 colunas de texto (contrato do
    # template — evita não-match por espaço acidental; não afeta a paridade dos
    # joins portados do Alteryx, que seguem byte-a-byte).
    for col in ("Grupo", "Conta OM", "Conta Contábil"):
        df[col] = df[col].apply(lambda v: v.strip() if isinstance(v, str) else v)
    log_step(logger, "R4", "Read De-Para Grupos", df)
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
    Tool 200 — cadastro de Entidades x Centros de Custo.

    V4 (dono, 2026-08-04) — deixou de ser input opcional avulso: agora é 1 das 5
    abas obrigatórias de 'cadastros_auxiliares' (valida_cadastros_auxiliares
    garante que a aba EXISTE antes desta função rodar no pipeline real). O
    fallback "cai na 1ª aba" da v3 foi REMOVIDO — dentro de 1 arquivo com 5 abas,
    cair na 1ª leria dados de outro cadastro em silêncio.

    Ainda devolve None (não levanta erro) só para "aba presente mas VAZIA" — é a
    diferença entre "faltou a aba" (ABA_AUXILIAR_FALTANDO, ERRO, bloqueante — já
    não chega aqui) e "a aba está lá mas sem conteúdo" (WARNING,
    CADASTRO_CC_NAO_VERIFICADO em transforms.build_exceptions, execução
    continua). Falha de LEITURA genuína (arquivo corrompido) propaga como
    exceção crua do pandas/openpyxl — mesma limitação conhecida e aceita, não
    corrigida de propósito, que já vale para os outros inputs obrigatórios
    (GUIA_INPUTS_TROUBLESHOOTING.md §3); não ficaria consistente blindar só este
    input agora que ele também é obrigatório.

    .xlsx: lido via openpyxl com data_only=True (pega o valor já calculado das
    fórmulas — pd.read_excel não garante isso).
    """
    cfg = INPUT_FILES["cadastros_auxiliares"]
    path: Path = cfg["path"]
    sheet = cfg["sheet_entidades_cc"]
    if _detecta_xlsb(path):
        # Não deveria acontecer aqui (cadastros_auxiliares é sempre .xlsx no
        # contrato v4), mas mantém a defesa por consistência com o resto do módulo.
        raise BloqueioError(
            "FORMATO_XLSB",
            f"O arquivo '{path.name}' está em formato .xlsb, que não pode ser "
            f"lido de forma confiável fora da plataforma. Abra no Excel e salve "
            f"como .xlsx.")
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    rows = list(wb[sheet].values)
    wb.close()
    if not rows:
        logger.warning(
            f"[Tool 200] aba '{sheet}' de '{path.name}' está vazia. "
            f"A aba 'Centros de Custo' do relatório de exceções ficará vazia."
        )
        return None
    header = [str(h) if h is not None else f"col{i}" for i, h in enumerate(rows[0])]
    df = pd.DataFrame(rows[1:], columns=header)
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
