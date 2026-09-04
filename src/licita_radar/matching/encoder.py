"""Quem transforma texto em vetor.

Isolado atrás de um `Protocol` por dois motivos práticos:

1. **Teste sem download.** A suíte roda com um encoder determinístico de
   mentira. Nenhum teste baixa 200 MB de pesos nem precisa de rede.
2. **Troca de modelo sem cirurgia.** Trocar o modelo é mexer numa
   configuração, não em cinco arquivos.

O padrão é `fastembed`, e não `sentence-transformers`, porque ele roda em
ONNX e **não arrasta o PyTorch junto** — a diferença entre uma instalação de
~200 MB e uma de ~2,5 GB. Para um projeto opensource, isso decide se a
pessoa termina de instalar ou desiste no meio.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

#: Multilíngue com português decente, 384 dimensões, ~220 MB.
#: Trocar por `intfloat/multilingual-e5-large` melhora a qualidade, mas custa
#: 2,2 GB e exige mudar `vector(384)` para `vector(1024)` na migração 002.
MODELO_PADRAO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DIMENSAO_PADRAO = 384


@runtime_checkable
class Encoder(Protocol):
    """Contrato mínimo: texto entra, vetor normalizado sai."""

    @property
    def nome(self) -> str: ...

    @property
    def dimensao(self) -> int: ...

    def codificar(self, textos: Sequence[str]) -> list[list[float]]: ...


class FastEmbedEncoder:
    """Encoder de produção. O modelo é baixado na primeira execução."""

    def __init__(self, modelo: str = MODELO_PADRAO, dimensao: int = DIMENSAO_PADRAO) -> None:
        self._nome = modelo
        self._dimensao = dimensao
        self._motor: object | None = None

    @property
    def nome(self) -> str:
        return self._nome

    @property
    def dimensao(self) -> int:
        return self._dimensao

    def _carregar(self) -> object:
        """Carrega sob demanda: importar fastembed custa segundos.

        Quem só quer rodar `licita-radar ingest` não deve pagar por isso.
        """
        if self._motor is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as erro:  # pragma: no cover
                raise RuntimeError(
                    "O matching semântico precisa do fastembed. "
                    'Instale com: pip install -e ".[semantico]"'
                ) from erro

            logger.info("carregando o modelo %s (baixa na primeira vez)", self._nome)
            self._motor = TextEmbedding(model_name=self._nome)
        return self._motor

    def codificar(self, textos: Sequence[str]) -> list[list[float]]:
        if not textos:
            return []
        motor = self._carregar()
        vetores: Iterable[object] = motor.embed(list(textos))  # type: ignore[attr-defined]
        return [[float(x) for x in vetor] for vetor in vetores]  # type: ignore[attr-defined]


def similaridade_cosseno(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosseno entre dois vetores, recortado em [0, 1].

    Os modelos usados aqui já devolvem vetores normalizados, mas normalizar
    de novo custa quase nada e evita um bug silencioso caso alguém troque o
    encoder por um que não normalize.
    """
    if len(a) != len(b) or not a:
        return 0.0

    produto = float(sum(x * y for x, y in zip(a, b, strict=True)))
    norma_a = float(sum(x * x for x in a)) ** 0.5
    norma_b = float(sum(y * y for y in b)) ** 0.5
    if norma_a == 0.0 or norma_b == 0.0:
        return 0.0

    cosseno: float = produto / (norma_a * norma_b)
    return max(0.0, min(1.0, cosseno))
