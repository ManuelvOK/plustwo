# SPDX-License-Identifier: Zlib
"""Voting."""

import time

from plustwo.git.errors import ChangeError
from plustwo.model.change import Change
from plustwo.model.vote import CODE_REVIEW, DEFAULT_VOTERS, LABEL_RANGES, Vote, label_name
from plustwo.review.changes import update_change


def vote_values(label: str = CODE_REVIEW) -> tuple[int, ...]:
    """Lists the values of a label.

    Args:
        label: the label or a name for it, see label_name().

    Returns:
        The values from the lowest to the highest, like -2 .. +2.
    """
    lowest, highest = LABEL_RANGES[label_name(label)]
    return tuple(range(lowest, highest + 1))


def set_vote(change_id: str, label: str, value: int, by: str | None = None,
             patch_set: int | None = None, message: str = '',
             repository: str | None = None) -> int:
    """Votes on a patch set. The vote replaces the voter's earlier votes of
    that label, 0 only removes them.

    Args:
        change_id: the full Change-Id.
        label: the label or a name for it, see label_name().
        value: the vote, within the label's range.
        by: who votes, None for the label's default voter.
        patch_set: the patch set's number, None for the latest.
        message: a message with the vote, '' for none.
        repository: the repository's directory, None for the one the
            change is in review in.

    Returns:
        The patch set's number.

    Raises:
        ChangeError: the label is unknown, the value out of its range, or
            the patch set doesn't exist.
    """
    label = label_name(label)
    voter = by or DEFAULT_VOTERS[label]
    lowest, highest = LABEL_RANGES[label]
    if not lowest <= value <= highest:
        raise ChangeError(f'{label} goes from {lowest:+d} to {highest:+d}')

    def edit(change: Change) -> int:
        """Replaces the voter's votes.

        Args:
            change: the change, read fresh.

        Returns:
            The patch set's number.

        Raises:
            ChangeError: the patch set doesn't exist.
        """
        number = patch_set or change.latest_patch_set()
        if not number:
            raise ChangeError(f'{change.short_id()} has no patch set yet, upload it first')
        if number not in change.patch_sets:
            raise ChangeError(f'{change.short_id()} has no patch set {number}')
        # A vote on an older patch set, e.g. from a slow check, leaves the
        # vote on a newer one alone.
        change.votes = [vote for vote in change.votes
                        if not (label == vote.label and voter == vote.by
                                and vote.patch_set <= number)]
        if value:
            change.votes.append(Vote(label=label, value=value, by=voter, patch_set=number,
                                     time=int(time.time()),
                                     message=message))
        return number

    return update_change(change_id, edit, repository)


def require_verified(change_id: str, does_need_verified: bool,
                     repository: str | None = None) -> None:
    """Sets whether a change needs Verified+1 to be submitted.

    Args:
        change_id: the full Change-Id.
        does_need_verified: whether the change needs Verified+1.
        repository: the repository's directory, None for the one the
            change is in review in.
    """
    def edit(change: Change) -> None:
        """Sets the flag.

        Args:
            change: the change, read fresh.
        """
        change.needs_verified = does_need_verified

    update_change(change_id, edit, repository)
