# SPDX-License-Identifier: Zlib
"""Rebasing a stack onto its base branch."""

import os
import subprocess

from plustwo.git.commits import commit_author_and_message
from plustwo.git.errors import ChangeError
from plustwo.git.lock import review_lock
from plustwo.git.notes import read_note, write_note
from plustwo.git.refs import is_ancestor
from plustwo.git.repository import Repository
from plustwo.model.change import Change
from plustwo.review.changes import change_from, note_of
from plustwo.review.patch_sets import add_patch_set
from plustwo.review.stacks import stack_base, stack_commits, stack_ref


def rebase_stack(repository: Repository, topic: str
                 ) -> list[tuple[Change, int, str | None]] | None:
    """Rebases a stack onto its base branch for the reviewer, like Gerrit's
    Rebase button. It needs no worktree: each commit is merged onto the new
    parent with git merge-tree. Every change gets a new patch set with its
    review, and the stack moves. The local branches stay, the author's
    plustwo pull brings the new commits. On a conflict nothing changes. The
    top change gets a rebase request instead, for the author to rebase
    locally and upload.

    Args:
        repository: the repository.
        topic: the stack's topic.

    Returns:
        (change, patch set number, patch set type) per new patch set, see
        add_patch_set(). None after a conflict.

    Raises:
        ChangeError: there is no such stack, or it is on its base already.
    """
    with review_lock(repository):
        base = stack_base(repository, topic)
        top = repository.git('rev-parse', '--verify', '--quiet', stack_ref(topic), check=False)
        if not top:
            raise ChangeError(f'no stack called {topic}')
        if is_ancestor(repository, base, top):
            raise ChangeError(f'{topic} is on {base} already')
        commits = stack_commits(repository, topic)
        parent = repository.git('rev-parse', base)
        # Each commit merged onto the new parent, as (old, new) commits
        rebased = []
        for commit in commits:
            merge = subprocess.run([*repository.git_command(),
                                    'merge-tree', '--write-tree', f'--merge-base={commit}^',
                                    parent, commit], capture_output=True, text=True)
            if 0 != merge.returncode:
                note = read_note(repository, top)
                note['rebase_requested'] = True
                write_note(repository, top, note)
                return None
            (name, email, date), message = commit_author_and_message(repository, commit)
            environment = dict(os.environ, GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email,
                               GIT_AUTHOR_DATE=date)
            parent = subprocess.run(
                [*repository.git_command(), 'commit-tree',
                 merge.stdout.split('\n')[0], '-p', parent, '-F', '-'],
                input=message, capture_output=True, text=True, env=environment,
                check=True).stdout.strip()
            rebased.append((commit, parent))

        # A new patch set per change, with its review
        uploaded = []
        for old, new in rebased:
            change = change_from(repository, new, topic, read_note(repository, old))
            change.rebase_is_requested = False
            number, patch_set_type_name = add_patch_set(change)
            change.rebased_patch_set = number
            write_note(repository, new, note_of(change))
            uploaded.append((change, number, patch_set_type_name))
        repository.git('update-ref', stack_ref(topic), parent, top)
        return uploaded
