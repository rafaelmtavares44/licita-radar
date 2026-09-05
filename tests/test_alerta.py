"""O canal de alerta e as duas mensagens que ele entrega."""

from __future__ import annotations

from typing import Any, ClassVar

import httpx
import pytest
import respx

from licita_radar.alerta.canal import (
    CanalDesligado,
    ErroDoTelegram,
    Telegram,
    construir_canal,
    cortar,
    escapar,
)
from licita_radar.alerta.mensagem import mensagem_da_analise, mensagem_de_triagem

TOKEN = "123456789:AAtoken-de-teste"
BASE = "https://api.telegram.exemplo.test"
ENVIAR = f"{BASE}/bot{TOKEN}/sendMessage"


def _bot(chat_id: str = "42") -> Telegram:
    return Telegram(token=TOKEN, chat_id=chat_id, base_url=BASE, timeout_s=2.0)


# ------------------------------------------------------------------ canal


def test_sem_token_o_canal_fica_desligado() -> None:
    """Quem clona o repositório não precisa de um bot para ver o radar rodar."""
    assert not construir_canal(token=None, chat_id="42").ativo
    assert not construir_canal(token=TOKEN, chat_id=None).ativo
    assert construir_canal(token=TOKEN, chat_id="42").ativo


@pytest.mark.asyncio
async def test_canal_desligado_nao_finge_que_entregou() -> None:
    assert await CanalDesligado().enviar("qualquer coisa") is False


def test_escapar_mexe_em_tres_caracteres() -> None:
    """A razão de o formato ser HTML e não MarkdownV2."""
    assert escapar("R$ 65.001,91 (vinte por cento) - item 11.1.15!") == (
        "R$ 65.001,91 (vinte por cento) - item 11.1.15!"
    )
    assert escapar("<b>x</b> & y") == "&lt;b&gt;x&lt;/b&gt; &amp; y"


def test_mensagem_longa_e_cortada_antes_de_sair() -> None:
    """O Telegram recusa acima de 4096 — melhor cortar que levar 400."""
    cortada = cortar("a" * 5000, limite=100)

    assert len(cortada) <= 100 + len("\n\n<i>(cortado)</i>")
    assert cortada.endswith("<i>(cortado)</i>")


@pytest.mark.asyncio
@respx.mock
async def test_envio_bem_sucedido(respx_mock: Any = None) -> None:
    rota = respx.post(ENVIAR).mock(return_value=httpx.Response(200, json={"ok": True}))

    assert await _bot().enviar("<b>oi</b>") is True

    corpo = rota.calls[0].request.content.decode()
    assert '"parse_mode": "HTML"' in corpo or '"parse_mode":"HTML"' in corpo


@pytest.mark.asyncio
@respx.mock
async def test_telegram_fora_do_ar_nao_levanta() -> None:
    """Alerta que não sai é aborrecimento; execução que morre é prejuízo."""
    respx.post(ENVIAR).mock(side_effect=httpx.ConnectError("sem rede"))

    assert await _bot().enviar("oi") is False


@pytest.mark.asyncio
@respx.mock
async def test_chat_nao_encontrado_vira_dica_no_log(caplog: Any) -> None:
    respx.post(ENVIAR).mock(
        return_value=httpx.Response(
            400, json={"ok": False, "description": "Bad Request: chat not found"}
        )
    )

    assert await _bot().enviar("oi") is False
    assert "/start" in caplog.text


@pytest.mark.asyncio
@respx.mock
async def test_token_recusado_explica_de_onde_vem_o_token() -> None:
    respx.get(f"{BASE}/bot{TOKEN}/getMe").mock(return_value=httpx.Response(401))

    with pytest.raises(ErroDoTelegram, match="BotFather"):
        await _bot().conferir()


@pytest.mark.asyncio
@respx.mock
async def test_conferir_devolve_o_nome_do_bot() -> None:
    respx.get(f"{BASE}/bot{TOKEN}/getMe").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"username": "meu_radar_bot"}})
    )

    assert await _bot().conferir() == "meu_radar_bot"


