# SPDX-License-Identifier: Zlib
"""The author's answers in one batch."""

import re
import time
from dataclasses import replace

from plustwo.model.change import Change

from plustwo.git.errors import ChangeError
from plustwo.model.comment import DONE, RESOLVED, UNRESOLVED
from plustwo.model.message import Message
from plustwo.model.roles import AUTHOR
from plustwo.review.changes import comment_of, find_change, update_change
from plustwo.review.moves import move_place, place_move


# The verb of an answer that moves its thread, see parse_answers()
MOVE = 'move'

# A line of `plustwo answer`: Change-Id prefix, thread, optional verb, text
_ANSWER_PATTERN = re.compile(
    r'^\s*(?:\S+\s+)?(I[0-9a-f]+)\s+#(\d+)\s*(?:\(PS\d+[^)]*\)\s*)?'
    r'(resolve|unresolve|done|move)?\s*:?\s*(.*)$')


def parse_answers(text: str) -> list[tuple[str, int, str | None, str]]:
    """Reads the author's answers. Each starts on a line like "I3f2a #2
    done", "I3f2a #3: text", "I3f2a #4 resolve: text" or "unresolve: text",
    or "I3f2a #5 move a.cc:12", which moves the thread to the newest patch
    set (see move_place()). A repository name before the Change-Id and a
    patch set note after the number, as inbox prints them, are allowed. The
    lines up to the next such line continue the answer's text, so it can
    have lists and paragraphs.

    Args:
        text: the answers.

    Returns:
        (Change-Id prefix, thread number, status, text) per answer. The
        status is RESOLVED, UNRESOLVED, MOVE (the text is the place) or
        None.

    Raises:
        ChangeError: one error naming every bad line.
    """
    return [answer[1:] for answer in _parsed_answers(text)]


def _parsed_answers(text: str) -> list[tuple[int, str, int, str | None, str]]:
    """Reads the author's answers, see parse_answers().

    Args:
        text: the answers.

    Returns:
        (line number, Change-Id prefix, thread number, status, text) per
        answer.

    Raises:
        ChangeError: one error naming every bad line.
    """
    answers: list[tuple[int, str, int, str | None, list[str]]] = []
    errors = []
    for number, line in enumerate(text.split('\n'), start=1):
        match = _ANSWER_PATTERN.match(line)
        if match is not None:
            prefix, thread, verb, message = match.groups()
            answers.append((number, prefix, int(thread), verb, [message.strip()]))
        elif answers:
            # The answer goes on, as written
            answers[-1][4].append(line.rstrip())
        elif line.strip() and not line.lstrip().startswith('#'):
            errors.append(f'line {number} is no answer: {line.strip()}')
    result = []
    for number, prefix, thread, verb, lines in answers:
        status = {'resolve': RESOLVED, 'done': RESOLVED, 'unresolve': UNRESOLVED,
                  'move': MOVE}.get(verb or '')
        message = '\n'.join(lines).strip() or (DONE if 'done' == verb else '')
        if not message and status in (None, MOVE):
            errors.append(f'line {number} has no ' + ('place' if MOVE == status else 'text'))
        result.append((number, prefix, thread, status, message))
    if errors:
        raise ChangeError('; '.join(errors))
    return result


def answer(text: str, repository: str | None = None) -> int:
    """Adds the author's answers to their threads, with one lock and one
    note write per change. Checks the format and the changes of all lines
    before writing.

    Args:
        text: the answers, see parse_answers().
        repository: the repository's directory, None for the one each
            change is in review in.

    Returns:
        The number of answers.

    Raises:
        ChangeError: a line is no answer, or its change or thread doesn't
            exist. One error names all of them.
    """
    answers = _parsed_answers(text)
    by_change: dict[str, list[tuple[int, str | None, str]]] = {}
    errors = []
    for line, prefix, number, status, message in answers:
        # An unknown change or thread stops the whole batch before anything
        # is written. All of them are named, so one retry is enough.
        try:
            change = find_change(prefix, repository)
            comment = comment_of(change, number)
            if MOVE == status:
                # Checked on a copy, the change is only read here
                place_move(change, replace(comment, moves=list(comment.moves)),
                           *move_place(message), by=AUTHOR, repository=repository)
        except ChangeError as error:
            errors.append(f'line {line}: {error}')
            continue
        by_change.setdefault(change.change_id, []).append((number, status, message))
    if errors:
        raise ChangeError('; '.join(errors))
    for change_id, entries in by_change.items():
        def edit(change: Change, entries: list[tuple[int, str | None, str]] = entries
                 ) -> None:
            """Adds the answers of one change.

            Args:
                change: the change, read fresh.
                entries: (thread number, status, text) per answer.
            """
            for number, status, message in entries:
                if MOVE == status:
                    place_move(change, comment_of(change, number), *move_place(message),
                               by=AUTHOR, repository=repository)
                    continue
                comment_of(change, number).messages.append(Message(
                    author=AUTHOR, text=message, sent=True, time=int(time.time()),
                    unresolved=None if status is None else UNRESOLVED == status))
        update_change(change_id, edit, repository)
    return len(answers)
