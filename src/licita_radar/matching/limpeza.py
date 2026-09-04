"""Tira a casca burocrática do objeto antes de compará-lo com o perfil.

Isto não saiu de teoria: saiu de olhar 37 contratações reais de Goiás
capturadas em 04/09/2026. O que o PNCP devolve como "objeto" quase nunca
começa pelo objeto:

    DESPESA REFERENTE A COMPRA DE PEÇA PARA A MANUTENÇÃO CORRETIVA...
    SOLICITAÇÃO DE CONTRATAÇÃO DE EMPRESA ESPECIALIZADA PARA O FORNECIMENTO...
    1 -TERMO DE SOLICITAÇÃO SOLICITAÇÃO DE AQUISIÇÃO DE MOBILIARIO EM GERAL...
    [Portal de Compras Públicas] - DISPENSA - Contratação de empresa...
    DOCUMENTO DE FORMALIZAÇÃO DA DEMANDA - DFD REFERENTE A CONTRATAÇÃO...

Esse prefixo é idêntico em licitação de software e em licitação de picolé.
Deixá-lo no texto aproxima artificialmente coisas que não têm nada a ver, e
é exatamente o tipo de ruído que faz a busca semântica parecer burra.
"""

from __future__ import annotations

import re

#: Aplicados em cadeia, do início do texto, até nenhum casar mais. A ordem
#: importa pouco; o laço resolve encadeamentos como "SOLICITAÇÃO DE
#: CONTRATAÇÃO DE EMPRESA ESPECIALIZADA PARA...".
_PREFIXOS = (
    r"\[[^\]]{1,60}\]\s*[-–]?\s*",  # [Portal de Compras Públicas] -
    r"\d+\s*[-–]\s*termo de (solicita[çc][ãa]o|refer[êe]ncia)\s*",
    r"documento de formaliza[çc][ãa]o da demanda\s*[-–]?\s*dfd\s*",
    r"despesa (referente a|com)\s*",
    r"solicita[çc][ãa]o de\s*",
    r"dispensa\s*[-–]\s*",
    r"o presente termo tem como objeto a?\s*",
    r"esta (aquisi[çc][ãa]o|contrata[çc][ãa]o) visa (prover|atender)\s*",
    r"contrata[çc][ãa]o direta,? por dispensa de licita[çc][ãa]o,? de\s*",
    (
        r"contrata[çc][ãa]o de empresa especializada (para|em|na|no|do ramo de)?\s*"
        r"(a )?(realiza[çc][ãa]o de|presta[çc][ãa]o de|fornecimento d[eo])?\s*"
    ),
    r"presta[çc][ãa]o de servi[çc]os? (especializados?|de)\s*",
    r"refer[êe]nte ao m[êe]s de [^.]{0,20}\.\s*",
)

_REGEX = [re.compile(p, re.IGNORECASE) for p in _PREFIXOS]
_ESPACOS = re.compile(r"\s+")

#: Abaixo disto o objeto não diz nada de útil — "PRESTAÇÃO DE SERVIÇOS
#: ESPECIALIZADOS", sem complemento, apareceu duas vezes na amostra real.
TAMANHO_VAGO = 40


def limpar_objeto(texto: str) -> str:
    """Remove a casca. Se sobrar quase nada, devolve o original.

    A salvaguarda importa: em "PRESTAÇÃO DE SERVIÇOS ESPECIALIZADOS" o
    prefixo *é* o texto inteiro, e um objeto vazio quebraria o embedding.
    """
    limpo = _ESPACOS.sub(" ", texto).strip()

    mudou = True
    while mudou:
        mudou = False
        for regex in _REGEX:
            novo = regex.sub("", limpo, count=1).strip()
            if novo != limpo and len(novo) >= 12:
                limpo = novo
                mudou = True

    return limpo if len(limpo) >= 12 else _ESPACOS.sub(" ", texto).strip()


def objeto_e_vago(texto: str) -> bool:
    """O objeto é curto ou genérico demais para decidir qualquer coisa?

    Não descarta nada — só marca, para o alerta poder avisar "abra o edital,
    o objeto não diz o suficiente" em vez de fingir uma nota confiável.
    """
    limpo = limpar_objeto(texto)
    if len(limpo) < TAMANHO_VAGO:
        return True

    generico = {
        "prestacao de servicos especializados",
        "aquisicao de materiais",
        "aquisicao de material",
        "contratacao de empresa especializada",
        "servicos diversos",
    }
    from licita_radar.ingest.normalizar import sem_acento

    return sem_acento(limpo) in generico


def texto_para_embedding(objeto: str, informacao_complementar: str | None = None) -> str:
    """Monta o texto que vai virar vetor.

    Na amostra real, `informacaoComplementar` veio preenchida em 16 de 37
    registros — e, na maioria, **repetindo o objeto palavra por palavra**.
    Concatenar sempre seria duplicar ruído. Só somamos quando o objeto está
    vago e o complemento traz de fato algo novo.
    """
    limpo = limpar_objeto(objeto)

    if informacao_complementar and objeto_e_vago(objeto):
        complemento = limpar_objeto(informacao_complementar)
        if complemento and complemento.lower() not in limpo.lower():
            return f"{limpo}. {complemento}"[:2000]

    return limpo[:2000]
