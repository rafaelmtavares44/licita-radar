"""O analista: recorte, contrato com o modelo e conferência das citações."""

from __future__ import annotations

import json

import pytest

from licita_radar.analise.analista import analisar_edital, interpretar_resposta
from licita_radar.analise.recorte import assuntos_ausentes, recortar
from licita_radar.llm import LLMDesligado
from tests.dubles import LLMFalso, LLMQueFalha

EDITAL = """
1. DO OBJETO
1.1. Contratação de empresa especializada na prestação de serviços de
desenvolvimento e sustentação de sistemas de informação, sob demanda.

7. DA HABILITAÇÃO
7.1. A licitante deverá apresentar atestado de capacidade técnica emitido por
pessoa jurídica de direito público ou privado.

9. DA GARANTIA
9.1. Será exigida garantia de execução contratual no percentual de 5% (cinco
por cento) do valor do contrato.

12. DAS SANÇÕES
12.1. Multa de 10% sobre o valor do contrato em caso de inexecução total.
"""


def _resposta(*afirmacoes: tuple[str, str, str], resumo: str = "Um resumo.") -> str:
    return json.dumps(
        {
            "resumo": resumo,
            "afirmacoes": [{"assunto": a, "texto": t, "trecho": c} for a, t, c in afirmacoes],
        },
        ensure_ascii=False,
    )


# ------------------------------------------------------------- interpretação


def test_json_em_cerca_de_markdown_ainda_e_lido() -> None:
    bruto = "```json\n" + _resposta(("objeto", "Serviços de TI.", "sistemas")) + "\n```"
    resumo, afirmacoes = interpretar_resposta(bruto)
    assert resumo == "Um resumo."
    assert len(afirmacoes) == 1


def test_item_sem_trecho_e_descartado() -> None:
    """Afirmação sem evidência não entra pela metade: não entra."""
    bruto = json.dumps(
        {"afirmacoes": [{"assunto": "garantia", "texto": "Exige 5%.", "trecho": ""}]}
    )
    _, afirmacoes = interpretar_resposta(bruto)
    assert afirmacoes == []


def test_assunto_fora_da_lista_cai_em_riscos() -> None:
    bruto = _resposta(("cronograma_fisico", "Entrega em fases.", "cronograma"))
    _, afirmacoes = interpretar_resposta(bruto)
    assert afirmacoes[0].assunto == "riscos"


def test_resposta_que_nao_e_json_nao_explode() -> None:
    assert interpretar_resposta("Claro! Aqui está o resumo do edital.") == ("", [])


def test_afirmacoes_em_formato_errado_nao_derrubam_o_resumo() -> None:
    bruto = json.dumps({"resumo": "Vale a pena.", "afirmacoes": "não é uma lista"})
    resumo, afirmacoes = interpretar_resposta(bruto)
    assert resumo == "Vale a pena."
    assert afirmacoes == []


# ------------------------------------------------------------------ recorte


def test_texto_curto_passa_inteiro() -> None:
    assert recortar(EDITAL, orcamento=100_000) == EDITAL.strip()


def test_recorte_cabe_no_orcamento_e_mantem_o_comeco() -> None:
    longo = EDITAL + ("\n\nblá blá irrelevante." * 4000)
    recorte = recortar(longo, orcamento=6_000)

    assert len(recorte) <= 6_000
    assert "DO OBJETO" in recorte


def test_recorte_prefere_as_secoes_com_o_vocabulario_que_importa() -> None:
    ruido = "\n\n30. DISPOSIÇÕES GERAIS\n" + ("texto genérico. " * 3000)
    recorte = recortar(EDITAL + ruido, orcamento=3_000)

    assert "garantia de execução" in recorte or "atestado de capacidade" in recorte


def test_assuntos_ausentes_lista_o_que_o_texto_nao_cobre() -> None:
    ausentes = assuntos_ausentes("Contratação de serviços de desenvolvimento de software.")
    assert "garantia" in ausentes
    assert "penalidades" in ausentes


