"""
helpers.py — Alteryx function equivalents and the Alteryx-style join helper.

Each function here maps to a specific Alteryx primitive used by the workflow.
"""
import re
import unicodedata
from typing import Tuple, List, Union

import pandas as pd


# -------------------------------------------------------------------------
# String normalization
# -------------------------------------------------------------------------
def decompose_unicode_for_match(value) -> str:
    """
    Replicates Alteryx's DecomposeUnicodeForMatch():
      - Removes diacritics (NFKD decomposition + drop combining chars)
      - Lowercases the result (per user confirmation)
      - NaN / None → ''

    Used in Tools 21 (HISTORICO2, Nome do Fornecedor) and 25 (FORNECEDOR).
    """
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    s = str(value)
    nfkd = unicodedata.normalize("NFKD", s)
    no_diacritics = "".join(c for c in nfkd if not unicodedata.combining(c))
    return no_diacritics.lower()


def remove_whitespace(value) -> str:
    """
    Replicates the Alteryx 'Data Cleansing' macro's "remove all whitespace" option, used
    by Tool 24 on the Cobrança HISTORICO2 (checkboxes 109/122, which Tool 26 does NOT set).

    Applied AFTER decompose_unicode_for_match. This is what makes the Tool 11 De-Para join
    work: the De-Para HISTORICO_2 has no spaces ("despesasneobpo-contencioso") while
    Getright([Historico],"-") keeps them ("jantar apos horario"), so without it the join
    matches only ~32 of ~600+ rows.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return re.sub(r"\s+", "", str(value))


def getright(value, delim: str) -> str:
    """
    Returns everything to the right of the FIRST occurrence of delim.
    If delim is not present, returns the original string.

    Used in Tool 21: HISTORICO2 = Getright([Historico], "-"). In practice this strips the
    "NF.<código>-" prefix from the Historico ("NF.006135-DIGITALIZACAO AUDITORIA - COTE"
    → "DIGITALIZACAO AUDITORIA - COTE"), which is what lines up with the De-Para
    HISTORICO_2. Validated against the real data: splitting on the FIRST dash yields ~1875
    De-Para matches vs only ~578 when splitting on the last dash.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    s = str(value)
    if delim not in s:
        return s
    return s.split(delim, 1)[1]


# -------------------------------------------------------------------------
# Alteryx-style Join
# -------------------------------------------------------------------------
def _normalize_join_key(s: pd.Series) -> pd.Series:
    """
    Render a join-key column to a consistent, comparable string, mirroring Alteryx's
    string-based matching.

    Excel reads give the SAME logical key different dtypes across files — e.g. base
    'Conta Contabil' comes back as object while Estrutura 'CONTA CONTÁBIL' is the pandas
    'str' extension dtype, and pandas .merge() will NOT match object against StringDtype,
    silently dropping every row. Account codes also exceed int64, and ids read as int64
    on one side may be float64 on another. We collapse all of that to plain str:
      - NaN / None / pd.NA  → None  (never matches, like an Alteryx null key)
      - whole-number floats → integer string  ("76766029.0" → "76766029")
      - everything else     → str(value)

    NOTE: we do NOT strip/trim here. Alteryx's Join matches keys byte-for-byte, so
    collapsing "Salários" and "Salários " would create matches Alteryx never makes
    (it inflated Arbitrado by ~11 rows). Whitespace cleansing that Alteryx *does* apply
    (Tools 24/26 on the De-Para keys) is done explicitly in transforms, not here.
    """
    def conv(v):
        if v is None:
            return None
        try:
            if pd.isna(v):
                return None
        except (TypeError, ValueError):
            pass
        if isinstance(v, float):
            return str(int(v)) if v.is_integer() else repr(v)
        if isinstance(v, int):
            return str(v)
        return str(v)

    return s.astype(object).map(conv)


def alteryx_join(
    left: pd.DataFrame,
    right: pd.DataFrame,
    left_on: Union[str, List[str]],
    right_on: Union[str, List[str]],
    suffixes: Tuple[str, str] = ("", "_R"),
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Replicates the 3-output Alteryx Join tool:
      - L: rows from `left` with NO match in `right`
      - J: matched rows (cartesian product on duplicate keys, like Alteryx)
      - R: rows from `right` with NO match in `left`

    Returns (L, J, R) in that order.

    Notes:
      - When key column names differ between left and right, both are kept in J.
      - Column name collisions on non-key columns get suffixed via `suffixes`.
      - Null keys never match (consistent with Alteryx behavior).
    """
    if isinstance(left_on, str):
        left_on = [left_on]
    if isinstance(right_on, str):
        right_on = [right_on]

    if len(left_on) != len(right_on):
        raise ValueError("left_on and right_on must have the same length")

    # Tag both sides so we can split outputs after an outer merge
    left = left.copy()
    right = right.copy()
    left["_left_idx"] = range(len(left))
    right["_right_idx"] = range(len(right))

    # Merge on NORMALIZED copies of the keys (see _normalize_join_key) so dtype
    # mismatches between Excel sources don't silently drop matches. The original key
    # columns are kept untouched in the outputs.
    norm_keys = [f"_jk{i}" for i in range(len(left_on))]
    for i, (lk, rk) in enumerate(zip(left_on, right_on)):
        left[norm_keys[i]] = _normalize_join_key(left[lk])
        right[norm_keys[i]] = _normalize_join_key(right[rk])

    # J via INNER merge so original dtypes survive. An outer merge would inject NaN into
    # the unmatched side and silently upcast int columns to float (e.g. Codigo Interno
    # 76744979 → 76744979.0, which then stringifies to "76744979.0"). L and R are the
    # anti-joins, taken from the untouched originals by row marker.
    j = left.merge(right, on=norm_keys, how="inner", suffixes=suffixes)

    matched_left = set(j["_left_idx"])
    matched_right = set(j["_right_idx"])
    l_only = left[~left["_left_idx"].isin(matched_left)]
    r_only = right[~right["_right_idx"].isin(matched_right)]

    helper_cols = ["_left_idx", "_right_idx", *norm_keys]
    left_cols = [c for c in left.columns if c not in helper_cols]
    right_cols = [c for c in right.columns if c not in helper_cols]

    l_out = l_only[left_cols].copy()
    r_out = r_only[right_cols].copy()
    j_out = j.drop(columns=[c for c in helper_cols if c in j.columns], errors="ignore")

    return l_out.reset_index(drop=True), j_out.reset_index(drop=True), r_out.reset_index(drop=True)


# -------------------------------------------------------------------------
# Logging helper
# -------------------------------------------------------------------------
def log_step(logger, tool_id: str, description: str, df: pd.DataFrame) -> None:
    """Standard one-liner so we can cross-check row counts against Alteryx Browse."""
    logger.info(f"[Tool {tool_id:>4}] {description:<55} → {len(df):>8,} rows × {len(df.columns):>3} cols")
