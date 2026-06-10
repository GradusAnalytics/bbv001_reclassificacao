"""
config.py — Paths and constants for the BBV001 reclassification pipeline.

All paths are relative to INPUT_DIR / OUTPUT_DIR. Override via environment
variables BBV001_INPUT_DIR and BBV001_OUTPUT_DIR if needed.
"""
import os
from pathlib import Path

# --- Directories ---------------------------------------------------------
INPUT_DIR = Path(os.environ.get("BBV001_INPUT_DIR", "./inputs"))
OUTPUT_DIR = Path(os.environ.get("BBV001_OUTPUT_DIR", "./outputs"))

# --- Inputs (mapped to Alteryx Tool IDs) ---------------------------------
INPUT_FILES = {
    "base_fechamento": {                   # Tool 4
        # Filename/sheet overridable so the same pipeline can run any monthly cycle
        # (e.g. BBV001_BASE_FILE="1. Base Fechamento Mar26.xlsx"). Default = Abr26.
        "path": INPUT_DIR / os.environ.get("BBV001_BASE_FILE", "Base Fechamento Abr26.xlsx"),
        "sheet": os.environ.get("BBV001_BASE_SHEET", "Base"),
    },
    "depara_custo": {                       # Tool 10
        "path": INPUT_DIR / "DeXPara_Custo_Gradus.xlsx",
        "sheet": "Planilha1",
    },
    "classe_valor_conta": {                 # Tool 47 & 48 (same file, different sheets)
        "path": INPUT_DIR / "BBV001-260509-Classe de Valor x Conta Contábil-v1 MU.xlsx",
        "sheet_base": "Base",               # Tool 47
        "sheet_unico_cv": "Unico CV",       # Tool 48
    },
    "estrutura_contas": {                   # Tool 61
        "path": INPUT_DIR / "20260509 - 12h30 - Estrutura de contas.xlsx",
        "sheet": "Estrutura de contas",
    },
    "base_reclassificada": {                # Tool 124 (optional — only on 2nd run)
        "path": INPUT_DIR / "base_reclassificada_a2f37.xlsx",
        "sheet": "Sheet1",
    },
    "estrutura_entidades_cc": {             # NOVO — cadastro de Centros de Custo (Tool 200)
        # Cadastro de Entidades x Centros de Custo. Enviar em .xlsx (pyxlsb não lê
        # este arquivo de forma confiável). Opcional: sem ele, a aba de CC sai vazia.
        "path": INPUT_DIR / "estrutura_entidades_cc.xlsx",
        "sheet": "Estrutura completa de Entidades",
    },
}

# --- Cadastros para o relatório de exceções (Tool 200) -------------------
# Centro de custo: coluna do código no cadastro de Entidades x CC
CC_CADASTRO_CODE_COL = "Cód. centro de custo"
# Base de fechamento: colunas de código e descrição do CC
BASE_CC_CODE_COL = "Centro de Custo"
BASE_CC_DESC_COL = "Nome do Centro de Custo"
# Base de fechamento: colunas de código e descrição da conta contábil
BASE_CONTA_CODE_COL = "Conta Contabil"
BASE_CONTA_DESC_COL = "Nome da Conta"

# --- Outputs --------------------------------------------------------------
OUTPUT_RECLASSIFIER = OUTPUT_DIR / "BBV001-Base_para_reclassificador.xlsx"   # Tool 123
OUTPUT_FINAL_CONSOLIDATED = OUTPUT_DIR / "BBV001-Base_consolidada_final.xlsx"  # Tool 114 (new — not in Alteryx)

# --- Business constants extracted from the workflow ----------------------

# Tool 63 filter — Value Classes excluded from processing
EXCLUDED_VALUE_CLASSES = ["Cost Sharing", "Outros Custos não Operacionais"]

# Tool 119 — hardcoded destination account for Consultoria records
CONSULTORIA_CONTA_DESTINO = "8176300000000000031000000"

# Tool 55 filter — Como tratar? values that should be excluded from the normal Unico CV path
SPECIAL_TREATMENT_VALUES = ["Utilizar Reclassificador", "Consultoria"]

