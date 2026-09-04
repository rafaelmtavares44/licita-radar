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

import json
import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import httpx

logger = logging.getLogger(__name__)


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

    async def responder(self, *, sistema: str, usuario: str, max_tokens: int = 220) -> Resposta: ...


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

    async def responder(self, *, sistema: str, usuario: str, max_tokens: int = 220) -> Resposta:
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

    async def responder(self, *, sistema: str, usuario: str, max_tokens: int = 220) -> Resposta:
        cabecalhos = {"Content-Type": "application/json"}
        if self._api_key:
            cabecalhos["Authorization"] = f"Bearer {self._api_key}"

        corpo = {
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

        async with httpx.AsyncClient(timeout=self._timeout) as cliente:
            resposta = await cliente.post(
                f"{self._base_url}/chat/completions", headers=cabecalhos, json=corpo
            )
            resposta.raise_for_status()
            dados = resposta.json()

        texto = (dados["choices"][0]["message"]["content"] or "").strip()
        uso = dados.get("usage") or {}
        return Resposta(
            texto=texto,
            modelo=dados.get("model", self._modelo),
            tokens=int(uso.get("total_tokens", 0)),
        )


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
    """Tenta ler a resposta como JSON, tolerando cerca de markdown."""
    bruto = texto.strip()
    if bruto.startswith("```"):
        bruto = bruto.split("```")[1] if "```" in bruto[3:] else bruto[3:]
        bruto = bruto.removeprefix("json").strip()
    try:
        dados = json.loads(bruto)
    except (json.JSONDecodeError, ValueError):
        return None
    return dados if isinstance(dados, dict) else None
