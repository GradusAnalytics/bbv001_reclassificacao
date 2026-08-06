> ✅ **PASTA v4 — VALIDADA (2026-08-03).** Cópia de `bbv001_reclassificacao-v3/` no
> fechamento da v3.3 (commit `d4ca60b`) — código **idêntico** à v3; a diferença é o
> contrato de input, feito para o arquivo mestre de cadastros do cliente
> (`BBV001-Classe de Valor x Conta Contábil.xlsx`) servir os 4 InputFields de cadastro
> (`classe_valor_conta`, `estrutura_contas`, `estrutura_entidades_cc`, `depara_grupos`)
> — a Base de Fechamento e o de-para de Cobrança continuam arquivos próprios (decisão
> do dono). O dono corrigiu 2 chaves duplicadas na aba `Base` do arquivo mestre em
> 2026-08-03 (`docs/PENDENCIAS_E_FOLLOWUPS.md §A8`, RESOLVIDA); reteste nos dois meses
> **passa** (conservação, censo, enum, 0 ERRO — números em `docs/FONTE_DA_VERDADE.md
> §3.5`). Resíduo medido (não é ação, é fato a saber): a `Conta destino` de **542
> lançamentos em Abr26 (R$ 1.284.797,54) e 418 em Jun26 (R$ 2.005.070,68)** diverge da
> rodada com o cadastro atual — cobertura de cadastro diferente, não erro de
> classificação (`docs/DECISOES.md §Engagement v3.3`, `FONTE_DA_VERDADE.md §3.4/§3.5`).
> `bbv001_reclassificacao-v3/` permanece intocada, fechada na v3.3, para quem seguir
> com os cadastros em arquivos separados. O texto abaixo ainda descreve a v3
> (paridade Alteryx, cascata, schema) — vale integralmente para a v4, que só muda o
> contrato de input, não a lógica.
>
> ✅ **Atualização 2026-08-04 — contrato de input consolidado.** Os 4 campos de
> cadastro (`classe_valor_conta`, `estrutura_contas`, `estrutura_entidades_cc`,
> `depara_grupos`) foram substituídos por **1 campo `cadastros_auxiliares`**: um
> arquivo com 5 abas obrigatórias, lidas por nome, sem fallback de posição —
> `Allowlist` (era `Base`) · `Arbitragem` (era `Unico CV`) · `Estrutura de
> contas` · `Estrutura completa de Entidades` · `Grupos Cobrança`. O cadastro de
> Centro de Custo deixou de ser opcional. `base_fechamento` e `depara_custo`
> passam a ler a 1ª aba do arquivo, sem checar nome. `main()` cai de 7 para 4
> parâmetros. Nenhuma coluna do schema de SAÍDA muda; nenhuma stage da cascata
> muda. Prova de zero-diff (mesmos dados, contrato novo, lançamento a
> lançamento contra a rodada anterior): `docs/FONTE_DA_VERDADE.md §3.6`. Spec:
> `docs/superpowers/specs/2026-08-04-cadastros-auxiliares-consolidados-design.md`.

# BBV001 v3 — O que mudou vs a v1 e a v2 (e o que cadastrar no PPR)

> **Para o time de analytics.** Esta pasta é a proposta **v3** da ferramenta
> `bbv001_reclassificacao`: a mesma cascata de classificação portada do Alteryx
> (paridade preservada), MAIS os controles de edge cases da v2, MAIS a inversão da
> política de saída descrita logo abaixo. A pasta `bbv001_reclassificacao-v2/`
> **continua existindo como entregável congelado** (seu próprio `README_V2.md`) — são
> dois entregáveis lado a lado, e a escolha entre eles é do time de analytics. A
> especificação completa está em `docs/SPEC_V2.md` (v2) e
> `docs/superpowers/specs/2026-07-27-v3-base-final-completa-design.md` (v3), no projeto
> de análise. O `README_TOOL.md` original da v1 continua valendo para o deploy/infra
> (ECS, workflow, bridge da API).

## O que mudou da v2 para a v3 (leia isto primeiro)

Até a v2, o `base_final` respondia *"o que a ferramenta conseguiu classificar"*: os
lançamentos problemáticos ou saíam da base (aparecendo, no melhor caso, **agregados** no
excel de exceções) ou abortavam a rodada inteira antes de gravar qualquer arquivo. Para
saber o que aconteceu com o fechamento, o usuário tinha de cruzar três artefatos.

**Na v3, a base bruta é a espinha da saída.** O `base_final` contém **exatamente os mesmos
lançamentos que entraram** na Base de Fechamento — mesmo conjunto, mesma soma de `Valor` —
em **2 abas**, cada linha marcada com 3 colunas novas: `Status` (enum de filtro), `Motivo`
(código(s) do que houve) e `Ação Recomendada` (texto pronto). Uma 4ª coluna nova,
`ID Lançamento`, é a chave verdadeira do lançamento (1..N na base bruta). A **v3.2** acrescentou
outras duas — `Veiculo Legal` e `Tipo` (ver ² abaixo).

| | v2 | v3 |
|---|---|---|
| Universo do `base_final` | só o classificado (Abr26: 97.957 linhas em modo bloqueia) | a base bruta inteira (Abr26: **106.188** linhas) |
| Abas do arquivo | 1 (`Consolidado`) | 2 (`Consolidado` + `Fora de Escopo`) |
| Colunas | 15 | **21** (as 15 nas mesmas posições + `Veiculo Legal` [16] · `ID Lançamento` [17] · `Tipo` [18] · `Status` · `Motivo` · `Ação Recomendada`) ² |
| Lançamento que a cascata não classifica por falta de cadastro (`CONTA_NAO_CADASTRADA` no T62, `GRUPO_NAO_CADASTRADO` no T88) | sai da base / aborta a rodada | fica na base, `Status = CADASTRO_BLOQUEANTE` **(v3.3; era `CADASTRO_PENDENTE`)**, `Conta destino` = conta de **origem**. ⚠️ Só **estes dois** códigos levam a conta de origem — o 3º código do mesmo `Status` (`CC_NAO_CADASTRADO`) e os da família `CLASSE_*` (agora `Status = CADASTRO_PENDENTE`) são informativos e a linha sai com destino calculado (ver "Advertência operacional") |
| Cruzamento de família de prefixo (R9) | sai da base (ou fica, se o toggle estivesse ligado) | fica na base, `Status = DESTINO_SUSPEITO`, conta mantida |
| O que ainda **aborta** a rodada | 10 códigos | **3 famílias / 7 códigos**: falha estrutural de leitura · duplicação de linha · violação da integridade da espinha |
| Toggle `r9_modo_warning` | existia (tipo no admin do PPR era pendência aberta) | **removido** — o modo warning virou o comportamento único; a pendência está encerrada |
| Dedup do Tool 132 | colapsava duplicata exata em silêncio | **saiu** — clone sobrevive até o censo, que bloqueia para ajuste no input |
| `Índice ERP` / sufixo `_N` | calculado sobre o union — misturava duplicata nativa com clone de join | calculado sobre a espinha — `_N` volta a significar só **duplicata nativa** ¹ |

