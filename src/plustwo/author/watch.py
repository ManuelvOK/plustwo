# SPDX-License-Identifier: Zlib
"""Watching repositories for the author."""

import re
import sys
import time

from plustwo.author.inbox import WATCH_INTERVAL_SECONDS
from plustwo.config.registry import repository_name
from plustwo.config.settings import base_branch
from plustwo.config.state import set_state, state_values
from plustwo.config.user import current_user
from plustwo.git.errors import ChangeError
from plustwo.git.notes import read_note, read_notes
from plustwo.git.refs import ABANDONED_PREFIX, PATCH_SET_PREFIX, is_ancestor, refs_with_prefix
from plustwo.git.repository import git
from plustwo.model.acknowledgement import says_nothing
from plustwo.model.place import PATCHSET_LEVEL
from plustwo.model.roles import AUTHOR, REVIEWER
from plustwo.review.changes import change_from, changes_in_stack
from plustwo.review.labels import message_text
from plustwo.review.stacks import stack_bases, stack_version


def events_of(repository, topic=None, changes=None, submitted=()):
    """Everything sent, voted or rebased in a repository, as event keys.
    changes are the stacks' changes if read already, submitted more changes
    to count in, which left the stacks.
    """
    events = set()
    if changes is None:
        changes = changes_in_stack(repository)
    changes = [change for change in changes if not topic or topic == change.topic]
    for change in changes + list(submitted):
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
    for topic, top in tops.items():
        if top.rebase_is_requested:
            events.add(('rebase', topic, top.change_id))
        if top.rebased_patch_set:
            events.add(('rebased', topic, top.change_id, top.rebased_patch_set))
    return events


def stacks_of(repository, topic=None):
    """The changes of each stack: topic -> {Change-Id: commit}."""
    stacks = {}
    for change in changes_in_stack(repository):
        if topic and topic != change.topic:
            continue
        stacks.setdefault(change.topic, {})[change.change_id] = change.commit
    return stacks


def watch_key(topic=None):
    """The key of the watch state file that keeps when the current user's
    watch (of one topic, if given) last reported something.
    """
    return f'watch.{current_user()}{" " + topic if topic else ""}.time'


def last_watched(topic=None):
    """When the user's last watch reported its last event, seconds since
    1970, None if never.
    """
    values = state_values('watch', watch_key(topic))
    return int(values[-1]) if values and values[-1].isdigit() else None


def parse_time(text):
    """A time like 1763030000 (seconds since 1970), 2026-11-13T10:00 or
    10:00 (today), as seconds since 1970.
    """
    if text.isdigit():
        return int(text)
    import datetime  # pylint: disable=import-outside-toplevel
    try:
        if re.fullmatch(r'\d{1,2}:\d{2}(:\d{2})?', text):
            moment = datetime.datetime.combine(datetime.date.today(),
                                               datetime.time.fromisoformat(text.zfill(5)))
        else:
            moment = datetime.datetime.fromisoformat(text)
    except ValueError as error:
        raise ChangeError(f'not a time: {text}, give e.g. 2026-11-13T10:00 or 10:00') from error
    return int(moment.timestamp())


def submitted_since(repository, since, topic=None, changes=None):
    """Changes submitted at or after since, by the submit record in their
    notes, see submit(). changes are the stacks' changes if read already,
    which are left out.
    """
    if changes is None:
        changes = changes_in_stack(repository)
    in_review = {change.change_id for change in changes}
    latest = {}
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


def changes_by_id(changes):
    """Change-Id -> every copy of the change, see event_message(). One
    change can be in several stacks, e.g. an old commit that stayed the
    base of other branches, and only some copies have all its messages.
    """
    by_id = {}
    for change in changes:
        by_id.setdefault(change.change_id, []).append(change)
    return by_id


def event_message(event, by_id):
    """The change, thread and message of a message event of events_of(),
    from the first copy of the change that has it.
    """
    _, change_id, number, index = event
    for change in by_id[change_id]:
        comment = change.comment(number)
        if comment is not None and index < len(comment.messages):
            return change, comment, comment.messages[index]
    raise ChangeError(f'{change_id[:8]} has no message {index} in thread #{number}')


def event_time(event, by_id):
    """When an event of events_of() happened, None if it doesn't say."""
    if 'message' == event[0]:
        return event_message(event, by_id)[2].time
    if 'vote' == event[0]:
        _, change_id, label, value, patch_set, by = event
        return next((vote.time for change in by_id[change_id] for vote in change.votes
                     if (vote.label, vote.value, vote.patch_set, vote.by)
                     == (label, value, patch_set, by)), None)
    if 'rebased' == event[0]:
        return next((change.uploads[event[3]] for change in by_id[event[2]]
                     if event[3] in change.uploads), None)
    return None


