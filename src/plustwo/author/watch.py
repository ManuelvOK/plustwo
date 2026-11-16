# SPDX-License-Identifier: Zlib
"""Watching repositories for the author."""

import datetime
import re
import sys
import time
from collections.abc import Callable
from typing import Any, TextIO

from plustwo.config.registry import repository_name
from plustwo.config.settings import base_branch
from plustwo.config.state import set_state, state_values
from plustwo.config.user import current_user
from plustwo.git.errors import ChangeError
from plustwo.git.notes import read_note, read_notes
from plustwo.git.refs import ABANDONED_PREFIX, PATCH_SET_PREFIX, is_ancestor, refs_with_prefix
from plustwo.git.repository import git
from plustwo.model.acknowledgement import says_nothing
from plustwo.model.change import Change
from plustwo.model.comment import Comment
from plustwo.model.message import Message
from plustwo.model.place import PATCHSET_LEVEL
from plustwo.model.roles import AUTHOR, REVIEWER
from plustwo.review.changes import change_from, changes_in_stack
from plustwo.review.labels import message_text
from plustwo.review.stacks import stack_bases, stack_version


# How long watch waits between two looks at the refs
_WATCH_INTERVAL_SECONDS = 1.0

# Something that happened in a review, as a key: its type first, then
# what tells it apart. ('message', Change-Id, thread number, message index),
# ('vote', Change-Id, label, value, patch set, voter), ('rebase', topic,
# Change-Id) or ('rebased', topic, Change-Id, patch set).
Event = tuple[Any, ...]

# Each stack's topic, mapped to its changes' Change-Ids, mapped to their
# commits
Stacks = dict[str, dict[str, str]]


# ── events ────────────────────────────────────────────────────────────────────

def events_of(repository: str | None, topic: str | None = None,
              changes: list[Change] | None = None,
              submitted: list[Change] | None = None) -> set[Event]:
    """Collects everything sent, voted or rebased in a repository.

    Args:
        repository: the repository's directory, None for the current one.
        topic: the one stack to look at, None for all.
        changes: the stacks' changes if read already, None to read them.
        submitted: more changes to count in, which left the stacks.

    Returns:
        The events, see Event.
    """
    events: set[Event] = set()
    if changes is None:
        changes = changes_in_stack(repository)
    changes = [change for change in changes if not topic or topic == change.topic]
    for change in changes + list(submitted or []):
        for comment in change.comments:
            for index, message in enumerate(comment.messages):
                if message.sent and REVIEWER == message.author:
                    events.add(('message', change.change_id, comment.number, index))
        for vote in change.votes:
            # Votes a new patch set kept were reported when they were given.
            if AUTHOR != vote.by and not vote.copied_from:
                events.add(('vote', change.change_id, vote.label, vote.value, vote.patch_set,
                            vote.by))
    # Rebases are told per stack, by its top change
    tops = {change.topic: change for change in changes if not change.is_submitted}
    for stack_topic, top in tops.items():
        if top.rebase_is_requested:
            events.add(('rebase', stack_topic, top.change_id))
        if top.rebased_patch_set:
            events.add(('rebased', stack_topic, top.change_id, top.rebased_patch_set))
    return events


def _stacks_of(repository: str | None, topic: str | None = None) -> Stacks:
    """Reads the changes of each stack.

    Args:
        repository: the repository's directory, None for the current one.
        topic: the one stack to look at, None for all.

    Returns:
        The stacks, see Stacks.
    """
    stacks: Stacks = {}
    for change in changes_in_stack(repository):
        if topic and topic != change.topic:
            continue
        stacks.setdefault(change.topic, {})[change.change_id] = change.commit
    return stacks


def _changes_by_id(changes: list[Change]) -> dict[str, list[Change]]:
    """Groups changes by their Change-Id. One change can be in several
    stacks, as an old commit that stayed the base of other branches, and
    only some copies have all its messages.

    Args:
        changes: the changes.

    Returns:
        Each Change-Id, mapped to every copy of the change.
    """
    by_id: dict[str, list[Change]] = {}
    for change in changes:
        by_id.setdefault(change.change_id, []).append(change)
    return by_id


