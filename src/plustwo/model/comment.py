# SPDX-License-Identifier: Zlib
"""A comment thread and its states."""

from dataclasses import dataclass, field, replace

from plustwo.model.acknowledgement import says_nothing
from plustwo.model.message import Message
from plustwo.model.move import Move
from plustwo.model.place import NEW_SIDE, PATCHSET_LEVEL, place_label
from plustwo.model.roles import REVIEWER


# A thread is unresolved or resolved, like in Gerrit
UNRESOLVED = 'unresolved'
RESOLVED = 'resolved'
STATUSES = (UNRESOLVED, RESOLVED)

# Status names of older notes and commands, mapped to what they mean now
OLD_STATUSES = {'open': UNRESOLVED, 'agreed': UNRESOLVED, 'dropped': RESOLVED, 'done': RESOLVED}

# The replies Gerrit offers with one click. Both resolve the thread.
DONE = 'Done'
ACK = 'Ack'


@dataclass
class Comment:
    """A comment thread on one place of a change, stored in the review note
    with these fields as keys.

    Like in Gerrit, the thread is unresolved or resolved as its last sent
    message that says so has set it, unresolved if none did.

    Attributes:
        number: the thread's number in its change, shown as #3. Numbers
            never change: deleting a draft thread leaves a gap.
        place: a file, or a file and a line like src/main.cc:12. The line
            counts in the new version of the file, or with side OLD_SIDE in
            the old version, for lines the change removed.
        patch_set: the patch set the place counts on, the one the thread
            was written on.
        messages: the messages, oldest first, drafts at the end.
        side: the side the place is on, NEW_SIDE or OLD_SIDE.
        id: an id that never changes, unlike a place.
        moves: moves by hand, in the order they were made, see anchored().
    """
    number: int
    place: str
    patch_set: int
    messages: list[Message] = field(default_factory=list)
    side: str = NEW_SIDE
    id: str = ''
    moves: list[Move] = field(default_factory=list)

    def anchored(self, patch_set: int | None = None) -> 'Comment':
        """Places the thread where it is on a patch set: at the newest move
        up to that patch set, else where it was written.

        Args:
            patch_set: the patch set, None for the newest.

        Returns:
            A copy with that place, side and patch set. The same object when
            no move counts.
        """
        moves = [(move.patch_set, index, move) for index, move in enumerate(self.moves)
                 if patch_set is None or move.patch_set <= patch_set]
        if not moves:
            return self
        move = max(moves, key=lambda entry: entry[:2])[2]
        return replace(self, place=move.place, side=move.side, patch_set=move.patch_set)

    def entries(self) -> list[Message | Move]:
        """Lists the messages and moves in the order they happened.

        Returns:
            The messages and moves. Moves go before the drafts, messages of
            old notes without a time first.
        """
        moves = sorted(self.moves, key=lambda move: move.time)
        entries: list[Message | Move] = []
        for message in self.messages:
            while moves and (not message.sent or moves[0].time < message.time):
                entries.append(moves.pop(0))
            entries.append(message)
        return entries + moves

    @property
    def status(self) -> str:
        """The thread's status from its sent messages, UNRESOLVED or
        RESOLVED.
        """
        flags = [message.sets_unresolved() for message in self.messages
                 if message.sent and message.sets_unresolved() is not None]
        return UNRESOLVED if not flags or flags[-1] else RESOLVED

    def is_unresolved(self) -> bool:
        """Tells whether the thread is unresolved, drafts left out.

        Returns:
            True if the status is UNRESOLVED.
        """
        return UNRESOLVED == self.status

    def draft_status(self) -> str | None:
        """Tells the status the thread gets when its draft is sent.

        Returns:
            UNRESOLVED or RESOLVED, None if the draft doesn't change the
            status or there is no draft.
        """
        if not self.has_draft() or self.messages[-1].unresolved is None:
            return None
        return UNRESOLVED if self.messages[-1].unresolved else RESOLVED

    def place_label(self) -> str:
        """Writes the thread's place for display, see place_label().

        Returns:
            The place, like src/main.cc:12 (old).
        """
        return place_label(self.place, self.side)

    def is_draft(self) -> bool:
        """Tells whether the thread exists only as the reviewer's draft.

        Returns:
            True if no message is sent yet.
        """
        return all(not message.sent for message in self.messages)

    def has_draft(self) -> bool:
        """Tells whether the thread ends with an unsent reviewer message.

        Returns:
            True if the last message is a draft.
        """
        return bool(self.messages) and not self.messages[-1].sent

    def last_sent(self) -> Message | None:
        """Finds the last sent message.

        Returns:
            The message, None if nothing is sent yet.
        """
        return next((message for message in reversed(self.messages) if message.sent), None)

    def is_unanswered(self) -> bool:
        """Tells whether the last sent message is the reviewer's.

        Returns:
            True if the reviewer wrote the last sent message.
        """
        last_sent = self.last_sent()
        return last_sent is not None and REVIEWER == last_sent.author

    def wants_answer(self) -> bool:
        """Tells whether the author has to answer: the reviewer wrote last
        and the thread is unresolved. A message on the whole change is
        resolved, but asks all the same, unless it says nothing.

        Returns:
            True if the author has to answer.
        """
        last_sent = self.last_sent()
        if last_sent is None or not self.is_unanswered():
            return False
        return self.is_unresolved() or (PATCHSET_LEVEL == self.place
                                        and not says_nothing(last_sent.text))
