# SPDX-License-Identifier: Zlib
"""The edits of a file's diff, as git finds them."""

import re

from plustwo.git.cache import cached_for_objects
from plustwo.git.repository import git


# Gerrit's whitespace modes and the git diff options for them
WHITESPACE_OPTIONS = {'none': (), 'trailing': ('--ignore-space-at-eol',),
                      'changed': ('--ignore-space-change',), 'all': ('--ignore-all-space',)}


WHITESPACE_MODES = tuple(WHITESPACE_OPTIONS)


@cached_for_objects(0, 1)
def edits_of(old, new, path, repository=None, old_path=None, whitespace='none'):
    """The edits of a file's diff: [(old begin, old end, new begin, new
    end)], line ranges counted from 1, the end not included. An insertion
    has an empty old range, a deletion an empty new range. old_path is the
    file's path in old if it was renamed. whitespace is one of Gerrit's
    modes, see WHITESPACE_OPTIONS.
    """
    # Histogram is the algorithm of JGit, so the edits are the ones Gerrit
    # shows. The options make the user's diff settings not count.
    options = ('--no-color', '--no-ext-diff', '--no-textconv', '--no-renames', '--histogram',
               '-U0', *WHITESPACE_OPTIONS.get(whitespace, ()))
    output = '' if old_path else git('diff', *options, old, new, '--', path,
                                     repository=repository)
    if old_path or output.count('diff --git ') > 1:
        # A file that became a symbolic link or the other way round is a
        # deletion and an addition to git. Its lines are diffed as blobs.
        # A renamed file is diffed the same way.
        blobs = [git('rev-parse', '--verify', '--quiet', f'{version}:{version_path}',
                     check=False, repository=repository)
                 or git('hash-object', '-w', '--stdin', input_text='', repository=repository)
                 for version, version_path in ((old, old_path or path), (new, path))]
        output = git('diff', *options, *blobs, repository=repository)
    edits = []
    for match in re.finditer(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', output, re.MULTILINE):
        old_start, old_count, new_start, new_count = (
            int(value) if value is not None else 1 for value in match.groups())
        # An empty range is given by the line before it.
        old_begin = old_start if old_count else old_start + 1
        new_begin = new_start if new_count else new_start + 1
        edits.append((old_begin, old_begin + old_count, new_begin, new_begin + new_count))
    return edits
