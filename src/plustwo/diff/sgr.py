# SPDX-License-Identifier: Zlib
"""Terminal colour sequences (SGR) as segments."""

import re


# An SGR escape sequence (colours and styles) in a highlighter's output
SGR_PATTERN = re.compile(r'\x1b\[([0-9;]*)m|\x1b\[[0-9;]*[A-Za-z]')


SGR_STYLES = {1: 'bold', 3: 'italic', 4: 'underline'}


SGR_RESETS = {22: 'bold', 23: 'italic', 24: 'underline'}


# xterm's 16 basic colours, for highlighters that use them
BASIC_HEX = ('#000000', '#cd0000', '#00cd00', '#cdcd00', '#0000ee', '#cd00cd', '#00cdcd',
             '#e5e5e5', '#7f7f7f', '#ff0000', '#00ff00', '#ffff00', '#5c5cff', '#ff00ff',
             '#00ffff', '#ffffff')


def palette_hex(index):
    """The colour of an entry of the 256 colour palette, as #rrggbb."""
    if index < 16:
        return BASIC_HEX[index]
    if index >= 232:
        grey = 8 + 10 * (index - 232)
        return f'#{grey:02x}{grey:02x}{grey:02x}'
    levels = (0, 95, 135, 175, 215, 255)
    index -= 16
    return '#' + ''.join(f'{levels[value]:02x}' for value in
                         (index // 36, index // 6 % 6, index % 6))


def sgr_segments(line):
    """Splits a line of terminal output into (text, colour, styles) parts:
    colour as #rrggbb or None for the default, styles a tuple of bold,
    italic and underline.
    """
    segments = []
    colour, styles = None, set()
    position = 0
    for match in SGR_PATTERN.finditer(line):
        if match.start() > position:
            segments.append((line[position:match.start()], colour, tuple(sorted(styles))))
        position = match.end()
        if match.group(1) is None:
            continue
        codes = [int(code) if code else 0 for code in match.group(1).split(';')]
        index = 0
        while index < len(codes):
            code = codes[index]
            index += 1
            if 0 == code:
                colour, styles = None, set()
            elif code in SGR_STYLES:
                styles.add(SGR_STYLES[code])
            elif code in SGR_RESETS:
                styles.discard(SGR_RESETS[code])
            elif 30 <= code <= 37 or 90 <= code <= 97:
                colour = BASIC_HEX[code - 30 if code < 90 else code - 82]
            elif 39 == code:
                colour = None
            elif code in (38, 48) and index < len(codes):
                if 5 == codes[index] and index + 1 < len(codes):
                    value, index = palette_hex(codes[index + 1]), index + 2
                elif 2 == codes[index] and index + 3 < len(codes):
                    value = '#' + ''.join(f'{part:02x}' for part in codes[index + 1:index + 4])
                    index += 4
                else:
                    value, index = None, len(codes)
                if 38 == code:
                    colour = value
    if position < len(line):
        segments.append((line[position:], colour, tuple(sorted(styles))))
    return segments
