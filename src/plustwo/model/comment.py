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


# Status names of older notes and commands, and what they mean now
OLD_STATUSES = {'open': UNRESOLVED, 'agreed': UNRESOLVED, 'dropped': RESOLVED, 'done': RESOLVED}


# The replies Gerrit offers with one click. Both resolve the thread.
DONE = 'Done'


ACK = 'Ack'


@dataclass
class Comment:
    """A comment thread on one place of a change.

    place is a file, or a file and a line like src/main.cc:12. The line
    counts in the new version of the file, or with side "old" in the old
    version, for lines the change removed.

    Like in Gerrit, the thread is unresolved or resolved as its last sent
    message that says so has set it, unresolved if none did.
    """
    number: int
    place: str
    patch_set: int
    messages: list[Message] = field(default_factory=list)
    side: str = NEW_SIDE
    # Never changes, unlike a place. Numbers don't change either: deleting a
    # draft thread leaves a gap.
    id: str = ''
    # Moves by hand, in the order they were made, see anchored()
    moves: list[Move] = field(default_factory=list)

    def anchored(self, patch_set=None):
        """The thread with the place it has on a patch set, None for the
        newest: the newest move up to that patch set, the place it was
        written on without one. The same object when it wasn't moved.
        """
        moves = [(move.patch_set, index, move) for index, move in enumerate(self.moves)
                 if patch_set is None or move.patch_set <= patch_set]
        if not moves:
            return self
        move = max(moves, key=lambda entry: entry[:2])[2]
        return replace(self, place=move.place, side=move.side, patch_set=move.patch_set)

    def entries(self):
        """The messages and moves in the order they happened. Moves go
        before the drafts, messages of old notes without a time first.
        """
        moves = sorted(self.moves, key=lambda move: move.time)
        entries = []
        for message in self.messages:
            while moves and (not message.sent or moves[0].time < message.time):
                entries.append(moves.pop(0))
            entries.append(message)
        return entries + moves

    @property
    def status(self):
        """unresolved or resolved, from the sent messages."""
        flags = [message.sets_unresolved() for message in self.messages
                 if message.sent and message.sets_unresolved() is not None]
        return UNRESOLVED if not flags or flags[-1] else RESOLVED

    def is_unresolved(self):
        """Whether the thread is unresolved, drafts left out."""
        return UNRESOLVED == self.status

    def draft_status(self):
        """The status the thread gets when its draft is sent, None if the
        draft doesn't change it or there is no draft.
        """
        if not self.has_draft() or self.messages[-1].unresolved is None:
            return None
        return UNRESOLVED if self.messages[-1].unresolved else RESOLVED

    def place_label(self):
        """The place for display, see place_label()."""
        return place_label(self.place, self.side)

    def is_draft(self):
        """Whether the thread exists only as the reviewer's draft so far."""
        return all(not message.sent for message in self.messages)

    def has_draft(self):
        """Whether the thread ends with an unsent reviewer message."""
        return bool(self.messages) and not self.messages[-1].sent

    def last_sent(self):
        """The last sent message, None if nothing is sent yet."""
        return next((message for message in reversed(self.messages) if message.sent), None)

    def is_unanswered(self):
        """Whether the last sent message is the reviewer's."""
        sent = [message for message in self.messages if message.sent]
        return bool(sent) and REVIEWER == sent[-1].author

    def wants_answer(self):
        """Whether the author has to answer: the reviewer wrote last and the
        thread is unresolved. A message on the whole change is resolved,
        but asks all the same, unless it says nothing.
        """
        if not self.is_unanswered():
            return False
        return self.is_unresolved() or (PATCHSET_LEVEL == self.place
                                        and not says_nothing(self.last_sent().text))
