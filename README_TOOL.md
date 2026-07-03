# BBV001 — Ferramenta tradicional do PPR

Implementada no padrão do template `analytics-standard-template` (dummy_repo):
um repositório que o GitHub Actions builda numa imagem ECR e implanta como task
**ECS Fargate**. O PPR aciona via `tipo_execucao=ecs` → POST em `/dev/generic_tool`
com `toolname=code`; o `gradus-platform` (`ecs_handler`) baixa os inputs do S3 já
abertos, chama `main()`, sobe os outputs e faz o callback. **Não escrevemos S3 nem
callback diretamente** — a única exceção é `engine/reclassifier_bridge.py`, que usa
as mesmas credenciais AWS já disponíveis no ambiente do processo para subir arquivos
de scratch e chamar a API pública do PPR (ver seção "Reclassificação via API" abaixo).

O engine BBV001 fica em `engine/` e **não é alterado** na sua lógica de negócio
original (paridade com o Alteryx) — a única adição é a etapa de reclassificação via
API descrita abaixo, que substitui o processo manual de Run 1/Run 2.

## Conteúdo do repositório

```
bbv001_reclassificacao/            # nome do repo = code do tool = nome da task ECS
├── bbv001_reclassificacao.py      # main(**inputs) — entrypoint do tool
├── ecs_handler.py                 # from gradus import ecs_handler; ecs_handler()
├── engine/                        # config, helpers, io_utils, transforms, pipeline (intactos)
├── requirements.txt               # pins padrão + gradus-platform
├── setup.py
├── Dockerfile                     # python:3.11; copia o tool + engine/
├── task-definition.json           # ECS Fargate, 2 vCPU / 8 GB, x86_64
└── .github/workflows/python-app.yml
```

> Já está customizado para o nome **`bbv001_reclassificacao`** e stage **dev**
> (não precisa rodar o `customize_repo.py`). Se quiser outro nome, troque
> `bbv001_reclassificacao` em todos os arquivos e renomeie o `.py`.

## Inputs / Outputs para cadastrar no PPR

Ao criar a `Ferramenta` no admin, os **códigos** dos campos têm que bater com os
nomes usados no `main()` e no dict de saída.

### Ferramenta
| Campo | Valor |
|---|---|
| nome | Reclassificação de Lançamentos (BBV001) |
| code | **`bbv001_reclassificacao`** (= nome do repo) |
| tipo_execucao | **ECS** |
| modo_render | Genérico |
| stage | dev |

### InputFields (todos tipo *Arquivo*)
| code | nome | required |
|---|---|---|
| `base_fechamento` | Base de Fechamento (mensal) | Obrigatório |
| `depara_custo` | De-Para Custo | Obrigatório |
| `classe_valor_conta` | Classe de Valor × Conta Contábil | Obrigatório |
| `estrutura_contas` | Estrutura de Contas | Obrigatório |
| `base_reclassificada` | Base Reclassificada — **override manual** (pula a chamada à API abaixo) | **Opcional** |
| `estrutura_entidades_cc` | Estrutura de Entidades e Centros de Custo | **Opcional** |

> `parametros_reclassificador` e `modelo_reclassificador` **não são mais inputs desta
> ferramenta**: a etapa de reclassificação chama `reclassificador_predicao_bbv001`
> (ferramenta dedicada, já publicada no PPR), que usa seus próprios arquivos default
> de modelo e parâmetros do lado dela.

