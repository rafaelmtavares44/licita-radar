"""Persistência do M2: embeddings e avaliações.

Sobre o tipo `vector`: em vez de trazer a dependência `pgvector-python`, o
vetor é enviado e lido como texto no formato que o próprio Postgres usa
(`[0.1,0.2,...]`), com um `::vector` explícito na consulta. São dez linhas
de conversão contra mais um pacote na árvore — e num projeto opensource
cada dependência a menos é um obstáculo a menos.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from psycopg.rows import dict_row

from licita_radar.ingest.modelos import Contratacao
from licita_radar.matching.pontuacao import Avaliacao
from licita_radar.matching.semantico import TextoCodificado
from licita_radar.storage.db import Banco

logger = logging.getLogger(__name__)


def vetor_para_texto(vetor: Sequence[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vetor) + "]"


def texto_para_vetor(texto: str | None) -> list[float]:
    if not texto:
        return []
    return [float(parte) for parte in texto.strip("[]").split(",") if parte]


class EmbeddingRepo:
    def __init__(self, banco: Banco) -> None:
        self._banco = banco

    async def salvar_muitos(self, codificados: Sequence[TextoCodificado], *, modelo: str) -> int:
        if not codificados:
            return 0

        sql = """
            INSERT INTO contratacao_embedding (numero_controle_pncp, modelo, texto, embedding)
            VALUES (%(numero)s, %(modelo)s, %(texto)s, %(vetor)s::vector)
            ON CONFLICT (numero_controle_pncp) DO UPDATE SET
                modelo = EXCLUDED.modelo,
                texto  = EXCLUDED.texto,
                embedding = EXCLUDED.embedding,
                criado_em = now()
        """
        async with self._banco.conexao() as conn:
            async with conn.cursor() as cur:
                for item in codificados:
                    await cur.execute(
                        sql,
                        {
                            "numero": item.numero_controle_pncp,
                            "modelo": modelo,
                            "texto": item.texto,
                            "vetor": vetor_para_texto(item.vetor),
                        },
                    )
            await conn.commit()
        return len(codificados)

    async def numeros_sem_embedding(
        self, *, modelo: str, entre: Sequence[str] | None = None, limite: int = 5000
    ) -> list[str]:
        """Quem ainda não foi codificado — ou foi por outro modelo.

        `entre` restringe a pergunta a um conjunto conhecido. Sem ele, esta
        consulta e a que carrega as contratações usavam ordens e limites
        diferentes, e sobravam contratações avaliadas sem embedding nenhum —
        pontuando 0,00 na semântica por não terem sido codificadas.
        """
        sql = """
            SELECT c.numero_controle_pncp
              FROM contratacao c
              LEFT JOIN contratacao_embedding e USING (numero_controle_pncp)
             WHERE (e.numero_controle_pncp IS NULL OR e.modelo <> %(modelo)s)
               AND (%(entre)s::text[] IS NULL
                    OR c.numero_controle_pncp = ANY(%(entre)s::text[]))
             ORDER BY c.ingerido_em DESC
             LIMIT %(limite)s
        """
        async with self._banco.conexao() as conn, conn.cursor() as cur:
            await cur.execute(
                sql,
                {
                    "modelo": modelo,
                    "entre": list(entre) if entre is not None else None,
                    "limite": limite,
                },
            )
            return [linha[0] for linha in await cur.fetchall()]


class MatchingRepo:
    """Leituras que juntam contratação e vetor, para alimentar o funil."""

    def __init__(self, banco: Banco) -> None:
        self._banco = banco

    async def carregar_contratacoes(
        self,
        *,
        numeros: Sequence[str] | None = None,
        uf: str | None = None,
        limite: int = 500,
        por_score_do_perfil: str | None = None,
    ) -> list[Contratacao]:
        """Carrega contratações para avaliar.

        `por_score_do_perfil` muda a ordem de "quem encerra primeiro" para
        "quem pontuou melhor". A diferença importa quando há limite: com um
        `--limite 20` sobre a ordem de prazo, as vinte primeiras a encerrar
        raramente são as vinte mais aderentes, e a execução não acha nada.
        """
        ordem = (
            "COALESCE(a.score_final, 0) DESC, c.encerramento_proposta ASC NULLS LAST"
            if por_score_do_perfil
            else "c.encerramento_proposta ASC NULLS LAST"
        )
        sql = f"""
            SELECT c.numero_controle_pncp, c.modalidade_codigo, c.objeto, c.orgao_cnpj,
                   c.orgao_nome, c.esfera, c.uf, c.municipio, c.valor_estimado,
                   c.data_publicacao, c.abertura_proposta, c.encerramento_proposta, c.payload
              FROM contratacao c
              LEFT JOIN avaliacao a
                     ON a.numero_controle_pncp = c.numero_controle_pncp
                    AND a.perfil_id = %(perfil)s
             WHERE (%(numeros)s::text[] IS NULL
                    OR c.numero_controle_pncp = ANY(%(numeros)s::text[]))
               AND (%(uf)s::text IS NULL OR c.uf = %(uf)s::text)
             ORDER BY {ordem}
             LIMIT %(limite)s
        """
        async with (
            self._banco.conexao() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(
                sql,
                {
                    "numeros": list(numeros) if numeros is not None else None,
                    "uf": uf,
                    "limite": limite,
                    "perfil": por_score_do_perfil,
                },
            )
            linhas = await cur.fetchall()

        return [
            Contratacao(
                numero_controle_pncp=linha["numero_controle_pncp"],
                modalidade_codigo=linha["modalidade_codigo"],
                objeto=linha["objeto"],
                orgao_cnpj=linha["orgao_cnpj"],
                orgao_nome=linha["orgao_nome"],
                esfera=linha["esfera"],
                uf=linha["uf"],
                municipio=linha["municipio"],
                valor_estimado=linha["valor_estimado"],
                data_publicacao=linha["data_publicacao"],
                abertura_proposta=linha["abertura_proposta"],
                encerramento_proposta=linha["encerramento_proposta"],
                payload=linha["payload"],
            )
            for linha in linhas
        ]

    async def vetores(self, numeros: Sequence[str]) -> dict[str, list[float]]:
        if not numeros:
            return {}
        sql = """
            SELECT numero_controle_pncp, embedding::text AS vetor
              FROM contratacao_embedding
             WHERE numero_controle_pncp = ANY(%(numeros)s::text[])
        """
        async with self._banco.conexao() as conn, conn.cursor() as cur:
            await cur.execute(sql, {"numeros": list(numeros)})
            return {linha[0]: texto_para_vetor(linha[1]) for linha in await cur.fetchall()}


class AvaliacaoRepo:
    def __init__(self, banco: Banco) -> None:
        self._banco = banco

    async def salvar_muitas(self, avaliacoes: Sequence[Avaliacao], *, perfil_id: str) -> int:
        if not avaliacoes:
            return 0

        sql = """
            INSERT INTO avaliacao (
                numero_controle_pncp, perfil_id, score_lexical, score_semantico,
                score_final, veredito, palavras_encontradas, motivo
            )
            VALUES (%(numero)s, %(perfil)s, %(lex)s, %(sem)s, %(final)s,
                    %(veredito)s, %(palavras)s, %(motivo)s)
            ON CONFLICT (numero_controle_pncp, perfil_id) DO UPDATE SET
                score_lexical        = EXCLUDED.score_lexical,
                score_semantico      = EXCLUDED.score_semantico,
                score_final          = EXCLUDED.score_final,
                veredito             = EXCLUDED.veredito,
                palavras_encontradas = EXCLUDED.palavras_encontradas,
                motivo               = EXCLUDED.motivo,
                criado_em            = now()
        """
        async with self._banco.conexao() as conn:
            async with conn.cursor() as cur:
                for a in avaliacoes:
                    await cur.execute(
                        sql,
                        {
                            "numero": a.contratacao.numero_controle_pncp,
                            "perfil": perfil_id,
                            "lex": a.score_lexical,
                            "sem": a.score_semantico,
                            "final": a.score_final,
                            "veredito": str(a.veredito),
                            "palavras": list(a.palavras_encontradas),
                            "motivo": a.motivo,
                        },
                    )
            await conn.commit()
        return len(avaliacoes)

    async def ranking(
        self,
        *,
        perfil_id: str,
        limite: int = 20,
        apenas_candidatas: bool = False,
        apenas_abertas: bool = False,
        uf: str | None = None,
    ) -> list[dict[str, object]]:
        """O ranking do perfil.

        `apenas_abertas` existe porque licitação com prazo vencido é ruído
        num radar: ela ocupa a lista, empurra para baixo o que ainda dá
        tempo de disputar, e não há nada a fazer a respeito dela. No
        terminal isso passava; numa tela que fica aberta o dia todo, não.

        `uf` filtra aqui, e não na tela, porque o corte vem antes do
        `LIMIT`: filtrar depois faria "as 40 melhores do Brasil, das quais
        as de Goiás" — que não é a mesma lista que "as 40 melhores de
        Goiás", e é a segunda que a pessoa pediu.
        """
        sql = """
            SELECT a.numero_controle_pncp, a.score_lexical, a.score_semantico, a.score_final,
                   a.veredito, a.palavras_encontradas, a.motivo,
                   c.objeto, c.orgao_nome, c.uf, c.municipio,
                   c.valor_estimado, c.encerramento_proposta
              FROM avaliacao a
              JOIN contratacao c USING (numero_controle_pncp)
             WHERE a.perfil_id = %(perfil)s
               AND (NOT %(so_candidatas)s OR a.veredito = 'candidata')
               AND (NOT %(so_abertas)s
                    OR c.encerramento_proposta IS NULL
                    OR c.encerramento_proposta >= now())
               AND (%(uf)s::text IS NULL OR c.uf = %(uf)s)
             ORDER BY a.score_final DESC, c.encerramento_proposta ASC NULLS LAST
             LIMIT %(limite)s
        """
        async with (
            self._banco.conexao() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(
                sql,
                {
                    "perfil": perfil_id,
                    "so_candidatas": apenas_candidatas,
                    "so_abertas": apenas_abertas,
                    "uf": uf,
                    "limite": limite,
                },
            )
            return list(await cur.fetchall())

    async def resumo(self, *, perfil_id: str) -> dict[str, int]:
        """Quantas caíram em cada veredito — o retrato do funil."""
        async with self._banco.conexao() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT veredito, count(*) FROM avaliacao WHERE perfil_id = %s GROUP BY veredito",
                (perfil_id,),
            )
            return {linha[0]: int(linha[1]) for linha in await cur.fetchall()}