¹ ⚠️ Provado por construção (`tests/bateria_v3_abr26_jun26.py`, V7c + V7s), mas **não exercitado por
dado real** nos dois meses validados: nem Abr26 nem Jun26 têm duplicação de join, então o sufixo sai
idêntico ao da v2 nos dois (0 e 0 em Abr26; 3 e 3 em Jun26 — a duplicata nativa real do mês). Um mês
com clone de join é que testaria a mudança de significado de verdade. Detalhe:
`.superpowers/sdd/task-13-report.md` §7 achado A4.

² **V3.2 (2026-07-30)** — `Veiculo Legal` (a origem do lançamento, vinda da Base de Fechamento) e
`Tipo` (o mecanismo que classificou) já atravessavam a cascata e eram descartadas no select final;
agora entram no arquivo. Na mesma rodada, **`Centro de Custo` passou a viajar como TEXTO** (era
número), coagido na **leitura** da base — o que cobre de uma vez os três consumidores da coluna: o
arquivo de carga, a base enviada ao reclassificador (onde ela se chama `Código Centro de Custo`) e as
abas do excel de exceções. ⚠️ `Veiculo Legal`, na posição 16, **só chega ao Matrix se o cadastro de
carga crescer para 16 colunas** — ação de quem mantém o cadastro, não do código
(`SCHEMA_OUTPUT.md §8.6`). Schema completo e os 7 valores de `Tipo`: `SCHEMA_OUTPUT.md §8.2`.

**A v3 não reclassificou ninguém.** Validado nos dois meses reais (Abr26 e Jun26,
reclassificador mockado): os 6 Tipos da cascata saem **idênticos** à v2 em registros e em
valor, e a `Conta destino` bate lançamento a lançamento (0 divergência em 106.188 e 87.186
linhas pareadas). A única diferença de contagem observada em todo o exercício são os **18
lançamentos de Jun26 sem match na Estrutura de Contas** (−R$ 26.712.349,66), que a v2
descartava e a v3 devolve marcados. Evidência: `.superpowers/sdd/task-13-report.md`.

## Advertência operacional — o que a ferramenta marca, e o que o Matrix faz com isso

**Leia antes de subir a v3.** Esta seção substitui a advertência que estava aqui até 2026-07-30, que
dizia "filtre por `Status` antes de carregar". **Aquela redação estava factualmente errada:** o
Matrix lê as primeiras N colunas cadastradas e ignora o resto à direita — e `Status` está à direita
(posição 19 de 21). **A coluna `Status` não chega ao Matrix**, portanto não existe filtro por `Status`
acontecendo lá. Isso é por desenho: as 4 colunas de auditoria existem para quem **lê e revisa** o
arquivo, não para a plataforma (`SCHEMA_OUTPUT.md §8.6`).

**Posição do dono (2026-07-30): a ferramenta marca; a decisão de revisar antes de carregar é do
cliente.** Se o cliente não revisar, **a reclassificação vale como está** e ele corrige os *inputs*
(cadastros) para a rodada seguinte — a marcação existe justamente para que ele saiba o que corrigir.
Não é uma obrigação que a ferramenta impõe; é uma consequência que ele precisa conhecer para decidir.

✅ **Desde a v3.3 (2026-08-03) a rede de segurança volta a ser enunciável por `Status`** — a
versão de 2026-07-31 desta seção (enunciada por `Motivo`, preservada nas entradas datadas de
`docs/DECISOES.md`) ainda vale como **descrição do comportamento por código**; o que mudou é que o
Status novo `CADASTRO_BLOQUEANTE` agora reúne **exatamente** os 3 códigos que tiravam a rede do
`CADASTRO_PENDENTE` — então filtrar por `Status` volta a bastar, sem a armadilha de 2026-07-30 (que
enunciava a rede para *todo* `CADASTRO_PENDENTE`, o que era falso):

| `Status` | Códigos de `Motivo` | Conta que a linha carrega | Consequência esperada na carga | Rede? |
|---|---|---|---|---|
| `CADASTRO_BLOQUEANTE` | `CONTA_NAO_CADASTRADA` · `GRUPO_NAO_CADASTRADO` · `CC_NAO_CADASTRADO` | `CONTA_NAO_CADASTRADA`/`GRUPO_NAO_CADASTRADO`: conta de **origem** (a cascata não calculou destino, D3). `CC_NAO_CADASTRADO`: conta **calculada e cadastrada** — o que falta é o `Centro de Custo` | **recusa** — por `Conta destino` fora do plano (2 primeiros) OU por `Centro de Custo` fora do cadastro (3º) | ✅ **rede confirmada** (dono, 2026-08-04) — ver nota abaixo |
| `CADASTRO_PENDENTE` | `CLASSE_NAO_CADASTRADA` · `CLASSE_RENOMEADA` · `CLASSE_SEM_UNICO_CV` | conta **calculada e cadastrada** — sempre informativos | **entra no Matrix normalmente** | ❌ **nenhuma** |
| `DESTINO_SUSPEITO` | `PREFIXO_818_CRUZADO` · `PREFIXO_FAMILIA_8_CRUZADO` · `RECLASSIFICADOR_FALLBACK` · `DEPARA_CONFLITO_*` | conta **calculada e cadastrada**, família errada | **entra no Matrix normalmente** | ❌ **nenhuma** |

