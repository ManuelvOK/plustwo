# SPDX-License-Identifier: Zlib
"""The refs plustwo keeps, and reading refs and branches."""

import os
import subprocess
from pathlib import Path

from plustwo.git.repository import current_repository, git, git_command


# ── the refs plustwo keeps ────────────────────────────────────────────────────

# The review notes, one JSON note per commit
NOTES_REF = 'refs/notes/review'

# The review state, like the repository on a Gerrit server: the uploaded
# stacks and the patch sets. The author's local branches stay apart.
PLUSTWO_PREFIX = 'refs/plustwo/'

# The uploaded stacks, one ref per topic
STACK_PREFIX = 'refs/plustwo/stacks/'

# The base branch of each stack, a symbolic ref to it (refs/heads/<base>),
# like the target branch of a change in Gerrit. It stays after a submit or
# abandon, so submitted changes are found on it. Delete one with git
# symbolic-ref --delete, git update-ref -d would delete the branch.
STACK_BASE_PREFIX = 'refs/plustwo/bases/'

# The patch sets, as <Change-Id>/<number>
PATCH_SET_PREFIX = 'refs/plustwo/patchsets/'

# The abandoned stacks, one ref per topic
ABANDONED_PREFIX = 'refs/plustwo/abandoned/'

# Where older versions kept patch sets and stacks (local review/ branches)
OLD_PATCH_SET_PREFIX = 'refs/patchsets/'
REVIEW_BRANCH_PREFIX = 'review/'


# ── reading refs and branches ─────────────────────────────────────────────────

# Each directory, mapped to the working tree of its repository, see
# toplevel()
_TOPLEVELS: dict[str, str] = {}


def toplevel(repository: str | None = None) -> str | None:
    """Finds the working tree of a repository, asking git once per directory.

    Args:
        repository: a directory in the repository, None for the current
            one (see current_repository()).

    Returns:
        The working tree's absolute path, None outside a repository.
    """
    directory = os.path.abspath(current_repository(repository) or os.getcwd())
    if directory not in _TOPLEVELS:
        result = subprocess.run(['git', '-C', directory, 'rev-parse', '--show-toplevel'],
                                capture_output=True, text=True)
        found = result.stdout.strip() or None if 0 == result.returncode else None
        if found is None:
            # Not kept: the directory can become a repository.
            return None
        _TOPLEVELS[directory] = found
    return _TOPLEVELS[directory]


def refs_with_prefix(*prefixes: str, repository: str | None = None) -> dict[str, str]:
    """Lists the refs below some prefixes.

    Args:
        *prefixes: the prefixes, like 'refs/plustwo/stacks/'.
        repository: the repository's directory, None for the current one.

    Returns:
        Each ref's full name, mapped to its object name.
    """
    output = git('for-each-ref', '--format=%(refname) %(objectname)', *prefixes, check=False,
                 repository=repository)
    return dict(line.split(' ', 1) for line in output.split('\n') if line)


def commits_between(start: str, end: str, repository: str | None = None) -> list[str]:
    """Lists the commits on end that are not on start.

    Args:
        start: the commit where the range starts, not included.
        end: the commit where the range ends, included.
        repository: the repository's directory, None for the current one.

    Returns:
        The commits' object names, oldest first.
    """
    output = git('rev-list', '--reverse', f'{start}..{end}', check=False, repository=repository)
    return output.split('\n') if output else []


def current_branch(repository: str | None = None) -> str | None:
    """Finds the checked out branch.

    Args:
        repository: the repository's directory, None for the current one.

    Returns:
        The branch's short name, None on a detached HEAD.
    """
    return git('symbolic-ref', '--quiet', '--short', 'HEAD', check=False,
               repository=repository) or None


def is_ancestor(commit: str, descendant: str, repository: str | None = None) -> bool:
    """Tells whether a commit is on another one's history.

    Args:
        commit: the commit that may be an ancestor.
        descendant: the commit whose history is searched.
        repository: the repository's directory, None for the current one.

    Returns:
        True if commit is descendant or one of its ancestors.
    """
    return 0 == subprocess.run([*git_command(repository),
                                'merge-base', '--is-ancestor', commit, descendant],
                               capture_output=True).returncode


def git_path(name: str, repository: str | None = None) -> Path:
    """Builds the absolute path of a file inside the .git directory.

    Args:
        name: the file's path relative to the .git directory.
        repository: the repository's directory, None for the current one.

    Returns:
        The absolute path, which git resolves for worktrees too.
    """
    repository = current_repository(repository)
    path = Path(git('rev-parse', '--git-path', name, repository=repository))
    return path if path.is_absolute() else (Path(repository or '.') / path).absolute()
