# SPDX-License-Identifier: Zlib
"""Registered repositories and lists of them."""

import re
from pathlib import Path

from plustwo.config.state import set_state, state_path, state_regexp
from plustwo.git.errors import ChangeError
from plustwo.git.refs import toplevel
from plustwo.git.repository import git


def registered_repositories():
    """The registered repositories: name -> path, in the order of the file."""
    return {key[len('repo.'):-len('.path')]: value
            for key, value in state_regexp('repos', r'^repo\..*\.path$')}


def repository_lists():
    """The lists of repositories: name -> [repository names]."""
    lists = {}
    for key, value in state_regexp('repos', r'^list\..*\.repo$'):
        lists.setdefault(key[len('list.'):-len('.repo')], []).append(value)
    return lists


def register_repository(path='.', name=None, list_name=None, unique=False):
    """Registers a repository for the dashboard, under its directory's name
    unless name is given, and adds it to a list. A name whose repository is
    gone is taken over. With unique, a taken name gets a number instead of
    an error, e.g. repo-2. Returns the name.
    """
    root = toplevel(path)
    if root is None:
        raise ChangeError(f'{path} is not a git repository')
    known = registered_repositories()
    existing = next((known_name for known_name, known_path in known.items()
                     if known_path == root), None)
    name = existing or name or Path(root).name
    if existing is None:
        if name in known and not Path(known[name]).exists():
            del known[name]
        if unique:
            stem, number = name, 2
            while name in known:
                name, number = f'{stem}-{number}', number + 1
        if name in known:
            raise ChangeError(f'a repository named {name} is registered already, '
                              'choose another with --name')
        set_state('repos', f'repo.{name}.path', root)
    if list_name and name not in repository_lists().get(list_name, []):
        set_state('repos', f'list.{list_name}.repo', name, add=True)
    return name


def unregister_repository(name):
    """Forgets a registered repository, also in every list."""
    if name not in registered_repositories():
        raise ChangeError(f'no registered repository {name}')
    path = state_path('repos')
    git('config', '--file', str(path), '--remove-section', f'repo.{name}', check=False)
    for list_name, names in repository_lists().items():
        if name in names:
            git('config', '--file', str(path), '--unset-all', f'list.{list_name}.repo',
                f'^{re.escape(name)}$', check=False)


def review_repositories(list_name=None):
    """The repositories reviews are shown for: the registered ones (of a
    list, if given) that still exist. Without any registered, the one of
    the working directory, so nothing changes until the first register.
    The working directory's repository comes along when it isn't
    registered, as long as no list is asked for.
    """
    known = registered_repositories()
    if list_name:
        names = repository_lists().get(list_name)
        if names is None:
            raise ChangeError(f'no list {list_name}')
        return [known[name] for name in names if name in known and Path(known[name]).exists()]
    paths = [path for path in known.values() if Path(path).exists()]
    here = toplevel()
    if here and here not in paths:
        paths.insert(0, here)
    return paths


def repository_name(repository):
    """Name of a repository for short messages: its directory's name."""
    return Path(git('rev-parse', '--show-toplevel', repository=repository)).name