✅ **A recusa na carga é FATO, confirmado pelo dono (2026-08-04) — não é mais inferência.** O Matrix
recusa lançamento cuja `Conta destino` não está no plano de contas dele, e recusa lançamento cujo
`Centro de Custo` não está no cadastro — as duas coisas, confirmadas oralmente pelo dono (conhecimento
operacional dele do Matrix, não documento escrito; `docs/PENDENCIAS_E_FOLLOWUPS.md §A7`, fechada). A
única sub-premissa que segue sem confirmação textual própria é se o plano de contas do Matrix é
byte-a-byte a mesma **Estrutura de Contas** que a ferramenta usa como cadastro — mas isso não muda a
orientação: **trate `Status = CADASTRO_BLOQUEANTE` como rede real.**

📌 **Posição do dono (2026-08-03) sobre a inclusão de `CC_NAO_CADASTRADO` no `CADASTRO_BLOQUEANTE`:**
mesmo sem promessa de "sem destino calculado", o Centro de Custo é uma dimensão de carga tão
obrigatória quanto a Conta — por isso o código entra no Status bloqueante, com a ressalva de que a
"rede" dele depende de uma premissa diferente (§A7, item novo) da rede da Conta.

⚠️ **Histórico das duas redações erradas desta seção (mantido para não se repetir o erro):** em
2026-07-30 a rede foi enunciada como "todo `CADASTRO_PENDENTE` tem rede" — **falso**, e errava para o
lado da falsa segurança. Em 2026-07-31 a correção passou a enunciar a rede **por `Motivo`**, porque o
`Status` da época misturava os 2 códigos que de fato tiram conta calculada (`CONTA_NAO_CADASTRADA`,
`GRUPO_NAO_CADASTRADO`) com 4 códigos informativos dentro do mesmo `CADASTRO_PENDENTE` — nesse
recorte, **Abr26: dos 2.320, ZERO** eram os 2 códigos (todos `CLASSE_*`); **Jun26: 18 dos 2.361**. A
**v3.3 (2026-08-03) resolve a causa, não só o enunciado**: separa esses 3 códigos (incluindo o
`CC_NAO_CADASTRADO`, que fica bloqueante por dimensão mesmo sem tirar conta calculada) num `Status`
próprio — `CADASTRO_BLOQUEANTE` — deixando o `CADASTRO_PENDENTE` como sinônimo exclusivo de
`CLASSE_*`. Voltar a filtrar por `Status` agora **não** reintroduz o erro de 2026-07-30, porque o
`Status` em si passou a refletir a distinção que antes só existia no `Motivo`.

⚠️ **A precedência ainda mascara `DESTINO_SUSPEITO`, agora sob DOIS Status possíveis.** Como
`CADASTRO_BLOQUEANTE` e `CADASTRO_PENDENTE` vencem `DESTINO_SUSPEITO`, **1.392 linhas em Abr26 e 1.419
em Jun26 SÃO destino suspeito exibindo outro `Status`** (total inalterado pela v3.3 — é propriedade de
que códigos coexistem no mesmo `Motivo`, não de qual `Status` cada código mapeia). Prova aritmética:
`PREFIXO_818_CRUZADO` aparece como **7.859** na quebra por `Status = DESTINO_SUSPEITO` e **7.861** no
agregado do aviso (Abr26); `PREFIXO_FAMILIA_8_CRUZADO` **360** vs **362** (Jun26). **Quem revisar
`DESTINO_SUSPEITO` deve filtrar pelos códigos de `Motivo`** (`PREFIXO_*_CRUZADO`,
`RECLASSIFICADOR_FALLBACK`, `DEPARA_CONFLITO_*`), não só pelo `Status`.

📌 **Precisão sobre quais códigos deixam conta de origem, ainda válida na v3.3:** além de
`CONTA_NAO_CADASTRADA`/`GRUPO_NAO_CADASTRADO`, dois outros códigos **também** deixam a linha com a
conta de origem — `PERDA_NAO_EXPLICADA` (`FALHA_INTERNA`) e `RECLASSIFICADOR_FALLBACK`
(`DESTINO_SUSPEITO`) — mas essas contas **passaram** pelo T62 e estão cadastradas. Só
`CONTA_NAO_CADASTRADA` tem, por definição, conta **fora** da Estrutura de Contas; `CC_NAO_CADASTRADO`
não deixa conta de origem (mantém a calculada) — o que falta nele é o `Centro de Custo`, não a conta.

Volume medido (reclassificador mockado; fonte `.superpowers/sdd/task-6-v33-report.md` §Rodada A,
consolidado em `SCHEMA_OUTPUT.md §8.5`):

- **Abr26** — `DESTINO_SUSPEITO` **8.630 lançamentos / R$ 11.810.587,57** (7.859 deles /
  R$ 7.608.310,00 por `PREFIXO_818_CRUZADO`) · `CADASTRO_BLOQUEANTE` **0** (lacuna de cobertura por
  dado real neste mês) · `CADASTRO_PENDENTE` 2.320 / R$ 17.746.931,22 (só `CLASSE_*`).
- **Jun26** — `DESTINO_SUSPEITO` **7.461 / −R$ 1.523.293,99** · `CADASTRO_BLOQUEANTE` **186 /
  −R$ 26.405.479,23** (17 `CONTA_NAO_CADASTRADA`, 164 `CC_NAO_CADASTRADO`, 5 em combinação) ·
  `CADASTRO_PENDENTE` 2.175 / R$ 16.723.931,85 (só `CLASSE_*`) — a soma dos dois bate exatamente com o
  antigo 2.361 / −R$ 9.681.547,38 medido em 2026-07-28/29: é relabeling, não reclassificação.

⚠️ Esses totais de `DESTINO_SUSPEITO` são **teto, não regime**: com o mock, 100% do Tipo
`Reclassificador` (1.782 em Abr26, 1.787 em Jun26 — ~2% da base) sai marcado via
`RECLASSIFICADOR_FALLBACK`; com a API real, boa parte tende a sair `OK`.

