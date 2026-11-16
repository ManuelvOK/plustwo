# SPDX-License-Identifier: Zlib
"""Threads on newer patch sets, like Gerrit's ported comments."""

from collections.abc import Iterable
from dataclasses import dataclass

from plustwo.diff.edits import Edit, edits_of
from plustwo.diff.pairing import paired_lines
from plustwo.git.cache import cached_for_objects
from plustwo.git.commit_message import COMMIT_MSG_NAME, commit_message_text, commit_message_tree
from plustwo.git.files import file_lines, renames_of
from plustwo.git.repository import Repository
from plustwo.model.change import Change
from plustwo.model.comment import Comment
from plustwo.model.place import (
    COMMIT_MSG, NEW_SIDE, OLD_SIDE, PATCHSET_LEVEL, join_place, split_place)
from plustwo.model.version import CURRENT
from plustwo.review.compare import old_path_of, versions_to_compare


# How a thread's line fared on the way to another patch set
SAME = 'same'
MOVED = 'moved'
EDITED = 'edited'
REMOVED = 'removed'
FILE_GONE = 'file gone'

# A place in a version: (commit or parent like abc^, path of a file in it)
ThreadVersion = tuple[str, str]

# A line moved into another version: (path, line or None, state)
MovedPlace = tuple[str, int | None, str]

# Lines that don't tell where code went
_BARE_LINES = ('', '{', '}', '};')

# A line of a gone file is looked for in the new files around it: this many
# lines on each side, and at least _FOUND_MIN_LINES of them unchanged.
_FOUND_RANGE = 5
_FOUND_MIN_LINES = 3


@dataclass
class PortedPlace:
    """Where a thread of an older patch set is in a newer one.

    Attributes:
        place: the thread's place there.
        line: the thread's line there, None for a thread on a whole file,
            or when the line or file is gone.
        state: SAME (same line number and text), MOVED (same text, other
            number or file), EDITED (the line was changed, line is the best
            guess), REMOVED or FILE_GONE.
        patch_set: the newer patch set's number, or CURRENT.
    """
    place: str
    line: int | None
    state: str
    patch_set: int | str

    def label(self, place: str) -> str:
        """Writes the note for inbox lines, without the patch set.

        Args:
            place: the place the thread was written on.

        Returns:
            '' when the thread is where it was, else like 'now :50' or
            'line removed'.
        """
        if self.state in (REMOVED, FILE_GONE):
            return 'line removed' if REMOVED == self.state else 'file gone'
        path, _, line = place.rpartition(':')
        new_path, _, new_line = self.place.rpartition(':')
        if not line.isdigit():
            return '' if self.place == place else f'now {self.place}'
        moved = f'{new_path if new_path != path else ""}:{new_line}'
        if EDITED == self.state:
            return f'edited, now {moved}'
        return '' if self.place == place else f'now {moved}'


# ── following files ───────────────────────────────────────────────────────────

@cached_for_objects(1, 2)
def path_in(repository: Repository, old: str, new: str, path: str) -> str | None:
    """Finds the path a file of one commit has in another.

    Args:
        repository: the repository.
        old: the commit the file is in.
        new: the other commit.
        path: the file's path in old.

    Returns:
        The same path if new has it, else where git's rename detection
        finds it or _rewritten_path(), None if the file is gone.
    """
    if repository.git('rev-parse', '--verify', '--quiet', f'{new}:{path}', check=False):
        return path
    fields = repository.git('diff-tree', '-M', '-r', '--name-status', '-z', old, new,
                            '--').split('\0')
    index = 0
    while index < len(fields) and fields[index]:
        if fields[index].startswith(('R', 'C')):
            if fields[index + 1] == path:
                return fields[index + 2]
            index += 3
        else:
            index += 2
    return _rewritten_path(repository, old, new, path)


