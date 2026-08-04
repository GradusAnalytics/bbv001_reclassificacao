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
    STATUS_OK,
    STATUS_FORA_DE_ESCOPO,
    CODIGOS_QUE_REMOVEM_LANCAMENTO,
    FAMILIA_PREFIXO_CONTA,
)
from controls import RunControls, coerce_conta_str, resolve_duplicatas_cadastro
from helpers import (
    decompose_unicode_for_match,
    remove_whitespace,
    getright,
    alteryx_join,
    log_step,
    _normalize_join_key,
)

logger = logging.getLogger(__name__)


# =========================================================================
# STAGE 1 — PREPROCESS
# =========================================================================
def preprocess_base(df_base: pd.DataFrame, df_estrutura: pd.DataFrame,
                    controls: RunControls):
    """
    Tools 63 (filter excluded CVs) + 62 (join with Estrutura de Contas).

    V2: R1 (resumo das exclusões por CV) · R2 (check de duplicatas na Estrutura) ·
    termos de conservação (A1). V3: censo_duplicata_nativa (informativo, R8) + razão
    por ID (`CV_EXCLUIDA` / `CONTA_NAO_CADASTRADA`) — é essa razão que devolve os
    lançamentos ao base_final via `reconciliar_espinha`, em vez de descartá-los.

    Retorna (j_out, l_out, df):
      j_out — base enriquecida (Conta GCUT / Pacote GCUT), segue para a cascata.
      l_out — contas da base SEM match na Estrutura; não seguem a cascata, mas
              voltam ao base_final (via reconciliar_espinha) com Status
              'CADASTRO_PENDENTE' e a própria conta de origem como destino.
      df    — base pós-T63 (universo canônico das exceções — R6).
    """
    # Tool 63 — filter excluded value classes
    mask = ~df_base["Nome da Classe de Valor"].isin(EXCLUDED_VALUE_CLASSES)
    df = df_base[mask].copy()
    log_step(logger, "63", "Filter excluded Value Classes", df)

    # V2/R1 — resumo do que saiu no T63, por Classe de Valor excluída (antes: fuga sem rastro)
    excluidas = df_base[~mask]
    if len(excluidas) > 0:
        por_cv = excluidas.groupby("Nome da Classe de Valor", dropna=False)["Valor"].agg(["count", "sum"])
        for cv, r in por_cv.iterrows():
            controls.add("T63", "INFO", "CV_EXCLUIDA",
                         f"Classe de Valor '{cv}' excluída do processamento (configuração).",
                         registros=int(r["count"]), valor=float(r["sum"]))
    controls.registra_termo("excluidas_t63", len(excluidas),
                            float(excluidas["Valor"].sum()) if len(excluidas) else 0.0)
    # V3 — razão por lançamento (a linha volta ao base_final marcada, aba 2)
    if len(excluidas) > 0:
        controls.registra_razao(excluidas["ID Lançamento"], "CV_EXCLUIDA")

    # V3/R8 — duplicata nativa é só informativa; o censo bloqueante roda no fim, por ID
    controls.censo_duplicata_nativa(df_base)

    # V2/R2 — Estrutura é relatório de sistema: duplicata de chave não deveria existir.
    # Dedup se idêntica (warning pedindo nova extração); ERRO se conflitante.
    df_estrutura = resolve_duplicatas_cadastro(
        df_estrutura, chave=["CONTA CONTÁBIL"], conteudo=["CONTA", "PACOTE"],
        nome_input="Estrutura de Contas", etapa="T62/Estrutura", controls=controls)

    # Tool 62 — left join with Estrutura de Contas
    # Keys: [Conta Contabil] = [CONTA CONTÁBIL]. We use INNER on J side
    # because the L output (unmatched) only feeds a validation Summarize (Tool 129).
    l_out, j_out, _ = alteryx_join(
        df, df_estrutura,
        left_on="Conta Contabil",
        right_on="CONTA CONTÁBIL",
    )
    # V2/A1 — exceções de conta (fora da base_final; vão ao relatório de exceções)
    controls.registra_termo("excecoes_t62", len(l_out),
                            float(l_out["Valor"].sum()) if len(l_out) else 0.0)
    if len(l_out) > 0:
        controls.add("T62/Estrutura", "WARNING", "CONTA_NAO_CADASTRADA",
                     f"{l_out['Conta Contabil'].nunique()} conta(s) contábil(is) da base não "
                     f"existem na Estrutura de Contas — os lançamentos entram na base final "
                     f"com Status 'CADASTRO_PENDENTE' e a própria conta de origem como "
                     f"destino (ficam sem Pacote no Matrix até o cadastro ser feito).",
                     registros=len(l_out), valor=float(l_out["Valor"].sum()))
        # V3 — razão por lançamento (volta ao base_final com Conta destino = origem, D3)
        controls.registra_razao(l_out["ID Lançamento"], "CONTA_NAO_CADASTRADA")
    if len(l_out) > 0:
        logger.warning(
            f"[Tool 62] {len(l_out)} rows in base have no match in Estrutura de Contas — "
            f"they skip the cascade (V3: they still return to base_final via "
            f"reconciliar_espinha, marked CADASTRO_PENDENTE). Unique Contas: "
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
    # l_out (contas sem match) não segue a cascata, mas alimenta o relatório de
    # exceções E (V3) volta ao base_final via reconciliar_espinha (razão registrada acima).
    # V2: devolve também o frame pós-T63 (universo canônico das exceções — R6).
    return j_out, l_out, df


def validar_mes(df_base: pd.DataFrame, controls: RunControls) -> None:
    """
    V3 (2026-07-28, Correção 1) — valida a coluna 'Mes' da BASE BRUTA e registra a
    razão `MES_INVALIDO` por 'ID Lançamento'. Não devolve nada — o efeito é o
    registro em `controls` (`registra_razao` + `controls.add` WARNING).

    **Por que roda AQUI, ANTES de `reconciliar_espinha` — não mova de volta para
    dentro de `build_final_consolidated`:** mês inválido é propriedade da LINHA DE
    ENTRADA (já nasce inválida na Base de Fechamento), não do estágio de escrita.
    `reconciliar_espinha` tira o SNAPSHOT de `controls.marcacao_por_id()` e estampa
    Status/Motivo/Ação Recomendada na base final; `build_final_consolidated` roda
    DEPOIS disso, só para formatar `DateTime_Out`. Uma razão registrada dentro de
    `build_final_consolidated` é registrada tarde demais — ninguém mais lê o
    snapshot depois de tirado — e o Status `DADO_INVALIDO` (que só `MES_INVALIDO`
    produz) nunca chegaria à base final. Esse foi exatamente o bug corrigido aqui
    (achado de revisão, 2026-07-28): a validação vivia dentro de
    `build_final_consolidated`, rodando depois de `reconciliar_espinha`.

    `build_final_consolidated` continua fazendo a CONVERSÃO/FORMATAÇÃO de 'Mes' →
    'DateTime_Out' (isso é escrita de saída, não validação de entrada) — só a
    validação (registra_razao + aviso) saiu de lá.
    """
    mes_dt = pd.to_datetime(df_base["Mes"], errors="coerce")
    invalidas = mes_dt.isna()
    n_invalidas = int(invalidas.sum())
    if n_invalidas == 0:
        return
    controls.registra_razao(df_base.loc[invalidas, "ID Lançamento"], "MES_INVALIDO")
    # o exemplo sai de uma linha INVÁLIDA (df_base['Mes'].iloc[0] podia ser uma data
    # válida no caso parcial, e o "exemplo" enganaria quem for corrigir o arquivo)
    exemplo_mes = df_base.loc[invalidas, "Mes"].iloc[0]
    controls.add("T115/Datas", "WARNING", "MES_INVALIDO",
                 f"{n_invalidas:,} de {len(df_base):,} lançamento(s) têm 'Mes' que não é uma "
                 f"data válida (exemplo: {exemplo_mes!r}) — eles entram na base "
                 f"final com DateTime_Out vazio, ou seja, SEM COMPETÊNCIA no Matrix. "
                 f"Corrija o formato da coluna na Base de Fechamento.",
                 registros=n_invalidas)


def validar_cobertura_classe_valor(
    df_pos_t63: pd.DataFrame,
    df_class: pd.DataFrame,
    df_unico_cv: pd.DataFrame,
    controls: RunControls,
) -> None:
    """
    V2/R5 (decisão dono 2026-07-09, implementado 2026-07-20) — Tool 49/Unico CV:
    cobertura das Classes de Valor do mês, código→nome, contra o cadastro (Classe×Conta
    aba Base) e o Unico CV. Só WARNING nos 3 casos (existe fallback pelo Reclassificador;
    objetivo é visibilidade + orientação de cadastro, não bloqueio).

    V3 (2026-07-27): além do aviso agregado, cada um dos 3 casos registra a razão nos
    LANÇAMENTOS daquela classe (CLASSE_NAO_CADASTRADA / CLASSE_RENOMEADA /
    CLASSE_SEM_UNICO_CV) — antes o achado existia só como total por classe.

    Mapa código→nome(s) vem da aba Base, chave = "Número att" (decisão do dono
    2026-07-20: a aba tem "Número CV" duplicado no cabeçalho do Excel — pandas lê como
    "Número CV" / "Número CV.1" — e diverge de "Número att" em ~40-50 das 693 linhas;
    "Número att" foi o escolhido como código canônico pelo dono, não uma inferência
    nossa).

    Por classe distinta do mês (base pós-T63; código = "Classe de Valor", nome = "Nome
    da Classe de Valor"):
      1. código ∉ mapa                              → CLASSE_NAO_CADASTRADA
      2. código ∈ mapa, nome não bate com nenhum nome cadastrado pro código → CLASSE_RENOMEADA
      3. código+nome OK, nome ∉ Unico CV            → CLASSE_SEM_UNICO_CV
    Código comparado via chave normalizada (_normalize_join_key); nome byte-a-byte —
    é o que os joins da cascata (Tools 49/60/56) fazem, sem decompose/lower.
    """
    cad = df_class[["Número att", "Nome classe de valor"]].dropna(subset=["Nome classe de valor"])
    cod_cadastro = _normalize_join_key(cad["Número att"])
    mapa: dict = {}
    for cod, nome in zip(cod_cadastro, cad["Nome classe de valor"]):
        if cod is None:
            continue
        mapa.setdefault(cod, set()).add(nome)

    nomes_unico_cv = set(df_unico_cv["Classe de valor"].dropna())

    classes_mes = (
        df_pos_t63.groupby(["Classe de Valor", "Nome da Classe de Valor"], dropna=False)
        # V3 — os IDs saem do MESMO groupby que os totais: um passo só na base (em vez
        # de uma varredura por classe) e o recorte vale também para chave nula, que um
        # `df[col] == valor` perderia em silêncio (NaN == NaN é False).
        # Correção 3: "size" conta LINHAS (bate com a lista de IDs marcados);
        # "count" ignoraria Valor nulo e divergiria da contagem de IDs marcados.
        .agg(registros=("Valor", "size"), valor=("Valor", "sum"),
             ids=("ID Lançamento", list))
        .reset_index()
    )
    classes_mes["_cod"] = _normalize_join_key(classes_mes["Classe de Valor"])

    for _, row in classes_mes.iterrows():
        cod, nome = row["_cod"], row["Nome da Classe de Valor"]
        codigo_original, registros, valor = row["Classe de Valor"], int(row["registros"]), float(row["valor"])
        # V3 — IDs dos lançamentos desta classe, para marcar o lançamento (não só o total)
        ids_classe = row["ids"]

        if cod not in mapa:
            controls.registra_razao(ids_classe, "CLASSE_NAO_CADASTRADA")
            controls.add(
                "T49/Unico CV", "WARNING", "CLASSE_NAO_CADASTRADA",
                f"Classe de Valor '{nome}' (código {codigo_original!r}) não está cadastrada "
                f"na aba Base do Classe×Conta — cadastre-a nas abas Base e Unico CV.",
                registros=registros, valor=valor)
            continue

        nomes_cadastrados = mapa[cod]
        if nome not in nomes_cadastrados:
            controls.registra_razao(ids_classe, "CLASSE_RENOMEADA")
            controls.add(
                "T49/Unico CV", "WARNING", "CLASSE_RENOMEADA",
                f"Classe de Valor código {codigo_original!r}: nome do mês {nome!r} diverge "
                f"do(s) nome(s) cadastrado(s) {sorted(nomes_cadastrados)!r} — atualize o "
                f"nome nas abas Base e Unico CV (os joins da cascata são por nome; classe "
                f"renomeada falha os matches em silêncio e cai no Reclassificador).",
                registros=registros, valor=valor)
            continue

        if nome not in nomes_unico_cv:
            controls.registra_razao(ids_classe, "CLASSE_SEM_UNICO_CV")
            controls.add(
                "T49/Unico CV", "WARNING", "CLASSE_SEM_UNICO_CV",
                f"Classe de Valor '{nome}' está cadastrada na aba Base mas não tem linha "
                f"na aba Unico CV — cairá no Reclassificador por eliminação (cadastro "
                f"incompleto).",
                registros=registros, valor=valor)


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


def prepare_depara_keys(df_depara: pd.DataFrame,
                        controls: RunControls) -> Tuple[pd.DataFrame, set, set]:
    """
    Tools 14 + 25 + 26 — summarize De-Para and normalize FORNECEDOR for join.

    Tool 14: GroupBy on [HISTORICO_2, FORNECEDOR, FINALIZACAO, GRUPO] (de-dup)
    Tool 25: FORNECEDOR = DecomposeUnicodeForMatch( IsNull → "" else [FORNECEDOR] )
    Tool 26: data cleansing (handled inside DecomposeUnicodeForMatch already)

    V2/R3 — MUDANÇA DE COMPORTAMENTO APROVADA (SPEC_V2 R3, decisão 2026-07-08):
    a v1 dedupava em 4 colunas mas o join do Tool 11 usa só 2 → chave com 2 regras
    distintas virava produto cartesiano (em Abr26: 21 chaves → 168 linhas fabricadas).
    A v2 resolve por RECÊNCIA: normaliza as chaves PRIMEIRO (a normalização pode
    colapsar chaves distintas) e garante 1 regra por [HISTORICO_2, FORNECEDOR]:
      - 1 regra só            → segue (dup exata entre competências = INFO);
      - regras divergentes    → vale a de DATA_BASE mais recente (WARNING);
      - divergência na MESMA DATA_BASE mais recente → desempate por
        SOMA(|VALOR_LANCAMENTO|) por regra nessa competência — vale a regra de
        maior valor (WARNING; decisão do dono 2026-07-13, achado real em Jun26:
        10 chaves, nenhuma com empate de valor); se o valor TAMBÉM empatar, a v2
        bloqueava (ERRO).
    Saída com grão único por chave ⇒ o Tool 11 não duplica mais.

    V3 (2026-07-27): o empate de valor deixou de bloquear — vira WARNING
    (`DEPARA_CONFLITO_MESMA_COMPETENCIA`) e a função devolve os DOIS conjuntos de
    chaves ambíguas para que `enrich_cobranca_with_depara` marque o LANÇAMENTO que
    casou com elas (não só o total agregado):
        (df_resolvido, chaves_conflito, chaves_valor)
    Cada chave é o par normalizado (HISTORICO_2, FORNECEDOR) — mesma ordem em que o
    join do Tool 11 compara com [HISTORICO2, Nome do Fornecedor] da base.
    """
    keys = ["HISTORICO_2", "FORNECEDOR", "FINALIZACAO", "GRUPO", "DATA_BASE"]
    df = df_depara[keys].copy()

    # Tools 25+26 primeiro (na v1 a ordem era dedup→normalize; normalizar antes é
    # necessário para o dedup enxergar chaves que a normalização colapsa)
    df["FORNECEDOR"] = df["FORNECEDOR"].apply(
        lambda x: decompose_unicode_for_match(x) if pd.notna(x) else ""
    )
    df["HISTORICO_2"] = df["HISTORICO_2"].apply(
        lambda x: remove_whitespace(decompose_unicode_for_match(x))
    )
    df["DATA_BASE"] = pd.to_datetime(df["DATA_BASE"], errors="coerce")

    # Tool 14 (dedup exato, agora incluindo DATA_BASE)
    df = df.drop_duplicates()
    log_step(logger, "14", "De-Para normalized + dedup exato", df)

    chave = ["HISTORICO_2", "FORNECEDOR"]
    regra = ["FINALIZACAO", "GRUPO"]

    n_regras = df.groupby(chave, dropna=False)[regra].nunique().max(axis=1)
    chaves_multi = n_regras[n_regras > 1].index
    resolvidas_por_valor: dict = {}
    # V3 — declarada FORA do if: o return no fim da função usa as duas listas mesmo
    # quando não há chave multi-regra (senão estoura NameError).
    irresolviveis: list = []
    # Correção 2 (2026-07-28) — (h, f) -> (FINALIZACAO, GRUPO) da PRIMEIRA linha em
    # ordem de leitura original, só para as chaves de `irresolviveis` (empate também
    # no valor). Declarada FORA do if pelo mesmo motivo de `irresolviveis` acima.
    primeira_regra_irresolvivel: dict = {}

    if len(chaves_multi) > 0:
        multi = df.set_index(chave).loc[chaves_multi].reset_index()
        # regra mais recente por chave
        max_data = multi.groupby(chave, dropna=False)["DATA_BASE"].transform("max")
        recentes = multi[multi["DATA_BASE"] == max_data]
        # conflito na MESMA competência mais recente: desempata por SOMA(|Valor|)
        # por regra; só é irresolvível (ERRO) se o valor TAMBÉM empatar.
        conflito = recentes.groupby(chave, dropna=False)[regra].nunique().max(axis=1)
        conflitantes = conflito[conflito > 1]
        if len(conflitantes) > 0:
            # soma de |VALOR_LANCAMENTO| por (chave, regra), buscando o valor de volta
            # em df_depara pela mesma chave normalizada + DATA_BASE + regra (recentes/
            # multi vêm do DataFrame já filtrado a [keys], sem a coluna de valor)
            base_valor = df_depara[["HISTORICO_2", "FORNECEDOR", "FINALIZACAO", "GRUPO",
                                    "DATA_BASE", "VALOR_LANCAMENTO"]].copy()
            base_valor["FORNECEDOR"] = base_valor["FORNECEDOR"].apply(
                lambda x: decompose_unicode_for_match(x) if pd.notna(x) else "")
            base_valor["HISTORICO_2"] = base_valor["HISTORICO_2"].apply(
                lambda x: remove_whitespace(decompose_unicode_for_match(x)))
            base_valor["DATA_BASE"] = pd.to_datetime(base_valor["DATA_BASE"], errors="coerce")

            for h, f in conflitantes.index:
                data_max = recentes.loc[
                    (recentes["HISTORICO_2"] == h) & (recentes["FORNECEDOR"] == f), "DATA_BASE"
                ].iloc[0]
                sub = base_valor[(base_valor["HISTORICO_2"] == h) & (base_valor["FORNECEDOR"] == f)
                                  & (base_valor["DATA_BASE"] == data_max)]
                soma = sub.groupby(regra)["VALOR_LANCAMENTO"].apply(lambda s: s.abs().sum()) \
                          .sort_values(ascending=False)
                if len(soma) > 1 and soma.iloc[0] == soma.iloc[1]:
                    irresolviveis.append((h, f))
                    # Correção 2 — `sub` preserva a ordem original de df_depara (só
                    # filtros booleanos até aqui, nenhum sort); a primeira linha
                    # desse recorte É a primeira em ordem de leitura para esta chave,
                    # dentre as da competência mais recente empatada.
                    primeira = sub.iloc[0]
                    primeira_regra_irresolvivel[(h, f)] = (
                        primeira["FINALIZACAO"], primeira["GRUPO"])
                else:
                    resolvidas_por_valor[(h, f)] = soma.index[0]  # (FINALIZACAO, GRUPO) vencedor

            if irresolviveis:
                exemplos = "; ".join(f"({h!r}, {f!r})" for h, f in irresolviveis[:5])
                # V3 (2026-07-27) — deixou de abortar: aplica a primeira em ordem de
                # leitura e marca os lançamentos que casarem com essas chaves.
                controls.add(
                    "T11/De-Para", "WARNING", "DEPARA_CONFLITO_MESMA_COMPETENCIA",
                    f"O De-Para de Cobrança tem {len(irresolviveis):,} chave(s) "
                    f"histórico×fornecedor com regras DIVERGENTES na mesma competência "
                    f"(DATA_BASE) mais recente E empate de valor — a v3 aplicou a "
                    f"primeira em ordem de leitura e marcou os lançamentos afetados com "
                    f"Status 'DESTINO_SUSPEITO'. Corrija o De-Para e reenvie. "
                    f"Chaves (até 5): {exemplos}",
                    registros=int(len(irresolviveis)))
            if resolvidas_por_valor:
                exemplos = "; ".join(
                    f"({h!r}, {f!r})→{g}" for (h, f), g in list(resolvidas_por_valor.items())[:5])
                controls.add(
                    "T11/De-Para", "WARNING", "DEPARA_CONFLITO_RESOLVIDO_POR_VALOR",
                    f"{len(resolvidas_por_valor):,} chave(s) histórico×fornecedor tinham regras "
                    f"DIVERGENTES na mesma competência mais recente — a v2 aplicou a regra com "
                    f"maior SOMA(|Valor|) nessa competência (revisar cadastro do De-Para). "
                    f"Chaves: {exemplos}",
                    registros=int(len(resolvidas_por_valor)))
        controls.add(
            "T11/De-Para", "WARNING", "DEPARA_REGRA_MAIS_RECENTE",
            f"{len(chaves_multi):,} chave(s) histórico×fornecedor tinham regras divergentes "
            f"entre competências — a v2 aplicou a regra da DATA_BASE mais recente "
            f"(a v1 duplicava o lançamento nesses casos).",
            registros=int(len(chaves_multi)))

    # resolução final: por chave, fica a linha de DATA_BASE mais recente (regra
    # idêntica repetida em competências diferentes colapsa de graça — INFO)
    # Correção 2 — kind="stable": sort_values usa quicksort por padrão, que NÃO é
    # estável; com 3+ regras empatadas na mesma DATA_BASE, o "last" de
    # drop_duplicates virava indefinido entre execuções. Estável preserva a ordem
    # de leitura original entre empates — determinismo aqui é requisito.
    antes = len(df)
    df = (df.sort_values("DATA_BASE", kind="stable")
            .drop_duplicates(subset=chave, keep="last")
            .drop(columns=["DATA_BASE"])
            .reset_index(drop=True))

    # chaves resolvidas por desempate de valor: a linha que sobreviveu ao
    # keep="last" acima pode não ser a de maior valor (empate de DATA_BASE é
    # desempatado por ordem original) — força a regra vencedora aqui.
    for (h, f), (fin, gru) in resolvidas_por_valor.items():
        mask = (df["HISTORICO_2"] == h) & (df["FORNECEDOR"] == f)
        df.loc[mask, "FINALIZACAO"] = fin
        df.loc[mask, "GRUPO"] = gru

    # Correção 2 — chaves IRRESOLVÍVEIS (empate também no valor): a linha que
    # sobreviveu ao keep="last" é a ÚLTIMA em ordem de leitura entre as empatadas,
    # não a primeira. O aviso DEPARA_CONFLITO_MESMA_COMPETENCIA promete a
    # PRIMEIRA — força aqui, espelhando o laço de resolvidas_por_valor acima.
    for (h, f), (fin, gru) in primeira_regra_irresolvivel.items():
        mask = (df["HISTORICO_2"] == h) & (df["FORNECEDOR"] == f)
        df.loc[mask, "FINALIZACAO"] = fin
        df.loc[mask, "GRUPO"] = gru
    colapsadas = antes - len(df)
    if colapsadas > 0:
        controls.add("T11/De-Para", "INFO", "DEPARA_COLAPSO_RECENCIA",
                     f"{colapsadas:,} linha(s) do De-Para colapsadas na resolução por "
                     f"recência (inclui regras idênticas repetidas entre competências).",
                     registros=int(colapsadas))
    log_step(logger, "R3", "De-Para resolvido: 1 regra por chave (recência)", df)
    # V3 — os dois conjuntos de chaves ambíguas seguem para a marcação por lançamento
    return df, set(irresolviveis), set(resolvidas_por_valor.keys())


def enrich_cobranca_with_depara(df_cobranca_keys: pd.DataFrame,
                                df_depara_norm: pd.DataFrame,
                                controls: RunControls,
                                chaves_conflito: set,
                                chaves_valor: set) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Tool 11 — join Cobrança (with normalized keys) × normalized De-Para.

    Keys: Left [Nome do Fornecedor, HISTORICO2] = Right [FORNECEDOR, HISTORICO_2]

    Returns:
        unmatched_cobranca (Tool 11 L) — go back to the main cascade via Tool 44
        matched_cobranca   (Tool 11 J) — continue to Tool 88 (manual overrides)

    V3 (2026-07-27): recebe os conjuntos de chaves ambíguas que `prepare_depara_keys`
    devolveu e registra a razão no LANÇAMENTO que casou com elas — antes o achado
    existia só como total agregado no aviso.
    """
    l_out, j_out, _ = alteryx_join(
        df_cobranca_keys, df_depara_norm,
        left_on=["Nome do Fornecedor", "HISTORICO2"],
        right_on=["FORNECEDOR", "HISTORICO_2"],
    )
    log_step(logger, "11L", "Cobrança unmatched in De-Para", l_out)
    log_step(logger, "11J", "Cobrança matched in De-Para", j_out)
    # V3 — o lançamento que casou com uma chave ambígua do De-Para sai marcado.
    # A chave do lado da base é [HISTORICO2, Nome do Fornecedor] (as colunas já
    # normalizadas por prepare_cobranca_keys), na mesma ordem em que
    # prepare_depara_keys montou os pares (HISTORICO_2, FORNECEDOR).
    if len(j_out) > 0:
        pares = list(zip(j_out["HISTORICO2"], j_out["Nome do Fornecedor"]))
        for chaves, codigo in (
            (chaves_conflito, "DEPARA_CONFLITO_MESMA_COMPETENCIA"),
            (chaves_valor, "DEPARA_CONFLITO_RESOLVIDO_POR_VALOR"),
        ):
            if not chaves:
                continue
            mask = pd.Series([p in chaves for p in pares], index=j_out.index)
            if mask.any():
                controls.registra_razao(j_out.loc[mask, "ID Lançamento"], codigo)
    return l_out, j_out


def apply_manual_group_override(
    df_cobranca_matched: pd.DataFrame,
    df_grupos: pd.DataFrame,
    controls: RunControls,
) -> pd.DataFrame:
    """
    Tool 88 — inner join Cobrança-matched × de-para GRUPO → Conta.

    V2/R4 — MUDANÇA DE COMPORTAMENTO APROVADA (SPEC_V2 R4, decisão 2026-07-09):
      - o de-para vem do input OBRIGATÓRIO `depara_grupos` (antes: 11 overrides
        hardcoded no config — manutenção exigia deploy);
      - GRUPO sem cadastro é ERRO BLOQUEANTE listando os faltantes (antes: descarte
        SILENCIOSO — em Abr26 sumiam 309 lançamentos / R$ 428.809,42 do GRUPO
        'REEMBOLSO' com só um warning no log);
      - o cadastro passa pelo mesmo padrão R2 (dedup se idêntico / erro se conflito).

    V3 (2026-07-27) — GRUPO sem cadastro deixou de ser ERRO BLOQUEANTE: vira razão
    `GRUPO_NAO_CADASTRADO` por ID Lançamento (WARNING) e o lançamento volta ao
    base_final via `reconciliar_espinha` (Status CADASTRO_PENDENTE, Conta destino =
    própria conta de origem), em vez de abortar a execução inteira.

    Conta destino for the Cobrança path is created HERE (not in a later formula):
    Tool 88's SelectConfiguration renames the override's Right_"Conta Contábil"
    → "Conta destino" and drops Right_"Grupo" / Right_"Conta OM" (XML line 2755).
    """
    # V2/R2 aplicado ao cadastro novo: unicidade de Grupo
    df_grupos = resolve_duplicatas_cadastro(
        df_grupos, chave=["Grupo"], conteudo=["Conta OM", "Conta Contábil"],
        nome_input="De-Para de Grupos (depara_grupos)", etapa="T88/Grupos",
        controls=controls)

    l_out, j_out, _ = alteryx_join(
        df_cobranca_matched, df_grupos,
        left_on="GRUPO",
        right_on="Grupo",
    )
    if len(l_out) > 0:
        faltantes = sorted(l_out["GRUPO"].dropna().unique().tolist())
        valor = float(l_out["Valor"].sum())
        # V3 (2026-07-27) — deixou de ser ERRO bloqueante: o lançamento volta ao
        # base_final via reconciliar_espinha, com Status CADASTRO_PENDENTE e a
        # própria Conta Contábil como destino (D3/D6 do spec da v3).
        controls.registra_razao(l_out["ID Lançamento"], "GRUPO_NAO_CADASTRADO")
        controls.add(
            "T88/Grupos", "WARNING", "GRUPO_NAO_CADASTRADO",
            f"{len(l_out):,} lançamento(s) de Cobrança (R$ {valor:,.2f}) têm GRUPO sem "
            f"cadastro no De-Para de Grupos: {faltantes}. Eles entram na base final com "
            f"Status 'CADASTRO_PENDENTE' e a própria conta de origem como destino. "
            f"Adicione esses GRUPOs ao cadastro 'depara_grupos' (colunas Grupo · Conta "
            f"OM · Conta Contábil) e re-execute para que sejam classificados.",
            registros=len(l_out), valor=valor)

    # Tool 88 select: rename override's "Conta Contábil" → "Conta destino", drop the
    # other right-side cols (the right key "Grupo" and "Conta OM").
    j_out = j_out.rename(columns={"Conta Contábil": "Conta destino"})
    # V2/N1 (escopo ampliado 2026-07-13): o fix original só cobria finalize_arbitrado
    # e integrate_reclassified — mas depara_grupos é um cadastro Excel externo (R4) e
    # "Conta Contábil" chega como int quando a célula está formatada como número
    # (achado real testando v2 em Jun26: 1.698 linhas do caminho Cobrança gravadas
    # como número, mesmo risco de estouro float64 no write).
    j_out["Conta destino"] = coerce_conta_str(j_out["Conta destino"])
    j_out = j_out.drop(columns=[c for c in ("Grupo", "Conta OM") if c in j_out.columns])
    log_step(logger, "88", "Cobrança × de-para de grupos (matched, Conta destino set)", j_out)
    return j_out


def consolidate_cobranca(df_cobranca_overridden: pd.DataFrame,
                         controls: RunControls) -> pd.DataFrame:
    """
    Tools 132 + 101 + 99.

    V2: Tool 132 fazia dedup pelas 30 colunas do workflow (agia como DISTINCT) e
    o colapso (duplicata exata) era medido como termo da equação de conservação
    (A1) — paridade Alteryx preservada.

    V3 (2026-07-27): o drop_duplicates saiu. Tool 132 agora é só o SELECT das
    mesmas colunas (mais "ID Lançamento", que precisa sobreviver ao select) —
    nunca colapsa linhas. Ver comentário junto de `select_cols_t132` abaixo para o
    racional. "Conta destino" já está presente aqui — foi criada no Tool 88
    (apply_manual_group_override), casando com a lista GroupBy do .yxmd.
    Tool 101: add Tipo = "Cobrança".
    Tool 99 : drop helper columns (Conta GCUT, Pacote GCUT, HISTORICO2,
              Nome do Fornecedor, FINALIZACAO, GRUPO).
    """
    # V3 (2026-07-27) — o Tool 132 continua sendo o SELECT de colunas do Alteryx,
    # mas o drop_duplicates saiu. Racional (spec da v3 §8): depois do R3 (De-Para
    # resolvido para 1 regra por chave) e do R2 (Estrutura deduplicada na leitura),
    # as duas fontes conhecidas de clone estão fechadas a montante — o dedup aqui
    # só conseguiria apagar duplicata NATIVA da base, que é lançamento real. Se um
    # clone aparecer, ele sobrevive até o censo (controls.censo_final), que acusa
    # DUPLICACAO_FABRICADA e manda corrigir o input.
    # Nome era `dedup_cols` na v2; virou só a lista do SELECT quando o dedup saiu.
    select_cols_t132 = [
        "ID Lançamento",
        "Codigo Interno", "Mes", "Data", "Grupo Acionista", "Plano de Contas Original",
        "Grupo Conta", "Conta Contabil", "Conta BRGaap", "Nome da Conta", "Valor",
        "Historico", "Fornecedor", "Veiculo Legal", "Centro de Custo",
        "Nome do Centro de Custo", "Diretoria", "Area", "Classe de Valor",
        "Nome da Classe de Valor", "Ajuste Manual", "Usuario Lancador",
        "Classificacao", "Cont.Doc", "Conta GCUT", "Pacote GCUT", "HISTORICO2",
        "Nome do Fornecedor", "FINALIZACAO", "GRUPO", "Conta destino",
    ]
    existing = [c for c in select_cols_t132 if c in df_cobranca_overridden.columns]
    df = df_cobranca_overridden[existing].copy()
    log_step(logger, "132", "Cobrança select (V3: sem dedup)", df)

    # V3 — o termo continua existindo no resumo do log, sempre zerado
    controls.registra_termo("colapso_t132", 0, 0.0)

    df["Tipo"] = "Cobrança"   # Tool 101
    log_step(logger, "101", "Cobrança: assign Tipo", df)

    # Tool 99 — drop helper columns. "Pacote GCUT" is kept (not dropped like in the
    # original Alteryx Tool 99): it feeds the NOVO relatório de Valor por Pacote
    # (build_pacote_report) — it's never selected into FINAL_OUTPUT_SCHEMA, so the
    # actual final_consolidated file is unaffected.
    drop_cols = ["Conta GCUT", "HISTORICO2", "Nome do Fornecedor", "FINALIZACAO", "GRUPO"]
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
    # "Pacote GCUT" é mantida (ver nota em consolidate_cobranca) para o relatório de
    # Valor por Pacote — não afeta o schema final (Tool 96/114).
    drop_cols = ["Conta GCUT", "HISTORICO2", "Nome do Fornecedor"]
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
    # "Pacote GCUT" mantida — ver nota em consolidate_cobranca.
    drop_cols = ["Conta GCUT", "HISTORICO2", "Nome do Fornecedor"]
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
    # "Pacote GCUT" mantida — ver nota em consolidate_cobranca.
    drop_cols = ["Conta GCUT", "HISTORICO2", "Nome do Fornecedor", "F1"]
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
        # V2/N1 (aprovado 2026-07-09): coerção a string — na v1 a coluna só não
        # corrompia porque o Unico CV tem valores alfanuméricos que forçam dtype str
        # (segurança acidental). Códigos de 25 dígitos estouram float64 no write.
        df["Conta destino"] = coerce_conta_str(df["Conta destino"])
    else:
        logger.warning(
            "[Tool 56] 'Código conta contábil' not found in Arbitrado matched rows — "
            "Conta destino will be missing. Verify the Unico CV column name."
        )
    df["Tipo"] = "Arbitrado classe"        # Tool 104
    # "Pacote GCUT" mantida — ver nota em consolidate_cobranca.
    drop_cols = ["Conta GCUT", "HISTORICO2", "Nome do Fornecedor", "F1"]
    df = df.drop(columns=[c for c in drop_cols if c in df.columns])
    log_step(logger, "104+98", "Finalize Arbitrado", df)
    return df


def mascaras_cruzamento_familia(
    conta_origem: pd.Series,
    conta_destino: pd.Series,
) -> Tuple[pd.Series, pd.Series]:
    """
    Regra de família de prefixo de Conta Contábil — FONTE ÚNICA (v3.1, 2026-07-30).

    Recebe as duas Series de conta JÁ COAGIDAS para string (coerce_conta_str no
    chamador) e devolve as duas máscaras separadas:

      r9a: prefixo de 3 dígitos 818 cruzando com {817, 819}. 817 e 819 são a
        MESMA família (não cruzam entre si); 818 é família separada.
      r9b: 1º dígito 8 cruzando com não-8, excluindo as linhas já capturadas
        por r9a (o 8 separa uma família maior que engloba 817/818/819).

    Por que a função existe: a regra é consumida por dois controles — R10
    (autocorrigir_conta_om, decide se vale reescrever a Conta destino do
    lançamento automaticamente) e R9 (marcar_bloqueio_prefixo, decide se marca
    Status=DESTINO_SUSPEITO). R9 precisa das duas máscaras separadas para
    diferenciar os códigos PREFIXO_818_CRUZADO / PREFIXO_FAMILIA_8_CRUZADO; R10
    só precisa saber se há violação (r9a | r9b). Antes desta função a regra
    estava copiada literalmente nas duas funções — risco de discordância
    silenciosa se alguém alterasse uma cópia e esquecesse a outra (pior caso:
    R10 mais permissivo que R9, reescrevendo uma conta que R9 marcaria como
    suspeita). O dicionário de família é config.FAMILIA_PREFIXO_CONTA — também
    fonte única, não redeclarar aqui.
    """
    fam_o = conta_origem.str[:3].map(FAMILIA_PREFIXO_CONTA)
    fam_d = conta_destino.str[:3].map(FAMILIA_PREFIXO_CONTA)
    r9a = fam_o.notna() & fam_d.notna() & (fam_o != fam_d)
    r9b = ((conta_origem.str[:1] == "8") != (conta_destino.str[:1] == "8")) & ~r9a
    return r9a, r9b


def autocorrigir_conta_om(
    df: pd.DataFrame,
    df_estrutura: pd.DataFrame,
    controls: RunControls,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    V2/R10 (decisão dono 2026-07-21) — pra cada linha com família(Conta Contabil)
    != família(Conta destino), busca uma conta alternativa na MESMA Conta OM
    (via Estrutura de Contas — não a coluna 'Conta OM' do Único CV, que pode
    estar desatualizada) e reescreve só o Conta destino DESSA linha se achar.
    Nunca muda nenhum cadastro (Único CV, Estrutura, depara_grupos) — a
    correção é sempre por lançamento. Chamada 1x por Tipo (Arbitrado,
    Reclassificador), ANTES de marcar_bloqueio_prefixo.

    Por que reescrever a conta do lançamento (não o cadastro): o papel do
    Único CV é só forçar que a Classe de Valor caia numa Conta OM determinada
    — o Matrix classifica por Conta OM, a Conta Contábil específica é só o
    veículo técnico. Trocar QUAL conta específica (mesma Conta OM) não muda o
    significado de negócio; mudar o cadastro do Único CV globalmente
    inverteria o problema pros lançamentos da mesma Classe que hoje já batem
    certo com a conta atual.

    Desempate quando há mais de uma conta compatível na Conta OM: a primeira
    em ordem de leitura da Estrutura de Contas (decisão do dono 2026-07-21) —
    sem critério de valor, a ordem das linhas não tem significado de negócio.

    Vale pros dois Tipos (Arbitrado classe e Reclassificador) igualmente — o
    mecanismo não depende do Único CV, só de olhar a Conta OM da Conta destino
    ATUAL via Estrutura de Contas, seja ela vinda do match por nome ou da
    predição do Reclassificador.

    Retorna (df, correcoes):
      df — mesmo DataFrame de entrada, com Conta destino reescrita nas linhas
        corrigidas e uma coluna interna '_conta_om_destino' preenchida em toda
        linha que violava a família (corrigida ou não — reaproveitada por
        marcar_bloqueio_prefixo pra montar a orientação sem recalcular).
      correcoes — 1 linha por correção feita: Codigo Interno · Tipo ·
        Nome da Classe de Valor · Valor · Conta destino original ·
        Conta destino corrigida · Conta OM (vazio se nada foi corrigido).
        Schema de 7 colunas = SCHEMA_OUTPUT.md §3 — 'ID Lançamento' NÃO entra
        aqui. Os IDs corrigidos são coletados numa lista local (não a partir
        do DataFrame de retorno) só p/ alimentar controls.registra_razao
        (V3), pra não alargar o schema documentado por conveniência interna.
    """
    cc = coerce_conta_str(df["Conta Contabil"])
    cd_original = coerce_conta_str(df["Conta destino"])

    r9a, r9b = mascaras_cruzamento_familia(cc, cd_original)
    violacao = r9a | r9b

    df = df.copy()
    df["_conta_om_destino"] = pd.NA

    cols_correcao = ["Codigo Interno", "Tipo", "Nome da Classe de Valor",
                     "Valor", "Conta destino original", "Conta destino corrigida", "Conta OM"]

    if not violacao.any():
        return df, pd.DataFrame(columns=cols_correcao)

    # Mapa Conta Contábil (normalizada) -> Conta OM, a partir da Estrutura de
    # Contas (gabarito — mesma fonte que build_pacote_report já usa pra um
    # propósito parecido, sem dedup adicional: 0 duplicatas na Estrutura real
    # hoje; R2 já cobre cadastro conflitante em preprocess_base).
    est = df_estrutura[["CONTA CONTÁBIL", "CONTA"]].copy()
    est["_chave"] = _normalize_join_key(est["CONTA CONTÁBIL"])
    est = est.dropna(subset=["_chave"])
    conta_para_conta_om = dict(zip(est["_chave"], est["CONTA"]))

    # Por Conta OM: lista de contas cadastradas, na ordem de leitura da
    # Estrutura (usada como desempate quando há mais de uma alternativa
    # compatível).
    contas_por_conta_om: dict = {}
    for chave, conta_om in zip(est["_chave"], est["CONTA"]):
        contas_por_conta_om.setdefault(conta_om, []).append(chave)

    correcoes = []
    ids_corrigidos = []  # lista local — nao vem do DataFrame de retorno (schema §3)
    for idx in df.index[violacao]:
        destino_atual = cd_original.loc[idx]
        conta_om = conta_para_conta_om.get(destino_atual)
        df.loc[idx, "_conta_om_destino"] = conta_om
        if conta_om is None:
            continue  # conta destino não está na Estrutura — nada a fazer aqui
        origem = cc.loc[idx]
        if r9a.loc[idx]:
            familia_necessaria = FAMILIA_PREFIXO_CONTA.get(origem[:3])
            candidato = next(
                (c for c in contas_por_conta_om.get(conta_om, [])
                 if FAMILIA_PREFIXO_CONTA.get(c[:3]) == familia_necessaria),
                None)
        else:
            precisa_comecar_com_8 = origem[:1] == "8"
            candidato = next(
                (c for c in contas_por_conta_om.get(conta_om, [])
                 if (c[:1] == "8") == precisa_comecar_com_8),
                None)
        if candidato is not None and candidato != destino_atual:
            df.loc[idx, "Conta destino"] = candidato
            ids_corrigidos.append(df.loc[idx, "ID Lançamento"])
            correcoes.append({
                "Codigo Interno": df.loc[idx, "Codigo Interno"],
                "Tipo": df.loc[idx, "Tipo"],
                "Nome da Classe de Valor": df.loc[idx, "Nome da Classe de Valor"],
                "Valor": df.loc[idx, "Valor"],
                "Conta destino original": destino_atual,
                "Conta destino corrigida": candidato,
                "Conta OM": conta_om,
            })

    df_correcoes = pd.DataFrame(correcoes, columns=cols_correcao)

    if len(df_correcoes) > 0:
        # V3 — fica registrado no próprio lançamento (Status OK, Motivo informativo).
        # ids_corrigidos vem da lista local coletada no loop acima, nao de
        # df_correcoes["ID Lançamento"] — essa coluna foi tirada do retorno pra
        # nao alargar o schema documentado em SCHEMA_OUTPUT.md §3 (ver docstring).
        controls.registra_razao(ids_corrigidos, "PREFIXO_CORRIGIDO_AUTOMATICAMENTE")
        controls.add(
            "R10/AutoCorrecao", "INFO", "PREFIXO_CORRIGIDO_AUTOMATICAMENTE",
            f"{len(df_correcoes):,} lançamento(s) tiveram a Conta destino "
            f"corrigida automaticamente pra uma conta da família certa, na "
            f"MESMA Conta OM (nenhum cadastro foi alterado — só a conta "
            f"daquele lançamento). Detalhe na aba 'Correção Automática de "
            f"Conta' do relatório de exceções.",
            registros=len(df_correcoes), valor=float(df_correcoes["Valor"].sum()))

    return df, df_correcoes


def marcar_bloqueio_prefixo(
    df: pd.DataFrame,
    controls: RunControls,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    V2/R9 (dono 2026-07-20), reescrita na V3 (dono 2026-07-27).

    Aplica as duas regras de família de prefixo a um DataFrame já finalizado de
    estágio (com 'Conta Contabil', 'Conta destino', 'Tipo', 'Valor'), usada só nos
    Tipos onde a conta final vem de mecanismo NÃO-determinístico — Arbitrado classe
    e Reclassificador — chamada 1x por Tipo em pipeline.py, DEPOIS de
    autocorrigir_conta_om (V2/R10). Cobrança e Consultorias ficam de fora (destino
    vem de regra de negócio deliberada, não de fallback).

      R9a: prefixo de 3 dígitos 818 cruzando com {817, 819} → 'PREFIXO_818_CRUZADO'
      R9b: 1º dígito 8 cruzando com não-8                   → 'PREFIXO_FAMILIA_8_CRUZADO'

    Regra de família (817/819 mesma família, 818 separada, 1º dígito 8 separa a
    família maior) — ver mascaras_cruzamento_familia(), fonte única desde a
    v3.1 (2026-07-30), para o texto completo e o racional de ela ser função
    compartilhada com o R10.

    **Mudança da V3:** a função não tira mais ninguém do fluxo. Ela MARCA (registra a
    razão por ID no RunControls, com ação recomendada específica por lançamento) e
    devolve o DataFrame inteiro. O lançamento segue com a `Conta destino` que cruza
    família (decisão D4 — o antigo campo de modo warning do R9 virou o comportamento único).
    O segundo retorno continua alimentando a aba 'Bloqueio Prefixo Conta' do excel.

    Nome antigo: `split_bloqueio_prefixo` (a coluna interna chamava-se `Motivo`, que
    na V3 passou a ser coluna de saída do base_final — por isso o `_motivo_r9`).
    """
    cc = coerce_conta_str(df["Conta Contabil"])
    cd = coerce_conta_str(df["Conta destino"])

    r9a, r9b = mascaras_cruzamento_familia(cc, cd)

    marcado = df.copy()
    marcado["_motivo_r9"] = pd.NA
    marcado.loc[r9a, "_motivo_r9"] = "PREFIXO_818_CRUZADO"
    marcado.loc[r9b, "_motivo_r9"] = "PREFIXO_FAMILIA_8_CRUZADO"

    bloqueados = marcado[marcado["_motivo_r9"].notna()].copy()

    # o fluxo segue com TODAS as linhas, sem colunas internas
    completo = marcado.drop(columns=[c for c in ("_motivo_r9", "_conta_om_destino")
                                     if c in marcado.columns])

    if len(bloqueados) > 0:
        bloqueados = bloqueados.rename(columns={"_motivo_r9": "Motivo"})
        if "_conta_om_destino" in bloqueados.columns:
            bloqueados["Conta OM"] = bloqueados["_conta_om_destino"]
            bloqueados = bloqueados.drop(columns=["_conta_om_destino"])
        else:
            bloqueados["Conta OM"] = pd.NA

        def _acao_recomendada(row):
            if row["Tipo"] == "Reclassificador":
                return (f"Revise manualmente o lançamento {row['Codigo Interno']} — "
                        f"a Conta OM '{row['Conta OM']}' não tem conta da família "
                        f"necessária cadastrada.")
            origem_pref3 = str(row["Conta Contabil"])[:3]
            origem_pref1 = str(row["Conta Contabil"])[:1]
            if row["Motivo"] == "PREFIXO_818_CRUZADO":
                falta = "818" if origem_pref3 == "818" else "817 ou 819"
            else:
                falta = "que comece com 8" if origem_pref1 == "8" else "que NÃO comece com 8"
            return (f"Cadastre uma conta {falta} na Conta OM '{row['Conta OM']}' na "
                    f"Estrutura de Contas — não há nenhuma hoje.")

        bloqueados["Ação Recomendada"] = bloqueados.apply(_acao_recomendada, axis=1)

        # V3 — razão por lançamento, com a ação específica (Conta OM daquela linha)
        for codigo in ("PREFIXO_818_CRUZADO", "PREFIXO_FAMILIA_8_CRUZADO"):
            sub = bloqueados[bloqueados["Motivo"] == codigo]
            if len(sub) == 0:
                continue
            controls.registra_razao(
                sub["ID Lançamento"], codigo,
                acoes=dict(zip(sub["ID Lançamento"],
                               sub["Ação Recomendada"])))
            descricao = ("prefixo 818 cruzando com 817/819" if codigo == "PREFIXO_818_CRUZADO"
                         else "prefixo 8 cruzando com não-8")
            controls.add(
                "R9/Prefixo", "WARNING", codigo,
                f"{len(sub):,} lançamento(s) com cruzamento de prefixo de Conta Contábil "
                f"({descricao}). Eles ENTRAM na base final com Status "
                f"'DESTINO_SUSPEITO' e a conta que cruza — filtre por Status antes de "
                f"carregar. Detalhe na aba 'Bloqueio Prefixo Conta' do relatório de "
                f"exceções, com a ação recomendada por lançamento.",
                registros=int(len(sub)), valor=float(sub["Valor"].sum()))

    log_step(logger, "R9", "Marcação de cruzamento de prefixo (fluxo completo)", completo)
    return completo, bloqueados


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
    controls: RunControls,
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

    # V2 — retorno do reclassificador com chave repetida duplicaria lançamentos no
    # join de volta: mesmo padrão R2 (dedup se idêntica / ERRO se conflitante).
    recl_slim = resolve_duplicatas_cadastro(
        recl_slim, chave=["Chave única"], conteudo=["Conta ajustada"],
        nome_input="base_reclassificada (retorno do Reclassificador)",
        etapa="T126/Reclassificador", controls=controls)

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
    # V2/N1: coerção a string (código de 25 dígitos estoura float64 no write)
    j_out["Conta destino"] = coerce_conta_str(j_out["Conta ajustada"])
    j_out = j_out.drop(
        columns=[c for c in ("Chave única", "Conta ajustada") if c in j_out.columns]
    )
    log_step(logger, "126J", "Reclassified J (Conta destino = Conta ajustada)", j_out)

    # Tool 133 — R output (current-run records not covered): Conta destino = Conta Contabil
    if len(r_out) > 0 and "Conta Contabil" in r_out.columns:
        r_out["Conta destino"] = coerce_conta_str(r_out["Conta Contabil"])  # V2/N1
        # V2/E — fallback silencioso na v1: agora o usuário vê quantos lançamentos o
        # reclassificador não cobriu (ficaram com a própria conta de origem)
        # Correção 2 (2026-07-28) — razão por lançamento (Status DESTINO_SUSPEITO):
        # o código já existia no CATALOGO_RAZOES e no spec, mas nenhum
        # registra_razao o emitia — os lançamentos do fallback saíam do base_final
        # sem o Motivo, contra o spec.
        controls.registra_razao(r_out["ID Lançamento"], "RECLASSIFICADOR_FALLBACK")
        controls.add("T126/Reclassificador", "WARNING", "RECLASSIFICADOR_FALLBACK",
                     f"{len(r_out):,} lançamento(s) enviados ao Reclassificador não vieram "
                     f"no retorno — mantiveram a própria Conta Contábil como destino.",
                     registros=len(r_out), valor=float(r_out["Valor"].sum()))
    log_step(logger, "133", "Reclassified R → Conta destino = Conta Contabil", r_out)

    # V2/E — linhas do retorno sem match no mês corrente (descartadas, paridade Alteryx)
    if len(l_out) > 0:
        controls.add("T126/Reclassificador", "INFO", "RETORNO_SEM_MATCH",
                     f"{len(l_out):,} linha(s) do retorno do Reclassificador não correspondem "
                     f"a nenhum lançamento desta execução — ignoradas (não são linhas da base).",
                     registros=len(l_out))

    # Tool 134 — union J + R'
    combined = pd.concat([j_out, r_out], ignore_index=True, sort=False)
    log_step(logger, "134", "Union J + R", combined)

    # Tool 127 — Tipo
    combined["Tipo"] = "Reclassificador"
    log_step(logger, "127", "Tipo = Reclassificador", combined)

    # Tool 128 — drop helpers. "Pacote GCUT" mantida — ver nota em consolidate_cobranca.
    drop_cols = ["Conta GCUT", "HISTORICO2", "Nome do Fornecedor", "F1"]
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
    non_empty = [p for p in parts if len(p) > 0]
    if not non_empty:
        # V2/D3 — todos os caminhos vazios: v1 quebrava com "No objects to concatenate"
        logger.warning("[Tool 95] União final VAZIA — nenhum lançamento classificado.")
        return pd.DataFrame(columns=["Codigo Interno", "Valor", "Tipo"])
    df = pd.concat(non_empty, ignore_index=True, sort=False)
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


# =========================================================================
# V3 (2026-07-27) — RECONCILIAÇÃO DA ESPINHA
# Spec: docs/superpowers/specs/2026-07-27-v3-base-final-completa-design.md §6
# =========================================================================
def reconciliar_espinha(df_base: pd.DataFrame,
                        df_unioned: pd.DataFrame,
                        controls: RunControls) -> pd.DataFrame:
    """V3 — devolve o UNIVERSO COMPLETO da base de fechamento, marcado.

    A base bruta (espinha) é a saída; o union da cascata só diz o que aconteceu
    com cada lançamento. Quem voltou da cascata entra com o que a cascata
    calculou; quem não voltou entra com `Conta destino` = `Conta Contabil`
    (decisão D3 — nenhuma reclassificação é inventada) e `Tipo` =
    "Não classificado".

    Lançamento que sumiu SEM razão registrada por nenhum stage é bug de
    orquestração — é a rede de segurança que impede um stage futuro de perder
    linha em silêncio. V3.1 (2026-07-29): esse caso PAROU de abortar a rodada.
    A linha volta à base com a conta de ORIGEM como `Conta destino` (valor
    conservado) e `Status = FALHA_INTERNA`; o evento sai como WARNING
    `PERDA_NAO_EXPLICADA` (não ERRO — o invariante "severidade ERRO sempre
    aborta a rodada" continua valendo). O objetivo é que o mês possa ser
    carregado mesmo quando a própria ferramenta falha, com a linha visível e
    auditável na base final em vez de a rodada morrer sem entregar nada.

    Simetricamente, um ID que aparece na união e NÃO existe na espinha
    (`CONSERVACAO_VIOLADA`) continua sendo outro bug de orquestração — e esse
    ramo CONTINUA bloqueante (ERRO, aborta a rodada): a conservação vale nos
    dois sentidos, mas só o sentido "sumiu" ganhou a rede de segurança nova.
    """
    if len(df_base) == 0:
        raise RuntimeError(
            "reconciliar_espinha recebeu df_base vazio — bug de orquestração "
            "(o pipeline já deveria ter bloqueado em BASE_VAZIA antes de chegar aqui).")
    if df_base["ID Lançamento"].isna().any():
        raise RuntimeError(
            "reconciliar_espinha recebeu 'ID Lançamento' nulo na espinha — bug de "
            "orquestração (a coluna é gerada pelo próprio read_base_fechamento; "
            "nulo ali não é input sujo, é bug interno).")
    # A união também: um ID nulo vindo de lá (stage que o perdeu num outer join)
    # cairia em `sobrando` e estouraria ValueError cru DENTRO da montagem da
    # mensagem de CONSERVACAO_VIOLADA — o bloqueio acionável nunca sairia.
    if len(df_unioned) > 0 and df_unioned["ID Lançamento"].isna().any():
        raise RuntimeError(
            "reconciliar_espinha recebeu 'ID Lançamento' nulo na união final — bug de "
            "orquestração (algum stage perdeu o ID no caminho; a coluna viaja da "
            "espinha e não pode ser nula).")

    ids_base = df_base["ID Lançamento"]
    ids_union = df_unioned["ID Lançamento"] if len(df_unioned) else pd.Series([], dtype="int64")

    faltantes = df_base[~ids_base.isin(ids_union)].copy()

    ids_faltantes = [int(i) for i in faltantes["ID Lançamento"]]
    codigos = controls.codigos_por_id()
    # V3.1 — isenção estreitada: só os 3 códigos que de fato REMOVEM o lançamento
    # explicam a ausência. Razão informativa é registrada em linha que SEGUE no
    # fluxo, então se essa linha sumiu, é defeito do motor e a rede tem de pegar.
    sem_razao = [
        i for i in ids_faltantes
        if not (set(codigos.get(i, ())) & CODIGOS_QUE_REMOVEM_LANCAMENTO)
    ]
    if sem_razao:
        exemplos = ", ".join(str(i) for i in sem_razao[:10])
        valor = float(faltantes.loc[faltantes["ID Lançamento"].isin(sem_razao), "Valor"].sum())
        # V3.1 (dono, 2026-07-29) — deixou de abortar. Era o ÚNICO código bloqueante
        # que o usuário não tinha como resolver: todos os outros nomeiam um input a
        # corrigir, este diz "o motor perdeu uma linha", e o mês não saía. Agora o
        # lançamento volta com a conta de origem (valor conservado, base carregável)
        # marcado com Status FALHA_INTERNA, e o evento fica no log/aba Avisos/tabela
        # warnings para auditoria da ferramenta. Severidade WARNING de propósito: o
        # invariante "severidade ERRO sempre aborta a rodada" continua valendo.
        controls.registra_razao(sem_razao, "PERDA_NAO_EXPLICADA")
        controls.add(
            "V3/Reconciliação", "WARNING", "PERDA_NAO_EXPLICADA",
            f"{len(sem_razao):,} lançamento(s) (R$ {valor:,.2f}) sumiram do tratamento "
            f"sem que nenhuma etapa registrasse o motivo. Isso é falha da própria "
            f"ferramenta, não do arquivo enviado: eles voltaram à base com a conta de "
            f"origem e Status 'FALHA_INTERNA'. NÃO carregue essas linhas e avise o time "
            f"responsável pela ferramenta. ID Lançamento (até 10): {exemplos}",
            registros=len(sem_razao), valor=valor)

    if len(df_unioned) > 0:
        sobrando = df_unioned[~df_unioned["ID Lançamento"].isin(ids_base)]
        if len(sobrando) > 0:
            exemplos = ", ".join(str(int(i)) for i in sobrando["ID Lançamento"].head(10))
            valor = float(sobrando["Valor"].sum())
            controls.erro(
                "V3/Reconciliação", "CONSERVACAO_VIOLADA",
                f"{len(sobrando):,} lançamento(s) (R$ {valor:,.2f}) apareceram na saída da "
                f"cascata sem existir na Base de Fechamento. Isso é falha do próprio "
                f"motor, não do input — a base_final NÃO deve ser usada. "
                f"ID Lançamento (até 10): {exemplos}",
                registros=len(sobrando), valor=valor)

    if len(faltantes) > 0:
        faltantes["Conta destino"] = coerce_conta_str(faltantes["Conta Contabil"])
        faltantes["Tipo"] = "Não classificado"

    partes = [p for p in (df_unioned, faltantes) if len(p) > 0]
    completa = pd.concat(partes, ignore_index=True, sort=False)
    completa = completa.sort_values("ID Lançamento", kind="stable").reset_index(drop=True)

    sem_razao_default = (STATUS_OK, "", "")

    # V3.1 (Correção 2) — leitura ÚNICA e INCONDICIONAL do snapshot, feita aqui,
    # imediatamente antes da estampagem: tem de ser POSTERIOR a toda e qualquer
    # chamada de `controls.registra_razao` desta função (ex.: PERDA_NAO_EXPLICADA
    # acima), senão as linhas marcadas depois do snapshot saem com Status/Motivo/
    # Ação vazios em silêncio — esse bug já aconteceu neste projeto (Task 9 da v3,
    # quando a leitura antecipada não era relida após uma razão nova).
    marcacao = controls.marcacao_por_id()
    # Correção 3: uma unica passada resolve a tupla (Status, Motivo, Acao) por ID e
    # expande nas 3 colunas de uma vez, em vez de 3 .map() repetindo o mesmo lookup.
    tuplas = [marcacao.get(int(i), sem_razao_default) for i in completa["ID Lançamento"]]
    completa[["Status", "Motivo", "Ação Recomendada"]] = pd.DataFrame(
        tuplas, columns=["Status", "Motivo", "Ação Recomendada"], index=completa.index)

    logger.info(f"[V3] Espinha reconciliada (universo completo): "
                f"{len(completa):,} lançamentos.")
    n_marcados = int((completa["Status"] != STATUS_OK).sum())
    logger.info(f"[V3] {len(completa):,} lançamentos na base completa; "
                f"{n_marcados:,} com Status != OK.")
    return completa


def build_final_consolidated(df_completa: pd.DataFrame,
                             controls: RunControls) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Tools 110 + 113 + 111 + 115 + 114 — Record IDs, chaves ERP, data e select final.

    V3 (2026-07-27): recebe a ESPINHA RECONCILIADA (universo completo), não o union.
    O `cumcount` do Tool 110 passa a rodar sobre a base completa ordenada por
    `ID Lançamento`, então o sufixo `_N` do Índice ERP volta a significar só
    duplicata NATIVA. Devolve (aba1, aba2): aba1 = universo gerencial, aba2 =
    lançamentos de Classe de Valor excluída (Status FORA_DE_ESCOPO).

    V2/D5: datas de `Mes` que o parser não entende viravam DateTime_Out vazio em
    SILÊNCIO (errors="coerce"); a v2 fazia ERRO se 100% inválido, WARNING se parcial.
    V3/D6: nunca bloqueia — os dois casos são o MESMO aviso (WARNING `MES_INVALIDO`),
    e cada lançamento com `Mes` inválido leva a razão `MES_INVALIDO` (Status
    DADO_INVALIDO) para a base final, com DateTime_Out vazio.

    V3 (2026-07-28, Correção 1): a VALIDAÇÃO de 'Mes' (registra_razao +
    controls.add do WARNING `MES_INVALIDO`) saiu daqui — mora agora em
    `validar_mes`, chamada ANTES de `reconciliar_espinha` (ver docstring de lá
    pro racional). Esta função só FORMATA (converte de novo, idempotente, e
    escreve `DateTime_Out`) — por isso `controls` deixou de ser usado no corpo,
    mas o parâmetro fica na assinatura (reservado: pipeline.py e outras tasks
    chamam esta função com `controls`; não é usado aqui, mas remover quebraria
    o contrato de chamada).
    """
    df = df_completa.copy()

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
    # V3 (2026-07-28, Correção 1) — a validação de 'Mes' (razão MES_INVALIDO) já rodou
    # em `validar_mes`, antes de `reconciliar_espinha`; aqui é só a formatação de saída.
    mes_dt = pd.to_datetime(df["Mes"], errors="coerce")
    df["DateTime_Out"] = mes_dt.dt.strftime("%d/%m/%Y")
    log_step(logger, "115", "Format Mes → DateTime_Out (dd/MM/yyyy)", df)

    # Tool 114 — select final (V3: 19 colunas, as 15 originais + as 4 novas)
    keep = [c for c in FINAL_OUTPUT_SCHEMA if c in df.columns]
    missing = set(FINAL_OUTPUT_SCHEMA) - set(df.columns)
    if missing:
        logger.warning(f"[Tool 114] Missing columns in final schema: {missing}")
    df_out = df[keep].copy()

    # V3 — split em 2 abas, ambas no mesmo schema de carga
    fora = df_out["Status"] == STATUS_FORA_DE_ESCOPO
    aba1 = df_out[~fora].reset_index(drop=True)
    aba2 = df_out[fora].reset_index(drop=True)
    log_step(logger, "114", "Final consolidated — aba 1 (universo gerencial)", aba1)
    log_step(logger, "114", "Final consolidated — aba 2 (fora de escopo)", aba2)
    return aba1, aba2


# =========================================================================
# NOVO — RELATÓRIO DE EXCEÇÕES (Tool 200/201) — não existe no Alteryx
# =========================================================================
def build_exceptions(dropped_contas, df_base_pos_t63, df_cc_cadastro,
                     bloqueados_r9, correcoes_r9, controls: RunControls):
    """
    Monta os quatro DataFrames de exceções:
      - Contas Contábeis e Centros de Custo: Código · Descrição · Valor, somados por código.
      - Bloqueio Prefixo Conta: grão de lançamento (ver detalhe abaixo).
      - Correção Automática de Conta: grão de lançamento (ver detalhe abaixo).

      - Contas Contábeis (Tool 200): contas da base SEM match na Estrutura de Contas
        (o L do Tool 62 — exatamente as linhas hoje descartadas).
      - Centros de Custo (Tool 201): CCs presentes na base que NÃO existem no cadastro
        de Entidades x CC.
      - Bloqueio Prefixo Conta (V2/R9, 2026-07-20; comportamento de MARCAÇÃO
        desde a reescrita V3 de 2026-07-27): lançamentos de Arbitrado
        classe/Reclassificador que cruzaram família de prefixo (818↔817/819 ou
        8↔não-8) — ficam no base_final com Status 'DESTINO_SUSPEITO' (NÃO são
        mais excluídos; `marcar_bloqueio_prefixo` só marca e devolve o
        DataFrame inteiro) — grão = 1 linha por lançamento (diferente das duas
        outras abas, que agregam por código).
      - Correção Automática de Conta (V2/R10, 2026-07-21): lançamentos de
        Arbitrado classe/Reclassificador cuja Conta destino foi trocada
        automaticamente por uma conta compatível na MESMA Conta OM (sem
        bloqueio nem mudança de cadastro) — grão de lançamento.

    V2/R6 (decisões 2026-07-09):
      - as DUAS abas usam o MESMO universo — a base PÓS-T63 (o que o pipeline
        processa); na v1 a aba de CC usava a base bruta (universos inconsistentes, N5);
      - a comparação de códigos de CC é por STRING normalizada (a v1 usava
        pd.to_numeric, que transformava CC alfanumérico em NaN → falso positivo de
        "não cadastrado", N6).

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
            and BASE_CC_CODE_COL in df_base_pos_t63.columns):
        # V2/R6-N6: comparação por string normalizada (preserva CC alfanumérico)
        cadastro = set(
            _normalize_join_key(df_cc_cadastro[CC_CADASTRO_CODE_COL]).dropna()
        )
        # V3 — 'ID Lançamento' entra no recorte para permitir a marcação por lançamento
        # (a agregação abaixo é nomeada, então a coluna extra não muda `centros`).
        base_cc = df_base_pos_t63[[BASE_CC_CODE_COL, BASE_CC_DESC_COL, "Valor",
                                   "ID Lançamento"]].copy()
        code_norm = _normalize_join_key(base_cc[BASE_CC_CODE_COL])
        nao_encontrados = base_cc[~code_norm.isin(cadastro)]
        if len(nao_encontrados) > 0:
            controls.registra_razao(nao_encontrados["ID Lançamento"], "CC_NAO_CADASTRADO")
            g = (nao_encontrados.groupby(BASE_CC_CODE_COL, dropna=False)
                               .agg(Descrição=(BASE_CC_DESC_COL, "first"),
                                    Valor=("Valor", "sum"))
                               .reset_index()
                               .rename(columns={BASE_CC_CODE_COL: "Código"}))
            centros = g[cols].sort_values("Valor", ascending=False).reset_index(drop=True)
            # V2/R6 — reportback visível (antes o achado morava só no excel)
            controls.add("T201/CC", "WARNING", "CC_NAO_CADASTRADO",
                         f"{len(centros):,} Centro(s) de Custo da base não existem no "
                         f"cadastro de Entidades×CC — detalhe na aba 'Centros de Custo' "
                         f"do relatório de exceções (a classificação NÃO é afetada).",
                         registros=int(len(nao_encontrados)),
                         valor=float(nao_encontrados["Valor"].sum()))
        else:
            centros = pd.DataFrame(columns=cols)
    else:
        centros = pd.DataFrame(columns=cols)
    log_step(logger, "201", "Exceções — Centros de Custo não cadastrados", centros)

    # --- V2/R9: Bloqueio de prefixo de Conta Contábil (Arbitrado + Reclassificador) ---
    cols_r9 = ["Codigo Interno", "Tipo", "Conta Contabil", "Nome da Conta", "Conta destino",
               "Nome conta contábil", "Nome da Classe de Valor", "Valor", "Centro de Custo",
               "Nome do Centro de Custo", "Motivo", "Conta OM", "Ação Recomendada"]
    if bloqueados_r9 is not None and len(bloqueados_r9) > 0:
        bloqueio_prefixo = (bloqueados_r9.reindex(columns=cols_r9)
                            .sort_values("Valor", ascending=False)
                            .reset_index(drop=True))
    else:
        bloqueio_prefixo = pd.DataFrame(columns=cols_r9)
    log_step(logger, "R9", "Exceções — Bloqueio de prefixo de Conta", bloqueio_prefixo)

    # --- V2/R10: Correção automática de Conta destino (mesma Conta OM) ---
    cols_correcao = ["Codigo Interno", "Tipo", "Nome da Classe de Valor", "Valor",
                     "Conta destino original", "Conta destino corrigida", "Conta OM"]
    if correcoes_r9 is not None and len(correcoes_r9) > 0:
        correcao_automatica = (correcoes_r9.reindex(columns=cols_correcao)
                               .sort_values("Valor", ascending=False)
                               .reset_index(drop=True))
    else:
        correcao_automatica = pd.DataFrame(columns=cols_correcao)
    log_step(logger, "R10", "Exceções — Correção Automática de Conta", correcao_automatica)

    return {"contas": contas, "centros_custo": centros, "bloqueio_prefixo": bloqueio_prefixo,
            "correcao_automatica": correcao_automatica}


# =========================================================================
# NOVO — VALOR POR PACOTE (antes × depois da cascata de classificação) —
# não existe no Alteryx
# =========================================================================
def build_pacote_report(df_unioned: pd.DataFrame, df_estrutura: pd.DataFrame) -> pd.DataFrame:
    """
    Monta a comparação de Valor por Pacote antes × depois de toda a cascata de
    classificação (não só do Reclassificador via API — qualquer um dos 6 caminhos
    pode levar um lançamento a uma Conta destino de Pacote diferente do original).

    "Antes": Pacote GCUT, já calculado no Tool 62 a partir da Conta Contabil
    ORIGINAL do lançamento (join × Estrutura de Contas), preservado até aqui em vez
    de descartado nos Tools 99/96/97/120/98/128.
    "Depois": Pacote da Conta destino final — recalculado aqui cruzando de novo com
    a Estrutura de Contas, já que Conta destino só é decidida ao fim da cascata.

    Contas destino sem match na Estrutura (ex.: a conta hardcoda de Consultoria) e
    Pacotes em branco caem no bucket "(sem pacote)", para não perder Valor do total.

    Correção 1 (2026-07-28) — 'Pacote GCUT' só nasce no rename do Tool 62
    (`preprocess_base`), quando pelo menos uma linha casa na Estrutura de Contas.
    Se a cascata inteira volta vazia (ex.: toda a base cai no filtro de Classe de
    Valor excluída, ou nenhuma Conta Contabil casa na Estrutura), a base completa
    devolvida por `reconciliar_espinha` não tem essa coluna — sem o guard abaixo,
    o acesso direto a `df_unioned["Pacote GCUT"]` estoura KeyError. Ausência de
    coluna é tratada como "todo mundo sem pacote de origem" — o mesmo destino que
    o `.fillna` já dá pro valor nulo quando a coluna existe.
    """
    lookup = (
        df_estrutura[["CONTA CONTÁBIL", "PACOTE"]]
        .assign(_jk=lambda d: _normalize_join_key(d["CONTA CONTÁBIL"]))
        .dropna(subset=["_jk"])
        .drop_duplicates(subset="_jk")
        .set_index("_jk")["PACOTE"]
    )
    pacote_destino = (
        _normalize_join_key(df_unioned["Conta destino"]).map(lookup).fillna("(sem pacote)")
    )
    if "Pacote GCUT" in df_unioned.columns:
        pacote_origem = df_unioned["Pacote GCUT"].fillna("(sem pacote)")
    else:
        pacote_origem = pd.Series("(sem pacote)", index=df_unioned.index)

    antes = df_unioned.groupby(pacote_origem)["Valor"].sum()
    antes.index.name = "Pacote"
    depois = df_unioned.groupby(pacote_destino)["Valor"].sum()
    depois.index.name = "Pacote"

    report = pd.concat(
        [antes.rename("Valor antes Reclassf."), depois.rename("Valor pós Reclassf.")],
        axis=1,
    ).fillna(0.0).reset_index()
    report = report.sort_values("Pacote").reset_index(drop=True)
    log_step(logger, "NOVO", "Valor por Pacote (antes × depois)", report)
    return report
