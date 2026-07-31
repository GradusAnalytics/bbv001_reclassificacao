# BBV001 v2 — O que mudou vs a v1 (e o que cadastrar no PPR)

> **Para o time de analytics.** Esta pasta é a proposta v2 da ferramenta
> `bbv001_reclassificacao`: a mesma cascata de classificação portada do Alteryx
> (paridade preservada), MAIS controles de edge cases, controle de duplicação de
> linhas nos joins e avisos transparentes ao usuário final. A especificação completa,
> com o racional e a evidência de cada mudança (medida nas bases reais de Abr26 e
> Jun26 — rodada completa validada nos dois meses em 2026-07-21), está no
> `docs/SPEC_V2.md` do projeto de análise. O `README_TOOL.md` original da v1
> continua valendo para o deploy/infra (ECS, workflow, bridge da API).

## Princípio de design

Toda a lógica nova mora em **`engine/controls.py`** (módulo novo) + pontos de
integração marcados com `# V2/...` nos módulos portados. A cascata Alteryx
(transforms) NÃO mudou de comportamento, exceto nas 3 mudanças aprovadas abaixo.
`git diff` contra a v1 mostra exatamente isso.

## As 3 mudanças de comportamento (aprovadas; alteram resultado quando o caso ocorre)

| # | Onde | v1 (Alteryx) | v2 |
|---|---|---|---|
| R3 | De-Para Cobrança (Tool 11/14) | chave com 2 regras divergentes → produto cartesiano (lançamento DUPLICADO) | vale a regra da `DATA_BASE` mais recente (WARNING); divergência na MESMA competência → **desempate por SOMA(\|Valor\|) da regra** (WARNING); só vira ERRO se o valor TAMBÉM empatar (2026-07-20, achado real testando Jun26: 10 chaves, nenhuma empatou) |
| R4 | Overrides de GRUPO (Tool 86/88) | 11 grupos hardcoded no código; GRUPO fora deles → lançamento DESCARTADO em silêncio | novo input obrigatório `depara_grupos`; GRUPO sem cadastro → **ERRO bloqueante** listando os faltantes |
| N1 | Conta destino (Tools 56/88/126/133) | coluna podia virar float64 → código de 25 dígitos corrompia no write | coagida a string ponta-a-ponta (escopo ampliado 2026-07-20 pro Tool 88/Cobrança — `depara_grupos.xlsx` lê `Conta Contábil` como número quando a célula não está formatada como texto) |

## Os controles novos (não mudam classificação; bloqueiam resultado errado)

- **Censo de duplicatas início×fim (R8):** nenhum `Codigo Interno` pode sair do
  tratamento mais duplicado do que entrou (duplicata nativa da base é válida; a
  fabricada por join com cadastro sujo é ERRO bloqueante com a lista das chaves).
- **Equação de conservação (A1):** base bruta = excluídas (CVs configuradas) +
  exceções de conta + colapso de duplicata exata + base final. Resíduo ≠ 0 → ERRO.
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
  (existe fallback pelo Reclassificador); objetivo é visibilidade + orientar cadastro.
- **xlsb (§3.10):** detecção pelo conteúdo (inclusive xlsb-ZIP, que a v1 não pegava);
  fora da plataforma é recusado com instrução de salvar como .xlsx (na plataforma o
  LibreOffice continua convertendo). Motivo: pyxlsb lê só a 1ª coluna EM SILÊNCIO.
- **Exceções (R6):** as 2 abas passam a usar o mesmo universo (base pós-filtro de
  CVs) e a comparação de CC preserva códigos alfanuméricos.
