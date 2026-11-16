# SPDX-License-Identifier: Zlib
"""The change screen's files pane: the change's files."""

import curses
from typing import TYPE_CHECKING

from plustwo.git.errors import ChangeError
from plustwo.model.change import Change
from plustwo.model.comment import Comment
from plustwo.review.compare import compared_files
from plustwo.review.reviewed import is_reviewed
from plustwo.tui.file_names import diff_path, file_label
from plustwo.tui.lists import keyed_cursor, position_after
from plustwo.tui.pane_frame import PaneBox, draw_frame

if TYPE_CHECKING:
    from plustwo.tui.interface import Interface


class FilesPane:
    """The file list, the commit message first, with a cursor that sticks
    to its path (see keyed_cursor()), so it stays on it when the list
    changes.

    Attributes:
        files: the paths, the commit message (COMMIT_MSG) first.
        renames: each renamed file's new path, mapped to its old one.
        counts: each file's added and removed lines in the change.
    """

    def __init__(self, interface: 'Interface') -> None:
        """Starts the pane empty, without a cursor until the change is read.

        Args:
            interface: the interface, which draws.
        """
        self._interface = interface
        self.files: list[str] = []
        self.renames: dict[str, str] = {}
        self.counts: dict[str, tuple[int, int]] = {}
        self._position: int | None = None
        self._key: str | None = None

    @property
    def has_cursor(self) -> bool:
        """Whether the cursor was put on a row since the change was read."""
        return self._position is not None

    @property
    def index(self) -> int:
        """The cursor's row, 0 until the change is read."""
        if self._position is None:
            return 0
        self._position, self._key = keyed_cursor(self.files, self._key, self._position)
        return self._position

    @index.setter
    def index(self, index: int | None) -> None:
        """Puts the cursor on a row.

        Args:
            index: the row, None before the change is read.
        """
        self._position = index
        self._key = self.files[index] if index is not None and \
            0 <= index < len(self.files) else None

    def selected(self) -> str | None:
        """Finds the file at the cursor.

        Returns:
            The file's path, None without files.
        """
        return self.files[self.index] if self.files else None

    def select(self, path: str) -> None:
        """Puts the cursor on a file.

        Args:
            path: the file's path, one of files.
        """
        self.index = self.files.index(path)

    def label(self, path: str) -> str:
        """Names a file with its old name if it was renamed, see
        file_label().

        Args:
            path: the file's path.

        Returns:
            The name.
        """
        return file_label(path, self.renames)

    def first(self, height: int) -> int:
        """Finds the first row the pane shows: the cursor stays visible, in
        the middle if it can.

        Args:
            height: the pane's height.

        Returns:
            The row.
        """
        return max(0, min(self.index - height // 2, len(self.files) - height))

    def move(self, action: str, count: int | None) -> None:
        """Moves the cursor, see position_after().

        Args:
            action: one of LIST_ACTIONS.
            count: the count typed before the key, None for none.
        """
        self.index = position_after(self.index, action, len(self.files), count)

    def step(self, n_files: int) -> None:
        """Moves the cursor by files, stopping at the ends.

        Args:
            n_files: how many files, negative for up.
        """
        self.index = max(0, min(self.index + n_files, len(self.files) - 1))

    def click(self, offset: int, height: int) -> None:
        """Puts the cursor on a clicked row.

        Args:
            offset: the row in the pane.
            height: the pane's height.
        """
        if self.first(height) + offset < len(self.files):
            self.index = self.first(height) + offset

    def draw(self, box: PaneBox, is_focused: bool, change: Change, compare: tuple[str, str],
             groups: list[tuple[str, list[Comment]]]) -> None:
        """Draws the file list: ✓ for reviewed files, the threads per file
        and a bar of the lines the comparison adds and removes.

        Args:
            box: the pane's inside.
            is_focused: whether the pane has the focus.
            change: the change shown.
            compare: the two versions compared, see versions_to_compare().
            groups: the threads by file, see threads_by_file().
        """
        out = self._interface.text
        reviewed = [is_reviewed(change, path) for path in self.files]
        top, left, height, width = box
        first = self.first(height)
        draw_frame(self._interface, box, f'Files  {sum(reviewed)}/{len(self.files)} reviewed',
                   is_focused, first, len(self.files) - first - height)
        # The counts between the compared versions. Files the comparison
        # doesn't change are grey.
        try:
            compared = compared_files(change, *compare)
        except ChangeError:
            compared = self.counts
        # Threads per file, as the diff shows them: N● with N unresolved
        # ones, grey with the number of threads when all are resolved
        counts: dict[str, tuple[int, int]] = {}
        for path, threads in groups:
            unresolved = sum(comment.is_unresolved() for comment in threads)
            so_far = counts.get(diff_path(path), (0, 0))
            counts[diff_path(path)] = (so_far[0] + unresolved, so_far[1] + len(threads))
        count_width = max((len(str(unresolved or total)) + 2
                           for unresolved, total in counts.values()), default=0)
        # Like Gerrit's size column: a short bar of added and removed lines
        bar_width = 6 if any(sum(compared.get(path, (0, 0))) for path in self.files) else 0
        name_width = max(width - 2 - count_width - bar_width, 1)
        for row, path in enumerate(self.files[first:first + height]):
            index = first + row
            selected = index == self.index
            attributes = curses.A_REVERSE if selected and is_focused else (
                curses.A_BOLD if selected else 0)
            name = self.label(path)
            out(top + row, left, '✓ ' if reviewed[index] else '  ',
                attributes | curses.color_pair(2), left + width)
            out(top + row, left + 2, ' ' * (width - 2), attributes, left + width)
            changed = path in compared
            out(top + row, left + 2, f'{name:<{name_width}.{name_width}}',
                attributes | (0 if changed else self._interface.dim(attributes)), left + width)
            unresolved, total = counts.get(path, (0, 0))
            if total:
                label = f'{unresolved or total}●'.rjust(count_width)
                out(top + row, left + 2 + name_width, label,
                    attributes | (0 if unresolved else self._interface.dim(attributes)),
                    left + width)
            added, removed = compared.get(path, (0, 0))
            if added + removed:
                scale = max(added + removed, 1) / 5 if added + removed > 5 else 1
                plus, minus = round(added / scale), round(removed / scale)
                out(top + row, left + width - 5, '+' * plus, curses.color_pair(2), left + width)
                out(top + row, left + width - 5 + plus, '-' * minus, curses.color_pair(1),
                    left + width)
