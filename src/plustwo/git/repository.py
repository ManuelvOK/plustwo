# SPDX-License-Identifier: Zlib
"""The repository commands act on, and running git in it."""

import contextvars
import subprocess

from plustwo.git.errors import ChangeError


# The repository commands without one act on: None for the working
# directory. A frontend showing changes of several repositories sets it to
# the one of the change it acts on. Per thread, so the web server's
# requests don't mix.
_REPOSITORY = contextvars.ContextVar('repository', default=None)


def use_repository(repository):
    """Makes later calls without a repository act on this one."""
    _REPOSITORY.set(str(repository) if repository else None)


def current_repository(repository=None):
    """The repository a call acts on: the given one, else the one set by
    use_repository(), else None for the working directory.
    """
    return repository or _REPOSITORY.get()


def git_command(repository=None):
    """The start of a git command line running in a repository."""
    repository = current_repository(repository)
    return ['git'] + (['-C', str(repository)] if repository else [])


def git(*arguments, input_text=None, check=True, repository=None):
    """Runs git and returns its output without the final newline."""
    command = git_command(repository) + list(arguments)
    # Files need not be UTF-8, so bytes that aren't become replacement
    # characters instead of an error.
    result = subprocess.run(command, input=input_text, encoding='utf-8', errors='replace',
                            capture_output=True)
    if check and 0 != result.returncode:
        raise ChangeError(result.stderr.strip() or f'git {arguments[0]} failed')
    return result.stdout.rstrip('\n')
