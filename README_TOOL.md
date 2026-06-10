# BBV001 — Ferramenta tradicional do PPR

Implementada no padrão do template `analytics-standard-template` (dummy_repo):
um repositório que o GitHub Actions builda numa imagem ECR e implanta como task
**ECS Fargate**. O PPR aciona via `tipo_execucao=ecs` → POST em `/dev/generic_tool`
com `toolname=code`; o `gradus-platform` (`ecs_handler`) baixa os inputs do S3 já
abertos, chama `main()`, sobe os outputs e faz o callback. **Não escrevemos S3 nem
callback** — só o `main()`.

O engine BBV001 fica em `engine/` e **não é alterado** (paridade com o Alteryx).

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
| `base_reclassificada` | Base Reclassificada (Run 2) | **Opcional** |
| `estrutura_entidades_cc` | Estrutura de Entidades e Centros de Custo | **Opcional** |

> O nome do arquivo enviado não importa — o `main()` renomeia para o nome
> canônico que o engine espera. As **abas** dos Excel precisam ser as padrão
> (Base / Planilha1 / Base+Unico CV / Estrutura de contas / Sheet1 /
> "Estrutura completa de Entidades").
>
> ⚠️ O cadastro `estrutura_entidades_cc` deve ser enviado em **.xlsx** — o
> arquivo original vem em .xlsb com fórmulas que o leitor não decodifica de
> forma confiável. Sem esse input, a aba "Centros de Custo" do relatório sai vazia.

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
5. No PPR, cadastre a `Ferramenta` + Inputs/Outputs acima, libere para os usuários
   e teste pela própria interface (upload dos 4 arquivos → executar).

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
arquivo aberto que o `gradus-platform` passa ao `main()` e o roteamento do
`generic_tool` para a task ECS — ambos se confirmam no primeiro teste em dev.
