"""Terminal user interface of git change, for the reviewer.

Started by `git change` without arguments. Three screens: the stacks in
review (one per review branch), one change with its files and comment
threads, and one thread.
Keys are shown at the bottom of every screen, `?` lists them all.

The change screen shows the diff as delta renders it (side by side). t hands
over to vimdiff. Comments are written in a small box over the diff, ^E
continues in the reviewer's editor. The screen reloads by itself when
commits or notes change, e.g. when the author answers.

The mouse works too: a click selects, a double click opens, the wheel
scrolls.
"""

import curses
import os
import re
import subprocess
import tempfile
import textwrap

# How often the screen checks for new commits or notes, in milliseconds
RELOAD_INTERVAL_MS = 700

# How long curses waits after Esc for more bytes of a key sequence. The
# default of one second makes Esc feel stuck.
ESC_DELAY_MS = 25

# Kinds of mouse events the views get
CLICK = 'click'
DOUBLE_CLICK = 'double click'
WHEEL_UP = 'wheel up'
WHEEL_DOWN = 'wheel down'

# The key views get for gg. curses keys are positive, so this one is free.
GO_TOP = -2

STATE_COLOURS = {'New': 0, 'In-Review': 3, 'Submitted': 2}
STATUS_COLOURS = {'open': 3, 'agreed': 6, 'done': 2, 'dropped': 8, 'draft': 5}

STACK_KEYS = 'j/k move  l open  d diff  S submit  R reload  ? help  q quit'
THREAD_KEYS = ('j/k scroll  r reply  e edit draft  s send  y agree  a apply  A apply all  '
               'x drop  o reopen  h back')

HELP_TEXT = """git change: keys

Everywhere
  j / k       move down / up. A count before moves that far: 15j
  gg / G      to the top / bottom. With a count to that row, in the diff
              to that line of the new version: 120G
  mouse       click selects, double click opens, the wheel scrolls
  l, Enter    open
  h, Esc      back (not on the first screen, q quits). In the diff, h
              goes back to the files or comments.
  R           reload now (it also reloads by itself)
  ?           this help
  q           quit

Stacks
  d           diff of the change
  S           submit: moves the base branch to the change, which submits
              the changes below it too. Asks first and lists them and
              their unfinished comments.

Change: files and comments on the left, the diff on the right
  Tab         next pane: files, comments, diff. A click focuses a pane.
  j / k       in the files: shows that file's diff. In the comments: also
              puts the diff on the thread's line. In the diff: moves the
              cursor line (^d / ^u half a page, the wheel scrolls).
  h           in the diff: back to the files or comments
  l           on a file: into the diff. In the comments: opens the thread
              screen. In the diff: folds or unfolds the thread at the
              cursor. Threads are shown under their line, unfinished ones
              and those with drafts unfolded.
  i           folds all threads, or unfolds all if all are folded
  r / e       on a thread in the diff or the comments: reply / edit your
              draft, in the box below the thread. c on a thread replies.
  c           in the diff: new comment on the cursor line. In the files:
              asks for the line. Write it in the box: Enter saves,
              Alt-Enter starts a new line, Esc cancels, ^E continues in
              your editor. The comment stays a draft until you send it.
  t           the file's diff in vimdiff
  p           choose the versions to compare: "1 3", "2 current" or
              "base 2" (the whole change as of PS2). Empty gives the
              default "base latest": the whole change, latest patch set.
  s           send all drafts of this change
  y           agree to the selected thread (or the one at the diff's
              cursor), apply later
  a           apply the selected thread now (agrees to it)
  A           apply all agreed threads of this change
  x / o       drop / reopen the selected thread
  S           submit

Thread
  r           reply, as a draft
  e           edit your draft. Saving it empty deletes it.
  s y a A x o as in the change screen

Press any key."""


def run(core):
    """Starts the interface. core is the git-change module."""
    curses.wrapper(lambda screen: Interface(screen, core).loop())


