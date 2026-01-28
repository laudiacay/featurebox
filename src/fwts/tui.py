"""Interactive TUI for fwts status dashboard."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from fwts.config import Config
from fwts.focus import get_focused_branch, has_focus
from fwts.git import Worktree, list_worktrees
from fwts.hooks import HookResult, get_builtin_hooks, run_all_hooks
from fwts.tmux import session_exists, session_name_from_branch

console = Console()


@dataclass
class WorktreeInfo:
    """Extended worktree information with hook data."""

    worktree: Worktree
    session_active: bool = False
    has_focus: bool = False
    hook_data: dict[str, HookResult] = field(default_factory=dict)


class FeatureboxTUI:
    """Interactive TUI with multi-select table."""

    def __init__(self, config: Config):
        self.config = config
        self.worktrees: list[WorktreeInfo] = []
        self.selected: set[int] = set()
        self.cursor: int = 0
        self.running = True
        self.needs_refresh = True
        self.loading = False

    def _get_feature_worktrees(self) -> list[Worktree]:
        """Get worktrees excluding main repo."""
        main_repo = self.config.project.main_repo.expanduser().resolve()
        all_worktrees = list_worktrees(main_repo)

        # Filter out bare repos and main branch
        return [
            wt
            for wt in all_worktrees
            if not wt.is_bare and wt.branch != self.config.project.base_branch
        ]

    async def _load_data(self) -> None:
        """Load worktree data and run hooks."""
        self.loading = True
        worktrees = self._get_feature_worktrees()

        # Create WorktreeInfo objects
        self.worktrees = []
        for wt in worktrees:
            session_name = session_name_from_branch(wt.branch)
            self.worktrees.append(
                WorktreeInfo(
                    worktree=wt,
                    session_active=session_exists(session_name),
                    has_focus=has_focus(wt, self.config),
                )
            )

        # Get hooks (configured + builtin)
        hooks = self.config.tui.columns if self.config.tui.columns else get_builtin_hooks()

        # Run hooks in parallel
        if hooks and worktrees:
            hook_results = await run_all_hooks(hooks, worktrees)
            for info in self.worktrees:
                if info.worktree.path in hook_results:
                    info.hook_data = hook_results[info.worktree.path]

        self.loading = False
        self.needs_refresh = False

    def _render_table(self) -> Table:
        """Render the worktree table."""
        # Get project name and focus info for title
        project_name = self.config.project.name or "fwts"
        focused_branch = get_focused_branch(self.config)
        focus_info = f" [green]◉ {focused_branch}[/green]" if focused_branch else ""

        table = Table(
            title=f"[bold]{project_name}[/bold]{focus_info}",
            show_header=True,
            header_style="bold cyan",
            border_style="dim",
        )

        table.add_column("", width=3)  # Selection/cursor
        table.add_column("Branch", style="bold")
        table.add_column("Focus", width=5)
        table.add_column("Tmux", width=5)

        # Add hook columns
        hooks = self.config.tui.columns if self.config.tui.columns else get_builtin_hooks()
        for hook in hooks:
            table.add_column(hook.name, width=12)

        table.add_column("Path", style="dim")

        if self.loading:
            table.add_row("[dim]Loading...[/dim]")
            return table

        if not self.worktrees:
            table.add_row("[dim]No feature worktrees found[/dim]")
            return table

        for idx, info in enumerate(self.worktrees):
            # Cursor and selection
            cursor = ">" if idx == self.cursor else " "
            selected = "✓" if idx in self.selected else " "
            prefix = f"{cursor}{selected}"

            # Branch name
            branch = info.worktree.branch

            # Focus status
            focus = "[green]◉[/green]" if info.has_focus else "[dim]○[/dim]"

            # Session status
            session = "[green]●[/green]" if info.session_active else "[dim]○[/dim]"

            # Hook columns
            hook_values = []
            for hook in hooks:
                result = info.hook_data.get(hook.name)
                if result:
                    text = Text(result.value)
                    if result.color:
                        text.stylize(result.color)
                    hook_values.append(text)
                else:
                    hook_values.append(Text("-", style="dim"))

            # Path (shortened)
            path = str(info.worktree.path).replace(str(Path.home()), "~")

            # Highlight row if selected
            style = "reverse" if idx == self.cursor else None

            table.add_row(prefix, branch, focus, session, *hook_values, path, style=style)

        return table

    def _render_help(self) -> Panel:
        """Render help panel."""
        help_text = (
            "[bold]j/↓[/bold] down  "
            "[bold]k/↑[/bold] up  "
            "[bold]space[/bold] select  "
            "[bold]a[/bold] all  "
            "[bold]enter[/bold] launch  "
            "[bold]f[/bold] focus  "
            "[bold]d[/bold] cleanup  "
            "[bold]r[/bold] refresh  "
            "[bold]q[/bold] quit"
        )
        return Panel(help_text, border_style="dim")

    def _handle_key(self, key: str) -> str | None:
        """Handle keyboard input.

        Returns action to perform: 'launch', 'cleanup', 'focus', or None
        """
        if key in ("q", "Q"):
            self.running = False
            return None

        if key in ("j", "KEY_DOWN"):
            self.cursor = min(self.cursor + 1, len(self.worktrees) - 1)
        elif key in ("k", "KEY_UP"):
            self.cursor = max(self.cursor - 1, 0)
        elif key == " ":
            if self.cursor in self.selected:
                self.selected.discard(self.cursor)
            else:
                self.selected.add(self.cursor)
        elif key in ("a", "A"):
            if len(self.selected) == len(self.worktrees):
                self.selected.clear()
            else:
                self.selected = set(range(len(self.worktrees)))
        elif key in ("KEY_ENTER", "\r", "\n"):
            return "launch"
        elif key in ("d", "D"):
            return "cleanup"
        elif key in ("f", "F"):
            return "focus"
        elif key in ("r", "R"):
            self.needs_refresh = True

        return None

    def get_selected_worktrees(self) -> list[WorktreeInfo]:
        """Get currently selected worktrees."""
        if not self.selected:
            # If nothing selected, return current cursor position
            if 0 <= self.cursor < len(self.worktrees):
                return [self.worktrees[self.cursor]]
            return []
        return [self.worktrees[i] for i in sorted(self.selected)]

    def run(self) -> tuple[str | None, list[WorktreeInfo]]:
        """Run the TUI.

        Returns:
            Tuple of (action, selected_worktrees) where action is 'launch', 'cleanup', 'focus', or None
        """
        # Simple fallback for non-TTY or when keyboard input isn't available
        if not sys.stdin.isatty():
            console.print("[yellow]TUI requires interactive terminal[/yellow]")
            return None, []

        try:
            import readchar  # type: ignore[import-not-found]
        except ImportError:
            console.print(
                "[yellow]Install 'readchar' for interactive mode: pip install readchar[/yellow]"
            )
            console.print("[dim]Falling back to list mode...[/dim]")
            return None, []

        # Initial data load
        asyncio.run(self._load_data())

        action = None
        selected = []

        with Live(auto_refresh=False) as live:
            while self.running:
                # Render
                table = self._render_table()
                help_panel = self._render_help()
                live.update(
                    Panel.fit(table, subtitle=help_panel.renderable),  # type: ignore[arg-type]
                    refresh=True,
                )

                # Handle input
                try:
                    key = readchar.readkey()
                    action = self._handle_key(key)

                    if action:
                        selected = self.get_selected_worktrees()
                        self.running = False
                        break

                    if self.needs_refresh:
                        asyncio.run(self._load_data())

                except KeyboardInterrupt:
                    self.running = False
                    break

        return action, selected


def simple_list(config: Config) -> None:
    """Display a simple non-interactive list of worktrees."""
    main_repo = config.project.main_repo.expanduser().resolve()
    worktrees = list_worktrees(main_repo)

    # Filter out bare repos and main branch
    feature_worktrees = [
        wt for wt in worktrees if not wt.is_bare and wt.branch != config.project.base_branch
    ]

    if not feature_worktrees:
        console.print("[dim]No feature worktrees found[/dim]")
        return

    # Get focus info
    focused_branch = get_focused_branch(config)

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Branch")
    table.add_column("Focus", width=5)
    table.add_column("Tmux", width=5)
    table.add_column("Path")

    for wt in feature_worktrees:
        session_name = session_name_from_branch(wt.branch)
        focus = "[green]◉[/green]" if wt.branch == focused_branch else "[dim]○[/dim]"
        session = "[green]●[/green]" if session_exists(session_name) else "[dim]○[/dim]"
        path = str(wt.path).replace(str(Path.home()), "~")
        table.add_row(wt.branch, focus, session, path)

    console.print(table)
