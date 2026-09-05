"""De arquivo para texto corrido.

PDF é o formato do edital brasileiro, e ele vem de duas maneiras muito
diferentes: gerado por editor de texto (tem camada de texto, extrair é
trivial) ou escaneado (é um álbum de fotos de páginas, e sem OCR não há
uma letra a extrair). A diferença não aparece no nome do arquivo nem no
`Content-Type` — só depois de tentar.

Este módulo trata isso como resultado, não como exceção: `Extracao` diz
quanto texto saiu, de quantas páginas, e se o arquivo parece escaneado.
Um edital escaneado que devolve string vazia em silêncio é a pior falha
possível aqui, porque o resumo seguinte sairia vazio sem dizer por quê.
"""

from __future__ import annotations

import logging
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

#: Abaixo disto por página, o PDF quase certamente é imagem. Uma página de
#: edital com texto de verdade passa de 800 caracteres com folga; o número
#: baixo é de propósito, para não acusar de escaneado um anexo que
#: legitimamente tem pouca coisa escrita.
_MINIMO_POR_PAGINA = 120


@dataclass
class Extracao:
    """O resultado de tentar ler um arquivo."""

    texto: str
    paginas: int = 0
    origem: str = ""
    escaneado: bool = False
    erro: str | None = None

    @property
    def vazia(self) -> bool:
        return not self.texto.strip()

    @property
    def caracteres(self) -> int:
        return len(self.texto)


@dataclass
class Leitura:
    """O texto de todos os anexos de uma contratação, junto."""

    texto: str = ""
    partes: list[Extracao] = field(default_factory=list)

    @property
    def escaneado(self) -> bool:
        """Todo o material legível é imagem — não há o que analisar."""
        return bool(self.partes) and all(p.escaneado or p.vazia for p in self.partes)

    @property
    def diagnostico(self) -> str:
        if not self.partes:
            return "nenhum documento foi lido"
        if self.escaneado:
            return "os documentos são digitalizados (imagem) e exigiriam OCR"
        lidas = sum(1 for p in self.partes if not p.vazia)
        return f"{lidas} de {len(self.partes)} documentos com texto · {len(self.texto)} caracteres"


def normalizar_texto(bruto: str) -> str:
    """Tira o ruído tipográfico do PDF sem tocar no conteúdo.

    Editais vêm cheios de hífen de quebra de linha, espaço duplicado e
    quebra no meio da frase. Isso não atrapalha só a leitura humana: um
    trecho citado pelo modelo precisa ser encontrável no texto original, e
    ele não será se o mesmo parágrafo existir em duas formatações.
    """
    texto = unicodedata.normalize("NFKC", bruto)
    texto = texto.replace("­", "").replace("﻿", "")
    # palavra-quebrada-no-fim-da-linha → palavra inteira
    texto = re.sub(r"(\w)-\n(\w)", r"\1\2", texto)
    texto = re.sub(r"[ \t ]+", " ", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


def _extrair_pdf(caminho: Path) -> Extracao:
    try:
        from pypdf import PdfReader
    except ImportError:
        return Extracao(
            texto="",
            origem=caminho.name,
            erro="pypdf não está instalado — rode: pip install -e '.[documentos]'",
        )

    try:
        leitor = PdfReader(str(caminho))
        paginas = [pagina.extract_text() or "" for pagina in leitor.pages]
    except Exception as erro:  # PDF corrompido, protegido por senha, truncado…
        return Extracao(texto="", origem=caminho.name, erro=f"PDF ilegível: {erro}")

    texto = normalizar_texto("\n\n".join(paginas))
    total = len(paginas) or 1
    escaneado = len(texto) < _MINIMO_POR_PAGINA * total

    if escaneado:
        logger.info(
            "%s parece digitalizado: %d caracteres em %d páginas",
            caminho.name,
            len(texto),
            total,
        )

    return Extracao(texto=texto, paginas=total, origem=caminho.name, escaneado=escaneado)


def _extrair_docx(caminho: Path) -> Extracao:
    """DOCX sem dependência nova: por baixo, é um zip com XML dentro."""
    try:
        with zipfile.ZipFile(caminho) as pacote:
            xml = pacote.read("word/document.xml").decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, KeyError, OSError) as erro:
        return Extracao(texto="", origem=caminho.name, erro=f"DOCX ilegível: {erro}")

    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    texto = normalizar_texto(re.sub(r"<[^>]+>", "", xml))
    return Extracao(texto=texto, paginas=0, origem=caminho.name)


