# Schema do output — `base_final` + excel de exceções + tabela `warnings`

> **Para o time de analytics.** Referência de colunas/tipos dos outputs da **v3**, para
> integração/monitoramento programático. Extraído diretamente do código (`engine/transforms.py`,
> `engine/controls.py`, `engine/config.py`, `bbv001_reclassificacao.py`) — cada tabela abaixo cita o
> arquivo/linha de origem. Cobre o excel de exceções (`BBV001-Excecoes_nao_cadastrados.xlsx` — nome
> real gravado pelo código, `bbv001_reclassificacao.py:77`/`:363`), o `OutputField` opcional
> `warnings` e — **novo na v3** — o schema da `base_final`
> (§8: 2 abas × 19 colunas, enum de `Status`, catálogo de `Motivo` por lançamento). Não inclui a
> `base_reclassificador`. Racional de cada aba: `README_V3.md` §"Transparência ao usuário"; racional
> de cada controle: `docs/SPEC_V2.md` + `docs/superpowers/specs/2026-07-27-v3-base-final-completa-design.md`
> do projeto de análise.

## Como o excel é montado

`engine.transforms.build_exceptions(...)` monta 4 dos 5 `DataFrame`s (todas as abas exceto "Avisos");
`bbv001_reclassificacao.py::_montar_excel_excecoes(exc, aviso_rows)` escreve as 5 abas via
`pandas.ExcelWriter(engine="openpyxl")`, na ordem abaixo. **Nenhuma aba nunca é omitida** — se não há
dado, a aba é escrita vazia (0 linhas) com o mesmo schema de colunas do caso populado (nunca uma coluna
a menos). Todos os valores numéricos (`Valor`) são `float64` puro escrito pelo `pandas`/`openpyxl` —
sem formatação de moeda aplicada na célula.

## 1. Aba "Contas Contábeis"

Grão: 1 linha por `Conta Contabil` sem match na Estrutura de Contas (agregado). Fonte:
`transforms.build_exceptions`, bloco Tool 200 (`engine/transforms.py:1498-1509`).

| Coluna | Tipo | Nulo? | Descrição |
|---|---|---|---|
| Código | Texto | Não | Conta Contábil da base sem match na Estrutura de Contas |
| Descrição | Texto | Não | `Nome da Conta` vindo da base do realizado (não existe descrição cadastrada — por definição, a conta não está na Estrutura) |
| Valor | Numérico (float) | Não | soma do `Valor` de todos os lançamentos com esse Código |

Ordenação: `Valor` decrescente. Vazio quando não há conta sem cadastro nessa rodada.

## 2. Aba "Centros de Custo"

Grão: 1 linha por Centro de Custo sem match no cadastro de Entidades×CC (agregado). Universo: base
pós-filtro de Classes de Valor (T63) — mesmo universo da aba "Contas Contábeis" (R6). Fonte:
`transforms.build_exceptions`, bloco Tool 201 (`engine/transforms.py:1511-1543`).

| Coluna | Tipo | Nulo? | Descrição |
|---|---|---|---|
| Código | Texto | Não | Centro de Custo (alfanumérico preservado — comparação por string normalizada, não `to_numeric`) |
| Descrição | Texto | Não | `Nome do Centro de Custo` vindo da base do realizado |
| Valor | Numérico (float) | Não | soma do `Valor` de todos os lançamentos com esse Código |

Ordenação: `Valor` decrescente. Vazio se o cadastro de Entidades×CC não foi enviado (opcional) ou se
todos os CCs da base batem com o cadastro.

## 3. Aba "Correção Automática de Conta" (R10)

Grão: 1 linha por lançamento cuja `Conta destino` foi reescrita automaticamente (nunca o cadastro).
Fonte: `transforms.autocorrigir_conta_om` (`engine/transforms.py:849-979`, retorno `correcoes`) →
`transforms.build_exceptions`, bloco R10 (`engine/transforms.py:1557-1566`).

| Coluna | Tipo | Nulo? | Descrição |
|---|---|---|---|
| Codigo Interno | Inteiro | Não | identificador do lançamento na Base de Fechamento |
| Tipo | Texto — enum `"Arbitrado classe"` \| `"Reclassificador"` | Não | estágio que originou o lançamento |
| Nome da Classe de Valor | Texto | Não | |
| Valor | Numérico (float) | Não | valor do lançamento |
| Conta destino original | Texto (conta de 25 dígitos, sempre string — `coerce_conta_str`) | Não | conta que o Arbitrado/Reclassificador tinha escolhido ANTES da correção |
| Conta destino corrigida | Texto (25 dígitos) | Não | conta final, na mesma Conta OM, com família compatível com a origem |
| Conta OM | Texto | Não | Conta OM comum às duas contas (origem do lookup: Estrutura de Contas, não o Único CV) |

