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

O manual do PNCP não documenta limite de requisições — mas ele existe. Uma
varredura nacional levou 429 a partir da página 16 e as modalidades
seguintes já nasceram bloqueadas: o limite é por cliente, não por rota.
Por isso, além do backoff por tentativa, há um `Freio` que espaça todas as
chamadas e aperta sozinho quando o servidor reclama.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential_jitter,
)

from licita_radar.config.settings import Settings, get_settings
from licita_radar.ingest.freio import Freio, ler_retry_after
from licita_radar.ingest.modelos import Contratacao, PaginaPNCP
from licita_radar.ingest.normalizar import normalizar_pagina

logger = logging.getLogger(__name__)

#: Códigos em que insistir faz sentido. 4xx (fora 429) é erro nosso —
#: repetir só gasta o tempo de todo mundo.
_STATUS_RETENTAVEIS = frozenset({429, 500, 502, 503, 504})

#: 429 merece mais paciência que os outros: não é falha, é o servidor
#: pedindo para diminuir o ritmo.
_TENTATIVAS_EXTRA_NO_429 = 4

#: Teto de tempo numa única página. Nove tentativas de 60s viraram 457
#: segundos numa modalidade que nunca ia responder — sete minutos e meio
#: gastos para descobrir o que os primeiros dois já diziam. Insistir tem
#: valor quando o servidor está pedindo calma; não tem, quando ele não
#: está respondendo.
_SEGUNDOS_NO_MAXIMO_POR_PAGINA = 180.0


class ErroPNCP(RuntimeError):
    """Falha ao conversar com o PNCP depois de esgotadas as tentativas."""


