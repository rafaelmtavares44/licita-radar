"""O bug que travou o projeto inteiro no Windows.

`asyncio.run()` no Windows usa o ProactorEventLoop, e o psycopg recusa
rodar em modo assíncrono nele: a conexão morre com InterfaceError antes de
tocar a rede. O sintoma é cruel — o container está de pé, a porta
publicada, e o erro fala de conexão.

A correção mora em `licita_radar.plataforma`, em duas metades: a política
(para quem chama `asyncio.run()`) e a fábrica de loop (para o uvicorn, que
passa `loop_factory` explícito e com isso ignora a política). Este teste
existe para ninguém remover nenhuma das duas achando que é linha morta.
"""

from __future__ import annotations

import inspect
import sys

import pytest

from licita_radar import cli, plataforma
from licita_radar.diagnostico import Estado, checar_event_loop


def test_a_cli_ajusta_a_politica_na_importacao() -> None:
    fonte = inspect.getsource(cli)

    assert "plataforma.ajustar_politica()" in fonte


def test_a_politica_do_windows_e_a_selector() -> None:
    fonte = inspect.getsource(plataforma)

    assert 'sys.platform == "win32"' in fonte
    assert "WindowsSelectorEventLoopPolicy" in fonte


def test_o_servidor_recebe_a_nossa_fabrica_de_loop() -> None:
    """O uvicorn passa `loop_factory` explícito, e isso ignora a política.

    Sem esta linha, no Windows o servidor morre com InterfaceError do
    psycopg antes de aceitar a primeira conexão — com a defesa instalada
    e simplesmente pulada.
    """
    assert "loop=plataforma.CAMINHO_DA_FABRICA" in inspect.getsource(cli)
    assert plataforma.CAMINHO_DA_FABRICA == "licita_radar.plataforma:fabrica_de_loop"


def test_a_fabrica_devolve_um_loop_utilizavel() -> None:
    loop = plataforma.fabrica_de_loop()
    try:
        assert loop.run_until_complete(_um()) == 1
        if sys.platform == "win32":  # pragma: no cover
            assert "Proactor" not in type(loop).__name__
    finally:
        loop.close()


async def _um() -> int:
    return 1


def test_o_doctor_verifica_o_event_loop() -> None:
    checagem = checar_event_loop()

    assert checagem.titulo == "event loop"
    assert checagem.estado in (Estado.OK, Estado.FALHA)


@pytest.mark.skipif(sys.platform != "win32", reason="só faz sentido no Windows")
def test_no_windows_a_politica_ativa_e_a_selector() -> None:
    import asyncio

    assert "Selector" in type(asyncio.get_event_loop_policy()).__name__


class TestOrigemDaConfiguracao:
    """A armadilha que custou uma rodada: o ambiente vence o .env em silêncio."""

    def test_avisa_quando_o_ambiente_sobrepoe_o_arquivo(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from licita_radar.config.settings import Settings
        from licita_radar.diagnostico import Estado, checar_origem_da_config

        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text(
            "LR_DATABASE_URL=postgresql://licita:licita@127.0.0.1:5433/licita_radar\n",
            encoding="utf-8",
        )
        monkeypatch.setenv(
            "LR_DATABASE_URL", "postgresql://licita:licita@127.0.0.1:5432/licita_radar"
        )

        checagem = checar_origem_da_config(Settings())

        assert checagem.estado is Estado.AVISO
        assert "sobrepõe o .env" in checagem.detalhe
        assert "Remove-Item" in checagem.dica

    def test_sem_variavel_de_ambiente_nao_avisa(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from licita_radar.config.settings import Settings
        from licita_radar.diagnostico import Estado, checar_origem_da_config

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("LR_DATABASE_URL", raising=False)

        assert checar_origem_da_config(Settings()).estado is Estado.OK
