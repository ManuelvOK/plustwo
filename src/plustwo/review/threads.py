# SPDX-License-Identifier: Zlib
"""Comment threads: writing, replying, drafts and sending."""

import os
import subprocess
import time

from plustwo.git.commit_message import commit_message_text
from plustwo.git.errors import ChangeError
from plustwo.git.files import file_lines, renames_of
from plustwo.git.repository import Repository
from plustwo.model.acknowledgement import is_acknowledgement
from plustwo.model.change import Change
from plustwo.model.comment import OLD_STATUSES, RESOLVED, STATUSES, UNRESOLVED, Comment
from plustwo.model.message import Message
from plustwo.model.place import COMMIT_MSG, NEW_SIDE, OLD_SIDE, PATCHSET_LEVEL, split_place
from plustwo.model.roles import AUTHOR, REVIEWER
from plustwo.review.changes import comment_of, update_change
from plustwo.review.porting import thread_version


def line_at(repository: Repository, commit: str, place: str, side: str = NEW_SIDE) -> str:
    """Reads the source line a place refers to.

    Args:
        repository: the repository.
        commit: the commit the place counts in.
        place: the place, like src/main.cc:12.
        side: NEW_SIDE, or OLD_SIDE to read the line from the commit's
            parent.

    Returns:
        The line, stripped. '' if the place has no line number or the file
        has no such line.
    """
    path, _, line = place.rpartition(':')
    if not path or not line.isdigit():
        return ''
    if COMMIT_MSG == path:
        if OLD_SIDE == side:
            return ''
        text = commit_message_text(repository, commit)
    elif OLD_SIDE == side:
        # A renamed file had its old path in the parent
        old_path = renames_of(repository, commit).get(path, path)
        text = repository.git('show', f'{commit}^:{old_path}', check=False)
    else:
        text = repository.git('show', f'{commit}:{path}', check=False)
    lines = text.split('\n')
    number = int(line)
    return lines[number - 1].strip() if 1 <= number <= len(lines) else ''


def status_name(name: str) -> str:
    """Finds the status a name stands for.

    Args:
        name: unresolved or resolved, or one of the older names open,
            agreed (unresolved), dropped, done (resolved).

    Returns:
        UNRESOLVED or RESOLVED.

    Raises:
        ChangeError: the name stands for no status.
    """
    status = name if name in STATUSES else OLD_STATUSES.get(name)
    if status is None:
        raise ChangeError(f'unknown status "{name}", use {UNRESOLVED} or {RESOLVED}')
    return status


def add_comment(repository: Repository, change_id: str, place: str, text: str, is_sent: bool = True,
                side: str = NEW_SIDE, author: str = REVIEWER, status: str | None = None,
                patch_set: int | None = None) -> int:
    """Starts a thread. Like in Gerrit it is unresolved unless status says
    otherwise, and a message on the whole change is resolved, like one sent
    with the drafts. Like Gerrit, refuses a file the version doesn't have
    and a line past its end.

    Args:
        repository: the repository.
        change_id: the full Change-Id.
        place: the place, see comment_place() for a line of a diff.
        text: the first message's text.
        is_sent: whether the message is sent, else a reviewer's draft. The
            author's threads are always sent.
        side: the side the place is on, NEW_SIDE or OLD_SIDE.
        author: who writes, REVIEWER or AUTHOR.
        status: the thread's status or an older name for it, see
            status_name(). None for the default.
        patch_set: the patch set the place counts on, None or 0 for the
            newest.

    Returns:
        The new thread's number.

    Raises:
        ChangeError: the status is unknown, or the place doesn't exist.
    """
    if status is None:
        status = RESOLVED if PATCHSET_LEVEL == place else UNRESOLVED
    status = status_name(status)

    def edit(change: Change) -> int:
        """Adds the thread.

        Args:
            change: the change, read fresh.

        Returns:
            The thread's number.
        """
        is_sent_now = is_sent or AUTHOR == author
        comment = Comment(number=max((other.number for other in change.comments), default=0) + 1,
                          place=place,
                          patch_set=patch_set or change.latest_patch_set(),
                          messages=[Message(author=author, text=text, sent=is_sent_now,
                                            unresolved=UNRESOLVED == status,
                                            time=int(time.time()) if is_sent_now else 0)],
                          side=side,
                          id=os.urandom(6).hex())
        check_place(change, comment)
        change.comments.append(comment)
        return comment.number

    return update_change(repository, change_id, edit)


def check_place(change: Change, comment: Comment) -> None:
    """Checks that a new thread's place exists. The commit message and the
    whole change are always there.

    Args:
        change: the thread's change.
        comment: the thread.

    Raises:
        ChangeError: the thread's file isn't in the version it counts in
            (see thread_version()), or its line is past the file's end.
    """
    path, line = split_place(comment.place)
    version = thread_version(change, comment)
    if path in (COMMIT_MSG, PATCHSET_LEVEL) or version is None:
        return
    commit, path = version
    where = f'PS{comment.patch_set}' + (' (old side)' if OLD_SIDE == comment.side else '')
    if 0 != subprocess.run([*change.repository.git_command(), 'cat-file', '-e', f'{commit}:{path}'],
                           capture_output=True).returncode:
        raise ChangeError(f'{where} has no file {path}')
    lines = file_lines(change.repository, commit, path)
    if line is not None and lines is not None and not 1 <= line <= len(lines):
        raise ChangeError(f'{path} has {len(lines)} lines in {where}, not line {line}')


