"""O fluxo do grafo, exercitado ponta a ponta sem rede nem banco.

O que importa aqui não é cada nó isolado — são as bifurcações: quem é
descartado onde, e se o descarte deixa rastro.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from licita_radar.config.perfil import Perfil
from licita_radar.config.settings import Settings
from licita_radar.graph import Dependencias, compilar, configuracao, estado_inicial
from licita_radar.llm import (
    LLMCompativelOpenAI,
    LLMDesligado,
    Resposta,
    e_de_raciocinio,
    extrair_frase,
    listar_modelos,
)
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


def _deps(perfil: Perfil, llm: Any = None, settings: Settings | None = None) -> Dependencias:
    return Dependencias(
        perfil=perfil,
        motor=MotorSemantico(EncoderFalso()),
        llm=llm or LLMDesligado(),
        settings=settings or Settings(database_url="postgresql://ninguem@localhost:1/inexistente"),
    )


def _entrada(objeto: str, perfil: Perfil, numero: str = "X-1-1/2026") -> Any:
    return estado_inicial(
        numero_controle=numero,
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
        assert final["trilha"][-3:] == ["revisar:aprovada", "analisar:sem_download", "notificar"]

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


class TestAnaliseDoEdital:
    """O nó mais caro do funil, e o único que roda depois de uma pessoa."""

    async def test_descartada_nunca_chega_a_baixar_edital(self, perfil: Perfil) -> None:
        app = compilar(_deps(perfil), checkpointer=InMemorySaver())
        config = configuracao("X-4-1/2026")

        await app.ainvoke(_entrada("AQUISIÇÃO DE TONER", perfil), config=config)
        estado = (await app.aget_state(config)).values

        assert estado["documentos"] == []
        assert estado["analise"] is None
        assert "analisar" not in " ".join(estado["trilha"])

    async def test_rejeitada_tambem_nao_paga_pela_analise(self, perfil: Perfil) -> None:
        """Analisar o que a pessoa acabou de descartar é gastar para não usar."""
        app = compilar(_deps(perfil), checkpointer=InMemorySaver())
        config = configuracao("X-4-2/2026")
        await app.ainvoke(
            _entrada("Desenvolvimento de software e sustentação de sistemas", perfil), config=config
        )

        final = await app.ainvoke(Command(resume={"decisao": "rejeitar"}), config=config)

        assert final.get("analise") is None
        assert final["trilha"][-1] == "arquivar"

    @respx.mock
    async def test_aprovada_baixa_le_e_resume_com_evidencia(
        self, perfil: Perfil, tmp_path: Any
    ) -> None:
        numero = "10825373000155-1-000157/2026"
        rota = (
            "https://pncp.exemplo.test/api/pncp/v1/orgaos/10825373000155/compras/2026/157/arquivos"
        )
        edital = (
            "7. DA HABILITAÇÃO\n7.1. A licitante deverá apresentar atestado de "
            "capacidade técnica emitido por pessoa jurídica."
        )
        respx.get(rota).mock(
            return_value=httpx.Response(
                200, json=[{"sequencialDocumento": 1, "titulo": "Edital.txt"}]
            )
        )
        respx.get(f"{rota}/1").mock(
            return_value=httpx.Response(200, content=edital.encode("utf-8"))
        )

        resposta = json.dumps(
            {
                "resumo": "Serviço de TI compatível com a empresa.",
                "afirmacoes": [
                    {
                        "assunto": "habilitacao",
                        "texto": "Pede atestado de capacidade técnica.",
                        "trecho": "apresentar atestado de capacidade técnica emitido por",
                    },
                    {
                        "assunto": "garantia",
                        "texto": "Exige garantia de 30%.",
                        "trecho": "garantia de execução no percentual de 30% do contrato",
                    },
                ],
            },
            ensure_ascii=False,
        )
        llm = LLMFalso(resposta)
        settings = Settings(
            pncp_integracao_base_url="https://pncp.exemplo.test/api/pncp",
            documentos_dir=tmp_path / "editais",
            database_url="postgresql://ninguem@localhost:1/inexistente",
        )

        app = compilar(_deps(perfil, llm, settings), checkpointer=InMemorySaver())
        config = configuracao(numero)
        await app.ainvoke(
            _entrada("Desenvolvimento de software e sustentação de sistemas", perfil, numero),
            config=config,
        )
        final = await app.ainvoke(Command(resume={"decisao": "aprovar"}), config=config)

        analise = final["analise"]
        assert analise["confiabilidade"] == 0.5
        assert final["documentos"][0]["titulo"] == "Edital.txt"
        assert final["situacao"] == "notificada"
        assert final["trilha"][-2:] == ["analisar", "notificar"]

    @respx.mock
    async def test_texto_do_edital_nao_vai_para_o_checkpoint(
        self, perfil: Perfil, tmp_path: Any
    ) -> None:
        """400 KB por transição de nó viram megabytes por contratação."""
        numero = "10825373000155-1-000158/2026"
        rota = (
            "https://pncp.exemplo.test/api/pncp/v1/orgaos/10825373000155/compras/2026/158/arquivos"
        )
        respx.get(rota).mock(
            return_value=httpx.Response(
                200, json=[{"sequencialDocumento": 1, "titulo": "Edital.txt"}]
            )
        )
        respx.get(f"{rota}/1").mock(
            return_value=httpx.Response(200, content=("cláusula. " * 5000).encode("utf-8"))
        )

        settings = Settings(
            pncp_integracao_base_url="https://pncp.exemplo.test/api/pncp",
            documentos_dir=tmp_path / "editais",
            database_url="postgresql://ninguem@localhost:1/inexistente",
        )
        app = compilar(
            _deps(perfil, LLMFalso('{"resumo": "ok", "afirmacoes": []}'), settings),
            checkpointer=InMemorySaver(),
        )
        config = configuracao(numero)
        await app.ainvoke(
            _entrada("Desenvolvimento de software e sustentação de sistemas", perfil, numero),
            config=config,
        )
        final = await app.ainvoke(Command(resume={"decisao": "aprovar"}), config=config)

        assert final["texto_edital"] is None
        assert final["analise"]["caracteres_lidos"] > 10_000  # foi lido, só não guardado

    @respx.mock
    async def test_pncp_fora_do_ar_nao_cancela_o_alerta(
        self, perfil: Perfil, tmp_path: Any
    ) -> None:
        """A licitação continua aprovada e notificada — só fica sem resumo."""
        numero = "10825373000155-1-000159/2026"
        rota = (
            "https://pncp.exemplo.test/api/pncp/v1/orgaos/10825373000155/compras/2026/159/arquivos"
        )
        respx.get(rota).mock(return_value=httpx.Response(503))

        settings = Settings(
            pncp_integracao_base_url="https://pncp.exemplo.test/api/pncp",
            documentos_dir=tmp_path / "editais",
            database_url="postgresql://ninguem@localhost:1/inexistente",
        )
        app = compilar(_deps(perfil, LLMFalso(), settings), checkpointer=InMemorySaver())
        config = configuracao(numero)
        await app.ainvoke(
            _entrada("Desenvolvimento de software e sustentação de sistemas", perfil, numero),
            config=config,
        )
        final = await app.ainvoke(Command(resume={"decisao": "aprovar"}), config=config)

        assert final["situacao"] == "notificada"
        assert final["analise"]["alertas"]


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


class TestModeloDeRaciocinio:
    """O gpt-oss gastou 500 tokens pensando e devolveu texto vazio."""

    def test_reconhece_pelos_nomes_conhecidos(self) -> None:
        assert e_de_raciocinio("openai/gpt-oss-120b")
        assert e_de_raciocinio("deepseek-r1")
        assert e_de_raciocinio("qwen3-32b-thinking")
        assert not e_de_raciocinio("llama-3.1-8b-instant")

    @respx.mock
    async def test_pede_esforco_baixo_para_sobrar_orcamento(self) -> None:
        respx.post("https://api.exemplo.test/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "model": "openai/gpt-oss-120b",
                    "choices": [{"message": {"content": "Combina com a empresa."}}],
                    "usage": {"total_tokens": 90},
                },
            )
        )
        llm = LLMCompativelOpenAI(
            base_url="https://api.exemplo.test/v1", modelo="openai/gpt-oss-120b", api_key="x"
        )

        await llm.responder(sistema="s", usuario="u")

        corpo = json.loads(respx.calls.last.request.content)
        assert corpo["reasoning_effort"] == "low"
        assert corpo["max_tokens"] >= 500

    @respx.mock
    async def test_content_vazio_cai_no_campo_de_raciocinio(self) -> None:
        respx.post("https://api.exemplo.test/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "model": "openai/gpt-oss-120b",
                    "choices": [
                        {"message": {"content": "", "reasoning": "A empresa faz software."}}
                    ],
                    "usage": {"total_tokens": 500},
                },
            )
        )
        llm = LLMCompativelOpenAI(
            base_url="https://api.exemplo.test/v1", modelo="openai/gpt-oss-120b", api_key="x"
        )

        resposta = await llm.responder(sistema="s", usuario="u")

        assert "software" in resposta.texto

    async def test_resposta_vazia_vira_heuristica_e_nao_silencio(self, perfil: Perfil) -> None:
        """Alerta sem justificativa parece defeito da licitação, não do modelo."""

        class LLMMudo(LLMFalso):
            async def responder(self, **_: Any) -> Resposta:
                self.chamadas += 1
                return Resposta(texto="", modelo="mudo", tokens=500)

        app = compilar(_deps(perfil, LLMMudo()), checkpointer=InMemorySaver())
        config = configuracao("X-5-1/2026")

        await app.ainvoke(
            _entrada("Desenvolvimento de software e sustentação de sistemas", perfil), config=config
        )
        estado = (await app.aget_state(config)).values

        assert estado["justificativa"]
        assert "similaridade" in estado["justificativa"]
        assert estado["tokens_gastos"] == 500  # o gasto é registrado mesmo assim
        assert estado["trilha"][-1] == "justificar:vazia"
