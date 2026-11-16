# SPDX-License-Identifier: Zlib
"""What the reviewer does with a thread: reply, edit a draft, resolve, and
send all drafts.
"""

from typing import TYPE_CHECKING

from plustwo.git.repository import Repository
from plustwo.model.change import Change
from plustwo.model.comment import ACK, DONE, RESOLVED, Comment
from plustwo.model.roles import REVIEWER
from plustwo.review import reply as review_reply
from plustwo.review import threads as review_threads
from plustwo.review.threads import add_message, delete_draft, toggle_draft_status
from plustwo.review.votes import vote_values
from plustwo.tui.choice import Choice
from plustwo.tui.messages import ERROR, OK

if TYPE_CHECKING:
    from plustwo.tui.interface import Interface
    from plustwo.tui.text_input import BoxAnchor

# The actions thread_action() handles
THREAD_ACTIONS = ('send', 'done', 'ack', 'resolve')


def reply_to_thread(repository: Repository, interface: 'Interface', change_id: str,
                    comment: Comment, anchor: BoxAnchor = None) -> None:
    """Writes a reply as a draft (r), in the comment box.

    Args:
        repository: the repository the change is in.
        interface: the interface.
        change_id: the change's full Change-Id.
        comment: the thread.
        anchor: where the box goes, see write_text().
    """
    if comment.has_draft():
        interface.say(f'You have a draft already. {interface.keymap.label("edit-draft")} '
                      'edits it.', ERROR)
        return
    text = interface.text_input.write_text(f'Reply to #{comment.number}', anchor=anchor)
    if text:
        add_message(repository, change_id, comment.number, REVIEWER, text, is_sent=False)
        interface.after_write()
        interface.say(f'Reply saved as a draft. {interface.keymap.label("send")} sends all '
                      'drafts.', OK)


def edit_draft(repository: Repository, interface: 'Interface', change_id: str, comment: Comment,
               anchor: BoxAnchor = None) -> None:
    """Edits a thread's draft (e). Saving it empty deletes it.

    Args:
        repository: the repository the change is in.
        interface: the interface.
        change_id: the change's full Change-Id.
        comment: the thread.
        anchor: where the box goes, see write_text().
    """
    if not comment.has_draft():
        interface.say('No draft to edit in this thread.', ERROR)
        return
    text = interface.text_input.write_text('Edit draft (empty deletes it)',
                                           comment.messages[-1].text, anchor)
    if text is None:
        return
    if text:
        review_threads.edit_draft(repository, change_id, comment.number, text)
    else:
        delete_draft(repository, change_id, comment.number)
    interface.after_write()


def thread_action(interface: 'Interface', change: Change, comment: Comment | None,
                  action: str) -> None:
    """Handles the actions on a thread or on all drafts.

    Args:
        interface: the interface.
        change: the change.
        comment: the selected thread, None while the files are selected.
        action: send, done, ack or resolve. Others do nothing.
    """
    if 'send' == action:
        # Like Gerrit's Reply dialog: the drafts, a message on the whole
        # change and the vote go in one step.
        drafts = change.n_drafts()
        choice: tuple[str, Choice] | None = None
        if change.patch_sets:
            choice = (f'Code-Review on PS{change.latest_patch_set()}',
                      Choice(vote_values(), change.own_vote()))
        message = interface.text_input.write_text(
            'Reply', save_label='send', choice=choice,
            lines=[(f'{drafts} draft{"" if 1 == drafts else "s"} to send. '
                    'A message on the whole change, optional:' if drafts
                    else 'No drafts. A message on the whole change, optional:', False)])
        if message is None:
            interface.say('Nothing sent.')
            return
        count, voted = review_reply.reply(change.repository, change.change_id, message,
                                          choice[1].value if choice else None)
        interface.after_write()
        done: list[str] = []
        if count:
            done.append(f'{count} draft{"" if 1 == count else "s"} sent'
                        + (' with a message' if message else ''))
        elif message:
            done.append('message sent')
        if voted:
            value = choice[1].value if choice else 0
            done.append(f'Code-Review{value:+d} on PS{voted}' if value else 'vote removed')
        text = ', '.join(done)
        interface.say(text[:1].upper() + text[1:] + '.' if done else 'Nothing to send.',
                      OK if done else None)
    elif comment is None:
        return
    elif action in ('done', 'ack'):
        if comment.has_draft():
            interface.say(f'#{comment.number} has a draft already. '
                          f'{interface.keymap.label("edit-draft")} edits it.')
            return
        text = DONE if 'done' == action else ACK
        add_message(change.repository, change.change_id, comment.number, REVIEWER, text,
                    is_sent=False, status=RESOLVED)
        interface.after_write()
        interface.say(f'"{text}" saved as a draft, it resolves #{comment.number}. '
                      f'{interface.keymap.label("send")} sends.', OK)
    elif 'resolve' == action:
        status = toggle_draft_status(change.repository, change.change_id, comment.number)
        interface.after_write()
        interface.say(f'Sending the draft makes #{comment.number} {status}. '
                      f'{interface.keymap.label("send")} sends.')
