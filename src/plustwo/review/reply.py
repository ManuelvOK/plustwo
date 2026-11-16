# SPDX-License-Identifier: Zlib
"""Gerrit's Reply: sending drafts, a message and a vote at once."""

from plustwo.model.vote import CODE_REVIEW
from plustwo.review.changes import find_change
from plustwo.review.threads import send_drafts
from plustwo.review.votes import set_vote


def reply(change_id: str, message: str = '', vote: int | None = None,
          repository: str | None = None) -> tuple[int, int | None]:
    """Does what Gerrit's Reply dialog does, in one step: sends the drafts
    and the message, and votes Code-Review.

    Args:
        change_id: the full Change-Id.
        message: a message on the whole change, '' for none.
        vote: the Code-Review vote, given only if it differs from the
            reviewer's vote now. None for no vote.
        repository: the repository's directory, None for the one the
            change is in review in.

    Returns:
        (number of drafts sent, patch set voted on or None).
    """
    voted = None
    if vote is not None and vote != find_change(change_id, repository).own_vote():
        voted = set_vote(change_id, CODE_REVIEW, vote, repository=repository)
    return send_drafts(change_id, message, repository), voted


def quick_approve(change_id: str, do_send: bool = False,
                  repository: str | None = None) -> tuple[int, int]:
    """Votes Code-Review+2 on the latest patch set, like Gerrit's Quick
    approve.

    Args:
        change_id: the full Change-Id.
        do_send: whether to send the reviewer's drafts along.
        repository: the repository's directory, None for the one the
            change is in review in.

    Returns:
        (patch set voted on, number of drafts sent).
    """
    n_sent = send_drafts(change_id, repository=repository) if do_send else 0
    return set_vote(change_id, CODE_REVIEW, 2, repository=repository), n_sent
