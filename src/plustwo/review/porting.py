# SPDX-License-Identifier: Zlib
"""Threads on newer patch sets, like Gerrit's ported comments."""

from dataclasses import dataclass

from plustwo.diff.edits import edits_of
from plustwo.diff.pairing import paired_lines
from plustwo.git.cache import cached_for_objects
from plustwo.git.commit_message import COMMIT_MSG_NAME, commit_message_text, commit_message_tree
from plustwo.git.files import file_lines, renames_of
from plustwo.git.repository import git
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


@dataclass
class PortedPlace:
    """Where a thread of an older patch set is in a newer one.

    place is the thread's place there, line its line (None for a thread
    on a whole file, or when the line or file is gone). state is SAME
    (same line number and text), MOVED (same text, other number or file),
    EDITED (the line was changed, line is the best guess), REMOVED or
    FILE_GONE.
    """
    place: str
    line: int | None
    state: str
    patch_set: int

    def label(self, place):
        """The note for inbox lines, without the patch set: '' when the
        thread is where it was, else like 'now :50' or 'line removed'.
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


@cached_for_objects(0, 1)
def path_in(old, new, path, repository=None):
    """The path a file of commit old has in commit new: the same if new
    has it, else where git's rename detection finds it or rewritten_path(),
    None if it is gone.
    """
    if git('rev-parse', '--verify', '--quiet', f'{new}:{path}', check=False,
           repository=repository):
        return path
    fields = git('diff-tree', '-M', '-r', '--name-status', '-z', old, new, '--',
                 repository=repository).split('\0')
    index = 0
    while index < len(fields) and fields[index]:
        if fields[index].startswith(('R', 'C')):
            if fields[index + 1] == path:
                return fields[index + 2]
            index += 3
        else:
            index += 2
    return rewritten_path(old, new, path, repository)


# Lines that don't tell where code went
BARE_LINES = ('', '{', '}', '};')


# A line of a gone file is looked for in the new files around it: this many
# lines on each side, and at least FOUND_MIN_LINES of them unchanged.
FOUND_RANGE = 5


FOUND_MIN_LINES = 3


@cached_for_objects(0, 1)
def added_files(old, new, repository=None):
    """The files commit new has and commit old hasn't, other than the ones
    git's rename detection finds as renamed or copied.
    """
    fields = git('diff-tree', '-M', '-r', '--name-status', '-z', old, new, '--',
                 repository=repository).split('\0')
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


def kept_lines(old, new, path, name, numbers, repository=None):
    """How many of these lines of file path in commit old the diff to file
    name in commit new keeps, the ones that aren't bare left out. The
    diff ignores whitespace: indentation often changes when code moves
    into another file.
    """
    lines = file_lines(old, path, repository) or []
    edits = edits_of(old, new, name, repository, path, 'all')
    return sum(1 for number in numbers if 1 <= number <= len(lines)
               and lines[number - 1].strip() not in BARE_LINES
               and not any(begin <= number < end for begin, end, _, _ in edits))


@cached_for_objects(0, 1)
def rewritten_path(old, new, path, repository=None):
    """The file of commit new that file path of commit old became when git
    finds no rename, its content too different by git's measure, which
    counts bytes: the added file that keeps more than half of its lines.
    None if no file does, as when it was split into several.
    """
    lines = file_lines(old, path, repository) or []
    numbers = range(1, len(lines) + 1)
    real = sum(1 for text in lines if text.strip() not in BARE_LINES)
    best = max(((kept_lines(old, new, path, name, numbers, repository), name)
                for name in added_files(old, new, repository)), default=(0, None))
    return best[1] if best[0] >= FOUND_MIN_LINES and best[0] > real / 2 else None


@cached_for_objects(0, 1)
def found_line(source, target, path, line, repository=None):
    """Where a line of file path of source went in target when it isn't in
    that file there, gone or renamed (see path_in()), as when the file was
    split into several: (path, line, state) or None.

    The added file whose diff with the gone one keeps the most lines
    around the line wins, if it keeps at least FOUND_MIN_LINES. Gerrit
    has no such search, its comments stay behind on the change.
    """
    near = range(line - FOUND_RANGE, line + FOUND_RANGE + 1)
    kept, name = max(((kept_lines(source, target, path, name, near, repository), name)
                      for name in added_files(source, target, repository)), default=(0, None))
    if kept < FOUND_MIN_LINES:
        return None
    number, state = line_in_file(source, target, path, name, line, repository)
    return name, number or None, EDITED if REMOVED == state else state


def line_in_file(source, target, path, name, line, repository=None):
    """Moves a line of file path in source to file name in target, another
    file or the same one renamed: (line, state). A changed line goes to
    the line it pairs with in the diff view, if it pairs.
    """
    edits = edits_of(source, target, name, repository, path, 'all')
    number, state = ported_line(line, edits)
    if state in (SAME, MOVED):
        return number, MOVED
    old_begin, old_end, new_begin, new_end = next(
        edit for edit in edits if edit[0] <= line < edit[1])
    old = file_lines(source, path, repository) or []
    new = file_lines(target, name, repository) or []
    for old_index, new_index in paired_lines(
            [text.strip() for text in old[old_begin - 1:old_end - 1]],
            [text.strip() for text in new[new_begin - 1:new_end - 1]]):
        if None not in (old_index, new_index) and old_begin + old_index == line:
            return new_begin + new_index, EDITED
    return number, state


def ported_line(line, edits):
    """Moves a line through the edits of a diff, like Gerrit's
    GitPositionTransformer: lines outside the edits shift by the lines the
    edits before them added or removed. Returns (line, state). For a
    removed line, line is the one before where it was, 0 at the top.
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


