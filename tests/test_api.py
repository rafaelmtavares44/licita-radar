"""A API do painel, exercitada inteira sem Postgres, sem grafo, sem rede."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any

import httpx
import pytest

from licita_radar.api.app import criar_app, obter_contexto
from licita_radar.api.esquemas import chave, numero_de
from licita_radar.api.servico import Contexto
from licita_radar.config.perfil import Perfil

NUMERO = "10825373000155-1-000157/2026"
CHAVE = "10825373000155-1-000157~2026"


class AvaliacoesFalsas:
    def __init__(self, linhas: list[dict[str, Any]] | None = None) -> None:
        self.linhas = linhas if linhas is not None else [_linha()]

    async def ranking(self, *, apenas_abertas: bool = False, **_: Any) -> list[dict[str, Any]]:
        if not apenas_abertas:
            return self.linhas
        hoje = date.today()
        return [
            linha
            for linha in self.linhas
            if linha.get("encerramento_proposta") is None or linha["encerramento_proposta"] >= hoje
        ]

    async def resumo(self, **_: Any) -> dict[str, int]:
        return {"candidata": 3, "abaixo_limiar": 40, "descartada": 7}


class ExecucoesFalsas:
    def __init__(self, quando: datetime | None = None) -> None:
        self.quando = quando

    async def ultima(self) -> dict[str, Any] | None:
        if self.quando is None:
            return None
        return {"concluida_em": self.quando, "total_novas": 12, "total_vistas": 300}


class AnalisesFalsas:
    def __init__(self) -> None:
        self.gravadas: dict[str, dict[str, Any]] = {}

    async def buscar(self, numero: str) -> Any:
        return None

    async def salvar(self, numero: str, *, analise: dict[str, Any], documentos: Any) -> None:
        self.gravadas[numero] = analise


class GrafoFalso:
    """Imita o suficiente do LangGraph: estado por thread e retomada."""

    def __init__(self, estado: dict[str, Any] | None = None, proximo: tuple[str, ...] = ()) -> None:
        self.estado = estado or {}
        self.proximo = proximo
        self.retomadas: list[tuple[str, bool]] = []
        self.demora = 0.0

    async def aget_state(self, config: dict[str, Any]) -> Any:
        class Instantaneo:
            values = self.estado
            next = self.proximo

        return Instantaneo()

    async def ainvoke(self, comando: Any, config: dict[str, Any]) -> dict[str, Any]:
        numero = config["configurable"]["thread_id"]
        aprovou = "aprovar" in str(getattr(comando, "resume", comando))
        if self.demora:
            await asyncio.sleep(self.demora)
        self.retomadas.append((numero, aprovou))
        self.estado = {
            **self.estado,
            "situacao": "notificada" if aprovou else "rejeitada",
            "decisao_humana": "aprovada" if aprovou else "rejeitada",
            "analise": {"resumo": "resumo do edital", "afirmacoes": [], "confiabilidade": 1.0}
            if aprovou
            else None,
            "documentos": [{"titulo": "Edital.pdf"}] if aprovou else [],
        }
        self.proximo = ()
        return self.estado


def _linha(**extra: Any) -> dict[str, Any]:
    base = {
        "numero_controle_pncp": NUMERO,
        "objeto": "[Portal] - DISPENSA - Contratação de desenvolvimento de sistemas",
        "orgao_nome": "INSTITUTO FEDERAL DE ALAGOAS",
        "uf": "AL",
        "municipio": "Maceió",
        "valor_estimado": 65001.91,
        "encerramento_proposta": date(2026, 9, 20),
        "score_final": 0.41,
        "score_lexical": 0.2,
        "score_semantico": 0.5,
        "veredito": "candidata",
        "palavras_encontradas": ["desenvolvimento de software"],
        "motivo": None,
    }
    return {**base, **extra}


@pytest.fixture
def contexto(perfil: Perfil) -> Contexto:
    return Contexto(
        banco=None,
        grafo=GrafoFalso(estado={"justificativa": "combina com o perfil"}, proximo=("revisar",)),
        perfil=perfil,
        avaliacoes=AvaliacoesFalsas(),
        analises=AnalisesFalsas(),
        execucoes=ExecucoesFalsas(datetime(2026, 9, 9, 7, 30)),
    )


@pytest.fixture
def cliente(contexto: Contexto) -> Any:
    """Um app novo por teste, sem banco: o contexto entra por injeção."""
    app = criar_app(servir_painel=False)
    app.dependency_overrides[obter_contexto] = lambda: contexto
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://teste")


# ------------------------------------------------------------------ chaves


def test_a_barra_do_numero_de_controle_vira_til() -> None:
    """`%2F` é decodificado antes do roteamento e parte a rota em duas."""
    assert chave(NUMERO) == CHAVE
    assert "/" not in chave(NUMERO)
    assert numero_de(chave(NUMERO)) == NUMERO


# ---------------------------------------------------------------- leituras


@pytest.mark.asyncio
async def test_resumo_traz_o_funil(cliente: Any) -> None:
    async with cliente as http:
        resposta = await http.get("/api/resumo")

    assert resposta.status_code == 200
    corpo = resposta.json()
    # `candidatas` conta o que ainda dá tempo de disputar, não o histórico
    assert corpo["candidatas"] == 1
    assert corpo["por_veredito"]["abaixo_limiar"] == 40


@pytest.mark.asyncio
async def test_resumo_diz_quando_foi_a_ultima_coleta(cliente: Any) -> None:
    """Sem isso, tela com dado de quatro dias parece tela atualizada."""
    async with cliente as http:
        corpo = (await http.get("/api/resumo")).json()

    assert corpo["ultima_coleta"].startswith("2026-09-09")
    assert corpo["novas_na_ultima"] == 12


@pytest.mark.asyncio
async def test_sem_nenhuma_coleta_o_campo_vem_vazio(contexto: Contexto) -> None:
    contexto.execucoes = ExecucoesFalsas(None)
    app = criar_app(servir_painel=False)
    app.dependency_overrides[obter_contexto] = lambda: contexto

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://teste"
    ) as http:
        corpo = (await http.get("/api/resumo")).json()

    assert corpo["ultima_coleta"] is None


class TestPrazoVencido:
    """Licitação encerrada é ruído: ocupa a lista e não há o que fazer."""

    @pytest.fixture
    def com_vencida(self, contexto: Contexto) -> Contexto:
        ontem = date.today() - timedelta(days=1)
        contexto.avaliacoes = AvaliacoesFalsas(
            [
                _linha(),
                _linha(numero_controle_pncp="x-1-9/2026", encerramento_proposta=ontem),
            ]
        )
        return contexto

    def _cliente(self, contexto: Contexto) -> Any:
        app = criar_app(servir_painel=False)
        app.dependency_overrides[obter_contexto] = lambda: contexto
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://teste")

    @pytest.mark.asyncio
    async def test_por_padrao_some_da_lista(self, com_vencida: Contexto) -> None:
        async with self._cliente(com_vencida) as http:
            itens = (await http.get("/api/candidatas")).json()

        assert [i["numero_controle"] for i in itens] == [NUMERO]

    @pytest.mark.asyncio
    async def test_mas_continua_alcancavel_de_propósito(self, com_vencida: Contexto) -> None:
        """Sumir com o dado sem oferecer como vê-lo seria esconder, não filtrar."""
        async with self._cliente(com_vencida) as http:
            itens = (await http.get("/api/candidatas?encerradas=true")).json()

        assert len(itens) == 2

    @pytest.mark.asyncio
    async def test_o_resumo_conta_quantas_foram_escondidas(self, com_vencida: Contexto) -> None:
        async with self._cliente(com_vencida) as http:
            corpo = (await http.get("/api/resumo")).json()

        assert corpo["encerradas_escondidas"] == 1


@pytest.mark.asyncio
async def test_lista_limpa_o_objeto_e_monta_a_url_do_pncp(cliente: Any) -> None:
    async with cliente as http:
        resposta = await http.get("/api/candidatas")

    (item,) = resposta.json()
    assert item["chave"] == CHAVE
    # a casca burocrática do objeto não vai para a tela
    assert not item["objeto"].startswith("[Portal]")
    assert item["url_pncp"] == "https://pncp.gov.br/app/editais/10825373000155/2026/157"
    assert item["aguardando_decisao"] is True
    assert item["justificativa"] == "combina com o perfil"


@pytest.mark.asyncio
async def test_detalhe_de_numero_inexistente_e_404(cliente: Any) -> None:
    async with cliente as http:
        resposta = await http.get("/api/candidatas/00000000000000-1-000001~2026")

    assert resposta.status_code == 404


# ----------------------------------------------------------------- decisão


@pytest.mark.asyncio
async def test_rejeitar_responde_pronta_na_hora(cliente: Any, contexto: Contexto) -> None:
    async with cliente as http:
        resposta = await http.post(
            f"/api/candidatas/{CHAVE}/decisao",
            json={"decisao": "rejeitar", "comentario": "exige hardware"},
        )

    assert resposta.status_code == 202
    assert resposta.json()["estado"] == "pronta"
    assert contexto.grafo.retomadas == [(NUMERO, False)]


@pytest.mark.asyncio
async def test_aprovar_volta_na_hora_e_analisa_depois(cliente: Any, contexto: Contexto) -> None:
    """O botão não pode parecer travado por um minuto."""
    contexto.grafo.demora = 0.05

    async with cliente as http:
        resposta = await http.post(f"/api/candidatas/{CHAVE}/decisao", json={"decisao": "aprovar"})
        assert resposta.status_code == 202
        assert resposta.json()["estado"] == "analisando"

        # enquanto o edital é lido, a tela pode perguntar como está
        andamento = await http.get(f"/api/candidatas/{CHAVE}/decisao")
        assert andamento.json()["estado"] == "analisando"

        await asyncio.sleep(0.2)
        final = await http.get(f"/api/candidatas/{CHAVE}/decisao")

    assert final.json()["estado"] == "pronta"
    assert contexto.grafo.retomadas == [(NUMERO, True)]
    assert NUMERO in contexto.analises.gravadas


@pytest.mark.asyncio
async def test_dois_cliques_no_aprovar_nao_analisam_duas_vezes(
    cliente: Any, contexto: Contexto
) -> None:
    contexto.grafo.demora = 0.05

    async with cliente as http:
        await http.post(f"/api/candidatas/{CHAVE}/decisao", json={"decisao": "aprovar"})
        await http.post(f"/api/candidatas/{CHAVE}/decisao", json={"decisao": "aprovar"})
        await asyncio.sleep(0.2)

    assert len(contexto.grafo.retomadas) == 1


@pytest.mark.asyncio
async def test_falha_na_analise_vira_estado_de_erro_e_nao_500(
    cliente: Any, contexto: Contexto
) -> None:
    async def explodir(*_: Any, **__: Any) -> None:
        raise RuntimeError("o PNCP caiu")

    contexto.grafo.ainvoke = explodir  # type: ignore[method-assign]

    async with cliente as http:
        resposta = await http.post(f"/api/candidatas/{CHAVE}/decisao", json={"decisao": "aprovar"})
        assert resposta.status_code == 202
        await asyncio.sleep(0.1)
        estado = await http.get(f"/api/candidatas/{CHAVE}/decisao")

    assert estado.json()["estado"] == "erro"
    assert "PNCP" in estado.json()["mensagem"]


@pytest.mark.asyncio
async def test_decisao_invalida_e_recusada_pelo_esquema(cliente: Any) -> None:
    async with cliente as http:
        resposta = await http.post(f"/api/candidatas/{CHAVE}/decisao", json={"decisao": "talvez"})

    assert resposta.status_code == 422


class TestMensagemNaoEMarcacao:
    """Rich come `[web]` achando que é estilo — e some com o extra a instalar.

    O erro apareceu na mensagem que ensina a instalar o painel: ela
    imprimiu `pip install -e "."`, um comando que não resolve nada. Toda
    mensagem que vem de dado (exceção, dica do doctor) precisa ser
    escapada antes de encontrar a marcação.
    """

    def test_extra_entre_colchetes_sobrevive_a_impressao(self) -> None:
        from rich.console import Console
        from rich.markup import escape

        gravador = Console(record=True, width=200)
        gravador.print(escape('instale com: pip install -e ".[web]"'))

        assert ".[web]" in gravador.export_text()

    def test_sem_escapar_o_extra_desapareceria(self) -> None:
        """A prova de que o cuidado é necessário, não decorativo."""
        from rich.console import Console

        gravador = Console(record=True, width=200)
        gravador.print('instale com: pip install -e ".[web]"')

        assert "[web]" not in gravador.export_text()


class TestQuandoOPainelNaoFoiConstruido:
    """`{"detail":"Not Found"}` na raiz é verdade e não ajuda ninguém.

    Quem acabou de subir o servidor pela primeira vez não sabe que a tela
    é construída à parte. A raiz explica; o resto continua 404 de verdade.
    """

    @pytest.mark.asyncio
    async def test_a_raiz_ensina_a_construir(self, contexto: Contexto) -> None:
        app = criar_app(servir_painel=False)
        app.dependency_overrides[obter_contexto] = lambda: contexto

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://teste"
        ) as http:
            resposta = await http.get("/")

        assert resposta.status_code == 200
        assert "npm run build" in resposta.text
        assert "/docs" in resposta.text

    @pytest.mark.asyncio
    async def test_a_explicacao_nao_engole_os_404_da_api(self, contexto: Contexto) -> None:
        """Uma rota coringa aqui esconderia erro de verdade da API."""
        app = criar_app(servir_painel=False)
        app.dependency_overrides[obter_contexto] = lambda: contexto

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://teste"
        ) as http:
            inexistente = await http.get("/api/nao-existe")
            outra = await http.get("/qualquer/coisa")

        assert inexistente.status_code == 404
        assert outra.status_code == 404
