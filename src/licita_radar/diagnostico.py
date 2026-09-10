"""O `licita-radar doctor`: descobre por que nada funciona.

Existe porque "não consegui conectar no banco" pode significar cinco
coisas diferentes, e adivinhar qual delas é um jeito ruim de passar a
tarde. Aqui cada camada é testada separadamente, na ordem em que uma
depende da outra, e a primeira que falhar já diz o que fazer.

Nenhuma verificação levanta exceção: falhar é o resultado esperado de
metade delas.
"""

from __future__ import annotations

import asyncio
import socket
import sys
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlparse

from licita_radar.config.perfil import ErroDePerfil, carregar_perfil
from licita_radar.config.settings import Settings
from licita_radar.erros import descrever


class Estado(StrEnum):
    OK = "ok"
    FALHA = "falha"
    AVISO = "aviso"
    PULADO = "pulado"


@dataclass(frozen=True)
class Checagem:
    grupo: str
    titulo: str
    estado: Estado
    detalhe: str = ""
    dica: str = ""


def _endereco(url: str) -> tuple[str, int]:
    partes = urlparse(url)
    return partes.hostname or "127.0.0.1", partes.port or 5432


# --------------------------------------------------------------- ambiente


def checar_python() -> Checagem:
    versao = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    return Checagem("Ambiente", f"Python {versao}", Estado.OK, sys.platform)


def checar_event_loop() -> Checagem:
    """No Windows, o psycopg recusa o ProactorEventLoop — o padrão de lá."""
    # O mypy resolve sys.platform estaticamente pela plataforma da checagem
    # e declara o resto inalcançável — mas em Windows ele é o caminho normal.
    if sys.platform != "win32":
        return Checagem("Ambiente", "event loop", Estado.OK, "compatível")

    politica = type(asyncio.get_event_loop_policy()).__name__  # type: ignore[unreachable]
    if "Selector" in politica:
        return Checagem("Ambiente", "event loop", Estado.OK, politica)
    return Checagem(
        "Ambiente",
        "event loop",
        Estado.FALHA,
        politica,
        "o psycopg não roda no ProactorEventLoop; a CLI deveria trocar a política",
    )


def checar_perfil(settings: Settings) -> Checagem:
    caminho = Path(settings.perfil_path)
    try:
        perfil = carregar_perfil(caminho)
    except ErroDePerfil as erro:
        primeira_linha = str(erro).splitlines()[0]
        return Checagem(
            "Ambiente",
            f"perfil ({caminho})",
            Estado.FALHA,
            primeira_linha,
            "copie o perfil.exemplo.yaml e ajuste",
        )
    return Checagem("Ambiente", f"perfil ({caminho})", Estado.OK, perfil.nome)


def checar_fastembed() -> Checagem:
    try:
        import fastembed  # noqa: F401
    except ImportError:
        return Checagem(
            "Ambiente",
            "camada semântica",
            Estado.AVISO,
            "fastembed não instalado",
            'opcional: pip install -e ".[semantico]" — sem ele use match --sem-semantica',
        )
    return Checagem("Ambiente", "camada semântica", Estado.OK, "fastembed disponível")


def checar_origem_da_config(settings: Settings) -> Checagem:
    """De onde veio a URL do banco: do .env ou do ambiente?

    Variável de ambiente vence o arquivo, e isso é invisível — a pessoa
    edita o .env, nada muda, e não há nenhuma pista do porquê.
    """
    import os

    do_ambiente = os.environ.get("LR_DATABASE_URL")
    if not do_ambiente:
        return Checagem("Ambiente", "origem da configuração", Estado.OK, ".env / padrões")

    arquivo = Path(".env")
    if arquivo.exists():
        for linha in arquivo.read_text(encoding="utf-8").splitlines():
            if linha.startswith("LR_DATABASE_URL=") and linha.split("=", 1)[1] != do_ambiente:
                return Checagem(
                    "Ambiente",
                    "origem da configuração",
                    Estado.AVISO,
                    "variável de ambiente sobrepõe o .env",
                    "para voltar ao arquivo: Remove-Item Env:LR_DATABASE_URL (ou "
                    "unset LR_DATABASE_URL)",
                )

    return Checagem("Ambiente", "origem da configuração", Estado.OK, "variável de ambiente")