@cached_for_objects(0, 1)
def moved_place(source, target, path, line, repository=None):
    """Moves a file's line from one version into another: (path, line,
    state), see ported_line(). source and target are commits, or parents
    like abc^, which never change, so the result is kept. line is None for
    the whole file, also in the result when the line or the file is gone.
    For /COMMIT_MSG, both must be commits.
    """
    if PATCHSET_LEVEL == path:
        return path, None, SAME
    if COMMIT_MSG == path:
        if line is None:
            return path, None, SAME
        edits = edits_of(commit_message_tree(source, repository),
                         commit_message_tree(target, repository), COMMIT_MSG_NAME, repository)
        number, state = ported_line(line, edits)
        return path, number, state
    new_path = path_in(source, target, path, repository)
    if new_path is None:
        found = found_line(source, target, path, line, repository) if line else None
        if found is None:
            return path, None, FILE_GONE
        return found
    if line is None:
        return new_path, None, SAME if new_path == path else MOVED
    if new_path != path:
        number, state = line_in_file(source, target, path, new_path, line, repository)
    else:
        number, state = ported_line(line, edits_of(source, target, path, repository))
    if REMOVED == state:
        # Maybe moved into a new file, as when a file was split
        return found_line(source, target, path, line, repository) or (new_path, number, state)
    return new_path, number, state


def thread_version(change, comment, repository=None):
    """The version a thread's place counts in: (version, path there). The
    new side is the thread's patch set, the old side its parent, with the
    file's path there. None when the patch set isn't known.
    """
    origin = change.patch_sets.get(comment.patch_set)
    if origin is None:
        return None
    path, _line = split_place(comment.place)
    if OLD_SIDE != comment.side:
        return origin, path
    if path in (COMMIT_MSG, PATCHSET_LEVEL):
        return f'{origin}^', path
    return f'{origin}^', renames_of(origin, repository).get(path, path)


def port_thread(change, comment, target=None, repository=None):
    """Where a thread of an older patch set is in a newer version, like
    Gerrit's ported comments, as a PortedPlace. target is a patch set
    number, the latest without it. None when the thread is on that patch
    set already, or on a newer one. target can also be CURRENT, the
    change's commit.

    Like Gerrit, a thread moves through one direct diff from its patch set
    to the target, rebases included, and an old side thread through the
    diff of the two parents. Renamed files are followed. Unlike Gerrit,
    which then keeps only the file, a thread on edited lines keeps the line
    at the same offset of the new lines (state EDITED). A thread on a
    removed line keeps its file, the interfaces show it at the top of the
    file. One on a gone file keeps its place here, thread_place() moves it
    to the whole change, like Gerrit.
    """
    target = target or change.latest_patch_set()
    # A thread moved by hand is ported from its newest move up to target.
    anchor = comment.anchored(None if CURRENT == target else target)
    here = None if anchor is comment else PortedPlace(
        anchor.place, split_place(anchor.place)[1], MOVED, target)
    if CURRENT == target:
        commit = change.commit
    elif target in change.patch_sets and anchor.patch_set < target:
        commit = change.patch_sets[target]
    else:
        return here
    version = thread_version(change, anchor, repository)
    if version is None or change.patch_sets[anchor.patch_set] == commit:
        return here
    place, line, state = _place_in(change, anchor, version, commit if OLD_SIDE != anchor.side
                                   else f'{commit}^', commit, repository)
    if anchor is not comment and SAME == state:
        state = MOVED
    return PortedPlace(place, line, state, target)


