# SPDX-License-Identifier: Zlib
"""Votes and their labels, like Gerrit's."""

from dataclasses import dataclass

from plustwo.git.errors import ChangeError
from plustwo.model.roles import REVIEWER


CODE_REVIEW = 'Code-Review'


VERIFIED = 'Verified'


# The lowest and highest vote of a label. Submitting needs the highest from
# someone and the lowest from no one.
LABEL_RANGES = {CODE_REVIEW: (-2, 2), VERIFIED: (-1, 1)}


LABEL_ALIASES = {'cr': CODE_REVIEW, 'code-review': CODE_REVIEW, 'v': VERIFIED,
                 'verified': VERIFIED}


# Who votes when no one is named
DEFAULT_VOTERS = {CODE_REVIEW: REVIEWER, VERIFIED: 'ci'}


@dataclass
class Vote:
    """A vote on one patch set, like Code-Review+2 by the reviewer."""
    label: str
    value: int
    by: str
    patch_set: int
    message: str = ''
    # The patch set the vote was copied from, None if given on this one
    copied_from: int | None = None
    time: int = 0

    def copied(self, patch_set):
        """This vote copied to a newer patch set."""
        return Vote(label=self.label, value=self.value, by=self.by, patch_set=patch_set,
                    message=self.message, copied_from=self.copied_from or self.patch_set,
                    time=self.time)


def label_name(label):
    """The label a name like cr, Code-Review or v stands for."""
    name = LABEL_ALIASES.get(label.lower())
    if name is None:
        raise ChangeError(f'unknown label "{label}", use {CODE_REVIEW} or {VERIFIED}')
    return name
