"""Main CLI entry point for fwts."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from fwts import __version__
from fwts.completions import generate_bash, generate_fish, generate_zsh, install_completion
from fwts.config import (
    Config,
    generate_example_config,
    generate_global_config_example,
    list_projects,
    load_config,
)
from fwts.focus import (
    focus_worktree,
    get_focus_state,
    get_focused_branch,
    unfocus,
)
from fwts.git import list_worktrees
from fwts.github import get_branch_from_pr, has_gh_cli
from fwts.lifecycle import full_cleanup, full_setup, get_worktree_for_input
from fwts.linear import (
    get_branch_from_ticket,
    list_my_tickets,
    list_review_requests,
    list_team_tickets,
    TicketListItem,
)
from fwts.tmux import attach_session, session_exists, session_name_from_branch
from fwts.tui import FeatureboxTUI, simple_list

app = typer.Typer(
    name="fwts",
    help="Git worktree workflow manager for feature development",
    no_args_is_help=True,
)
console = Console()


def version_callback(value: bool) -> None:
    if value:
        console.print(f"fwts {__version__}")
        raise typer.Exit()


# Global state for project/config from callback
_global_project: str | None = None
_global_config_path: Path | None = None


# Per-command options (can override global)
ProjectOption = Annotated[
    str | None,
    typer.Option(
        "--project",
        "-p",
        help="Named project from global config (auto-detects if not specified)",
    ),
]

ConfigOption = Annotated[
    Path | None,
    typer.Option("--config", "-c", help="Path to config file"),
]


def _get_config(project: str | None = None, config_path: Path | None = None) -> Config:
    """Load config with project or path override."""
    # Use command-level options if provided, else fall back to global
    proj = project if project is not None else _global_project
    path = config_path if config_path is not None else _global_config_path
    return load_config(path=path, project_name=proj)


@app.callback()
def main(
    ctx: typer.Context,
    version: Annotated[
        bool | None,
        typer.Option("--version", "-V", callback=version_callback, is_eager=True),
    ] = None,
    project: Annotated[
        str | None,
        typer.Option(
            "--project",
            "-p",
            help="Named project from global config",
        ),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Path to config file"),
    ] = None,
) -> None:
    """fwts - Git worktree workflow manager."""
    global _global_project, _global_config_path
    _global_project = project
    _global_config_path = config


def _resolve_input_to_branch(input_str: str, config: Config) -> str | None:
    """Resolve various input formats to a branch name.

    Accepts:
    - Branch name
    - Linear ticket (SUP-123 or URL)
    - GitHub PR (#123 or URL)
    """
    if not input_str:
        return None

    # Check if it looks like a Linear ticket
    is_linear = input_str.upper().startswith(("SUP-", "ENG-", "DEV-")) or "linear.app" in input_str
    if is_linear and config.linear.enabled:
        try:
            return asyncio.run(get_branch_from_ticket(input_str, config.linear.api_key))
        except Exception as e:
            console.print(f"[yellow]Could not resolve Linear ticket: {e}[/yellow]")
            return None

    # Check if it looks like a GitHub PR
    is_github_pr = input_str.startswith("#") or input_str.isdigit() or "github.com" in input_str
    if is_github_pr and has_gh_cli() and config.project.github_repo:
        branch = get_branch_from_pr(input_str, config.project.github_repo)
        if branch:
            return branch

    # Assume it's a branch name
    return input_str


@app.command()
def start(
    input: Annotated[
        str | None,
        typer.Argument(help="Linear ticket, PR #, branch name, or URL"),
    ] = None,
    base: Annotated[
        str | None,
        typer.Option("--base", "-b", help="Base branch to create from"),
    ] = None,
    project: ProjectOption = None,
    config_path: ConfigOption = None,
) -> None:
    """Start or resume a feature worktree.

    Creates a new worktree if needed, sets up tmux session, and attaches.
    If the branch already exists, attaches to existing session.
    """
    config = _get_config(project, config_path)

    if not input:
        # Interactive mode - show TUI and let user pick
        tui = FeatureboxTUI(config)
        action, selected = tui.run()

        if action == "launch" and selected:
            for info in selected:
                session_name = session_name_from_branch(info.worktree.branch)
                if session_exists(session_name):
                    attach_session(session_name)
                else:
                    full_setup(info.worktree.branch, config, base)
        elif action == "cleanup" and selected:
            tui.run_with_cleanup_status(full_cleanup, selected)
        return

    # Resolve input to branch name and get ticket info if applicable
    ticket_info = ""
    branch = _resolve_input_to_branch(input, config)

    # If input looks like a Linear ticket, save it as ticket info
    if input and (input.upper().startswith("SUP-") or "linear.app" in input.lower()):
        ticket_info = input

    if not branch:
        console.print(f"[red]Could not resolve input to branch: {input}[/red]")
        raise typer.Exit(1)

    # Check if worktree already exists
    main_repo = config.project.main_repo.expanduser().resolve()
    worktrees = list_worktrees(main_repo)
    existing = next((wt for wt in worktrees if wt.branch == branch), None)

    if existing:
        session_name = session_name_from_branch(branch)
        if session_exists(session_name):
            console.print(f"[blue]Attaching to existing session: {session_name}[/blue]")
            attach_session(session_name)
        else:
            full_setup(branch, config, base, ticket_info=ticket_info)
    else:
        full_setup(branch, config, base, ticket_info=ticket_info)


@app.command()
def cleanup(
    input: Annotated[
        str | None,
        typer.Argument(help="Branch name, worktree path, or partial match"),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Force removal even with uncommitted changes"),
    ] = False,
    delete_remote: Annotated[
        bool,
        typer.Option("--remote", "-r", help="Also delete remote branch"),
    ] = False,
    project: ProjectOption = None,
    config_path: ConfigOption = None,
) -> None:
    """Clean up a feature worktree.

    Stops docker, kills tmux session, removes worktree, and optionally deletes branches.
    """
    config = _get_config(project, config_path)

    if not input:
        # Interactive mode
        tui = FeatureboxTUI(config)
        action, selected = tui.run()

        if action == "cleanup" and selected:
            for info in selected:
                full_cleanup(info.worktree, config, force=force, delete_remote=delete_remote)
        return

    # Find matching worktree
    worktree = get_worktree_for_input(input, config)
    if not worktree:
        console.print(f"[red]No worktree found matching: {input}[/red]")
        raise typer.Exit(1)

    full_cleanup(worktree, config, force=force, delete_remote=delete_remote)


@app.command()
def status(
    project: ProjectOption = None,
    config_path: ConfigOption = None,
) -> None:
    """Interactive TUI - view all worktrees, multi-select for actions.

    Keys:
    - j/k or arrows: navigate
    - space: toggle select
    - a: select all
    - enter: launch selected
    - d: cleanup selected
    - f: focus selected
    - r: refresh
    - q: quit
    """
    config = _get_config(project, config_path)

    tui = FeatureboxTUI(config)
    action, selected = tui.run()

    if action == "launch" and selected:
        for info in selected:
            session_name = session_name_from_branch(info.worktree.branch)
            if session_exists(session_name):
                attach_session(session_name)
            else:
                full_setup(info.worktree.branch, config)
    elif action == "cleanup" and selected:
        tui.run_with_cleanup_status(full_cleanup, selected)
    elif action == "focus" and selected:
        # Focus the first selected worktree
        focus_worktree(selected[0].worktree, config, force=True)


@app.command(name="list")
def list_cmd(
    project: ProjectOption = None,
    config_path: ConfigOption = None,
) -> None:
    """Simple list of worktrees (non-interactive)."""
    config = _get_config(project, config_path)
    simple_list(config)


@app.command()
def focus(
    input: Annotated[
        str | None,
        typer.Argument(help="Branch name or worktree path to focus"),
    ] = None,
    clear: Annotated[
        bool,
        typer.Option("--clear", help="Clear focus (unfocus current worktree)"),
    ] = False,
    show: Annotated[
        bool,
        typer.Option("--show", "-s", help="Show current focus status"),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Force focus switch"),
    ] = False,
    project: ProjectOption = None,
    config_path: ConfigOption = None,
) -> None:
    """Switch focus to a worktree, claiming shared resources.

    Focus runs configured commands (like `just docker expose-db`) to claim
    shared resources like database ports for the selected worktree.

    Only one worktree per project can have focus at a time.
    """
    config = _get_config(project, config_path)

    if show or (not input and not clear):
        # Show current focus status
        state = get_focus_state(config)
        if state.branch:
            console.print(f"[green]Focused:[/green] {state.branch}")
            console.print(f"[dim]Path: {state.worktree_path}[/dim]")
            if state.focused_at:
                console.print(f"[dim]Since: {state.focused_at.strftime('%Y-%m-%d %H:%M')}[/dim]")
        else:
            console.print("[dim]No worktree currently has focus[/dim]")
        return

    if clear:
        unfocus(config)
        return

    # Find the worktree to focus
    assert input is not None  # Guarded by condition on line 290
    worktree = get_worktree_for_input(input, config)
    if not worktree:
        console.print(f"[red]No worktree found matching: {input}[/red]")
        raise typer.Exit(1)

    focus_worktree(worktree, config, force=force)


@app.command()
def projects() -> None:
    """List configured projects from global config."""
    project_names = list_projects()

    if not project_names:
        console.print("[dim]No projects configured in ~/.config/fwts/config.toml[/dim]")
        console.print("[dim]Run 'fwts init --global' to create global config[/dim]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Project")
    table.add_column("Focus")

    for name in project_names:
        try:
            config = load_config(project_name=name)
            focused = get_focused_branch(config)
            focus_str = f"[green]{focused}[/green]" if focused else "[dim]-[/dim]"
            table.add_row(name, focus_str)
        except Exception:
            table.add_row(name, "[red]error[/red]")

    console.print(table)


@app.command()
def init(
    path: Annotated[
        Path | None,
        typer.Argument(help="Directory to initialize (default: current)"),
    ] = None,
    global_config: Annotated[
        bool,
        typer.Option("--global", "-g", help="Initialize global config instead"),
    ] = False,
) -> None:
    """Initialize fwts configuration.

    Without --global: Creates .fwts.toml in current repo.
    With --global: Creates ~/.config/fwts/config.toml with named projects.
    """
    if global_config:
        config_dir = Path.home() / ".config" / "fwts"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.toml"

        if config_file.exists():
            console.print(f"[yellow]Config file already exists: {config_file}[/yellow]")
            if not typer.confirm("Overwrite?"):
                raise typer.Exit()

        config_file.write_text(generate_global_config_example())
        console.print(f"[green]Created {config_file}[/green]")
        console.print("[dim]Edit the file to add your projects.[/dim]")
    else:
        target_dir = path or Path.cwd()
        config_file = target_dir / ".fwts.toml"

        if config_file.exists():
            console.print(f"[yellow]Config file already exists: {config_file}[/yellow]")
            if not typer.confirm("Overwrite?"):
                raise typer.Exit()

        config_file.write_text(generate_example_config())
        console.print(f"[green]Created {config_file}[/green]")
        console.print("[dim]Edit the file to configure your project settings.[/dim]")


@app.command()
def completions(
    shell: Annotated[
        str,
        typer.Argument(help="Shell to generate completions for (bash, zsh, fish)"),
    ],
    install: Annotated[
        bool,
        typer.Option("--install", "-i", help="Show installation instructions"),
    ] = False,
) -> None:
    """Generate shell completions for bash/zsh/fish."""
    shell = shell.lower()

    if install:
        console.print(install_completion(shell))
        return

    generators = {
        "bash": generate_bash,
        "zsh": generate_zsh,
        "fish": generate_fish,
    }

    if shell not in generators:
        console.print(f"[red]Unknown shell: {shell}[/red]")
        console.print("Supported: bash, zsh, fish")
        raise typer.Exit(1)

    print(generators[shell]())


@app.command()
def tickets(
    mode: Annotated[
        str,
        typer.Argument(help="Filter mode: mine, review, all"),
    ] = "mine",
    project: ProjectOption = None,
    config_path: ConfigOption = None,
) -> None:
    """Browse Linear tickets and start worktrees.

    Modes:
    - mine: Tickets assigned to you (default)
    - review: Tickets awaiting your review
    - all: All open team tickets

    Use j/k to navigate, Enter to start worktree, q to quit.
    """
    config = _get_config(project, config_path)

    if not config.linear.enabled:
        console.print("[red]Linear integration not enabled in config[/red]")
        raise typer.Exit(1)

    # Fetch tickets based on mode
    console.print(f"[dim]Fetching {mode} tickets from Linear...[/dim]")

    try:
        if mode == "mine":
            tickets_list = asyncio.run(list_my_tickets(config.linear.api_key))
        elif mode == "review":
            tickets_list = asyncio.run(list_review_requests(config.linear.api_key))
        elif mode == "all":
            tickets_list = asyncio.run(list_team_tickets(config.linear.api_key))
        else:
            console.print(f"[red]Unknown mode: {mode}[/red]")
            console.print("Valid modes: mine, review, all")
            raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Failed to fetch tickets: {e}[/red]")
        raise typer.Exit(1)

    if not tickets_list:
        console.print(f"[dim]No tickets found for mode: {mode}[/dim]")
        return

    # Run ticket picker TUI
    selected = _run_ticket_picker(tickets_list, mode)

    if selected:
        # Start worktree for selected ticket
        console.print(f"[blue]Starting worktree for {selected.identifier}...[/blue]")
        branch = selected.branch_name
        if not branch:
            # Generate branch name
            import re
            safe_title = re.sub(r"[^a-zA-Z0-9]+", "-", selected.title.lower()).strip("-")[:50]
            branch = f"{selected.identifier.lower()}-{safe_title}"

        full_setup(branch, config, ticket_info=selected.identifier)


def _run_ticket_picker(tickets: list[TicketListItem], mode: str) -> TicketListItem | None:
    """Run interactive ticket picker TUI."""
    import sys

    if not sys.stdin.isatty():
        console.print("[yellow]TUI requires interactive terminal[/yellow]")
        return None

    try:
        import readchar
    except ImportError:
        console.print("[yellow]Install 'readchar' for interactive mode[/yellow]")
        return None

    from rich.live import Live
    from rich.panel import Panel
    from rich.text import Text

    cursor = 0
    running = True

    def render() -> Panel:
        table = Table(
            title=f"[bold]Linear Tickets[/bold] ({mode})",
            show_header=True,
            header_style="bold cyan",
            border_style="dim",
        )

        table.add_column("", width=2)
        table.add_column("ID", style="cyan", width=10)
        table.add_column("Title", style="bold")
        table.add_column("State", width=12)
        table.add_column("Assignee", width=15)

        for idx, ticket in enumerate(tickets):
            prefix = ">" if idx == cursor else " "

            # Color state based on type
            state_style = {
                "backlog": "dim",
                "unstarted": "yellow",
                "started": "blue",
                "completed": "green",
                "canceled": "red",
            }.get(ticket.state_type, "dim")

            state_text = Text(ticket.state)
            state_text.stylize(state_style)

            assignee = ticket.assignee or "[dim]-[/dim]"

            # Truncate title
            title = ticket.title
            if len(title) > 50:
                title = title[:47] + "..."

            style = "reverse" if idx == cursor else None
            table.add_row(prefix, ticket.identifier, title, state_text, assignee, style=style)

        help_text = Text()
        help_text.append("j/↓", style="bold")
        help_text.append(" down  ")
        help_text.append("k/↑", style="bold")
        help_text.append(" up  ")
        help_text.append("enter", style="bold")
        help_text.append(" start worktree  ")
        help_text.append("o", style="bold")
        help_text.append(" open in browser  ")
        help_text.append("q", style="bold")
        help_text.append(" quit")

        from rich.console import Group
        return Panel(Group(table, Text(""), help_text), border_style="blue")

    KEY_UP = "\x1b[A"
    KEY_DOWN = "\x1b[B"

    selected = None

    with Live(render(), auto_refresh=False, console=console) as live:
        while running:
            live.update(render(), refresh=True)

            try:
                key = readchar.readkey()

                if key in ("q", "Q"):
                    running = False
                elif key in ("j", KEY_DOWN):
                    cursor = min(cursor + 1, len(tickets) - 1)
                elif key in ("k", KEY_UP):
                    cursor = max(cursor - 1, 0)
                elif key in ("\r", "\n"):
                    selected = tickets[cursor]
                    running = False
                elif key in ("o", "O"):
                    # Open in browser
                    import subprocess
                    ticket = tickets[cursor]
                    subprocess.run(["open", ticket.url], check=False)

            except KeyboardInterrupt:
                running = False

    return selected


if __name__ == "__main__":
    app()
