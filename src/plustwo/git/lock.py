# SPDX-License-Identifier: Zlib
"""The lock every write of a review note holds."""

import contextlib
import fcntl
from collections.abc import Iterator
from pathlib import Path

from plustwo.git.repository import current_repository, git


@contextlib.contextmanager
def review_lock(repository: str | None = None) -> Iterator[None]:
    """Lets only one process change review notes at a time.

    The terminal user interface and the author's commands both read, change
    and write notes. Without the lock, one of two writes at the same moment
    would get lost.

    Args:
        repository: the repository's directory, None for the current one
            (see current_repository()).

    Yields:
        Nothing. The lock is held while the with block runs.
    """
    repository = current_repository(repository)
    path = Path(git('rev-parse', '--git-path', 'git-change.lock', repository=repository))
    if repository and not path.is_absolute():
        path = Path(repository) / path
    with open(path, 'w') as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
