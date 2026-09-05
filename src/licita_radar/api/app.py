"""O aplicativo FastAPI: rotas finas por cima de `servico`.

`criar_app()` é fábrica de verdade — devolve uma instância nova a cada
chamada, em vez de configurar um objeto de módulo. A diferença aparece no
segundo uso: middleware não pode ser adicionado depois que o aplicativo
subiu, então um singleton compartilhado quebra ao ser montado duas vezes,
que é exatamente o que uma suíte de testes faz.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from licita_radar.api import servico
from licita_radar.api.esquemas import (
    Detalhe,
    EstadoDoTrabalho,
    ItemLista,
    PedidoDeDecisao,
    ResumoFunil,
    Saude,
    numero_de,
)
from licita_radar.api.servico import Contexto
from licita_radar.config.perfil import carregar_perfil
from licita_radar.config.settings import Settings, get_settings
from licita_radar.graph.runner import abrir_radar
from licita_radar.storage.db import Banco

logger = logging.getLogger(__name__)

VERSAO = "0.1.0.dev0"

#: Onde o painel construído fica depois do `npm run build`. Não existe no
#: repositório: é artefato de build, e o servidor funciona sem ele — a API
#: sozinha já é útil, e em desenvolvimento quem serve a tela é o Vite.
PAINEL = Path(__file__).resolve().parents[3] / "web" / "dist"


def obter_contexto(request: Request) -> Contexto:
    """Injeção que os testes substituem para não precisar de banco."""
    contexto: Contexto | None = getattr(request.app.state, "contexto", None)
    if contexto is None:  # pragma: no cover — só se a subida falhar
        raise HTTPException(status_code=503, detail="o servidor ainda está subindo")
    return contexto


Ctx = Annotated[Contexto, Depends(obter_contexto)]


def criar_app(settings: Settings | None = None, *, servir_painel: bool = True) -> FastAPI:
    """Monta o aplicativo. Sem `web/dist`, serve só a API."""
    s = settings or get_settings()

    @asynccontextmanager
    async def ciclo(aplicativo: FastAPI) -> AsyncIterator[None]:
        perfil = carregar_perfil(s.perfil_path)
        async with AsyncExitStack() as pilha:
            banco = await pilha.enter_async_context(Banco(s))
            # O grafo fica aberto durante toda a vida do servidor: o
            # checkpointer do LangGraph mantém a própria conexão, e abri-lo
            # por requisição custaria um handshake com o Postgres a cada
            # clique da tela.
            grafo = await pilha.enter_async_context(abrir_radar(perfil, s))
            aplicativo.state.contexto = Contexto.de_banco(banco, grafo, perfil)
            logger.info("painel pronto — perfil %s", perfil.nome)
            yield

    app = FastAPI(
        title="licita-radar",
        version=VERSAO,
        description="Radar de licitações públicas com análise de edital.",
        lifespan=ciclo,
    )

    # Em desenvolvimento o painel roda no Vite, noutra porta: sem CORS, o
    # navegador recusa as chamadas antes de elas saírem.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/saude", response_model=Saude)
    async def saude(contexto: Ctx) -> Saude:
        try:
            if contexto.banco is not None:
                async with contexto.banco.conexao() as conn:
                    await conn.execute("SELECT 1")
            banco_ok = True
        except Exception:
            banco_ok = False
        return Saude(banco=banco_ok, llm=s.llm_modelo, perfil=contexto.perfil.nome, versao=VERSAO)

    @app.get("/api/resumo", response_model=ResumoFunil)
    async def resumo(contexto: Ctx) -> ResumoFunil:
        return await servico.resumo(contexto)

    @app.get("/api/candidatas", response_model=list[ItemLista])
    async def candidatas(
        contexto: Ctx,
        limite: Annotated[int, Query(ge=1, le=200)] = 30,
        todas: Annotated[bool, Query(description="Inclui as descartadas pelo funil")] = False,
    ) -> list[ItemLista]:
        return await servico.listar(contexto, limite=limite, so_candidatas=not todas)

    @app.get("/api/candidatas/{chave}", response_model=Detalhe)
    async def detalhe(contexto: Ctx, chave: str) -> Detalhe:
        achado = await servico.detalhar(contexto, numero_de(chave))
        if achado is None:
            raise HTTPException(status_code=404, detail="contratação não encontrada")
        return achado

    @app.post("/api/candidatas/{chave}/decisao", response_model=EstadoDoTrabalho, status_code=202)
    async def decidir(contexto: Ctx, chave: str, pedido: PedidoDeDecisao) -> EstadoDoTrabalho:
        """Volta na hora. Aprovar dispara a leitura do edital em segundo plano."""
        return await servico.decidir(
            contexto,
            numero_de(chave),
            aprovar=pedido.decisao == "aprovar",
            comentario=pedido.comentario,
        )

    @app.get("/api/candidatas/{chave}/decisao", response_model=EstadoDoTrabalho)
    async def estado_da_decisao(contexto: Ctx, chave: str) -> EstadoDoTrabalho:
        return contexto.trabalhos.consultar(numero_de(chave))

    if servir_painel and PAINEL.is_dir():
        _montar_painel(app)

    return app


def _montar_painel(app: FastAPI) -> None:
    """Serve o front construído, com o index respondendo qualquer rota.

    Quem roteia `/candidatas/xyz` é o React, não o servidor: sem a
    devolução do index, recarregar a página numa rota interna daria 404.
    A rota coringa é registrada por último, senão engoliria o `/api`.
    """
    app.mount("/assets", StaticFiles(directory=PAINEL / "assets"), name="assets")

    @app.get("/{caminho:path}", include_in_schema=False)
    async def painel(caminho: str) -> Any:
        arquivo = PAINEL / caminho
        if caminho and arquivo.is_file():
            return FileResponse(arquivo)
        return FileResponse(PAINEL / "index.html")
