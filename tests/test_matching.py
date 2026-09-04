"""O funil inteiro, exercitado contra as contratações reais da amostra."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from licita_radar.config.perfil import Perfil
from licita_radar.ingest.modelos import Contratacao
from licita_radar.ingest.normalizar import normalizar_pagina
from licita_radar.matching.lexical import avaliar_elegibilidade, pontuar_lexicalmente
from licita_radar.matching.pontuacao import Veredito, avaliar, explicar
from licita_radar.matching.semantico import MotorSemantico
from tests.dubles import EncoderFalso


def _contratacao(**ajustes: Any) -> Contratacao:
    base: dict[str, Any] = {
        "numero_controle_pncp": "X-1-1/2026",
        "modalidade_codigo": 6,
        "objeto": "Desenvolvimento de software sob demanda para gestão de protocolos",
        "uf": "GO",
        "valor_estimado": Decimal("200000.00"),
        "encerramento_proposta": datetime.now(UTC) + timedelta(days=20),
        "esfera": "M",
        "payload": {},
    }
    return Contratacao(**{**base, **ajustes})


# ---------------------------------------------------------------- elegibilidade


class TestElegibilidade:
    def test_uf_de_fora_e_inelegivel(self, perfil: Perfil) -> None:
        motivo = avaliar_elegibilidade(_contratacao(uf="SP"), perfil)

        assert motivo is not None
        assert "UFs do perfil" in motivo

    def test_modalidade_fora_do_perfil(self, perfil: Perfil) -> None:
        motivo = avaliar_elegibilidade(_contratacao(modalidade_codigo=3), perfil)

        assert motivo is not None
        assert "modalidade 3" in motivo

    def test_valor_abaixo_do_minimo(self, perfil: Perfil) -> None:
        motivo = avaliar_elegibilidade(_contratacao(valor_estimado=Decimal("1000")), perfil)

        assert motivo is not None
        assert "abaixo do mínimo" in motivo

    def test_prazo_curto_demais(self, perfil: Perfil) -> None:
        apertada = _contratacao(encerramento_proposta=datetime.now(UTC) + timedelta(hours=10))
        motivo = avaliar_elegibilidade(apertada, perfil)

        assert motivo is not None
        assert "prazo curto" in motivo

    def test_prazo_ja_encerrado_tem_motivo_proprio(self, perfil: Perfil) -> None:
        """Visto na amostra real: o endpoint devolve coisa encerrando hoje.

        "Já encerrou" e "não dá tempo" são situações diferentes — e um motivo
        com número de dias negativo é uma mensagem quebrada.
        """
        vencida = _contratacao(encerramento_proposta=datetime.now(UTC) - timedelta(days=2))
        motivo = avaliar_elegibilidade(vencida, perfil)

        assert motivo is not None
        assert "já encerrado" in motivo
        assert "-" not in motivo.split("em ")[-1]

    def test_valor_ausente_nao_reprova(self, perfil: Perfil) -> None:
        """Muita dispensa vem sem valor estimado — isso não pode eliminar."""
        assert avaliar_elegibilidade(_contratacao(valor_estimado=None), perfil) is None

    def test_valor_zero_e_ausencia_de_informacao_nao_um_valor(self, perfil: Perfil) -> None:
        """5 de 37 contratações reais de Goiás vieram com valorTotalEstimado 0.

        Zero ali é o órgão que não preencheu o campo. Descartar por isso é
        perder licitação boa por falha de terceiro.
        """
        assert avaliar_elegibilidade(_contratacao(valor_estimado=Decimal("0")), perfil) is None

    def test_contratacao_boa_passa(self, perfil: Perfil) -> None:
        assert avaliar_elegibilidade(_contratacao(), perfil) is None


# ------------------------------------------------------------------- léxico


class TestLexical:
    def test_conta_as_positivas(self, perfil: Perfil) -> None:
        resultado = pontuar_lexicalmente(
            "Contratação de desenvolvimento de software e sustentação de sistemas", perfil
        )

        assert resultado.score > 0
        assert "desenvolvimento de software" in resultado.encontradas

    def test_veto_por_negativa_zera_tudo(self, perfil: Perfil) -> None:
        resultado = pontuar_lexicalmente(
            "AQUISIÇÃO DE MATERIAIS DE INFORMÁTICA SENDO IMPRESSORA E TONER", perfil
        )

        assert resultado.vetada
        assert resultado.score == 0.0

    def test_veto_ignora_acento_e_caixa(self, perfil: Perfil) -> None:
        assert pontuar_lexicalmente("Serviço de vigilância patrimonial", perfil).vetada

    def test_objeto_neutro_nao_pontua_nem_veta(self, perfil: Perfil) -> None:
        resultado = pontuar_lexicalmente("AQUISIÇÃO DE ADUBOS PARA A HORTA MUNICIPAL", perfil)

        assert not resultado.vetada
        assert resultado.score == 0.0


# ---------------------------------------------------------------- funil todo


class TestAvaliar:
    def test_inelegivel_nem_chega_a_pontuar(self, perfil: Perfil) -> None:
        avaliacao = avaliar(_contratacao(uf="SP"), perfil, score_semantico=0.99)

        assert avaliacao.veredito is Veredito.INELEGIVEL
        assert avaliacao.score_final == 0.0  # a semântica alta não salva

    def test_vetada_vence_score_semantico_alto(self, perfil: Perfil) -> None:
        toner = _contratacao(objeto="AQUISIÇÃO DE TONER PARA IMPRESSORAS DO SETOR DE TI")
        avaliacao = avaliar(toner, perfil, score_semantico=0.95)

        assert avaliacao.veredito is Veredito.VETADA
        assert "toner" in (avaliacao.motivo or "")

    def test_candidata_quando_passa_do_limiar(self, perfil: Perfil) -> None:
        avaliacao = avaliar(_contratacao(), perfil, score_semantico=0.9)

        assert avaliacao.veredito is Veredito.CANDIDATA
        assert avaliacao.alerta

    def test_abaixo_do_limiar_fica_registrada(self, perfil: Perfil) -> None:
        avaliacao = avaliar(_contratacao(), perfil, score_semantico=0.1)

        assert avaliacao.veredito is Veredito.ABAIXO_DO_LIMIAR
        assert not avaliacao.alerta
        assert avaliacao.motivo is not None

    def test_pesos_do_perfil_sao_respeitados(self, perfil: Perfil) -> None:
        avaliacao = avaliar(_contratacao(), perfil, score_semantico=1.0)
        esperado = (
            perfil.pontuacao.peso_lexical * avaliacao.score_lexical
            + perfil.pontuacao.peso_semantico * 1.0
        )

        assert avaliacao.score_final == pytest.approx(esperado)

    def test_toda_avaliacao_tem_explicacao_em_portugues(self, perfil: Perfil) -> None:
        for score in (0.0, 0.5, 0.95):
            frase = explicar(avaliar(_contratacao(), perfil, score_semantico=score))
            assert frase and not frase.startswith("Veredito.")


# ------------------------------------------------------------- semântica


class TestMotorSemantico:
    def test_software_pontua_mais_que_picole(self, perfil: Perfil) -> None:
        motor = MotorSemantico(EncoderFalso())
        alvos = [
            _contratacao(objeto="Desenvolvimento de sistema e software de gestão"),
            _contratacao(objeto="AQUISIÇÃO DE PICOLÉS PARA O DIA DAS CRIANÇAS"),
        ]
        codificados = motor.codificar_contratacoes(alvos)
        scores = [motor.pontuar(perfil, c.vetor) for c in codificados]

        assert scores[0] > scores[1]

    def test_vetor_do_perfil_e_cacheado(self, perfil: Perfil) -> None:
        motor = MotorSemantico(EncoderFalso())

        assert motor.vetor_do_perfil(perfil) is motor.vetor_do_perfil(perfil)

    def test_codifica_em_lotes_sem_perder_ninguem(self, perfil: Perfil) -> None:
        alvos = [_contratacao(numero_controle_pncp=f"X-1-{i}/2026") for i in range(70)]
        codificados = MotorSemantico(EncoderFalso()).codificar_contratacoes(alvos, lote=16)

        assert len(codificados) == 70
        assert {c.numero_controle_pncp for c in codificados} == {
            a.numero_controle_pncp for a in alvos
        }

    def test_lista_vazia_nao_chama_o_modelo(self) -> None:
        assert MotorSemantico(EncoderFalso()).codificar_contratacoes([]) == []


# --------------------------------------------------- a amostra real do PNCP


class TestContraAAmostraReal:
    def test_o_funil_descarta_o_ruido_de_informatica(
        self, perfil: Perfil, amostra_real: dict[str, Any]
    ) -> None:
        """Impressora e material de informática não podem virar alerta.

        São os falsos positivos clássicos de quem monta um radar de TI: as
        palavras estão no campo certo, o assunto é o errado.
        """
        contratacoes = normalizar_pagina(amostra_real["data"])
        motor = MotorSemantico(EncoderFalso())
        codificados = {
            c.numero_controle_pncp: c.vetor for c in motor.codificar_contratacoes(contratacoes)
        }

        for contratacao in contratacoes:
            if "IMPRESSORA" not in contratacao.objeto.upper():
                continue
            score = motor.pontuar(perfil, codificados[contratacao.numero_controle_pncp])
            avaliacao = avaliar(contratacao, perfil, score_semantico=score)
            assert avaliacao.veredito is not Veredito.CANDIDATA, contratacao.objeto[:60]

    def test_nenhuma_das_37_reais_vira_alerta_falso(
        self, perfil: Perfil, amostra_real: dict[str, Any]
    ) -> None:
        """A amostra real não tem nenhuma licitação de software.

        Se alguma virar candidata, o filtro está solto — e é melhor descobrir
        aqui do que no celular do usuário às sete da manhã.
        """
        contratacoes = normalizar_pagina(amostra_real["data"])
        motor = MotorSemantico(EncoderFalso())
        codificados = {
            c.numero_controle_pncp: c.vetor for c in motor.codificar_contratacoes(contratacoes)
        }

        candidatas = [
            c.objeto[:70]
            for c in contratacoes
            if avaliar(
                c,
                perfil,
                score_semantico=motor.pontuar(perfil, codificados[c.numero_controle_pncp]),
            ).alerta
        ]

        assert candidatas == []


class TestEsfera:
    """Filtrar por esfera é o "quero só o governo federal"."""

    def test_esfera_vazia_no_perfil_aceita_todas(self, perfil: Perfil) -> None:
        for esfera in ("F", "E", "M", None):
            assert avaliar_elegibilidade(_contratacao(esfera=esfera), perfil) is None

    def test_so_federal_barra_municipal(self, perfil_valido: dict[str, Any]) -> None:
        perfil_valido["restricoes"]["esferas"] = ["F"]
        so_federal = Perfil.model_validate(perfil_valido)

        assert avaliar_elegibilidade(_contratacao(esfera="F"), so_federal) is None

        motivo = avaliar_elegibilidade(_contratacao(esfera="M"), so_federal)
        assert motivo is not None
        assert "Municipal" in motivo

    def test_esfera_ausente_no_dado_nao_reprova(self, perfil_valido: dict[str, Any]) -> None:
        """Órgão sem esferaId no payload não pode ser descartado por isso."""
        perfil_valido["restricoes"]["esferas"] = ["F"]
        so_federal = Perfil.model_validate(perfil_valido)

        assert avaliar_elegibilidade(_contratacao(esfera=None), so_federal) is None