Os números completos por `Status` e por `Motivo` estão em `SCHEMA_OUTPUT.md §8.5`; a decisão e este
racional, em `docs/DECISOES.md` (D4, 2026-07-27, com a consequência corrigida em 2026-07-30).

## Princípio de design

Toda a lógica nova mora em **`engine/controls.py`** (módulo novo) + pontos de
integração marcados com `# V2/...` / `# V3/...` nos módulos portados. A cascata Alteryx
(transforms) NÃO mudou de comportamento, exceto nas mudanças aprovadas abaixo —
**nenhuma delas altera a classificação**: elas decidem o que a ferramenta faz com o
lançamento problemático, não em que Tipo ele cai. `git diff` contra a v1 mostra
exatamente isso.

Na v3 há **um único ponto de ruptura de paridade Alteryx**: o `drop_duplicates` de 30
colunas do Tool 132 saiu. Ele só colapsava linhas idênticas em tudo (inclusive no
`Codigo Interno`), o que tanto podia ser antídoto de clone de join quanto apagar uma
duplicata nativa real — em silêncio, nos dois casos. Medido: `colapso_t132 = 0` nos dois
meses reais, na v2 e na v3.

⚠️ **Leia isto antes de decidir subir a v3 as-is — a proteção contra duplicação de
cadastro NÃO é uniforme.** O controle que deduplica cadastro na leitura
(`resolve_duplicatas_cadastro`: dedup silencioso se a linha duplicada é idêntica, ERRO
`CADASTRO_CONFLITANTE` se divergente) é aplicado a **três** inputs — Estrutura de Contas
(`transforms.py:89`), de-para de Grupos (`:552`) e o retorno do Reclassificador (`:1159`).
**Não** é aplicado ao `classe_valor_conta` (abas `Base` e `Unico CV`), que são joinados
sem esse tratamento. Consequência da remoção do Tool 132: uma linha duplicada nesses dois
cadastros fabrica linha no join e agora **aborta o mês** com `DUPLICACAO_FABRICADA`
(mensagem acionável, pedindo a correção do cadastro), onde a v2 podia colapsar em
silêncio. Isso é a política do dono aplicada de propósito — duplicação se corrige no
input, não se absorve — mas vale saber que o arquivo `classe_valor_conta` é o mantido à
mão (o cabeçalho da aba `Base` já tem `Número CV` fisicamente duplicado), portanto é o
candidato mais provável a disparar esse bloqueio. Medido `0` nos dois meses validados.

## As mudanças de comportamento (aprovadas; alteram resultado quando o caso ocorre)

| # | Onde | v1 (Alteryx) | v2 / v3 |
|---|---|---|---|
| R3 | De-Para Cobrança (Tool 11/14) | chave com 2 regras divergentes → produto cartesiano (lançamento DUPLICADO) | vale a regra da `DATA_BASE` mais recente (WARNING); divergência na MESMA competência → **desempate por SOMA(\|Valor\|) da regra** (WARNING); com empate de valor, **v3** aplica a primeira em ordem de leitura e marca o lançamento (`DEPARA_CONFLITO_MESMA_COMPETENCIA`, `DESTINO_SUSPEITO`) em vez de abortar a rodada como a v2 fazia (2026-07-20, achado real testando Jun26: 10 chaves, nenhuma empatou) |
| R4 | Overrides de GRUPO (Tool 86/88) | 11 grupos hardcoded no código; GRUPO fora deles → lançamento DESCARTADO em silêncio | novo input obrigatório `depara_grupos`; GRUPO sem cadastro → **v2: ERRO bloqueante** listando os faltantes · **v3: o lançamento fica na base** com `Status = CADASTRO_BLOQUEANTE` (**v3.3**; era `CADASTRO_PENDENTE`), `Motivo = GRUPO_NAO_CADASTRADO` e `Conta destino` = conta de origem (o aviso agregado continua, como WARNING) |
| N1 | Conta destino (Tools 56/88/126/133) | coluna podia virar float64 → código de 25 dígitos corrompia no write | coagida a string ponta-a-ponta (escopo ampliado 2026-07-20 pro Tool 88/Cobrança — `depara_grupos.xlsx` lê `Conta Contábil` como número quando a célula não está formatada como texto) |
| T132 | Dedup do caminho Cobrança | `drop_duplicates` de 30 colunas colapsa duplicata exata | **v3: removido** (ver "Princípio de design") — clone sobrevive até o censo, que bloqueia para ajuste no input em vez de reparar em silêncio |

## Os controles novos (não mudam classificação; bloqueiam resultado errado)

- **Censo de duplicação fabricada (R8):** **v3** — como o `ID Lançamento` é único por
  construção na base bruta, QUALQUER ID que apareça 2× na saída da cascata é duplicação
  fabricada por join → ERRO bloqueante com a lista dos IDs. Fica mais forte e mais simples
  que a comparação início×fim por `Codigo Interno` da v2 (que precisava guardar estado e
  podia confundir duplicata nativa, esta sempre válida — é para ela que o `Índice ERP`
  sufixa `_N`).
- **Conservação por identidade de conjunto (A1):** **v3** — deixa de ser a soma de 6 termos
  que cada stage precisava registrar corretamente e passa a ser identidade:
  `set(ID Lançamento` da saída`) == set(ID Lançamento` da base bruta`)` **e** `SUM(Valor)`
  das 2 abas = `SUM(Valor)` da base bruta ao centavo. É a diferença entre "a conta fechou" e
  "é a mesma base". Resíduo ≠ 0 → ERRO (`CONSERVACAO_VIOLADA`).