@cached_for_objects(1, 2)
def _added_files(repository: Repository, old: str, new: str) -> list[str]:
    """Finds the files one commit has and another hasn't.

    Args:
        repository: the repository.
        old: the older commit.
        new: the newer commit.

    Returns:
        The paths of the files new has and old hasn't, other than the ones
        git's rename detection finds as renamed or copied.
    """
    fields = repository.git('diff-tree', '-M', '-r', '--name-status', '-z', old, new,
                            '--').split('\0')
    added = []
    index = 0
    while index < len(fields) and fields[index]:
        if fields[index].startswith(('R', 'C')):
            index += 3
            continue
        if 'A' == fields[index]:
            added.append(fields[index + 1])
        index += 2
    return added


def _n_kept_lines(repository: Repository, old: str, new: str, path: str, name: str,
                  numbers: Iterable[int]) -> int:
    """Counts how many of some lines of a file the diff to another file
    keeps. The diff ignores whitespace: indentation often changes when code
    moves into another file.

    Args:
        repository: the repository.
        old: the commit of the first file.
        new: the commit of the other file.
        path: the first file's path in old.
        name: the other file's path in new.
        numbers: the line numbers in the first file. Lines that aren't there
            and bare lines (see _BARE_LINES) don't count.

    Returns:
        The number of kept lines.
    """
    lines = file_lines(repository, old, path) or []
    edits = edits_of(repository, old, new, name, path, 'all')
    return sum(1 for number in numbers if 1 <= number <= len(lines)
               and lines[number - 1].strip() not in _BARE_LINES
               and not any(begin <= number < end for begin, end, _, _ in edits))


@cached_for_objects(1, 2)
def _rewritten_path(repository: Repository, old: str, new: str, path: str) -> str | None:
    """Finds the file a file became when git finds no rename, its content
    too different by git's measure, which counts bytes.

    Args:
        repository: the repository.
        old: the commit the file is in.
        new: the newer commit.
        path: the file's path in old.

    Returns:
        The path of the file new added that keeps more than half of the
        file's lines. None if no file does, as when it was split into
        several.
    """
    lines = file_lines(repository, old, path) or []
    numbers = range(1, len(lines) + 1)
    n_real_lines = sum(1 for text in lines if text.strip() not in _BARE_LINES)
    n_kept, name = max(((_n_kept_lines(repository, old, new, path, name, numbers), name)
                        for name in _added_files(repository, old, new)),
                       default=(0, None))
    return name if n_kept >= _FOUND_MIN_LINES and n_kept > n_real_lines / 2 else None


@cached_for_objects(1, 2)
def _found_line(repository: Repository, source: str, target: str, path: str,
                line: int) -> MovedPlace | None:
    """Finds where a line of a file went when it isn't in that file in the
    other version, gone or renamed (see path_in()), as when the file was
    split into several. The added file whose diff with the gone one keeps
    the most lines around the line wins, if it keeps at least
    _FOUND_MIN_LINES. Gerrit has no such search, its comments stay behind
    on the change.

    Args:
        repository: the repository.
        source: the commit the line is in.
        target: the other commit.
        path: the file's path in source.
        line: the line's number.

    Returns:
        The line's place in target, see MovedPlace. None if no file wins.
    """
    near = range(line - _FOUND_RANGE, line + _FOUND_RANGE + 1)
    n_kept, name = max(((_n_kept_lines(repository, source, target, path, name, near), name)
                        for name in _added_files(repository, source, target)),
                       default=(0, ''))
    if n_kept < _FOUND_MIN_LINES:
        return None
    number, state = _line_in_file(repository, source, target, path, name, line)
    return name, number or None, EDITED if REMOVED == state else state


