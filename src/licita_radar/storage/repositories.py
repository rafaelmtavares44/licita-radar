"""Leitura e escrita de contratações. Não decide nada, só guarda e devolve."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from psycopg.rows import dict_row

from licita_radar.ingest.modelos import Contratacao
from licita_radar.storage.db import Banco

logger = logging.getLogger(__name__)

_UPSERT = """
INSERT INTO contratacao (
    numero_controle_pncp, modalidade_codigo, objeto, orgao_cnpj, orgao_nome,
    esfera, uf, municipio, valor_estimado, data_publicacao, abertura_proposta,
    encerramento_proposta, payload
)
VALUES (%(numero)s, %(modalidade)s, %(objeto)s, %(orgao_cnpj)s, %(orgao_nome)s,
        %(esfera)s, %(uf)s, %(municipio)s, %(valor)s, %(publicacao)s, %(abertura)s,
        %(encerramento)s, %(payload)s)
ON CONFLICT (numero_controle_pncp) DO UPDATE SET
    objeto                = EXCLUDED.objeto,
    valor_estimado        = EXCLUDED.valor_estimado,
    abertura_proposta     = EXCLUDED.abertura_proposta,
    encerramento_proposta = EXCLUDED.encerramento_proposta,
    payload               = EXCLUDED.payload,
    atualizado_em         = now()
RETURNING (xmax = 0) AS inserida;
"""
# `xmax = 0` é o truque padrão do Postgres para saber se o UPSERT inseriu
# ou atualizou. É o que permite responder "quantas eram novas?" sem
# consultar antes de gravar.


@dataclass(frozen=True)
class ResultadoIngestao:
    vistas: int
    novas: int

    @property
    def atualizadas(self) -> int:
        return self.vistas - self.novas


class ContratacaoRepo:
    def __init__(self, banco: Banco) -> None:
        self._banco = banco

    async def salvar_muitas(self, contratacoes: list[Contratacao]) -> ResultadoIngestao:
        """Grava em lote, de forma idempotente. Rodar duas vezes não duplica."""
        if not contratacoes:
            return ResultadoIngestao(vistas=0, novas=0)

        novas = 0
        async with self._banco.conexao() as conn:
            async with conn.cursor() as cur:
                for c in contratacoes:
                    await cur.execute(
                        _UPSERT,
                        {
                            "numero": c.numero_controle_pncp,
                            "modalidade": c.modalidade_codigo,
                            "objeto": c.objeto,
                            "orgao_cnpj": c.orgao_cnpj,
                            "orgao_nome": c.orgao_nome,
                            "esfera": c.esfera,
                            "uf": c.uf,
                            "municipio": c.municipio,
                            "valor": c.valor_estimado,
                            "publicacao": c.data_publicacao,
                            "abertura": c.abertura_proposta,
                            "encerramento": c.encerramento_proposta,
                            "payload": json.dumps(c.payload, ensure_ascii=False, default=str),
                        },
                    )
                    linha = await cur.fetchone()
                    if linha and linha[0]:
                        novas += 1
            await conn.commit()

        return ResultadoIngestao(vistas=len(contratacoes), novas=novas)

    async def contar(self) -> int:
        async with self._banco.conexao() as conn, conn.cursor() as cur:
            await cur.execute("SELECT count(*) FROM contratacao")
            linha = await cur.fetchone()
            return int(linha[0]) if linha else 0

    async def listar_abertas(
        self, *, uf: str | None = None, limite: int = 20
    ) -> list[dict[str, Any]]:
        """As que ainda dá tempo de disputar, quem encerra primeiro na frente."""
        sql = """
            SELECT numero_controle_pncp, modalidade_codigo, objeto, orgao_nome,
                   uf, municipio, valor_estimado, encerramento_proposta
            FROM contratacao
            WHERE (encerramento_proposta IS NULL OR encerramento_proposta >= now())
              -- o cast é obrigatório: sem ele o Postgres não consegue inferir
              -- o tipo do parâmetro quando ele vem NULL (AmbiguousParameter)
              AND (%(uf)s::text IS NULL OR uf = %(uf)s::text)
            ORDER BY encerramento_proposta ASC NULLS LAST
            LIMIT %(limite)s
        """
        async with (
            self._banco.conexao() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(sql, {"uf": uf, "limite": limite})
            return list(await cur.fetchall())


class ExecucaoRepo:
    """Histórico de execuções — o que responde 'a ingestão está saudável?'."""

    def __init__(self, banco: Banco) -> None:
        self._banco = banco

    async def abrir(
        self,
        *,
        uf: str | None,
        modalidades: list[int],
        janela_inicio: date | None,
        janela_fim: date | None,
    ) -> int:
        async with self._banco.conexao() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO execucao_ingestao (uf, modalidades, janela_inicio, janela_fim)
                    VALUES (%s, %s, %s, %s) RETURNING id
                    """,
                    (uf, modalidades, janela_inicio, janela_fim),
                )
                linha = await cur.fetchone()
            await conn.commit()
        return int(linha[0]) if linha else 0

    async def ultima(self) -> dict[str, Any] | None:
        """Quando foi a última coleta bem-sucedida, e o que ela trouxe.

        Existe porque um painel que mostra dado de quatro dias atrás sem
        dizer que ele é de quatro dias atrás não está informando: está
        enganando com precisão.
        """
        async with (
            self._banco.conexao() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(
                """
                SELECT iniciada_em, concluida_em, total_vistas, total_novas, uf, erro
                  FROM execucao_ingestao
                 WHERE concluida_em IS NOT NULL
                 ORDER BY concluida_em DESC
                 LIMIT 1
                """
            )
            linha = await cur.fetchone()
        return dict(linha) if linha else None

    async def concluir(
        self, execucao_id: int, *, resultado: ResultadoIngestao, erro: str | None = None
    ) -> None:
        async with self._banco.conexao() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE execucao_ingestao
                       SET concluida_em = now(), total_vistas = %s, total_novas = %s, erro = %s
                     WHERE id = %s
                    """,
                    (resultado.vistas, resultado.novas, erro, execucao_id),
                )
            await conn.commit()
