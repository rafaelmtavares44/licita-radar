"""O checkpoint do LangGraph num Postgres de verdade.

    export LR_TEST_DATABASE_URL=postgresql://licita:licita@127.0.0.1:5432/licita_radar
    pytest -m integracao

O que se testa aqui não dá para testar com o InMemorySaver: se o estado
sobrevive ao processo morrer. A retomada é a razão de o projeto usar
LangGraph, e ela só vale se o checkpoint for durável.
"""

from __future__ import annotations

import os
import uuid

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command

from licita_radar.config.perfil import Perfil
from licita_radar.graph import Dependencias, compilar, configuracao, estado_inicial
from licita_radar.llm import LLMDesligado
from licita_radar.matching.semantico import MotorSemantico
from tests.dubles import EncoderFalso

pytestmark = pytest.mark.integracao

URL_TESTE = os.environ.get("LR_TEST_DATABASE_URL")
sem_banco = pytest.mark.skipif(not URL_TESTE, reason="defina LR_TEST_DATABASE_URL para rodar")

OBJETO_BOM = "Desenvolvimento de software e sustentação de sistemas web sob demanda"


def _numero(prefixo: str) -> str:
    """Thread nova a cada execução.

    O checkpoint é durável — é esse o ponto dele — então reusar o mesmo
    thread_id entre rodadas do pytest faz a trilha da execução anterior
    reaparecer. Um id único por execução isola os testes sem precisar
    apagar as tabelas do LangGraph.
    """
    return f"{prefixo}-{uuid.uuid4().hex[:8]}/2026"


def _deps(perfil: Perfil) -> Dependencias:
    return Dependencias(perfil=perfil, motor=MotorSemantico(EncoderFalso()), llm=LLMDesligado())


def _entrada(numero: str, perfil: Perfil, objeto: str = OBJETO_BOM):  # type: ignore[no-untyped-def]
    return estado_inicial(
        numero_controle=numero, objeto=objeto, perfil_id=perfil.id, orgao="MGI", uf="DF"
    )


@sem_banco
async def test_o_estado_sobrevive_a_um_processo_novo(perfil: Perfil) -> None:
    """O ponto do checkpoint durável.

    Dois `async with` separados simulam duas execuções do programa: a
    primeira para no interrupt, a segunda retoma sem saber de nada da
    anterior — só do que está gravado no Postgres.
    """
    numero = _numero("CKPT-1")
    config = configuracao(numero)

    async with AsyncPostgresSaver.from_conn_string(URL_TESTE or "") as saver:
        await saver.setup()
        app = compilar(_deps(perfil), checkpointer=saver)
        saida = await app.ainvoke(_entrada(numero, perfil), config=config)
        assert "__interrupt__" in saida

    # o "processo" acabou aqui; nada em memória atravessa

    async with AsyncPostgresSaver.from_conn_string(URL_TESTE or "") as saver:
        app = compilar(_deps(perfil), checkpointer=saver)

        antes = await app.aget_state(config)
        assert antes.values["situacao"] == "aguardando_revisao"
        assert antes.next  # a thread está parada esperando

        final = await app.ainvoke(Command(resume={"decisao": "aprovar"}), config=config)

    assert final["situacao"] == "notificada"
    assert final["trilha"] == [
        "triar",
        "pontuar",
        "justificar:heuristica",
        "revisar:aprovada",
        "notificar",
    ]


@sem_banco
async def test_threads_diferentes_nao_se_misturam(perfil: Perfil) -> None:
    """thread_id = numeroControlePNCP: cada contratação tem a sua história."""
    async with AsyncPostgresSaver.from_conn_string(URL_TESTE or "") as saver:
        await saver.setup()
        app = compilar(_deps(perfil), checkpointer=saver)

        boa, ruim = _numero("CKPT-2A"), _numero("CKPT-2B")
        await app.ainvoke(_entrada(boa, perfil), config=configuracao(boa))
        await app.ainvoke(_entrada(ruim, perfil, "AQUISIÇÃO DE TONER"), config=configuracao(ruim))

        uma = (await app.aget_state(configuracao(boa))).values
        outra = (await app.aget_state(configuracao(ruim))).values

    assert uma["situacao"] == "aguardando_revisao"
    assert outra["situacao"] == "triada_fora"


@sem_banco
async def test_retomar_e_reexecutar_sao_coisas_diferentes(perfil: Perfil) -> None:
    """Uma nuance do LangGraph que é fácil errar — e cara quando se erra.

    Chamar `ainvoke` numa thread parada **com um estado de entrada** não
    retoma: inicia um novo passo a partir do checkpoint, e os nós rodam de
    novo (a trilha cresce). Para retomar de onde parou, o input tem que ser
    `None` ou um `Command(resume=...)`.

    Confundir os dois faz o nó de LLM ser cobrado duas vezes pela mesma
    contratação — silenciosamente, porque o resultado parece correto.
    """
    numero = _numero("CKPT-3")
    config = configuracao(numero)

    async with AsyncPostgresSaver.from_conn_string(URL_TESTE or "") as saver:
        await saver.setup()
        app = compilar(_deps(perfil), checkpointer=saver)

        await app.ainvoke(_entrada(numero, perfil), config=config)
        primeira = list((await app.aget_state(config)).values["trilha"])

        # com estado de entrada: reexecuta os nós
        await app.ainvoke(_entrada(numero, perfil), config=config)
        reexecutada = list((await app.aget_state(config)).values["trilha"])

        # com Command(resume): retoma, sem repetir o que já rodou
        await app.ainvoke(Command(resume={"decisao": "aprovar"}), config=config)
        retomada = list((await app.aget_state(config)).values["trilha"])

    assert len(reexecutada) > len(primeira)  # rodou de novo
    assert retomada[-2:] == ["revisar:aprovada", "notificar"]
    assert retomada[: len(reexecutada)] == reexecutada  # nada foi refeito
