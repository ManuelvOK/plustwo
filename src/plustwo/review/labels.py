# SPDX-License-Identifier: Zlib
"""Texts of threads, votes and versions that every interface shows."""

from collections.abc import Collection, Sequence

from plustwo.model.change import Change
from plustwo.model.comment import RESOLVED, STATUSES, UNRESOLVED, Comment
from plustwo.model.message import Message
from plustwo.model.roles import AUTHOR
from plustwo.model.vote import VERIFIED


# Each status, mapped to a short word for narrow columns
_SHORT_STATUSES = {UNRESOLVED: 'open', RESOLVED: 'done'}


def thread_file_after(files: Sequence[str], path: str | None, thread_paths: Collection[str],
                      step: int) -> str | None:
    """Finds the next or previous file with a thread.

    Args:
        files: the files in list order.
        path: the file to start after, None to start at an end.
        thread_paths: the files that have threads.
        step: 1 for the next file, -1 for the previous one.

    Returns:
        The file, None when there is none in that direction.
    """
    if path in files:
        index = files.index(path) + step
    else:
        index = 0 if step > 0 else len(files) - 1
    while 0 <= index < len(files):
        if files[index] in thread_paths:
            return files[index]
        index += step
    return None


def threads_in_list_order(comments: list[Comment]) -> list[Comment]:
    """Sorts threads for a list of them.

    Args:
        comments: the threads.

    Returns:
        The unresolved ones (drafts too) first, then the resolved ones,
        each by number.
    """
    return sorted(comments, key=lambda comment: (not comment.is_unresolved(), comment.number))


def answered_by_author(comment: Comment) -> bool:
    """Tells whether the author wrote a thread's last sent message.

    Args:
        comment: the thread.

    Returns:
        True if the last sent message is the author's.
    """
    last_sent = comment.last_sent()
    return last_sent is not None and AUTHOR == last_sent.author


def message_text(message: Message) -> str:
    """Writes a message's text for display.

    Args:
        message: the message.

    Returns:
        The text. For an empty one, which only changed the status, the
        status in parentheses.
    """
    if message.text:
        return message.text
    return f'({"unresolved" if message.unresolved else "resolved"})'


def comment_counts(change: Change, is_short: bool = False) -> str:
    """Counts a change's threads per status for display.

    Args:
        change: the change.
        is_short: whether to use the short words, see _SHORT_STATUSES.

    Returns:
        A text like "2 unresolved, 1 resolved, 1 draft", or short "2 open ·
        1 done". '-' without threads.
    """
    sent = [comment for comment in change.comments if not comment.is_draft()]
    counts = [f'{sum(status == comment.status for comment in sent)} '
              + (_SHORT_STATUSES[status] if is_short else status)
              for status in STATUSES if any(status == comment.status for comment in sent)]
    n_drafts = sum(comment.has_draft() for comment in change.comments)
    if n_drafts:
        counts.append(f'{n_drafts} draft' + ('s' if n_drafts > 1 else ''))
    return (' · ' if is_short else ', ').join(counts) or '-'


def vote_label(change: Change, label: str) -> str:
    """Sums up a label's votes for display.

    Args:
        change: the change.
        label: the label, CODE_REVIEW or VERIFIED.

    Returns:
        Like +2 or -1, '' without votes. A required Verified without votes
        shows as "?".
    """
    value = change.label_value(label)
    if value:
        return f'{value:+d}'
    return '?' if VERIFIED == label and change.needs_verified else ''


def version_label(change: Change) -> str:
    """Names a change's newest patch set for display.

    Args:
        change: the change.

    Returns:
        Like PS2, PS2+ if the change changed since, '-' without one.
    """
    if not change.latest_patch_set():
        return '-'
    return f'PS{change.latest_patch_set()}' + ('' if change.is_uploaded() else '+')
