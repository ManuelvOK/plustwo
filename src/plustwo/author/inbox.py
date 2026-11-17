# SPDX-License-Identifier: Zlib
"""The author's inbox: threads to answer."""

from plustwo.config.registry import repository_name
from plustwo.git.repository import Repository
from plustwo.model.change import Change
from plustwo.model.comment import Comment
from plustwo.model.place import PATCHSET_LEVEL, split_place
from plustwo.model.vote import LABEL_RANGES
from plustwo.review.changes import changes_in_stack
from plustwo.review.labels import message_text
from plustwo.review.porting import (
    EDITED, FILE_GONE, MOVED, REMOVED, SAME, UNCERTAIN, PortedPlace, port_thread, ported_version,
    thread_version, version_line, version_lines)
from plustwo.review.threads import line_at


# How many lines of a negative vote's message the inbox shows
_N_VOTE_MESSAGE_LINES = 10

# How many lines around a thread's line ported_threads() quotes
_N_CONTEXT_LINES = 2


def inbox_lines(repositories: list[Repository], topic: str | None = None) -> list[str]:
    """Lists what the author has to answer: rebase requests, negative votes
    and unanswered unresolved threads, each thread with the quoted source
    line, so the author needs nothing else to start. Replies on resolved
    threads follow as fyi lines.

    Args:
        repositories: the repositories.
        topic: the one stack to look at, None for all.

    Returns:
        Short lines.
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
            lines += _vote_lines(name, change)
            for comment in change.comments:
                if comment.wants_answer():
                    lines += _thread_lines(name, change, comment)
            lines += _news_lines(name, change)
    return lines


def _vote_lines(name: str, change: Change) -> list[str]:
    """Lists the negative votes on a change's latest patch set.

    Args:
        name: the repository's name.
        change: the change.

    Returns:
        A line per vote, then the end of its message, indented.
    """
    lines = []
    for label in LABEL_RANGES:
        for vote in change.current_votes(label):
            if vote.value >= 0:
                continue
            lines.append(f'vote {name} {change.short_id()} {label}{vote.value:+d} '
                         f'PS{vote.patch_set} by {vote.by}')
            if vote.message:
                lines += [f'    {line}'
                          for line in vote.message.split('\n')[-_N_VOTE_MESSAGE_LINES:]]
    return lines


def _thread_lines(name: str, change: Change, comment: Comment) -> list[str]:
    """Writes a thread to answer.

    Args:
        name: the repository's name.
        change: the thread's change.
        comment: the thread.

    Returns:
        The head with the place and the quoted line, then the reviewer's
        last message, indented.
    """
    commit = change.patch_sets.get(comment.patch_set, change.commit)
    quoted = line_at(change.repository, commit, comment.place, comment.side)
    head = (f'{name} {change.short_id()} #{comment.number} '
            + patch_set_note(change, comment)
            + comment.place_label() + (f'  | {quoted}' if quoted else ''))
    last_sent = comment.last_sent()
    return [head] + ([f'    {message_text(last_sent)}'] if last_sent else [])


def _news_lines(name: str, change: Change) -> list[str]:
    """Writes the fyi lines of a change's news(): replies on resolved
    threads, to read, not to answer.

    Args:
        name: the repository's name.
        change: the change.

    Returns:
        A head per thread, then the reply, indented.
    """
    lines = []
    for comment in change.news():
        last_sent = comment.last_sent()
        lines.append(f'fyi {name} {change.short_id()} #{comment.number} (resolved) '
                     + comment.place_label())
        if last_sent is not None:
            lines += [f'    {line}' for line in message_text(last_sent).split('\n')]
    return lines


def lost_threads(changes: list[Change]) -> list[str]:
    """Lists the unresolved threads whose line the newest patch set lost or
    changed (file gone, line removed or edited), or whose move is a guess
    (see kept_state()), for the author to move where they belong.

    Args:
        changes: the changes.

    Returns:
        Lines like 'I3f2a #4 (PS1, line removed) a.cc:12: int x = 3', as
        heads of answers.
    """
    lines = []
    for change in changes:
        for comment in change.comments:
            if PATCHSET_LEVEL == comment.place or not comment.is_unresolved():
                continue
            ported = port_thread(change, comment)
            if ported is None or ported.state not in (FILE_GONE, REMOVED, EDITED, UNCERTAIN):
                continue
            anchor = comment.anchored(ported.patch_set if isinstance(ported.patch_set, int)
                                      else None)
            version = thread_version(change, anchor)
            _path, line = split_place(anchor.place)
            text = version_line(change.repository, version, line) if version and line else ''
            note = patch_set_note(change, comment)
            lines.append(f'{change.short_id()} #{comment.number} {note}{anchor.place_label()}'
                         + (f': {text[:60]}' if text else ''))
    return lines


def ported_threads(change: Change) -> list[str]:
    """Lists every thread that isn't at its exact old place in the newest
    patch set, resolved ones too, with the line it was written on and
    where it is now, each with _N_CONTEXT_LINES lines around it, so the
    author can judge the guess and move the thread where it belongs.
    A plain move (MOVED, a safe shift) gets only its head, to save
    tokens.

    Args:
        change: the change.

    Returns:
        Per thread a head like 'I3f2a #4 [unresolved] (PS1, now :50)
        a.cc:44', then, unless it only moved, the two quotes, indented.
    """
    lines = []
    for comment in change.comments:
        if PATCHSET_LEVEL == comment.place:
            continue
        ported = port_thread(change, comment)
        if ported is None or SAME == ported.state:
            continue
        anchor = comment.anchored(ported.patch_set if isinstance(ported.patch_set, int)
                                  else None)
        status = 'unresolved' if comment.is_unresolved() else 'resolved'
        lines.append(f'{change.short_id()} #{comment.number} [{status}] '
                     f'{patch_set_note(change, comment)}{anchor.place_label()}')
        if MOVED == ported.state:
            continue
        _path, line = split_place(anchor.place)
        version = thread_version(change, anchor)
        if line is None or version is None:
            continue
        lines += _quote(f'PS{anchor.patch_set} {anchor.place_label()}',
                        version_lines(change.repository, version), line, line)
        lines += _ported_quote(change, anchor, ported)
    return lines


def _ported_quote(change: Change, anchor: Comment, ported: PortedPlace) -> list[str]:
    """Quotes where a thread is in the newest patch set, see
    ported_threads().

    Args:
        change: the thread's change.
        anchor: the thread, or the move it was ported from.
        ported: where it is now, see port_thread().

    Returns:
        A head and the quoted lines, indented.
    """
    new_version = ported_version(change, anchor, ported)
    head = f'PS{ported.patch_set}'
    if new_version is None:
        return [f'  {head}: file gone']
    path, number = split_place(ported.place)
    if number is None:
        return [f'  {head} {path}: line gone']
    new_lines = version_lines(change.repository, new_version)
    if REMOVED == ported.state:
        # number is the line before where it was, 0 at the top
        where = f'after line {number}' if number else 'at the top'
        return _quote(f'{head} {path}, line removed {where}', new_lines,
                      number + 1, None)
    return _quote(f'{head} {ported.place}', new_lines, number, number)


def _quote(head: str, lines: list[str], middle: int, marked: int | None) -> list[str]:
    """Quotes lines of a file around one.

    Args:
        head: the first line, like 'PS1 a.cc:12'.
        lines: the file's lines.
        middle: the line in the middle.
        marked: the line marked with '>', None for none.

    Returns:
        The head and the lines, numbered and indented.
    """
    first = max(1, middle - _N_CONTEXT_LINES)
    last = min(len(lines), middle + _N_CONTEXT_LINES - (marked is None))
    width = len(str(last))
    quoted = [f'  {head}']
    for number in range(first, last + 1):
        mark = '>' if number == marked else ' '
        quoted.append(f'    {mark} {number:{width}} | {lines[number - 1]}'.rstrip())
    return quoted


def patch_set_note(change: Change, comment: Comment) -> str:
    """Writes where a thread of an older patch set than the latest is now,
    see port_thread().

    Args:
        change: the thread's change.
        comment: the thread.

    Returns:
        Like '(PS2, now :50) ', '' for a thread on the latest.
    """
    ported = port_thread(change, comment)
    if ported is None:
        return ''
    label = ported.label(comment.place)
    return f'(PS{comment.patch_set}' + (f', {label}' if label else '') + ') '
