# SPDX-License-Identifier: Zlib
"""Bringing the reviewer's changes to the author's branch."""

import subprocess

from plustwo.git.commits import change_id_of
from plustwo.git.errors import ChangeError
from plustwo.git.refs import commits_between, current_branch
from plustwo.git.repository import git, git_command
from plustwo.review.patch_sets import patch_sets_of
from plustwo.review.setup import ensure_layout
from plustwo.review.stacks import stack_base, stack_ref, topic_of_branch


def pull(repository=None):
    """Brings what the reviewer did into the checked out branch: a rebase,
    a submit. Like a fetch and rebase from Gerrit. Returns what happened,
    as a short line.

    The branch's commits that are patch sets are the uploaded ones. Without
    newer local commits, the branch moves to the uploaded stack, or to the
    base branch when all was submitted. Newer local commits are rebased
    onto it, which stops like any rebase on a conflict. Needs a clean
    worktree. The branch is never deleted.
    """
    branch = current_branch(repository)
    base = stack_base(topic_of_branch(branch or ''), repository)
    if branch is None:
        raise ChangeError('HEAD is detached, check out the branch to pull into')
    if base == branch:
        raise ChangeError(f'{base} is the base branch, check out the branch to pull into')
    if git('status', '--porcelain', '--untracked-files=no', repository=repository):
        raise ChangeError('the worktree has changes, commit or stash them first')
    ensure_layout(repository)
    topic = topic_of_branch(branch)
    server = git('rev-parse', '--verify', '--quiet', stack_ref(topic), check=False,
                 repository=repository)
    target = server or git('rev-parse', base, repository=repository)
    local = commits_between(base, 'HEAD', repository)
    uploaded = []
    for commit in local:
        change_id = change_id_of(commit, repository)
        if not change_id or commit not in patch_sets_of(change_id, repository).values():
            break
        uploaded.append(commit)
    newer = local[len(uploaded):]
    head = git('rev-parse', 'HEAD', repository=repository)
    if not local and head == target:
        return f'{branch} has nothing beyond {base}' + ('' if server else ', all is submitted')
    if head == target or (uploaded and uploaded[-1] == target):
        return f'{branch} is up to date'
    if not newer:
        git('reset', '--quiet', '--keep', target, repository=repository)
        return (f'{branch} moved to the reviewed stack {topic}' if server
                else f'{topic} is submitted, {branch} has nothing beyond {base} now')
    upstream = uploaded[-1] if uploaded else git('merge-base', base, 'HEAD',
                                                 repository=repository)
    result = subprocess.run([*git_command(repository), 'rebase',
                             '--quiet', '--onto', target, upstream, branch],
                            capture_output=True, text=True)
    if 0 != result.returncode:
        return (f'conflict while moving {len(newer)} local commit(s) onto {topic}: resolve, '
                'then git rebase --continue')
    return f'{len(newer)} local commit(s) moved onto ' + (topic if server else base)
