# Implementação de novas ferramentas no backend para a PPR

Esse readme descreve objetivamente como implementar um novo repositório para uma nova ferramenta, cobrindo desde a configuração da ferramenta até a implementação no backend da AWS

## Sumário
1. [Clonar repositório existente e customizar para nova ferramenta](#clone)
2. [Implementar a nova ferramenta na AWS](#implement)
3. [Customizando a versão do Python e bibliotecas necessárias](#customize)

## Clonar repositório existente e customizar para nova ferramenta
1. Vá até o Dummy Repo templete para utilizá-lo como base para a construção da nova ferramenta. Link: https://github.com/GradusAnalytics/dummy_repo
2. Clique em usar como templete, conforme imagem abaixo:
<img width="930" alt="image" src="https://github.com/GradusAnalytics/dummy_repo/assets/28466048/203ae21e-acc7-48b9-ba27-2187ff0f6a70" style="display: block; margin: 0 auto;">

3. Clone o repositório em uma pasta local. Como ferramenta sugerida para gerenciamento de repositório, sugerimos o Tortoise Git - ```https://tortoisegit.org/```

<img width="317" alt="image" src="https://github.com/GradusAnalytics/dummy_repo/assets/28466048/85c9b759-809e-4023-bed8-705133861244" style="display: block; margin: 0 auto;">

4. Na pasta local, vá até a subpasta *aux_files* e abra e execute o arquivo *customize_repo.py*
5. Na tela que abrir, insira o nome da nova função e o *stage* (como é um novo repositório, recomenda-se começar com o *stage* dev)

<img width="215" alt="image" src="https://github.com/GradusAnalytics/dummy_repo/assets/28466048/0da32ff0-b5eb-490b-8b9c-2b246724e28a" style="display: block; margin: 0 auto;">

6. Ajuste os itens do repositório para corresponder à nova ferramenta (remova o antigo arquivo dummy_repo.py e garanta que no *nome_ferramenta*.py está adicionado)
7. Dê um *commit* no novo repositório
8. Solicite à equipe de Analytics para adicionar as credenciais AWS aos segredos do novo repositório, para posteriormente o workflow de implementação na AWS funcionar

## Implementar a nova ferramenta na AWS
1. Uma vez que o repositório esteja implementado, vá até o código __*nome_ferramenta.py*__ e customize ele de acordo com o templete fornecido em dummy_repo.py
2. Ajuste a versão do Python e as bibliotecas necessárias para a nova ferramenta (mais detalhes na próxima seção)
3. Após isso, teste localmente e depois faça um novo __*commit*__
4. O workflow de implementação na AWS vai rodar e a nova ferramenta será implementada no *backend*
5. Para garantir que a implementação funcionou, certifique-se de que o *workflow* funcionou, conforme imagem abaixo:

<img width="927" alt="image" src="https://github.com/GradusAnalytics/dummy_repo/assets/28466048/34a0d91a-ecb4-4302-b023-7cffadf68815">

6. Utilizando a parametrização de inputs definida em ```https://github.com/GradusAnalytics/lambda_function```, faça uma chamada API REST com o método POST para o endpoint ```https://ohc07tmq2i.execute-api.us-east-1.amazonaws.com/dev/generic_tool``` para teste da nova ferramenta
7. O retorno será enviado para ```https://only-capable-grizzly.ngrok-free.app/callback/{execution_id}```

## Customizando a versão do Python e bibliotecas necessárias
1. A versão do Python padrão é a ```3.11``` que está definida no arquivo *Dockerfile*. Se necessário, altere a versão do Python que será utilizada nesse arquivo
2. No arquivo *requirements.txt* existem as dependências relacionadas ao código fornecido. Se necessário, altere ou adicione novas linhas, relacionadas às bibliotecas necessárias. Contudo, algumas bibliotecas são imprescindíveis para a execução no ambiente AWS, então __não__ as __remova__. Especificamente:
```
numpy==1.25.1
pandas==2.0.3
boto3==1.34.7
requests==2.31.0
openpyxl==3.1.2
```
3. Para visualizar a versão das bibliotecas que você está usando para desenvolver o código localmente, utilize o comando ```pip freeze``` em um terminal do computador para visualizar todas as bibliotecas instaladas com suas respectivas versões, conforme imagem abaixo:

<img width="674" alt="image" src="https://github.com/GradusAnalytics/dummy_repo/assets/28466048/44d42e45-183b-4e30-ba3b-fb7d13eeac67">

Adicione apenas as bibliotecas necessárias à execução do código, evitando criação de ambientes muito grandes e com muitas instalações sem necessidade
