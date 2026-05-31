"""
Dashboard CLI — Sistema de Renda com Agentes IA
Controle central para gerenciar múltiplos nichos.
"""
import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich import print as rprint
from rich.markdown import Markdown

from dados.banco import (
    listar_nichos, adicionar_nicho, obter_nicho, historico_nicho
)
from agentes.orquestrador import ciclo_nicho, ciclo_todos, iniciar_agendamento
from agentes import copy, flyer
from agentes.aliexpress_scraper import buscar_e_atualizar_site as ali_scraper

console = Console()

LOGO = """
[bold cyan]
  ██████╗ ███████╗███╗   ██╗██████╗  █████╗     ██╗ █████╗
  ██╔══██╗██╔════╝████╗  ██║██╔══██╗██╔══██╗    ██║██╔══██╗
  ██████╔╝█████╗  ██╔██╗ ██║██║  ██║███████║    ██║███████║
  ██╔══██╗██╔══╝  ██║╚██╗██║██║  ██║██╔══██║    ██║██╔══██║
  ██║  ██║███████╗██║ ╚████║██████╔╝██║  ██║    ██║██║  ██║
  ╚═╝  ╚═╝╚══════╝╚═╝  ╚═══╝╚═════╝ ╚═╝  ╚═╝    ╚═╝╚═╝  ╚═╝
[/bold cyan]
[dim]  Sistema Multi-Nicho com Agentes IA — v1.0[/dim]
"""

NICHOS_EXEMPLOS = [
    ("saude-longevidade", "Saúde & Longevidade",
     "Cursos e produtos sobre saúde hormonal, longevidade e energia para adultos 35+"),
    ("financas-renda-extra", "Finanças & Renda Extra",
     "Estratégias para sair das dívidas, investir e criar renda passiva no Brasil"),
    ("emagrecimento", "Emagrecimento & Corpo",
     "Método natural de perda de peso sem dieta radical para mulheres 30+"),
    ("marketing-digital", "Marketing Digital",
     "Como vender online e construir negócios digitais lucrativos do zero"),
    ("pets-premium", "Pets Premium",
     "Produtos e serviços premium para donos de pets que tratam animais como família"),
]


def header():
    console.clear()
    console.print(LOGO)


def menu_principal():
    header()
    console.print(Panel(
        "[bold]1.[/bold] Ver nichos cadastrados\n"
        "[bold]2.[/bold] Adicionar novo nicho\n"
        "[bold]3.[/bold] Rodar ciclo completo (todos os nichos)\n"
        "[bold]4.[/bold] Rodar ciclo em um nicho específico\n"
        "[bold]5.[/bold] Gerar copy agora\n"
        "[bold]6.[/bold] Gerar flyer agora\n"
        "[bold]7.[/bold] Ver histórico de um nicho\n"
        "[bold]8.[/bold] Iniciar modo automático (agendado)\n"
        "[bold]9.[/bold] Adicionar nichos de exemplo\n"
        "[bold cyan]A.[/bold cyan] [cyan]Buscar produtos REAIS no AliExpress e atualizar site[/cyan]\n"
        "[bold]0.[/bold] Sair",
        title="[bold cyan]MENU PRINCIPAL[/bold cyan]",
        border_style="cyan",
    ))
    return Prompt.ask("\n[bold]Escolha[/bold]", choices=["0","1","2","3","4","5","6","7","8","9","a","A"])


def tela_nichos():
    nichos = listar_nichos()
    if not nichos:
        console.print("[yellow]Nenhum nicho cadastrado ainda.[/yellow]")
        return

    tabela = Table(title="Nichos Cadastrados", border_style="cyan")
    tabela.add_column("Slug", style="cyan")
    tabela.add_column("Nome", style="bold")
    tabela.add_column("Copies", justify="right")
    tabela.add_column("Flyers", justify="right")
    tabela.add_column("Melhores Horários")

    for slug in nichos:
        nicho = obter_nicho(slug)
        m = nicho["metricas"]
        horarios = ", ".join(m.get("melhores_horarios", [])[:3]) or "—"
        tabela.add_row(
            slug,
            nicho["nome"],
            str(m["copies_gerados"]),
            str(m["flyers_gerados"]),
            horarios,
        )

    console.print(tabela)


def tela_adicionar_nicho():
    console.print("\n[bold cyan]Adicionar Novo Nicho[/bold cyan]\n")
    slug = Prompt.ask("Slug (ex: saude-hormonal)").lower().replace(" ", "-")
    nome = Prompt.ask("Nome do nicho")
    descricao = Prompt.ask("Descrição do público e produto")

    ok = adicionar_nicho(slug, nome, descricao)
    if ok:
        console.print(f"\n[green]✓ Nicho '[bold]{nome}[/bold]' adicionado![/green]")
    else:
        console.print(f"\n[yellow]Nicho '{slug}' já existe.[/yellow]")


