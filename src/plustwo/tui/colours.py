# SPDX-License-Identifier: Zlib
"""Colours of the interface, made readable on the terminal's background."""

import curses
from typing import TYPE_CHECKING

from plustwo.diff import contrast as diff_contrast
from plustwo.diff.contrast import MIN_CONTRAST, Rgb

if TYPE_CHECKING:
    from plustwo.tui.interface import Interface


# A colour: a palette index (-1 the default, the negative ones below the
# marks of SECONDARY and the cursor's frame) or (red, green, blue)
Colour = int | Rgb

# Each review state, mapped to its colour
STATE_COLOURS = {'New': 0, 'In-Review': 3, 'Submitted': 2}

# Each thread status, and draft, mapped to its colour
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

# Each vote value, mapped to its colour
VOTE_COLOURS = {2: 2, 1: 2, -1: 1, -2: 1}

# Background of thread boxes
THREAD_TINT = 238

# The names' colours and the header's background: (256 colours, 8 colours)
REVIEWER_COLOURS = (81, 6)
AUTHOR_COLOURS = (214, 3)
HEADER_BACKGROUND = (237, 0)


def tone(colours: tuple[int, int]) -> int:
    """Picks the colour for this terminal.

    Args:
        colours: (colour with 256 colours, colour with 8 colours).

    Returns:
        The palette index.
    """
    return colours[0] if curses.COLORS >= 256 else colours[1]


def vote_badge(interface: 'Interface', label: str, value: int,
               is_required: bool = False) -> tuple[str, int] | None:
    """Draws a vote as a coloured badge, like "CR +2" on green.

    Args:
        interface: the interface, for its colour pairs.
        label: the label's short name, like CR.
        value: the vote, 0 for none.
        is_required: whether the label is required, which shows "?"
            without a vote.

    Returns:
        (text, curses attribute). None without a vote, unless the label
        is required.
    """
    if not value and not is_required:
        return None
    text = f' {label} {value:+d} ' if value else f' {label} ? '
    background = {2: 2, 1: 2, -1: 1, -2: 1}.get(value, 8)
    return text, interface.colour_pairs.attribute(0 if value else 15, background) | curses.A_BOLD


# The 6 levels of each channel in the 256 colour palette's 6x6x6 cube
_CUBE_LEVELS = (0, 95, 135, 175, 215, 255)

# Colour pairs of the interface itself use the numbers below this one.
FIRST_DIFF_PAIR = 16

# Difference between the strongest and weakest channel from which a true
# colour counts as coloured, not grey
_MIN_HUE = 30


