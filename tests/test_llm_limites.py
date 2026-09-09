"""O que o cliente de LLM aprendeu com o PNCP — tarde demais.

Os dois casos deste arquivo vieram de execução real no painel: um 429 da
Groq que matou a análise, e uma resposta JSON cortada pelo teto de tokens
que jogou fora oito mil tokens já pagos.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from licita_radar.llm import LLMCompativelOpenAI, como_json, espera_pedida

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

        assert rota.call_count == 4


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
