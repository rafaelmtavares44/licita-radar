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
from fastapi.responses import FileResponse, HTMLResponse
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
        encerradas: Annotated[
            bool, Query(description="Inclui as que já passaram do prazo de proposta")
        ] = False,
        uf: Annotated[
            str | None, Query(min_length=2, max_length=2, description="Sigla, ex.: GO")
        ] = None,
    ) -> list[ItemLista]:
        return await servico.listar(
            contexto,
            limite=limite,
            so_candidatas=not todas,
            so_abertas=not encerradas,
            uf=uf.upper() if uf else None,
        )

    @app.post("/api/atualizar", status_code=202)
    async def disparar_atualizacao(
        contexto: Ctx,
        uf: Annotated[
            str | None,
            Query(min_length=2, max_length=2, description="Coleta só esta UF. Vazio = Brasil."),
        ] = None,
    ) -> dict[str, Any]:
        """Volta na hora; o ciclo corre em segundo plano.

        A UF não é só filtro de tela: uma varredura nacional de Pregão
        Eletrônico passa de cem páginas e o PNCP começa a barrar. Restringir
        o estado é o que transforma dez minutos de espera em um.
        """
        progresso = await servico.atualizar(contexto, settings=s, uf=uf.upper() if uf else None)
        return progresso.como_dict()

    @app.get("/api/atualizar")
    async def estado_da_atualizacao(contexto: Ctx) -> dict[str, Any]:
        return contexto.atualizacao.progresso.como_dict()

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
    else:
        _explicar_a_ausencia_do_painel(app)

    return app


def _montar_painel(app: FastAPI) -> None:
    """Serve o front construído, com o index respondendo qualquer rota.

    Quem roteia `/candidatas/xyz` é o React, não o servidor: sem a
    devolução do index, recarregar a página numa rota interna daria 404.
    A rota coringa é registrada por último, senão engoliria o `/api`.
    """
    app.mount("/assets", StaticFiles(directory=PAINEL / "assets"), name="assets")

    #: O index aponta para arquivos cujo nome muda a cada build
    #: (`index-Doq-q4wD.js`). Se o navegador guardar o index, ele pede na
    #: próxima vez um arquivo que o build anterior já apagou: o CSS vem do
    #: cache, o script dá 404, e a tela fica branca com o fundo certo —
    #: o pior sintoma possível, porque parece que o servidor respondeu.
    #: Os assets podem ser cacheados à vontade justamente porque o nome
    #: deles carrega o conteúdo; o index, nunca.
    nunca_guarde = {"Cache-Control": "no-store, must-revalidate"}

    @app.get("/{caminho:path}", include_in_schema=False)
    async def painel(caminho: str) -> Any:
        arquivo = PAINEL / caminho
        if caminho and arquivo.is_file():
            return FileResponse(arquivo)
        return FileResponse(PAINEL / "index.html", headers=nunca_guarde)


#: Sem `web/dist`, a raiz não tem rota e o FastAPI devolve
#: `{"detail":"Not Found"}` — que é verdade e não ajuda ninguém. Quem
#: acabou de subir o servidor quer saber o que fazer, não o código HTTP.
_SEM_PAINEL = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<title>licita-radar — o painel ainda não foi construído</title>
<style>
 body{margin:0;display:grid;place-items:center;min-height:100vh;background:#f7f5f1;color:#12161f;
      font:15px/1.6 "Segoe UI",system-ui,sans-serif}
 main{max-width:52ch;padding:32px}
 h1{font-size:20px;margin:0 0 6px}
 p{color:#4b5567}
 pre{background:#fff;border:1px solid #e2ded4;border-left:3px solid #2f4fcb;
     border-radius:0 8px 8px 0;padding:14px 16px;overflow-x:auto;
     font-family:Consolas,monospace;font-size:13.5px}
 a{color:#2f4fcb}
 @media(prefers-color-scheme:dark){body{background:#0f131b;color:#eceef3}p{color:#a3acbf}
   pre{background:#171c26;border-color:#262d3a;border-left-color:#8fa4ff}a{color:#8fa4ff}}
</style></head><body><main>
<h1>A API está no ar. O painel, ainda não.</h1>
<p>A tela é construída à parte e não vem pronta no repositório. Uma vez só:</p>
<pre>cd web
npm install
npm run build</pre>
<p>Depois reinicie o <code>licita-radar servir</code> e recarregue esta página.</p>
<p>Enquanto isso, a API funciona sozinha —
<a href="/docs">documentação interativa em /docs</a>.</p>
</main></body></html>"""


def _explicar_a_ausencia_do_painel(app: FastAPI) -> None:
    """Uma página em vez de um 404 quando `web/dist` não existe.

    Só na raiz, e não como rota coringa: uma coringa aqui capturaria
    qualquer caminho e esconderia os 404 verdadeiros da API.
    """

    @app.get("/", include_in_schema=False)
    async def raiz() -> HTMLResponse:
        return HTMLResponse(_SEM_PAINEL)
