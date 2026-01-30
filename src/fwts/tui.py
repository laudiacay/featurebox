"""Interactive TUI for fwts status dashboard."""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from fwts.config import Config
from fwts.focus import get_focused_branch, has_focus
from fwts.git import Worktree, list_worktrees
from fwts.github import get_pr_by_branch
from fwts.hooks import HookResult, get_builtin_hooks, run_all_hooks
from fwts.tmux import session_exists, session_name_from_branch

console = Console()

# Auto-refresh interval in seconds
AUTO_REFRESH_INTERVAL = 30


@dataclass
class WorktreeInfo:
    """Extended worktree information with hook data."""

    worktree: Worktree
    session_active: bool = False
    has_focus: bool = False
    hook_data: dict[str, HookResult] = field(default_factory=dict)
    pr_url: str | None = None


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
        self.status_message: str | None = None
        self.status_style: str = "dim"
        self.last_refresh: float = 0
        self._refresh_lock = threading.Lock()

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
        with self._refresh_lock:
            self.loading = True
            self.status_message = "Refreshing..."
            self.status_style = "yellow"

        worktrees = self._get_feature_worktrees()
        github_repo = self.config.project.github_repo

        # Create WorktreeInfo objects
        new_worktrees = []
        for wt in worktrees:
            session_name = session_name_from_branch(wt.branch)
            info = WorktreeInfo(
                worktree=wt,
                session_active=session_exists(session_name),
                has_focus=has_focus(wt, self.config),
            )

            # Fetch PR URL
            if github_repo:
                try:
                    pr = get_pr_by_branch(wt.branch, github_repo)
                    if pr:
                        info.pr_url = pr.url
                except Exception:
                    pass

            new_worktrees.append(info)

        # Get hooks (configured + builtin)
        hooks = self.config.tui.columns if self.config.tui.columns else get_builtin_hooks()

        # Run hooks in parallel
        if hooks and worktrees:
            hook_results = await run_all_hooks(hooks, worktrees)
            for info in new_worktrees:
                if info.worktree.path in hook_results:
                    info.hook_data = hook_results[info.worktree.path]

        with self._refresh_lock:
            self.worktrees = new_worktrees
            self.loading = False
            self.needs_refresh = False
            self.last_refresh = time.time()
            self.status_message = None

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

        table.add_column("PR", style="cyan")

        if self.loading and not self.worktrees:
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

            # Branch name (truncate if too long)
            branch = info.worktree.branch
            if len(branch) > 50:
                branch = branch[:47] + "..."

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

            # PR URL or path
            if info.pr_url:
                # Show just the PR number as a clickable-looking link
                pr_num = info.pr_url.split("/")[-1]
                pr_display = f"#{pr_num}"
            else:
                pr_display = "[dim]no PR[/dim]"

            # Highlight row if at cursor
            style = "reverse" if idx == self.cursor else None

            table.add_row(prefix, branch, focus, session, *hook_values, pr_display, style=style)

        return table

    def _render_help(self) -> Text:
        """Render help text."""
        help_text = Text()
        help_text.append("j/↓", style="bold")
        help_text.append(" down  ")
        help_text.append("k/↑", style="bold")
        help_text.append(" up  ")
        help_text.append("space", style="bold")
        help_text.append(" select  ")
        help_text.append("a", style="bold")
        help_text.append(" all  ")
        help_text.append("enter", style="bold")
        help_text.append(" launch  ")
        help_text.append("f", style="bold")
        help_text.append(" focus  ")
        help_text.append("d", style="bold")
        help_text.append(" cleanup  ")
        help_text.append("r", style="bold")
        help_text.append(" refresh  ")
        help_text.append("q", style="bold")
        help_text.append(" quit")
        return help_text

    def _render_status(self) -> Text:
        """Render status line."""
        status = Text()

        if self.status_message:
            status.append(self.status_message, style=self.status_style)
        elif self.loading:
            status.append("⟳ Refreshing...", style="yellow")
        else:
            # Show time since last refresh
            elapsed = int(time.time() - self.last_refresh)
            if elapsed < 60:
                status.append(f"Updated {elapsed}s ago", style="dim")
            else:
                mins = elapsed // 60
                status.append(f"Updated {mins}m ago", style="dim")

            # Show auto-refresh info
            next_refresh = AUTO_REFRESH_INTERVAL - (elapsed % AUTO_REFRESH_INTERVAL)
            status.append(f" · auto-refresh in {next_refresh}s", style="dim")

        return status

    def _render(self) -> Panel:
        """Render the full TUI."""
        table = self._render_table()
        help_text = self._render_help()
        status_text = self._render_status()

        # Combine help and status
        footer = Text()
        footer.append_text(help_text)
        footer.append("\n")
        footer.append_text(status_text)

        return Panel(
            Group(table, Text(""), footer),
            border_style="blue",
        )

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

    def set_status(self, message: str, style: str = "dim") -> None:
        """Set status message."""
        self.status_message = message
        self.status_style = style

    def clear_status(self) -> None:
        """Clear status message."""
        self.status_message = None

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

        with Live(self._render(), auto_refresh=False, console=console) as live:
            while self.running:
                # Update display
                live.update(self._render(), refresh=True)

                # Check for auto-refresh
                if time.time() - self.last_refresh > AUTO_REFRESH_INTERVAL:
                    self.needs_refresh = True

                # Handle input with timeout for auto-refresh
                try:
                    # Use a short timeout to allow checking auto-refresh
                    import select

                    if select.select([sys.stdin], [], [], 1.0)[0]:
                        key = readchar.readkey()
                        action = self._handle_key(key)

                        if action:
                            selected = self.get_selected_worktrees()
                            self.running = False
                            break

                    # Refresh data if needed
                    if self.needs_refresh:
                        live.update(self._render(), refresh=True)
                        asyncio.run(self._load_data())
                        live.update(self._render(), refresh=True)

                except KeyboardInterrupt:
                    self.running = False
                    break

        return action, selected

    def run_with_cleanup_status(
        self, cleanup_func: callable, worktrees: list[WorktreeInfo]
    ) -> None:
        """Run cleanup with status updates in the TUI.

        Args:
            cleanup_func: Function to call for cleanup (takes worktree and config)
            worktrees: Worktrees to clean up
        """
        if not sys.stdin.isatty():
            # Fall back to simple execution
            for info in worktrees:
                cleanup_func(info.worktree, self.config)
            return

        with Live(self._render(), auto_refresh=False, console=console) as live:
            for i, info in enumerate(worktrees):
                branch = info.worktree.branch
                self.set_status(
                    f"Cleaning up [{i + 1}/{len(worktrees)}]: {branch}...",
                    style="yellow",
                )
                live.update(self._render(), refresh=True)

                try:
                    cleanup_func(info.worktree, self.config)
                    self.set_status(f"✓ Cleaned up: {branch}", style="green")
                except Exception as e:
                    self.set_status(f"✗ Failed: {branch} - {e}", style="red")

                live.update(self._render(), refresh=True)
                time.sleep(0.5)  # Brief pause to show status

            # Final status
            self.set_status(f"Cleanup complete ({len(worktrees)} worktrees)", style="green")
            live.update(self._render(), refresh=True)
            time.sleep(1)


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
    github_repo = config.project.github_repo

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Branch")
    table.add_column("Focus", width=5)
    table.add_column("Tmux", width=5)
    table.add_column("PR")

    for wt in feature_worktrees:
        session_name = session_name_from_branch(wt.branch)
        focus = "[green]◉[/green]" if wt.branch == focused_branch else "[dim]○[/dim]"
        session = "[green]●[/green]" if session_exists(session_name) else "[dim]○[/dim]"

        # Fetch PR URL
        pr_display = "[dim]no PR[/dim]"
        if github_repo:
            try:
                pr = get_pr_by_branch(wt.branch, github_repo)
                if pr:
                    pr_num = pr.url.split("/")[-1]
                    pr_display = f"[cyan]#{pr_num}[/cyan]"
            except Exception:
                pass

        table.add_row(wt.branch, focus, session, pr_display)

    console.print(table)
