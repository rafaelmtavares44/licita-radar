"""O fluxo do grafo, exercitado ponta a ponta sem rede nem banco.

O que importa aqui não é cada nó isolado — são as bifurcações: quem é
descartado onde, e se o descarte deixa rastro.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from licita_radar.config.perfil import Perfil
from licita_radar.graph import Dependencias, compilar, configuracao, estado_inicial
from licita_radar.llm import LLMDesligado, Resposta, extrair_frase, listar_modelos
from licita_radar.matching.semantico import MotorSemantico
from tests.dubles import EncoderFalso


class LLMFalso:
    """Devolve sempre a mesma frase, e conta quantas vezes foi chamado."""

    def __init__(
        self, texto: str = "Combina: a empresa desenvolve o tipo de sistema pedido."
    ) -> None:
        self._texto = texto
        self.chamadas = 0

    @property
    def modelo(self) -> str:
        return "dublê/fixo"

    @property
    def ativo(self) -> bool:
        return True

    async def responder(self, *, sistema: str, usuario: str, max_tokens: int = 220) -> Resposta:
        self.chamadas += 1
        return Resposta(texto=self._texto, modelo=self.modelo, tokens=42)


def _deps(perfil: Perfil, llm: Any = None) -> Dependencias:
    return Dependencias(
        perfil=perfil,
        motor=MotorSemantico(EncoderFalso()),
        llm=llm or LLMDesligado(),
    )


def _entrada(objeto: str, perfil: Perfil) -> Any:
    return estado_inicial(
        numero_controle="X-1-1/2026",
        objeto=objeto,
        perfil_id=perfil.id,
        orgao="MINISTÉRIO DA GESTÃO",
        uf="DF",
    )


class TestBifurcacoes:
    async def test_vetada_vai_direto_para_arquivar(self, perfil: Perfil) -> None:
        app = compilar(_deps(perfil), checkpointer=InMemorySaver())

        final = await app.ainvoke(
            _entrada("AQUISIÇÃO DE TONER E IMPRESSORA", perfil),
            config=configuracao("X-1-1/2026"),
        )

        assert final["situacao"] == "triada_fora"
        assert "toner" in (final["motivo"] or "")
        assert final["trilha"] == ["triar:vetada", "arquivar"]
        # nem chegou a pontuar: a camada barata resolveu
        assert "pontuar" not in final["trilha"]

    async def test_abaixo_do_limiar_tambem_arquiva(self, perfil: Perfil) -> None:
        app = compilar(_deps(perfil), checkpointer=InMemorySaver())

        final = await app.ainvoke(
            _entrada("Aquisição de gêneros alimentícios para a merenda", perfil),
            config=configuracao("X-1-2/2026"),
        )

        assert final["situacao"] == "abaixo_limiar"
        assert final["trilha"] == ["triar", "pontuar:abaixo", "arquivar"]

    async def test_candidata_para_na_revisao_humana(self, perfil: Perfil) -> None:
        """O grafo dorme no interrupt e não segue sozinho."""
        app = compilar(_deps(perfil), checkpointer=InMemorySaver())
        config = configuracao("X-1-3/2026")

        resultado = await app.ainvoke(
            _entrada("Desenvolvimento de software e sustentação de sistemas web", perfil),
            config=config,
        )

        assert "__interrupt__" in resultado
        estado = (await app.aget_state(config)).values
        assert estado["situacao"] == "aguardando_revisao"
        assert estado["justificativa"]


class TestPausaHumana:
    async def test_aprovar_retoma_e_notifica(self, perfil: Perfil) -> None:
        app = compilar(_deps(perfil), checkpointer=InMemorySaver())
        config = configuracao("X-2-1/2026")
        await app.ainvoke(
            _entrada("Desenvolvimento de software e sustentação de sistemas", perfil), config=config
        )

        final = await app.ainvoke(Command(resume={"decisao": "aprovar"}), config=config)

        assert final["situacao"] == "notificada"
        assert final["decisao_humana"] == "aprovada"
        assert final["trilha"][-2:] == ["revisar:aprovada", "notificar"]

    async def test_rejeitar_arquiva_com_o_comentario(self, perfil: Perfil) -> None:
        app = compilar(_deps(perfil), checkpointer=InMemorySaver())
        config = configuracao("X-2-2/2026")
        await app.ainvoke(
            _entrada("Desenvolvimento de software e sustentação de sistemas", perfil), config=config
        )

        final = await app.ainvoke(
            Command(resume={"decisao": "rejeitar", "comentario": "exige atestado que não temos"}),
            config=config,
        )

        assert final["situacao"] == "rejeitada"
        assert final["comentario_humano"] == "exige atestado que não temos"
        assert final["trilha"][-1] == "arquivar"

    async def test_retomar_nao_reprocessa_o_que_ja_rodou(self, perfil: Perfil) -> None:
        """O ponto do checkpoint: a segunda metade não paga pela primeira."""
        llm = LLMFalso()
        app = compilar(_deps(perfil, llm), checkpointer=InMemorySaver())
        config = configuracao("X-2-3/2026")

        await app.ainvoke(
            _entrada("Desenvolvimento de software e sustentação de sistemas", perfil), config=config
        )
        assert llm.chamadas == 1

        await app.ainvoke(Command(resume={"decisao": "aprovar"}), config=config)

        assert llm.chamadas == 1  # não chamou de novo ao retomar


class TestJustificativa:
    async def test_sem_llm_a_explicacao_e_heuristica(self, perfil: Perfil) -> None:
        app = compilar(_deps(perfil), checkpointer=InMemorySaver())
        config = configuracao("X-3-1/2026")

        await app.ainvoke(
            _entrada("Desenvolvimento de software e sustentação de sistemas", perfil), config=config
        )
        estado = (await app.aget_state(config)).values

        assert "similaridade" in estado["justificativa"]
        assert estado["tokens_gastos"] == 0
        assert estado["trilha"][-1] == "justificar:heuristica"

    async def test_com_llm_usa_a_frase_e_conta_os_tokens(self, perfil: Perfil) -> None:
        app = compilar(_deps(perfil, LLMFalso()), checkpointer=InMemorySaver())
        config = configuracao("X-3-2/2026")

        await app.ainvoke(
            _entrada("Desenvolvimento de software e sustentação de sistemas", perfil), config=config
        )
        estado = (await app.aget_state(config)).values

        assert estado["justificativa"].startswith("Combina:")
        assert estado["tokens_gastos"] == 42
        assert estado["modelo_usado"] == "dublê/fixo"

    async def test_llm_que_falha_nao_derruba_a_contratacao(self, perfil: Perfil) -> None:
        class LLMQuebrado(LLMFalso):
            async def responder(self, **_: Any) -> Resposta:
                raise RuntimeError("modelo fora do ar")

        app = compilar(_deps(perfil, LLMQuebrado()), checkpointer=InMemorySaver())
        config = configuracao("X-3-3/2026")

        await app.ainvoke(
            _entrada("Desenvolvimento de software e sustentação de sistemas", perfil), config=config
        )
        estado = (await app.aget_state(config)).values

        assert estado["situacao"] == "aguardando_revisao"  # segue para revisão mesmo assim
        assert estado["trilha"][-1] == "justificar:erro"


class TestExtrairFrase:
    @pytest.mark.parametrize(
        ("bruto", "esperado"),
        [
            ('"Combina com a empresa."', "Combina com a empresa."),
            ("Resposta: Não combina.", "Não combina."),
            ("Claro! Combina sim.", "Combina sim."),
            ("Primeira linha.\nSegunda linha.", "Primeira linha."),
        ],
    )
    def test_limpa_o_cacoete_dos_modelos_pequenos(self, bruto: str, esperado: str) -> None:
        assert extrair_frase(bruto) == esperado


class TestEstadoPreveAnaliseDeEdital:
    async def test_campos_da_analise_existem_e_comecam_vazios(self, perfil: Perfil) -> None:
        """Reservados no M3 para o M4 não invalidar os checkpoints gravados."""
        app = compilar(_deps(perfil), checkpointer=InMemorySaver())
        config = configuracao("X-4-1/2026")

        await app.ainvoke(_entrada("AQUISIÇÃO DE TONER", perfil), config=config)
        estado = (await app.aget_state(config)).values

        assert estado["documentos"] == []
        assert estado["texto_edital"] is None
        assert estado["analise"] is None


class TestListarModelos:
    """Provedor aposenta modelo direto — o llama-3.3-70b durou menos de um ano."""

    @respx.mock
    async def test_devolve_os_ids_ordenados(self) -> None:
        respx.get("https://api.exemplo.test/v1/models").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "openai/gpt-oss-120b"},
                        {"id": "llama-3.1-8b-instant"},
                        {"id": "whisper-large-v3"},
                    ]
                },
            )
        )

        modelos = await listar_modelos(base_url="https://api.exemplo.test/v1", api_key="x")

        assert modelos == ["llama-3.1-8b-instant", "openai/gpt-oss-120b", "whisper-large-v3"]

    @respx.mock
    async def test_provedor_fora_do_ar_devolve_lista_vazia(self) -> None:
        """Falhar aqui não pode derrubar o diagnóstico: isto é informação extra."""
        respx.get("https://api.exemplo.test/v1/models").mock(return_value=httpx.Response(500))

        assert await listar_modelos(base_url="https://api.exemplo.test/v1", api_key="x") == []
