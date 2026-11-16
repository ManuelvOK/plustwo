# SPDX-License-Identifier: Zlib
"""Printing changes and threads."""

import argparse

from plustwo.config.view_state import SECTION_TITLES, folded_sections
from plustwo.git.errors import ChangeError
from plustwo.git.repository import current_repository
from plustwo.model.vote import CODE_REVIEW, VERIFIED
from plustwo.paths import GUIDE
from plustwo.review.changes import all_changes, find_change, submitted_change
from plustwo.review.dashboard import dashboard
from plustwo.review.labels import comment_counts, message_text, vote_label


def print_overview(arguments: argparse.Namespace) -> None:
    """Prints the dashboard like Gerrit's: the sections with their changes,
    the repository as a column. Empty sections say so.

    Args:
        arguments: the command line, with list.
    """
    changes = all_changes(arguments.list)
    folded = folded_sections()
    for section, rows in dashboard(changes):
        is_folded = section in folded
        print(f'{SECTION_TITLES[section]} ({len(rows)})' + (' [folded]' if is_folded else ''))
        if is_folded:
            continue
        if not rows:
            print('     (none)')
        for number, change in rows:
            print(f'{number:>4}  {change.short_id()}  {change.repository_name:<12.12} '
                  f'{change.topic:<14.14} {"→ " + change.base:<12.12} {change.subject:<40.40}  '
                  f'{vote_label(change, CODE_REVIEW):>2} {vote_label(change, VERIFIED):>2}  '
                  f'{change.turn():<11}  {comment_counts(change)}')


def print_comments(arguments: argparse.Namespace) -> None:
    """Prints the votes and threads of a change, also of a submitted one.

    Args:
        arguments: the command line, with change.

    Raises:
        ChangeError: no change fits.
    """
    try:
        change = find_change(arguments.change)
    except ChangeError:
        if arguments.change.isdigit():
            raise
        change = submitted_change(arguments.change, current_repository())
    print(f'{change.short_id()} ({change.state()}): {change.subject}')
    if change.needs_verified:
        print('Needs Verified+1.')
    for vote in change.votes:
        older_note = '' if vote.patch_set == change.latest_patch_set() else ' (older patch set)'
        copied_note = f', copied from PS{vote.copied_from}' if vote.copied_from else ''
        print(f'{vote.label}{vote.value:+d} by {vote.by} on PS{vote.patch_set}'
              f'{copied_note}{older_note}')
        for line in filter(None, vote.message.split('\n')):
            print(f'  {line}')
    if not change.comments:
        print('No comments.')
    for comment in change.comments:
        print(f'\n#{comment.number} [{comment.status}] {comment.place_label()} '
              f'(PS{comment.patch_set})')
        for message in comment.messages:
            draft_note = ' (draft)' if not message.sent else ''
            print(f'  {message.author}{draft_note}: {message_text(message)}')


def guide_text() -> str:
    """Reads the guide for agents working as the author.

    Returns:
        The text of AGENTS.md next to the launcher (links resolved).

    Raises:
        ChangeError: the guide can't be read.
    """
    try:
        return GUIDE.read_text()
    except OSError as error:
        raise ChangeError(f'cannot read the guide: {error}') from error
