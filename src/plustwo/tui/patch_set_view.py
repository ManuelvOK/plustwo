# SPDX-License-Identifier: Zlib
"""The patch set view: the history of a change."""

import curses
import time

from plustwo.model.place import place_label
from plustwo.model.roles import AUTHOR, REVIEWER
from plustwo.model.vote import CODE_REVIEW, VERIFIED
from plustwo.review.compare import versions_to_compare
from plustwo.review.history import patch_set_history
from plustwo.review.last_send import last_send
from plustwo.tui.colours import HEADER_BACKGROUND, REVIEWER_COLOURS, tone
from plustwo.tui.keys import key_sequences
from plustwo.tui.lists import LIST_ACTIONS, move, status_attribute, wheel_action
from plustwo.tui.mouse import DOUBLE_CLICK


class PatchSetView:
    """The patch sets of a change, like Gerrit's patch set list: when, by
    whom, their type, votes and comments. A row unfolds into what happened
    on it. Chooses the versions the change screen compares. The history
    comes from patch_set_history().
    """

    TOP = 3

    def __init__(self, interface, change_view):
        self._interface = interface
        self._view = change_view
        (first, _), (second, _) = versions_to_compare(change_view.change,
                                                           *change_view.compare)
        # The chosen sides: base or a patch set number, CURRENT on the right
        self._a, self._b = first, second
        self._expanded = set()
        self._history = []
        # Where the reviewer's newest send ends, see last_send(). The
        # patch set holding it is unfolded once, when the view opens.
        self._last_send = None
        self._opened = False
        # ('base',), ('patch set', entry), ('event', entry, event) or
        # ('last send', entry), the line below the newest send
        self._rows = []
        self._selected = 0
        self._scroll = 0

    def reload(self):
        """Reads the change again and lays out the rows."""
        self._view.reload()
        change = self._view.change
        if change is None:
            return
        shown = int(self._b) if self._b.isdigit() else None
        self._history = patch_set_history(change, shown)
        self._last_send = last_send(self._history)
        if not self._opened:
            self._opened = True
            if self._last_send:
                self._expanded.add(self._last_send[0])
        self._rows = [('base',)]
        for entry in self._history:
            self._rows.append(('patch set', entry))
            if entry.number in self._expanded:
                for index, event in enumerate(entry.events):
                    self._rows.append(('event', entry, event))
                    if (entry.number, index) == self._last_send:
                        self._rows.append(('last send', entry))
        if not self._selected and len(self._rows) > 1:
            # On the right side's patch set at first
            self._selected = next((index for index, row in enumerate(self._rows)
                                   if 'patch set' == row[0] and str(row[1].number) == self._b),
                                  len(self._rows) - 1)
        self._selected = min(self._selected, len(self._rows) - 1)

    def footer(self):
        """The keys of this view."""
        row = self._rows[self._selected] if self._rows else ('base',)
        entries = [(('down', 'up'), 'move')]
        if 'patch set' == row[0]:
            entries.append((('open',), 'fold' if row[1].number in self._expanded else 'unfold'))
        entries += [(('set-a',), 'pick left side'), (('set-b',), 'pick right side'),
                    (('compare-prompt',), 'type versions'),
                    (('back',), 'back'), (('help',), 'all keys')]
        return entries

    def draw(self, area):
        """The header, the column titles and a row per patch set."""
        out = self._interface.text
        change = self._view.change
        bar = self._interface.colour_pairs.attribute(15, tone(HEADER_BACKGROUND))
        out(0, 0, ' ' * area.width, bar)
        title = f'Patch sets {change.short_id()} {change.subject}'
        out(0, 1, title, bar | curses.A_BOLD)
        sides = f'A: {self._side_label(self._a)}  B: {self._side_label(self._b)} '
        out(0, max(area.width - len(sides), len(title) + 3), sides, bar | curses.A_BOLD)
        out(2, 1, f'{"A":<2}{"B":<2} {"PS":<6} {"Uploaded":<17} {"By":<12} {"Type":<15} '
            f'{"CR":<6} {"V":<6} Comments', self._interface.dim() | curses.A_UNDERLINE)
        height = area.height - self.TOP
        self._scroll = min(self._scroll, self._selected)
        if self._selected >= self._scroll + height:
            self._scroll = self._selected - height + 1
        for offset, content in enumerate(self._rows[self._scroll:self._scroll + height]):
            index = self._scroll + offset
            row = self.TOP + offset
            attributes = curses.A_REVERSE if index == self._selected else 0
            out(row, 1, ' ' * (area.width - 2), attributes)
            if 'event' == content[0]:
                self._draw_event(row, content[2], attributes, area.width)
                continue
            if 'last send' == content[0]:
                line = f'── your last send {"─" * max(area.width - 30, 0)}'
                out(row, 10, line, attributes | self._reviewer_colour(), area.width - 1)
                continue
            version = 'base' if 'base' == content[0] else str(content[1].number)
            marks = ('●' if version == self._a else ' ') + ' ' \
                + ('●' if version == self._b else ' ') + ' '
            out(row, 1, marks, attributes | curses.A_BOLD | curses.color_pair(3))
            if 'base' == content[0]:
                out(row, 6, 'base', attributes | self._interface.dim(attributes))
                continue
            entry = content[1]
            when = time.strftime('%Y-%m-%d %H:%M', time.localtime(entry.upload_time)) \
                if entry.upload_time else '-'
            fold = '▾' if entry.number in self._expanded else '▸'
            out(row, 6, f'{fold} PS{entry.number:<3} {when:<17} {entry.uploader:<12.12} '
                f'{entry.type:<15} {entry.vote_label(CODE_REVIEW):<6} '
                f'{entry.vote_label(VERIFIED):<6} ', attributes)
            # A folded patch set holding the newest send says so after its
            # counts, which get shorter first when the row is narrow.
            holds_send = (self._last_send and self._last_send[0] == entry.number
                          and entry.number not in self._expanded)
            marks = (' ◂ your last send', ' ◂ last send', ' ◂') if holds_send else ('',)
            labels = (entry.comments_label(), entry.comments_label(is_compact=True))
            room = area.width - 72
            comments, mark = next(((label, mark) for mark in marks for label in labels
                                   if len(label) + len(mark) <= room),
                                  (labels[1][:max(room - len(marks[-1]), 0)], marks[-1]))
            out(row, 71, comments, attributes | (
                status_attribute('unresolved') | curses.A_BOLD if entry.open_threads()
                else self._interface.dim(attributes)), area.width - 1)
            if mark:
                out(row, 71 + len(comments), mark, attributes | self._reviewer_colour(),
                    area.width - 1)

    def _reviewer_colour(self):
        """The reviewer's colour, the viewer's own, as in threads."""
        return self._interface.colour_pairs.attribute(tone(REVIEWER_COLOURS), -1)

    def _draw_event(self, row, event, attributes, width):
        """An unfolded row: a thread, a reply or a vote. The reviewer's own,
        the viewer's, get a bar in the reviewer's colour, as in threads.
        """
        if event.is_yours():
            self._interface.text(row, 8, '▎', attributes | self._reviewer_colour())
        when = time.strftime('%m-%d %H:%M', time.localtime(event.time)) if event.time else ''
        who = 'Reviewer' if REVIEWER == event.author else (
            'Author' if AUTHOR == event.author else event.author)
        if 'vote' == event.type:
            text = f'{when:<11} {who}: {event.text}'
            out_attributes = attributes | self._interface.dim(attributes)
        else:
            label = ('draft' if event.is_draft else event.status)
            what = 'reply on' if 'reply' == event.type else 'thread'
            text = (f'{when:<11} {what} #{event.number} [{label}] '
                    f'{place_label(event.place)}  {who}: '
                    f'{event.text}')
            out_attributes = attributes | (status_attribute('unresolved') | curses.A_BOLD
                                           if event.is_open() else
                                           status_attribute('draft') if event.is_draft else 0)
        self._interface.text(row, 10, text, out_attributes, width - 1)

    @staticmethod
    def _side_label(version):
        return version if version in ('base', 'current') else f'PS{version}'

    def mouse(self, row, _column, kind):
        """A click selects a row, a double click unfolds it."""
        if wheel_action(kind):
            self.action(wheel_action(kind))
            return
        index = self._scroll + row - self.TOP
        if 0 <= index < len(self._rows):
            self._selected = index
            if DOUBLE_CLICK == kind:
                self._open(enter=False)

    def action(self, action, count=None):
        """Handles an action of the patch set view."""
        if action in LIST_ACTIONS:
            self._selected = move(self._selected, action, len(self._rows), count)
            return
        row = self._rows[self._selected] if self._rows else ('base',)
        version = 'base' if 'base' == row[0] else str(row[1].number)
        if action in ('set-a', 'side-old'):
            self._a = version
        elif action in ('set-b', 'side-new'):
            if 'base' == version:
                self._interface.say('base can only be A, the left side.')
            else:
                self._b = version
                self.reload()
        elif 'open' == action:
            self._open(enter=self._interface.keymap.last in key_sequences('Enter'))
        elif action in ('compare-prompt', 'patch-sets'):
            self._interface.back()
            self._view.action('compare-prompt')

    def _open(self, enter):
        """l unfolds a patch set's row, Enter shows the diff of A and B, on
        a thread at that thread with its patch set as B.
        """
        row = self._rows[self._selected] if self._rows else ('base',)
        if 'event' == row[0] and row[2].number is not None:
            entry, event = row[1], row[2]
            first = self._a if self._a.isdigit() and int(self._a) < entry.number else 'base'
            self._view.compare = (first, str(entry.number))
            self._interface.back()
            self._view.show_thread(event.number)
            return
        if enter:
            self._view.compare = (self._a, self._b)
            self._interface.back()
            return
        if 'patch set' == row[0]:
            self._expanded ^= {row[1].number}
            self.reload()
