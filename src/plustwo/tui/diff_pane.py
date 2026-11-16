# SPDX-License-Identifier: Zlib
"""The diff pane: rows of the diff with threads between them."""

import curses
import textwrap
from collections.abc import Sequence
from typing import Any

from plustwo.diff.rows import expand_gaps
from plustwo.diff.search import gaps_with_matches, search_matches
from plustwo.git.files import file_lines
from plustwo.model.change import Change
from plustwo.model.comment import Comment
from plustwo.model.message import Message
from plustwo.model.roles import REVIEWER
from plustwo.review import file_diff
from plustwo.review.compare import diff_endpoints, old_path_of, versions_to_compare
from plustwo.review.file_diff import file_diff_rows
from plustwo.review.labels import message_text
from plustwo.review.moves import move_text
from plustwo.review.rebase_lines import rebase_lines
from plustwo.tui.colours import (
    AUTHOR_COLOURS, CURSOR_LINE_GREY, FRAME_COLOUR, LEFT_MARK, LINE_NUMBER,
    LINE_NUMBER_BACKGROUND, REVIEWER_COLOURS, RIGHT_MARK, SECONDARY, STATUS_COLOURS, THREAD_TINT,
    cursor_tint, tone)
from plustwo.tui.colour_pairs import ColourPairs
from plustwo.tui.diff_cells import (
    HALF_SEPARATOR, CharacterColour, RenderedRow, Segment, highlighted, rendered_rows)
from plustwo.tui.lists import comment_label


# What a row of the pane is: ('diff', index of the diff line) or ('thread',
# thread number, row inside the thread)
PaneRow = tuple[Any, ...]

# A row to keep the cursor on: (the row, the diff line it belongs to, -1
# above the first)
KeptRow = tuple[PaneRow, int]

# The rows of the threads by their place: (side, line), None for the whole
# file, mapped to (thread number, segments of each row) per thread
ThreadBlocks = dict[tuple[str, int] | None, list[tuple[int, list[list[Segment]]]]]

# Columns a thread is indented by in the diff
_THREAD_INDENT = 3


