# SPDX-License-Identifier: Zlib
"""Settings: own files in git config syntax, user and repository."""

import functools
import os
import subprocess
from pathlib import Path

from plustwo.git.repository import current_repository, git


DEFAULT_BASE = 'main'


# Settings that lived in git config before, read from there as a fallback
LEGACY_SETTINGS = {'review.base': 'change.base'}


def user_config_path():
    """The user's settings file, in git config syntax."""
    home = os.environ.get('XDG_CONFIG_HOME') or str(Path.home() / '.config')
    return Path(home) / 'plustwo' / 'config'


def repository_config_path(repository=None):
    """The repository's settings file, in git config syntax, inside .git."""
    repository = current_repository(repository)
    path = Path(git('rev-parse', '--git-path', 'plustwo/config', repository=repository))
    return Path(repository) / path if repository and not path.is_absolute() else path.absolute()


def config_files(repository=None):
    """The settings files, the one that wins first: the repository's, then
    the user's. The repository's only inside a repository.
    """
    directory = os.path.abspath(current_repository(repository) or os.getcwd())
    own = _REPOSITORY_CONFIG_FILES.get(directory)
    if own is None:
        inside = subprocess.run(['git', '-C', directory, 'rev-parse', '--git-dir'],
                                capture_output=True).returncode == 0
        own = repository_config_path(directory) if inside else None
        # Only a found one is kept: a directory can become a repository.
        if own is not None:
            _REPOSITORY_CONFIG_FILES[directory] = own
    return ([own] if own else []) + [user_config_path()]


# Directory -> its repository's settings file, see config_files(). The
# interfaces read settings on every poll, so git is asked once.
_REPOSITORY_CONFIG_FILES = {}


def file_stamp(path):
    """What tells that a file changed: (modification time, size), None
    when it doesn't exist.
    """
    try:
        status = os.stat(path)
    except OSError:
        return None
    return status.st_mtime_ns, status.st_size


def config_entries(path):
    """The (key, value) pairs of a file in git config syntax, in order,
    keys as git writes them (section and name in lower case). Read again
    only when the file changed, see file_stamp().
    """
    return _config_entries(str(path), file_stamp(path))


@functools.lru_cache(maxsize=64)
def _config_entries(path, stamp):
    if stamp is None:
        return ()
    output = git('config', '--file', path, '--list', '-z', check=False)
    return tuple(tuple(entry.partition('\n')[::2]) for entry in output.split('\0') if entry)


def config_key(key):
    """A key as git config lists it: section and name in lower case, a
    subsection between them as it is.
    """
    section, _, rest = key.partition('.')
    middle, _, name = rest.rpartition('.')
    return '.'.join(filter(None, (section.lower(), middle, name.lower())))


def config_values(path, key):
    """All values of a key in a file in git config syntax."""
    key = config_key(key)
    return [value for name, value in config_entries(path) if name == key]


def setting(key, default=None, repository=None):
    """A setting like review.base: from the repository's file, else the
    user's, else (for older settings) git config, else default. The files
    use git config syntax and are read with git config --file, so no
    setting of ours goes into git's own config.
    """
    for path in config_files(repository):
        values = config_values(path, key)
        if values and values[-1]:
            return values[-1]
    legacy = LEGACY_SETTINGS.get(key)
    if legacy:
        value = legacy_setting(legacy, current_repository(repository) or os.getcwd())
        if value:
            return value
    return default


def legacy_setting(key, repository):
    """An older setting from git's own config, read again only when one of
    its files changed.
    """
    home = Path.home()
    xdg = Path(os.environ.get('XDG_CONFIG_HOME') or home / '.config')
    stamps = tuple(file_stamp(path) for path in (
        Path(repository) / '.git' / 'config', home / '.gitconfig', xdg / 'git' / 'config'))
    return _legacy_setting(key, repository, stamps)


@functools.lru_cache(maxsize=64)
def _legacy_setting(key, repository, _stamps):
    return git('config', key, check=False, repository=repository)


def settings_section(section, repository=None):
    """All settings of a section like keys, as {name: value}, the
    repository's over the user's.
    """
    values = {}
    start = section.lower() + '.'
    for path in reversed(config_files(repository)):
        for key, value in config_entries(path):
            if key.startswith(start):
                values[key[len(start):]] = value
    return values


def set_setting(key, value, user=False, repository=None):
    """Stores a setting in the repository's file, or with user in the
    user's. Value None removes it.
    """
    path = user_config_path() if user else repository_config_path(repository)
    if value is None:
        if path.exists():
            git('config', '--file', str(path), '--unset', key, check=False)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    git('config', '--file', str(path), key, value)


def list_settings(repository=None):
    """Every setting as "key=value (file)" lines, the user's first."""
    lines = []
    for path in reversed(config_files(repository)):
        if path.exists():
            output = git('config', '--file', str(path), '--list', check=False)
            lines += [f'{line}  ({path})' for line in filter(None, output.split('\n'))]
    return lines


def migrate_settings(repository=None):
    """Moves settings of older versions out of the repository's git config
    into its own file. Ones in the user's global git config are left
    alone, they still count as a fallback. Returns the moved keys.
    """
    moved = []
    for key, legacy in LEGACY_SETTINGS.items():
        value = git('config', '--local', legacy, check=False, repository=repository)
        if not value:
            continue
        path = repository_config_path(repository)
        if not (path.exists() and git('config', '--file', str(path), '--get', key, check=False)):
            set_setting(key, value, repository=repository)
        git('config', '--local', '--unset', legacy, repository=repository)
        moved.append(f'{legacy} -> {key}')
    return moved


def base_branch(repository=None):
    """The branch reviewed changes go to: the setting review.base, else
    main.
    """
    return setting('review.base', DEFAULT_BASE, repository)
