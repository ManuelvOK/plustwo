# SPDX-License-Identifier: Zlib
"""Lists with a cursor: keys, the wheel, labels of their rows."""

import curses

from plustwo.tui.colours import STATUS_COLOURS
from plustwo.tui.mouse import WHEEL_DOWN, WHEEL_UP


def status_attribute(status):
    """Colour for a thread status or draft marker."""
    return curses.color_pair(STATUS_COLOURS.get(status, 0))


def comment_label(comment):
    """Status shown for a thread: its status, or draft if unsent. With a
    draft that changes the status, the status it will have.
    """
    if comment.is_draft():
        return 'draft' + (' resolved' if 'resolved' == comment.draft_status() else '')
    after = comment.draft_status()
    if after and after != comment.status:
        return f'{comment.status} → {after} (draft)'
    return comment.status + (' +draft' if comment.has_draft() else '')


# Actions that move in a list
LIST_ACTIONS = ('down', 'up', 'top', 'bottom', 'page-down', 'page-up')


# How far page-down and page-up move in a list
LIST_PAGE = 10


def wheel_action(kind):
    """The action a wheel turn stands for: down, up, None otherwise."""
    return {WHEEL_DOWN: 'down', WHEEL_UP: 'up'}.get(kind)


def keyed_cursor(keys, key, position):
    """A list cursor that sticks to its item: the row of key in keys (the
    list after a change), else the row nearest to the old position.
    Returns (row, key of that row), (0, key) for an empty list.
    """
    if key in keys:
        return keys.index(key), key
    if not keys:
        return 0, key
    row = max(0, min(position, len(keys) - 1))
    return row, keys[row]


def move(index, action, length, count=None):
    """New list position after down, up, top, bottom or a page, staying
    inside the list. A count moves that many rows, or with top and bottom
    goes to that row.
    """
    if not length:
        return 0
    steps = {'down': count or 1, 'up': -(count or 1), 'page-down': LIST_PAGE,
             'page-up': -LIST_PAGE}.get(action)
    if steps is not None:
        return max(0, min(index + steps, length - 1))
    if action in ('top', 'bottom'):
        last = 0 if 'top' == action else length - 1
        return min(count - 1, length - 1) if count else last
    return index
