# SPDX-License-Identifier: Zlib
"""The cells of diff rows: colours, tabs, marks of search matches."""

import curses
from collections.abc import Sequence

from plustwo.config.settings import setting
from plustwo.diff.contrast import readable
from plustwo.diff.rows import ADDED, CONTEXT, REMOVED, Half, Row
from plustwo.diff.search import search_matches
from plustwo.diff.syntax import syntax_segments
from plustwo.tui import colours as tui_colours
from plustwo.tui.colours import (
    ADDED_BACKGROUNDS, ADDED_BASIC, FROM_BELOW_BACKGROUNDS, FROM_BELOW_BASIC, LEFT_MARK,
    LINE_NUMBER, MOVED_BACKGROUNDS, MOVED_BASIC, PARTLY_BACKGROUNDS, PARTLY_BASIC,
    REMOVED_BACKGROUNDS, REMOVED_BASIC, RIGHT_MARK, SECONDARY, TRUE_SHADES, Colour, rgb_of)


# A character or a run of characters with one look: (text, foreground,
# background, curses attributes like bold)
Segment = tuple[str, Colour, Colour, int]

# The look of a character from syntax highlighting: (foreground, curses
# attributes)
CharacterColour = tuple[Colour, int]

# The line numbers of one side that differ because of changes below: (from
# below, partly from below)
SideMarks = tuple[set[int], set[int]]

# A screen row of the diff, see rendered_rows(): (segments, (old number,
# new number), whether it continues the line above, whether it has
# removed or added lines, the first old line of a gap row or None, (old
# text, new text) on a line's first row or None)
RenderedRow = tuple[list[Segment], tuple[int | None, int | None], bool, bool, int | None,
                    tuple[str, str] | None]

# How many lines around each change the diff shows, + and - step through
# them. None is the whole file.
CONTEXT_STEPS = (3, 10, 25, 100, None)

# The columns a tab takes up to
_TAB_WIDTH = 4

# Columns between the halves of a row: a line
HALF_SEPARATOR = '│'

# Each style of syntax highlighting, mapped to its curses attribute
_STYLE_ATTRIBUTES = {'bold': curses.A_BOLD, 'italic': curses.A_ITALIC,
                     'underline': curses.A_UNDERLINE}

# Where a search looks, for the prompt and the messages, by SEARCH_MODES
SEARCH_PLACES = {'shown': 'the lines shown', 'file': 'the whole file',
                 'all': 'the file and its comments', 'comments': 'the comments'}


def highlighted(lines: Sequence[str], path: str) -> list[list[CharacterColour]] | None:
    """Colours the lines of a file with syntax highlighting.

    Args:
        lines: the file's lines.
        path: the file's path, from which the language is picked.

    Returns:
        For each line, the look of each character. None without bat, or if
        its output doesn't fit the lines.
    """
    segments = syntax_segments(lines, path, setting('tui.theme'))
    if segments is None:
        return None
    colours = []
    for line in segments:
        cells: list[CharacterColour] = []
        for text, colour, styles in line:
            # Kept as RGB, curses_colour() rounds them without true colour
            foreground: Colour = -1 if colour is None else (
                int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16))
            attributes = 0
            for style in styles:
                attributes |= _STYLE_ATTRIBUTES[style]
            cells += [(foreground, attributes)] * len(text)
        colours.append(cells)
    return colours


def _half_backgrounds(half: Half, marks: SideMarks) -> tuple[Colour, Colour]:
    """Finds the shades of one half of a row.

    Args:
        half: the half.
        marks: the line numbers on this side from below, see SideMarks.

    Returns:
        (background of the line, background of its changed characters).
    """
    if half.type not in (REMOVED, ADDED):
        backgrounds: tuple[Colour, Colour] = (-1, -1)
    else:
        if half.number in marks[0] or half.number in marks[1]:
            is_partly = half.number not in marks[0]
            backgrounds = ((PARTLY_BASIC,) * 2 if is_partly else (FROM_BELOW_BASIC,) * 2) \
                if curses.COLORS < 256 else (PARTLY_BACKGROUNDS if is_partly
                                             else FROM_BELOW_BACKGROUNDS)
        elif half.is_moved:
            backgrounds = (MOVED_BASIC,) * 2 if curses.COLORS < 256 else MOVED_BACKGROUNDS
        elif curses.COLORS < 256:
            backgrounds = (REMOVED_BASIC,) * 2 if REMOVED == half.type else (ADDED_BASIC,) * 2
        else:
            backgrounds = REMOVED_BACKGROUNDS if REMOVED == half.type else ADDED_BACKGROUNDS
        if tui_colours.TRUE_COLOUR:
            backgrounds = TRUE_SHADES[
                'partly' if half.number in marks[1] and half.number not in marks[0]
                else 'below' if half.number in marks[0] else 'moved' if half.is_moved
                else half.type]
    if half.is_whole:
        # New text all over gets the strong shade, see Half.
        backgrounds = (backgrounds[1], backgrounds[1])
    return backgrounds


