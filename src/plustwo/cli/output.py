# SPDX-License-Identifier: Zlib
"""Printing changes and threads."""

from plustwo.config.view_state import SECTION_TITLES, folded_sections
from plustwo.git.errors import ChangeError
from plustwo.git.repository import current_repository
from plustwo.model.vote import CODE_REVIEW, VERIFIED
from plustwo.paths import GUIDE
from plustwo.review.changes import all_changes, find_change, submitted_change
from plustwo.review.dashboard import dashboard
from plustwo.review.labels import comment_counts, message_text, vote_label


def print_overview(arguments):
    """Prints the dashboard: the sections with their changes, the repo as a
    column, like Gerrit's. Empty sections say so.
    """
    changes = all_changes(arguments.list)
    folded = folded_sections()
    for section, rows in dashboard(changes):
        print(f'{SECTION_TITLES[section]} ({len(rows)})' + (' [folded]' if section in folded
                                                             else ''))
        if section in folded:
            continue
        if not rows:
            print('     (none)')
        for number, change in rows:
            print(f'{number:>4}  {change.short_id()}  {change.repository_name:<12.12} {change.topic:<14.14} '
                  f'{"→ " + change.base:<12.12} {change.subject:<40.40}  {vote_label(change, CODE_REVIEW):>2} '
                  f'{vote_label(change, VERIFIED):>2}  {change.turn():<11}  '
                  f'{comment_counts(change)}')


def print_comments(arguments):
    """Prints the threads of a change."""
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
        stale = '' if vote.patch_set == change.latest_patch_set() else ' (older patch set)'
        copied = f', copied from PS{vote.copied_from}' if vote.copied_from else ''
        print(f'{vote.label}{vote.value:+d} by {vote.by} on PS{vote.patch_set}{copied}{stale}')
        for line in filter(None, vote.message.split('\n')):
            print(f'  {line}')
    if not change.comments:
        print('No comments.')
    for comment in change.comments:
        print(f'\n#{comment.number} [{comment.status}] {comment.place_label()} '
              f'(PS{comment.patch_set})')
        for message in comment.messages:
            draft = ' (draft)' if not message.sent else ''
            print(f'  {message.author}{draft}: {message_text(message)}')


def guide_text():
    """The guide for agents working as the author, AGENTS.md next to the
    launcher (links resolved).
    """
    path = GUIDE
    try:
        return path.read_text()
    except OSError as error:
        raise ChangeError(f'cannot read the guide: {error}') from error