def _vale_retentar(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _STATUS_RETENTAVEIS
    return isinstance(exc, httpx.TransportError)


#: O que dizer para cada falha de transporte. A ordem importa: a primeira
#: que casar vence, então as classes mais específicas vêm antes.
_NOMES: tuple[tuple[type[BaseException], str], ...] = (
    (httpx.ConnectTimeout, "o PNCP não aceitou a conexão a tempo"),
    (httpx.ReadTimeout, "o PNCP não respondeu a tempo"),
    (httpx.WriteTimeout, "não deu para enviar o pedido a tempo"),
    (httpx.PoolTimeout, "a fila de conexões estourou o tempo"),
    (httpx.ConnectError, "não deu para conectar no PNCP"),
    (httpx.RemoteProtocolError, "o PNCP encerrou a conexão no meio"),
    (httpx.ReadError, "a conexão caiu durante a leitura"),
)


def descrever(erro: BaseException) -> str:
    """Uma frase sobre a falha que nunca sai vazia.

    `str(httpx.ReadTimeout())` é string vazia. O log saía como
    ``modalidade 6 falhou e foi pulada:`` e terminava ali — indistinguível
    de uma linha truncada, e sem nenhuma pista do que aconteceu. Um erro
    sem mensagem custa mais caro que erro nenhum: manda investigar o
    lugar errado.
    """
    if isinstance(erro, httpx.HTTPStatusError):
        return f"HTTP {erro.response.status_code} em {erro.request.url.path}"

    texto = str(erro).strip()
    primeira = texto.splitlines()[0] if texto else ""
    for classe, nome in _NOMES:
        if isinstance(erro, classe):
            return f"{nome} ({primeira})" if primeira else nome
    return primeira or type(erro).__name__


def _aaaammdd(dia: date) -> str:
    return dia.strftime("%Y%m%d")


class PNCPClient:
    """Fala HTTP com o PNCP. Não sabe o que é perfil, score ou alerta."""

    def __init__(
        self,
        settings: Settings | None = None,
        cliente: httpx.AsyncClient | None = None,
        freio: Freio | None = None,
        ao_paginar: Callable[[int, int], None] | None = None,
    ):
        #: Avisado a cada página que chega — (número, total). Quem varre o
        #: Brasil inteiro precisa mostrar que está andando dentro da
        #: modalidade, não só entre elas.
        self._ao_paginar = ao_paginar
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
        self._freio = freio or Freio(
            intervalo_inicial_s=self._s.pncp_intervalo_min_s,
            intervalo_maximo_s=self._s.pncp_intervalo_max_s,
        )

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

        tentativas = self._s.pncp_max_tentativas + _TENTATIVAS_EXTRA_NO_429

        async with self._semaforo:
            async for tentativa in AsyncRetrying(
                stop=stop_after_attempt(tentativas)
                | stop_after_delay(_SEGUNDOS_NO_MAXIMO_POR_PAGINA),
                wait=wait_exponential_jitter(initial=1, max=60),
                retry=retry_if_exception(_vale_retentar),
                reraise=True,
            ):
                with tentativa:
                    # o freio espaça as chamadas e segura mais quando apertado
                    await self._freio.aguardar()
                    resposta = await self._cliente.get(rota, params=params)

                    if resposta.status_code == httpx.codes.TOO_MANY_REQUESTS:
                        await self._freio.penalizar(
                            ler_retry_after(resposta.headers.get("Retry-After"))
                        )
                        resposta.raise_for_status()

                    # 204 = sem conteúdo para os filtros dados. Não é erro.
                    if resposta.status_code == httpx.codes.NO_CONTENT:
                        self._freio.registrar_sucesso()
                        return {"data": [], "paginasRestantes": 0, "empty": True}

                    resposta.raise_for_status()
                    self._freio.registrar_sucesso()
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
        total_conhecido = 0
        while True:
            # O aviso vem ANTES do pedido, não depois. Avisando depois, a
            # primeira página só aparece quando chega — e enquanto o PNCP
            # pensa, a tela fica sem nada para mostrar justamente no
            # momento em que a pessoa mais duvida que algo esteja vivo.
            if self._ao_paginar:
                self._ao_paginar(pagina_atual, total_conhecido)
            corpo = await self._get(rota, {**params, "pagina": pagina_atual})
            pagina = PaginaPNCP.de_resposta(corpo)
            total_conhecido = pagina.total_estimado
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


#: Nome de cada modalidade, para o log e a tela falarem a língua de quem
#: espera. "modalidade 6 falhou" não diz nada; "Pregão Eletrônico falhou"
#: diz que a maior fatia da coleta ficou de fora.
MODALIDADES: dict[int, str] = {
    1: "Leilão eletrônico",
    2: "Diálogo competitivo",
    3: "Concurso",
    4: "Concorrência eletrônica",
    5: "Concorrência presencial",
    6: "Pregão eletrônico",
    7: "Pregão presencial",
    8: "Dispensa de licitação",
    9: "Inexigibilidade",
    10: "Manifestação de interesse",
    11: "Pré-qualificação",
    12: "Credenciamento",
    13: "Leilão presencial",
}


def nome_da_modalidade(codigo: int) -> str:
    return MODALIDADES.get(codigo, f"modalidade {codigo}")


@dataclass(frozen=True)
class Pulada:
    """Uma modalidade que não terminou, e o que se sabe sobre isso."""

    modalidade: int
    motivo: str
    #: Quantas já tinham vindo antes de a falha interromper a paginação.
    trazidas: int = 0

    def __str__(self) -> str:
        return f"{nome_da_modalidade(self.modalidade)}: {self.motivo}"


@dataclass
class Coleta:
    """O resultado da varredura — e o que ficou faltando nela.

    Devolver só a lista fazia a coleta parcial passar por completa: quem
    chamava contava as contratações e dizia "atualizado". O que falhou
    morria no log. Aqui as duas coisas voltam juntas, porque quem mostra o
    resultado precisa poder mostrar o buraco.
    """

    contratacoes: list[Contratacao] = field(default_factory=list)
    puladas: list[Pulada] = field(default_factory=list)

    @property
    def completa(self) -> bool:
        return not self.puladas


@dataclass(frozen=True)
class Passo:
    """Onde a varredura está, com detalhe suficiente para virar frase.

    Só a modalidade não basta: o Pregão Eletrônico nacional passa de
    trinta páginas, e "1 de 3" parado por dez minutos volta a ser
    indistinguível de travado — o mesmo problema, um nível abaixo.
    """

    indice: int
    total: int
    modalidade: int
    pagina: int = 0
    de_paginas: int = 0


#: Chamado ao trocar de modalidade e a cada página que chega.
Andamento = Callable[[Passo], None]


async def coletar(
    *,
    modalidades: Sequence[int],
    data_inicial: date,
    data_final: date,
    uf: str | None = None,
    apenas_abertas: bool = True,
    settings: Settings | None = None,
    andamento: Andamento | None = None,
) -> Coleta:
    """Varre todas as modalidades pedidas e devolve o conjunto deduplicado.

    Uma modalidade que falhar não derruba as outras: o erro é registrado e
    a varredura continua. Meia ingestão vale mais que nenhuma — desde que
    quem receber saiba que é meia, e é para isso que serve `Coleta.puladas`.
    """
    encontradas: dict[str, Contratacao] = {}
    puladas: list[Pulada] = []

    # Um freio só para a varredura inteira: o limite do PNCP é por cliente,
    # então a modalidade seguinte precisa herdar o ritmo que a anterior
    # aprendeu — senão ela começa levando 429 na primeira página.
    s = settings or get_settings()
    freio = Freio(
        intervalo_inicial_s=s.pncp_intervalo_min_s, intervalo_maximo_s=s.pncp_intervalo_max_s
    )

    # O passo corrente vive fora do laço porque o aviso de página não sabe
    # em que modalidade está — só o laço sabe.
    passo = Passo(0, len(modalidades), modalidades[0] if modalidades else 0)

    def paginou(pagina: int, de_paginas: int) -> None:
        if andamento:
            andamento(Passo(passo.indice, passo.total, passo.modalidade, pagina, de_paginas))

    async with PNCPClient(settings, freio=freio, ao_paginar=paginou) as cliente:
        for indice, modalidade in enumerate(modalidades, start=1):
            passo = Passo(indice, len(modalidades), modalidade)
            if andamento:
                andamento(passo)
            antes, comeco = len(encontradas), time.monotonic()
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
                motivo = descrever(erro)
                puladas.append(Pulada(modalidade, motivo, len(encontradas) - antes))
                logger.error(
                    "%s foi pulada depois de %ds: %s",
                    nome_da_modalidade(modalidade),
                    int(time.monotonic() - comeco),
                    motivo,
                )
            else:
                # Uma linha por modalidade concluída. Sem ela, uma varredura
                # nacional passa minutos em silêncio e a única prova de que
                # algo aconteceu é o erro de quem falhou.
                logger.info(
                    "%s: %d novas em %ds",
                    nome_da_modalidade(modalidade),
                    len(encontradas) - antes,
                    int(time.monotonic() - comeco),
                )

    return Coleta(list(encontradas.values()), puladas)
