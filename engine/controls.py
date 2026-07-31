"""
controls.py — Controles transversais da v2 (NOVO — não existe no Alteryx nem na v1).

Concentra num único módulo os mecanismos que a v2 adiciona por cima da cascata portada
do Alteryx, para que transforms.py mude o mínimo possível e o diff v1→v2 fique legível:

  - Aviso          : registro estruturado de um evento (etapa, severidade, código,
                     mensagem, registros afetados, valor afetado)
  - BloqueioError  : erro de negócio com mensagem acionável ao usuário final do PPR
                     (ERRO bloqueante — a execução NÃO produz base_final)
  - RunControls    : coletor de avisos + razões por lançamento (V3) + censo de
                     duplicação por ID Lançamento (R8) + termos da equação de
                     conservação (A1)

V3 (2026-07-27): o censo deixou de comparar multiplicidade de `Codigo Interno`
entre início e fim da cascata — como `ID Lançamento` é único por construção na
base bruta, basta detectar ID repetido na união final. Duplicata NATIVA de
`Codigo Interno` virou aviso informativo (`censo_duplicata_nativa`).

V3 (2026-07-28): a conservação (A1) deixou de ser a SOMA de 6 termos por etapa e
passou a ser IDENTIDADE DE CONJUNTO entre a Base de Fechamento e a base final —
como a base final agora é o universo completo (nada é descartado), a verificação
certa é "mesmo conjunto de ID Lançamento, mesma soma de Valor". Os termos por
etapa continuam sendo registrados, mas só alimentam o resumo do log.

Severidades:
  ERRO    — bloqueia a execução (levanta BloqueioError)
  WARNING — a execução segue; o evento é reportado ao usuário
  INFO    — informativo/resumo (ex.: exclusões do Tool 63 por Classe de Valor)

Racional e decisões de design: docs/SPEC_V2.md (R1-R8) e docs/DECISOES.md do projeto.
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from config import CATALOGO_RAZOES, STATUS_PRECEDENCIA, STATUS_OK

logger = logging.getLogger(__name__)

# Tolerância monetária p/ conciliação (acumulação de float em ~100k parcelas)
TOL_VALOR = 0.01


class BloqueioError(RuntimeError):
    """Erro bloqueante com mensagem acionável ao usuário (não é bug do engine).

    A mensagem deve dizer: QUAL input/etapa, O QUE foi encontrado e O QUE o usuário
    deve fazer. O main() propaga a mensagem ao PPR como falha de execução.
    """

    def __init__(self, codigo: str, mensagem: str):
        self.codigo = codigo
        super().__init__(f"[{codigo}] {mensagem}")


@dataclass
class Aviso:
    etapa: str        # ex.: "T63", "T11/De-Para", "R8/censo"
    severidade: str   # ERRO | WARNING | INFO
    codigo: str       # ex.: "CV_EXCLUIDA", "GRUPO_NAO_CADASTRADO"
    mensagem: str
    registros: int = 0
    valor: float = 0.0


@dataclass
class RunControls:
    """Estado dos controles de uma execução. Instanciado 1x por run_pipeline()."""

    avisos: list = field(default_factory=list)
    _conservacao: dict = field(default_factory=dict)  # nome do termo -> (nrows, valor)
    _razoes: dict = field(default_factory=dict)   # V3: ID Lançamento -> [códigos]
    _acoes: dict = field(default_factory=dict)    # V3: (ID, código) -> texto de ação

    # ------------------------------------------------------------------ avisos
    def add(self, etapa: str, severidade: str, codigo: str, mensagem: str,
            registros: int = 0, valor: float = 0.0) -> None:
        aviso = Aviso(etapa, severidade, codigo, mensagem, int(registros), float(valor))
        self.avisos.append(aviso)
        nivel = {"ERRO": logging.ERROR, "WARNING": logging.WARNING}.get(severidade, logging.INFO)
        logger.log(nivel, f"[{etapa}] {codigo}: {mensagem} "
                          f"(registros={registros:,}, valor={valor:,.2f})")

    def erro(self, etapa: str, codigo: str, mensagem: str,
             registros: int = 0, valor: float = 0.0) -> None:
        """Registra o aviso ERRO e levanta o bloqueio (execução para aqui)."""
        self.add(etapa, "ERRO", codigo, mensagem, registros, valor)
        raise BloqueioError(codigo, mensagem)

    def to_rows(self) -> list:
        """Linhas p/ a aba 'Avisos' do excel de exceções e p/ a tabela `warnings` do PPR."""
        return [
            {"etapa": a.etapa, "severidade": a.severidade, "codigo": a.codigo,
             "mensagem": a.mensagem, "registros": a.registros, "valor": a.valor}
            for a in self.avisos
        ]

    # ------------------------------------------------------- razões V3 (por ID)
    def registra_razao(self, ids, codigo: str, acoes: Optional[dict] = None) -> None:
        """V3 — marca lançamentos (por 'ID Lançamento') com um código de razão.

        `ids`   : qualquer iterável de ID Lançamento (Series, list, Index).
        `codigo`: precisa estar em config.CATALOGO_RAZOES.
        `acoes` : dict {ID: texto} opcional — ação POR LANÇAMENTO (ex.: R9, que
                  monta o texto com a Conta OM específica). Sobrepõe o padrão.

        Chamar 2x com o mesmo (ID, código) é idempotente: o código não duplica.
        """
        if codigo not in CATALOGO_RAZOES:
            raise RuntimeError(f"Código de razão '{codigo}' fora do CATALOGO_RAZOES — "
                               f"bug de implementação (v3).")
        for i in pd.Series(list(ids)).dropna().tolist():
            lista = self._razoes.setdefault(int(i), [])
            if codigo not in lista:
                lista.append(codigo)
        if acoes:
            for i, txt in acoes.items():
                self._acoes[(int(i), codigo)] = txt

    def marcacao_por_id(self) -> dict:
        """V3 — ID Lançamento -> (Status, Motivo, Ação Recomendada).

        Status = o mais grave dos códigos da linha, pela ordem de
        config.STATUS_PRECEDENCIA. Motivo = TODOS os códigos, na ordem de
        registro, separados por '; '. Ação = os textos correspondentes, na
        mesma ordem, separados por ' '.
        """
        out = {}
        for id_lanc, codigos in self._razoes.items():
            status = min(
                (CATALOGO_RAZOES[c][0] for c in codigos),
                key=STATUS_PRECEDENCIA.index,
            )
            motivo = "; ".join(codigos)
            acao = " ".join(
                self._acoes.get((id_lanc, c), CATALOGO_RAZOES[c][1]) for c in codigos
            )
            out[id_lanc] = (status, motivo, acao)
        return out

    def codigos_por_id(self) -> dict:
        """V3.1 — ID Lançamento -> [códigos], na ordem de registro.

        Acessor público dos códigos CRUS, sem Status nem texto de ação. Existe porque
        a rede de segurança da reconciliação precisa saber QUAIS códigos a linha
        carrega (para isentar só os de config.CODIGOS_QUE_REMOVEM_LANCAMENTO) — o
        `Status` agregado de marcacao_por_id() não distingue razão que remove
        lançamento de razão informativa.

        Devolve listas novas: mexer no retorno não corrompe o estado interno.
        """
        return {id_lanc: list(codigos) for id_lanc, codigos in self._razoes.items()}

    # ---------------------------------------------------------- censo R8 (V3: por ID)
    def censo_duplicata_nativa(self, df_base: pd.DataFrame) -> None:
        """V3 — informativo: quantos 'Codigo Interno' já entram duplicados na base
        do mês. Duplicata nativa é VÁLIDA (dono, 2026-07-08) — é para ela que o
        Índice ERP sufixa _N. Nunca bloqueia; só dá visibilidade.
        """
        vc = df_base["Codigo Interno"].value_counts()
        nativas = int((vc > 1).sum())
        if nativas > 0:
            self.add("R8/censo", "INFO", "DUPLICATA_NATIVA",
                     f"{nativas:,} Codigo Interno já entram duplicados na base do mês "
                     f"(válido; Índice ERP recebe sufixo _N).", registros=nativas)

    def censo_final(self, df_union: pd.DataFrame) -> None:
        """V3 — 'ID Lançamento' é único por construção na base bruta (1..N), então
        QUALQUER ID que apareça 2x no union é duplicação fabricada por join.
        Substitui a comparação m1>m0 por Codigo Interno da v2: mais forte (pega
        também a duplicação do T62, que entrava no m0) e mais simples (não precisa
        guardar estado entre o início e o fim).

        Duplicação fabricada = ERRO bloqueante (decisão do dono 2026-07-08 mantida
        na v3: Valor duplicado não pode chegar ao Matrix; o ajuste é no input).
        """
        if len(df_union) == 0:
            return
        vc = df_union["ID Lançamento"].value_counts()
        violadas = vc[vc > 1]
        if len(violadas) > 0:
            extras = int((violadas - 1).sum())
            mask = df_union["ID Lançamento"].isin(violadas.index)
            valor = float(df_union.loc[mask, "Valor"].sum())
            tipos = sorted(df_union.loc[mask, "Tipo"].dropna().unique().tolist())
            exemplos = ", ".join(str(k) for k in list(violadas.index[:10]))
            self.erro(
                "R8/censo", "DUPLICACAO_FABRICADA",
                f"{len(violadas):,} lançamento(s) saíram do tratamento duplicados "
                f"({extras:,} linha(s) fabricadas por join; R$ {valor:,.2f} envolvidos; "
                f"caminhos: {tipos}). Causa provável: chave duplicada em cadastro "
                f"(Classe de Valor × Conta / Unico CV / De-Para / Estrutura). Corrija o "
                f"cadastro e re-execute. ID Lançamento (até 10): {exemplos}",
                registros=extras, valor=valor)
        self.add("R8/censo", "INFO", "CENSO_OK",
                 "Censo OK: nenhum lançamento saiu duplicado do tratamento.",
                 registros=len(vc))

    # ------------------------------- conservação A1 (V3: identidade de conjunto)
    def registra_termo(self, nome: str, nrows: int, valor: float) -> None:
        """Termos por etapa — na V3 servem só ao resumo do log (visibilidade de
        quanto passou por cada caminho). A conservação NÃO é mais a soma deles:
        ver verifica_conservacao."""
        self._conservacao[nome] = (int(nrows), float(valor))

    def verifica_conservacao(self, df_base: pd.DataFrame,
                             df_completa: pd.DataFrame) -> None:
        """V3 — a conservação deixa de ser a soma de 6 termos (que dependia de cada
        stage registrar o seu) e passa a ser IDENTIDADE: a saída tem que ser o mesmo
        conjunto de lançamentos que entrou, com a mesma soma de Valor.

        Resíduo ≠ 0 aqui é bug do motor, não input sujo → ERRO bloqueante
        (a base_final não presta).
        """
        ids_in = set(int(i) for i in df_base["ID Lançamento"])
        ids_out = set(int(i) for i in df_completa["ID Lançamento"])
        faltando = sorted(ids_in - ids_out)
        sobrando = sorted(ids_out - ids_in)
        v_in = float(df_base["Valor"].sum())
        v_out = float(df_completa["Valor"].sum())
        dif = v_out - v_in

        if faltando or sobrando or abs(dif) > TOL_VALOR:
            self.erro(
                "A1/conservacao", "CONSERVACAO_VIOLADA",
                f"A base final NÃO é a mesma base que entrou: {len(faltando):,} "
                f"lançamento(s) faltando, {len(sobrando):,} sobrando, diferença de "
                f"R$ {dif:,.2f} no Valor. A base_final NÃO deve ser usada. "
                f"IDs faltando (até 10): {faltando[:10]} · sobrando (até 10): "
                f"{sobrando[:10]}",
                registros=len(faltando) + len(sobrando), valor=abs(dif))

        self.add("A1/conservacao", "INFO", "CONSERVACAO_OK",
                 f"Base final é o universo completo: {len(ids_out):,} lançamentos, "
                 f"R$ {v_out:,.2f} — idêntico à Base de Fechamento.",
                 registros=len(ids_out), valor=v_out)

    # ------------------------------------------------------------------ resumo
    def resumo_log(self) -> str:
        """Bloco de resumo que o main() põe no TOPO do log_execucao."""
        linhas = ["=" * 70, "RESUMO DA EXECUÇÃO (v3) — conciliação e avisos", "=" * 70]
        if self._conservacao:
            for nome, (n, v) in self._conservacao.items():
                linhas.append(f"  {nome:<16} {n:>10,} linhas   R$ {v:>18,.2f}")
        n_err = sum(1 for a in self.avisos if a.severidade == "ERRO")
        n_warn = sum(1 for a in self.avisos if a.severidade == "WARNING")
        n_info = sum(1 for a in self.avisos if a.severidade == "INFO")
        linhas.append(f"  Avisos: {n_err} ERRO · {n_warn} WARNING · {n_info} INFO "
                      f"(detalhe: aba 'Avisos' do excel de exceções / tabela warnings)")
        for a in self.avisos:
            if a.severidade in ("ERRO", "WARNING"):
                linhas.append(f"  [{a.severidade}] {a.etapa} {a.codigo}: {a.mensagem}")
        linhas.append("=" * 70)
        return "\n".join(linhas) + "\n"


# ------------------------------------------------------------------ helpers

def coerce_conta_str(s: pd.Series) -> pd.Series:
    """Fix N1 (aprovado 2026-07-09): garante que colunas de conta viajem como string.

    Códigos de conta têm 25 dígitos; se a coluna chegar numérica do Excel, o
    to_excel grava float64 e perde precisão (8172700000000000010000000 →
    8172699999999999715835904). Espelha a proteção que a v1 já dava só à
    'Conta Contabil' na leitura (io_utils.read_base_fechamento)."""
    def conv(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return v
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        if isinstance(v, (int,)):
            return str(v)
        return v if isinstance(v, str) else str(v)
    return s.map(conv)


def resolve_duplicatas_cadastro(df: pd.DataFrame, chave: list, conteudo: list,
                                nome_input: str, etapa: str,
                                controls: RunControls) -> pd.DataFrame:
    """Padrão R2 para cadastros: dedup se idêntica + WARNING; ERRO se conflitante.

    chave    = colunas que identificam a linha do cadastro (ex.: CONTA CONTÁBIL)
    conteudo = colunas de conteúdo que definem 'idêntica' (ex.: CONTA, PACOTE)
    """
    dup_mask = df.duplicated(subset=chave, keep=False)
    if not dup_mask.any():
        return df
    # conflito = mesma chave com conteúdo divergente
    grupos = df.loc[dup_mask].groupby(chave, dropna=False)[conteudo].nunique()
    conflitantes = grupos[(grupos > 1).any(axis=1)]
    if len(conflitantes) > 0:
        exemplos = "; ".join(str(idx) for idx in list(conflitantes.index[:5]))
        controls.erro(
            etapa, "CADASTRO_CONFLITANTE",
            f"O cadastro '{nome_input}' tem {len(conflitantes):,} chave(s) duplicada(s) com "
            f"conteúdo DIVERGENTE — não é possível saber qual linha vale. Solicite uma "
            f"extração nova do relatório (causa provável: arquivo editado manualmente). "
            f"Chaves (até 5): {exemplos}",
            registros=int(len(conflitantes)))
    antes = len(df)
    df = df.drop_duplicates(subset=chave + conteudo, keep="first")
    removidas = antes - len(df)
    controls.add(
        etapa, "WARNING", "CADASTRO_DUPLICADO",
        f"O cadastro '{nome_input}' tinha {removidas:,} linha(s) duplicada(s) idêntica(s) — "
        f"deduplicadas automaticamente. Solicite uma extração nova do relatório "
        f"(causa provável: arquivo editado manualmente).",
        registros=removidas)
    return df
