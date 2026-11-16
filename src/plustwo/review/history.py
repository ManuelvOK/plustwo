# SPDX-License-Identifier: Zlib
"""The patch set history of a change."""

from dataclasses import dataclass

from plustwo.model.change import Change
from plustwo.model.comment import UNRESOLVED, Comment
from plustwo.model.roles import REVIEWER
from plustwo.model.version import LATEST
from plustwo.model.vote import Vote
from plustwo.review.labels import message_text
from plustwo.review.patch_sets import patch_set_type


# Types of events on a patch set, see PatchSetEvent
THREAD = 'thread'
REPLY = 'reply'
VOTE = 'vote'


@dataclass
class PatchSetEvent:
    """Something that happened on a patch set.

    Attributes:
        type: THREAD for a thread started on it, REPLY for a reply while it
            was the newest, VOTE for a vote on it.
        time: when it happened, seconds since 1970, 0 for a draft.
        author: who wrote or voted.
        text: the first line of the message, or the vote like
            Code-Review+2.
        number: the thread's number, None for a vote.
        place: the thread's place, '' for a vote.
        status: the thread's status now, '' for a vote.
        is_draft: whether it is the reviewer's draft.
    """
    type: str
    time: int
    author: str
    text: str
    number: int | None = None
    place: str = ''
    status: str = ''
    is_draft: bool = False

    def is_open(self) -> bool:
        """Tells whether the event started a thread that is still
        unresolved.

        Returns:
            True for a sent thread that is unresolved.
        """
        return THREAD == self.type and UNRESOLVED == self.status and not self.is_draft

    def is_yours(self) -> bool:
        """Tells whether the viewer of the interfaces, the reviewer, wrote
        it or voted, drafts included.

        Returns:
            True if the reviewer is the author.
        """
        return REVIEWER == self.author


@dataclass
class PatchSetEntry:
    """One patch set in a change's history, see patch_set_history().

    Attributes:
        number: the patch set's number.
        commit: its commit.
        upload_time: when it was uploaded, seconds since 1970, 0 if
            unknown.
        uploader: who uploaded it, '' if unknown.
        type: its type against the patch set before, see
            patch_set_type(), '-' for the first.
        votes: the votes on it.
        events: what happened on it, see patch_set_events().
    """
    number: int
    commit: str
    upload_time: int
    uploader: str
    type: str
    votes: list[Vote]
    events: list[PatchSetEvent]

    def new_threads(self) -> list[PatchSetEvent]:
        """Lists the threads started on this patch set.

        Returns:
            The events of type THREAD.
        """
        return [event for event in self.events if THREAD == event.type]

    def replies(self) -> list[PatchSetEvent]:
        """Lists the replies sent while it was the newest patch set.

        Returns:
            The events of type REPLY.
        """
        return [event for event in self.events if REPLY == event.type]

    def open_threads(self) -> list[PatchSetEvent]:
        """Lists the threads started on it that are still unresolved.

        Returns:
            The open events, see PatchSetEvent.is_open().
        """
        return [event for event in self.events if event.is_open()]

    def comments_label(self, is_compact: bool = False) -> str:
        """Writes whose threads and replies these are, "you" being the
        reviewer.

        Args:
            is_compact: whether to count messages only, like "you 3 (1
                open) · author 5".

        Returns:
            A label like "you: 2 threads (1 open), 1 reply · author: 5
            replies", '' without any.
        """
        parts = []
        for name, are_yours in (('you', True), ('author', False)):
            events = [event for event in self.events
                      if VOTE != event.type and are_yours == event.is_yours()]
            n_threads = sum(THREAD == event.type for event in events)
            n_replies = len(events) - n_threads
            n_open = sum(event.is_open() for event in events)
            open_text = f' ({n_open} open)' if n_open else ''
            if is_compact:
                if events:
                    parts.append(f'{name} {len(events)}{open_text}')
                continue
            counts = []
            if n_threads:
                counts.append(f'{n_threads} thread{"s" if n_threads > 1 else ""}{open_text}')
            if n_replies:
                counts.append(f'{n_replies} repl{"y" if 1 == n_replies else "ies"}')
            if counts:
                parts.append(f'{name}: ' + ', '.join(counts))
        return ' · '.join(parts)

    def vote_label(self, label: str) -> str:
        """Writes the vote of a label on it for display. Like Gerrit, the
        lowest counts if negative, else the highest.

        Args:
            label: the label, CODE_REVIEW or VERIFIED.

        Returns:
            The vote like +2, in parentheses when only copied from an
            earlier patch set. '' without a vote.
        """
        votes = [vote for vote in self.votes if label == vote.label]
        if not votes:
            return ''
        values = [vote.value for vote in votes]
        value = min(values) if min(values) < 0 else max(values)
        text = f'{value:+d}' if value else '0'
        return f'({text})' if all(vote.copied_from for vote in votes) else text


