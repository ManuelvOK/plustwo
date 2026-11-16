# SPDX-License-Identifier: Zlib
"""Colours of the interface, made readable on the terminal's background."""

import curses

from plustwo.diff import contrast as diff_contrast
from plustwo.diff.contrast import MIN_CONTRAST


STATE_COLOURS = {'New': 0, 'In-Review': 3, 'Submitted': 2}


STATUS_COLOURS = {'unresolved': 3, 'resolved': 2, 'draft': 5}


# Backgrounds of diff lines that differ because the changes below changed:
# (line, changed words) of lines from below and of lines partly from below,
# and for terminals with fewer colours
FROM_BELOW_BACKGROUNDS = (18, 26)


PARTLY_BACKGROUNDS = (54, 92)


FROM_BELOW_BASIC = 4


PARTLY_BASIC = 5


# Moved lines, like git diff --color-moved, in teal
MOVED_BACKGROUNDS = (23, 30)


MOVED_BASIC = 6


# Colours of vote values
VOTE_COLOURS = {2: 2, 1: 2, -1: 1, -2: 1}


# Background of thread boxes, and the names' colours: (256 colours, 8 colours)
THREAD_TINT = 238


REVIEWER_COLOURS = (81, 6)


AUTHOR_COLOURS = (214, 3)


HEADER_BACKGROUND = (237, 0)


def tone(colours):
    """The colour of a (256 colours, 8 colours) pair for this terminal."""
    return colours[0] if curses.COLORS >= 256 else colours[1]


def vote_badge(interface, label, value, required=False):
    """A vote as a coloured badge, like "CR +2" on green. None without
    a vote, unless the label is required.
    """
    if not value and not required:
        return None
    text = f' {label} {value:+d} ' if value else f' {label} ? '
    background = {2: 2, 1: 2, -1: 1, -2: 1}.get(value, 8)
    return text, interface.colour_pairs.attribute(0 if value else 15, background) | curses.A_BOLD


# The 6 levels of each channel in the 256 colour palette's 6x6x6 cube
CUBE_LEVELS = (0, 95, 135, 175, 215, 255)


# Colour pairs of the interface itself use the numbers below this one.
FIRST_DIFF_PAIR = 16


# Difference between the strongest and weakest channel from which a true
# colour counts as coloured, not grey
MIN_HUE = 30


