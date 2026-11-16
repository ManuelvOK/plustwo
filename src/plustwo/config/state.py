# SPDX-License-Identifier: Zlib
"""State files in git config syntax, below $XDG_STATE_HOME/plustwo."""

import os
import re
from pathlib import Path

from plustwo.config.settings import config_entries, config_values
from plustwo.git.repository import git


def state_path(name):
    """A state file, like repos or dashboard."""
    home = os.environ.get('XDG_STATE_HOME') or str(Path.home() / '.local' / 'state')
    return Path(home) / 'plustwo' / name


def state_values(name, key):
    """All values of a key in a state file."""
    return [value for value in config_values(state_path(name), key) if value]


def state_regexp(name, pattern):
    """(key, value) pairs of a state file whose keys match a pattern."""
    return [(key, value) for key, value in config_entries(state_path(name))
            if re.search(pattern, key)]


def set_state(name, key, value, add=False):
    """Sets a key of a state file, adds a value with add, removes all of
    its values with value None.
    """
    path = state_path(name)
    if value is None:
        if path.exists():
            git('config', '--file', str(path), '--unset-all', key, check=False)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    git('config', '--file', str(path), *(['--add'] if add else []), key, value)
