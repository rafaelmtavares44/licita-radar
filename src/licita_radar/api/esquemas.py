"""O contrato com o painel. Nomes em português, como o resto do projeto."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

#: O `/` do numeroControlePNCP não sobrevive a um caminho de URL: `%2F` é
#: decodificado antes do roteamento e parte a rota em duas. `~` não aparece
#: em número de controle nenhum e não precisa de escape.
_SEPARADOR = "~"


def chave(numero_controle: str) -> str:
    return numero_controle.replace("/", _SEPARADOR)


def numero_de(chave_url: str) -> str:
    return chave_url.replace(_SEPARADOR, "/")


class Saude(BaseModel):
    banco: bool
    llm: str | None
    perfil: str
    versao: str


class ResumoFunil(BaseModel):
    """Quantas contratações caíram em cada estágio do funil."""

    contratacoes: int = 0
    avaliadas: int = 0
    candidatas: int = 0
    analisadas: int = 0
    por_veredito: dict[str, int] = Field(default_factory=dict)


class ItemLista(BaseModel):
    """Uma linha da lista de candidatas."""

    chave: str
    numero_controle: str
    objeto: str
    orgao: str | None = None
    uf: str | None = None
    valor_estimado: float | None = None
    encerramento: str | None = None
    url_pncp: str | None = None
    score: float = 0.0
    score_lexical: float = 0.0
    score_semantico: float = 0.0
    palavras_encontradas: list[str] = Field(default_factory=list)
    justificativa: str | None = None
    #: Onde a contratação está no grafo, do ponto de vista de quem decide.
    situacao: str = "coletada"
    aguardando_decisao: bool = False
    tem_analise: bool = False


class AfirmacaoDTO(BaseModel):
    assunto: str
    texto: str
    trecho: str
    estado: Literal["sustentada", "numero_sem_apoio", "nao_encontrada"] = "nao_encontrada"
    observacao: str = ""
    numeros_sem_apoio: list[str] = Field(default_factory=list)


class AnaliseDTO(BaseModel):
    resumo: str = ""
    afirmacoes: list[AfirmacaoDTO] = Field(default_factory=list)
    alertas: list[str] = Field(default_factory=list)
    confiabilidade: float = 0.0
    modelo: str | None = None
    tokens: int = 0
    caracteres_lidos: int = 0
    documentos: list[dict[str, Any]] = Field(default_factory=list)


class Detalhe(ItemLista):
    analise: AnaliseDTO | None = None
    comentario_humano: str | None = None
    trilha: list[str] = Field(default_factory=list)
    tokens_gastos: int = 0


class PedidoDeDecisao(BaseModel):
    decisao: Literal["aprovar", "rejeitar"]
    comentario: str | None = None


class EstadoDoTrabalho(BaseModel):
    """Como está a análise disparada por uma aprovação.

    `analisando` existe porque a resposta do POST volta antes do trabalho
    terminar: sem isso, a tela não teria como distinguir "ainda lendo o
    edital" de "esta contratação não tem análise".
    """

    chave: str
    estado: Literal["analisando", "pronta", "erro", "desconhecido"]
    mensagem: str | None = None