def _extrair_txt(caminho: Path) -> Extracao:
    try:
        bruto = caminho.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Órgão público e Windows-1252 andam juntos com frequência.
        bruto = caminho.read_text(encoding="latin-1", errors="replace")
    except OSError as erro:
        return Extracao(texto="", origem=caminho.name, erro=str(erro))
    return Extracao(texto=normalizar_texto(bruto), origem=caminho.name)


def extrair(caminho: Path) -> Extracao:
    """Lê um arquivo. Nunca levanta exceção: devolve o erro no resultado.

    A extração roda no meio de um lote, e um anexo ilegível não pode
    derrubar a análise dos outros — nem sumir sem deixar rastro.
    """
    if not caminho.exists():
        return Extracao(texto="", origem=caminho.name, erro="arquivo não encontrado")

    sufixo = caminho.suffix.lower()
    if sufixo == ".pdf":
        return _extrair_pdf(caminho)
    if sufixo == ".docx":
        return _extrair_docx(caminho)
    if sufixo in {".txt", ".rtf"}:
        return _extrair_txt(caminho)
    return Extracao(
        texto="",
        origem=caminho.name,
        erro=f"não sei ler {sufixo or 'arquivo sem extensão'}",
    )


def _desempacotar(caminho: Path) -> list[Path]:
    """Zip é comum como "pacote do edital". Abre num diretório ao lado."""
    pasta = caminho.with_suffix("")
    try:
        with zipfile.ZipFile(caminho) as pacote:
            pasta.mkdir(parents=True, exist_ok=True)
            nomes = [
                n
                for n in pacote.namelist()
                # Um zip pode conter caminhos absolutos ou `..` e escrever
                # fora da pasta de destino. É antigo, tem nome (zip slip) e
                # continua funcionando em quem extrai sem olhar.
                if not n.endswith("/") and not Path(n).is_absolute() and ".." not in Path(n).parts
            ]
            pacote.extractall(pasta, members=nomes)
            return [pasta / nome for nome in nomes]
    except (zipfile.BadZipFile, OSError) as erro:
        logger.warning("zip ilegível %s: %s", caminho.name, erro)
        return []


def ler_documentos(caminhos: list[Path], *, limite_caracteres: int = 400_000) -> Leitura:
    """Extrai e concatena, na ordem dada, com o nome do arquivo como cabeçalho.

    O cabeçalho não é enfeite: quando o resumo citar um trecho, é ele que
    permite dizer *de qual anexo* a exigência veio — e "está no Anexo III"
    é metade da resposta que a pessoa procurava.
    """
    leitura = Leitura()
    pedacos: list[str] = []
    total = 0

    fila = list(caminhos)
    while fila:
        caminho = fila.pop(0)
        if caminho.suffix.lower() == ".zip":
            fila = _desempacotar(caminho) + fila
            continue

        extracao = extrair(caminho)
        leitura.partes.append(extracao)
        if extracao.vazia:
            continue

        cabecalho = f"\n\n===== {extracao.origem} =====\n"
        espaco = limite_caracteres - total - len(cabecalho)
        if espaco <= 0:
            logger.info("limite de %d caracteres atingido", limite_caracteres)
            break

        corpo = extracao.texto[:espaco]
        pedacos.append(cabecalho + corpo)
        total += len(cabecalho) + len(corpo)

    leitura.texto = "".join(pedacos).strip()
    return leitura
