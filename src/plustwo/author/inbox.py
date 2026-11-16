# SPDX-License-Identifier: Zlib
"""The author's inbox: threads to answer."""

from plustwo.config.registry import repository_name
from plustwo.diff.rows import REMOVED
from plustwo.model.place import PATCHSET_LEVEL, split_place
from plustwo.model.vote import LABEL_RANGES
from plustwo.review.changes import changes_in_stack
from plustwo.review.labels import message_text
from plustwo.review.porting import EDITED, FILE_GONE, port_thread, thread_version, version_line
from plustwo.review.threads import line_at


WATCH_INTERVAL_SECONDS = 1.0


def inbox_lines(repositories, topic=None):
    """What the author has to answer, as short lines.

    Only rebase requests, negative votes and unanswered unresolved threads,
    each thread with the quoted source line, so the author needs nothing
    else to start.
    """
    lines = []
    for repository in repositories:
        name = repository_name(repository)
        for change in changes_in_stack(repository):
            if topic and topic != change.topic:
                continue
            if change.rebase_is_requested:
                lines.append(f'rebase {name} {change.topic}: conflicts with '
                             f'{change.base}, rebase locally and upload')
            for label in LABEL_RANGES:
                for vote in change.current_votes(label):
                    if vote.value < 0:
                        lines.append(f'vote {name} {change.short_id()} {label}{vote.value:+d} '
                                     f'PS{vote.patch_set} by {vote.by}')
                        lines += [f'    {line}' for line in vote.message.split('\n')[-10:]
                                  if vote.message]
            for comment in change.comments:
                if not comment.wants_answer():
                    continue
                commit = change.patch_sets.get(comment.patch_set, change.commit)
                quoted = line_at(commit, comment.place, repository, comment.side)
                lines.append(f'{name} {change.short_id()} #{comment.number} '
                             + patch_set_note(change, comment, repository)
                             + comment.place_label() + (f'  | {quoted}' if quoted else ''))
                sent = [message for message in comment.messages if message.sent]
                lines.append(f'    {message_text(sent[-1])}')
            lines += news_lines(name, change)
    return lines


def news_lines(name, change):
    """'fyi' lines of a change's news(): replies on resolved threads,
    to read, not to answer.
    """
    lines = []
    for comment in change.news():
        lines.append(f'fyi {name} {change.short_id()} #{comment.number} (resolved) '
                     + comment.place_label())
        lines += [f'    {line}' for line in message_text(comment.last_sent()).split('\n')]
    return lines


def lost_threads(changes, repository=None):
    """The unresolved threads of changes whose line the newest patch set
    lost or changed (file gone, line removed or edited), for the author to
    move where they belong: lines like 'I3f2a #4 a.cc:12 (PS1, line
    removed): int x = 3;', as answer heads.
    """
    lines = []
    for change in changes:
        for comment in change.comments:
            if PATCHSET_LEVEL == comment.place or not comment.is_unresolved():
                continue
            ported = port_thread(change, comment, repository=repository)
            if ported is None or ported.state not in (FILE_GONE, REMOVED, EDITED):
                continue
            anchor = comment.anchored(ported.patch_set)
            version = thread_version(change, anchor, repository)
            _path, line = split_place(anchor.place)
            text = version_line(version, line, repository) if version and line else ''
            note = patch_set_note(change, comment, repository)
            lines.append(f'{change.short_id()} #{comment.number} {note}{anchor.place_label()}'
                         + (f': {text[:60]}' if text else ''))
    return lines


def patch_set_note(change, comment, repository=None):
    """'(PS2, now :50) ' for a thread of an older patch set than the latest,
    with where it is now, see port_thread(). Empty for one on the latest.
    """
    ported = port_thread(change, comment, repository=repository)
    if ported is None:
        return ''
    label = ported.label(comment.place)
    return f'(PS{comment.patch_set}' + (f', {label}' if label else '') + ') '