class Interface:
    """The running interface: screen stack, drawing and keys."""

    def __init__(self, screen, core):
        self._screen = screen
        self._core = core
        self._views = [StackView(self)]
        self._message = ''
        self._version = None
        # A count typed before a key, like vim's 15j, and a g waiting for
        # the second g of gg
        self._count = ''
        self._g_pending = False
        curses.curs_set(0)
        curses.set_escdelay(ESC_DELAY_MS)
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
        curses.mouseinterval(300)
        curses.use_default_colors()
        for number in range(1, 9):
            curses.init_pair(number, number if number < 8 else 8, -1)
        screen.timeout(RELOAD_INTERVAL_MS)
        self.colour_pairs = ColourPairs()

    # ── state shared by the views ─────────────────────────────────────────

    @property
    def core(self):
        """The git-change module with the review logic."""
        return self._core

    def say(self, message):
        """Shows a message in the bottom line until the next key."""
        self._message = message

    def open(self, view):
        """Shows a view on top of the current one."""
        self._views.append(view)
        view.reload()

    def back(self):
        """Goes back to the previous view. Returns False at the first one."""
        if len(self._views) > 1:
            self._views.pop()
            self._views[-1].reload()
            return True
        return False

    # ── main loop ─────────────────────────────────────────────────────────

    def loop(self):
        """Draws, waits for a key or the reload interval, repeats."""
        self._views[-1].reload()
        self._version = self._core.stack_version()
        while True:
            self._draw()
            key = self._screen.getch()
            if -1 == key:
                self._reload_if_changed()
                continue
            self._message = ''
            if curses.KEY_MOUSE == key:
                self._count, self._g_pending = '', False
                self._handle_mouse()
                continue
            key = self._take_prefix(key)
            if key is None:
                continue
            count = int(self._count) if self._count else None
            self._count = ''
            if not self._handle_global(key):
                try:
                    self._views[-1].key(key, count)
                except self._core.ChangeError as error:
                    self.say(str(error))
            if not self._views:
                return

    def _take_prefix(self, key):
        """Collects a count and the first g of gg. Returns the key to
        handle, GO_TOP for gg, or None while the prefix isn't complete.
        Esc cancels a prefix, like in vim.
        """
        if self._g_pending:
            self._g_pending = False
            if ord('g') == key:
                return GO_TOP
            self._count = ''
            return None
        if ord('1') <= key <= ord('9') or (ord('0') == key and self._count):
            self._count += chr(key)
            return None
        if ord('g') == key:
            self._g_pending = True
            return None
        if 27 == key and self._count:
            self._count = ''
            return None
        return key

    def _handle_global(self, key):
        """Handles keys that work everywhere. Returns whether it did."""
        if ord('q') == key:
            self._views.clear()
            return True
        if ord('?') == key:
            self._show_help()
            return True
        if ord('R') == key:
            self._views[-1].reload()
            return True
        if ord('h') == key and getattr(self._views[-1], 'go_left', lambda: False)():
            return True
        if key in (ord('h'), 27):
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
        except self._core.ChangeError as error:
            self.say(str(error))

    def _reload_if_changed(self):
        """Reloads the current view when commits or notes changed."""
        version = self._core.stack_version()
        if version != self._version:
            self._version = version
            self._views[-1].reload()

    def after_write(self):
        """Reloads after the interface itself wrote, without waiting."""
        self._version = self._core.stack_version()
        self._views[-1].reload()

    # ── drawing ───────────────────────────────────────────────────────────

    def _draw(self, refresh=True):
        """Draws the current view, its keys and the message line."""
        self._screen.erase()
        height, width = self._screen.getmaxyx()
        view = self._views[-1]
        # The keys wrap onto at most two lines above the message line.
        key_lines = textwrap.wrap(view.KEYS, max(width - 1, 20), break_on_hyphens=False)[:2]
        view.draw(Area(self._screen, 0, height - 1 - len(key_lines), width))
        for offset, line in enumerate(key_lines):
            self.text(height - 1 - len(key_lines) + offset, 0, line, curses.A_DIM, width)
        self.text(height - 1, 0, self._message, curses.A_BOLD, width)
        # The typed prefix in the bottom right corner, like vim's showcmd
        prefix = self._count + ('g' if self._g_pending else '')
        if prefix:
            self.text(height - 1, width - len(prefix) - 2, prefix, curses.A_BOLD)
        if refresh:
            self._screen.refresh()

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
        """Shows the key overview until a key is pressed."""
        self._screen.erase()
        for row, line in enumerate(HELP_TEXT.split('\n')):
            self.text(row, 1, line)
        self._screen.refresh()
        self._screen.timeout(-1)
        self._screen.getch()
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

    def write_text(self, title, initial='', anchor=None):
        """Lets the reviewer write a comment in a small box over the screen,
        so the diff stays visible. Returns the text, '' if left empty, None
        if cancelled.

        anchor is (row, left, width): the box goes below that screen row,
        or above it when there is no room. Without one it sits above the
        bottom line, as wide as the screen.

        Enter saves, Alt-Enter starts a new line, Esc cancels, ^E continues
        in the editor.
        """
        box = TextBox(initial)
        curses.curs_set(1)
        self._screen.timeout(-1)
        try:
            while True:
                self._draw(refresh=False)
                self._draw_text_box(box, title, anchor)
                self._screen.refresh()
                key = self._screen.get_wch()
                if key in ('\n', '\r', curses.KEY_ENTER):
                    return box.text.strip()
                if '\x1b' == key:
                    # Alt-Enter arrives as Esc and Enter, a lone Esc cancels.
                    self._screen.timeout(ESC_DELAY_MS)
                    try:
                        following = self._screen.get_wch()
                    except curses.error:
                        return None
                    finally:
                        self._screen.timeout(-1)
                    if following in ('\n', '\r'):
                        box.insert('\n')
                elif '\x05' == key:
                    return self.edit_text(box.text)
                else:
                    box.key(key)
        finally:
            curses.curs_set(0)
            self._screen.timeout(RELOAD_INTERVAL_MS)

    def _draw_text_box(self, box, title, anchor):
        """Draws the box of write_text() and puts the terminal cursor in it."""
        height, width = self._screen.getmaxyx()
        row, left, box_width = anchor or (height - 2, 0, width - 1)
        box_width = max(box_width, 20)
        rows = box.rows(box_width - 2)
        inside = min(len(rows), TEXT_BOX_ROWS)
        cursor_row, cursor_column = box.cursor_position(box_width - 2)
        first = max(0, min(cursor_row - inside + 1, len(rows) - inside))
        top = row + 1
        if not anchor or top + inside + 2 > height - 1:
            top = max(row - inside - 2, 0)
        hint = ' Enter save  Alt-Enter new line  Esc cancel  ^E editor '
        self.text(top, left, '┌' + '─' * (box_width - 2) + '┐', curses.A_BOLD)
        self.text(top, left + 2, f' {title} ', curses.A_BOLD)
        self.text(top + inside + 1, left, '└' + '─' * (box_width - 2) + '┘', curses.A_BOLD)
        if len(hint) + 4 < box_width:
            self.text(top + inside + 1, left + box_width - len(hint) - 2, hint, curses.A_DIM)
        for offset in range(inside):
            line = rows[first + offset] if first + offset < len(rows) else ''
            self.text(top + 1 + offset, left, '│', curses.A_BOLD)
            self.text(top + 1 + offset, left + 1, f'{line:<{box_width - 2}}')
            self.text(top + 1 + offset, left + box_width - 1, '│', curses.A_BOLD)
        try:
            self._screen.move(top + 1 + cursor_row - first, left + 1 + cursor_column)
        except curses.error:
            pass

    def ask(self, question):
        """Asks for one line in the bottom line. Returns None on Esc."""
        height, width = self._screen.getmaxyx()
        answer = ''
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
                if '\x1b' == key:
                    return None
                if key in ('\x7f', '\b', curses.KEY_BACKSPACE):
                    answer = answer[:-1]
                elif isinstance(key, str) and key.isprintable():
                    answer += key
        finally:
            curses.curs_set(0)
            self._screen.timeout(RELOAD_INTERVAL_MS)

    def confirm(self, lines, question):
        """Shows lines and asks a yes/no question. Returns True for y."""
        self._screen.erase()
        for row, line in enumerate(lines):
            self.text(row + 1, 2, line)
        self.text(len(lines) + 2, 2, f'{question} (y/N)', curses.A_BOLD)
        self._screen.refresh()
        self._screen.timeout(-1)
        key = self._screen.getch()
        self._screen.timeout(RELOAD_INTERVAL_MS)
        return ord('y') == key


def pager_environment():
    """Environment for programs the interface hands over to.

    git starts less with LESS=FRX, and delta with --quit-if-one-screen. Both
    make the pager quit at once when the text fits on one screen, so a short
    diff would only flash. Here the pager stays until q is pressed.
    """
    environment = dict(os.environ)
    environment['LESS'] = '-R'
    environment['DELTA_PAGER'] = 'less -R'
    return environment


# Most text rows the comment box shows, it scrolls beyond
TEXT_BOX_ROWS = 6


class TextBox:
    """The text and cursor of the comment box, independent of the screen.

    Lines wrap at the box's width, by character, so every position of the
    text has one place on the screen.
    """

    def __init__(self, text=''):
        self.text = text
        self.position = len(text)

    def insert(self, characters):
        """Inserts at the cursor."""
        self.text = self.text[:self.position] + characters + self.text[self.position:]
        self.position += len(characters)

    def key(self, key):
        """Handles an editing key from get_wch()."""
        before, after = self.text[:self.position], self.text[self.position:]
        if key in ('\x7f', '\b', curses.KEY_BACKSPACE):
            if before:
                self.text, self.position = before[:-1] + after, self.position - 1
        elif curses.KEY_DC == key:
            self.text = before + after[1:]
        elif curses.KEY_LEFT == key:
            self.position = max(self.position - 1, 0)
        elif curses.KEY_RIGHT == key:
            self.position = min(self.position + 1, len(self.text))
        elif key in (curses.KEY_HOME, '\x01'):
            self.position = before.rfind('\n') + 1
        elif curses.KEY_END == key:
            end = self.text.find('\n', self.position)
            self.position = len(self.text) if -1 == end else end
        elif '\x15' == key:
            # ^U deletes back to the start of the line
            start = before.rfind('\n') + 1
            self.text, self.position = before[:start] + after, start
        elif '\x17' == key:
            # ^W deletes the word before the cursor
            kept = before.rstrip(' ')
            kept = kept[:max(kept.rfind(' '), kept.rfind('\n')) + 1]
            self.text, self.position = kept + after, len(kept)
        elif isinstance(key, str) and key.isprintable():
            self.insert(key)

    def _layout(self, width):
        """[(start index, text)] of every screen row."""
        rows = []
        start = 0
        for line in self.text.split('\n'):
            # One column stays free for the cursor at the end of a row.
            step = max(width - 1, 1)
            for offset in range(0, max(len(line), 1), step):
                rows.append((start + offset, line[offset:offset + step]))
            start += len(line) + 1
        return rows

    def rows(self, width):
        """The text as screen rows."""
        return [text for _, text in self._layout(width)]

    def cursor_position(self, width):
        """(row, column) of the cursor inside the rows."""
        layout = self._layout(width)
        for row in range(len(layout) - 1, -1, -1):
            start, text = layout[row]
            if start <= self.position:
                return row, min(self.position - start, len(text))
        return 0, 0


