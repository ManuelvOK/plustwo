# SPDX-License-Identifier: Zlib
"""The repository commands act on, and running git in it."""

import contextvars
import subprocess

from plustwo.git.errors import ChangeError


# The repository commands without one act on: None for the working
# directory. A frontend showing changes of several repositories sets it to
# the one of the change it acts on. Per thread, so the web server's
# requests don't mix.
_REPOSITORY: contextvars.ContextVar[str | None] = contextvars.ContextVar('repository',
                                                                         default=None)


def use_repository(repository: object) -> None:
    """Makes later calls without a repository act on this one.

    Args:
        repository: the repository's directory, a string or a path. None or
            '' for the working directory.
    """
    _REPOSITORY.set(str(repository) if repository else None)


def current_repository(repository: str | None = None) -> str | None:
    """Finds the repository a call acts on.

    Args:
        repository: the repository's directory, None if the call names none.

    Returns:
        The given repository, else the one set by use_repository(), else
        None for the working directory.
    """
    return repository or _REPOSITORY.get()


def git_command(repository: str | None = None) -> list[str]:
    """Builds the start of a git command line that runs in a repository.

    Args:
        repository: the repository's directory, None for the current one
            (see current_repository()).

    Returns:
        The command's first words, like ['git', '-C', '/path'].
    """
    repository = current_repository(repository)
    return ['git'] + (['-C', str(repository)] if repository else [])


def git(*arguments: str, input_text: str | None = None, check: bool = True,
        repository: str | None = None) -> str:
    """Runs git.

    Args:
        *arguments: git's arguments, the command first.
        input_text: what git reads from its standard input, None for nothing.
        check: whether a failing git is an error.
        repository: the repository's directory, None for the current one
            (see current_repository()).

    Returns:
        git's output without the final newline.

    Raises:
        ChangeError: git failed and check is set. Its message is git's.
    """
    command = git_command(repository) + list(arguments)
    # Files need not be UTF-8, so bytes that aren't become replacement
    # characters instead of an error.
    result = subprocess.run(command, input=input_text, encoding='utf-8', errors='replace',
                            capture_output=True)
    if check and 0 != result.returncode:
        raise ChangeError(result.stderr.strip() or f'git {arguments[0]} failed')
    return result.stdout.rstrip('\n')
