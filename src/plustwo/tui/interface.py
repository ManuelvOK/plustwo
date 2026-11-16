# SPDX-License-Identifier: Zlib
"""The interface: screens, the message line, dialogs and the loop."""

import curses
import os
import subprocess
import tempfile

from plustwo.tui import colours as tui_colours
from plustwo.config.registry import review_repositories
from plustwo.config.settings import setting
from plustwo.config.view_state import follows_latest
from plustwo.diff.edits import WHITESPACE_MODES
from plustwo.diff.search import SEARCH_MODES
from plustwo.git.errors import ChangeError
from plustwo.review.stacks import stack_version
from plustwo.tui.area import Area
from plustwo.tui.colour_pairs import ColourPairs
from plustwo.tui.colours import SECONDARY, curses_colour
from plustwo.tui.diff_gap import DiffGap
from plustwo.tui.keymap import keymap_from_settings
from plustwo.tui.keys import META
from plustwo.tui.messages import ERROR, OK, PENDING
from plustwo.tui.mouse import CLICK, DOUBLE_CLICK, WHEEL_DOWN, WHEEL_UP
from plustwo.tui.sidebar import SIDEBAR_MODES
from plustwo.tui.stack_view import StackView
from plustwo.tui.terminal import (
    keys_as_keys, pager_environment, parse_colour, query_background, true_colour_terminal)
from plustwo.tui.text_box import TEXT_BOX_ROWS, TextBox, box_keys, text_box_height


# How often the screen checks for new commits or notes, in milliseconds
RELOAD_INTERVAL_MS = 700


# How long curses waits after Esc for more bytes of a key sequence. The
# default of one second makes Esc feel stuck.
ESC_DELAY_MS = 25


def run(list_name=None):
    """Starts the interface. list_name limits
    the dashboard to a list of repositories.
    """
    tui_colours.TERMINAL_BACKGROUND = (parse_colour(setting('tui.background', '')) or query_background()
                           or (0, 0, 0))
    wanted = true_colour_terminal(setting('tui.colours', 'auto'))

    def start(screen):
        tui_colours.TRUE_COLOUR = wanted and curses.COLORS >= 1 << 24
        # Enter then arrives as \r and Ctrl+J as \n, so the comment box
        # tells them apart.
        curses.nonl()
        keys_as_keys()
        Interface(screen, list_name).loop()

    curses.wrapper(start)


