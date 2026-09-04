from __future__ import annotations

from licita_radar.ingest.modalidades import MODALIDADES_TI, Modalidade, rotular


def test_codigos_batem_com_a_tabela_do_pncp() -> None:
    assert Modalidade.PREGAO_ELETRONICO == 6
    assert Modalidade.DISPENSA_DE_LICITACAO == 8
    assert Modalidade.CONCORRENCIA_ELETRONICA == 4
    assert Modalidade.INEXIGIBILIDADE == 9
    assert Modalidade.CREDENCIAMENTO == 12


def test_todas_as_treze_tem_rotulo() -> None:
    assert len(Modalidade) == 13
    assert all(m.rotulo for m in Modalidade)


def test_padrao_de_ti_prioriza_pregao_e_dispensa() -> None:
    assert MODALIDADES_TI[0] == Modalidade.PREGAO_ELETRONICO
    assert Modalidade.DISPENSA_DE_LICITACAO in MODALIDADES_TI


def test_codigo_desconhecido_vira_texto_e_nao_excecao() -> None:
    """A tabela de domínio do PNCP pode crescer; ingestão não falha por rótulo."""
    assert rotular(99) == "Modalidade 99"
    assert rotular(6) == "Pregão — Eletrônico"
