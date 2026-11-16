# SPDX-License-Identifier: Zlib
"""A message of a thread."""

from dataclasses import dataclass

from plustwo.model.acknowledgement import is_acknowledgement
from plustwo.model.roles import REVIEWER


@dataclass
class Message:
    """One message in a comment thread.

    A reviewer's message starts as a draft (sent is False), which only the
    reviewer sees, until the reviewer sends it. Like in Gerrit, a message
    can resolve its thread or make it unresolved again: unresolved is
    True or False then, None leaves the thread as it is.
    """
    author: str
    text: str
    sent: bool = True
    unresolved: bool | None = None
    # When it was sent, seconds since 1970. 0 for drafts and older notes.
    time: int = 0

    def sets_unresolved(self):
        """What the message makes its thread: unresolved (True), resolved
        (False) or as it was (None). A reviewer's acknowledgement without a
        flag resolves, also in notes written before typed acknowledgements were
        resolved when written.
        """
        if self.unresolved is None and REVIEWER == self.author and is_acknowledgement(self.text):
            return False
        return self.unresolved
