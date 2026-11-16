# SPDX-License-Identifier: Zlib
"""Settings: own files in git config syntax, user and repository."""

import functools
import os
from pathlib import Path

from plustwo.git.repository import Repository, run_git


# The base branch without the setting review.base
_DEFAULT_BASE = 'main'

# Settings that lived in git config before, read from there as a fallback:
# each setting's key, mapped to its old key
_LEGACY_SETTINGS = {'review.base': 'change.base'}

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


def _config_files(repository: Repository) -> list[Path]:
    """Finds the settings files.

    Args:
        repository: the repository.

    Returns:
        The files, the one that wins first: the repository's, then the
        user's.
    """
    return [repository.config_file, _user_config_path()]


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
    output = run_git('config', '--file', path, '--list', '-z', check=False)
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

def setting(repository: Repository, key: str, default: str | None = None) -> str | None:
    """Reads a setting like review.base. The files use git config syntax
    and are read with git config --file, so no setting of plustwo goes into
    git's own config.

    Args:
        repository: the repository.
        key: the setting's key.
        default: the value when no file has the setting.

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
        value = _legacy_setting(legacy_key, repository)
        if value:
            return value
    return default


def user_setting(key: str, default: str | None = None) -> str | None:
    """Reads a setting from the user's file alone, for programs that run
    outside any repository.

    Args:
        key: the setting's key.
        default: the value when the file has no such setting.

    Returns:
        The value from the user's file, else default.
    """
    values = config_values(_user_config_path(), key)
    return values[-1] if values and values[-1] else default


def _legacy_setting(key: str, repository: Repository) -> str:
    """Reads an older setting from git's own config, again only when one of
    git's config files changed.

    Args:
        key: the setting's old key, like change.base.
        repository: the repository.

    Returns:
        The value, '' without one.
    """
    home = Path.home()
    xdg = Path(os.environ.get('XDG_CONFIG_HOME') or home / '.config')
    stamps = tuple(_file_stamp(path) for path in (
        repository.git_directory / 'config', home / '.gitconfig', xdg / 'git' / 'config'))
    return _read_legacy_setting(key, repository, stamps)


@functools.lru_cache(maxsize=64)
def _read_legacy_setting(key: str, repository: Repository,
                         _stamps: tuple[FileStamp | None, ...]) -> str:
    """Reads an older setting from git's own config, once per stamps.

    Args:
        key: the setting's old key.
        repository: the repository.
        _stamps: the stamps of git's config files, only part of the
            cache's key.

    Returns:
        The value, '' without one.
    """
    return repository.git('config', key, check=False)


def settings_section(repository: Repository, section: str) -> dict[str, str]:
    """Reads all settings of a section, like keys.

    Args:
        repository: the repository.
        section: the section's name.

    Returns:
        Each name in the section, mapped to its value, the repository's
        over the user's.
    """
    return _section_of(_config_files(repository), section)


def user_settings_section(section: str) -> dict[str, str]:
    """Reads all settings of a section from the user's file alone, see
    user_setting().

    Args:
        section: the section's name.

    Returns:
        Each name in the section, mapped to its value.
    """
    return _section_of([_user_config_path()], section)


def _section_of(files: list[Path], section: str) -> dict[str, str]:
    """Reads all settings of a section from some files.

    Args:
        files: the files, the one that wins first.
        section: the section's name.

    Returns:
        Each name in the section, mapped to its value.
    """
    values = {}
    start = section.lower() + '.'
    for path in reversed(files):
        for key, value in config_entries(path):
            if key.startswith(start):
                values[key[len(start):]] = value
    return values


def set_setting(repository: Repository, key: str, value: str | None) -> None:
    """Stores a setting in the repository's file.

    Args:
        repository: the repository.
        key: the setting's key.
        value: the new value, None to remove the setting.
    """
    _store(repository.config_file, key, value)


def set_user_setting(key: str, value: str | None) -> None:
    """Stores a setting in the user's file.

    Args:
        key: the setting's key.
        value: the new value, None to remove the setting.
    """
    _store(_user_config_path(), key, value)


def _store(path: Path, key: str, value: str | None) -> None:
    """Stores a setting in a file.

    Args:
        path: the file.
        key: the setting's key.
        value: the new value, None to remove the setting.
    """
    if value is None:
        if path.exists():
            run_git('config', '--file', str(path), '--unset', key, check=False)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    run_git('config', '--file', str(path), key, value)


def list_settings(repository: Repository) -> list[str]:
    """Lists every setting.

    Args:
        repository: the repository.

    Returns:
        Lines like "key=value  (file)", the user's file first.
    """
    return _list_files(_config_files(repository))


def list_user_settings() -> list[str]:
    """Lists the settings of the user's file, see list_settings().

    Returns:
        Lines like "key=value  (file)".
    """
    return _list_files([_user_config_path()])


def _list_files(files: list[Path]) -> list[str]:
    """Lists the settings of some files.

    Args:
        files: the files, the one that wins first.

    Returns:
        Lines like "key=value  (file)", the last file first.
    """
    lines = []
    for path in reversed(files):
        if path.exists():
            output = run_git('config', '--file', str(path), '--list', check=False)
            lines += [f'{line}  ({path})' for line in filter(None, output.split('\n'))]
    return lines


def migrate_settings(repository: Repository) -> list[str]:
    """Moves settings of older versions out of the repository's git config
    into its own file. Ones in the user's global git config are left alone,
    they still count as a fallback.

    Args:
        repository: the repository.

    Returns:
        A line like "change.base -> review.base" per moved setting.
    """
    moved = []
    for key, legacy_key in _LEGACY_SETTINGS.items():
        value = repository.git('config', '--local', legacy_key, check=False)
        if not value:
            continue
        path = repository.config_file
        if not (path.exists()
                and run_git('config', '--file', str(path), '--get', key, check=False)):
            set_setting(repository, key, value)
        repository.git('config', '--local', '--unset', legacy_key)
        moved.append(f'{legacy_key} -> {key}')
    return moved


def base_branch(repository: Repository) -> str:
    """Finds the branch reviewed changes go to.

    Args:
        repository: the repository.

    Returns:
        The setting review.base, else main.
    """
    return setting(repository, 'review.base', _DEFAULT_BASE) or _DEFAULT_BASE
