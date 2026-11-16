# SPDX-License-Identifier: Zlib
"""Commits: their trees, patch ids, Change-Ids and rebased trees."""

import re
import subprocess

from plustwo.git.cache import cached_for_objects
from plustwo.git.repository import git, git_command


CHANGE_ID_PATTERN = re.compile(r'^Change-Id: (I[0-9a-f]{40})\s*$', re.MULTILINE)


def change_id_of(commit, repository=None):
    """The Change-Id in a commit's message, None if it has none."""
    match = CHANGE_ID_PATTERN.search(git('log', '-1', '--format=%B', commit,
                                         repository=repository))
    return match.group(1) if match else None


@cached_for_objects(0)
def tree_of(commit, repository=None):
    """The tree of a commit."""
    return git('rev-parse', f'{commit}^{{tree}}', repository=repository)


def patch_id(commit, repository=None):
    """The patch id of a commit: the same for the same diff, whatever its
    parent and message.
    """
    patch = git('diff-tree', '-p', '--root', commit, repository=repository)
    output = git('patch-id', '--stable', input_text=patch + '\n', repository=repository)
    return output.split(' ')[0] if output else ''


def rebased_tree(commit, parent, repository=None):
    """The tree of a commit replayed onto another parent, like a rebase
    without touching anything: a merge of the commit into parent with the
    commit's own parent as base. '' on a conflict, None when git can't do
    it in memory: that needs git 2.40 (merge-tree --merge-base).
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


def raw_commit(commit, repository=None):
    """A commit's author (name, email, date) and its message, exactly."""
    text = git('cat-file', 'commit', commit, repository=repository) + '\n'
    headers, _, message = text.partition('\n\n')
    author = next(line for line in headers.split('\n') if line.startswith('author '))
    match = re.match(r'author (.*) <(.*)> (\d+ [+-]\d{4})$', author)
    return match.groups(), message