> O nome do arquivo enviado não importa — o `main()` renomeia para o nome
> canônico que o engine espera. As **abas** dos Excel precisam ser as padrão
> (Base / Planilha1 / Base+Unico CV / Estrutura de contas / Sheet1 /
> "Estrutura completa de Entidades").
>
> Todos os inputs de planilha (`base_fechamento`, `depara_custo`,
> `classe_valor_conta`, `estrutura_contas`, `base_reclassificada`,
> `estrutura_entidades_cc`) aceitam tanto **.xlsx** quanto **.xlsb** —
> `engine/io_utils.py` detecta o formato real pelos magic bytes do arquivo (a
> extensão não é confiável, já que `main()` sempre grava com nome `.xlsx` fixo) e usa
> `pyxlsb` automaticamente quando necessário.
>
> ⚠️ **Atenção com `estrutura_entidades_cc` em .xlsb**: `pyxlsb` não recalcula
> fórmulas, só devolve o valor bruto armazenado no arquivo (diferente do `openpyxl`
> com `data_only=True`, usado para `.xlsx`, que traz o valor já calculado pelo
> Excel). Se esse cadastro específico depender de fórmulas, confira se os valores da
> aba "Centros de Custo" saíram corretos — um WARNING é emitido no log sempre que
> esse input vier em `.xlsb`. Sem esse input, a aba sai vazia; não impede a execução.

### OutputFields
| code | nome | tipo |
|---|---|---|
| `base_final` | Base consolidada final | Arquivo |
| `base_reclassificador` | Base para reclassificador | Arquivo |
| `auditoria` | Auditoria por Tipo | **Tabela** |
| `log_execucao` | Log de execução | Texto longo |
| `excecoes` | Exceções — não cadastrados (Excel 2 abas) | Arquivo |

> **`excecoes`** é um .xlsx com duas abas: **Contas Contábeis** (contas da base
> ausentes da Estrutura de Contas — as que hoje são descartadas no Tool 62) e
> **Centros de Custo** (CCs da base ausentes do cadastro de Entidades x CC).
> Cada aba traz Código · Descrição · Valor (somado por código). A descrição vem
> da base do realizado — os itens, por definição, não estão no cadastro. O fluxo
> principal não muda: é só um relatório (paridade com o Alteryx preservada).

### Colunas da tabela `auditoria` (ColunaTabelaOutput, no OutputField `auditoria`)
| code | nome | tipo | ordem |
|---|---|---|---|
| `tipo` | Tipo | Texto | 1 |
| `registros` | Registros | Inteiro | 2 |
| `soma_valor` | Soma do Valor | Float | 3 |

(O `main()` ainda devolve `run_mode` = "1"/"2"; opcionalmente crie um OutputField
`run_mode` tipo Texto curto se quiser exibi-lo.)

## Reclassificação via API (substitui o Run 1 / Run 2 manual)

A etapa de "Reclassificador" (Tools 121-134 do Alteryx original) hoje chama, dentro da
própria execução do BBV001, a ferramenta `reclassificador_predicao_bbv001` — uma
ferramenta dedicada ao BBV001, já publicada no PPR, que usa seus próprios arquivos
default de modelo e parâmetros do reclassificador (`engine/reclassifier_bridge.py`):

1. Traduz a `base_reclassificador` (schema `RECLASSIFIER_OUTPUT_SCHEMA`) renomeando
   3 colunas (`Codigo Interno`→`Chave única`, `Conta Contabil`→`Cód Conta Contábil`,
   `Centro de Custo`→`Código Centro de Custo`) para o formato esperado pela `Base` do
   `reclassificador_predicao_bbv001`.
2. Sobe essa base traduzida para uma chave de scratch em
   `s3://bucket-ppr/tools/reclassificador_predicao_bbv001/_bridge/<uuid>/base.xlsx`.
   Os arquivos de modelo e parâmetros **não são enviados** — `reclassificador_predicao_bbv001`
   já os resolve pelos seus próprios `default_file`.
3. Dispara `POST {PPR_API_BASE_URL}/ferramentas/api/exec/` com
   `ferramenta=reclassificador_predicao_bbv001`, espera por polling em
   `GET .../api/exec/<execution_id>/` e baixa o output `output`
   (`base_reclassificada.xlsx`).
