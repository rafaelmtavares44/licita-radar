"""O bug que travou o projeto inteiro no Windows.

`asyncio.run()` no Windows usa o ProactorEventLoop, e o psycopg recusa
rodar em modo assíncrono nele: a conexão morre com InterfaceError antes de
tocar a rede. O sintoma é cruel — o container está de pé, a porta
publicada, e o erro fala de conexão.

A correção mora na importação de `cli.py` e de `conftest.py`. Este teste
existe para ninguém remover achando que é linha morta.
"""

from __future__ import annotations

import inspect
import sys

import pytest

from licita_radar import cli
from licita_radar.diagnostico import Estado, checar_event_loop


def test_a_cli_troca_a_politica_de_event_loop_no_windows() -> None:
    fonte = inspect.getsource(cli)

    assert 'sys.platform == "win32"' in fonte
    assert "WindowsSelectorEventLoopPolicy" in fonte


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
