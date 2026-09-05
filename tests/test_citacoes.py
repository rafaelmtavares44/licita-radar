"""A conferência de citações — a defesa contra resumo inventado.

Cada teste aqui descreve uma forma de o modelo errar, e o que deve
acontecer com ela. É a suíte mais importante do M4: se ela passar por
engano, o projeto passa a afirmar coisas que o edital não diz.
"""

from __future__ import annotations

from licita_radar.analise.citacoes import (
    Afirmacao,
    Fonte,
    conferir_todas,
    normalizar,
    taxa_de_confirmacao,
)

EDITAL = """
7. DA HABILITAÇÃO TÉCNICA
7.1. A licitante deverá apresentar atestado de capacidade técnica, fornecido
por pessoa jurídica de direito público ou privado, comprovando a execução de
serviços de desenvolvimento de sistemas compatíveis com o objeto.
7.2. Será exigida a comprovação de capital social mínimo de 10% (dez por cento)
do valor estimado da contratação.

12. DAS SANÇÕES ADMINISTRATIVAS
12.1. Pelo atraso injustificado, será aplicada multa de 0,5% (cinco décimos por
cento) por dia de atraso, limitada a 10% do valor do contrato.
"""


def test_normalizar_ignora_acento_caixa_e_pontuacao() -> None:
    assert normalizar("Atestado de Capacidade Técnica!") == "atestado de capacidade tecnica"


def test_citacao_literal_e_confirmada() -> None:
    fonte = Fonte(EDITAL)
    veredito = fonte.conferir("comprovação de capital social mínimo de 10% (dez por cento)")
    assert veredito.confirmado
    assert veredito.similaridade == 1.0


def test_citacao_com_espacos_e_acentos_diferentes_ainda_e_confirmada() -> None:
    """Transcrição honesta não é literal: o PDF quebra linha e o modelo junta."""
    fonte = Fonte(EDITAL)
    veredito = fonte.conferir(
        "a licitante devera apresentar   atestado de capacidade tecnica,\nfornecido"
    )
    assert veredito.confirmado


def test_citacao_que_corta_o_meio_da_frase_e_confirmada() -> None:
    """O modelo elide "(cinco décimos por cento)"; as duas pontas são do edital.

    Comparar posição a posição reprovaria isto — o corte desloca todo o
    resto. Somar as sequências encontradas em ordem, não.
    """
    fonte = Fonte(EDITAL)
    veredito = fonte.conferir(
        "Pelo atraso injustificado, será aplicada multa de 0,5% por dia de atraso"
    )
    assert veredito.confirmado
    assert veredito.similaridade == 1.0


def test_exigencia_inventada_nao_passa() -> None:
    """O caso que justifica o módulo inteiro."""
    fonte = Fonte(EDITAL)
    veredito = fonte.conferir(
        "A licitante deverá comprovar certificação ISO 27001 válida na data da sessão."
    )
    assert not veredito.confirmado
    assert veredito.similaridade < 0.7


def test_parafrase_do_edital_tambem_nao_passa() -> None:
    """Pode até estar certa — mas paráfrase não é evidência, é opinião."""
    fonte = Fonte(EDITAL)
    veredito = fonte.conferir("exige-se que a empresa tenha experiência anterior comprovada")
    assert not veredito.confirmado


def test_citacao_vazia_e_recusada() -> None:
    assert not Fonte(EDITAL).conferir("   ").confirmado


def test_citacao_curta_demais_exige_literalidade() -> None:
    fonte = Fonte(EDITAL)
    assert fonte.conferir("multa de 0,5%").confirmado
    assert not fonte.conferir("garantia de 5%").confirmado


