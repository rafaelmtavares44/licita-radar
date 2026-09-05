"""Os arquivos do edital: descobrir quais existem e trazer para o disco.

Aqui muda a API. A de consultas (`/api/consulta`), que sustenta a
ingestão, só devolve metadados da contratação — objeto, órgão, valor,
prazo. O edital em si, o termo de referência, os anexos, tudo isso vive na
API de integração (`/api/pncp`), noutra base e com outro formato de rota:

``GET /v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/arquivos``
    A lista dos documentos daquela contratação.

``GET /v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/arquivos/{sequencial_doc}``
    O arquivo, com redirecionamento para o armazenamento.

A chave para montar as duas é o `numeroControlePNCP`, que parece um código
opaco e não é: ele carrega o CNPJ do órgão, o sequencial da compra e o ano,
nesta ordem. Decompô-lo é o que liga o que já temos no banco ao que
precisamos baixar — não há endpoint de "arquivos por número de controle".
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import httpx

from licita_radar.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

#: ``10825373000155-1-000157/2026`` — CNPJ, código da fonte, sequencial, ano.
_NUMERO_CONTROLE = re.compile(r"^(?P<cnpj>\d{14})-(?P<fonte>\d+)-(?P<seq>\d+)/(?P<ano>\d{4})$")

#: Extensões que sabemos ler. O resto é baixado e registrado, mas não entra
#: na análise — melhor dizer "não sei ler .dwg" do que analisar meio edital
#: em silêncio.
EXTENSOES_LEGIVEIS = frozenset({".pdf", ".txt", ".docx", ".doc", ".rtf", ".zip"})


class ErroDocumentos(RuntimeError):
    """Não foi possível listar ou baixar os arquivos da contratação."""


@dataclass(frozen=True)
class Coordenadas:
    """O endereço de uma contratação na API de integração."""

    cnpj: str
    ano: int
    sequencial: int

    @property
    def rota_arquivos(self) -> str:
        return f"/v1/orgaos/{self.cnpj}/compras/{self.ano}/{self.sequencial}/arquivos"


def decompor(numero_controle: str) -> Coordenadas:
    """Quebra o `numeroControlePNCP` nas partes que a rota de arquivos pede.

    O sequencial vem zerado à esquerda (``000157``) e a rota quer o número
    (``157``): passar a string com zeros devolve 404, e um 404 aqui é
    indistinguível de "esta contratação não tem anexos".
    """
    achado = _NUMERO_CONTROLE.match(numero_controle.strip())
    if not achado:
        raise ErroDocumentos(
            f"número de controle fora do formato esperado: {numero_controle!r}\n"
            "esperado algo como 10825373000155-1-000157/2026"
        )
    return Coordenadas(
        cnpj=achado["cnpj"],
        ano=int(achado["ano"]),
        sequencial=int(achado["seq"]),
    )


@dataclass(frozen=True)
class Documento:
    """Um arquivo anexo à contratação, ainda no servidor."""

    sequencial: int
    titulo: str
    tipo: str | None
    url: str | None
    extensao: str

    @property
    def legivel(self) -> bool:
        return self.extensao in EXTENSOES_LEGIVEIS

    def nome_local(self, numero_controle: str) -> str:
        """Nome de arquivo previsível e seguro para qualquer sistema.

        O título vem do órgão e já apareceu com barra, dois-pontos e aspas
        — no Windows, qualquer um deles derruba a gravação.
        """
        base = re.sub(r"[^\w.\- ]+", "_", self.titulo).strip()[:80] or "documento"
        chave = numero_controle.replace("/", "-")
        return f"{chave}_{self.sequencial:02d}_{base}{self.extensao}"


@dataclass(frozen=True)
class DocumentoBaixado:
    documento: Documento
    caminho: Path
    bytes_gravados: int

    def como_dict(self) -> dict[str, Any]:
        """A forma que vai para o estado do grafo e para o banco."""
        return {
            "sequencial": self.documento.sequencial,
            "titulo": self.documento.titulo,
            "tipo": self.documento.tipo,
            "extensao": self.documento.extensao,
            "caminho": str(self.caminho),
            "bytes": self.bytes_gravados,
        }


def _extensao_de(titulo: str, tipo: str | None, url: str | None) -> str:
    """A extensão real do arquivo, procurada em três lugares.

    Nenhum dos três é confiável sozinho: o título às vezes vem sem
    extensão, o `tipoDocumentoNome` descreve o papel do documento ("Edital",
    "Termo de Referência") e não o formato, e a URL nem sempre existe.
    """
    for candidato in (titulo, url or ""):
        sufixo = Path(candidato.split("?")[0]).suffix.lower()
        if sufixo and len(sufixo) <= 5:
            return sufixo
    if tipo and "pdf" in tipo.lower():
        return ".pdf"
    return ""


def interpretar_lista(dados: Any) -> list[Documento]:
    """Lê a lista de arquivos tolerando as variações de nome de campo.

    O PNCP agrega publicações de centenas de sistemas diferentes, e os
    nomes chegam em mais de uma grafia. Preferir um `.get` encadeado a um
    modelo rígido é o que evita perder o edital inteiro por causa de uma
    chave com nome diferente.
    """
    if not isinstance(dados, list):
        return []

    documentos: list[Documento] = []
    for i, bruto in enumerate(dados, start=1):
        if not isinstance(bruto, dict):
            continue
        titulo = str(
            bruto.get("titulo") or bruto.get("nomeArquivo") or bruto.get("nome") or f"anexo-{i}"
        ).strip()
        tipo = bruto.get("tipoDocumentoNome") or bruto.get("tipoDocumentoDescricao")
        url = bruto.get("url") or bruto.get("uri") or bruto.get("uriArquivo")
        sequencial = bruto.get("sequencialDocumento") or bruto.get("sequencial") or i

        # Documento excluído continua aparecendo na lista, com a marca.
        if bruto.get("statusAtivo") is False:
            continue

        documentos.append(
            Documento(
                sequencial=int(sequencial),
                titulo=titulo,
                tipo=str(tipo) if tipo else None,
                url=str(url) if url else None,
                extensao=_extensao_de(titulo, str(tipo) if tipo else None, str(url) if url else ""),
            )
        )
    return documentos


def ordenar_por_relevancia(documentos: list[Documento]) -> list[Documento]:
    """Edital primeiro, anexo de planilha por último.

    Um pregão costuma ter de 3 a 15 anexos, e o orçamento de leitura não
    dá para todos. A ordem aqui é a ordem em que o dinheiro é gasto, então
    ela precisa refletir onde a informação que importa costuma estar: o
    edital e o termo de referência dizem o que se compra e o que se exige;
    a planilha de composição de custos, não.
    """
    prioridades = (
        ("edital", 0),
        ("termo de referencia", 1),
        ("termo de referência", 1),
        ("projeto basico", 1),
        ("projeto básico", 1),
        ("aviso", 3),
        ("minuta", 4),
        ("contrato", 4),
        ("planilha", 6),
        ("orcamento", 6),
        ("orçamento", 6),
    )

    def peso(doc: Documento) -> tuple[int, int]:
        alvo = f"{doc.titulo} {doc.tipo or ''}".lower()
        for marca, valor in prioridades:
            if marca in alvo:
                return (valor, doc.sequencial)
        return (2, doc.sequencial)

    return sorted(documentos, key=peso)


class DocumentosPNCP:
    """Lista e baixa os anexos de uma contratação."""

    def __init__(
        self,
        settings: Settings | None = None,
        cliente: httpx.AsyncClient | None = None,
    ) -> None:
        self._s = settings or get_settings()
        self._cliente = cliente or httpx.AsyncClient(
            base_url=self._s.pncp_integracao_base_url,
            timeout=self._s.pncp_download_timeout_s,
            follow_redirects=True,
            headers={"User-Agent": "licita-radar/0.1"},
        )
        self._proprio_cliente = cliente is None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._proprio_cliente:
            await self._cliente.aclose()

    async def listar(self, numero_controle: str) -> list[Documento]:
        coord = decompor(numero_controle)
        try:
            resposta = await self._cliente.get(coord.rota_arquivos, headers={"Accept": "*/*"})
        except httpx.HTTPError as erro:
            raise ErroDocumentos(f"falha ao listar arquivos de {numero_controle}: {erro}") from erro

        if resposta.status_code in (httpx.codes.NOT_FOUND, httpx.codes.NO_CONTENT):
            logger.info("%s não tem arquivos publicados", numero_controle)
            return []
        if resposta.status_code >= 400:
            raise ErroDocumentos(
                f"o PNCP recusou a lista de arquivos de {numero_controle} "
                f"(HTTP {resposta.status_code}) em {coord.rota_arquivos}"
            )

        try:
            dados = resposta.json()
        except ValueError as erro:
            raise ErroDocumentos(
                f"a lista de arquivos de {numero_controle} não veio em JSON"
            ) from erro

        return ordenar_por_relevancia(interpretar_lista(dados))

    async def baixar(
        self, numero_controle: str, documento: Documento, destino: Path
    ) -> DocumentoBaixado:
        """Grava o arquivo em disco. Se já estiver lá, não baixa de novo.

        Edital não muda depois de publicado — quando muda, vira errata com
        outro sequencial. Por isso o cache pelo nome é seguro, e ele é o que
        torna reprocessar uma contratação barato.
        """
        coord = decompor(numero_controle)
        destino.mkdir(parents=True, exist_ok=True)
        caminho = destino / documento.nome_local(numero_controle)

        if caminho.exists() and caminho.stat().st_size > 0:
            logger.debug("já baixado: %s", caminho.name)
            return DocumentoBaixado(documento, caminho, caminho.stat().st_size)

        rota = f"{coord.rota_arquivos}/{documento.sequencial}"
        try:
            resposta = await self._cliente.get(rota, headers={"Accept": "*/*"})
            resposta.raise_for_status()
        except httpx.HTTPError as erro:
            raise ErroDocumentos(f"falha ao baixar {documento.titulo!r}: {erro}") from erro

        conteudo = resposta.content
        if len(conteudo) > self._s.documento_tamanho_max_mb * 1024 * 1024:
            raise ErroDocumentos(
                f"{documento.titulo!r} tem mais de "
                f"{self._s.documento_tamanho_max_mb} MB e foi recusado"
            )

        caminho.write_bytes(conteudo)
        logger.info("baixado %s (%d KB)", caminho.name, len(conteudo) // 1024)
        return DocumentoBaixado(documento, caminho, len(conteudo))


async def baixar_edital(
    numero_controle: str,
    *,
    destino: Path | None = None,
    maximo: int = 3,
    settings: Settings | None = None,
) -> list[DocumentoBaixado]:
    """Traz os documentos mais relevantes da contratação para o disco.

    Um anexo que falha não cancela os outros: meio edital lido vale mais
    que nenhum, e a planilha que não baixou raramente é a que continha a
    exigência de habilitação.
    """
    s = settings or get_settings()
    pasta = destino or (s.documentos_dir / numero_controle.replace("/", "-"))

    baixados: list[DocumentoBaixado] = []
    async with DocumentosPNCP(s) as cliente:
        documentos = await cliente.listar(numero_controle)
        legiveis = [doc for doc in documentos if doc.legivel]

        if documentos and not legiveis:
            formatos = ", ".join(sorted({d.extensao or "?" for d in documentos}))
            logger.warning("%s só tem anexos em formato não legível: %s", numero_controle, formatos)

        for documento in legiveis[:maximo]:
            try:
                baixados.append(await cliente.baixar(numero_controle, documento, pasta))
            except ErroDocumentos as erro:
                logger.warning("anexo pulado: %s", erro)

    return baixados