Ordenação: `Valor` decrescente. Vazio quando nenhum lançamento precisou de correção nessa rodada.

## 4. Aba "Bloqueio Prefixo Conta" (R9 + R10)

Grão: 1 linha por lançamento que **sobrou sinalizado** depois da correção automática (R10) — ou seja,
o residual genuíno do R9. Fonte: `transforms.marcar_bloqueio_prefixo`
(`engine/transforms.py:982-1076`, 2º retorno) → `transforms.build_exceptions`, bloco R9
(`engine/transforms.py:1545-1555`).

⚠️ **V3:** o nome da aba é herdado, mas **nada é bloqueado** — o lançamento continua no `base_final`
com `Status = DESTINO_SUSPEITO` (decisão D4). Esta aba é a visão **por cadastro a corrigir**; a visão
por lançamento é a própria `base_final` (§8).

| Coluna | Tipo | Nulo? | Descrição |
|---|---|---|---|
| Codigo Interno | Inteiro | Não | |
| Tipo | Texto — enum `"Arbitrado classe"` \| `"Reclassificador"` | Não | |
| Conta Contabil | Texto (25 dígitos) | Não | conta de ORIGEM do lançamento |
| Nome da Conta | Texto | Não | |
| Conta destino | Texto (25 dígitos) | Não | conta que causou o cruzamento de família (já pós-tentativa de correção automática) |
| Nome conta contábil | Texto | **Sim** | vem do Único CV — sempre nulo em linhas de origem Reclassificador (esse caminho não passa pelo Único CV) |
| Nome da Classe de Valor | Texto | Não | |
| Valor | Numérico (float) | Não | pode ser **negativo** na soma agregada dos avisos (ver §"Avisos" abaixo) mesmo que cada linha individual não seja |
| Centro de Custo | Texto | Não | |
| Nome do Centro de Custo | Texto | Não | |
| Motivo | Texto — enum `"PREFIXO_818_CRUZADO"` \| `"PREFIXO_FAMILIA_8_CRUZADO"` | Não | qual das 2 regras de família disparou (nunca as duas ao mesmo tempo — mutuamente exclusivas por construção) |
| Conta OM | Texto | Não (na wiring atual — `autocorrigir_conta_om` sempre roda antes) | Conta OM da `Conta destino`, usada para montar `Ação Recomendada` |
| Ação Recomendada | Texto | Não | texto pronto: orienta cadastro (Tipo Arbitrado classe) ou pede revisão manual (Tipo Reclassificador) |

Ordenação: `Valor` decrescente. Vazio quando não há residual sinalizado (correção automática resolveu
tudo, ou não havia cruzamento de família nessa rodada).

## 5. Aba "Avisos" (sempre presente)

Grão: 1 linha por evento de controle disparado na rodada. Fonte: `controls.RunControls.to_rows()`
(`engine/controls.py:93-99`), renomeado pelas colunas do PPR em
`bbv001_reclassificacao.py:198-201`.

| Coluna | Tipo | Nulo? | Descrição |
|---|---|---|---|
| Etapa | Texto | Não | identificador do estágio/controle (ex.: `"T63"`, `"R8/censo"`, `"R9/Prefixo"`) — ver catálogo completo abaixo |
| Severidade | Texto — enum `"ERRO"` \| `"WARNING"` \| `"INFO"` | Não | ⚠️ **na prática, só `WARNING`/`INFO` aparecem aqui** — ver nota crítica abaixo |
| Código | Texto — enum, catálogo completo abaixo | Não | |
| Mensagem | Texto | Não | mensagem final já com a ação recomendada, pronta para o usuário |
| Registros | Inteiro | Não (default `0`) | quantidade de lançamentos afetados por esse evento |
| Valor | Numérico (float) | Não (default `0.0`) | soma do `Valor` dos lançamentos afetados — **pode ser negativo** (débitos/créditos se cancelam na soma; ex. real: `PREFIXO_FAMILIA_8_CRUZADO` deu `-R$ 3.596.588,37` em Jun26) |

0 ou mais linhas por execução. Mesmo schema/conteúdo da tabela `warnings` (ver §7).

### ⚠️ Nota crítica: nem todo código bloqueante chega até aqui

`RunControls.erro(...)` registra o evento em `self.avisos` (severidade `"ERRO"`) e **imediatamente
levanta `BloqueioError`**, que interrompe `run_pipeline()` inteiro. `bbv001_reclassificacao.py::main()`
captura esse `BloqueioError`, loga e re-levanta como `RuntimeError` — **sem nunca chamar
`build_exceptions`/`_montar_excel_excecoes`**. Ou seja: **todo evento de severidade ERRO aborta a
rodada antes de qualquer excel ser gravado** — o `Aviso` existe só na memória daquela execução que
falhou, nunca em um arquivo. Um evento ERRO só chega até o usuário/analytics via a **mensagem de erro
da execução** (status da rodada no PPR), nunca via a aba "Avisos" nem via a tabela `warnings`.

