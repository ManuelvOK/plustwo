# SPDX-License-Identifier: Zlib
"""Stacks: the uploaded commits of a topic and their base branch."""

from plustwo.config.settings import base_branch
from plustwo.git.refs import (
    NOTES_REF, PLUSTWO_PREFIX, REVIEW_BRANCH_PREFIX, STACK_BASE_PREFIX, STACK_PREFIX,
    commits_between, is_ancestor, refs_with_prefix)
from plustwo.git.repository import Repository


def topics(repository: Repository) -> list[str]:
    """Lists the topics of the uploaded stacks.

    Args:
        repository: the repository.

    Returns:
        The topics, sorted.
    """
    return sorted(ref[len(STACK_PREFIX):] for ref in refs_with_prefix(repository, STACK_PREFIX))


def stack_ref(topic: str) -> str:
    """Names the ref of an uploaded stack.

    Args:
        topic: the stack's topic.

    Returns:
        The ref's full name.
    """
    return STACK_PREFIX + topic


def stack_bases(repository: Repository) -> dict[str, str]:
    """Reads the recorded base branches of the stacks. Stacks uploaded by
    older versions have none.

    Args:
        repository: the repository.

    Returns:
        Each topic, mapped to its base branch's short name.
    """
    output = repository.git('for-each-ref', '--format=%(refname) %(symref)', STACK_BASE_PREFIX,
                            check=False)
    bases = {}
    for line in filter(None, output.split('\n')):
        ref, _, target = line.partition(' ')
        if target.startswith('refs/heads/'):
            bases[ref[len(STACK_BASE_PREFIX):]] = target[len('refs/heads/'):]
    return bases


def stack_base(repository: Repository, topic: str) -> str:
    """Finds the base branch of a stack.

    Args:
        repository: the repository.
        topic: the stack's topic.

    Returns:
        The branch recorded at its upload, else the setting review.base.
    """
    target = repository.git('symbolic-ref', '--quiet', STACK_BASE_PREFIX + topic, check=False)
    if target.startswith('refs/heads/'):
        return target[len('refs/heads/'):]
    return base_branch(repository)


def set_stack_base(repository: Repository, topic: str, base: str) -> None:
    """Records the base branch of a stack.

    Args:
        repository: the repository.
        topic: the stack's topic.
        base: the base branch's short name.
    """
    repository.git('symbolic-ref', STACK_BASE_PREFIX + topic, f'refs/heads/{base}')


def base_branches(repository: Repository) -> list[str]:
    """Lists every base branch in use.

    Args:
        repository: the repository.

    Returns:
        The setting's and the stacks' base branches, sorted.
    """
    return sorted({base_branch(repository), *stack_bases(repository).values()})


def needs_rebase(repository: Repository, topic: str) -> bool:
    """Tells whether a stack's base branch moved on since its last rebase.

    Args:
        repository: the repository.
        topic: the stack's topic.

    Returns:
        True if the base branch has commits the stack hasn't.
    """
    return not is_ancestor(repository, stack_base(repository, topic), stack_ref(topic))


def stack_commits(repository: Repository, topic: str) -> list[str]:
    """Lists the commits of an uploaded stack that are not on its base
    branch.

    Args:
        repository: the repository.
        topic: the stack's topic.

    Returns:
        The commits, oldest first.
    """
    return commits_between(repository, stack_base(repository, topic), stack_ref(topic))


def topic_of_branch(branch: str) -> str:
    """Finds the topic a local branch uploads to.

    Args:
        branch: the branch's short name.

    Returns:
        The branch's name, without a leading review/ (the branch names of
        older versions).
    """
    return branch[len(REVIEW_BRANCH_PREFIX):] if branch.startswith(REVIEW_BRANCH_PREFIX) else branch


def stack_version(repository: Repository) -> str:
    """Reads all refs a review depends on. They change with every upload,
    rebase, submit and note write, so a viewer knows when to reload.

    Args:
        repository: the repository.

    Returns:
        A line per ref, its name and object name.
    """
    # The stacks' base branches are in it too, by their refs in
    # refs/plustwo/bases/.
    return repository.git('for-each-ref', '--format=%(refname) %(objectname)',
                          f'refs/heads/{base_branch(repository)}', NOTES_REF, PLUSTWO_PREFIX,
                          check=False)


def worktrees_with(repository: Repository, branch: str) -> list[str]:
    """Finds the worktrees that have a branch checked out.

    Args:
        repository: the repository.
        branch: the branch's short name.

    Returns:
        The worktrees' paths.
    """
    output = repository.git('worktree', 'list', '--porcelain')
    paths: list[str] = []
    path = ''
    for line in output.split('\n'):
        if line.startswith('worktree '):
            path = line[len('worktree '):]
        elif f'branch refs/heads/{branch}' == line:
            paths.append(path)
    return paths
