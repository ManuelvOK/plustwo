# SPDX-License-Identifier: Zlib
"""Patch sets and their kinds."""

import time

from plustwo.config.user import current_user
from plustwo.git.cache import cached_for_objects
from plustwo.git.commits import patch_id, rebased_tree, tree_of
from plustwo.git.refs import PATCH_SET_PREFIX
from plustwo.git.repository import git
from plustwo.model.change import NO_CODE_CHANGE, REWORK, TRIVIAL_REBASE
from plustwo.model.vote import CODE_REVIEW, LABEL_RANGES, VERIFIED


def patch_sets_of(change_id, repository=None):
    """Uploaded patch sets of a change: number -> commit."""
    output = git('for-each-ref', '--format=%(refname) %(objectname)',
                 f'{PATCH_SET_PREFIX}{change_id}/', repository=repository)
    patch_sets = {}
    for line in filter(None, output.split('\n')):
        ref, commit = line.split(' ')
        patch_sets[int(ref.rsplit('/', 1)[1])] = commit
    return patch_sets


@cached_for_objects(0, 1)
def patch_set_kind(old, new, repository=None):
    """How a new patch set differs from the one before, like Gerrit's
    change kinds: no code change (the same files, e.g. a new message),
    trivial rebase (the same patch on another parent) or rework.

    Like Gerrit's ChangeKindCacheImpl, a trivial rebase is one where the
    old patch set, replayed onto the new one's parent, gives the new one's
    tree. So a change whose context moved because the change below edited
    nearby lines is one too. A conflict means rework. Without git 2.40
    it falls back to the patch ids, which see context changes as rework.
    """
    if tree_of(old, repository) == tree_of(new, repository):
        return NO_CODE_CHANGE
    if not (git('rev-parse', '--verify', '--quiet', f'{old}^', check=False,
                repository=repository)
            and git('rev-parse', '--verify', '--quiet', f'{new}^', check=False,
                    repository=repository)):
        # A root commit has no parent to rebase onto, like in Gerrit
        return REWORK
    rebased = rebased_tree(old, f'{new}^', repository)
    if rebased is not None:
        return TRIVIAL_REBASE if rebased and rebased == tree_of(new, repository) else REWORK
    if patch_id(old, repository) == patch_id(new, repository):
        return TRIVIAL_REBASE
    return REWORK


def is_kept(vote, kind):
    """Whether a new patch set of this kind keeps a vote, with Gerrit's
    default rules: Code-Review after a trivial rebase or without code change,
    Verified only without code change, and Code-Review-2 always.
    """
    if VERIFIED == vote.label:
        return NO_CODE_CHANGE == kind
    return kind in (NO_CODE_CHANGE, TRIVIAL_REBASE) or LABEL_RANGES[CODE_REVIEW][0] == vote.value


def add_patch_set(change, repository=None):
    """Saves a change's current commit as its next patch set and copies the
    votes it keeps. Returns (number, kind), kind None for the first one.
    """
    latest = change.latest_patch_set()
    number = latest + 1
    git('update-ref', f'{PATCH_SET_PREFIX}{change.change_id}/{number}', change.commit,
        repository=repository)
    kind = None
    if latest:
        kind = patch_set_kind(change.patch_sets[latest], change.commit, repository)
        change.votes += [vote.copied(number) for vote in change.current_votes(CODE_REVIEW)
                         + change.current_votes(VERIFIED) if is_kept(vote, kind)]
    change.patch_sets[number] = change.commit
    change.uploads[number] = int(time.time())
    change.uploaders[number] = current_user()
    return number, kind
