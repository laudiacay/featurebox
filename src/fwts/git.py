"""Git operations for fwts."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(Exception):
    """Git operation failed."""

    pass


@dataclass
class Worktree:
    """Represents a git worktree."""

    path: Path
    branch: str
    head: str
    is_bare: bool = False
    is_detached: bool = False


def run_git(
    args: list[str], cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run a git command."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=check,
        )
    except subprocess.CalledProcessError as e:
        raise GitError(f"Git command failed: git {' '.join(args)}\n{e.stderr}") from e


def get_repo_root(path: Path | None = None) -> Path:
    """Get the root of the git repository."""
    result = run_git(["rev-parse", "--show-toplevel"], cwd=path)
    return Path(result.stdout.strip())


def branch_exists(branch: str, cwd: Path | None = None) -> bool:
    """Check if a branch exists locally."""
    result = run_git(
        ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=cwd,
        check=False,
    )
    return result.returncode == 0


def remote_branch_exists(branch: str, remote: str = "origin", cwd: Path | None = None) -> bool:
    """Check if a branch exists on remote."""
    result = run_git(
        ["rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}/{branch}"],
        cwd=cwd,
        check=False,
    )
    return result.returncode == 0


def branch_is_pushed(branch: str, remote: str = "origin", cwd: Path | None = None) -> bool:
    """Check if local branch is pushed to remote and up to date."""
    if not remote_branch_exists(branch, remote, cwd):
        return False

    # Check if local and remote are at same commit
    local_result = run_git(["rev-parse", f"refs/heads/{branch}"], cwd=cwd, check=False)
    remote_result = run_git(["rev-parse", f"refs/remotes/{remote}/{branch}"], cwd=cwd, check=False)

    if local_result.returncode != 0 or remote_result.returncode != 0:
        return False

    return local_result.stdout.strip() == remote_result.stdout.strip()


def get_current_branch(cwd: Path | None = None) -> str:
    """Get the current branch name."""
    result = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    return result.stdout.strip()


def list_worktrees(cwd: Path | None = None) -> list[Worktree]:
    """List all worktrees in the repository."""
    result = run_git(["worktree", "list", "--porcelain"], cwd=cwd)

    worktrees = []
    current: dict[str, str] = {}

    for line in result.stdout.strip().split("\n"):
        if not line:
            if current:
                worktrees.append(
                    Worktree(
                        path=Path(current.get("worktree", "")),
                        branch=current.get("branch", "").replace("refs/heads/", ""),
                        head=current.get("HEAD", ""),
                        is_bare=current.get("bare") == "bare",
                        is_detached="detached" in current,
                    )
                )
                current = {}
            continue

        if line.startswith("worktree "):
            current["worktree"] = line[9:]
        elif line.startswith("HEAD "):
            current["HEAD"] = line[5:]
        elif line.startswith("branch "):
            current["branch"] = line[7:]
        elif line == "bare":
            current["bare"] = "bare"
        elif line == "detached":
            current["detached"] = "detached"

    # Don't forget the last one
    if current:
        worktrees.append(
            Worktree(
                path=Path(current.get("worktree", "")),
                branch=current.get("branch", "").replace("refs/heads/", ""),
                head=current.get("HEAD", ""),
                is_bare=current.get("bare") == "bare",
                is_detached="detached" in current,
            )
        )

    return worktrees


def create_worktree(
    branch: str,
    path: Path,
    base_branch: str | None = None,
    cwd: Path | None = None,
) -> None:
    """Create a new worktree.

    If branch doesn't exist, creates it from base_branch (or current HEAD).
    """
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    if branch_exists(branch, cwd):
        # Branch exists, just add worktree
        run_git(["worktree", "add", str(path), branch], cwd=cwd)
    elif remote_branch_exists(branch, cwd=cwd):
        # Branch exists on remote, track it
        run_git(["worktree", "add", str(path), branch], cwd=cwd)
    else:
        # Create new branch
        if base_branch:
            run_git(["worktree", "add", "-b", branch, str(path), base_branch], cwd=cwd)
        else:
            run_git(["worktree", "add", "-b", branch, str(path)], cwd=cwd)


def remove_worktree(path: Path, force: bool = False, cwd: Path | None = None) -> None:
    """Remove a worktree."""
    path = path.expanduser().resolve()
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(path))
    run_git(args, cwd=cwd)


def delete_branch(branch: str, force: bool = False, cwd: Path | None = None) -> None:
    """Delete a local branch."""
    flag = "-D" if force else "-d"
    run_git(["branch", flag, branch], cwd=cwd)


def push_branch(branch: str, remote: str = "origin", cwd: Path | None = None) -> None:
    """Push a branch to remote."""
    run_git(["push", "-u", remote, branch], cwd=cwd)


def delete_remote_branch(branch: str, remote: str = "origin", cwd: Path | None = None) -> None:
    """Delete a branch from remote."""
    run_git(["push", remote, "--delete", branch], cwd=cwd)


def fetch(remote: str = "origin", cwd: Path | None = None) -> None:
    """Fetch from remote."""
    run_git(["fetch", remote], cwd=cwd)


def prune_worktrees(cwd: Path | None = None) -> None:
    """Prune stale worktree references."""
    run_git(["worktree", "prune"], cwd=cwd)


def has_graphite() -> bool:
    """Check if graphite CLI is installed."""
    try:
        subprocess.run(
            ["gt", "--version"],
            capture_output=True,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def graphite_init(trunk: str, cwd: Path | None = None) -> None:
    """Initialize graphite in the worktree."""
    subprocess.run(
        ["gt", "repo", "init", "--trunk", trunk],
        cwd=cwd,
        capture_output=True,
        check=True,
    )


def graphite_track(parent: str | None = None, cwd: Path | None = None) -> None:
    """Track current branch with graphite."""
    args = ["gt", "branch", "track"]
    if parent:
        args.extend(["--parent", parent])
    subprocess.run(args, cwd=cwd, capture_output=True, check=True)


def get_branch_from_worktree_path(path: Path) -> str | None:
    """Get branch name from worktree path."""
    worktrees = list_worktrees()
    path = path.expanduser().resolve()
    for wt in worktrees:
        if wt.path.resolve() == path:
            return wt.branch
    return None
