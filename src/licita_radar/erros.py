"""Traduzir falha em frase — o único trabalho deste módulo.

Nasceu no cliente do PNCP, quando um `ReadTimeout` produziu a linha de log
``modalidade 6 falhou e foi pulada:`` e terminou ali. `str(httpx.ReadTimeout())`
é string vazia, e um erro sem mensagem custa mais caro que erro nenhum:
manda investigar o lugar errado.

Está aqui, e não lá, porque a mesma armadilha esperava no diagnóstico —
`X gemini-flash-latest` seguido de coluna em branco. Toda camada que fala
com um serviço de fora precisa da mesma tradução, então ela deixou de ser
detalhe de um cliente para virar regra da casa.
"""

from __future__ import annotations

import httpx

#: O que dizer para cada falha de transporte. A ordem importa: a primeira
#: que casar vence, então as classes mais específicas vêm antes.
_NOMES: tuple[tuple[type[BaseException], str], ...] = (
    (httpx.ConnectTimeout, "o servidor não aceitou a conexão a tempo"),
    (httpx.ReadTimeout, "o servidor não respondeu a tempo"),
    (httpx.WriteTimeout, "não deu para enviar o pedido a tempo"),
    (httpx.PoolTimeout, "a fila de conexões estourou o tempo"),
    (httpx.ConnectError, "não deu para conectar"),
    (httpx.RemoteProtocolError, "o servidor encerrou a conexão no meio"),
    (httpx.ReadError, "a conexão caiu durante a leitura"),
)


def descrever(erro: BaseException) -> str:
    """Uma frase sobre a falha que nunca sai vazia."""
    if isinstance(erro, httpx.HTTPStatusError):
        return f"HTTP {erro.response.status_code} em {erro.request.url.path}"

    texto = str(erro).strip()
    primeira = texto.splitlines()[0] if texto else ""
    for classe, nome in _NOMES:
        if isinstance(erro, classe):
            return f"{nome} ({primeira})" if primeira else nome
    return primeira or type(erro).__name__
