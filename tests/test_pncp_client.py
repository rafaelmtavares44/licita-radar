"""Testes do cliente HTTP — nenhum toca a rede.

O CI não pode depender do PNCP estar no ar. Tudo aqui roda contra
respostas gravadas, interceptadas pelo respx.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pytest
import respx

from licita_radar.config.settings import Settings
from licita_radar.ingest.pncp_client import Passo, PNCPClient, coletar, descrever

ROTA_PROPOSTA = "https://pncp.exemplo.test/api/consulta/v1/contratacoes/proposta"
ROTA_PUBLICACAO = "https://pncp.exemplo.test/api/consulta/v1/contratacoes/publicacao"


@respx.mock
async def test_pagina_unica_devolve_as_contratacoes(
    settings_teste: Settings, pagina2: dict[str, Any]
) -> None:
    respx.get(ROTA_PROPOSTA).mock(return_value=httpx.Response(200, json=pagina2))

    async with PNCPClient(settings_teste) as cliente:
        resultado = [
            c
            async for c in cliente.contratacoes_com_proposta_aberta(
                modalidade=8, data_final=date(2026, 9, 30), uf="GO"
            )
        ]

    assert len(resultado) == 1
    assert resultado[0].numero_controle_pncp == "02318901000177-1-000013/2026"


@respx.mock
async def test_segue_paginando_ate_paginas_restantes_zerar(
    settings_teste: Settings, pagina1: dict[str, Any], pagina2: dict[str, Any]
) -> None:
    rota = respx.get(ROTA_PROPOSTA)
    rota.side_effect = [
        httpx.Response(200, json=pagina1),
        httpx.Response(200, json=pagina2),
    ]

    async with PNCPClient(settings_teste) as cliente:
        resultado = [
            c
            async for c in cliente.contratacoes_com_proposta_aberta(
                modalidade=6, data_final=date(2026, 9, 30)
            )
        ]

    assert rota.call_count == 2
    assert len(resultado) == 4  # 3 aproveitáveis na página 1 + 1 na página 2
    assert respx.calls[0].request.url.params["pagina"] == "1"
    assert respx.calls[1].request.url.params["pagina"] == "2"


@respx.mock
async def test_monta_os_parametros_obrigatorios_da_api(
    settings_teste: Settings, pagina2: dict[str, Any]
) -> None:
    respx.get(ROTA_PUBLICACAO).mock(return_value=httpx.Response(200, json=pagina2))

    async with PNCPClient(settings_teste) as cliente:
        _ = [
            c
            async for c in cliente.contratacoes_por_publicacao(
                modalidade=6,
                data_inicial=date(2026, 9, 1),
                data_final=date(2026, 9, 30),
                uf="GO",
            )
        ]

    params = respx.calls.last.request.url.params
    assert params["dataInicial"] == "20260901"  # formato AAAAMMDD, exigido pelo PNCP
    assert params["dataFinal"] == "20260930"
    assert params["codigoModalidadeContratacao"] == "6"
    assert params["uf"] == "GO"


@respx.mock
async def test_204_sem_conteudo_nao_e_erro(settings_teste: Settings) -> None:
    respx.get(ROTA_PROPOSTA).mock(return_value=httpx.Response(204))

    async with PNCPClient(settings_teste) as cliente:
        resultado = [
            c
            async for c in cliente.contratacoes_com_proposta_aberta(
                modalidade=6, data_final=date(2026, 9, 30)
            )
        ]

    assert resultado == []


@respx.mock
async def test_retenta_no_503_e_sucede(settings_teste: Settings, pagina2: dict[str, Any]) -> None:
    rota = respx.get(ROTA_PROPOSTA)
    rota.side_effect = [
        httpx.Response(503),
        httpx.Response(200, json=pagina2),
    ]

    async with PNCPClient(settings_teste) as cliente:
        resultado = [
            c
            async for c in cliente.contratacoes_com_proposta_aberta(
                modalidade=8, data_final=date(2026, 9, 30)
            )
        ]

    assert rota.call_count == 2
    assert len(resultado) == 1


@respx.mock
async def test_nao_retenta_no_400(settings_teste: Settings) -> None:
    """Erro nosso não melhora com insistência — falha na primeira."""
    rota = respx.get(ROTA_PROPOSTA).mock(return_value=httpx.Response(400))

    async with PNCPClient(settings_teste) as cliente:
        with pytest.raises(httpx.HTTPStatusError):
            _ = [
                c
                async for c in cliente.contratacoes_com_proposta_aberta(
                    modalidade=99, data_final=date(2026, 9, 30)
                )
            ]

    assert rota.call_count == 1


@respx.mock
async def test_cache_local_evita_a_segunda_chamada(
    settings_teste: Settings, pagina2: dict[str, Any]
) -> None:
    settings = settings_teste.model_copy(update={"pncp_cache_local": True})
    rota = respx.get(ROTA_PROPOSTA).mock(return_value=httpx.Response(200, json=pagina2))

    for _ in range(2):
        async with PNCPClient(settings) as cliente:
            _ = [
                c
                async for c in cliente.contratacoes_com_proposta_aberta(
                    modalidade=8, data_final=date(2026, 9, 30)
                )
            ]

    assert rota.call_count == 1


@respx.mock
async def test_coletar_deduplica_entre_modalidades(
    settings_teste: Settings, pagina2: dict[str, Any]
) -> None:
    """A mesma contratação aparecendo em duas varreduras conta uma vez só."""
    respx.get(ROTA_PROPOSTA).mock(return_value=httpx.Response(200, json=pagina2))

    resultado = await coletar(
        modalidades=[6, 8],
        data_inicial=date(2026, 9, 1),
        data_final=date(2026, 9, 30),
        settings=settings_teste,
    )

    assert len(resultado.contratacoes) == 1


@respx.mock
async def test_modalidade_que_falha_nao_derruba_as_outras(
    settings_teste: Settings, pagina2: dict[str, Any]
) -> None:
    rota = respx.get(ROTA_PROPOSTA)
    rota.side_effect = [
        httpx.Response(400),  # modalidade 6 quebra
        httpx.Response(200, json=pagina2),  # modalidade 8 funciona
    ]

    resultado = await coletar(
        modalidades=[6, 8],
        data_inicial=date(2026, 9, 1),
        data_final=date(2026, 9, 30),
        settings=settings_teste,
    )

    assert len(resultado.contratacoes) == 1  # meia ingestão vale mais que nenhuma
    # ...desde que a metade que faltou apareça em algum lugar
    assert [p.modalidade for p in resultado.puladas] == [6]
    assert not resultado.completa


@respx.mock
async def test_timeout_nao_vira_mensagem_vazia(
    settings_teste: Settings, pagina2: dict[str, Any]
) -> None:
    """`str(httpx.ReadTimeout())` é vazio — e um log vazio manda investigar
    o lugar errado. O log dizia "modalidade 6 falhou e foi pulada:" e
    terminava ali; parecia print cortado, era mensagem inexistente.
    """

    def responder(pedido: httpx.Request) -> httpx.Response:
        # O timeout é retentável, então a modalidade 6 precisa falhar
        # sempre — casar por parâmetro em vez de contar chamadas.
        if pedido.url.params["codigoModalidadeContratacao"] == "6":
            raise httpx.ReadTimeout("")
        return httpx.Response(200, json=pagina2)

    respx.get(ROTA_PROPOSTA).mock(side_effect=responder)

    resultado = await coletar(
        modalidades=[6, 8],
        data_inicial=date(2026, 9, 1),
        data_final=date(2026, 9, 30),
        settings=settings_teste,
    )

    (pulada,) = resultado.puladas
    assert pulada.motivo.strip()
    assert "Pregão eletrônico" in str(pulada)


async def test_descrever_sempre_diz_alguma_coisa() -> None:
    pedido = httpx.Request("GET", "https://pncp.gov.br/api/consulta/v1/contratacoes/proposta")
    casos: list[BaseException] = [
        httpx.ReadTimeout(""),
        httpx.ConnectTimeout(""),
        httpx.PoolTimeout(""),
        httpx.RemoteProtocolError(""),
        httpx.HTTPStatusError("", request=pedido, response=httpx.Response(429, request=pedido)),
        ValueError(""),
    ]
    for erro in casos:
        assert descrever(erro).strip(), f"{type(erro).__name__} descrito com string vazia"


@respx.mock
async def test_andamento_conta_modalidade_e_pagina(
    settings_teste: Settings, pagina1: dict[str, Any], pagina2: dict[str, Any]
) -> None:
    """Nove minutos na mesma frase é indistinguível de travado."""

    def responder(pedido: httpx.Request) -> httpx.Response:
        pagina = pedido.url.params["pagina"]
        return httpx.Response(200, json=pagina1 if pagina == "1" else pagina2)

    respx.get(ROTA_PROPOSTA).mock(side_effect=responder)
    passos: list[Passo] = []

    await coletar(
        modalidades=[6, 8],
        data_inicial=date(2026, 9, 1),
        data_final=date(2026, 9, 30),
        settings=settings_teste,
        andamento=passos.append,
    )

    # cada modalidade: o aviso de entrada, e depois um por página
    assert [(p.indice, p.modalidade, p.pagina) for p in passos[:3]] == [
        (1, 6, 0),
        (1, 6, 1),
        (1, 6, 2),
    ]
    assert passos[1].de_paginas > 0  # a tela precisa do denominador
    assert passos[3].modalidade == 8
