# SPDX-License-Identifier: Zlib
"""Who the user is."""

import functools
import os

from plustwo.git.repository import run_git



def current_user() -> str:
    """Finds who acts.

    Returns:
        PLUSTWO_USER (agents set it to agent), else git's user.name.
    """
    return os.environ.get('PLUSTWO_USER') or git_user_name()


@functools.lru_cache(maxsize=1)
def git_user_name() -> str:
    """Reads git's user.name, once per process: the dashboard asks for every
    change.

    Returns:
        The name, 'unknown' without one.
    """
    return run_git('config', 'user.name', check=False) or 'unknown'
