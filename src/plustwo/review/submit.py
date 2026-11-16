# SPDX-License-Identifier: Zlib
"""Submitting changes."""

import time

from plustwo.git.errors import ChangeError
from plustwo.git.lock import review_lock
from plustwo.git.notes import read_note, write_note
from plustwo.git.refs import current_branch, is_ancestor
from plustwo.git.repository import current_repository, git
from plustwo.model.vote import CODE_REVIEW
from plustwo.review.changes import changes_in_stack, locate
from plustwo.review.stacks import stack_ref, worktrees_with


def changes_submitted_with(change, repository=None):
    """The changes a submit of this change submits: it and all changes below
    it in its stack, oldest first.
    """
    stack = [other for other in changes_in_stack(repository) if other.topic == change.topic]
    return stack[:[other.change_id for other in stack].index(change.change_id) + 1]


def submittable_in(stack, repository=None):
    """The Change-Ids of a stack's changes (oldest first) that submit()
    would submit now without force: they and the changes below them have
    their votes and nothing unresolved, and the base branch didn't move.
    """
    base = stack[0].base if stack else None
    result = set()
    for change in stack:
        if change.submit_problems() or change.unfinished_comments():
            break
        if is_ancestor(base, change.commit, repository):
            result.add(change.change_id)
    return result


# The marks of a stack's changes, see stack_marks()
SUBMITTABLE_MARK = '✓'


APPROVED_MARK = '+2'


def is_review_finished(change):
    """Whether the reviewer is done with a change: the highest Code-Review
    on its latest patch set, no -2, and no thread unresolved.
    """
    return change.is_approved(CODE_REVIEW) and not change.unfinished_comments()


def stack_marks(stack, repository=None):
    """Marks for the changes of a stack (oldest first): Change-Id ->
    SUBMITTABLE_MARK for those submit would take now, APPROVED_MARK for
    those whose review is finished but which can't be submitted yet, e.g.
    because a change below isn't ready, Verified is missing or the stack
    needs a rebase. Changes without a mark are left out.
    """
    submittable = submittable_in(stack, repository)
    return {change.change_id: SUBMITTABLE_MARK if change.change_id in submittable
            else APPROVED_MARK for change in stack
            if change.change_id in submittable or is_review_finished(change)}


def submit(change_id, force=False, repository=None):
    """Moves the base branch forward to a change, which submits it and every
    change below it in its stack, like Gerrit does with a chain. A stack
    with nothing left is removed. Local branches stay as they are, the
    author's plustwo pull catches up.

    Refuses when one of these changes lacks the votes it needs
    (Code-Review+2, Verified+1 if required), and when the base branch moved
    on and the stack needs a rebase first. Without force it also refuses
    while threads are unresolved, so no point gets lost. The interface uses
    force after listing those threads and asking.
    """
    repository = current_repository(repository) or locate(change_id)
    with review_lock(repository):
        change = next((change for change in changes_in_stack(repository)
                       if change_id == change.change_id), None)
        if change is None:
            raise ChangeError(f'the change {change_id[:8]} is not in review')
        submitted = changes_submitted_with(change, repository)
        problems = [problem for other in submitted for problem in other.submit_problems()]
        if problems:
            raise ChangeError('; '.join(problems))
        unfinished = [f'{other.short_id()} #{comment.number}' for other in submitted
                      for comment in other.unfinished_comments()]
        if unfinished and not force:
            raise ChangeError(f'unresolved threads: {", ".join(unfinished)}')

        base = change.base
        if not is_ancestor(base, change.commit, repository):
            raise ChangeError(f'{base} moved on, rebase the stack {change.topic} onto it first')

        # The base branch is moved like a fast-forward merge. If it is checked
        # out here, merge keeps the working copy right, update-ref would not.
        # Checked out in another worktree, only that one can move it.
        here = git('rev-parse', '--show-toplevel', repository=repository)
        elsewhere = [path for path in worktrees_with(base, repository) if path != here]
        if elsewhere:
            raise ChangeError(f'{base} is checked out in {elsewhere[0]}, switch away from it '
                              'there first')
        if base == current_branch(repository):
            git('merge', '--ff-only', '--quiet', change.commit, repository=repository)
        else:
            git('update-ref', f'refs/heads/{base}', change.commit, repository=repository)

        # When and from which stack, for a watch started later, see
        # submitted_since()
        moment = int(time.time())
        for other in submitted:
            note = read_note(other.commit, repository)
            note['submit'] = {'time': moment, 'topic': other.topic, 'base': base}
            write_note(other.commit, note, repository)
        top = git('rev-parse', stack_ref(change.topic), repository=repository)
        if top == change.commit:
            # The stack's base ref stays, so show still finds the
            # submitted changes on that branch.
            git('update-ref', '-d', stack_ref(change.topic), top, repository=repository)
        return submitted