Mais ainda: 3 códigos (`FORMATO_XLSB`, `COLUNAS_FALTANDO`, e uma segunda origem de
`INPUT_OBRIGATORIO_AUSENTE` específica do `depara_grupos`) são levantados **direto em `io_utils.py`**
como `BloqueioError`, **sem nunca passar por `RunControls.erro()`** — nem chegam a existir como `Aviso`
em memória, só como exceção. E a checagem D1 de inputs obrigatórios ausentes
(`bbv001_reclassificacao.py:264-269`) roda **antes de `pipeline.run_pipeline()` ser chamado** (não há
`RunControls` instanciado ainda) e levanta um `RuntimeError` puro, com o código embutido no TEXTO da
mensagem (`"[INPUT_OBRIGATORIO_AUSENTE] Os seguintes inputs..."`), não como campo estruturado.

**Implicação prática para monitoramento automatizado:** consumir só a aba "Avisos"/tabela `warnings`
NÃO é suficiente para detectar falhas de execução — é preciso também monitorar o **status/mensagem de
erro da rodada no PPR**, que é o único lugar onde qualquer evento de severidade ERRO aparece.

**V3 — a assimetria continua, mas a lista de ERROs encurtou.** Só **3 famílias** ainda abortam a
rodada, **7 códigos** no total: falha **estrutural de leitura** (`INPUT_OBRIGATORIO_AUSENTE`,
`COLUNAS_FALTANDO`, `FORMATO_XLSB`, `BASE_VAZIA`), **duplicação de linha** (`DUPLICACAO_FABRICADA`,
`CADASTRO_CONFLITANTE`) e **violação da integridade da espinha** (`CONSERVACAO_VIOLADA` — único
código da família). Tudo o mais que a v2 tratava como ERRO virou WARNING **e** marcação por
lançamento na `base_final` (§8): `GRUPO_NAO_CADASTRADO`, `MES_INVALIDO` e
`DEPARA_CONFLITO_MESMA_COMPETENCIA`.

**V3.1 (2026-07-29) — `PERDA_NAO_EXPLICADA` saiu da família de bloqueio.** Era o 8º código
bloqueante e o único que o usuário **não tinha como resolver** (os outros nomeiam um input a
corrigir; esse diz "o motor perdeu uma linha"). Agora o lançamento **volta à base**, na aba 1, com
`Conta destino` = conta de **origem**, `Tipo = "Não classificado"`, `Status = FALHA_INTERNA` (§8.3)
e `Motivo` contendo `PERDA_NAO_EXPLICADA`; o evento sai com severidade **WARNING**, então — ao
contrário do que acontecia quando era ERRO — ele **chega** ao log, à aba "Avisos" e à tabela
`warnings`. O invariante desta seção **não mudou**: severidade ERRO continua abortando sempre; foi
exatamente por isso que a severidade escolhida foi WARNING. O ramo espelho — ID que **sobra** na
saída sem existir na base bruta — **continua bloqueando** com `CONSERVACAO_VIOLADA`: não há como
devolver uma linha que não deveria existir, só apagá-la, e apagar em silêncio é pior que parar.

## 6. Catálogo completo de códigos (Etapa · Severidade · Código)

Extraído de todo `controls.add(...)`/`controls.erro(...)` no código (`engine/controls.py`,
`engine/pipeline.py`, `engine/transforms.py`) + os 3 códigos levantados direto em `io_utils.py` sem
passar por `RunControls` (marcados **†**, nunca aparecem na aba "Avisos"/tabela `warnings`).

