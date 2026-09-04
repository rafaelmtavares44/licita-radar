"""Os nós do grafo. Cada um faz uma coisa e devolve só o que mudou.

A ordem em que aparecem aqui é a ordem do funil, e a ordem do funil é a
tese do projeto: o que é barato de decidir é decidido antes. Uma licitação
vetada por "toner" nunca vira embedding, e muito menos custa um token.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from langgraph.types import interrupt

from licita_radar.config.perfil import Perfil
from licita_radar.graph.state import EditalState
from licita_radar.llm import (
    LLM,
    SISTEMA_JUSTIFICATIVA,
    extrair_frase,
    prompt_justificativa,
)
from licita_radar.matching.lexical import pontuar_lexicalmente
from licita_radar.matching.limpeza import limpar_objeto
from licita_radar.matching.semantico import MotorSemantico

logger = logging.getLogger(__name__)


@dataclass
class Dependencias:
    """O que os nós precisam do mundo exterior.

    Injetadas na construção do grafo em vez de importadas dentro dos nós:
    é o que permite testar o fluxo inteiro com um encoder de mentira e um
    LLM desligado, sem tocar em rede nem em banco.
    """

    perfil: Perfil
    motor: MotorSemantico
    llm: LLM


# --------------------------------------------------------------- camada 1


def triar(estado: EditalState, deps: Dependencias) -> EditalState:
    """Palavras-chave e veto. Custo zero, e derruba a maior parte."""
    resultado = pontuar_lexicalmente(estado["objeto"], deps.perfil)

    if resultado.vetada:
        return EditalState(
            situacao="triada_fora",
            motivo=f"contém a palavra negativa '{resultado.vetada_por}'",
            score_lexical=0.0,
            trilha=["triar:vetada"],
        )

    return EditalState(
        score_lexical=resultado.score,
        palavras_encontradas=list(resultado.encontradas),
        trilha=["triar"],
    )


# --------------------------------------------------------------- camada 2


def pontuar(estado: EditalState, deps: Dependencias) -> EditalState:
    """Similaridade de significado entre o objeto e o perfil."""
    texto = limpar_objeto(estado["objeto"])
    vetor = deps.motor.encoder.codificar([texto])[0]
    semantico = deps.motor.pontuar(deps.perfil, vetor)

    p = deps.perfil.pontuacao
    final = p.peso_lexical * estado.get("score_lexical", 0.0) + p.peso_semantico * semantico

    if final < p.limiar_alerta:
        return EditalState(
            score_semantico=semantico,
            score_final=final,
            situacao="abaixo_limiar",
            motivo=f"score {final:.2f} < {p.limiar_alerta:.2f}",
            trilha=["pontuar:abaixo"],
        )

    return EditalState(
        score_semantico=semantico,
        score_final=final,
        trilha=["pontuar"],
    )


# --------------------------------------------------------------- camada 3


async def justificar(estado: EditalState, deps: Dependencias) -> EditalState:
    """Uma frase em português dizendo por que esta licitação apareceu.

    Só chega aqui quem passou pelas duas camadas baratas — menos de 1% do
    que entra. É onde o dinheiro é gasto, e por isso é o último.
    """
    if not deps.llm.ativo:
        # Sem LLM configurado, a explicação heurística já responde bem.
        achadas = ", ".join(estado.get("palavras_encontradas", []))
        frase = (
            f"casou com {achadas}" if achadas else "sem palavra-chave, mas semanticamente próxima"
        )
        return EditalState(
            justificativa=f"{frase} · similaridade {estado.get('score_semantico', 0):.2f}",
            situacao="aguardando_revisao",
            trilha=["justificar:heuristica"],
        )

    try:
        resposta = await deps.llm.responder(
            sistema=SISTEMA_JUSTIFICATIVA,
            usuario=prompt_justificativa(
                descricao_empresa=deps.perfil.descricao,
                objeto=limpar_objeto(estado["objeto"]),
                score=estado.get("score_semantico", 0.0),
            ),
        )
    except Exception as erro:  # o modelo caiu, a licitação não pode sumir
        logger.warning("LLM falhou em %s: %s", estado.get("numero_controle"), erro)
        return EditalState(
            justificativa="(não foi possível gerar a justificativa)",
            situacao="aguardando_revisao",
            trilha=["justificar:erro"],
        )

    frase = extrair_frase(resposta.texto)

    if not frase:
        # Aconteceu com o gpt-oss-120b: 500 tokens gastos e content vazio,
        # sem erro. Um alerta sem justificativa é pior que um alerta com a
        # explicação heurística — o silêncio parece defeito da licitação.
        logger.warning(
            "%s: o modelo gastou %d tokens e não devolveu texto",
            estado.get("numero_controle"),
            resposta.tokens,
        )
        achadas = ", ".join(estado.get("palavras_encontradas", []))
        base = f"casou com {achadas}" if achadas else "semanticamente próxima do perfil"
        return EditalState(
            justificativa=f"{base} · similaridade {estado.get('score_semantico', 0):.2f}",
            modelo_usado=resposta.modelo,
            tokens_gastos=resposta.tokens,
            situacao="aguardando_revisao",
            trilha=["justificar:vazia"],
        )

    return EditalState(
        justificativa=frase,
        modelo_usado=resposta.modelo,
        tokens_gastos=resposta.tokens,
        situacao="aguardando_revisao",
        trilha=["justificar:llm"],
    )


# ------------------------------------------------------------ humano e fim


def revisar(estado: EditalState) -> EditalState:
    """Congela a execução até alguém decidir.

    `interrupt()` levanta uma exceção especial que o LangGraph captura: o
    estado vai para o checkpoint e o processo pode terminar. Quando a
    resposta chegar, a execução recomeça exatamente aqui — não do começo.
    """
    decisao = interrupt(
        {
            "pergunta": "Vale a pena disputar?",
            "numero": estado.get("numero_controle"),
            "objeto": limpar_objeto(estado.get("objeto", ""))[:300],
            "orgao": estado.get("orgao"),
            "valor": estado.get("valor_estimado"),
            "encerramento": estado.get("encerramento"),
            "score": round(estado.get("score_final", 0.0), 2),
            "justificativa": estado.get("justificativa"),
            "url": estado.get("url_pncp"),
        }
    )

    if isinstance(decisao, dict):
        veredito = str(decisao.get("decisao", "")).lower()
        comentario = decisao.get("comentario")
    else:
        veredito = str(decisao).lower()
        comentario = None

    aprovou = veredito in {"aprovar", "aprovado", "sim", "s", "yes"}
    return EditalState(
        decisao_humana="aprovada" if aprovou else "rejeitada",
        comentario_humano=comentario,
        situacao="aprovada" if aprovou else "rejeitada",
        trilha=[f"revisar:{'aprovada' if aprovou else 'rejeitada'}"],
    )


def notificar(estado: EditalState) -> EditalState:
    """O alerta em si. O canal entra no M4; aqui fica o registro."""
    logger.info("ALERTA %s — %s", estado.get("numero_controle"), estado.get("justificativa"))
    return EditalState(situacao="notificada", trilha=["notificar"])


def arquivar(estado: EditalState) -> EditalState:
    """Fim de linha, com o motivo registrado.

    Descarte precisa deixar rastro: "por que este edital não me foi
    mostrado?" é uma pergunta que o usuário vai fazer, e ela merece resposta.
    """
    logger.debug(
        "arquivada %s: %s", estado.get("numero_controle"), estado.get("motivo") or "sem motivo"
    )
    return EditalState(trilha=["arquivar"])
