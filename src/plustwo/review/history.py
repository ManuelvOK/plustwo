# SPDX-License-Identifier: Zlib
"""The patch set history of a change."""

from dataclasses import dataclass

from plustwo.model.comment import UNRESOLVED
from plustwo.model.roles import REVIEWER
from plustwo.model.version import LATEST
from plustwo.model.vote import Vote
from plustwo.review.labels import message_text
from plustwo.review.patch_sets import patch_set_kind


@dataclass
class PatchSetEvent:
    """Something that happened on a patch set: a thread started on it
    (THREAD), a reply while it was the newest (REPLY) or a vote on it
    (VOTE). number and place are the thread's, text the first line of the
    message, or the vote like Code-Review+2. status is the thread's now.
    """
    kind: str
    time: int
    author: str
    text: str
    number: int | None = None
    place: str = ''
    status: str = ''
    draft: bool = False

    def is_open(self):
        """Whether the event's thread is still unresolved."""
        return THREAD == self.kind and UNRESOLVED == self.status and not self.draft

    def is_yours(self):
        """Whether the reviewer, the viewer of the interfaces, wrote it or
        voted, drafts included.
        """
        return REVIEWER == self.author


THREAD = 'thread'


REPLY = 'reply'


VOTE = 'vote'


@dataclass
class PatchSetEntry:
    """One patch set in a change's history, see patch_set_history()."""
    number: int
    commit: str
    uploaded: int
    uploader: str
    # Its kind against the patch set before, see patch_set_kind(), '-' for
    # the first
    kind: str
    votes: list[Vote]
    events: list[PatchSetEvent]

    def new_threads(self):
        """The threads started on this patch set."""
        return [event for event in self.events if THREAD == event.kind]

    def replies(self):
        """The replies sent while it was the newest patch set."""
        return [event for event in self.events if REPLY == event.kind]

    def open_threads(self):
        """The threads started on it that are still unresolved."""
        return [event for event in self.events if event.is_open()]

    def comments_label(self, compact=False):
        """Whose threads and replies these are, like "you: 2 threads (1
        open), 1 reply · author: 5 replies", "you" being the reviewer.
        compact counts messages only, like "you 3 (1 open) · author 5".
        Empty without any.
        """
        parts = []
        for name, yours in (('you', True), ('author', False)):
            events = [event for event in self.events
                      if VOTE != event.kind and yours == event.is_yours()]
            threads = sum(THREAD == event.kind for event in events)
            replies = len(events) - threads
            open_ = sum(event.is_open() for event in events)
            open_text = f' ({open_} open)' if open_ else ''
            if compact:
                if events:
                    parts.append(f'{name} {len(events)}{open_text}')
                continue
            counts = []
            if threads:
                counts.append(f'{threads} thread{"s" if threads > 1 else ""}{open_text}')
            if replies:
                counts.append(f'{replies} repl{"y" if 1 == replies else "ies"}')
            if counts:
                parts.append(f'{name}: ' + ', '.join(counts))
        return ' · '.join(parts)

    def vote_label(self, label):
        """The vote of a label on it for display, like +2, in parentheses
        when only copied from an earlier one. Empty without a vote. Like
        Gerrit, the lowest counts if negative, else the highest.
        """
        votes = [vote for vote in self.votes if label == vote.label]
        if not votes:
            return ''
        values = [vote.value for vote in votes]
        value = min(values) if min(values) < 0 else max(values)
        text = f'{value:+d}' if value else '0'
        return f'({text})' if all(vote.copied_from for vote in votes) else text


def newest_patch_set_at(change, when):
    """The patch set that was the newest at a time, by the upload times,
    the first if none was uploaded then. 0 for a change without one.
    """
    numbers = sorted(change.patch_sets)
    uploaded = [number for number in numbers if change.uploads.get(number, 0) <= when]
    return uploaded[-1] if uploaded else (numbers[0] if numbers else 0)