class Area:
    """The part of the screen a view may draw into: rows 0 to height-1."""

    def __init__(self, screen, top, height, width):
        self.screen = screen
        self.top = top
        self.height = height
        self.width = width


def status_attribute(status):
    """Colour for a thread status or draft marker."""
    return curses.color_pair(STATUS_COLOURS.get(status, 0))


def comment_label(comment):
    """Status shown for a thread: its status, or draft if unsent."""
    if comment.is_draft():
        return 'draft'
    return comment.status + (' +draft' if comment.has_draft() else '')


# Keys that move in a list
LIST_KEYS = (ord('j'), ord('k'), curses.KEY_DOWN, curses.KEY_UP, GO_TOP, ord('G'))


def wheel_key(kind):
    """The key a wheel turn stands for: j down, k up, None otherwise."""
    return {WHEEL_DOWN: ord('j'), WHEEL_UP: ord('k')}.get(kind)


def move(index, key, length, count=None):
    """New list position after j, k, gg or G, staying inside the list.
    A count moves that many rows, or with gg and G goes to that row.
    """
    if not length:
        return 0
    if ord('j') == key or curses.KEY_DOWN == key:
        return min(index + (count or 1), length - 1)
    if ord('k') == key or curses.KEY_UP == key:
        return max(index - (count or 1), 0)
    if key in (GO_TOP, ord('G')):
        last = 0 if GO_TOP == key else length - 1
        return min(count - 1, length - 1) if count else last
    return index


class StackView:
    """The stacks in review, one per review branch, like Gerrit's dashboard."""

    KEYS = STACK_KEYS

    # First screen row of the list
    TOP = 3

    def __init__(self, interface):
        self._interface = interface
        self._changes = []
        self._rows = []
        self._selected = 0
        self._repository = ''
        self._base = ''

    def reload(self):
        """Reads the stacks again and lays out the rows: a heading per branch,
        then its changes.
        """
        core = self._interface.core
        self._changes = core.changes_in_stack()
        self._repository = core.repository_name('.')
        self._base = core.base_branch()
        self._selected = min(self._selected, max(len(self._changes) - 1, 0))
        self._rows = []
        branch = None
        for index, change in enumerate(self._changes):
            if change.branch != branch:
                branch = change.branch
                self._rows.append(branch)
            self._rows.append(index)

    def draw(self, area):
        """Draws the branches and their changes."""
        out = self._interface.text
        core = self._interface.core
        out(0, 1, f'{self._repository}: {len(self._changes)} changes in review, base {self._base}',
            curses.A_BOLD)
        out(2, 1, f'{"#":>4}  {"Change":<9} {"State":<10} {"PS":<5} {"Subject":<44} Comments',
            curses.A_UNDERLINE)
        for offset, row_content in enumerate(self._rows):
            row = self.TOP + offset
            if row >= area.height:
                break
            if isinstance(row_content, str):
                out(row, 1, row_content, curses.A_BOLD)
                continue
            index = row_content
            change = self._changes[index]
            attributes = curses.A_REVERSE if index == self._selected else 0
            out(row, 1, ' ' * (area.width - 2), attributes)
            out(row, 1, f'{index + 1:>4}  {change.short_id():<9} ', attributes)
            out(row, 17, f'{change.state():<10}',
                attributes | curses.color_pair(STATE_COLOURS[change.state()]))
            out(row, 28, f'{core.version_label(change):<5} {change.subject:<44.44} '
                f'{core.comment_counts(change)}', attributes)
        if not self._changes:
            out(self.TOP, 1, f'Nothing in review. Changes live on {core.REVIEW_BRANCH_PREFIX}* '
                'branches.')

    def mouse(self, row, _column, kind):
        """A click selects a change, a double click opens it."""
        if wheel_key(kind):
            self.key(wheel_key(kind))
            return
        offset = row - self.TOP
        if 0 <= offset < len(self._rows) and not isinstance(self._rows[offset], str):
            self._selected = self._rows[offset]
            if DOUBLE_CLICK == kind:
                self.key(ord('l'))

    def key(self, key, count=None):
        """Handles a key of the stack view."""
        self._selected = move(self._selected, key, len(self._changes), count)
        if not self._changes:
            return
        change = self._changes[self._selected]
        if key in (ord('l'), ord('\n'), curses.KEY_ENTER):
            self._interface.open(ChangeView(self._interface, change.change_id))
        elif ord('d') == key:
            self._interface.run_outside(self._interface.core.diff_command(change))
        elif ord('S') == key:
            submit_with_confirmation(self._interface, change)


def submit_with_confirmation(interface, change):
    """Asks before submitting. Lists the changes that get submitted along
    and the threads that are not finished.
    """
    core = interface.core
    submitted = core.changes_submitted_with(change)
    lines = [f'Submit to {core.base_branch()}:']
    lines += [f'  {other.short_id()}  {other.subject}' for other in submitted]
    unfinished = [(other, comment) for other in submitted
                  for comment in other.unfinished_comments()]
    lines.append('')
    if unfinished:
        lines.append('These comments are not finished:')
        lines += [f'  {other.short_id()} #{comment.number} [{comment.status}] {comment.place_label()}: '
                  f'{comment.messages[0].text.splitlines()[0]}' for other, comment in unfinished]
    else:
        lines.append('All comments are finished.')
    if interface.confirm(lines, 'Submit?'):
        core.submit(change.change_id, force=True)
        interface.after_write()
        interface.say('Submitted: ' + ', '.join(other.short_id() for other in submitted))


# ── the diff pane ─────────────────────────────────────────────────────────

# An escape sequence in delta's output: colours and styles (SGR, ending in m),
# other control sequences, and hyperlinks (OSC 8). Only SGR is used.
ESCAPE_PATTERN = re.compile(r'\x1b\[([0-9;]*)([A-Za-z])|\x1b\]8;[^\x1b\x07]*(?:\x1b\\|\x07)')

# Line numbers in delta's side by side output: "│ old │ ... │ new │"
NUMBERS_PATTERN = re.compile(r'^│\s*(\d*)\s*│.*?│\s*(\d*)\s*│')

# The 6 levels of each channel in the 256 colour palette's 6x6x6 cube
CUBE_LEVELS = (0, 95, 135, 175, 215, 255)

# Colour pairs of the interface itself use the numbers below this one.
FIRST_DIFF_PAIR = 16


# Difference between the strongest and weakest channel from which a true
# colour counts as coloured, not grey
MIN_HUE = 30