def _event_message(event: Event,
                   by_id: dict[str, list[Change]]) -> tuple[Change, Comment, Message]:
    """Finds the message of a message event, in the first copy of the
    change that has it.

    Args:
        event: the event, of type message.
        by_id: the changes, see _changes_by_id().

    Returns:
        (change, thread, message).

    Raises:
        ChangeError: no copy of the change has the message.
    """
    _, change_id, number, index = event
    for change in by_id[change_id]:
        comment = change.comment(number)
        if comment is not None and index < len(comment.messages):
            return change, comment, comment.messages[index]
    raise ChangeError(f'{change_id[:8]} has no message {index} in thread #{number}')


def _event_time(event: Event, by_id: dict[str, list[Change]]) -> int | None:
    """Finds when an event happened.

    Args:
        event: the event.
        by_id: the changes, see _changes_by_id().

    Returns:
        The time, seconds since 1970. None if the event doesn't say.
    """
    if 'message' == event[0]:
        return _event_message(event, by_id)[2].time
    if 'vote' == event[0]:
        _, change_id, label, value, patch_set, by = event
        return next((vote.time for change in by_id[change_id] for vote in change.votes
                     if (vote.label, vote.value, vote.patch_set, vote.by)
                     == (label, value, patch_set, by)), None)
    if 'rebased' == event[0]:
        return next((change.uploads[event[3]] for change in by_id[event[2]]
                     if event[3] in change.uploads), None)
    return None


def _report_events(name: str, new: set[Event], by_id: dict[str, list[Change]],
                   say: Callable[[str], None]) -> None:
    """Says the new events of a repository, see watch().

    Args:
        name: the repository's name.
        new: the new events.
        by_id: the changes, see _changes_by_id().
        say: the function that prints a line.
    """
    n_new_messages: dict[str, int] = {}
    for event in sorted(new, key=str):
        if 'message' == event[0]:
            change, comment, message = _event_message(event, by_id)
            if comment.is_unresolved() or (PATCHSET_LEVEL == comment.place
                                           and not says_nothing(message.text)):
                n_new_messages[event[1]] = n_new_messages.get(event[1], 0) + 1
            elif PATCHSET_LEVEL != comment.place and not says_nothing(message.text):
                # Nothing to answer, and not in inbox once submitted
                say(f'fyi {name} {change.short_id()} #{comment.number} (resolved) '
                    + comment.place_label())
                for line in message_text(message).split('\n'):
                    say(f'    {line}')
        elif 'vote' == event[0]:
            _, change_id, label, value, patch_set, by = event
            say(f'vote {name} {change_id[:8]} {label}{value:+d} PS{patch_set} by {by}')
        else:
            say(f'{event[0]} {name} {event[1]}')
    for change_id, n_messages in n_new_messages.items():
        say(f'review {name} {change_id[:8]}: {n_messages} new')


def _submitted_line(name: str, topic: str, n_changes: int) -> str:
    """Writes the line of watch() for a stack's submitted changes.

    Args:
        name: the repository's name.
        topic: the stack's topic.
        n_changes: the number of submitted changes.

    Returns:
        Like "submitted repo topic: 2 changes".
    """
    return f'submitted {name} {topic}: {n_changes} change' + ('s' if n_changes > 1 else '')


# ── the time of the last watch ────────────────────────────────────────────────

def _watch_key(topic: str | None = None) -> str:
    """Names the key of the state file watch that keeps when the current
    user's watch last reported something.

    Args:
        topic: the one stack watched, None for all.

    Returns:
        The key.
    """
    return f'watch.{current_user()}{" " + topic if topic else ""}.time'


def last_watched(topic: str | None = None) -> int | None:
    """Reads when the user's last watch reported its last event.

    Args:
        topic: the one stack watched, None for all.

    Returns:
        The time, seconds since 1970. None if never.
    """
    values = state_values('watch', _watch_key(topic))
    return int(values[-1]) if values and values[-1].isdigit() else None


def parse_time(text: str) -> int:
    """Reads a time.

    Args:
        text: a time like 1763030000 (seconds since 1970),
            2026-11-13T10:00 or 10:00 (today).

    Returns:
        The time, seconds since 1970.

    Raises:
        ChangeError: the text is no time.
    """
    if text.isdigit():
        return int(text)
    try:
        if re.fullmatch(r'\d{1,2}:\d{2}(:\d{2})?', text):
            moment = datetime.datetime.combine(datetime.date.today(),
                                               datetime.time.fromisoformat(text.zfill(5)))
        else:
            moment = datetime.datetime.fromisoformat(text)
    except ValueError as error:
        raise ChangeError(f'not a time: {text}, give e.g. 2026-11-13T10:00 or 10:00') from error
    return int(moment.timestamp())