# Tool 34 — filters De-Para to DATA_BASE >= 2026-01-01, BUT this feeds only a side
# validation branch (Tool 30 → Browse 46), NOT the main classification path: Tool 14
# (the De-Para summarize used for the Cobrança join) reads the UNFILTERED De-Para
# straight from Tool 10 (XML connections 3065 and 3077). So this constant is vestigial
# and intentionally not applied in the pipeline. Kept only for reference.
DEPARA_MIN_DATE = "2026-01-01"  # unused — see note above

# Tool 86 — manual Grupo → Conta OM / Conta Contábil overrides (TextInput hardcoded in Alteryx)
MANUAL_GROUP_OVERRIDES = [
    {"Grupo": "REMUNERACAO",          "Conta OM": "Assessorias de Cobrança",            "Conta Contábil": "8199900000012000001000000"},
    {"Grupo": "AJUIZAMENTO",          "Conta OM": "Emolumentos e Custas de Ações de Cobrança", "Conta Contábil": "8179900000000000011000000"},
    {"Grupo": "AJUIZAMENTO ADICIONAL","Conta OM": "Emolumentos e Custas de Ações de Cobrança", "Conta Contábil": "8179900000000000011000000"},
    {"Grupo": "RETOMADOS",            "Conta OM": "Custos com Garantias Retomadas",     "Conta Contábil": "8179900000000000020000000"},
    {"Grupo": "HONORARIO",            "Conta OM": "Assessorias de Cobrança",            "Conta Contábil": "8199900000012000001000000"},
    {"Grupo": "TECNOLOGIA_PACOTE",    "Conta OM": "TI e Telecom",                       "Conta Contábil": "8173900000000000002000000"},
    {"Grupo": "SERVICOS_COBRANCA",    "Conta OM": "Cobrança Extrajudicial",             "Conta Contábil": "8199900000012000006000000"},
    {"Grupo": "RETOMADOS ADICIONAL",  "Conta OM": "Custos com Garantias Retomadas",     "Conta Contábil": "8179900000000000020000000"},
    {"Grupo": "DEPOSITO_JUDICIAL",    "Conta OM": "Emolumentos e Custas de Ações de Cobrança", "Conta Contábil": "8179900000000000011000000"},
    {"Grupo": "HONORARIOS ADICIONAL", "Conta OM": "Assessorias de Cobrança",            "Conta Contábil": "8199900000012000001000000"},
    {"Grupo": "RESSARCIMENTO",        "Conta OM": "Emolumentos e Custas de Ações de Cobrança", "Conta Contábil": "7193000000001000003000000"},
]

# --- Output schema for Tool 123 (reclassifier input base) ----------------
# Columns kept in Tool 122 Select (after dropping the listed ones)
RECLASSIFIER_OUTPUT_SCHEMA = [
    "Codigo Interno",
    "Grupo Acionista",
    "Grupo Conta",
    "Conta Contabil",
    "Valor",
    "Centro de Custo",
    "Classe de Valor",
    "Conta destino",
    "Como tratar?",          # only this one stays from the dropped list per Tool 122
    "FINALIZACAO",
    "GRUPO",
    "Fornecedor Histórico",  # generated in Tool 121
]

# --- Output schema for Tool 114 (final consolidated base for matrix upload) ---
# Exact field list + order, matched against the real Alteryx output
# (BBV001-...-Carga Matrix...xlsx), which is the Tool 114 Select (XML lines 1592-1625):
#   - "DateTime_Out": Tool 115 writes the dd/MM/yyyy date into a NEW field with this
#     name (not "Mes"); Tool 114 exports it verbatim.
#   - "CV Matrix": Tool 111's formula names it " CV Matrix" (leading space), but the
#     Alteryx Excel writer drops the space — the real output header has no space.
FINAL_OUTPUT_SCHEMA = [
    "Índice ERP",
    "DateTime_Out",
    "Grupo Acionista",
    "GC Matrix",
    "Centro de Custo",
    "Conta destino",
    "Valor",
    "Moeda",
    "CV Matrix",
    "Nome da Classe de Valor",
    "Historico",
    "Número NF",
    "Fornecedor",
    "Conta Contabil",
    "Nome da Conta",
]