def palette_index(red, green, blue):
    """The nearest of the 256 palette colours to a true colour."""
    def nearest_level(value):
        return min(range(6), key=lambda index: abs(CUBE_LEVELS[index] - value))

    cube = [nearest_level(value) for value in (red, green, blue)]
    cube_colour = [CUBE_LEVELS[index] for index in cube]
    grey_index = min(23, max(0, round((red + green + blue) / 3 - 8) // 10))
    grey = 8 + 10 * grey_index

    def distance(colour):
        return sum((a - b) ** 2 for a, b in zip(colour, (red, green, blue)))

    # A coloured tone stays coloured even where a grey is nearer, e.g. the
    # dark green of added lines must not turn grey.
    has_hue = max(red, green, blue) - min(red, green, blue) >= MIN_HUE
    if not has_hue and distance((grey, grey, grey)) < distance(cube_colour):
        return 232 + grey_index
    return 16 + 36 * cube[0] + 6 * cube[1] + cube[2]


class ColourPairs:
    """Hands out curses colour pairs for foreground/background combinations.

    curses can only draw colours through numbered pairs, so every
    combination delta uses gets one, the first time it shows up.
    """

    def __init__(self):
        self._pairs = {}
        self._next = FIRST_DIFF_PAIR

    def attribute(self, foreground, background):
        """The curses attribute for two palette colours (-1 is default)."""
        key = (foreground, background)
        if key not in self._pairs:
            if self._next >= curses.COLOR_PAIRS or max(key) >= curses.COLORS:
                return 0
            curses.init_pair(self._next, foreground, background)
            self._pairs[key] = self._next
            self._next += 1
        return curses.color_pair(self._pairs[key])


def colour_from(codes, index):
    """Reads a colour after 38 or 48 in an SGR code list.

    Returns the palette colour and the index after it.
    """
    if index + 1 < len(codes) and 5 == codes[index + 1] and index + 2 < len(codes):
        return codes[index + 2], index + 3
    if index + 1 < len(codes) and 2 == codes[index + 1] and index + 4 < len(codes):
        return palette_index(*codes[index + 2:index + 5]), index + 5
    return -1, len(codes)


def styled_segments(line):
    """Splits a line of terminal output into (text, foreground, background,
    styles) parts. Colours are palette numbers, -1 the default.
    """
    segments = []
    foreground, background, styles = -1, -1, 0
    position = 0
    for match in ESCAPE_PATTERN.finditer(line):
        if match.start() > position:
            segments.append((line[position:match.start()], foreground, background, styles))
        position = match.end()
        if 'm' != match.group(2):
            continue
        codes = [int(code) if code else 0 for code in match.group(1).split(';')]
        index = 0
        while index < len(codes):
            code = codes[index]
            index += 1
            if 0 == code:
                foreground, background, styles = -1, -1, 0
            elif code in (1, 2, 3, 4, 7):
                styles |= {1: curses.A_BOLD, 2: curses.A_DIM, 3: curses.A_ITALIC,
                           4: curses.A_UNDERLINE, 7: curses.A_REVERSE}[code]
            elif code in (22, 23, 24, 27):
                styles &= ~{22: curses.A_BOLD | curses.A_DIM, 23: curses.A_ITALIC,
                            24: curses.A_UNDERLINE, 27: curses.A_REVERSE}[code]
            elif 30 <= code <= 37 or 90 <= code <= 97:
                foreground = code - 30 if code < 90 else code - 82
            elif 40 <= code <= 47 or 100 <= code <= 107:
                background = code - 40 if code < 100 else code - 92
            elif 38 == code:
                foreground, index = colour_from(codes, index - 1)
            elif 48 == code:
                background, index = colour_from(codes, index - 1)
            elif 39 == code:
                foreground = -1
            elif 49 == code:
                background = -1
    if position < len(line):
        segments.append((line[position:], foreground, background, styles))
    return segments


# Approximate colours of the 16 basic palette entries (xterm's defaults).
# The terminal may use others, good enough for lightening them a bit.
BASIC_COLOURS = ((0, 0, 0), (205, 0, 0), (0, 205, 0), (205, 205, 0), (0, 0, 238), (205, 0, 205),
                 (0, 205, 205), (229, 229, 229), (127, 127, 127), (255, 0, 0), (0, 255, 0),
                 (255, 255, 0), (92, 92, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255))

# Background of the cursor line where the diff has none: a dark grey
CURSOR_LINE_GREY = 236


def palette_colour(index):
    """(red, green, blue) of a palette colour."""
    if index < 16:
        return BASIC_COLOURS[index]
    if index >= 232:
        grey = 8 + 10 * (index - 232)
        return grey, grey, grey
    index -= 16
    return tuple(CUBE_LEVELS[level] for level in (index // 36, index // 6 % 6, index % 6))


def lighter(background):
    """A lighter palette colour for the cursor line's background, so the
    line stands out but keeps the diff's red or green.
    """
    if background < 0 or curses.COLORS < 256:
        return CURSOR_LINE_GREY if curses.COLORS >= 256 else background
    red, green, blue = palette_colour(background)
    # The palette is coarse: mix in more white until the colour changes.
    for share in (0.15, 0.25, 0.35, 0.5):
        colour = palette_index(*(round(value + (255 - value) * share)
                                 for value in (red, green, blue)))
        if colour != background:
            return colour
    return background


def plain(line):
    """A line of terminal output without its escape sequences."""
    return ESCAPE_PATTERN.sub('', line)


class DiffPane:
    """The diff of one file, rendered by delta and drawn inside the screen,
    with the comment threads shown under their lines.

    The rows are diff lines and thread lines. Every row has its parts
    (lines), the old and new line number (numbers, None on thread rows) and
    what it is (rows): ('diff', index of the diff line) or ('thread',
    thread number, row inside the thread). Keeps a cursor row and the
    scroll position.
    """

    def __init__(self, pairs):
        self._pairs = pairs
        self._key = None
        self._path = None
        self._threads_key = None
        self._diff_lines = []
        self._diff_numbers = []
        # The row to keep the cursor on when the rows are laid out again,
        # and whether to show the rows from the top then
        self._keep = None
        self._from_top = False
        self.rows = []
        self.lines = []
        self.numbers = []
        self._anchors = []
        self.cursor = 0
        self.scroll = 0

    def show(self, core, change, compare, path, width):
        """Renders the diff of a file, unless it is rendered already. The
        threads come with arrange(), call it after this.
        """
        key = (path, core.versions_to_compare(change, *compare), width)
        if key == self._key:
            return
        is_other_file = self._path != path
        self._key = key
        self._path = path
        (_, old), (_, new) = key[1]
        diff = subprocess.run(['git', '-c', 'core.pager=cat', 'diff', '--color=always', old, new,
                               '--', path], capture_output=True, text=True).stdout
        try:
            rendered = subprocess.run(['delta', '--side-by-side', f'--width={width}',
                                       '--paging=never'], input=diff, capture_output=True,
                                      text=True).stdout
        except FileNotFoundError:
            rendered = diff
        lines = (rendered.rstrip('\n').split('\n') if rendered.strip()
                 else ['No differences in this file.'])
        self._diff_lines = [styled_segments(line) for line in lines]
        self._diff_numbers = []
        for line in lines:
            match = NUMBERS_PATTERN.match(plain(line))
            self._diff_numbers.append((int(match.group(1)) if match and match.group(1) else None,
                                       int(match.group(2)) if match and match.group(2) else None))
        if is_other_file:
            first = next((index for index, numbers in enumerate(self._diff_numbers)
                          if any(numbers)), 0)
            self._keep = (('diff', first), first)
            # A new file starts at its top, file threads and all.
            self.cursor = self.scroll = 0
            self._from_top = True
        elif self.rows and not self._keep:
            self._keep = (self.rows[self.cursor], self._anchors[self.cursor])
        self._threads_key = None

    def forget(self):
        """Makes the next show() render again, e.g. after a reload."""
        self._key = None

    def arrange(self, blocks, threads_key):
        """Lays out the rows: the diff lines, each followed by the threads on
        it, file threads first. blocks maps (side, line), or None for the
        whole file, to [(thread number, [parts of each row])].
        Does nothing while threads_key stays the same.
        """
        if threads_key == self._threads_key:
            return
        self._threads_key = threads_key
        if self.rows and not self._keep:
            self._keep = (self.rows[self.cursor], self._anchors[self.cursor])
        offset = self.cursor - self.scroll
        self.rows, self.lines, self.numbers, self._anchors = [], [], [], []

        def add_threads(place, anchor):
            for number, thread_lines in blocks.pop(place, []):
                for row, parts in enumerate(thread_lines):
                    self.rows.append(('thread', number, row))
                    self.lines.append(parts)
                    self.numbers.append((None, None))
                    self._anchors.append(anchor)

        add_threads(None, -1)
        for index, (old, new) in enumerate(self._diff_numbers):
            self.rows.append(('diff', index))
            self.lines.append(self._diff_lines[index])
            self.numbers.append((old, new))
            self._anchors.append(index)
            add_threads(('new', new), index)
            add_threads(('old', old), index)
        self._place_cursor(offset)

    def _place_cursor(self, offset):
        """Puts the cursor back on the kept row: the same one, else the
        thread's first row, else the diff line it was below.
        """
        if not self._keep:
            self.cursor = min(self.cursor, max(len(self.rows) - 1, 0))
            return
        row, anchor = self._keep
        self._keep = None
        candidates = [row, ('diff', max(anchor, 0))]
        if 'thread' == row[0]:
            candidates.insert(1, ('thread', row[1], 0))
        index = next((self.rows.index(candidate) for candidate in candidates
                      if candidate in self.rows), 0)
        self.cursor = index
        self.scroll = 0 if self._from_top else max(0, index - max(offset, 0))
        self._from_top = False

    def thread_at(self, index):
        """Number of the thread a row belongs to, None on diff rows."""
        row = self.rows[index] if index < len(self.rows) else ('diff', 0)
        return row[1] if 'thread' == row[0] else None

    def thread_end(self, number):
        """Index of the last row of a thread, None if it isn't shown."""
        indexes = [index for index, row in enumerate(self.rows)
                   if 'thread' == row[0] and number == row[1]]
        return indexes[-1] if indexes else None

    def segments(self, index, highlighted):
        """The parts of a row as (text, curses attribute). The cursor row
        is highlighted with lighter backgrounds.
        """
        result = []
        for text, foreground, background, styles in self.lines[index]:
            if highlighted:
                background = lighter(background)
                styles |= 0 if curses.COLORS >= 256 else curses.A_REVERSE
            result.append((text, self._pairs.attribute(foreground, background) | styles))
        return result

    def cursor_line_attribute(self):
        """Attribute for the empty rest of the cursor row."""
        if curses.COLORS < 256:
            return curses.A_REVERSE
        return self._pairs.attribute(-1, CURSOR_LINE_GREY)

    def go_to_new_line(self, line, height):
        """Puts the cursor on line `line` of the new version, or the first
        line after it that the diff shows, else the last row (vim's NG).
        """
        index = next((index for index, (_, new) in enumerate(self.numbers)
                      if new is not None and new >= line), len(self.rows) - 1)
        self.move_cursor(index - self.cursor, height)

    def move_cursor(self, steps, height):
        """Moves the cursor and scrolls so it stays visible."""
        self.cursor = max(0, min(self.cursor + steps, len(self.rows) - 1))
        if self.cursor < self.scroll:
            self.scroll = self.cursor
        elif self.cursor >= self.scroll + height:
            self.scroll = self.cursor - height + 1

    def _jump(self, index, height):
        """Puts the cursor on a row, a third down the pane."""
        self.move_cursor(index - self.cursor, height)
        self.scroll = max(0, min(self.cursor - height // 3, len(self.rows) - height))

    def go_to_line(self, line, side, height):
        """Puts the cursor on a line number of the old or new version."""
        column = 0 if 'old' == side else 1
        for index, numbers in enumerate(self.numbers):
            if line == numbers[column]:
                self._jump(index, height)
                return

    def go_to_thread(self, number, height):
        """Puts the cursor on a thread's first row. Returns whether the
        thread is shown.
        """
        if ('thread', number, 0) not in self.rows:
            return False
        self._jump(self.rows.index(('thread', number, 0)), height)
        return True

    def make_room_below(self, index, height, room):
        """Scrolls so that `room` rows below row `index` are visible, for
        the comment box. Moves the cursor along if it would leave the pane.
        """
        if index - self.scroll > height - room - 1:
            self.scroll = max(0, index - max(height - room - 1, height // 3))
        if not self.scroll <= self.cursor < self.scroll + height:
            self.cursor = index

    def cursor_place(self):
        """(line, side) of the cursor line. New side where the line exists
        in the new version, else old side. None on rows without numbers.
        """
        old, new = self.numbers[self.cursor] if self.numbers else (None, None)
        if new is not None:
            return new, 'new'
        if old is not None:
            return old, 'old'
        return None


# Columns a thread is indented by in the diff
THREAD_INDENT = 3


def thread_rows(comment, width, expanded, reviewer):
    """A thread as rows of the diff, each a list of (text, foreground,
    background, styles). Folded it is one row with the start of the first
    message.
    """
    label = comment_label(comment)
    colour = STATUS_COLOURS['draft' if comment.is_draft() else comment.status]
    pad = ' ' * THREAD_INDENT
    title = (f'#{comment.number} {label}', colour, -1, curses.A_BOLD)
    if not expanded:
        first = comment.messages[0].text.split('\n')[0] if comment.messages else ''
        return [[(pad + '▸ ', -1, -1, curses.A_DIM), title, (f'  {first}', -1, -1, curses.A_DIM)]]
    inner = max(width - THREAD_INDENT - 2, 20)
    rows = [[(pad + '┌ ', -1, -1, curses.A_DIM), title,
             (' ' + '─' * max(inner - len(title[0]) - 2, 0), -1, -1, curses.A_DIM)]]
    for message in comment.messages:
        name = ('Reviewer' if reviewer == message.author else 'Author') + (
            ' (draft)' if not message.sent else '')
        name_colour = STATUS_COLOURS['draft'] if not message.sent else -1
        prefix = f'{name}: '
        lines = [line for paragraph in message.text.split('\n')
                 for line in textwrap.wrap(paragraph, max(inner - len(prefix), 10)) or ['']]
        for number, line in enumerate(lines):
            start = (prefix, name_colour, -1, curses.A_BOLD) if 0 == number else (
                ' ' * len(prefix), -1, -1, 0)
            rows.append([(pad + '│ ', -1, -1, curses.A_DIM), start, (line, -1, -1, 0)])
    rows.append([(pad + '└' + '─' * inner, -1, -1, curses.A_DIM)])
    return rows


# ── the change screen ─────────────────────────────────────────────────────

FILES = 'files'
COMMENTS = 'comments'
DIFF = 'diff'
PANES = (FILES, COMMENTS, DIFF)

PANE_KEYS = {
    FILES: 'j/k file  tab next pane  c comment (asks line)  h back',
    COMMENTS: 'j/k thread  l open thread  y agree  a apply  x drop  o reopen  h back',
    DIFF: ('j/k line  gg/G  ^d/^u half page  c comment on line  l fold thread  i fold all  '
           'r reply  e edit draft  y/a/x/o thread  h files'),
}
COMMON_CHANGE_KEYS = 's send  A apply all  p patch sets  t vimdiff  S submit  ? help'


# What the change screen compares at first: the whole change as of the
# latest patch set
DEFAULT_COMPARE = ('base', 'latest')


class ChangeView:
    """One change: files and threads on the left, the diff on the right."""

    def __init__(self, interface, change_id):
        self._interface = interface
        self._change_id = change_id
        self._change = None
        self._files = []
        self._compare = DEFAULT_COMPARE
        self._focus = FILES
        # The pane h returns to from the diff
        self._left_pane = FILES
        self._file_index = 0
        self._comment_index = 0
        self._diff = DiffPane(interface.colour_pairs)
        self._layout = None
        # Threads folded or unfolded by hand: number -> expanded
        self._expanded = {}

    @property
    def KEYS(self):  # pylint: disable=invalid-name
        """The keys of the focused pane, then the ones that work everywhere."""
        return f'{PANE_KEYS[self._focus]}  {COMMON_CHANGE_KEYS}'

    def reload(self):
        """Reads the change again. Goes back if it is gone."""
        core = self._interface.core
        for change in core.changes_in_stack():
            if change.change_id == self._change_id:
                self._change = change
                self._files = sorted(core.files_of(change.commit))
                self._file_index = min(self._file_index, max(len(self._files) - 1, 0))
                self._comment_index = min(self._comment_index, max(len(change.comments) - 1, 0))
                self._diff.forget()
                return
        self._interface.back()
        self._interface.say('The change is not in review any more.')

    # ── layout and drawing ────────────────────────────────────────────────

    def _compute_layout(self, area):
        """Positions of the three panes: (top, left, height, width) each,
        the inside without the frame.
        """
        left_width = min(max(area.width * 3 // 10, 28), 50)
        files_height = max((area.height - 3) // 2, 3)
        comments_top = 2 + files_height + 1
        return {FILES: (3, 1, files_height - 1, left_width - 2),
                COMMENTS: (comments_top + 1, 1, area.height - comments_top - 2, left_width - 2),
                DIFF: (3, left_width + 1, area.height - 4, area.width - left_width - 3)}

    def _frame(self, pane, title):
        """Draws the frame around a pane, with its title."""
        out = self._interface.text
        top, left, height, width = self._layout[pane]
        attributes = curses.A_BOLD if pane == self._focus else curses.A_DIM
        out(top - 1, left - 1, '┌' + '─' * width + '┐', attributes)
        out(top - 1, left + 1, f' {title} '[:max(width - 2, 0)], attributes)
        for row in range(top, top + height):
            out(row, left - 1, '│', attributes)
            out(row, left + width, '│', attributes)
        out(top + height, left - 1, '└' + '─' * width + '┘', attributes)

    def draw(self, area):
        """Draws the header and the three panes."""
        out = self._interface.text
        core = self._interface.core
        change = self._change
        self._layout = self._compute_layout(area)
        out(0, 1, change.subject, curses.A_BOLD)
        state = f'{change.short_id()}  {change.state()}  {core.version_label(change)}'
        out(0, max(area.width - len(state) - 2, 1), state)
        patch_sets = ' '.join(str(number) for number in sorted(change.patch_sets)) or '-'
        try:
            (first, _), (second, _) = core.versions_to_compare(change, *self._compare)
            comparing = f'comparing {first} -> {second}'
        except core.ChangeError as error:
            comparing = str(error)
        out(1, 1, f'Patch sets: {patch_sets}   {comparing}', curses.A_DIM)
        self._draw_files()
        self._draw_comments()
        self._draw_diff()

    def _draw_files(self):
        """Draws the file list."""
        out = self._interface.text
        self._frame(FILES, 'Files')
        top, left, height, width = self._layout[FILES]
        for index, path in enumerate(self._files[:height]):
            selected = index == self._file_index
            attributes = curses.A_REVERSE if selected and FILES == self._focus else (
                curses.A_BOLD if selected else 0)
            out(top + index, left, f'{path:<{width}.{width}}', attributes, left + width)

    def _draw_comments(self):
        """Draws the thread list."""
        out = self._interface.text
        self._frame(COMMENTS, 'Comments')
        top, left, height, width = self._layout[COMMENTS]
        comments = self._change.comments
        if not comments:
            out(top, left, 'none yet', curses.A_DIM, left + width)
        for index, comment in enumerate(comments[:height]):
            selected = index == self._comment_index
            attributes = curses.A_REVERSE if selected and COMMENTS == self._focus else 0
            label = comment_label(comment)
            out(top + index, left, ' ' * width, attributes, left + width)
            out(top + index, left, f'#{comment.number} ', attributes, left + width)
            out(top + index, left + 4, f'[{label}]',
                attributes | status_attribute('draft' if comment.is_draft() else comment.status),
                left + width)
            place = comment.place_label().rsplit('/', 1)[-1]
            out(top + index, left + 6 + len(label), f' {place}', attributes, left + width)

    def _markers(self):
        """Threads on the shown file: (side, line) -> numbers of threads."""
        path = self._selected_file()
        markers = {}
        for comment in self._change.comments:
            file_path, _, line = comment.place.rpartition(':')
            if file_path == path and line.isdigit():
                markers.setdefault((comment.side, int(line)), []).append(comment.number)
        return markers

    def _draw_diff(self):
        """Draws the diff of the selected file, with cursor and thread marks."""
        out = self._interface.text
        path = self._selected_file()
        self._frame(DIFF, path or 'Diff')
        top, left, height, width = self._layout[DIFF]
        if not path:
            return
        self._prepare_diff()
        markers = self._markers()
        for row in range(height):
            index = self._diff.scroll + row
            if index >= len(self._diff.lines):
                break
            old, new = self._diff.numbers[index]
            has_thread = (('new', new) in markers) or (('old', old) in markers)
            if self._diff.thread_at(index) is not None:
                has_thread = False
            # The cursor line is highlighted in every pane, the > only shows
            # while the diff has the focus.
            is_cursor = index == self._diff.cursor
            gutter = '>' if is_cursor and DIFF == self._focus else ' '
            out(top + row, left, gutter, curses.A_BOLD)
            out(top + row, left + 1, '●' if has_thread else ' ', status_attribute('open'))
            if is_cursor:
                out(top + row, left + 2, ' ' * (width - 2), self._diff.cursor_line_attribute(),
                    left + width)
            column = left + 2
            for text, attributes in self._diff.segments(index, is_cursor):
                if column >= left + width:
                    break
                out(top + row, column, text, attributes, left + width)
                column += len(text)

    # ── selection ─────────────────────────────────────────────────────────

    def _selected_file(self):
        return self._files[self._file_index] if self._files else None

    def _selected_comment(self):
        comments = self._change.comments
        return comments[self._comment_index] if comments else None

    def _diff_height(self):
        return self._layout[DIFF][2] if self._layout else 20

    def _is_expanded(self, comment):
        """Whether a thread shows all its messages in the diff. Unfinished
        ones and those with drafts do, unless folded by hand.
        """
        default = 'open' == comment.status or comment.has_draft() or comment.is_draft()
        return self._expanded.get(comment.number, default)

    def _prepare_diff(self):
        """Renders the selected file's diff and lays out its threads.
        Returns False if there is nothing to show.
        """
        path = self._selected_file()
        if not path or not self._layout:
            return False
        width = self._layout[DIFF][3] - 2
        core = self._interface.core
        self._diff.show(core, self._change, self._compare, path, width)
        blocks = {}
        signature = [path, width]
        for comment in self._change.comments:
            file_path, _, line = comment.place.rpartition(':')
            if not line.isdigit():
                file_path, line = comment.place, ''
            if file_path != path:
                continue
            expanded = self._is_expanded(comment)
            place = (comment.side, int(line)) if line else None
            blocks.setdefault(place, []).append(
                (comment.number, thread_rows(comment, width, expanded, core.REVIEWER)))
            signature.append((comment.number, comment.status, place, expanded,
                              tuple((message.author, message.text, message.sent)
                                    for message in comment.messages)))
        self._diff.arrange(blocks, tuple(signature))
        return True

    def _show_comment_in_diff(self, comment):
        """Selects the thread's file and puts the diff cursor on the thread,
        or on its line if the thread isn't in the diff.
        """
        path, _, line = comment.place.rpartition(':')
        path = path if line.isdigit() else comment.place
        if path in self._files:
            self._file_index = self._files.index(path)
            if self._prepare_diff():
                height = self._diff_height()
                if not self._diff.go_to_thread(comment.number, height) and line.isdigit():
                    self._diff.go_to_line(int(line), comment.side, height)

    def _threads_at_cursor(self):
        """Numbers of the threads on the diff's cursor row: the thread the
        row belongs to, or the threads on the cursor line.
        """
        if not self._diff.numbers:
            return []
        number = self._diff.thread_at(self._diff.cursor)
        if number is not None:
            return [number]
        old, new = self._diff.numbers[self._diff.cursor]
        markers = self._markers()
        return markers.get(('new', new), []) + markers.get(('old', old), [])

    def _thread_at_cursor(self):
        """The thread the diff's cursor is on, or the first on its line."""
        numbers = self._threads_at_cursor()
        return self._change.comments[numbers[0] - 1] if numbers else None

    def _box_below_thread(self, comment):
        """Where the comment box goes for a reply: below the thread in the
        diff, scrolled into view. None outside the diff.
        """
        if DIFF != self._focus or not self._layout:
            return None
        end = self._diff.thread_end(comment.number)
        if end is None:
            return None
        top, left, height, width = self._layout[DIFF]
        self._diff.make_room_below(end, height, TEXT_BOX_ROWS + 2)
        return top + end - self._diff.scroll, left - 1, width + 2

    def _target_thread(self):
        """The thread keys like y or r act on: the selected one in the
        comments, the one at the cursor in the diff.
        """
        if COMMENTS == self._focus:
            return self._selected_comment()
        if DIFF == self._focus:
            return self._thread_at_cursor()
        return None

    def _toggle_threads(self):
        """l in the diff: folds or unfolds the thread at the cursor."""
        numbers = self._threads_at_cursor()
        if not numbers:
            self._interface.say('No thread on this line. c starts one.')
            return
        comments = [self._change.comments[number - 1] for number in numbers]
        expand = not any(self._is_expanded(comment) for comment in comments)
        for comment in comments:
            self._expanded[comment.number] = expand

    def _toggle_all_threads(self):
        """i: folds all threads if one is unfolded, else unfolds all."""
        comments = self._change.comments
        expand = not any(self._is_expanded(comment) for comment in comments)
        self._expanded = {comment.number: expand for comment in comments}

    # ── keys and mouse ────────────────────────────────────────────────────

    def key(self, key, count=None):
        """Handles a key of the change view. count is a typed count, e.g.
        the 15 of 15j, or None.
        """
        interface = self._interface
        core = interface.core
        change = self._change
        if ord('\t') == key:
            self._focus_pane(PANES[(PANES.index(self._focus) + 1) % len(PANES)])
        elif FILES == self._focus and key in LIST_KEYS:
            self._file_index = move(self._file_index, key, len(self._files), count)
        elif COMMENTS == self._focus and key in LIST_KEYS:
            self._comment_index = move(self._comment_index, key, len(change.comments), count)
            if self._selected_comment():
                self._show_comment_in_diff(self._selected_comment())
        elif DIFF == self._focus and key in (ord('j'), curses.KEY_DOWN):
            self._diff.move_cursor(count or 1, self._diff_height())
        elif DIFF == self._focus and key in (ord('k'), curses.KEY_UP):
            self._diff.move_cursor(-(count or 1), self._diff_height())
        elif DIFF == self._focus and key in (GO_TOP, ord('G')) and count:
            self._diff.go_to_new_line(count, self._diff_height())
        elif DIFF == self._focus and GO_TOP == key:
            self._diff.move_cursor(-self._diff.cursor, self._diff_height())
        elif DIFF == self._focus and ord('G') == key:
            self._diff.move_cursor(len(self._diff.rows), self._diff_height())
        elif DIFF == self._focus and 4 == key:
            self._diff.move_cursor(self._diff_height() // 2, self._diff_height())
        elif DIFF == self._focus and 21 == key:
            self._diff.move_cursor(-(self._diff_height() // 2), self._diff_height())
        elif key in (ord('l'), ord('\n'), curses.KEY_ENTER):
            self._open()
        elif ord('t') == key and self._selected_file():
            interface.run_outside(core.diff_command(change, *self._compare, tool=True,
                                                    path=self._selected_file()))
        elif ord('p') == key:
            self._choose_patch_sets()
        elif ord('i') == key:
            self._toggle_all_threads()
        elif ord('c') == key and DIFF == self._focus and self._diff.thread_at(self._diff.cursor):
            # c on a thread answers it
            self.key(ord('r'))
        elif ord('c') == key:
            self._new_comment()
        elif ord('S') == key:
            submit_with_confirmation(interface, change)
        elif key in (ord('r'), ord('e')) and self._target_thread():
            comment = self._target_thread()
            anchor = self._box_below_thread(comment)
            if ord('r') == key:
                reply_to_thread(interface, self._change_id, comment, anchor)
            else:
                edit_draft(interface, self._change_id, comment, anchor)
        else:
            thread_action(interface, change, self._target_thread(), key)

    def _focus_pane(self, pane):
        """Moves the focus. Remembers the pane left for the diff, h goes
        back there.
        """
        if DIFF == pane and DIFF != self._focus:
            self._left_pane = self._focus
        self._focus = pane

    def go_left(self):
        """h in the diff: back to the files or comments. Returns whether it
        did, elsewhere h goes back to the previous screen.
        """
        if DIFF != self._focus:
            return False
        self._focus = self._left_pane
        return True

    def _open(self):
        """l: in the files, shows the diff. Elsewhere, opens a thread."""
        interface = self._interface
        if FILES == self._focus:
            self._focus_pane(DIFF)
        elif COMMENTS == self._focus and self._selected_comment():
            interface.open(ThreadView(interface, self._change_id,
                                      self._selected_comment().number))
        elif DIFF == self._focus:
            self._toggle_threads()

    def mouse(self, row, column, kind):
        """A click focuses a pane and selects the row under it. A double
        click opens, the wheel scrolls the pane under the pointer.
        """
        if not self._layout:
            return
        for pane, (top, left, height, width) in self._layout.items():
            if top <= row < top + height and left <= column < left + width:
                self._focus_pane(pane)
                if wheel_key(kind):
                    steps = 3 if DIFF == pane else 1
                    for _ in range(steps):
                        self.key(wheel_key(kind))
                    return
                offset = row - top
                if FILES == pane and offset < len(self._files):
                    self._file_index = offset
                elif COMMENTS == pane and offset < len(self._change.comments):
                    self._comment_index = offset
                    self._show_comment_in_diff(self._selected_comment())
                elif DIFF == pane:
                    self._diff.cursor = min(self._diff.scroll + offset,
                                            max(len(self._diff.rows) - 1, 0))
                if DOUBLE_CLICK == kind:
                    self._open()
                return

    def _choose_patch_sets(self):
        """Asks which two versions to compare."""
        answer = self._interface.ask('Compare (e.g. "1 3", "2 current", "base 2", '
                                     'empty for base latest):')
        if answer is None:
            return
        parts = answer.split()
        if not parts:
            self._compare = DEFAULT_COMPARE
        elif len(parts) <= 2 and all(part.isdigit() or part in ('current', 'base', 'latest')
                                     for part in parts):
            compare = (parts[0], parts[1] if 2 == len(parts) else None)
            # Checked here: a wrong choice must not break drawing the diff.
            self._interface.core.versions_to_compare(self._change, *compare)
            self._compare = compare
        else:
            self._interface.say('Give one or two of: patch set numbers, "base", "latest", '
                                '"current".')

    def _new_comment(self):
        """Starts a draft thread: on the diff's cursor line, or, from the
        file list, on a line that is asked for.
        """
        interface = self._interface
        path = self._selected_file()
        if not path:
            return
        side = 'new'
        anchor = None
        if DIFF == self._focus:
            place = self._diff.cursor_place()
            if place is None:
                interface.say('Put the cursor on a line with a line number.')
                return
            line, side = place
            line = str(line)
            # The box opens right below the commented line.
            # If the line is too far down for that, the diff scrolls up.
            top, left, height, width = self._layout[DIFF]
            self._diff.move_cursor(0, height)
            self._diff.make_room_below(self._diff.cursor, height, TEXT_BOX_ROWS + 2)
            anchor = (top + self._diff.cursor - self._diff.scroll, left - 1, width + 2)
        else:
            line = interface.ask(f'Line in {path} (empty for the whole file):')
            if line is None:
                return
            if line and not line.isdigit():
                interface.say('The line has to be a number.')
                return
        text = interface.write_text(f'Comment on {path.rsplit("/", 1)[-1]}'
                                    + (f':{line}' if line else ''), anchor=anchor)
        if not text:
            interface.say('Nothing saved.' if text is None else 'Empty comment, nothing saved.')
            return
        place = f'{path}:{line}' if line else path
        interface.core.add_comment(self._change_id, place, text, sent=False, side=side)
        interface.after_write()
        interface.say('Draft saved. s sends all drafts.')


def reply_to_thread(interface, change_id, comment, anchor=None):
    """r: writes a reply as a draft, in the comment box at anchor."""
    if comment.has_draft():
        interface.say('You have a draft already. e edits it.')
        return
    text = interface.write_text(f'Reply to #{comment.number}', anchor=anchor)
    if text:
        core = interface.core
        core.add_message(change_id, comment.number, core.REVIEWER, text, sent=False)
        interface.after_write()
        interface.say('Reply saved as a draft. s sends all drafts.')


def edit_draft(interface, change_id, comment, anchor=None):
    """e: edits the thread's draft. Saving it empty deletes it."""
    if not comment.has_draft():
        interface.say('No draft to edit in this thread.')
        return
    text = interface.write_text('Edit draft (empty deletes it)', comment.messages[-1].text,
                                anchor)
    if text is None:
        return
    if text:
        interface.core.edit_draft(change_id, comment.number, text)
    else:
        interface.core.delete_draft(change_id, comment.number)
    interface.after_write()


def thread_action(interface, change, comment, key):
    """Handles the keys that act on a thread or on all drafts.

    Shared by the change view (comment = the selected thread, or None while
    the files are selected) and the thread view.
    """
    core = interface.core
    if ord('s') == key:
        count = core.send_drafts(change.change_id)
        interface.after_write()
        interface.say(f'{count} draft(s) sent.' if count else 'No drafts to send.')
    elif ord('A') == key:
        core.request_apply(change.change_id)
        interface.after_write()
        interface.say('Asked to apply all agreed comments.')
    elif comment is None:
        return
    elif ord('a') == key:
        core.request_apply(change.change_id, comment.number)
        interface.after_write()
        interface.say(f'Asked to apply #{comment.number}.')
    elif key in (ord('y'), ord('x'), ord('o')):
        status = {ord('y'): 'agreed', ord('x'): 'dropped', ord('o'): 'open'}[key]
        core.set_status(change.change_id, comment.number, status)
        interface.after_write()
        interface.say(f'#{comment.number} {status}.')


class ThreadView:
    """One comment thread with all its messages."""

    KEYS = THREAD_KEYS

    def __init__(self, interface, change_id, number):
        self._interface = interface
        self._change_id = change_id
        self._number = number
        self._change = None
        self._comment = None
        self._quoted = ''
        self._scroll = 0

    def reload(self):
        """Reads the thread again. Goes back if it is gone."""
        core = self._interface.core
        for change in core.changes_in_stack():
            if change.change_id == self._change_id and self._number <= len(change.comments):
                self._change = change
                self._comment = change.comments[self._number - 1]
                commit = change.patch_sets.get(self._comment.patch_set, change.commit)
                self._quoted = core.line_at(commit, self._comment.place, side=self._comment.side)
                return
        self._interface.back()
        self._interface.say('The thread is gone.')

    def _lines(self, width):
        """The thread as display lines: (text, attributes)."""
        lines = []
        for message in self._comment.messages:
            name = 'Reviewer' if self._interface.core.REVIEWER == message.author else 'Author'
            draft = ' (draft)' if not message.sent else ''
            lines.append((f'{name}{draft}:', curses.A_BOLD
                          | (status_attribute('draft') if draft else 0)))
            for paragraph in message.text.split('\n'):
                for line in textwrap.wrap(paragraph, max(width - 6, 20)) or ['']:
                    lines.append((f'  {line}', 0))
            lines.append(('', 0))
        return lines

    def draw(self, area):
        """Draws the thread's header, the quoted line and the messages."""
        out = self._interface.text
        comment = self._comment
        label = comment_label(comment)
        out(0, 1, f'#{comment.number} ', curses.A_BOLD)
        out(0, 5, f'[{label}]', status_attribute('draft' if comment.is_draft() else comment.status))
        out(0, 8 + len(label), f'{comment.place_label()}  (PS{comment.patch_set})  '
            f'{self._change.subject}', curses.A_BOLD)
        if self._quoted:
            out(1, 3, f'| {self._quoted}', curses.A_DIM)
        lines = self._lines(area.width)
        self._scroll = min(self._scroll, max(len(lines) - (area.height - 3), 0))
        for row, (text, attributes) in enumerate(lines[self._scroll:]):
            if 3 + row >= area.height:
                break
            out(3 + row, 1, text, attributes)

    def mouse(self, _row, _column, kind):
        """The wheel scrolls the thread."""
        if wheel_key(kind):
            self.key(wheel_key(kind))

    def key(self, key, count=None):
        """Handles a key of the thread view."""
        interface = self._interface
        if ord('j') == key:
            self._scroll += count or 1
        elif ord('k') == key:
            self._scroll = max(self._scroll - (count or 1), 0)
        elif GO_TOP == key:
            self._scroll = 0
        elif ord('G') == key:
            # draw() limits it to the end of the thread
            self._scroll = 1 << 30
        elif ord('r') == key:
            reply_to_thread(interface, self._change_id, self._comment)
        elif ord('e') == key:
            edit_draft(interface, self._change_id, self._comment)
        else:
            thread_action(interface, self._change, self._comment, key)
