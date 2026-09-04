"""Onde as camadas viram uma nota só, e a nota vira um veredito."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from licita_radar.config.perfil import Perfil
from licita_radar.ingest.modelos import Contratacao
from licita_radar.matching.lexical import (
    ResultadoLexical,
    avaliar_elegibilidade,
    pontuar_lexicalmente,
)


class Veredito(StrEnum):
    INELEGIVEL = "inelegivel"  # não passou nos filtros duros
    VETADA = "vetada"  # bateu numa palavra negativa
    ABAIXO_DO_LIMIAR = "abaixo_limiar"  # aderente demais de menos
    CANDIDATA = "candidata"  # merece o olho humano


@dataclass(frozen=True)
class Avaliacao:
    contratacao: Contratacao
    veredito: Veredito
    score_lexical: float = 0.0
    score_semantico: float = 0.0
    score_final: float = 0.0
    palavras_encontradas: tuple[str, ...] = ()
    motivo: str | None = None

    @property
    def alerta(self) -> bool:
        return self.veredito is Veredito.CANDIDATA


def avaliar(
    contratacao: Contratacao,
    perfil: Perfil,
    *,
    score_semantico: float = 0.0,
    com_semantica: bool = True,
    agora: datetime | None = None,
) -> Avaliacao:
    """Aplica o funil inteiro a uma contratação.

    A ordem é a tese do projeto: o que é barato de decidir é decidido antes.
    Uma licitação vetada por "toner" nunca chega a virar embedding, e muito
    menos a custar um token de LLM.
    """
    motivo_inelegivel = avaliar_elegibilidade(contratacao, perfil, agora=agora)
    if motivo_inelegivel:
        return Avaliacao(
            contratacao=contratacao,
            veredito=Veredito.INELEGIVEL,
            motivo=motivo_inelegivel,
        )

    lexical: ResultadoLexical = pontuar_lexicalmente(contratacao.objeto, perfil)
    if lexical.vetada:
        return Avaliacao(
            contratacao=contratacao,
            veredito=Veredito.VETADA,
            motivo=f"contém a palavra negativa '{lexical.vetada_por}'",
        )

    p = perfil.pontuacao

    if com_semantica:
        final = p.peso_lexical * lexical.score + p.peso_semantico * score_semantico
    else:
        # Sem a camada semântica, o peso dela não pode simplesmente sumir: com
        # peso_lexical de 0,35, o teto do score viraria 0,35 e o limiar de 0,72
        # ficaria inalcançável — nenhuma contratação jamais seria candidata.
        # Redistribuir devolve a escala de 0 a 1 ao que sobrou.
        final = lexical.score

    passou = final >= p.limiar_alerta
    veredito = Veredito.CANDIDATA if passou else Veredito.ABAIXO_DO_LIMIAR
    motivo = None if passou else f"score {final:.2f} < {p.limiar_alerta:.2f}"

    return Avaliacao(
        contratacao=contratacao,
        veredito=veredito,
        score_lexical=lexical.score,
        score_semantico=score_semantico,
        score_final=final,
        palavras_encontradas=lexical.encontradas,
        motivo=motivo,
    )


def explicar(avaliacao: Avaliacao) -> str:
    """Uma linha em português dizendo por que a licitação está onde está.

    Enquanto o M3 não traz a justificativa por LLM, é isto que aparece no
    terminal — e, honestamente, para a maioria dos casos já basta.
    """
    match avaliacao.veredito:
        case Veredito.INELEGIVEL | Veredito.VETADA:
            return f"descartada: {avaliacao.motivo}"
        case Veredito.ABAIXO_DO_LIMIAR:
            if avaliacao.palavras_encontradas:
                achadas = ", ".join(avaliacao.palavras_encontradas)
                return (
                    f"perto: casou com {achadas}, mas o score ficou em {avaliacao.score_final:.2f}"
                )
            return f"sem sinal léxico; semântica em {avaliacao.score_semantico:.2f}"
        case Veredito.CANDIDATA:
            if avaliacao.palavras_encontradas:
                achadas = ", ".join(avaliacao.palavras_encontradas)
                return f"casou com {achadas} · semântica {avaliacao.score_semantico:.2f}"
            return (
                f"sem palavra-chave, mas semanticamente próxima ({avaliacao.score_semantico:.2f})"
            )
