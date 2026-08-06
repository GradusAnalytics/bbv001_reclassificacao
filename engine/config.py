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

# --- Reclassification bridge (calls reclassificador_predicao_bbv001 via PPR API) --
# aws_access_key_id/aws_secret_access_key seguem o mesmo padrão (nomes em minúsculo)
# que o dispatcher generic_tool do PPR injeta em toda execução tipo_execucao=ecs —
# ver ecs_handler.py legado de reclassificador_predicao/reclassificador_metricas.
AWS_ACCESS_KEY_ID = os.environ.get("aws_access_key_id")
AWS_SECRET_ACCESS_KEY = os.environ.get("aws_secret_access_key")
BBV001_S3_BUCKET = os.environ.get("BBV001_S3_BUCKET", "bucket-ppr")

PPR_API_BASE_URL = os.environ.get("PPR_API_BASE_URL", "https://ppr.gradusanalytics.com.br")
PPR_API_TOKEN = os.environ.get("PPR_API_TOKEN")  # obrigatório para a etapa de reclassificação via API
# Ferramenta dedicada ao BBV001 — já usa arquivos default (modelo + parâmetros) do
# próprio lado dela, então a bridge não precisa mais enviar esses dois arquivos.
RECLASSIFICADOR_PREDICAO_TOOLNAME = "reclassificador_predicao_bbv001"
RECLASSIFIER_BRIDGE_POLL_INTERVAL_S = int(os.environ.get("RECLASSIFIER_BRIDGE_POLL_INTERVAL_S", 10))
RECLASSIFIER_BRIDGE_TIMEOUT_S = int(os.environ.get("RECLASSIFIER_BRIDGE_TIMEOUT_S", 1800))

# --- Inputs (mapped to Alteryx Tool IDs) ---------------------------------
INPUT_FILES = {
    "base_fechamento": {                   # Tool 4
        # Filename overridable so the same pipeline can run any monthly cycle
        # (e.g. BBV001_BASE_FILE="1. Base Fechamento Mar26.xlsx"). Default = Abr26.
        # V4 (dono, 2026-08-04) — lê a 1ª aba do arquivo (índice 0), sem checar nome
        # (antes exigia a aba 'Base' exata). Ver io_utils._read_excel_primeira_aba.
        # Não há mais chave 'sheet' aqui.
        "path": INPUT_DIR / os.environ.get("BBV001_BASE_FILE", "Base Fechamento Abr26.xlsx"),
    },
    "depara_custo": {                       # Tool 10
        # V4 (dono, 2026-08-04) — idem base_fechamento: 1ª aba, sem nome fixo (antes
        # exigia 'Planilha1' exata).
        "path": INPUT_DIR / "DeXPara_Custo_Gradus.xlsx",
    },
    "cadastros_auxiliares": {               # Tools 47, 48, 61, 200, 86/88
        # V4 (dono, 2026-08-04) — substitui os 4 campos de cadastro que existiam
        # separados (classe_valor_conta, estrutura_contas, estrutura_entidades_cc,
        # depara_grupos). 1 arquivo, 5 abas OBRIGATÓRIAS, lidas por NOME — sem
        # fallback de posição (io_utils.valida_cadastros_auxiliares aborta com
        # BloqueioError ABA_AUXILIAR_FALTANDO se faltar qualquer uma). O fallback
        # "cai na 1ª aba" que depara_grupos/estrutura_entidades_cc tinham como
        # arquivos avulsos (v3.1/v3.3) foi removido: dentro de 1 arquivo com 5
        # abas, cair na 1ª aba por nome não encontrado leria dados de OUTRO
        # cadastro em silêncio.
        #
        # Renomes de aba vs a v3 (pedido do time de analytics + achado de sessão):
        #   'Base'     -> 'Allowlist'  (Tool 47) — a aba é uma allowlist de pares
        #                 [Classe de Valor, Conta Contábil] válidos, não uma "base".
        #   'Unico CV' -> 'Arbitragem' (Tool 48) — nome que o próprio arquivo do
        #                 cliente já usa no banner interno da aba, e que bate com o
        #                 Tipo "Arbitrado classe" que a ferramenta produz.
        #
        # O cadastro de Centro de Custo (Estrutura completa de Entidades) deixa de
        # ser opcional — agora é 1 das 5 abas obrigatórias, igual às outras.
        "path": INPUT_DIR / "cadastros_auxiliares.xlsx",
        "sheet_allowlist":    "Allowlist",
        "sheet_arbitragem":   "Arbitragem",
        "sheet_estrutura":    "Estrutura de contas",
        "sheet_entidades_cc": "Estrutura completa de Entidades",
        # BBV001_DEPARA_GRUPOS_SHEET preservado como override explícito de teste —
        # não é fallback silencioso (que foi removido): é o dev escolhendo outro
        # nome de propósito, não a ferramenta adivinhando.
        "sheet_grupos":       os.environ.get("BBV001_DEPARA_GRUPOS_SHEET", "Grupos Cobrança"),
    },
    "base_reclassificada": {                # Tool 124 (optional — only on 2nd run)
        "path": INPUT_DIR / "base_reclassificada_a2f37.xlsx",
        "sheet": "Sheet1",
    },
}

