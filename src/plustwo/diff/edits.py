# SPDX-License-Identifier: Zlib
"""The edits of a file's diff, as git finds them."""

import re

from plustwo.git.cache import cached_for_objects
from plustwo.git.repository import Repository


# An edit of a diff: (old begin, old end, new begin, new end), line ranges
# counted from 1, the ends not included. An insertion has an empty old
# range, a deletion an empty new range.
Edit = tuple[int, int, int, int]

# Gerrit's whitespace modes, mapped to the git diff options for them
_WHITESPACE_OPTIONS = {'none': (), 'trailing': ('--ignore-space-at-eol',),
                      'changed': ('--ignore-space-change',), 'all': ('--ignore-all-space',)}
WHITESPACE_MODES = tuple(_WHITESPACE_OPTIONS)


@cached_for_objects(1, 2)
def edits_of(repository: Repository, old: str, new: str, path: str, old_path: str | None = None,
             whitespace: str = 'none') -> list[Edit]:
    """Finds the edits of a file's diff between two versions.

    Args:
        repository: the repository.
        old: the old version, a commit or tree.
        new: the new version, a commit or tree.
        path: the file's path in the new version.
        old_path: the file's path in the old version if it was renamed,
            None if it is path.
        whitespace: one of Gerrit's whitespace modes, WHITESPACE_MODES.

    Returns:
        The edits, top to bottom, see Edit.
    """
    # Histogram is the algorithm of JGit, so the edits are the ones Gerrit
    # shows. The options make the user's diff settings not count.
    options = ('--no-color', '--no-ext-diff', '--no-textconv', '--no-renames', '--histogram',
               '-U0', *_WHITESPACE_OPTIONS.get(whitespace, ()))
    output = '' if old_path else repository.git('diff', *options, old, new, '--', path)
    if old_path or output.count('diff --git ') > 1:
        # A file that became a symbolic link or the other way round is a
        # deletion and an addition to git. Its lines are diffed as blobs.
        # A renamed file is diffed the same way.
        blobs = [repository.git('rev-parse', '--verify', '--quiet', f'{version}:{version_path}',
                                check=False)
                 or repository.git('hash-object', '-w', '--stdin', input_text='')
                 for version, version_path in ((old, old_path or path), (new, path))]
        output = repository.git('diff', *options, *blobs)
    edits = []
    for match in re.finditer(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', output, re.MULTILINE):
        old_start, n_old_lines, new_start, n_new_lines = (
            int(value) if value is not None else 1 for value in match.groups())
        # An empty range is given by the line before it.
        old_begin = old_start if n_old_lines else old_start + 1
        new_begin = new_start if n_new_lines else new_start + 1
        edits.append((old_begin, old_begin + n_old_lines, new_begin, new_begin + n_new_lines))
    return edits
