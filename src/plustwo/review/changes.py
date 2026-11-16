# SPDX-License-Identifier: Zlib
"""Finding changes and reading and writing their review."""

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from plustwo.config.registry import registered_repositories, review_repositories
from plustwo.config.settings import base_branch
from plustwo.git.commits import CHANGE_ID_PATTERN, change_id_of
from plustwo.git.errors import ChangeError
from plustwo.git.lock import review_lock
from plustwo.git.notes import read_note, read_notes, write_note
from plustwo.git.refs import (
    PATCH_SET_PREFIX, commits_between, current_branch, is_ancestor, refs_with_prefix, toplevel)
from plustwo.git.repository import current_repository, git, use_repository
from plustwo.model.change import Change
from plustwo.model.comment import OLD_STATUSES, RESOLVED, Comment
from plustwo.model.message import Message
from plustwo.model.move import Move
from plustwo.model.vote import Vote
from plustwo.review.patch_sets import patch_sets_of
from plustwo.review.setup import ensure_layout
from plustwo.review.stacks import base_branches, stack_bases, stack_ref, topics


# What an edit of update_change() returns
T = TypeVar('T')


def change_from(commit: str, topic: str, note: dict[str, Any], repository: str | None = None,
                change_id: str | None = None, subject: str | None = None,
                patch_sets: dict[int, str] | None = None) -> Change:
    """Builds a Change from a commit of a stack and its note. A note whose
    fields were broken by hand counts as empty, see parse_note().

    Args:
        commit: the change's commit.
        topic: the topic of the change's stack, '' for none.
        note: the commit's review note.
        repository: the repository's directory, None for the current one.
        change_id: the Change-Id, None to read it from the commit.
        subject: the commit message's first line, None to read it.
        patch_sets: each patch set's number, mapped to its commit. None to
            read them from git.

    Returns:
        The change.
    """
    try:
        return _change_from(commit, topic, note, repository, change_id, subject, patch_sets)
    except (TypeError, ValueError, KeyError, AttributeError):
        return _change_from(commit, topic, {}, repository, change_id, subject, patch_sets)


def _change_from(commit: str, topic: str, note: dict[str, Any], repository: str | None,
                 change_id: str | None, subject: str | None,
                 patch_sets: dict[int, str] | None) -> Change:
    """Builds a Change, see change_from(), failing on a broken note.

    Args:
        commit: the change's commit.
        topic: the topic of the change's stack.
        note: the commit's review note.
        repository: the repository's directory, None for the current one.
        change_id: the Change-Id, None to read it.
        subject: the commit message's first line, None to read it.
        patch_sets: the patch sets, None to read them.

    Returns:
        The change.

    Raises:
        TypeError: a field of the note has the wrong form, also KeyError,
            ValueError and AttributeError.
    """
    change_id = change_id or change_id_of(commit, repository) or ''
    comments = []
    for comment in note.get('comments', []):
        # A copy: one note can be read for several copies of a change
        comment = dict(comment)
        messages = [Message(**message) for message in comment.pop('messages', [])]
        # Notes of older versions keep a status per thread instead
        old_status = OLD_STATUSES.get(comment.pop('status', None))
        sent = [message for message in messages if message.sent]
        if RESOLVED == old_status and sent and all(message.unresolved is None
                                                  for message in messages):
            sent[-1].unresolved = False
        comment['moves'] = [Move(**move) for move in comment.get('moves', [])]
        comment.setdefault('id', hashlib.sha1(
            f'{change_id} {comment["number"]} {comment["place"]}'.encode()).hexdigest()[:12])
        comments.append(Comment(**comment, messages=messages))
    return Change(change_id=change_id,
                  commit=commit,
                  subject=subject if subject is not None else git(
                      'log', '-1', '--format=%s', commit, repository=repository),
                  topic=topic,
                  comments=comments,
                  patch_sets=(patch_sets if patch_sets is not None
                              else patch_sets_of(change_id, repository)),
                  votes=[Vote(**vote) for vote in note.get('votes', [])],
                  needs_verified=note.get('verified_required', False),
                  rebase_is_requested=note.get('rebase_requested', False),
                  rebased_patch_set=note.get('rebased_patch_set', 0),
                  uploads={int(number): time for number, time in note.get('uploads', {}).items()},
                  reviewed=note.get('reviewed', {}),
                  uploaders={int(number): name
                             for number, name in note.get('uploaders', {}).items()},
                  is_wip=note.get('wip', False))


