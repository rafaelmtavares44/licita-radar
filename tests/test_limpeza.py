"""Testes da limpeza, escritos a partir de dados reais do PNCP.

Os casos abaixo não foram inventados: saíram das 37 contratações de Goiás
capturadas em 04/09/2026, que estão em `fixtures/pncp/amostra_real_go.json`.
"""

from __future__ import annotations

from typing import Any

import pytest

from licita_radar.matching.limpeza import limpar_objeto, objeto_e_vago, texto_para_embedding


class TestLimparObjeto:
    @pytest.mark.parametrize(
        ("bruto", "esperado_contem"),
        [
            (
                "DESPESA REFERENTE A COMPRA DE PEÇA PARA A MANUTENÇÃO CORRETIVA NA MOTONIVELADORA",
                "COMPRA DE PEÇA",
            ),
            (
                "SOLICITAÇÃO DE AQUISIÇÃO DE MATERIAL INFORMÁTICA PARA O DEPARTAMENTO",
                "AQUISIÇÃO DE MATERIAL INFORMÁTICA",
            ),
            (
                "[Portal de Compras Públicas] - DISPENSA - Contratação de empresa para limpeza",
                "limpeza",
            ),
            (
                "O PRESENTE TERMO TEM COMO OBJETO A AQUISIÇÃO DE 3 IMPRESSORAS TANQUE DE TINTA",
                "AQUISIÇÃO DE 3 IMPRESSORAS",
            ),
            (
                "1 -TERMO DE SOLICITAÇÃO SOLICITAÇÃO DE AQUISIÇÃO DE MOBILIARIO EM GERAL",
                "MOBILIARIO EM GERAL",
            ),
        ],
    )
    def test_remove_a_casca_burocratica(self, bruto: str, esperado_contem: str) -> None:
        limpo = limpar_objeto(bruto)

        assert esperado_contem in limpo
        assert len(limpo) < len(bruto)

    def test_nao_come_o_texto_inteiro(self) -> None:
        """Quando o prefixo É o objeto, devolve o original em vez de vazio."""
        assert limpar_objeto("PRESTAÇÃO DE SERVIÇOS ESPECIALIZADOS") != ""

    def test_colapsa_espacos_e_quebras(self) -> None:
        assert limpar_objeto("AQUISIÇÃO   DE \n ADUBOS") == "AQUISIÇÃO DE ADUBOS"

    def test_objeto_ja_limpo_passa_intacto(self) -> None:
        texto = "Desenvolvimento de sistema web para gestão de protocolos"
        assert limpar_objeto(texto) == texto


class TestObjetoVago:
    def test_reconhece_os_vagos_reais(self) -> None:
        assert objeto_e_vago("PRESTAÇÃO DE SERVIÇOS ESPECIALIZADOS")
        assert objeto_e_vago("AQUISIÇÃO DE ADUBOS")

    def test_objeto_descritivo_nao_e_vago(self) -> None:
        assert not objeto_e_vago(
            "CONTRATAÇÃO DE EMPRESA ESPECIALIZADA DO RAMO DE TELECOMUNICAÇÕES "
            "PARA FORNECIMENTO DO SERVIÇO DE TELEFONIA MÓVEL"
        )


class TestTextoParaEmbedding:
    def test_usa_o_objeto_limpo(self) -> None:
        texto = texto_para_embedding("DESPESA REFERENTE A AQUISIÇÃO DE MOTONIVELADORA PATROL XCMG")

        assert texto.startswith("AQUISIÇÃO")

    def test_completa_o_objeto_vago_com_a_informacao_complementar(self) -> None:
        texto = texto_para_embedding(
            "PRESTAÇÃO DE SERVIÇOS ESPECIALIZADOS",
            "Desenvolvimento de sistema de gestão escolar sob demanda",
        )

        assert "gestão escolar" in texto

    def test_nao_duplica_quando_o_complemento_repete_o_objeto(self) -> None:
        """Na amostra real, informacaoComplementar quase sempre repete o objeto."""
        objeto = "AQUISIÇÃO DE MATERIAIS DE INFORMÁTICA SENDO IMPRESSORA PARA O SETOR"
        texto = texto_para_embedding(objeto, objeto)

        assert texto.count("IMPRESSORA") == 1


class TestContraAAmostraReal:
    def test_toda_a_amostra_sobrevive_a_limpeza(self, amostra_real: dict[str, Any]) -> None:
        """Nenhum objeto real pode virar string vazia ou perder o sentido."""
        for item in amostra_real["data"]:
            limpo = limpar_objeto(item["objetoCompra"])

            assert limpo, f"objeto zerado: {item['numeroControlePNCP']}"
            assert len(limpo) >= 12

    def test_a_limpeza_encurta_a_maioria_dos_objetos_reais(
        self, amostra_real: dict[str, Any]
    ) -> None:
        encurtados = sum(
            1
            for item in amostra_real["data"]
            if len(limpar_objeto(item["objetoCompra"])) < len(item["objetoCompra"].strip())
        )

        assert encurtados >= len(amostra_real["data"]) // 2