# --- V2 — colunas mínimas por input (validação pós-leitura; SPEC_V2 §3.10/D1) ------
# Falta de coluna → BloqueioError com mensagem acionável (qual arquivo, qual aba,
# quais colunas), em vez de KeyError críptico no meio da cascata.
REQUIRED_COLUMNS = {
    "base_fechamento": [
        "Codigo Interno", "Valor",  # "Valor do Lancamento" já renomeada na leitura
        "Conta Contabil", "Nome da Conta", "Nome da Classe de Valor", "Classe de Valor",
        "Grupo Acionista", "Grupo Conta", "Historico", "Fornecedor", "Mes",
        "Centro de Custo", "Nome do Centro de Custo",
    ],
    "depara_custo": ["HISTORICO_2", "FORNECEDOR", "FINALIZACAO", "GRUPO", "DATA_BASE",
                     "VALOR_LANCAMENTO"],
    "classe_valor_conta_base": ["Nome classe de valor", "Cód conta contábil", "Número att"],
    "classe_valor_conta_unico_cv": ["Classe de valor", "Como tratar?", "Código conta contábil"],
    "estrutura_contas": ["CONTA CONTÁBIL", "CONTA", "PACOTE"],
    "depara_grupos": ["Grupo", "Conta OM", "Conta Contábil"],
}

# --- V3.3 — aliases de coluna e markers de cabeçalho -------------------------
# O arquivo mestre de cadastros (BBV001-Classe de Valor x Conta Contábil.xlsx,
# 2026-08-03) renomeou 3 colunas da aba Base. Aceitar as duas grafias mantém
# legíveis o mestre E o arquivo anterior (...v1 MU.xlsx), que é o input das
# rodadas de regressão e das medições registradas no FONTE_DA_VERDADE.
# Sentido do dicionário: grafia encontrada no arquivo -> nome canônico do código.
COLUNAS_ALIAS = {
    "classe_valor_conta_base": {
        "Nome da classe de valor": "Nome classe de valor",
        "Cód conta contábil permitida": "Cód conta contábil",
        "Cód Classe de Valor": "Número att",
    },
}

# Marcadores que identificam a linha do cabeçalho real (as abas trazem linhas de
# título acima da tabela). Qualquer um da lista serve; a ordem não importa.
HEADER_MARKERS = {
    "classe_valor_conta_base": ["Nome classe de valor", "Nome da classe de valor"],
    "classe_valor_conta_unico_cv": ["Classe de valor"],
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
# V2/R4: NÃO é mais consumido pelo pipeline — o de-para vem do input obrigatório
# `depara_grupos` (INPUT_FILES acima). Mantido SOMENTE como seed do template do cadastro
# (aux_files/gera_seed_depara_grupos.py) e referência histórica da paridade Alteryx.
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

# V2/R9-R10 (dono 2026-07-20/21) — família de prefixo de Conta Contábil, usada para
# decidir se a Conta destino de um lançamento "cruzou" a família da Conta Contábil
# de origem. 817 e 819 são a MESMA família (podem cruzar entre si sem violação);
# 818 é família separada (não pode cruzar com 817/819); o 1º dígito 8 separa a
# família maior (8 cruzando com não-8 também é violação, exceto quando já pega
# pelo caso 817/818/819). ÚNICA FONTE da regra — antes da refatoração de v3.1
# (2026-07-30) este dicionário estava copiado literalmente em duas funções de
# transforms.py (autocorrigir_conta_om/R10 e marcar_bloqueio_prefixo/R9), com
# risco de as duas discordarem em silêncio se alguém mudasse uma cópia e
# esquecesse a outra. Consumido por transforms.mascaras_cruzamento_familia().
FAMILIA_PREFIXO_CONTA = {"817": "817/819", "819": "817/819", "818": "818"}

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
    # V3.2 (dono, 2026-07-30) — coluna de ORIGEM do lançamento, que já atravessava a
    # cascata e era descartada no select final. Posição logo após as 15 originais,
    # escolhida pelo dono: o Matrix lê as primeiras N colunas cadastradas e ignora o
    # resto à direita, então esta só chega ao Matrix se o cadastro crescer para 16.
    "Veiculo Legal",
    # V3 (2026-07-27) — as 4 colunas de auditoria por lançamento. Ficam à direita
    # de propósito: o Matrix não as lê, e não precisa.
    "ID Lançamento",
    # V3.2 (dono, 2026-07-30) — o mecanismo que classificou o lançamento. Mesmos
    # valores da tabela de auditoria, incluindo "Não classificado" para quem voltou
    # sem passar pela cascata. Posição entre ID Lançamento e Status, pedida pelo dono.
    "Tipo",
    "Status",
    "Motivo",
    "Ação Recomendada",
]

