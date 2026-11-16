# SPDX-License-Identifier: Zlib
"""The threads in the change screen's diff: at the cursor, folding, going
to them, their boxes and moving them by hand.
"""

from typing import TYPE_CHECKING

from plustwo.git.errors import ChangeError
from plustwo.model.change import Change
from plustwo.model.comment import Comment
from plustwo.model.place import PATCHSET_LEVEL, WHOLE_CHANGE, place_label
from plustwo.review.compare import versions_to_compare
from plustwo.review.history import message_patch_sets
from plustwo.review.labels import thread_file_after
from plustwo.review.moves import move_text, move_thread
from plustwo.review.porting import ShownThread, comment_place, thread_place
from plustwo.tui.diff_cells import HALF_SEPARATOR, marked_segments
from plustwo.tui.diff_gap import DiffGap
from plustwo.tui.diff_pane import ThreadBlocks, in_half, thread_rows
from plustwo.tui.file_names import diff_path
from plustwo.tui.messages import ERROR, OK
from plustwo.tui.sidebar import COMMENTS, DIFF

if TYPE_CHECKING:
    from plustwo.tui.change_view import ChangeView
    from plustwo.tui.interface import Interface


class DiffThreads:
    """The threads of the change screen's diff. Reads the shown change, the
    comparison and the diff from the change screen.

    Attributes:
        moving: the number of the thread being moved by hand, None while
            none is, see drop().
    """

    def __init__(self, interface: 'Interface', view: 'ChangeView') -> None:
        """Starts with every thread folded or unfolded by its state.

        Args:
            interface: the interface.
            view: the change screen.
        """
        self._interface = interface
        self._view = view
        # Threads folded or unfolded by hand: number -> expanded
        self._expanded: dict[int, bool] = {}
        self.moving: int | None = None

    @property
    def _change(self) -> Change:
        """The change shown."""
        return self._view.change

    def forget(self) -> None:
        """Forgets the threads folded or unfolded by hand, for another
        change.
        """
        self._expanded = {}

    # ── places ────────────────────────────────────────────────────────────────

    def place_of(self, comment: Comment) -> ShownThread:
        """Finds where the shown diff has a thread, see thread_place().

        Args:
            comment: the thread.

        Returns:
            The thread's place.
        """
        return thread_place(self._change, comment, *self._view.compare)

    def markers(self) -> dict[tuple[str, int | None], list[int]]:
        """Finds the threads on the shown file.

        Returns:
            Each (side, line), mapped to the numbers of its threads.
        """
        path = self._view.files_pane.selected()
        markers: dict[tuple[str, int | None], list[int]] = {}
        for comment in self._change.comments:
            shown = self.place_of(comment)
            if shown.path == path and shown.line is not None:
                markers.setdefault((shown.side, shown.line), []).append(comment.number)
        return markers

    def _shown_patch_set(self) -> int | None:
        """Finds the patch set on the right side of the diff.

        Returns:
            The patch set's number, None for the current commit.
        """
        (_first, _), (second, _) = versions_to_compare(self._change, *self._view.compare)
        return int(second) if second.isdigit() else None

    # ── at the cursor ─────────────────────────────────────────────────────────

    def threads_at_cursor(self) -> list[int]:
        """Finds the threads on the diff's cursor row: the thread the row
        belongs to, or the threads on the cursor line.

        Returns:
            The threads' numbers.
        """
        diff = self._view.diff
        if not diff.numbers:
            return []
        number = diff.thread_at(diff.cursor)
        if number is not None:
            return [number]
        old, new = diff.numbers[diff.cursor]
        markers = self.markers()
        return markers.get(('new', new), []) + markers.get(('old', old), [])

    def thread_at_cursor(self) -> Comment | None:
        """Finds the thread the diff's cursor is on, or the first on its
        line.

        Returns:
            The thread, None without.
        """
        numbers = self.threads_at_cursor()
        return self._change.comment(numbers[0]) if numbers else None

    def target_thread(self) -> Comment | None:
        """Finds the thread keys like y or r act on: the selected one in the
        comments, the one at the cursor in the diff.

        Returns:
            The thread, None elsewhere.
        """
        if COMMENTS == self._view.focus:
            return self._view.comments_pane.selected()
        if DIFF == self._view.focus:
            return self.thread_at_cursor()
        return None

    # ── folding ───────────────────────────────────────────────────────────────

    def is_expanded(self, comment: Comment | None) -> bool:
        """Tells whether a thread shows all its messages in the diff.
        Unresolved ones and those with drafts do, unless folded by hand.

        Args:
            comment: the thread, None for none.

        Returns:
            True if the thread is unfolded.
        """
        if comment is None:
            return False
        default = comment.is_unresolved() or comment.has_draft() or comment.is_draft()
        return self._expanded.get(comment.number, default)

    def set_expanded(self, number: int, is_expanded: bool) -> None:
        """Folds or unfolds a thread.

        Args:
            number: the thread's number.
            is_expanded: whether to unfold it, else fold it.
        """
        self._expanded[number] = is_expanded

    def set_all_expanded(self, is_expanded: bool) -> None:
        """Folds or unfolds every thread.

        Args:
            is_expanded: whether to unfold them, else fold them.
        """
        self._expanded = {comment.number: is_expanded for comment in self._change.comments}

    def toggle_at_cursor(self) -> None:
        """Folds or unfolds the threads at the cursor (l in the diff)."""
        numbers = self.threads_at_cursor()
        if not numbers:
            self._interface.say('No thread on this line. c starts one.')
            return
        comments = [self._change.comment(number) for number in numbers]
        expand = not any(self.is_expanded(comment) for comment in comments)
        for comment in comments:
            if comment is not None:
                self._expanded[comment.number] = expand

    def toggle_all(self) -> None:
        """Folds all threads if one is unfolded, else unfolds all (i)."""
        self.set_all_expanded(not any(self.is_expanded(comment)
                                      for comment in self._change.comments))

    # ── layout ────────────────────────────────────────────────────────────────

    def arrange(self, path: str, width: int, thread_search: str) -> None:
        """Lays out the threads of a file in the diff: a thread on a line in
        its side's half, others on top, as wide as the diff.

        Args:
            path: the file in the diff.
            width: the width of the diff's text.
            thread_search: the text whose matches in threads are marked,
                '' for none.
        """
        change = self._change
        diff = self._view.diff
        blocks: ThreadBlocks = {}
        signature: list[object] = [path, width, thread_search]
        for comment in change.comments:
            shown = self.place_of(comment)
            if diff_path(shown.path) != path:
                continue
            expanded = self.is_expanded(comment)
            place = (shown.side, shown.line) if shown.line is not None else None
            note, origin = shown.note, shown.origin
            if PATCHSET_LEVEL == shown.path:
                note = note or WHOLE_CHANGE
            if place and place not in diff.shown_places():
                note = ((f'{note}, ' if note else '')
                        + f'on {"old " if "old" == shown.side else ""}line {shown.line}, '
                        'not in the diff '
                        f'({self._interface.keymap.label("more-context")} shows more context)')
            # Like in Gerrit, a thread on a line goes into its side's half.
            # Threads on lines not shown go on top, as wide as the diff.
            side = self.thread_half(comment)
            patch_sets = message_patch_sets(change, comment, self._shown_patch_set())
            rows = thread_rows(comment, self.half(side)[1], expanded, note, origin, patch_sets)
            if side:
                rows = [in_half(parts, side, self.half('old')[1]) for parts in rows]
            if thread_search:
                rows = [marked_segments(parts, thread_search) for parts in rows]
            blocks.setdefault(place, []).append((comment.number, rows))
            signature.append((comment.number, comment.status, place, expanded, tuple(patch_sets),
                              tuple((message.author, message.text, message.sent, message.unresolved)
                                    for message in comment.messages),
                              tuple((move.patch_set, move.place, move.side)
                                    for move in comment.moves)))
        diff.arrange(blocks, tuple(signature))

    def thread_half(self, comment: Comment) -> str | None:
        """Finds the half a thread is drawn in. Its boxes take the same
        width.

        Args:
            comment: the thread.

        Returns:
            The side, None when the thread is as wide as the diff: on the
            whole file, or on a line the diff doesn't show, which go on top.
        """
        shown = self.place_of(comment)
        place = (shown.side, shown.line)
        return shown.side if shown.line is not None \
            and place in self._view.diff.shown_places() else None

    def half(self, side: str | None) -> tuple[int, int]:
        """Finds the columns of one half of the diff's text.

        Args:
            side: old or new, None for the whole width.

        Returns:
            (offset, width).
        """
        width = self._view.diff_box[3] - 2
        half = (width - len(HALF_SEPARATOR)) // 2
        if side is None:
            return 0, width
        if 'old' == side:
            return 0, half
        return half + len(HALF_SEPARATOR), width - half - len(HALF_SEPARATOR)

    def gap(self, index: int, side: str | None) -> DiffGap:
        """Makes the comment box's room below a row, in the half of a side
        like the threads there.

        Args:
            index: the diff pane's row.
            side: old or new, None for the whole width.

        Returns:
            The gap.
        """
        box = self._view.diff_box
        if side is None:
            return DiffGap(self._view.diff, box, index)
        top, left, height, _width = box
        offset, width = self.half(side)
        return DiffGap(self._view.diff, (top, left + 2 + offset, height, width), index)

    def gap_below(self, comment: Comment) -> DiffGap | None:
        """Finds where the comment box goes for a thread: in a gap below the
        thread in the diff.

        Args:
            comment: the thread.

        Returns:
            The gap, None if the diff doesn't show the thread.
        """
        if not self._view.is_laid_out:
            return None
        end = self._view.diff.thread_end(comment.number)
        if end is None:
            return None
        return self.gap(end, self.thread_half(comment))

    # ── going to threads ──────────────────────────────────────────────────────

    def show(self, comment: Comment) -> None:
        """Selects the thread's file and puts the diff cursor on the thread,
        or on its line if the thread isn't in the diff.

        Args:
            comment: the thread.
        """
        view = self._view
        shown = self.place_of(comment)
        path = diff_path(shown.path)
        if path in view.files_pane.files:
            view.files_pane.select(path)
            if view.prepare_diff():
                height = view.diff_height()
                if not view.diff.go_to_thread(comment.number, height) and shown.line is not None:
                    view.diff.go_to_line(shown.line, shown.side, height)

    def go_to(self, is_forward: bool, thread_type: str = 'thread') -> None:
        """Goes to the next or previous thread, in the next file with
        threads at the file's end, like going through all of them (]t and
        [t).

        Args:
            is_forward: whether to go down, else up.
            thread_type: thread for all threads, unresolved for only those
                (]u and [u), relevant for those worth a look (]n and [n),
                see relevant_threads().
        """
        view = self._view
        view.focus_pane(DIFF)
        # The diff may not be laid out for this file yet
        view.prepare_diff()
        relevant = view.relevant() if 'relevant' == thread_type else None
        threads = [comment for comment in self._change.comments
                   if ('unresolved' != thread_type or comment.is_unresolved())
                   and (relevant is None or comment.number in relevant)]
        numbers = {comment.number for comment in threads}
        if view.diff.go_to_next_thread(is_forward, view.diff_height(), numbers):
            return
        paths = {diff_path(self.place_of(comment).path) for comment in threads}
        path = thread_file_after(view.files_pane.files, view.files_pane.selected(), paths,
                                 1 if is_forward else -1)
        if path is None:
            what = 'threads' if 'thread' == thread_type else thread_type + ' threads'
            self._interface.say(f'No more {what} in this direction.')
            return
        view.files_pane.select(path)
        view.prepare_diff()
        view.diff.go_to_next_thread(is_forward, view.diff_height(), numbers, starts_at_edge=True)
        self._interface.say(f'In {view.files_pane.label(path)}.')

    # ── moving by hand ────────────────────────────────────────────────────────

    def start_moving(self) -> None:
        """Picks up the target thread to move it by hand: the diff's cursor
        goes to its new line, drop() puts it there.
        """
        comment = self.target_thread()
        if comment is None:
            self._interface.say('No thread here to move.')
            return
        self.moving = comment.number
        self._view.focus_pane(DIFF)
        keymap = self._interface.keymap
        self._interface.say(f'Moving #{comment.number}: go to its line, '
                            f'{keymap.label("open")} drops it, {keymap.label("back")} cancels.')

    def cancel_moving(self) -> None:
        """Leaves the thread being moved where it was."""
        self._interface.say(f'#{self.moving} stays where it was.')
        self.moving = None

    def drop(self) -> None:
        """Moves the thread being moved to the cursor's line, or to the
        whole file on a row without a line (Enter while moving).
        """
        interface = self._interface
        view = self._view
        number, self.moving = self.moving, None
        assert number is not None
        path = view.files_pane.selected()
        if DIFF != view.focus or path is None:
            interface.say(f'#{number} stays where it was: no file shown.', ERROR)
            return
        cursor = view.diff.cursor_place()
        line, side = cursor if cursor is not None else (None, 'new')
        change = self._change
        try:
            place, side, patch_set = comment_place(change, path, line, side, *view.compare)
            move = move_thread(change.repository, change.change_id, number, place, side,
                               patch_set)
        except ChangeError as error:
            interface.say(str(error), ERROR)
            return
        interface.after_write()
        interface.say(f'#{number} ' + (move_text(move) if move
                                       else f'now on {place_label(place, side)}') + '.', OK)
