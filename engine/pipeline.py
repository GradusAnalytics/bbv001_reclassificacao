"""
pipeline.py — Orchestrates the full BBV001 reclassification flow.

Mirrors the .yxmd topology exactly. Reads inputs, runs the 8 stages,
and writes both outputs (reclassifier input base + final consolidated base).

V2 — por cima da topologia intacta, o pipeline agora carrega um RunControls
(engine/controls.py) que: valida cadastros na entrada (R2/R4), faz o censo de
duplicatas (R8, ERRO bloqueante), fecha a conservação entrada×saída (A1, ERRO
bloqueante) e coleta os avisos estruturados que o main() expõe ao usuário (aba
'Avisos' + tabela `warnings` + resumo no log). Racional: docs/SPEC_V2.md.

V3 (2026-07-28) — nenhum lançamento é descartado: a saída é o universo completo da
Base de Fechamento, marcado com Status/Motivo/Ação Recomendada. Os estágios
continuam iguais, mas quem NÃO volta da cascata é reintegrado por
`transforms.reconciliar_espinha`, e a conservação virou identidade de conjunto
(`controls.verifica_conservacao(df_base, base_completa)`) em vez de soma de termos.
A base final sai em 2 abas (universo gerencial + fora de escopo).
Racional: docs/superpowers/specs/2026-07-27-v3-base-final-completa-design.md.
"""
import logging

import pandas as pd

import io_utils as io
import reclassifier_bridge
import transforms as t
from controls import RunControls

logger = logging.getLogger(__name__)