def add_message(repository: Repository, change_id: str, number: int, author: str, text: str,
                is_sent: bool = True,
                status: str | None = None) -> None:
    """Adds a message to a thread. Like Gerrit's Reply, a reviewer has one
    draft per thread. A reviewer's acknowledgement resolves, see
    is_acknowledgement().

    Args:
        repository: the repository.
        change_id: the full Change-Id.
        number: the thread's number.
        author: who writes, REVIEWER or AUTHOR.
        text: the message's text.
        is_sent: whether the message is sent, else a reviewer's draft. An
            author's message is always sent.
        status: the status the message gives the thread, see
            status_name(). None leaves it as it is.

    Raises:
        ChangeError: the status is unknown, or the reviewer has a draft in
            the thread already.
    """
    unresolved = None if status is None else UNRESOLVED == status_name(status)
    if unresolved is None and REVIEWER == author and is_acknowledgement(text):
        unresolved = False

    def edit(change: Change) -> None:
        """Adds the message.

        Args:
            change: the change, read fresh.

        Raises:
            ChangeError: the reviewer has a draft in the thread already.
        """
        comment = comment_of(change, number)
        if AUTHOR != author and not is_sent and comment.has_draft():
            raise ChangeError(f'comment {number} has a draft already, edit that one')
        is_sent_now = is_sent or AUTHOR == author
        comment.messages.append(Message(author=author, text=text, sent=is_sent_now,
                                        time=int(time.time()) if is_sent_now else 0,
                                        unresolved=unresolved))

    update_change(repository, change_id, edit)


def edit_draft(repository: Repository, change_id: str, number: int, text: str) -> None:
    """Replaces the text of the reviewer's draft at the end of a thread. An
    acknowledgement resolves, see is_acknowledgement().

    Args:
        repository: the repository.
        change_id: the full Change-Id.
        number: the thread's number.
        text: the draft's new text.
    """
    def edit(change: Change) -> None:
        """Changes the draft.

        Args:
            change: the change, read fresh.

        Raises:
            ChangeError: the thread has no draft.
        """
        comment = comment_of(change, number)
        if not comment.has_draft():
            raise ChangeError(f'comment {number} has no draft')
        comment.messages[-1].text = text
        if comment.messages[-1].unresolved is None and is_acknowledgement(text):
            comment.messages[-1].unresolved = False

    update_change(repository, change_id, edit)


def delete_draft(repository: Repository, change_id: str, number: int) -> None:
    """Deletes the reviewer's draft at the end of a thread. A thread that
    was only a draft disappears completely. The other threads keep their
    numbers.

    Args:
        repository: the repository.
        change_id: the full Change-Id.
        number: the thread's number.
    """
    def edit(change: Change) -> None:
        """Deletes the draft.

        Args:
            change: the change, read fresh.

        Raises:
            ChangeError: the thread has no draft.
        """
        comment = comment_of(change, number)
        if not comment.has_draft():
            raise ChangeError(f'comment {number} has no draft')
        comment.messages.pop()
        if not comment.messages:
            change.comments.remove(comment)

    update_change(repository, change_id, edit)


def send_drafts(repository: Repository, change_id: str, message: str = '') -> int:
    """Sends all of the reviewer's drafts of a change, like Gerrit's Reply.

    Args:
        repository: the repository.
        change_id: the full Change-Id.
        message: a message on the whole change, resolved like a note. ''
            for none.

    Returns:
        The number of drafts sent.
    """
    def edit(change: Change) -> int:
        """Sends the drafts and adds the message.

        Args:
            change: the change, read fresh.

        Returns:
            The number of drafts sent.
        """
        now = int(time.time())
        drafts = [draft for comment in change.comments for draft in comment.messages
                  if not draft.sent]
        for draft in drafts:
            draft.sent = True
            draft.time = now
        if message:
            change.comments.append(Comment(
                number=max((other.number for other in change.comments), default=0) + 1,
                place=PATCHSET_LEVEL, patch_set=change.latest_patch_set(),
                messages=[Message(author=REVIEWER, text=message, unresolved=False, time=now)],
                id=os.urandom(6).hex()))
        return len(drafts)

    return update_change(repository, change_id, edit)


def set_status(repository: Repository, change_id: str, number: int, status: str,
               text: str = '') -> None:
    """Lets the author resolve a thread or make it unresolved again.

    Args:
        repository: the repository.
        change_id: the full Change-Id.
        number: the thread's number.
        status: the new status, see status_name().
        text: the message with it, '' for none.
    """
    add_message(repository, change_id, number, AUTHOR, text or '', status=status)


def toggle_draft_status(repository: Repository, change_id: str, number: int) -> str | None:
    """Flips whether sending a thread's draft resolves it, like the
    reviewer's Resolved checkbox. Without a draft, an empty one is made
    that flips the thread's status.

    Args:
        repository: the repository.
        change_id: the full Change-Id.
        number: the thread's number.

    Returns:
        The status the draft will give, see Comment.draft_status().
    """
    def edit(change: Change) -> str | None:
        """Flips the draft's flag.

        Args:
            change: the change, read fresh.

        Returns:
            The status the draft will give.
        """
        comment = comment_of(change, number)
        if not comment.has_draft():
            comment.messages.append(Message(author=REVIEWER, text='', sent=False,
                                            unresolved=not comment.is_unresolved()))
        else:
            draft = comment.messages[-1]
            is_unresolved_now = (comment.is_unresolved() if draft.unresolved is None
                                 else draft.unresolved)
            draft.unresolved = not is_unresolved_now
        return comment.draft_status()

    return update_change(repository, change_id, edit)
