# SPDX-License-Identifier: Zlib
"""A message of a thread."""

from dataclasses import dataclass

from plustwo.model.acknowledgement import is_acknowledgement
from plustwo.model.roles import REVIEWER


@dataclass
class Message:
    """One message in a comment thread, stored in the review note with
    these fields as keys.

    A reviewer's message starts as a draft, which only the reviewer sees,
    until the reviewer sends it. Like in Gerrit, a message can resolve its
    thread or make it unresolved again.

    Attributes:
        author: who wrote it, REVIEWER or AUTHOR.
        text: the text, '' for a message that only changes the status.
        sent: whether it is sent, False for a reviewer's draft.
        unresolved: True if it makes its thread unresolved, False if it
            resolves it, None if it leaves the thread as it is. Named like
            Gerrit's field.
        time: when it was sent, seconds since 1970. 0 for drafts and older
            notes.
    """
    author: str
    text: str
    sent: bool = True
    unresolved: bool | None = None
    time: int = 0

    def sets_unresolved(self) -> bool | None:
        """Tells what the message makes its thread. A reviewer's
        acknowledgement without a flag resolves, also in notes written
        before typed acknowledgements were resolved when written.

        Returns:
            True for unresolved, False for resolved, None for as it was.
        """
        if self.unresolved is None and REVIEWER == self.author and is_acknowledgement(self.text):
            return False
        return self.unresolved
