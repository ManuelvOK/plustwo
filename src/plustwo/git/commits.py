# SPDX-License-Identifier: Zlib
"""Commits: their trees, patch ids, Change-Ids and rebased trees."""

import re
import subprocess

from plustwo.git.cache import cached_for_objects
from plustwo.git.repository import git, git_command


# The Change-Id trailer of a commit message, like Gerrit's
CHANGE_ID_PATTERN = re.compile(r'^Change-Id: (I[0-9a-f]{40})\s*$', re.MULTILINE)


def change_id_of(commit: str, repository: str | None = None) -> str | None:
    """Reads the Change-Id in a commit's message.

    Args:
        commit: the commit, any name git understands.
        repository: the repository's directory, None for the current one.

    Returns:
        The Change-Id, None if the message has none.
    """
    match = CHANGE_ID_PATTERN.search(git('log', '-1', '--format=%B', commit,
                                         repository=repository))
    return match.group(1) if match else None


@cached_for_objects(0)
def tree_of(commit: str, repository: str | None = None) -> str:
    """Finds the tree of a commit.

    Args:
        commit: the commit, any name git understands.
        repository: the repository's directory, None for the current one.

    Returns:
        The tree's object name.
    """
    return git('rev-parse', f'{commit}^{{tree}}', repository=repository)


def patch_id(commit: str, repository: str | None = None) -> str:
    """Computes the patch id of a commit: the same for the same diff,
    whatever the commit's parent and message.

    Args:
        commit: the commit, any name git understands.
        repository: the repository's directory, None for the current one.

    Returns:
        The patch id, '' for a commit without changes.
    """
    patch = git('diff-tree', '-p', '--root', commit, repository=repository)
    output = git('patch-id', '--stable', input_text=patch + '\n', repository=repository)
    return output.split(' ')[0] if output else ''


def rebased_tree(commit: str, parent: str, repository: str | None = None) -> str | None:
    """Replays a commit onto another parent, like a rebase without touching
    anything: a merge of the commit into the parent with the commit's own
    parent as base.

    Args:
        commit: the commit to replay.
        parent: the new parent.
        repository: the repository's directory, None for the current one.

    Returns:
        The resulting tree's object name. '' on a conflict. None when git
        can't do it in memory, which needs git 2.40 (merge-tree
        --merge-base).
    """
    result = subprocess.run([*git_command(repository), 'merge-tree', '--write-tree',
                             f'--merge-base={commit}^', parent, commit],
                            capture_output=True, text=True)
    # merge-tree exits with 1 on conflicts, anything else is an error.
    if 1 == result.returncode:
        return ''
    if 0 != result.returncode:
        return None
    return result.stdout.split('\n')[0].strip() or None


def commit_author_and_message(commit: str, repository: str | None = None
                              ) -> tuple[tuple[str, str, str], str]:
    """Reads a commit's author and message exactly as they are stored, to
    write a copy of the commit.

    Args:
        commit: the commit, any name git understands.
        repository: the repository's directory, None for the current one.

    Returns:
        ((author's name, email, date as "<seconds> <zone>"), message).

    Raises:
        ValueError: the commit has no author line git would write.
    """
    text = git('cat-file', 'commit', commit, repository=repository) + '\n'
    headers, _, message = text.partition('\n\n')
    author = next(line for line in headers.split('\n') if line.startswith('author '))
    match = re.match(r'author (.*) <(.*)> (\d+ [+-]\d{4})$', author)
    if match is None:
        raise ValueError(f'unexpected author line in {commit}: {author}')
    name, email, date = match.groups()
    return (name, email, date), message
