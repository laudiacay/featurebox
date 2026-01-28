"""Tests for lifecycle orchestration."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fwts.config import (
    Config,
    DockerConfig,
    LifecycleConfig,
    ProjectConfig,
    TmuxConfig,
)
from fwts.git import Worktree
from fwts.lifecycle import (
    create_symlinks,
    get_worktree_for_input,
    run_lifecycle_commands,
)


@pytest.fixture
def sample_config():
    """Create a sample configuration for testing."""
    return Config(
        project=ProjectConfig(
            name="test",
            main_repo=Path("/tmp/main"),
            worktree_base=Path("/tmp/worktrees"),
            base_branch="main",
        ),
        lifecycle=LifecycleConfig(
            on_start=["echo start"],
            on_cleanup=["echo cleanup"],
        ),
        tmux=TmuxConfig(),
        docker=DockerConfig(),
        symlinks=[".env.local", "config/settings.json"],
    )


@pytest.fixture
def sample_worktrees():
    """Create sample worktrees for testing."""
    return [
        Worktree(
            path=Path("/tmp/worktrees/feature-a"),
            branch="feature-a",
            head="abc123",
        ),
        Worktree(
            path=Path("/tmp/worktrees/feature-b"),
            branch="feature-b",
            head="def456",
        ),
        Worktree(
            path=Path("/tmp/main"),
            branch="main",
            head="789ghi",
        ),
    ]


def test_create_symlinks(tmp_path):
    """Test creating symlinks from main repo to worktree."""
    main_repo = tmp_path / "main"
    worktree = tmp_path / "worktree"

    # Create source files
    main_repo.mkdir()
    (main_repo / ".env.local").write_text("SECRET=value")
    (main_repo / "config").mkdir()
    (main_repo / "config" / "settings.json").write_text("{}")

    # Create worktree directory
    worktree.mkdir()
    (worktree / "config").mkdir()

    symlinks = [".env.local", "config/settings.json"]

    create_symlinks(worktree, main_repo, symlinks)

    # Check symlinks were created
    assert (worktree / ".env.local").is_symlink()
    assert (worktree / ".env.local").resolve() == (main_repo / ".env.local").resolve()
    assert (worktree / "config" / "settings.json").is_symlink()


def test_create_symlinks_skips_missing(tmp_path):
    """Test that create_symlinks skips missing source files."""
    main_repo = tmp_path / "main"
    worktree = tmp_path / "worktree"

    main_repo.mkdir()
    worktree.mkdir()

    # Only create one of the symlink sources
    (main_repo / ".env.local").write_text("exists")

    symlinks = [".env.local", "nonexistent.txt"]

    # Should not raise
    create_symlinks(worktree, main_repo, symlinks)

    assert (worktree / ".env.local").is_symlink()
    assert not (worktree / "nonexistent.txt").exists()


def test_run_lifecycle_commands_on_start(tmp_path, sample_config):
    """Test running on_start lifecycle commands."""
    with patch("fwts.lifecycle.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)

        run_lifecycle_commands("on_start", tmp_path, sample_config)

        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[0][0] == "echo start"
        assert call_args[1]["shell"] is True
        assert call_args[1]["cwd"] == tmp_path


def test_run_lifecycle_commands_on_cleanup(tmp_path, sample_config):
    """Test running on_cleanup lifecycle commands."""
    with patch("fwts.lifecycle.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)

        run_lifecycle_commands("on_cleanup", tmp_path, sample_config)

        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[0][0] == "echo cleanup"


def test_get_worktree_for_input_exact_match(sample_config, sample_worktrees):
    """Test finding worktree by exact branch name."""
    with patch("fwts.lifecycle.list_worktrees") as mock_list:
        mock_list.return_value = sample_worktrees

        result = get_worktree_for_input("feature-a", sample_config)

        assert result is not None
        assert result.branch == "feature-a"


def test_get_worktree_for_input_partial_match(sample_config, sample_worktrees):
    """Test finding worktree by partial branch name."""
    with patch("fwts.lifecycle.list_worktrees") as mock_list:
        mock_list.return_value = sample_worktrees

        result = get_worktree_for_input("feature-b", sample_config)

        assert result is not None
        assert result.branch == "feature-b"


def test_get_worktree_for_input_path_match(sample_config, tmp_path):
    """Test finding worktree by path."""
    # Use real tmp_path to avoid macOS /tmp -> /private/tmp issues
    worktree_path = tmp_path / "worktrees" / "feature-a"
    worktrees = [
        Worktree(
            path=worktree_path,
            branch="feature-a",
            head="abc123",
        ),
    ]

    with patch("fwts.lifecycle.list_worktrees") as mock_list:
        mock_list.return_value = worktrees

        result = get_worktree_for_input(str(worktree_path), sample_config)

        assert result is not None
        assert result.branch == "feature-a"


def test_get_worktree_for_input_excludes_main(sample_config, sample_worktrees):
    """Test that main branch worktree is excluded from matching."""
    with patch("fwts.lifecycle.list_worktrees") as mock_list:
        mock_list.return_value = sample_worktrees

        result = get_worktree_for_input("main", sample_config)

        # Should not match main branch
        assert result is None


def test_get_worktree_for_input_no_match(sample_config, sample_worktrees):
    """Test returning None when no worktree matches."""
    with patch("fwts.lifecycle.list_worktrees") as mock_list:
        mock_list.return_value = sample_worktrees

        result = get_worktree_for_input("nonexistent", sample_config)

        assert result is None
