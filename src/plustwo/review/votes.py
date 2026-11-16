# SPDX-License-Identifier: Zlib
"""Voting."""

import time

from plustwo.git.errors import ChangeError
from plustwo.model.vote import CODE_REVIEW, DEFAULT_VOTERS, LABEL_RANGES, Vote, label_name
from plustwo.review.changes import update_change


def vote_values(label=CODE_REVIEW):
    """A label's values from the lowest to the highest, like -2 .. +2."""
    low, high = LABEL_RANGES[label_name(label)]
    return tuple(range(low, high + 1))


def set_vote(change_id, label, value, by=None, patch_set=None, message='', repository=None):
    """Votes on the latest patch set, or on patch set patch_set. It replaces
    the voter's earlier votes of that label, 0 only removes them. Returns
    the patch set's number.
    """
    label = label_name(label)
    by = by or DEFAULT_VOTERS[label]
    low, high = LABEL_RANGES[label]
    if not low <= value <= high:
        raise ChangeError(f'{label} goes from {low:+d} to {high:+d}')

    def edit(change):
        number = patch_set or change.latest_patch_set()
        if not number:
            raise ChangeError(f'{change.short_id()} has no patch set yet, upload it first')
        if number not in change.patch_sets:
            raise ChangeError(f'{change.short_id()} has no patch set {number}')
        # A vote on an older patch set, e.g. from a slow check, leaves the
        # vote on a newer one alone.
        change.votes = [vote for vote in change.votes
                        if not (label == vote.label and by == vote.by
                                and vote.patch_set <= number)]
        if value:
            change.votes.append(Vote(label=label, value=value, by=by, patch_set=number,
                                     time=int(time.time()),
                                     message=message))
        return number

    return update_change(change_id, edit, repository)


def require_verified(change_id, required, repository=None):
    """Sets whether the change needs Verified+1 to be submitted."""
    def edit(change):
        change.needs_verified = required

    update_change(change_id, edit, repository)
