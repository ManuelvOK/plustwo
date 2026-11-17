# SPDX-License-Identifier: Zlib
"""Files the reviewer marked as reviewed or approved."""

import functools
import hashlib

from collections.abc import Sequence

from plustwo.git.repository import Repository
from plustwo.model.change import Change
from plustwo.model.place import COMMIT_MSG
from plustwo.review.changes import update_change


@functools.lru_cache(maxsize=4096)
def _file_diff_hash(repository: Repository, commit: str, path: str) -> str:
    """Hashes a file's own diff in a commit, against the commit's parent.
    The hash stays the same while the file's change does, also after a
    rebase.

    Args:
        repository: the repository.
        commit: the commit.
        path: the file's path, or COMMIT_MSG for the message.

    Returns:
        The hash, 16 hex digits.
    """
    if COMMIT_MSG == path:
        text = repository.git('log', '-1', '--format=%B', commit)
    else:
        text = repository.git('diff', '--no-color', '--no-ext-diff', '--no-textconv', '-U0',
                              f'{commit}^',
                              commit, '--', path, check=False)
        # Line numbers move with a rebase, the edits themselves don't.
        text = '\n'.join(line for line in text.split('\n') if not line.startswith('@@'))
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def is_reviewed(change: Change, path: str) -> bool:
    """Tells whether the reviewer marked a file reviewed and its diff is
    the same as then.

    Args:
        change: the change.
        path: the file's path.

    Returns:
        True if the mark holds.
    """
    mark = change.reviewed.get(path)
    return bool(mark) and mark == _file_diff_hash(change.repository, change.commit, path)


def toggle_reviewed(repository: Repository, change_id: str, path: str) -> bool:
    """Marks a file of a change reviewed, or unmarks it.

    Args:
        repository: the repository.
        change_id: the full Change-Id.
        path: the file's path.

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
        if is_reviewed(change, path):
            del change.reviewed[path]
            return False
        change.reviewed[path] = _file_diff_hash(repository, change.commit, path)
        return True

    return update_change(repository, change_id, edit)


def mark_reviewed(repository: Repository, change_id: str, path: str) -> None:
    """Marks a file of a change reviewed, also when it is already.

    Args:
        repository: the repository.
        change_id: the full Change-Id.
        path: the file's path.
    """
    def edit(change: Change) -> None:
        """Sets the mark.

        Args:
            change: the change, read fresh.
        """
        change.reviewed[path] = _file_diff_hash(repository, change.commit, path)

    update_change(repository, change_id, edit)


def is_approved(change: Change, path: str) -> bool:
    """Tells whether the reviewer marked a file approved and its diff is
    the same as then.

    Args:
        change: the change.
        path: the file's path.

    Returns:
        True if the mark holds.
    """
    mark = change.approved.get(path)
    return bool(mark) and mark == _file_diff_hash(change.repository, change.commit, path)


def toggle_approved(repository: Repository, change_id: str, path: str) -> bool:
    """Marks a file of a change approved, or unmarks it. Approving also
    marks the file reviewed, unmarking leaves reviewed as it is.

    Args:
        repository: the repository.
        change_id: the full Change-Id.
        path: the file's path.

    Returns:
        Whether the file is approved now.
    """
    def edit(change: Change) -> bool:
        """Flips the mark.

        Args:
            change: the change, read fresh.

        Returns:
            Whether the file is approved now.
        """
        if is_approved(change, path):
            del change.approved[path]
            return False
        mark = _file_diff_hash(repository, change.commit, path)
        change.approved[path] = mark
        change.reviewed[path] = mark
        return True

    return update_change(repository, change_id, edit)


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