def _line_in_file(repository: Repository, source: str, target: str, path: str, name: str,
                  line: int) -> tuple[int, str]:
    """Moves a line of a file into another file or the same one renamed. A
    changed line goes to the line it pairs with in the diff view, if it
    pairs.

    Args:
        repository: the repository.
        source: the commit the line is in.
        target: the other commit.
        path: the file's path in source.
        name: the other file's path in target.
        line: the line's number.

    Returns:
        (line, state), see ported_line(). SAME counts as MOVED.
    """
    edits = edits_of(repository, source, target, name, path, 'all')
    number, state = ported_line(line, edits)
    if state in (SAME, MOVED):
        return number, MOVED
    old_begin, old_end, new_begin, new_end = next(
        edit for edit in edits if edit[0] <= line < edit[1])
    old = file_lines(repository, source, path) or []
    new = file_lines(repository, target, name) or []
    for old_index, new_index in paired_lines(
            [text.strip() for text in old[old_begin - 1:old_end - 1]],
            [text.strip() for text in new[new_begin - 1:new_end - 1]]):
        if old_index is not None and new_index is not None and old_begin + old_index == line:
            return new_begin + new_index, EDITED
    return number, state


# ── moving lines ──────────────────────────────────────────────────────────────

def ported_line(line: int, edits: list[Edit]) -> tuple[int, str]:
    """Moves a line through the edits of a diff, like Gerrit's
    GitPositionTransformer: lines outside the edits shift by the lines the
    edits before them added or removed.

    Args:
        line: the line's number in the old version.
        edits: the diff's edits, see edits_of().

    Returns:
        (line, state), state SAME, MOVED, EDITED or REMOVED. For a removed
        line, the line is the one before where it was, 0 at the top.
    """
    shift = 0
    for old_begin, old_end, new_begin, new_end in edits:
        if old_begin <= line < old_end:
            if new_begin == new_end:
                return new_begin - 1, REMOVED
            # Gerrit drops the line here and keeps the file. The line at
            # the same offset in the new lines is a better place to show.
            return new_begin + min(line - old_begin, new_end - new_begin - 1), EDITED
        if old_end <= line:
            shift += new_end - new_begin - (old_end - old_begin)
    return line + shift, SAME if 0 == shift else MOVED


@cached_for_objects(1, 2)
def moved_place(repository: Repository, source: str, target: str, path: str,
                line: int | None) -> MovedPlace:
    """Moves a file's line from one version into another. The versions
    never change, so the result is kept.

    Args:
        repository: the repository.
        source: the version the line is in, a commit or a parent like abc^.
            A commit for COMMIT_MSG.
        target: the other version, a commit or a parent like abc^. A commit
            for COMMIT_MSG.
        path: the file's path in source.
        line: the line's number, None for the whole file.

    Returns:
        The place in target, see MovedPlace and ported_line(). The line is
        None for the whole file and when the file is gone.
    """
    if PATCHSET_LEVEL == path:
        return path, None, SAME
    if COMMIT_MSG == path:
        if line is None:
            return path, None, SAME
        edits = edits_of(repository, commit_message_tree(repository, source),
                         commit_message_tree(repository, target), COMMIT_MSG_NAME)
        number, state = ported_line(line, edits)
        return path, number, state
    new_path = path_in(repository, source, target, path)
    if new_path is None:
        found = _found_line(repository, source, target, path, line) if line else None
        if found is None:
            return path, None, FILE_GONE
        return found
    if line is None:
        return new_path, None, SAME if new_path == path else MOVED
    if new_path != path:
        number, state = _line_in_file(repository, source, target, path, new_path, line)
    else:
        number, state = ported_line(line, edits_of(repository, source, target, path))
    if REMOVED == state:
        # Maybe moved into a new file, as when a file was split
        return _found_line(repository, source, target, path, line) or (new_path, number, state)
    return new_path, number, state


# ── threads ───────────────────────────────────────────────────────────────────

