"""
pipeline.py — Orchestrates the full BBV001 reclassification flow.

Mirrors the .yxmd topology exactly. Reads inputs, runs the 8 stages,
and writes both outputs (reclassifier input base + final consolidated base).
"""
import logging

import pandas as pd

import io_utils as io
import reclassifier_bridge
import transforms as t

logger = logging.getLogger(__name__)


def run_pipeline(base_reclassificada_override=None) -> dict:
    """
    Execute the full pipeline.

    base_reclassificada_override: se informado, pula a chamada à API (feita via
    reclassifier_bridge para a ferramenta reclassificador_predicao_bbv001, que já usa
    seus próprios arquivos default de modelo/parâmetros) e usa esse DataFrame
    diretamente — mantém o fluxo manual antigo como plano B.

    Returns a dict of intermediate DataFrames for inspection/testing,
    plus paths to the two output files.
    """
    logger.info("=" * 70)
    logger.info("BBV001 — RUNNING PIPELINE")
    logger.info("=" * 70)

    # -------------------------------------------------------------------
    # READ INPUTS
    # -------------------------------------------------------------------
    df_base       = io.read_base_fechamento()        # Tool 4  + 87
    df_depara     = io.read_depara_custo()           # Tool 10
    df_class      = io.read_classe_valor_conta()     # Tool 47
    df_unico_cv   = io.read_unico_cv()               # Tool 48
    df_estrutura  = io.read_estrutura_contas()       # Tool 61
    df_manual     = io.get_manual_overrides()        # Tool 86
    df_cc_cad     = io.read_estrutura_entidades_cc() # Tool 200 (cadastro de CC, opcional)

    # -------------------------------------------------------------------
    # STAGE 1 — PREPROCESS
    # -------------------------------------------------------------------
    df_enriched, dropped_contas = t.preprocess_base(df_base, df_estrutura)

    # -------------------------------------------------------------------
    # STAGE 2 — COBRANÇA SPLIT
    # -------------------------------------------------------------------
    nao_cobranca, cobranca = t.split_cobranca(df_enriched)

    cobranca_keys     = t.prepare_cobranca_keys(cobranca)
    depara_norm       = t.prepare_depara_keys(df_depara)
    cob_unmatched, cob_matched = t.enrich_cobranca_with_depara(cobranca_keys, depara_norm)

    cob_overridden    = t.apply_manual_group_override(cob_matched, df_manual)
    cobranca_final    = t.consolidate_cobranca(cob_overridden)

    # Tool 44 — union of (non-cobrança) + (cobrança unmatched in De-Para)
    df_cascade = pd.concat([nao_cobranca, cob_unmatched], ignore_index=True, sort=False)
    t.log_step(logger, "44", "Union for main cascade", df_cascade)

    # -------------------------------------------------------------------
    # STAGE 3 — MATCH CONTA × CLASSE
    # -------------------------------------------------------------------
    unm_49, match_49     = t.match_conta_classe(df_cascade, df_class)
    match_cc_final       = t.finalize_match_conta_classe(match_49)

    # -------------------------------------------------------------------
    # STAGE 4 — MATCH CLASSE × CONTA OM
    # -------------------------------------------------------------------
    df_class_om          = t.summarize_classe_om(df_class)
    unm_60, match_60     = t.match_classe_conta_om(unm_49, df_class_om)
    match_om_final       = t.finalize_match_classe_conta_om(match_60)

    # -------------------------------------------------------------------
    # STAGE 5 — SPECIAL ROUTING (Reclassificador / Consultoria)
    # -------------------------------------------------------------------
    normal_cv, special_cv  = t.split_unico_cv(df_unico_cv)
    unm_79, match_79       = t.route_special_classes(unm_60, special_cv)
    to_reclass_t117, cons  = t.split_consultoria(match_79)
    consultoria_final      = t.finalize_consultoria(cons)

    # -------------------------------------------------------------------
    # STAGE 6 — ARBITRADO
    # -------------------------------------------------------------------
    unm_56, match_56     = t.match_arbitrado(unm_79, normal_cv)
    arbitrado_final      = t.finalize_arbitrado(match_56)

    # -------------------------------------------------------------------
    # STAGE 7 — RECLASSIFICADOR
    # -------------------------------------------------------------------
    for_reclass          = t.union_for_reclassificador(unm_56, to_reclass_t117)
    reclassifier_base    = t.build_reclassifier_base(for_reclass)

    if base_reclassificada_override is not None:
        logger.info("[Bridge] Usando base_reclassificada informada manualmente (pulando chamada à API).")
        df_reclass = base_reclassificada_override
    else:
        logger.info("[Bridge] Chamando reclassificador_predicao_bbv001 via API do PPR...")
        df_reclass = reclassifier_bridge.run_predicao_via_api(reclassifier_base)

    reclassificador_final = t.integrate_reclassified(for_reclass, df_reclass)

    # -------------------------------------------------------------------
    # STAGE 8 — FINAL UNION + CONSOLIDATE
    # -------------------------------------------------------------------
    unioned    = t.final_union(
        cobranca_final,
        match_cc_final,
        match_om_final,
        arbitrado_final,
        consultoria_final,
        reclassificador_final,
    )
    final_consolidated = t.build_final_consolidated(unioned)

    # -------------------------------------------------------------------
    # NOVO — RELATÓRIO DE EXCEÇÕES (Tool 200/201)
    # -------------------------------------------------------------------
    exceptions = t.build_exceptions(dropped_contas, df_base, df_cc_cad)

    # -------------------------------------------------------------------
    # NOVO — VALOR POR PACOTE (antes × depois da cascata de classificação)
    # -------------------------------------------------------------------
    pacote_report = t.build_pacote_report(unioned, df_estrutura)

    # -------------------------------------------------------------------
    # WRITE OUTPUTS
    # -------------------------------------------------------------------
    path_reclass = io.write_reclassifier_base(reclassifier_base)
    path_final   = io.write_final_consolidated(final_consolidated)

    logger.info("=" * 70)
    logger.info("PIPELINE COMPLETE")
    logger.info(f"  Reclassifier base : {path_reclass}")
    logger.info(f"  Final consolidated: {path_final}")
    logger.info("=" * 70)

    return {
        "reclassifier_base": reclassifier_base,
        "final_consolidated": final_consolidated,
        "path_reclass": path_reclass,
        "path_final": path_final,
        "exceptions": exceptions,   # {"contas": df, "centros_custo": df}
        "pacote_report": pacote_report,
        # Intermediates for debugging
        "_intermediates": {
            "enriched": df_enriched,
            "cobranca_final": cobranca_final,
            "match_cc_final": match_cc_final,
            "match_om_final": match_om_final,
            "consultoria_final": consultoria_final,
            "arbitrado_final": arbitrado_final,
            "reclassificador_final": reclassificador_final,
            "unioned": unioned,
        },
    }
