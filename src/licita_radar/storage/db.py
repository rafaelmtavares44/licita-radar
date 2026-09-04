"""Conexão com o Postgres e aplicação das migrações.

Migração aqui é arquivo `.sql` numerado, aplicado em ordem e registrado
numa tabela de controle. Não é Alembic — de propósito: o esquema é
pequeno, e um diretório de SQL legível é mais fácil de auditar por quem
chega no repositório do que uma cadeia de revisões geradas.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from psycopg import AsyncConnection, OperationalError
from psycopg_pool import AsyncConnectionPool, PoolTimeout

from licita_radar.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class ErroDeBanco(RuntimeError):
    """Não foi possível falar com o Postgres — sempre com dica de conserto.

    Banco fora do ar é a falha mais comum de quem está começando, e um
    stack trace de quarenta linhas depois de trinta segundos de espera é a
    pior forma possível de comunicar isso.
    """


_CRIAR_CONTROLE = """
CREATE TABLE IF NOT EXISTS schema_migracao (
    nome        text PRIMARY KEY,
    aplicada_em timestamptz NOT NULL DEFAULT now()
);
"""


def traduzir_falha(erro: BaseException, alvo: str) -> str:
    """Transforma o erro do Postgres na dica certa.

    Recusar conexão, recusar usuário e não achar o banco são problemas
    diferentes; mandar a mesma mensagem para os três faz a pessoa procurar
    no lugar errado.
    """
    detalhe = str(erro).lower()

    # "role não existe" e "senha recusada" parecem a mesma coisa e têm causas
    # opostas: no primeiro caso o container é o certo mas nasceu sem o
    # usuário; no segundo, quem atende é outro Postgres.
    if "role" in detalhe and ("does not exist" in detalhe or "não existe" in detalhe):
        return (
            f"O banco em {alvo} respondeu, mas o usuário não existe nele.\n\n"
            "  • Acabou de subir o container? Espere o healthy antes de tentar:\n"
            "        docker compose ps\n"
            "    Na primeira vez o Postgres leva de 10 a 30 s criando o cluster,\n"
            "    e a porta já responde antes de o usuário existir.\n\n"
            "  • Já está healthy e mesmo assim falha? O volume foi inicializado\n"
            "    numa tentativa anterior, e o Postgres só cria o usuário do\n"
            "    compose quando o volume está vazio. Apague e recrie:\n"
            "        docker compose down -v\n"
            "        docker compose up -d db\n"
            "    (só faça isso se não houver dado que você queira manter)"
        )

    if "authentication" in detalhe or "senha" in detalhe or "password" in detalhe:
        return (
            f"O banco em {alvo} respondeu, mas recusou a senha.\n\n"
            "  Isso costuma significar que há OUTRO Postgres nessa porta —\n"
            "  o que você instalou na máquina, e não o do docker compose.\n\n"
            "  • Confira quem atende:       docker compose ps\n"
            "  • Se o container está de pé, mude a porta: LR_DB_PORT=5433 no .env\n"
            "    e ajuste a porta também na LR_DATABASE_URL. Depois:\n"
            "        docker compose up -d db"
        )

    if "does not exist" in detalhe or "não existe" in detalhe:
        return (
            f"O banco de dados em {alvo} não existe nesse servidor.\n\n"
            "  • Subiu pelo compose?          docker compose up -d db\n"
            "  • Aponta para outro Postgres?  confira LR_DATABASE_URL no .env"
        )

    return (
        f"Não consegui conectar no banco em {alvo}.\n\n"
        "  • O Postgres está rodando?   docker compose up -d db\n"
        "  • Acabou de subir? Espere uns 20 s: na primeira vez o container\n"
        "    cria o cluster antes de aceitar conexão.\n"
        "  • Confira o estado:          docker compose ps\n"
        "  • No Windows, troque 'localhost' por '127.0.0.1' na URL: o Docker\n"
        "    Desktop costuma publicar a porta só em IPv4.\n"
        "  • Usa outro banco?           ajuste LR_DATABASE_URL no .env"
    )


class Banco:
    """Dono do pool de conexões. Uma instância por processo."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._s = settings or get_settings()
        self._pool: AsyncConnectionPool | None = None

    def _dica(self, erro: BaseException) -> str:
        return traduzir_falha(erro, self._s.database_url_segura)

    async def _ping(self) -> None:
        """Uma conexão de teste antes de abrir o pool.

        O pool tenta reconectar em laço e, quando desiste, entrega um
        `PoolTimeout` que já perdeu a causa original — "senha recusada" e
        "ninguém atende" chegam idênticos. Uma conexão direta preserva o
        erro do Postgres, e é dele que sai a dica certa.
        """
        try:
            conexao = await AsyncConnection.connect(
                self._s.database_url,
                connect_timeout=int(self._s.database_timeout_s),
            )
        except OperationalError as erro:
            raise ErroDeBanco(self._dica(erro)) from erro
        await conexao.close()

    async def abrir(self) -> None:
        if self._pool is None:
            await self._ping()

            pool = AsyncConnectionPool(
                self._s.database_url,
                min_size=1,
                max_size=5,
                open=False,
                timeout=self._s.database_timeout_s,
            )
            try:
                await pool.open(wait=True, timeout=self._s.database_timeout_s)
            except (PoolTimeout, OperationalError) as erro:
                await pool.close()
                raise ErroDeBanco(self._dica(erro)) from erro

            self._pool = pool
            logger.debug("pool de conexões aberto")

    async def fechar(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @asynccontextmanager
    async def conexao(self) -> AsyncIterator[AsyncConnection]:
        if self._pool is None:
            await self.abrir()
        if self._pool is None:  # pragma: no cover — garantido pela linha acima
            raise RuntimeError("pool de conexões não pôde ser aberto")
        async with self._pool.connection() as conn:
            yield conn

    async def __aenter__(self) -> Banco:
        await self.abrir()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.fechar()


async def migrar(banco: Banco) -> list[str]:
    """Aplica as migrações pendentes, em ordem de nome. Devolve o que rodou."""
    aplicadas: list[str] = []

    async with banco.conexao() as conn:
        async with conn.cursor() as cur:
            await cur.execute(_CRIAR_CONTROLE)
            await cur.execute("SELECT nome FROM schema_migracao")
            ja_aplicadas = {linha[0] for linha in await cur.fetchall()}

        for arquivo in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if arquivo.name in ja_aplicadas:
                continue
            logger.info("aplicando migração %s", arquivo.name)
            async with conn.cursor() as cur:
                await cur.execute(arquivo.read_text(encoding="utf-8"))
                await cur.execute("INSERT INTO schema_migracao (nome) VALUES (%s)", (arquivo.name,))
            aplicadas.append(arquivo.name)

        await conn.commit()

    return aplicadas