def test_conferir_todas_marca_cada_afirmacao() -> None:
    afirmacoes = [
        Afirmacao(
            assunto="habilitacao",
            texto="Pede atestado de capacidade técnica.",
            trecho="apresentar atestado de capacidade técnica, fornecido por pessoa jurídica",
        ),
        Afirmacao(
            assunto="garantia",
            texto="Exige seguro-garantia de 5%.",
            trecho="será exigida garantia de execução de 5% do valor do contrato",
        ),
    ]
    conferidas = conferir_todas(afirmacoes, EDITAL)

    assert conferidas[0].confirmada
    assert not conferidas[1].confirmada
    assert taxa_de_confirmacao(conferidas) == 0.5
    assert conferidas[1].observacao  # sempre há um motivo legível


def test_taxa_de_lista_vazia_nao_explode() -> None:
    assert taxa_de_confirmacao([]) == 0.0


class TestNumeroSemApoio:
    """Citação verdadeira não é o mesmo que afirmação sustentada.

    O caso real: o modelo citou "11.1.15. Multa de % ( por cento) sobre o
    valor estimado…" — frase que está mesmo no edital, porque a extração
    do PDF tinha jogado o "20 vinte" para o fim da linha — e afirmou que a
    multa era de 15%. A citação passou. O número era invenção.
    """

    TRECHO_COM_LACUNA = (
        "11.1.15. Multa de % ( por cento) sobre o valor estimado do(s) item(s) "
        "prejudicado(s) pela conduta do fornecedor, por qualquer das infrações"
    )
    EDITAL_COM_LACUNA = "Das sanções administrativas\n" + TRECHO_COM_LACUNA

    def test_estado_intermediario_nao_e_confirmado_nem_invencao(self) -> None:
        afirmacao = Afirmacao(
            assunto="penalidades",
            texto="Multas podem chegar a 15% do valor da contratação.",
            trecho=self.TRECHO_COM_LACUNA,
        )
        (conferida,) = conferir_todas([afirmacao], self.EDITAL_COM_LACUNA)

        assert conferida.confirmada  # a citação existe mesmo
        assert not conferida.sustentada  # mas não prova os 15%
        assert conferida.estado == "numero_sem_apoio"
        assert conferida.numeros_sem_apoio == ["15"]
        assert "15" in conferida.observacao

    def test_a_taxa_conta_o_que_da_para_repetir_sem_ressalva(self) -> None:
        edital = self.EDITAL_COM_LACUNA + "\n7.27. O pagamento em até dez dias úteis."
        afirmacoes = [
            Afirmacao(
                assunto="penalidades",
                texto="Multa de 15% do valor.",
                trecho=self.TRECHO_COM_LACUNA,
            ),
            Afirmacao(
                assunto="prazos",
                texto="Pagamento em até 10 dias úteis.",
                trecho="7.27. O pagamento em até dez dias úteis.",
            ),
        ]
        conferidas = conferir_todas(afirmacoes, edital)

        # as duas citações são reais; só uma sustenta o número que afirma
        assert all(a.confirmada for a in conferidas)
        assert taxa_de_confirmacao(conferidas) == 0.5

    def test_numero_escrito_por_extenso_na_fonte_sustenta_o_digito(self) -> None:
        edital = "7.27. O pagamento será efetuado no prazo máximo de até dez dias úteis."
        afirmacao = Afirmacao(
            assunto="prazos",
            texto="O pagamento deverá ser efetuado em até 10 dias úteis.",
            trecho="O pagamento será efetuado no prazo máximo de até dez dias úteis",
        )
        (conferida,) = conferir_todas([afirmacao], edital)

        assert conferida.sustentada
        assert conferida.estado == "sustentada"

    def test_citacao_inventada_nem_chega_a_conferir_numero(self) -> None:
        afirmacao = Afirmacao(
            assunto="garantia",
            texto="Exige garantia de 5%.",
            trecho="será exigida garantia de execução de 5% do valor do contrato",
        )
        (conferida,) = conferir_todas([afirmacao], self.EDITAL_COM_LACUNA)

        assert conferida.estado == "nao_encontrada"
        assert conferida.numeros_sem_apoio == []
