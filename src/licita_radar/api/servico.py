"""O que a API faz, sem saber que é HTTP.

Separado das rotas de propósito: aqui dá para testar "aprovar uma
contratação retoma o grafo e grava a análise" sem levantar servidor, e as
rotas ficam sendo só tradução de JSON.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from licita_radar import ciclo
from licita_radar.api.esquemas import (
    AfirmacaoDTO,
    AnaliseDTO,
    Detalhe,
    Escopo,
    EstadoDoTrabalho,
    ItemLista,
    ResumoFunil,
    chave,
)
from licita_radar.config.perfil import ESFERAS, Perfil
from licita_radar.graph.build import configuracao
from licita_radar.graph.runner import responder
from licita_radar.ingest.pncp_client import nome_da_modalidade
from licita_radar.matching.limpeza import limpar_objeto
from licita_radar.storage.analise_repo import AnaliseRepo
from licita_radar.storage.db import Banco
from licita_radar.storage.matching_repo import AvaliacaoRepo
from licita_radar.storage.repositories import ExecucaoRepo

logger = logging.getLogger(__name__)


@dataclass
class Trabalhos:
    """Quem está sendo analisado agora.

    Um dicionário em memória basta: o painel é de uma pessoa só, e a
    verdade durável está no banco — este registro serve para a tela saber
    que *já pediu* e ainda não voltou, distinção que o banco não consegue
    fazer sozinho.
    """

    _estados: dict[str, EstadoDoTrabalho] = field(default_factory=dict)
    #: A referência precisa existir: o asyncio só guarda referência fraca à
    #: tarefa, e uma tarefa sem dono pode ser coletada no meio do trabalho.
    _tarefas: set[asyncio.Task[None]] = field(default_factory=set)

    def acompanhar(self, tarefa: asyncio.Task[None]) -> None:
        self._tarefas.add(tarefa)
        tarefa.add_done_callback(self._tarefas.discard)

    def marcar(self, numero: str, estado: str, mensagem: str | None = None) -> None:
        self._estados[numero] = EstadoDoTrabalho(
            chave=chave(numero),
            estado=estado,  # type: ignore[arg-type]
            mensagem=mensagem,
        )

    def consultar(self, numero: str) -> EstadoDoTrabalho:
        return self._estados.get(
            numero, EstadoDoTrabalho(chave=chave(numero), estado="desconhecido")
        )

    def em_andamento(self, numero: str) -> bool:
        return self.consultar(numero).estado == "analisando"


@dataclass
class Contexto:
    """Tudo o que as rotas precisam, montado uma vez na subida do servidor.

    Os repositórios entram prontos, e não são construídos dentro de cada
    função, porque é o que permite testar a API inteira com dublês — sem
    Postgres, sem grafo compilado, sem rede.
    """

    banco: Banco | None
    grafo: Any
    perfil: Perfil
    avaliacoes: Any
    analises: Any
    execucoes: Any = None
    trabalhos: Trabalhos = field(default_factory=Trabalhos)
    atualizacao: ciclo.Atualizacao = field(default_factory=ciclo.Atualizacao)
    #: A tarefa da atualização em curso. Guardada porque o asyncio só
    #: mantém referência fraca, e tarefa sem dono pode ser coletada no meio.
    _tarefa_da_atualizacao: asyncio.Task[None] | None = None

    @classmethod
    def de_banco(cls, banco: Banco, grafo: Any, perfil: Perfil) -> Contexto:
        return cls(
            banco=banco,
            grafo=grafo,
            perfil=perfil,
            avaliacoes=AvaliacaoRepo(banco),
            analises=AnaliseRepo(banco),
            execucoes=ExecucaoRepo(banco),
        )


def _iso(valor: Any) -> str | None:
    return valor.isoformat() if hasattr(valor, "isoformat") else (str(valor) if valor else None)


def _url_pncp(numero: str) -> str | None:
    """A página pública da contratação, remontada a partir do número."""
    try:
        identificacao, ano = numero.split("/")
        cnpj, _, sequencial = identificacao.split("-")
    except ValueError:
        return None
    return f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{int(sequencial)}"


async def _estado_no_grafo(contexto: Contexto, numero: str) -> dict[str, Any]:
    """O que o grafo sabe sobre esta contratação, se souber alguma coisa."""
    try:
        instantaneo = await contexto.grafo.aget_state(configuracao(numero))
    except Exception as erro:  # thread inexistente, checkpoint de outro formato…
        logger.debug("sem estado de grafo para %s: %s", numero, erro)
        return {}
    valores: dict[str, Any] = dict(instantaneo.values or {})
    # `next` não vazio significa que há nó pendente: a thread dormiu no
    # interrupt e está esperando alguém decidir.
    valores["_aguardando"] = bool(instantaneo.next)
    return valores


def _para_item(linha: dict[str, Any], estado: dict[str, Any]) -> ItemLista:
    numero = str(linha["numero_controle_pncp"])
    return ItemLista(
        chave=chave(numero),
        numero_controle=numero,
        objeto=limpar_objeto(str(linha.get("objeto") or "")),
        orgao=linha.get("orgao_nome"),  # type: ignore[arg-type]
        uf=linha.get("uf"),  # type: ignore[arg-type]
        valor_estimado=float(linha["valor_estimado"]) if linha.get("valor_estimado") else None,
        encerramento=_iso(linha.get("encerramento_proposta")),
        url_pncp=_url_pncp(numero),
        score=float(linha.get("score_final") or 0.0),
        score_lexical=float(linha.get("score_lexical") or 0.0),
        score_semantico=float(linha.get("score_semantico") or 0.0),
        palavras_encontradas=list(linha.get("palavras_encontradas") or []),
        justificativa=estado.get("justificativa"),
        situacao=str(estado.get("situacao") or linha.get("veredito") or "coletada"),
        aguardando_decisao=bool(estado.get("_aguardando")),
        tem_analise=bool(estado.get("analise")),
    )


def _para_analise(bruta: dict[str, Any], documentos: list[dict[str, Any]]) -> AnaliseDTO:
    afirmacoes = [
        AfirmacaoDTO(
            assunto=str(a.get("assunto") or "riscos"),
            texto=str(a.get("texto") or ""),
            trecho=str(a.get("trecho") or ""),
            estado=a.get("estado") or ("sustentada" if a.get("confirmada") else "nao_encontrada"),
            observacao=str(a.get("observacao") or ""),
            numeros_sem_apoio=list(a.get("numeros_sem_apoio") or []),
        )
        for a in (bruta.get("afirmacoes") or [])
        if isinstance(a, dict)
    ]
    return AnaliseDTO(
        resumo=str(bruta.get("resumo") or ""),
        afirmacoes=afirmacoes,
        alertas=list(bruta.get("alertas") or []),
        confiabilidade=float(bruta.get("confiabilidade") or 0.0),
        modelo=bruta.get("modelo"),
        tokens=int(bruta.get("tokens") or 0),
        caracteres_lidos=int(bruta.get("caracteres_lidos") or 0),
        documentos=documentos,
    )


# ---------------------------------------------------------------- leituras


def _escopo(perfil: Perfil) -> Escopo:
    """Traduz as restrições do perfil para o que a tela mostra.

    O perfil fala em códigos — `[6, 8, 9]`, `["GO"]`. Ninguém lê o
    cabeçalho de um painel para descobrir o que é a modalidade 6.
    """
    r = perfil.restricoes
    return Escopo(
        foco=perfil.foco,
        modalidades=[nome_da_modalidade(m) for m in r.modalidades],
        ufs=list(r.ufs),
        esferas=[ESFERAS.get(e.upper(), e) for e in r.esferas],
    )


async def resumo(contexto: Contexto) -> ResumoFunil:
    por_veredito = await contexto.avaliacoes.resumo(perfil_id=contexto.perfil.id)

    total = analisadas = 0
    if contexto.banco is not None:
        async with contexto.banco.conexao() as conn:
            cursor = await conn.execute("SELECT count(*) FROM contratacao")
            total = int((await cursor.fetchone() or [0])[0])
            cursor = await conn.execute("SELECT count(*) FROM analise_edital")
            analisadas = int((await cursor.fetchone() or [0])[0])

    ultima = await contexto.execucoes.ultima() if contexto.execucoes else None

    # Quantas candidatas o filtro de prazo tirou da tela — o número existe
    # para a interface poder oferecer "mostrar encerradas" em vez de
    # simplesmente sumir com elas.
    abertas = await contexto.avaliacoes.ranking(
        perfil_id=contexto.perfil.id, limite=200, apenas_candidatas=True, apenas_abertas=True
    )
    todas = await contexto.avaliacoes.ranking(
        perfil_id=contexto.perfil.id, limite=200, apenas_candidatas=True
    )

    return ResumoFunil(
        contratacoes=total,
        avaliadas=sum(por_veredito.values()),
        candidatas=len(abertas),
        analisadas=analisadas,
        por_veredito=por_veredito,
        ultima_coleta=_iso(ultima.get("concluida_em")) if ultima else None,
        novas_na_ultima=int(ultima.get("total_novas") or 0) if ultima else 0,
        encerradas_escondidas=max(0, len(todas) - len(abertas)),
        escopo=_escopo(contexto.perfil),
        # As UFs vêm do que existe na lista, não da tabela de siglas: um
        # seletor com 27 opções das quais 22 não filtram nada é um seletor
        # que mente sobre o acervo.
        ufs_com_candidatas=sorted({str(x["uf"]) for x in abertas if x.get("uf")}),
    )


async def listar(
    contexto: Contexto,
    *,
    limite: int = 30,
    so_candidatas: bool = True,
    so_abertas: bool = True,
    uf: str | None = None,
) -> list[ItemLista]:
    linhas = await contexto.avaliacoes.ranking(
        perfil_id=contexto.perfil.id,
        limite=limite,
        apenas_candidatas=so_candidatas,
        apenas_abertas=so_abertas,
        uf=uf,
    )
    itens = []
    for linha in linhas:
        numero = str(linha["numero_controle_pncp"])
        itens.append(_para_item(linha, await _estado_no_grafo(contexto, numero)))
    return itens


async def detalhar(contexto: Contexto, numero: str) -> Detalhe | None:
    linhas = await contexto.avaliacoes.ranking(
        perfil_id=contexto.perfil.id, limite=1000, apenas_candidatas=False
    )
    linha = next((x for x in linhas if str(x["numero_controle_pncp"]) == numero), None)
    if linha is None:
        return None

    estado = await _estado_no_grafo(contexto, numero)
    item = _para_item(linha, estado)

    salva = await contexto.analises.buscar(numero)
    analise = None
    if salva:
        analise = _para_analise(
            {
                "resumo": salva.resumo,
                "afirmacoes": salva.afirmacoes,
                "alertas": salva.alertas,
                "confiabilidade": salva.confiabilidade,
                "modelo": salva.modelo,
                "tokens": salva.tokens,
                # A coluna existia e era gravada; a leitura tinha esquecido
                # dela, e a tela mostrava "0 caracteres lidos" para um
                # edital de 130 mil — número errado é pior que número
                # nenhum, porque parece diagnóstico.
                "caracteres_lidos": salva.caracteres_lidos,
            },
            salva.documentos,
        )
    elif estado.get("analise"):
        analise = _para_analise(estado["analise"], estado.get("documentos") or [])

    return Detalhe(
        **item.model_dump(),
        analise=analise,
        comentario_humano=estado.get("comentario_humano"),
        trilha=list(estado.get("trilha") or []),
        tokens_gastos=int(estado.get("tokens_gastos") or 0),
    )


# ------------------------------------------------------------- atualização


async def atualizar(
    contexto: Contexto, settings: Any = None, *, uf: str | None = None
) -> ciclo.Progresso:
    """Dispara o ciclo e volta na hora, como o "aprovar" faz.

    A atualização leva minutos — varredura nacional, embeddings e grafo.
    Segurar a resposta HTTP até o fim daria um botão que parece travado, e
    esta base de código já pagou por esse erro três vezes.
    """
    if contexto.atualizacao.ocupada:
        return contexto.atualizacao.progresso

    contexto.atualizacao.registrar(ciclo.Progresso(etapa="parado"))
    contexto.atualizacao.registrar(ciclo.Progresso(etapa="coletando"))

    async def _rodar() -> None:
        final = await ciclo.atualizar(
            contexto.perfil,
            settings=settings,
            opcoes=ciclo.Opcoes(brasil=uf is None, uf=uf),
            aviso=contexto.atualizacao.registrar,
        )
        contexto.atualizacao.registrar(final)

    tarefa = asyncio.create_task(_rodar())
    contexto._tarefa_da_atualizacao = tarefa
    tarefa.add_done_callback(lambda _: setattr(contexto, "_tarefa_da_atualizacao", None))
    return contexto.atualizacao.progresso


# ----------------------------------------------------------------- decisão


async def decidir(
    contexto: Contexto, numero: str, *, aprovar: bool, comentario: str | None = None
) -> EstadoDoTrabalho:
    """Retoma o grafo com a resposta da pessoa.

    Rejeitar é instantâneo — arquiva e acabou. Aprovar dispara o nó de
    análise, que baixa o edital e chama o modelo: um minuto, às vezes mais.
    Por isso a aprovação vira tarefa de fundo e esta função volta na hora
    com "analisando"; a tela pergunta depois se já ficou pronta.
    """
    if contexto.trabalhos.em_andamento(numero):
        return contexto.trabalhos.consultar(numero)

    if not aprovar:
        await responder(contexto.grafo, numero, aprovar=False, comentario=comentario)
        contexto.trabalhos.marcar(numero, "pronta", "rejeitada")
        return contexto.trabalhos.consultar(numero)

    contexto.trabalhos.marcar(numero, "analisando", "lendo o edital")
    contexto.trabalhos.acompanhar(
        asyncio.create_task(_analisar_em_segundo_plano(contexto, numero, comentario))
    )
    return contexto.trabalhos.consultar(numero)


async def _analisar_em_segundo_plano(
    contexto: Contexto, numero: str, comentario: str | None
) -> None:
    try:
        estado = await responder(contexto.grafo, numero, aprovar=True, comentario=comentario)
    except Exception as erro:
        logger.exception("falha ao analisar %s", numero)
        contexto.trabalhos.marcar(numero, "erro", str(erro))
        return

    analise = estado.get("analise")
    if analise:
        try:
            await contexto.analises.salvar(
                numero, analise=analise, documentos=list(estado.get("documentos") or [])
            )
        except Exception as erro:  # o grafo já terminou: gravar é o bônus
            logger.warning("análise de %s não foi gravada: %s", numero, erro)

    contexto.trabalhos.marcar(numero, "pronta")