def palette_index(red: int, green: int, blue: int) -> int:
    """Finds the nearest of the 256 palette colours to a true colour.

    Args:
        red: the red channel, 0 to 255.
        green: the green channel, 0 to 255.
        blue: the blue channel, 0 to 255.

    Returns:
        The palette index, 16 to 255.
    """
    def nearest_level(value: int) -> int:
        """Finds the cube level nearest to a channel's value.

        Args:
            value: the channel's value, 0 to 255.

        Returns:
            The level's index, 0 to 5.
        """
        return min(range(6), key=lambda index: abs(_CUBE_LEVELS[index] - value))

    cube = [nearest_level(value) for value in (red, green, blue)]
    cube_colour = [_CUBE_LEVELS[index] for index in cube]
    grey_index = min(23, max(0, round((red + green + blue) / 3 - 8) // 10))
    grey = 8 + 10 * grey_index

    def distance(colour: list[int] | tuple[int, int, int]) -> int:
        """Measures how far a colour is from the true colour.

        Args:
            colour: the colour's channels.

        Returns:
            The squared distance.
        """
        return sum((a - b) ** 2 for a, b in zip(colour, (red, green, blue)))

    # A coloured tone stays coloured even where a grey is nearer, e.g. the
    # dark green of added lines must not turn grey.
    has_hue = max(red, green, blue) - min(red, green, blue) >= _MIN_HUE
    if not has_hue and distance((grey, grey, grey)) < distance(cube_colour):
        return 232 + grey_index
    return 16 + 36 * cube[0] + 6 * cube[1] + cube[2]


# The foreground of secondary text, like line numbers and ids, in segments
# and colour pairs: a grey readable on the background, see secondary().
# Not curses' A_DIM, which many terminals draw almost invisible.
SECONDARY = -2


# The grey of secondary text, the setting tui.dim-colour
DIM_COLOUR = 246


def contrast(first: Colour, second: Colour) -> float:
    """Computes the contrast ratio of two colours, like WCAG.

    Args:
        first: a colour.
        second: another colour.

    Returns:
        The ratio, 1 to 21.
    """
    return diff_contrast.contrast(rgb_of(first), rgb_of(second))


def secondary(background: Colour = -1) -> int:
    """Finds the colour of secondary text on a background.

    Args:
        background: the background, -1 for the terminal's.

    Returns:
        DIM_COLOUR, lighter or darker on the ramp of greys where the
        background needs it. -1, normal text, with fewer than 256 colours.
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
_BASIC_COLOURS: tuple[Rgb, ...] = (
    (0, 0, 0), (205, 0, 0), (0, 205, 0), (205, 205, 0), (0, 0, 238), (205, 0, 205),
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
TERMINAL_BACKGROUND: Rgb = (0, 0, 0)


# The cursor's tint: this share of white mixed into a row's background
CURSOR_TINT = 0.07


def rgb_of(colour: Colour) -> Rgb:
    """Finds the channels of a colour.

    Args:
        colour: the colour, not -1.

    Returns:
        (red, green, blue).
    """
    return colour if isinstance(colour, tuple) else palette_colour(colour)


def curses_colour(colour: Colour) -> int:
    """Writes a colour as curses takes it.

    Args:
        colour: the colour, -1 for the default.

    Returns:
        -1 for the default. In true colour an RGB number, where a direct
        colour terminal reads 0 to 7 as the basic colours, which stay. Else
        a palette index.
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


def is_light(colour: Colour) -> bool:
    """Tells whether dark text reads better on a colour than light text.

    Args:
        colour: the colour.

    Returns:
        True for a light colour.
    """
    return contrast(colour, (0, 0, 0)) > contrast(colour, (255, 255, 255))


def cursor_tint(background: Colour) -> Colour:
    """Finds the cursor's background over a row's.

    Args:
        background: the row's background, -1 for the terminal's.

    Returns:
        The background mixed CURSOR_TINT towards white in true colour,
        towards black on a light one. The same without true colour, where
        only the side lines mark the cursor.
    """
    if not TRUE_COLOUR:
        return background
    colour = TERMINAL_BACKGROUND if -1 == background else rgb_of(background)
    red, green, blue = colour
    if is_light(colour):
        return (round(red * (1 - CURSOR_TINT)), round(green * (1 - CURSOR_TINT)),
                round(blue * (1 - CURSOR_TINT)))
    return (round(red + (255 - red) * CURSOR_TINT), round(green + (255 - green) * CURSOR_TINT),
            round(blue + (255 - blue) * CURSOR_TINT))


def palette_colour(index: int) -> Rgb:
    """Finds the channels of a palette colour.

    Args:
        index: the palette index, 0 to 255.

    Returns:
        (red, green, blue).
    """
    if index < 16:
        return _BASIC_COLOURS[index]
    if index >= 232:
        grey = 8 + 10 * (index - 232)
        return grey, grey, grey
    index -= 16
    return (_CUBE_LEVELS[index // 36], _CUBE_LEVELS[index // 6 % 6], _CUBE_LEVELS[index % 6])


# Backgrounds of removed and added lines: (line, changed characters). The
# greens are the ones the reviewer's delta used, the reds delta's defaults.
REMOVED_BACKGROUNDS = (palette_index(0x3f, 0x00, 0x01), palette_index(0x90, 0x10, 0x11))
ADDED_BACKGROUNDS = (palette_index(0x1f, 0x4d, 0x2a), palette_index(0x2f, 0x7a, 0x40))

# The shades of the diff in true colour, light and strong, the exact ones
# of delta and the web page
TRUE_SHADES = {'removed': ((0x4b, 0x11, 0x13), (0xa3, 0x26, 0x2a)),
               'added': ((0x1f, 0x4d, 0x2a), (0x2f, 0x7a, 0x40)),
               'below': ((0x0c, 0x2d, 0x6b), (0x1f, 0x4f, 0x9e)),
               'partly': ((0x2e, 0x14, 0x52), (0x4f, 0x2a, 0x85)),
               'moved': ((0x0f, 0x3b, 0x3a), (0x1d, 0x6b, 0x68))}

# The backgrounds of removed and added lines for terminals with fewer
# colours: red and green
REMOVED_BASIC = 1
ADDED_BASIC = 2