# ------------------------------------------------------------------ rede


def checar_dns(settings: Settings) -> Checagem:
    host, _ = _endereco(settings.database_url)
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as erro:
        return Checagem("Banco", f"resolver {host}", Estado.FALHA, str(erro))

    familias = {"IPv6" if info[0] == socket.AF_INET6 else "IPv4" for info in infos}
    enderecos = sorted({str(info[4][0]) for info in infos})

    # A armadilha clássica no Windows: 'localhost' resolve para ::1 primeiro
    # e o Docker Desktop publica a porta só em IPv4.
    if "IPv6" in familias and host.lower() == "localhost":
        return Checagem(
            "Banco",
            f"resolver {host}",
            Estado.AVISO,
            ", ".join(enderecos),
            "no Windows, troque 'localhost' por '127.0.0.1' na LR_DATABASE_URL",
        )
    return Checagem("Banco", f"resolver {host}", Estado.OK, ", ".join(enderecos))


def checar_porta(settings: Settings) -> Checagem:
    host, porta = _endereco(settings.database_url)
    inicio = time.monotonic()
    try:
        with socket.create_connection((host, porta), timeout=settings.database_timeout_s):
            ms = (time.monotonic() - inicio) * 1000
            return Checagem("Banco", f"porta {porta} aberta", Estado.OK, f"{ms:.0f} ms")
    except TimeoutError:
        return Checagem(
            "Banco",
            f"porta {porta} aberta",
            Estado.FALHA,
            "tempo esgotado",
            "firewall bloqueando, ou o Docker não publicou a porta",
        )
    except OSError as erro:
        return Checagem(
            "Banco",
            f"porta {porta} aberta",
            Estado.FALHA,
            str(erro),
            "suba o banco: docker compose up -d db",
        )


# ----------------------------------------------------------------- banco


async def checar_postgres(settings: Settings) -> list[Checagem]:
    from psycopg import AsyncConnection, OperationalError

    from licita_radar.storage.db import traduzir_falha

    try:
        conexao = await AsyncConnection.connect(
            settings.database_url, connect_timeout=int(settings.database_timeout_s)
        )
    except OperationalError as erro:
        dica = traduzir_falha(erro, settings.database_url_segura).splitlines()[0]
        return [
            Checagem("Banco", "autenticar", Estado.FALHA, str(erro).strip()[:90], dica),
            Checagem("Banco", "extensão vector", Estado.PULADO),
            Checagem("Banco", "migrações aplicadas", Estado.PULADO),
        ]

    resultado: list[Checagem] = []
    async with conexao, conexao.cursor() as cur:
        if True:
            await cur.execute("SELECT version()")
            linha = await cur.fetchone()
            versao = str(linha[0]).split(" on ")[0] if linha else "?"
            resultado.append(Checagem("Banco", "autenticar", Estado.OK, versao))

            await cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            tem_vector = await cur.fetchone() is not None
            resultado.append(
                Checagem("Banco", "extensão vector", Estado.OK, "instalada")
                if tem_vector
                else Checagem(
                    "Banco",
                    "extensão vector",
                    Estado.AVISO,
                    "ausente",
                    "o M2 precisa dela: rode licita-radar migrar",
                )
            )

            try:
                await cur.execute("SELECT count(*) FROM schema_migracao")
                total = await cur.fetchone()
                quantas = int(total[0]) if total else 0
            except Exception:  # tabela de controle ainda não existe
                quantas = 0

            resultado.append(
                Checagem("Banco", "migrações aplicadas", Estado.OK, f"{quantas}")
                if quantas
                else Checagem(
                    "Banco",
                    "migrações aplicadas",
                    Estado.AVISO,
                    "nenhuma",
                    "rode: licita-radar migrar",
                )
            )
    return resultado