# =========================================================================
# V3 (2026-07-27) — catálogo de razões da coluna Status/Motivo/Ação Recomendada
# Spec: docs/superpowers/specs/2026-07-27-v3-base-final-completa-design.md §5
# =========================================================================
STATUS_OK = "OK"
STATUS_CADASTRO_PENDENTE = "CADASTRO_PENDENTE"
# V3.3 (dono, 2026-08-03) — parte o antigo CADASTRO_PENDENTE em dois. Aqui ficam os
# códigos em que falta um cadastro de DIMENSÃO que a carga no Matrix precisa (conta
# contábil, centro de custo, grupo de cobrança). NÃO é sinônimo de "carrega conta de
# origem": o CC_NAO_CADASTRADO mantém a conta calculada — o que falta é a dimensão CC.
# O ganho é a advertência D4 voltar a ser enunciável por Status em vez de por Motivo.
# (O Matrix não lê a coluna Status — é filtro para quem revisa antes de carregar.)
STATUS_CADASTRO_BLOQUEANTE = "CADASTRO_BLOQUEANTE"
STATUS_DESTINO_SUSPEITO = "DESTINO_SUSPEITO"
STATUS_DADO_INVALIDO = "DADO_INVALIDO"
STATUS_FORA_DE_ESCOPO = "FORA_DE_ESCOPO"
# V3.1 (2026-07-29) — defeito do MOTOR, não do input: o lançamento sumiu da cascata
# sem que nenhuma etapa assumisse a autoria. Volta à base com a conta de origem para
# que o valor se conserve e o mês possa ser carregado, marcado para auditoria nossa.
STATUS_FALHA_INTERNA = "FALHA_INTERNA"

# Do mais grave ao menos grave. FORA_DE_ESCOPO continua em 1º porque é ele que define
# a ABA da saída (o split de build_final_consolidated compara com ele) — trocar essa
# posição mudaria em silêncio para qual aba a linha vai. FALHA_INTERNA vem logo depois,
# vencendo todos os demais. CADASTRO_BLOQUEANTE entrou na v3.3 entre FALHA_INTERNA e
# CADASTRO_PENDENTE.
STATUS_PRECEDENCIA = [
    STATUS_FORA_DE_ESCOPO,
    STATUS_FALHA_INTERNA,
    # V3.3 — acima do CADASTRO_PENDENTE: quando um lançamento tem os dois, o que
    # importa para quem carrega é o cadastro de dimensão que falta.
    STATUS_CADASTRO_BLOQUEANTE,
    STATUS_CADASTRO_PENDENTE,
    STATUS_DESTINO_SUSPEITO,
    STATUS_DADO_INVALIDO,
    STATUS_OK,
]

# V3.1 — os ÚNICOS códigos cuja presença EXPLICA um lançamento ter saído da cascata.
# Todas as outras razões do catálogo são informativas: são registradas em linhas que
# SEGUEM no fluxo. Por isso a rede de segurança da reconciliação só pode isentar estes
# três — isentar qualquer `Status != OK` deixava ~10 mil lançamentos fora da rede
# (achado da revisão final da v3, 2026-07-29).
CODIGOS_QUE_REMOVEM_LANCAMENTO = frozenset({
    "CV_EXCLUIDA",            # T63 — filtro de Classe de Valor excluída
    "CONTA_NAO_CADASTRADA",   # T62 — conta sem match na Estrutura de Contas
    "GRUPO_NAO_CADASTRADO",   # T88 — GRUPO de Cobrança sem cadastro
})