- **Bloqueio de prefixo de Conta Contábil (R9, 2026-07-21) + correção automática e modo warning
  (R10, 2026-07-22):** só nos estágios em que a conta final vem de um mecanismo
  NÃO-determinístico — **Arbitrado classe** (match só por nome de Classe de Valor) e
  **Reclassificador** (predição de ML) — se a conta original e a conta final cruzarem uma família
  de prefixo proibida, o lançamento é candidato a **bloqueio individual**: sai do `base_final` e
  cai numa aba nova de exceção, sem travar a rodada. Duas regras: `818` cruzando com `{817,819}`
  (`PREFIXO_818_BLOQUEADO`) e 1º dígito `8` cruzando com não-`8` (`PREFIXO_FAMILIA_8_BLOQUEADO`).
  Cobrança e Consultorias ficam de fora — nesses dois estágios a conta final vem de um cadastro
  deliberado (não de um fallback), então o mesmo cruzamento ali é roteamento intencional, não
  sintoma de cadastro incompleto (achado real: Cobrança tem hoje 160 lançamentos/R$594.745,67
  cruzando essa fronteira por design, via `REEMBOLSO`/`RESSARCIMENTO`).

  **R10 — correção automática ANTES do bloqueio.** Antes de decidir bloquear, `autocorrigir_conta_om`
  busca na Estrutura de Contas (gabarito Conta Contábil→Conta OM) uma conta alternativa na MESMA
  Conta OM cuja família bata com a conta de ORIGEM do lançamento. Se acha, reescreve só a `Conta
  destino` daquele lançamento (nunca o cadastro — desempate = primeira encontrada na ordem de
  leitura da Estrutura); se não acha, o lançamento segue pro bloqueio, exatamente como no R9. Vale
  pros dois Tipos (Arbitrado e Reclassificador), mesma função, um call site por Stage. Reduz o
  volume que efetivamente bloqueia: em Jun26 a correção automática resolveu 424 lançamentos/
  R$ 12.488.125,82, caindo o bloqueio de 7.049+468 (só R9) para 6.731+362 (com R10); em Abr26
  resolveu 413/R$ 19.519.751,90, caindo o bloqueio de 8.174+470 para 7.861+370.

  **R10 — modo warning configurável.** Novo campo `r9_modo_warning` (default = **bloqueia**,
  comportamento herdado do R9) decide o destino do residual que sobra pós-correção automática:
  ausente/`False` → o residual sai do `base_final` (bloqueia); `True` → o residual volta ao
  `base_final` com só um aviso agregado (`PREFIXO_MODO_WARNING_ATIVO`), sem excluir nada. Validado
  nos dois modos em Jun26 (mesma base): modo bloqueia → 80.093 lançamentos no final; modo warning
  → 87.186 (= 80.093 + 7.093, o residual bloqueado volta inteiro) — conservação e censo OK nos
  dois. ⚠️ **Pendência**: o tipo real do campo `r9_modo_warning` no admin do PPR ainda não foi
  confirmado com o time de analytics — a ferramenta hoje só tem inputs de arquivo, não um toggle
  booleano nativo; o código já faz parsing tolerante de string (`"true"/"1"/"sim"/"yes"`), mesma
  natureza da pendência que o `depara_grupos` teve no R4.

  Validado em Abr26 (R9 sozinho: 8.174 + 470 lançamentos bloqueados; com R10: 413 corrigidos
  automaticamente, restam 7.861 + 370 bloqueados no modo bloqueia) e Jun26 (R9 sozinho: 7.049 +
  468; com R10: 424 corrigidos, restam 6.731 + 362 bloqueados no modo bloqueia / voltam inteiros
  no modo warning) — conservação e censo OK em todas as rodadas dos dois meses. Números completos:
  ver `docs/DECISOES.md` 2026-07-22 no projeto de análise.

## Transparência ao usuário (o que ele passa a ver)

O excel de exceções agora tem **5 abas** (era 3 na v1; R9 trouxe a 4ª, R10 a 5ª):
`Contas Contábeis` · `Centros de Custo` · `Correção Automática de Conta` (R10, grão = 1 linha por
lançamento corrigido — `Codigo Interno`, `Tipo`, `Nome da Classe de Valor`, `Valor`, `Conta destino
original`, `Conta destino corrigida`, `Conta OM`) · `Bloqueio Prefixo Conta` (R9, grão = 1 linha
por lançamento bloqueado — `Codigo Interno`, `Tipo`, contas origem/destino, Classe de Valor, Valor,
Centro de Custo, `Motivo`, e agora também `Conta OM` e `Ação Recomendada` [R10], orientando o
cadastro específico que resolveria aquele lançamento) · `Avisos`.

Schema completo (colunas, tipos, nulabilidade e o catálogo de todos os códigos possíveis por
Etapa/Severidade) em `SCHEMA_OUTPUT.md` — inclui uma nota importante: eventos de severidade **ERRO**
nunca chegam à aba "Avisos" nem à tabela `warnings` (a execução aborta antes do excel ser gravado);
só aparecem na mensagem de erro da rodada.

1. **Aba "Avisos"** no excel de exceções (sempre): Etapa · Severidade · Código ·
   Mensagem · Registros · Valor — inclui o resumo de conservação e as exclusões
   por Classe de Valor (R1).
2. **Resumo no topo do `log_execucao`**: termos da conciliação + ERRO/WARNINGs.
3. **Tabela `warnings`** (mesmo conteúdo; OutputField novo, cadastro OPCIONAL).
4. **Erros bloqueantes com mensagem acionável** no status da execução: qual arquivo,
   o que foi encontrado, o que fazer.

## O que cadastrar no admin do PPR (delta vs v1)

