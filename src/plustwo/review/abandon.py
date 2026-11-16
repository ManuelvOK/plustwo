# SPDX-License-Identifier: Zlib
"""Abandoning and restoring stacks."""

from plustwo.git.errors import ChangeError
from plustwo.git.lock import review_lock
from plustwo.git.refs import ABANDONED_PREFIX
from plustwo.git.repository import Repository
from plustwo.review.stacks import stack_ref


def abandon_stack(repository: Repository, topic: str) -> None:
    """Abandons a stack, like Gerrit's Abandon: it leaves the dashboard,
    its patch sets and notes stay, restore_stack() brings it back. Whole
    stacks only, a change in the middle would leave the ones above it
    without their parent.

    Args:
        repository: the repository.
        topic: the stack's topic.

    Raises:
        ChangeError: there is no such stack.
    """
    with review_lock(repository):
        top = repository.git('rev-parse', '--verify', '--quiet', stack_ref(topic), check=False)
        if not top:
            raise ChangeError(f'no stack {topic}')
        repository.git('update-ref', f'{ABANDONED_PREFIX}{topic}', top)
        repository.git('update-ref', '-d', stack_ref(topic))


def restore_stack(repository: Repository, topic: str) -> None:
    """Brings an abandoned stack back into review.

    Args:
        repository: the repository.
        topic: the stack's topic.

    Raises:
        ChangeError: there is no such abandoned stack, or a stack of the
            same topic is in review.
    """
    with review_lock(repository):
        top = repository.git('rev-parse', '--verify', '--quiet', f'{ABANDONED_PREFIX}{topic}',
                             check=False)
        if not top:
            raise ChangeError(f'no abandoned stack {topic}')
        if repository.git('rev-parse', '--verify', '--quiet', stack_ref(topic), check=False):
            raise ChangeError(f'a stack {topic} is in review again, upload it instead')
        repository.git('update-ref', stack_ref(topic), top)
        repository.git('update-ref', '-d', f'{ABANDONED_PREFIX}{topic}')
