# SPDX-License-Identifier: Zlib
"""Settings: own files in git config syntax, user and repository."""

import functools
import os
import subprocess
from pathlib import Path

from plustwo.git.repository import current_repository, git


# The base branch without the setting review.base
_DEFAULT_BASE = 'main'

# Settings that lived in git config before, read from there as a fallback:
# each setting's key, mapped to its old key
_LEGACY_SETTINGS = {'review.base': 'change.base'}

# Each directory, mapped to its repository's settings file, see
# _config_files(). The interfaces read settings on every poll, so git is
# asked once.
_REPOSITORY_CONFIG_FILES: dict[str, Path] = {}

# A file's modification time in nanoseconds and its size, see _file_stamp()
FileStamp = tuple[int, int]


# ── where settings are ────────────────────────────────────────────────────────

def _user_config_path() -> Path:
    """Finds the user's settings file.

    Returns:
        The path of the file, in git config syntax, which need not exist.
    """
    home = os.environ.get('XDG_CONFIG_HOME') or str(Path.home() / '.config')
    return Path(home) / 'plustwo' / 'config'


def _repository_config_path(repository: str | None = None) -> Path:
    """Finds the repository's settings file, inside .git.

    Args:
        repository: the repository's directory, None for the current one.

    Returns:
        The absolute path of the file, in git config syntax, which need not
        exist.
    """
    repository = current_repository(repository)
    path = Path(git('rev-parse', '--git-path', 'plustwo/config', repository=repository))
    return Path(repository) / path if repository and not path.is_absolute() else path.absolute()


def _config_files(repository: str | None = None) -> list[Path]:
    """Finds the settings files.

    Args:
        repository: the repository's directory, None for the current one.

    Returns:
        The files, the one that wins first: the repository's, then the
        user's. The repository's only inside a repository.
    """
    directory = os.path.abspath(current_repository(repository) or os.getcwd())
    repository_file = _REPOSITORY_CONFIG_FILES.get(directory)
    if repository_file is None:
        is_inside = 0 == subprocess.run(['git', '-C', directory, 'rev-parse', '--git-dir'],
                                        capture_output=True).returncode
        repository_file = _repository_config_path(directory) if is_inside else None
        # Only a found one is kept: a directory can become a repository.
        if repository_file is not None:
            _REPOSITORY_CONFIG_FILES[directory] = repository_file
    return ([repository_file] if repository_file else []) + [_user_config_path()]


# ── reading files in git config syntax ────────────────────────────────────────

def _file_stamp(path: str | Path) -> FileStamp | None:
    """Reads what tells that a file changed.

    Args:
        path: the file's path.

    Returns:
        (modification time in nanoseconds, size), None when the file
        doesn't exist.
    """
    try:
        status = os.stat(path)
    except OSError:
        return None
    return status.st_mtime_ns, status.st_size


def config_entries(path: str | Path) -> tuple[tuple[str, str], ...]:
    """Reads the entries of a file in git config syntax, again only when the
    file changed.

    Args:
        path: the file's path.

    Returns:
        (key, value) pairs in the file's order, keys as git writes them
        (section and name in lower case). Empty if the file doesn't exist.
    """
    return _read_config_entries(str(path), _file_stamp(path))


@functools.lru_cache(maxsize=64)
def _read_config_entries(path: str, stamp: FileStamp | None) -> tuple[tuple[str, str], ...]:
    """Reads the entries of a file in git config syntax, once per stamp.

    Args:
        path: the file's path.
        stamp: the file's _file_stamp(), part of the cache's key. None for
            a file that doesn't exist.

    Returns:
        (key, value) pairs, see config_entries().
    """
    if stamp is None:
        return ()
    output = git('config', '--file', path, '--list', '-z', check=False)
    entries = []
    for entry in filter(None, output.split('\0')):
        key, _, value = entry.partition('\n')
        entries.append((key, value))
    return tuple(entries)


def _config_key(key: str) -> str:
    """Writes a key as git config lists it.

    Args:
        key: the key, like Review.Base or list.MyList.repo.

    Returns:
        The key with section and name in lower case and a subsection
        between them as it is.
    """
    section, _, rest = key.partition('.')
    subsection, _, name = rest.rpartition('.')
    return '.'.join(filter(None, (section.lower(), subsection, name.lower())))


def config_values(path: str | Path, key: str) -> list[str]:
    """Reads all values of a key in a file in git config syntax.

    Args:
        path: the file's path.
        key: the key, in any case.

    Returns:
        The values in the file's order.
    """
    key = _config_key(key)
    return [value for name, value in config_entries(path) if name == key]


