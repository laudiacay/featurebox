"""Shell completion generation for fwts."""

from __future__ import annotations


def generate_bash() -> str:
    """Generate bash completion script."""
    return """# fwts bash completion
_fwts_completions() {
    local cur prev opts
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    # Main commands
    opts="start cleanup status list init completions --help --version"

    case "${prev}" in
        fwts|fb)
            COMPREPLY=( $(compgen -W "${opts}" -- ${cur}) )
            return 0
            ;;
        start)
            # Could complete branch names or Linear tickets
            return 0
            ;;
        cleanup)
            # Complete with existing worktree branches
            local branches=$(git worktree list --porcelain 2>/dev/null | grep "^branch" | cut -d' ' -f2 | sed 's|refs/heads/||')
            COMPREPLY=( $(compgen -W "${branches} --force" -- ${cur}) )
            return 0
            ;;
        completions)
            COMPREPLY=( $(compgen -W "bash zsh fish" -- ${cur}) )
            return 0
            ;;
    esac

    COMPREPLY=( $(compgen -W "${opts}" -- ${cur}) )
}

complete -F _fwts_completions fwts
complete -F _fwts_completions fb
"""


def generate_zsh() -> str:
    """Generate zsh completion script."""
    return """#compdef fwts fb

_fwts() {
    local -a commands
    commands=(
        'start:Start or resume a feature worktree'
        'cleanup:Clean up worktree, branch, tmux session, docker'
        'status:Interactive TUI - view all worktrees'
        'list:Simple list of worktrees (non-interactive)'
        'init:Initialize .fwts.toml in current repo'
        'completions:Generate shell completions'
    )

    _arguments -C \\
        '1: :->command' \\
        '*: :->args'

    case $state in
        command)
            _describe 'command' commands
            ;;
        args)
            case $words[2] in
                start)
                    # Could complete branch names or Linear tickets
                    ;;
                cleanup)
                    # Complete with existing worktree branches
                    local branches
                    branches=(${(f)"$(git worktree list --porcelain 2>/dev/null | grep "^branch" | cut -d' ' -f2 | sed 's|refs/heads/||')"})
                    _describe 'branch' branches
                    _arguments '--force[Force removal]'
                    ;;
                completions)
                    _values 'shell' bash zsh fish
                    ;;
            esac
            ;;
    esac
}

compdef _fwts fwts fb
"""


def generate_fish() -> str:
    """Generate fish completion script."""
    return """# fwts fish completions

# Disable file completions for all subcommands
complete -c fwts -f
complete -c fb -f

# Main commands
complete -c fwts -n "__fish_use_subcommand" -a "start" -d "Start or resume a feature worktree"
complete -c fwts -n "__fish_use_subcommand" -a "cleanup" -d "Clean up worktree, branch, tmux session, docker"
complete -c fwts -n "__fish_use_subcommand" -a "status" -d "Interactive TUI - view all worktrees"
complete -c fwts -n "__fish_use_subcommand" -a "list" -d "Simple list of worktrees (non-interactive)"
complete -c fwts -n "__fish_use_subcommand" -a "init" -d "Initialize .fwts.toml in current repo"
complete -c fwts -n "__fish_use_subcommand" -a "completions" -d "Generate shell completions"

complete -c fb -n "__fish_use_subcommand" -a "start" -d "Start or resume a feature worktree"
complete -c fb -n "__fish_use_subcommand" -a "cleanup" -d "Clean up worktree, branch, tmux session, docker"
complete -c fb -n "__fish_use_subcommand" -a "status" -d "Interactive TUI - view all worktrees"
complete -c fb -n "__fish_use_subcommand" -a "list" -d "Simple list of worktrees (non-interactive)"
complete -c fb -n "__fish_use_subcommand" -a "init" -d "Initialize .fwts.toml in current repo"
complete -c fb -n "__fish_use_subcommand" -a "completions" -d "Generate shell completions"

# cleanup subcommand
complete -c fwts -n "__fish_seen_subcommand_from cleanup" -l force -s f -d "Force removal"
complete -c fb -n "__fish_seen_subcommand_from cleanup" -l force -s f -d "Force removal"

# completions subcommand
complete -c fwts -n "__fish_seen_subcommand_from completions" -a "bash zsh fish"
complete -c fb -n "__fish_seen_subcommand_from completions" -a "bash zsh fish"
"""


def install_completion(shell: str) -> str:
    """Get installation instructions for a shell."""
    instructions = {
        "bash": """# Add to ~/.bashrc:
eval "$(fwts completions bash)"

# Or save to a file:
fwts completions bash > ~/.local/share/bash-completion/completions/fwts""",
        "zsh": """# Add to ~/.zshrc:
eval "$(fwts completions zsh)"

# Or save to a file:
fwts completions zsh > ~/.zfunc/_fwts
# Then add to fpath in .zshrc:
fpath=(~/.zfunc $fpath)
autoload -Uz compinit && compinit""",
        "fish": """# Save to fish completions directory:
fwts completions fish > ~/.config/fish/completions/fwts.fish""",
    }
    return instructions.get(shell, f"Unknown shell: {shell}")
