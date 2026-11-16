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
from plustwo.git.repository import git
from plustwo.review.patch_sets import patch_sets_of
from plustwo.review.stacks import stack_ref, topic_of_branch


# The hook only calls this script, so the logic stays in one place. It finds
# it through PATH, under its new name or the old one.
HOOK_TEXT = ('#!/bin/sh\n# Installed by plustwo: adds a Gerrit Change-Id.\n'
             'command -v plustwo >/dev/null 2>&1 && exec plustwo commit-msg-hook "$1"\n'
             'exec git-change commit-msg-hook "$1"\n')


# Hooks installed before the rename, also ours
OLD_HOOK_TEXTS = ('#!/bin/sh\n# Installed by git change: adds a Gerrit Change-Id.\n'
                  'exec git-change commit-msg-hook "$1"\n',)


def init_repository():
    """Installs the commit-msg hook and makes notes follow amends and rebases."""
    path = Path(git('rev-parse', '--git-path', 'hooks/commit-msg'))
    existing = path.read_text(errors='replace') if path.exists() else None
    # Gerrit's own hook adds the same Change-Id trailer, so it stays.
    is_gerrit_hook = existing is not None and 'Change-Id' in existing and 'Gerrit' in existing
    if existing is not None and existing not in (HOOK_TEXT, *OLD_HOOK_TEXTS) and not is_gerrit_hook:
        raise ChangeError(f'{path} exists already and is not ours. Merge it by hand.')
    if not is_gerrit_hook or existing in (HOOK_TEXT, *OLD_HOOK_TEXTS):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(HOOK_TEXT)
        path.chmod(0o755)
    if NOTES_REF not in git('config', '--get-all', 'notes.rewriteRef', check=False).split('\n'):
        git('config', '--add', 'notes.rewriteRef', NOTES_REF)

    # Without an existing notes ref, every amend warns that it is invalid.
    # An empty notes commit makes it exist before the first comment.
    if not notes_version():
        empty_tree = git('hash-object', '-t', 'tree', '-w', '--stdin', input_text='')
        notes_commit = git('commit-tree', empty_tree, '-m', 'Notes created by plustwo init')
        git('update-ref', NOTES_REF, notes_commit)
    migrate_layout(remove_old=True)
    return migrate_settings()


def migrate_layout(remove_old=False, repository=None):
    """Moves the review state of older versions into refs/plustwo/.

    The patch sets are copied from refs/patchsets/. Every local review/
    branch becomes the stack of its topic, as far as its commits are
    uploaded, i.e. the latest patch sets of their changes. The branches
    stay. Only creates refs, so running it twice or at the same time does
    no harm. remove_old deletes the old patch set refs afterwards.
    """
    old = refs_with_prefix(OLD_PATCH_SET_PREFIX, repository=repository)
    new = refs_with_prefix(PATCH_SET_PREFIX, repository=repository)
    for ref, commit in old.items():
        target = PATCH_SET_PREFIX + ref[len(OLD_PATCH_SET_PREFIX):]
        if target not in new:
            git('update-ref', target, commit, repository=repository)
    base = base_branch(repository)
    branches = refs_with_prefix(f'refs/heads/{REVIEW_BRANCH_PREFIX}', repository=repository)
    stacks = refs_with_prefix(STACK_PREFIX, repository=repository)
    for ref in branches:
        topic = topic_of_branch(ref[len('refs/heads/'):])
        if stack_ref(topic) in stacks:
            continue
        top = None
        for commit in commits_between(base, ref, repository):
            change_id = change_id_of(commit, repository)
            patch_sets = patch_sets_of(change_id, repository) if change_id else {}
            if not patch_sets or patch_sets[max(patch_sets)] != commit:
                break
            top = commit
        if top:
            git('update-ref', stack_ref(topic), top, repository=repository)
    if remove_old:
        for ref, commit in old.items():
            git('update-ref', '-d', ref, commit, repository=repository)


def ensure_layout(repository=None):
    """Migrates a repository of an older version on first use, so nothing
    breaks before plustwo init runs again.
    """
    refs = refs_with_prefix(OLD_PATCH_SET_PREFIX, PLUSTWO_PREFIX, repository=repository)
    if refs and not any(ref.startswith(PLUSTWO_PREFIX) for ref in refs):
        migrate_layout(repository=repository)


def new_change_id(message):
    """A new Change-Id: "I" and 40 hex digits, as Gerrit's hook makes them."""
    seed = f'{time.time_ns()} {os.getpid()} {os.urandom(16).hex()} {message}'
    return 'I' + hashlib.sha1(seed.encode()).hexdigest()


def add_change_id(message_path):
    """Adds a Change-Id trailer to a commit message file if it has none.

    Like Gerrit's hook, it leaves out fixup!, squash! and amend! commits,
    which get folded into a commit that has its own Change-Id, and empty
    messages, which abort the commit anyway.
    """
    message = Path(message_path).read_text()
    meaningful = [line for line in message.split('\n') if not line.startswith('#')]
    if not ''.join(meaningful).strip():
        return
    if re.match(r'(fixup|squash|amend)! ', meaningful[0]):
        return
    if CHANGE_ID_PATTERN.search(message):
        return
    git('interpret-trailers', '--in-place', '--trailer',
        f'Change-Id: {new_change_id(message)}', message_path)
