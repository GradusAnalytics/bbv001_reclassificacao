"""
transforms.py — One function per logical stage of the BBV001 workflow.

Each function references the original Alteryx Tool IDs it implements,
so you can cross-check against the .yxmd in Alteryx Designer.

Stages follow the .yxmd topology:
    1. Preprocess        — Tools 63, 62, 87
    2. Cobrança split    — Tools 5, 21, 24, 14, 25, 26, 11, 88, 132, 101, 99
    3. Match conta×cc    — Tools 44, 49, 93, 102, 96
    4. Match classe OM   — Tools 59, 60, 94, 103, 97
    5. Special routing   — Tools 55, 79, 117, 119, 120
    6. Arbitrado classe  — Tools 56, 104, 98
    7. Reclassificador   — Tools 118, 121, 122, 123, 126, 133, 134, 127, 128
    8. Final consolidate — Tools 95, 110, 113, 111, 115, 114
"""
import logging
from typing import Tuple, Optional

import numpy as np
import pandas as pd

from config import (
    EXCLUDED_VALUE_CLASSES,
    SPECIAL_TREATMENT_VALUES,
    CONSULTORIA_CONTA_DESTINO,
    RECLASSIFIER_OUTPUT_SCHEMA,
    FINAL_OUTPUT_SCHEMA,
)
from helpers import (
    decompose_unicode_for_match,
    remove_whitespace,
    getright,
    alteryx_join,
    log_step,
)

logger = logging.getLogger(__name__)


# =========================================================================
# STAGE 1 — PREPROCESS
# =========================================================================
def preprocess_base(df_base: pd.DataFrame, df_estrutura: pd.DataFrame):
    """
    Tools 63 (filter excluded CVs) + 62 (join with Estrutura de Contas).

    Retorna (j_out, l_out):
      j_out — base enriquecida (Conta GCUT / Pacote GCUT), segue para a cascata.
      l_out — contas da base SEM match na Estrutura (descartadas) → relatório de exceções.
    """
    # Tool 63 — filter excluded value classes
    mask = ~df_base["Nome da Classe de Valor"].isin(EXCLUDED_VALUE_CLASSES)
    df = df_base[mask].copy()
    log_step(logger, "63", "Filter excluded Value Classes", df)

    # Tool 62 — left join with Estrutura de Contas
    # Keys: [Conta Contabil] = [CONTA CONTÁBIL]. We use INNER on J side
    # because the L output (unmatched) only feeds a validation Summarize (Tool 129).
    l_out, j_out, _ = alteryx_join(
        df, df_estrutura,
        left_on="Conta Contabil",
        right_on="CONTA CONTÁBIL",
    )
    if len(l_out) > 0:
        logger.warning(
            f"[Tool 62] {len(l_out)} rows in base have no match in Estrutura de Contas — "
            f"they are dropped, matching Alteryx behavior. Unique Contas: "
            f"{l_out['Conta Contabil'].nunique()}"
        )

    # Tool 62 SelectConfiguration (XML lines 460-472): keep the base columns (*Unknown),
    # rename Right_"CONTA" → "Conta GCUT" and Right_"PACOTE" → "Pacote GCUT", and DROP every
    # other Estrutura column. Dropping is load-bearing: Estrutura's own "GRUPO" would
    # otherwise collide with the De-Para "GRUPO" used by the Tool 88 join.
    j_out = j_out.rename(columns={"CONTA": "Conta GCUT", "PACOTE": "Pacote GCUT"})
    estrutura_drop = [
        "CONTA CONTÁBIL", "DESCRIÇÃO DA CONTA CONTÁBIL",
        "TIPO DE CONTA (DESDOBRAMENTO)", "TIPO DE CONTA (ACOMPANHAMENTO)",
        "GRUPO", "O QUE LANÇAR", "O QUE NÃO LANÇAR",
        "Classificação 1 (Conta contábil)", "Classificação 2 (Conta contábil)",
    ]
    j_out = j_out.drop(columns=[c for c in estrutura_drop if c in j_out.columns])
    log_step(logger, "62", "Join Base × Estrutura (matched)", j_out)
    # j_out segue para a cascata EXATAMENTE como antes (paridade preservada);
    # l_out (contas sem match) vai só para o relatório de exceções.
    return j_out, l_out


