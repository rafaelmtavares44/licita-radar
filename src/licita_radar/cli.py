"""A interface do licita-radar na v0.1: a linha de comando."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from licita_radar.config.perfil import ErroDePerfil, Perfil, carregar_perfil
from licita_radar.config.settings import get_settings
from licita_radar.ingest.modalidades import rotular
from licita_radar.ingest.pncp_client import coletar
from licita_radar.storage.db import Banco, migrar
from licita_radar.storage.repositories import ContratacaoRepo, ExecucaoRepo

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

    aplicadas = asyncio.run(_executar())
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
    dias: Annotated[int, typer.Option("--dias", help="Janela para trás, no backfill")] = 7,
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
    uf_alvo = uf or (perfil.restricoes.ufs[0] if perfil.restricoes.ufs else None)

    console.print(
        f"[dim]coletando · uf={uf_alvo or 'todas'} · modalidades={modalidades} · "
        f"{'publicação desde ' + inicio.isoformat() if historico else 'proposta aberta'}[/dim]"
    )

    async def _executar() -> None:
        contratacoes = await coletar(
            modalidades=modalidades,
            data_inicial=inicio,
            data_final=hoje,
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

    asyncio.run(_executar())


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

    linhas = asyncio.run(_executar())
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


if __name__ == "__main__":  # pragma: no cover
    app()
