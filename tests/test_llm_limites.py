"""O que o cliente de LLM aprendeu com o PNCP — tarde demais.

Os dois casos deste arquivo vieram de execução real no painel: um 429 da
Groq que matou a análise, e uma resposta JSON cortada pelo teto de tokens
que jogou fora oito mil tokens já pagos.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from licita_radar.llm import (
    _TENTATIVAS,
    CotaDiariaEsgotada,
    LLMCompativelOpenAI,
    como_json,
    cota_diaria,
    espera_pedida,
)

BASE = "https://api.exemplo.test/v1"
ROTA = f"{BASE}/chat/completions"


def _llm() -> LLMCompativelOpenAI:
    return LLMCompativelOpenAI(base_url=BASE, modelo="modelo/teste", api_key="k", timeout_s=5.0)


def _ok(texto: str = "tudo certo") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": texto}}],
            "model": "modelo/teste",
            "usage": {"total_tokens": 10},
        },
    )


class TestLimiteDeRequisicoes:
    """429 não é falha: é o provedor pedindo para diminuir o ritmo."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_espera_e_tenta_de_novo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dormidas: list[float] = []

        async def _fingir_sono(segundos: float) -> None:
            dormidas.append(segundos)

        monkeypatch.setattr("licita_radar.llm.asyncio.sleep", _fingir_sono)
        respx.post(ROTA).mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "3"}, json={"error": {}}),
                _ok("consegui"),
            ]
        )

        resposta = await _llm().responder(sistema="s", usuario="u")

        assert resposta.texto == "consegui"
        assert dormidas == [3.0]

    def test_le_os_segundos_do_cabecalho(self) -> None:
        resposta = httpx.Response(429, headers={"Retry-After": "7"}, json={})
        assert espera_pedida(resposta) == 7.0

    def test_le_os_segundos_da_mensagem_da_groq(self) -> None:
        """A Groq põe o tempo no texto do erro, não só no cabeçalho."""
        resposta = httpx.Response(
            429,
            json={"error": {"message": "Rate limit reached. Please try again in 19.153s"}},
        )
        espera = espera_pedida(resposta)

        assert espera is not None
        assert 20.0 <= espera <= 21.0

    def test_sem_indicacao_nenhuma_devolve_nada(self) -> None:
        assert espera_pedida(httpx.Response(429, json={"error": {}})) is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_desiste_depois_de_insistir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Insistir para sempre seria pior: a licitação nunca sairia da fila."""

        async def _fingir_sono(segundos: float) -> None:
            return None

        monkeypatch.setattr("licita_radar.llm.asyncio.sleep", _fingir_sono)
        rota = respx.post(ROTA).mock(return_value=httpx.Response(429, json={"error": {}}))

        with pytest.raises(httpx.HTTPStatusError):
            await _llm().responder(sistema="s", usuario="u")

        # o número exato é detalhe de ajuste; o que importa é que ele para
        assert rota.call_count == _TENTATIVAS


class TestFormatoJson:
    @pytest.mark.asyncio
    @respx.mock
    async def test_pede_json_ao_provedor(self) -> None:
        rota = respx.post(ROTA).mock(return_value=_ok('{"a":1}'))

        await _llm().responder(sistema="s", usuario="u", formato_json=True)

        assert "response_format" in rota.calls[0].request.content.decode()

    @pytest.mark.asyncio
    @respx.mock
    async def test_provedor_que_recusa_o_formato_ainda_responde(self) -> None:
        """Ollama e LM Studio variam por modelo — perder a análise por um
        campo opcional seria trocar robustez por elegância."""
        rota = respx.post(ROTA).mock(
            side_effect=[httpx.Response(400, json={"error": {}}), _ok('{"a":1}')]
        )

        resposta = await _llm().responder(sistema="s", usuario="u", formato_json=True)

        assert resposta.texto == '{"a":1}'
        assert "response_format" not in rota.calls[1].request.content.decode()


class TestJsonCortado:
    """O teto de tokens corta no meio da lista; o que veio antes vale."""

    def test_aproveita_as_afirmacoes_completas(self) -> None:
        cortado = (
            '{"resumo":"ok","afirmacoes":['
            '{"assunto":"objeto","texto":"a","trecho":"b"},'
            '{"assunto":"prazos","texto":"c'
        )
        dados = como_json(cortado)

        assert dados is not None
        assert dados["resumo"] == "ok"
        assert len(dados["afirmacoes"]) == 1  # type: ignore[arg-type]

    def test_json_inteiro_continua_passando(self) -> None:
        assert como_json('{"resumo":"ok","afirmacoes":[]}') == {"resumo": "ok", "afirmacoes": []}

    def test_texto_que_nao_e_json_continua_sendo_recusado(self) -> None:
        assert como_json("Desculpe, não consegui ler o edital.") is None

    def test_cerca_de_markdown_continua_tolerada(self) -> None:
        assert como_json('```json\n{"resumo":"x"}\n```') == {"resumo": "x"}


@respx.mock
async def test_duas_analises_nao_disputam_a_mesma_cota(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry por chamada não resolve colisão — só sincroniza os colididos.

    Duas análises aprovadas em sequência disparavam juntas, tomavam 429
    juntas, esperavam o mesmo tanto e voltavam juntas. Em fila, a segunda
    sai depois que a primeira terminou, e o 429 não acontece.
    """
    simultaneas = 0
    pico = 0

    def responder(pedido: httpx.Request) -> httpx.Response:
        nonlocal simultaneas, pico
        simultaneas += 1
        pico = max(pico, simultaneas)
        simultaneas -= 1
        return _ok()

    respx.post(ROTA).mock(side_effect=responder)

    async def sem_dormir(_: float) -> None:
        return None

    monkeypatch.setattr("licita_radar.llm.asyncio.sleep", sem_dormir)

    llm = _llm()
    await asyncio.gather(*(llm.responder(sistema="s", usuario=f"u{i}") for i in range(4)))

    assert pico == 1