def tela_historico():
    slug = _selecionar_nicho()
    if not slug:
        return

    tipo = Prompt.ask("Tipo de histórico", choices=["spy", "copy", "flyer", "todos"], default="todos")
    registros = historico_nicho(slug, tipo=None if tipo == "todos" else tipo, limite=10)

    if not registros:
        console.print("[yellow]Sem histórico ainda.[/yellow]")
        return

    for r in registros:
        console.print(Panel(
            json.dumps(r["conteudo"], ensure_ascii=False, indent=2)[:800],
            title=f"[cyan]{r['tipo']}[/cyan] — {r['timestamp'][:16]}",
            border_style="dim",
        ))


def _selecionar_nicho() -> str | None:
    nichos = listar_nichos()
    if not nichos:
        console.print("[yellow]Nenhum nicho cadastrado.[/yellow]")
        return None
    console.print("\nNichos disponíveis: " + ", ".join(f"[cyan]{n}[/cyan]" for n in nichos))
    return Prompt.ask("Slug do nicho")


def tela_copy():
    slug = _selecionar_nicho()
    if not slug:
        return
    formato = Prompt.ask("Formato", choices=["post", "stories", "email", "anuncio"], default="post")
    tom = Prompt.ask("Tom", choices=["direto", "emocional", "educativo", "urgente"], default="direto")

    with console.status("[cyan]Gerando copy...[/cyan]"):
        resultado = copy.rodar(slug, formato=formato, tom=tom)

    console.print(Panel(
        f"[bold yellow]{resultado['gancho']}[/bold yellow]\n\n"
        f"[bold]Legenda curta:[/bold] {resultado['legenda_curta']}\n\n"
        f"[bold]CTA:[/bold] {resultado['cta']}\n\n"
        f"[bold]Stories:[/bold] {resultado['stories_texto']}\n\n"
        f"[dim]Gerado às {resultado['horario_geracao']}[/dim]",
        title=f"[cyan]Copy — {formato} / {tom}[/cyan]",
        border_style="yellow",
    ))


def tela_flyer():
    slug = _selecionar_nicho()
    if not slug:
        return
    gancho = Prompt.ask("Gancho (Enter para usar o melhor aprendido)", default="")

    with console.status("[cyan]Gerando flyer...[/cyan]"):
        resultado = flyer.rodar(slug, gancho=gancho or None)

    console.print(f"\n[green]✓ Flyer gerado![/green]")
    console.print(f"Arquivo: [cyan]{resultado['arquivo']}[/cyan]")
    console.print(f"Título: [bold]{resultado['textos']['titulo']}[/bold]")
    console.print("\n[dim]Abra o arquivo .html no navegador para visualizar.[/dim]")


def adicionar_exemplos():
    adicionados = 0
    for slug, nome, desc in NICHOS_EXEMPLOS:
        if adicionar_nicho(slug, nome, desc):
            console.print(f"[green]+ {nome}[/green]")
            adicionados += 1
        else:
            console.print(f"[dim]  {nome} (já existe)[/dim]")
    console.print(f"\n[bold]{adicionados} nicho(s) adicionado(s).[/bold]")


def main():
    if not os.getenv("ANTHROPIC_API_KEY"):
        console.print("[red]ANTHROPIC_API_KEY não configurada. Copie .env.example para .env e preencha.[/red]")
        sys.exit(1)

    while True:
        opcao = menu_principal()

        if opcao == "0":
            console.print("\n[dim]Saindo...[/dim]")
            break
        elif opcao == "1":
            header(); tela_nichos()
        elif opcao == "2":
            header(); tela_adicionar_nicho()
        elif opcao == "3":
            header()
            with console.status("[cyan]Rodando ciclo completo...[/cyan]"):
                ciclo_todos()
            console.print("[green]✓ Ciclo completo![/green]")
        elif opcao == "4":
            header()
            slug = _selecionar_nicho()
            if slug:
                with console.status(f"[cyan]Rodando ciclo para {slug}...[/cyan]"):
                    ciclo_nicho(slug)
        elif opcao == "5":
            header(); tela_copy()
        elif opcao == "6":
            header(); tela_flyer()
        elif opcao == "7":
            header(); tela_historico()
        elif opcao == "8":
            header()
            console.print("[yellow]Modo automático iniciado. Ctrl+C para parar.[/yellow]")
            iniciar_agendamento()
        elif opcao == "9":
            header(); adicionar_exemplos()
        elif opcao in ("a", "A"):
            header()
            console.print("[bold cyan]Buscando produtos reais no AliExpress...[/bold cyan]")
            console.print("[dim]Isso leva ~30 segundos. O site será atualizado automaticamente.[/dim]\n")
            with console.status("[cyan]Conectando ao AliExpress...[/cyan]"):
                produtos = ali_scraper(max_produtos=6)
            if produtos:
                console.print(f"\n[green]✓ {len(produtos)} produtos reais adicionados ao site![/green]\n")
                for p in produtos:
                    console.print(f"  • [bold]{p['titulo_pt'][:50]}[/bold] — [cyan]R$ {p['preco_venda']:.0f}[/cyan] (margem {p['margem_pct']:.0f}%)")
            else:
                console.print("[yellow]Nenhum produto encontrado. Verifique sua conexão.[/yellow]")

        if opcao != "0":
            Prompt.ask("\n[dim]Enter para continuar[/dim]")


if __name__ == "__main__":
    main()
