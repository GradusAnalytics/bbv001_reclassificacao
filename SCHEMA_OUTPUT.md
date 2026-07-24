# Schema do output — excel de exceções + tabela `warnings`

> **Para o time de analytics.** Referência de colunas/tipos dos outputs de exceções e avisos da v2,
> para integração/monitoramento programático. Extraído diretamente do código (`engine/transforms.py`,
> `engine/controls.py`, `bbv001_reclassificacao.py`) — cada tabela abaixo cita o arquivo/linha de
> origem. Não inclui o schema da `base_final` (Matrix) nem da `base_reclassificador` — só o excel de
> exceções (`BBV001-v2_Excecoes.xlsx`, nome final decidido no deploy) e o `OutputField` opcional
> `warnings`. Racional de cada aba: `README_V2.md` §"Transparência ao usuário"; racional de cada
> controle: `docs/SPEC_V2.md` do projeto de análise.

## Como o excel é montado

`engine.transforms.build_exceptions(...)` monta 4 dos 5 `DataFrame`s (todas as abas exceto "Avisos");
`bbv001_reclassificacao.py::_montar_excel_excecoes(exc, aviso_rows)` escreve as 5 abas via
`pandas.ExcelWriter(engine="openpyxl")`, na ordem abaixo. **Nenhuma aba nunca é omitida** — se não há
dado, a aba é escrita vazia (0 linhas) com o mesmo schema de colunas do caso populado (nunca uma coluna
a menos). Todos os valores numéricos (`Valor`) são `float64` puro escrito pelo `pandas`/`openpyxl` —
sem formatação de moeda aplicada na célula.

## 1. Aba "Contas Contábeis"

Grão: 1 linha por `Conta Contabil` sem match na Estrutura de Contas (agregado). Fonte:
`transforms.build_exceptions`, bloco Tool 200 (`engine/transforms.py:1239-1250`).

| Coluna | Tipo | Nulo? | Descrição |
|---|---|---|---|
| Código | Texto | Não | Conta Contábil da base sem match na Estrutura de Contas |
| Descrição | Texto | Não | `Nome da Conta` vindo da base do realizado (não existe descrição cadastrada — por definição, a conta não está na Estrutura) |
| Valor | Numérico (float) | Não | soma do `Valor` de todos os lançamentos com esse Código |

Ordenação: `Valor` decrescente. Vazio quando não há conta sem cadastro nessa rodada.

## 2. Aba "Centros de Custo"

Grão: 1 linha por Centro de Custo sem match no cadastro de Entidades×CC (agregado). Universo: base
pós-filtro de Classes de Valor (T63) — mesmo universo da aba "Contas Contábeis" (R6). Fonte:
`transforms.build_exceptions`, bloco Tool 201 (`engine/transforms.py:1252-1280`).

| Coluna | Tipo | Nulo? | Descrição |
|---|---|---|---|
| Código | Texto | Não | Centro de Custo (alfanumérico preservado — comparação por string normalizada, não `to_numeric`) |
| Descrição | Texto | Não | `Nome do Centro de Custo` vindo da base do realizado |
| Valor | Numérico (float) | Não | soma do `Valor` de todos os lançamentos com esse Código |

Ordenação: `Valor` decrescente. Vazio se o cadastro de Entidades×CC não foi enviado (opcional) ou se
todos os CCs da base batem com o cadastro.

## 3. Aba "Correção Automática de Conta" (R10)

Grão: 1 linha por lançamento cuja `Conta destino` foi reescrita automaticamente (nunca o cadastro).
Fonte: `transforms.autocorrigir_conta_om` (`engine/transforms.py:705-826`, retorno `correcoes`) →
`transforms.build_exceptions`, bloco R10 (`engine/transforms.py:1294-1303`).

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

Grão: 1 linha por lançamento que **sobrou bloqueado/sinalizado** depois da correção automática (R10)
— ou seja, o residual genuíno do R9. Fonte: `transforms.split_bloqueio_prefixo`
(`engine/transforms.py:827-965`, retorno `bloqueados`) → `transforms.build_exceptions`, bloco R9
(`engine/transforms.py:1282-1292`).

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
| Motivo | Texto — enum `"PREFIXO_818_BLOQUEADO"` \| `"PREFIXO_FAMILIA_8_BLOQUEADO"` | Não | qual das 2 regras de família disparou (nunca as duas ao mesmo tempo — mutuamente exclusivas por construção) |
| Conta OM | Texto | Não (na wiring atual — `autocorrigir_conta_om` sempre roda antes) | Conta OM da `Conta destino`, usada para montar `Ação Recomendada` |
| Ação Recomendada | Texto | Não | texto pronto: orienta cadastro (Tipo Arbitrado classe) ou pede revisão manual (Tipo Reclassificador) |

Ordenação: `Valor` decrescente. Vazio quando não há residual bloqueado (correção automática resolveu
tudo, ou não havia cruzamento de família nessa rodada). ⚠️ Presente **nos dois modos** do toggle
`r9_modo_warning` — a diferença entre modos é só se esses lançamentos saem ou não do `base_final`, não
se aparecem aqui (sempre aparecem).

