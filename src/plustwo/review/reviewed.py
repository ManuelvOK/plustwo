# SPDX-License-Identifier: Zlib
"""Files the reviewer marked as reviewed."""

import functools
import hashlib

from plustwo.git.repository import git
from plustwo.model.place import COMMIT_MSG
from plustwo.review.changes import update_change


@functools.lru_cache(maxsize=4096)
def file_diff_hash(commit, path, repository=None):
    """A hash of a file's own diff in a commit, against the commit's parent.
    It stays the same while the file's change does, also after a rebase.
    """
    if COMMIT_MSG == path:
        text = git('log', '-1', '--format=%B', commit, repository=repository)
    else:
        text = git('diff', '--no-color', '--no-ext-diff', '--no-textconv', '-U0', f'{commit}^',
                   commit, '--', path, check=False, repository=repository)
        # Line numbers move with a rebase, the edits themselves don't.
        text = '\n'.join(line for line in text.split('\n') if not line.startswith('@@'))
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def is_reviewed(change, path, repository=None):
    """Whether the reviewer marked the file reviewed and its diff is the same
    as then.
    """
    mark = change.reviewed.get(path)
    return bool(mark) and mark == file_diff_hash(change.commit, path, repository)


def toggle_reviewed(change_id, path, repository=None):
    """Marks a file of the change reviewed, or unmarks it. Returns whether
    it is marked now.
    """
    def edit(change):
        if is_reviewed(change, path, repository):
            del change.reviewed[path]
            return False
        change.reviewed[path] = file_diff_hash(change.commit, path, repository)
        return True

    return update_change(change_id, edit, repository)


def mark_reviewed(change_id, path, repository=None):
    """Marks a file of the change reviewed, also when it is already."""
    def edit(change):
        change.reviewed[path] = file_diff_hash(change.commit, path, repository)

    update_change(change_id, edit, repository)


def file_after(files, path):
    """The file after path in the file list, None on the last."""
    index = files.index(path) + 1 if path in files else len(files)
    return files[index] if index < len(files) else None
