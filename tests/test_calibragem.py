"""Onde colocar o limiar — a partir dos dados, não da intuição.

O 0,72 do perfil de exemplo veio de um chute. Numa varredura federal real,
o melhor score foi 0,49: o limiar não era seletivo, era inalcançável.
"""

from __future__ import annotations

import pytest

from licita_radar.matching.calibragem import percentil, sugerir_limiar


class TestPercentil:
    def test_extremos(self) -> None:
        valores = [0.1, 0.2, 0.3, 0.4, 0.5]

        assert percentil(valores, 0.0) == pytest.approx(0.1)
        assert percentil(valores, 1.0) == pytest.approx(0.5)

    def test_mediana(self) -> None:
        assert percentil([0.1, 0.2, 0.3], 0.5) == pytest.approx(0.2)

    def test_interpola_entre_dois_pontos(self) -> None:
        assert percentil([0.0, 1.0], 0.25) == pytest.approx(0.25)

    def test_lista_vazia_ou_unitaria(self) -> None:
        assert percentil([], 0.5) == 0.0
        assert percentil([0.42], 0.9) == pytest.approx(0.42)


class TestSugerirLimiar:
    def test_sugere_um_corte_alcancavel(self) -> None:
        """O caso real: nenhum score chegou a 0,50 e o limiar era 0,72."""
        scores = [0.49, 0.43, 0.41, 0.41, 0.40, 0.39, 0.39, 0.38] + [0.05] * 200

        sugestao = sugerir_limiar(scores, alvo_diario=10)

        assert sugestao.limiar < 0.49
        assert sugestao.maximo == pytest.approx(0.49)
        assert sugestao.quantos_alertariam > 0

    def test_deixa_passar_aproximadamente_o_alvo(self) -> None:
        scores = [i / 100 for i in range(1, 101)]

        sugestao = sugerir_limiar(scores, alvo_diario=10)

        assert 5 <= sugestao.quantos_alertariam <= 20

    def test_nunca_sugere_zero(self) -> None:
        """Limiar zero alerta tudo, o que é o mesmo que não ter radar."""
        assert sugerir_limiar([0.01] * 50).limiar >= 0.05

    def test_tudo_zerado_nao_quebra(self) -> None:
        sugestao = sugerir_limiar([0.0, 0.0, 0.0])

        assert sugestao.limiar == 0.0
        assert sugestao.quantos_alertariam == 0

    def test_lista_vazia(self) -> None:
        assert sugerir_limiar([]).total == 0

    def test_poucos_scores_usa_o_menor(self) -> None:
        sugestao = sugerir_limiar([0.4, 0.5], alvo_diario=10)

        assert sugestao.limiar == pytest.approx(0.4)
