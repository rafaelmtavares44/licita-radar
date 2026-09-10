"""Extração de texto: PDF com e sem camada de texto, DOCX, TXT, ZIP."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from licita_radar.analise.extracao import _desempacotar, extrair, ler_documentos, normalizar_texto

pypdf = pytest.importorskip("pypdf")


def _pdf_em_branco(caminho: Path, paginas: int = 3) -> Path:
    """Um PDF sem uma letra dentro — o que um edital escaneado parece.

    A diferença entre "digitalizado" e "gerado por editor" não aparece no
    nome do arquivo nem no Content-Type; só depois de tentar extrair.
    """
    escritor = pypdf.PdfWriter()
    for _ in range(paginas):
        escritor.add_blank_page(width=595, height=842)
    escritor.write(str(caminho))
    return caminho


def test_normalizar_junta_palavra_quebrada_por_hifen() -> None:
    assert "desenvolvimento" in normalizar_texto("desenvolvi-\nmento de sistemas")


def test_normalizar_colapsa_espaco_e_linhas_em_branco() -> None:
    assert normalizar_texto("a    b\n\n\n\nc") == "a b\n\nc"


def test_pdf_sem_texto_e_marcado_como_escaneado(tmp_path: Path) -> None:
    resultado = extrair(_pdf_em_branco(tmp_path / "edital.pdf"))

    assert resultado.escaneado
    assert resultado.vazia
    assert resultado.erro is None  # não é erro: é um fato sobre o arquivo
    assert resultado.paginas == 3


def test_pdf_corrompido_devolve_erro_e_nao_explode(tmp_path: Path) -> None:
    ruim = tmp_path / "quebrado.pdf"
    ruim.write_bytes(b"isto nao e um pdf")

    resultado = extrair(ruim)
    assert resultado.erro is not None
    assert resultado.vazia


def test_arquivo_inexistente_vira_erro_legivel(tmp_path: Path) -> None:
    assert extrair(tmp_path / "sumiu.pdf").erro == "arquivo não encontrado"


def test_extensao_desconhecida_diz_o_que_nao_sabe_ler(tmp_path: Path) -> None:
    planta = tmp_path / "planta.dwg"
    planta.write_bytes(b"\x00\x01")
    assert "dwg" in (extrair(planta).erro or "")


def test_txt_em_latin1_ainda_e_lido(tmp_path: Path) -> None:
    """Órgão público e Windows-1252 andam juntos com frequência."""
    arquivo = tmp_path / "aviso.txt"
    arquivo.write_bytes("Pregão Eletrônico nº 07/2026".encode("latin-1"))
    assert "Pregão" in extrair(arquivo).texto


def test_docx_e_lido_sem_dependencia_nova(tmp_path: Path) -> None:
    caminho = tmp_path / "termo.docx"
    with zipfile.ZipFile(caminho, "w") as pacote:
        pacote.writestr(
            "word/document.xml",
            "<w:document><w:body><w:p><w:r><w:t>Objeto: sistema de gestão"
            "</w:t></w:r></w:p></w:body></w:document>",
        )
    assert "Objeto: sistema de gestão" in extrair(caminho).texto


def test_zip_e_aberto_e_o_conteudo_lido(tmp_path: Path) -> None:
    dentro = tmp_path / "edital.txt"
    dentro.write_text("cláusula sétima da habilitação", encoding="utf-8")
    pacote = tmp_path / "pacote.zip"
    with zipfile.ZipFile(pacote, "w") as z:
        z.write(dentro, arcname="edital.txt")

    leitura = ler_documentos([pacote])
    assert "cláusula sétima" in leitura.texto


def test_zip_com_caminho_para_fora_nao_escreve_fora(tmp_path: Path) -> None:
    """Zip slip: caminho com `..` gravaria por cima de arquivo do usuário."""
    pacote = tmp_path / "malicioso.zip"
    with zipfile.ZipFile(pacote, "w") as z:
        z.writestr("../fora.txt", "nao deveria existir")

    ler_documentos([pacote])
    assert not (tmp_path / "fora.txt").exists()


def test_cabecalho_identifica_de_qual_anexo_veio_o_texto(tmp_path: Path) -> None:
    """ "Está no Anexo III" é metade da resposta que a pessoa procurava."""
    a = tmp_path / "edital.txt"
    b = tmp_path / "anexo3.txt"
    a.write_text("objeto da contratação", encoding="utf-8")
    b.write_text("planilha de custos", encoding="utf-8")

    leitura = ler_documentos([a, b])
    assert "===== edital.txt =====" in leitura.texto
    assert "===== anexo3.txt =====" in leitura.texto


def test_limite_de_caracteres_corta_sem_perder_o_primeiro(tmp_path: Path) -> None:
    a = tmp_path / "edital.txt"
    b = tmp_path / "anexo.txt"
    a.write_text("A" * 500, encoding="utf-8")
    b.write_text("B" * 500, encoding="utf-8")

    leitura = ler_documentos([a, b], limite_caracteres=300)
    assert "A" in leitura.texto
    assert "B" not in leitura.texto


def test_diagnostico_diz_quando_tudo_e_imagem(tmp_path: Path) -> None:
    leitura = ler_documentos([_pdf_em_branco(tmp_path / "escaneado.pdf")])

    assert leitura.escaneado
    assert "OCR" in leitura.diagnostico


def test_zip_com_caminho_longo_nao_derruba_o_pacote(tmp_path: Path) -> None:
    """O Windows corta o caminho em 260 caracteres.

    Um edital vem em zip com pastas aninhadas e nomes de anexo enormes.
    Com `extractall`, um único nome comprido levantava OSError e o
    projeto perdia o pacote inteiro — trinta anexos por causa de um.
    """
    fundo = "03 - Anexos do Termo de Referencia/03.1 - Anexo I do TR - " + "x" * 120 + ".txt"
    pacote = tmp_path / "edital.zip"
    with zipfile.ZipFile(pacote, "w") as z:
        z.writestr("Edital.pdf", "conteudo do edital")
        z.writestr(fundo, "conteudo do anexo")

    extraidos = _desempacotar(pacote)

    assert len(extraidos) == 2
    for arquivo in extraidos:
        assert arquivo.is_file()
        # o nome achatado ainda diz de onde veio
        assert len(arquivo.name) < 80
    assert any("Anexo I do TR" in a.name for a in extraidos)


def test_zip_slip_continua_barrado(tmp_path: Path) -> None:
    """Achatar não pode ter aberto a porta que a filtragem fechava."""
    pacote = tmp_path / "malicioso.zip"
    with zipfile.ZipFile(pacote, "w") as z:
        z.writestr("../fora.txt", "não deveria sair")
        z.writestr("dentro.txt", "ok")

    extraidos = _desempacotar(pacote)

    assert [a.name for a in extraidos] == ["01 dentro.txt"]
    assert not (tmp_path / "fora.txt").exists()
