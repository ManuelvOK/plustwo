# SPDX-License-Identifier: Zlib
"""Contrast of colours, readable text colours."""

import functools


# The least contrast of text on its background, WCAG's for normal text.
# Secondary text and syntax colours on diff shades are lifted to it.
MIN_CONTRAST = 4.5


def hex_rgb(text):
    """(red, green, blue) of a colour written #rrggbb."""
    return tuple(int(text[index:index + 2], 16) for index in (1, 3, 5))


def rgb_hex(colour):
    """A colour (red, green, blue) as #rrggbb."""
    return '#' + ''.join(f'{value:02x}' for value in colour)


def luminance(colour):
    """The relative luminance of a colour (red, green, blue), like WCAG."""
    channels = [value / 255 for value in colour]
    channels = [value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
                for value in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(first, second):
    """The contrast ratio of two colours (red, green, blue), 1 to 21."""
    lighter_one, darker_one = sorted((luminance(first), luminance(second)), reverse=True)
    return (lighter_one + 0.05) / (darker_one + 0.05)


@functools.lru_cache(maxsize=4096)
def readable(foreground, background, minimum=MIN_CONTRAST):
    """A text colour (red, green, blue) with at least the minimum contrast
    on the background: the colour itself if it has it, else mixed towards
    white (on a dark background) or black (on a light one) just enough. So
    a syntax colour keeps its hue and only gets lighter or darker.
    """
    if contrast(foreground, background) >= minimum:
        return foreground
    target = (255, 255, 255) if contrast((255, 255, 255), background) > contrast(
        (0, 0, 0), background) else (0, 0, 0)
    for step in range(1, 21):
        share = step / 20
        mixed = tuple(round(value + (end - value) * share)
                      for value, end in zip(foreground, target))
        if contrast(mixed, background) >= minimum:
            return mixed
    return target


def readable_hex(foreground, background, minimum=MIN_CONTRAST):
    """readable() for colours written #rrggbb."""
    return rgb_hex(readable(hex_rgb(foreground), hex_rgb(background), minimum))


def readable_colours(colours, backgrounds):
    """For the web page: each background (name -> #rrggbb) with the syntax
    colours (#rrggbb) that need lifting on it, see readable(): name ->
    {colour: lifted colour}.
    """
    result = {}
    for name, background in backgrounds.items():
        lifted = {colour: readable_hex(colour, background) for colour in colours}
        result[name] = {colour: other for colour, other in lifted.items() if other != colour}
    return result
