# SPDX-License-Identifier: Zlib
"""The refs plustwo keeps, and reading refs and branches."""

import os
import subprocess
from pathlib import Path

from plustwo.git.repository import current_repository, git, git_command


NOTES_REF = 'refs/notes/review'


# The review state, like the repository on a Gerrit server: the uploaded
# stacks and the patch sets. The author's local branches stay apart.
PLUSTWO_PREFIX = 'refs/plustwo/'


STACK_PREFIX = 'refs/plustwo/stacks/'


# The base branch of each stack, a symbolic ref to it (refs/heads/<base>),
# like the target branch of a change in Gerrit. It stays after a submit or
# abandon, so submitted changes are found on it. Delete one with git
# symbolic-ref --delete, git update-ref -d would delete the branch.
STACK_BASE_PREFIX = 'refs/plustwo/bases/'


PATCH_SET_PREFIX = 'refs/plustwo/patchsets/'


# Where older versions kept patch sets and stacks (local review/ branches)
OLD_PATCH_SET_PREFIX = 'refs/patchsets/'


REVIEW_BRANCH_PREFIX = 'review/'


def toplevel(repository=None):
    """The absolute path of a repository's working tree, None outside one.
    Asked once per directory.
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


# Directory -> its repository's working tree, see toplevel()
_TOPLEVELS = {}


def refs_with_prefix(*prefixes, repository=None):
    """Refs below the prefixes: name -> object."""
    output = git('for-each-ref', '--format=%(refname) %(objectname)', *prefixes, check=False,
                 repository=repository)
    return dict(line.split(' ') for line in output.split('\n') if line)


def commits_between(start, end, repository=None):
    """Commits on end that are not on start, oldest first."""
    output = git('rev-list', '--reverse', f'{start}..{end}', check=False, repository=repository)
    return output.split('\n') if output else []


def current_branch(repository=None):
    """The checked out branch, None on a detached HEAD."""
    return git('symbolic-ref', '--quiet', '--short', 'HEAD', check=False,
               repository=repository) or None


def is_ancestor(commit, other, repository=None):
    """Whether commit is on other."""
    return 0 == subprocess.run([*git_command(repository),
                                'merge-base', '--is-ancestor', commit, other],
                               capture_output=True).returncode


ABANDONED_PREFIX = 'refs/plustwo/abandoned/'


def git_path(name, repository=None):
    """An absolute path inside the repository's .git directory."""
    repository = current_repository(repository)
    path = Path(git('rev-parse', '--git-path', name, repository=repository))
    return path if path.is_absolute() else (Path(repository or '.') / path).absolute()