def newest_patch_set_at(change: Change, when: int) -> int:
    """Finds the patch set that was the newest at a time, by the upload
    times.

    Args:
        change: the change.
        when: the time, seconds since 1970.

    Returns:
        The patch set's number, the first if none was uploaded then. 0 for
        a change without one.
    """
    numbers = sorted(change.patch_sets)
    uploaded = [number for number in numbers if change.uploads.get(number, 0) <= when]
    return uploaded[-1] if uploaded else (numbers[0] if numbers else 0)


def message_patch_sets(change: Change, comment: Comment, shown: int | None = None) -> list[int]:
    """Finds the patch set each message of a thread belongs to, like
    Gerrit's "Patchset 3" on each comment: the first message's is the
    thread's, a reply's the newest when it was sent, as in
    patch_set_history(). Messages of older notes without a time get the
    thread's.

    Args:
        change: the thread's change.
        comment: the thread.
        shown: the patch set looked at, which drafts belong to. None for
            the newest.

    Returns:
        A patch set's number per message.
    """
    latest = change.latest_patch_set()
    shown = shown if shown in change.patch_sets else latest
    numbers = []
    for index, message in enumerate(comment.messages):
        if 0 == index or (message.sent and not message.time):
            numbers.append(comment.patch_set)
        elif not message.sent:
            numbers.append(shown)
        else:
            numbers.append(newest_patch_set_at(change, message.time))
    return numbers


def patch_set_history(change: Change, shown: int | None = None,
                      repository: str | None = None) -> list[PatchSetEntry]:
    """Lists the change's patch sets with what happened on them.

    Args:
        change: the change.
        shown: the patch set the reviewer looks at, see patch_set_events().
        repository: the repository's directory, None for the current one.

    Returns:
        An entry per patch set, oldest first: when and by whom it was
        uploaded, its type against the one before (computed, so older
        notes work), the votes on it and its events.
    """
    numbers = sorted(change.patch_sets)
    events = patch_set_events(change, shown)
    history = []
    for index, number in enumerate(numbers):
        commit = change.patch_sets[number]
        patch_set_type_text = (patch_set_type(change.patch_sets[numbers[index - 1]], commit,
                                              repository) if index else '-')
        history.append(PatchSetEntry(number, commit, change.uploads.get(number, 0),
                                     change.uploaders.get(number, ''), patch_set_type_text,
                                     [vote for vote in change.votes if number == vote.patch_set],
                                     events[number]))
    return history


def patch_set_events(change: Change, shown: int | None = None) -> dict[int, list[PatchSetEvent]]:
    """Finds what happened on each patch set, without running git. A thread
    belongs to the patch set it was started on. A reply belongs to the
    patch set that was the newest when it was sent, by the upload times,
    like Gerrit's comments per patch set. A draft thread belongs to the
    patch set it is on, like a sent one.

    Args:
        change: the change.
        shown: the patch set the reviewer looks at, which draft replies
            belong to. None for the newest.

    Returns:
        Each patch set's number, mapped to its events in order, drafts
        last.
    """
    numbers = sorted(change.patch_sets)
    if not numbers:
        return {}
    shown = shown if shown in change.patch_sets else numbers[-1]
    events: dict[int, list[PatchSetEvent]] = {number: [] for number in numbers}
    # The threads and replies
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
                number = newest_patch_set_at(change, message.time)
            else:
                number = comment.patch_set if comment.patch_set in events else numbers[-1]
            events[number].append(PatchSetEvent(REPLY, message.time, message.author, text,
                                                comment.number, comment.place, comment.status,
                                                not message.sent))
    # The votes given on each patch set
    for vote in change.votes:
        if vote.patch_set in events and not vote.copied_from:
            events[vote.patch_set].append(PatchSetEvent(
                VOTE, vote.time, vote.by, f'{vote.label}{vote.value:+d}'
                + (f': {vote.message.split(chr(10))[0]}' if vote.message else '')))
    # Drafts have no time yet: they come last.
    return {number: sorted(found, key=lambda event: (event.is_draft, event.time))
            for number, found in events.items()}


def follow_latest(compare: tuple[str, str], before: int, now: int,
                  does_follow_latest: bool = True) -> tuple[tuple[str, str], bool]:
    """Updates the versions to compare after the latest patch set changed.

    Args:
        compare: the versions compared, (left, right).
        before: the latest patch set's number before.
        now: the latest patch set's number now.
        does_follow_latest: whether a right side on the latest stays
            LATEST and moves along, else it stays on the patch set it was.

    Returns:
        (versions, whether the right side moved).
    """
    first, second = compare
    if now <= before or LATEST != second:
        return compare, False
    if does_follow_latest:
        return compare, True
    return (first, str(before)), False


def right_side(change: Change, version: str, does_follow_latest: bool = True) -> str:
    """Names a version picked for the right side.

    Args:
        change: the change.
        version: the version, a patch set's number or a name.
        does_follow_latest: whether the latest patch set is kept as LATEST,
            so it moves along with a new upload.

    Returns:
        LATEST or the version.
    """
    if does_follow_latest and version == str(change.latest_patch_set()):
        return LATEST
    return version
