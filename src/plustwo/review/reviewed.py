# SPDX-License-Identifier: Zlib
"""Files the reviewer marked as reviewed."""

import functools
import hashlib

from collections.abc import Sequence

from plustwo.git.repository import git
from plustwo.model.change import Change
from plustwo.model.place import COMMIT_MSG
from plustwo.review.changes import update_change


@functools.lru_cache(maxsize=4096)
def _file_diff_hash(commit: str, path: str, repository: str | None = None) -> str:
    """Hashes a file's own diff in a commit, against the commit's parent.
    The hash stays the same while the file's change does, also after a
    rebase.

    Args:
        commit: the commit.
        path: the file's path, or COMMIT_MSG for the message.
        repository: the repository's directory, None for the current one.

    Returns:
        The hash, 16 hex digits.
    """
    if COMMIT_MSG == path:
        text = git('log', '-1', '--format=%B', commit, repository=repository)
    else:
        text = git('diff', '--no-color', '--no-ext-diff', '--no-textconv', '-U0', f'{commit}^',
                   commit, '--', path, check=False, repository=repository)
        # Line numbers move with a rebase, the edits themselves don't.
        text = '\n'.join(line for line in text.split('\n') if not line.startswith('@@'))
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def is_reviewed(change: Change, path: str, repository: str | None = None) -> bool:
    """Tells whether the reviewer marked a file reviewed and its diff is
    the same as then.

    Args:
        change: the change.
        path: the file's path.
        repository: the repository's directory, None for the current one.

    Returns:
        True if the mark holds.
    """
    mark = change.reviewed.get(path)
    return bool(mark) and mark == _file_diff_hash(change.commit, path, repository)


def toggle_reviewed(change_id: str, path: str, repository: str | None = None) -> bool:
    """Marks a file of a change reviewed, or unmarks it.

    Args:
        change_id: the full Change-Id.
        path: the file's path.
        repository: the repository's directory, None for the one the
            change is in review in.

    Returns:
        Whether the file is marked now.
    """
    def edit(change: Change) -> bool:
        """Flips the mark.

        Args:
            change: the change, read fresh.

        Returns:
            Whether the file is marked now.
        """
        if is_reviewed(change, path, repository):
            del change.reviewed[path]
            return False
        change.reviewed[path] = _file_diff_hash(change.commit, path, repository)
        return True

    return update_change(change_id, edit, repository)


def mark_reviewed(change_id: str, path: str, repository: str | None = None) -> None:
    """Marks a file of a change reviewed, also when it is already.

    Args:
        change_id: the full Change-Id.
        path: the file's path.
        repository: the repository's directory, None for the one the
            change is in review in.
    """
    def edit(change: Change) -> None:
        """Sets the mark.

        Args:
            change: the change, read fresh.
        """
        change.reviewed[path] = _file_diff_hash(change.commit, path, repository)

    update_change(change_id, edit, repository)


def file_after(files: Sequence[str], path: str) -> str | None:
    """Finds the file after another in the file list.

    Args:
        files: the files in list order.
        path: the file.

    Returns:
        The next file, None after the last.
    """
    index = files.index(path) + 1 if path in files else len(files)
    return files[index] if index < len(files) else None