- **Rede de segurança contra perda silenciosa (`PERDA_NAO_EXPLICADA`, novo na v3):** cada
  ponto que separa lançamentos registra o **conjunto de IDs** que separou e por quê; um
  lançamento que não volta da cascata **sem que nenhuma etapa tenha assumido a autoria** é
  acusado pela reconciliação. Um stage futuro que perca linha sem registrar razão não
  consegue passar em silêncio.
  **V3.1 (2026-07-29): a rede deixou de abortar a rodada e passou a devolver o lançamento.**
  A linha volta na **aba 1** com `Conta destino` = conta de **origem**,
  `Tipo = "Não classificado"`, `Status = FALHA_INTERNA` e `Motivo = PERDA_NAO_EXPLICADA`; o
  evento sai como **WARNING**, então chega ao log, à aba "Avisos" e à tabela `warnings`
  (quando era ERRO, nunca chegava — a execução abortava antes de gravar qualquer arquivo).
  Duas razões: era o **único** bloqueio que o usuário não tinha como resolver — todos os
  outros nomeiam um input a corrigir, esse diz "o motor perdeu uma linha" — e o valor da
  linha se conserva em vez de o mês inteiro não sair. O invariante *"severidade ERRO sempre
  aborta a rodada"* **continua valendo**: foi por isso que a severidade escolhida foi
  WARNING. O ramo espelho — ID que **sobra** na saída sem existir na base bruta — **continua
  bloqueando** com `CONSERVACAO_VIOLADA`, porque uma linha que não deveria existir só
  poderia ser apagada, e apagar em silêncio é pior que parar.
  A **isenção também ficou mais sensível na v3.1**: deixou de ser "qualquer `Status != OK`"
  e passou a ser "carrega um dos 3 códigos que de fato removem o lançamento da cascata"
  (`CV_EXCLUIDA` · `CONTA_NAO_CADASTRADA` · `GRUPO_NAO_CADASTRADO`). Como a isenção antiga
  valia para **qualquer** `Status != OK` — inclusive razões meramente informativas, que são
  registradas em linhas que seguem no fluxo —, ~10 mil lançamentos por mês ficavam fora da
  rede (10.950 em Abr26 e 9.822 em Jun26 — a contagem de linhas com `Status != OK` de cada
  mês; proveniência `.superpowers/sdd/task-13-report.md` §6.3). Medido depois do estreitamento:
  **0 lançamento em `FALHA_INTERNA` nos dois meses validados** — o estreitamento não expôs
  nenhuma perda escondida (`.superpowers/sdd/task-4-v31-report.md` §3/§5).
- **Validação de inputs (D1/D2):** obrigatórios presentes, abas e colunas mínimas —
  erro acionável ANTES da cascata (nada de KeyError críptico).
- **Cadastros com chave duplicada (R2):** dedup automático se as linhas são
  idênticas (warning pedindo extração nova); ERRO se conflitantes. Aplicado à
  Estrutura de Contas, ao `depara_grupos` e ao retorno do Reclassificador.
- **Cobertura de Classe de Valor código→nome (R5):** por classe do mês, 3 WARNINGs —
  `CLASSE_NAO_CADASTRADA` (código sem match no Classe×Conta), `CLASSE_RENOMEADA`
  (código com match, nome do mês diverge do nome cadastrado — os joins da cascata são
  por nome, então uma classe renomeada falha em silêncio e cai no Reclassificador) e
  `CLASSE_SEM_UNICO_CV` (código+nome OK, mas sem linha na aba Unico CV). Não bloqueia
  (existe fallback pelo Reclassificador); objetivo é visibilidade + orientar cadastro. **Desde a
  v3.3, estes 3 códigos são a totalidade do `Status = CADASTRO_PENDENTE`** — os códigos de dimensão
  (`CONTA_NAO_CADASTRADA`, `GRUPO_NAO_CADASTRADO`, `CC_NAO_CADASTRADO`) saíram para
  `CADASTRO_BLOQUEANTE` (ver "Advertência operacional").
- **xlsb (§3.10):** detecção pelo conteúdo (inclusive xlsb-ZIP, que a v1 não pegava);
  fora da plataforma é recusado com instrução de salvar como .xlsx (na plataforma o
  LibreOffice continua convertendo). Motivo: pyxlsb lê só a 1ª coluna EM SILÊNCIO.
- **Exceções (R6):** as 2 abas passam a usar o mesmo universo (base pós-filtro de
  CVs) e a comparação de CC preserva códigos alfanuméricos.
- **Cruzamento de família de prefixo de Conta Contábil (R9, 2026-07-21) + correção automática
  (R10, 2026-07-22), na v3 sem bloqueio (D4):** só nos estágios em que a conta final vem de um
  mecanismo NÃO-determinístico — **Arbitrado classe** (match só por nome de Classe de Valor) e
  **Reclassificador** (predição de ML) — se a conta original e a conta final cruzarem uma família
  de prefixo proibida, o lançamento **fica no `base_final` com a conta calculada mantida** e
  recebe `Status = DESTINO_SUSPEITO` + `Motivo` + `Ação Recomendada` **por lançamento**
  (na v2 ele saía da base, salvo se o toggle de modo warning estivesse ligado). Duas regras:
  `818` cruzando com `{817,819}` (`PREFIXO_818_CRUZADO`) e 1º dígito `8` cruzando com não-`8`
  (`PREFIXO_FAMILIA_8_CRUZADO`) — códigos renomeados na v3 (eram `*_BLOQUEADO`), porque nada
  mais é bloqueado e o código é o texto que o usuário lê na coluna `Motivo`.
  Cobrança e Consultorias ficam de fora — nesses dois estágios a conta final vem de um cadastro
  deliberado (não de um fallback), então o mesmo cruzamento ali é roteamento intencional, não
  sintoma de cadastro incompleto (achado real: Cobrança tem hoje 160 lançamentos/R$594.745,67
  cruzando essa fronteira por design, via `REEMBOLSO`/`RESSARCIMENTO`).

  **R10 — correção automática ANTES da marcação.** Antes de marcar o lançamento como suspeito,
  `autocorrigir_conta_om` busca na Estrutura de Contas (gabarito Conta Contábil→Conta OM) uma conta alternativa na MESMA
  Conta OM cuja família bata com a conta de ORIGEM do lançamento. Se acha, reescreve só a `Conta
  destino` daquele lançamento (nunca o cadastro — desempate = primeira encontrada na ordem de
  leitura da Estrutura) e o lançamento sai com `Status = OK` + `Motivo =
  PREFIXO_CORRIGIDO_AUTOMATICAMENTE` (informativo); se não acha, segue para a marcação do R9.
  Vale pros dois Tipos (Arbitrado e Reclassificador), mesma função, um call site por Stage.
  Reduz o volume que fica marcado como suspeito: em Jun26 a correção automática resolveu 424
  lançamentos/R$ 12.488.125,82, caindo o residual de 7.049+468 (só R9) para 6.731+362 (com R10);
  em Abr26 resolveu 413/R$ 19.519.751,90, caindo de 8.174+470 para 7.861+370.

  **Na v3 não há mais toggle.** O campo `r9_modo_warning` da v2 foi **removido**: o residual
  pós-correção sempre fica no `base_final`, marcado (D4) — o que antes era o "modo warning" é
  agora o comportamento único. Isso **encerra** a pendência que a v2 registrava sobre o tipo real
  desse campo no admin do PPR; a ferramenta volta a ter só inputs de arquivo.

  Números REAIS observados na v3, nos dois meses (reclassificador mockado, API nunca chamada —
  `.superpowers/sdd/task-13-report.md` §6.5): Abr26 `PREFIXO_CORRIGIDO_AUTOMATICAMENTE` 413 /
  R$ 19.519.751,90 · `PREFIXO_818_CRUZADO` 7.861 / R$ 7.617.426,30 · `PREFIXO_FAMILIA_8_CRUZADO`
  370 / −R$ 993.452,46; Jun26 424 / R$ 12.488.125,82 · 6.731 / R$ 6.408.549,66 · 362 /
  −R$ 3.596.588,37. Idênticos aos da v2+R10 nos mesmos inputs (prova de que o R9/R10 não
  regrediu na reescrita da v3). Conservação e censo OK em todas as rodadas dos dois meses.
  Racional das decisões: `docs/DECISOES.md` 2026-07-22 e 2026-07-29 no projeto de análise.