| Etapa | Severidade | Código | Aparece na aba "Avisos"? |
|---|---|---|---|
| — | ERRO † | `FORMATO_XLSB` | Não — só na mensagem de erro da execução |
| — | ERRO † | `COLUNAS_FALTANDO` | Não — idem |
| — | ERRO † | `INPUT_OBRIGATORIO_AUSENTE` | Não — idem (2 origens: `io_utils.py` e o check D1 upfront em `main()`) |
| T4/Base | ERRO | `BASE_VAZIA` | Não (severidade ERRO aborta antes do excel) |
| (variável, por cadastro) | ERRO | `CADASTRO_CONFLITANTE` | Não |
| R8/censo | ERRO | `DUPLICACAO_FABRICADA` | Não |
| A1/conservacao · V3/Reconciliação | ERRO | `CONSERVACAO_VIOLADA` | Não — **2 etapas de origem** na v3: `A1/conservacao` (identidade de conjunto no fim) e `V3/Reconciliação` (ID na união que não existe na espinha). Filtro por código pega os dois; filtro por etapa, não |
| T63 | INFO | `CV_EXCLUIDA` | Sim |
| V3/Reconciliação | WARNING | `PERDA_NAO_EXPLICADA` | Sim — **novo na v3**; era ERRO na v3.0 e por isso nunca chegava aqui. Na **v3.1** virou WARNING: o lançamento volta marcado com `Status = FALHA_INTERNA` (§8.3) e o evento aparece na aba "Avisos"/tabela `warnings` |
| T62/Estrutura | WARNING | `CONTA_NAO_CADASTRADA` | Sim |
| (variável, por cadastro) | WARNING | `CADASTRO_DUPLICADO` | Sim |
| T49/Unico CV | WARNING | `CLASSE_NAO_CADASTRADA` | Sim |
| T49/Unico CV | WARNING | `CLASSE_RENOMEADA` | Sim |
| T49/Unico CV | WARNING | `CLASSE_SEM_UNICO_CV` | Sim |
| T11/De-Para | WARNING | `DEPARA_CONFLITO_RESOLVIDO_POR_VALOR` | Sim |
| T11/De-Para | WARNING | `DEPARA_CONFLITO_MESMA_COMPETENCIA` | Sim — **era ERRO na v2** (V3/D6): com empate de valor, aplica a 1ª em ordem de leitura e marca o lançamento |
| T11/De-Para | WARNING | `DEPARA_REGRA_MAIS_RECENTE` | Sim |
| T11/De-Para | INFO | `DEPARA_COLAPSO_RECENCIA` | Sim |
| T88/Grupos | WARNING | `GRUPO_NAO_CADASTRADO` | Sim — **era ERRO na v2** (V3/D6) |
| T115/Datas | WARNING | `MES_INVALIDO` | Sim — **era ERRO na v2** (V3/D6). Aviso único: cobre tanto `Mes` 100% ilegível quanto parcialmente ilegível (o antigo `MES_PARCIALMENTE_INVALIDO` deixou de existir) |
| T126/Reclassificador | WARNING | `RECLASSIFICADOR_FALLBACK` | Sim |
| T126/Reclassificador | INFO | `RETORNO_SEM_MATCH` | Sim |
| T201/CC | WARNING | `CC_NAO_CADASTRADO` | Sim |
| R8/censo | INFO | `DUPLICATA_NATIVA` | Sim |
| R8/censo | INFO | `CENSO_OK` | Sim |
| A1/conservacao | INFO | `CONSERVACAO_OK` | Sim |
| R9/Prefixo | WARNING | `PREFIXO_818_CRUZADO` | Sim |
| R9/Prefixo | WARNING | `PREFIXO_FAMILIA_8_CRUZADO` | Sim |
| R10/AutoCorrecao | INFO | `PREFIXO_CORRIGIDO_AUTOMATICAMENTE` | Sim |

**Códigos da v2 que deixaram de existir na v3** (não são emitidos por nenhum `controls.add`/`erro` —
conferido por grep em `bbv001_reclassificacao-v3/`): `BASE_VAZIA_POS_FILTROS` (o universo agora é a
base bruta, não o pós-filtro), `MES_PARCIALMENTE_INVALIDO` (fundido em `MES_INVALIDO`),
`DUPLICATA_EXATA_COLAPSADA` (o `drop_duplicates` do T132 saiu — D7) e `PREFIXO_MODO_WARNING_ATIVO`
(o toggle `r9_modo_warning` foi removido; D4 tornou o modo warning o comportamento único).
Renomeados: `PREFIXO_818_BLOQUEADO` → `PREFIXO_818_CRUZADO` e `PREFIXO_FAMILIA_8_BLOQUEADO` →
`PREFIXO_FAMILIA_8_CRUZADO` (decisão do dono 2026-07-28 — pós-D4 nada é bloqueado, e o código é o
texto que o usuário lê na coluna `Motivo`).

## 7. Tabela `warnings` (OutputField opcional do PPR)

Mesmo grão/conteúdo da aba "Avisos" (§5) — inclusive a mesma limitação de severidade ERRO nunca
aparecer. Nomes de coluna em minúsculo (contrato declarado no admin do PPR, ver
`README_V3.md` §"O que cadastrar no admin do PPR"):

| Coluna | Tipo declarado no PPR |
|---|---|
| `etapa` | Texto |
| `severidade` | Texto |
| `codigo` | Texto |
| `mensagem` | Texto |
| `registros` | Inteiro |
| `valor` | Numérico/Texto |

