# SPDX-License-Identifier: Zlib
"""Results kept for git objects, which never change."""

import functools
import re
from collections.abc import Callable
from typing import Any

from plustwo.git.repository import Repository


# Object names that never change: a full hash, or the parent of one. What
# git says about them can be kept. Names like main or HEAD can't.
IMMUTABLE_NAME = re.compile(r'^[0-9a-f]{40}(\^|~[0-9]*)*$')


# How many results each cached function keeps
CACHE_SIZE = 2048


def cached_for_objects(*positions: int) -> Callable[[Callable[..., Any]], Any]:
    """Makes a decorator that keeps a function's results, the CACHE_SIZE
    latest, when the arguments at the given positions are names of objects
    that never change (see IMMUTABLE_NAME).

    Commits and blobs don't change, so neither do their diffs. The
    repository, the first argument, is part of the key. Callers must not change
    what they get, because the next caller gets the same object.

    Args:
        *positions: the positions of the arguments that must be immutable
            object names for a result to be kept, counted with the
            repository at position 0.

    Returns:
        The decorator. The decorated function has cache_clear(), like one
        of functools.lru_cache.
    """
    def decorate(function: Callable[..., Any]) -> Any:
        """Wraps a function in the cache.

        Args:
            function: the function whose results are kept.

        Returns:
            The wrapper.
        """
        @functools.lru_cache(maxsize=CACHE_SIZE)
        def cached(repository: Repository, arguments: tuple[Any, ...],
                   keywords: tuple[tuple[str, Any], ...]) -> Any:
            """Calls the function, once per key.

            Args:
                repository: the repository.
                arguments: the call's positional arguments.
                keywords: the call's keyword arguments, sorted by name.

            Returns:
                The function's result.
            """
            return function(*arguments, **dict(keywords))

        @functools.wraps(function)
        def wrapper(*arguments: Any, **keywords: Any) -> Any:
            """Calls the function through the cache if the arguments allow.

            Args:
                *arguments: the function's positional arguments.
                **keywords: the function's keyword arguments.

            Returns:
                The function's result.
            """
            if arguments and isinstance(arguments[0], Repository) and all(
                    position < len(arguments) and IMMUTABLE_NAME.match(str(arguments[position]))
                    for position in positions):
                try:
                    return cached(arguments, tuple(sorted(keywords.items())))
                except TypeError:
                    # An argument that can't be a key
                    pass
            return function(*arguments, **keywords)

        wrapper.cache_clear = cached.cache_clear  # type: ignore[attr-defined]
        return wrapper
    return decorate
