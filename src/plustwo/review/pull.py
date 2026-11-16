# SPDX-License-Identifier: Zlib
"""Bringing the reviewer's changes to the author's branch."""

import subprocess

from plustwo.git.commits import change_id_of
from plustwo.git.errors import ChangeError
from plustwo.git.refs import commits_between, current_branch
from plustwo.git.repository import Repository
from plustwo.review.patch_sets import patch_sets_of
from plustwo.review.setup import ensure_layout
from plustwo.review.stacks import stack_base, stack_ref, topic_of_branch


def pull(repository: Repository) -> str:
    """Brings what the reviewer did into the checked out branch: a rebase,
    a submit. Like a fetch and rebase from Gerrit.

    The branch's commits that are patch sets are the uploaded ones. Without
    newer local commits, the branch moves to the uploaded stack, or to the
    base branch when all was submitted. Newer local commits are rebased
    onto it, which stops like any rebase on a conflict. Needs a clean
    worktree. The branch is never deleted.

    Args:
        repository: the repository.

    Returns:
        What happened, as a short line.

    Raises:
        ChangeError: HEAD is detached or the base branch, or the worktree
            has changes.
    """
    branch = current_branch(repository)
    base = stack_base(repository, topic_of_branch(branch or ''))
    if branch is None:
        raise ChangeError('HEAD is detached, check out the branch to pull into')
    if base == branch:
        raise ChangeError(f'{base} is the base branch, check out the branch to pull into')
    if repository.git('status', '--porcelain', '--untracked-files=no'):
        raise ChangeError('the worktree has changes, commit or stash them first')
    ensure_layout(repository)
    topic = topic_of_branch(branch)
    server = repository.git('rev-parse', '--verify', '--quiet', stack_ref(topic), check=False)
    target = server or repository.git('rev-parse', base)
    local = commits_between(repository, base, 'HEAD')
    uploaded = []
    for commit in local:
        change_id = change_id_of(repository, commit)
        if not change_id or commit not in patch_sets_of(repository, change_id).values():
            break
        uploaded.append(commit)
    newer = local[len(uploaded):]
    head = repository.git('rev-parse', 'HEAD')
    if not local and head == target:
        return f'{branch} has nothing beyond {base}' + ('' if server else ', all is submitted')
    if head == target or (uploaded and uploaded[-1] == target):
        return f'{branch} is up to date'
    if not newer:
        repository.git('reset', '--quiet', '--keep', target)
        return (f'{branch} moved to the reviewed stack {topic}' if server
                else f'{topic} is submitted, {branch} has nothing beyond {base} now')
    upstream = uploaded[-1] if uploaded else repository.git('merge-base', base, 'HEAD')
    result = subprocess.run([*repository.git_command(), 'rebase',
                             '--quiet', '--onto', target, upstream, branch],
                            capture_output=True, text=True)
    if 0 != result.returncode:
        return (f'conflict while moving {len(newer)} local commit(s) onto {topic}: resolve, '
                'then git rebase --continue')
    return f'{len(newer)} local commit(s) moved onto ' + (topic if server else base)
