"""Os números da afirmação contra os do trecho citado.

O caso que originou o módulo está no primeiro teste, com o texto real do
Aviso de Contratação Direta 1/2026 do IF/AL.
"""

from __future__ import annotations

from licita_radar.analise.numeros import e_referencia, extrair, nao_sustentados, por_extenso


class TestOCasoQueOriginouOModulo:
    """Citação verdadeira, número inventado — e a extração como cúmplice."""

    def test_multa_de_15_com_citacao_sem_numero_nenhum(self) -> None:
        # O que o pypdf devolveu no modo padrão: o "20 vinte" foi parar no
        # fim da linha e deixou uma lacuna no lugar do percentual.
        trecho = (
            "11.1.15. Multa de % ( por cento) sobre o valor estimado do(s) item(s) "
            "prejudicado(s) pela conduta do fornecedor"
        )
        afirmacao = "Multas podem chegar a 15 % do valor da contratação"

        assert nao_sustentados(afirmacao, trecho) == ["15"]

    def test_com_a_extracao_certa_o_numero_se_sustenta(self) -> None:
        # O mesmo trecho extraído com `extraction_mode="layout"`.
        trecho = (
            "11.1.15. Multa de 20% (vinte por cento) sobre o valor estimado do(s) item(s) "
            "prejudicado(s) pela conduta do fornecedor"
        )
        assert nao_sustentados("A multa chega a 20% do valor", trecho) == []

    def test_a_numeracao_da_clausula_nao_serve_de_prova(self) -> None:
        """ "11.1.15" contém "15", e isso não sustenta "15%" coisa nenhuma."""
        trecho = "11.1.15. Multa de % ( por cento) sobre o valor estimado"
        assert nao_sustentados("multa de 15%", trecho) == ["15"]


class TestNumeroPorExtenso:
    """Edital escreve número por extenso o tempo todo."""

    def test_dez_dias_uteis_sustenta_10_dias_uteis(self) -> None:
        trecho = (
            "7.27. O pagamento será efetuado no prazo máximo de até dez dias úteis, "
            "contados da finalização da liquidação da despesa"
        )
        afirmacao = "O pagamento deverá ser efetuado em até 10 dias úteis"

        assert nao_sustentados(afirmacao, trecho) == []

    def test_compostos_ate_noventa_e_nove(self) -> None:
        assert por_extenso(24) == {"vinte e quatro"}
        assert por_extenso(60) == {"sessenta"}
        assert nao_sustentados("prazo de 24 meses", "vigência de vinte e quatro meses") == []

    def test_cem_aceita_as_duas_grafias(self) -> None:
        assert nao_sustentados("100% do valor", "cem por cento do valor") == []
        assert nao_sustentados("100 dias", "cento e vinte dias") == []

    def test_meio_por_cento(self) -> None:
        assert nao_sustentados("multa de 0,5%", "multa de meio por cento ao dia") == []

    def test_extenso_nao_casa_dentro_de_outra_palavra(self) -> None:
        """ "seis" não pode ser encontrado dentro de "seiscentos"."""
        assert nao_sustentados("6 anos", "seiscentos reais") == ["6"]


class TestExtracaoDeNumeros:
    def test_pontuacao_faz_parte_do_token(self) -> None:
        assert extrair("11.1.15. Multa de 20% em 0,5 dias") == ["11.1.15", "20", "0,5"]

    def test_separador_de_milhar_nao_muda_o_numero(self) -> None:
        assert nao_sustentados("valor de 1.000 reais", "o valor de 1000 reais") == []

    def test_repeticao_conta_uma_vez(self) -> None:
        assert extrair("30 dias, prorrogável por mais 30 dias") == ["30"]


class TestQuandoNaoHaNumero:
    def test_afirmacao_sem_numero_nao_tem_o_que_conferir(self) -> None:
        assert nao_sustentados("O pagamento será por ordem bancária", "qualquer coisa") == []

    def test_varios_numeros_sem_apoio_saem_na_ordem(self) -> None:
        afirmacao = "impedimento de licitar por até 3 anos ou inidoneidade por até 6 anos"
        assert nao_sustentados(afirmacao, "11.1.16. Impedimento de licitar e contratar") == [
            "3",
            "6",
        ]


class TestNumeracaoNaoEValor:
    """Ponteiro para cláusula não é afirmação factual.

    Saiu do segundo edital real: a frase "Multa de 20% ... nos subitens
    11.1.1 a 11.1.12" afirma *um* número — os vinte por cento. Os outros
    dois são endereços, e cobrar prova deles acendia alarme amarelo na
    linha em que o número que importa estava certo.
    """

    TRECHO = (
        "11.1.15. Multa de 20% (vinte por cento) sobre o valor estimado do(s) item(s) "
        "prejudicado(s) pela conduta do fornecedor"
    )

    def test_subitens_citados_nao_precisam_de_prova(self) -> None:
        afirmacao = (
            "Multa de 20% sobre o valor do item em caso de infrações previstas "
            "nos subitens 11.1.1 a 11.1.12."
        )
        assert nao_sustentados(afirmacao, self.TRECHO) == []

    def test_dois_separadores_ja_denunciam_o_endereco(self) -> None:
        assert e_referencia("11.1.12")
        assert e_referencia("7.27.1")
        assert not e_referencia("65.001,91")  # dois separadores, mas é dinheiro
        assert not e_referencia("1.000.000")  # milhar vem em trincas
        assert not e_referencia("20")

    def test_a_palavra_ao_lado_denuncia_o_resto(self) -> None:
        assert e_referencia("14133", "conforme a Lei 14133 de 2021")
        assert e_referencia("7.2", "na forma do subitem 7.2 do edital")
        assert not e_referencia("7.2", "índice de liquidez de 7.2")

    def test_o_numero_que_importa_continua_sendo_cobrado(self) -> None:
        """A regra afrouxa endereço, não valor."""
        afirmacao = "Multa de 15% conforme os subitens 11.1.1 a 11.1.12."
        assert nao_sustentados(afirmacao, self.TRECHO) == ["15"]

    def test_o_caso_das_duas_horas_continua_pego(self) -> None:
        """O acerto de verdade do segundo edital: regra colada em trecho alheio."""
        trecho = (
            "É dever do fornecedor atualizar previamente as comprovações constantes "
            "do Sicaf para que estejam vigentes na data da abertura da sessão pública"
        )
        afirmacao = "Enviar documentos complementares em até 2 horas pode excluir empresas."
        assert nao_sustentados(afirmacao, trecho) == ["2"]
