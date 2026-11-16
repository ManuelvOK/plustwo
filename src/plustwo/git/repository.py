# SPDX-License-Identifier: Zlib
"""A git repository, handed to everything that acts on one, and running git
in it."""

import functools
import subprocess
from pathlib import Path

from plustwo.git.errors import ChangeError


class Repository:
    """A repository: its working tree and what belongs to it alone.

    There is no current repository. Whoever acts on one gets the object
    handed in. Repositories are equal when their paths are.

    Attributes:
        path: the absolute path of the repository's working tree.
    """

    def __init__(self, path: str | Path) -> None:
        """Wraps the working tree of a repository, without asking git.

        Args:
            path: the working tree's directory, as git's --show-toplevel
                prints it. Use of_directory() for any directory inside.
        """
        self.path = Path(path).absolute()

    @classmethod
    def of_directory(cls, directory: str | Path) -> 'Repository':
        """Finds the repository a directory is in.

        Args:
            directory: a directory in the repository.

        Returns:
            The repository.

        Raises:
            ChangeError: the directory is in no repository. The message is
                git's.
        """
        result = subprocess.run(['git', '-C', str(directory), 'rev-parse', '--show-toplevel'],
                                capture_output=True, text=True)
        if 0 != result.returncode or not result.stdout.strip():
            raise ChangeError(result.stderr.strip() or 'not a git repository')
        return cls(result.stdout.strip())

    @classmethod
    def of_working_directory(cls) -> 'Repository':
        """Finds the repository the working directory is in.

        Returns:
            The repository.

        Raises:
            ChangeError: the working directory is in no repository.
        """
        return cls.of_directory(Path.cwd())

    def __eq__(self, other: object) -> bool:
        """Compares by path.

        Args:
            other: what to compare with.

        Returns:
            True for a repository with the same path.
        """
        return isinstance(other, Repository) and self.path == other.path

    def __hash__(self) -> int:
        """Hashes the path.

        Returns:
            The hash.
        """
        return hash(self.path)

    def __repr__(self) -> str:
        """Shows the path.

        Returns:
            The text.
        """
        return f'Repository({str(self.path)!r})'

    def git_command(self) -> list[str]:
        """Builds the start of a git command line that runs in the repository.

        Returns:
            The command's first words, like ['git', '-C', '/path'].
        """
        return ['git', '-C', str(self.path)]

    def git(self, *arguments: str, input_text: str | None = None, check: bool = True) -> str:
        """Runs git in the repository.

        Args:
            *arguments: git's arguments, the command first.
            input_text: what git reads from its standard input, None for
                nothing.
            check: whether a failing git is an error.

        Returns:
            git's output without the final newline.

        Raises:
            ChangeError: git failed and check is set. Its message is git's.
        """
        command = self.git_command() + list(arguments)
        # Files need not be UTF-8, so bytes that aren't become replacement
        # characters instead of an error.
        result = subprocess.run(command, input=input_text, encoding='utf-8', errors='replace',
                                capture_output=True)
        if check and 0 != result.returncode:
            raise ChangeError(result.stderr.strip() or f'git {arguments[0]} failed')
        return result.stdout.rstrip('\n')

    def git_path(self, name: str) -> Path:
        """Builds the absolute path of a file inside the .git directory.

        Args:
            name: the file's path relative to the .git directory.

        Returns:
            The absolute path, which git resolves for worktrees too.
        """
        path = Path(self.git('rev-parse', '--git-path', name))
        return path if path.is_absolute() else (self.path / path).absolute()

    @functools.cached_property
    def git_directory(self) -> Path:
        """The repository's .git directory, which git is asked for once.

        Returns:
            The absolute path.
        """
        return Path(self.git('rev-parse', '--absolute-git-dir'))

    @functools.cached_property
    def config_file(self) -> Path:
        """The repository's settings file, inside .git, asked for once.

        Returns:
            The absolute path of the file, in git config syntax, which need
            not exist.
        """
        return self.git_path('plustwo/config')