def note_of(change: Change) -> dict[str, Any]:
    """Writes a change's review as its note.

    Args:
        change: the change.

    Returns:
        The note's JSON object.
    """
    return {'comments': [{'number': comment.number,
                          'id': comment.id,
                          'place': comment.place,
                          'patch_set': comment.patch_set,
                          'side': comment.side,
                          'messages': [vars(message) for message in comment.messages],
                          **({'moves': [vars(move) for move in comment.moves]}
                             if comment.moves else {})}
                         for comment in change.comments],
            'votes': [vars(vote) for vote in change.votes],
            'verified_required': change.needs_verified,
            'rebase_requested': change.rebase_is_requested,
            'rebased_patch_set': change.rebased_patch_set,
            'uploads': {str(number): time for number, time in change.uploads.items()},
            'reviewed': change.reviewed,
            'uploaders': {str(number): name for number, name in change.uploaders.items()},
            'wip': change.is_wip}


def changes_in_stack(repository: str | None = None) -> list[Change]:
    """Reads all changes of the uploaded stacks of a repository.

    Args:
        repository: the repository's directory, None for the current one.

    Returns:
        The changes, stack by stack and oldest first within a stack.
    """
    ensure_layout(repository)
    # A few git calls for all changes, not several per change
    all_patch_sets: dict[str, dict[int, str]] = {}
    output = git('for-each-ref', '--format=%(refname) %(objectname)', PATCH_SET_PREFIX,
                 repository=repository)
    for line in filter(None, output.split('\n')):
        ref, commit = line.split(' ')
        change_id, number = ref[len(PATCH_SET_PREFIX):].rsplit('/', 1)
        all_patch_sets.setdefault(change_id, {})[int(number)] = commit
    stacks = []
    default = base_branch(repository)
    bases = stack_bases(repository)
    for topic in topics(repository):
        base = bases.get(topic, default)
        output = git('log', '--reverse', '--format=%H%x1f%s%x1f%B%x1e',
                     f'{base}..{stack_ref(topic)}', check=False, repository=repository)
        for entry in filter(str.strip, output.split('\x1e')):
            commit, subject, body = entry.strip('\n').split('\x1f', 2)
            match = CHANGE_ID_PATTERN.search(body)
            if match:
                stacks.append((topic, commit, subject, match.group(1)))
    notes = read_notes([commit for _, commit, _, _ in stacks], repository)
    changes = [change_from(commit, topic, notes.get(commit, {}), repository, change_id, subject,
                           all_patch_sets.get(change_id, {}))
               for topic, commit, subject, change_id in stacks]
    root = toplevel(repository)
    for change in changes:
        change.repository = root
        change.repository_name = Path(root).name if root else ''
        change.base = bases.get(change.topic, default)
    return changes


def all_changes(list_name: str | None = None) -> list[Change]:
    """Reads the changes of every repository reviews are shown for, see
    review_repositories().

    Args:
        list_name: the list of repositories, None for all.

    Returns:
        The changes, repository by repository. Each change knows its
        repository and its registered name.
    """
    names = {path: name for name, path in registered_repositories().items()}
    changes = []
    for repository in review_repositories(list_name):
        for change in changes_in_stack(repository):
            change.repository_name = names.get(change.repository or '', change.repository_name)
            changes.append(change)
    return changes


def locate(change_id: str) -> str | None:
    """Finds the repository a change is in review in.

    Args:
        change_id: the full Change-Id.

    Returns:
        The working directory's repository if it has the change, else the
        first registered one that has it. None when none has it, which
        means the working directory.
    """
    for repository in review_repositories():
        if git('for-each-ref', '--count=1', f'{PATCH_SET_PREFIX}{change_id}/', check=False,
               repository=repository):
            return repository
    return None


def find_change(reference: str, repository: str | None = None) -> Change:
    """Finds the change a reference refers to. Without a repository, later
    calls without one act on the change's repository.

    Args:
        reference: an overview number, a Change-Id prefix or repo:prefix.
        repository: the repository's directory. None to search all
            repositories reviews are shown for, numbers counting over all
            of them.

    Returns:
        The change.

    Raises:
        ChangeError: no change or several fit, or no such repository.
    """
    if repository is not None:
        changes = changes_in_stack(repository)
    else:
        changes = all_changes()
        name, colon, rest = reference.rpartition(':')
        if colon:
            changes = [change for change in changes if name in (change.repository_name, Path(
                change.repository or '').name)]
            if not changes:
                raise ChangeError(f'no repository {name} with changes in review')
            reference = rest
    change = _find_in(changes, reference)
    if repository is None:
        use_repository(change.repository)
    return change


