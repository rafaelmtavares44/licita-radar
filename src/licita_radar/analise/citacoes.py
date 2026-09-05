"""Conferir se o trecho citado existe mesmo no edital.

Este é o módulo que justifica o resto do pacote. Um modelo de linguagem
resumindo um edital de 80 páginas acerta quase sempre — e o "quase" é o
problema: uma exigência de habilitação inventada é indistinguível de uma
real para quem não leu o documento, que é exatamente a pessoa que pediu o
resumo.

Pedir a citação no prompt não resolve sozinho, porque o modelo também
consegue inventar a citação. O que resolve é voltar ao texto original e
procurar. Uma afirmação cujo trecho não é encontrado não é apagada — ela é
rebaixada e marcada, porque "o modelo disse isto e eu não confirmei" é uma
informação melhor que o silêncio.

A comparação não pode ser literal: o modelo normaliza espaço, corrige o
hífen da quebra de linha, corta o meio com reticências. Por isso se
compara sequência de palavras normalizadas, aceitando sobreposição
parcial alta — rigoroso o bastante para pegar invenção, tolerante o
bastante para não reprovar transcrição honesta.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from licita_radar.analise import numeros

#: Tamanho do trecho indexado. Três palavras seguidas iguais é o menor
#: pedaço que ainda diz alguma coisa: com duas, "de acordo" casaria com
#: meio edital.
_N = 3

#: Trechos com menos palavras que isto não são evidência de nada, e para
#: eles se exige literalidade — é mais barato que discutir estatística de
#: coincidência em frase de cinco palavras.
_MINIMO_PARA_COBERTURA = 6

#: Proporção das palavras do trecho que precisa ser encontrada na fonte,
#: em sequências de pelo menos `_N`. Abaixo disso o "trecho" é paráfrase —
#: que pode até estar certa, mas não é prova.
_LIMIAR = 0.70


def normalizar(texto: str) -> str:
    """Reduz o texto ao que sobrevive a uma transcrição honesta."""
    sem_acento = unicodedata.normalize("NFKD", texto.lower())
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", sem_acento).strip()


@dataclass(frozen=True)
class Conferencia:
    confirmado: bool
    similaridade: float
    motivo: str


class Fonte:
    """O texto do edital preparado para busca, indexado uma vez só.

    O índice é montado no construtor porque a conferência roda uma vez por
    afirmação — de 10 a 30 por edital — e reconstruir a lista de palavras
    de 200 mil caracteres a cada uma transformaria a verificação no passo
    mais lento da análise.
    """

    def __init__(self, texto: str) -> None:
        self.palavras = normalizar(texto).split()
        self._indice: dict[tuple[str, ...], list[int]] = {}
        for i in range(len(self.palavras) - _N + 1):
            chave = tuple(self.palavras[i : i + _N])
            self._indice.setdefault(chave, []).append(i)

    def _tamanho_do_trecho(self, alvo: list[str], i: int, inicio: int) -> int:
        """Quantas palavras seguem iguais a partir daqui, nos dois lados."""
        n = 0
        while (
            i + n < len(alvo)
            and inicio + n < len(self.palavras)
            and alvo[i + n] == self.palavras[inicio + n]
        ):
            n += 1
        return n

    def _cobertura(self, alvo: list[str]) -> float:
        """Fração do trecho coberta por sequências longas achadas na fonte.

        Comparar posição a posição, como uma janela deslizante faria, parece
        natural e está errado: o modelo corta o meio da frase ("multa de
        0,5% […] por dia de atraso") e todo o resto do alinhamento anda,
        derrubando uma citação honesta. Somar as sequências encontradas, em
        ordem, tolera o corte sem tolerar a invenção — porque quem inventa
        não produz sequências longas do edital em ordem nenhuma.
        """
        cobertas = 0
        i = 0
        limite_esquerdo = 0  # a leitura anda para a frente, nunca volta

        while i <= len(alvo) - _N:
            melhor = 0
            melhor_inicio = 0
            for inicio in self._indice.get(tuple(alvo[i : i + _N]), ()):
                if inicio < limite_esquerdo:
                    continue
                tamanho = self._tamanho_do_trecho(alvo, i, inicio)
                if tamanho > melhor:
                    melhor, melhor_inicio = tamanho, inicio

            if melhor >= _N:
                cobertas += melhor
                limite_esquerdo = melhor_inicio + melhor
                i += melhor
            else:
                i += 1

        return cobertas / len(alvo)

    def conferir(self, trecho: str) -> Conferencia:
        alvo = normalizar(trecho).split()

        if not alvo:
            return Conferencia(False, 0.0, "citação vazia")

        if len(alvo) < _MINIMO_PARA_COBERTURA:
            achou = " ".join(alvo) in " ".join(self.palavras)
            return Conferencia(
                achou,
                1.0 if achou else 0.0,
                "citação curta encontrada" if achou else "citação curta demais e não localizada",
            )

        cobertura = self._cobertura(alvo)

        if cobertura >= 0.999:
            return Conferencia(True, 1.0, "citação encontrada")
        if cobertura >= _LIMIAR:
            return Conferencia(
                True, cobertura, f"citação encontrada com {cobertura:.0%} de aderência"
            )
        if cobertura > 0:
            return Conferencia(
                False, cobertura, f"o trecho só bate {cobertura:.0%} com o edital — é paráfrase"
            )
        return Conferencia(False, 0.0, "o trecho citado não existe no edital")


#: Em que pé ficou uma afirmação depois da conferência. São três, e não
#: dois, porque "a citação é real mas não fala do número que você afirmou"
#: é diferente tanto de "conferi" quanto de "inventou a citação".
Estado = Literal["sustentada", "numero_sem_apoio", "nao_encontrada"]


@dataclass
class Afirmacao:
    """Uma frase do resumo e a evidência que a sustenta."""

    assunto: str
    texto: str
    trecho: str
    #: A citação foi localizada no edital.
    confirmada: bool = False
    similaridade: float = 0.0
    observacao: str = ""
    #: Números que a frase afirma e a citação não contém.
    numeros_sem_apoio: list[str] = field(default_factory=list)

    @property
    def sustentada(self) -> bool:
        """A citação existe **e** cobre os números que a frase afirma."""
        return self.confirmada and not self.numeros_sem_apoio

    @property
    def estado(self) -> Estado:
        if not self.confirmada:
            return "nao_encontrada"
        return "numero_sem_apoio" if self.numeros_sem_apoio else "sustentada"

    def como_dict(self) -> dict[str, object]:
        return {
            "assunto": self.assunto,
            "texto": self.texto,
            "trecho": self.trecho,
            "confirmada": self.confirmada,
            "sustentada": self.sustentada,
            "estado": self.estado,
            "similaridade": round(self.similaridade, 3),
            "observacao": self.observacao,
            "numeros_sem_apoio": list(self.numeros_sem_apoio),
        }


def conferir_todas(afirmacoes: list[Afirmacao], texto_fonte: str) -> list[Afirmacao]:
    """Marca cada afirmação com o veredito da conferência.

    São duas perguntas, e a segunda só existe porque a primeira sozinha
    deixou passar um erro real: *a citação está no edital?* e *a citação
    fala do número que a frase afirma?*

    Nada é removido de propósito. Quem lê o resumo precisa poder distinguir
    "o edital diz isto, e aqui está onde" de "o modelo afirmou isto e eu
    não achei onde" — e as duas coisas juntas, sem marca, seriam pior que
    qualquer uma das duas separadas.
    """
    fonte = Fonte(texto_fonte)
    for afirmacao in afirmacoes:
        veredito = fonte.conferir(afirmacao.trecho)
        afirmacao.confirmada = veredito.confirmado
        afirmacao.similaridade = veredito.similaridade
        afirmacao.observacao = veredito.motivo

        if not veredito.confirmado:
            continue

        afirmacao.numeros_sem_apoio = numeros.nao_sustentados(afirmacao.texto, afirmacao.trecho)
        if afirmacao.numeros_sem_apoio:
            faltando = ", ".join(afirmacao.numeros_sem_apoio)
            afirmacao.observacao = (
                f"a citação é do edital, mas não contém {faltando} — "
                "confira esse número no documento antes de usar"
            )

    return afirmacoes


def taxa_de_confirmacao(afirmacoes: list[Afirmacao]) -> float:
    """A fração que se pode repetir para outra pessoa sem ressalva.

    Conta `sustentada`, não `confirmada`: uma afirmação com número que a
    citação não cobre é justamente a que alguém repetiria errado.
    """
    if not afirmacoes:
        return 0.0
    return sum(1 for a in afirmacoes if a.sustentada) / len(afirmacoes)