## Transparência ao usuário (o que ele passa a ver)

**Na v3 o canal principal de transparência é a própria base final.** O contrato novo é: *a base
responde o que aconteceu com cada lançamento*, sem precisar cruzar artefato nenhum.

1. **`base_final` — 2 abas × 21 colunas, marcação por lançamento.** Todo lançamento da Base de
   Fechamento está lá, com `Status` (o filtro), `Motivo` (os códigos do que houve) e `Ação
   Recomendada` (o texto pronto). Aba 1 `Consolidado` = universo gerencial; aba 2 `Fora de Escopo`
   = só as Classes de Valor excluídas por configuração. O `Status` tem **7 valores** (`FALHA_INTERNA`
   entrou na v3.1; `CADASTRO_BLOQUEANTE` entrou na v3.3, 2026-08-03 — separa os 3 códigos que faltam
   cadastro de dimensão do `CADASTRO_PENDENTE`, que passou a significar só a família `CLASSE_*`). Na
   **v3.2** entraram mais 2 colunas: `Veiculo Legal` (16) e `Tipo` (18) — esta
   última traz o mecanismo que classificou o lançamento, com 7 valores possíveis, e dispensa cruzar a
   base com a tabela `auditoria` para saber por onde a linha passou. ⚠️ **As 4 colunas de auditoria
   não chegam ao Matrix** (ele lê as primeiras N cadastradas e ignora o resto à direita) — elas são
   para quem lê e revisa o arquivo. Schema completo, os 7 valores de `Tipo`, enum de `Status`,
   catálogo dos 15 códigos de `Motivo` e o que o Matrix lê: **`SCHEMA_OUTPUT.md §8`** (§8.6 para o
   Matrix).
2. **Tabela `auditoria`** — passa a agregar o **universo completo** por `Tipo` (com o bucket
   `"Não classificado"` para quem não voltou da cascata), contando `ID Lançamento`.
3. **Tabela `auditoria_status`** (nova) — o mesmo universo quebrado por `Status`. ⚠️ o
   `OutputField` precisa ser **cadastrado no admin do PPR** (ver a seção de cadastro abaixo).
4. **Excel de exceções (5 abas)** — continua, e continua útil: o grão dele é o **cadastro a
   corrigir** (agregado), que é diferente do grão de lançamento da base final. As 5 abas (era 3 na
   v1; R9 trouxe a 4ª, R10 a 5ª): `Contas Contábeis` · `Centros de Custo` ·
   `Correção Automática de Conta` (R10, grão = 1 linha por lançamento corrigido — `Codigo Interno`,
   `Tipo`, `Nome da Classe de Valor`, `Valor`, `Conta destino original`, `Conta destino corrigida`,
   `Conta OM`) · `Bloqueio Prefixo Conta` (R9, grão = 1 linha por lançamento sinalizado —
   `Codigo Interno`, `Tipo`, contas origem/destino, Classe de Valor, Valor, Centro de Custo,
   `Motivo`, e agora também `Conta OM` e `Ação Recomendada` [R10], orientando o cadastro específico
   que resolveria aquele lançamento) · **`Avisos`** (sempre presente: Etapa · Severidade · Código ·
   Mensagem · Registros · Valor — inclui o resumo de conservação e as exclusões por Classe de
   Valor). ⚠️ o **nome** da 4ª aba (`Bloqueio Prefixo Conta`) é herdado da v2; na v3 nada é
   bloqueado — os mesmos lançamentos estão no `base_final` com `Status = DESTINO_SUSPEITO`. Schema
   completo (colunas, tipos, nulabilidade e o catálogo de todos os códigos possíveis por
   Etapa/Severidade) em `SCHEMA_OUTPUT.md` — inclui uma nota importante: eventos de severidade
   **ERRO** nunca chegam à aba "Avisos" nem à tabela `warnings` (a execução aborta antes do excel
   ser gravado); só aparecem na mensagem de erro da rodada.
5. **Resumo no topo do `log_execucao`**: termos da conciliação + ERRO/WARNINGs.
6. **Tabela `warnings`** — mesmo conteúdo da aba "Avisos" acima; **não é `OutputField` novo da
   v3** (já existia na v2), cadastro no admin do PPR continua **opcional**.
