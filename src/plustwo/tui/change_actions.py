# SPDX-License-Identifier: Zlib
"""Actions on changes that both screens offer."""

import curses
from typing import TYPE_CHECKING

from plustwo.model.change import Change
from plustwo.model.vote import CODE_REVIEW
from plustwo.review.rebase import rebase_stack
from plustwo.review.reply import quick_approve
from plustwo.review.stacks import needs_rebase
from plustwo.review.submit import changes_submitted_with, submit
from plustwo.review.votes import require_verified, set_vote, vote_values
from plustwo.tui.choice import Choice
from plustwo.tui.colours import VOTE_COLOURS
from plustwo.tui.messages import ERROR, OK

if TYPE_CHECKING:
    from plustwo.tui.interface import Interface


def approve(interface: 'Interface', change: Change) -> None:
    """Votes Code-Review+2 on the latest patch set, after asking like
    quitting: the approve key again or y gives it. Then the drafts go
    along if the reviewer wants.

    Args:
        interface: the interface.
        change: the change.
    """
    key = interface.ask_key(f'Code-Review+2 on PS{change.latest_patch_set()} of '
                            f'{change.short_id()} {change.subject}? '
                            f'({interface.keymap.label("approve")} again or y)')
    if key not in interface.keymap.single_keys('approve') | {ord('y')}:
        interface.say('No vote given.')
        return
    n_drafts = change.n_drafts()
    do_send = bool(n_drafts) and ord('y') == interface.ask_key(
        f'{n_drafts} draft(s) unsent. Send them along? (y/N)')
    number, n_sent = quick_approve(change.change_id, do_send)
    interface.after_write()
    interface.say(f'Code-Review+2 on PS{number}'
                  + (f', {n_sent} draft(s) sent.' if n_sent else '.'), OK)


def rebase_with_confirmation(interface: 'Interface', change: Change) -> None:
    """Rebases the change's stack onto the base branch, after asking. On a
    conflict the author is asked to do it.

    Args:
        interface: the interface.
        change: a change of the stack.
    """
    if not needs_rebase(change.topic):
        interface.say(f'{change.topic} is on {change.base} already.')
        return
    if not interface.confirm([f'Rebase the stack {change.topic} onto {change.base}.',
                              'Every change gets a new patch set.'], 'Rebase?'):
        return
    rebased = rebase_stack(change.topic)
    interface.after_write()
    interface.say(f'Rebased, {len(rebased)} new patch set(s).' if rebased is not None
                  else 'Conflicts: the author is asked to rebase.',
                  OK if rebased is not None else ERROR)


def vote_attribute(value: int) -> int:
    """Finds the look of a vote's value.

    Args:
        value: the vote.

    Returns:
        The curses attribute, see VOTE_COLOURS, bold for the highest and
        lowest votes.
    """
    return curses.color_pair(VOTE_COLOURS.get(value, 0)) | (curses.A_BOLD if abs(value) > 1
                                                            else 0)


def vote_action(interface: 'Interface', change: Change, action: str) -> None:
    """Runs a vote action.

    Args:
        interface: the interface.
        change: the change.
        action: vote votes Code-Review, verified switches whether
            Verified+1 is needed.
    """
    if 'vote' == action:
        if not change.patch_sets:
            interface.say(f'{change.short_id()} has no patch set yet, nothing to vote on.', ERROR)
            return
        value = interface.choose(f'Code-Review on PS{change.latest_patch_set()}:',
                                 Choice(vote_values(), change.own_vote()))
        if value is None or value == change.own_vote():
            return
        number = set_vote(change.change_id, CODE_REVIEW, value)
        interface.after_write()
        interface.say(f'Code-Review{value:+d} on PS{number}.' if value
                      else 'Code-Review vote removed.')
    elif 'verified' == action:
        require_verified(change.change_id, not change.needs_verified)
        interface.after_write()
        interface.say('Needs Verified+1 now.' if not change.needs_verified
                      else 'Verified+1 is not needed any more.')


def submit_with_confirmation(interface: 'Interface', change: Change) -> None:
    """Submits a change after asking. Lists the changes that get submitted
    along and the unresolved threads. Says first what is missing, if a
    change lacks votes or an upload.

    Args:
        interface: the interface.
        change: the change.
    """
    submitted = changes_submitted_with(change)
    problems = [problem for other in submitted for problem in other.submit_problems()]
    if problems:
        interface.show_lines(['Cannot submit yet:', '']
                             + [f'  {problem}' for problem in problems]
                             + ['', f'{interface.keymap.label("approve")} gives Code-Review+2. '
                                'Verified comes from a check.'])
        return
    # What goes, what is left open
    lines = [f'Submit to {change.base}:']
    lines += [f'  {other.short_id()}  {other.subject}' for other in submitted]
    unfinished = [(other, comment) for other in submitted
                  for comment in other.unfinished_comments()]
    lines.append('')
    if unfinished:
        lines.append('These comments are unresolved:')
        lines += [f'  {other.short_id()} #{comment.number} [{comment.status}] '
                  f'{comment.place_label()}: '
                  f'{(comment.messages[0].text.splitlines() or [""])[0]}'
                  for other, comment in unfinished]
    else:
        lines.append('All comments are resolved.')
    n_drafts = sum(comment.has_draft() for other in submitted for comment in other.comments)
    if n_drafts:
        lines += ['', f'{n_drafts} draft(s) are not sent and stay unsent.']
    if interface.confirm(lines, 'Submit?'):
        submit(change.change_id, do_force=True)
        interface.after_write()
        interface.say('Submitted: ' + ', '.join(other.short_id() for other in submitted), OK)
