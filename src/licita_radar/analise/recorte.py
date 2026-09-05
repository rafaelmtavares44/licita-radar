"""Escolher, de 80 páginas, as 6 que respondem às perguntas.

Mandar o edital inteiro para o modelo é caro e, pior, é ruim: o que
importa fica diluído em minuta de contrato, planilha e declarações
padronizadas, e a atenção do modelo se dispersa junto.

O recorte aqui é deliberadamente burro — contagem de palavras-chave por
bloco, sem embedding, sem modelo. É o mesmo princípio do funil de
matching: a camada barata reduz o material antes que a cara encoste nele.
E ele é auditável: dá para olhar o bloco escolhido e entender por quê.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Cada assunto do resumo tem seu vocabulário. Os termos são os que
#: aparecem na Lei 14.133 e nos editais que a seguem — não sinônimos
#: genéricos, porque é o jargão que marca a seção certa.
TERMOS: dict[str, tuple[str, ...]] = {
    "objeto": ("objeto", "objeto da licitação", "contratação de", "aquisição de"),
    "habilitacao": (
        "habilitação",
        "qualificação técnica",
        "qualificação econômico",
        "atestado de capacidade",
        "acervo técnico",
        "regularidade fiscal",
        "documentos de habilitação",
        "cnd",
        "certidão negativa",
    ),
    "garantia": (
        "garantia de execução",
        "garantia contratual",
        "garantia da proposta",
        "seguro-garantia",
        "caução",
    ),
    "prazos": (
        "prazo de execução",
        "prazo de vigência",
        "prazo de entrega",
        "cronograma",
        "vigência do contrato",
        "sessão pública",
        "abertura das propostas",
    ),
    "penalidades": (
        "sanções",
        "penalidade",
        "multa",
        "impedimento de licitar",
        "declaração de inidoneidade",
        "advertência",
    ),
    "pagamento": ("pagamento", "medição", "nota fiscal", "reajuste", "repactuação"),
}

#: Marcas de início de seção num edital. A numeração ("7.1.", "12 -") é o
#: sinal mais confiável que existe nesses documentos: quase todo edital é
#: numerado, mesmo os mal formatados.
_CABECALHO = re.compile(
    r"^\s*(?:\d{1,2}(?:\.\d{1,2})*\s*[.\-–)]?\s+\S|"
    r"(?:CAPÍTULO|CLÁUSULA|ANEXO|SEÇÃO|TÍTULO)\b|"
    r"===== )",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Bloco:
    texto: str
    inicio: int
    pontos: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.pontos.values())


def _quebrar(texto: str, *, minimo: int = 400, maximo: int = 3000) -> list[str]:
    """Divide por cabeçalho de seção, respeitando um tamanho útil.

    Blocos pequenos demais perdem o contexto da exigência; grandes demais
    trazem carona. O agrupamento por `minimo` junta as seções curtas e o
    corte por `maximo` parte as quilométricas.
    """
    linhas = texto.split("\n")
    blocos: list[str] = []
    atual: list[str] = []

    def fechar() -> None:
        if atual:
            junto = "\n".join(atual).strip()
            if junto:
                blocos.append(junto)
            atual.clear()

    for linha in linhas:
        tamanho = sum(len(x) + 1 for x in atual)
        if (_CABECALHO.match(linha) and tamanho >= minimo) or tamanho >= maximo:
            fechar()
        atual.append(linha)
    fechar()
    return blocos


def _pontuar(bloco: str) -> dict[str, int]:
    baixo = bloco.lower()
    return {
        assunto: sum(baixo.count(termo) for termo in termos)
        for assunto, termos in TERMOS.items()
        if any(termo in baixo for termo in termos)
    }


def recortar(texto: str, *, orcamento: int = 24_000) -> str:
    """Devolve as partes do edital que falam do que o resumo precisa dizer.

    O começo do documento entra sempre: é onde ficam o objeto e o valor,
    e é a única parte cuja ausência o modelo não consegue contornar. O
    resto entra por pontuação, e o texto sai remontado na ordem original —
    trechos fora de ordem confundem o modelo tanto quanto uma pessoa.
    """
    texto = texto.strip()
    if len(texto) <= orcamento:
        return texto

    partes = _quebrar(texto)
    blocos: list[Bloco] = []
    posicao = 0
    for parte in partes:
        blocos.append(Bloco(texto=parte, inicio=posicao, pontos=_pontuar(parte)))
        posicao += len(parte)

    escolhidos: list[Bloco] = []
    gasto = 0

    # o preâmbulo, sempre
    if blocos:
        cabeca = blocos[0]
        escolhidos.append(cabeca)
        gasto += len(cabeca.texto)

    for bloco in sorted(blocos[1:], key=lambda b: (-b.total, b.inicio)):
        if bloco.total == 0:
            break
        if gasto + len(bloco.texto) > orcamento:
            continue
        escolhidos.append(bloco)
        gasto += len(bloco.texto)

    escolhidos.sort(key=lambda b: b.inicio)
    return "\n\n[…]\n\n".join(bloco.texto for bloco in escolhidos)


def assuntos_ausentes(texto: str) -> list[str]:
    """Assuntos sobre os quais o texto recortado não tem nada a dizer.

    Serve para o resumo poder responder "o edital não trata disso" com
    alguma base, em vez de deixar o modelo preencher o silêncio.
    """
    baixo = texto.lower()
    return [
        assunto for assunto, termos in TERMOS.items() if not any(termo in baixo for termo in termos)
    ]
