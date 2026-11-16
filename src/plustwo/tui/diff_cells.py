# SPDX-License-Identifier: Zlib
"""The cells of diff rows: colours, tabs, marks of search matches."""

import curses

from plustwo.config.settings import setting
from plustwo.diff.contrast import readable
from plustwo.diff.rows import ADDED, CONTEXT, REMOVED
from plustwo.diff.search import search_matches
from plustwo.diff.syntax import syntax_segments
from plustwo.tui import colours as tui_colours
from plustwo.tui.colours import (
    ADDED_BACKGROUNDS, ADDED_BASIC, FROM_BELOW_BACKGROUNDS, FROM_BELOW_BASIC, LEFT_MARK,
    LINE_NUMBER, MOVED_BACKGROUNDS, MOVED_BASIC, PARTLY_BACKGROUNDS, PARTLY_BASIC,
    REMOVED_BACKGROUNDS, REMOVED_BASIC, RIGHT_MARK, SECONDARY, TRUE_SHADES, rgb_of)


# How many lines around each change the diff shows, + and - step through
# them. None is the whole file.
CONTEXT_STEPS = (3, 10, 25, 100, None)


TAB_WIDTH = 4


# Columns between the halves of a row: a line
HALF_SEPARATOR = '│'


STYLE_ATTRIBUTES = {'bold': curses.A_BOLD, 'italic': curses.A_ITALIC,
                    'underline': curses.A_UNDERLINE}


def highlighted(lines, path):
    """Colours of the lines of a file as syntax highlighting gives them:
    for each line a list of (foreground, styles) per character. None
    without bat, or if its output doesn't fit the lines.
    """
    segments = syntax_segments(lines, path, setting('tui.theme'))
    if segments is None:
        return None
    colours = []
    for line in segments:
        cells = []
        for text, colour, styles in line:
            # Kept as RGB, curses_colour() rounds them without true colour
            foreground = -1 if colour is None else tuple(
                int(colour[index:index + 2], 16) for index in (1, 3, 5))
            attributes = 0
            for style in styles:
                attributes |= STYLE_ATTRIBUTES[style]
            cells += [(foreground, attributes)] * len(text)
        colours.append(cells)
    return colours


def half_cells(half, colours, marks, search=''):
    """The characters of one half of a row as (character, foreground,
    background, styles), tabs expanded and control characters made visible.
    colours are the line's syntax colours or None. marks are the sets of
    line numbers (from below, partly from below) on this side. The matches
    of a search are shown reversed.
    """
    if half.kind in (REMOVED, ADDED):
        if half.number in marks[0] or half.number in marks[1]:
            partly = half.number not in marks[0]
            backgrounds = ((PARTLY_BASIC,) * 2 if partly else (FROM_BELOW_BASIC,) * 2) \
                if curses.COLORS < 256 else (PARTLY_BACKGROUNDS if partly
                                             else FROM_BELOW_BACKGROUNDS)
        elif half.moved:
            backgrounds = (MOVED_BASIC,) * 2 if curses.COLORS < 256 else MOVED_BACKGROUNDS
        elif curses.COLORS < 256:
            backgrounds = ((REMOVED_BASIC,) if REMOVED == half.kind else (ADDED_BASIC,)) * 2
        else:
            backgrounds = REMOVED_BACKGROUNDS if REMOVED == half.kind else ADDED_BACKGROUNDS
        if tui_colours.TRUE_COLOUR:
            backgrounds = TRUE_SHADES[
                'partly' if half.number in marks[1] and half.number not in marks[0]
                else 'below' if half.number in marks[0] else 'moved' if half.moved
                else half.kind]
    else:
        backgrounds = (-1, -1)
    if half.whole:
        # New text all over gets the strong shade, see Half.
        backgrounds = (backgrounds[1],) * 2
    changed = set()
    for begin, end in half.edits:
        changed.update(range(begin, end))
    cells = []
    text = half.text[:-1] if half.text.endswith('\r') else half.text
    found = set()
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
            cells += [(' ', foreground, background, styles)] * (TAB_WIDTH - len(cells) % TAB_WIDTH)
        elif ord(character) < 32 or 127 == ord(character):
            cells.append(('·', SECONDARY, background, styles))
        else:
            cells.append((character, foreground, background, styles))
    return cells, backgrounds[0]


