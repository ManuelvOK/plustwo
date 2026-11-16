# SPDX-License-Identifier: Zlib
"""The reviewer's last send of a change."""

from plustwo.git.errors import ChangeError
from plustwo.model.version import LATEST
from plustwo.review.history import patch_set_events


def last_send(history):
    """Where the reviewer's newest send ends in a history from
    patch_set_history(): (patch set number, index of its last event), None
    before the first. A send is the messages and the vote sent together, so
    its newest entry ends it. What comes after it happened since.
    """
    found = None
    for entry in history:
        for index, event in enumerate(entry.events):
            if event.is_yours() and not event.draft and event.time and (
                    found is None or event.time >= found[0]):
                found = (event.time, entry.number, index)
    return found[1:] if found else None


def last_send_patch_set(change):
    """The patch set the reviewer's newest send belongs to, like
    last_send() but without git, None before the first send.
    """
    found = None
    for number, events in patch_set_events(change).items():
        for event in events:
            if event.is_yours() and not event.draft and event.time and (
                    found is None or event.time >= found[0]):
                found = (event.time, number)
    return found[1] if found else None


def last_send_compare(change):
    """The versions to compare to see what happened since the reviewer's
    last send: its patch set against the latest. Raises ChangeError if
    there is nothing to compare.
    """
    number = last_send_patch_set(change)
    if number is None:
        raise ChangeError('Nothing sent yet.')
    if number == change.latest_patch_set():
        raise ChangeError('Your last send is on the latest patch set.')
    return str(number), LATEST
