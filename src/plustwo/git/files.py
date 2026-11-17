# SPDX-License-Identifier: Zlib
"""Files of a commit: their lines and the empty tree."""

import subprocess

from plustwo.git.cache import cached_for_objects
from plustwo.git.repository import Repository


# git's empty tree, the same in every repository
EMPTY_TREE = '4b825dc642cb6eb9a060e54bf8d69288fbee4904'


@cached_for_objects(1)
def files_of(repository: Repository, commit: str) -> dict[str, tuple[int, int]]:
    """Finds the files a commit changes, with their added and removed lines.

    Args:
        repository: the repository.
        commit: the commit, any name git understands.

    Returns:
        Each path, mapped to (number of added lines, number of removed
        lines). Binary files count 0 for both.
    """
    output = repository.git('diff-tree', '--no-commit-id', '--numstat', '-r', '--root', commit)
    files = {}
    for line in filter(None, output.split('\n')):
        n_added, n_removed, path = line.split('\t', 2)
        files[path] = (int(n_added) if n_added.isdigit() else 0,
                       int(n_removed) if n_removed.isdigit() else 0)
    return files


@cached_for_objects(1)
def file_lines(repository: Repository, version: str, path: str) -> list[str] | None:
    """Reads the lines of a file in a commit or tree.

    Args:
        repository: the repository.
        version: the commit or tree, any name git understands.
        path: the file's path in it.

    Returns:
        The lines without line ends, empty if the file isn't there. None for
        a binary file. Bytes that aren't UTF-8 become replacement
        characters, so every file can be shown.
    """
    command = [*repository.git_command(), 'cat-file', 'blob',
               f'{version}:{path}']
    result = subprocess.run(command, capture_output=True)
    if 0 != result.returncode:
        # A submodule is a commit, which git diffs as one line like this
        entry = repository.git('ls-tree', version, '--', path, check=False).split()
        if entry and '160000' == entry[0]:
            return [f'Subproject commit {entry[2]}']
        return []
    if b'\0' in result.stdout[:8000]:
        return None
    text = result.stdout.decode('utf-8', errors='replace')
    if not text:
        return []
    return text.split('\n')[:-1] if text.endswith('\n') else text.split('\n')