Cadastro deste `OutputField` é **opcional** — sem ele, o mesmo conteúdo já sai na aba "Avisos" do excel
de exceções e no resumo do `log_execucao`.

## 8. `base_final` (V3) — 2 abas × 19 colunas, com marcação por lançamento

**A mudança central da v3.** Até a v2, o `base_final` continha só os lançamentos que a cascata
conseguiu classificar — os problemáticos eram descartados (em silêncio ou como agregado no excel de
exceções) ou abortavam a rodada inteira. Na v3 a **base bruta é a espinha da saída**: o `base_final`
contém exatamente o mesmo conjunto de lançamentos que entrou na Base de Fechamento, cada linha
marcada com `Status` / `Motivo` / `Ação Recomendada`. Fonte:
`transforms.reconciliar_espinha` (`engine/transforms.py:1259-1378`) →
`transforms.build_final_consolidated` (`engine/transforms.py:1381-1452`) →
`io_utils.write_final_consolidated` (`engine/io_utils.py:302-315`). Racional:
`docs/superpowers/specs/2026-07-27-v3-base-final-completa-design.md` (decisões D1-D8).

### 8.1 As 2 abas

| Aba | Nome da planilha | O que contém | Critério |
|---|---|---|---|
| 1 | `Consolidado` (nome mantido da v2 — não quebra quem já consome) | universo gerencial | `Status != FORA_DE_ESCOPO` |
| 2 | `Fora de Escopo` | só os lançamentos de Classe de Valor excluída por configuração (Tool 63) | `Status == FORA_DE_ESCOPO`, i.e. `Nome da Classe de Valor ∈ EXCLUDED_VALUE_CLASSES` |

Mesmo schema de 19 colunas nas duas, na mesma ordem, **inclusive quando uma delas está vazia** (a aba
é sempre escrita, com o cabeçalho). É uma **partição exata**: nenhum lançamento fica nas duas nem
fora das duas. `ID Lançamento` e `Índice ERP` são únicos **globalmente** entre as 2 abas.

⚠️ **Observado nos dois meses reais testados, a aba 2 sai VAZIA** (Abr26 e Jun26 não têm nenhum
lançamento em `EXCLUDED_VALUE_CLASSES`) — o split só foi exercitado por teste sintético. Números em
§8.5.

### 8.2 As 19 colunas (`config.FINAL_OUTPUT_SCHEMA`, `engine/config.py:167-190`)

As **15 primeiras são as da v2, nas mesmas posições** (a carga do Matrix depende da ordem); as 4
últimas são novas e ficam sempre no fim.

| # | Coluna | Tipo | Vazio? | Descrição |
|---:|---|---|---|---|
| 1 | `Índice ERP` | Texto | Não | `Codigo Interno`, com sufixo `_N` a partir da 2ª ocorrência. **V3: calculado sobre a espinha** — o `_N` volta a significar só **duplicata nativa** da base (na v2 rodava sobre o union e misturava duplicata nativa com clone de join) |
| 2 | `DateTime_Out` | Texto `dd/mm/aaaa` | **Sim** | competência derivada de `Mes`; **vazio** quando `Mes` não é data válida — a linha sai com `Motivo = MES_INVALIDO` |
| 3 | `Grupo Acionista` | Texto | Não | da base bruta |
| 4 | `GC Matrix` | Texto | Não | `"GC-" + Grupo Conta` |
| 5 | `Centro de Custo` | Texto | Não | alfanumérico preservado |
| 6 | `Conta destino` | Texto (conta de 25 dígitos, sempre string) | Não | conta calculada pela cascata. Para quem a cascata não classificou, é a **conta de ORIGEM** (`Conta Contabil`) — decisão D3, nenhuma reclassificação é inventada |
| 7 | `Valor` | Numérico (float) | Não | pode ser negativo (crédito) |
| 8 | `Moeda` | Texto | Não | constante `"BRL"` |
| 9 | `CV Matrix` | Texto | Não | `Classe de Valor + "-" + Nome da Classe de Valor` |
| 10 | `Nome da Classe de Valor` | Texto | Não | |
| 11 | `Historico` | Texto | Não | |
| 12 | `Número NF` | Texto | sempre vazio | herdado do Alteryx (Tool 111 escreve `""`) |
| 13 | `Fornecedor` | Texto | Não | |
| 14 | `Conta Contabil` | Texto (25 dígitos, sempre string) | Não | conta de ORIGEM do lançamento |
| 15 | `Nome da Conta` | Texto | Não | |
| 16 | `ID Lançamento` | Inteiro | Não | **V3** — posição da linha na base bruta (`1..N`), imutável. É a **única chave verdadeira por lançamento**: `Codigo Interno` não serve, porque duplicata nativa existe e é válida. Não confundir com `RecordID` (interno, `cumcount` por `Codigo Interno`) |
| 17 | `Status` | Texto — enum fechado (§8.3) | Não | **V3** — exatamente 1 por linha; é a coluna de filtro |
| 18 | `Motivo` | Texto — 0..N códigos do catálogo (§8.4) separados por `"; "` | Sim (quando `Status = OK` e não houve correção automática) | **V3** — acumula **todos** os códigos aplicáveis à linha; nunca perde código |
| 19 | `Ação Recomendada` | Texto | Sim (idem `Motivo`) | **V3** — texto pronto para o usuário, um por código de `Motivo`, concatenados na mesma ordem |

