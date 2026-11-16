# SPDX-License-Identifier: Zlib
"""Moving threads by hand."""

import time
from dataclasses import replace

from plustwo.git.errors import ChangeError
from plustwo.model.change import Change
from plustwo.model.comment import Comment
from plustwo.model.move import Move
from plustwo.model.place import (
    CHANGE_PLACE, NEW_SIDE, OLD_SIDE, PATCHSET_LEVEL, place_label, split_place)
from plustwo.model.roles import REVIEWER
from plustwo.review.changes import comment_of, update_change
from plustwo.review.threads import check_place
from plustwo.git.repository import Repository


def move_place(text: str) -> tuple[str, str]:
    """Reads a place to move a thread to, as place_label() shows it.

    Args:
        text: the place, like "a.cc:12", or "a.cc:12 (old)" for the old
            side.

    Returns:
        (place, side).
    """
    text = text.strip()
    if text.endswith('(old)'):
        return text[:-len('(old)')].strip(), OLD_SIDE
    return text, NEW_SIDE


def place_move(change: Change, comment: Comment, place: str, side: str = NEW_SIDE,
               patch_set: int = 0, by: str = REVIEWER) -> Move | None:
    """Moves a thread to a place on a patch set. Checks the place like a new
    thread's. A thread that is only a draft just gets the new place, else
    the move is kept and shown (see Move).

    Args:
        change: the thread's change, changed in place.
        comment: the thread, changed in place.
        place: the new place, see comment_place() for a line of a diff.
        side: the side the place is on, NEW_SIDE or OLD_SIDE.
        patch_set: the patch set the place counts on, 0 for the newest.
        by: who moves the thread, REVIEWER or AUTHOR.

    Returns:
        The move, None for a draft.

    Raises:
        ChangeError: the patch set or the place doesn't exist.
    """
    patch_set = patch_set or change.latest_patch_set()
    if patch_set not in change.patch_sets:
        raise ChangeError(f'{change.short_id()} has no patch set {patch_set}')
    path, _line = split_place(place)
    if path in (PATCHSET_LEVEL, CHANGE_PLACE):
        place, side = PATCHSET_LEVEL, NEW_SIDE
    check_place(change, replace(comment, place=place, side=side, patch_set=patch_set))
    if comment.is_draft():
        comment.place, comment.side, comment.patch_set = place, side, patch_set
        return None
    move = Move(patch_set=patch_set, place=place, side=side, by=by, time=int(time.time()))
    comment.moves.append(move)
    return move


def move_thread(repository: Repository, change_id: str, number: int, place: str,
                side: str = NEW_SIDE,
                patch_set: int = 0, by: str = REVIEWER) -> Move | None:
    """Moves a thread by hand under the lock, see place_move().

    Args:
        repository: the repository.
        change_id: the full Change-Id.
        number: the thread's number.
        place: the new place.
        side: the side the place is on, NEW_SIDE or OLD_SIDE.
        patch_set: the patch set the place counts on, 0 for the newest.
        by: who moves the thread, REVIEWER or AUTHOR.

    Returns:
        The move, None for a draft.
    """
    def edit(change: Change) -> Move | None:
        """Moves the thread.

        Args:
            change: the change, read fresh.

        Returns:
            The move, None for a draft.
        """
        return place_move(change, comment_of(change, number), place, side, patch_set, by)

    return update_change(repository, change_id, edit)


def move_text(move: Move) -> str:
    """Writes a move for display.

    Args:
        move: the move.

    Returns:
        A line like "moved by author to a.cc:12 (PS3)".
    """
    return f'moved by {move.by} to {place_label(move.place, move.side)} (PS{move.patch_set})'
