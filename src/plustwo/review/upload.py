# SPDX-License-Identifier: Zlib
"""Uploading a stack."""

from plustwo.config.settings import base_branch
from plustwo.git.commits import change_id_of
from plustwo.git.errors import ChangeError
from plustwo.git.lock import review_lock
from plustwo.git.notes import read_note, write_note
from plustwo.git.refs import commits_between, current_branch
from plustwo.git.repository import git
from plustwo.model.change import Change
from plustwo.review.changes import change_from, changes_in_stack, note_of, reference_change_id
from plustwo.review.patch_sets import add_patch_set, patch_sets_of
from plustwo.review.setup import ensure_layout
from plustwo.review.stacks import set_stack_base, stack_bases, stack_ref, topic_of_branch


def upload(branch: str | None = None, topic: str | None = None, repository: str | None = None,
           is_wip: bool | None = None, from_change: str | None = None
           ) -> tuple[list[tuple[Change, int, str | None]], str, bool, str | None]:
    """Uploads the stack of a local branch: its commits that are not on the
    base branch. Like a push to Gerrit's refs/for/, every commit that isn't
    its change's latest patch set yet becomes the next one, and the stack's
    ref points to the branch's top.

    The review of a change lives on its latest patch set, so a new patch
    set gets that note, whatever an amend copied to the local commit. A
    pending rebase request is cleared. A branch on top of another stack
    makes that stack bigger, under its topic, like a longer relation chain
    in Gerrit. The uploader's base branch is recorded as the stack's, like
    the target branch of a Gerrit change. An upload from a branch on
    another base moves the stack there.

    Args:
        branch: the local branch, None for the checked out one.
        topic: the stack's topic, None for the branch's name without a
            leading review/.
        repository: the repository's directory, None for the current one.
        is_wip: True or False marks every change of the stack work in
            progress or ready, like Gerrit's %wip and %ready. None leaves
            the marks.
        from_change: a change of the stack, see reference_change_id(). Then
            only it and the changes above it are marked work in progress,
            or it and the changes below it ready. None for all.

    Returns:
        ((change, patch set number, patch set type) per new patch set, the
        topic, whether an existing stack grew, the base the stack moved
        away from or None).

    Raises:
        ChangeError: HEAD is detached without a topic, the branch is the
            base, has no commits beyond it or a commit without Change-Id, a
            change is in another stack, from_change isn't in the stack, or
            nothing changed.
    """
    with review_lock(repository):
        ensure_layout(repository)
        base = base_branch(repository)
        branch = branch or current_branch(repository)
        if branch is None and topic is None:
            raise ChangeError('HEAD is detached, check out a branch or give a topic')
        if base == branch:
            raise ChangeError(f'{base} is the base branch, upload from another branch')
        topic = topic or topic_of_branch(branch or '')
        tip = git('rev-parse', branch or 'HEAD', repository=repository)
        commits = commits_between(base, tip, repository)
        if not commits:
            raise ChangeError(f'{branch or "HEAD"} has no commits beyond {base}')
        change_ids = []
        for commit in commits:
            change_id = change_id_of(commit, repository)
            if change_id is None:
                raise ChangeError(f'{commit[:8]} has no Change-Id. Run plustwo init, then '
                                  'amend it to get one.')
            change_ids.append(change_id)
        in_review = {change.change_id: change.topic for change in changes_in_stack(repository)}
        has_grown = False
        if not git('rev-parse', '--verify', '--quiet', stack_ref(topic), check=False,
                   repository=repository):
            others = {in_review[change_id] for change_id in change_ids if change_id in in_review}
            if 1 == len(others):
                topic, has_grown = others.pop(), True
        elsewhere = {change_id: other for change_id, other in in_review.items()
                     if other != topic}
        for change_id in change_ids:
            if change_id in elsewhere:
                raise ChangeError(f'{change_id[:8]} is in review in the stack '
                                  f'{elsewhere[change_id]} already')

        # The work in progress mark of each commit, None to leave it
        marks = [is_wip] * len(commits)
        if is_wip is not None and from_change:
            from_id = reference_change_id(from_change, repository)
            index = next((index for index, change_id in enumerate(change_ids)
                          if change_id.startswith(from_id)), None)
            if index is None:
                raise ChangeError(f'{from_change} is not in the stack of {branch or "HEAD"}')
            # wip goes up the stack from the change, ready down from it
            marks = [is_wip if (position >= index if is_wip else position <= index) else None
                     for position in range(len(commits))]

        # A new patch set per commit that isn't one yet
        uploaded = []
        is_marked = False
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
                    is_marked = True
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
        if not uploaded and old_top == tip and not is_marked and base == old_base:
            raise ChangeError(f'{topic} is uploaded already, nothing changed')
        git('update-ref', stack_ref(topic), tip, repository=repository)
        set_stack_base(topic, base, repository)
        moved = old_base if old_top and old_base and old_base != base else None
        return uploaded, topic, has_grown, moved
