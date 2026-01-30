"""Tmux session management for fwts."""

from __future__ import annotations

import subprocess
from pathlib import Path

from fwts.config import TmuxConfig


class TmuxError(Exception):
    """Tmux operation failed."""

    pass


def has_tmux() -> bool:
    """Check if tmux is installed."""
    try:
        subprocess.run(["tmux", "-V"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def session_exists(name: str) -> bool:
    """Check if a tmux session exists."""
    result = subprocess.run(
        ["tmux", "has-session", "-t", name],
        capture_output=True,
    )
    return result.returncode == 0


def create_session(name: str, path: Path, config: TmuxConfig) -> None:
    """Create a new tmux session with editor and side command.

    Args:
        name: Session name
        path: Working directory for the session
        config: Tmux configuration
    """
    path = path.expanduser().resolve()

    if session_exists(name):
        raise TmuxError(f"Session '{name}' already exists")

    # Create new detached session
    subprocess.run(
        [
            "tmux",
            "new-session",
            "-d",
            "-s",
            name,
            "-c",
            str(path),
        ],
        check=True,
    )

    # Get the first window and pane indices (handles base-index and pane-base-index)
    result = subprocess.run(
        ["tmux", "list-panes", "-t", name, "-F", "#{window_index}.#{pane_index}"],
        capture_output=True,
        text=True,
    )
    first_pane = result.stdout.strip().split("\n")[0] if result.stdout.strip() else "0.0"
    first_window = first_pane.split(".")[0]

    # Run editor in first pane
    subprocess.run(
        [
            "tmux",
            "send-keys",
            "-t",
            f"{name}:{first_pane}",
            config.editor,
            "Enter",
        ],
        check=True,
    )

    # Split window based on layout preference
    split_flag = "-h" if config.layout == "vertical" else "-v"
    subprocess.run(
        [
            "tmux",
            "split-window",
            split_flag,
            "-t",
            f"{name}:{first_window}",
            "-c",
            str(path),
        ],
        check=True,
    )

    # Get the second pane index after split
    result = subprocess.run(
        ["tmux", "list-panes", "-t", name, "-F", "#{window_index}.#{pane_index}"],
        capture_output=True,
        text=True,
    )
    panes = result.stdout.strip().split("\n") if result.stdout.strip() else []
    second_pane = panes[1] if len(panes) > 1 else f"{first_window}.1"

    # Run side command in second pane
    subprocess.run(
        [
            "tmux",
            "send-keys",
            "-t",
            f"{name}:{second_pane}",
            config.side_command,
            "Enter",
        ],
        check=True,
    )

    # Focus on the editor pane
    subprocess.run(
        ["tmux", "select-pane", "-t", f"{name}:{first_pane}"],
        check=True,
    )


def attach_session(name: str) -> None:
    """Attach to an existing tmux session.

    If already in tmux, switches to the session.
    Otherwise, attaches to it.
    """
    import os

    if os.environ.get("TMUX"):
        # Already in tmux, switch client
        subprocess.run(
            ["tmux", "switch-client", "-t", name],
            check=True,
        )
    else:
        # Not in tmux, attach
        subprocess.run(
            ["tmux", "attach-session", "-t", name],
            check=True,
        )


def kill_session(name: str) -> None:
    """Kill a tmux session."""
    if session_exists(name):
        subprocess.run(
            ["tmux", "kill-session", "-t", name],
            capture_output=True,
        )


def list_sessions() -> list[str]:
    """List all tmux session names."""
    result = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    return [s.strip() for s in result.stdout.strip().split("\n") if s.strip()]


def session_name_from_branch(branch: str) -> str:
    """Generate a valid tmux session name from a branch name.

    Tmux session names can't contain '.' or ':'.
    """
    return branch.replace(".", "-").replace(":", "-").replace("/", "-")