def submitted_since(repository: str | None, since: int, topic: str | None = None,
                    changes: list[Change] | None = None) -> list[Change]:
    """Finds the changes submitted at or after a time, by the submit record
    in their notes, see submit().

    Args:
        repository: the repository's directory, None for the current one.
        since: the time, seconds since 1970.
        topic: the one stack to look at, None for all.
        changes: the stacks' changes if read already, which are left out.
            None to read them.

    Returns:
        The submitted changes, marked submitted.
    """
    if changes is None:
        changes = changes_in_stack(repository)
    in_review = {change.change_id for change in changes}
    latest: dict[str, tuple[int, str]] = {}
    for ref, commit in refs_with_prefix(PATCH_SET_PREFIX, repository=repository).items():
        change_id, number = ref[len(PATCH_SET_PREFIX):].rsplit('/', 1)
        if change_id not in in_review and int(number) > latest.get(change_id, (0, ''))[0]:
            latest[change_id] = (int(number), commit)
    notes = read_notes([commit for _, commit in latest.values()], repository)
    submitted = []
    for change_id, (_, commit) in latest.items():
        record = notes.get(commit, {}).get('submit') or {}
        if record.get('time', 0) >= since and (not topic or topic == record.get('topic')):
            change = change_from(commit, record.get('topic', ''), notes[commit], repository,
                                 change_id)
            change.is_submitted = True
            submitted.append(change)
    return submitted


# ── watching ──────────────────────────────────────────────────────────────────

class _RepositoryWatch:
    """What watch() knows of one repository between two looks.

    Attributes:
        repository: the repository's directory, None for the current one.
        topic: the one stack watched, None for all.
        version: the refs as stack_version() read them last.
        known: the events reported or known before.
        stacks: the stacks at the last look.
        bases: the stacks' base branches, as they were before a submit.
    """

    def __init__(self, repository: str | None, topic: str | None, version: str,
                 current: list[Change]) -> None:
        """Takes the first look at a repository.

        Args:
            repository: the repository's directory, None for the current one.
            topic: the one stack watched, None for all.
            version: the refs, see stack_version(), read before current.
            current: the stacks' changes now.
        """
        self.repository = repository
        self.topic = topic
        self.version = version
        self.known = events_of(repository, topic, current)
        self.stacks = _stacks_of(repository, topic)
        self.bases = stack_bases(repository)

    def catch_up(self, since: int, current: list[Change], say: Callable[[str], None]) -> bool:
        """Says what happened since a time, while no watch ran. A rebase
        request still open is said again.

        Args:
            since: the time, seconds since 1970.
            current: the stacks' changes now.
            say: the function that prints a line.

        Returns:
            Whether anything was said.
        """
        submitted = submitted_since(self.repository, since, self.topic, current)
        by_id = _changes_by_id(current + submitted)
        missed = {event for event in events_of(self.repository, self.topic, current, submitted)
                  if 'rebase' == event[0] or (_event_time(event, by_id) or 0) >= since}
        name = repository_name(self.repository)
        n_submitted_by_topic: dict[str, int] = {}
        for change in submitted:
            n_submitted_by_topic[change.topic] = n_submitted_by_topic.get(change.topic, 0) + 1
        for stack_topic, n_changes in n_submitted_by_topic.items():
            say(_submitted_line(name, stack_topic, n_changes))
        _report_events(name, missed, by_id, say)
        return bool(missed or n_submitted_by_topic)

    def look(self) -> tuple[float, list[str]]:
        """Looks again, if a ref of the review changed.

        Returns:
            (when the look found the change, seconds since 1970, the lines
            to say). The lines are empty if nothing happened.
        """
        version = stack_version(self.repository)
        if version == self.version:
            return 0, []
        checked = time.time()
        self.version = version
        current = changes_in_stack(self.repository)
        name = repository_name(self.repository)
        stacks_before, bases_before = self.stacks, self.bases
        self.stacks = _stacks_of(self.repository, self.topic)
        self.bases = {**bases_before, **stack_bases(self.repository)}
        lines = self._left_stack_lines(name, stacks_before, bases_before)
        # The submitted changes' last messages and votes, as a reply sent
        # together with the submit, are in their commits' notes.
        submitted = self._submitted_changes(current, stacks_before, bases_before)
        events = events_of(self.repository, self.topic, current, submitted)
        new = events - self.known
        self.known = events
        _report_events(name, new, _changes_by_id(current + submitted), lines.append)
        return checked, lines

    def _left_stack_lines(self, name: str, stacks_before: Stacks,
                          bases_before: dict[str, str]) -> list[str]:
        """Writes a line per stack whose changes left it: submitted onto the
        base branch, or abandoned.

        Args:
            name: the repository's name.
            stacks_before: the stacks at the look before.
            bases_before: the stacks' base branches at the look before.

        Returns:
            The lines.
        """
        lines = []
        default_base = base_branch(self.repository)
        for stack_topic, before in stacks_before.items():
            base = bases_before.get(stack_topic, default_base)
            left = [commit for change_id, commit in before.items()
                    if change_id not in self.stacks.get(stack_topic, {})]
            gone = [commit for commit in left if is_ancestor(commit, base, self.repository)]
            is_abandoned = bool(left) and not gone and bool(git(
                'rev-parse', '--verify', '--quiet', f'{ABANDONED_PREFIX}{stack_topic}',
                check=False, repository=self.repository))
            if is_abandoned:
                lines.append(f'abandoned {name} {stack_topic}')
            if gone:
                lines.append(_submitted_line(name, stack_topic, len(gone)))
        return lines

    def _submitted_changes(self, current: list[Change], stacks_before: Stacks,
                           bases_before: dict[str, str]) -> list[Change]:
        """Reads the changes that left a stack onto its base branch.

        Args:
            current: the stacks' changes now.
            stacks_before: the stacks at the look before.
            bases_before: the stacks' base branches at the look before.

        Returns:
            The changes, marked submitted, with their commits' notes.
        """
        current_ids = {change.change_id for change in current}
        default_base = base_branch(self.repository)
        submitted = []
        for stack_topic, before in stacks_before.items():
            base = bases_before.get(stack_topic, default_base)
            for change_id, commit in before.items():
                if change_id in current_ids or not is_ancestor(commit, base, self.repository):
                    continue
                change = change_from(commit, '', read_note(commit, self.repository),
                                     self.repository, change_id)
                change.is_submitted = True
                submitted.append(change)
        return submitted


