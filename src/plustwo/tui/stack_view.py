# SPDX-License-Identifier: Zlib
"""The dashboard: the changes of all repositories."""

import curses

from plustwo.config.user import current_user
from plustwo.config.view_state import SECTION_TITLES, folded_sections, move_section, toggle_section
from plustwo.git.repository import use_repository
from plustwo.model.change import SUBMITTABLE
from plustwo.model.vote import CODE_REVIEW, VERIFIED
from plustwo.review.changes import all_changes
from plustwo.review.compare import diff_command
from plustwo.review.dashboard import dashboard
from plustwo.review.labels import comment_counts, version_label
from plustwo.review.stacks import needs_rebase
from plustwo.tui.change_actions import (
    approve, rebase_with_confirmation, submit_with_confirmation, vote_action)
from plustwo.tui.change_view import ChangeView
from plustwo.tui.colours import HEADER_BACKGROUND, tone, vote_badge
from plustwo.tui.lists import LIST_ACTIONS, move, wheel_action
from plustwo.tui.mouse import DOUBLE_CLICK


class StackView:
    """The dashboard, like Gerrit's: sections like Your turn and Incoming,
    each a table of changes with the repository as a column. A change can
    be in several sections. Sections fold and move.
    """

    def footer(self):
        """The actions that work on the cursor row: fold and move on a
        heading, open, +2, submit or rebase on a change, as its state
        allows.
        """
        row = self._rows[self._selected] if self._selected < len(self._rows) else None
        if row and 'section' == row[0]:
            entries = [(('open',), 'fold'), (('section-down', 'section-up'), 'move')]
        else:
            entries = [(('open',), 'open')]
            change = self._selected_change()
            if change is not None:
                if not change.submit_problems():
                    entries.append((('submit',), 'submit'))
                elif not change.is_approved(CODE_REVIEW):
                    entries.append((('approve',), '+2'))
                if (change.repository, change.topic) in self._behind:
                    entries.append((('rebase',), 'rebase'))
        return entries + [(('next-turn',), 'my turn'), (('quit',), 'quit'),
                          (('help',), 'all keys')]

    # First screen row of the list
    TOP = 3

    def __init__(self, interface):
        self._interface = interface
        self._changes = []
        self._behind = set()
        # The sections of each change and the folded sections, see reload()
        self._sections = []
        self._folded = set()
        # ('section', name, count), ('change', index) or ('none',)
        self._rows = []
        self._selected = 0
        self._scroll = 0

    def _selectable(self):
        """Indexes of the rows the cursor can be on: headings and changes."""
        return [index for index, row in enumerate(self._rows) if 'none' != row[0]]

    def reload(self):
        """Reads the changes of all shown repositories and lays out the
        sections.
        """
        use_repository(None)
        selected = self._rows[self._selected] if self._selected < len(self._rows) else None
        self._changes = all_changes(self._interface.list_name)
        # Stacks the base branch moved away from, marked in the topic column
        self._behind = {(change.repository, change.topic) for change in self._changes
                        if change.rebase_is_requested or needs_rebase(change.topic,
                                                                         change.repository)}
        # What draw() needs, so drawing runs no git command
        self._folded = folded = folded_sections()
        user = current_user()
        self._sections = [change.sections(user) for change in self._changes]
        self._rows = []
        for section, entries in dashboard(self._changes, user):
            self._rows.append(('section', section, len(entries)))
            if section in folded:
                continue
            self._rows += [('change', number - 1) for number, _ in entries] or [('none',)]
        if selected in self._rows:
            self._selected = self._rows.index(selected)
        selectable = self._selectable()
        if self._selected not in selectable:
            self._selected = next((index for index in selectable if index >= self._selected),
                                  selectable[-1] if selectable else 0)

    def _selected_change(self):
        """The change on the cursor row, None on a heading."""
        row = self._rows[self._selected] if self._selected < len(self._rows) else None
        return self._changes[row[1]] if row and 'change' == row[0] else None

    def draw(self, area):
        """Draws the header bar and the sections, a row per change with the
        repository, badges for votes and the turn.
        """
        out = self._interface.text
        mine = sum('your-turn' in sections for sections in self._sections)
        bar = self._interface.colour_pairs.attribute(15, tone(HEADER_BACKGROUND))
        out(0, 0, ' ' * area.width, bar)
        title = 'plustwo' + (f'  list {self._interface.list_name}'
                             if self._interface.list_name else '')
        out(0, 1, title, bar | curses.A_BOLD)
        repositories = len({change.repository for change in self._changes})
        right = f'{len(self._changes)} in review in {repositories} repo(s)' + (
            f', your turn on {mine}' if mine else '') + ' '
        out(0, max(area.width - len(right), 1), right, bar)
        subject_width = max(area.width - 115, 16)
        out(2, 1, f'{"#":>3}  {"Change":<9} {"Repo":<12} {"Topic":<14} {"Base":<10} '
            f'{"Subject":<{subject_width}} {"PS":<5} {"CR":<7} {"V":<7} {"Turn":<10} Comments',
            self._interface.dim() | curses.A_UNDERLINE)
        height = area.height - self.TOP
        self._scroll = min(self._scroll, self._selected)
        if self._selected >= self._scroll + height:
            self._scroll = self._selected - height + 1
        folded = self._folded
        for offset, row_content in enumerate(self._rows[self._scroll:self._scroll + height]):
            index = self._scroll + offset
            row = self.TOP + offset
            selected = index == self._selected
            attributes = curses.A_REVERSE if selected else 0
            if 'section' == row_content[0]:
                _, section, count = row_content
                mark = '▸' if section in folded else '▾'
                out(row, 1, ' ' * (area.width - 2), attributes)
                out(row, 1, f'{mark} {SECTION_TITLES[section]} ({count})',
                    attributes | curses.A_BOLD)
                continue
            if 'none' == row_content[0]:
                out(row, 6, '(none)', self._interface.dim())
                continue
            change = self._changes[row_content[1]]
            # Plain text on the reversed cursor row
            secondary_text = self._interface.dim(attributes)
            dim = secondary_text if change.is_wip else 0
            out(row, 1, ' ' * (area.width - 2), attributes)
            out(row, 1, f'{row_content[1] + 1:>3}  ', attributes | dim)
            out(row, 6, f'{change.short_id():<9} ', attributes | secondary_text)
            out(row, 16, f'{change.repository_name:<12.12} ', attributes | dim)
            behind = (change.repository, change.topic) in self._behind
            out(row, 29, f'{("↻ " if behind else "") + change.topic:<14.14} ',
                attributes | (curses.color_pair(3) if behind else secondary_text))
            out(row, 44, f'{change.base:<10.10} ', attributes | secondary_text)
            out(row, 55, f'{change.subject:<{subject_width}.{subject_width}} ', attributes | dim)
            column = 56 + subject_width
            out(row, column, f'{version_label(change):<5} ', attributes | secondary_text)
            column += 6
            for label, short in ((CODE_REVIEW, 'CR'), (VERIFIED, 'V')):
                badge = vote_badge(self._interface, short, change.label_value(label),
                                   VERIFIED == label and change.needs_verified)
                if badge:
                    out(row, column, badge[0], badge[1])
                column += 8
            turn = change.turn()
            yours = 'your-turn' in self._sections[row_content[1]]
            # The same rule as the Your turn section: "you" there and here
            mine = ('▶ submit' if SUBMITTABLE == turn else '▶ you') if yours else None
            out(row, column, f'{mine or TURN_LABELS.get(turn, turn):<10} ',
                attributes | (curses.A_BOLD | curses.color_pair(3) if yours
                              else secondary_text))
            out(row, column + 11, comment_counts(change, is_short=True),
                attributes | secondary_text)

    def mouse(self, row, _column, kind):
        """A click selects a row, a double click opens it."""
        if wheel_action(kind):
            self.action(wheel_action(kind))
            return
        index = self._scroll + row - self.TOP
        if 0 <= index < len(self._rows) and 'none' != self._rows[index][0]:
            self._selected = index
            if DOUBLE_CLICK == kind or 'section' == self._rows[index][0]:
                self.action('open')

    def action(self, action, count=None):
        """Handles an action of the dashboard."""
        selectable = self._selectable()
        if action in LIST_ACTIONS and selectable:
            position = selectable.index(self._selected) if self._selected in selectable else 0
            self._selected = selectable[move(position, action, len(selectable), count)]
            return
        row = self._rows[self._selected] if self._selected < len(self._rows) else None
        if row and 'section' == row[0]:
            if 'open' == action:
                toggle_section(row[1])
            elif action in ('section-down', 'section-up'):
                move_section(row[1], 1 if 'section-down' == action else -1)
            else:
                return
            self.reload()
            self._selected = self._rows.index(next(entry for entry in self._rows
                                                   if entry[:2] == row[:2]))
            return
        if 'next-turn' == action:
            turns = [index for index in selectable if 'change' == self._rows[index][0]
                     and 'your-turn' in self._sections[self._rows[index][1]]]
            later = [index for index in turns if index > self._selected]
            if later or turns:
                self._selected = (later or turns)[0]
            else:
                self._interface.say('It is your turn nowhere.')
            return
        change = self._selected_change()
        if change is None:
            return
        use_repository(change.repository)
        if 'open' == action:
            self._interface.open(ChangeView(self._interface, change.change_id,
                                            change.repository))
        elif 'diff' == action:
            self._interface.run_outside(diff_command(change))
        elif 'submit' == action:
            submit_with_confirmation(self._interface, change)
        elif 'rebase' == action:
            rebase_with_confirmation(self._interface, change)
        elif 'approve' == action:
            approve(self._interface, change)
        else:
            vote_action(self._interface, change, action)


# The turn as the stack list shows it, and the turns that are the reviewer's
TURN_LABELS = {'reviewer': 'reviewer', 'author': 'author', 'submittable': 'submit',
               'wip': 'WIP'}


MY_TURNS = ('reviewer', 'submittable')
