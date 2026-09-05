"""Para onde o aviso vai. Hoje, Telegram — ou lugar nenhum.

O formato das mensagens usa `parse_mode=HTML` e não MarkdownV2, e a razão
é a mesma que o terminal já ensinou nesta base de código: o MarkdownV2 do
Telegram exige escapar dezoito caracteres (`.`, `-`, `(`, `)`, `!`, `=`…),
e um objeto de licitação vem cheio de todos eles. Uma mensagem que falha
por causa de um parêntese no nome do órgão é o mesmo defeito do `[web]`
comido pelo Rich — conteúdo tratado como marcação. Em HTML são três
caracteres para escapar, e a regra cabe numa função de duas linhas.
"""

from __future__ import annotations

import html
import logging
from types import TracebackType
from typing import Protocol, Self, runtime_checkable

import httpx

logger = logging.getLogger(__name__)

#: O Telegram recusa mensagem acima disto. Cortar antes é melhor que
#: descobrir com 400 Bad Request depois de gastar tokens no resumo.
LIMITE_DE_CARACTERES = 4000


def escapar(texto: str) -> str:
    """`&`, `<` e `>`. Só isso, e é por isso que o formato é HTML."""
    return html.escape(texto, quote=False)


def cortar(texto: str, limite: int = LIMITE_DE_CARACTERES) -> str:
    if len(texto) <= limite:
        return texto
    return texto[: limite - 20].rstrip() + "\n\n<i>(cortado)</i>"


@runtime_checkable
class Canal(Protocol):
    @property
    def ativo(self) -> bool: ...

    async def enviar(self, texto: str) -> bool: ...


class CanalDesligado:
    """O canal que não avisa ninguém — e é o padrão do projeto.

    Não é um stub de teste: quem clona o repositório não deve precisar de
    um bot do Telegram para ver o radar funcionando.
    """

    @property
    def ativo(self) -> bool:
        return False

    async def enviar(self, texto: str) -> bool:
        logger.info("alerta (canal desligado):\n%s", texto)
        return False


class ErroDoTelegram(RuntimeError):
    """Falha ao entregar a mensagem, já traduzida para quem lê."""


class Telegram:
    """Fala com a Bot API. Uma requisição, sem SDK."""

    def __init__(
        self,
        *,
        token: str,
        chat_id: str,
        timeout_s: float = 20.0,
        base_url: str = "https://api.telegram.org",
    ) -> None:
        self._token = token
        self._chat_id = chat_id
        self._timeout = timeout_s
        self._base = base_url.rstrip("/")
        self._cliente: httpx.AsyncClient | None = None

    @property
    def ativo(self) -> bool:
        return True

    async def __aenter__(self) -> Self:
        self._cliente = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._cliente:
            await self._cliente.aclose()
            self._cliente = None

    async def enviar(self, texto: str) -> bool:
        """Manda a mensagem. Falhar aqui nunca derruba quem chamou.

        Um alerta que não sai é ruim; uma execução do radar que morre
        porque o Telegram estava fora do ar é pior — o trabalho todo já
        foi feito e está gravado no banco.
        """
        corpo = {
            "chat_id": self._chat_id,
            "text": cortar(texto),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        url = f"{self._base}/bot{self._token}/sendMessage"

        try:
            if self._cliente is not None:
                resposta = await self._cliente.post(url, json=corpo)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as cliente:
                    resposta = await cliente.post(url, json=corpo)
        except httpx.HTTPError as erro:
            logger.warning("o Telegram não respondeu: %s", erro)
            return False

        if resposta.status_code == httpx.codes.OK:
            return True

        logger.warning(
            "o Telegram recusou a mensagem (%s): %s", resposta.status_code, _dica(resposta)
        )
        return False

    async def conferir(self) -> str:
        """Quem é o bot, para o `doctor` dizer que o token vale.

        Levanta com mensagem legível: aqui o silêncio não serve, porque a
        pessoa está justamente perguntando se a configuração está certa.
        """
        url = f"{self._base}/bot{self._token}/getMe"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as cliente:
                resposta = await cliente.get(url)
        except httpx.HTTPError as erro:
            raise ErroDoTelegram(f"não foi possível falar com o Telegram: {erro}") from erro

        if resposta.status_code == httpx.codes.UNAUTHORIZED:
            raise ErroDoTelegram(
                "o Telegram recusou o token (401).\n"
                "Confira LR_TELEGRAM_TOKEN — ele vem do @BotFather e tem a forma "
                "123456789:AA..."
            )
        if resposta.status_code != httpx.codes.OK:
            raise ErroDoTelegram(f"o Telegram respondeu {resposta.status_code}")

        dados = resposta.json().get("result") or {}
        return str(dados.get("username") or "bot sem nome")


def _dica(resposta: httpx.Response) -> str:
    """Traduz o erro do Telegram para o que fazer a respeito."""
    try:
        descricao = str(resposta.json().get("description", ""))
    except ValueError:
        return resposta.text[:200]

    if "chat not found" in descricao.lower():
        return (
            f"{descricao} — mande /start para o seu bot no Telegram uma vez, "
            "senão ele não tem permissão de falar com você"
        )
    if "bot was blocked" in descricao.lower():
        return f"{descricao} — desbloqueie o bot no aplicativo"
    return descricao


def construir_canal(*, token: str | None, chat_id: str | None, timeout_s: float = 20.0) -> Canal:
    """Monta o canal a partir da configuração, ou devolve o desligado."""
    if not token or not chat_id:
        logger.debug("Telegram não configurado: os alertas ficam só no log")
        return CanalDesligado()
    return Telegram(token=token, chat_id=chat_id, timeout_s=timeout_s)
