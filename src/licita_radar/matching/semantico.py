"""Camada 2 do funil: similaridade de significado.

Existe porque a camada léxica é literal. O PNCP publica "confecção de
sistema informatizado", "solução tecnológica para gestão" e "fábrica de
software" para pedir a mesma coisa; nenhuma lista de palavras-chave cobre
todas as variações que 5.570 municípios inventam.

O embedding do perfil é calculado uma vez e reaproveitado. O das
contratações é calculado uma vez por contratação e guardado no banco —
reprocessar o mesmo edital não recalcula nada.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from licita_radar.config.perfil import Perfil
from licita_radar.ingest.modelos import Contratacao
from licita_radar.matching.encoder import Encoder, similaridade_cosseno
from licita_radar.matching.limpeza import texto_para_embedding

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TextoCodificado:
    numero_controle_pncp: str
    texto: str
    vetor: list[float]


class MotorSemantico:
    """Calcula e compara embeddings. Não sabe de banco nem de veredito."""

    def __init__(self, encoder: Encoder) -> None:
        self._encoder = encoder
        self._vetor_perfil: list[float] | None = None
        self._perfil_id: str | None = None

    @property
    def encoder(self) -> Encoder:
        return self._encoder

    def vetor_do_perfil(self, perfil: Perfil) -> list[float]:
        """Codifica a descrição do perfil, com cache por identificador."""
        if self._vetor_perfil is None or self._perfil_id != perfil.id:
            texto = f"{perfil.descricao} {' '.join(perfil.palavras_chave.positivas)}".strip()
            self._vetor_perfil = self._encoder.codificar([texto])[0]
            self._perfil_id = perfil.id
            logger.debug("perfil %s codificado em %d dimensões", perfil.id, len(self._vetor_perfil))
        return self._vetor_perfil

    def codificar_contratacoes(
        self, contratacoes: Sequence[Contratacao], *, lote: int = 32
    ) -> list[TextoCodificado]:
        """Codifica em lotes — chamar o modelo item a item é desperdício."""
        resultado: list[TextoCodificado] = []

        for inicio in range(0, len(contratacoes), lote):
            fatia = contratacoes[inicio : inicio + lote]
            textos = [
                texto_para_embedding(
                    c.objeto, c.payload.get("informacaoComplementar") if c.payload else None
                )
                for c in fatia
            ]
            vetores = self._encoder.codificar(textos)
            resultado.extend(
                TextoCodificado(numero_controle_pncp=c.numero_controle_pncp, texto=t, vetor=v)
                for c, t, v in zip(fatia, textos, vetores, strict=True)
            )

        return resultado

    def pontuar(self, perfil: Perfil, vetor_contratacao: Sequence[float]) -> float:
        return similaridade_cosseno(self.vetor_do_perfil(perfil), vetor_contratacao)