def report_events(name, new, by_id, say):
    """Says the new events of a repository, see watch()."""
    new_messages = {}
    for event in sorted(new, key=str):
        if 'message' == event[0]:
            change, comment, message = event_message(event, by_id)
            if comment.is_unresolved() or (PATCHSET_LEVEL == comment.place
                                           and not says_nothing(message.text)):
                new_messages[event[1]] = new_messages.get(event[1], 0) + 1
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
    for change_id, count in new_messages.items():
        say(f'review {name} {change_id[:8]}: {count} new')


def submitted_line(name, topic, count):
    """The line of watch() for a stack's submitted changes."""
    return f'submitted {name} {topic}: {count} change' + ('s' if count > 1 else '')


def watch(repositories, output=sys.stdout, topic=None, since=None, minutes=None):
    """Prints one line per change with new sent messages, one per vote, and
    one per stack that was submitted, rebased or needs a rebase.

    First it says what happened since the user's last watch ended (or
    since, seconds since 1970), so a restart loses nothing: the time of
    the last reported event is kept in the state file watch. A rebase
    request still open is said again. Then it runs until stopped, or for
    the given minutes, ending with a line that says so. It only looks
    closer when a ref of the review changed, and it prints nothing else,
    so watching costs nothing while nothing happens.
    """
    end = time.time() + 60 * minutes if minutes else None
    if since is None:
        since = last_watched(topic)

    def say(line):
        print(line, file=output, flush=True)

    def reported(moment):
        set_state('watch', watch_key(topic), str(int(moment)))

    versions, known, stacks, bases = {}, {}, {}, {}
    for repository in repositories:
        checked = time.time()
        versions[repository] = stack_version(repository)
        current = changes_in_stack(repository)
        known[repository] = events_of(repository, topic, current)
        stacks[repository] = stacks_of(repository, topic)
        # The stacks' base branches, as they were before a submit
        bases[repository] = stack_bases(repository)
        if since is None:
            continue
        # What happened while no watch ran
        submitted = submitted_since(repository, since, topic, current)
        by_id = changes_by_id(current + submitted)
        missed = {event for event in events_of(repository, topic, current, submitted)
                  if 'rebase' == event[0] or (event_time(event, by_id) or 0) >= since}
        name = repository_name(repository)
        counts = {}
        for change in submitted:
            counts[change.topic] = counts.get(change.topic, 0) + 1
        for stack_topic, count in counts.items():
            say(submitted_line(name, stack_topic, count))
        report_events(name, missed, by_id, say)
        if missed or counts:
            reported(checked)
    while end is None or time.time() < end:
        time.sleep(WATCH_INTERVAL_SECONDS)
        for repository in repositories:
            version = stack_version(repository)
            if version == versions[repository]:
                continue
            checked = time.time()
            versions[repository] = version
            current = changes_in_stack(repository)
            name = repository_name(repository)
            lines = []

            # Changes that left a stack and are on its base branch now
            now = stacks_of(repository, topic)
            stacks_before = stacks[repository]
            default = base_branch(repository)
            bases_before = bases[repository]
            bases[repository] = {**bases_before, **stack_bases(repository)}
            for stack_topic, before in stacks_before.items():
                base = bases_before.get(stack_topic, default)
                left = [commit for change_id, commit in before.items()
                        if change_id not in now.get(stack_topic, {})]
                gone = [commit for commit in left if is_ancestor(commit, base, repository)]
                if left and not gone and git('rev-parse', '--verify', '--quiet',
                                             f'{ABANDONED_PREFIX}{stack_topic}', check=False,
                                             repository=repository):
                    lines.append(f'abandoned {name} {stack_topic}')
                if gone:
                    lines.append(submitted_line(name, stack_topic, len(gone)))
            stacks[repository] = now
            # The submitted changes' last messages and votes, e.g. a reply
            # sent together with the submit, are in their commits' notes.
            submitted = []
            for stack_topic, before in stacks_before.items():
                base = bases_before.get(stack_topic, default)
                for change_id, commit in before.items():
                    if change_id not in {change.change_id for change in current} and \
                            is_ancestor(commit, base, repository):
                        change = change_from(commit, '', read_note(commit, repository),
                                             repository, change_id)
                        change.is_submitted = True
                        submitted.append(change)
            events = events_of(repository, topic, current, submitted)
            new = events - known[repository]
            known[repository] = events
            by_id = changes_by_id(current + submitted)
            report_events(name, new, by_id, lines.append)
            for line in lines:
                say(line)
            if lines:
                reported(checked)
    say('watch ended, restart it to continue')
