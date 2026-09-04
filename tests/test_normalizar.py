from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from licita_radar.ingest.normalizar import (
    limpar_texto,
    normalizar_contratacao,
    normalizar_pagina,
    para_decimal,
    sem_acento,
)


class TestSemAcento:
    def test_remove_acento_e_caixa(self) -> None:
        assert sem_acento("LICITAÇÃO Pública") == "licitacao publica"

    def test_colapsa_espacos(self) -> None:
        assert sem_acento("  manutenção   evolutiva \n ") == "manutencao evolutiva"

    def test_e_idempotente(self) -> None:
        uma_vez = sem_acento("Aquisição de Softwares")
        assert sem_acento(uma_vez) == uma_vez


class TestLimparTexto:
    def test_colapsa_quebras_preservando_acento(self) -> None:
        assert limpar_texto("toner   \n e suprimentos") == "toner e suprimentos"

    def test_nulo_vira_string_vazia(self) -> None:
        assert limpar_texto(None) == ""


class TestParaDecimal:
    def test_converte_float_sem_perder_centavo(self) -> None:
        assert para_decimal(1250000.0) == Decimal("1250000.0")

    def test_vazio_e_lixo_viram_none(self) -> None:
        assert para_decimal(None) is None
        assert para_decimal("") is None
        assert para_decimal("não informado") is None


class TestNormalizarContratacao:
    def test_extrai_os_campos_que_importam(self, pagina1: dict[str, Any]) -> None:
        contratacao = normalizar_contratacao(pagina1["data"][0])

        assert contratacao is not None
        assert contratacao.numero_controle_pncp == "01612092000123-1-000045/2026"
        assert contratacao.modalidade_codigo == 6
        assert "sustentação de sistemas web" in contratacao.objeto
        assert contratacao.uf == "GO"
        assert contratacao.municipio == "Goiânia"
        assert contratacao.valor_estimado == Decimal("1250000.0")
        assert contratacao.data_publicacao == date(2026, 9, 1)
        assert contratacao.encerramento_proposta is not None
        assert contratacao.encerramento_proposta.day == 18

    def test_preserva_o_payload_cru_inteiro(self, pagina1: dict[str, Any]) -> None:
        bruto = pagina1["data"][0]
        contratacao = normalizar_contratacao(bruto)

        assert contratacao is not None
        # inclusive campos que o modelo não mapeia
        assert contratacao.payload["unidadeOrgao"]["nomeUnidade"].startswith("Superintendência")

    def test_limpa_quebra_de_linha_do_objeto(self, pagina1: dict[str, Any]) -> None:
        contratacao = normalizar_contratacao(pagina1["data"][1])

        assert contratacao is not None
        assert "\n" not in contratacao.objeto
        assert "impressão para as unidades" in contratacao.objeto

    def test_campo_ausente_vira_none_e_nao_excecao(self, pagina1: dict[str, Any]) -> None:
        # o terceiro item não tem valor estimado nem encerramento
        contratacao = normalizar_contratacao(pagina1["data"][2])

        assert contratacao is not None
        assert contratacao.valor_estimado is None
        assert contratacao.encerramento_proposta is None
        assert contratacao.abertura_proposta is not None

    def test_registro_sem_chave_e_descartado(self, pagina1: dict[str, Any]) -> None:
        assert normalizar_contratacao(pagina1["data"][3]) is None

    def test_registro_sem_objeto_e_descartado(self) -> None:
        bruto = {"numeroControlePNCP": "x-1-1/2026", "objetoCompra": ""}
        assert normalizar_contratacao(bruto) is None


class TestNormalizarPagina:
    def test_pula_os_inaproveitaveis_sem_derrubar_o_resto(self, pagina1: dict[str, Any]) -> None:
        contratacoes = normalizar_pagina(pagina1["data"])

        assert len(contratacoes) == 3  # 4 itens, 1 sem chave
        assert all(c.numero_controle_pncp for c in contratacoes)


class TestUrlPncp:
    def test_monta_link_do_portal(self, pagina1: dict[str, Any]) -> None:
        contratacao = normalizar_contratacao(pagina1["data"][0])

        assert contratacao is not None
        assert contratacao.url_pncp == "https://pncp.gov.br/app/editais/01612092000123/2026/45"

    def test_chave_fora_do_padrao_nao_quebra(self, pagina1: dict[str, Any]) -> None:
        bruto = {**pagina1["data"][0], "numeroControlePNCP": "formato-estranho"}
        contratacao = normalizar_contratacao(bruto)

        assert contratacao is not None
        assert contratacao.url_pncp is None
