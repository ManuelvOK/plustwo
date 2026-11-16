# SPDX-License-Identifier: Zlib
"""Gerrit's Reply: sending drafts, a message and a vote at once."""

from plustwo.model.vote import CODE_REVIEW
from plustwo.review.changes import find_change
from plustwo.review.threads import send_drafts
from plustwo.review.votes import set_vote
from plustwo.git.repository import Repository


def reply(repository: Repository, change_id: str, message: str = '',
          vote: int | None = None) -> tuple[int, int | None]:
    """Does what Gerrit's Reply dialog does, in one step: sends the drafts
    and the message, and votes Code-Review.

    Args:
        repository: the repository.
        change_id: the full Change-Id.
        message: a message on the whole change, '' for none.
        vote: the Code-Review vote, given only if it differs from the
            reviewer's vote now. None for no vote.

    Returns:
        (number of drafts sent, patch set voted on or None).
    """
    voted = None
    if vote is not None and vote != find_change(repository, change_id).own_vote():
        voted = set_vote(repository, change_id, CODE_REVIEW, vote)
    return send_drafts(repository, change_id, message), voted


def quick_approve(repository: Repository, change_id: str, do_send: bool = False) -> tuple[int, int]:
    """Votes Code-Review+2 on the latest patch set, like Gerrit's Quick
    approve.

    Args:
        repository: the repository.
        change_id: the full Change-Id.
        do_send: whether to send the reviewer's drafts along.

    Returns:
        (patch set voted on, number of drafts sent).
    """
    n_sent = send_drafts(repository, change_id) if do_send else 0
    return set_vote(repository, change_id, CODE_REVIEW, 2), n_sent