# ── settings ──────────────────────────────────────────────────────────────────

def setting(key: str, default: str | None = None, repository: str | None = None) -> str | None:
    """Reads a setting like review.base. The files use git config syntax
    and are read with git config --file, so no setting of plustwo goes into
    git's own config.

    Args:
        key: the setting's key.
        default: the value when no file has the setting.
        repository: the repository's directory, None for the current one.

    Returns:
        The value from the repository's file, else the user's, else (for
        older settings) git config, else default.
    """
    for path in _config_files(repository):
        values = config_values(path, key)
        if values and values[-1]:
            return values[-1]
    legacy_key = _LEGACY_SETTINGS.get(key)
    if legacy_key:
        value = _legacy_setting(legacy_key, current_repository(repository) or os.getcwd())
        if value:
            return value
    return default


def _legacy_setting(key: str, repository: str) -> str:
    """Reads an older setting from git's own config, again only when one of
    git's config files changed.

    Args:
        key: the setting's old key, like change.base.
        repository: the repository's directory.

    Returns:
        The value, '' without one.
    """
    home = Path.home()
    xdg = Path(os.environ.get('XDG_CONFIG_HOME') or home / '.config')
    stamps = tuple(_file_stamp(path) for path in (
        Path(repository) / '.git' / 'config', home / '.gitconfig', xdg / 'git' / 'config'))
    return _read_legacy_setting(key, repository, stamps)


@functools.lru_cache(maxsize=64)
def _read_legacy_setting(key: str, repository: str,
                         _stamps: tuple[FileStamp | None, ...]) -> str:
    """Reads an older setting from git's own config, once per stamps.

    Args:
        key: the setting's old key.
        repository: the repository's directory.
        _stamps: the stamps of git's config files, only part of the
            cache's key.

    Returns:
        The value, '' without one.
    """
    return git('config', key, check=False, repository=repository)


def settings_section(section: str, repository: str | None = None) -> dict[str, str]:
    """Reads all settings of a section, like keys.

    Args:
        section: the section's name.
        repository: the repository's directory, None for the current one.

    Returns:
        Each name in the section, mapped to its value, the repository's
        over the user's.
    """
    values = {}
    start = section.lower() + '.'
    for path in reversed(_config_files(repository)):
        for key, value in config_entries(path):
            if key.startswith(start):
                values[key[len(start):]] = value
    return values


def set_setting(key: str, value: str | None, is_for_user: bool = False,
                repository: str | None = None) -> None:
    """Stores a setting.

    Args:
        key: the setting's key.
        value: the new value, None to remove the setting.
        is_for_user: whether the setting goes into the user's file instead
            of the repository's.
        repository: the repository's directory, None for the current one.
    """
    path = _user_config_path() if is_for_user else _repository_config_path(repository)
    if value is None:
        if path.exists():
            git('config', '--file', str(path), '--unset', key, check=False)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    git('config', '--file', str(path), key, value)


def list_settings(repository: str | None = None) -> list[str]:
    """Lists every setting.

    Args:
        repository: the repository's directory, None for the current one.

    Returns:
        Lines like "key=value  (file)", the user's file first.
    """
    lines = []
    for path in reversed(_config_files(repository)):
        if path.exists():
            output = git('config', '--file', str(path), '--list', check=False)
            lines += [f'{line}  ({path})' for line in filter(None, output.split('\n'))]
    return lines


def migrate_settings(repository: str | None = None) -> list[str]:
    """Moves settings of older versions out of the repository's git config
    into its own file. Ones in the user's global git config are left alone,
    they still count as a fallback.

    Args:
        repository: the repository's directory, None for the current one.

    Returns:
        A line like "change.base -> review.base" per moved setting.
    """
    moved = []
    for key, legacy_key in _LEGACY_SETTINGS.items():
        value = git('config', '--local', legacy_key, check=False, repository=repository)
        if not value:
            continue
        path = _repository_config_path(repository)
        if not (path.exists() and git('config', '--file', str(path), '--get', key, check=False)):
            set_setting(key, value, repository=repository)
        git('config', '--local', '--unset', legacy_key, repository=repository)
        moved.append(f'{legacy_key} -> {key}')
    return moved


def base_branch(repository: str | None = None) -> str:
    """Finds the branch reviewed changes go to.

    Args:
        repository: the repository's directory, None for the current one.

    Returns:
        The setting review.base, else main.
    """
    return setting('review.base', _DEFAULT_BASE, repository) or _DEFAULT_BASE
