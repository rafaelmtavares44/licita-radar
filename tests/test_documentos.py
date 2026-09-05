"""O cliente de arquivos do PNCP: decompor o número, listar, baixar."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from licita_radar.config.settings import Settings
from licita_radar.ingest.documentos import (
    Documento,
    DocumentosPNCP,
    ErroDocumentos,
    baixar_edital,
    decompor,
    interpretar_lista,
    ordenar_por_relevancia,
)

BASE = "https://pncp.exemplo.test/api/pncp"
NUMERO = "10825373000155-1-000157/2026"
ROTA = f"{BASE}/v1/orgaos/10825373000155/compras/2026/157/arquivos"


@pytest.fixture
def settings_docs(tmp_path: Path) -> Settings:
    return Settings(
        pncp_integracao_base_url=BASE,
        documentos_dir=tmp_path / "editais",
        pncp_download_timeout_s=10.0,
        database_url="postgresql://ninguem@localhost:1/inexistente",
    )


# ------------------------------------------------------------ decomposição


def test_decompor_extrai_as_tres_partes() -> None:
    coord = decompor(NUMERO)
    assert coord.cnpj == "10825373000155"
    assert coord.ano == 2026
    assert coord.sequencial == 157


def test_sequencial_perde_os_zeros_a_esquerda() -> None:
    """`000157` na rota devolve 404, e 404 aqui parece 'não tem anexo'."""
    assert decompor(NUMERO).rota_arquivos.endswith("/2026/157/arquivos")


def test_numero_fora_do_formato_diz_o_que_esperava() -> None:
    with pytest.raises(ErroDocumentos, match="10825373000155-1-000157/2026"):
        decompor("um-numero-qualquer")


# -------------------------------------------------------------- a listagem


def test_interpretar_aceita_grafias_diferentes_de_campo() -> None:
    docs = interpretar_lista(
        [
            {"sequencialDocumento": 1, "titulo": "Edital.pdf", "tipoDocumentoNome": "Edital"},
            {"sequencial": 2, "nomeArquivo": "anexo.docx"},
            {"nome": "planilha.xlsx", "uri": "https://x/y/planilha.xlsx"},
        ]
    )
    assert [d.titulo for d in docs] == ["Edital.pdf", "anexo.docx", "planilha.xlsx"]
    assert [d.extensao for d in docs] == [".pdf", ".docx", ".xlsx"]


def test_documento_excluido_nao_entra() -> None:
    docs = interpretar_lista(
        [
            {"sequencialDocumento": 1, "titulo": "Edital.pdf"},
            {"sequencialDocumento": 2, "titulo": "Edital-revogado.pdf", "statusAtivo": False},
        ]
    )
    assert len(docs) == 1


def test_extensao_vem_do_tipo_quando_o_titulo_nao_tem() -> None:
    docs = interpretar_lista([{"titulo": "Edital do Pregão", "tipoDocumentoNome": "Edital PDF"}])
    assert docs[0].extensao == ".pdf"


def test_em_dispensa_o_aviso_faz_o_papel_do_edital() -> None:
    """Anexos reais da dispensa 10825373000155-1-000157/2026 (IF/AL).

    A primeira versão procurava "edital" e mandava qualquer "aviso" para o
    quarto lugar. Como só os três primeiros são analisados, o documento que
    traz as regras de participação ficava de fora — na modalidade que é 36
    de cada 37 contratações abertas.
    """
    docs = interpretar_lista(
        [
            {
                "sequencialDocumento": 1,
                "titulo": "Minuta de Contrato.pdf",
                "tipoDocumentoNome": "Minuta do Contrato",
            },
            {
                "sequencialDocumento": 2,
                "titulo": "11/2026.pdf",
                "tipoDocumentoNome": "Estudo Técnico Preliminar",
            },
            {
                "sequencialDocumento": 3,
                "titulo": "06. ETP_158381-000011-2026.pdf",
                "tipoDocumentoNome": "Outros Documentos",
            },
            {
                "sequencialDocumento": 4,
                "titulo": "12/2026.pdf",
                "tipoDocumentoNome": "Termo de Referência",
            },
            {
                "sequencialDocumento": 5,
                "titulo": "1/2026.pdf",
                "tipoDocumentoNome": "Aviso de Contratação Direta",
            },
        ]
    )
    ordenados = ordenar_por_relevancia(docs)

    # o aviso é o documento-mestre da dispensa, e o TR diz o que se compra
    assert [d.sequencial for d in ordenados[:2]] == [5, 4]
    # o ETP justifica a compra para o controle interno; não decide disputa
    assert [d.sequencial for d in ordenados[-2:]] == [2, 3]


def test_titulo_inutil_nao_atrapalha_porque_o_tipo_decide() -> None:
    """ "12/2026.pdf" não diz nada; `tipoDocumentoNome` diz tudo."""
    docs = interpretar_lista(
        [
            {"sequencialDocumento": 1, "titulo": "9/2026.pdf"},
            {
                "sequencialDocumento": 2,
                "titulo": "8/2026.pdf",
                "tipoDocumentoNome": "Aviso de Contratação Direta",
            },
        ]
    )
    assert ordenar_por_relevancia(docs)[0].sequencial == 2


def test_etp_nao_casa_com_palavra_que_so_contem_as_letras() -> None:
    docs = interpretar_lista([{"sequencialDocumento": 1, "titulo": "Vetplan etc detalhado.pdf"}])
    # cairia em 5 se "etp" casasse solto; fica no peso padrão
    assert ordenar_por_relevancia(docs)[0].sequencial == 1


def test_edital_vem_antes_da_planilha() -> None:
    docs = interpretar_lista(
        [
            {"sequencialDocumento": 1, "titulo": "Planilha de custos.pdf"},
            {"sequencialDocumento": 2, "titulo": "Edital 07-2026.pdf"},
            {"sequencialDocumento": 3, "titulo": "Termo de Referência.pdf"},
        ]
    )
    ordenados = ordenar_por_relevancia(docs)
    assert [d.sequencial for d in ordenados] == [2, 3, 1]


def test_nome_local_sobrevive_a_titulo_com_barra() -> None:
    doc = Documento(
        sequencial=1, titulo='Edital 07/2026: "final"', tipo=None, url=None, extensao=".pdf"
    )
    nome = doc.nome_local(NUMERO)
    assert "/" not in nome and '"' not in nome
    assert nome.endswith(".pdf")


@pytest.mark.asyncio
@respx.mock
async def test_listar_devolve_documentos_ordenados(settings_docs: Settings) -> None:
    respx.get(ROTA).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"sequencialDocumento": 1, "titulo": "Anexo I - planilha.pdf"},
                {"sequencialDocumento": 2, "titulo": "Edital.pdf"},
            ],
        )
    )
    async with DocumentosPNCP(settings_docs) as cliente:
        docs = await cliente.listar(NUMERO)

    assert [d.sequencial for d in docs] == [2, 1]


@pytest.mark.asyncio
@respx.mock
async def test_404_significa_sem_anexo_e_nao_erro(settings_docs: Settings) -> None:
    """Contratação sem arquivo publicado é rotina — não é falha."""
    respx.get(ROTA).mock(return_value=httpx.Response(404))
    async with DocumentosPNCP(settings_docs) as cliente:
        assert await cliente.listar(NUMERO) == []


@pytest.mark.asyncio
@respx.mock
async def test_500_vira_erro_com_a_rota_na_mensagem(settings_docs: Settings) -> None:
    respx.get(ROTA).mock(return_value=httpx.Response(500))
    async with DocumentosPNCP(settings_docs) as cliente:
        with pytest.raises(ErroDocumentos, match="/2026/157/arquivos"):
            await cliente.listar(NUMERO)


# --------------------------------------------------------------- o download


@pytest.mark.asyncio
@respx.mock
async def test_baixa_e_reaproveita_o_arquivo_do_disco(settings_docs: Settings) -> None:
    lista = respx.get(ROTA).mock(
        return_value=httpx.Response(200, json=[{"sequencialDocumento": 2, "titulo": "Edital.pdf"}])
    )
    arquivo = respx.get(f"{ROTA}/2").mock(
        return_value=httpx.Response(200, content=b"%PDF-1.4 conteudo")
    )

    primeiros = await baixar_edital(NUMERO, settings=settings_docs)
    assert len(primeiros) == 1
    assert primeiros[0].caminho.read_bytes() == b"%PDF-1.4 conteudo"

    segundos = await baixar_edital(NUMERO, settings=settings_docs)

    assert lista.call_count == 2  # a lista é sempre conferida
    assert arquivo.call_count == 1  # o arquivo, não: edital publicado não muda
    assert segundos[0].caminho == primeiros[0].caminho


@pytest.mark.asyncio
@respx.mock
async def test_anexo_ilegivel_e_ignorado(settings_docs: Settings) -> None:
    respx.get(ROTA).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"sequencialDocumento": 1, "titulo": "planta.dwg"},
                {"sequencialDocumento": 2, "titulo": "Edital.pdf"},
            ],
        )
    )
    respx.get(f"{ROTA}/2").mock(return_value=httpx.Response(200, content=b"x"))

    baixados = await baixar_edital(NUMERO, settings=settings_docs)
    assert [b.documento.extensao for b in baixados] == [".pdf"]


@pytest.mark.asyncio
@respx.mock
async def test_um_anexo_que_falha_nao_derruba_os_outros(settings_docs: Settings) -> None:
    respx.get(ROTA).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"sequencialDocumento": 1, "titulo": "Edital.pdf"},
                {"sequencialDocumento": 2, "titulo": "Termo de Referência.pdf"},
            ],
        )
    )
    respx.get(f"{ROTA}/1").mock(return_value=httpx.Response(500))
    respx.get(f"{ROTA}/2").mock(return_value=httpx.Response(200, content=b"ok"))

    baixados = await baixar_edital(NUMERO, settings=settings_docs)
    assert len(baixados) == 1
    assert baixados[0].documento.sequencial == 2


@pytest.mark.asyncio
@respx.mock
async def test_arquivo_grande_demais_e_recusado(settings_docs: Settings) -> None:
    pequeno = settings_docs.model_copy(update={"documento_tamanho_max_mb": 1})
    respx.get(ROTA).mock(
        return_value=httpx.Response(200, json=[{"sequencialDocumento": 1, "titulo": "Edital.pdf"}])
    )
    respx.get(f"{ROTA}/1").mock(return_value=httpx.Response(200, content=b"x" * (2 * 1024 * 1024)))

    assert await baixar_edital(NUMERO, settings=pequeno) == []
