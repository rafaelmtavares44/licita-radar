"""O ciclo de atualização, sem saber quem está olhando.

Coletar do PNCP, pontuar contra o perfil e passar pelo grafo eram três
comandos de terminal — e a lógica morava dentro deles, misturada com
`console.print`. Isso funcionou enquanto o terminal era a única interface.
Quando o painel pediu um botão de atualizar, ficou claro o custo: a API
não tinha como reaproveitar nada.

Aqui a mesma sequência roda para qualquer chamador. Quem quiser mostrar
progresso passa um `aviso`, que é chamado a cada mudança de etapa — o
terminal imprime, a tela guarda para responder ao próximo `GET`.

A atualização leva minutos: varredura nacional, embeddings e grafo. Por
isso o progresso é parte do contrato, e não um detalhe de apresentação.
Uma barra que não anda é indistinguível de um travamento, como esta base
de código já aprendeu três vezes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

from licita_radar.config.perfil import Perfil
from licita_radar.config.settings import Settings, get_settings
from licita_radar.graph.runner import abrir_radar, processar
from licita_radar.ingest.pncp_client import coletar
from licita_radar.matching.encoder import FastEmbedEncoder, similaridade_cosseno
from licita_radar.matching.pontuacao import Avaliacao, Veredito, avaliar
from licita_radar.matching.semantico import MotorSemantico
from licita_radar.storage.db import Banco
from licita_radar.storage.matching_repo import AvaliacaoRepo, EmbeddingRepo, MatchingRepo
from licita_radar.storage.repositories import ContratacaoRepo, ExecucaoRepo

logger = logging.getLogger(__name__)

Etapa = Literal["parado", "coletando", "pontuando", "avaliando", "pronto", "erro"]

#: O que cada etapa está fazendo, em português de quem está esperando.
DESCRICAO: dict[str, str] = {
    "parado": "nada em andamento",
    "coletando": "buscando contratações no PNCP",
    "pontuando": "comparando com o seu perfil",
    "avaliando": "passando pelo grafo",
    "pronto": "atualizado",
    "erro": "a atualização falhou",
}


@dataclass
class Progresso:
    """O estado da atualização, do jeito que a tela precisa mostrar."""

    etapa: Etapa = "parado"
    coletadas: int = 0
    novas: int = 0
    avaliadas: int = 0
    candidatas: int = 0
    aguardando: int = 0
    erro: str | None = None

    @property
    def mensagem(self) -> str:
        return self.erro or DESCRICAO.get(self.etapa, self.etapa)

    @property
    def em_andamento(self) -> bool:
        return self.etapa in ("coletando", "pontuando", "avaliando")

    def como_dict(self) -> dict[str, Any]:
        return {
            "etapa": self.etapa,
            "mensagem": self.mensagem,
            "em_andamento": self.em_andamento,
            "coletadas": self.coletadas,
            "novas": self.novas,
            "avaliadas": self.avaliadas,
            "candidatas": self.candidatas,
            "aguardando": self.aguardando,
            "erro": self.erro,
        }


Aviso = Callable[[Progresso], None]


@dataclass
class Opcoes:
    """O que muda entre uma atualização e outra."""

    brasil: bool = True
    ate_dias: int = 30
    limite_do_grafo: int = 200
    #: Teto de contratações avaliadas por rodada. Existe para a primeira
    #: execução num banco cheio não virar uma espera de dez minutos.
    limite_de_avaliacao: int = 5000
    com_semantica: bool = True
    uf: str | None = None

    def alvo(self, perfil: Perfil) -> str | None:
        if self.brasil:
            return None
        return self.uf or (perfil.restricoes.ufs[0] if perfil.restricoes.ufs else None)


async def _coletar(
    perfil: Perfil, opcoes: Opcoes, settings: Settings, progresso: Progresso
) -> None:
    hoje = date.today()
    uf_alvo = opcoes.alvo(perfil)

    contratacoes = await coletar(
        modalidades=perfil.restricoes.modalidades,
        data_inicial=hoje,
        # O endpoint de propostas filtra pelo FIM do prazo: a data precisa
        # estar à frente, senão só volta o que já encerrou.
        data_final=hoje + timedelta(days=opcoes.ate_dias),
        uf=uf_alvo,
        apenas_abertas=True,
        settings=settings,
    )
    progresso.coletadas = len(contratacoes)

    async with Banco(settings) as banco:
        execucoes = ExecucaoRepo(banco)
        execucao_id = await execucoes.abrir(
            uf=uf_alvo,
            modalidades=list(perfil.restricoes.modalidades),
            janela_inicio=None,
            janela_fim=hoje,
        )
        resultado = await ContratacaoRepo(banco).salvar_muitas(contratacoes)
        await execucoes.concluir(execucao_id, resultado=resultado)

    progresso.novas = resultado.novas


async def pontuar(
    perfil: Perfil,
    *,
    settings: Settings | None = None,
    opcoes: Opcoes | None = None,
) -> list[Avaliacao]:
    """Compara o que está no banco com o perfil e grava as avaliações.

    Devolve a lista para quem quiser mostrar o funil — o terminal mostra,
    a tela só conta.
    """
    s = settings or get_settings()
    o = opcoes or Opcoes()

    async with Banco(s) as banco:
        matching = MatchingRepo(banco)
        contratacoes = await matching.carregar_contratacoes(
            uf=o.alvo(perfil), limite=o.limite_de_avaliacao
        )
        if not contratacoes:
            return []

        scores: dict[str, float] = {}
        if o.com_semantica:
            motor = MotorSemantico(FastEmbedEncoder())
            embeddings = EmbeddingRepo(banco)
            # A pergunta é feita sobre exatamente as que serão avaliadas:
            # sem esse recorte, "quais faltam?" devolvia outro conjunto e
            # parte do que era avaliado ficava sem vetor.
            pendentes = set(
                await embeddings.numeros_sem_embedding(
                    modelo=motor.encoder.nome,
                    entre=[c.numero_controle_pncp for c in contratacoes],
                )
            )
            a_codificar = [c for c in contratacoes if c.numero_controle_pncp in pendentes]
            if a_codificar:
                await embeddings.salvar_muitos(
                    motor.codificar_contratacoes(a_codificar), modelo=motor.encoder.nome
                )

            vetores = await matching.vetores([c.numero_controle_pncp for c in contratacoes])
            vetor_perfil = motor.vetor_do_perfil(perfil)
            scores = {
                numero: similaridade_cosseno(vetor_perfil, vetor)
                for numero, vetor in vetores.items()
                if vetor
            }

        avaliacoes = [
            avaliar(
                c,
                perfil,
                score_semantico=scores.get(c.numero_controle_pncp, 0.0),
                com_semantica=o.com_semantica,
            )
            for c in contratacoes
        ]
        await AvaliacaoRepo(banco).salvar_muitas(avaliacoes, perfil_id=perfil.id)

    return avaliacoes


async def _avaliar_no_grafo(
    perfil: Perfil, opcoes: Opcoes, settings: Settings, progresso: Progresso
) -> None:
    async with Banco(settings) as banco:
        contratacoes = await MatchingRepo(banco).carregar_contratacoes(
            uf=opcoes.alvo(perfil),
            limite=opcoes.limite_do_grafo,
            por_score_do_perfil=perfil.id,
        )
    if not contratacoes:
        return

    async with abrir_radar(perfil, settings) as grafo:
        execucoes = await processar(grafo, contratacoes, perfil)

    progresso.aguardando = sum(1 for e in execucoes if e.aguardando)


async def atualizar(
    perfil: Perfil,
    *,
    settings: Settings | None = None,
    opcoes: Opcoes | None = None,
    aviso: Aviso | None = None,
) -> Progresso:
    """Coleta, pontua e passa pelo grafo. Uma etapa por vez, com aviso.

    Erro em qualquer etapa para a sequência e fica registrado no
    progresso: seguir para a pontuação depois de a coleta falhar produziria
    um resultado que parece atualizado e não é.
    """
    s = settings or get_settings()
    o = opcoes or Opcoes()
    progresso = Progresso()

    def anunciar(etapa: Etapa) -> None:
        progresso.etapa = etapa
        logger.info("atualização: %s", progresso.mensagem)
        if aviso:
            aviso(progresso)

    try:
        anunciar("coletando")
        await _coletar(perfil, o, s, progresso)

        anunciar("pontuando")
        avaliacoes = await pontuar(perfil, settings=s, opcoes=o)
        progresso.avaliadas = len(avaliacoes)
        progresso.candidatas = sum(1 for a in avaliacoes if a.veredito is Veredito.CANDIDATA)

        anunciar("avaliando")
        await _avaliar_no_grafo(perfil, o, s, progresso)

        anunciar("pronto")
    except Exception as erro:
        logger.exception("a atualização falhou")
        progresso.erro = str(erro)
        anunciar("erro")

    return progresso


@dataclass
class Atualizacao:
    """Guarda o progresso da atualização em andamento, para a tela ler.

    Um objeto em memória basta: o painel é de uma pessoa só, e o que é
    durável já está no banco. Isto existe para distinguir "estou
    atualizando agora" de "não pedi nada" — distinção que o banco não
    consegue fazer.
    """

    progresso: Progresso = field(default_factory=Progresso)

    def registrar(self, novo: Progresso) -> None:
        self.progresso = novo

    @property
    def ocupada(self) -> bool:
        return self.progresso.em_andamento