# ------------------------------------------------------------- ponta a ponta


@pytest.mark.asyncio
async def test_afirmacao_verdadeira_e_confirmada_e_a_inventada_nao() -> None:
    """O teste que resume o M4 inteiro."""
    llm = LLMFalso(
        _resposta(
            (
                "habilitacao",
                "Pede atestado de capacidade técnica.",
                "A licitante deverá apresentar atestado de capacidade técnica emitido por",
            ),
            (
                "habilitacao",
                "Exige certificação ISO 27001.",
                "a licitante deverá comprovar certificação ISO 27001 vigente",
            ),
        )
    )

    analise = await analisar_edital(llm=llm, objeto="serviços de TI", texto_edital=EDITAL)

    assert len(analise.confirmadas) == 1
    assert analise.confirmadas[0].texto.startswith("Pede atestado")
    assert len(analise.suspeitas) == 1
    assert analise.confiabilidade == 0.5
    assert any("não foram encontrados" in a for a in analise.alertas)


@pytest.mark.asyncio
async def test_conferencia_usa_o_texto_inteiro_e_nao_o_recorte() -> None:
    """Reprovar citação verdadeira por causa do nosso próprio corte seria injusto."""
    enchimento = "\n\n30. DISPOSIÇÕES GERAIS\n" + ("prosa administrativa. " * 3000)
    llm = LLMFalso(
        _resposta(
            ("penalidades", "Multa de 10% por inexecução.", "Multa de 10% sobre o valor do"),
        )
    )

    analise = await analisar_edital(
        llm=llm,
        objeto="serviços de TI",
        texto_edital=EDITAL + enchimento,
        orcamento_caracteres=2_000,
    )

    # o modelo recebeu um recorte pequeno…
    assert len(llm.prompts) == 1
    assert len(llm.prompts[0]) < 4_000
    # …mas a citação foi conferida contra o edital inteiro
    assert len(analise.confirmadas) == 1


@pytest.mark.asyncio
async def test_sem_llm_a_analise_avisa_em_vez_de_fingir() -> None:
    analise = await analisar_edital(
        llm=LLMDesligado(), objeto="serviços de TI", texto_edital=EDITAL
    )
    assert analise.afirmacoes == []
    assert any("LLM" in a for a in analise.alertas)


@pytest.mark.asyncio
async def test_modelo_fora_do_ar_vira_alerta_e_nao_excecao() -> None:
    analise = await analisar_edital(llm=LLMQueFalha(), objeto="serviços de TI", texto_edital=EDITAL)
    assert any("503" in a for a in analise.alertas)


@pytest.mark.asyncio
async def test_texto_vazio_nem_chega_ao_modelo() -> None:
    llm = LLMFalso(_resposta(("objeto", "x", "y")))
    analise = await analisar_edital(llm=llm, objeto="serviços", texto_edital="   ")

    assert llm.chamadas == 0
    assert analise.alertas == ["não havia texto para analisar"]


@pytest.mark.asyncio
async def test_json_invalido_do_modelo_vira_alerta_acionavel() -> None:
    llm = LLMFalso("Desculpe, não consegui ler o edital.")
    analise = await analisar_edital(llm=llm, objeto="serviços", texto_edital=EDITAL)

    assert analise.afirmacoes == []
    assert any("JSON" in a for a in analise.alertas)


@pytest.mark.asyncio
async def test_resumo_com_tudo_inventado_avisa_para_tratar_como_rascunho() -> None:
    llm = LLMFalso(
        _resposta(
            ("garantia", "Exige 30% de garantia.", "garantia de execução no valor de 30%"),
            ("prazos", "Prazo de 5 anos.", "o prazo de vigência será de 60 meses"),
        )
    )
    analise = await analisar_edital(llm=llm, objeto="serviços", texto_edital=EDITAL)

    assert analise.confiabilidade == 0.0
    assert any("rascunho" in a for a in analise.alertas)
