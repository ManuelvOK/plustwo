# SPDX-License-Identifier: Zlib
"""Stacks: the uploaded commits of a topic and their base branch."""

from plustwo.config.settings import base_branch
from plustwo.git.refs import (
    NOTES_REF, PLUSTWO_PREFIX, REVIEW_BRANCH_PREFIX, STACK_BASE_PREFIX, STACK_PREFIX,
    commits_between, is_ancestor, refs_with_prefix)
from plustwo.git.repository import git


def topics(repository: str | None = None) -> list[str]:
    """Lists the topics of the uploaded stacks.

    Args:
        repository: the repository's directory, None for the current one.

    Returns:
        The topics, sorted.
    """
    return sorted(ref[len(STACK_PREFIX):] for ref in refs_with_prefix(STACK_PREFIX,
                                                                      repository=repository))


def stack_ref(topic: str) -> str:
    """Names the ref of an uploaded stack.

    Args:
        topic: the stack's topic.

    Returns:
        The ref's full name.
    """
    return STACK_PREFIX + topic


def stack_bases(repository: str | None = None) -> dict[str, str]:
    """Reads the recorded base branches of the stacks. Stacks uploaded by
    older versions have none.

    Args:
        repository: the repository's directory, None for the current one.

    Returns:
        Each topic, mapped to its base branch's short name.
    """
    output = git('for-each-ref', '--format=%(refname) %(symref)', STACK_BASE_PREFIX, check=False,
                 repository=repository)
    bases = {}
    for line in filter(None, output.split('\n')):
        ref, _, target = line.partition(' ')
        if target.startswith('refs/heads/'):
            bases[ref[len(STACK_BASE_PREFIX):]] = target[len('refs/heads/'):]
    return bases


def stack_base(topic: str, repository: str | None = None) -> str:
    """Finds the base branch of a stack.

    Args:
        topic: the stack's topic.
        repository: the repository's directory, None for the current one.

    Returns:
        The branch recorded at its upload, else the setting review.base.
    """
    target = git('symbolic-ref', '--quiet', STACK_BASE_PREFIX + topic, check=False,
                 repository=repository)
    if target.startswith('refs/heads/'):
        return target[len('refs/heads/'):]
    return base_branch(repository)


def set_stack_base(topic: str, base: str, repository: str | None = None) -> None:
    """Records the base branch of a stack.

    Args:
        topic: the stack's topic.
        base: the base branch's short name.
        repository: the repository's directory, None for the current one.
    """
    git('symbolic-ref', STACK_BASE_PREFIX + topic, f'refs/heads/{base}', repository=repository)


def base_branches(repository: str | None = None) -> list[str]:
    """Lists every base branch in use.

    Args:
        repository: the repository's directory, None for the current one.

    Returns:
        The setting's and the stacks' base branches, sorted.
    """
    return sorted({base_branch(repository), *stack_bases(repository).values()})


def needs_rebase(topic: str, repository: str | None = None) -> bool:
    """Tells whether a stack's base branch moved on since its last rebase.

    Args:
        topic: the stack's topic.
        repository: the repository's directory, None for the current one.

    Returns:
        True if the base branch has commits the stack hasn't.
    """
    return not is_ancestor(stack_base(topic, repository), stack_ref(topic), repository)


def stack_commits(topic: str, repository: str | None = None) -> list[str]:
    """Lists the commits of an uploaded stack that are not on its base
    branch.

    Args:
        topic: the stack's topic.
        repository: the repository's directory, None for the current one.

    Returns:
        The commits, oldest first.
    """
    return commits_between(stack_base(topic, repository), stack_ref(topic), repository)


def topic_of_branch(branch: str) -> str:
    """Finds the topic a local branch uploads to.

    Args:
        branch: the branch's short name.

    Returns:
        The branch's name, without a leading review/ (the branch names of
        older versions).
    """
    return branch[len(REVIEW_BRANCH_PREFIX):] if branch.startswith(REVIEW_BRANCH_PREFIX) else branch


def stack_version(repository: str | None = None) -> str:
    """Reads all refs a review depends on. They change with every upload,
    rebase, submit and note write, so a viewer knows when to reload.

    Args:
        repository: the repository's directory, None for the current one.

    Returns:
        A line per ref, its name and object name.
    """
    # The stacks' base branches are in it too, by their refs in
    # refs/plustwo/bases/.
    return git('for-each-ref', '--format=%(refname) %(objectname)',
               f'refs/heads/{base_branch(repository)}', NOTES_REF, PLUSTWO_PREFIX, check=False,
               repository=repository)


def worktrees_with(branch: str, repository: str | None = None) -> list[str]:
    """Finds the worktrees that have a branch checked out.

    Args:
        branch: the branch's short name.
        repository: the repository's directory, None for the current one.

    Returns:
        The worktrees' paths.
    """
    output = git('worktree', 'list', '--porcelain', repository=repository)
    paths: list[str] = []
    path = ''
    for line in output.split('\n'):
        if line.startswith('worktree '):
            path = line[len('worktree '):]
        elif f'branch refs/heads/{branch}' == line:
            paths.append(path)
    return paths
