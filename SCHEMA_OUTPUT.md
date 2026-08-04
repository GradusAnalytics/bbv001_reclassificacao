# Schema do output — `base_final` + excel de exceções + tabela `warnings`

> **Para o time de analytics.** Referência de colunas/tipos dos outputs da **v3**, para
> integração/monitoramento programático. Extraído diretamente do código (`engine/transforms.py`,
> `engine/controls.py`, `engine/config.py`, `bbv001_reclassificacao.py`) — cada tabela abaixo cita o
> arquivo/linha de origem. Cobre o excel de exceções (`BBV001-Excecoes_nao_cadastrados.xlsx` — nome
> real gravado pelo código, `bbv001_reclassificacao.py:77`/`:363`), o `OutputField` opcional
> `warnings` e — **novo na v3** — o schema da `base_final`
> (§8: 2 abas × **21 colunas** desde a v3.2, enum de `Status`, catálogo de `Motivo` por lançamento;
> §8.6 = o que o Matrix de fato lê desse arquivo). Não inclui a
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
| Código | Texto | Não | Centro de Custo (alfanumérico preservado — comparação por string normalizada, não `to_numeric`). **V3.2:** a coluna já chega aqui como texto porque a coerção passou a acontecer na **leitura** da Base de Fechamento (§8.2) — antes era texto por acidente do caminho, agora é por contrato |
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
| Centro de Custo | Texto | Não | **V3.2:** texto por contrato — coagido na leitura da base (§8.2) |
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
| T114/Select final | WARNING | `COLUNA_AUSENTE_NA_SAIDA` | Sim — **novo na v3.2** (2026-07-31). Uma coluna do `FINAL_OUTPUT_SCHEMA` não existe no resultado e sai **ausente do arquivo**. É aviso de **arquivo**, não de lançamento: não está (nem deve estar) no `CATALOGO_RAZOES`, e não tem `Status` correspondente. Antes era só um `logger.warning`, que o usuário não vê se um bloqueio posterior impedir a produção do log. Cobre as **21** colunas — inclusive `Veiculo Legal`, a única do schema que não está em `REQUIRED_COLUMNS` |

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

## 8. `base_final` (V3) — 2 abas × 21 colunas, com marcação por lançamento

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

Mesmo schema de 21 colunas nas duas, na mesma ordem, **inclusive quando uma delas está vazia** (a aba
é sempre escrita, com o cabeçalho). É uma **partição exata**: nenhum lançamento fica nas duas nem
fora das duas. `ID Lançamento` e `Índice ERP` são únicos **globalmente** entre as 2 abas.

⚠️ **Observado nos dois meses reais testados, a aba 2 sai VAZIA** (Abr26 e Jun26 não têm nenhum
lançamento em `EXCLUDED_VALUE_CLASSES`) — o split só foi exercitado por teste sintético. Números em
§8.5.

### 8.2 As 21 colunas (`config.FINAL_OUTPUT_SCHEMA`, `engine/config.py:179-210`)

As **15 primeiras são as da v2, nas mesmas posições** (a carga do Matrix depende da ordem). As 6
restantes entraram em duas rodadas: 4 na v3 (auditoria por lançamento) e **2 na v3.2** —
`Veiculo Legal` na posição **16** e `Tipo` na **18**, ordem escolhida pelo dono em 2026-07-30.
Nenhuma das 15 originais mudou de posição em nenhuma das duas rodadas. Por que acrescentar à direita
é seguro por construção: **§8.6**.

