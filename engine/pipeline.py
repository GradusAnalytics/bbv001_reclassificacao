"""
pipeline.py — Orchestrates the full BBV001 reclassification flow.

Mirrors the .yxmd topology exactly. Reads inputs, runs the 8 stages,
and writes both outputs (reclassifier input base + final consolidated base).

V2 — por cima da topologia intacta, o pipeline agora carrega um RunControls
(engine/controls.py) que: valida cadastros na entrada (R2/R4), faz o censo de
duplicatas início×fim (R8, ERRO bloqueante), fecha a equação de conservação
entrada×saída (A1, ERRO bloqueante) e coleta os avisos estruturados que o main()
expõe ao usuário (aba 'Avisos' + tabela `warnings` + resumo no log).
Racional: docs/SPEC_V2.md do projeto.
"""
import logging

import pandas as pd

import io_utils as io
import reclassifier_bridge
import transforms as t
from controls import RunControls

logger = logging.getLogger(__name__)


def run_pipeline(base_reclassificada_override=None, modo_r9_warning: bool = False) -> dict:
    """
    Execute the full pipeline.

    base_reclassificada_override: se informado, pula a chamada à API (feita via
    reclassifier_bridge para a ferramenta reclassificador_predicao_bbv001, que já usa
    seus próprios arquivos default de modelo/parâmetros) e usa esse DataFrame
    diretamente — mantém o fluxo manual antigo como plano B.

    modo_r9_warning: V2/R10 (decisão dono 2026-07-21) — default False (bloqueia,
    preserva o comportamento já validado do R9). Se True, lançamentos que
    cruzam família de prefixo e não têm correção automática possível (ver
    autocorrigir_conta_om) sobem pro base_final com aviso em vez de serem
    excluídos.

    Returns a dict of intermediate DataFrames for inspection/testing,
    plus paths to the two output files, plus (V2) `controls` com avisos/conservação.

    Erros de negócio (input inválido, GRUPO não cadastrado, duplicação fabricada,
    conservação violada) sobem como controls.BloqueioError com mensagem acionável.
    """
    logger.info("=" * 70)
    logger.info("BBV001 — RUNNING PIPELINE (v2)")
    logger.info("=" * 70)

    controls = RunControls()

    # -------------------------------------------------------------------
    # READ INPUTS (V2: validação de colunas mínimas dentro de cada reader)
    # -------------------------------------------------------------------
    df_base       = io.read_base_fechamento()        # Tool 4  + 87
    df_depara     = io.read_depara_custo()           # Tool 10
    df_class      = io.read_classe_valor_conta()     # Tool 47
    df_unico_cv   = io.read_unico_cv()               # Tool 48
    df_estrutura  = io.read_estrutura_contas()       # Tool 61
    df_grupos     = io.read_depara_grupos()          # V2/R4 (obrigatório; era Tool 86 hardcoded)
    df_cc_cad     = io.read_estrutura_entidades_cc() # Tool 200 (cadastro de CC, opcional)

    # V2/D3 — base vazia é erro acionável, não crash críptico lá na frente
    if len(df_base) == 0:
        controls.erro("T4/Base", "BASE_VAZIA",
                      "A Base de Fechamento está vazia (0 lançamentos). Verifique se o "
                      "arquivo/aba corretos foram enviados.")
    controls.registra_termo("base_bruta", len(df_base), float(df_base["Valor"].sum()))

    # -------------------------------------------------------------------
    # STAGE 1 — PREPROCESS (V2: R1 + R2 + censo inicial R8 + termos A1)
    # -------------------------------------------------------------------
    df_enriched, dropped_contas, df_pos_t63 = t.preprocess_base(df_base, df_estrutura, controls)
    if len(df_enriched) == 0:
        controls.erro("T62/Estrutura", "BASE_VAZIA_POS_FILTROS",
                      "Nenhum lançamento restou após os filtros de entrada (Classes de "
                      "Valor excluídas + match com a Estrutura de Contas). Verifique os "
                      "arquivos enviados.")

    # V2/R5 — cobertura das Classes de Valor do mês (código→nome) contra o cadastro
    t.validar_cobertura_classe_valor(df_pos_t63, df_class, df_unico_cv, controls)

    # -------------------------------------------------------------------
    # STAGE 2 — COBRANÇA SPLIT (V2: R3 no De-Para; R4 no de-para de grupos)
    # -------------------------------------------------------------------
    nao_cobranca, cobranca = t.split_cobranca(df_enriched)

    cobranca_keys     = t.prepare_cobranca_keys(cobranca)
    depara_norm       = t.prepare_depara_keys(df_depara, controls)
    cob_unmatched, cob_matched = t.enrich_cobranca_with_depara(cobranca_keys, depara_norm)

    cob_overridden    = t.apply_manual_group_override(cob_matched, df_grupos, controls)
    cobranca_final    = t.consolidate_cobranca(cob_overridden, controls)

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
    # STAGE 6 — ARBITRADO (V2/R9: bloqueio de cruzamento de prefixo;
    # V2/R10: correção automática por Conta OM antes de bloquear)
    # -------------------------------------------------------------------
    unm_56, match_56     = t.match_arbitrado(unm_79, normal_cv)
    arbitrado_final_todos = t.finalize_arbitrado(match_56)
    arbitrado_corrigido, corr_arb = t.autocorrigir_conta_om(
        arbitrado_final_todos, df_estrutura, controls)
    arb_aprovados, bloq_r9_arb = t.split_bloqueio_prefixo(arbitrado_corrigido, controls)
    arbitrado_final, (arb_bloq_n, arb_bloq_v) = t.aplicar_modo_r9(
        arb_aprovados, bloq_r9_arb, modo_r9_warning, controls)

    # -------------------------------------------------------------------
    # STAGE 7 — RECLASSIFICADOR (V2/R9: bloqueio de cruzamento de prefixo;
    # V2/R10: correção automática por Conta OM antes de bloquear)
    # -------------------------------------------------------------------
    for_reclass          = t.union_for_reclassificador(unm_56, to_reclass_t117)
    reclassifier_base    = t.build_reclassifier_base(for_reclass)

    if base_reclassificada_override is not None:
        logger.info("[Bridge] Usando base_reclassificada informada manualmente (pulando chamada à API).")
        df_reclass = base_reclassificada_override
    else:
        logger.info("[Bridge] Chamando reclassificador_predicao_bbv001 via API do PPR...")
        df_reclass = reclassifier_bridge.run_predicao_via_api(reclassifier_base)

    reclassificador_final_todos = t.integrate_reclassified(for_reclass, df_reclass, controls)
    reclassificador_corrigido, corr_recl = t.autocorrigir_conta_om(
        reclassificador_final_todos, df_estrutura, controls)
    recl_aprovados, bloq_r9_recl = t.split_bloqueio_prefixo(reclassificador_corrigido, controls)
    reclassificador_final, (recl_bloq_n, recl_bloq_v) = t.aplicar_modo_r9(
        recl_aprovados, bloq_r9_recl, modo_r9_warning, controls)

    # -------------------------------------------------------------------
    # STAGE 8 — FINAL UNION + CONSOLIDATE
    # (V2: censo final R8 + conservação A1 ANTES de gravar qualquer saída)
    # -------------------------------------------------------------------
    unioned    = t.final_union(
        cobranca_final,
        match_cc_final,
        match_om_final,
        arbitrado_final,
        consultoria_final,
        reclassificador_final,
    )

    # V2/R8 — nenhuma chave pode sair mais duplicada do que entrou (ERRO bloqueante)
    controls.censo_final(unioned)

    # V2/R9 — bloqueados de Arbitrado + Reclassificador, concatenados (pro
    # relatório, sempre — independente do modo). V2/R10 — o termo de
    # conservação usa (arb_bloq_n/v + recl_bloq_n/v), que aplicar_modo_r9 já
    # zera quando modo_r9_warning=True.
    bloqueados_r9 = pd.concat([bloq_r9_arb, bloq_r9_recl], ignore_index=True, sort=False)
    corrigidos_r9 = pd.concat([corr_arb, corr_recl], ignore_index=True, sort=False)
    controls.registra_termo("bloqueio_prefixo_r9",
                            arb_bloq_n + recl_bloq_n,
                            arb_bloq_v + recl_bloq_v)

    # V2/A1 — equação de conservação: base bruta = T63 + T62 + colapso T132 + R9 + union
    controls.registra_termo("union_final", len(unioned),
                            float(unioned["Valor"].sum()) if len(unioned) else 0.0)
    controls.verifica_conservacao()

    final_consolidated = t.build_final_consolidated(unioned, controls)

    # -------------------------------------------------------------------
    # RELATÓRIO DE EXCEÇÕES (Tool 200/201) — V2/R6: universo pós-T63 nas 2 abas
    # -------------------------------------------------------------------
    exceptions = t.build_exceptions(dropped_contas, df_pos_t63, df_cc_cad,
                                    bloqueados_r9, corrigidos_r9, controls)

    # -------------------------------------------------------------------
    # VALOR POR PACOTE (antes × depois da cascata de classificação)
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
        "controls": controls,       # V2 — avisos estruturados + termos de conservação
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
            "bloqueados_r9": bloqueados_r9,
            "corrigidos_r9": corrigidos_r9,
        },
    }
