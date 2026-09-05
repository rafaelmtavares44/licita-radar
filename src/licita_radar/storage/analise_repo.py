"""Persistência do M4: a análise do edital e suas evidências.

Uma linha por contratação, sobrescrita quando a análise é refeita — não é
histórico: se o resumo foi gerado de novo, é porque o anterior não servia.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from psycopg.rows import dict_row

from licita_radar.storage.db import Banco

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnaliseSalva:
    numero_controle_pncp: str
    resumo: str | None
    afirmacoes: list[dict[str, Any]]
    documentos: list[dict[str, Any]]
    alertas: list[str]
    confiabilidade: float
    modelo: str | None
    tokens: int


class AnaliseRepo:
    def __init__(self, banco: Banco) -> None:
        self._banco = banco

    async def salvar(
        self,
        numero_controle: str,
        *,
        analise: dict[str, Any],
        documentos: list[dict[str, Any]] | None = None,
    ) -> None:
        sql = """
            INSERT INTO analise_edital (
                numero_controle_pncp, resumo, afirmacoes, documentos, alertas,
                confiabilidade, modelo, tokens, caracteres_lidos
            ) VALUES (
                %(numero)s, %(resumo)s, %(afirmacoes)s::jsonb, %(documentos)s::jsonb,
                %(alertas)s::jsonb, %(confiabilidade)s, %(modelo)s, %(tokens)s, %(caracteres)s
            )
            ON CONFLICT (numero_controle_pncp) DO UPDATE SET
                resumo           = EXCLUDED.resumo,
                afirmacoes       = EXCLUDED.afirmacoes,
                documentos       = EXCLUDED.documentos,
                alertas          = EXCLUDED.alertas,
                confiabilidade   = EXCLUDED.confiabilidade,
                modelo           = EXCLUDED.modelo,
                tokens           = EXCLUDED.tokens,
                caracteres_lidos = EXCLUDED.caracteres_lidos,
                criado_em        = now()
        """
        async with self._banco.conexao() as conn:
            await conn.execute(
                sql,
                {
                    "numero": numero_controle,
                    "resumo": analise.get("resumo") or None,
                    "afirmacoes": json.dumps(analise.get("afirmacoes", []), ensure_ascii=False),
                    "documentos": json.dumps(documentos or [], ensure_ascii=False),
                    "alertas": json.dumps(analise.get("alertas", []), ensure_ascii=False),
                    "confiabilidade": float(analise.get("confiabilidade", 0.0)),
                    "modelo": analise.get("modelo"),
                    "tokens": int(analise.get("tokens", 0)),
                    "caracteres": int(analise.get("caracteres_lidos", 0)),
                },
            )

    async def buscar(self, numero_controle: str) -> AnaliseSalva | None:
        sql = """
            SELECT numero_controle_pncp, resumo, afirmacoes, documentos, alertas,
                   confiabilidade, modelo, tokens
              FROM analise_edital
             WHERE numero_controle_pncp = %(numero)s
        """
        async with self._banco.conexao() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, {"numero": numero_controle})
            linha = await cur.fetchone()

        if not linha:
            return None
        return AnaliseSalva(
            numero_controle_pncp=linha["numero_controle_pncp"],
            resumo=linha["resumo"],
            afirmacoes=linha["afirmacoes"] or [],
            documentos=linha["documentos"] or [],
            alertas=linha["alertas"] or [],
            confiabilidade=float(linha["confiabilidade"]),
            modelo=linha["modelo"],
            tokens=int(linha["tokens"]),
        )

    async def listar(self, *, limite: int = 20) -> list[AnaliseSalva]:
        sql = """
            SELECT numero_controle_pncp, resumo, afirmacoes, documentos, alertas,
                   confiabilidade, modelo, tokens
              FROM analise_edital
             ORDER BY criado_em DESC
             LIMIT %(limite)s
        """
        async with self._banco.conexao() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, {"limite": limite})
            linhas = await cur.fetchall()

        return [
            AnaliseSalva(
                numero_controle_pncp=linha["numero_controle_pncp"],
                resumo=linha["resumo"],
                afirmacoes=linha["afirmacoes"] or [],
                documentos=linha["documentos"] or [],
                alertas=linha["alertas"] or [],
                confiabilidade=float(linha["confiabilidade"]),
                modelo=linha["modelo"],
                tokens=int(linha["tokens"]),
            )
            for linha in linhas
        ]
