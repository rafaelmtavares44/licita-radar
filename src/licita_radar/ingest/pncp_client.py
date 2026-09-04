"""Cliente da API pública de consultas do PNCP.

Base: ``https://pncp.gov.br/api/consulta`` — sem autenticação.

Dois endpoints sustentam a v0.1:

``/v1/contratacoes/proposta``
    Contratações com recebimento de proposta **ainda aberto**. É o
    endpoint do dia a dia: só interessa avisar sobre o que dá tempo de
    disputar.

``/v1/contratacoes/publicacao``
    Janela de datas de publicação. Serve para o backfill inicial e para
    gravar fixtures de teste.

O PNCP não documenta limite de requisições. Isso não é permissão para
martelar o servidor: tratamos como se houvesse, com concorrência baixa e
backoff exponencial.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncIterator, Sequence
from datetime import date
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from licita_radar.config.settings import Settings, get_settings
from licita_radar.ingest.modelos import Contratacao, PaginaPNCP
from licita_radar.ingest.normalizar import normalizar_pagina

logger = logging.getLogger(__name__)

#: Códigos em que insistir faz sentido. 4xx (fora 429) é erro nosso —
#: repetir só gasta o tempo de todo mundo.
_STATUS_RETENTAVEIS = frozenset({429, 500, 502, 503, 504})


class ErroPNCP(RuntimeError):
    """Falha ao conversar com o PNCP depois de esgotadas as tentativas."""


def _vale_retentar(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _STATUS_RETENTAVEIS
    return isinstance(exc, httpx.TransportError)


def _aaaammdd(dia: date) -> str:
    return dia.strftime("%Y%m%d")


class PNCPClient:
    """Fala HTTP com o PNCP. Não sabe o que é perfil, score ou alerta."""

    def __init__(self, settings: Settings | None = None, cliente: httpx.AsyncClient | None = None):
        self._s = settings or get_settings()
        self._cliente = cliente or httpx.AsyncClient(
            base_url=self._s.pncp_base_url,
            timeout=self._s.pncp_timeout_s,
            headers={
                "Accept": "application/json",
                "User-Agent": "licita-radar/0.1 (+https://github.com/SEU-USUARIO/licita-radar)",
            },
        )
        self._proprio_cliente = cliente is None
        self._semaforo = asyncio.Semaphore(self._s.pncp_concorrencia)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._proprio_cliente:
            await self._cliente.aclose()

    # ------------------------------------------------------------------
    # camada baixa: uma requisição, com retry e cache opcional
    # ------------------------------------------------------------------

    def _caminho_cache(self, rota: str, params: dict[str, Any]) -> Path:
        assinatura = json.dumps({"rota": rota, "params": params}, sort_keys=True)
        digest = hashlib.sha256(assinatura.encode()).hexdigest()[:16]
        return self._s.pncp_cache_dir / f"{digest}.json"

    async def _get(self, rota: str, params: dict[str, Any]) -> dict[str, Any]:
        cache = self._caminho_cache(rota, params) if self._s.pncp_cache_local else None
        if cache and cache.exists():
            logger.debug("cache local: %s", cache.name)
            dados: dict[str, Any] = json.loads(cache.read_text(encoding="utf-8"))
            return dados

        async with self._semaforo:
            async for tentativa in AsyncRetrying(
                stop=stop_after_attempt(self._s.pncp_max_tentativas),
                wait=wait_exponential_jitter(initial=1, max=30),
                retry=retry_if_exception(_vale_retentar),
                reraise=True,
            ):
                with tentativa:
                    resposta = await self._cliente.get(rota, params=params)
                    # 204 = sem conteúdo para os filtros dados. Não é erro.
                    if resposta.status_code == httpx.codes.NO_CONTENT:
                        return {"data": [], "paginasRestantes": 0, "empty": True}
                    resposta.raise_for_status()
                    corpo: dict[str, Any] = resposta.json()

        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(corpo, ensure_ascii=False), encoding="utf-8")
        return corpo

    # ------------------------------------------------------------------
    # camada alta: páginas e contratações
    # ------------------------------------------------------------------

    async def _paginar(self, rota: str, params: dict[str, Any]) -> AsyncIterator[PaginaPNCP]:
        pagina_atual = 1
        while True:
            corpo = await self._get(rota, {**params, "pagina": pagina_atual})
            pagina = PaginaPNCP.de_resposta(corpo)
            logger.debug(
                "%s página %d/%d — %d itens",
                rota,
                pagina.numero_pagina,
                pagina.total_paginas,
                len(pagina.data),
            )
            yield pagina

            if not pagina.tem_proxima or not pagina.data:
                return
            pagina_atual += 1

    async def contratacoes_com_proposta_aberta(
        self,
        *,
        modalidade: int,
        data_final: date,
        uf: str | None = None,
    ) -> AsyncIterator[Contratacao]:
        """Contratações cujo prazo de proposta ainda não encerrou.

        Atenção ao `data_final`: ele é o **fim do período de recebimento**,
        não "até quando eu quero olhar". Passar a data de hoje devolve o que
        encerra hoje — quase tudo já fechado. Para ver o que ainda dá tempo
        de disputar, `data_final` precisa estar no futuro.

        Descoberto na prática: com dataFinal = hoje, 31 de 37 contratações de
        Goiás voltaram com o prazo vencido.
        """
        params: dict[str, Any] = {
            "dataFinal": _aaaammdd(data_final),
            "codigoModalidadeContratacao": modalidade,
            "tamanhoPagina": self._s.pncp_tamanho_pagina,
        }
        if uf:
            params["uf"] = uf

        async for pagina in self._paginar("/v1/contratacoes/proposta", params):
            for contratacao in normalizar_pagina(pagina.data):
                yield contratacao

    async def contratacoes_por_publicacao(
        self,
        *,
        modalidade: int,
        data_inicial: date,
        data_final: date,
        uf: str | None = None,
    ) -> AsyncIterator[Contratacao]:
        """Contratações publicadas numa janela de datas — backfill."""
        params: dict[str, Any] = {
            "dataInicial": _aaaammdd(data_inicial),
            "dataFinal": _aaaammdd(data_final),
            "codigoModalidadeContratacao": modalidade,
            "tamanhoPagina": self._s.pncp_tamanho_pagina,
        }
        if uf:
            params["uf"] = uf

        async for pagina in self._paginar("/v1/contratacoes/publicacao", params):
            for contratacao in normalizar_pagina(pagina.data):
                yield contratacao


async def coletar(
    *,
    modalidades: Sequence[int],
    data_inicial: date,
    data_final: date,
    uf: str | None = None,
    apenas_abertas: bool = True,
    settings: Settings | None = None,
) -> list[Contratacao]:
    """Varre todas as modalidades pedidas e devolve o conjunto deduplicado.

    Uma modalidade que falhar não derruba as outras: o erro é registrado e
    a varredura continua. Meia ingestão vale mais que nenhuma.
    """
    encontradas: dict[str, Contratacao] = {}

    async with PNCPClient(settings) as cliente:
        for modalidade in modalidades:
            try:
                if apenas_abertas:
                    fluxo = cliente.contratacoes_com_proposta_aberta(
                        modalidade=modalidade, data_final=data_final, uf=uf
                    )
                else:
                    fluxo = cliente.contratacoes_por_publicacao(
                        modalidade=modalidade,
                        data_inicial=data_inicial,
                        data_final=data_final,
                        uf=uf,
                    )
                async for contratacao in fluxo:
                    encontradas[contratacao.numero_controle_pncp] = contratacao
            except (httpx.HTTPError, ValueError) as erro:
                logger.error("modalidade %s falhou e foi pulada: %s", modalidade, erro)

    return list(encontradas.values())
