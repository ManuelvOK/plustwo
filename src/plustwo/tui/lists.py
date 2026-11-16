# SPDX-License-Identifier: Zlib
"""Lists with a cursor: keys, the wheel, labels of their rows."""

import curses
from collections.abc import Sequence
from typing import TypeVar

from plustwo.model.comment import Comment
from plustwo.tui.colours import STATUS_COLOURS
from plustwo.tui.mouse import WHEEL_DOWN, WHEEL_UP


# What identifies a list's rows, see keyed_cursor()
K = TypeVar('K')

# Actions that move in a list
LIST_ACTIONS = ('down', 'up', 'top', 'bottom', 'page-down', 'page-up')

# How far page-down and page-up move in a list
_LIST_PAGE = 10


def status_attribute(status: str) -> int:
    """Finds the colour for a thread status or the draft marker.

    Args:
        status: the status, or draft.

    Returns:
        The curses attribute, see STATUS_COLOURS.
    """
    return curses.color_pair(STATUS_COLOURS.get(status, 0))


def comment_label(comment: Comment) -> str:
    """Writes the status shown for a thread.

    Args:
        comment: the thread.

    Returns:
        Its status, or draft if unsent. With a draft that changes the
        status, the status it will have.
    """
    if comment.is_draft():
        return 'draft' + (' resolved' if 'resolved' == comment.draft_status() else '')
    status_after = comment.draft_status()
    if status_after and status_after != comment.status:
        return f'{comment.status} → {status_after} (draft)'
    return comment.status + (' +draft' if comment.has_draft() else '')


def wheel_action(mouse_event: str) -> str | None:
    """Finds the action a wheel turn stands for.

    Args:
        mouse_event: the mouse event, see the module mouse.

    Returns:
        down or up, None for other events.
    """
    return {WHEEL_DOWN: 'down', WHEEL_UP: 'up'}.get(mouse_event)


def keyed_cursor(keys: Sequence[K], key: K, position: int) -> tuple[int, K]:
    """Places a list cursor that sticks to its item.

    Args:
        keys: what identifies each row of the list, after a change.
        key: the cursor's item before the change.
        position: the cursor's row before the change.

    Returns:
        (row, key of that row): the row of key, else the row nearest to the
        old position. (0, key) for an empty list.
    """
    if key in keys:
        return keys.index(key), key
    if not keys:
        return 0, key
    row = max(0, min(position, len(keys) - 1))
    return row, keys[row]


def position_after(index: int, action: str, n_rows: int, count: int | None = None) -> int:
    """Moves a list position by an action, staying inside the list.

    Args:
        index: the position.
        action: down, up, top, bottom, page-down or page-up. Others don't
            move.
        n_rows: the number of rows in the list.
        count: how many rows down and up move, or the row top and bottom go
            to, counted from 1. None for the default.

    Returns:
        The new position.
    """
    if not n_rows:
        return 0
    n_steps = {'down': count or 1, 'up': -(count or 1), 'page-down': _LIST_PAGE,
               'page-up': -_LIST_PAGE}.get(action)
    if n_steps is not None:
        return max(0, min(index + n_steps, n_rows - 1))
    if action in ('top', 'bottom'):
        last = 0 if 'top' == action else n_rows - 1
        return min(count - 1, n_rows - 1) if count else last
    return index
