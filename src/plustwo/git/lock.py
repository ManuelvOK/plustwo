# SPDX-License-Identifier: Zlib
"""The lock every write of a review note holds."""

import contextlib
import fcntl
from collections.abc import Iterator

from plustwo.git.repository import Repository


@contextlib.contextmanager
def review_lock(repository: Repository) -> Iterator[None]:
    """Lets only one process change review notes at a time.

    The terminal user interface and the author's commands both read, change
    and write notes. Without the lock, one of two writes at the same moment
    would get lost.

    Args:
        repository: the repository.

    Yields:
        Nothing. The lock is held while the with block runs.
    """
    path = repository.git_path('git-change.lock')
    with open(path, 'w') as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