@respx.mock
async def test_o_castigo_do_429_vale_para_quem_vem_depois(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "Tente em 19s" é informação sobre a conta, não sobre a requisição.

    Sem guardar isso, a chamada seguinte descobre o mesmo limite levando
    outro 429 — pagando duas vezes pela mesma informação.
    """
    esperas: list[float] = []

    async def anotar(segundos: float) -> None:
        esperas.append(segundos)

    monkeypatch.setattr("licita_radar.llm.asyncio.sleep", anotar)

    rota = respx.post(ROTA)
    rota.side_effect = [
        httpx.Response(429, json={"error": {"message": "Please try again in 19.153s"}}),
        _ok(),
        _ok(),
    ]

    llm = _llm()
    await llm.responder(sistema="s", usuario="primeira")
    await llm.responder(sistema="s", usuario="segunda")

    # a espera do 429 e, na chamada seguinte, o resto do castigo
    assert len(esperas) >= 2
    assert esperas[0] == pytest.approx(20.153, abs=0.01)


@respx.mock
async def test_cota_diaria_nao_se_resolve_esperando(monkeypatch: pytest.MonkeyPatch) -> None:
    """Há dois 429 no mesmo código: "devagar" e "volte amanhã".

    Tratados igual, o segundo gastava cinco tentativas de vinte segundos
    e terminava reportando timeout — escondendo o número da cota, que era
    a única informação acionável.
    """
    esperas: list[float] = []

    async def anotar(segundos: float) -> None:
        esperas.append(segundos)

    monkeypatch.setattr("licita_radar.llm.asyncio.sleep", anotar)

    rota = respx.post(ROTA).mock(
        return_value=httpx.Response(
            429,
            json={
                "error": {
                    "message": "Quota exceeded. Please retry in 20.7s.",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                            "violations": [
                                {
                                    "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                                    "quotaDimensions": {"model": "gemini-3.8-flash"},
                                    "quotaValue": "20",
                                }
                            ],
                        }
                    ],
                }
            },
        )
    )

    with pytest.raises(CotaDiariaEsgotada) as capturado:
        await _llm().responder(sistema="s", usuario="u")

    assert rota.call_count == 1  # insistir não ajudaria
    assert not esperas  # e esperar, menos ainda
    assert "20 pedidos por dia" in str(capturado.value)
    assert "gemini-3.8-flash" in str(capturado.value)


def test_erro_em_array_e_lido_igual() -> None:
    """O Gemini responde `[{"error": ...}]` onde a OpenAI responde `{...}`.

    Assumir o dicionário rendeu `AttributeError: 'list' object has no
    attribute 'get'` — o código escrito para explicar a falha falhou
    antes de explicar coisa alguma.
    """
    corpo = {
        "error": {
            "message": "Quota exceeded. Please retry in 20.7s.",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [
                        {
                            "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                            "quotaDimensions": {"model": "gemini-3.8-flash"},
                            "quotaValue": "20",
                        }
                    ],
                }
            ],
        }
    }

    for formato in (corpo, [corpo]):
        resposta = httpx.Response(429, json=formato)
        assert "20 pedidos por dia" in (cota_diaria(resposta) or "")
        assert espera_pedida(resposta) == pytest.approx(21.7, abs=0.01)

    # e nada disso pode explodir com corpo que sequer é JSON
    nao_json = httpx.Response(502, text="<html>bad gateway</html>")
    assert cota_diaria(nao_json) is None
    assert espera_pedida(nao_json) is None


def test_le_o_retry_in_do_google_alem_do_try_again_da_groq() -> None:
    """Duas formas de dizer a mesma coisa. Conhecer só uma custa uma espera cega."""
    google = httpx.Response(429, json=[{"error": {"message": "Please retry in 20.739s."}}])
    groq = httpx.Response(429, json={"error": {"message": "Please try again in 19.153s"}})

    assert espera_pedida(google) == pytest.approx(21.739, abs=0.01)
    assert espera_pedida(groq) == pytest.approx(20.153, abs=0.01)