⚠️ **Cobertura:** a mudança de significado do sufixo `_N` (coluna 1) **não foi exercitada por dado
real** em nenhum dos dois meses validados — Abr26 e Jun26 não têm duplicação de join (só Jun26 tem
duplicata nativa, e o sufixo sai idêntico ao da v2 nesse caso: 3 e 3). A propriedade está provada por
construção (`tests/bateria_v3_abr26_jun26.py`, V7c + V7s), não por um mês real com clone de join.
Detalhe: `.superpowers/sdd/task-13-report.md` §7 achado A4.

⚠️ `Conta Contabil` e `Conta destino` **têm** de viajar como texto: códigos de 25 dígitos gravados
como número estouram o float64 no write (`8172700000000000010000000` → `8172699999999999715835904`).
Verificado no arquivo gravado nos dois meses (nenhuma célula voltou como número).

### 8.3 Enum de `Status`

**6 valores** (`FALHA_INTERNA` entrou na v3.1). Ordem abaixo = **precedência** quando a linha
acumula problemas de famílias diferentes (`config.STATUS_PRECEDENCIA`) — do mais grave ao menos
grave. `FORA_DE_ESCOPO` vem primeiro porque define a **aba**; `FALHA_INTERNA` vem logo depois,
vencendo todos os demais; nas restantes, falta de destino é mais grave que destino suspeito.
**Só o `Status` é único — o `Motivo` nunca perde código.**

| `Status` | Significado | Aba | Ação de quem carrega no Matrix |
|---|---|---|---|
| `FORA_DE_ESCOPO` | filtro de escopo por desenho (Classe de Valor excluída) | 2 | nenhuma — é intencional |
| `FALHA_INTERNA` | **V3.1** — defeito da **própria ferramenta**: o lançamento sumiu da cascata sem que nenhuma etapa registrasse o motivo (`Motivo = PERDA_NAO_EXPLICADA`). Voltou à base com a `Conta destino` = conta de **origem** e `Tipo = "Não classificado"`, para o valor não se perder | 1 | ⛔ **não carregar esta linha** e **avisar o time responsável pela ferramenta**, informando o `ID Lançamento`. **Não** é problema de cadastro do cliente — não há nada a corrigir no input |
| `CADASTRO_PENDENTE` | falta cadastro a montante | 1 | cadastrar o item apontado e re-executar no ciclo seguinte |
| `DESTINO_SUSPEITO` | tem destino calculado, mas a regra desconfia dele | 1 | ⚠️ **decidir se carrega** — ver a advertência da D4 no `README_V3.md` |
| `DADO_INVALIDO` | campo da própria base inutilizável | 1 | corrigir a Base de Fechamento |
| `OK` | classificado sem ressalva | 1 | carregar |

### 8.4 Catálogo de `Motivo` (`config.CATALOGO_RAZOES`, `engine/config.py:233-293`)

**15 códigos** (`PERDA_NAO_EXPLICADA` entrou no catálogo na v3.1). Cada um mapeia para exatamente 1
`Status` e tem um texto de `Ação Recomendada` padrão. Os 2 códigos de prefixo (R9) têm ação **por
lançamento**, gerada em `marcar_bloqueio_prefixo` (com a `Conta OM` concreta); o texto do catálogo é
o fallback.

