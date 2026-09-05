"""Os números da afirmação precisam estar no trecho que a sustenta.

Conferir que a citação existe não basta, e um caso real mostrou por quê. O
modelo escreveu:

    "Multas podem chegar a 15% do valor da contratação"
    citando: "11.1.15. Multa de % ( por cento) sobre o valor estimado…"

A citação era verdadeira — aquela frase está no edital, exatamente assim,
porque a extração do PDF tinha perdido o número de lugar. Mas ela não
sustenta coisa nenhuma sobre 15%. O edital dizia 20%.

Uma frase inteira pode ser confirmada com o número errado, e o número é
justamente a parte em que alguém age: 15% ou 20% de multa, 3 ou 6 anos de
impedimento, 30 ou 60 dias de prazo. Por isso os números da afirmação são
conferidos contra a citação, um a um.

A comparação tem um cuidado que dado real também impôs: edital escreve
número por extenso o tempo todo. "prazo máximo de até dez dias úteis"
sustenta perfeitamente "em até 10 dias úteis" — reprovar isso seria trocar
alucinação por falso alarme.
"""

from __future__ import annotations

import contextlib
import re
import unicodedata

#: Um número: 15, 0,5, 1.000, 11.1.15. A pontuação faz parte do token de
#: propósito — é o que impede "15" de casar dentro de "11.1.15", que é a
#: numeração da cláusula e não a multa.
_TOKEN = re.compile(r"\d+(?:[.,]\d+)*")

_UNIDADES = (
    "zero",
    "um",
    "dois",
    "tres",
    "quatro",
    "cinco",
    "seis",
    "sete",
    "oito",
    "nove",
    "dez",
    "onze",
    "doze",
    "treze",
    "quatorze",
    "quinze",
    "dezesseis",
    "dezessete",
    "dezoito",
    "dezenove",
)
_DEZENAS = {
    20: "vinte",
    30: "trinta",
    40: "quarenta",
    50: "cinquenta",
    60: "sessenta",
    70: "setenta",
    80: "oitenta",
    90: "noventa",
}
_CENTENAS = {
    100: "cem",
    200: "duzentos",
    300: "trezentos",
    400: "quatrocentos",
    500: "quinhentos",
    600: "seiscentos",
    700: "setecentos",
    800: "oitocentos",
    900: "novecentos",
}


def sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in decomposto if not unicodedata.combining(c))


def por_extenso(numero: int) -> set[str]:
    """As grafias em que um inteiro pode aparecer num edital.

    Não é um conversor completo — é o suficiente para os números que
    aparecem em cláusula: percentual, prazo em dias, vigência em meses,
    anos de impedimento. Acima de mil, o dígito é a forma usada.
    """
    if numero < 0:
        return set()
    if numero < 20:
        return {_UNIDADES[numero]}
    if numero in _DEZENAS:
        return {_DEZENAS[numero]}
    if numero < 100:
        dezena, unidade = divmod(numero, 10)
        if unidade:
            return {f"{_DEZENAS[dezena * 10]} e {_UNIDADES[unidade]}"}
        return {_DEZENAS[numero]}
    if numero in _CENTENAS:
        # "cem por cento" e "cento e cinquenta" convivem: as duas grafias
        # da centena redonda entram, e a busca aceita qualquer uma.
        return {_CENTENAS[numero], "cento"} if numero == 100 else {_CENTENAS[numero]}
    if numero == 1000:
        return {"mil"}
    return set()


def _formas(token: str) -> set[str]:
    formas = {token}
    # 1.000 e 1000 são o mesmo número escrito por dois órgãos diferentes.
    if "," not in token:
        formas.add(token.replace(".", ""))
    # "11.1.15" não é um número, é a numeração da cláusula: não tem extenso
    with contextlib.suppress(ValueError):
        formas |= por_extenso(int(token.replace(".", "")))
    if token in {"0,5", "0.5"}:
        formas.add("meio")
    return formas


def extrair(texto: str) -> list[str]:
    """Os números citados no texto, na ordem, sem repetição."""
    vistos: list[str] = []
    for achado in _TOKEN.findall(texto):
        if achado not in vistos:
            vistos.append(achado)
    return vistos


def nao_sustentados(afirmacao: str, trecho: str) -> list[str]:
    """Os números da afirmação que não aparecem no trecho citado.

    Lista vazia é o caso bom: ou a afirmação não cita número, ou todos os
    que ela cita estão na evidência.
    """
    alvo = sem_acento(trecho)
    tokens_do_trecho = set(extrair(trecho))
    ausentes: list[str] = []

    for numero in extrair(afirmacao):
        formas = _formas(numero)
        # o dígito precisa bater como token inteiro; o extenso, como palavra
        casou = bool(formas & tokens_do_trecho) or any(
            re.search(rf"(?<![a-z]){re.escape(forma)}(?![a-z])", alvo)
            for forma in formas
            if forma.isalpha() or " " in forma
        )
        if not casou:
            ausentes.append(numero)

    return ausentes
