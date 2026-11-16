# SPDX-License-Identifier: Zlib
"""Submitting changes."""

import time

from plustwo.git.errors import ChangeError
from plustwo.git.lock import review_lock
from plustwo.git.notes import read_note, write_note
from plustwo.git.refs import current_branch, is_ancestor
from plustwo.git.repository import Repository
from plustwo.model.change import Change
from plustwo.model.vote import CODE_REVIEW
from plustwo.review.changes import changes_in_stack
from plustwo.review.stacks import stack_ref, worktrees_with


def changes_submitted_with(change: Change) -> list[Change]:
    """Finds the changes a submit of a change submits.

    Args:
        change: the change.

    Returns:
        The change and all changes below it in its stack, oldest first.
    """
    stack = [other for other in changes_in_stack(change.repository)
             if other.topic == change.topic]
    return stack[:[other.change_id for other in stack].index(change.change_id) + 1]


def submittable_in(stack: list[Change]) -> set[str]:
    """Finds the changes of a stack that submit() would submit now without
    forcing: they and the changes below them have their votes and nothing
    unresolved, and the base branch didn't move.

    Args:
        stack: the stack's changes, oldest first.

    Returns:
        The Change-Ids of those changes.
    """
    if not stack:
        return set()
    base = stack[0].base
    result = set()
    for change in stack:
        if change.submit_problems() or change.unfinished_comments():
            break
        if is_ancestor(change.repository, base, change.commit):
            result.add(change.change_id)
    return result


# The marks of a stack's changes, see stack_marks()
_SUBMITTABLE_MARK = '✓'
_APPROVED_MARK = '+2'


def _is_review_finished(change: Change) -> bool:
    """Tells whether the reviewer is done with a change.

    Args:
        change: the change.

    Returns:
        True with the highest Code-Review on its latest patch set, no -2,
        and no thread unresolved.
    """
    return change.is_approved(CODE_REVIEW) and not change.unfinished_comments()


def stack_marks(stack: list[Change]) -> dict[str, str]:
    """Marks the changes of a stack for display.

    Args:
        stack: the stack's changes, oldest first.

    Returns:
        Each marked change's Change-Id, mapped to _SUBMITTABLE_MARK for
        those submit would take now, or _APPROVED_MARK for those whose
        review is finished but which can't be submitted yet, as when a
        change below isn't ready, Verified is missing or the stack needs a
        rebase. Changes without a mark are left out.
    """
    submittable = submittable_in(stack)
    return {change.change_id: _SUBMITTABLE_MARK if change.change_id in submittable
            else _APPROVED_MARK for change in stack
            if change.change_id in submittable or _is_review_finished(change)}


def submit(repository: Repository, change_id: str, do_force: bool = False) -> list[Change]:
    """Moves the base branch forward to a change, which submits it and every
    change below it in its stack, like Gerrit does with a chain. A stack
    with nothing left is removed. Local branches stay as they are, the
    author's plustwo pull catches up.

    Args:
        repository: the repository.
        change_id: the full Change-Id.
        do_force: whether to submit while threads are unresolved. The
            interface forces after listing those threads and asking.

    Returns:
        The submitted changes, oldest first.

    Raises:
        ChangeError: a change lacks the votes it needs (Code-Review+2,
            Verified+1 if required), the base branch moved on and the stack
            needs a rebase first, the base branch is checked out in another
            worktree, or, without forcing, threads are unresolved, so no
            point gets lost.
    """
    with review_lock(repository):
        change = next((change for change in changes_in_stack(repository)
                       if change_id == change.change_id), None)
        if change is None:
            raise ChangeError(f'the change {change_id[:8]} is not in review')
        submitted = changes_submitted_with(change)
        problems = [problem for other in submitted for problem in other.submit_problems()]
        if problems:
            raise ChangeError('; '.join(problems))
        unfinished = [f'{other.short_id()} #{comment.number}' for other in submitted
                      for comment in other.unfinished_comments()]
        if unfinished and not do_force:
            raise ChangeError(f'unresolved threads: {", ".join(unfinished)}')

        base = change.base
        if not is_ancestor(repository, base, change.commit):
            raise ChangeError(f'{base} moved on, rebase the stack {change.topic} onto it first')

        # The base branch is moved like a fast-forward merge. If it is checked
        # out here, merge keeps the working copy right, update-ref would not.
        # Checked out in another worktree, only that one can move it.
        here = str(repository.path)
        elsewhere = [path for path in worktrees_with(repository, base) if path != here]
        if elsewhere:
            raise ChangeError(f'{base} is checked out in {elsewhere[0]}, switch away from it '
                              'there first')
        if base == current_branch(repository):
            repository.git('merge', '--ff-only', '--quiet', change.commit)
        else:
            repository.git('update-ref', f'refs/heads/{base}', change.commit)

        # When and from which stack, for a watch started later, see
        # submitted_since()
        moment = int(time.time())
        for other in submitted:
            note = read_note(repository, other.commit)
            note['submit'] = {'time': moment, 'topic': other.topic, 'base': base}
            write_note(repository, other.commit, note)
        top = repository.git('rev-parse', stack_ref(change.topic))
        if top == change.commit:
            # The stack's base ref stays, so show still finds the
            # submitted changes on that branch.
            repository.git('update-ref', '-d', stack_ref(change.topic), top)
        return submitted
