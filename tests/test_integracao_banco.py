"""Testes que exigem um Postgres de verdade.

Pulados por padrão. Para rodar:

    export LR_TEST_DATABASE_URL=postgresql://licita:licita@127.0.0.1:5432/licita_radar
    pytest -m integracao

O que está aqui não dá para testar com dublê: migração aplicada em ordem,
UPSERT idempotente e a detecção de "esta linha é nova?" dependem do
comportamento real do Postgres.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from licita_radar.config.settings import Settings
from licita_radar.ingest.modelos import Contratacao
from licita_radar.storage.db import Banco, ErroDeBanco, migrar, traduzir_falha
from licita_radar.storage.repositories import ContratacaoRepo, ExecucaoRepo

pytestmark = pytest.mark.integracao

URL_TESTE = os.environ.get("LR_TEST_DATABASE_URL")
sem_banco = pytest.mark.skipif(not URL_TESTE, reason="defina LR_TEST_DATABASE_URL para rodar")


def _contratacao(numero: str, *, objeto: str = "Desenvolvimento de sistemas web") -> Contratacao:
    return Contratacao(
        numero_controle_pncp=numero,
        modalidade_codigo=6,
        objeto=objeto,
        orgao_cnpj="01612092000123",
        orgao_nome="SECRETARIA DE ESTADO DA ADMINISTRACAO",
        uf="GO",
        municipio="Goiânia",
        valor_estimado=Decimal("1250000.00"),
        data_publicacao=datetime(2026, 9, 1, tzinfo=UTC).date(),
        abertura_proposta=datetime(2026, 9, 2, 8, 0, tzinfo=UTC),
        encerramento_proposta=datetime(2099, 9, 18, 17, 0, tzinfo=UTC),
        payload={"numeroControlePNCP": numero, "objetoCompra": objeto},
    )


@pytest.fixture
async def banco():  # type: ignore[no-untyped-def]
    settings = Settings(database_url=URL_TESTE or "")
    async with Banco(settings) as b:
        async with b.conexao() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DROP TABLE IF EXISTS contratacao, execucao_ingestao, schema_migracao CASCADE"
                )
            await conn.commit()
        await migrar(b)
        yield b


class TestTraducaoDeFalha:
    """As três falhas de banco precisam de três recados diferentes."""

    def test_conexao_recusada(self) -> None:
        recado = traduzir_falha(OSError("connection refused"), "postgresql://x")

        assert "O Postgres está rodando?" in recado
        assert "127.0.0.1" in recado  # a dica do Windows

    def test_senha_recusada_aponta_o_postgres_errado(self) -> None:
        erro = OSError('password authentication failed for user "licita"')
        recado = traduzir_falha(erro, "postgresql://x")

        assert "recusou a senha" in recado
        assert "OUTRO Postgres" in recado

    def test_role_inexistente_aponta_o_volume_velho(self) -> None:
        """Causa oposta à da senha recusada: o container é o certo, mas o
        volume foi inicializado antes e ignorou POSTGRES_USER."""
        recado = traduzir_falha(OSError('role "licita" does not exist'), "postgresql://x")

        assert "usuário não existe nele" in recado
        assert "docker compose down -v" in recado

    def test_banco_inexistente(self) -> None:
        recado = traduzir_falha(OSError('database "licita_radar" does not exist'), "postgresql://x")

        assert "não existe nesse servidor" in recado


async def test_banco_fora_do_ar_vira_recado_e_nao_stack_trace() -> None:
    """Falha em segundos, com dica de conserto. Não exige banco nenhum."""
    settings = Settings(
        database_url="postgresql://ninguem:segredo@127.0.0.1:9/inexistente",
        database_timeout_s=1.0,
    )

    with pytest.raises(ErroDeBanco) as capturado:
        async with Banco(settings):
            pass

    mensagem = str(capturado.value)
    assert "docker compose up -d db" in mensagem
    assert "segredo" not in mensagem  # a senha nunca aparece na mensagem


@sem_banco
async def test_migracao_e_idempotente(banco: Banco) -> None:
    """Rodar de novo não reaplica nada — o controle de versão funciona."""
    assert await migrar(banco) == []


@sem_banco
async def test_upsert_nao_duplica_e_conta_as_novas(banco: Banco) -> None:
    repo = ContratacaoRepo(banco)
    lote = [_contratacao("A-1-1/2026"), _contratacao("B-1-2/2026")]

    primeira = await repo.salvar_muitas(lote)
    assert (primeira.vistas, primeira.novas, primeira.atualizadas) == (2, 2, 0)

    segunda = await repo.salvar_muitas(lote)
    assert (segunda.vistas, segunda.novas, segunda.atualizadas) == (2, 0, 2)

    assert await repo.contar() == 2  # o ponto: rodar duas vezes não duplica


@sem_banco
async def test_upsert_atualiza_o_que_mudou(banco: Banco) -> None:
    repo = ContratacaoRepo(banco)
    await repo.salvar_muitas([_contratacao("C-1-3/2026", objeto="Objeto antigo do edital")])
    await repo.salvar_muitas([_contratacao("C-1-3/2026", objeto="Objeto retificado do edital")])

    linhas = await repo.listar_abertas(uf="GO")
    assert len(linhas) == 1
    assert linhas[0]["objeto"] == "Objeto retificado do edital"


@sem_banco
async def test_lista_vazia_nao_toca_o_banco(banco: Banco) -> None:
    resultado = await ContratacaoRepo(banco).salvar_muitas([])
    assert (resultado.vistas, resultado.novas) == (0, 0)


@sem_banco
async def test_execucao_registra_o_resultado(banco: Banco) -> None:
    execucoes = ExecucaoRepo(banco)
    repo = ContratacaoRepo(banco)

    execucao_id = await execucoes.abrir(
        uf="GO", modalidades=[6, 8], janela_inicio=None, janela_fim=None
    )
    resultado = await repo.salvar_muitas([_contratacao("D-1-4/2026")])
    await execucoes.concluir(execucao_id, resultado=resultado)

    async with banco.conexao() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT total_vistas, total_novas, concluida_em FROM execucao_ingestao WHERE id = %s",
            (execucao_id,),
        )
        linha = await cur.fetchone()

    assert linha is not None
    assert linha[0] == 1
    assert linha[1] == 1
    assert linha[2] is not None