def palette_index(red, green, blue):
    """The nearest of the 256 palette colours to a true colour."""
    def nearest_level(value):
        return min(range(6), key=lambda index: abs(CUBE_LEVELS[index] - value))

    cube = [nearest_level(value) for value in (red, green, blue)]
    cube_colour = [CUBE_LEVELS[index] for index in cube]
    grey_index = min(23, max(0, round((red + green + blue) / 3 - 8) // 10))
    grey = 8 + 10 * grey_index

    def distance(colour):
        return sum((a - b) ** 2 for a, b in zip(colour, (red, green, blue)))

    # A coloured tone stays coloured even where a grey is nearer, e.g. the
    # dark green of added lines must not turn grey.
    has_hue = max(red, green, blue) - min(red, green, blue) >= MIN_HUE
    if not has_hue and distance((grey, grey, grey)) < distance(cube_colour):
        return 232 + grey_index
    return 16 + 36 * cube[0] + 6 * cube[1] + cube[2]


# The foreground of secondary text, like line numbers and ids, in segments
# and colour pairs: a grey readable on the background, see secondary().
# Not curses' A_DIM, which many terminals draw almost invisible.
SECONDARY = -2


# The grey of secondary text, the setting tui.dim-colour
DIM_COLOUR = 246


def contrast(first, second):
    """The contrast ratio of two colours (palette or RGB), 1 to 21."""
    return diff_contrast.contrast(rgb_of(first), rgb_of(second))


def secondary(background=-1):
    """The colour of secondary text on a background (-1 the default):
    DIM_COLOUR, lighter on the ramp of greys where the background needs
    it. -1, normal text, with fewer than 256 colours.
    """
    if curses.COLORS < 256:
        return -1
    colour = DIM_COLOUR
    if -1 == background:
        background = TERMINAL_BACKGROUND
    # Lighter on a dark background, darker on a light one
    step = -1 if is_light(background) else 1
    while 232 < colour + step <= 255 and 232 <= colour <= 255 \
            and contrast(colour, background) < MIN_CONTRAST:
        colour += step
    return colour


# Approximate colours of the 16 basic palette entries (xterm's defaults).
# The terminal may use others, good enough for lightening them a bit.
BASIC_COLOURS = ((0, 0, 0), (205, 0, 0), (0, 205, 0), (205, 205, 0), (0, 0, 238), (205, 0, 205),
                 (0, 205, 205), (229, 229, 229), (127, 127, 127), (255, 0, 0), (0, 255, 0),
                 (255, 255, 0), (92, 92, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255))


# Background of the cursor line where the diff has none: a dark grey
CURSOR_LINE_GREY = 236


# The cursor's frame: thin lines left and right of its half's code, in
# columns every row reserves, so nothing moves with the cursor. Segments
# mark those columns with these foregrounds, the line number with
# LINE_NUMBER, which the cursor row highlights.
LEFT_MARK = -3


RIGHT_MARK = -4


LINE_NUMBER = -5


FRAME_COLOUR = 250


LINE_NUMBER_BACKGROUND = 237


# Whether the interface draws in true colour, see true_colour_terminal().
# Colours are palette indexes or (red, green, blue): in true colour all go
# to the terminal as RGB, else as the nearest of the 256.
TRUE_COLOUR = False


# The terminal's own background, for the cursor's tint and the contrast of
# secondary text on it: the setting tui.background, else what the terminal
# says (see query_background()), else black
TERMINAL_BACKGROUND = (0, 0, 0)


# The cursor's tint: this share of white mixed into a row's background
CURSOR_TINT = 0.07


def rgb_of(colour):
    """(red, green, blue) of a palette index or an RGB colour."""
    return colour if isinstance(colour, tuple) else palette_colour(colour)


def curses_colour(colour):
    """A colour as curses takes it: -1 is the default. In true colour an
    RGB number, where a direct colour terminal reads 0 to 7 as the basic
    colours, which stay. Else a palette index.
    """
    if -1 == colour:
        return -1
    if not TRUE_COLOUR:
        return palette_index(*colour) if isinstance(colour, tuple) else colour
    if not isinstance(colour, tuple) and colour < 8:
        return colour
    red, green, blue = rgb_of(colour)
    # Below 8 would be a basic colour: almost black is near enough.
    return max(red << 16 | green << 8 | blue, 8)


def is_light(colour):
    """Whether dark text reads better on a colour than light text."""
    return contrast(colour, (0, 0, 0)) > contrast(colour, (255, 255, 255))


def cursor_tint(background):
    """The cursor's background over a row's: CURSOR_TINT towards white in
    true colour, towards black on a light one. The same without true
    colour, where only the side lines mark it.
    """
    if not TRUE_COLOUR:
        return background
    colour = TERMINAL_BACKGROUND if -1 == background else rgb_of(background)
    if is_light(colour):
        return tuple(round(value * (1 - CURSOR_TINT)) for value in colour)
    return tuple(round(value + (255 - value) * CURSOR_TINT) for value in colour)


def palette_colour(index):
    """(red, green, blue) of a palette colour."""
    if index < 16:
        return BASIC_COLOURS[index]
    if index >= 232:
        grey = 8 + 10 * (index - 232)
        return grey, grey, grey
    index -= 16
    return tuple(CUBE_LEVELS[level] for level in (index // 36, index // 6 % 6, index % 6))


# Backgrounds of removed and added lines: (line, changed characters). The
# greens are the ones the reviewer's delta used, the reds delta's defaults.
REMOVED_BACKGROUNDS = (palette_index(0x3f, 0x00, 0x01), palette_index(0x90, 0x10, 0x11))


# The shades of the diff in true colour, light and strong, the exact ones
# of delta and the web page
TRUE_SHADES = {'removed': ((0x4b, 0x11, 0x13), (0xa3, 0x26, 0x2a)),
               'added': ((0x1f, 0x4d, 0x2a), (0x2f, 0x7a, 0x40)),
               'below': ((0x0c, 0x2d, 0x6b), (0x1f, 0x4f, 0x9e)),
               'partly': ((0x2e, 0x14, 0x52), (0x4f, 0x2a, 0x85)),
               'moved': ((0x0f, 0x3b, 0x3a), (0x1d, 0x6b, 0x68))}


ADDED_BACKGROUNDS = (palette_index(0x1f, 0x4d, 0x2a), palette_index(0x2f, 0x7a, 0x40))


# The same for terminals with fewer colours: red and green
REMOVED_BASIC = 1


ADDED_BASIC = 2
