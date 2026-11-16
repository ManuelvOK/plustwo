# SPDX-License-Identifier: Zlib
"""Who the user is."""

import functools
import os

from plustwo.git.repository import git


def current_user():
    """Who acts: PLUSTWO_USER (agents set it to agent), else git's user.name,
    read once per process.
    """
    return os.environ.get('PLUSTWO_USER') or git_user_name()


@functools.lru_cache(maxsize=1)
def git_user_name():
    """git's user.name, 'unknown' without one. The dashboard asks for every
    change, so git runs only once.
    """
    return git('config', 'user.name', check=False) or 'unknown'
