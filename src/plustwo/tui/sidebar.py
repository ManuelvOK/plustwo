# SPDX-License-Identifier: Zlib
"""The sidebar of the change screen: its panes and their heights."""


STACK = 'stack'


MESSAGE = 'message'


FILES = 'files'


COMMENTS = 'comments'


DIFF = 'diff'


PANES = (STACK, MESSAGE, FILES, COMMENTS, DIFF)


def sidebar_heights(rows, wants):
    """The inside heights of the sidebar's panes (stack, message, files,
    comments), for rows inside their frames together and the rows their
    content wants. Max-min fair: panes whose content fits an equal share
    get exactly that, the rest is shared equally among those that want
    more, again until all rows are used. Every pane gets at least 3 rows
    (2 or 1 on a tiny screen). Rows nobody needs go to the comments, the
    last pane, and so do the rows an equal share leaves over.
    """
    count = len(wants)
    floor = 3 if rows >= 3 * count else 2 if rows >= 2 * count else 1
    wants = [max(want, floor) for want in wants]
    heights = [0] * count
    waiting = list(range(count))
    left = rows
    while waiting and left > 0:
        share = left // len(waiting)
        fitting = [index for index in waiting if wants[index] <= share]
        if not fitting:
            for index in waiting:
                heights[index] = share
            # The odd rows to the lowest panes, the comments first
            for index in waiting[::-1][:left - share * len(waiting)]:
                heights[index] += 1
            return heights
        for index in fitting:
            heights[index] = wants[index]
            left -= wants[index]
            waiting.remove(index)
    heights[-1] += max(left, 0)
    return heights


def thread_footer(comment):
    """The footer's actions on a thread: reply or edit the draft, Done,
    Ack, Resolved.
    """
    if comment is None:
        return []
    if comment.has_draft():
        return [(('edit-draft',), 'edit draft'), (('resolve',), 'resolved')]
    return [(('reply',), 'reply'), (('done',), 'Done'), (('ack',), 'Ack'),
            (('resolve',), 'resolved'), (('move-thread',), 'move')]


# The modes of the setting tui.sidebar. auto hides the left column on
# screens narrower than this.
SIDEBAR_MODES = ('auto', 'shown', 'hidden')


SIDEBAR_MIN_WIDTH = 100


def sidebar_shown(mode, width):
    """Whether the change screen shows its left column."""
    return 'shown' == mode or ('auto' == mode and width >= SIDEBAR_MIN_WIDTH)
