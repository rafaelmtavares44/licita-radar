"""A interface do licita-radar na v0.1: a linha de comando."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from licita_radar.config.perfil import ErroDePerfil, Perfil, carregar_perfil
from licita_radar.config.settings import get_settings
from licita_radar.diagnostico import Estado
from licita_radar.diagnostico import executar as executar_diagnostico
from licita_radar.graph.build import configuracao
from licita_radar.graph.runner import abrir_radar, pendentes, processar, responder
from licita_radar.ingest.modalidades import rotular
from licita_radar.ingest.pncp_client import coletar
from licita_radar.matching.calibragem import sugerir_limiar
from licita_radar.matching.encoder import FastEmbedEncoder, similaridade_cosseno
from licita_radar.matching.limpeza import limpar_objeto
from licita_radar.matching.pontuacao import Avaliacao, Veredito, avaliar, explicar
from licita_radar.matching.semantico import MotorSemantico
from licita_radar.storage.db import Banco, ErroDeBanco, migrar
from licita_radar.storage.matching_repo import AvaliacaoRepo, EmbeddingRepo, MatchingRepo
from licita_radar.storage.repositories import ContratacaoRepo, ExecucaoRepo

# ---------------------------------------------------------------------------
# No Windows, o asyncio usa o ProactorEventLoop por padrão, e o psycopg
# recusa rodar em modo assíncrono nele — a conexão falha com InterfaceError
# antes mesmo de tocar a rede. Isto precisa acontecer na importação, antes
# de qualquer asyncio.run(), e não custa nada nos outros sistemas.
if sys.platform == "win32":  # pragma: no cover — só roda no Windows
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
# ---------------------------------------------------------------------------

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Radar de licitações públicas a partir do PNCP.",
)
console = Console()


def _configurar_log() -> None:
    logging.basicConfig(
        level=get_settings().log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    # O pool grita um aviso por tentativa de conexão. Quando o banco está
    # fora do ar, isso vira uma parede de texto antes da mensagem útil.
    logging.getLogger("psycopg.pool").setLevel(logging.ERROR)


def _rodar(corrotina: Any) -> Any:
    """Executa e traduz falha de banco em recado, não em stack trace."""
    try:
        return asyncio.run(corrotina)
    except ErroDeBanco as erro:
        console.print(f"[bold red]{erro}[/bold red]")
        raise typer.Exit(code=1) from erro


def _carregar_ou_sair(caminho: Path) -> Perfil:
    try:
        return carregar_perfil(caminho)
    except ErroDePerfil as erro:
        console.print(f"[bold red]{erro}[/bold red]")
        raise typer.Exit(code=1) from erro


def _moeda(valor: Any) -> str:
    if valor is None:
        return "—"
    return f"R$ {float(valor):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


# ---------------------------------------------------------------------------


@app.command("migrar")
def cmd_migrar() -> None:
    """Cria ou atualiza o esquema do banco."""
    _configurar_log()

    async def _executar() -> list[str]:
        async with Banco() as banco:
            return await migrar(banco)

    aplicadas = _rodar(_executar())
    if aplicadas:
        for nome in aplicadas:
            console.print(f"[green]aplicada[/green] {nome}")
    else:
        console.print("[dim]banco já estava atualizado[/dim]")


@app.command("perfil")
def cmd_perfil(
    caminho: Annotated[Path | None, typer.Option("--arquivo", "-a")] = None,
) -> None:
    """Valida o perfil e mostra como ele foi interpretado."""
    perfil = _carregar_ou_sair(caminho or get_settings().perfil_path)

    console.print(f"[bold green]✓[/bold green] perfil [bold]{perfil.nome}[/bold] válido\n")
    tabela = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    tabela.add_row("identificador", perfil.id)
    tabela.add_row("UFs", ", ".join(perfil.restricoes.ufs) or "todas")
    tabela.add_row(
        "modalidades",
        "\n".join(f"{c} · {rotular(c)}" for c in perfil.restricoes.modalidades),
    )
    tabela.add_row("valor mínimo", _moeda(perfil.restricoes.valor_minimo))
    tabela.add_row("palavras positivas", str(len(perfil.palavras_chave.positivas)))
    tabela.add_row("palavras negativas", str(len(perfil.palavras_chave.negativas)))
    tabela.add_row("limiar de alerta", f"{perfil.pontuacao.limiar_alerta:.2f}")
    console.print(tabela)


@app.command("ingest")
def cmd_ingest(
    uf: Annotated[str | None, typer.Option("--uf", help="Sigla, ex.: GO")] = None,
    brasil: Annotated[
        bool,
        typer.Option("--brasil", help="Ignora as UFs do perfil e varre o país inteiro"),
    ] = False,
    dias: Annotated[int, typer.Option("--dias", help="Janela para trás, no backfill")] = 7,
    ate: Annotated[
        int,
        typer.Option(
            "--ate",
            help="Quantos dias à frente procurar propostas que ainda vão encerrar",
        ),
    ] = 60,
    historico: Annotated[
        bool,
        typer.Option(
            "--historico/--abertas",
            help=(
                "--historico busca por data de publicação; "
                "--abertas (padrão) traz só o que ainda dá tempo de disputar"
            ),
        ),
    ] = False,
    caminho_perfil: Annotated[Path | None, typer.Option("--perfil", "-p")] = None,
    seco: Annotated[bool, typer.Option("--seco", help="Não grava: só mostra o que viria")] = False,
) -> None:
    """Coleta contratações do PNCP e grava no banco."""
    _configurar_log()
    perfil = _carregar_ou_sair(caminho_perfil or get_settings().perfil_path)

    hoje = date.today()
    inicio = hoje - timedelta(days=dias)
    modalidades = perfil.restricoes.modalidades

    if brasil:
        uf_alvo = None
    else:
        uf_alvo = uf or (perfil.restricoes.ufs[0] if perfil.restricoes.ufs else None)

    # O endpoint /proposta filtra pelo FIM do período de recebimento. Para
    # trazer o que ainda está aberto, a data precisa estar à frente.
    limite_futuro = hoje + timedelta(days=ate)

    janela = (
        f"publicação desde {inicio.isoformat()}"
        if historico
        else f"proposta encerrando até {limite_futuro.isoformat()}"
    )
    console.print(
        f"[dim]coletando · uf={uf_alvo or 'BRASIL'} · modalidades={modalidades} · {janela}[/dim]"
    )
    if uf_alvo is None and not historico:
        console.print("[yellow]varredura nacional: isso pode levar alguns minutos[/yellow]")

    async def _executar() -> None:
        contratacoes = await coletar(
            modalidades=modalidades,
            data_inicial=inicio,
            data_final=hoje if historico else limite_futuro,
            uf=uf_alvo,
            apenas_abertas=not historico,
        )
        console.print(f"[bold]{len(contratacoes)}[/bold] contratações vieram da API")

        if seco:
            for c in contratacoes[:10]:
                console.print(f"  [dim]{c.numero_controle_pncp}[/dim] {c.objeto[:90]}")
            console.print("[yellow]execução seca: nada foi gravado[/yellow]")
            return

        async with Banco() as banco:
            execucoes = ExecucaoRepo(banco)
            repo = ContratacaoRepo(banco)
            execucao_id = await execucoes.abrir(
                uf=uf_alvo,
                modalidades=modalidades,
                janela_inicio=inicio if historico else None,
                janela_fim=hoje,
            )
            resultado = await repo.salvar_muitas(contratacoes)
            await execucoes.concluir(execucao_id, resultado=resultado)

            console.print(
                f"[green]{resultado.novas}[/green] novas · "
                f"[dim]{resultado.atualizadas} já existiam[/dim] · "
                f"total no banco: {await repo.contar()}"
            )

    _rodar(_executar())


@app.command("listar")
def cmd_listar(
    uf: Annotated[str | None, typer.Option("--uf")] = None,
    limite: Annotated[int, typer.Option("--limite", "-n")] = 20,
) -> None:
    """Mostra as contratações com proposta ainda aberta."""
    _configurar_log()

    async def _executar() -> list[dict[str, Any]]:
        async with Banco() as banco:
            return await ContratacaoRepo(banco).listar_abertas(uf=uf, limite=limite)

    linhas = _rodar(_executar())
    if not linhas:
        console.print("[dim]nada no banco ainda — rode `licita-radar ingest` antes[/dim]")
        return

    tabela = Table(title=f"Propostas abertas ({len(linhas)})", header_style="bold")
    tabela.add_column("encerra", no_wrap=True)
    tabela.add_column("modalidade", no_wrap=True)
    tabela.add_column("objeto", overflow="ellipsis", max_width=64)
    tabela.add_column("órgão", overflow="ellipsis", max_width=28)
    tabela.add_column("valor", justify="right", no_wrap=True)

    for linha in linhas:
        encerra = linha["encerramento_proposta"]
        tabela.add_row(
            encerra.strftime("%d/%m %H:%M") if encerra else "—",
            rotular(int(linha["modalidade_codigo"])),
            str(linha["objeto"]),
            str(linha["orgao_nome"] or "—"),
            _moeda(linha["valor_estimado"]),
        )
    console.print(tabela)


@app.command("radar")
def cmd_radar(
    uf: Annotated[str | None, typer.Option("--uf")] = None,
    limite: Annotated[int, typer.Option("--limite", "-n", help="Quantas processar")] = 200,
    caminho_perfil: Annotated[Path | None, typer.Option("--perfil", "-p")] = None,
) -> None:
    """Passa as contratações pelo grafo, até a revisão humana."""
    _configurar_log()
    perfil = _carregar_ou_sair(caminho_perfil or get_settings().perfil_path)

    async def _executar() -> None:
        async with Banco() as banco:
            contratacoes = await MatchingRepo(banco).carregar_contratacoes(uf=uf, limite=limite)

        if not contratacoes:
            console.print("[dim]nada no banco — rode `licita-radar ingest` antes[/dim]")
            return

        console.print(f"[dim]passando {len(contratacoes)} contratações pelo grafo[/dim]")
        async with abrir_radar(perfil) as grafo:
            execucoes = await processar(grafo, contratacoes, perfil)

        aguardando = [e for e in execucoes if e.aguardando]
        tokens = sum(e.tokens for e in execucoes)

        contagem = Counter(e.situacao for e in execucoes)
        tabela = Table(title="O grafo", header_style="bold", title_justify="left")
        tabela.add_column("situação")
        tabela.add_column("qtd", justify="right")
        for situacao, qtd in contagem.most_common():
            tabela.add_row(situacao, str(qtd))
        console.print(tabela)

        if tokens:
            console.print(f"[dim]{tokens} tokens gastos nesta execução[/dim]")

        if aguardando:
            console.print(
                f"\n[bold green]{len(aguardando)}[/bold green] esperando a sua decisão — "
                f"rode [bold]licita-radar revisar[/bold]"
            )
        else:
            console.print("\n[dim]nenhuma chegou à revisão desta vez[/dim]")

    _rodar(_executar())


@app.command("revisar")
def cmd_revisar(
    limite: Annotated[int, typer.Option("--limite", "-n")] = 10,
    caminho_perfil: Annotated[Path | None, typer.Option("--perfil", "-p")] = None,
) -> None:
    """Mostra o que está esperando decisão e retoma o grafo com a resposta."""
    _configurar_log()
    perfil = _carregar_ou_sair(caminho_perfil or get_settings().perfil_path)

    async def _executar() -> None:
        async with Banco() as banco:
            candidatas = await AvaliacaoRepo(banco).ranking(
                perfil_id=perfil.id, limite=limite, apenas_candidatas=True
            )
            numeros = [str(linha["numero_controle_pncp"]) for linha in candidatas]
            contratacoes = await MatchingRepo(banco).carregar_contratacoes(numeros=numeros)

        if not contratacoes:
            console.print("[dim]nada para revisar — rode `licita-radar radar` antes[/dim]")
            return

        por_numero = {c.numero_controle_pncp: c for c in contratacoes}

        async with abrir_radar(perfil) as grafo:
            parados = await pendentes(grafo, list(por_numero))
            if not parados:
                console.print("[dim]nenhuma thread parada esperando decisão[/dim]")
                return

            for numero in parados:
                contratacao = por_numero[numero]
                estado = (await grafo.aget_state(configuracao(numero))).values

                console.print()
                console.rule(f"[bold]{numero}[/bold]", align="left")
                console.print(f"[bold]{limpar_objeto(contratacao.objeto)[:400]}[/bold]")
                console.print(
                    f"[dim]{contratacao.orgao_nome or '—'} · {contratacao.uf or '—'} · "
                    f"{_moeda(contratacao.valor_estimado)}[/dim]"
                )
                if estado.get("justificativa"):
                    console.print(f"[green]{estado['justificativa']}[/green]")
                if contratacao.url_pncp:
                    console.print(f"[dim]{contratacao.url_pncp}[/dim]")

                escolha = typer.prompt("  [a]provar / [r]ejeitar / [p]ular", default="p")
                if escolha.lower().startswith("p"):
                    continue

                aprovar = escolha.lower().startswith("a")
                comentario = typer.prompt("  comentário (enter para pular)", default="") or None
                final = await responder(grafo, numero, aprovar=aprovar, comentario=comentario)
                cor = "green" if aprovar else "yellow"
                console.print(f"  [{cor}]{final.get('situacao')}[/{cor}]")

    _rodar(_executar())


@app.command("doctor")
def cmd_doctor(
    sem_rede: Annotated[
        bool, typer.Option("--sem-rede", help="Pula a checagem da API do PNCP")
    ] = False,
) -> None:
    """Diz o que está quebrado e o que fazer a respeito."""
    checagens = executar_diagnostico(get_settings(), com_pncp=not sem_rede)

    simbolo = {
        Estado.OK: "[green]✓[/green]",
        Estado.FALHA: "[red]✗[/red]",
        Estado.AVISO: "[yellow]![/yellow]",
        Estado.PULADO: "[dim]–[/dim]",
    }

    grupo_atual = ""
    for c in checagens:
        if c.grupo != grupo_atual:
            grupo_atual = c.grupo
            console.print(f"\n[bold]{grupo_atual}[/bold]")
        detalhe = f"  [dim]{c.detalhe}[/dim]" if c.detalhe else ""
        console.print(f"  {simbolo[c.estado]} {c.titulo}{detalhe}")
        if c.dica:
            console.print(f"      [yellow]→ {c.dica}[/yellow]")

    falhas = [c for c in checagens if c.estado is Estado.FALHA]
    console.print()
    if falhas:
        console.print(f"[bold red]{len(falhas)} problema(s).[/bold red] Comece pelo primeiro ✗.")
        raise typer.Exit(code=1)
    console.print("[bold green]Tudo pronto.[/bold green]")


@app.command("match")
def cmd_match(
    uf: Annotated[str | None, typer.Option("--uf")] = None,
    limite: Annotated[int, typer.Option("--limite", "-n", help="Linhas no ranking")] = 20,
    caminho_perfil: Annotated[Path | None, typer.Option("--perfil", "-p")] = None,
    sem_semantica: Annotated[
        bool,
        typer.Option(
            "--sem-semantica",
            help="Só a camada léxica. Não baixa modelo — bom para calibrar as palavras-chave",
        ),
    ] = False,
    limite_avaliacao: Annotated[
        int,
        typer.Option("--avaliar", help="Quantas contratações avaliar de uma vez"),
    ] = 5000,
) -> None:
    """Pontua o que está no banco contra o seu perfil."""
    _configurar_log()
    perfil = _carregar_ou_sair(caminho_perfil or get_settings().perfil_path)

    async def _executar() -> None:
        async with Banco() as banco:
            matching = MatchingRepo(banco)
            contratacoes = await matching.carregar_contratacoes(uf=uf, limite=limite_avaliacao)

            if not contratacoes:
                console.print("[dim]nada no banco — rode `licita-radar ingest` antes[/dim]")
                return

            console.print(f"[dim]avaliando {len(contratacoes)} contratações[/dim]")
            if len(contratacoes) == limite_avaliacao:
                console.print(
                    f"[yellow]o limite de {limite_avaliacao} foi atingido — "
                    f"use --avaliar para aumentar[/yellow]"
                )
            scores: dict[str, float] = {}

            if not sem_semantica:
                motor = MotorSemantico(FastEmbedEncoder())
                embeddings = EmbeddingRepo(banco)
                # a pergunta é feita sobre exatamente as contratações que serão
                # avaliadas: perguntar "quais faltam?" sem esse recorte devolvia
                # outro conjunto, e parte do que era avaliado ficava sem vetor
                pendentes = set(
                    await embeddings.numeros_sem_embedding(
                        modelo=motor.encoder.nome,
                        entre=[c.numero_controle_pncp for c in contratacoes],
                    )
                )
                a_codificar = [c for c in contratacoes if c.numero_controle_pncp in pendentes]

                if a_codificar:
                    console.print(f"[dim]codificando {len(a_codificar)} novas…[/dim]")
                    codificados = motor.codificar_contratacoes(a_codificar)
                    await embeddings.salvar_muitos(codificados, modelo=motor.encoder.nome)

                vetores = await matching.vetores([c.numero_controle_pncp for c in contratacoes])
                vetor_perfil = motor.vetor_do_perfil(perfil)
                scores = {
                    numero: similaridade_cosseno(vetor_perfil, vetor)
                    for numero, vetor in vetores.items()
                    if vetor
                }
            else:
                console.print(
                    "[yellow]modo léxico: a camada semântica não vai rodar — o score é só "
                    "das palavras-chave[/yellow]"
                )

            avaliacoes = [
                avaliar(
                    c,
                    perfil,
                    score_semantico=scores.get(c.numero_controle_pncp, 0.0),
                    com_semantica=not sem_semantica,
                )
                for c in contratacoes
            ]
            await AvaliacaoRepo(banco).salvar_muitas(avaliacoes, perfil_id=perfil.id)

            _mostrar_funil(avaliacoes)
            _mostrar_motivos(avaliacoes)
            _mostrar_ranking(avaliacoes, limite=limite)
            _sugerir_limiar(avaliacoes, perfil)

    _rodar(_executar())


def _mostrar_funil(avaliacoes: list[Avaliacao]) -> None:
    contagem = Counter(a.veredito for a in avaliacoes)
    total = len(avaliacoes)

    tabela = Table(title="O funil", header_style="bold", title_justify="left")
    tabela.add_column("etapa")
    tabela.add_column("qtd", justify="right")
    tabela.add_column("", justify="left")

    rotulos = {
        Veredito.INELEGIVEL: ("não passou nos filtros", "dim"),
        Veredito.VETADA: ("vetada por palavra negativa", "red"),
        Veredito.ABAIXO_DO_LIMIAR: ("abaixo do limiar", "yellow"),
        Veredito.CANDIDATA: ("candidata — merece o seu olho", "green"),
    }
    for veredito, (rotulo, cor) in rotulos.items():
        qtd = contagem.get(veredito, 0)
        barra = "█" * round(20 * qtd / total) if total else ""
        tabela.add_row(f"[{cor}]{rotulo}[/{cor}]", str(qtd), f"[{cor}]{barra}[/{cor}]")

    console.print(tabela)


def _mostrar_motivos(avaliacoes: list[Avaliacao]) -> None:
    """Por que as descartadas foram descartadas.

    "Nada apareceu" é a primeira frustração de quem usa o radar, e a
    resposta quase nunca é o matching: é uma restrição do perfil apertada
    demais. Mostrar os motivos agrupados transforma um beco sem saída em
    uma linha do YAML para ajustar.
    """
    descartadas = [
        a for a in avaliacoes if a.veredito in (Veredito.INELEGIVEL, Veredito.VETADA) and a.motivo
    ]
    if not descartadas:
        return

    # "valor abaixo do mínimo (R$ 901,00)" e "(R$ 2.670,00)" são o mesmo
    # motivo: agrupa pelo texto antes do parêntese.
    contagem = Counter(a.motivo.split(" (")[0] for a in descartadas if a.motivo)

    tabela = Table(title="\nPor que foram descartadas", header_style="bold", title_justify="left")
    tabela.add_column("motivo")
    tabela.add_column("qtd", justify="right", no_wrap=True)
    tabela.add_column("onde ajustar", overflow="fold")

    onde = {
        "fora das UFs do perfil": "restricoes.ufs",
        "valor abaixo do mínimo": "restricoes.valor_minimo",
        "prazo curto demais": "restricoes.dias_minimos_ate_encerramento",
        "prazo já encerrado": "—  a proposta fechou antes de você coletar",
        "contém a palavra negativa": "palavras_chave.negativas",
    }

    for motivo, qtd in contagem.most_common(8):
        ajuste = next((v for k, v in onde.items() if motivo.startswith(k)), "")
        if motivo.startswith("modalidade"):
            ajuste = "restricoes.modalidades"
        tabela.add_row(motivo, str(qtd), f"[dim]{ajuste}[/dim]")

    console.print(tabela)


def _sugerir_limiar(avaliacoes: list[Avaliacao], perfil: Perfil) -> None:
    """Se ninguém passou, o limiar provavelmente está fora da escala real."""
    vivas = [a for a in avaliacoes if a.veredito is not Veredito.INELEGIVEL]
    if not vivas or any(a.alerta for a in vivas):
        return

    sugestao = sugerir_limiar([a.score_final for a in vivas])
    atual = perfil.pontuacao.limiar_alerta
    if sugestao.limiar >= atual:
        return

    console.print(
        f"\n[yellow]Nenhuma candidata, e o limiar pode ser a causa.[/yellow]\n"
        f"  O melhor score de hoje foi [bold]{sugestao.maximo:.2f}[/bold]; "
        f"o limiar do perfil é [bold]{atual:.2f}[/bold].\n"
        f"  Com [bold]limiar_alerta: {sugestao.limiar}[/bold] passariam "
        f"{sugestao.quantos_alertariam} de {sugestao.total} — "
        f"[dim]mediana {sugestao.mediana:.2f} · p90 {sugestao.percentil_90:.2f}[/dim]"
    )


def _mostrar_ranking(avaliacoes: list[Avaliacao], *, limite: int) -> None:
    vivas = sorted(
        (a for a in avaliacoes if a.veredito in (Veredito.CANDIDATA, Veredito.ABAIXO_DO_LIMIAR)),
        key=lambda a: a.score_final,
        reverse=True,
    )[:limite]

    if not vivas:
        console.print("\n[dim]nenhuma contratação sobreviveu aos filtros desta vez[/dim]")
        return

    tabela = Table(title=f"\nTop {len(vivas)}", header_style="bold", title_justify="left")
    tabela.add_column("score", justify="right", no_wrap=True)
    tabela.add_column("lex", justify="right", no_wrap=True)
    tabela.add_column("sem", justify="right", no_wrap=True)
    # uma licitação por linha: objeto real tem até 500 caracteres e, sem
    # no_wrap, cada linha vira um parágrafo e o ranking fica ilegível
    tabela.add_column("objeto", overflow="ellipsis", max_width=58, no_wrap=True)
    tabela.add_column("por quê", overflow="ellipsis", max_width=38, no_wrap=True)

    for a in vivas:
        cor = "green" if a.alerta else "yellow"
        tabela.add_row(
            f"[{cor}]{a.score_final:.2f}[/{cor}]",
            f"{a.score_lexical:.2f}",
            f"{a.score_semantico:.2f}",
            limpar_objeto(a.contratacao.objeto),
            explicar(a),
        )

    console.print(tabela)


if __name__ == "__main__":  # pragma: no cover
    app()
