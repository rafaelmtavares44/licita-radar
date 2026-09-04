"""O freio que aprende com o 429.

Escrito depois de uma varredura nacional real ser cortada na página 16: o
manual do PNCP diz que não há limite de requisições, e há.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from licita_radar.config.settings import Settings
from licita_radar.ingest.freio import Freio, ler_retry_after
from licita_radar.ingest.pncp_client import PNCPClient

ROTA = "https://pncp.exemplo.test/api/consulta/v1/contratacoes/proposta"


class TestLerRetryAfter:
    def test_segundos(self) -> None:
        assert ler_retry_after("30") == 30.0

    def test_ausente_ou_invalido(self) -> None:
        assert ler_retry_after(None) is None
        assert ler_retry_after("") is None
        assert ler_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None

    def test_valor_absurdo_e_limitado(self) -> None:
        """Não vamos dormir uma hora porque o servidor pediu."""
        assert ler_retry_after("99999") == 120.0


class TestFreio:
    def test_dobra_o_intervalo_a_cada_bloqueio(self) -> None:
        freio = Freio(intervalo_inicial_s=0.1, intervalo_maximo_s=1.0)

        freio.registrar_bloqueio()
        assert freio.intervalo_s == pytest.approx(0.2)

        freio.registrar_bloqueio()
        assert freio.intervalo_s == pytest.approx(0.4)

    def test_respeita_o_teto(self) -> None:
        freio = Freio(intervalo_inicial_s=0.5, intervalo_maximo_s=1.0)
        for _ in range(10):
            freio.registrar_bloqueio()

        assert freio.intervalo_s == 1.0

    def test_usa_o_retry_after_do_servidor_quando_ele_manda(self) -> None:
        freio = Freio(intervalo_inicial_s=0.1)

        assert freio.registrar_bloqueio(retry_after_s=7.0) == 7.0

    def test_alivia_depois_de_uma_sequencia_boa(self) -> None:
        freio = Freio(intervalo_inicial_s=0.1, sucessos_para_aliviar=3)
        freio.registrar_bloqueio()
        freio.registrar_bloqueio()
        apertado = freio.intervalo_s

        for _ in range(3):
            freio.registrar_sucesso()

        assert freio.intervalo_s < apertado

    def test_nunca_afrouxa_abaixo_do_inicial(self) -> None:
        freio = Freio(intervalo_inicial_s=0.1, sucessos_para_aliviar=1)
        for _ in range(50):
            freio.registrar_sucesso()

        assert freio.intervalo_s == pytest.approx(0.1)

    async def test_aguardar_espaca_as_chamadas(self) -> None:
        import time

        freio = Freio(intervalo_inicial_s=0.05)
        inicio = time.monotonic()
        for _ in range(3):
            await freio.aguardar()

        assert time.monotonic() - inicio >= 0.09  # duas esperas entre três chamadas


@respx.mock
async def test_cliente_aperta_o_freio_ao_levar_429(
    settings_teste: Settings, pagina2: dict[str, object]
) -> None:
    """O 429 não é falha de rede: é pedido de calma, e tem que ser ouvido."""
    veloz = settings_teste.model_copy(
        update={"pncp_intervalo_min_s": 0.01, "pncp_intervalo_max_s": 0.05}
    )
    freio = Freio(intervalo_inicial_s=0.01, intervalo_maximo_s=0.05)

    rota = respx.get(ROTA)
    rota.side_effect = [
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(200, json=pagina2),
    ]

    from datetime import date

    async with PNCPClient(veloz, freio=freio) as cliente:
        resultado = [
            c
            async for c in cliente.contratacoes_com_proposta_aberta(
                modalidade=8, data_final=date(2026, 9, 30)
            )
        ]

    assert len(resultado) == 1
    assert freio.intervalo_s > 0.01  # apertou depois do 429