class DiffPane:
    """The diff of one file, side by side like in Gerrit, drawn inside the
    screen, with the comment threads shown under their lines. Keeps a
    cursor row and the scroll position.

    Attributes:
        expanded_gaps: the gaps of common lines the reviewer opened, by
            their first old line.
        side: the side of the diff the cursor is on, old or new. Comments
            go there.
        rows: what each row is, see PaneRow.
        lines: the segments of each row.
        numbers: the (old, new) line numbers of each row, (None, None) on
            thread rows.
        cursor: the cursor's row.
        scroll: the first row shown.
        gap: (row, height) of the comment box's room below that row, None
            without, see DiffGap.
        marks_from_below: whether the diff has lines from below, and lines
            partly from below.
        has_moved: whether the diff has moved lines, see moved_lines().
    """

    def __init__(self, pairs: ColourPairs) -> None:
        """Starts the pane empty, see show().

        Args:
            pairs: the interface's colour pairs.
        """
        self._pairs = pairs
        self._key: tuple[Any, ...] | None = None
        self._path: str | None = None
        self._threads_key: object = None
        self._diff_lines: list[list[Segment]] = []
        self._diff_numbers: list[tuple[int | None, int | None]] = []
        # Whether a diff row goes on with the line of the row above (wrapped),
        # and whether it has removed or added lines
        self._continues: list[bool] = []
        self._changed: list[bool] = []
        # Syntax colours per (version, path), see _colours()
        self._colour_cache: dict[tuple[str, str], list[list[CharacterColour]] | None] = {}
        self.expanded_gaps: set[int] = set()
        # The first old line of each gap row, else None
        self._gaps: list[int | None] = []
        # The (old, new) text of each diff line on its first row, and the
        # gaps left (first old line, first new line, lines), for search
        self._texts: list[tuple[str, str] | None] = []
        self._closed_gaps: list[tuple[int, int, int]] = []
        # (old version, old path, new version, new path) of the diff
        self._sources: tuple[str, str, str, str] | None = None
        self.side = 'new'
        # The row to keep the cursor on when the rows are laid out again,
        # and whether to show the rows from the top then
        self._keep: KeptRow | None = None
        self._is_from_top = False
        self.rows: list[PaneRow] = []
        self.lines: list[list[Segment]] = []
        self.numbers: list[tuple[int | None, int | None]] = []
        # The diff line each row belongs to, -1 above the first
        self._anchors: list[int] = []
        self.cursor = 0
        self.scroll = 0
        self.gap: tuple[int, int] | None = None
        self.marks_from_below = (False, False)
        self.has_moved = False

    def show(self, change: Change, compare: tuple[str, str], path: str, width: int,
             context: int | None, whitespace: str = 'none', search: str = '') -> None:
        """Renders the diff of a file, unless it is rendered already. The
        threads come with arrange(), call it after this. The matches of a
        search are marked.

        Args:
            change: the change.
            compare: the (left, right) versions, see versions_to_compare().
            path: the file's path.
            width: the pane's width.
            context: the number of common lines around each change, None
                for the whole file.
            whitespace: one of WHITESPACE_MODES.
            search: the searched text, '' for none.
        """
        if self._path != path:
            self.expanded_gaps = set()
        # The lines with threads stay visible, see file_diff_rows().
        threads = file_diff.thread_lines(change, *compare, path)
        key = (path, versions_to_compare(change, *compare), width, context,
               frozenset(self.expanded_gaps), whitespace, threads, search)
        if key == self._key:
            return
        is_other_file = self._path != path
        self._key = key
        self._path = path
        old, new, paths = diff_endpoints(change, *compare, path=path)
        rows = file_diff_rows(change, *compare, path=path, context=context,
                              whitespace=whitespace, shown=threads)
        old_path = old_path_of(change, *compare, paths[0]) or paths[0]
        from_below, partly = rebase_lines(change, *compare, path=path)
        marks = ((from_below[0], partly[0]), (from_below[1], partly[1]))
        rendered: list[RenderedRow]
        if rows is None:
            rendered = [([('Binary file, not shown.', SECONDARY, -1, 0)], (None, None), False,
                          False, None, None)]
        elif not rows:
            rendered = [([('No differences in this file.', SECONDARY, -1, 0)], (None, None),
                          False, False, None, None)]
        else:
            if self.expanded_gaps and any(row.n_skipped for row in rows):
                rows = expand_gaps(rows, self.expanded_gaps, file_lines(old, old_path) or [],
                                   file_lines(new, paths[0]) or [])
            colours = (self._colours(old, old_path), self._colours(new, paths[0]))
            rendered = rendered_rows(rows, width, colours, marks, search)
        self._closed_gaps = [(row.old.number or 0, row.new.number or 0, row.n_skipped)
                             for row in rows or () if row.n_skipped]
        self._sources = (old, old_path, new, paths[0])
        self._gaps = [entry[4] for entry in rendered]
        self._texts = [entry[5] for entry in rendered]
        self._diff_lines = [segments for segments, *_ in rendered]
        previous_numbers = self._diff_numbers
        self._diff_numbers = [numbers for _, numbers, *_ in rendered]
        self._continues = [continues for _, _, continues, *_ in rendered]
        self._changed = [entry[3] for entry in rendered]
        has_from_below, has_partly = (any(numbers[side] in marks[side][type_index]
                                          for numbers in self._diff_numbers for side in (0, 1))
                                      for type_index in (0, 1))
        self.marks_from_below = (has_from_below, has_partly)
        self.has_moved = any(row.old.is_moved or row.new.is_moved for row in rows or ())
        if not is_other_file:
            if self.rows and not self._keep:
                self._keep = (self.rows[self.cursor], self._anchors[self.cursor])
            if self._keep:
                self._keep = self._renumbered(self._keep, previous_numbers)
        if is_other_file:
            first = next((index for index, numbers in enumerate(self._diff_numbers)
                          if any(numbers)), 0)
            self._keep = (('diff', first), first)
            # A new file starts at its top, file threads and all.
            self.cursor = self.scroll = 0
            self._is_from_top = True
        self._threads_key = None

    def _colours(self, version: str, path: str) -> list[list[CharacterColour]] | None:
        """Colours a file's version, kept per version, so moving around
        doesn't run bat again.

        Args:
            version: the version, a commit or tree.
            path: the file's path.

        Returns:
            The syntax colours, see highlighted().
        """
        key = (version, path)
        if key not in self._colour_cache:
            self._colour_cache[key] = highlighted(file_lines(version, path) or [], path)
        return self._colour_cache[key]

    def _renumbered(self, keep: KeptRow,
                    previous_numbers: list[tuple[int | None, int | None]]) -> KeptRow:
        """Moves a kept row to the new diff lines, by its line numbers. With
        more or less context, the same line has another index.

        Args:
            keep: the kept row.
            previous_numbers: the line numbers of the diff lines before.

        Returns:
            The kept row in the new diff lines.
        """
        def new_index(index: int) -> int:
            """Finds a diff line's index in the new diff lines.

            Args:
                index: the index before.

            Returns:
                The index of the same line, or the first after it.
            """
            if not 0 <= index < len(previous_numbers):
                return index
            old, new = previous_numbers[index]
            if old is None and new is None:
                return min(index, max(len(self._diff_numbers) - 1, 0))
            column, line = (1, new) if new is not None else (0, old or 0)
            for other, numbers in enumerate(self._diff_numbers):
                number = numbers[column]
                if number is not None and number >= line:
                    return other
            return max(len(self._diff_numbers) - 1, 0)

        row, anchor = keep
        if 'diff' == row[0]:
            row = ('diff', new_index(row[1]))
        return row, new_index(anchor) if anchor >= 0 else anchor

    def forget(self) -> None:
        """Makes the next show() render again, e.g. after a reload."""
        self._key = None

    def arrange(self, blocks: ThreadBlocks, threads_key: object) -> None:
        """Lays out the rows: the diff lines, each followed by the threads on
        it, file threads first. Does nothing while threads_key stays the
        same.

        Args:
            blocks: the threads' rows by their place, see ThreadBlocks.
                Emptied.
            threads_key: what the threads depend on.
        """
        if threads_key == self._threads_key:
            return
        self._threads_key = threads_key
        if self.rows and not self._keep:
            self._keep = (self.rows[self.cursor], self._anchors[self.cursor])
        offset = self.cursor - self.scroll
        self.rows, self.lines, self.numbers, self._anchors = [], [], [], []

        def add_threads(place: tuple[str, int] | None, anchor: int) -> None:
            """Adds the rows of the threads at a place.

            Args:
                place: (side, line), None for the whole file.
                anchor: the diff line the threads belong to, -1 for none.
            """
            for number, thread_lines in blocks.pop(place, []):
                for row, parts in enumerate(thread_lines):
                    self.rows.append(('thread', number, row))
                    self.lines.append(parts)
                    self.numbers.append((None, None))
                    self._anchors.append(anchor)

        add_threads(None, -1)
        # Threads on lines the diff doesn't show come next, so none gets lost.
        for place in sorted(place for place in blocks
                            if place is not None and place not in self.shown_places()):
            add_threads(place, -1)
        # A line's threads come below its last row, after the rows it
        # wrapped onto.
        line: tuple[int | None, int | None, int] | None = None
        for index, (old, new) in enumerate(self._diff_numbers):
            if line and not self._continues[index]:
                self._add_line_threads(add_threads, line)
            if not self._continues[index]:
                line = (old, new, index)
            self.rows.append(('diff', index))
            self.lines.append(self._diff_lines[index])
            self.numbers.append((old, new))
            self._anchors.append(index)
        if line:
            self._add_line_threads(add_threads, line)
        self._place_cursor(offset)

    @staticmethod
    def _add_line_threads(add_threads: Any, line: tuple[int | None, int | None, int]) -> None:
        """Adds the threads of a diff line, those of the new side first.

        Args:
            add_threads: the function that adds the threads at a place.
            line: (old number, new number, index) of the line.
        """
        if line[1] is not None:
            add_threads(('new', line[1]), line[2])
        if line[0] is not None:
            add_threads(('old', line[0]), line[2])

    def shown_places(self) -> set[tuple[str, int]]:
        """Lists the places the rendered diff shows.

        Returns:
            (side, line) per shown line.
        """
        return ({('new', new) for _, new in self._diff_numbers if new is not None}
                | {('old', old) for old, _ in self._diff_numbers if old is not None})

    def _place_cursor(self, offset: int) -> None:
        """Puts the cursor back on the kept row: the same one, else the
        thread's first row, else the diff line it was below.

        Args:
            offset: the cursor's row in the pane before, kept if possible.
        """
        if not self._keep:
            self.cursor = self.stop_of(min(self.cursor, max(len(self.rows) - 1, 0)))
            return
        row, anchor = self._keep
        self._keep = None
        candidates = [row, ('diff', max(anchor, 0))]
        if 'thread' == row[0]:
            candidates.insert(1, ('thread', row[1], 0))
        index = next((self.rows.index(candidate) for candidate in candidates
                      if candidate in self.rows), 0)
        self.cursor = index = self.stop_of(index)
        self.scroll = 0 if self._is_from_top else max(0, index - max(offset, 0))
        self._is_from_top = False

    def thread_at(self, index: int) -> int | None:
        """Finds the thread a row belongs to.

        Args:
            index: the row.

        Returns:
            The thread's number, None on diff rows.
        """
        row = self.rows[index] if index < len(self.rows) else ('diff', 0)
        return row[1] if 'thread' == row[0] else None

    def thread_end(self, number: int) -> int | None:
        """Finds the last row of a thread.

        Args:
            number: the thread's number.

        Returns:
            The row, None if the thread isn't shown.
        """
        indexes = [index for index, row in enumerate(self.rows)
                   if 'thread' == row[0] and number == row[1]]
        return indexes[-1] if indexes else None

    def gap_at(self, index: int) -> int | None:
        """Finds the gap at a row.

        Args:
            index: the row.

        Returns:
            The gap's first old line, None on other rows.
        """
        row = self.rows[index] if index < len(self.rows) else ('thread',)
        return self._gaps[row[1]] if 'diff' == row[0] and row[1] < len(self._gaps) else None

    def segments(self, index: int, is_cursor_row: bool) -> list[tuple[str, int]]:
        """Finds the look of a row. The cursor row gets thin lines in the
        columns reserved left and right of the code, a highlighted line
        number and a subtle tint, on the cursor's side only.

        Args:
            index: the row.
            is_cursor_row: whether the row belongs to the cursor's stop.

        Returns:
            (text, curses attribute) per part.
        """
        result = []
        parts = self.lines[index]
        separator = next((number for number, part in enumerate(parts)
                          if HALF_SEPARATOR == part[0]), None)
        is_thread = index < len(self.rows) and 'thread' == self.rows[index][0]
        for number, (text, foreground, background, styles) in enumerate(parts):
            on_side = separator is None or (number < separator if 'old' == self.side
                                            else number > separator)
            mark = is_cursor_row and on_side
            if foreground in (LEFT_MARK, RIGHT_MARK):
                result.append((('▏' if LEFT_MARK == foreground else '▕') if mark else ' ',
                               self._pairs.attribute(FRAME_COLOUR if curses.COLORS >= 256
                                                     else -1, -1)))
                continue
            if LINE_NUMBER == foreground:
                result.append((text, (self._pairs.attribute(255, LINE_NUMBER_BACKGROUND)
                                      if curses.COLORS >= 256 else curses.A_REVERSE)
                               | curses.A_BOLD if mark
                               else self._pairs.attribute(SECONDARY, -1)))
                continue
            if is_thread and not text.strip(HALF_SEPARATOR + ' '):
                # A thread is tinted, not the blanks around it.
                on_side = False
            if is_cursor_row and on_side:
                background = cursor_tint(background)
            result.append((text, self._pairs.attribute(foreground, background) | styles))
        return result

    def cursor_line_attribute(self) -> int:
        """Finds the look of the empty rest of the cursor row.

        Returns:
            The curses attribute.
        """
        if curses.COLORS < 256:
            return curses.A_REVERSE
        return self._pairs.attribute(-1, CURSOR_LINE_GREY)

    def go_to_new_line(self, line: int, height: int) -> None:
        """Puts the cursor on a line of the new version, or the first line
        after it that the diff shows, else the last row (vim's NG).

        Args:
            line: the line's number.
            height: the pane's height.
        """
        index = next((index for index, (_, new) in enumerate(self.numbers)
                      if new is not None and new >= line), len(self.rows) - 1)
        self.set_cursor(index, height)

    def stop_of(self, index: int) -> int:
        """Finds the cursor stop of a row: a thread is one stop, at its
        first row. Every other row is a stop of its own.

        Args:
            index: the row.

        Returns:
            The stop's first row.
        """
        while 0 < index < len(self.rows) and 'thread' == self.rows[index][0] \
                and self.rows[index][2] > 0:
            index -= 1
        return index

    def stop_end(self, index: int) -> int:
        """Finds the last row of the stop at a row.

        Args:
            index: the row.

        Returns:
            The stop's last row.
        """
        row = self.rows[index] if index < len(self.rows) else ('diff', 0)
        if 'thread' != row[0]:
            return index
        while index + 1 < len(self.rows) and self.rows[index + 1][:2] == row[:2]:
            index += 1
        return index

    def _stops(self) -> list[int]:
        """Lists the rows the cursor stops on.

        Returns:
            The rows.
        """
        return [index for index, row in enumerate(self.rows) if 'thread' != row[0] or 0 == row[2]]

    def set_cursor(self, index: int, height: int, is_upwards: bool = False) -> None:
        """Puts the cursor on the stop of a row and scrolls it into view,
        all of it if it fits. A taller thread shows its start, or its end
        when the cursor came from below, to read on from there.

        Args:
            index: the row.
            height: the pane's height.
            is_upwards: whether the cursor came from below.
        """
        self.cursor = self.stop_of(max(0, min(index, len(self.rows) - 1)))
        end = self.stop_end(self.cursor)
        if is_upwards and end - self.cursor + 1 > height:
            self.scroll = max(end - height + 1, 0)
        elif self.cursor < self.scroll:
            self.scroll = self.cursor
        elif end >= self.scroll + height:
            self.scroll = max(min(self.cursor, end - height + 1), 0)

    def move_cursor(self, n_steps: int, height: int) -> None:
        """Moves the cursor by stops, a whole thread being one, and
        scrolls so it stays visible. In a thread taller than the pane, each
        step first scrolls one row through it, until its end (or start)
        shows.

        Args:
            n_steps: how many stops, down if positive, up if negative.
            height: the pane's height.
        """
        if not self.rows:
            self.cursor = 0
            return
        if not n_steps:
            self.set_cursor(self.cursor, height)
            return
        direction = 1 if n_steps > 0 else -1
        for _ in range(abs(n_steps)):
            start = self.stop_of(min(self.cursor, len(self.rows) - 1))
            end = self.stop_end(start)
            if end - start + 1 > height:
                if direction > 0 and end >= self.scroll + height:
                    self.scroll += 1
                    continue
                if direction < 0 and start < self.scroll:
                    self.scroll -= 1
                    continue
            stops = self._stops()
            position = stops.index(start) if start in stops else 0
            self.set_cursor(stops[max(0, min(position + direction, len(stops) - 1))], height,
                            direction < 0)

    def scroll_rows(self, n_rows: int, height: int) -> None:
        """Scrolls the view by rows without moving the cursor, unless it
        would leave the pane: then it goes to the nearest stop shown.

        Args:
            n_rows: how many rows, down if positive, up if negative.
            height: the pane's height.
        """
        if not self.rows:
            return
        self.scroll = max(0, min(self.scroll + n_rows, max(len(self.rows) - height, 0)))
        if self.stop_end(self.cursor) < self.scroll:
            self.cursor = next((stop for stop in self._stops() if stop >= self.scroll),
                               self.cursor)
        elif self.cursor >= self.scroll + height:
            self.cursor = self.stop_of(self.scroll + height - 1)

    def is_tall(self, height: int) -> bool:
        """Tells whether the cursor's stop is a thread taller than the pane.

        Args:
            height: the pane's height.

        Returns:
            True for a taller thread.
        """
        return self.stop_end(self.cursor) - self.cursor + 1 > height

    def scroll_by(self, n_rows: int, height: int) -> None:
        """Scrolls the view by rows, the cursor along at the same place of
        the pane (^d, ^u, the wheel). A thread taller than the pane is read
        this way, the cursor stays on it while any of it is shown.

        Args:
            n_rows: how many rows, down if positive, up if negative.
            height: the pane's height.
        """
        if not self.rows:
            return
        offset = max(self.cursor - self.scroll, 0)
        scroll = max(0, min(self.scroll + n_rows, max(len(self.rows) - height, 0)))
        if scroll == self.scroll:
            # At the end: the cursor moves instead.
            self.move_cursor(1 if n_rows > 0 else -1, height)
            return
        self.scroll = scroll
        self.cursor = self.stop_of(max(0, min(scroll + offset, len(self.rows) - 1)))

    def is_on_cursor(self, index: int) -> bool:
        """Tells whether a row belongs to the cursor's stop.

        Args:
            index: the row.

        Returns:
            True if it does.
        """
        return self.cursor <= index <= self.stop_end(self.cursor)

    def _jump(self, index: int, height: int) -> None:
        """Puts the cursor on a row, a third down the pane.

        Args:
            index: the row.
            height: the pane's height.
        """
        self.set_cursor(index, height)
        self.scroll = max(0, min(self.cursor - height // 3, len(self.rows) - height))

    def go_to_line(self, line: int, side: str, height: int) -> None:
        """Puts the cursor on a line of the old or new version, if the diff
        shows it.

        Args:
            line: the line's number.
            side: old or new.
            height: the pane's height.
        """
        column = 0 if 'old' == side else 1
        for index, numbers in enumerate(self.numbers):
            if line == numbers[column]:
                self._jump(index, height)
                return

    def go_to_chunk(self, is_forward: bool, height: int) -> bool:
        """Puts the cursor on the first row of the next or previous changed
        lines, like Gerrit's n and p.

        Args:
            is_forward: whether to go down, else up.
            height: the pane's height.

        Returns:
            Whether there are such lines.
        """
        starts = [index for index, row in enumerate(self.rows)
                  if 'diff' == row[0] and self._changed[row[1]]
                  and not (row[1] and self._changed[row[1] - 1])]
        return self._jump_to_one_of(starts, is_forward, height)

    def open_gaps_with(self, search: str) -> None:
        """Opens the gaps of common lines with a match of a search. show()
        renders them next time.

        Args:
            search: the searched text.
        """
        if not self._closed_gaps or self._sources is None:
            return
        old, old_path, new, path = self._sources
        self.expanded_gaps |= gaps_with_matches(
            self._closed_gaps, file_lines(old, old_path) or [],
            file_lines(new, path) or [], search)

    def go_to_match(self, search: str, is_forward: bool, height: int,
                    mode: str = 'shown') -> bool | None:
        """Puts the cursor on the next or previous line with a match of a
        search, on the side of the match, after the cursor's side of its
        line. A thread is one place. Goes on from the other end.

        Args:
            search: the searched text.
            is_forward: whether to go down, else up.
            height: the pane's height.
            mode: what the search looks at, one of SEARCH_MODES: the diff
                lines, the threads or both.

        Returns:
            None if nothing matches, else whether it went on from the other
            end.
        """
        line = self.cursor
        while 0 < line and 'diff' == self.rows[line][0] and self._continues[self.rows[line][1]]:
            line -= 1
        here = (line, 'old' != self.side)
        places: list[tuple[int, int]] = []
        if 'comments' != mode:
            places += [(index, side) for index, row in enumerate(self.rows)
                       if 'diff' == row[0] and self._texts[row[1]]
                       for side in (0, 1)
                       if search_matches((self._texts[row[1]] or ('', ''))[side], search)]
        if mode in ('all', 'comments'):
            places += sorted({(self.stop_of(index), 1) for index, row in enumerate(self.rows)
                              if 'thread' == row[0] and search_matches(
                                  ''.join(part[0] for part in self.lines[index]), search)})
            places.sort()
        if not places:
            return None
        after = ([place for place in places if place > here] if is_forward
                 else [place for place in places if place < here][::-1])
        index, side = after[0] if after else places[0 if is_forward else -1]
        self._jump(index, height)
        if 'diff' == self.rows[index][0]:
            self.side = ('old', 'new')[side]
        return not after

    def go_to_next_thread(self, is_forward: bool, height: int,
                          numbers: set[int] | None = None, starts_at_edge: bool = False) -> bool:
        """Puts the cursor on the first row of the next or previous thread.

        Args:
            is_forward: whether to go down, else up.
            height: the pane's height.
            numbers: the threads to look at, None for all.
            starts_at_edge: whether to start from the top (or the bottom)
                instead of the cursor.

        Returns:
            Whether there is such a thread.
        """
        starts = [index for index, row in enumerate(self.rows)
                  if 'thread' == row[0] and 0 == row[2] and (numbers is None or row[1] in numbers)]
        if starts_at_edge and starts:
            self._jump(starts[0] if is_forward else starts[-1], height)
            return True
        return self._jump_to_one_of(starts, is_forward, height)

    def _jump_to_one_of(self, indexes: list[int], is_forward: bool, height: int) -> bool:
        """Jumps to the nearest of some rows after or before the cursor.

        Args:
            indexes: the rows.
            is_forward: whether to look after the cursor, else before.
            height: the pane's height.

        Returns:
            Whether there is such a row.
        """
        candidates = ([index for index in indexes if index > self.cursor] if is_forward
                      else [index for index in indexes if index < self.cursor])
        if not candidates:
            return False
        self._jump(candidates[0] if is_forward else candidates[-1], height)
        return True

    def go_to_thread(self, number: int, height: int) -> bool:
        """Puts the cursor on a thread's first row.

        Args:
            number: the thread's number.
            height: the pane's height.

        Returns:
            Whether the thread is shown.
        """
        if ('thread', number, 0) not in self.rows:
            return False
        self._jump(self.rows.index(('thread', number, 0)), height)
        return True

    def make_room_below(self, index: int, height: int, n_rows: int) -> None:
        """Scrolls so that rows below a row are visible, for the comment
        box. Moves the cursor along if it would leave the pane.

        Args:
            index: the row.
            height: the pane's height.
            n_rows: how many rows below it must be visible.
        """
        if index - self.scroll > height - n_rows - 1:
            self.scroll = max(0, index - max(height - n_rows - 1, height // 3))
        if not self.scroll <= self.cursor < self.scroll + height:
            self.cursor = self.stop_of(index)

    def line_end(self, index: int) -> int:
        """Finds the last row of the line at a row, after the rows it
        wrapped onto.

        Args:
            index: the row.

        Returns:
            The line's last row.
        """
        while (index + 1 < len(self.rows) and 'diff' == self.rows[index + 1][0]
               and self._continues[self.rows[index + 1][1]]):
            index += 1
        return index

    def line_above(self, index: int) -> int | None:
        """Finds the first row of the line above the line at a row: the
        nearest one with a line number, over threads, gaps and the rows a
        line wrapped onto.

        Args:
            index: the row.

        Returns:
            The row, None at the top.
        """
        while (0 < index < len(self.rows) and 'diff' == self.rows[index][0]
               and self._continues[self.rows[index][1]]):
            index -= 1
        index -= 1
        while index >= 0:
            if 'diff' == self.rows[index][0] and self.numbers[index] != (None, None):
                while (0 < index and 'diff' == self.rows[index][0]
                       and self._continues[self.rows[index][1]]):
                    index -= 1
                return index
            index -= 1
        return None

    def cursor_place(self) -> tuple[int, str] | None:
        """Finds the cursor's line.

        Returns:
            (line, side), on the cursor's side if the line exists there,
            else on the other side. None on rows without numbers.
        """
        index = self.cursor
        # A wrapped row belongs to the line above.
        while (0 < index < len(self.rows) and 'diff' == self.rows[index][0]
               and self._continues[self.rows[index][1]]):
            index -= 1
        old, new = self.numbers[index] if self.numbers else (None, None)
        if 'old' == self.side and old is not None:
            return old, 'old'
        if new is not None:
            return new, 'new'
        if old is not None:
            return old, 'old'
        return None


def in_half(parts: list[Segment], side: str, left_width: int) -> list[Segment]:
    """Moves the segments of a thread's row into one half of the diff: the
    other half blank, the halves' separator between them.

    Args:
        parts: the row's segments.
        side: the half, old or new.
        left_width: the width of the old half.

    Returns:
        The row's segments.
    """
    # The separator is joined to the blanks, so the row counts as one whole
    # for the cursor's side.
    if 'old' == side:
        used = sum(len(part[0]) for part in parts)
        return parts + [(' ' * max(left_width - used, 0) + HALF_SEPARATOR, SECONDARY, -1, 0)]
    return [(' ' * left_width + HALF_SEPARATOR, SECONDARY, -1, 0)] + parts


def patch_set_count(n_patch_sets: int) -> str:
    """Writes the header's count of patch sets, which the p key lists.

    Args:
        n_patch_sets: the number of patch sets.

    Returns:
        Like "3 patch sets".
    """
    return f'{n_patch_sets} patch set{"" if 1 == n_patch_sets else "s"}'


def thread_rows(comment: Comment, width: int, is_expanded: bool, note: str = '',
                origin: str = '', patch_sets: Sequence[int] = ()) -> list[list[Segment]]:
    """Draws a thread as rows of the diff: a box with a border in the
    status' colour and a tinted inside, the reviewer's and the author's
    names in two colours.

    Args:
        comment: the thread.
        width: the rows' width.
        is_expanded: whether the thread is unfolded. Folded it is one row
            with the start of the first message.
        note: what goes into the title, as that the thread's line isn't
            shown. '' for nothing.
        origin: the older patch set the thread was made on, like PS2, also
            in the folded title. '' for none.
        patch_sets: the patch set of each message, see
            message_patch_sets().

    Returns:
        The segments of each row.
    """
    status = comment_label(comment)
    origin = f' {origin}' if origin else ''
    colour = STATUS_COLOURS['draft' if comment.is_draft() else comment.status]
    tint = THREAD_TINT if curses.COLORS >= 256 else -1
    # The first column and the last are the cursor's frame, see LEFT_MARK.
    pad: list[Segment] = [(' ', LEFT_MARK, -1, 0), (' ' * (_THREAD_INDENT - 1), -1, -1, 0)]
    # Indent, the two borders and one space: the rows are as wide as the pane
    inner = max(width - _THREAD_INDENT - 4, 20)
    title = f' #{comment.number} {status}{origin} '

    def title_parts(background: int) -> list[Segment]:
        """Draws the title. Only the status is bold: if everything is bold,
        nothing is.

        Args:
            background: the title's background.

        Returns:
            The title's segments.
        """
        return [(f' #{comment.number} ', colour, background, 0),
                (status, colour, background, curses.A_BOLD),
                (origin, SECONDARY, background, 0), (' ', colour, background, 0)]

    if not is_expanded:
        first = message_text(comment.messages[0]).split('\n')[0] if comment.messages else ''
        rest = f' {first}'[:max(inner - len(title), 0)]
        return [[*pad, ('▸', colour, tint, 0), *title_parts(tint),
                 (rest.ljust(inner + 1 - len(title)), SECONDARY, tint, 0),
                 (' ', RIGHT_MARK, -1, 0)]]
    border = (colour, -1, 0)
    # A long note, like the line the thread was on, goes on below the
    # title, so it can always be read whole.
    n_note_columns = max(inner - len(title) - 3, 0)
    note_rows: list[str] = []
    if note and len(note) > n_note_columns:
        # Whole onto rows of its own, never split between title and body
        note_rows = textwrap.wrap(note, inner)
        note = ''
    extra = f' {note} ' if note else ''
    top = '─' * max(inner - len(title) - len(extra), 0)
    rows: list[list[Segment]] = [[*pad, ('┌─', *border), *title_parts(-1),
             (extra, SECONDARY, -1, 0), (top + '┐', *border)]]
    for line in note_rows:
        rows.append([*pad, ('│', *border), (' ' + line.ljust(inner), SECONDARY, -1, 0),
                     ('│', *border)])
    message_index = -1
    for message in comment.entries():
        if not isinstance(message, Message):
            # A move by hand: a quiet line, no message
            for line in textwrap.wrap(move_text(message), max(inner - 1, 10)):
                rows.append([*pad, ('│', *border),
                             (' ' + line.ljust(inner), SECONDARY, tint, 0), ('│', *border)])
            continue
        message_index += 1
        is_reviewer = REVIEWER == message.author
        name = ('Reviewer' if is_reviewer else 'Author') + (' (draft)' if not message.sent else '')
        name_colour = (STATUS_COLOURS['draft'] if not message.sent
                       else tone(REVIEWER_COLOURS if is_reviewer else AUTHOR_COLOURS))
        # The patch set the message belongs to, like Gerrit's "Patchset 3"
        patch_set = (f' · PS{patch_sets[message_index]}'
                     if message_index < len(patch_sets) and patch_sets[message_index] else '')
        # The reviewer's own messages, the viewer's, get a bar in their
        # colour on the left, so they stand out from the author's.
        bar: Segment = ('▎', name_colour, tint, 0) if is_reviewer else (' ', -1, tint, 0)
        # The name on a row of its own, like Gerrit, the text below it at
        # the box's whole width
        rows.append([*pad, ('│', *border), bar, (name, name_colour, tint, 0),
                     (patch_set.ljust(inner - len(name)), SECONDARY, tint, 0), ('│', *border)])
        lines = [line for paragraph in message_text(message).split('\n')
                 for line in textwrap.wrap(paragraph, max(inner - 1, 10)) or ['']]
        for line in lines:
            rows.append([*pad, ('│', *border), bar, (' ' + line.ljust(inner - 1), -1, tint, 0),
                         ('│', *border)])
    rows.append([*pad, ('└' + '─' * (inner + 1) + '┘', *border)])
    return [row + [(' ', RIGHT_MARK, -1, 0)] for row in rows]
