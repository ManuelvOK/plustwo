# SPDX-License-Identifier: Zlib
"""A rectangle of the screen."""


class Area:
    """The part of the screen a view may draw into: rows 0 to height-1."""

    def __init__(self, screen, top, height, width):
        self.screen = screen
        self.top = top
        self.height = height
        self.width = width