def thread_version(change: Change, comment: Comment) -> ThreadVersion | None:
    """Finds the version a thread's place counts in.

    Args:
        change: the thread's change.
        comment: the thread.

    Returns:
        (version, path there). The new side is the thread's patch set, the
        old side its parent, with the file's path there. None when the
        patch set isn't known.
    """
    origin = change.patch_sets.get(comment.patch_set)
    if origin is None:
        return None
    path, _line = split_place(comment.place)
    if OLD_SIDE != comment.side:
        return origin, path
    if path in (COMMIT_MSG, PATCHSET_LEVEL):
        return f'{origin}^', path
    return f'{origin}^', renames_of(change.repository, origin).get(path, path)


def port_thread(change: Change, comment: Comment,
                target: int | str | None = None) -> PortedPlace | None:
    """Finds where a thread of an older patch set is in a newer version,
    like Gerrit's ported comments.

    Like Gerrit, a thread moves through one direct diff from its patch set
    to the target, rebases included, and an old side thread through the
    diff of the two parents. Renamed files are followed. Unlike Gerrit,
    which then keeps only the file, a thread on edited lines keeps the line
    at the same offset of the new lines (state EDITED). A thread on a
    removed line keeps its file, the interfaces show it at the top of the
    file. One on a gone file keeps its place here, thread_place() moves it
    to the whole change, like Gerrit.

    Args:
        change: the thread's change.
        comment: the thread.
        target: a patch set's number, CURRENT (the only text it may be)
            for the change's commit, None for the latest patch set.

    Returns:
        The place, None when the thread is on that patch set already, or on
        a newer one.
    """
    target = target or change.latest_patch_set()
    # A thread moved by hand is ported from its newest move up to target.
    anchor = comment.anchored(None if isinstance(target, str) else target)
    moved_here = None if anchor is comment else PortedPlace(
        anchor.place, split_place(anchor.place)[1], MOVED, target)
    if isinstance(target, str):
        commit = change.commit
    elif target in change.patch_sets and anchor.patch_set < target:
        commit = change.patch_sets[target]
    else:
        return moved_here
    version = thread_version(change, anchor)
    if version is None or change.patch_sets[anchor.patch_set] == commit:
        return moved_here
    place, line, state = _place_in(change.repository, anchor, version,
                                   commit if OLD_SIDE != anchor.side
                                   else f'{commit}^', commit)
    if anchor is not comment and SAME == state:
        state = MOVED
    return PortedPlace(place, line, state, target)


def _place_in(repository: Repository, comment: Comment, version: ThreadVersion, target: str,
              shown: str) -> tuple[str, int | None, str]:
    """Moves a thread's place from its version into another.

    Args:
        repository: the repository.
        comment: the thread.
        version: the thread's version, see thread_version().
        target: the other version, a commit or a parent like abc^.
        shown: the commit whose diff names the file. For the old side, the
            path is the one in shown, where the interfaces look for it.

    Returns:
        (place, line, state), see ported_line() for the state.
    """
    source, source_path = version
    _path, line = split_place(comment.place)
    if COMMIT_MSG == source_path and (source.endswith('^') or target.endswith('^')):
        # Base has no commit message.
        return comment.place, line, SAME if source == target else FILE_GONE
    path, number, state = moved_place(repository, source, target, source_path, line)
    if FILE_GONE != state and target != shown and path not in (COMMIT_MSG, PATCHSET_LEVEL):
        # A file of the left version under its name in the right one
        path = path_in(repository, target, shown, path) or path
    return join_place(path, number), number, state


def comment_place(change: Change, path: str, line: int | None, side: str,
                  first: str | None = None, second: str | None = None) -> tuple[str, str, int]:
    """Finds where a new thread on a line of the diff of two versions goes.
    Like in Gerrit, a thread in the left pane is on the left patch set when
    that is one, else on the old side of the right one.

    Args:
        change: the change.
        path: the file's path in the right version.
        line: the line's number, None for the whole file.
        side: the pane, OLD_SIDE for the left, NEW_SIDE for the right.
        first: the left version, see versions_to_compare().
        second: the right version, see versions_to_compare().

    Returns:
        (place, side, patch set), the patch set 0 for the newest.
    """
    (first_label, _left), (second_label, _right) = versions_to_compare(change, first, second)
    patch_set = int(second_label) if second_label.isdigit() else 0
    if OLD_SIDE == side and first_label.isdigit():
        old_path = old_path_of(change, first, second, path) or path
        return join_place(old_path, line), NEW_SIDE, int(first_label)
    return join_place(path, line), side, patch_set


