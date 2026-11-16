# SPDX-License-Identifier: Zlib
"""Uploading a stack."""

from plustwo.config.settings import base_branch
from plustwo.git.commits import change_id_of
from plustwo.git.errors import ChangeError
from plustwo.git.lock import review_lock
from plustwo.git.notes import read_note, write_note
from plustwo.git.refs import commits_between, current_branch
from plustwo.git.repository import git
from plustwo.review.changes import change_from, changes_in_stack, note_of, reference_change_id
from plustwo.review.patch_sets import add_patch_set, patch_sets_of
from plustwo.review.setup import ensure_layout
from plustwo.review.stacks import set_stack_base, stack_bases, stack_ref, topic_of_branch


def upload(branch=None, topic=None, repository=None, wip=None, at=None):
    """Uploads the stack of a local branch (the checked out one if None):
    its commits that are not on the base branch. Like a push to Gerrit's
    refs/for/, every commit that isn't its change's latest patch set yet
    becomes the next one, and the stack's ref points to the branch's top.

    topic defaults to the branch's name without a leading review/. The
    review of a change lives on its latest patch set, so a new patch set
    gets that note, whatever an amend copied to the local commit. A
    pending rebase request is cleared.

    A branch on top of another stack makes that stack bigger, under its
    topic, like a longer relation chain in Gerrit. wip True or False marks
    every change of the stack work in progress or ready, like Gerrit's %wip
    and %ready. With at (a change of the stack, see reference_change_id())
    only at and the changes above it get wip True, or at and the changes
    below it wip False. The others stay as they are.

    The uploader's base branch is recorded as the stack's, like the target
    branch of a Gerrit change. An upload from a branch on another base
    moves the stack there. Returns ([(change, patch set number, kind)] of
    the new patch sets, the topic, whether an existing stack grew, the base
    the stack moved away from or None).
    """
    with review_lock(repository):
        ensure_layout(repository)
        base = base_branch(repository)
        branch = branch or current_branch(repository)
        if branch is None and topic is None:
            raise ChangeError('HEAD is detached, check out a branch or give a topic')
        if base == branch:
            raise ChangeError(f'{base} is the base branch, upload from another branch')
        topic = topic or topic_of_branch(branch)
        tip = git('rev-parse', branch or 'HEAD', repository=repository)
        commits = commits_between(base, tip, repository)
        if not commits:
            raise ChangeError(f'{branch or "HEAD"} has no commits beyond {base}')
        change_ids = [change_id_of(commit, repository) for commit in commits]
        for commit, change_id in zip(commits, change_ids):
            if change_id is None:
                raise ChangeError(f'{commit[:8]} has no Change-Id. Run plustwo init, then '
                                  'amend it to get one.')
        in_review = {change.change_id: change.topic for change in changes_in_stack(repository)}
        grown = False
        if not git('rev-parse', '--verify', '--quiet', stack_ref(topic), check=False,
                   repository=repository):
            others = {in_review[change_id] for change_id in change_ids if change_id in in_review}
            if 1 == len(others):
                topic, grown = others.pop(), True
        elsewhere = {change_id: other for change_id, other in in_review.items()
                     if other != topic}
        for change_id in change_ids:
            if change_id in elsewhere:
                raise ChangeError(f'{change_id[:8]} is in review in the stack '
                                  f'{elsewhere[change_id]} already')

        marks = [wip] * len(commits)
        if wip is not None and at:
            at_id = reference_change_id(at, repository)
            index = next((index for index, change_id in enumerate(change_ids)
                          if change_id.startswith(at_id)), None)
            if index is None:
                raise ChangeError(f'{at} is not in the stack of {branch or "HEAD"}')
            # wip goes up the stack from at, ready down from it
            marks = [wip if (position >= index if wip else position <= index) else None
                     for position in range(len(commits))]

        uploaded = []
        marked = False
        for commit, change_id, mark in zip(commits, change_ids, marks):
            patch_sets = patch_sets_of(change_id, repository)
            latest = max(patch_sets, default=0)
            if latest and patch_sets[latest] == commit:
                note = read_note(commit, repository)
                if note.get('rebase_requested') or (mark is not None
                                                    and mark != note.get('wip', False)):
                    note['rebase_requested'] = False
                    if mark is not None:
                        note['wip'] = mark
                    write_note(commit, note, repository)
                    marked = True
                continue
            note = read_note(patch_sets[latest] if latest else commit, repository)
            change = change_from(commit, topic, note, repository)
            change.rebase_is_requested = False
            if mark is not None:
                change.is_wip = mark
            uploaded.append((change, *add_patch_set(change, repository)))
            write_note(commit, note_of(change), repository)
        old_top = git('rev-parse', '--verify', '--quiet', stack_ref(topic), check=False,
                      repository=repository)
        old_base = stack_bases(repository).get(topic)
        if not uploaded and old_top == tip and not marked and base == old_base:
            raise ChangeError(f'{topic} is uploaded already, nothing changed')
        git('update-ref', stack_ref(topic), tip, repository=repository)
        set_stack_base(topic, base, repository)
        moved = old_base if old_top and old_base and old_base != base else None
        return uploaded, topic, grown, moved
