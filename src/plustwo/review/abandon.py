# SPDX-License-Identifier: Zlib
"""Abandoning and restoring stacks."""

from plustwo.git.errors import ChangeError
from plustwo.git.lock import review_lock
from plustwo.git.refs import ABANDONED_PREFIX
from plustwo.git.repository import git
from plustwo.review.stacks import stack_ref


def abandon_stack(topic, repository=None):
    """Abandons a stack, like Gerrit's Abandon: it leaves the dashboard,
    its patch sets and notes stay, restore_stack() brings it back. Whole
    stacks only, a change in the middle would leave the ones above it
    without their parent.
    """
    with review_lock(repository):
        top = git('rev-parse', '--verify', '--quiet', stack_ref(topic), check=False,
                  repository=repository)
        if not top:
            raise ChangeError(f'no stack {topic}')
        git('update-ref', f'{ABANDONED_PREFIX}{topic}', top, repository=repository)
        git('update-ref', '-d', stack_ref(topic), repository=repository)


def restore_stack(topic, repository=None):
    """Brings an abandoned stack back into review."""
    with review_lock(repository):
        top = git('rev-parse', '--verify', '--quiet', f'{ABANDONED_PREFIX}{topic}', check=False,
                  repository=repository)
        if not top:
            raise ChangeError(f'no abandoned stack {topic}')
        if git('rev-parse', '--verify', '--quiet', stack_ref(topic), check=False,
               repository=repository):
            raise ChangeError(f'a stack {topic} is in review again, upload it instead')
        git('update-ref', stack_ref(topic), top, repository=repository)
        git('update-ref', '-d', f'{ABANDONED_PREFIX}{topic}', repository=repository)
