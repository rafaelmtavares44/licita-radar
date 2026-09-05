"""Os nós do grafo. Cada um faz uma coisa e devolve só o que mudou.

A ordem em que aparecem aqui é a ordem do funil, e a ordem do funil é a
tese do projeto: o que é barato de decidir é decidido antes. Uma licitação
vetada por "toner" nunca vira embedding, e muito menos custa um token.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from langgraph.types import interrupt

from licita_radar.alerta import Canal, CanalDesligado, mensagem_da_analise
from licita_radar.analise.analista import analisar_edital
from licita_radar.analise.extracao import ler_documentos
from licita_radar.config.perfil import Perfil
from licita_radar.config.settings import Settings, get_settings
from licita_radar.graph.state import EditalState
from licita_radar.ingest.documentos import baixar_edital
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
    settings: Settings = field(default_factory=get_settings)
    canal: Canal = field(default_factory=CanalDesligado)


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


# ------------------------------------------------------------------ humano


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


# --------------------------------------------------------------- camada 4


async def analisar(estado: EditalState, deps: Dependencias) -> EditalState:
    """Baixa o edital, lê e resume — só para o que a pessoa aprovou.

    Este é o nó mais caro do grafo, e por isso é o último. Ele fica depois
    do `revisar` de propósito: analisar um edital de 80 páginas de uma
    licitação que a pessoa vai descartar em dois segundos é gastar dinheiro
    para produzir nada. O funil que começou em palavra-chave termina aqui,
    e cada camada só deixa passar o que a próxima merece receber.

    Falhar aqui não pode custar o alerta. Se o PNCP estiver fora do ar, se
    o edital for um escaneado sem OCR ou se o modelo devolver bobagem, a
    licitação continua aprovada e notificada — apenas sem o resumo.
    """
    numero = estado.get("numero_controle", "")

    try:
        baixados = await baixar_edital(
            numero,
            maximo=deps.settings.analise_max_documentos,
            settings=deps.settings,
        )
    except Exception as erro:
        logger.warning("não foi possível baixar o edital de %s: %s", numero, erro)
        return EditalState(
            analise={"alertas": [f"não foi possível baixar o edital: {erro}"]},
            trilha=["analisar:sem_download"],
        )

    if not baixados:
        return EditalState(
            documentos=[],
            analise={"alertas": ["esta contratação não tem anexos legíveis publicados"]},
            trilha=["analisar:sem_anexo"],
        )

    leitura = ler_documentos([Path(b.caminho) for b in baixados])
    documentos = [b.como_dict() for b in baixados]

    if leitura.escaneado or not leitura.texto:
        return EditalState(
            documentos=documentos,
            analise={"alertas": [leitura.diagnostico]},
            trilha=["analisar:sem_texto"],
        )

    analise = await analisar_edital(
        llm=deps.llm,
        objeto=limpar_objeto(estado.get("objeto", "")),
        texto_edital=leitura.texto,
        orcamento_caracteres=deps.settings.analise_orcamento_caracteres,
    )

    logger.info(
        "%s: %d afirmações, %.0f%% confirmadas, %d tokens",
        numero,
        len(analise.afirmacoes),
        analise.confiabilidade * 100,
        analise.tokens,
    )

    # O texto do edital NÃO entra no estado. O LangGraph grava um
    # checkpoint a cada transição de nó: 400 KB de edital viram alguns
    # megabytes no banco por contratação, e nada nos nós seguintes precisa
    # do texto — as citações já vêm dentro da análise, e o arquivo continua
    # em disco para quem quiser conferir.
    return EditalState(
        documentos=documentos,
        analise=analise.como_dict(),
        tokens_gastos=estado.get("tokens_gastos", 0) + analise.tokens,
        trilha=["analisar"],
    )


# ------------------------------------------------------------------- fim


async def notificar(estado: EditalState, deps: Dependencias) -> EditalState:
    """O alerta com o resumo do edital, para quem aprovou.

    Falhar aqui não desfaz nada: a licitação foi aprovada, o edital foi
    lido e a análise está gravada. Um alerta que não sai é um aborrecimento;
    uma execução que morre no último nó jogaria fora o trabalho todo.
    """
    numero = estado.get("numero_controle", "")
    analise = estado.get("analise") or {}

    if not deps.canal.ativo:
        logger.info("ALERTA %s — %s", numero, estado.get("justificativa"))
        return EditalState(situacao="notificada", trilha=["notificar:log"])

    texto = mensagem_da_analise(
        analise,
        objeto=limpar_objeto(estado.get("objeto", "")),
        numero_controle=numero,
    )
    try:
        entregue = await deps.canal.enviar(texto)
    except Exception as erro:  # canal quebrado não cancela a aprovação
        logger.warning("o alerta de %s não saiu: %s", numero, erro)
        entregue = False

    return EditalState(
        situacao="notificada",
        trilha=["notificar" if entregue else "notificar:falhou"],
    )


def arquivar(estado: EditalState) -> EditalState:
    """Fim de linha, com o motivo registrado.

    Descarte precisa deixar rastro: "por que este edital não me foi
    mostrado?" é uma pergunta que o usuário vai fazer, e ela merece resposta.
    """
    logger.debug(
        "arquivada %s: %s", estado.get("numero_controle"), estado.get("motivo") or "sem motivo"
    )
    return EditalState(trilha=["arquivar"])
