# SPDX-License-Identifier: Zlib
"""The frame around a pane of the change screen."""

import curses
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from plustwo.tui.interface import Interface

# A pane's inside on the screen, without the frame: (top, left, height,
# width)
PaneBox = tuple[int, int, int, int]


def draw_frame(interface: 'Interface', box: PaneBox, title: str, is_focused: bool,
               n_rows_above: int = 0, n_rows_below: int = 0) -> None:
    """Draws the frame around a pane, with its title. When the content
    doesn't fit, ↑N at the top border's end says N rows are above, ↓N at the
    bottom border's end N rows below. The title is cut for ↑N.

    Args:
        interface: the interface, which draws.
        box: the pane's inside.
        title: the title.
        is_focused: whether the pane has the focus, which draws it bold.
        n_rows_above: the rows of content above the shown ones.
        n_rows_below: the rows of content below the shown ones.
    """
    out = interface.text
    top, left, height, width = box
    attributes = curses.A_BOLD if is_focused else interface.dim()
    grey = interface.dim()
    up = f' ↑{n_rows_above} ' if n_rows_above > 0 else ''
    down = f' ↓{n_rows_below} ' if n_rows_below > 0 else ''
    out(top - 1, left - 1, '┌' + '─' * width + '┐', attributes)
    title_room = width - 2 - (len(up) + 1 if up else 0)
    out(top - 1, left + 1, f' {title} '[:max(title_room, 0)], attributes)
    if up:
        out(top - 1, left + width - len(up), up, grey)
    for row in range(top, top + height):
        out(row, left - 1, '│', attributes)
        out(row, left + width, '│', attributes)
    out(top + height, left - 1, '└' + '─' * width + '┘', attributes)
    if down:
        out(top + height, left + width - len(down), down, grey)
