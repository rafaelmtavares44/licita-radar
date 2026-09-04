"""Tabela de domínio das modalidades de contratação do PNCP.

Fonte: Manual das APIs de Consultas do PNCP. A API **exige**
`codigoModalidadeContratacao` em toda consulta de contratações e aceita
apenas um código por chamada — não existe "me dê tudo". Por isso a
ingestão itera sobre os códigos que interessam ao perfil.
"""

from __future__ import annotations

from enum import IntEnum


class Modalidade(IntEnum):
    LEILAO_ELETRONICO = 1
    DIALOGO_COMPETITIVO = 2
    CONCURSO = 3
    CONCORRENCIA_ELETRONICA = 4
    CONCORRENCIA_PRESENCIAL = 5
    PREGAO_ELETRONICO = 6
    PREGAO_PRESENCIAL = 7
    DISPENSA_DE_LICITACAO = 8
    INEXIGIBILIDADE = 9
    MANIFESTACAO_DE_INTERESSE = 10
    PRE_QUALIFICACAO = 11
    CREDENCIAMENTO = 12
    LEILAO_PRESENCIAL = 13

    @property
    def rotulo(self) -> str:
        return _ROTULOS[self]


_ROTULOS: dict[Modalidade, str] = {
    Modalidade.LEILAO_ELETRONICO: "Leilão — Eletrônico",
    Modalidade.DIALOGO_COMPETITIVO: "Diálogo Competitivo",
    Modalidade.CONCURSO: "Concurso",
    Modalidade.CONCORRENCIA_ELETRONICA: "Concorrência — Eletrônica",
    Modalidade.CONCORRENCIA_PRESENCIAL: "Concorrência — Presencial",
    Modalidade.PREGAO_ELETRONICO: "Pregão — Eletrônico",
    Modalidade.PREGAO_PRESENCIAL: "Pregão — Presencial",
    Modalidade.DISPENSA_DE_LICITACAO: "Dispensa de Licitação",
    Modalidade.INEXIGIBILIDADE: "Inexigibilidade",
    Modalidade.MANIFESTACAO_DE_INTERESSE: "Manifestação de Interesse",
    Modalidade.PRE_QUALIFICACAO: "Pré-qualificação",
    Modalidade.CREDENCIAMENTO: "Credenciamento",
    Modalidade.LEILAO_PRESENCIAL: "Leilão — Presencial",
}

#: Padrão razoável para empresas de TI e serviços de software.
#: Um perfil pode sobrescrever isso em `restricoes.modalidades`.
MODALIDADES_TI: tuple[Modalidade, ...] = (
    Modalidade.PREGAO_ELETRONICO,
    Modalidade.DISPENSA_DE_LICITACAO,
    Modalidade.CONCORRENCIA_ELETRONICA,
    Modalidade.INEXIGIBILIDADE,
    Modalidade.CREDENCIAMENTO,
)


def rotular(codigo: int) -> str:
    """Nome legível de um código, sem explodir em código desconhecido.

    A tabela de domínio do PNCP pode ganhar entradas novas; um código que
    não conhecemos vira texto, não exceção — ingestão não é lugar de
    falhar por causa de rótulo.
    """
    try:
        return Modalidade(codigo).rotulo
    except ValueError:
        return f"Modalidade {codigo}"