# código → (Status, Ação Recomendada padrão).
# Códigos de prefixo (R9) têm ação POR LANÇAMENTO gerada em marcar_bloqueio_prefixo;
# o texto aqui é o fallback caso a ação específica não venha.
CATALOGO_RAZOES = {
    "CV_EXCLUIDA": (
        STATUS_FORA_DE_ESCOPO,
        "Classe de Valor excluída do escopo por configuração; nenhuma ação necessária."),
    "CONTA_NAO_CADASTRADA": (
        STATUS_CADASTRO_BLOQUEANTE,
        "Cadastre esta Conta Contábil na Estrutura de Contas e re-execute — sem isso o "
        "lançamento fica sem Pacote no Matrix."),
    "GRUPO_NAO_CADASTRADO": (
        STATUS_CADASTRO_BLOQUEANTE,
        "Cadastre o GRUPO de Cobrança no de-para de grupos (colunas Grupo · Conta OM · "
        "Conta Contábil) e re-execute."),
    "CC_NAO_CADASTRADO": (
        STATUS_CADASTRO_BLOQUEANTE,
        "Cadastre o Centro de Custo na Estrutura de Entidades×CC. A classificação deste "
        "lançamento NÃO foi afetada (a Conta destino é a calculada), mas a carga no "
        "Matrix depende de o Centro de Custo existir lá."),
    "CLASSE_NAO_CADASTRADA": (
        STATUS_CADASTRO_PENDENTE,
        "Cadastre a Classe de Valor nas abas Base e Unico CV do Classe×Conta."),
    "CLASSE_RENOMEADA": (
        STATUS_CADASTRO_PENDENTE,
        "O nome da Classe de Valor na base não bate com o cadastrado para esse código — "
        "alinhe o cadastro Classe×Conta com a base."),
    "CLASSE_SEM_UNICO_CV": (
        STATUS_CADASTRO_PENDENTE,
        "Cadastre a Classe de Valor na aba Unico CV do Classe×Conta."),
    "PREFIXO_818_CRUZADO": (
        STATUS_DESTINO_SUSPEITO,
        "A Conta destino cruza a família de prefixo 818↔817/819 da conta de origem — "
        "cadastre uma conta da família correta na Conta OM."),
    "PREFIXO_FAMILIA_8_CRUZADO": (
        STATUS_DESTINO_SUSPEITO,
        "A Conta destino cruza a família de prefixo 8↔não-8 da conta de origem — "
        "cadastre uma conta da família correta na Conta OM."),
    "RECLASSIFICADOR_FALLBACK": (
        STATUS_DESTINO_SUSPEITO,
        "O Reclassificador não cobriu este lançamento — manteve a conta de origem como "
        "destino. Revise se a conta está correta."),
    "DEPARA_CONFLITO_RESOLVIDO_POR_VALOR": (
        STATUS_DESTINO_SUSPEITO,
        "O De-Para tinha regras divergentes na mesma competência — aplicada a de maior "
        "SOMA(|Valor|). Revise o cadastro."),
    "DEPARA_CONFLITO_MESMA_COMPETENCIA": (
        STATUS_DESTINO_SUSPEITO,
        "O De-Para tem regras divergentes com empate de valor na mesma competência — "
        "aplicada a primeira em ordem de leitura. Corrija o cadastro."),
    "MES_INVALIDO": (
        STATUS_DADO_INVALIDO,
        "A coluna 'Mes' deste lançamento não é uma data válida — o Matrix receberá a "
        "linha sem competência. Corrija a Base de Fechamento."),
    "PREFIXO_CORRIGIDO_AUTOMATICAMENTE": (
        STATUS_OK,
        "A Conta destino foi corrigida automaticamente para uma conta compatível na "
        "mesma Conta OM — nenhuma ação necessária."),
    "PERDA_NAO_EXPLICADA": (
        STATUS_FALHA_INTERNA,
        "Este lançamento sumiu do tratamento sem que nenhuma etapa registrasse o "
        "motivo — é defeito da própria ferramenta, não do arquivo enviado. Ele voltou "
        "à base com a conta de origem para que o valor não se perca. NÃO carregue esta "
        "linha e avise o time responsável pela ferramenta, informando o ID Lançamento."),
}
