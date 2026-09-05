"""Dublês para a suíte. Nenhum teste baixa modelo nem toca a rede."""

from __future__ import annotations

from collections.abc import Sequence

from licita_radar.ingest.normalizar import sem_acento
from licita_radar.llm import Resposta

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


class LLMFalso:
    """Devolve respostas combinadas, na ordem. Nenhuma chamada de rede.

    Guarda os prompts recebidos: em vários testes o que importa não é a
    resposta, é *o que foi perguntado* — por exemplo, se o recorte enviado
    ao modelo cabia no orçamento de caracteres.
    """

    def __init__(self, *respostas: str, modelo: str = "duble/modelo", tokens: int = 42) -> None:
        self._respostas = list(respostas) or [""]
        self._modelo = modelo
        self._tokens = tokens
        self.prompts: list[str] = []
        self.chamadas = 0

    @property
    def modelo(self) -> str:
        return self._modelo

    @property
    def ativo(self) -> bool:
        return True

    async def responder(self, *, sistema: str, usuario: str, max_tokens: int = 600) -> Resposta:
        self.prompts.append(usuario)
        indice = min(self.chamadas, len(self._respostas) - 1)
        self.chamadas += 1
        return Resposta(texto=self._respostas[indice], modelo=self._modelo, tokens=self._tokens)


class LLMQueFalha:
    """Simula o provedor fora do ar."""

    @property
    def modelo(self) -> str:
        return "duble/quebrado"

    @property
    def ativo(self) -> bool:
        return True

    async def responder(self, *, sistema: str, usuario: str, max_tokens: int = 600) -> Resposta:
        raise RuntimeError("503 Service Unavailable")