def _half_cells(half: Half, colours: list[CharacterColour] | None, marks: SideMarks,
                search: str = '') -> tuple[list[Segment], Colour]:
    """Splits one half of a row into its characters, tabs expanded and
    control characters made visible. The matches of a search are shown
    reversed.

    Args:
        half: the half.
        colours: the line's syntax colours, None for none.
        marks: the line numbers on this side from below, see SideMarks.
        search: the searched text, '' for none.

    Returns:
        (a segment per character, the line's background).
    """
    backgrounds = _half_backgrounds(half, marks)
    changed: set[int] = set()
    for begin, end in half.edits:
        changed.update(range(begin, end))
    cells: list[Segment] = []
    text = half.text[:-1] if half.text.endswith('\r') else half.text
    found: set[int] = set()
    for begin, end in search_matches(text, search):
        found.update(range(begin, end))
    for index, character in enumerate(text):
        foreground, styles = colours[index] if colours and index < len(colours) else (-1, 0)
        if index in found:
            styles |= curses.A_REVERSE
        background = backgrounds[index in changed]
        if isinstance(foreground, tuple) and -1 != background:
            # Syntax colours readable on the diff's shades
            foreground = readable(foreground, rgb_of(background))
        if '\t' == character:
            cells += [(' ', foreground, background, styles)] * (_TAB_WIDTH
                                                                 - len(cells) % _TAB_WIDTH)
        elif ord(character) < 32 or 127 == ord(character):
            cells.append(('·', SECONDARY, background, styles))
        else:
            cells.append((character, foreground, background, styles))
    return cells, backgrounds[0]


def grouped(cells: list[Segment]) -> list[Segment]:
    """Joins neighbouring cells of the same look.

    Args:
        cells: the cells, see Segment.

    Returns:
        The segments.
    """
    segments: list[Segment] = []
    for character, foreground, background, styles in cells:
        if segments and segments[-1][1:] == (foreground, background, styles):
            segments[-1] = (segments[-1][0] + character, foreground, background, styles)
        else:
            segments.append((character, foreground, background, styles))
    return segments


def search_question(mode: str) -> str:
    """Writes the search prompt.

    Args:
        mode: where the search looks, one of SEARCH_MODES.

    Returns:
        The prompt, with where the search looks.
    """
    return f'Search {SEARCH_PLACES[mode]} (Tab: elsewhere):'


def marked_segments(segments: list[Segment], search: str) -> list[Segment]:
    """Marks the matches of a search in a row.

    Args:
        segments: the row's segments.
        search: the searched text.

    Returns:
        The segments with the matches reversed.
    """
    text = ''.join(segment[0] for segment in segments)
    found: set[int] = set()
    for begin, end in search_matches(text, search):
        found.update(range(begin, end))
    if not found:
        return segments
    characters = [(character, foreground, background, styles)
                  for part, foreground, background, styles in segments for character in part]
    cells: list[Segment] = [
        (character, foreground, background, styles | (curses.A_REVERSE if index in found else 0))
        for index, (character, foreground, background, styles) in enumerate(characters)]
    return grouped(cells)


def rendered_rows(rows: list[Row], width: int,
                  colours: tuple[list[list[CharacterColour]] | None,
                                 list[list[CharacterColour]] | None],
                  marks: tuple[SideMarks, SideMarks], search: str = '') -> list[RenderedRow]:
    """Draws side by side rows into screen rows like Gerrit's diff: old
    number, old text, new number, new text. A long line wraps onto more
    rows. Matches of a search are marked.

    Args:
        rows: the diff's rows, see side_by_side_rows().
        width: the screen rows' width.
        colours: (old, new) syntax colours, see highlighted(), None for a
            side without.
        marks: (old, new) line numbers from below, see SideMarks.
        search: the searched text, '' for none.

    Returns:
        The screen rows, see RenderedRow.
    """
    digits = max([4] + [len(str(half.number)) for row in rows for half in (row.old, row.new)
                        if half.number])
    half_width = max((width - len(HALF_SEPARATOR)) // 2, digits + 4)
    # Two columns for the cursor's frame, see LEFT_MARK
    text_width = half_width - digits - 1 - 2
    result: list[RenderedRow] = []
    for row in rows:
        if row.n_skipped:
            label = f' {row.n_skipped} common line{"s" if row.n_skipped > 1 else ""} '
            result.append(([(label.center(width, '┄'), SECONDARY, -1, 0)],
                           (None, None), False, False, row.old.number, None))
            continue
        changed = (CONTEXT, CONTEXT) != (row.old.type, row.new.type)
        halves = []
        for half, side in ((row.old, 0), (row.new, 1)):
            side_colours = colours[side]
            line_colours = (side_colours[half.number - 1]
                            if half.number and side_colours else None)
            cells, background = _half_cells(half, line_colours, marks[side], search)
            pieces = [cells[start:start + text_width]
                      for start in range(0, len(cells), text_width)] or [[]]
            halves.append((half, pieces, background))
        for piece in range(max(len(pieces) for _, pieces, _ in halves)):
            segments: list[Segment] = []
            for side, (half, pieces, background) in enumerate(halves):
                number = f'{half.number:>{digits}} ' if half.number and 0 == piece else \
                    ' ' * (digits + 1)
                segments.append((number, LINE_NUMBER if 0 == piece and half.number else SECONDARY,
                                 -1, 0))
                segments.append((' ', LEFT_MARK, -1, 0))
                cells = pieces[piece] if piece < len(pieces) else []
                padding = text_width - len(cells)
                fill: Colour = background if piece < len(pieces) else -1
                segments += grouped(cells + [(' ', -1, fill, 0)] * padding)
                segments.append((' ', RIGHT_MARK, -1, 0))
                if 0 == side:
                    segments.append((HALF_SEPARATOR, SECONDARY, -1, 0))
            numbers = (row.old.number, row.new.number) if 0 == piece else (None, None)
            texts = (row.old.text, row.new.text) if 0 == piece else None
            result.append((segments, numbers, 0 != piece, changed, None, texts))
    return result
