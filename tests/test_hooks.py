"""Tests for hook execution."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fwts.config import ColumnHook
from fwts.git import Worktree
from fwts.hooks import get_builtin_hooks, run_hook


@pytest.fixture
def sample_worktree():
    """Create a sample worktree for testing."""
    return Worktree(
        path=Path("/tmp/test-worktree"),
        branch="feature-branch",
        head="abc123",
    )


@pytest.fixture
def sample_hook():
    """Create a sample hook configuration."""
    return ColumnHook(
        name="CI",
        hook='echo "success"',
        color_map={"success": "green", "failure": "red"},
    )


@pytest.mark.asyncio
async def test_run_hook_success(sample_worktree, sample_hook):
    """Test running a hook that succeeds."""
    with patch("fwts.hooks.anyio.run_process") as mock_run:
        mock_run.return_value = MagicMock(stdout=b"success\n")

        result = await run_hook(sample_hook, sample_worktree)

        assert result.value == "success"
        assert result.color == "green"
        assert result.column_name == "CI"


@pytest.mark.asyncio
async def test_run_hook_with_color_map(sample_worktree):
    """Test hook result color mapping."""
    hook = ColumnHook(
        name="Review",
        hook='echo "APPROVED"',
        color_map={"approved": "green", "changes_requested": "red"},
    )

    with patch("fwts.hooks.anyio.run_process") as mock_run:
        mock_run.return_value = MagicMock(stdout=b"APPROVED\n")

        result = await run_hook(hook, sample_worktree)

        # Should match case-insensitively
        assert result.color == "green"


@pytest.mark.asyncio
async def test_run_hook_partial_color_match(sample_worktree):
    """Test hook result partial color matching."""
    hook = ColumnHook(
        name="Status",
        hook='echo "test success here"',
        color_map={"success": "green", "failure": "red"},
    )

    with patch("fwts.hooks.anyio.run_process") as mock_run:
        mock_run.return_value = MagicMock(stdout=b"test success here\n")

        result = await run_hook(hook, sample_worktree)

        # Should match 'success' as partial match
        assert result.color == "green"


@pytest.mark.asyncio
async def test_run_hook_truncates_long_output(sample_worktree):
    """Test that hook output is truncated for display."""
    hook = ColumnHook(
        name="Long",
        hook='echo "this is a very long output that should be truncated"',
        color_map={},
    )

    with patch("fwts.hooks.anyio.run_process") as mock_run:
        long_output = "x" * 100
        mock_run.return_value = MagicMock(stdout=long_output.encode())

        result = await run_hook(hook, sample_worktree)

        assert len(result.value) <= 20


@pytest.mark.asyncio
async def test_run_hook_handles_error(sample_worktree, sample_hook):
    """Test hook handling when command fails."""
    with patch("fwts.hooks.anyio.run_process") as mock_run:
        mock_run.side_effect = Exception("Command failed")

        result = await run_hook(sample_hook, sample_worktree)

        assert result.value == "error"


@pytest.mark.asyncio
async def test_run_hook_sets_env_vars(sample_worktree, sample_hook):
    """Test that hook receives correct environment variables."""
    with patch("fwts.hooks.anyio.run_process") as mock_run:
        mock_run.return_value = MagicMock(stdout=b"ok\n")

        await run_hook(sample_hook, sample_worktree)

        # Check that env was passed correctly
        call_kwargs = mock_run.call_args[1]
        env = call_kwargs["env"]
        assert env["WORKTREE_PATH"] == str(sample_worktree.path)
        assert env["BRANCH_NAME"] == sample_worktree.branch


def test_get_builtin_hooks():
    """Test that builtin hooks are returned."""
    hooks = get_builtin_hooks()

    assert len(hooks) >= 2
    hook_names = [h.name for h in hooks]
    assert "CI" in hook_names
    assert "PR" in hook_names


def test_builtin_hooks_have_color_maps():
    """Test that builtin hooks have color maps configured."""
    hooks = get_builtin_hooks()

    for hook in hooks:
        assert hook.color_map, f"Hook {hook.name} should have color_map"