7. **Erros bloqueantes com mensagem acionável** no status da execução: qual arquivo,
   o que foi encontrado, o que fazer.

### `Status = FALHA_INTERNA` — o que fazer quando aparecer (V3.1)

É o único `Status` que **não** aponta nada para o cliente corrigir: significa que **a ferramenta**
perdeu o lançamento no caminho e nenhuma etapa registrou o motivo (`Motivo = PERDA_NAO_EXPLICADA`).
Para quem opera:

- **Não é problema de cadastro nem de arquivo enviado.** Não há input a corrigir — reenviar os mesmos
  arquivos não resolve.
- **O valor foi preservado:** a linha volta na aba 1 com a `Conta destino` = a conta de **origem** e
  `Tipo = "Não classificado"`, para a soma de `Valor` da saída continuar igual à da Base de
  Fechamento. É preservação de valor, não classificação — a conta ali é a de origem, não um destino
  calculado.
- **Não carregue essas linhas no Matrix**, e **avise o time responsável pela ferramenta informando o
  `ID Lançamento`** de cada uma (a coluna **17** da base desde a v3.2 — era a 16 até a v3.1, quando
  `Veiculo Legal` entrou à frente dela; é a chave que permite achar a linha na base
  bruta). O mesmo evento sai como **WARNING** na aba "Avisos", na tabela `warnings` e no log, com a
  contagem, o valor e os 10 primeiros IDs.
- **Se a linha tiver mais de um `Motivo`, a instrução de NÃO carregar prevalece** sobre as outras
  ações do texto — a `Ação Recomendada` é concatenada na ordem de registro das razões, não por
  gravidade (`SCHEMA_OUTPUT.md §8.4`).
- **Observado nos dois meses validados: 0 lançamento** (`.superpowers/sdd/task-4-v31-report.md`).
  É o resultado esperado — este caminho existe para o dia em que uma regressão o disparar.
- ⚠️ **Lacuna de cobertura declarada:** por consequência do zero acima, o caminho `FALHA_INTERNA`
  **nunca foi exercitado por dado real** — e não há como exercitá-lo, porque zero é o resultado
  desejado. Quem o prova é teste **sintético**: a verificação V6 da bateria (5 asserções:
  devolução, `Status`, `Motivo`, conta de origem preservada e severidade WARNING) e
  `tests/test_v3_perda_devolve_lancamento.py`. Está na mesma família das outras lacunas declaradas
  desta entrega (aba 2 vazia nos dois meses, `GRUPO_NAO_CADASTRADO`, `DADO_INVALIDO`, sufixo `_N`):
  declarada, não escondida.

## O que cadastrar no admin do PPR (delta vs v1)

| Item | Tipo | Obrigatório? |
|---|---|---|
| InputField `depara_grupos` — "De-Para Grupos → Conta (Cobrança)" | Arquivo | **Sim** (a execução falha sem ele, com mensagem clara) |
| OutputField `warnings` — "Avisos da execução" | Tabela (colunas: etapa·severidade·codigo·mensagem [Texto], registros [Inteiro], valor [Texto/Numérico]) | Opcional (conteúdo já sai na aba "Avisos" e no log) |
| **OutputField `auditoria_status`** — "Auditoria por Status (V3)" | Tabela (colunas: status [Texto], registros [Inteiro], soma_valor [Texto]) | ⚠️ **PENDENTE — precisa ser cadastrado.** `main()` **sempre** emite essa chave; não é possível determinar daqui se a plataforma ignora uma chave não cadastrada ou devolve erro |

⚠️ **Pendência do `OutputField` `auditoria_status`** — mesma natureza da pendência que o
`depara_grupos` teve no R4: é um item de admin do PPR que a ferramenta já assume existir. `main()`
emite a chave em toda execução (a quebra do universo completo por `Status`, ao lado da `auditoria`
por `Tipo`). **Confirmar com o time de analytics antes de subir** — se a plataforma rejeitar chave
não cadastrada, a execução falha no callback, não no engine.

✅ **Encerrada:** o campo `r9_modo_warning` que a v2 pedia (e cujo TIPO no admin era pendência
aberta) **não existe mais na v3** — a D4 tornou o modo warning o comportamento único. A ferramenta
volta a declarar só InputFields de arquivo.

Template do `depara_grupos`: header na linha 1, colunas
**Grupo · Conta OM · Conta Contábil**, na aba **`Grupos Cobrança`** (nome vigente desde a **v3.3**,
2026-08-03 — é a aba que o arquivo mestre de cadastros do cliente já usa; até a v3.2 o nome esperado
era `De-Para Grupos`, mantido abaixo como registro histórico datado). Seed inicial (os 11 grupos da
v1): `aux_files/depara_grupos_seed.xlsx` (regenerável por `aux_files/gera_seed_depara_grupos.py`).

📌 **V3.1 (2026-07-29) — a aba do `depara_grupos` passou a ser lida por NOME.** Até a v3.0 este
input era lido pela **posição** (a 1ª aba do arquivo, `sheet: 0`); agora é lido pelo nome (na v3.1/
v3.2 era `De-Para Grupos`; **desde a v3.3, `Grupos Cobrança`**), **caindo para a 1ª aba** quando esse
nome não existe no arquivo — o fallback preserva os arquivos de `depara_grupos` já em uso, cuja aba
pode ter qualquer nome (e é o que roda em **todas** as rodadas de validação até 2026-08-03, que usam
o arquivo legado de aba `Sheet1`). O nome é sobreponível por env var
(`BBV001_DEPARA_GRUPOS_SHEET`). Consequência prática: **dá para enviar
todos os cadastros num arquivo único, um por aba**, subindo o mesmo arquivo em todos os campos de
cadastro do formulário — cada leitor acha a sua aba pelo nome. ⚠️ **A Base de Fechamento continua em
arquivo separado** (decisão do dono: ela nunca vem junto dos cadastros). Nomes de aba esperados por
input e as ressalvas do arquivo único: `docs/GUIA_INPUTS_TROUBLESHOOTING.md §1` do projeto de análise.
⚠️ Na **v3**, GRUPO de Cobrança não cadastrado aqui **não bloqueia mais** a execução: os
lançamentos ficam no `base_final` com `Status = CADASTRO_BLOQUEANTE` (era `CADASTRO_PENDENTE` até a
v3.2 — ver "Advertência operacional"), `Motivo =
GRUPO_NAO_CADASTRADO` e `Conta destino` = conta de origem — mas **sem a conta de destino
correta**, então o cadastro continua sendo o que precisa ser corrigido. Em Abr26/Jun26 isso já
pegou o GRUPO `REEMBOLSO` (309 lançamentos/R$428.809,42 em Abr26; 34/R$64.409,78 em Jun26), hoje
já cadastrado (`Fluxo reclassificacao/BBV001-Grupo x Contas Cobrança.xlsx`, 12 grupos) — por isso
esse caminho sai com **0 lançamentos nos dois meses testados**, e só é exercitado por teste.