4. Segue o pipeline normalmente (`transforms.integrate_reclassified`), que só exige
   as colunas `Chave única` e `Conta ajustada` desse arquivo — presentes sem
   necessidade de tradução no sentido de volta.

Se o input `base_reclassificada` for informado manualmente, essa chamada é **pulada**
e o arquivo fornecido é usado diretamente (comportamento equivalente ao antigo Run 2).

### Pré-requisitos operacionais
- Variáveis de ambiente na task ECS (`task-definition.json` →
  `containerDefinitions[0].environment`):
  - `PPR_API_BASE_URL` (ex.: `https://ppr.gradusanalytics.com.br`)
  - `PPR_API_TOKEN` — token de um usuário do PPR (`tokens.UserToken`) com permissão
    para a ferramenta `reclassificador_predicao_bbv001` e `time_credits` suficientes
    (ou `is_staff=True`). Por ora reaproveita um token de staff existente; migrar para
    um usuário de serviço dedicado + AWS Secrets Manager é a hardening recomendada.
- `aws_access_key_id`/`aws_secret_access_key` — já chegam ao ambiente do processo via
  o dispatcher `generic_tool` do PPR (mesmas variáveis que o `ecs_handler.py` legado
  de `reclassificador_predicao`/`reclassificador_metricas` usa para ler/escrever S3).

## Deploy

1. Crie o repositório **`bbv001_reclassificacao`** na org `GradusAnalytics`
   (pode ser "use this template" a partir do dummy_repo, depois substitua os
   arquivos por estes — ou suba estes arquivos direto).
2. Peça à equipe de Analytics para adicionar os **secrets** `AWS_ACCESS_KEY_ID` /
   `AWS_SECRET_ACCESS_KEY` ao repositório (o workflow precisa para ECR/ECS).
3. Push na branch **`dev`**. O workflow `python-app.yml`:
   - builda a imagem e faz push para o ECR `bbv001_reclassificacao_ecs:dev`;
   - registra a task definition `dev-bbv001_reclassificacao_ecs`;
   - publica o pacote no S3 `bucket-gradus-packages`.
4. Confirme que o dispatcher `generic_tool` reconhece o `toolname`
   `bbv001_reclassificacao` (mesma convenção das outras ferramentas ECS de vocês).
5. No PPR, cadastre a `Ferramenta` + Inputs/Outputs acima, libere para os usuários e
   teste pela própria interface (upload dos 4 arquivos obrigatórios → executar).
6. Configure `PPR_API_BASE_URL`/`PPR_API_TOKEN` no `task-definition.json` (ver seção
   "Reclassificação via API") e re-registre a task definition antes do primeiro teste
   real — sem isso a execução falha ao tentar chamar `reclassificador_predicao_bbv001`.

## Validação já feita (offline)

Rodei o `main()` simulando o wrapper (inputs como arquivos abertos), com os dados
reais:
- **Run 2** (com Base Reclassificada): `base_final` 6,4 MB, `base_reclassificador`
  90 KB, auditoria com 6 tipos; totais conferem (Match conta×classe 45.235 /
  321.914.823,95 · Arbitrado 39.264 / 113.126.253,49 · Match classe OM 17.989 /
  −14.744.162,31 · Cobrança 1.875 / 25.598.905,78 · Reclassificador 1.805 /
  6.943.220,80 · Consultorias 20 / 1.266.652,07).
- **Run 1** (sem Base Reclassificada): run_mode 1, 5 tipos, base do reclassificador
  gerada.

Falta confirmar em ambiente real só o que depende da infra: o contrato exato de
arquivo aberto que o `gradus-platform` passa ao `main()`, o roteamento do
`generic_tool` para a task ECS, e a chamada real via API a
`reclassificador_predicao_bbv001` (`engine/reclassifier_bridge.py`) — todos se
confirmam no primeiro teste em dev, com `PPR_API_TOKEN`/`PPR_API_BASE_URL`
configurados.