@dataclass
class ShownThread:
    """Where the diff of two versions shows a thread, see thread_place().

    Attributes:
        path: the file's path in the right version, PATCHSET_LEVEL for the
            whole change.
        line: the line's number, None for a thread at the top of the file:
            one on the whole file, or whose line is gone.
        side: the pane, OLD_SIDE or NEW_SIDE.
        origin: the patch set the thread was moved from, like PS2. '' when
            it wasn't moved.
        note: what became of the line, like "line removed", else ''.
    """
    path: str
    line: int | None
    side: str
    origin: str = ''
    note: str = ''


def thread_place(change: Change, comment: Comment, first: str | None = None,
                 second: str | None = None) -> ShownThread:
    """Finds where the diff of two versions shows a thread.

    A thread goes into the version a pane shows: a new side one of the
    left version's patch set into the left pane, other new side ones into
    the right pane, old side ones into the left pane. A thread on another
    version than the pane's is moved there, see port_thread(). Like in
    Gerrit, a thread on a file the version doesn't have becomes one on the
    whole change, its note keeps the old place: "file gone: a.txt:30".

    Args:
        change: the thread's change.
        comment: the thread.
        first: the left version, see versions_to_compare().
        second: the right version, see versions_to_compare().

    Returns:
        The place.
    """
    (first_label, left), (second_label, right) = versions_to_compare(change, first, second)
    # A thread moved by hand counts from its newest move up to the right
    # version, see Comment.anchored().
    comment = comment.anchored(int(second_label) if second_label.isdigit() else None)
    path, line = split_place(comment.place)
    version = thread_version(change, comment)
    if version is None:
        return ShownThread(path, line, comment.side)
    if OLD_SIDE == comment.side or first_label == str(comment.patch_set):
        side, target = OLD_SIDE, left
    else:
        side, target = NEW_SIDE, right
    if version[0] == target or PATCHSET_LEVEL == path:
        return ShownThread(path, line, side)
    place, number, state = _place_in(change.repository, comment, version, target, right)
    origin = f'PS{comment.patch_set}'
    if FILE_GONE == state:
        return ShownThread(PATCHSET_LEVEL, None, NEW_SIDE, origin, f'file gone: {comment.place}')
    # The line the thread was written on, when it isn't the one shown
    note = ''
    if REMOVED == state:
        # Below the line before, where the line was, 0 is the top
        note = f'line {line}, removed: {version_line(change.repository, version, line)}'
        number = number or None
    new_path = split_place(place)[0]
    # A file renamed or rewritten under another name says where it was.
    where = f'{origin} {comment.place}' if new_path != path else origin
    if EDITED == state:
        note = f'in {where}: {version_line(change.repository, version, line)}'
    elif MOVED == state and new_path != path:
        note = f'in {where}'
    return ShownThread(new_path, number, side, origin, note)


def version_line(repository: Repository, version: ThreadVersion, line: int | None) -> str:
    """Reads a line of a thread's version.

    Args:
        repository: the repository.
        version: the version, see thread_version().
        line: the line's number, None for none.

    Returns:
        The line, stripped. '' if there is none.
    """
    source, path = version
    if line is None:
        return ''
    if COMMIT_MSG == path:
        lines = (commit_message_text(repository, source).split('\n')
                 if not source.endswith('^') else [])
    else:
        lines = file_lines(repository, source, path) or []
    return lines[line - 1].strip() if 1 <= line <= len(lines) else ''
