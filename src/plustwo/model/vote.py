# SPDX-License-Identifier: Zlib
"""Votes and their labels, like Gerrit's."""

from dataclasses import dataclass

from plustwo.git.errors import ChangeError
from plustwo.model.roles import REVIEWER


# The labels, named like Gerrit's
CODE_REVIEW = 'Code-Review'
VERIFIED = 'Verified'

# Each label, mapped to its lowest and highest vote. Submitting needs the
# highest from someone and the lowest from no one.
LABEL_RANGES = {CODE_REVIEW: (-2, 2), VERIFIED: (-1, 1)}

# The short and lower case names of the labels, mapped to the labels
_LABEL_ALIASES = {'cr': CODE_REVIEW, 'code-review': CODE_REVIEW, 'v': VERIFIED,
                  'verified': VERIFIED}

# Each label, mapped to who votes when no one is named
DEFAULT_VOTERS = {CODE_REVIEW: REVIEWER, VERIFIED: 'ci'}


@dataclass
class Vote:
    """A vote on one patch set, like Code-Review+2 by the reviewer, stored
    in the review note with these fields as keys.

    Attributes:
        label: the label, CODE_REVIEW or VERIFIED.
        value: the vote, within the label's range in LABEL_RANGES.
        by: who voted, like REVIEWER or ci.
        patch_set: the patch set the vote is on.
        message: a message with the vote, like the end of a check's log.
        copied_from: the patch set the vote was copied from, None if given
            on this one.
        time: when it was given, seconds since 1970.
    """
    label: str
    value: int
    by: str
    patch_set: int
    message: str = ''
    copied_from: int | None = None
    time: int = 0

    def copied(self, patch_set: int) -> 'Vote':
        """Copies this vote to a newer patch set.

        Args:
            patch_set: the newer patch set.

        Returns:
            The copy, which remembers the patch set it was first given on.
        """
        return Vote(label=self.label, value=self.value, by=self.by, patch_set=patch_set,
                    message=self.message, copied_from=self.copied_from or self.patch_set,
                    time=self.time)


def label_name(label: str) -> str:
    """Finds the label a name stands for.

    Args:
        label: a name like cr, Code-Review or v, in any case.

    Returns:
        The label, CODE_REVIEW or VERIFIED.

    Raises:
        ChangeError: the name stands for no label.
    """
    name = _LABEL_ALIASES.get(label.lower())
    if name is None:
        raise ChangeError(f'unknown label "{label}", use {CODE_REVIEW} or {VERIFIED}')
    return name