async def checar_alerta(settings: Settings) -> Checagem:
    """Confere o bot e o destino, não só se as variáveis estão preenchidas.

    O erro mais comum não é token errado: é o chat_id faltando ou o bot
    nunca ter recebido um /start. Nos dois casos a configuração *parece*
    completa e a mensagem simplesmente não chega — que é o pior tipo de
    falha para um canal de alerta.
    """
    from licita_radar.alerta.canal import ErroDoTelegram, Telegram

    if not settings.telegram_token:
        return Checagem(
            "Alerta",
            "Telegram",
            Estado.AVISO,
            "não configurado",
            "opcional: sem ele o alerta fica só no log — veja `licita-radar alertar`",
        )

    bot = Telegram(
        token=settings.telegram_token,
        chat_id=settings.telegram_chat_id or "",
        timeout_s=min(settings.telegram_timeout_s, 15.0),
    )
    try:
        nome = await bot.conferir()
    except ErroDoTelegram as erro:
        return Checagem("Alerta", "Telegram", Estado.FALHA, "token recusado", str(erro))

    if not settings.telegram_chat_id:
        return Checagem(
            "Alerta",
            "Telegram",
            Estado.FALHA,
            f"@{nome} responde, mas falta o destino",
            "mande /start para o bot e rode: licita-radar alertar --descobrir",
        )

    return Checagem("Alerta", "Telegram", Estado.OK, f"@{nome} → {settings.telegram_chat_id}")


async def checar_llm(settings: Settings) -> Checagem:
    """Testa a chave com a menor requisição possível.

    Verificar só se a variável está preenchida não serve de nada: chave
    errada, cota estourada e URL trocada dão todas o mesmo "configurado".
    """
    from licita_radar.llm import construir_llm

    llm = construir_llm(
        base_url=settings.llm_base_url,
        modelo=settings.llm_modelo,
        api_key=settings.llm_api_key,
        timeout_s=min(settings.llm_timeout_s, 20.0),
    )

    if not llm.ativo:
        return Checagem(
            "LLM",
            "configurado",
            Estado.AVISO,
            "nenhum",
            "opcional: sem LLM, a justificativa é heurística e tudo funciona",
        )

    # Cinco tokens bastavam para um modelo que só escreve. Um modelo de
    # raciocínio gasta tokens PENSANDO antes de escrever, do mesmo
    # orçamento — com teto de cinco, ele nunca chega à resposta, e o que
    # a tela mostra é um timeout que parece problema de rede. O teste de
    # saúde precisa ter a mesma forma da chamada real, senão ele reprova
    # configurações que funcionam.
    from licita_radar.llm import e_de_raciocinio

    orcamento = 512 if e_de_raciocinio(settings.llm_modelo or "") else 32

    inicio = time.monotonic()
    try:
        resposta = await llm.responder(
            sistema="Responda apenas com a palavra: ok",
            usuario="Diga ok.",
            max_tokens=orcamento,
        )
    except Exception as erro:  # a mensagem do provedor é a informação útil
        # `str()` de um timeout do httpx é string vazia. Sem esta tradução
        # a linha saía "X gemini-flash-latest" e mais nada — o mesmo erro
        # mudo que já tinha custado uma busca inteira no cliente do PNCP.
        detalhe = descrever(erro)
        dica = "confira LR_LLM_API_KEY e LR_LLM_BASE_URL no .env"
        if "a tempo" in detalhe or "conectar" in detalhe:
            dica = (
                "o provedor não respondeu: confira a URL, a rede, "
                "e se LR_LLM_TIMEOUT_S não está curto demais"
            )
        elif "401" in detalhe or "invalid_api_key" in detalhe:
            dica = "a chave foi recusada — gere outra em console.groq.com"
        elif "pedidos por dia" in detalhe:
            dica = "troque LR_LLM_MODELO por outro — a cota é por modelo, não por conta"
        elif "429" in detalhe:
            dica = "o provedor pediu para diminuir o ritmo; tente de novo em um minuto"
        elif "404" in detalhe:
            # provedor aposenta modelo com frequência: em vez de mandar a
            # pessoa procurar na documentação, pergunta a ele o que existe
            from licita_radar.llm import listar_modelos, provaveis_de_chat

            todos = await listar_modelos(
                base_url=settings.llm_base_url or "", api_key=settings.llm_api_key
            )
            disponiveis = provaveis_de_chat(todos)
            if disponiveis:
                amostra = ", ".join(disponiveis[:6])
                dica = f"'{settings.llm_modelo}' não existe mais. Tente um destes: {amostra}" + (
                    f" (+{len(disponiveis) - 6} outros de texto)" if len(disponiveis) > 6 else ""
                )
            else:
                dica = f"o modelo '{settings.llm_modelo}' não existe nesse provedor"
        return Checagem("LLM", settings.llm_modelo or "?", Estado.FALHA, detalhe[:110], dica)

    ms = (time.monotonic() - inicio) * 1000
    return Checagem(
        "LLM", resposta.modelo, Estado.OK, f"respondeu em {ms:.0f} ms · {resposta.tokens} tokens"
    )


