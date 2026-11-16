# SPDX-License-Identifier: Zlib
"""Comment threads: writing, replying, drafts and sending."""

import os
import subprocess
import time

from plustwo.git.commit_message import commit_message_text
from plustwo.git.errors import ChangeError
from plustwo.git.files import file_lines, renames_of
from plustwo.git.repository import git, git_command
from plustwo.model.acknowledgement import is_acknowledgement
from plustwo.model.comment import OLD_STATUSES, RESOLVED, STATUSES, UNRESOLVED, Comment
from plustwo.model.message import Message
from plustwo.model.place import COMMIT_MSG, NEW_SIDE, OLD_SIDE, PATCHSET_LEVEL, split_place
from plustwo.model.roles import AUTHOR, REVIEWER
from plustwo.review.changes import comment_of, update_change
from plustwo.review.porting import thread_version


def line_at(commit, place, repository=None, side=NEW_SIDE):
    """The source line a place like src/main.cc:12 refers to, stripped.

    On the old side, the line is read from the commit's parent. Empty if
    the place has no line number or the file has no such line.
    """
    path, _, line = place.rpartition(':')
    if not path or not line.isdigit():
        return ''
    if COMMIT_MSG == path:
        if OLD_SIDE == side:
            return ''
        text = commit_message_text(commit, repository)
    elif OLD_SIDE == side:
        # A renamed file had its old path in the parent
        old_path = renames_of(commit, repository).get(path, path)
        text = git('show', f'{commit}^:{old_path}', check=False, repository=repository)
    else:
        text = git('show', f'{commit}:{path}', check=False, repository=repository)
    lines = text.split('\n')
    number = int(line)
    return lines[number - 1].strip() if 1 <= number <= len(lines) else ''


def status_name(name):
    """The status a name stands for: unresolved or resolved, or one of the
    older names open, agreed (unresolved), dropped, done (resolved).
    """
    status = name if name in STATUSES else OLD_STATUSES.get(name)
    if status is None:
        raise ChangeError(f'unknown status "{name}", use {UNRESOLVED} or {RESOLVED}')
    return status


def add_comment(change_id, place, text, sent=True, side=NEW_SIDE, author=REVIEWER,
                status=None, repository=None, patch_set=None):
    """Starts a thread on a patch set, the newest without one. Returns its
    number. Like in Gerrit it is unresolved unless status says otherwise,
    and a message on the whole change is resolved, like one sent with the
    drafts. The author's threads are always sent. comment_place() gives
    place, side and patch set for a line of a diff. Like Gerrit, refuses a
    file the version doesn't have and a line past its end.
    """
    if status is None:
        status = RESOLVED if PATCHSET_LEVEL == place else UNRESOLVED
    status = status_name(status)

    def edit(change):
        sent_now = sent or AUTHOR == author
        comment = Comment(number=max((other.number for other in change.comments), default=0) + 1,
                          place=place,
                          patch_set=patch_set or change.latest_patch_set(),
                          messages=[Message(author=author, text=text, sent=sent_now,
                                            unresolved=UNRESOLVED == status,
                                            time=int(time.time()) if sent_now else 0)],
                          side=side,
                          id=os.urandom(6).hex())
        check_place(change, comment, repository)
        change.comments.append(comment)
        return comment.number

    return update_change(change_id, edit, repository)


def check_place(change, comment, repository=None):
    """Raises ChangeError when a new thread's file isn't in the version it
    counts in, see thread_version(), or its line is past the file's end.
    The commit message and the whole change are always there.
    """
    path, line = split_place(comment.place)
    version = thread_version(change, comment, repository)
    if path in (COMMIT_MSG, PATCHSET_LEVEL) or version is None:
        return
    commit, path = version
    where = f'PS{comment.patch_set}' + (' (old side)' if OLD_SIDE == comment.side else '')
    if 0 != subprocess.run([*git_command(repository), 'cat-file', '-e', f'{commit}:{path}'],
                           capture_output=True).returncode:
        raise ChangeError(f'{where} has no file {path}')
    lines = file_lines(commit, path, repository)
    if line is not None and lines is not None and not 1 <= line <= len(lines):
        raise ChangeError(f'{path} has {len(lines)} lines in {where}, not line {line}')


def add_message(change_id, number, author, text, sent=True, status=None, repository=None):
    """Adds a message to a thread. An author's message is always sent.
    status resolves the thread or makes it unresolved again, None leaves
    it as it is. Like Gerrit's Reply, a reviewer has one draft per thread.
    """
    unresolved = None if status is None else UNRESOLVED == status_name(status)
    if unresolved is None and REVIEWER == author and is_acknowledgement(text):
        unresolved = False

    def edit(change):
        comment = comment_of(change, number)
        if AUTHOR != author and not sent and comment.has_draft():
            raise ChangeError(f'comment {number} has a draft already, edit that one')
        sent_now = sent or AUTHOR == author
        comment.messages.append(Message(author=author, text=text, sent=sent_now,
                                        time=int(time.time()) if sent_now else 0,
                                        unresolved=unresolved))

    update_change(change_id, edit, repository)


def edit_draft(change_id, number, text, repository=None):
    """Replaces the text of the reviewer's draft at the end of a thread."""
    def edit(change):
        comment = comment_of(change, number)
        if not comment.has_draft():
            raise ChangeError(f'comment {number} has no draft')
        comment.messages[-1].text = text
        if comment.messages[-1].unresolved is None and is_acknowledgement(text):
            comment.messages[-1].unresolved = False

    update_change(change_id, edit, repository)


def delete_draft(change_id, number, repository=None):
    """Deletes the reviewer's draft at the end of a thread.

    A thread that was only a draft disappears completely. The other
    threads keep their numbers.
    """
    def edit(change):
        comment = comment_of(change, number)
        if not comment.has_draft():
            raise ChangeError(f'comment {number} has no draft')
        comment.messages.pop()
        if not comment.messages:
            change.comments.remove(comment)

    update_change(change_id, edit, repository)


def send_drafts(change_id, message='', repository=None):
    """Sends all of the reviewer's drafts of a change, like Gerrit's Reply.
    message, if given, becomes a comment on the whole change, resolved like
    a note. Returns the number of drafts sent.
    """
    def edit(change):
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

    return update_change(change_id, edit, repository)


def set_status(change_id, number, status, text='', repository=None):
    """The author resolves a thread or makes it unresolved again, with a
    message (text may be empty).
    """
    add_message(change_id, number, AUTHOR, text or '', status=status, repository=repository)


def toggle_draft_status(change_id, number, repository=None):
    """The reviewer's Resolved checkbox: flips whether sending the thread's
    draft resolves it. Without a draft, an empty one is made that flips
    the thread's status. Returns the status the draft will give.
    """
    def edit(change):
        comment = comment_of(change, number)
        if not comment.has_draft():
            comment.messages.append(Message(author=REVIEWER, text='', sent=False,
                                            unresolved=not comment.is_unresolved()))
        else:
            draft = comment.messages[-1]
            now = comment.is_unresolved() if draft.unresolved is None else draft.unresolved
            draft.unresolved = not now
        return comment.draft_status()

    return update_change(change_id, edit, repository)
