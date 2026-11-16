# SPDX-License-Identifier: Zlib
"""Setting up a repository and the commit-msg hook."""

import hashlib
import os
import re
import time
from pathlib import Path

from plustwo.config.settings import base_branch, migrate_settings
from plustwo.git.commits import CHANGE_ID_PATTERN, change_id_of
from plustwo.git.errors import ChangeError
from plustwo.git.notes import notes_version
from plustwo.git.refs import (
    NOTES_REF, OLD_PATCH_SET_PREFIX, PATCH_SET_PREFIX, PLUSTWO_PREFIX, REVIEW_BRANCH_PREFIX,
    STACK_PREFIX, commits_between, refs_with_prefix)
from plustwo.git.repository import Repository, run_git
from plustwo.review.patch_sets import patch_sets_of
from plustwo.review.stacks import stack_ref, topic_of_branch


# The hook only calls this script, so the logic stays in one place. It finds
# it through PATH, under its new name or the old one.
_HOOK_TEXT = ('#!/bin/sh\n# Installed by plustwo: adds a Gerrit Change-Id.\n'
             'command -v plustwo >/dev/null 2>&1 && exec plustwo commit-msg-hook "$1"\n'
             'exec git-change commit-msg-hook "$1"\n')


# Hooks installed before the rename, also ours
_OLD_HOOK_TEXTS = ('#!/bin/sh\n# Installed by git change: adds a Gerrit Change-Id.\n'
                  'exec git-change commit-msg-hook "$1"\n',)


def init_repository(repository: Repository) -> list[str]:
    """Sets up a repository: installs the commit-msg hook, makes
    notes follow amends and rebases, migrates older layouts and settings.

    Args:
        repository: the repository.

    Returns:
        A line per moved setting, see migrate_settings().

    Raises:
        ChangeError: a commit-msg hook that is neither plustwo's nor
            Gerrit's exists.
    """
    path = repository.git_path('hooks/commit-msg')
    existing = path.read_text(errors='replace') if path.exists() else None
    # Gerrit's own hook adds the same Change-Id trailer, so it stays.
    is_gerrit_hook = existing is not None and 'Change-Id' in existing and 'Gerrit' in existing
    is_own_hook = existing in (_HOOK_TEXT, *_OLD_HOOK_TEXTS)
    if existing is not None and not is_own_hook and not is_gerrit_hook:
        raise ChangeError(f'{path} exists already and is not ours. Merge it by hand.')
    if not is_gerrit_hook or is_own_hook:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_HOOK_TEXT)
        path.chmod(0o755)
    if NOTES_REF not in repository.git('config', '--get-all', 'notes.rewriteRef',
                                        check=False).split('\n'):
        repository.git('config', '--add', 'notes.rewriteRef', NOTES_REF)

    # Without an existing notes ref, every amend warns that it is invalid.
    # An empty notes commit makes it exist before the first comment.
    if not notes_version(repository):
        empty_tree = repository.git('hash-object', '-t', 'tree', '-w', '--stdin', input_text='')
        notes_commit = repository.git('commit-tree', empty_tree, '-m',
                                      'Notes created by plustwo init')
        repository.git('update-ref', NOTES_REF, notes_commit)
    migrate_layout(repository, do_remove_old=True)
    return migrate_settings(repository)


def migrate_layout(repository: Repository, do_remove_old: bool = False) -> None:
    """Moves the review state of older versions into refs/plustwo/.

    The patch sets are copied from refs/patchsets/. Every local review/
    branch becomes the stack of its topic, as far as its commits are
    uploaded, i.e. the latest patch sets of their changes. The branches
    stay. Only creates refs, so running it twice or at the same time does
    no harm.

    Args:
        repository: the repository.
        do_remove_old: whether to delete the old patch set refs afterwards.
    """
    old = refs_with_prefix(repository, OLD_PATCH_SET_PREFIX)
    new = refs_with_prefix(repository, PATCH_SET_PREFIX)
    for ref, commit in old.items():
        target = PATCH_SET_PREFIX + ref[len(OLD_PATCH_SET_PREFIX):]
        if target not in new:
            repository.git('update-ref', target, commit)
    base = base_branch(repository)
    branches = refs_with_prefix(repository, f'refs/heads/{REVIEW_BRANCH_PREFIX}')
    stacks = refs_with_prefix(repository, STACK_PREFIX)
    for ref in branches:
        topic = topic_of_branch(ref[len('refs/heads/'):])
        if stack_ref(topic) in stacks:
            continue
        top = None
        for commit in commits_between(repository, base, ref):
            change_id = change_id_of(repository, commit)
            patch_sets = patch_sets_of(repository, change_id) if change_id else {}
            if not patch_sets or patch_sets[max(patch_sets)] != commit:
                break
            top = commit
        if top:
            repository.git('update-ref', stack_ref(topic), top)
    if do_remove_old:
        for ref, commit in old.items():
            repository.git('update-ref', '-d', ref, commit)


def ensure_layout(repository: Repository) -> None:
    """Migrates a repository of an older version on first use, so nothing
    breaks before plustwo init runs again.

    Args:
        repository: the repository.
    """
    refs = refs_with_prefix(repository, OLD_PATCH_SET_PREFIX, PLUSTWO_PREFIX)
    if refs and not any(ref.startswith(PLUSTWO_PREFIX) for ref in refs):
        migrate_layout(repository)


def _new_change_id(message: str) -> str:
    """Makes a new Change-Id, as Gerrit's hook makes them.

    Args:
        message: the commit message, part of the seed.

    Returns:
        "I" and 40 hex digits.
    """
    seed = f'{time.time_ns()} {os.getpid()} {os.urandom(16).hex()} {message}'
    return 'I' + hashlib.sha1(seed.encode()).hexdigest()


def add_change_id(message_path: str) -> None:
    """Adds a Change-Id trailer to a commit message file if it has none.

    Like Gerrit's hook, it leaves out fixup!, squash! and amend! commits,
    which get folded into a commit that has its own Change-Id, and empty
    messages, which abort the commit anyway.

    Args:
        message_path: the path of the commit message file.
    """
    message = Path(message_path).read_text()
    meaningful = [line for line in message.split('\n') if not line.startswith('#')]
    if not ''.join(meaningful).strip():
        return
    if re.match(r'(fixup|squash|amend)! ', meaningful[0]):
        return
    if CHANGE_ID_PATTERN.search(message):
        return
    run_git('interpret-trailers', '--in-place', '--trailer',
            f'Change-Id: {_new_change_id(message)}', message_path)