# -------------------------------------------------------------- mensagens


class TestTriagem:
    """ "Tem coisa esperando você" — o alerta que faz pegar o celular."""

    ITENS: ClassVar[list[dict[str, Any]]] = [
        {
            "numero_controle": "10825373000155-1-000157/2026",
            "objeto": "Contratação de fábrica de software para sustentação de sistemas",
            "orgao": "MINISTÉRIO DA GESTÃO",
            "valor_estimado": 1240000.0,
            "encerramento": "2026-09-19",
            "score": 0.49,
            "justificativa": "Combina: a empresa desenvolve e sustenta sistemas web.",
        }
    ]

    def test_traz_o_que_decide_se_vale_abrir(self) -> None:
        texto = mensagem_de_triagem(self.ITENS)

        assert "1 licitação espera a sua decisão" in texto
        assert "R$ 1.240.000,00" in texto
        assert "encerra 2026-09-19" in texto
        assert "score 0.49" in texto
        assert "Combina:" in texto
        assert "pncp.gov.br/app/editais/10825373000155/2026/157" in texto

    def test_lista_grande_e_cortada_para_caber_numa_tela(self) -> None:
        """Alerta que precisa de rolagem para ser compreendido já falhou."""
        muitos = [{**self.ITENS[0], "numero_controle": f"x-1-{i}/2026"} for i in range(12)]

        texto = mensagem_de_triagem(muitos, mostrar=5)

        assert "12 licitações esperam" in texto
        assert "e mais 7" in texto

    def test_com_painel_o_alerta_vira_link_em_vez_de_comando(self) -> None:
        """Quem lê no celular não vai digitar um comando no computador."""
        texto = mensagem_de_triagem(self.ITENS, painel="http://192.168.0.9:8000")

        assert 'href="http://192.168.0.9:8000"' in texto
        assert "licita-radar revisar" not in texto

    def test_sem_painel_ensina_o_comando(self) -> None:
        assert "licita-radar revisar" in mensagem_de_triagem(self.ITENS)

    def test_nada_novo_tambem_e_notícia(self) -> None:
        assert "Nenhuma licitação nova" in mensagem_de_triagem([])

    def test_orgao_com_e_comercial_nao_quebra_o_html(self) -> None:
        item = {**self.ITENS[0], "orgao": "FUNDAÇÃO A & B"}

        assert "A &amp; B" in mensagem_de_triagem([item])


class TestAnalise:
    ANALISE: ClassVar[dict[str, Any]] = {
        "resumo": "Locação de solução de controle de acesso.",
        "afirmacoes": [
            {
                "assunto": "penalidades",
                "texto": "Multa de 20% sobre o valor do item.",
                "trecho": "Multa de 20% (vinte por cento)",
                "estado": "sustentada",
                "observacao": "citação encontrada",
            },
            {
                "assunto": "riscos",
                "texto": "Documentos complementares em até 2 horas.",
                "trecho": "É dever do fornecedor atualizar o Sicaf",
                "estado": "numero_sem_apoio",
                "observacao": "a citação é do edital, mas não contém 2",
            },
        ],
        "alertas": ["1 afirmação cita o edital mas traz número que a citação não contém"],
        "modelo": "openai/gpt-oss-120b",
        "tokens": 7714,
    }

    def test_separa_o_que_da_para_repetir_do_que_precisa_ser_conferido(self) -> None:
        """No celular ninguém confere edital de 80 páginas — a marca precisa saltar."""
        texto = mensagem_da_analise(
            self.ANALISE, objeto="Controle de acesso", numero_controle="x-1-157/2026"
        )

        assert "✓ <b>Multas</b> — Multa de 20%" in texto
        assert "Confira no documento antes de usar" in texto
        assert "≈ Documentos complementares em até 2 horas." in texto
        assert "1/2 conferidas" in texto

    def test_analise_vazia_ainda_diz_alguma_coisa(self) -> None:
        texto = mensagem_da_analise({}, objeto="Alguma contratação")

        assert "Alguma contratação" in texto
        assert "0/0 conferidas" in texto
