# SPDX-License-Identifier: Zlib
"""Terminal colour sequences (SGR) as segments."""

import re


# A part of a line with one look: (text, colour as #rrggbb or None for the
# default, styles as a sorted tuple of bold, italic and underline)
Segment = tuple[str, str | None, tuple[str, ...]]

# An SGR escape sequence (colours and styles) in a highlighter's output
_SGR_PATTERN = re.compile(r'\x1b\[([0-9;]*)m|\x1b\[[0-9;]*[A-Za-z]')

# The SGR codes that turn styles on, mapped to the styles
_SGR_STYLES = {1: 'bold', 3: 'italic', 4: 'underline'}

# The SGR codes that turn styles off, mapped to the styles
_SGR_RESETS = {22: 'bold', 23: 'italic', 24: 'underline'}

# xterm's 16 basic colours, for highlighters that use them
_BASIC_HEX = ('#000000', '#cd0000', '#00cd00', '#cdcd00', '#0000ee', '#cd00cd', '#00cdcd',
             '#e5e5e5', '#7f7f7f', '#ff0000', '#00ff00', '#ffff00', '#5c5cff', '#ff00ff',
             '#00ffff', '#ffffff')

# The levels of each channel in the 6x6x6 colour cube of the 256 colours
_CUBE_LEVELS = (0, 95, 135, 175, 215, 255)


def _palette_hex(index: int) -> str:
    """Finds the colour of an entry of the 256 colour palette.

    Args:
        index: the entry, 0 to 255.

    Returns:
        The colour, written #rrggbb.
    """
    if index < 16:
        return _BASIC_HEX[index]
    if index >= 232:
        grey = 8 + 10 * (index - 232)
        return f'#{grey:02x}{grey:02x}{grey:02x}'
    index -= 16
    return '#' + ''.join(f'{_CUBE_LEVELS[value]:02x}' for value in
                         (index // 36, index // 6 % 6, index % 6))


def sgr_segments(line: str) -> list[Segment]:
    """Splits a line of terminal output into parts with one look each.

    Args:
        line: the line, with SGR escape sequences.

    Returns:
        The segments, see Segment. Escape sequences other than SGR are
        dropped.
    """
    segments = []
    colour: str | None = None
    styles: set[str] = set()
    position = 0
    for match in _SGR_PATTERN.finditer(line):
        if match.start() > position:
            segments.append((line[position:match.start()], colour, tuple(sorted(styles))))
        position = match.end()
        if match.group(1) is None:
            continue
        codes = [int(code) if code else 0 for code in match.group(1).split(';')]
        colour, styles = _applied_codes(codes, colour, styles)
    if position < len(line):
        segments.append((line[position:], colour, tuple(sorted(styles))))
    return segments


def _applied_codes(codes: list[int], colour: str | None,
                   styles: set[str]) -> tuple[str | None, set[str]]:
    """Applies the codes of one SGR sequence to a look.

    Args:
        codes: the sequence's numbers.
        colour: the colour before, #rrggbb or None for the default.
        styles: the styles before. Changed in place.

    Returns:
        (colour, styles) after the sequence.
    """
    index = 0
    while index < len(codes):
        code = codes[index]
        index += 1
        if 0 == code:
            colour, styles = None, set()
        elif code in _SGR_STYLES:
            styles.add(_SGR_STYLES[code])
        elif code in _SGR_RESETS:
            styles.discard(_SGR_RESETS[code])
        elif 30 <= code <= 37 or 90 <= code <= 97:
            colour = _BASIC_HEX[code - 30 if code < 90 else code - 82]
        elif 39 == code:
            colour = None
        elif code in (38, 48) and index < len(codes):
            # 38 sets the text's colour, 48 the background's, which is
            # skipped. 5 picks a palette entry, 2 gives red, green and blue.
            value: str | None
            if 5 == codes[index] and index + 1 < len(codes):
                value, index = _palette_hex(codes[index + 1]), index + 2
            elif 2 == codes[index] and index + 3 < len(codes):
                value = '#' + ''.join(f'{part:02x}' for part in codes[index + 1:index + 4])
                index += 4
            else:
                value, index = None, len(codes)
            if 38 == code:
                colour = value
    return colour, styles
