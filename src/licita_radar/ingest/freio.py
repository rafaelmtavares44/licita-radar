"""Um freio que aprende com o 429.

O manual do PNCP não documenta limite de requisições, e por isso o cliente
foi escrito só com backoff por tentativa. Uma varredura nacional derrubou
essa suposição: a partir da página 16 o servidor passou a responder
429 em série, e o limite não é por requisição — é por cliente. Insistir na
mesma página não adianta, e a modalidade seguinte já começa punida.

O que este módulo faz é simples: mantém um intervalo mínimo entre chamadas,
**dobra** esse intervalo a cada 429 e o reduz devagar conforme as respostas
voltam a dar certo. É controle de fluxo do lado de cá, para não depender da
boa vontade de um servidor público.
"""

from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)


class Freio:
    """Espaça as requisições, e aperta quando o servidor reclama."""

    def __init__(
        self,
        *,
        intervalo_inicial_s: float = 0.2,
        intervalo_maximo_s: float = 8.0,
        sucessos_para_aliviar: int = 20,
    ) -> None:
        self._intervalo = intervalo_inicial_s
        self._inicial = intervalo_inicial_s
        self._maximo = intervalo_maximo_s
        self._sucessos_para_aliviar = sucessos_para_aliviar
        self._sucessos = 0
        self._proxima_livre = 0.0
        self._trava = asyncio.Lock()

    @property
    def intervalo_s(self) -> float:
        return self._intervalo

    async def aguardar(self) -> None:
        """Segura a chamada até o intervalo mínimo ter passado."""
        async with self._trava:
            agora = time.monotonic()
            espera = self._proxima_livre - agora
            if espera > 0:
                await asyncio.sleep(espera)
                agora = time.monotonic()
            self._proxima_livre = agora + self._intervalo

    def registrar_sucesso(self) -> None:
        """Depois de uma sequência boa, afrouxa um pouco."""
        self._sucessos += 1
        if self._sucessos >= self._sucessos_para_aliviar and self._intervalo > self._inicial:
            self._sucessos = 0
            self._intervalo = max(self._inicial, self._intervalo / 2)
            logger.debug("freio aliviado para %.2fs", self._intervalo)

    def registrar_bloqueio(self, retry_after_s: float | None = None) -> float:
        """Recebeu 429. Dobra o intervalo e diz quanto esperar agora.

        Se o servidor mandou `Retry-After`, ele manda — é a única informação
        confiável sobre quando vale a pena voltar.
        """
        self._sucessos = 0
        self._intervalo = min(self._maximo, max(self._inicial, self._intervalo * 2))
        pausa = retry_after_s if retry_after_s is not None else self._intervalo * 4
        logger.warning(
            "429 do PNCP: intervalo agora é %.2fs, pausando %.1fs", self._intervalo, pausa
        )
        return pausa

    async def penalizar(self, retry_after_s: float | None = None) -> None:
        pausa = self.registrar_bloqueio(retry_after_s)
        async with self._trava:
            self._proxima_livre = max(self._proxima_livre, time.monotonic() + pausa)
        await asyncio.sleep(pausa)


def ler_retry_after(valor: str | None) -> float | None:
    """`Retry-After` em segundos. Ignora o formato de data HTTP, raro aqui."""
    if not valor:
        return None
    try:
        segundos = float(valor)
    except ValueError:
        return None
    return max(0.0, min(segundos, 120.0))