class Interface:
    """The running interface: screen stack, drawing and keys."""

    def __init__(self, screen, list_name=None):
        self._screen = screen
        self.list_name = list_name
        self._views = [StackView(self)]
        self._message = ''
        self._version = None
        self.keymap = keymap_from_settings()
        if self.keymap.errors:
            self._message = 'Settings: ' + '; '.join(self.keymap.errors)
        curses.curs_set(0)
        curses.set_escdelay(ESC_DELAY_MS)
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
        curses.mouseinterval(300)
        curses.use_default_colors()
        for number in range(1, 9):
            curses.init_pair(number, curses_colour(number if number < 8 else 8), -1)
        screen.timeout(RELOAD_INTERVAL_MS)
        self.colour_pairs = ColourPairs()
        dim_colour = setting('tui.dim-colour', str(tui_colours.DIM_COLOUR))
        if dim_colour.isdigit() and int(dim_colour) < 256:
            tui_colours.DIM_COLOUR = int(dim_colour)
        # Index into CONTEXT_STEPS, kept for all changes
        self.context_step = 0
        # Gerrit's whitespace mode of the diff, one of WHITESPACE_MODES
        self.whitespace = setting('diff.whitespace', 'none')
        # Whether a right side on the latest patch set moves to a new upload,
        # state that the follow-latest key toggles
        self.follow_latest = follows_latest()
        if self.whitespace not in WHITESPACE_MODES:
            self.whitespace = 'none'
        # The text searched last in the diff, whether its matches are
        # marked, the texts searched before (the latest last) and where it
        # looks, one of SEARCH_MODES
        self.search = ''
        self.marking = False
        self.search_history = []
        self.search_mode = setting('tui.search', 'shown')
        if self.search_mode not in SEARCH_MODES:
            self.search_mode = 'shown'
        # The left column of the change screen: auto, shown or hidden. The
        # sidebar key sets it for the rest of the session.
        self.sidebar = setting('tui.sidebar', 'auto')
        if self.sidebar not in SIDEBAR_MODES:
            self.sidebar = 'auto'

    # ── state shared by the views ─────────────────────────────────────────

    def say(self, message, kind=None):
        """Shows a message in the bottom line until the next key. kind is
        OK (green), ERROR (red) or None, neutral information.
        """
        self._message = message
        self._message_kind = kind

    def open(self, view):
        """Shows a view on top of the current one."""
        self._views.append(view)
        view.reload()

    def back(self):
        """Goes back to the previous view. Returns False at the first one."""
        if len(self._views) > 1:
            note = getattr(self._views.pop(), 'leaving_note', lambda: '')()
            self._views[-1].reload()
            if note:
                self.say(note)
            return True
        return False

    # ── main loop ─────────────────────────────────────────────────────────

    def loop(self):
        """Draws, waits for a key or the reload interval, repeats. Keys
        typed while one was handled, like a held j, are all handled before
        the next draw: over a slow connection the screen skips steps
        instead of lagging behind.
        """
        self._views[-1].reload()
        self._version = self.version()
        while True:
            self._draw()
            key = self._screen.getch()
            if -1 == key:
                self._reload_if_changed()
                continue
            while -1 != key:
                self._handle_key(key)
                if not self._views:
                    return
                key = self._typed_ahead()

    def _typed_ahead(self):
        """The next key if one is waiting already, else -1."""
        self._screen.timeout(0)
        try:
            return self._screen.getch()
        finally:
            self._screen.timeout(RELOAD_INTERVAL_MS)

    def _handle_key(self, key):
        """Does what a key from getch() asks for."""
        self._message = ''
        if curses.KEY_MOUSE == key:
            self.keymap.reset()
            self._handle_mouse()
            return
        result = self.keymap.feed(self._with_meta(key))
        if PENDING == result:
            return
        action, count = result
        if action is None:
            return
        if not self._handle_global(action):
            try:
                self._views[-1].action(action, count)
            except ChangeError as error:
                self.say(str(error), ERROR)

    def _with_meta(self, key):
        """Alt plus a key arrives as Esc and the key: then the key plus
        META. A lone Esc stays Esc.
        """
        if 27 != key:
            return key
        self._screen.timeout(ESC_DELAY_MS)
        try:
            following = self._screen.getch()
        finally:
            self._screen.timeout(RELOAD_INTERVAL_MS)
        return key if following in (-1, 27) else META | following

    def _handle_global(self, action):
        """Handles actions that work everywhere. Returns whether it did."""
        if 'quit-now' == action:
            self._views.clear()
            return True
        if 'quit' == action:
            # Quitting is easy by mistake, e.g. q instead of a tab
            # q twice quits too, like qq
            if self.ask_key('Quit plustwo? (y or q quits)') in (ord('y'), ord('q')):
                self._views.clear()
            return True
        if 'help' == action:
            self._show_help()
            return True
        if 'reload' == action:
            self._views[-1].reload()
            return True
        if 'back' == action:
            if getattr(self._views[-1], 'go_left', lambda: False)():
                return True
            # On the first screen, back does nothing: a stray Esc, e.g. from
            # a key sequence curses didn't understand, must not quit.
            self.back()
            return True
        return False

    def _handle_mouse(self):
        """Passes a click, double click or wheel turn to the current view."""
        try:
            _id, column, row, _z, buttons = curses.getmouse()
        except curses.error:
            return
        if buttons & curses.BUTTON1_DOUBLE_CLICKED:
            kind = DOUBLE_CLICK
        elif buttons & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED):
            kind = CLICK
        elif buttons & curses.BUTTON4_PRESSED:
            kind = WHEEL_UP
        elif buttons & getattr(curses, 'BUTTON5_PRESSED', 0):
            kind = WHEEL_DOWN
        else:
            return
        try:
            self._views[-1].mouse(row, column, kind)
        except ChangeError as error:
            self.say(str(error), ERROR)

    def _reload_if_changed(self):
        """Reloads the current view when commits or notes changed."""
        version = self.version()
        if version != self._version:
            self._version = version
            self._views[-1].reload()

    def version(self):
        """The refs of every shown repository, see stack_version()."""
        return '\n'.join(stack_version(repository)
                         for repository in review_repositories(self.list_name))

    def after_write(self):
        """Reloads after the interface itself wrote, without waiting."""
        self._version = self.version()
        self._views[-1].reload()

    # ── drawing ───────────────────────────────────────────────────────────

    def _draw(self, refresh=True):
        """Draws the current view, its keys and the message line."""
        self._screen.erase()
        height, width = self._screen.getmaxyx()
        view = self._views[-1]
        view.draw(Area(self._screen, 0, height - 2, width))
        # The keys in bold, what they do dimmed. The help key stays, the
        # other hints go from the end when the line is too narrow.
        entries = []
        if getattr(self, '_box_footer', None):
            # A text box is open: its keys
            entries = [((), keys, label) for keys, label in self._box_footer()]
        for names, label in () if entries else view.footer():
            keys = '/'.join(filter(None, (self.keymap.label(name) for name in names)))
            if keys:
                entries.append((names, keys, label))
        kept = [entry for entry in entries if ('help',) == entry[0]]
        others = [entry for entry in entries if ('help',) != entry[0]]
        while others and 1 + sum(len(keys) + len(label) + 4
                                 for _, keys, label in others + kept) - 3 >= width:
            others.pop()
        column = 1
        for _names, keys, label in others + kept:
            if column + len(keys) + len(label) + 1 >= width:
                continue
            self.text(height - 2, column, keys, curses.A_BOLD, width)
            self.text(height - 2, column + len(keys) + 1, label, self.dim(), width)
            column += len(keys) + len(label) + 4
        colour = {OK: curses.color_pair(2), ERROR: curses.color_pair(1)}.get(
            getattr(self, '_message_kind', None), 0)
        self.text(height - 1, 0, self._message, curses.A_BOLD | colour, width)
        # The typed prefix in the bottom right corner, like vim's showcmd
        prefix = self.keymap.typed()
        if prefix:
            self.text(height - 1, width - len(prefix) - 2, prefix, curses.A_BOLD)
        if refresh:
            self._screen.refresh()

    def dim(self, attributes=0, background=-1):
        """The attribute of secondary text, see secondary(). Nothing on a
        reversed row, whose text is plain there.
        """
        if attributes & curses.A_REVERSE:
            return 0
        return self.colour_pairs.attribute(SECONDARY, background)

    def text(self, row, column, text, attributes=0, width=None):
        """Writes text, cut at the screen's edge. Never raises for space."""
        height, screen_width = self._screen.getmaxyx()
        if not 0 <= row < height or column >= screen_width:
            return
        limit = (width or screen_width) - column
        try:
            self._screen.addnstr(row, column, text, max(0, min(limit, screen_width - column - 1)),
                                 attributes)
        except curses.error:
            pass

    def _show_help(self):
        """Shows every action with its keys. The moving keys scroll, any
        other key closes it.
        """
        height, width = self._screen.getmaxyx()
        lines = self.keymap.help_lines(width - 2)
        first = 0
        self._screen.timeout(-1)
        try:
            while True:
                self._screen.erase()
                shown = lines[first:first + height - 1]
                for row, line in enumerate(shown):
                    self.text(row, 1, line)
                more = len(lines) > height - 1
                self.text(height - 1, 1, (f'{self.keymap.label("down")}/'
                                          f'{self.keymap.label("up")} scroll, ' if more else '')
                          + 'any other key closes', self.dim())
                self._screen.refresh()
                result = self.keymap.feed(self._with_meta(self._screen.getch()))
                if PENDING == result:
                    continue
                step = {'down': 1, 'up': -1, 'page-down': height // 2,
                        'page-up': -(height // 2)}.get(result[0])
                if step is None:
                    return
                first = max(0, min(first + step, max(len(lines) - height + 1, 0)))
        finally:
            self._screen.timeout(RELOAD_INTERVAL_MS)

    # ── leaving the screen for other programs ─────────────────────────────

    def run_outside(self, command):
        """Runs a program on the real terminal, e.g. git diff with delta."""
        curses.endwin()
        subprocess.run(command, env=pager_environment())
        # A refresh after endwin switches back to the alternate screen and
        # curses' modes. reset_prog_mode alone would not switch the screen,
        # and the interface would be left on the terminal after quitting.
        self._screen.clearok(True)
        self._screen.refresh()

    def edit_text(self, initial=''):
        """Opens the reviewer's editor. Returns the text, '' if left empty."""
        editor = os.environ.get('VISUAL') or os.environ.get('EDITOR') or 'vi'
        with tempfile.NamedTemporaryFile('w+', suffix='.txt', delete=False) as file:
            file.write(initial)
            path = file.name
        try:
            self.run_outside([editor, path])
            with open(path, encoding='utf-8') as file:
                return file.read().strip()
        finally:
            os.unlink(path)

    def write_text(self, title, initial='', anchor=None, save='save', choice=None,
                   lines=()):
        """Lets the reviewer write a comment in a small box, so the diff
        stays visible. Returns the text, '' if left empty, None if
        cancelled.

        anchor is a DiffGap: the diff makes room for the box below a row,
        as high as the box grows, and moves the rows below down. Or anchor
        is (row, left, width): the box goes over the screen below that
        row, or above it when there is no room. Without one it sits above
        the bottom line, as wide as the screen.

        save is what Enter does, for the hints. choice is (label, Choice):
        a row above the text to pick a value, like the vote of Gerrit's
        Reply dialog. Tab, or Up on the first line, goes there. lines are
        rows of information above it.

        Enter saves, Alt-Enter starts a new line, Esc or C-g cancels (with
        text in the box only when pressed twice), ^E continues in the
        editor. With the emacs keys, C-e goes to the end of the line, and
        C-x C-e continues in the editor.
        """
        emacs = 'emacs' == self.keymap.preset
        box = TextBox(initial, emacs)
        # Whether the keys go to the choice's row instead of the text
        state = {'on_choice': False}

        def footer():
            keys = box_keys(emacs, save, box.discarding)
            if choice:
                keys.insert(1, ('Tab', 'message' if state['on_choice'] else 'vote'))
            return keys

        self._box_footer = footer
        curses.curs_set(1)
        self._screen.timeout(-1)
        try:
            while True:
                extra = list(lines)
                if choice:
                    extra.append((f'{choice[0]}: {choice[1].text()}', state['on_choice']))
                place = anchor
                if isinstance(anchor, DiffGap):
                    # The box's height depends on its width, which the gap
                    # gives: once to learn the width, once with the height.
                    place = anchor.place(TEXT_BOX_ROWS + 2 + len(extra))
                    place = anchor.place(text_box_height(box, place[2]) + len(extra))
                self._draw(refresh=False)
                self._draw_text_box(box, title, place, footer(), extra)
                self._screen.refresh()
                key = self._screen.get_wch()
                cancel = '\x07' == key
                if '\x1b' == key:
                    # Alt-Enter arrives as Esc and Enter, a lone Esc cancels.
                    self._screen.timeout(ESC_DELAY_MS)
                    try:
                        following = self._screen.get_wch()
                    except curses.error:
                        following = None
                    finally:
                        self._screen.timeout(-1)
                    if following is None:
                        cancel = True
                    elif '\x1b' == following:
                        # Two quick presses
                        box.cancel()
                        cancel = True
                    else:
                        box.keep()
                        if following in ('\n', '\r'):
                            box.insert('\n')
                        elif '[' == following:
                            # Shift+Enter, where the terminal tells it apart:
                            # CSI 13;2u or CSI 27;2;13~
                            sequence = self._read_sequence()
                            if sequence in ('13;2u', '27;2;13~'):
                                box.insert('\n')
                        continue
                if cancel:
                    if box.cancel():
                        return None
                    continue
                box.keep()
                if choice and ('\t' == key or (curses.KEY_UP == key and not state['on_choice']
                                               and '\n' not in box.text[:box.position])):
                    state['on_choice'] = not state['on_choice']
                    curses.curs_set(0 if state['on_choice'] else 1)
                    continue
                if state['on_choice'] and key not in ('\r', curses.KEY_ENTER):
                    if curses.KEY_DOWN == key:
                        state['on_choice'] = False
                        curses.curs_set(1)
                    elif not choice[1].key(key) and isinstance(key, str) and key.isprintable():
                        # Typing goes back to the message
                        state['on_choice'] = False
                        curses.curs_set(1)
                        box.key(key)
                    continue
                if '\n' == key:
                    # Ctrl+J: Enter is \r, see curses.nonl() in run()
                    box.insert('\n')
                    continue
                if key in ('\r', curses.KEY_ENTER):
                    before = box.text[:box.position]
                    if before.endswith('\\\\'):
                        # \\ at the end keeps one backslash and saves
                        box.text = before[:-1] + box.text[box.position:]
                        return box.text.strip()
                    if before.endswith('\\'):
                        # \ at the end, like in a shell: a new line
                        box.text = before[:-1] + box.text[box.position:]
                        box.position -= 1
                        box.insert('\n')
                        continue
                    return box.text.strip()
                if emacs and '\x18' == key:
                    # C-x C-e continues in the editor, like in a shell
                    if '\x05' == self._screen.get_wch():
                        return self.edit_text(box.text)
                    continue
                if '\x05' == key and not emacs:
                    return self.edit_text(box.text)
                box.key(key)
        finally:
            self._box_footer = None
            if isinstance(anchor, DiffGap):
                anchor.close()
            curses.curs_set(0)
            self._screen.timeout(RELOAD_INTERVAL_MS)

    def _read_sequence(self):
        """The rest of an escape sequence after Esc [, up to its final
        character.
        """
        sequence = ''
        self._screen.timeout(ESC_DELAY_MS)
        try:
            while len(sequence) < 16:
                character = self._screen.get_wch()
                if not isinstance(character, str):
                    break
                sequence += character
                if character.isalpha() or '~' == character:
                    break
        except curses.error:
            pass
        finally:
            self._screen.timeout(-1)
        return sequence

    def _draw_text_box(self, box, title, anchor, keys=(), extra=()):
        """Draws the box of write_text() and puts the terminal cursor in it.
        keys go into the frame's bottom, extra are (text, focused) rows
        above the text.
        """
        height, width = self._screen.getmaxyx()
        row, left, box_width = anchor or (height - 2, 0, width - 1)
        box_width = max(box_width, 20)
        rows = box.rows(box_width - 2)
        inside = min(len(rows), TEXT_BOX_ROWS)
        cursor_row, cursor_column = box.cursor_position(box_width - 2)
        first = max(0, min(cursor_row - inside + 1, len(rows) - inside))
        above = len(extra)
        top = row + 1
        if not anchor or top + inside + above + 2 > height - 1:
            top = max(row - inside - above - 2, 0)
        hint = ' ' + '  '.join(f'{key} {label}' for key, label in keys) + ' '
        bottom = top + above + inside + 1
        self.text(top, left, '┌' + '─' * (box_width - 2) + '┐', curses.A_BOLD)
        self.text(top, left + 2, f' {title} ', curses.A_BOLD)
        self.text(bottom, left, '└' + '─' * (box_width - 2) + '┘', curses.A_BOLD)
        if len(hint) + 4 < box_width:
            discarding = box.discarding
            self.text(bottom, left + box_width - len(hint) - 2, hint,
                      curses.A_BOLD if discarding else self.dim())
        for offset, (line, focused) in enumerate(extra):
            self.text(top + 1 + offset, left, '│', curses.A_BOLD)
            self.text(top + 1 + offset, left + 1, f'{line:<{box_width - 2}}'[:box_width - 2],
                      curses.A_REVERSE if focused else self.dim())
            self.text(top + 1 + offset, left + box_width - 1, '│', curses.A_BOLD)
        for offset in range(inside):
            line = rows[first + offset] if first + offset < len(rows) else ''
            self.text(top + 1 + above + offset, left, '│', curses.A_BOLD)
            self.text(top + 1 + above + offset, left + 1, f'{line:<{box_width - 2}}')
            self.text(top + 1 + above + offset, left + box_width - 1, '│', curses.A_BOLD)
        try:
            self._screen.move(top + 1 + above + cursor_row - first, left + 1 + cursor_column)
        except curses.error:
            pass

    def ask(self, question, history=(), on_tab=None):
        """Asks for one line in the bottom line. Returns None on Esc. Up and
        Down go through history, the earlier answers, the latest last. Tab
        calls on_tab, which returns the new question.
        """
        height, width = self._screen.getmaxyx()
        answer = ''
        # Where in the history Up and Down are, len(history) is the answer typed
        position = len(history)
        typed = ''
        curses.curs_set(1)
        self._screen.timeout(-1)
        try:
            while True:
                self._screen.move(height - 1, 0)
                self._screen.clrtoeol()
                self.text(height - 1, 0, f'{question} {answer}', curses.A_BOLD, width)
                key = self._screen.get_wch()
                if key in ('\n', '\r'):
                    return answer.strip()
                if key in ('\x1b', '\x07'):
                    return None
                if key in (curses.KEY_UP, curses.KEY_DOWN) and history:
                    if len(history) == position:
                        typed = answer
                    step = -1 if curses.KEY_UP == key else 1
                    position = max(0, min(position + step, len(history)))
                    answer = history[position] if position < len(history) else typed
                elif '\t' == key and on_tab:
                    question = on_tab()
                elif key in ('\x7f', '\b', curses.KEY_BACKSPACE):
                    answer = answer[:-1]
                elif isinstance(key, str) and key.isprintable():
                    answer += key
        finally:
            curses.curs_set(0)
            self._screen.timeout(RELOAD_INTERVAL_MS)

    def choose(self, question, choice):
        """Picks a value of a Choice in the bottom line: Left, Right or a
        digit, Enter takes it. Returns the value, None on Esc or C-g.
        """
        height, width = self._screen.getmaxyx()
        self._screen.timeout(-1)
        try:
            while True:
                self._draw(refresh=False)
                self._screen.move(height - 1, 0)
                self._screen.clrtoeol()
                line = f'{question} {choice.text()}   ←/→ or a digit, Enter takes it'
                self.text(height - 1, 0, line, curses.A_BOLD, width)
                self._screen.refresh()
                key = self._screen.get_wch()
                if key in ('\r', '\n', curses.KEY_ENTER):
                    return choice.value
                if key in ('\x1b', '\x07', 'q'):
                    return None
                choice.key(key)
        finally:
            self._screen.timeout(RELOAD_INTERVAL_MS)

    def ask_key(self, question):
        """Shows a question in the bottom line over the screen. Returns the
        key pressed.
        """
        height, width = self._screen.getmaxyx()
        self._draw(refresh=False)
        self._screen.move(height - 1, 0)
        self._screen.clrtoeol()
        self.text(height - 1, 0, question, curses.A_BOLD, width)
        self._screen.refresh()
        self._screen.timeout(-1)
        try:
            return self._screen.getch()
        finally:
            self._screen.timeout(RELOAD_INTERVAL_MS)

    def dialog(self, lines, footer):
        """Shows lines in a box in the middle of the screen, over the
        current view, and waits for a key. Returns the key.
        """
        height, width = self._screen.getmaxyx()
        inner = min(max([len(line) for line in lines] + [len(footer), 30]) + 2, width - 4)
        shown = lines[:max(height - 6, 1)]
        top = max((height - len(shown) - 4) // 2, 0)
        left = max((width - inner - 2) // 2, 0)
        self._draw(refresh=False)
        rows = shown + ['', footer]
        self.text(top, left, '┌' + '─' * inner + '┐', curses.A_BOLD)
        for offset, line in enumerate(rows, start=1):
            attributes = curses.A_BOLD if offset == len(rows) else 0
            self.text(top + offset, left, '│', curses.A_BOLD)
            self.text(top + offset, left + 1, f' {line:<{inner - 1}.{inner - 1}}', attributes)
            self.text(top + offset, left + inner + 1, '│', curses.A_BOLD)
        self.text(top + len(rows) + 1, left, '└' + '─' * inner + '┘', curses.A_BOLD)
        self._screen.refresh()
        self._screen.timeout(-1)
        try:
            return self._screen.getch()
        finally:
            self._screen.timeout(RELOAD_INTERVAL_MS)

    def show_lines(self, lines):
        """Shows lines in a box until a key is pressed."""
        self.dialog(lines, 'Press any key.')

    def confirm(self, lines, question):
        """Shows lines in a box and asks a yes/no question. Returns True
        for y.
        """
        return ord('y') == self.dialog(lines, f'{question} (y/N)')
