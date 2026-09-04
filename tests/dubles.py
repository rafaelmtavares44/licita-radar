"""Dublês para a suíte. Nenhum teste baixa modelo nem toca a rede."""

from __future__ import annotations

from collections.abc import Sequence

from licita_radar.ingest.normalizar import sem_acento

#: Vocabulário mínimo para o encoder falso. Não é um modelo — é uma
#: bag-of-words determinística que basta para exercitar a mecânica:
#: cosseno, cache do perfil, lotes, persistência.
VOCABULARIO = (
    "software",
    "sistema",
    "desenvolvimento",
    "informatica",
    "impressora",
    "toner",
    "veiculo",
    "manutencao",
    "obra",
    "alimento",
    "saude",
    "servico",
)


class EncoderFalso:
    """Vetor = presença das palavras do vocabulário. Sem download, sem rede."""

    def __init__(self, nome: str = "dubl e/vocabulario-fixo") -> None:
        self._nome = nome

    @property
    def nome(self) -> str:
        return self._nome

    @property
    def dimensao(self) -> int:
        return len(VOCABULARIO)

    def codificar(self, textos: Sequence[str]) -> list[list[float]]:
        vetores: list[list[float]] = []
        for texto in textos:
            alvo = sem_acento(texto)
            bruto = [1.0 if palavra in alvo else 0.0 for palavra in VOCABULARIO]
            norma = sum(bruto) ** 0.5
            vetores.append([x / norma for x in bruto] if norma else bruto)
        return vetores