| # | Coluna | Tipo | Vazio? | Descrição |
|---:|---|---|---|---|
| 1 | `Índice ERP` | Texto | Não | `Codigo Interno`, com sufixo `_N` a partir da 2ª ocorrência. **V3: calculado sobre a espinha** — o `_N` volta a significar só **duplicata nativa** da base (na v2 rodava sobre o union e misturava duplicata nativa com clone de join) |
| 2 | `DateTime_Out` | Texto `dd/mm/aaaa` | **Sim** | competência derivada de `Mes`; **vazio** quando `Mes` não é data válida — a linha sai com `Motivo = MES_INVALIDO` |
| 3 | `Grupo Acionista` | Texto | Não | da base bruta |
| 4 | `GC Matrix` | Texto | Não | `"GC-" + Grupo Conta` |
| 5 | `Centro de Custo` | Texto | Não | alfanumérico preservado. **V3.2: viaja como TEXTO por contrato** (era número) — coagido por `coerce_conta_str` na **leitura** da base (`engine/io_utils.py:153`), não na escrita. Ver a nota depois da tabela |
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
| 16 | `Veiculo Legal` | Texto | Não observado nulo (ver ressalva abaixo) | **V3.2** — a **origem do lançamento**, vinda da Base de Fechamento. Já atravessava a cascata inteira (única menção em `engine/transforms.py:624`, o select do Tool 132) e era descartada no select final; agora entra no arquivo. A ferramenta não a transforma nem a usa em nenhum join — é passagem |
| 17 | `ID Lançamento` | Inteiro | Não | **V3** — posição da linha na base bruta (`1..N`), imutável. É a **única chave verdadeira por lançamento**: `Codigo Interno` não serve, porque duplicata nativa existe e é válida. Não confundir com `RecordID` (interno, `cumcount` por `Codigo Interno`) |
| 18 | `Tipo` | Texto — 7 valores possíveis (lista abaixo) | Não | **V3.2** — o **mecanismo que classificou** o lançamento. Mesma coluna que a tabela `auditoria` agrega; já existia em `base_completa` com zero nulos, só não estava no schema de saída |
| 19 | `Status` | Texto — enum fechado (§8.3) | Não | **V3** — exatamente 1 por linha; é a coluna de filtro |
| 20 | `Motivo` | Texto — 0..N códigos do catálogo (§8.4) separados por `"; "` | Sim (quando `Status = OK` e não houve correção automática) | **V3** — acumula **todos** os códigos aplicáveis à linha; nunca perde código |
| 21 | `Ação Recomendada` | Texto | Sim (idem `Motivo`) | **V3** — texto pronto para o usuário, um por código de `Motivo`, concatenados na mesma ordem |

#### `Centro de Custo` como texto — a coerção é na LEITURA, e por isso vale para os 3 consumidores

Até a v3.1 a coluna viajava como **número** (`int` em 100% das linhas nos dois meses validados —
6 dígitos em Abr26, 9 em Jun26). Na **v3.2** ela é coagida a texto em
`io_utils.read_base_fechamento` (`engine/io_utils.py:153`, via `controls.coerce_conta_str`), ou seja
**antes** de qualquer consumidor. Consequência de contrato, deliberada: passam a receber texto os
**três** destinos da coluna —

1. o **arquivo de carga** (esta tabela, coluna 5);
2. a **base enviada ao reclassificador**, onde a coluna é renomeada para `Código Centro de Custo`
   (`engine/reclassifier_bridge.py:42`) — ver a pendência aberta em
   `docs/PENDENCIAS_E_FOLLOWUPS.md` §A5;
