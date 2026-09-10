"""A camada de LLM: uma interface, muitos provedores, nenhum obrigatório.

Três decisões moldam este módulo:

**O protocolo é o da OpenAI.** Groq, Ollama, OpenRouter, LM Studio e vários
outros o implementam. Uma requisição HTTP bem escrita atende todos, e o
usuário troca de provedor mexendo numa URL — sem SDK novo, sem dependência
extra na árvore.

**Nada aqui é obrigatório.** Sem chave configurada, o `LLMDesligado` assume
e devolve a explicação heurística que o matching já sabe produzir. O
projeto continua rodando de ponta a ponta, e quem clona não esbarra num
"preciso de uma API key" na primeira execução.

**Cada resposta contabiliza tokens.** A camada de LLM é a única cara do
funil, e o que não é medido vira surpresa no fim do mês.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol, runtime_checkable

import httpx

logger = logging.getLogger(__name__)

#: Códigos em que insistir faz sentido. 429 é o caso comum nas camadas
#: gratuitas: não é erro, é o provedor pedindo para diminuir o ritmo.
_STATUS_RETENTAVEIS = frozenset({429, 500, 502, 503, 504})

#: O cliente do PNCP já tinha aprendido isto, e o do LLM não: uma análise
#: de edital gasta uns 8 mil tokens, e três seguidas estouram o limite por
#: minuto de qualquer plano gratuito. Sem espera, o alerta some e a pessoa
#: vê "o modelo falhou" sem saber que bastava aguardar vinte segundos.
_TENTATIVAS = 5


class _Vez:
    """A fila de quem fala com o provedor, e o tempo que ele pediu.

    O retry por chamada não bastava, e o motivo é que ele trata cada
    chamada como se estivesse sozinha. Duas análises aprovadas em
    sequência disparam ao mesmo tempo, tomam 429 juntas, esperam o mesmo
    tanto e voltam juntas — colidindo de novo, indefinidamente. A cota é
    da conta, não da chamada: enquanto elas não forem postas em fila, o
    "aguarde e tente de novo" só sincroniza as duas.

    Guardar o tempo pedido também importa. Quando o provedor diz "tente
    em 19s", esse 19 vale para a *próxima* chamada também — descobrir
    isso levando outro 429 é pagar duas vezes pela mesma informação.
    """

    #: Uma fila por event loop. O Lock se prende ao loop no primeiro
    #: await, e a suíte de testes roda um loop por teste.
    _por_loop: ClassVar[dict[object, tuple[asyncio.Lock, list[float]]]] = {}

    @classmethod
    def _estado(cls) -> tuple[asyncio.Lock, list[float]]:
        loop = asyncio.get_running_loop()
        if loop not in cls._por_loop:
            cls._por_loop[loop] = (asyncio.Lock(), [0.0])
        return cls._por_loop[loop]

    @classmethod
    @asynccontextmanager
    async def esperar(cls) -> AsyncIterator[Callable[[float], None]]:
        trava, liberado_em = cls._estado()
        async with trava:
            atraso = liberado_em[0] - time.monotonic()
            if atraso > 0:
                logger.info("o provedor ainda está de castigo; aguardando %.0fs", atraso)
                await asyncio.sleep(atraso)

            def adiar(segundos: float) -> None:
                liberado_em[0] = time.monotonic() + segundos

            yield adiar


#: Modelos que raciocinam antes de responder. O nome é o único sinal
#: disponível antes da primeira chamada.
_MARCAS_DE_RACIOCINIO = (
    "gpt-oss",
    "o1",
    "o3",
    "o4",
    "deepseek-r",
    "qwq",
    "thinking",
    # O Gemini Flash pensa por padrão desde a 2.5, e o pensamento sai do
    # mesmo orçamento da resposta. A camada compatível aceita
    # `reasoning_effort`; se um dia deixar de aceitar, ela ignora em
    # silêncio, que é o pior caso tolerável aqui.
    "gemini-2.5",
    "gemini-3",
    # `gemini-flash-latest` é apelido: não diz a versão, mas aponta para
    # um Flash, e todo Flash desde a 2.5 pensa por padrão.
    "gemini-flash",
    "gemini-pro-latest",
    "gemini-omni",
)


def e_de_raciocinio(modelo: str) -> bool:
    nome = modelo.lower()
    return any(marca in nome for marca in _MARCAS_DE_RACIOCINIO)


#: O tempo de espera no texto do erro. A Groq escreve "Please try again
#: in 19.153s"; o Google escreve "Please retry in 20.739736704s". Um
#: padrão que só conhecia a primeira forma devolvia None para a segunda,
#: e o código caía no backoff cego tendo a resposta na mão.
_SEGUNDOS_NO_TEXTO = re.compile(r"(?:try again|retry) in ([\d.]+)s", re.IGNORECASE)


def erro_do_corpo(resposta: httpx.Response) -> dict[str, Any]:
    """O objeto `error` da resposta, venha ele como for.

    O Gemini responde `[{"error": {...}}]` — um array de um elemento —
    onde a OpenAI responde `{"error": {...}}`. Assumir o dicionário
    rendeu um `AttributeError` no meio do diagnóstico: o código que
    existia para explicar a falha falhou primeiro.
    """
    try:
        corpo = resposta.json()
    except ValueError:
        return {}
    if isinstance(corpo, list):
        corpo = corpo[0] if corpo else {}
    if not isinstance(corpo, dict):
        return {}
    erro = corpo.get("error")
    return erro if isinstance(erro, dict) else {}


def espera_pedida(resposta: httpx.Response) -> float | None:
    """Quantos segundos o provedor pediu para esperar, se pediu.

    O cabeçalho é o caminho padrão; a Groq também põe o número na
    mensagem de erro ("Please try again in 19.153s"), e ler dali é a
    diferença entre esperar o certo e chutar.
    """
    cabecalho = resposta.headers.get("Retry-After") or resposta.headers.get("retry-after")
    if cabecalho:
        try:
            return min(float(cabecalho), 120.0)
        except ValueError:
            pass

    mensagem = str(erro_do_corpo(resposta).get("message", ""))
    if achado := _SEGUNDOS_NO_TEXTO.search(mensagem):
        return min(float(achado.group(1)) + 1.0, 120.0)
    return None


class CotaDiariaEsgotada(RuntimeError):
    """O 429 que esperar não resolve.

    Há dois 429 muito diferentes escondidos no mesmo código. Um diz
    "devagar" e passa em vinte segundos; o outro diz "volte amanhã".
    Tratar os dois igual gastava cinco tentativas de vinte segundos —
    cem segundos — para no fim estourar o prazo e reportar um timeout,
    escondendo a única informação que importava: o número da cota.
    """


def cota_diaria(resposta: httpx.Response) -> str | None:
    """Descreve a cota diária estourada, se for esse o caso.

    O Google devolve a resposta em `error.details`, num bloco
    `QuotaFailure` com `quotaId` e `quotaValue`. O `quotaId` é quem
    separa os dois casos: quando ele fala em `PerDay`, nenhuma espera
    razoável resolve.
    """
    for detalhe in erro_do_corpo(resposta).get("details", []) or []:
        if not str(detalhe.get("@type", "")).endswith("QuotaFailure"):
            continue
        for violacao in detalhe.get("violations", []) or []:
            identificador = str(violacao.get("quotaId", ""))
            if "PerDay" not in identificador:
                continue
            valor = violacao.get("quotaValue", "?")
            modelo = (violacao.get("quotaDimensions") or {}).get("model", "este modelo")
            return f"a cota gratuita de {modelo} é de {valor} pedidos por dia, e ela acabou"
    return None


@dataclass(frozen=True)
class Resposta:
    texto: str
    modelo: str
    tokens: int


@runtime_checkable
class LLM(Protocol):
    @property
    def modelo(self) -> str: ...

    @property
    def ativo(self) -> bool: ...

    async def responder(
        self,
        *,
        sistema: str,
        usuario: str,
        max_tokens: int = 600,
        formato_json: bool = False,
    ) -> Resposta: ...


class LLMDesligado:
    """O provedor que não chama ninguém.

    Não é um erro nem um stub de teste: é o modo padrão do projeto. Quem não
    configurou LLM recebe a justificativa heurística, que para a maioria dos
    casos já responde "por que este edital apareceu?".
    """

    @property
    def modelo(self) -> str:
        return "desligado"

    @property
    def ativo(self) -> bool:
        return False

    async def responder(
        self,
        *,
        sistema: str,
        usuario: str,
        max_tokens: int = 600,
        formato_json: bool = False,
    ) -> Resposta:
        return Resposta(texto="", modelo=self.modelo, tokens=0)


class LLMCompativelOpenAI:
    """Fala `/chat/completions`. Serve OpenAI, Groq, Ollama, OpenRouter…"""

    def __init__(
        self,
        *,
        base_url: str,
        modelo: str,
        api_key: str | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._modelo = modelo
        self._api_key = api_key
        self._timeout = timeout_s

    @property
    def modelo(self) -> str:
        return self._modelo

    @property
    def ativo(self) -> bool:
        return True

    async def responder(
        self,
        *,
        sistema: str,
        usuario: str,
        max_tokens: int = 600,
        formato_json: bool = False,
    ) -> Resposta:
        cabecalhos = {"Content-Type": "application/json"}
        if self._api_key:
            cabecalhos["Authorization"] = f"Bearer {self._api_key}"

        corpo: dict[str, Any] = {
            "model": self._modelo,
            "messages": [
                {"role": "system", "content": sistema},
                {"role": "user", "content": usuario},
            ],
            "max_tokens": max_tokens,
            # Justificativa não é lugar para criatividade: queremos a mesma
            # resposta para o mesmo edital, hoje e daqui a um mês.
            "temperature": 0.0,
        }

        # Modelos de raciocínio (gpt-oss, o-series, qwen3 "thinking") gastam
        # tokens pensando ANTES de escrever, e o pensamento sai do mesmo
        # orçamento. Com um teto apertado, eles consomem tudo raciocinando e
        # devolvem `content` vazio — sem erro nenhum, o que é pior. Pedir
        # esforço baixo evita isso numa justificativa de uma frase.
        if e_de_raciocinio(self._modelo):
            corpo["reasoning_effort"] = "low"

        # Quando a resposta precisa ser JSON, pedir ao provedor é muito mais
        # eficaz que pedir no prompt: o modelo passa a ser restringido na
        # geração em vez de convencido por instrução.
        if formato_json:
            corpo["response_format"] = {"type": "json_object"}

        try:
            dados = await self._pedir(cabecalhos, corpo)
        except httpx.HTTPStatusError as erro:
            # Nem todo provedor aceita `response_format` — Ollama e LM Studio
            # variam por modelo. Perder a análise inteira por um campo
            # opcional seria trocar robustez por elegância.
            if not (formato_json and erro.response.status_code == httpx.codes.BAD_REQUEST):
                raise
            logger.info("o provedor recusou response_format; repetindo sem ele")
            corpo.pop("response_format", None)
            dados = await self._pedir(cabecalhos, corpo)

        mensagem = dados["choices"][0].get("message") or {}
        texto = (mensagem.get("content") or "").strip()

        # Alguns provedores devolvem o raciocínio num campo separado e deixam
        # `content` vazio quando o corte de tokens chega no meio. Melhor uma
        # frase tirada dali do que um alerta em branco.
        if not texto:
            texto = str(
                mensagem.get("reasoning") or mensagem.get("reasoning_content") or ""
            ).strip()

        uso = dados.get("usage") or {}
        return Resposta(
            texto=texto,
            modelo=dados.get("model", self._modelo),
            tokens=int(uso.get("total_tokens", 0)),
        )

    async def _pedir(self, cabecalhos: dict[str, str], corpo: dict[str, Any]) -> dict[str, Any]:
        """Uma chamada, com paciência para o limite de requisições.

        O provedor manda `Retry-After` dizendo quantos segundos esperar —
        obedecer é mais rápido e mais educado que backoff cego, e é o que
        transforma um 429 em atraso de vinte segundos em vez de um alerta
        perdido.
        """
        rota = f"{self._base_url}/chat/completions"
        ultima: Exception | None = None

        # Uma chamada por vez, para a conta inteira. Duas análises em
        # paralelo não são duas vezes mais rápidas: são dois 429.
        async with _Vez.esperar() as adiar, httpx.AsyncClient(timeout=self._timeout) as cliente:
            for tentativa in range(1, _TENTATIVAS + 1):
                try:
                    resposta = await cliente.post(rota, headers=cabecalhos, json=corpo)
                except httpx.TransportError as erro:
                    ultima = erro
                    if tentativa == _TENTATIVAS:
                        raise
                    await asyncio.sleep(2**tentativa)
                    continue

                if resposta.status_code not in _STATUS_RETENTAVEIS:
                    resposta.raise_for_status()
                    dados: dict[str, Any] = resposta.json()
                    return dados

                if resposta.status_code == httpx.codes.TOO_MANY_REQUESTS and (
                    diaria := cota_diaria(resposta)
                ):
                    # Amanhã, não daqui a vinte segundos.
                    raise CotaDiariaEsgotada(diaria)

                espera = espera_pedida(resposta) or float(2**tentativa)
                # Quem vier depois herda a espera, mesmo que esta chamada
                # desista: o castigo é da conta, não desta requisição.
                adiar(espera)

                if tentativa == _TENTATIVAS:
                    resposta.raise_for_status()

                logger.info(
                    "o provedor respondeu %s; aguardando %.0fs (tentativa %d de %d)",
                    resposta.status_code,
                    espera,
                    tentativa,
                    _TENTATIVAS,
                )
                await asyncio.sleep(espera)

        raise ultima or RuntimeError("não foi possível falar com o provedor")


def construir_llm(
    *, base_url: str | None, modelo: str | None, api_key: str | None, timeout_s: float = 60.0
) -> LLM:
    """Monta o provedor a partir da configuração, ou devolve o desligado.

    Ollama não pede chave; OpenAI e Groq pedem. Por isso a presença da URL e
    do modelo é o que decide, não a da chave.
    """
    if not base_url or not modelo:
        logger.info("LLM não configurado: as justificativas serão heurísticas")
        return LLMDesligado()
    return LLMCompativelOpenAI(
        base_url=base_url, modelo=modelo, api_key=api_key, timeout_s=timeout_s
    )


async def listar_modelos(
    *, base_url: str, api_key: str | None, timeout_s: float = 15.0
) -> list[str]:
    """Pergunta ao provedor quais modelos ele tem.

    Serve para transformar um 404 opaco em algo acionável: provedores
    aposentam modelo com frequência — o `llama-3.3-70b-versatile` durou
    menos de um ano — e "esse modelo não existe" sem a lista do que existe
    deixa a pessoa procurando na documentação.
    """
    cabecalhos = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as cliente:
            resposta = await cliente.get(f"{base_url.rstrip('/')}/models", headers=cabecalhos)
            resposta.raise_for_status()
            dados = resposta.json()
    except (httpx.HTTPError, ValueError, KeyError):
        return []

    modelos = [str(item.get("id")) for item in dados.get("data", []) if item.get("id")]
    return sorted(modelos)


#: O que não serve para conversar: embeddings, imagem, vídeo, áudio,
#: agentes de uso específico. Alguns provedores devolvem mais deles do
#: que modelos de texto.
_NAO_E_CHAT = (
    "embedding",
    "imagen",
    "veo",
    "tts",
    "audio",
    "whisper",
    "aqa",
    "guard",
    "computer-use",
    "deep-research",
    "live",
    "image",
    "vision",
    "rerank",
    "robotics",
    "coder",
)


def _peso(modelo: str) -> tuple[int, str]:
    """Quanto este nome parece com o que o projeto precisa.

    Ler edital pede um modelo rápido, barato e de contexto grande — e,
    de preferência, um apelido estável. O Google acabou de aposentar o
    `gemini-2.5-flash` e pôs `gemini-flash-latest` no lugar: quem
    apontar para o apelido não precisa mexer no `.env` na próxima vez.
    """
    nome = modelo.lower()
    pontos = 0
    if "flash" in nome:
        pontos += 4
    if "latest" in nome:
        pontos += 3
    if any(marca in nome for marca in ("mini", "instant", "oss", "lite", "turbo")):
        pontos += 1
    if "preview" in nome or "exp" in nome:
        # Preview muda sem aviso, e o que quebra é a análise de alguém.
        pontos -= 3
    return (-pontos, nome)


def provaveis_de_chat(modelos: list[str]) -> list[str]:
    """Ordena a lista pelo que provavelmente serve, não pelo alfabeto.

    O 404 vinha com "disponíveis agora:" seguido dos seis primeiros em
    ordem alfabética — que num provedor grande são `aqa`, embeddings e
    modelos de robótica. Uma lista assim é pior que nenhuma: parece
    resposta e manda a pessoa para o lugar errado.
    """
    uteis = [m for m in modelos if not any(marca in m.lower() for marca in _NAO_E_CHAT)]
    return sorted(uteis, key=_peso)


# ---------------------------------------------------------------------------

SISTEMA_JUSTIFICATIVA = """Você avalia licitações públicas brasileiras para uma empresa.
Responda em UMA frase de no máximo 25 palavras, em português do Brasil.
Diga se a licitação combina com a empresa e por quê, em linguagem direta.
Não repita o objeto da licitação. Não use adjetivos vagos como "interessante".
Se não combinar, diga isso claramente."""


def prompt_justificativa(*, descricao_empresa: str, objeto: str, score: float) -> str:
    return (
        f"EMPRESA: {descricao_empresa}\n\n"
        f"LICITAÇÃO: {objeto[:900]}\n\n"
        f"Similaridade calculada: {score:.2f} de 1,00.\n"
        f"Esta licitação combina com a empresa?"
    )


def extrair_frase(texto: str) -> str:
    """Uma frase, sem aspas nem prefixo de conversa.

    Modelos pequenos gostam de começar com "Claro! Aqui está:" — o que não é
    justificativa nenhuma e polui o alerta.
    """
    limpo = texto.strip().strip('"').strip()
    for lixo in ("Resposta:", "Justificativa:", "Claro!", "Certamente,"):
        if limpo.startswith(lixo):
            limpo = limpo[len(lixo) :].strip()

    if "\n" in limpo:
        limpo = limpo.split("\n", 1)[0].strip()
    return limpo[:300]


def como_json(texto: str) -> dict[str, object] | None:
    """Lê a resposta como JSON, tolerando cerca de markdown e corte no meio."""
    bruto = texto.strip()
    if bruto.startswith("```"):
        bruto = bruto.split("```")[1] if "```" in bruto[3:] else bruto[3:]
        bruto = bruto.removeprefix("json").strip()

    try:
        dados = json.loads(bruto)
    except (json.JSONDecodeError, ValueError):
        dados = _remendar(bruto)

    return dados if isinstance(dados, dict) else None


def _remendar(bruto: str) -> dict[str, Any] | None:
    """Aproveita um JSON que o modelo não terminou de escrever.

    Acontece quando o teto de tokens corta a resposta no meio de uma lista:
    o que veio antes está correto e completo, e jogar tudo fora por causa
    de uma chave que faltou fecha significa perder oito mil tokens já
    pagos. A tentativa é conservadora — corta no último item inteiro e
    fecha o que estiver aberto.
    """
    for corte in range(len(bruto), 0, -1):
        if bruto[corte - 1] != "}":
            continue
        tentativa = bruto[:corte]
        # fecha na ordem inversa da abertura, contando o que ficou aberto
        pendentes = []
        dentro_de_texto = False
        escapado = False
        for caractere in tentativa:
            if escapado:
                escapado = False
                continue
            if caractere == "\\":
                escapado = True
            elif caractere == '"':
                dentro_de_texto = not dentro_de_texto
            elif not dentro_de_texto and caractere in "{[":
                pendentes.append("}" if caractere == "{" else "]")
            elif not dentro_de_texto and caractere in "}]" and pendentes:
                pendentes.pop()
        try:
            dados = json.loads(tentativa + "".join(reversed(pendentes)))
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(dados, dict):
            logger.info("a resposta do modelo veio cortada; aproveitei a parte completa")
            return dados
    return None
