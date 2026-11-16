# SPDX-License-Identifier: Zlib
"""Results kept for git objects, which never change."""

import functools
import re

from plustwo.git.repository import current_repository


# Object names that never change: a full hash, or the parent of one. What
# git says about them can be kept. Names like main or HEAD can't.
IMMUTABLE_NAME = re.compile(r'^[0-9a-f]{40}(\^|~[0-9]*)*$')


# How many results each cached function keeps
CACHE_SIZE = 2048


def cached_for_objects(*positions):
    """Keeps a function's results, the CACHE_SIZE latest, when the
    arguments at these positions are names of objects that never change
    (see IMMUTABLE_NAME). Commits and blobs don't change, so neither do
    their diffs. The repository a call acts on is part of the key.
    Callers must not change what they get.
    """
    def decorate(function):
        @functools.lru_cache(maxsize=CACHE_SIZE)
        def cached(repository, arguments, keywords):
            return function(*arguments, **dict(keywords))

        @functools.wraps(function)
        def wrapper(*arguments, **keywords):
            if all(position < len(arguments) and IMMUTABLE_NAME.match(str(arguments[position]))
                   for position in positions):
                try:
                    return cached(current_repository(keywords.get('repository')), arguments,
                                  tuple(sorted(keywords.items())))
                except TypeError:
                    # An argument that can't be a key
                    pass
            return function(*arguments, **keywords)

        wrapper.cache_clear = cached.cache_clear
        return wrapper
    return decorate