| Código | `Status` | Quando |
|---|---|---|
| `CV_EXCLUIDA` | `FORA_DE_ESCOPO` | `Nome da Classe de Valor` ∈ `EXCLUDED_VALUE_CLASSES` (Tool 63) |
| `PERDA_NAO_EXPLICADA` | `FALHA_INTERNA` | **V3.1** — o lançamento sumiu da cascata sem que nenhuma etapa registrasse o motivo. É defeito do **motor**, não do input: voltou à base com a conta de origem. Ação = **não carregar a linha** e avisar o time da ferramenta com o `ID Lançamento`. Até a v3.0 este código era ERRO bloqueante e não existia no catálogo |
| `CONTA_NAO_CADASTRADA` | `CADASTRO_PENDENTE` | `Conta Contabil` sem match na Estrutura de Contas (Tool 62) — a v2 descartava essas linhas |
| `GRUPO_NAO_CADASTRADO` | `CADASTRO_PENDENTE` | GRUPO de Cobrança fora do `depara_grupos` (Tool 88) — **era ERRO bloqueante na v2** |
| `CC_NAO_CADASTRADO` | `CADASTRO_PENDENTE` | Centro de Custo sem cadastro em Entidades×CC (não afeta a classificação) |
| `CLASSE_NAO_CADASTRADA` | `CADASTRO_PENDENTE` | código de Classe de Valor sem match no Classe×Conta (R5) |
| `CLASSE_RENOMEADA` | `CADASTRO_PENDENTE` | código com match, nome do mês divergindo do cadastrado (R5) |
| `CLASSE_SEM_UNICO_CV` | `CADASTRO_PENDENTE` | código+nome OK, sem linha na aba `Unico CV` (R5) |
| `PREFIXO_818_CRUZADO` | `DESTINO_SUSPEITO` | `Conta destino` cruza a família 818↔817/819 da conta de origem (R9) |
| `PREFIXO_FAMILIA_8_CRUZADO` | `DESTINO_SUSPEITO` | `Conta destino` cruza a família 8↔não-8 da conta de origem (R9) |
| `RECLASSIFICADOR_FALLBACK` | `DESTINO_SUSPEITO` | o Reclassificador não cobriu o lançamento — manteve a conta de origem (Tool 133) |
| `DEPARA_CONFLITO_RESOLVIDO_POR_VALOR` | `DESTINO_SUSPEITO` | De-Para com regras divergentes na mesma competência; aplicada a de maior `SOMA(\|Valor\|)` (R3) |
| `DEPARA_CONFLITO_MESMA_COMPETENCIA` | `DESTINO_SUSPEITO` | idem, **com empate de valor**; aplicada a 1ª em ordem de leitura — **era ERRO bloqueante na v2** |
| `MES_INVALIDO` | `DADO_INVALIDO` | `Mes` não é data válida → `DateTime_Out` vazio — **era ERRO bloqueante na v2** |
| `PREFIXO_CORRIGIDO_AUTOMATICAMENTE` | `OK` | a `Conta destino` foi reescrita para uma conta compatível na mesma Conta OM (R10) — informativo |

⚠️ **Consequência de leitura:** `PREFIXO_CORRIGIDO_AUTOMATICAMENTE` tem `Status = OK`, então existem
linhas com `Status = OK` **e** `Motivo`/`Ação Recomendada` preenchidos (413 em Abr26, 424 em Jun26).
Quem filtrar por `Motivo != ""` pega essas linhas; o filtro correto para "sem ressalva" é
`Status == "OK"`.

⚠️ **A `Ação Recomendada` de uma linha com mais de um `Motivo` sai concatenada na ordem de
REGISTRO das razões, não por gravidade** — então os textos podem se contradizer entre si. O par
concreto: um lançamento que carregue `MES_INVALIDO` **e** `PERDA_NAO_EXPLICADA` lê primeiro
*"Corrija a Base de Fechamento"* e só depois *"é defeito da própria ferramenta … NÃO carregue esta
linha"*. **Regra de leitura: quando há mais de um motivo, a instrução que manda NÃO carregar
prevalece sobre todas as demais** (o `Status` — que é único e segue a precedência do §8.3 — é o
campo confiável para decidir; o texto da ação é auxiliar). Comportamento pré-existente, conhecido e
não corrigido nesta rodada (`.superpowers/sdd/progress.md`, Task 2 da v3.1, Minor diferido (a)).

⚠️ **Só 3 dos 15 códigos explicam a ausência de um lançamento na saída da cascata**
(`config.CODIGOS_QUE_REMOVEM_LANCAMENTO`): `CV_EXCLUIDA`, `CONTA_NAO_CADASTRADA` e
`GRUPO_NAO_CADASTRADO`. Todos os outros são registrados em linhas que **seguem** no fluxo. É essa a
lista que isenta um lançamento da rede de segurança `PERDA_NAO_EXPLICADA` — na v3.0 a isenção era
"qualquer `Status != OK`", o que deixava ~10 mil lançamentos/mês fora da rede — **10.950** em Abr26 e
**9.822** em Jun26, que é exatamente a contagem de linhas com `Status != OK` de cada mês
(`106.188 − 95.238` e `87.204 − 77.382` na tabela do §8.5; proveniência
`.superpowers/sdd/task-13-report.md` §6.3, coluna "marcados"). Depois do estreitamento,
`FALHA_INTERNA` medido = **0 nos dois meses** (§8.5).

