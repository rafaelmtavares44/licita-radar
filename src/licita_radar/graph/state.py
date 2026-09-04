"""O estado que atravessa o grafo.

É o contrato entre os nós, e mantê-lo pequeno é o que impede o grafo de
virar um saco de variáveis globais. Cada nó devolve apenas o que mudou; o
LangGraph funde no estado corrente.

Uma execução do grafo = uma contratação. O `thread_id` do checkpoint é o
`numeroControlePNCP`, então reprocessar o mesmo edital retoma de onde parou
em vez de pagar de novo pela avaliação.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

#: Onde a contratação parou. Espelha o `Veredito` do matching e acrescenta
#: os estados que só existem depois que uma pessoa opina.
Situacao = Literal[
    "coletada",
    "triada_fora",
    "abaixo_limiar",
    "aguardando_revisao",
    "aprovada",
    "rejeitada",
    "notificada",
]


class EditalState(TypedDict, total=False):
    """O que se sabe sobre uma contratação enquanto ela atravessa o grafo."""

    # --- identidade: não muda durante a execução ---
    numero_controle: str
    objeto: str
    orgao: str
    uf: str
    esfera: str | None
    valor_estimado: float | None
    encerramento: str | None
    url_pncp: str | None

    # --- entrada de configuração ---
    perfil_id: str

    # --- construído ao longo do caminho ---
    score_lexical: float
    score_semantico: float
    score_final: float
    palavras_encontradas: list[str]
    situacao: Situacao
    motivo: str | None

    #: Escrita pelo LLM. Sem LLM configurado, cai na explicação heurística.
    justificativa: str | None
    modelo_usado: str | None
    tokens_gastos: int

    #: Preenchido quando a pessoa responde ao `interrupt`.
    decisao_humana: str | None
    comentario_humano: str | None

    # --- análise do edital (M4) ---
    # O campo existe desde já, vazio, porque o custo de prevê-lo agora é
    # zero e o de retrofitá-lo depois não é: mudar o formato do estado
    # invalida todo checkpoint gravado. O grafo hoje passa direto por aqui.
    #: Arquivos do edital baixados do PNCP: nome, tipo e caminho local.
    documentos: list[dict[str, Any]]
    #: Texto extraído dos documentos, já limpo.
    texto_edital: str | None
    #: O resumo executivo: objeto real, habilitação, garantia, prazos,
    #: penalidades e riscos — em linguagem de gente.
    analise: dict[str, Any] | None

    # --- diagnóstico acumulado ---
    #: `operator.add` faz deste campo um *reducer*: cada nó devolve só o que
    #: acrescenta e o LangGraph concatena. Saber por que este campo precisa
    #: de reducer e os outros não é a diferença entre ter usado o framework
    #: e ter entendido o framework.
    trilha: Annotated[list[str], operator.add]


def estado_inicial(
    *,
    numero_controle: str,
    objeto: str,
    perfil_id: str,
    orgao: str = "",
    uf: str = "",
    esfera: str | None = None,
    valor_estimado: float | None = None,
    encerramento: str | None = None,
    url_pncp: str | None = None,
) -> EditalState:
    return EditalState(
        numero_controle=numero_controle,
        objeto=objeto,
        orgao=orgao,
        uf=uf,
        esfera=esfera,
        valor_estimado=valor_estimado,
        encerramento=encerramento,
        url_pncp=url_pncp,
        perfil_id=perfil_id,
        score_lexical=0.0,
        score_semantico=0.0,
        score_final=0.0,
        palavras_encontradas=[],
        situacao="coletada",
        motivo=None,
        justificativa=None,
        modelo_usado=None,
        tokens_gastos=0,
        decisao_humana=None,
        comentario_humano=None,
        documentos=[],
        texto_edital=None,
        analise=None,
        trilha=[],
    )


def resumo(estado: EditalState) -> dict[str, Any]:
    """Uma linha legível do estado, para log e para o terminal."""
    return {
        "numero": estado.get("numero_controle"),
        "situacao": estado.get("situacao"),
        "score": round(estado.get("score_final", 0.0), 2),
        "trilha": " → ".join(estado.get("trilha", [])),
        "tokens": estado.get("tokens_gastos", 0),
    }
