# SPDX-License-Identifier: Zlib
"""The reviewer's last send of a change."""

from plustwo.git.errors import ChangeError
from plustwo.model.change import Change
from plustwo.model.version import LATEST
from plustwo.review.history import PatchSetEntry, patch_set_events


def last_send(history: list[PatchSetEntry]) -> tuple[int, int] | None:
    """Finds where the reviewer's newest send ends. A send is the messages
    and the vote sent together, so its newest entry ends it. What comes
    after it happened since.

    Args:
        history: the change's history, see patch_set_history().

    Returns:
        (patch set number, index of its last event), None before the first
        send.
    """
    found = None
    for entry in history:
        for index, event in enumerate(entry.events):
            if event.is_yours() and not event.is_draft and event.time and (
                    found is None or event.time >= found[0]):
                found = (event.time, entry.number, index)
    return (found[1], found[2]) if found else None


def last_send_patch_set(change: Change) -> int | None:
    """Finds the patch set the reviewer's newest send belongs to, like
    last_send() but without git.

    Args:
        change: the change.

    Returns:
        The patch set's number, None before the first send.
    """
    found = None
    for number, events in patch_set_events(change).items():
        for event in events:
            if event.is_yours() and not event.is_draft and event.time and (
                    found is None or event.time >= found[0]):
                found = (event.time, number)
    return found[1] if found else None


def last_send_compare(change: Change) -> tuple[str, str]:
    """Finds the versions to compare to see what happened since the
    reviewer's last send: its patch set against the latest.

    Args:
        change: the change.

    Returns:
        (left, right) versions, see versions_to_compare().

    Raises:
        ChangeError: nothing was sent yet, or the last send is on the
            latest patch set.
    """
    number = last_send_patch_set(change)
    if number is None:
        raise ChangeError('Nothing sent yet.')
    if number == change.latest_patch_set():
        raise ChangeError('Your last send is on the latest patch set.')
    return str(number), LATEST