def grouped(cells):
    """Joins cells of the same colours into (text, foreground, background,
    styles) segments.
    """
    segments = []
    for character, *attributes in cells:
        if segments and segments[-1][1:] == tuple(attributes):
            segments[-1] = (segments[-1][0] + character, *attributes)
        else:
            segments.append((character, *attributes))
    return segments


# Where a search looks, for the prompt and the messages, by SEARCH_MODES
SEARCH_PLACES = {'shown': 'the lines shown', 'file': 'the whole file',
                 'all': 'the file and its comments', 'comments': 'the comments'}


def search_question(mode):
    """The search prompt, with where it looks."""
    return f'Search {SEARCH_PLACES[mode]} (Tab: elsewhere):'


def marked_segments(segments, search):
    """Segments (text, foreground, background, styles) of a row with the
    matches of a search reversed.
    """
    text = ''.join(segment[0] for segment in segments)
    found = set()
    for begin, end in search_matches(text, search):
        found.update(range(begin, end))
    if not found:
        return segments
    cells = [(character, foreground, background, styles | (curses.A_REVERSE if index in found
                                                            else 0))
             for index, (character, foreground, background, styles) in enumerate(
                 (character, *rest) for part, *rest in segments for character in part)]
    return grouped(cells)


def rendered_rows(rows, width, colours, marks, search=''):
    """Draws side by side rows into screen rows like Gerrit's diff: old
    number, old text, new number, new text. A long line wraps onto more
    rows. Returns [(segments, (old number, new number), continues,
    changed, gap, texts)], where continues says that the row goes on with
    the line of the row above, changed that it has removed or added lines,
    gap is the first old line of a gap row, else None, and texts the (old,
    new) text of the line on its first row, else None. Matches of a search
    are marked.
    """
    digits = max([4] + [len(str(half.number)) for row in rows for half in (row.old, row.new)
                        if half.number])
    half_width = max((width - len(HALF_SEPARATOR)) // 2, digits + 4)
    # Two columns for the cursor's frame, see LEFT_MARK
    text_width = half_width - digits - 1 - 2
    result = []
    for row in rows:
        if row.skipped:
            label = f' {row.skipped} common line{"s" if row.skipped > 1 else ""} '
            result.append(([(label.center(width, '┄'), SECONDARY, -1, 0)],
                           (None, None), False, False, row.old.number, None))
            continue
        changed = (CONTEXT, CONTEXT) != (row.old.kind, row.new.kind)
        halves = []
        for half, side in ((row.old, 0), (row.new, 1)):
            line_colours = (colours[side][half.number - 1]
                            if half.number and colours[side] else None)
            cells, background = half_cells(half, line_colours, marks[side], search)
            pieces = [cells[start:start + text_width]
                      for start in range(0, len(cells), text_width)] or [[]]
            halves.append((half, pieces, background))
        for piece in range(max(len(pieces) for _, pieces, _ in halves)):
            segments = []
            for side, (half, pieces, background) in enumerate(halves):
                number = f'{half.number:>{digits}} ' if half.number and 0 == piece else \
                    ' ' * (digits + 1)
                segments.append((number, LINE_NUMBER if 0 == piece and half.number else SECONDARY,
                                 -1, 0))
                segments.append((' ', LEFT_MARK, -1, 0))
                cells = pieces[piece] if piece < len(pieces) else []
                padding = text_width - len(cells)
                fill = background if piece < len(pieces) else -1
                segments += grouped(cells + [(' ', -1, fill, 0)] * padding)
                segments.append((' ', RIGHT_MARK, -1, 0))
                if 0 == side:
                    segments.append((HALF_SEPARATOR, SECONDARY, -1, 0))
            numbers = (row.old.number, row.new.number) if 0 == piece else (None, None)
            texts = (row.old.text, row.new.text) if 0 == piece else None
            result.append((segments, numbers, 0 != piece, changed, None, texts))
    return result