# =========================================================================
# STAGE 2 — COBRANÇA SPLIT + DE-PARA ENRICHMENT
# =========================================================================
def split_cobranca(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Tool 5 — splits records on whether 'Grupo Acionista' contains 'Cobrança'.

    Returns:
        nao_cobranca : pd.DataFrame   (Tool 5 True output)
        cobranca     : pd.DataFrame   (Tool 5 False output)
    """
    contains_cobranca = df["Grupo Acionista"].fillna("").str.contains("Cobrança", na=False)
    cobranca = df[contains_cobranca].copy()
    nao_cobranca = df[~contains_cobranca].copy()
    log_step(logger, "5T", "Não-Cobrança records", nao_cobranca)
    log_step(logger, "5F", "Cobrança records", cobranca)
    return nao_cobranca, cobranca


def prepare_cobranca_keys(df_cobranca: pd.DataFrame) -> pd.DataFrame:
    """
    Tools 21 + 24 — derive HISTORICO2 and normalize Nome do Fornecedor
    using DecomposeUnicodeForMatch.

    HISTORICO2     = DecomposeUnicodeForMatch(Getright([Historico], "-"))
    Nome do Fornecedor = DecomposeUnicodeForMatch( if [Fornecedor] == "0" then "" else [Fornecedor] )
    """
    df = df_cobranca.copy()
    # Tool 21: HISTORICO2 = DecomposeUnicodeForMatch(Getright([Historico], "-"))
    # Tool 24: Data Cleansing removes ALL whitespace from HISTORICO2 (so it lines up with
    # the De-Para HISTORICO_2, which has no spaces). Nome do Fornecedor is NOT cleansed.
    df["HISTORICO2"] = df["Historico"].apply(
        lambda x: remove_whitespace(decompose_unicode_for_match(getright(x, "-")))
    )
    df["Nome do Fornecedor"] = df["Fornecedor"].apply(
        lambda x: "" if str(x) == "0" else decompose_unicode_for_match(x)
    )
    log_step(logger, "21+24", "Cobrança: compute HISTORICO2 + Nome do Fornecedor", df)
    return df


def prepare_depara_keys(df_depara: pd.DataFrame) -> pd.DataFrame:
    """
    Tools 14 + 25 + 26 — summarize De-Para and normalize FORNECEDOR for join.

    Tool 14: GroupBy on [HISTORICO_2, FORNECEDOR, FINALIZACAO, GRUPO] (de-dup)
    Tool 25: FORNECEDOR = DecomposeUnicodeForMatch( IsNull → "" else [FORNECEDOR] )
    Tool 26: data cleansing (handled inside DecomposeUnicodeForMatch already)
    """
    keys = ["HISTORICO_2", "FORNECEDOR", "FINALIZACAO", "GRUPO"]
    df = df_depara[keys].drop_duplicates().copy()
    log_step(logger, "14", "De-Para summarized (dedup keys)", df)

    # Tool 25 — both FORNECEDOR and HISTORICO_2 pass through DecomposeUnicodeForMatch
    # (confirmed in the .yxmd, line 2590).
    df["FORNECEDOR"] = df["FORNECEDOR"].apply(
        lambda x: decompose_unicode_for_match(x) if pd.notna(x) else ""
    )
    # Tool 26 Data Cleansing — HISTORICO_2 has all whitespace removed (mirrors the
    # cleanse applied to the Cobrança HISTORICO2 in Tool 24, so the Tool 11 join lines up).
    # FORNECEDOR keeps its internal spaces (matches the un-cleansed Nome do Fornecedor).
    df["HISTORICO_2"] = df["HISTORICO_2"].apply(
        lambda x: remove_whitespace(decompose_unicode_for_match(x))
    )
    log_step(logger, "25+26", "De-Para: normalize FORNECEDOR + HISTORICO_2", df)
    return df


def enrich_cobranca_with_depara(
    df_cobranca_keys: pd.DataFrame,
    df_depara_norm: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Tool 11 — join Cobrança (with normalized keys) × normalized De-Para.

    Keys: Left [Nome do Fornecedor, HISTORICO2] = Right [FORNECEDOR, HISTORICO_2]

    Returns:
        unmatched_cobranca (Tool 11 L) — go back to the main cascade via Tool 44
        matched_cobranca   (Tool 11 J) — continue to Tool 88 (manual overrides)
    """
    l_out, j_out, _ = alteryx_join(
        df_cobranca_keys, df_depara_norm,
        left_on=["Nome do Fornecedor", "HISTORICO2"],
        right_on=["FORNECEDOR", "HISTORICO_2"],
    )
    log_step(logger, "11L", "Cobrança unmatched in De-Para", l_out)
    log_step(logger, "11J", "Cobrança matched in De-Para", j_out)
    return l_out, j_out


def apply_manual_group_override(
    df_cobranca_matched: pd.DataFrame,
    df_manual: pd.DataFrame,
) -> pd.DataFrame:
    """
    Tool 88 — inner join Cobrança-matched × manual Grupo overrides.

    IMPORTANT: only the J output is kept downstream. Cobrança records whose GRUPO
    is not in the 11 manual values are SILENTLY DROPPED — replicating the
    Alteryx behavior. We log a warning if any are dropped so you can audit.

    Conta destino for the Cobrança path is created HERE (not in a later formula):
    Tool 88's SelectConfiguration renames the override's Right_"Conta Contábil"
    → "Conta destino" and drops Right_"Grupo" / Right_"Conta OM" (XML line 2755).
    """
    l_out, j_out, _ = alteryx_join(
        df_cobranca_matched, df_manual,
        left_on="GRUPO",
        right_on="Grupo",
    )
    if len(l_out) > 0:
        dropped_groups = sorted(l_out["GRUPO"].dropna().unique().tolist())
        logger.warning(
            f"[Tool 88] {len(l_out)} Cobrança records dropped — GRUPO not in manual overrides. "
            f"Distinct dropped GRUPOs: {dropped_groups}"
        )

    # Tool 88 select: rename override's "Conta Contábil" → "Conta destino", drop the
    # other right-side cols (the right key "Grupo" and "Conta OM").
    j_out = j_out.rename(columns={"Conta Contábil": "Conta destino"})
    j_out = j_out.drop(columns=[c for c in ("Grupo", "Conta OM") if c in j_out.columns])
    log_step(logger, "88", "Cobrança × manual overrides (matched, Conta destino set)", j_out)
    return j_out


def consolidate_cobranca(df_cobranca_overridden: pd.DataFrame) -> pd.DataFrame:
    """
    Tools 132 + 101 + 99.

    Tool 132: dedup by all 30 columns listed in the workflow (acts as DISTINCT).
              "Conta destino" is already present at this point — it was created in
              Tool 88 (apply_manual_group_override), matching the .yxmd GroupBy list.
    Tool 101: add Tipo = "Cobrança".
    Tool 99 : drop helper columns (Conta GCUT, Pacote GCUT, HISTORICO2,
              Nome do Fornecedor, FINALIZACAO, GRUPO).
    """
    dedup_cols = [
        "Codigo Interno", "Mes", "Data", "Grupo Acionista", "Plano de Contas Original",
        "Grupo Conta", "Conta Contabil", "Conta BRGaap", "Nome da Conta", "Valor",
        "Historico", "Fornecedor", "Veiculo Legal", "Centro de Custo",
        "Nome do Centro de Custo", "Diretoria", "Area", "Classe de Valor",
        "Nome da Classe de Valor", "Ajuste Manual", "Usuario Lancador",
        "Classificacao", "Cont.Doc", "Conta GCUT", "Pacote GCUT", "HISTORICO2",
        "Nome do Fornecedor", "FINALIZACAO", "GRUPO", "Conta destino",
    ]
    existing = [c for c in dedup_cols if c in df_cobranca_overridden.columns]
    df = df_cobranca_overridden[existing].drop_duplicates().copy()
    log_step(logger, "132", "Cobrança consolidated (dedup incl. Conta destino)", df)

    df["Tipo"] = "Cobrança"   # Tool 101
    log_step(logger, "101", "Cobrança: assign Tipo", df)

    # Tool 99 — drop helper columns
    drop_cols = ["Conta GCUT", "Pacote GCUT", "HISTORICO2",
                 "Nome do Fornecedor", "FINALIZACAO", "GRUPO"]
    df = df.drop(columns=[c for c in drop_cols if c in df.columns])
    log_step(logger, "99", "Cobrança: drop helper columns", df)
    return df


# =========================================================================
# STAGE 3 — MATCH CONTA × CLASSE
# =========================================================================
def match_conta_classe(
    df_main: pd.DataFrame,
    df_classe_conta: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Tool 49 — join main cascade with Classe × Conta Contábil mapping.
    Keys: Left [Nome da Classe de Valor, Conta Contabil] = Right [Nome classe de valor, Cód conta contábil]

    Returns:
        unmatched (Tool 49 L)  → goes to next stage (Match classe conta OM)
        matched   (Tool 49 J)  → labeled 'Match conta x classe' and sent to final Union
    """
    l_out, j_out, _ = alteryx_join(
        df_main, df_classe_conta,
        left_on=["Nome da Classe de Valor", "Conta Contabil"],
        right_on=["Nome classe de valor", "Cód conta contábil"],
    )
    log_step(logger, "49L", "Unmatched in Classe × Conta", l_out)
    log_step(logger, "49J", "Matched in Classe × Conta", j_out)
    return l_out, j_out


def finalize_match_conta_classe(df_matched: pd.DataFrame) -> pd.DataFrame:
    """Tools 93 + 102 + 96 — assign Conta destino, Tipo, drop helpers."""
    df = df_matched.copy()
    df["Conta destino"] = df["Conta Contabil"]    # Tool 93
    df["Tipo"] = "Match conta x classe"           # Tool 102
    drop_cols = ["Conta GCUT", "Pacote GCUT", "HISTORICO2", "Nome do Fornecedor"]
    df = df.drop(columns=[c for c in drop_cols if c in df.columns])
    log_step(logger, "93+102+96", "Finalize Match conta × classe", df)
    return df


# =========================================================================
# STAGE 4 — MATCH CLASSE × CONTA OM
# =========================================================================
def summarize_classe_om(df_classe_conta: pd.DataFrame) -> pd.DataFrame:
    """Tool 59 — dedup df_classe_conta on [Nome classe de valor, Conta OM]."""
    df = df_classe_conta[["Nome classe de valor", "Conta OM"]].drop_duplicates().copy()
    log_step(logger, "59", "Summarized Classe × Conta OM", df)
    return df


def match_classe_conta_om(
    df_unmatched: pd.DataFrame,
    df_classe_om: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Tool 60 — Left [Nome da Classe de Valor, Conta GCUT] = Right [Nome classe de valor, Conta OM]
    """
    l_out, j_out, _ = alteryx_join(
        df_unmatched, df_classe_om,
        left_on=["Nome da Classe de Valor", "Conta GCUT"],
        right_on=["Nome classe de valor", "Conta OM"],
    )
    log_step(logger, "60L", "Unmatched in Classe × Conta OM", l_out)
    log_step(logger, "60J", "Matched in Classe × Conta OM", j_out)
    return l_out, j_out


def finalize_match_classe_conta_om(df_matched: pd.DataFrame) -> pd.DataFrame:
    """Tools 94 + 103 + 97."""
    df = df_matched.copy()
    df["Conta destino"] = df["Conta Contabil"]    # Tool 94
    df["Tipo"] = "Match classe conta OM"          # Tool 103
    drop_cols = ["Conta GCUT", "Pacote GCUT", "HISTORICO2", "Nome do Fornecedor"]
    df = df.drop(columns=[c for c in drop_cols if c in df.columns])
    log_step(logger, "94+103+97", "Finalize Match classe conta OM", df)
    return df


# =========================================================================
# STAGE 5 — SPECIAL ROUTING (Reclassificador / Consultoria) + ARBITRADO
# =========================================================================
def split_unico_cv(df_unico_cv: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Tool 55 — split Unico CV by 'Como tratar?'.

    Returns:
        normal_unico_cv  (T) → used for Arbitrado match (Tool 56)
        special_unico_cv (F) → used for Reclassificador/Consultoria routing (Tool 79)
    """
    mask = ~df_unico_cv["Como tratar?"].isin(SPECIAL_TREATMENT_VALUES)
    normal = df_unico_cv[mask].copy()
    special = df_unico_cv[~mask].copy()
    log_step(logger, "55T", "Unico CV — normal", normal)
    log_step(logger, "55F", "Unico CV — special (Reclass/Consult)", special)
    return normal, special


def route_special_classes(
    df_unmatched_after_om: pd.DataFrame,
    df_special_unico_cv: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Tool 79 — Left [Nome da Classe de Valor] = Right [Classe de valor]
    against the SPECIAL Unico CV table.

    Returns:
        unmatched_special (Tool 79 L) — go to Tool 56 (Arbitrado normal path)
        matched_special   (Tool 79 J) — go to Tool 117 (split Consultoria vs Reclassificador)
    """
    l_out, j_out, _ = alteryx_join(
        df_unmatched_after_om, df_special_unico_cv,
        left_on="Nome da Classe de Valor",
        right_on="Classe de valor",
    )
    log_step(logger, "79L", "Unmatched in special Unico CV", l_out)
    log_step(logger, "79J", "Matched in special Unico CV", j_out)
    return l_out, j_out


def split_consultoria(df_matched_special: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Tool 117 — filter [Como tratar?] != 'Consultoria'.

    Returns:
        to_reclassificador (T) → goes to Tool 118 Union
        consultoria        (F) → labeled 'Consultorias' and sent to final Union
    """
    is_consultoria = df_matched_special["Como tratar?"] == "Consultoria"
    consultoria = df_matched_special[is_consultoria].copy()
    to_reclass = df_matched_special[~is_consultoria].copy()
    log_step(logger, "117T", "Going to Reclassificador (not Consultoria)", to_reclass)
    log_step(logger, "117F", "Consultorias", consultoria)
    return to_reclass, consultoria


def finalize_consultoria(df_consultoria: pd.DataFrame) -> pd.DataFrame:
    """Tools 119 + 120 — hardcoded Conta destino + Tipo = 'Consultorias'."""
    df = df_consultoria.copy()
    df["Conta destino"] = CONSULTORIA_CONTA_DESTINO  # Tool 119
    df["Tipo"] = "Consultorias"                       # Tool 119
    drop_cols = ["Conta GCUT", "Pacote GCUT", "HISTORICO2", "Nome do Fornecedor", "F1"]
    df = df.drop(columns=[c for c in drop_cols if c in df.columns])
    log_step(logger, "119+120", "Finalize Consultorias", df)
    return df


# =========================================================================
# STAGE 6 — ARBITRADO CLASSE
# =========================================================================
def match_arbitrado(
    df_unmatched_special: pd.DataFrame,
    df_normal_unico_cv: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Tool 56 — Left [Nome da Classe de Valor] = Right [Classe de valor]
    against NORMAL Unico CV.

    Returns:
        unmatched_final (Tool 56 L) → goes to Tool 118 Union (Reclassificador)
        matched         (Tool 56 J) → labeled 'Arbitrado classe' and sent to final Union
    """
    l_out, j_out, _ = alteryx_join(
        df_unmatched_special, df_normal_unico_cv,
        left_on="Nome da Classe de Valor",
        right_on="Classe de valor",
    )
    log_step(logger, "56L", "Unmatched in normal Unico CV", l_out)
    log_step(logger, "56J", "Arbitrado matched", j_out)
    return l_out, j_out


def finalize_arbitrado(df_matched: pd.DataFrame) -> pd.DataFrame:
    """
    Tools 56 (Conta destino) + 104 (Tipo) + 98 (drop helpers).

    Tool 56's SelectConfiguration renames the matched (normal) Unico CV's
    Right_"Código conta contábil" → "Conta destino" (XML line 371). alteryx_join
    keeps that right column, so we just rename it here.
    """
    df = df_matched.copy()
    # Tool 56 — Conta destino = Unico CV "Código conta contábil"
    if "Código conta contábil" in df.columns:
        df = df.rename(columns={"Código conta contábil": "Conta destino"})
    else:
        logger.warning(
            "[Tool 56] 'Código conta contábil' not found in Arbitrado matched rows — "
            "Conta destino will be missing. Verify the Unico CV column name."
        )
    df["Tipo"] = "Arbitrado classe"        # Tool 104
    drop_cols = ["Conta GCUT", "Pacote GCUT", "HISTORICO2", "Nome do Fornecedor", "F1"]
    df = df.drop(columns=[c for c in drop_cols if c in df.columns])
    log_step(logger, "104+98", "Finalize Arbitrado", df)
    return df


# =========================================================================
# STAGE 7 — RECLASSIFICADOR
# =========================================================================
def union_for_reclassificador(
    df_unmatched_final: pd.DataFrame,
    df_to_reclass: pd.DataFrame,
) -> pd.DataFrame:
    """Tool 118 — union of (Tool 56 L) + (Tool 117 T)."""
    df = pd.concat([df_unmatched_final, df_to_reclass], ignore_index=True, sort=False)
    log_step(logger, "118", "Union for Reclassificador", df)
    return df


def build_reclassifier_base(df_for_reclass: pd.DataFrame) -> pd.DataFrame:
    """
    Tools 121 + 122 — build the file that goes to the manual reclassifier process.

    Tool 121: Fornecedor Histórico = [Fornecedor] + "_" + [Historico]
    Tool 122: Select the schema defined in config.RECLASSIFIER_OUTPUT_SCHEMA
    """
    df = df_for_reclass.copy()
    df["Fornecedor Histórico"] = (
        df["Fornecedor"].fillna("").astype(str) + "_" + df["Historico"].fillna("").astype(str)
    )
    log_step(logger, "121", "Build Fornecedor Histórico key", df)

    # Tool 122 select — keep only columns in the schema (gracefully handle missing)
    keep = [c for c in RECLASSIFIER_OUTPUT_SCHEMA if c in df.columns]
    missing = set(RECLASSIFIER_OUTPUT_SCHEMA) - set(df.columns)
    if missing:
        logger.warning(f"[Tool 122] Missing columns in reclassifier schema: {missing}")
    df_out = df[keep].copy()
    log_step(logger, "122", "Reclassifier output schema", df_out)
    return df_out


def integrate_reclassified(
    df_for_reclass: pd.DataFrame,
    df_reclassified: Optional[pd.DataFrame],
) -> pd.DataFrame:
    """
    Tools 126 + 133 + 134 + 127 + 128.

    If df_reclassified is None (first run — file doesn't exist yet),
    the Reclassificador path returns an empty frame.

    Otherwise (orientation confirmed against the .yxmd connections):
      Tool 126: Join with LEFT = reclassified base (key "Chave única", a Double; XML
                connection 3196-3199, Tool 124) and RIGHT = df_for_reclass
                (key "Codigo Interno"; connection 3192-3195, Tool 118).
        - J (matched)    → Conta destino = reclassified's "Conta ajustada"
                           (Tool 126 select renames Left_"Conta ajustada", XML line 1841)
        - R (right-only) → current-run records the reclassifier did NOT cover →
                           Tool 133 sets Conta destino = "Conta Contabil"
        - L (left-only)  → reclassified rows with no match in the current run →
                           DROPPED (Tool 126 Left output has no downstream connection)
      Tool 134: Union J + R'
      Tool 127: Tipo = "Reclassificador"
      Tool 128: drop helpers
    """
    if df_reclassified is None:
        logger.warning(
            "[Tool 126] No reclassified base provided. Reclassificador path will be EMPTY. "
            "Run the workflow again after the manual reclassifier produces its output."
        )
        return pd.DataFrame()

    # Tool 126 keeps only Left_"Conta ajustada" from the reclassified base (every other
    # left column is dropped). Slim the left side to its key + that column so the J output
    # carries the df_for_reclass columns intact, with no name collisions / _R suffixes.
    for required in ("Chave única", "Conta ajustada"):
        if required not in df_reclassified.columns:
            raise KeyError(
                f"[Tool 126] '{required}' not found in base_reclassificada. "
                f"Columns available: {list(df_reclassified.columns)}"
            )
    recl_slim = df_reclassified[["Chave única", "Conta ajustada"]].copy()

    # alteryx_join normalizes keys to a common string form, so "Chave única" and
    # "Codigo Interno" (both read as int64) match by value. VALIDATE on the first real Run 2.
    l_out, j_out, r_out = alteryx_join(
        recl_slim, df_for_reclass,
        left_on="Chave única", right_on="Codigo Interno",
    )
    logger.info(
        f"[Tool 126] reclassifier join → J={len(j_out)} matched, "
        f"R={len(r_out)} current-run unmatched, L={len(l_out)} reclassified-only (dropped)"
    )

    # Tool 126 J — Conta destino from reclassified "Conta ajustada"
    j_out["Conta destino"] = j_out["Conta ajustada"]
    j_out = j_out.drop(
        columns=[c for c in ("Chave única", "Conta ajustada") if c in j_out.columns]
    )
    log_step(logger, "126J", "Reclassified J (Conta destino = Conta ajustada)", j_out)

    # Tool 133 — R output (current-run records not covered): Conta destino = Conta Contabil
    if len(r_out) > 0 and "Conta Contabil" in r_out.columns:
        r_out["Conta destino"] = r_out["Conta Contabil"]
    log_step(logger, "133", "Reclassified R → Conta destino = Conta Contabil", r_out)

    # Tool 134 — union J + R'
    combined = pd.concat([j_out, r_out], ignore_index=True, sort=False)
    log_step(logger, "134", "Union J + R", combined)

    # Tool 127 — Tipo
    combined["Tipo"] = "Reclassificador"
    log_step(logger, "127", "Tipo = Reclassificador", combined)

    # Tool 128 — drop helpers
    drop_cols = ["Conta GCUT", "Pacote GCUT", "HISTORICO2", "Nome do Fornecedor", "F1"]
    combined = combined.drop(columns=[c for c in drop_cols if c in combined.columns])
    log_step(logger, "128", "Drop helpers", combined)
    return combined


# =========================================================================
# STAGE 8 — FINAL CONSOLIDATION
# =========================================================================
def final_union(
    df_cobranca: pd.DataFrame,
    df_match_cc: pd.DataFrame,
    df_match_om: pd.DataFrame,
    df_arbitrado: pd.DataFrame,
    df_consultoria: pd.DataFrame,
    df_reclassificador: pd.DataFrame,
) -> pd.DataFrame:
    """Tool 95 — union all 6 classification paths."""
    parts = [df_cobranca, df_match_cc, df_match_om, df_arbitrado, df_consultoria, df_reclassificador]
    df = pd.concat([p for p in parts if len(p) > 0], ignore_index=True, sort=False)
    log_step(logger, "95", "Final Union (6 paths)", df)

    # Audit summary by Tipo (mirrors Tool 105)
    if "Tipo" in df.columns:
        audit = (
            df.groupby("Tipo")
              .agg(Count=("Codigo Interno", "count"),
                   Sum_Valor=("Valor", "sum"))
              .reset_index()
        )
        logger.info(f"[Tool 105] Classification audit:\n{audit.to_string(index=False)}")
    return df


def build_final_consolidated(df_unioned: pd.DataFrame) -> pd.DataFrame:
    """
    Tools 110 + 113 + 111 + 115 + 114 — apply Record IDs, build ERP keys,
    format date, and select final schema.
    """
    df = df_unioned.copy()

    # Tool 110 — RecordID grouped by Codigo Interno, starting at 0
    df["RecordID"] = df.groupby("Codigo Interno").cumcount()
    log_step(logger, "110", "RecordID grouped by Codigo Interno (start=0)", df)

    # Tool 113 — convert RecordID and Codigo Interno to string (for concatenation)
    df["RecordID"] = df["RecordID"].astype(str)
    df["Codigo Interno"] = df["Codigo Interno"].astype(str)

    # Tool 111 — build ERP index and matrix keys
    df["Índice ERP"] = np.where(
        df["RecordID"].astype(int) != 0,
        df["Codigo Interno"] + "_" + df["RecordID"],
        df["Codigo Interno"],
    )
    # Tool 111's formula names this field " CV Matrix" (leading space, XML line 1545), but
    # the actual Alteryx Excel output drops the space — the real artifact header is "CV Matrix".
    df["CV Matrix"] = df["Classe de Valor"].astype(str) + "-" + df["Nome da Classe de Valor"].astype(str)
    df["GC Matrix"] = "GC-" + df["Grupo Conta"].astype(str)
    df["Moeda"] = "BRL"
    df["Número NF"] = ""
    log_step(logger, "111", "Build Índice ERP + matrix keys", df)

    # Tool 115 — write the dd/MM/yyyy date into a NEW field "DateTime_Out" (Mes is preserved,
    # then dropped by the Tool 114 select). The exported column is named "DateTime_Out".
    df["DateTime_Out"] = pd.to_datetime(df["Mes"], errors="coerce").dt.strftime("%d/%m/%Y")
    log_step(logger, "115", "Format Mes → DateTime_Out (dd/MM/yyyy)", df)

    # Tool 114 — final select
    keep = [c for c in FINAL_OUTPUT_SCHEMA if c in df.columns]
    missing = set(FINAL_OUTPUT_SCHEMA) - set(df.columns)
    if missing:
        logger.warning(f"[Tool 114] Missing columns in final schema: {missing}")
    df_out = df[keep].copy()
    log_step(logger, "114", "Final consolidated schema", df_out)
    return df_out


# =========================================================================
# NOVO — RELATÓRIO DE EXCEÇÕES (Tool 200/201) — não existe no Alteryx
# =========================================================================
def build_exceptions(dropped_contas, df_base_raw, df_cc_cadastro):
    """
    Monta os dois DataFrames de exceções (Código · Descrição · Valor), somados por código:

      - Contas Contábeis (Tool 200): contas da base SEM match na Estrutura de Contas
        (o L do Tool 62 — exatamente as linhas hoje descartadas).
      - Centros de Custo (Tool 201): CCs presentes na base de fechamento (realizado) que
        NÃO existem no cadastro de Entidades x CC.

    A descrição vem da base do realizado: os itens, por definição, não estão no cadastro,
    então o cadastro não tem descrição para eles. O fluxo principal NÃO é alterado.
    """
    from config import (
        CC_CADASTRO_CODE_COL, BASE_CC_CODE_COL, BASE_CC_DESC_COL,
        BASE_CONTA_CODE_COL, BASE_CONTA_DESC_COL,
    )
    cols = ["Código", "Descrição", "Valor"]

    # --- Tool 200: Contas Contábeis não cadastradas ---
    if (dropped_contas is not None and len(dropped_contas) > 0
            and BASE_CONTA_CODE_COL in dropped_contas.columns):
        g = (dropped_contas.groupby(BASE_CONTA_CODE_COL, dropna=False)
                          .agg(Descrição=(BASE_CONTA_DESC_COL, "first"),
                               Valor=("Valor", "sum"))
                          .reset_index()
                          .rename(columns={BASE_CONTA_CODE_COL: "Código"}))
        contas = g[cols].sort_values("Valor", ascending=False).reset_index(drop=True)
    else:
        contas = pd.DataFrame(columns=cols)
    log_step(logger, "200", "Exceções — Contas Contábeis não cadastradas", contas)

    # --- Tool 201: Centros de Custo não cadastrados ---
    if (df_cc_cadastro is not None and CC_CADASTRO_CODE_COL in df_cc_cadastro.columns
            and BASE_CC_CODE_COL in df_base_raw.columns):
        cadastro = set(
            pd.to_numeric(df_cc_cadastro[CC_CADASTRO_CODE_COL], errors="coerce")
              .dropna().astype("int64")
        )
        base_cc = df_base_raw[[BASE_CC_CODE_COL, BASE_CC_DESC_COL, "Valor"]].copy()
        code_num = pd.to_numeric(base_cc[BASE_CC_CODE_COL], errors="coerce").astype("Int64")
        nao_encontrados = base_cc[~code_num.isin(cadastro)]
        if len(nao_encontrados) > 0:
            g = (nao_encontrados.groupby(BASE_CC_CODE_COL, dropna=False)
                               .agg(Descrição=(BASE_CC_DESC_COL, "first"),
                                    Valor=("Valor", "sum"))
                               .reset_index()
                               .rename(columns={BASE_CC_CODE_COL: "Código"}))
            centros = g[cols].sort_values("Valor", ascending=False).reset_index(drop=True)
        else:
            centros = pd.DataFrame(columns=cols)
    else:
        centros = pd.DataFrame(columns=cols)
    log_step(logger, "201", "Exceções — Centros de Custo não cadastrados", centros)

    return {"contas": contas, "centros_custo": centros}
