"""Integração do M2: pgvector, embeddings e avaliações num Postgres real.

    export LR_TEST_DATABASE_URL=postgresql://licita:licita@127.0.0.1:5432/licita_radar
    pytest -m integracao

O que se testa aqui não dá para testar com dublê: se o vetor sobrevive à
ida e volta pelo tipo `vector`, se o UPSERT de avaliação não duplica, e se
a consulta de ranking ordena como se espera.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from licita_radar.config.perfil import Perfil
from licita_radar.config.settings import Settings
from licita_radar.ingest.modelos import Contratacao
from licita_radar.matching.pontuacao import avaliar
from licita_radar.matching.semantico import MotorSemantico
from licita_radar.storage.db import Banco, migrar
from licita_radar.storage.matching_repo import (
    AvaliacaoRepo,
    EmbeddingRepo,
    MatchingRepo,
    texto_para_vetor,
    vetor_para_texto,
)
from licita_radar.storage.repositories import ContratacaoRepo
from tests.dubles import EncoderFalso

pytestmark = pytest.mark.integracao

URL_TESTE = os.environ.get("LR_TEST_DATABASE_URL")
sem_banco = pytest.mark.skipif(not URL_TESTE, reason="defina LR_TEST_DATABASE_URL para rodar")

DIMENSAO_MIGRACAO = 384


def _contratacao(numero: str, objeto: str) -> Contratacao:
    return Contratacao(
        numero_controle_pncp=numero,
        modalidade_codigo=6,
        objeto=objeto,
        orgao_nome="PREFEITURA DE GOIÂNIA",
        uf="GO",
        municipio="Goiânia",
        valor_estimado=Decimal("300000.00"),
        encerramento_proposta=datetime.now(UTC) + timedelta(days=15),
        payload={"objetoCompra": objeto},
    )


@pytest.fixture
async def banco():  # type: ignore[no-untyped-def]
    settings = Settings(database_url=URL_TESTE or "")
    async with Banco(settings) as b:
        async with b.conexao() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DROP TABLE IF EXISTS avaliacao, contratacao_embedding, contratacao, "
                    "execucao_ingestao, schema_migracao CASCADE"
                )
            await conn.commit()
        await migrar(b)
        yield b


def _preencher(vetor: list[float]) -> list[float]:
    """Estica o vetor do dublê até a dimensão que a migração declara."""
    return (vetor + [0.0] * DIMENSAO_MIGRACAO)[:DIMENSAO_MIGRACAO]


class TestSerializacaoDeVetor:
    def test_ida_e_volta_preserva_os_valores(self) -> None:
        original = [0.125, -0.5, 0.0, 1.0]

        assert texto_para_vetor(vetor_para_texto(original)) == pytest.approx(original)

    def test_texto_vazio_vira_lista_vazia(self) -> None:
        assert texto_para_vetor(None) == []
        assert texto_para_vetor("") == []


@sem_banco
async def test_migracao_002_cria_a_extensao_e_o_indice(banco: Banco) -> None:
    async with banco.conexao() as conn, conn.cursor() as cur:
        await cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        assert await cur.fetchone() is not None

        await cur.execute("SELECT 1 FROM pg_indexes WHERE indexname = 'idx_embedding_hnsw'")
        assert await cur.fetchone() is not None


@sem_banco
async def test_embedding_sobrevive_a_ida_e_volta_pelo_banco(banco: Banco) -> None:
    contratacao = _contratacao("A-1-1/2026", "Desenvolvimento de sistema web")
    await ContratacaoRepo(banco).salvar_muitas([contratacao])

    motor = MotorSemantico(EncoderFalso())
    codificados = motor.codificar_contratacoes([contratacao])
    esticados = [
        type(c)(
            numero_controle_pncp=c.numero_controle_pncp, texto=c.texto, vetor=_preencher(c.vetor)
        )
        for c in codificados
    ]
    await EmbeddingRepo(banco).salvar_muitos(esticados, modelo="teste/dublê")

    lidos = await MatchingRepo(banco).vetores(["A-1-1/2026"])

    assert len(lidos["A-1-1/2026"]) == DIMENSAO_MIGRACAO
    assert lidos["A-1-1/2026"][: len(codificados[0].vetor)] == pytest.approx(
        codificados[0].vetor, abs=1e-5
    )


@sem_banco
async def test_pendentes_de_embedding_encolhe_conforme_se_codifica(banco: Banco) -> None:
    contratacoes = [
        _contratacao("B-1-1/2026", "Desenvolvimento de software"),
        _contratacao("B-1-2/2026", "Aquisição de toner"),
    ]
    await ContratacaoRepo(banco).salvar_muitas(contratacoes)
    embeddings = EmbeddingRepo(banco)

    assert len(await embeddings.numeros_sem_embedding(modelo="m1")) == 2

    motor = MotorSemantico(EncoderFalso())
    primeiro = motor.codificar_contratacoes(contratacoes[:1])
    await embeddings.salvar_muitos(
        [
            type(c)(
                numero_controle_pncp=c.numero_controle_pncp,
                texto=c.texto,
                vetor=_preencher(c.vetor),
            )
            for c in primeiro
        ],
        modelo="m1",
    )

    assert await embeddings.numeros_sem_embedding(modelo="m1") == ["B-1-2/2026"]
    # trocar de modelo invalida tudo que foi codificado pelo anterior
    assert len(await embeddings.numeros_sem_embedding(modelo="m2")) == 2


@sem_banco
async def test_pendentes_pode_ser_restrito_a_um_conjunto(banco: Banco) -> None:
    """O bug que fez licitação de software pontuar 0,00 na semântica.

    A consulta de pendentes e a que carrega as contratações usavam ordens e
    limites diferentes. Parte do que era avaliado nunca era codificada, e
    ficava com vetor ausente — indistinguível de "nada a ver com o perfil".
    """
    contratacoes = [
        _contratacao("F-1-1/2026", "Licença de software de design gráfico"),
        _contratacao("F-1-2/2026", "Aquisição de gás de cozinha"),
        _contratacao("F-1-3/2026", "Serviço de jardinagem"),
    ]
    await ContratacaoRepo(banco).salvar_muitas(contratacoes)
    embeddings = EmbeddingRepo(banco)

    todas = await embeddings.numeros_sem_embedding(modelo="m1")
    assert len(todas) == 3

    recorte = await embeddings.numeros_sem_embedding(
        modelo="m1", entre=["F-1-1/2026", "F-1-3/2026"]
    )
    assert sorted(recorte) == ["F-1-1/2026", "F-1-3/2026"]


@sem_banco
async def test_avaliacao_faz_upsert_e_ranking_ordena(banco: Banco, perfil: Perfil) -> None:
    contratacoes = [
        _contratacao("C-1-1/2026", "Desenvolvimento de software e sustentação de sistemas"),
        _contratacao("C-1-2/2026", "Aquisição de adubos para a horta municipal"),
    ]
    await ContratacaoRepo(banco).salvar_muitas(contratacoes)

    repo = AvaliacaoRepo(banco)
    avaliacoes = [
        avaliar(contratacoes[0], perfil, score_semantico=0.95),
        avaliar(contratacoes[1], perfil, score_semantico=0.05),
    ]
    await repo.salvar_muitas(avaliacoes, perfil_id=perfil.id)
    await repo.salvar_muitas(avaliacoes, perfil_id=perfil.id)  # de novo: não pode duplicar

    ranking = await repo.ranking(perfil_id=perfil.id, limite=10)

    assert len(ranking) == 2
    assert ranking[0]["numero_controle_pncp"] == "C-1-1/2026"
    assert float(ranking[0]["score_final"]) > float(ranking[1]["score_final"])  # type: ignore[arg-type]
    assert "desenvolvimento de software" in list(ranking[0]["palavras_encontradas"])  # type: ignore[call-overload]


@sem_banco
async def test_resumo_do_funil_conta_por_veredito(banco: Banco, perfil: Perfil) -> None:
    contratacoes = [
        _contratacao("D-1-1/2026", "Desenvolvimento de software e sustentação de sistemas"),
        _contratacao("D-1-2/2026", "AQUISIÇÃO DE TONER E IMPRESSORA"),
    ]
    await ContratacaoRepo(banco).salvar_muitas(contratacoes)

    repo = AvaliacaoRepo(banco)
    await repo.salvar_muitas(
        [
            avaliar(contratacoes[0], perfil, score_semantico=0.95),
            avaliar(contratacoes[1], perfil, score_semantico=0.95),
        ],
        perfil_id=perfil.id,
    )

    resumo = await repo.resumo(perfil_id=perfil.id)

    assert resumo.get("candidata") == 1
    assert resumo.get("vetada") == 1


@sem_banco
async def test_carregar_por_score_traz_as_melhores_primeiro(banco: Banco, perfil: Perfil) -> None:
    """Com limite, a ordem decide o que o grafo vê.

    Ordenar por prazo gasta as vagas nas que encerram cedo — que raramente
    são as mais aderentes. Foi o que fez um `--limite 20` real não achar
    nenhuma candidata, sendo que havia seis no lote completo.
    """
    ruim = _contratacao("ORD-1-1/2026", "Aquisição de adubos")
    boa = _contratacao("ORD-1-2/2026", "Desenvolvimento de software e sustentação de sistemas")
    # a ruim encerra antes: pela ordem de prazo, ela viria primeiro
    ruim = ruim.model_copy(update={"encerramento_proposta": datetime.now(UTC) + timedelta(days=1)})
    boa = boa.model_copy(update={"encerramento_proposta": datetime.now(UTC) + timedelta(days=30)})
    await ContratacaoRepo(banco).salvar_muitas([ruim, boa])

    await AvaliacaoRepo(banco).salvar_muitas(
        [
            avaliar(ruim, perfil, score_semantico=0.02),
            avaliar(boa, perfil, score_semantico=0.90),
        ],
        perfil_id=perfil.id,
    )

    matching = MatchingRepo(banco)
    por_prazo = await matching.carregar_contratacoes(limite=1)
    por_score = await matching.carregar_contratacoes(limite=1, por_score_do_perfil=perfil.id)

    assert por_prazo[0].numero_controle_pncp == "ORD-1-1/2026"
    assert por_score[0].numero_controle_pncp == "ORD-1-2/2026"


@sem_banco
async def test_carregar_contratacoes_filtra_por_uf(banco: Banco) -> None:
    goias = _contratacao("E-1-1/2026", "Desenvolvimento de sistema")
    outra = Contratacao(
        numero_controle_pncp="E-1-2/2026",
        modalidade_codigo=6,
        objeto="Desenvolvimento de sistema",
        uf="SP",
        payload={},
    )
    await ContratacaoRepo(banco).salvar_muitas([goias, outra])

    matching = MatchingRepo(banco)

    assert len(await matching.carregar_contratacoes(uf="GO")) == 1
    assert len(await matching.carregar_contratacoes()) == 2
