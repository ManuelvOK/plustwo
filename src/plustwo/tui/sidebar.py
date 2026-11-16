# SPDX-License-Identifier: Zlib
"""The sidebar of the change screen: its panes and their heights."""

from plustwo.model.comment import Comment


# The panes of the change screen, in the order Tab goes through them
STACK = 'stack'
MESSAGE = 'message'
FILES = 'files'
COMMENTS = 'comments'
DIFF = 'diff'
PANES = (STACK, MESSAGE, FILES, COMMENTS, DIFF)

# The modes of the setting tui.sidebar. auto hides the left column on
# screens narrower than SIDEBAR_MIN_WIDTH.
SIDEBAR_MODES = ('auto', 'shown', 'hidden')
SIDEBAR_MIN_WIDTH = 100

# An entry of the footer: (actions, label)
FooterEntry = tuple[tuple[str, ...], str]


def sidebar_heights(n_rows: int, n_wanted_rows: list[int]) -> list[int]:
    """Shares the rows of the sidebar among its panes, max-min fair: panes
    whose content fits an equal share get exactly that, the rest is shared
    equally among those that want more, again until all rows are used.
    Every pane gets at least 3 rows (2 or 1 on a tiny screen). Rows nobody
    needs go to the comments, the last pane, and so do the rows an equal
    share leaves over.

    Args:
        n_rows: the rows inside the panes' frames together.
        n_wanted_rows: the rows each pane's content wants (stack, message,
            files, comments).

    Returns:
        The inside height of each pane.
    """
    n_panes = len(n_wanted_rows)
    floor = 3 if n_rows >= 3 * n_panes else 2 if n_rows >= 2 * n_panes else 1
    wants = [max(want, floor) for want in n_wanted_rows]
    heights = [0] * n_panes
    waiting = list(range(n_panes))
    n_left = n_rows
    while waiting and n_left > 0:
        share = n_left // len(waiting)
        fitting = [index for index in waiting if wants[index] <= share]
        if not fitting:
            for index in waiting:
                heights[index] = share
            # The odd rows to the lowest panes, the comments first
            for index in waiting[::-1][:n_left - share * len(waiting)]:
                heights[index] += 1
            return heights
        for index in fitting:
            heights[index] = wants[index]
            n_left -= wants[index]
            waiting.remove(index)
    heights[-1] += max(n_left, 0)
    return heights


def thread_footer(comment: Comment | None) -> list[FooterEntry]:
    """Lists the footer's actions on a thread.

    Args:
        comment: the thread, None for none.

    Returns:
        Reply or edit the draft, Done, Ack, Resolved and move. Empty
        without a thread.
    """
    if comment is None:
        return []
    if comment.has_draft():
        return [(('edit-draft',), 'edit draft'), (('resolve',), 'resolved')]
    return [(('reply',), 'reply'), (('done',), 'Done'), (('ack',), 'Ack'),
            (('resolve',), 'resolved'), (('move-thread',), 'move')]


def sidebar_shown(mode: str, width: int) -> bool:
    """Tells whether the change screen shows its left column.

    Args:
        mode: the setting tui.sidebar, one of SIDEBAR_MODES.
        width: the screen's width.

    Returns:
        True if the sidebar is shown.
    """
    return 'shown' == mode or ('auto' == mode and width >= SIDEBAR_MIN_WIDTH)
