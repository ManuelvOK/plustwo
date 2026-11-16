# SPDX-License-Identifier: Zlib
"""Stacks: the uploaded commits of a topic and their base branch."""

from plustwo.config.settings import base_branch
from plustwo.git.refs import (
    NOTES_REF, PLUSTWO_PREFIX, REVIEW_BRANCH_PREFIX, STACK_BASE_PREFIX, STACK_PREFIX,
    commits_between, is_ancestor, refs_with_prefix)
from plustwo.git.repository import git


def topics(repository=None):
    """The topics of the uploaded stacks, sorted."""
    return sorted(ref[len(STACK_PREFIX):] for ref in refs_with_prefix(STACK_PREFIX,
                                                                      repository=repository))


def stack_ref(topic):
    """The ref of an uploaded stack."""
    return STACK_PREFIX + topic


def stack_bases(repository=None):
    """The recorded base branches of the stacks: topic -> branch. Stacks
    uploaded by older versions have none.
    """
    output = git('for-each-ref', '--format=%(refname) %(symref)', STACK_BASE_PREFIX, check=False,
                 repository=repository)
    bases = {}
    for line in filter(None, output.split('\n')):
        ref, _, target = line.partition(' ')
        if target.startswith('refs/heads/'):
            bases[ref[len(STACK_BASE_PREFIX):]] = target[len('refs/heads/'):]
    return bases


def stack_base(topic, repository=None):
    """The base branch of a stack: the one recorded at its upload, else the
    setting review.base.
    """
    target = git('symbolic-ref', '--quiet', STACK_BASE_PREFIX + topic, check=False,
                 repository=repository)
    if target.startswith('refs/heads/'):
        return target[len('refs/heads/'):]
    return base_branch(repository)


def set_stack_base(topic, base, repository=None):
    """Records the base branch of a stack."""
    git('symbolic-ref', STACK_BASE_PREFIX + topic, f'refs/heads/{base}', repository=repository)


def base_branches(repository=None):
    """Every base branch in use: the setting's and the stacks' ones."""
    return sorted({base_branch(repository), *stack_bases(repository).values()})


def needs_rebase(topic, repository=None):
    """Whether the stack's base branch moved on since its last rebase."""
    return not is_ancestor(stack_base(topic, repository), stack_ref(topic), repository)


def stack_commits(topic, repository=None):
    """Commits of an uploaded stack that are not on its base branch, oldest
    first.
    """
    return commits_between(stack_base(topic, repository), stack_ref(topic), repository)


def topic_of_branch(branch):
    """The topic a local branch uploads to: its name, without a leading
    review/ (the branch names of older versions).
    """
    return branch[len(REVIEW_BRANCH_PREFIX):] if branch.startswith(REVIEW_BRANCH_PREFIX) else branch


def stack_version(repository=None):
    """All refs a review depends on, with their hashes. They change with
    every upload, rebase, submit and note write, so a viewer knows when to
    reload.
    """
    # The stacks' base branches are in it too, by their refs in
    # refs/plustwo/bases/.
    return git('for-each-ref', '--format=%(refname) %(objectname)',
               f'refs/heads/{base_branch(repository)}', NOTES_REF, PLUSTWO_PREFIX, check=False,
               repository=repository)


def worktrees_with(branch, repository=None):
    """Paths of the worktrees that have a branch checked out."""
    output = git('worktree', 'list', '--porcelain', repository=repository)
    paths, path = [], None
    for line in output.split('\n'):
        if line.startswith('worktree '):
            path = line[len('worktree '):]
        elif f'branch refs/heads/{branch}' == line:
            paths.append(path)
    return paths
