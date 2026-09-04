"""Roda o grafo com checkpoint em Postgres, uma contratação por thread."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command

from licita_radar.config.perfil import Perfil
from licita_radar.config.settings import Settings, get_settings
from licita_radar.graph.build import compilar, configuracao
from licita_radar.graph.nodes import Dependencias
from licita_radar.graph.state import EditalState, estado_inicial
from licita_radar.ingest.modelos import Contratacao
from licita_radar.llm import construir_llm
from licita_radar.matching.encoder import FastEmbedEncoder
from licita_radar.matching.semantico import MotorSemantico

logger = logging.getLogger(__name__)


@dataclass
class Execucao:
    numero_controle: str
    situacao: str
    score: float
    justificativa: str | None
    tokens: int
    aguardando: bool


def _para_estado(contratacao: Contratacao, perfil: Perfil) -> EditalState:
    return estado_inicial(
        numero_controle=contratacao.numero_controle_pncp,
        objeto=contratacao.objeto,
        perfil_id=perfil.id,
        orgao=contratacao.orgao_nome or "",
        uf=contratacao.uf or "",
        esfera=contratacao.esfera,
        valor_estimado=float(contratacao.valor_estimado) if contratacao.valor_estimado else None,
        encerramento=(
            contratacao.encerramento_proposta.isoformat()
            if contratacao.encerramento_proposta
            else None
        ),
        url_pncp=contratacao.url_pncp,
    )


@asynccontextmanager
async def abrir_radar(
    perfil: Perfil, settings: Settings | None = None, *, com_semantica: bool = True
) -> AsyncIterator[Any]:
    """Compila o grafo com o checkpointer do Postgres já preparado.

    O `setup()` cria as tabelas do LangGraph na primeira execução. Elas não
    entram nas migrações do projeto de propósito: são schema da biblioteca,
    e é ela quem deve versioná-las.
    """
    s = settings or get_settings()

    motor = MotorSemantico(FastEmbedEncoder())
    llm = construir_llm(
        base_url=s.llm_base_url,
        modelo=s.llm_modelo,
        api_key=s.llm_api_key,
        timeout_s=s.llm_timeout_s,
    )
    deps = Dependencias(perfil=perfil, motor=motor, llm=llm)

    async with AsyncPostgresSaver.from_conn_string(s.database_url) as checkpointer:
        await checkpointer.setup()
        yield compilar(deps, checkpointer=checkpointer)


async def processar(
    app: Any, contratacoes: Sequence[Contratacao], perfil: Perfil
) -> list[Execucao]:
    """Passa cada contratação pelo grafo. Uma falha não derruba as outras."""
    resultados: list[Execucao] = []

    for contratacao in contratacoes:
        config = configuracao(contratacao.numero_controle_pncp)
        try:
            saida = await app.ainvoke(_para_estado(contratacao, perfil), config=config)
        except Exception as erro:
            logger.error("grafo falhou em %s: %s", contratacao.numero_controle_pncp, erro)
            continue

        estado = (await app.aget_state(config)).values
        resultados.append(
            Execucao(
                numero_controle=contratacao.numero_controle_pncp,
                situacao=str(estado.get("situacao", "?")),
                score=float(estado.get("score_final", 0.0)),
                justificativa=estado.get("justificativa"),
                tokens=int(estado.get("tokens_gastos", 0)),
                aguardando="__interrupt__" in saida,
            )
        )

    return resultados


async def responder(
    app: Any, numero_controle: str, *, aprovar: bool, comentario: str | None = None
) -> EditalState:
    """Retoma a thread parada no `interrupt` com a decisão da pessoa."""
    config = configuracao(numero_controle)
    await app.ainvoke(
        Command(resume={"decisao": "aprovar" if aprovar else "rejeitar", "comentario": comentario}),
        config=config,
    )
    estado: EditalState = (await app.aget_state(config)).values
    return estado


async def pendentes(app: Any, numeros: Sequence[str]) -> list[str]:
    """Quais threads estão paradas esperando alguém decidir."""
    parados: list[str] = []
    for numero in numeros:
        estado = await app.aget_state(configuracao(numero))
        if estado.next:  # há próximo nó a executar: a thread está pausada
            parados.append(numero)
    return parados