3. as **abas do excel de exceções** que carregam o CC (§2 "Centros de Custo" e §4 "Bloqueio Prefixo
   Conta").

Coagir só na escrita deixaria dois dos três recebendo número — era exatamente a inconsistência a
resolver. **Não há perda de precisão** (9 dígitos cabem em `int64` com folga); o ganho é consistência
de tipo, e o fato que a torna segura é que **o Matrix lê `Centro de Custo` como texto** (dono,
2026-07-30 — §8.6). O único join que usa a coluna (cadastro de CC no relatório de exceções) normaliza
os dois lados via `_normalize_join_key` antes de comparar, então `"220344"` e `220344` convergem e o
match não muda.

Verificado no arquivo **gravado**, nos dois meses: `0 célula numérica`, tipo observado `['str']` e só
`str` (V1d-8 da bateria — `.superpowers/sdd/task-3b-v32-fix-report.md` §2).

#### Os 7 valores de `Tipo` (`tests/bateria_v3_abr26_jun26.py`, constante `TIPOS_ESPERADOS`)

Os 6 mecanismos da cascata mais o bucket dos que voltaram sem passar por ela:

`Match conta x classe` · `Match classe conta OM` · `Arbitrado classe` · `Reclassificador` ·
`Cobrança` · `Consultorias` · **`Não classificado`**

`"Não classificado"` é o `Tipo` dos lançamentos **devolvidos pela reconciliação** — aqueles que a
cascata não classificou e que a v3 traz de volta com a `Conta destino` = conta de **origem**
(`engine/transforms.py:1381`). É o mesmo bucket da tabela `auditoria`. Casos que caem nele: conta sem
match na Estrutura (`CONTA_NAO_CADASTRADA`), GRUPO sem cadastro (`GRUPO_NAO_CADASTRADO`) e
`FALHA_INTERNA` (`PERDA_NAO_EXPLICADA`). **Não** é "sem destino calculado por erro" — é preservação de
valor, declarada.

**Medido no arquivo gravado:** **6** valores em Abr26 e **7** em Jun26. A diferença é exatamente
`"Não classificado"`, que só existe em Jun26 — são os **18** lançamentos sem match na Estrutura de
Contas, a única diferença de contagem v2→v3 de todo o exercício (Abr26 não tem nenhum). Fonte:
`.superpowers/sdd/task-3-v32-report.md` §4/§6 e `.superpowers/sdd/task-3b-v32-fix-report.md` §2.
Distribuição completa de Jun26 por `Tipo` × `Status`: `task-3-v32-report.md` §6.

⚠️ **`Veiculo Legal` — cobertura medida, obrigatoriedade NÃO confirmada.** No arquivo gravado a
coluna veio **preenchida em 100% das linhas** nos dois meses (106.188 de 106.188 em Abr26; 87.204 de
87.204 em Jun26 — V1d-9, `task-3b-v32-fix-report.md` §2), e a bateria passou a exigir **cobertura
total** por causa disso. Mas **não há confirmação de regra de negócio do Banco BV** de que a coluna é
obrigatória em todo lançamento — a base é a observação empírica de 2 meses. `Veiculo Legal` também
**não está** em `config.REQUIRED_COLUMNS["base_fechamento"]`: se ela faltar no arquivo do mês, a
leitura **não** bloqueia e o select final a omite com apenas um warning no log (ver a nota do select
silencioso, §8.6). Se um mês futuro reprovar a V1d-9, o tratamento é **investigar**, não suavizar a
condição do teste (`docs/PENDENCIAS_E_FOLLOWUPS.md` §C8).

⚠️ **Cobertura:** a mudança de significado do sufixo `_N` (coluna 1) **não foi exercitada por dado
real** em nenhum dos dois meses validados — Abr26 e Jun26 não têm duplicação de join (só Jun26 tem
duplicata nativa, e o sufixo sai idêntico ao da v2 nesse caso: 3 e 3). A propriedade está provada por
construção (`tests/bateria_v3_abr26_jun26.py`, V7c + V7s), não por um mês real com clone de join.
Detalhe: `.superpowers/sdd/task-13-report.md` §7 achado A4.

⚠️ `Conta Contabil` e `Conta destino` **têm** de viajar como texto: códigos de 25 dígitos gravados
como número estouram o float64 no write (`8172700000000000010000000` → `8172699999999999715835904`).
Verificado no arquivo gravado nos dois meses (nenhuma célula voltou como número).

### 8.3 Enum de `Status`

**7 valores** (`FALHA_INTERNA` entrou na v3.1; `CADASTRO_BLOQUEANTE` entrou na **v3.3**, 2026-08-03).
Ordem abaixo = **precedência** quando a linha acumula problemas de famílias diferentes
(`config.STATUS_PRECEDENCIA`) — do mais grave ao menos grave. `FORA_DE_ESCOPO` vem primeiro porque
define a **aba**; `FALHA_INTERNA` vem logo depois, vencendo todos os demais; `CADASTRO_BLOQUEANTE`
entra na posição 2 (acima de `CADASTRO_PENDENTE`); nas restantes, falta de destino/dimensão é mais
grave que destino suspeito. **Só o `Status` é único — o `Motivo` nunca perde código.**

A coluna de ação abaixo descreve o que **quem revisa a base** deveria fazer com a linha. ⚠️ **Não é
um filtro que o Matrix aplica** — o Matrix não lê a coluna `Status` (§8.6): a revisão é decisão do
cliente.

✅ **Desde a v3.3 (2026-08-03) a rede de segurança da carga volta a ser enunciável por `Status`** —
mas o enunciado **não é "`CADASTRO_PENDENTE` tem rede" (isso continua errado, e foi o erro corrigido em
2026-07-31)**: é **`CADASTRO_BLOQUEANTE`** que reúne exatamente os 3 códigos que faltam cadastro de
**dimensão** (`CONTA_NAO_CADASTRADA`, `GRUPO_NAO_CADASTRADO`, `CC_NAO_CADASTRADO`), separados do
`CADASTRO_PENDENTE` (que agora é só a família `CLASSE_*`, sempre informativa). **Isso não promete
"sem destino calculado" para os 3 códigos** — só `CONTA_NAO_CADASTRADA` e `GRUPO_NAO_CADASTRADO` (os
que de fato tiram o lançamento da cascata — `config.CODIGOS_QUE_REMOVEM_LANCAMENTO`, que **não**
mudou nesta versão) deixam a linha com a conta de **origem**; `CC_NAO_CADASTRADO` mantém a `Conta
destino` **calculada e cadastrada** — o que falta é a **dimensão Centro de Custo**, não a conta. A
recusa na carga é **fato, confirmado pelo dono (2026-08-04)** para os três — nas duas variantes
(recusa por `Conta destino` fora do plano; recusa por `Centro de Custo` fora do cadastro):
`docs/PENDENCIAS_E_FOLLOWUPS.md §A7` (fechada). Medido: **Abr26, `CADASTRO_BLOQUEANTE` = 0** (lacuna de
cobertura por dado real — só teste sintético); **Jun26, `CADASTRO_BLOQUEANTE` = 186 / −R$
26.405.479,23** (17 `CONTA_NAO_CADASTRADA`, 164 `CC_NAO_CADASTRADO`, 5 em combinação — ver §8.5).
Detalhe e números em `README_V3.md`, "Advertência operacional".

| `Status` | Significado | Aba | Ação de quem revisa a base antes de carregar |
|---|---|---|---|
| `FORA_DE_ESCOPO` | filtro de escopo por desenho (Classe de Valor excluída) | 2 | nenhuma — é intencional |
| `FALHA_INTERNA` | **V3.1** — defeito da **própria ferramenta**: o lançamento sumiu da cascata sem que nenhuma etapa registrasse o motivo (`Motivo = PERDA_NAO_EXPLICADA`). Voltou à base com a `Conta destino` = conta de **origem** e `Tipo = "Não classificado"`, para o valor não se perder | 1 | ⛔ **não carregar esta linha** e **avisar o time responsável pela ferramenta**, informando o `ID Lançamento`. **Não** é problema de cadastro do cliente — não há nada a corrigir no input |
| `CADASTRO_BLOQUEANTE` | **V3.3 (2026-08-03)** — falta cadastro de **dimensão** que a carga no Matrix precisa: Conta Contábil (`CONTA_NAO_CADASTRADA`), GRUPO de Cobrança (`GRUPO_NAO_CADASTRADO`) ou Centro de Custo (`CC_NAO_CADASTRADO`). ⚠️ **Não é sinônimo de "carrega conta de origem"** — só os 2 primeiros; o `CC_NAO_CADASTRADO` mantém a `Conta destino` calculada | 1 | cadastrar a dimensão apontada e re-executar. Rede de carga **confirmada** (§A7, fechada) — para `CONTA_NAO_CADASTRADA`/`GRUPO_NAO_CADASTRADO` é sobre a `Conta destino`; para `CC_NAO_CADASTRADO` é sobre o `Centro de Custo` |
| `CADASTRO_PENDENTE` | **V3.3: passou a significar só uma coisa** — família `CLASSE_*` (`CLASSE_NAO_CADASTRADA`, `CLASSE_RENOMEADA`, `CLASSE_SEM_UNICO_CV`), sempre **informativa** | 1 | cadastrar/alinhar a Classe de Valor e re-executar. A linha leva destino **calculado e cadastrado** e **entra no Matrix normalmente** — nenhuma rede |
| `DESTINO_SUSPEITO` | tem destino calculado, mas a regra desconfia dele | 1 | ⚠️ **decidir se carrega — sem nenhuma rede:** a conta é **válida e cadastrada**, só de família errada, então o Matrix **aceita** e nada acusa. ⚠️ E **não filtre por este `Status` para achar todos**: a precedência põe `CADASTRO_BLOQUEANTE`/`CADASTRO_PENDENTE` na frente, então 1.392 linhas em Abr26 e 1.419 em Jun26 são destino suspeito exibindo outro `Status` — filtre pelos **códigos de `Motivo`**. Ver a advertência operacional no `README_V3.md` |
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
| `CONTA_NAO_CADASTRADA` | `CADASTRO_BLOQUEANTE` **(v3.3; era `CADASTRO_PENDENTE`)** | `Conta Contabil` sem match na Estrutura de Contas (Tool 62) — a v2 descartava essas linhas |
| `GRUPO_NAO_CADASTRADO` | `CADASTRO_BLOQUEANTE` **(v3.3; era `CADASTRO_PENDENTE`)** | GRUPO de Cobrança fora do `depara_grupos` (Tool 88) — **era ERRO bloqueante na v2** |
| `CC_NAO_CADASTRADO` | `CADASTRO_BLOQUEANTE` **(v3.3; era `CADASTRO_PENDENTE`)** | Centro de Custo sem cadastro em Entidades×CC — **não afeta a classificação** (`Conta destino` continua calculada e cadastrada) |
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
| `Status = CADASTRO_BLOQUEANTE` (V3.3) | 0 / R$ 0,00 | 186 / −R$ 26.405.479,23 |
| `Status = CADASTRO_PENDENTE` (V3.3: só `CLASSE_*`) | 2.320 / R$ 17.746.931,22 | 2.175 / R$ 16.723.931,85 |
| `Status = DESTINO_SUSPEITO` | 8.630 / R$ 11.810.587,57 | 7.461 / −R$ 1.523.293,99 |
| `Status = FALHA_INTERNA` (V3.1) | 0 / R$ 0,00 | 0 / R$ 0,00 |
| `Status = DADO_INVALIDO` | 0 | 0 |
| `Status = FORA_DE_ESCOPO` | 0 | 0 |

`DADO_INVALIDO`, `FORA_DE_ESCOPO`, `FALHA_INTERNA` e `CADASTRO_BLOQUEANTE` em Abr26 **não foram
exercitados por dado real** nestes dois meses (nem `GRUPO_NAO_CADASTRADO`, cadastro completo desde
2026-07-20) — só por teste sintético.

📌 **V3.3 (2026-08-03) — a linha `CADASTRO_PENDENTE` de 2026-07-28/29 foi PARTIDA em duas**, sem
mudar nenhum outro número desta tabela (prova mecânica: `diff` do log inteiro da bateria contra a
rodada anterior — `.superpowers/sdd/task-6-v33-report.md` §Rodada A). Em Jun26, `CADASTRO_BLOQUEANTE`
(186 / −R$ 26.405.479,23) + `CADASTRO_PENDENTE` novo (2.175 / R$ 16.723.931,85) somam
**exatamente** os 2.361 / −R$ 9.681.547,38 registrados em 2026-07-28/29 — é relabeling puro, não
reclassificação. Em Abr26 o mês não tem nenhum lançamento com os 3 códigos de dimensão, então
`CADASTRO_BLOQUEANTE` sai zero e a Task 13 anterior (2.320 / R$ 17.746.931,22) permanece idêntica.
Proveniência: `.superpowers/sdd/task-6-v33-report.md` §Rodada A (bateria V1-V9 + V3d nova).

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

### 8.6 O que o Matrix de fato lê deste arquivo (dono, 2026-07-30)

**Fato que o dono confirmou e que muda como o arquivo deve ser lido: o Matrix lê as primeiras N
colunas cadastradas e IGNORA tudo o que estiver à direita disso.** Não é tolerância acidental — é o
comportamento do cadastro de carga. Três consequências, todas de desenho:

1. **Acrescentar coluna à direita é seguro por construção.** Nenhuma coluna nova além da última
   posição cadastrada pode quebrar a carga, porque o Matrix não chega a ler. Foi o que permitiu somar
   6 colunas (4 na v3, 2 na v3.2) sem tocar nas 15 originais nem no cadastro. O que **não** é seguro
   é mexer na **ordem** das 15 primeiras — daí a trava de posição no schema e o teste que a verifica.
2. **As 4 colunas de auditoria (`ID Lançamento`, `Status`, `Motivo`, `Ação Recomendada`) NÃO chegam ao
   Matrix — e isso é por desenho.** Elas existem para quem **lê e revisa** o arquivo (pessoa,
   planilha, script), não para a plataforma. ⚠️ Corolário importante: **não existe "filtrar por
   `Status` na carga do Matrix"** — a coluna não chega lá. O que existe é a revisão da base **antes**
   de carregar, que é decisão do cliente (`README_V3.md`, "Advertência operacional").
3. **`Veiculo Legal`, na posição 16, só chega ao Matrix se o cadastro crescer para 16 colunas.** Com o
   cadastro em 15, a coluna está no arquivo e é ignorada — a ferramenta cumpriu a parte dela. Crescer
   o cadastro é **ação de quem mantém o cadastro do Matrix, não do código**
   (`docs/PENDENCIAS_E_FOLLOWUPS.md` §A3).

**`Centro de Custo` (posição 5) o Matrix lê como TEXTO** (dono, 2026-07-30) — é esse fato que torna a
mudança da v3.2 segura do lado do consumidor, e não só consistente do lado do produtor.

⚠️ **O select final é TOLERANTE, mas não é mais silencioso** (`engine/transforms.py`, Tool 114):
coluna que está no `FINAL_OUTPUT_SCHEMA` mas falta no `DataFrame` continua saindo **ausente do
arquivo** (não há erro — é por desenho, para não barrar o mês), **mas desde a v3.2 (2026-07-31,
Correção 2) o evento passa por `controls.add`** e sai como WARNING `COLUNA_AUSENTE_NA_SAIDA` no log,
na aba "Avisos" **e** na tabela `warnings`. Antes era só um `logger.warning` — e como um bloqueio
posterior interrompe a execução antes de o log ser produzido, esse aviso **não chegava ao usuário**.
A guarda cobre as **21** colunas, não só as que a leitura não exige. Independente disso, a bateria
segue conferindo no arquivo **gravado** que as colunas chegaram **com valor** — não só que estão na
constante do schema. ⚠️ **`Veiculo Legal` NÃO foi acrescentada a `REQUIRED_COLUMNS`** de propósito:
criaria bloqueio novo para um mês que legitimamente não a tenha, contra a filosofia da v3 de marcar
em vez de barrar, e não há confirmação de que a coluna é obrigatória
(`docs/PENDENCIAS_E_FOLLOWUPS.md §C8`).

## 9. O que cadastrar no admin do PPR (OutputFields)

| `OutputField` | Tipo | Situação |
|---|---|---|
| `base_final` | Arquivo | já existe — **passa a ter 2 abas** (§8.1) e **21 colunas** em vez de 15 (19 na v3.0/v3.1; `Veiculo Legal` e `Tipo` entraram na v3.2). Nada a mudar no admin do PPR por causa disso; o cadastro que pode precisar crescer é o **do Matrix**, e só se `Veiculo Legal` tiver de ser lida (§8.6) |
| `auditoria` | Tabela | já existe — na v3 agrega o **universo completo** por `Tipo`, com o bucket `"Não classificado"` |
| `auditoria_status` | Tabela (`status` [Texto], `registros` [Inteiro], `soma_valor` [Texto — BR formatado por `_fmt_valor_br`]) | ⚠️ **PENDENTE — precisa ser cadastrado.** `main()` **sempre** emite esta chave (`bbv001_reclassificacao.py:336`, a quebra do mesmo universo por `Status`); não temos como saber daqui se a plataforma ignora chave não cadastrada ou devolve erro. Confirmar com o time de analytics antes de subir |
| `warnings` | Tabela (§7) | opcional |
| `valor_por_pacote` · `log_execucao` · `excecoes` · `base_reclassificador` | — | já existem, sem mudança de contrato |

**Nenhum campo de configuração novo.** O toggle `r9_modo_warning` do R10 foi **removido** (D4 tornou
o modo warning o comportamento único), o que **encerra** a pendência aberta na v2 sobre o tipo real
desse campo — a ferramenta volta a ter só InputFields de arquivo.
