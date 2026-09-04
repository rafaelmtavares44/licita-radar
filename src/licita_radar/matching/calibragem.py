"""Onde colocar o limiar, a partir dos scores que de fato apareceram.

O valor 0,72 do perfil de exemplo foi um chute — e um chute ruim. Numa
varredura federal real, o melhor score foi 0,49 e nenhuma contratação
chegou perto: o limiar tornava o alerta impossível, não seletivo.

A causa é aritmética. A similaridade de cosseno entre dois textos
diferentes dificilmente passa de 0,6, mesmo quando eles falam da mesma
coisa; combinada com um score léxico quase sempre zero, o teto prático do
score final fica em torno de 0,5. Um limiar acima disso nunca dispara.

Em vez de adivinhar de novo, este módulo olha a distribuição real e sugere
um corte. É a diferença entre configurar por intuição e configurar por
evidência.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median


@dataclass(frozen=True)
class Sugestao:
    limiar: float
    quantos_alertariam: int
    total: int
    maximo: float
    mediana: float
    percentil_90: float

    @property
    def proporcao(self) -> float:
        return self.quantos_alertariam / self.total if self.total else 0.0


def percentil(valores: Sequence[float], p: float) -> float:
    """Percentil por interpolação linear. Sem numpy: são poucos milhares."""
    if not valores:
        return 0.0
    ordenados = sorted(valores)
    if len(ordenados) == 1:
        return ordenados[0]

    posicao = (len(ordenados) - 1) * p
    baixo = int(posicao)
    alto = min(baixo + 1, len(ordenados) - 1)
    peso = posicao - baixo
    return ordenados[baixo] * (1 - peso) + ordenados[alto] * peso


def sugerir_limiar(scores: Sequence[float], *, alvo_diario: int = 10) -> Sugestao:
    """Sugere um corte que deixe passar cerca de `alvo_diario` contratações.

    O critério é prático, não estatístico: um radar que alerta cinquenta
    vezes por dia é ignorado na segunda semana, e um que nunca alerta é
    desinstalado na primeira. Dez por dia é o que uma pessoa lê no café.
    """
    validos = [s for s in scores if s > 0]
    if not validos:
        return Sugestao(0.0, 0, len(scores), 0.0, 0.0, 0.0)

    if alvo_diario >= len(validos):
        limiar = min(validos)
    else:
        # o corte que deixa passar aproximadamente `alvo_diario` itens
        limiar = percentil(validos, 1 - (alvo_diario / len(validos)))

    limiar = round(max(0.05, limiar), 2)
    return Sugestao(
        limiar=limiar,
        quantos_alertariam=sum(1 for s in scores if s >= limiar),
        total=len(scores),
        maximo=round(max(validos), 2),
        mediana=round(median(validos), 2),
        percentil_90=round(percentil(validos, 0.9), 2),
    )
