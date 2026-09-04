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

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from licita_radar.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_CRIAR_CONTROLE = """
CREATE TABLE IF NOT EXISTS schema_migracao (
    nome        text PRIMARY KEY,
    aplicada_em timestamptz NOT NULL DEFAULT now()
);
"""


class Banco:
    """Dono do pool de conexões. Uma instância por processo."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._s = settings or get_settings()
        self._pool: AsyncConnectionPool | None = None

    async def abrir(self) -> None:
        if self._pool is None:
            self._pool = AsyncConnectionPool(
                self._s.database_url, min_size=1, max_size=5, open=False
            )
            await self._pool.open(wait=True)
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