⚠️ **Gotcha no cadastro Classe×Conta (aba `Base`):** o cabeçalho tem "Número CV" **duplicado**
(2 colunas físicas com o mesmo nome — o Excel lê como `Número CV`/`Número CV.1`, que divergem
entre si em parte das linhas). O código canônico usado pelo R5 é a 3ª coluna, **`Número att`**
— não confundir com as duas "Número CV". Detalhe em `docs/CATALOGO_BASES.md` do projeto de análise.

📌 **V3.3 (2026-08-03) — a aba `Base` aceita DUAS grafias de coluna.** O arquivo mestre de cadastros
do cliente (`BBV001-Classe de Valor x Conta Contábil.xlsx`) renomeou 3 colunas desta aba
(`Nome da classe de valor`→`Nome classe de valor`, `Cód conta contábil permitida`→`Cód conta
contábil`, `Cód Classe de Valor`→`Número att`) — o código detecta a grafia do mestre e renomeia para
o nome canônico **antes** da validação de colunas, então tanto o arquivo mestre quanto o arquivo de
regressão (`...v1 MU.xlsx`, grafia antiga) leem sem erro. `config.COLUNAS_ALIAS["classe_valor_conta_base"]`
é a fonte única do mapeamento; nunca sobrescreve coluna canônica já presente. O log emite
`[classe_valor_conta_base] colunas renomeadas por alias (V3.3): {...}` (nível INFO — não aparece na
bateria, que silencia INFO; confirmado via leitura direta em `.superpowers/sdd/task-6-v33-report.md`
§Rodada B). Detalhe do layout de arquivo único (quais 4 cadastros podem sair do mesmo arquivo, quais
2 não podem, e o nome exato de cada aba): `docs/GUIA_INPUTS_TROUBLESHOOTING.md §1`.

## Catálogo dos erros bloqueantes (código → causa → ação)

Na **v3** só **3 famílias** ainda abortam a rodada — **7 códigos** (eram 8 até a v3.0; ver a nota
depois da tabela). Todo o resto virou WARNING **e** marcação por lançamento na `base_final`
(`Status`/`Motivo`/`Ação Recomendada` — catálogo completo dos 15 códigos em
`SCHEMA_OUTPUT.md §8.4`).

| Família | Código | Causa | Ação do usuário |
|---|---|---|---|
| Estrutural de leitura | `INPUT_OBRIGATORIO_AUSENTE` | input obrigatório não enviado | enviar o arquivo |
| Estrutural de leitura | `FORMATO_XLSB` | arquivo .xlsb fora da plataforma | salvar como .xlsx e reenviar |
| Estrutural de leitura | `COLUNAS_FALTANDO` | template mudou / arquivo ou aba trocada | conferir arquivo/aba/colunas (a mensagem lista quais faltam) |
| Estrutural de leitura | `BASE_VAZIA` | Base de Fechamento sem nenhum lançamento | conferir arquivo/aba |
| Duplicação | `DUPLICACAO_FABRICADA` | join duplicou lançamentos (cadastro com chave repetida) | corrigir o cadastro apontado |
| Duplicação | `CADASTRO_CONFLITANTE` | cadastro com chave duplicada e conteúdo divergente | pedir extração nova do relatório |
| Integridade da espinha | `CONSERVACAO_VIOLADA` | a saída não é o mesmo conjunto de lançamentos que entrou (inclui o ramo "ID sobrando": lançamento na saída da cascata que não existe na base bruta) | reportar ao time de analytics — **não usar a base desta execução** |

⚠️ **`PERDA_NAO_EXPLICADA` saiu desta tabela na v3.1** (2026-07-29) — era o 8º código e o único da
família "integridade da espinha" além do `CONSERVACAO_VIOLADA`. Hoje ele **não bloqueia**: o
lançamento volta marcado com `Status = FALHA_INTERNA` e o evento sai como **WARNING** (ver
"Transparência ao usuário" e `SCHEMA_OUTPUT.md §8.3`). O que **não** mudou: severidade ERRO continua
abortando a rodada sempre, e o ramo "ID sobrando" continua bloqueando com `CONSERVACAO_VIOLADA`.

**Saíram da lista de bloqueio na v3** (agora marcam o lançamento e a rodada segue):
`GRUPO_NAO_CADASTRADO` · `MES_INVALIDO` (o lançamento sai no Matrix **sem competência**,
`DateTime_Out` vazio — consequência assumida da D6) · `DEPARA_CONFLITO_MESMA_COMPETENCIA` (com
empate de valor, aplica a **primeira regra em ordem de leitura** e marca o lançamento).
`BASE_VAZIA_POS_FILTROS` **deixou de existir** — o universo agora é a base bruta, não o pós-filtro.

⚠️ `PREFIXO_818_CRUZADO` e `PREFIXO_FAMILIA_8_CRUZADO` (R9) nunca estiveram nesta tabela — são
WARNING agregado + marcação por lançamento. Idem `PREFIXO_CORRIGIDO_AUTOMATICAMENTE` (R10,
informativo — reporta o que foi corrigido sem intervenção humana). Ver "Cruzamento de família de
prefixo de Conta Contábil" acima e as abas "Correção Automática de Conta" / "Bloqueio Prefixo
Conta" no excel.