⚠️ `Motivo` e `Ação Recomendada` também existem, com **nomes iguais e grão diferente**, na aba
"Bloqueio Prefixo Conta" do excel de exceções (§4): lá o grão é o **cadastro a corrigir**; aqui é o
**lançamento**.

### 8.5 Números REAIS observados (Task 13, 2026-07-28/29 — reclassificador **mockado**, API nunca chamada)

Proveniência: `.superpowers/sdd/task-13-report.md` §4 (Abr26) e §5 (Jun26), saída literal de
`tests/bateria_v3_abr26_jun26.py`; conferidos também no arquivo `.xlsx` **gravado** (V1d).

| | Abr26 | Jun26 |
|---|---:|---:|
| base bruta (= espinha) | 106.188 / R$ 454.105.693,78 | 87.204 / R$ 556.390.391,34 |
| aba 1 `Consolidado` | 106.188 | 87.204 |
| aba 2 `Fora de Escopo` | 0 | 0 |
| `Status = OK` | 95.238 / R$ 424.548.174,99 | 77.382 / R$ 567.595.232,71 |
| `Status = CADASTRO_PENDENTE` | 2.320 / R$ 17.746.931,22 | 2.361 / −R$ 9.681.547,38 |
| `Status = DESTINO_SUSPEITO` | 8.630 / R$ 11.810.587,57 | 7.461 / −R$ 1.523.293,99 |
| `Status = FALHA_INTERNA` (V3.1) | 0 / R$ 0,00 | 0 / R$ 0,00 |
| `Status = DADO_INVALIDO` | 0 | 0 |
| `Status = FORA_DE_ESCOPO` | 0 | 0 |

`DADO_INVALIDO`, `FORA_DE_ESCOPO` e `FALHA_INTERNA` **não foram exercitados por dado real** nestes
dois meses (nem `GRUPO_NAO_CADASTRADO`, cadastro completo desde 2026-07-20) — só por teste sintético.

**A linha de `FALHA_INTERNA` foi medida na v3.1, não inferida.** Proveniência:
`.superpowers/sdd/task-4-v31-report.md` §3/§5 — a bateria ganhou a verificação **V9**
(`nenhum lançamento em FALHA_INTERNA`), que saiu `0 linha(s) / R$ 0,00` **nos dois meses**. Como a
isenção da rede de segurança foi estreitada na mesma rodada (§8.4), esse zero é a prova de que o
estreitamento **não expôs nenhuma perda escondida**. Todos os outros números desta tabela saíram
**byte a byte idênticos** aos da rodada anterior (`diff` do log inteiro — task-4-v31-report §5).
Como zero é o resultado desejado, o caminho `FALHA_INTERNA` **continua sem cobertura por dado
real**: quem o exercita é a V6 sintética da bateria (5 asserções) e
`tests/test_v3_perda_devolve_lancamento.py`.

> ⚠️ O `DESTINO_SUSPEITO` destes dois meses é **teto, não regime**: com o Reclassificador mockado,
> 100% do Tipo `Reclassificador` (1.782 em Abr26, 1.787 em Jun26 — ~2% da base) sai marcado via
> `RECLASSIFICADOR_FALLBACK`. Com a API real esses lançamentos recebem conta ajustada e tendem a sair
> `OK`. O caminho Reclassificador segue sem validação ponta a ponta em dado real.

## 9. O que cadastrar no admin do PPR (OutputFields)

| `OutputField` | Tipo | Situação |
|---|---|---|
| `base_final` | Arquivo | já existe — **passa a ter 2 abas** (§8.1) e 19 colunas em vez de 15 |
| `auditoria` | Tabela | já existe — na v3 agrega o **universo completo** por `Tipo`, com o bucket `"Não classificado"` |
| `auditoria_status` | Tabela (`status` [Texto], `registros` [Inteiro], `soma_valor` [Texto — BR formatado por `_fmt_valor_br`]) | ⚠️ **PENDENTE — precisa ser cadastrado.** `main()` **sempre** emite esta chave (`bbv001_reclassificacao.py:336`, a quebra do mesmo universo por `Status`); não temos como saber daqui se a plataforma ignora chave não cadastrada ou devolve erro. Confirmar com o time de analytics antes de subir |
| `warnings` | Tabela (§7) | opcional |
| `valor_por_pacote` · `log_execucao` · `excecoes` · `base_reclassificador` | — | já existem, sem mudança de contrato |

**Nenhum campo de configuração novo.** O toggle `r9_modo_warning` do R10 foi **removido** (D4 tornou
o modo warning o comportamento único), o que **encerra** a pendência aberta na v2 sobre o tipo real
desse campo — a ferramenta volta a ter só InputFields de arquivo.
