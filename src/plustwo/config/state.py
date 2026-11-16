# SPDX-License-Identifier: Zlib
"""State files in git config syntax, below $XDG_STATE_HOME/plustwo.

State is what plustwo remembers, as opposed to what the user configures:
the registered repositories, the dashboard's sections, the diff's
follow-latest switch, when watch last reported.
"""

import os
import re
from pathlib import Path

from plustwo.git.repository import run_git

from plustwo.config.settings import config_entries, config_values


def state_path(name: str) -> Path:
    """Finds a state file.

    Args:
        name: the file's name, like repos or dashboard.

    Returns:
        The file's path, which need not exist.
    """
    home = os.environ.get('XDG_STATE_HOME') or str(Path.home() / '.local' / 'state')
    return Path(home) / 'plustwo' / name


def state_values(name: str, key: str) -> list[str]:
    """Reads all values of a key in a state file.

    Args:
        name: the file's name, see state_path().
        key: the key.

    Returns:
        The values that aren't empty, in the file's order.
    """
    return [value for value in config_values(state_path(name), key) if value]


def state_entries_matching(name: str, pattern: str) -> list[tuple[str, str]]:
    """Reads the entries of a state file whose keys match a pattern.

    Args:
        name: the file's name, see state_path().
        pattern: a regular expression, searched in each key.

    Returns:
        (key, value) pairs in the file's order.
    """
    return [(key, value) for key, value in config_entries(state_path(name))
            if re.search(pattern, key)]


def set_state(name: str, key: str, value: str | None, do_add: bool = False) -> None:
    """Changes a key of a state file.

    Args:
        name: the file's name, see state_path().
        key: the key.
        value: the new value, None to remove all of the key's values.
        do_add: whether to add the value to the key's values instead of
            replacing them.
    """
    path = state_path(name)
    if value is None:
        if path.exists():
            run_git('config', '--file', str(path), '--unset-all', key, check=False)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    run_git('config', '--file', str(path), *(['--add'] if do_add else []), key, value)
