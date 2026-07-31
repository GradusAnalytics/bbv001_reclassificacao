"""
controls.py — Controles transversais da v2 (NOVO — não existe no Alteryx nem na v1).

Concentra num único módulo os mecanismos que a v2 adiciona por cima da cascata portada
do Alteryx, para que transforms.py mude o mínimo possível e o diff v1→v2 fique legível:

  - Aviso          : registro estruturado de um evento (etapa, severidade, código,
                     mensagem, registros afetados, valor afetado)
  - BloqueioError  : erro de negócio com mensagem acionável ao usuário final do PPR
                     (ERRO bloqueante — a execução NÃO produz base_final)
  - RunControls    : coletor de avisos + censo de duplicatas início×fim (R8) +
                     termos da equação de conservação (A1)

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
    _censo_m0: Optional[pd.Series] = None          # multiplicidade por Codigo Interno pós-T63/pré-T62
    _conservacao: dict = field(default_factory=dict)  # nome do termo -> (nrows, valor)

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

    # ------------------------------------------------------- censo R8 (início×fim)
    def censo_inicial(self, df: pd.DataFrame) -> None:
        """m0(k): multiplicidade de cada Codigo Interno pós-T63 e ANTES do join T62.

        Duplicata NATIVA (m0>1) é válida — existe na base mensal do cliente e é para
        ela que o Índice ERP sufixa _N. O censo só condena o que os joins FABRICAREM.
        """
        self._censo_m0 = df["Codigo Interno"].value_counts()
        nativas = int((self._censo_m0 > 1).sum())
        if nativas > 0:
            self.add("R8/censo", "INFO", "DUPLICATA_NATIVA",
                     f"{nativas:,} Codigo Interno já entram duplicados na base do mês "
                     f"(válido; Índice ERP recebe sufixo _N).", registros=nativas)

    def censo_final(self, df_union: pd.DataFrame) -> None:
        """m1(k) no union final (pré-RecordID). Chave com m1>m0 = duplicação fabricada
        por join → ERRO bloqueante (decisão do dono 2026-07-08: Valor duplicado não
        pode chegar ao Matrix). Saídas legítimas (exceção T62, colapso T132) só
        REDUZEM multiplicidade — nunca aumentam."""
        if self._censo_m0 is None:
            raise RuntimeError("censo_final() chamado sem censo_inicial() — bug de orquestração.")
        m1 = df_union["Codigo Interno"].value_counts()
        m0 = self._censo_m0.reindex(m1.index).fillna(0)
        violadas = m1[m1 > m0]
        if len(violadas) > 0:
            extras = int((m1[violadas.index] - m0[violadas.index]).sum())
            mask = df_union["Codigo Interno"].isin(violadas.index)
            valor = float(df_union.loc[mask, "Valor"].sum())
            tipos = sorted(df_union.loc[mask, "Tipo"].dropna().unique().tolist())
            exemplos = ", ".join(str(k) for k in list(violadas.index[:10]))
            self.erro(
                "R8/censo", "DUPLICACAO_FABRICADA",
                f"{len(violadas):,} lançamento(s) saíram do tratamento MAIS duplicados do que "
                f"entraram ({extras:,} linha(s) fabricadas por join; R$ {valor:,.2f} envolvidos; "
                f"caminhos: {tipos}). Causa provável: chave duplicada em cadastro "
                f"(Classe de Valor × Conta / Unico CV / De-Para / Estrutura). Corrija o cadastro "
                f"e re-execute. Códigos Internos (até 10): {exemplos}",
                registros=extras, valor=valor)
        self.add("R8/censo", "INFO", "CENSO_OK",
                 "Censo início×fim OK: nenhuma chave saiu mais duplicada do que entrou.",
                 registros=len(m1))

    # -------------------------------------------------- conservação A1 (6 termos)
    def registra_termo(self, nome: str, nrows: int, valor: float) -> None:
        self._conservacao[nome] = (int(nrows), float(valor))

    def verifica_conservacao(self) -> None:
        """base bruta = excluídas T63 + exceções T62 + colapso T132 +
        bloqueio_prefixo_r9 (V2/R9, 2026-07-20) + union final.

        Na v2 não existem os termos 'descartadas T88' (GRUPO não cadastrado = ERRO
        bloqueante, R4) nem 'duplicações de join' (censo R8 bloqueia). Resíduo ≠ 0
        significa perda/criação NÃO RASTREADA → ERRO (nunca deixar passar em silêncio).
        """
        c = self._conservacao
        obrigatorios = ("base_bruta", "excluidas_t63", "excecoes_t62", "colapso_t132",
                        "bloqueio_prefixo_r9", "union_final")
        faltando = [t for t in obrigatorios if t not in c]
        if faltando:
            raise RuntimeError(f"Conservação sem os termos {faltando} — bug de orquestração.")
        res_rows = c["base_bruta"][0] - c["excluidas_t63"][0] - c["excecoes_t62"][0] \
            - c["colapso_t132"][0] - c["bloqueio_prefixo_r9"][0] - c["union_final"][0]
        res_valor = c["base_bruta"][1] - c["excluidas_t63"][1] - c["excecoes_t62"][1] \
            - c["colapso_t132"][1] - c["bloqueio_prefixo_r9"][1] - c["union_final"][1]
        if res_rows != 0 or abs(res_valor) > TOL_VALOR:
            self.erro(
                "A1/conservacao", "CONSERVACAO_VIOLADA",
                f"A conciliação entrada×saída não fechou: resíduo de {res_rows:,} linha(s) e "
                f"R$ {res_valor:,.2f} sem destino rastreado. A base_final NÃO deve ser usada. "
                f"Termos: " + "; ".join(f"{k}={v[0]:,}/R$ {v[1]:,.2f}" for k, v in c.items()),
                registros=abs(res_rows), valor=abs(res_valor))
        self.add("A1/conservacao", "INFO", "CONSERVACAO_OK",
                 "Conciliação entrada×saída fechada (resíduo 0 linhas; "
                 f"R$ {res_valor:,.2f}).", registros=c["union_final"][0], valor=c["union_final"][1])

    # ------------------------------------------------------------------ resumo
    def resumo_log(self) -> str:
        """Bloco de resumo que o main() põe no TOPO do log_execucao."""
        linhas = ["=" * 70, "RESUMO DA EXECUÇÃO (v2) — conciliação e avisos", "=" * 70]
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