| Item | Tipo | Obrigatório? |
|---|---|---|
| InputField `depara_grupos` — "De-Para Grupos → Conta (Cobrança)" | Arquivo | **Sim** (a execução falha sem ele, com mensagem clara) |
| OutputField `warnings` — "Avisos da execução" | Tabela (colunas: etapa·severidade·codigo·mensagem [Texto], registros [Inteiro], valor [Texto/Numérico]) | Opcional (conteúdo já sai na aba "Avisos" e no log) |
| Campo `r9_modo_warning` — "Modo warning pro bloqueio de prefixo (R10)" | ⚠️ **Tipo a confirmar com o time de analytics** — o desenho assume booleano/toggle, mas a ferramenta hoje só declara InputFields de arquivo | Opcional (default = bloqueia, comportamento herdado do R9, se o campo não existir ou vier vazio) |

⚠️ **Pendência de tipo do campo `r9_modo_warning`** — mesma natureza da pendência que o
`depara_grupos` teve no R4 (lá era template/obrigatoriedade do InputField; aqui é o TIPO do campo:
booleano? seleção? texto?), já que o PPR hoje não tem precedente de campo booleano nesta
ferramenta. Enquanto não confirmado, `main()` aceita qualquer representação textual comum de
verdadeiro (`"true"`, `"1"`, `"sim"`, `"yes"`, case-insensitive) e trata qualquer outra coisa —
incluindo o campo ausente — como falso (modo bloqueia, igual ao R9).

Template do `depara_grupos`: 1 aba, header na linha 1, colunas
**Grupo · Conta OM · Conta Contábil**. Seed inicial (os 11 grupos da v1):
`aux_files/depara_grupos_seed.xlsx` (regenerável por `aux_files/gera_seed_depara_grupos.py`).
⚠️ Qualquer GRUPO de Cobrança que aparecer na base e não estiver cadastrado aqui bloqueia a
execução (`GRUPO_NAO_CADASTRADO`) — em Abr26/Jun26 isso já pegou o GRUPO `REEMBOLSO` (309
lançamentos/R$428.809,42 em Abr26; 34/R$64.409,78 em Jun26), hoje já cadastrado
(`Fluxo reclassificacao/BBV001-Grupo x Contas Cobrança.xlsx`, 12 grupos).

⚠️ **Gotcha no cadastro Classe×Conta (aba `Base`):** o cabeçalho tem "Número CV" **duplicado**
(2 colunas físicas com o mesmo nome — o Excel lê como `Número CV`/`Número CV.1`, que divergem
entre si em parte das linhas). O código canônico usado pelo R5 é a 3ª coluna, **`Número att`**
— não confundir com as duas "Número CV". Detalhe em `docs/CATALOGO_BASES.md` do projeto de análise.

## Catálogo dos erros bloqueantes (código → causa → ação)

⚠️ `PREFIXO_818_BLOQUEADO` e `PREFIXO_FAMILIA_8_BLOQUEADO` (R9) **não estão nesta tabela** — são
WARNING agregado (não travam a execução), porque o bloqueio é por lançamento, não por rodada.
Idem `PREFIXO_CORRIGIDO_AUTOMATICAMENTE` (R10, informativo — reporta o que foi corrigido sem
intervenção humana) e `PREFIXO_MODO_WARNING_ATIVO` (R10, só aparece quando `r9_modo_warning` está
ligado). Ver "Bloqueio de prefixo de Conta Contábil (R9) + correção automática e modo warning
(R10)" acima e as abas "Correção Automática de Conta" / "Bloqueio Prefixo Conta" no excel.

| Código | Causa | Ação do usuário |
|---|---|---|
| `INPUT_OBRIGATORIO_AUSENTE` | input obrigatório não enviado | enviar o arquivo |
| `FORMATO_XLSB` | arquivo .xlsb fora da plataforma | salvar como .xlsx e reenviar |
| `COLUNAS_FALTANDO` | template mudou / arquivo trocado | conferir arquivo/aba/colunas |
| `BASE_VAZIA` / `BASE_VAZIA_POS_FILTROS` | base sem lançamentos (ou tudo filtrado) | conferir arquivo/aba |
| `CADASTRO_CONFLITANTE` | cadastro com chave duplicada e conteúdo divergente | pedir extração nova do relatório |
| `DEPARA_CONFLITO_MESMA_COMPETENCIA` | De-Para com regras divergentes na mesma DATA_BASE **E** empate de valor (SOMA(\|Valor\|) igual pras duas regras — caso raro; sem empate de valor a v2 resolve sozinha com WARNING `DEPARA_CONFLITO_RESOLVIDO_POR_VALOR`) | corrigir o De-Para |
| `GRUPO_NAO_CADASTRADO` | GRUPO de Cobrança fora do `depara_grupos` | adicionar o(s) GRUPO(s) ao cadastro |
| `DUPLICACAO_FABRICADA` | join duplicou lançamentos (cadastro com chave repetida) | corrigir o cadastro apontado |
| `CONSERVACAO_VIOLADA` | perda/criação de valor não rastreada (defesa final) | reportar ao time de analytics |
| `MES_INVALIDO` | coluna Mes 100% ilegível como data | corrigir o formato na base |
