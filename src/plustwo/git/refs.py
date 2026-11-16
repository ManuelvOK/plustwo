# SPDX-License-Identifier: Zlib
"""The refs plustwo keeps, and reading refs and branches."""

import subprocess

from plustwo.git.repository import Repository


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

def refs_with_prefix(repository: Repository, *prefixes: str) -> dict[str, str]:
    """Lists the refs below some prefixes.

    Args:
        repository: the repository.
        *prefixes: the prefixes, like 'refs/plustwo/stacks/'.

    Returns:
        Each ref's full name, mapped to its object name.
    """
    output = repository.git('for-each-ref', '--format=%(refname) %(objectname)', *prefixes,
                            check=False)
    return dict(line.split(' ', 1) for line in output.split('\n') if line)


def commits_between(repository: Repository, start: str, end: str) -> list[str]:
    """Lists the commits on end that are not on start.

    Args:
        repository: the repository.
        start: the commit where the range starts, not included.
        end: the commit where the range ends, included.

    Returns:
        The commits' object names, oldest first.
    """
    output = repository.git('rev-list', '--reverse', f'{start}..{end}', check=False)
    return output.split('\n') if output else []


def current_branch(repository: Repository) -> str | None:
    """Finds the checked out branch.

    Args:
        repository: the repository.

    Returns:
        The branch's short name, None on a detached HEAD.
    """
    return repository.git('symbolic-ref', '--quiet', '--short', 'HEAD', check=False) or None


def is_ancestor(repository: Repository, commit: str, descendant: str) -> bool:
    """Tells whether a commit is on another one's history.

    Args:
        repository: the repository.
        commit: the commit that may be an ancestor.
        descendant: the commit whose history is searched.

    Returns:
        True if commit is descendant or one of its ancestors.
    """
    return 0 == subprocess.run([*repository.git_command(),
                                'merge-base', '--is-ancestor', commit, descendant],
                               capture_output=True).returncode