# ------------------------------------------------------------------ PNCP


async def checar_pncp(settings: Settings) -> Checagem:
    import httpx

    url = f"{settings.pncp_base_url}/v1/contratacoes/proposta"
    params: dict[str, str | int] = {
        "dataFinal": time.strftime("%Y%m%d"),
        "codigoModalidadeContratacao": 6,
        "pagina": 1,
        "tamanhoPagina": 1,
    }
    inicio = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=15.0) as cliente:
            resposta = await cliente.get(url, params=params)
    except httpx.HTTPError as erro:
        return Checagem(
            "PNCP",
            "API de consultas",
            Estado.FALHA,
            str(erro)[:80],
            "sem internet, ou a rede bloqueia o pncp.gov.br",
        )

    ms = (time.monotonic() - inicio) * 1000
    codigo = resposta.status_code

    if codigo >= 500:
        return Checagem(
            "PNCP", "API de consultas", Estado.AVISO, f"HTTP {codigo}", "o portal está fora do ar"
        )
    if codigo >= 400:
        return Checagem(
            "PNCP",
            "API de consultas",
            Estado.AVISO,
            f"HTTP {codigo} · {ms:.0f} ms",
            "a API respondeu, mas recusou os parâmetros desta checagem — "
            "a rede está boa, e o ingest usa outros",
        )
    return Checagem("PNCP", "API de consultas", Estado.OK, f"HTTP {codigo} · {ms:.0f} ms")


# ------------------------------------------------------------- orquestra


async def diagnosticar(settings: Settings, *, com_pncp: bool = True) -> list[Checagem]:
    checagens = [
        checar_python(),
        checar_event_loop(),
        checar_origem_da_config(settings),
        checar_perfil(settings),
        checar_fastembed(),
    ]

    dns = checar_dns(settings)
    porta = checar_porta(settings)
    checagens += [dns, porta]

    if porta.estado is Estado.OK:
        checagens += await checar_postgres(settings)
    else:
        checagens += [
            Checagem("Banco", "autenticar", Estado.PULADO),
            Checagem("Banco", "extensão vector", Estado.PULADO),
            Checagem("Banco", "migrações aplicadas", Estado.PULADO),
        ]

    checagens.append(await checar_llm(settings))
    checagens.append(await checar_alerta(settings))

    if com_pncp:
        checagens.append(await checar_pncp(settings))

    return checagens


def executar(settings: Settings, *, com_pncp: bool = True) -> list[Checagem]:
    return asyncio.run(diagnosticar(settings, com_pncp=com_pncp))
