# SPDX-License-Identifier: Zlib
"""The two versions of a change that are compared."""

from plustwo.git.cache import cached_for_objects
from plustwo.git.commit_message import COMMIT_MSG_NAME, commit_message, commit_message_tree
from plustwo.git.errors import ChangeError
from plustwo.git.files import EMPTY_TREE, file_lines, files_of
from plustwo.git.repository import Repository
from plustwo.model.change import Change
from plustwo.model.place import COMMIT_MSG
from plustwo.model.version import BASE, CURRENT, LATEST
from plustwo.review.last_send import last_send_patch_set
from plustwo.review.renames import file_sources, own_sources


# A version of a change and its commit: (label, commit). The label is a
# patch set's number, BASE or CURRENT. The commit can be a parent like
# abc^.
Version = tuple[str, str]


def compare_label(change: Change, first: str | None = None, second: str | None = None,
                  does_follow_latest: bool = True, is_short: bool = False) -> str:
    """Writes the compared versions for display, like "comparing 1 [your
    last send] -> 2 [latest]". The latest patch set and the one of the
    reviewer's last send are tagged when they are a side.

    Args:
        change: the change.
        first: the left version, see versions_to_compare().
        second: the right version, see versions_to_compare().
        does_follow_latest: whether the right side moves to a new upload.
            Without, a newer patch set than the right side is mentioned
            ("PS3 is newer").
        is_short: whether the tags are shortened for narrow lines.

    Returns:
        The label.
    """
    (left, _), (right, _) = versions_to_compare(change, first, second)
    latest = str(change.latest_patch_set()) if change.patch_sets else None
    sent_patch_set = last_send_patch_set(change)
    sent = str(sent_patch_set) if sent_patch_set is not None else None

    def tagged(version: str) -> str:
        """Tags a version.

        Args:
            version: the version's label.

        Returns:
            The label with its tags in brackets, if it has any.
        """
        tags = (['latest'] if version == latest else []) + (
            [('last send' if is_short else 'your last send')] if version == sent else [])
        return f'{version} [{", ".join(tags)}]' if tags else version

    label = f'comparing {tagged(left)} -> {tagged(right)}'
    if not does_follow_latest and latest and right.isdigit() and int(right) < int(latest):
        label += f'   PS{latest} is newer'
    return label


def can_compare(change: Change, first: str | None = None, second: str | None = None) -> bool:
    """Tells whether a change has two versions, see versions_to_compare().

    Args:
        change: the change.
        first: the left version.
        second: the right version.

    Returns:
        True if both exist.
    """
    try:
        versions_to_compare(change, first, second)
    except ChangeError:
        return False
    return True


def versions_to_compare(change: Change, first: str | None = None,
                        second: str | None = None) -> tuple[Version, Version]:
    """Finds the two versions a diff compares.

    Args:
        change: the change.
        first: the left version: a patch set's number, BASE (the parent of
            the second version, which shows the whole change as of that
            version), LATEST or CURRENT. None for the default.
        second: the right version: a patch set's number, LATEST or CURRENT.
            None for the current commit, or the default with first None.

    Returns:
        (left, right), see Version. Without versions given: the latest
        patch set against the current commit if it changed since, else the
        last two patch sets, else the only patch set against its parent.

    Raises:
        ChangeError: a patch set doesn't exist, or second is BASE.
    """
    latest = change.latest_patch_set()
    first, second = (
        (str(latest) if latest else CURRENT) if LATEST == version else version
        for version in (first, second))

    def commit_of(version: str) -> str:
        """Finds the commit of a version.

        Args:
            version: a patch set's number or CURRENT.

        Returns:
            The commit.

        Raises:
            ChangeError: the patch set doesn't exist.
        """
        if CURRENT == version:
            return change.commit
        if int(version) not in change.patch_sets:
            raise ChangeError(f'{change.short_id()} has no patch set {version}')
        return change.patch_sets[int(version)]

    # The defaults
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
    assert second is not None
    if BASE == second:
        raise ChangeError('base can only be the first of the two versions')
    if BASE == first:
        # The second version's own parent, so a rebase between patch sets
        # doesn't bring other commits into the diff
        new = commit_of(second)
        return (BASE, f'{new}^'), (second, new)
    return (first, commit_of(first)), (second, commit_of(second))


def diff_endpoints(change: Change, first: str | None = None, second: str | None = None,
                   path: str | None = None) -> tuple[str, str, list[str]]:
    """Finds what git diff compares for two versions of a change.

    Args:
        change: the change.
        first: the left version, see versions_to_compare().
        second: the right version, see versions_to_compare().
        path: the one file to diff, None for the files the change touches
            in either version.

    Returns:
        (old, new, paths). For COMMIT_MSG, old and new are trees holding
        the two commit messages, and BASE has none.
    """
    (first, old), (_second, new) = versions_to_compare(change, first, second)
    if COMMIT_MSG == path:
        return (commit_message_tree(change.repository, None if BASE == first else old),
                commit_message_tree(change.repository, new), [COMMIT_MSG_NAME])
    if path:
        return old, new, [path]
    files = set(files_of(change.repository, new))
    if BASE != first:
        files |= set(files_of(change.repository, old))
    return old, new, sorted(files)


