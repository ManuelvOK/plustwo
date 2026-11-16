# SPDX-License-Identifier: Zlib
"""The two versions of a change that are compared."""

from plustwo.git.cache import cached_for_objects
from plustwo.git.commit_message import COMMIT_MSG_NAME, commit_message, commit_message_tree
from plustwo.git.errors import ChangeError
from plustwo.git.files import EMPTY_TREE, file_lines, files_of, renames_of
from plustwo.git.repository import git
from plustwo.model.place import COMMIT_MSG
from plustwo.model.version import BASE, CURRENT, LATEST
from plustwo.review.last_send import last_send_patch_set


def compare_label(change, first=None, second=None, follow=True, short=False):
    """The versions compared for display, like "comparing 1 [your last
    send] -> 2 [latest]": the latest patch set and the one of the
    reviewer's last send are tagged when they are a side. Without follow, a
    newer patch set than the right side is mentioned instead ("PS3 is
    newer"). short shortens the tags for narrow lines.
    """
    (left, _), (right, _) = versions_to_compare(change, first, second)
    latest = str(change.latest_patch_set()) if change.patch_sets else None
    sent = last_send_patch_set(change)
    sent = str(sent) if sent is not None else None

    def tagged(version):
        tags = (['latest'] if version == latest else []) + (
            [('last send' if short else 'your last send')] if version == sent else [])
        return f'{version} [{", ".join(tags)}]' if tags else version

    label = f'comparing {tagged(left)} -> {tagged(right)}'
    if not follow and latest and right.isdigit() and int(right) < int(latest):
        label += f'   PS{latest} is newer'
    return label


def can_compare(change, first=None, second=None):
    """Whether the change has the versions, see versions_to_compare()."""
    try:
        versions_to_compare(change, first, second)
    except ChangeError:
        return False
    return True


def versions_to_compare(change, first=None, second=None):
    """The two versions a diff compares, as (label, commit) pairs.

    Without patch sets given: the latest patch set against the current
    commit if it changed since, else the last two patch sets, else the only
    patch set against its parent ("base").
    first can be "base" too: the parent of the second version, which shows
    the whole change as of that version. Either can be "latest".
    """
    latest = change.latest_patch_set()
    first, second = (
        (str(latest) if latest else CURRENT) if LATEST == version else version
        for version in (first, second))

    def commit_of(version):
        if CURRENT == version:
            return change.commit
        if int(version) not in change.patch_sets:
            raise ChangeError(f'{change.short_id()} has no patch set {version}')
        return change.patch_sets[int(version)]

    if first is None:
        if 0 == latest:
            return (BASE, f'{change.commit}^'), (CURRENT, change.commit)
        if change.is_uploaded():
            if latest < 2:
                return (BASE, f'{change.commit}^'), (str(latest), change.commit)
            first, second = str(latest - 1), str(latest)
        else:
            first, second = str(latest), CURRENT
    elif second is None:
        second = CURRENT
    if BASE == second:
        raise ChangeError('base can only be the first of the two versions')
    if BASE == first:
        # The second version's own parent, so a rebase between patch sets
        # doesn't bring other commits into the diff
        new = commit_of(second)
        return (BASE, f'{new}^'), (second, new)
    return (first, commit_of(first)), (second, commit_of(second))


def diff_endpoints(change, first=None, second=None, path=None, repository=None):
    """What git diff compares for two versions of a change: (old, new,
    paths).

    path limits the diff to one file. Without it, the diff covers the files
    the change touches in either version. For /COMMIT_MSG, old and new are
    trees holding the two commit messages. Base has no commit message.
    """
    (first, old), (_second, new) = versions_to_compare(change, first, second)
    if COMMIT_MSG == path:
        return (commit_message_tree(None if BASE == first else old, repository),
                commit_message_tree(new, repository), [COMMIT_MSG_NAME])
    if path:
        return old, new, [path]
    files = set(files_of(new, repository))
    if BASE != first:
        files |= set(files_of(old, repository))
    return old, new, sorted(files)


@cached_for_objects(0, 1)
def numstat(old, new, repository=None):
    """Files that differ between two commits, with added and removed line
    counts: path -> (added, removed). Binary files count as 0. /COMMIT_MSG
    is in it too, with (0, 0), when the messages differ.
    """
    # The parent of a root commit: nothing
    exists = git('rev-parse', '--verify', '--quiet', old, check=False, repository=repository)
    output = git('diff', '--numstat', '--no-renames', old if exists else EMPTY_TREE, new,
                 repository=repository)
    files = {}
    for line in filter(None, output.split('\n')):
        added, removed, path = line.split('\t', 2)
        files[path] = (int(added) if added.isdigit() else 0,
                       int(removed) if removed.isdigit() else 0)
    if not exists or commit_message(old, repository) != commit_message(new, repository):
        files[COMMIT_MSG] = (0, 0)
    return files


def compared_files(change, first=None, second=None, repository=None):
    """The files that differ between the two compared versions of a change,
    with the added and removed lines between them: path -> (added,
    removed). The file box shows these counts, and the change's other
    files as unchanged. /COMMIT_MSG is in it, with (0, 0), when the
    message differs (always against base).
    """
    (first, old), (_second, new) = versions_to_compare(change, first, second)
    files = dict(numstat(old, new, repository))
    if BASE == first:
        files[COMMIT_MSG] = (0, 0)
    return files


def diff_command(change, first=None, second=None, tool=False, path=None):
    """The git command showing the diff between two versions of a change."""
    old, new, files = diff_endpoints(change, first, second, path)
    command = ['difftool', '-y'] if tool else ['diff']
    return ['git', *command, old, new, '--', *files]


def old_path_of(change, first, second, path, repository=None):
    """The path of a file in the older of two versions, if the change
    renamed it there and the older version doesn't have it. Else None.
    """
    old, new, _ = diff_endpoints(change, first, second, path, repository)
    if COMMIT_MSG == path or file_lines(old, path, repository) != []:
        return None
    # Renamed by the change: the new version's own rename
    source = renames_of(new, repository).get(path)
    return source if source and file_lines(old, source, repository) else None


def range_diff_command(change, first=None, second=None):
    """The git command showing the range-diff between two versions."""
    if BASE in (first, second):
        raise ChangeError('a range-diff needs two patch sets, not base')
    (_first, old), (_second, new) = versions_to_compare(change, first, second)
    # Both sides hold one commit each, which are versions of the same change.
    # The creation factor makes range-diff pair them even after big edits.
    return ['git', 'range-diff', '--creation-factor=100', f'{old}^!', f'{new}^!']
