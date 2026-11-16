# SPDX-License-Identifier: Zlib
"""Contrast of colours, readable text colours."""

import functools


# A colour as (red, green, blue), each 0 to 255
Rgb = tuple[int, int, int]

# The least contrast of text on its background, WCAG's for normal text.
# Secondary text and syntax colours on diff shades are lifted to it.
MIN_CONTRAST = 4.5

# How many steps readable() takes at most towards white or black
_N_MIXING_STEPS = 20


def _hex_rgb(text: str) -> Rgb:
    """Reads a colour written #rrggbb.

    Args:
        text: the colour, like #1c1c1c.

    Returns:
        The colour as (red, green, blue).
    """
    return int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16)


def _rgb_hex(colour: Rgb) -> str:
    """Writes a colour as #rrggbb.

    Args:
        colour: the colour as (red, green, blue).

    Returns:
        The colour, like #1c1c1c.
    """
    return '#' + ''.join(f'{value:02x}' for value in colour)


def _luminance(colour: Rgb) -> float:
    """Computes the relative _luminance of a colour, like WCAG.

    Args:
        colour: the colour as (red, green, blue).

    Returns:
        The _luminance, 0 for black to 1 for white.
    """
    channels = [value / 255 for value in colour]
    channels = [value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
                for value in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(first: Rgb, second: Rgb) -> float:
    """Computes the contrast ratio of two colours, like WCAG.

    Args:
        first: a colour as (red, green, blue).
        second: another colour as (red, green, blue).

    Returns:
        The ratio, 1 to 21, the same in either order.
    """
    lighter_luminance, darker_luminance = sorted((_luminance(first), _luminance(second)),
                                                 reverse=True)
    return (lighter_luminance + 0.05) / (darker_luminance + 0.05)


@functools.lru_cache(maxsize=4096)
def readable(foreground: Rgb, background: Rgb, minimum: float = MIN_CONTRAST) -> Rgb:
    """Finds a text colour with at least a minimum contrast on a background:
    the colour itself if it has it, else the colour mixed towards white (on
    a dark background) or black (on a light one) just enough. So a syntax
    colour keeps its hue and only gets lighter or darker.

    Args:
        foreground: the text's colour as (red, green, blue).
        background: the background's colour as (red, green, blue).
        minimum: the least contrast ratio.

    Returns:
        The readable colour as (red, green, blue).
    """
    if contrast(foreground, background) >= minimum:
        return foreground
    white, black = (255, 255, 255), (0, 0, 0)
    target = white if contrast(white, background) > contrast(black, background) else black
    for step in range(1, _N_MIXING_STEPS + 1):
        share = step / _N_MIXING_STEPS
        red, green, blue = (round(value + (end - value) * share)
                            for value, end in zip(foreground, target))
        if contrast((red, green, blue), background) >= minimum:
            return red, green, blue
    return target


def _readable_hex(foreground: str, background: str, minimum: float = MIN_CONTRAST) -> str:
    """Finds a readable text colour, like readable(), for colours written
    #rrggbb.

    Args:
        foreground: the text's colour, like #ff0000.
        background: the background's colour, like #1c1c1c.
        minimum: the least contrast ratio.

    Returns:
        The readable colour, like #ff4c4c.
    """
    return _rgb_hex(readable(_hex_rgb(foreground), _hex_rgb(background), minimum))


def readable_colours(colours: list[str],
                     backgrounds: dict[str, str]) -> dict[str, dict[str, str]]:
    """Finds the syntax colours that need lifting on each background, for
    the web page, see readable().

    Args:
        colours: syntax colours, written #rrggbb.
        backgrounds: each background's name, mapped to its colour.

    Returns:
        Each background's name, mapped to the colours that need lifting on
        it, each mapped to its lifted colour.
    """
    lifted_by_background = {}
    for name, background in backgrounds.items():
        lifted = {colour: _readable_hex(colour, background) for colour in colours}
        lifted_by_background[name] = {colour: lifted_colour
                                      for colour, lifted_colour in lifted.items()
                                      if lifted_colour != colour}
    return lifted_by_background
