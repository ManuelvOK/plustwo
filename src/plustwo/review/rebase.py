# SPDX-License-Identifier: Zlib
"""Rebasing a stack onto its base branch."""

import os
import subprocess

from plustwo.git.commits import commit_author_and_message
from plustwo.git.errors import ChangeError
from plustwo.git.lock import review_lock
from plustwo.git.notes import read_note, write_note
from plustwo.git.refs import is_ancestor
from plustwo.git.repository import git, git_command
from plustwo.review.changes import change_from, note_of
from plustwo.review.patch_sets import add_patch_set
from plustwo.review.stacks import stack_base, stack_commits, stack_ref


def rebase_stack(topic, repository=None):
    """The reviewer's rebase of a stack onto its base branch, like Gerrit's
    Rebase button. It needs no worktree: each commit is merged onto the new
    parent with git merge-tree. Every change gets a new patch set with its
    review, and the stack moves. The local branches stay, the author's
    plustwo pull brings the new commits.

    On a conflict nothing changes. The top change gets a rebase request
    instead, for the author to rebase locally and upload. Returns the new
    patch sets [(change, number, kind)], or None after a conflict.
    """
    with review_lock(repository):
        base = stack_base(topic, repository)
        top = git('rev-parse', '--verify', '--quiet', stack_ref(topic), check=False,
                  repository=repository)
        if not top:
            raise ChangeError(f'no stack called {topic}')
        if is_ancestor(base, top, repository):
            raise ChangeError(f'{topic} is on {base} already')
        commits = stack_commits(topic, repository)
        parent = git('rev-parse', base, repository=repository)
        rebased = []
        for commit in commits:
            merge = subprocess.run([*git_command(repository),
                                    'merge-tree', '--write-tree', f'--merge-base={commit}^',
                                    parent, commit], capture_output=True, text=True)
            if 0 != merge.returncode:
                note = read_note(top, repository)
                note['rebase_requested'] = True
                write_note(top, note, repository)
                return None
            (name, email, date), message = commit_author_and_message(commit, repository)
            environment = dict(os.environ, GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email,
                               GIT_AUTHOR_DATE=date)
            parent = subprocess.run(
                [*git_command(repository), 'commit-tree',
                 merge.stdout.split('\n')[0], '-p', parent, '-F', '-'],
                input=message, capture_output=True, text=True, env=environment,
                check=True).stdout.strip()
            rebased.append((commit, parent))

        uploaded = []
        for old, new in rebased:
            change = change_from(new, topic, read_note(old, repository), repository)
            change.rebase_requested = False
            number, kind = add_patch_set(change, repository)
            change.rebased_patch_set = number
            write_note(new, note_of(change), repository)
            uploaded.append((change, number, kind))
        git('update-ref', stack_ref(topic), parent, top, repository=repository)
        return uploaded