def run_pipeline(base_reclassificada_override=None) -> dict:
    """
    Execute the full pipeline.

    base_reclassificada_override: se informado, pula a chamada à API (feita via
    reclassifier_bridge para a ferramenta reclassificador_predicao_bbv001, que já usa
    seus próprios arquivos default de modelo/parâmetros) e usa esse DataFrame
    diretamente — mantém o fluxo manual antigo como plano B.

    Returns a dict of intermediate DataFrames for inspection/testing,
    plus paths to the two output files, plus (V2) `controls` com avisos/conservação.

    Erros de negócio (input inválido, duplicação fabricada, conservação violada)
    sobem como controls.BloqueioError com mensagem acionável.

    V3 — ORDEM DE EXECUÇÃO É CONTRATO, não estilo: `reconciliar_espinha` tira o
    SNAPSHOT de `controls.marcacao_por_id()` e estampa Status/Motivo/Ação
    Recomendada na base final. Toda razão registrada (`controls.registra_razao`)
    DEPOIS dela morre em silêncio. Por isso `validar_mes` roda no início, sobre a
    base bruta, e `build_exceptions` (que registra CC_NAO_CADASTRADO) subiu para
    antes da reconciliação. Ao acrescentar qualquer razão nova, confira o grep de
    `registra_razao` contra esta ordem.
    """
    logger.info("=" * 70)
    logger.info("BBV001 — RUNNING PIPELINE (v3)")
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

    # V3 — 'Mes' inválido é propriedade da linha de ENTRADA e tem de ser marcado
    # antes da reconciliação: reconciliar_espinha tira o snapshot da marcação e
    # estampa as 3 colunas, então uma razão registrada depois dela morre no
    # caminho. Foi exatamente o bug achado na revisão da Task 9 — o Status
    # DADO_INVALIDO ficava inalcançável. Não mova esta chamada para depois.
    t.validar_mes(df_base, controls)

    # -------------------------------------------------------------------
    # STAGE 1 — PREPROCESS (V2: R1 + R2 + termos A1; V3/R8: censo de duplicata
    # NATIVA, informativo — o censo bloqueante roda no fim, por ID Lançamento)
    # V3 — não há mais bloqueio BASE_VAZIA_POS_FILTROS: os filtros de entrada
    # (T63/T62) deixaram de descartar lançamentos, só os marcam, e o universo da
    # saída é a base BRUTA. Cascata vazia é um caso legítimo (tudo marcado), não
    # um erro de input — a base vazia de verdade já bloqueou em BASE_VAZIA acima.
    # -------------------------------------------------------------------
    df_enriched, dropped_contas, df_pos_t63 = t.preprocess_base(df_base, df_estrutura, controls)

    # V2/R5 — cobertura das Classes de Valor do mês (código→nome) contra o cadastro
    t.validar_cobertura_classe_valor(df_pos_t63, df_class, df_unico_cv, controls)

    # -------------------------------------------------------------------
    # STAGE 2 — COBRANÇA SPLIT (V2: R3 no De-Para; R4 no de-para de grupos)
    # -------------------------------------------------------------------
    nao_cobranca, cobranca = t.split_cobranca(df_enriched)

    cobranca_keys     = t.prepare_cobranca_keys(cobranca)
    # V3 — prepare_depara_keys devolve também as chaves ambíguas (conflito na mesma
    # competência / desempate por valor); enrich_cobranca_with_depara marca com elas
    # o LANÇAMENTO que casou, não só o total agregado no aviso.
    depara_norm, chaves_conflito, chaves_valor = t.prepare_depara_keys(df_depara, controls)
    cob_unmatched, cob_matched = t.enrich_cobranca_with_depara(
        cobranca_keys, depara_norm, controls, chaves_conflito, chaves_valor)

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
    # V3.3 — a Conta OM do par vem da Estrutura de Contas, não da coluna do cadastro
    df_class_om          = t.summarize_classe_om(df_class, df_estrutura, controls)
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
    # STAGE 6 — ARBITRADO (V2/R10: correção automática por Conta OM; V3/R9: o
    # cruzamento de prefixo residual é MARCADO por lançamento, não removido)
    # -------------------------------------------------------------------
    unm_56, match_56     = t.match_arbitrado(unm_79, normal_cv)
    arbitrado_final_todos = t.finalize_arbitrado(match_56)
    arbitrado_corrigido, corr_arb = t.autocorrigir_conta_om(
        arbitrado_final_todos, df_estrutura, controls)
    arbitrado_final, bloq_r9_arb = t.marcar_bloqueio_prefixo(arbitrado_corrigido, controls)

    # -------------------------------------------------------------------
    # STAGE 7 — RECLASSIFICADOR (V2/R10: correção automática por Conta OM;
    # V3/R9: cruzamento de prefixo residual MARCADO, não removido)
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
    reclassificador_final, bloq_r9_recl = t.marcar_bloqueio_prefixo(
        reclassificador_corrigido, controls)

    # -------------------------------------------------------------------
    # STAGE 8 — FINAL UNION + RECONCILIAÇÃO DA ESPINHA + CONSOLIDATE
    # (V3: censo final R8 + conservação por identidade ANTES de gravar saída)
    # -------------------------------------------------------------------
    unioned = t.final_union(
        cobranca_final,
        match_cc_final,
        match_om_final,
        arbitrado_final,
        consultoria_final,
        reclassificador_final,
    )

    # V3/R8 — nenhum ID pode sair duplicado (ERRO bloqueante)
    controls.censo_final(unioned)

    bloqueados_r9 = pd.concat([bloq_r9_arb, bloq_r9_recl], ignore_index=True, sort=False)
    corrigidos_r9 = pd.concat([corr_arb, corr_recl], ignore_index=True, sort=False)
    controls.registra_termo("union_final", len(unioned),
                            float(unioned["Valor"].sum()) if len(unioned) else 0.0)

    # RELATÓRIO DE EXCEÇÕES — roda ANTES da reconciliação porque é quem registra a
    # razão CC_NAO_CADASTRADO por lançamento (V3)
    exceptions = t.build_exceptions(dropped_contas, df_pos_t63, df_cc_cad,
                                    bloqueados_r9, corrigidos_r9, controls)

    # V3 — a espinha reconciliada É a base final (universo completo, marcado)
    base_completa = t.reconciliar_espinha(df_base, unioned, controls)
    controls.verifica_conservacao(df_base, base_completa)

    final_aba1, final_aba2 = t.build_final_consolidated(base_completa, controls)

    # -------------------------------------------------------------------
    # VALOR POR PACOTE (antes × depois da cascata de classificação)
    # -------------------------------------------------------------------
    # V3 — o "antes" do relatório sai de 'Pacote GCUT', que só existe nas linhas que
    # passaram pelo T62. Nas demais a coluna vem NaN do concat e o
    # .fillna("(sem pacote)") que build_pacote_report já faz as agrupa num bucket
    # próprio — comportamento desejado (é o valor que ainda não tem pacote no GCUT).
    pacote_report = t.build_pacote_report(base_completa, df_estrutura)

    # -------------------------------------------------------------------
    # WRITE OUTPUTS
    # -------------------------------------------------------------------
    path_reclass = io.write_reclassifier_base(reclassifier_base)
    path_final   = io.write_final_consolidated(final_aba1, final_aba2)

    logger.info("=" * 70)
    logger.info("PIPELINE COMPLETE")
    logger.info(f"  Reclassifier base : {path_reclass}")
    logger.info(f"  Final consolidated: {path_final}")
    logger.info("=" * 70)

    return {
        "reclassifier_base": reclassifier_base,
        # V3 — "final_consolidated" continua sendo a base de carga, agora só a ABA 1
        # (universo gerencial); a aba 2 (Classes de Valor excluídas) sai em
        # "final_aba2" e o universo completo pré-formatação em "base_completa".
        "final_consolidated": final_aba1,
        "final_aba2": final_aba2,
        "base_completa": base_completa,
        "path_reclass": path_reclass,
        "path_final": path_final,
        "exceptions": exceptions,   # {"contas": df, "centros_custo": df, "bloqueio_prefixo": df, "correcao_automatica": df}
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
            "base_completa": base_completa,
            "bloqueados_r9": bloqueados_r9,
            "corrigidos_r9": corrigidos_r9,
        },
    }