def message_patch_sets(change, comment, shown=None):
    """The patch set each message of a thread belongs to, like Gerrit's
    "Patchset 3" on each comment: the first message's is the thread's, a
    reply's the newest when it was sent, as in patch_set_history(). Drafts
    belong to shown, the patch set looked at, the newest without one.
    Messages of older notes without a time get the thread's.
    """
    latest = change.latest_patch_set()
    shown = shown if shown in change.patch_sets else latest
    result = []
    for index, message in enumerate(comment.messages):
        if 0 == index or (message.sent and not message.time):
            result.append(comment.patch_set)
        elif not message.sent:
            result.append(shown)
        else:
            result.append(newest_patch_set_at(change, message.time))
    return result


def patch_set_history(change, shown=None, repository=None):
    """The change's patch sets, oldest first, as PatchSetEntry: when and by
    whom each was uploaded, its kind against the one before (computed, so
    older notes work), the votes on it and what happened on it, see
    patch_set_events().
    """
    numbers = sorted(change.patch_sets)
    events = patch_set_events(change, shown)
    history = []
    for index, number in enumerate(numbers):
        commit = change.patch_sets[number]
        kind = (patch_set_kind(change.patch_sets[numbers[index - 1]], commit, repository)
                if index else '-')
        history.append(PatchSetEntry(number, commit, change.uploads.get(number, 0),
                                     change.uploaders.get(number, ''), kind,
                                     [vote for vote in change.votes if number == vote.patch_set],
                                     events[number]))
    return history


def patch_set_events(change, shown=None):
    """What happened on each patch set: number -> PatchSetEvent list, in
    order, without running git. A thread belongs to the patch set it was
    started on. A reply belongs to the patch set that was the newest when
    it was sent, by the upload times, like Gerrit's comments per patch set.
    Draft replies belong to shown, the patch set the reviewer looks at, the
    newest without one. A draft thread belongs to the patch set it is on,
    like a sent one.
    """
    numbers = sorted(change.patch_sets)
    if not numbers:
        return {}
    shown = shown if shown in change.patch_sets else numbers[-1]

    def newest_at(when):
        return newest_patch_set_at(change, when)

    events = {number: [] for number in numbers}
    for comment in change.comments:
        for index, message in enumerate(comment.messages):
            text = message_text(message).split('\n')[0]
            if 0 == index:
                number = (comment.patch_set if comment.patch_set in events
                          else shown if not message.sent else numbers[-1])
                events[number].append(PatchSetEvent(
                    THREAD, message.time, message.author, text, comment.number, comment.place,
                    comment.status, comment.is_draft()))
                continue
            if not message.sent:
                number = shown
            elif message.time:
                number = newest_at(message.time)
            else:
                number = comment.patch_set if comment.patch_set in events else numbers[-1]
            events[number].append(PatchSetEvent(REPLY, message.time, message.author, text,
                                                comment.number, comment.place, comment.status,
                                                not message.sent))
    for vote in change.votes:
        if vote.patch_set in events and not vote.copied_from:
            events[vote.patch_set].append(PatchSetEvent(
                VOTE, vote.time, vote.by, f'{vote.label}{vote.value:+d}'
                + (f': {vote.message.split(chr(10))[0]}' if vote.message else '')))
    # Drafts have no time yet: they come last.
    return {number: sorted(found, key=lambda event: (event.draft, event.time))
            for number, found in events.items()}


def follow_latest(compare, before, now, follow=True):
    """The versions to compare after the latest patch set went from before
    to now: with follow, a right side on the latest stays "latest" and
    moves along, else it stays on the patch set it was. Returns the
    versions and whether the right side moved.
    """
    first, second = compare
    if now <= before or LATEST != second:
        return compare, False
    if follow:
        return compare, True
    return (first, str(before)), False


def right_side(change, version, follow=True):
    """A version picked for the right side: with follow, the latest patch
    set is kept as "latest", so it moves along with a new upload.
    """
    return LATEST if follow and version == str(change.latest_patch_set()) else version
