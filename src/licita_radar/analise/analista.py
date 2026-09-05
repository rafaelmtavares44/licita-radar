"""O analista de editais: lê o recorte e devolve afirmações com evidência.

O contrato com o modelo é estreito de propósito. Ele não escreve um
relatório: ele preenche uma lista de afirmações curtas, e **cada uma
carrega o trecho literal do edital que a sustenta**. Depois de responder,
todo trecho volta ao texto original para conferência (`citacoes`).

Duas consequências dessa escolha aparecem no resultado:

* o que o modelo inventar fica marcado como não confirmado, em vez de se
  misturar ao que é verdade;
* o que o edital não diz tende a não ser dito, porque o modelo precisaria
  fabricar também a citação — o que é mais difícil que fabricar a frase.

Nenhuma das duas elimina o erro. As duas juntas fazem o erro ser visível,
que é o máximo que se pode prometer com honestidade.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from licita_radar.analise.citacoes import Afirmacao, conferir_todas, taxa_de_confirmacao
from licita_radar.analise.recorte import TERMOS, assuntos_ausentes, recortar
from licita_radar.llm import LLM, como_json

logger = logging.getLogger(__name__)

#: Os assuntos que o resumo cobre, na ordem em que uma pessoa os lê.
ASSUNTOS: tuple[str, ...] = (
    "objeto",
    "habilitacao",
    "garantia",
    "prazos",
    "pagamento",
    "penalidades",
    "riscos",
)

ROTULOS: dict[str, str] = {
    "objeto": "O que está sendo comprado",
    "habilitacao": "O que exigem para participar",
    "garantia": "Garantias exigidas",
    "prazos": "Prazos",
    "pagamento": "Pagamento",
    "penalidades": "Multas e sanções",
    "riscos": "Pontos de atenção",
}

SISTEMA_ANALISE = """Você lê editais de licitação pública brasileira e extrai o que uma \
empresa precisa saber para decidir se participa.

Responda SOMENTE com um objeto JSON, sem texto antes ou depois, neste formato:

{"resumo": "<dois a quatro períodos em português claro>",
 "afirmacoes": [{"assunto": "<um dos assuntos>", "texto": "<a informação, em uma frase>",
                 "trecho": "<a frase LITERAL do edital que prova isso>"}]}

Assuntos válidos: objeto, habilitacao, garantia, prazos, pagamento, penalidades, riscos.