@cached_for_objects(1, 2)
def _numstat(repository: Repository, old: str, new: str) -> dict[str, tuple[int, int]]:
    """Finds the files that differ between two commits.

    Args:
        repository: the repository.
        old: the old commit, a parent like abc^ too.
        new: the new commit.

    Returns:
        Each path, mapped to (number of added lines, number of removed
        lines). Binary files count 0 for both. COMMIT_MSG is in it too, with
        (0, 0), when the messages differ.
    """
    # The parent of a root commit: nothing
    exists = repository.git('rev-parse', '--verify', '--quiet', old, check=False)
    # A renamed file counts its edits under its new path, like git diff -M.
    fields = repository.git('diff', '--numstat', '-M', '-z', old if exists else EMPTY_TREE,
                            new).split('\0')
    files = {}
    index = 0
    while index < len(fields) and fields[index]:
        n_added, n_removed, path = fields[index].split('\t', 2)
        index += 1
        if not path:
            # A rename: the old and the new path follow.
            path = fields[index + 1]
            index += 2
        files[path] = (int(n_added) if n_added.isdigit() else 0,
                       int(n_removed) if n_removed.isdigit() else 0)
    # Renames git doesn't find, see file_sources(): counted blob to blob
    for path, source in file_sources(repository, old, new).items() if exists else ():
        if source in files and path in files:
            del files[source]
            blobs = [repository.git('rev-parse', f'{version}:{name}')
                     for version, name in ((old, source), (new, path))]
            counts = repository.git('diff', '--numstat', *blobs).split('\t')
            files[path] = (int(counts[0]) if counts[0].isdigit() else 0,
                           int(counts[1]) if len(counts) > 1 and counts[1].isdigit() else 0)
    if not exists or commit_message(repository, old) != commit_message(repository, new):
        files[COMMIT_MSG] = (0, 0)
    return files


def compared_files(change: Change, first: str | None = None,
                   second: str | None = None) -> dict[str, tuple[int, int]]:
    """Finds the files that differ between the two compared versions of a
    change. The file box shows their counts, and the change's other files
    as unchanged.

    Args:
        change: the change.
        first: the left version, see versions_to_compare().
        second: the right version, see versions_to_compare().

    Returns:
        Each path, mapped to (number of added lines, number of removed
        lines) between the versions. COMMIT_MSG is in it, with (0, 0), when
        the message differs (always against base).
    """
    (first, old), (_second, new) = versions_to_compare(change, first, second)
    files = dict(_numstat(change.repository, old, new))
    if BASE == first:
        files[COMMIT_MSG] = (0, 0)
    return files


def diff_command(change: Change, first: str | None = None, second: str | None = None,
                 uses_difftool: bool = False, path: str | None = None) -> list[str]:
    """Builds the git command showing the diff between two versions of a
    change.

    Args:
        change: the change.
        first: the left version, see versions_to_compare().
        second: the right version, see versions_to_compare().
        uses_difftool: whether the command is git difftool, else git diff.
        path: the one file to diff, None for all, see diff_endpoints().

    Returns:
        The command line.
    """
    old, new, files = diff_endpoints(change, first, second, path)
    command = ['difftool', '-y'] if uses_difftool else ['diff']
    return ['git', *command, old, new, '--', *files]


def old_path_of(change: Change, first: str | None, second: str | None, path: str) -> str | None:
    """Finds the path of a file in the older of two versions, when the
    change renamed it.

    Args:
        change: the change.
        first: the left version, see versions_to_compare().
        second: the right version, see versions_to_compare().
        path: the file's path in the newer version.

    Returns:
        The old path, if the change renamed the file and the older version
        doesn't have it under path. Else None.
    """
    old, new, _ = diff_endpoints(change, first, second, path)
    if COMMIT_MSG == path or file_lines(change.repository, old, path) != []:
        return None
    # Renamed between the versions, as when two patch sets add the file
    # under different names, else by the change itself
    source = (file_sources(change.repository, old, new).get(path)
              or own_sources(change.repository, new).get(path))
    return source if source and file_lines(change.repository, old, source) else None


def compared_renames(change: Change, first: str | None = None,
                     second: str | None = None) -> dict[str, str]:
    """Finds the files that have another path in the older of two versions,
    see old_path_of().

    Args:
        change: the change.
        first: the left version, see versions_to_compare().
        second: the right version, see versions_to_compare().

    Returns:
        Each path in the newer version, mapped to its path in the older one.
    """
    _old, _new, paths = diff_endpoints(change, first, second)
    return {path: source for path in paths
            if (source := old_path_of(change, first, second, path))}


def range_diff_command(change: Change, first: str | None = None,
                       second: str | None = None) -> list[str]:
    """Builds the git command showing the range-diff between two versions.

    Args:
        change: the change.
        first: the left version, a patch set, see versions_to_compare().
        second: the right version, a patch set, see versions_to_compare().

    Returns:
        The command line.

    Raises:
        ChangeError: a version is BASE.
    """
    if BASE in (first, second):
        raise ChangeError('a range-diff needs two patch sets, not base')
    (_first, old), (_second, new) = versions_to_compare(change, first, second)
    # Both sides hold one commit each, which are versions of the same change.
    # The creation factor makes range-diff pair them even after big edits.
    return ['git', 'range-diff', '--creation-factor=100', f'{old}^!', f'{new}^!']
