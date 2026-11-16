# SPDX-License-Identifier: Zlib
"""Registered repositories and lists of them."""

import re
from pathlib import Path

from plustwo.config.state import set_state, state_entries_matching, state_path
from plustwo.git.errors import ChangeError
from plustwo.git.repository import Repository, run_git


def registered_repositories() -> dict[str, Repository]:
    """Reads the registered repositories.

    Returns:
        Each repository's name, mapped to its repository, in the order of
        the state file.
    """
    return {key[len('repo.'):-len('.path')]: Repository(value)
            for key, value in state_entries_matching('repos', r'^repo\..*\.path$')}


def repository_lists() -> dict[str, list[str]]:
    """Reads the lists of repositories.

    Returns:
        Each list's name, mapped to the names of its repositories.
    """
    lists: dict[str, list[str]] = {}
    for key, value in state_entries_matching('repos', r'^list\..*\.repo$'):
        lists.setdefault(key[len('list.'):-len('.repo')], []).append(value)
    return lists


def register_repository(repository: Repository, name: str | None = None,
                        list_name: str | None = None, do_number_on_clash: bool = False) -> str:
    """Registers a repository for the dashboard and adds it to a list. A
    name whose repository is gone is taken over.

    Args:
        repository: the repository.
        name: the name to register it under, None for its directory's name.
        list_name: the list to add it to, None for none.
        do_number_on_clash: whether a name that is taken gets a number,
            like repo-2, instead of an error.

    Returns:
        The repository's registered name, an earlier one if it was
        registered already.

    Raises:
        ChangeError: the name is taken.
    """
    known = registered_repositories()
    existing = next((known_name for known_name, known_repository in known.items()
                     if known_repository == repository), None)
    name = existing or name or repository.path.name
    if existing is None:
        if name in known and not known[name].path.exists():
            del known[name]
        if do_number_on_clash:
            stem, number = name, 2
            while name in known:
                name, number = f'{stem}-{number}', number + 1
        if name in known:
            raise ChangeError(f'a repository named {name} is registered already, '
                              'choose another with --name')
        set_state('repos', f'repo.{name}.path', str(repository.path))
    if list_name and name not in repository_lists().get(list_name, []):
        set_state('repos', f'list.{list_name}.repo', name, do_add=True)
    return name


def unregister_repository(name: str) -> None:
    """Forgets a registered repository, also in every list.

    Args:
        name: the repository's registered name.

    Raises:
        ChangeError: no repository is registered under the name.
    """
    if name not in registered_repositories():
        raise ChangeError(f'no registered repository {name}')
    path = state_path('repos')
    run_git('config', '--file', str(path), '--remove-section', f'repo.{name}', check=False)
    for list_name, names in repository_lists().items():
        if name in names:
            run_git('config', '--file', str(path), '--unset-all', f'list.{list_name}.repo',
                f'^{re.escape(name)}$', check=False)


def review_repositories(list_name: str | None = None) -> list[Repository]:
    """Finds the repositories reviews are shown for.

    Args:
        list_name: the list to show, None for all registered repositories.

    Returns:
        The registered repositories (of the list, if given)
        that still exist. Without a list, the working directory's
        repository comes first when it isn't registered, so nothing changes
        until the first register.

    Raises:
        ChangeError: there is no list of that name.
    """
    known = registered_repositories()
    if list_name:
        names = repository_lists().get(list_name)
        if names is None:
            raise ChangeError(f'no list {list_name}')
        return [known[name] for name in names if name in known and known[name].path.exists()]
    repositories = [repository for repository in known.values() if repository.path.exists()]
    try:
        here = Repository.of_working_directory()
    except ChangeError:
        return repositories
    if here not in repositories:
        repositories.insert(0, here)
    return repositories


def repository_name(repository: Repository) -> str:
    """Names a repository for short messages.

    Args:
        repository: the repository.

    Returns:
        The name of the repository's working tree directory.
    """
    return repository.path.name
