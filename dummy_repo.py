# Exemplo de main file do programa, que vai ser executada pelo django.

# 1. Imports, edite com os imports necessários e atualize requirements.txt de acordo
import numpy as np
import pandas as pd

# 2. Funções que serão usadas
def eco(texto, numero):
    return texto * int(numero)


# 3. Main. Função que será executada pelo Django.

#    3.1 Aqui, edite as variáveis de entrada, colocando de acordo com o código que será registrado no Django
def main(texto=None, numero=None, arquivo=None):

    
#    3.2 Coloque as suas funções ou o código que será executado, usando as variáveis de entrada
#           - É importante notar que se seu programa espera receber um arquivo, ele vai receber o arquivo já aberto, não o caminho para tal
#           - O mesmo se aplica para o output, a resposta esperada é um arquivo do tipo BytesIO
#           - Garanta que seus números e strings estão nos formatos certos, forçando a conversão usando int(), float() e str() se necessário

    ## leitura de arquivo
    # df = pd.io.excel.read_excel(arquivo)

    novo_texto = eco(texto,numero)

#    3.3 Edite o dicionário com os parâmetros de saída do seu programa, para serem registrados pelo Django
#           - Para arquivos, coloque o nome da saída seguida de __nome para setar o nome do arquivo
    
    ## retorno de arquivo
    # output = BytesIO()
    # result_df.to_excel(output, sheet_name='clusters', index=False)
    
    parametros_saida = {
        "texto_longo":novo_texto,
        # "output": output,
        # "output__nome": "arquivo.xlsx"
    }
    
    return parametros_saida