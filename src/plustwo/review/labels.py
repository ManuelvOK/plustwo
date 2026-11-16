# SPDX-License-Identifier: Zlib
"""Texts of threads, votes and versions that every interface shows."""

from plustwo.model.comment import RESOLVED, STATUSES, UNRESOLVED
from plustwo.model.roles import AUTHOR
from plustwo.model.vote import VERIFIED


def thread_file_after(files, path, thread_paths, step):
    """The next file after path (step 1) or the previous one (step -1) in
    files that has a thread: thread_paths are the files of the threads.
    None when there is none in that direction.
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


def threads_in_list_order(comments):
    """Threads for a list of them: the unresolved ones (drafts too) first,
    then the resolved ones, each by number.
    """
    return sorted(comments, key=lambda comment: (not comment.is_unresolved(), comment.number))


def answered_by_author(comment):
    """Whether the author wrote the thread's last sent message."""
    last = comment.last_sent()
    return last is not None and AUTHOR == last.author


def message_text(message):
    """A message's text for display. An empty one only changed the status."""
    if message.text:
        return message.text
    return f'({"unresolved" if message.unresolved else "resolved"})'


# Short words for the statuses, for narrow columns
SHORT_STATUSES = {UNRESOLVED: 'open', RESOLVED: 'done'}


def comment_counts(change, short=False):
    """Thread counts per status for display, like "2 unresolved, 1
    resolved", or short "2 open · 1 done".
    """
    sent = [comment for comment in change.comments if not comment.is_draft()]
    counts = [f'{sum(status == comment.status for comment in sent)} '
              + (SHORT_STATUSES[status] if short else status)
              for status in STATUSES if any(status == comment.status for comment in sent)]
    drafts = sum(comment.has_draft() for comment in change.comments)
    if drafts:
        counts.append(f'{drafts} draft' + ('s' if drafts > 1 else ''))
    return (' · ' if short else ', ').join(counts) or '-'


def vote_label(change, label):
    """A label's summary for display: +2, -1, or empty without votes. A
    required Verified without votes shows as "?".
    """
    value = change.label_value(label)
    if value:
        return f'{value:+d}'
    return '?' if VERIFIED == label and change.verified_required else ''


def version_label(change):
    """Patch set label for display: PS2, PS2+ if changed since, - if none."""
    if not change.latest_patch_set():
        return '-'
    return f'PS{change.latest_patch_set()}' + ('' if change.is_uploaded() else '+')
