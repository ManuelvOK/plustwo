# SPDX-License-Identifier: Zlib
"""Moving threads by hand."""

import time
from dataclasses import replace

from plustwo.git.errors import ChangeError
from plustwo.model.move import Move
from plustwo.model.place import (
    CHANGE_PLACE, NEW_SIDE, OLD_SIDE, PATCHSET_LEVEL, place_label, split_place)
from plustwo.model.roles import REVIEWER
from plustwo.review.changes import comment_of, update_change
from plustwo.review.threads import check_place


def move_place(text):
    """A place to move a thread to as written: "a.cc:12", "a.cc:12 (old)"
    for the old side, as place_label() shows it. Returns (place, side).
    """
    text = text.strip()
    if text.endswith('(old)'):
        return text[:-len('(old)')].strip(), OLD_SIDE
    return text, NEW_SIDE


def place_move(change, comment, place, side=NEW_SIDE, patch_set=0, by=REVIEWER,
               repository=None):
    """Moves a thread of change to place on a patch set, the newest for 0,
    see comment_place() for a line of a diff. Checks the place like a new
    thread's. A thread that is only a draft just gets the new place, else
    the move is kept and shown (see Move). Returns the Move, None for a
    draft.
    """
    patch_set = patch_set or change.latest_patch_set()
    if patch_set not in change.patch_sets:
        raise ChangeError(f'{change.short_id()} has no patch set {patch_set}')
    path, _line = split_place(place)
    if path in (PATCHSET_LEVEL, CHANGE_PLACE):
        place, side = PATCHSET_LEVEL, NEW_SIDE
    check_place(change, replace(comment, place=place, side=side, patch_set=patch_set),
                repository)
    if comment.is_draft():
        comment.place, comment.side, comment.patch_set = place, side, patch_set
        return None
    move = Move(patch_set=patch_set, place=place, side=side, by=by, time=int(time.time()))
    comment.moves.append(move)
    return move


def move_thread(change_id, number, place, side=NEW_SIDE, patch_set=0, by=REVIEWER,
                repository=None):
    """Moves a thread by hand, see place_move(), under the lock."""
    def edit(change):
        return place_move(change, comment_of(change, number), place, side, patch_set, by,
                          repository)

    return update_change(change_id, edit, repository)


def move_text(move):
    """A move for display, like "moved by author to a.cc:12 (PS3)"."""
    return f'moved by {move.by} to {place_label(move.place, move.side)} (PS{move.patch_set})'