Regras que não podem ser quebradas:
1. O campo "trecho" deve ser copiado LETRA POR LETRA do edital fornecido. \
Nunca reescreva, resuma ou traduza o trecho.
2. Se o edital não trata de um assunto, NÃO invente: simplesmente não gere \
afirmação para ele.
3. "texto" é sua explicação em linguagem simples; "trecho" é a prova. \
Nunca use o mesmo conteúdo genérico nos dois.
4. Em "riscos", aponte apenas exigências concretas que costumam eliminar \
empresas (atestado de acervo, capital mínimo, visita técnica obrigatória, \
garantia alta), sempre com o trecho.
5. Números, prazos e percentuais devem vir exatamente como aparecem no edital.
6. Se você afirmar um número, o "trecho" DEVE conter esse número. Quando o \
edital não informa o valor (aparece em branco, "___" ou "%" sozinho), diga que \
não está informado — nunca complete com um valor típico."""


def prompt_analise(*, objeto: str, edital: str, faltantes: list[str]) -> str:
    aviso = ""
    if faltantes:
        aviso = (
            "\nO recorte recebido não contém seção sobre: "
            + ", ".join(faltantes)
            + ". Não gere afirmações sobre esses assuntos.\n"
        )
    return (
        f"CONTRATAÇÃO: {objeto[:400]}\n"
        f"{aviso}\n"
        f'EDITAL (trechos):\n"""\n{edital}\n"""\n\n'
        "Extraia as afirmações com seus trechos literais."
    )


@dataclass
class Analise:
    """O resumo executivo do edital, com a evidência ao lado."""

    resumo: str = ""
    afirmacoes: list[Afirmacao] = field(default_factory=list)
    modelo: str | None = None
    tokens: int = 0
    caracteres_lidos: int = 0
    alertas: list[str] = field(default_factory=list)

    @property
    def confiabilidade(self) -> float:
        return taxa_de_confirmacao(self.afirmacoes)

    @property
    def confirmadas(self) -> list[Afirmacao]:
        return [a for a in self.afirmacoes if a.confirmada]

    @property
    def suspeitas(self) -> list[Afirmacao]:
        return [a for a in self.afirmacoes if not a.confirmada]

    def por_assunto(self, *, somente_confirmadas: bool = False) -> dict[str, list[Afirmacao]]:
        fonte = self.confirmadas if somente_confirmadas else self.afirmacoes
        agrupado: dict[str, list[Afirmacao]] = {}
        for afirmacao in fonte:
            agrupado.setdefault(afirmacao.assunto, []).append(afirmacao)
        return {assunto: agrupado[assunto] for assunto in ASSUNTOS if assunto in agrupado}

    def como_dict(self) -> dict[str, Any]:
        return {
            "resumo": self.resumo,
            "afirmacoes": [a.como_dict() for a in self.afirmacoes],
            "modelo": self.modelo,
            "tokens": self.tokens,
            "caracteres_lidos": self.caracteres_lidos,
            "confiabilidade": round(self.confiabilidade, 3),
            "alertas": self.alertas,
        }


def interpretar_resposta(bruto: str) -> tuple[str, list[Afirmacao]]:
    """Lê o JSON do modelo sem confiar em nenhum campo.

    Modelo pequeno erra o formato com frequência: manda string onde
    prometeu lista, inventa assunto fora da lista, esquece o trecho. Cada
    um desses casos vira descarte silencioso do item — nunca exceção, e
    nunca item pela metade entrando no resumo.
    """
    dados = como_json(bruto)
    if not dados:
        return "", []

    resumo = str(dados.get("resumo") or "").strip()
    itens = dados.get("afirmacoes")
    if not isinstance(itens, list):
        return resumo, []

    afirmacoes: list[Afirmacao] = []
    for item in itens:
        if not isinstance(item, dict):
            continue
        assunto = str(item.get("assunto") or "").strip().lower()
        texto = str(item.get("texto") or "").strip()
        trecho = str(item.get("trecho") or "").strip()

        if assunto not in TERMOS and assunto != "riscos":
            assunto = "riscos" if assunto else ""
        if not assunto or not texto or not trecho:
            continue

        afirmacoes.append(Afirmacao(assunto=assunto, texto=texto, trecho=trecho))
    return resumo, afirmacoes


async def analisar_edital(
    *,
    llm: LLM,
    objeto: str,
    texto_edital: str,
    orcamento_caracteres: int = 24_000,
    max_tokens: int = 1500,
) -> Analise:
    """Recorta, pergunta ao modelo e confere cada citação contra o original.

    A conferência usa o texto **completo**, não o recorte: o modelo às
    vezes cita uma frase que estava na borda do corte, e reprovar uma
    citação verdadeira por causa do nosso próprio recorte seria injusto
    com ele e enganoso com quem lê.
    """
    analise = Analise(caracteres_lidos=len(texto_edital))

    if not texto_edital.strip():
        analise.alertas.append("não havia texto para analisar")
        return analise

    if not llm.ativo:
        analise.alertas.append(
            "nenhum LLM configurado — a análise do edital exige um (veja LR_LLM_BASE_URL no .env)"
        )
        return analise

    recorte = recortar(texto_edital, orcamento=orcamento_caracteres)
    faltantes = assuntos_ausentes(recorte)

    try:
        resposta = await llm.responder(
            sistema=SISTEMA_ANALISE,
            usuario=prompt_analise(objeto=objeto, edital=recorte, faltantes=faltantes),
            max_tokens=max_tokens,
        )
    except Exception as erro:
        logger.warning("o modelo falhou ao analisar o edital: %s", erro)
        analise.alertas.append(f"o modelo falhou: {erro}")
        return analise

    analise.modelo = resposta.modelo
    analise.tokens = resposta.tokens

    resumo, afirmacoes = interpretar_resposta(resposta.texto)
    if not afirmacoes and not resumo:
        analise.alertas.append(
            "o modelo não devolveu JSON válido — tente um modelo maior ou aumente o teto de tokens"
        )
        return analise

    analise.resumo = resumo
    analise.afirmacoes = conferir_todas(afirmacoes, texto_edital)

    suspeitas = len(analise.suspeitas)
    if suspeitas:
        analise.alertas.append(
            f"{suspeitas} de {len(analise.afirmacoes)} afirmações citam trechos "
            "que não foram encontrados no edital"
        )
    if analise.afirmacoes and analise.confiabilidade < 0.5:
        analise.alertas.append(
            "menos da metade das citações confere: trate este resumo como rascunho"
        )

    return analise
