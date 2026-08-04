# -*- coding: utf-8 -*-
"""Gera o arquivo-seed do novo input obrigatório `depara_grupos` (V2/R4) a partir
dos 11 overrides que eram hardcoded no engine/config.py (Tool 86 do Alteryx).

Uso:  py -X utf8 aux_files/gera_seed_depara_grupos.py
Saída: aux_files/depara_grupos_seed.xlsx (Grupo · Conta OM · Conta Contábil)

Este arquivo é o ponto de partida do cadastro que o usuário mantém — novos GRUPOs
(ex.: REEMBOLSO, que em Abr26 tinha 309 lançamentos sem cadastro) são adicionados
pelo próprio usuário, sem deploy de código.

V3.1 (2026-07-30): a aba do arquivo gravado tem de se chamar exatamente o que
`read_depara_grupos` espera (`INPUT_FILES["depara_grupos"]["sheet"]`, default
`De-Para Grupos` até a v3.2; **`Grupos Cobrança` desde a v3.3**, 2026-08-03) —
lido daqui, nunca hardcoded, para template e leitor não poderem divergir. Sem
isso, no layout de ARQUIVO ÚNICO (todos os cadastros em abas do mesmo arquivo)
a aba 0 pertenceria a outro cadastro e o fallback do leitor cairia na aba
errada; no arquivo isolado o fallback mascarava o problema (achado da revisão
final da v3.1)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "engine"))

import pandas as pd  # noqa: E402
from config import INPUT_FILES, MANUAL_GROUP_OVERRIDES  # noqa: E402

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "depara_grupos_seed.xlsx")
aba = INPUT_FILES["depara_grupos"]["sheet"]
df = pd.DataFrame(MANUAL_GROUP_OVERRIDES)[["Grupo", "Conta OM", "Conta Contábil"]]
df.to_excel(out, sheet_name=aba, index=False)
print(f"seed gravado: {out} ({len(df)} grupos, aba {aba!r})")
