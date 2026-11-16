# SPDX-License-Identifier: Zlib
"""The change screen's comments pane: the threads, grouped by file."""

import curses
from collections.abc import Callable
from typing import TYPE_CHECKING

from plustwo.model.comment import Comment
from plustwo.model.place import CHANGE_PLACE, OLD_SIDE, PATCHSET_LEVEL
from plustwo.review.labels import answered_by_author
from plustwo.review.porting import ShownThread
from plustwo.tui.file_names import diff_path, file_label
from plustwo.tui.lists import comment_label, keyed_cursor, status_attribute
from plustwo.tui.pane_frame import PaneBox, draw_frame

if TYPE_CHECKING:
    from plustwo.tui.interface import Interface


class CommentsPane:
    """The thread list, grouped by file in the file list's order, with a
    cursor that sticks to its thread (see keyed_cursor()), so it stays on it
    when the list changes, e.g. when a thread gets resolved.
    """

    def __init__(self, interface: 'Interface',
                 groups: Callable[[], list[tuple[str, list[Comment]]]]) -> None:
        """Starts the pane, the cursor on the first thread.

        Args:
            interface: the interface, which draws.
            groups: gives the threads by file, see threads_by_file().
        """
        self._interface = interface
        self._groups = groups
        self._position = 0
        self._key: str | None = None
        # The first row shown, for clicks
        self._first = 0

    def comments(self) -> list[Comment]:
        """Lists the threads in the pane's order: by file.

        Returns:
            The threads.
        """
        return [comment for _path, threads in self._groups() for comment in threads]

    def rows(self) -> list[tuple[str, int | None]]:
        """Lists the pane's rows.

        Returns:
            (path, None) for a file's header, (path, index into comments())
            for a thread.
        """
        rows: list[tuple[str, int | None]] = []
        index = 0
        for path, threads in self._groups():
            rows.append((path, None))
            for _comment in threads:
                rows.append((path, index))
                index += 1
        return rows

    @property
    def index(self) -> int:
        """The cursor's thread, an index into comments()."""
        keys = [comment.id for comment in self.comments()]
        self._position, self._key = keyed_cursor(keys, self._key, self._position)
        return self._position

    @index.setter
    def index(self, index: int) -> None:
        """Puts the cursor on a thread.

        Args:
            index: the index into comments().
        """
        keys = [comment.id for comment in self.comments()]
        self._position = index
        self._key = keys[index] if 0 <= index < len(keys) else None

    def selected(self) -> Comment | None:
        """Finds the thread at the cursor.

        Returns:
            The thread, None without threads.
        """
        comments = self.comments()
        return comments[min(self.index, len(comments) - 1)] if comments else None

    def click(self, offset: int) -> bool:
        """Puts the cursor on a clicked thread. A header picks its first
        thread.

        Args:
            offset: the row in the pane.

        Returns:
            Whether a row was clicked.
        """
        rows = self.rows()
        row = offset + self._first
        if row >= len(rows):
            return False
        picked = rows[row][1]
        if picked is None:
            picked = rows[min(row + 1, len(rows) - 1)][1]
        self.index = picked or 0
        return True

    def draw(self, box: PaneBox, is_focused: bool, touched: set[int], open_path: str | None,
             place_of: Callable[[Comment], ShownThread], renames: dict[str, str]) -> None:
        """Draws the threads, each group under a header (▶ for the file in
        the diff). A thread has ↩ when the author answered last and the
        start of its last message. Threads the comparison leaves as they
        were (see touched_threads()) get a · and a grey place, resolved ones
        go grey all over. Unresolved ones keep their text.

        Args:
            box: the pane's inside.
            is_focused: whether the pane has the focus.
            touched: the numbers of the threads the comparison touches.
            open_path: the file in the diff, None for none.
            place_of: finds where the diff shows a thread, see
                thread_place().
            renames: each renamed file's new path, mapped to its old one.
        """
        out = self._interface.text
        top, left, height, width = box
        rows = self.rows()
        if not rows:
            out(top, left, 'none yet', self._interface.dim(), left + width)
        cursor = self.index
        selected_row = next((row for row, (_path, index) in enumerate(rows)
                             if index == cursor), 0)
        first = max(0, min(selected_row - height + 1, len(rows) - height))
        self._first = first
        draw_frame(self._interface, box, 'Comments', is_focused, first,
                   len(rows) - first - height)
        open_group = diff_path(open_path) if open_path else None
        comments = self.comments()
        for row, (path, index) in enumerate(rows[first:first + height]):
            if index is None:
                current = diff_path(path) == open_group
                name = CHANGE_PLACE if PATCHSET_LEVEL == path else file_label(path, renames)
                out(top + row, left, f'{"▶" if current else " "} {name}'[:width],
                    curses.A_BOLD if current else self._interface.dim(), left + width)
                continue
            comment = comments[index]
            selected = index == cursor
            attributes = curses.A_REVERSE if selected and is_focused else 0
            unchanged = comment.number not in touched
            # Grey all over: resolved and left as it was
            quiet = unchanged and not comment.is_unresolved()
            grey = self._interface.dim(attributes)
            plain = attributes | grey if quiet else attributes
            out(top + row, left, ' ' * width, attributes, left + width)
            column = left
            answered = answered_by_author(comment)
            status = 'draft' if comment.is_draft() else comment.status
            parts = [(f' #{comment.number}'.ljust(4), plain),
                     ('·' if unchanged else ' ', attributes | grey),
                     ('↩ ' if answered else '  ',
                      attributes | curses.color_pair(3) if answered and not quiet else plain),
                     (f'[{comment_label(comment)}]',
                      plain if quiet else attributes | status_attribute(status))]
            shown = place_of(comment)
            if PATCHSET_LEVEL == shown.path != comment.place:
                # Like in Gerrit, a thread on a gone file is on the whole change.
                place = f' ({shown.origin}, {shown.note})'
            elif shown.line is not None:
                place = f' {"old " if OLD_SIDE == comment.side else ""}:{shown.line}'
            else:
                place = '' if PATCHSET_LEVEL == shown.path else ' file'
            parts.append((place, attributes | grey if unchanged else attributes))
            # The last message with text, not one that only changed the status
            text = next((' '.join(message.text.split()) for message in reversed(comment.messages)
                         if message.text.strip()), '')
            if text:
                parts.append((f'  {text}', attributes | grey))
            for text, attribute in parts:
                out(top + row, column, text, attribute, left + width)
                column += len(text)
