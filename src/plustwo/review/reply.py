# SPDX-License-Identifier: Zlib
"""Gerrit's Reply: sending drafts, a message and a vote at once."""

from plustwo.model.vote import CODE_REVIEW
from plustwo.review.changes import find_change
from plustwo.review.threads import send_drafts
from plustwo.review.votes import set_vote


def reply(change_id, message='', vote=None, repository=None):
    """Gerrit's Reply dialog in one step: sends the drafts and the message,
    and votes Code-Review if vote differs from the reviewer's vote now.
    Returns (drafts sent, patch set voted on or None).
    """
    voted = None
    if vote is not None and vote != find_change(change_id, repository).own_vote():
        voted = set_vote(change_id, CODE_REVIEW, vote, repository=repository)
    return send_drafts(change_id, message, repository), voted


def quick_approve(change_id, send=False, repository=None):
    """Code-Review+2 on the latest patch set, like Gerrit's Quick approve.
    With send, the reviewer's drafts are sent along. Returns (patch set,
    number of drafts sent).
    """
    sent = send_drafts(change_id, repository=repository) if send else 0
    return set_vote(change_id, CODE_REVIEW, 2, repository=repository), sent