def watch(repositories: list[str | None], output: TextIO = sys.stdout,
          topic: str | None = None, since: int | None = None,
          minutes: float | None = None) -> None:
    """Prints one line per change with new sent messages, one per vote, and
    one per stack that was submitted, rebased or needs a rebase.

    First it says what happened since the user's last watch ended, so a
    restart loses nothing: the time of the last reported event is kept in
    the state file watch. Then it runs until stopped, or for the given
    minutes, ending with a line that says so. It only looks closer when a
    ref of the review changed, and it prints nothing else, so watching
    costs nothing while nothing happens.

    Args:
        repositories: the repositories' directories, None for the current
            one.
        output: where the lines go.
        topic: the one stack to watch, None for all.
        since: the time to catch up from, seconds since 1970. None for the
            end of the user's last watch.
        minutes: how long to run, None until stopped.
    """
    end = time.time() + 60 * minutes if minutes else None
    if since is None:
        since = last_watched(topic)

    def say(line: str) -> None:
        """Prints a line at once.

        Args:
            line: the line.
        """
        print(line, file=output, flush=True)

    def mark_reported(moment: float) -> None:
        """Keeps when the watch last reported something.

        Args:
            moment: the time of the look that found it, seconds since 1970.
        """
        set_state('watch', _watch_key(topic), str(int(moment)))

    # The first look, which also catches up
    watches = []
    for repository in repositories:
        checked = time.time()
        version = stack_version(repository)
        current = changes_in_stack(repository)
        repository_watch = _RepositoryWatch(repository, topic, version, current)
        watches.append(repository_watch)
        if since is not None and repository_watch.catch_up(since, current, say):
            mark_reported(checked)
    # Then a look per interval
    while end is None or time.time() < end:
        time.sleep(_WATCH_INTERVAL_SECONDS)
        for repository_watch in watches:
            checked, lines = repository_watch.look()
            for line in lines:
                say(line)
            if lines:
                mark_reported(checked)
    say('watch ended, restart it to continue')