## 5. Aba "Avisos" (sempre presente)

Grão: 1 linha por evento de controle disparado na rodada. Fonte: `controls.RunControls.to_rows()`
(`engine/controls.py:78-84`), renomeado pelas colunas do PPR em
`bbv001_reclassificacao.py:199-202`.

| Coluna | Tipo | Nulo? | Descrição |
|---|---|---|---|
| Etapa | Texto | Não | identificador do estágio/controle (ex.: `"T63"`, `"R8/censo"`, `"R9/Prefixo"`) — ver catálogo completo abaixo |
| Severidade | Texto — enum `"ERRO"` \| `"WARNING"` \| `"INFO"` | Não | ⚠️ **na prática, só `WARNING`/`INFO` aparecem aqui** — ver nota crítica abaixo |
| Código | Texto — enum, catálogo completo abaixo | Não | |
| Mensagem | Texto | Não | mensagem final já com a ação recomendada, pronta para o usuário |
| Registros | Inteiro | Não (default `0`) | quantidade de lançamentos afetados por esse evento |
| Valor | Numérico (float) | Não (default `0.0`) | soma do `Valor` dos lançamentos afetados — **pode ser negativo** (débitos/créditos se cancelam na soma; ex. real: `PREFIXO_FAMILIA_8_BLOQUEADO` deu `-R$ 3.596.588,37` em Jun26) |

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
(`bbv001_reclassificacao.py:249-250`) roda **antes de `pipeline.run_pipeline()` ser chamado** (não há
`RunControls` instanciado ainda) e levanta um `RuntimeError` puro, com o código embutido no TEXTO da
mensagem (`"[INPUT_OBRIGATORIO_AUSENTE] Os seguintes inputs..."`), não como campo estruturado.

**Implicação prática para monitoramento automatizado:** consumir só a aba "Avisos"/tabela `warnings`
NÃO é suficiente para detectar falhas de execução — é preciso também monitorar o **status/mensagem de
erro da rodada no PPR**, que é o único lugar onde qualquer evento de severidade ERRO aparece.

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
| T62/Estrutura | ERRO | `BASE_VAZIA_POS_FILTROS` | Não |
| (variável, por cadastro) | ERRO | `CADASTRO_CONFLITANTE` | Não |
| T11/De-Para | ERRO | `DEPARA_CONFLITO_MESMA_COMPETENCIA` | Não |
| T88/Grupos | ERRO | `GRUPO_NAO_CADASTRADO` | Não |
| T115/Datas | ERRO | `MES_INVALIDO` | Não |
| R8/censo | ERRO | `DUPLICACAO_FABRICADA` | Não |
| A1/conservacao | ERRO | `CONSERVACAO_VIOLADA` | Não |
| T63 | INFO | `CV_EXCLUIDA` | Sim |
| T62/Estrutura | WARNING | `CONTA_NAO_CADASTRADA` | Sim |
| (variável, por cadastro) | WARNING | `CADASTRO_DUPLICADO` | Sim |
| T49/Unico CV | WARNING | `CLASSE_NAO_CADASTRADA` | Sim |
| T49/Unico CV | WARNING | `CLASSE_RENOMEADA` | Sim |
| T49/Unico CV | WARNING | `CLASSE_SEM_UNICO_CV` | Sim |
| T11/De-Para | WARNING | `DEPARA_CONFLITO_RESOLVIDO_POR_VALOR` | Sim |
| T11/De-Para | WARNING | `DEPARA_REGRA_MAIS_RECENTE` | Sim |
| T11/De-Para | INFO | `DEPARA_COLAPSO_RECENCIA` | Sim |
| T132/Cobrança | INFO | `DUPLICATA_EXATA_COLAPSADA` | Sim |
| T126/Reclassificador | WARNING | `RECLASSIFICADOR_FALLBACK` | Sim |
| T126/Reclassificador | INFO | `RETORNO_SEM_MATCH` | Sim |
| T115/Datas | WARNING | `MES_PARCIALMENTE_INVALIDO` | Sim |
| T201/CC | WARNING | `CC_NAO_CADASTRADO` | Sim |
| R8/censo | INFO | `DUPLICATA_NATIVA` | Sim |
| R9/Prefixo | WARNING | `PREFIXO_818_BLOQUEADO` | Sim |
| R9/Prefixo | WARNING | `PREFIXO_FAMILIA_8_BLOQUEADO` | Sim |
| R9/Prefixo | INFO | `PREFIXO_MODO_WARNING_ATIVO` | Sim (só quando `r9_modo_warning` está ligado) |
| R10/AutoCorrecao | INFO | `PREFIXO_CORRIGIDO_AUTOMATICAMENTE` | Sim |

## 7. Tabela `warnings` (OutputField opcional do PPR)

Mesmo grão/conteúdo da aba "Avisos" (§5) — inclusive a mesma limitação de severidade ERRO nunca
aparecer. Nomes de coluna em minúsculo (contrato declarado no admin do PPR, ver
`README_V2.md` §"O que cadastrar no admin do PPR"):

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
