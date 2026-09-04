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


class Banco:
    """Dono do pool de conexões. Uma instância por processo."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._s = settings or get_settings()
        self._pool: AsyncConnectionPool | None = None

    def _dica(self) -> str:
        return (
            f"Não consegui conectar no banco em {self._s.database_url_segura}.\n\n"
            "  • O Postgres está rodando?   docker compose up -d db\n"
            "  • Já subiu?  confira:        docker compose ps\n"
            "  • Usa outro banco?           ajuste LR_DATABASE_URL no .env"
        )

    async def abrir(self) -> None:
        if self._pool is None:
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
                raise ErroDeBanco(self._dica()) from erro

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