def _place_in(change, comment, version, target, shown, repository=None):
    """A thread's place moved from its version into target, a version of
    the file as the diff of shown (a commit) names it: (place, line,
    state). For the old side, the path is the one in shown, where the
    interfaces look for it.
    """
    source, source_path = version
    _path, line = split_place(comment.place)
    if COMMIT_MSG == source_path and (source.endswith('^') or target.endswith('^')):
        # Base has no commit message.
        return comment.place, line, SAME if source == target else FILE_GONE
    path, number, state = moved_place(source, target, source_path, line, repository)
    if FILE_GONE != state and target != shown and path not in (COMMIT_MSG, PATCHSET_LEVEL):
        # A file of the left version under its name in the right one
        path = path_in(target, shown, path, repository) or path
    return join_place(path, number), number, state


def comment_place(change, path, line, side, first=None, second=None, repository=None):
    """Where a new thread on a line of the diff of two versions goes:
    (place, side, patch set). path is the file's path in the right version,
    line None for the whole file. Like in Gerrit, a thread in the left pane
    is on the left patch set when that is one, else on the old side of the
    right one. 0 as patch set means the newest.
    """
    (first_label, _left), (second_label, _right) = versions_to_compare(change, first, second)
    patch_set = int(second_label) if second_label.isdigit() else 0
    if OLD_SIDE == side and first_label.isdigit():
        old_path = old_path_of(change, first, second, path, repository) or path
        return join_place(old_path, line), NEW_SIDE, int(first_label)
    return join_place(path, line), side, patch_set


@dataclass
class ShownThread:
    """Where the diff of two versions shows a thread, see thread_place()."""
    path: str
    line: int | None
    side: str
    origin: str = ''
    note: str = ''


def thread_place(change, comment, first=None, second=None, repository=None):
    """Where the diff of two versions shows a thread, as a ShownThread.

    A thread goes into the version a pane shows: a new side one of the
    left version's patch set into the left pane, other new side ones into
    the right pane, old side ones into the left pane. A thread on another
    version than the pane's is moved there, see port_thread(), and its
    origin is like "PS2". path is the file's path in the right version.
    line is None for a thread at the top of the file: one on the whole
    file, or whose line is gone. note says what became of the line, like
    "line removed", else empty.

    Like in Gerrit, a thread on a file the version doesn't have becomes
    one on the whole change (path PATCHSET_LEVEL), its note keeps the old
    place: "file gone: a.txt:30".
    """
    (first_label, left), (second_label, right) = versions_to_compare(change, first, second)
    # A thread moved by hand counts from its newest move up to the right
    # version, see Comment.anchored().
    comment = comment.anchored(int(second_label) if second_label.isdigit() else None)
    path, line = split_place(comment.place)
    version = thread_version(change, comment, repository)
    if version is None:
        return ShownThread(path, line, comment.side)
    if OLD_SIDE == comment.side or first_label == str(comment.patch_set):
        side, target = OLD_SIDE, left
    else:
        side, target = NEW_SIDE, right
    if version[0] == target or PATCHSET_LEVEL == path:
        return ShownThread(path, line, side)
    place, number, state = _place_in(change, comment, version, target, right, repository)
    origin = f'PS{comment.patch_set}'
    if FILE_GONE == state:
        return ShownThread(PATCHSET_LEVEL, None, NEW_SIDE, origin, f'file gone: {comment.place}')
    # The line the thread was written on, when it isn't the one shown
    note = ''
    if REMOVED == state:
        # Below the line before, where the line was, 0 is the top
        note = f'line {line}, removed: {version_line(version, line, repository)}'
        number = number or None
    new_path = split_place(place)[0]
    # A file renamed or rewritten under another name says where it was.
    where = f'{origin} {comment.place}' if new_path != path else origin
    if EDITED == state:
        note = f'in {where}: {version_line(version, line, repository)}'
    elif MOVED == state and new_path != path:
        note = f'in {where}'
    return ShownThread(new_path, number, side, origin, note)


def version_line(version, line, repository=None):
    """A line of a thread's version (see thread_version()), stripped, ''
    if there is none.
    """
    source, path = version
    if line is None:
        return ''
    if COMMIT_MSG == path:
        lines = (commit_message_text(source, repository).split('\n')
                 if not source.endswith('^') else [])
    else:
        lines = file_lines(source, path, repository) or []
    return lines[line - 1].strip() if 1 <= line <= len(lines) else ''
