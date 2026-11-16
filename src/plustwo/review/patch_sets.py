# SPDX-License-Identifier: Zlib
"""Patch sets and their types."""

import time

from plustwo.config.user import current_user
from plustwo.git.cache import cached_for_objects
from plustwo.git.commits import patch_id, rebased_tree, tree_of
from plustwo.git.refs import PATCH_SET_PREFIX
from plustwo.git.repository import Repository
from plustwo.model.change import NO_CODE_CHANGE, REWORK, TRIVIAL_REBASE, Change
from plustwo.model.vote import CODE_REVIEW, LABEL_RANGES, VERIFIED, Vote


def patch_sets_of(repository: Repository, change_id: str) -> dict[int, str]:
    """Reads the uploaded patch sets of a change.

    Args:
        repository: the repository.
        change_id: the full Change-Id.

    Returns:
        Each patch set's number, mapped to its commit.
    """
    output = repository.git('for-each-ref', '--format=%(refname) %(objectname)',
                            f'{PATCH_SET_PREFIX}{change_id}/')
    patch_sets = {}
    for line in filter(None, output.split('\n')):
        ref, commit = line.split(' ')
        patch_sets[int(ref.rsplit('/', 1)[1])] = commit
    return patch_sets


@cached_for_objects(1, 2)
def patch_set_type(repository: Repository, old: str, new: str) -> str:
    """Finds how a new patch set differs from the one before, like Gerrit's
    change kinds.

    Like Gerrit's ChangeKindCacheImpl, a trivial rebase is one where the
    old patch set, replayed onto the new one's parent, gives the new one's
    tree. So a change whose context moved because the change below edited
    nearby lines is one too. A conflict means rework. Without git 2.40 it
    falls back to the patch ids, which see context changes as rework.

    Args:
        repository: the repository.
        old: the commit of the patch set before.
        new: the commit of the new patch set.

    Returns:
        NO_CODE_CHANGE (the same files, as with a new message),
        TRIVIAL_REBASE (the same patch on another parent) or REWORK.
    """
    if tree_of(repository, old) == tree_of(repository, new):
        return NO_CODE_CHANGE
    if not (repository.git('rev-parse', '--verify', '--quiet', f'{old}^', check=False)
            and repository.git('rev-parse', '--verify', '--quiet', f'{new}^', check=False)):
        # A root commit has no parent to rebase onto, like in Gerrit
        return REWORK
    rebased = rebased_tree(repository, old, f'{new}^')
    if rebased is not None:
        return TRIVIAL_REBASE if rebased and rebased == tree_of(repository, new) else REWORK
    if patch_id(repository, old) == patch_id(repository, new):
        return TRIVIAL_REBASE
    return REWORK


def _is_kept(vote: Vote, patch_set_type_name: str) -> bool:
    """Tells whether a new patch set keeps a vote, with Gerrit's default
    rules: Code-Review after a trivial rebase or without code change,
    Verified only without code change, and Code-Review-2 always.

    Args:
        vote: the vote on the patch set before.
        patch_set_type_name: the new patch set's type, see
            patch_set_type().

    Returns:
        True if the vote is copied to the new patch set.
    """
    if VERIFIED == vote.label:
        return NO_CODE_CHANGE == patch_set_type_name
    return patch_set_type_name in (NO_CODE_CHANGE, TRIVIAL_REBASE) \
        or LABEL_RANGES[CODE_REVIEW][0] == vote.value


def add_patch_set(change: Change) -> tuple[int, str | None]:
    """Saves a change's current commit as its next patch set and copies the
    votes it keeps.

    Args:
        change: the change, changed in place.

    Returns:
        (number, type), see patch_set_type(). The type is None for the
        first patch set.
    """
    latest = change.latest_patch_set()
    number = latest + 1
    change.repository.git('update-ref', f'{PATCH_SET_PREFIX}{change.change_id}/{number}',
                          change.commit)
    patch_set_type_name = None
    if latest:
        patch_set_type_name = patch_set_type(change.repository, change.patch_sets[latest],
                                             change.commit)
        change.votes += [vote.copied(number) for vote in change.current_votes(CODE_REVIEW)
                         + change.current_votes(VERIFIED)
                         if _is_kept(vote, patch_set_type_name)]
    change.patch_sets[number] = change.commit
    change.uploads[number] = int(time.time())
    change.uploaders[number] = current_user()
    return number, patch_set_type_name