def submitted_change(reference: str, repository: str | None = None) -> Change:
    """Finds a submitted change by its patch sets, whose newest one is on a
    base branch. Its review is the note of that commit.

    Args:
        reference: a Change-Id prefix or repo:prefix.
        repository: the repository's directory, None to search all
            repositories reviews are shown for.

    Returns:
        The change, marked submitted.

    Raises:
        ChangeError: no submitted change or several fit.
    """
    name, colon, prefix = reference.rpartition(':')
    if repository is not None:
        repositories = [repository]
    else:
        registered = registered_repositories()
        repositories = [path for path in review_repositories()
                        if not colon or name in (Path(path).name, next(
                            (key for key, value in registered.items() if value == path), None))]
    found = []
    for path in repositories:
        output = git('for-each-ref', '--format=%(refname)', PATCH_SET_PREFIX, check=False,
                     repository=path)
        ids = {ref[len(PATCH_SET_PREFIX):].rsplit('/', 1)[0]
               for ref in filter(None, output.split('\n'))}
        found += [(path, change_id) for change_id in ids if change_id.startswith(prefix)]
    if len(found) > 1:
        raise ChangeError(f'"{reference}" fits several changes, give more of the Change-Id')
    if found:
        path, change_id = found[0]
        patch_sets = patch_sets_of(change_id, path)
        commit = patch_sets[max(patch_sets)]
        base = next((branch for branch in base_branches(path)
                     if is_ancestor(commit, branch, path)), None)
        if base is not None:
            change = change_from(commit, '', read_note(commit, path), path, change_id,
                                 patch_sets=patch_sets)
            change.is_submitted = True
            change.base = base
            change.repository = toplevel(path)
            change.repository_name = Path(change.repository or path).name
            return change
    raise ChangeError(f'no change with a Change-Id starting with "{prefix}"')


def _find_in(changes: list[Change], reference: str) -> Change:
    """Finds the change of a list a reference refers to.

    Args:
        changes: the changes, in overview order.
        reference: an overview number or a Change-Id prefix.

    Returns:
        The change.

    Raises:
        ChangeError: no change or several fit.
    """
    if reference.isdigit():
        index = int(reference)
        if 1 <= index <= len(changes):
            return changes[index - 1]
        raise ChangeError(f'no change number {index}, the stacks have {len(changes)}')
    matches = [change for change in changes if change.change_id.startswith(reference)]
    if 1 == len(matches):
        return matches[0]
    if not matches:
        raise ChangeError(f'no change with a Change-Id starting with "{reference}"')
    raise ChangeError(f'"{reference}" fits several changes, give more of the Change-Id')


def update_change(change_id: str, edit: Callable[[Change], T],
                  repository: str | None = None) -> T:
    """Changes a change's review under the lock and stores it. Reads the
    change fresh, because another process may have written since it was
    shown, and because an amend may have given it a new commit.

    Args:
        change_id: the full Change-Id.
        edit: a function that changes the change it is given.
        repository: the repository's directory, None for the one the
            change is in review in.

    Returns:
        What edit returned.

    Raises:
        ChangeError: the change is not in review any more.
    """
    repository = current_repository(repository) or locate(change_id)
    with review_lock(repository):
        for change in changes_in_stack(repository):
            if change_id == change.change_id:
                result = edit(change)
                write_note(change.commit, note_of(change), repository)
                return result
    raise ChangeError(f'the change {change_id[:8]} is not in review any more')


def comment_of(change: Change, number: int) -> Comment:
    """Finds a thread of a change by its number.

    Args:
        change: the change.
        number: the thread's number.

    Returns:
        The thread.

    Raises:
        ChangeError: the change has no thread with that number.
    """
    comment = change.comment(number)
    if comment is None:
        raise ChangeError(f'{change.short_id()} has no comment {number}')
    return comment


def reference_change_id(reference: str, repository: str | None = None) -> str:
    """Finds the Change-Id a reference of an upload refers to.

    Args:
        reference: an overview number of an uploaded change, or a Change-Id
            prefix.
        repository: the repository's directory, None for the current one.

    Returns:
        The full Change-Id for a number, else the reference itself.
    """
    if reference.isdigit():
        return find_change(reference, repository).change_id
    return reference


def branch_with_change(reference: str, repository: str | None = None) -> str:
    """Finds the local branch holding a change, for an upload naming one.

    Args:
        reference: an overview number or a Change-Id prefix.
        repository: the repository's directory, None for the current one.

    Returns:
        The checked out branch if it has the change, else the only branch
        that has it.

    Raises:
        ChangeError: no branch or several have the change.
    """
    base = base_branch(repository)
    current = current_branch(repository)
    try:
        change_id = reference_change_id(reference, repository)
    except ChangeError:
        # Numbers count uploaded changes only. A new change has none yet,
        # so an old habit like "upload 3" uploads the checked out branch.
        if current and current != base:
            return current
        raise

    def has_change(branch: str) -> bool:
        """Tells whether a branch has the change.

        Args:
            branch: the branch's name.

        Returns:
            True if a commit between the base and the branch has the
            Change-Id.
        """
        return any((change_id_of(commit, repository) or '').startswith(change_id)
                   for commit in commits_between(base, branch, repository))

    if current and current != base and has_change(current):
        return current
    branches = [ref[len('refs/heads/'):] for ref in refs_with_prefix('refs/heads/',
                                                                      repository=repository)]
    matches = [branch for branch in branches if branch != base and has_change(branch)]
    if 1 != len(matches):
        raise ChangeError(f'{len(matches)} local branches have the change {reference}, '
                          'check out the one to upload')
    return matches[0]
