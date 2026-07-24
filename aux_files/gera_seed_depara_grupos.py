# -*- coding: utf-8 -*-
"""Gera o arquivo-seed do novo input obrigatório `depara_grupos` (V2/R4) a partir
dos 11 overrides que eram hardcoded no engine/config.py (Tool 86 do Alteryx).

Uso:  py -X utf8 aux_files/gera_seed_depara_grupos.py
Saída: aux_files/depara_grupos_seed.xlsx (Grupo · Conta OM · Conta Contábil)

Este arquivo é o ponto de partida do cadastro que o usuário mantém — novos GRUPOs
(ex.: REEMBOLSO, que em Abr26 tinha 309 lançamentos sem cadastro) são adicionados
pelo próprio usuário, sem deploy de código."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "engine"))

import pandas as pd  # noqa: E402
from config import MANUAL_GROUP_OVERRIDES  # noqa: E402

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "depara_grupos_seed.xlsx")
df = pd.DataFrame(MANUAL_GROUP_OVERRIDES)[["Grupo", "Conta OM", "Conta Contábil"]]
df.to_excel(out, index=False)
print(f"seed gravado: {out} ({len(df)} grupos)")
