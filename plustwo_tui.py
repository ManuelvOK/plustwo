"""Terminal user interface of plustwo, for the reviewer.

Started by `plustwo` without arguments. Two screens: the stacks in
review (one per topic), and one change with its files, its comment
threads and the diff, which shows the threads under their lines.
Keys are shown at the bottom of every screen, `?` lists them all. Every
key is an action, see ACTIONS. The setting keys.preset picks vim (the
default), emacs or gerrit keys, keys.<action> sets single ones.

The change screen shows the diff side by side like Gerrit, drawn here from
the rows core.file_diff_rows() lays out, with syntax colours from bat if it
is installed. t hands over to vimdiff. Comments are written in a small box that opens inside the
diff and pushes the lines below down, ^E continues in the reviewer's
editor. The screen reloads by itself when commits or notes change, e.g.
when the author answers.

The mouse works too: a click selects, a double click opens, the wheel
scrolls.
"""

import curses
import os
import re
import shutil
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


STATE_COLOURS = {'New': 0, 'In-Review': 3, 'Submitted': 2}
STATUS_COLOURS = {'unresolved': 3, 'resolved': 2, 'draft': 5}

# How many lines around each change the diff shows, + and - step through
# them. None is the whole file.
CONTEXT_STEPS = (3, 10, 25, 100, None)

# Backgrounds of diff lines that differ because the changes below changed:
# (line, changed words) of lines from below and of lines partly from below,
# and for terminals with fewer colours
FROM_BELOW_BACKGROUNDS = (18, 26)
PARTLY_BACKGROUNDS = (54, 92)
FROM_BELOW_BASIC = 4
PARTLY_BASIC = 5

# ── keys ──────────────────────────────────────────────────────────────────

# Alt plus a key arrives as Esc and the key. It becomes the key plus this.
META = 1 << 24

# Every action of the interface: (name, group, short label for the footer,
# description for the help). The keys come from a preset.
ACTIONS = (
    ('quit', 'Everywhere', 'quit', 'quit, after asking'),
    ('help', 'Everywhere', 'help', 'this help'),
    ('reload', 'Everywhere', 'reload', 'reload now (it also reloads by itself)'),
    ('back', 'Everywhere', 'back', 'back (not on the first screen, quit quits). In the diff, '
     'back to the files or comments.'),
    ('down', 'Moving', 'move', 'down. A count before moves that far, e.g. 15 down.'),
    ('up', 'Moving', 'move', 'up'),
    ('top', 'Moving', 'top', 'to the top. With a count to that row.'),
    ('bottom', 'Moving', 'bottom', 'to the bottom. With a count to that row, in the diff to '
     'that line of the new version.'),
    ('page-down', 'Moving', 'half page', 'half a page down'),
    ('page-up', 'Moving', 'half page', 'half a page up'),
    ('open', 'Moving', 'open', 'open. On a file: into the diff. In the comments: unfolds the '
     'thread and goes to it in the diff. In the diff: folds or unfolds the thread.'),
    ('diff', 'Stacks', 'diff', 'the diff of the change in git\'s pager'),
    ('vote', 'Stacks and changes', 'vote', 'vote Code-Review (-2..+2) on the latest patch set'),
    ('verified', 'Stacks and changes', 'needs Verified', 'whether the change needs Verified+1 '
     '(from a check calling plustwo vote CHANGE Verified +1)'),
    ('submit', 'Stacks and changes', 'submit', 'submit: moves the base branch to the change, '
     'which submits the changes below it too. Needs Code-Review+2 (and Verified+1 if '
     'required). Asks first and lists the unresolved comments.'),
    ('approve', 'Stacks and changes', '+2', 'vote Code-Review+2 on the latest patch set, like '
     'Gerrit\'s Quick approve. Offers to send your drafts along.'),
    ('next-turn', 'Stacks', 'my turn', 'to the next change where it is your turn'),
    ('rebase', 'Stacks and changes', 'rebase', 'rebase the stack onto the base branch, without '
     'touching any checkout. On a conflict the author is asked to rebase.'),
    ('next-pane', 'Change', 'next pane', 'next pane: files, comments, diff'),
    ('next-file', 'Change', 'file', 'next file'),
    ('prev-file', 'Change', 'file', 'previous file'),
    ('next-chunk', 'Change', 'chunk', 'in the diff: to the next changed lines'),
    ('prev-chunk', 'Change', 'chunk', 'in the diff: to the previous changed lines'),
    ('next-thread', 'Change', 'thread', 'in the diff: to the next thread'),
    ('prev-thread', 'Change', 'thread', 'in the diff: to the previous thread'),
    ('comment', 'Change', 'comment', 'in the diff: comment on the cursor line, or on the whole '
     'file on rows without a line number. On a thread it replies. In the files: comment on '
     'the file. It stays a draft until sent.'),
    ('file-comment', 'Change', 'on file', 'comment on the whole file'),
    ('more-context', 'Change', 'context', 'more context around the changes, up to the whole '
     'file'),
    ('less-context', 'Change', 'context', 'less context around the changes'),
    ('vimdiff', 'Change', 'vimdiff', 'the file\'s diff in vimdiff'),
    ('patch-sets', 'Change', 'patch sets', 'choose the versions to compare: "1 3", "2 '
     'current" or "base 2" (the whole change as of PS2). Empty: the whole change, latest '
     'patch set.'),
    ('fold-all', 'Change', 'fold all', 'fold all threads, or unfold all if all are folded'),
    ('reviewed', 'Change', 'reviewed', 'mark the file reviewed, or unmark it. The mark stays while '
     'the file\'s diff stays the same.'),
    ('side-old', 'Change', 'old side', 'in the diff: the cursor to the old side, comments go there'),
    ('side-new', 'Change', 'new side', 'in the diff: the cursor to the new side'),
    ('reply', 'Threads', 'reply', 'reply to the thread, as a draft'),
    ('edit-draft', 'Threads', 'edit draft', 'edit your draft in the thread. Empty deletes it.'),
    ('done', 'Threads', 'Done', 'reply "Done" as a draft, which resolves the thread'),
    ('ack', 'Threads', 'Ack', 'reply "Ack" as a draft, which resolves the thread'),
    ('resolve', 'Threads', 'resolved', 'whether sending the draft resolves the thread or makes '
     'it unresolved again (Gerrit\'s Resolved checkbox). Without a draft, it makes an empty '
     'one.'),
    ('send', 'Threads', 'send', 'send all drafts of this change, with an optional message on '
     'the whole change, which tells the author to start'),
)
ACTION_NAMES = tuple(action[0] for action in ACTIONS)

# The keys of each action, per preset. Alternatives are separated by
# commas, the keys of a sequence like gg by spaces. C-x is Ctrl, M-x Alt.
VIM_KEYS = {
    'quit': 'q', 'help': '?', 'reload': 'R', 'back': 'h,Esc',
    'down': 'j,Down', 'up': 'k,Up', 'top': 'g g,Home', 'bottom': 'G,End',
    'page-down': 'C-d,PageDown', 'page-up': 'C-u,PageUp', 'open': 'l,Enter',
    'diff': 'd', 'vote': 'v', 'verified': 'V', 'submit': 'S', 'rebase': 'B',
    'next-pane': 'Tab', 'next-file': '] f', 'prev-file': '[ f',
    'next-chunk': '] c', 'prev-chunk': '[ c', 'next-thread': '] t', 'prev-thread': '[ t',
    'comment': 'c', 'file-comment': 'C', 'more-context': '+,=', 'less-context': '-',
    'vimdiff': 't', 'patch-sets': 'p', 'fold-all': 'i',
    'reply': 'r', 'edit-draft': 'e', 'done': 'y', 'ack': 'Y', 'resolve': 'x', 'send': 's',
    'approve': 'A', 'next-turn': 'T', 'reviewed': 'm', 'side-old': '<', 'side-new': '>',
}
PRESETS = {
    'vim': VIM_KEYS,
    # Emacs moves with C-n, C-p, C-v, M-v, M-< and M->, and C-g cancels.
    # Like in its diff-mode, n and p go to the next and previous changes,
    # N and P to the next and previous file. Counts start with C-u.
    'emacs': {**VIM_KEYS,
              'quit': 'q,C-x C-c', 'reload': 'g,R', 'back': 'C-g,Esc,C-b',
              'down': 'C-n,Down', 'up': 'C-p,Up', 'top': 'M-<,Home', 'bottom': 'M->,End',
              'page-down': 'C-v,PageDown', 'page-up': 'M-v,PageUp', 'open': 'Enter,C-f',
              'next-chunk': 'n', 'prev-chunk': 'p', 'next-file': 'N', 'prev-file': 'P',
              'next-thread': ']', 'prev-thread': '[', 'patch-sets': 'b'},
    # Gerrit's web interface: j and k move, n and p go to the next and
    # previous changes, N and P to the next and previous thread, ] and [ to
    # the next and previous file, u goes up, o opens, c comments, and a
    # opens the reply dialog, which sends the drafts.
    'gerrit': {**VIM_KEYS,
               'back': 'u,h,Esc', 'open': 'o,l,Enter',
               'next-chunk': 'n', 'prev-chunk': 'p', 'next-thread': 'N', 'prev-thread': 'P',
               'next-file': ']', 'prev-file': '[', 'send': 'a', 'patch-sets': 'b',
               # Gerrit's r marks the file reviewed
               'reviewed': 'r,m', 'reply': 'R', 'reload': 'C-r'},
}
# Presets whose counts start with a key, like Emacs' C-u 15 C-n. The
# others take digits right away, like vim's 15j.
COUNT_PREFIXES = {'emacs': 'C-u'}

NAMED_KEYS = {
    'Enter': (10, 13, curses.KEY_ENTER), 'Esc': (27,), 'Tab': (9,), 'S-Tab': (curses.KEY_BTAB,),
    'Space': (32,), 'Comma': (ord(','),), 'Up': (curses.KEY_UP,), 'Down': (curses.KEY_DOWN,),
    'Left': (curses.KEY_LEFT,), 'Right': (curses.KEY_RIGHT,), 'PageUp': (curses.KEY_PPAGE,),
    'PageDown': (curses.KEY_NPAGE,), 'Home': (curses.KEY_HOME,), 'End': (curses.KEY_END,),
    'Backspace': (curses.KEY_BACKSPACE, 127, 8),
}


def key_codes(token):
    """The key codes a key like j, C-n, M-v or Enter stands for."""
    if token in NAMED_KEYS:
        return NAMED_KEYS[token]
    if token.startswith('M-') and len(token) > 2:
        return tuple(META | code for code in key_codes(token[2:]))
    if token.startswith('C-') and 3 == len(token):
        return ((ord(token[2].lower()) & 0x1f) if token[2].isalpha() else ord(token[2]) & 0x1f,)
    if 1 == len(token):
        return (ord(token),)
    raise ValueError(f'unknown key "{token}"')


def key_sequences(spec):
    """The key code sequences of a spec like "g g,Home"."""
    sequences = []
    for alternative in filter(None, (part.strip() for part in spec.split(','))):
        options = [()]
        for token in alternative.split():
            options = [sequence + (code,) for sequence in options for code in key_codes(token)]
        sequences += options
    return sequences


def key_label(alternative):
    """A key alternative for display: gg for "g g", C-x C-c stays."""
    tokens = alternative.split()
    return ''.join(tokens) if all(1 == len(token) for token in tokens) else ' '.join(tokens)


# What Keymap.feed() returns while a count or a key sequence goes on
PENDING = 'pending'


class Keymap:
    """The keys of a preset, with the reviewer's own on top, turned into
    actions. Counts and key sequences like gg are collected here, so the
    views only see actions.
    """

    def __init__(self, preset='vim', overrides=None):
        self.errors = []
        if preset not in PRESETS:
            self.errors.append(f'unknown keys.preset "{preset}", using vim')
            preset = 'vim'
        self.preset = preset
        specs = dict(PRESETS[preset])
        overrides = dict(overrides or {})
        for name in list(overrides):
            if name not in ACTION_NAMES:
                self.errors.append(f'unknown action keys.{name}')
                del overrides[name]
        specs.update(overrides)
        self._sequences = {}
        # The reviewer's own keys are bound last, so they win over the
        # preset's.
        for name in sorted(specs, key=lambda name: name in overrides):
            try:
                sequences = key_sequences(specs[name])
            except ValueError as error:
                self.errors.append(f'keys.{name}: {error}')
                continue
            for sequence in sequences:
                self._sequences[sequence] = name
        self._prefixes = {sequence[:end] for sequence in self._sequences
                          for end in range(1, len(sequence))}
        self._labels = {}
        for name, spec in specs.items():
            alternatives = [part.strip() for part in spec.split(',') if part.strip()]
            self._labels[name] = [alternative for alternative in alternatives
                                  if self._is_bound(alternative, name)]
        prefix = COUNT_PREFIXES.get(preset)
        self._count_key = key_codes(prefix)[0] if prefix else None
        self._pending = ()
        self._count = ''
        self._counting = False

    def _is_bound(self, alternative, name):
        try:
            return all(name == self._sequences.get(sequence)
                       for sequence in key_sequences(alternative))
        except ValueError:
            return False

    def labels(self, name):
        """The keys of an action for display, like ['j', 'Down']."""
        return self._labels.get(name, [])

    def label(self, name):
        """The first key of an action for display, '' if it has none."""
        labels = self.labels(name)
        return key_label(labels[0]) if labels else ''

    def typed(self):
        """What is typed so far of a count or sequence, for display."""
        return self._count + ''.join(chr(code) if code < 0x110000 and chr(code).isprintable()
                                     else '' for code in self._pending)

    def reset(self):
        """Forgets a count or sequence typed so far."""
        self._pending, self._count, self._counting = (), '', False

    def feed(self, code):
        """Takes one key code. Returns PENDING while a count or sequence
        goes on, else (action or None, count or None).
        """
        if 27 == code and (self._pending or self._count or self._counting):
            # Esc cancels a count or sequence, like in vim
            self.reset()
            return PENDING
        takes_digits = self._count_key is None or self._counting
        if takes_digits and not self._pending and (
                ord('1') <= code <= ord('9') or (ord('0') == code and self._count)):
            self._count += chr(code)
            return PENDING
        if code == self._count_key and not self._pending and not self._counting:
            self._counting = True
            return PENDING
        self._counting = False
        sequence = self._pending + (code,)
        if sequence in self._prefixes:
            self._pending = sequence
            return PENDING
        self._pending = ()
        count = int(self._count) if self._count else None
        self._count = ''
        return self._sequences.get(sequence), count

    def footer(self, entries):
        """Footer text from entries ((action, ...), label): "j/k move"."""
        parts = []
        for names, label in entries:
            keys = '/'.join(filter(None, (self.label(name) for name in names)))
            if keys:
                parts.append(f'{keys} {label}')
        return '  '.join(parts)

    def help_lines(self, width):
        """The help screen: every action with its keys, by group."""
        lines = [f'plustwo: keys ({self.preset})', '']
        group = None
        key_width = 14
        for name, action_group, _label, description in ACTIONS:
            if action_group != group:
                if group:
                    lines.append('')
                lines.append(action_group)
                group = action_group
            keys = ', '.join(key_label(label) for label in self.labels(name)) or '-'
            wrapped = textwrap.wrap(description, max(width - key_width - 4, 20))
            lines.append(f'  {keys:<{key_width - 1}} {wrapped[0]}')
            lines += [' ' * (key_width + 2) + line for line in wrapped[1:]]
        lines += ['', 'Mouse: a click selects, a double click opens, the wheel scrolls.',
                  'Comment box: Enter saves, ' + ('M-Enter starts a new line, C-g cancels, '
                                                  'C-x C-e continues in your editor.'
                                                  if 'emacs' == self.preset else
                                                  'Alt-Enter starts a new line, Esc cancels, '
                                                  '^E continues in your editor.'),
                  'Comparing two patch sets, lines that differ because the changes below '
                  'changed',
                  'have a blue background, purple where the change itself edited them too.',
                  'Keys: plustwo config --global keys.preset vim|emacs|gerrit, or one action',
                  'like keys.reply = R.']
        return lines


def keymap_from_settings(core):
    """The keymap the settings ask for."""
    overrides = core.settings_section('keys')
    preset = overrides.pop('preset', 'vim')
    return Keymap(preset, overrides)


# Colours of vote values
VOTE_COLOURS = {2: 2, 1: 2, -1: 1, -2: 1}

STACK_FOOTER = ((('down', 'up'), 'move'), (('open',), 'open'), (('diff',), 'diff'),
                (('vote',), 'vote'), (('verified',), 'needs Verified'), (('rebase',), 'rebase'),
                (('submit',), 'submit'), (('reload',), 'reload'), (('help',), 'help'),
                (('quit',), 'quit'))


def run(core):
    """Starts the interface. core is the plustwo module."""
    curses.wrapper(lambda screen: Interface(screen, core).loop())


class Interface:
    """The running interface: screen stack, drawing and keys."""

    def __init__(self, screen, core):
        self._screen = screen
        self._core = core
        self._views = [StackView(self)]
        self._message = ''
        self._version = None
        self.keymap = keymap_from_settings(core)
        if self.keymap.errors:
            self._message = 'Settings: ' + '; '.join(self.keymap.errors)
        curses.curs_set(0)
        curses.set_escdelay(ESC_DELAY_MS)
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
        curses.mouseinterval(300)
        curses.use_default_colors()
        for number in range(1, 9):
            curses.init_pair(number, number if number < 8 else 8, -1)
        screen.timeout(RELOAD_INTERVAL_MS)
        self.colour_pairs = ColourPairs()
        # Index into CONTEXT_STEPS, kept for all changes
        self.context_step = 0

    # ── state shared by the views ─────────────────────────────────────────

    @property
    def core(self):
        """The plustwo module with the review logic."""
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
                self.keymap.reset()
                self._handle_mouse()
                continue
            result = self.keymap.feed(self._with_meta(key))
            if PENDING == result:
                continue
            action, count = result
            if action is None:
                continue
            if not self._handle_global(action):
                try:
                    self._views[-1].action(action, count)
                except self._core.ChangeError as error:
                    self.say(str(error))
            if not self._views:
                return

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
        if 'quit' == action:
            # Quitting is easy by mistake, e.g. q instead of a tab
            if ord('y') == self.ask_key('Quit plustwo? (y/N)'):
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
        key_lines = textwrap.wrap(self.keymap.footer(view.footer()), max(width - 1, 20),
                                  break_on_hyphens=False)[:2]
        view.draw(Area(self._screen, 0, height - 1 - len(key_lines), width))
        for offset, line in enumerate(key_lines):
            self.text(height - 1 - len(key_lines) + offset, 0, line, curses.A_DIM, width)
        self.text(height - 1, 0, self._message, curses.A_BOLD, width)
        # The typed prefix in the bottom right corner, like vim's showcmd
        prefix = self.keymap.typed()
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
                          + 'any other key closes', curses.A_DIM)
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

    def write_text(self, title, initial='', anchor=None):
        """Lets the reviewer write a comment in a small box, so the diff
        stays visible. Returns the text, '' if left empty, None if
        cancelled.

        anchor is a DiffGap: the diff makes room for the box below a row,
        as high as the box grows, and moves the rows below down. Or anchor
        is (row, left, width): the box goes over the screen below that
        row, or above it when there is no room. Without one it sits above
        the bottom line, as wide as the screen.

        Enter saves, Alt-Enter starts a new line, Esc or C-g cancels, ^E
        continues in the editor. With the emacs keys, C-e goes to the end of
        the line, and C-x C-e continues in the editor.
        """
        emacs = 'emacs' == self.keymap.preset
        box = TextBox(initial, emacs)
        curses.curs_set(1)
        self._screen.timeout(-1)
        try:
            while True:
                place = anchor
                if isinstance(anchor, DiffGap):
                    # The box's height depends on its width, which the gap
                    # gives: once to learn the width, once with the height.
                    place = anchor.place(TEXT_BOX_ROWS + 2)
                    place = anchor.place(text_box_height(box, place[2]))
                self._draw(refresh=False)
                self._draw_text_box(box, title, place)
                self._screen.refresh()
                key = self._screen.get_wch()
                if key in ('\n', '\r', curses.KEY_ENTER):
                    return box.text.strip()
                if '\x07' == key:
                    return None
                if emacs and '\x18' == key:
                    # C-x C-e continues in the editor, like in a shell
                    if '\x05' == self._screen.get_wch():
                        return self.edit_text(box.text)
                    continue
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
                elif '\x05' == key and not emacs:
                    return self.edit_text(box.text)
                else:
                    box.key(key)
        finally:
            if isinstance(anchor, DiffGap):
                anchor.close()
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
        hint = (' Enter save  M-Enter new line  C-g cancel  C-x C-e editor '
                if box.emacs else ' Enter save  Alt-Enter new line  Esc cancel  ^E editor ')
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
                if key in ('\x1b', '\x07'):
                    return None
                if key in ('\x7f', '\b', curses.KEY_BACKSPACE):
                    answer = answer[:-1]
                elif isinstance(key, str) and key.isprintable():
                    answer += key
        finally:
            curses.curs_set(0)
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


def text_box_height(box, width):
    """Rows the comment box takes at a width, with its frame."""
    return min(len(box.rows(max(width, 20) - 2)), TEXT_BOX_ROWS) + 2


class DiffGap:
    """Room for the comment box in the diff, below row `index` (-1: above
    the first row). While the box is open, the rows below move down by its
    height, so it hides nothing.
    """

    def __init__(self, diff, layout, index):
        self._diff = diff
        self._top, self._left, self._height, self._width = layout
        self._index = index

    def place(self, box_height):
        """Opens the gap for a box this high, scrolls it into view and
        returns where the box goes: (row above it, left, width).
        """
        self._diff.gap = (self._index, box_height)
        if self._index < 0:
            self._diff.scroll = 0
        else:
            self._diff.make_room_below(self._index, self._height, box_height)
        return self._top + self._index - self._diff.scroll, self._left, self._width

    def close(self):
        """Closes the gap again."""
        self._diff.gap = None


class TextBox:
    """The text and cursor of the comment box, independent of the screen.

    Lines wrap at the box's width, by character, so every position of the
    text has one place on the screen.
    """

    def __init__(self, text='', emacs=False):
        self.text = text
        self.position = len(text)
        # Whether Emacs' editing keys work: C-a, C-e, C-f, C-b, C-k, C-d
        self.emacs = emacs

    def insert(self, characters):
        """Inserts at the cursor."""
        self.text = self.text[:self.position] + characters + self.text[self.position:]
        self.position += len(characters)

    def key(self, key):
        """Handles an editing key from get_wch()."""
        if self.emacs:
            key = {'\x06': curses.KEY_RIGHT, '\x02': curses.KEY_LEFT, '\x05': curses.KEY_END,
                   '\x04': curses.KEY_DC}.get(key, key)
        before, after = self.text[:self.position], self.text[self.position:]
        if self.emacs and '\x0b' == key:
            # C-k deletes to the end of the line, or the line end itself
            end = self.text.find('\n', self.position)
            end = len(self.text) if -1 == end else end
            self.text = before + self.text[max(end, self.position + 1)
                                           if end == self.position else end:]
        elif key in ('\x7f', '\b', curses.KEY_BACKSPACE):
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
    """Status shown for a thread: its status, or draft if unsent. With a
    draft that changes the status, the status it will have.
    """
    if comment.is_draft():
        return 'draft' + (' resolved' if 'resolved' == comment.draft_status() else '')
    after = comment.draft_status()
    if after and after != comment.status:
        return f'{comment.status} → {after} (draft)'
    return comment.status + (' +draft' if comment.has_draft() else '')


# Actions that move in a list
LIST_ACTIONS = ('down', 'up', 'top', 'bottom', 'page-down', 'page-up')
# How far page-down and page-up move in a list
LIST_PAGE = 10


def wheel_action(kind):
    """The action a wheel turn stands for: down, up, None otherwise."""
    return {WHEEL_DOWN: 'down', WHEEL_UP: 'up'}.get(kind)


def move(index, action, length, count=None):
    """New list position after down, up, top, bottom or a page, staying
    inside the list. A count moves that many rows, or with top and bottom
    goes to that row.
    """
    if not length:
        return 0
    steps = {'down': count or 1, 'up': -(count or 1), 'page-down': LIST_PAGE,
             'page-up': -LIST_PAGE}.get(action)
    if steps is not None:
        return max(0, min(index + steps, length - 1))
    if action in ('top', 'bottom'):
        last = 0 if 'top' == action else length - 1
        return min(count - 1, length - 1) if count else last
    return index


class StackView:
    """The stacks in review, one per topic, like Gerrit's dashboard."""

    @staticmethod
    def footer():
        """The footer's actions, see Keymap.footer()."""
        return STACK_FOOTER

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
        """Reads the stacks again and lays out the rows: a heading per stack,
        then its changes.
        """
        core = self._interface.core
        self._changes = core.changes_in_stack()
        self._repository = core.repository_name('.')
        self._base = core.base_branch()
        self._selected = min(self._selected, max(len(self._changes) - 1, 0))
        self._rows = []
        topic = None
        for index, change in enumerate(self._changes):
            if change.topic != topic:
                topic = change.topic
                self._rows.append(stack_heading(core, topic, self._changes,
                                                self._interface.keymap.label('rebase')))
            self._rows.append(index)

    def draw(self, area):
        """Draws the stacks and their changes."""
        out = self._interface.text
        core = self._interface.core
        out(0, 1, f'{self._repository}: {len(self._changes)} changes in review, base {self._base}',
            curses.A_BOLD)
        out(2, 1, f'{"#":>4}  {"Change":<9} {"State":<10} {"PS":<5} {"Subject":<44} {"CR":>2} '
            f'{"V":>2}  {"Turn":<11} Comments', curses.A_UNDERLINE)
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
            out(row, 28, f'{core.version_label(change):<5} {change.subject:<44.44} ', attributes)
            for column, label in ((79, core.CODE_REVIEW), (82, core.VERIFIED)):
                out(row, column, f'{core.vote_label(change, label):>2}',
                    attributes | vote_attribute(change.label_value(label)))
            turn = change.turn()
            out(row, 84, f'  {TURN_LABELS[turn]:<11}', attributes | (
                curses.A_BOLD if turn in MY_TURNS else curses.A_DIM))
            out(row, 97, f' {core.comment_counts(change)}', attributes)
        if not self._changes:
            out(self.TOP, 1, 'Nothing in review. plustwo upload uploads the stack of a '
                'branch.')

    def mouse(self, row, _column, kind):
        """A click selects a change, a double click opens it."""
        if wheel_action(kind):
            self.action(wheel_action(kind))
            return
        offset = row - self.TOP
        if 0 <= offset < len(self._rows) and not isinstance(self._rows[offset], str):
            self._selected = self._rows[offset]
            if DOUBLE_CLICK == kind:
                self.action('open')

    def action(self, action, count=None):
        """Handles an action of the stack view."""
        self._selected = move(self._selected, action, len(self._changes), count)
        if not self._changes:
            return
        change = self._changes[self._selected]
        if 'open' == action:
            self._interface.open(ChangeView(self._interface, change.change_id))
        elif 'diff' == action:
            self._interface.run_outside(self._interface.core.diff_command(change))
        elif 'submit' == action:
            submit_with_confirmation(self._interface, change)
        elif 'rebase' == action:
            rebase_with_confirmation(self._interface, change)
        elif 'next-turn' == action:
            later = [index for index in range(self._selected + 1, len(self._changes))
                     if self._changes[index].turn() in MY_TURNS]
            earlier = [index for index in range(self._selected + 1)
                       if self._changes[index].turn() in MY_TURNS]
            if later or earlier:
                self._selected = (later or earlier)[0]
            else:
                self._interface.say('It is your turn nowhere.')
        elif 'approve' == action:
            approve(self._interface, change)
        else:
            vote_action(self._interface, change, action)


# The turn as the stack list shows it, and the turns that are the reviewer's
TURN_LABELS = {'reviewer': '▶ yours', 'author': 'author', 'submittable': '▶ submit'}
MY_TURNS = ('reviewer', 'submittable')


def approve(interface, change):
    """Code-Review+2 on the latest patch set, sending the drafts along if
    the reviewer wants.
    """
    core = interface.core
    core.set_vote(change.change_id, core.CODE_REVIEW, 2)
    drafts = sum(not message.sent for comment in change.comments for message in comment.messages)
    sent = ''
    if drafts and ord('y') == interface.ask_key(f'{drafts} draft(s) unsent. Send them along? (y/N)'):
        core.send_drafts(change.change_id)
        sent = f', {drafts} draft(s) sent'
    interface.after_write()
    interface.say(f'Code-Review+2 on PS{change.latest_patch_set()}{sent}.')


def stack_heading(core, topic, changes, rebase_key='B'):
    """A stack's heading in the list: its topic and whether it needs a
    rebase, which rebase_key does.
    """
    if any(change.rebase_requested for change in changes if change.topic == topic):
        return f'{topic}  (conflicts with {core.base_branch()}, the author rebases)'
    if core.needs_rebase(topic):
        return f'{topic}  ({core.base_branch()} moved on, {rebase_key} rebases)'
    return topic


def rebase_with_confirmation(interface, change):
    """B: rebases the change's stack onto the base branch, after asking.
    On a conflict the author is asked to do it.
    """
    core = interface.core
    if not core.needs_rebase(change.topic):
        interface.say(f'{change.topic} is on {core.base_branch()} already.')
        return
    if not interface.confirm([f'Rebase the stack {change.topic} onto {core.base_branch()}.',
                              'Every change gets a new patch set.'], 'Rebase?'):
        return
    rebased = core.rebase_stack(change.topic)
    interface.after_write()
    interface.say(f'Rebased, {len(rebased)} new patch set(s).' if rebased is not None
                  else 'Conflicts: the author is asked to rebase.')


def vote_attribute(value):
    """Colour for a vote's value."""
    return curses.color_pair(VOTE_COLOURS.get(value, 0)) | (curses.A_BOLD if abs(value) > 1 else 0)


def vote_action(interface, change, action):
    """vote votes Code-Review, verified switches whether Verified+1 is
    needed.
    """
    core = interface.core
    if 'vote' == action:
        if not change.patch_sets:
            interface.say(f'{change.short_id()} has no patch set yet, nothing to vote on.')
            return
        answer = interface.ask(f'Code-Review on PS{change.latest_patch_set()} (+2 +1 0 -1 -2):')
        if not answer:
            return
        if not re.fullmatch(r'[+-]?[0-2]', answer):
            interface.say('A vote is one of +2, +1, 0, -1, -2.')
            return
        number = core.set_vote(change.change_id, core.CODE_REVIEW, int(answer))
        interface.after_write()
        interface.say(f'Code-Review{int(answer):+d} on PS{number}.' if int(answer)
                      else 'Code-Review vote removed.')
    elif 'verified' == action:
        core.require_verified(change.change_id, not change.verified_required)
        interface.after_write()
        interface.say('Needs Verified+1 now.' if not change.verified_required
                      else 'Verified+1 is not needed any more.')


def submit_with_confirmation(interface, change):
    """Asks before submitting. Lists the changes that get submitted along
    and the unresolved threads. Says first what is missing, if
    a change lacks votes or an upload.
    """
    core = interface.core
    submitted = core.changes_submitted_with(change)
    problems = [problem for other in submitted for problem in other.submit_problems()]
    if problems:
        interface.show_lines(['Cannot submit yet:', ''] + [f'  {problem}' for problem in problems]
                             + ['', 'v gives Code-Review. Verified comes from a check calling',
                                '  plustwo vote CHANGE Verified +1'])
        return
    lines = [f'Submit to {core.base_branch()}:']
    lines += [f'  {other.short_id()}  {other.subject}' for other in submitted]
    unfinished = [(other, comment) for other in submitted
                  for comment in other.unfinished_comments()]
    lines.append('')
    if unfinished:
        lines.append('These comments are unresolved:')
        lines += [f'  {other.short_id()} #{comment.number} [{comment.status}] {comment.place_label()}: '
                  f'{(comment.messages[0].text.splitlines() or [""])[0]}' for other, comment in unfinished]
    else:
        lines.append('All comments are resolved.')
    drafts = sum(comment.has_draft() for other in submitted for comment in other.comments)
    if drafts:
        lines += ['', f'{drafts} draft(s) are not sent and stay unsent.']
    if interface.confirm(lines, 'Submit?'):
        core.submit(change.change_id, force=True)
        interface.after_write()
        interface.say('Submitted: ' + ', '.join(other.short_id() for other in submitted))


# ── the diff pane ─────────────────────────────────────────────────────────

# An escape sequence in bat's output: colours and styles (SGR, ending in m),
# other control sequences, and hyperlinks (OSC 8). Only SGR is used.
ESCAPE_PATTERN = re.compile(r'\x1b\[([0-9;]*)([A-Za-z])|\x1b\]8;[^\x1b\x07]*(?:\x1b\\|\x07)')

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
    combination the diff uses gets one, the first time it shows up.
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


# Backgrounds of removed and added lines: (line, changed characters). The
# greens are the ones the reviewer's delta used, the reds delta's defaults.
REMOVED_BACKGROUNDS = (palette_index(0x3f, 0x00, 0x01), palette_index(0x90, 0x10, 0x11))
ADDED_BACKGROUNDS = (palette_index(0x1f, 0x4d, 0x2a), palette_index(0x2f, 0x7a, 0x40))
# The same for terminals with fewer colours: red and green
REMOVED_BASIC = 1
ADDED_BASIC = 2
TAB_WIDTH = 4
# Columns between the halves of a row: a line
HALF_SEPARATOR = '│'


def highlighter():
    """The program that colours source code: bat (batcat on Debian), None
    without it.
    """
    return shutil.which('bat') or shutil.which('batcat')


def highlighted(lines, path):
    """Colours of the lines of a file as syntax highlighting gives them:
    for each line a list of (foreground, styles) per character. None
    without bat, or if its output doesn't fit the lines.
    """
    program = highlighter()
    if not program or not lines:
        return None
    result = subprocess.run([program, '--color=always', '--style=plain', '--paging=never',
                             '--wrap=never', f'--file-name={path}'],
                            input='\n'.join(lines) + '\n', capture_output=True, text=True,
                            errors='replace')
    output = result.stdout.split('\n')
    if 0 != result.returncode or len(output) < len(lines):
        return None
    colours = []
    for line, rendered in zip(lines, output):
        segments = styled_segments(rendered)
        if ''.join(segment[0] for segment in segments) != line:
            return None
        colours.append([(foreground, styles) for text, foreground, _, styles in segments
                        for _ in text])
    return colours


def half_cells(core, half, colours, marks):
    """The characters of one half of a row as (character, foreground,
    background, styles), tabs expanded and control characters made visible.
    colours are the line's syntax colours or None. marks are the sets of
    line numbers (from below, partly from below) on this side.
    """
    if half.kind in (core.REMOVED, core.ADDED):
        if half.number in marks[0] or half.number in marks[1]:
            partly = half.number not in marks[0]
            backgrounds = ((PARTLY_BASIC,) * 2 if partly else (FROM_BELOW_BASIC,) * 2) \
                if curses.COLORS < 256 else (PARTLY_BACKGROUNDS if partly
                                             else FROM_BELOW_BACKGROUNDS)
        elif curses.COLORS < 256:
            backgrounds = ((REMOVED_BASIC,) if core.REMOVED == half.kind else (ADDED_BASIC,)) * 2
        else:
            backgrounds = REMOVED_BACKGROUNDS if core.REMOVED == half.kind else ADDED_BACKGROUNDS
    else:
        backgrounds = (-1, -1)
    changed = set()
    for begin, end in half.edits:
        changed.update(range(begin, end))
    cells = []
    text = half.text[:-1] if half.text.endswith('\r') else half.text
    for index, character in enumerate(text):
        foreground, styles = colours[index] if colours and index < len(colours) else (-1, 0)
        background = backgrounds[index in changed]
        if '\t' == character:
            cells += [(' ', foreground, background, styles)] * (TAB_WIDTH - len(cells) % TAB_WIDTH)
        elif ord(character) < 32 or 127 == ord(character):
            cells.append(('·', foreground, background, styles | curses.A_DIM))
        else:
            cells.append((character, foreground, background, styles))
    return cells, backgrounds[0]


def grouped(cells):
    """Joins cells of the same colours into (text, foreground, background,
    styles) segments.
    """
    segments = []
    for character, *attributes in cells:
        if segments and segments[-1][1:] == tuple(attributes):
            segments[-1] = (segments[-1][0] + character, *attributes)
        else:
            segments.append((character, *attributes))
    return segments


def rendered_rows(core, rows, width, colours, marks):
    """Draws side by side rows into screen rows like Gerrit's diff: old
    number, old text, new number, new text. A long line wraps onto more
    rows. Returns [(segments, (old number, new number), continues,
    changed, gap)], where continues says that the row goes on with the line
    of the row above, changed that it has removed or added lines, and gap is
    the first old line of a gap row, else None.
    """
    digits = max([4] + [len(str(half.number)) for row in rows for half in (row.old, row.new)
                        if half.number])
    half_width = max((width - len(HALF_SEPARATOR)) // 2, digits + 2)
    text_width = half_width - digits - 1
    result = []
    for row in rows:
        if row.skipped:
            label = f'… {row.skipped} common line{"s" if row.skipped > 1 else ""} …'
            result.append(([(label.center(width), -1, -1, curses.A_DIM)],
                           (None, None), False, False, row.old.number))
            continue
        changed = (core.CONTEXT, core.CONTEXT) != (row.old.kind, row.new.kind)
        halves = []
        for half, side in ((row.old, 0), (row.new, 1)):
            line_colours = (colours[side][half.number - 1]
                            if half.number and colours[side] else None)
            cells, background = half_cells(core, half, line_colours, marks[side])
            pieces = [cells[start:start + text_width]
                      for start in range(0, len(cells), text_width)] or [[]]
            halves.append((half, pieces, background))
        for piece in range(max(len(pieces) for _, pieces, _ in halves)):
            segments = []
            for side, (half, pieces, background) in enumerate(halves):
                number = f'{half.number:>{digits}} ' if half.number and 0 == piece else \
                    ' ' * (digits + 1)
                segments.append((number, -1, -1, curses.A_DIM))
                cells = pieces[piece] if piece < len(pieces) else []
                padding = text_width - len(cells)
                fill = background if piece < len(pieces) else -1
                segments += grouped(cells + [(' ', -1, fill, 0)] * padding)
                if 0 == side:
                    segments.append((HALF_SEPARATOR, -1, -1, curses.A_DIM))
            numbers = (row.old.number, row.new.number) if 0 == piece else (None, None)
            result.append((segments, numbers, 0 != piece, changed, None))
    return result


class DiffPane:
    """The diff of one file, side by side like in Gerrit, drawn inside the screen,
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
        # Whether a diff row goes on with the line of the row above (wrapped),
        # and whether it has removed or added lines
        self._continues = []
        self._changed = []
        # Syntax colours per (version, path), see _colours()
        self._colour_cache = {}
        # Gaps of common lines the reviewer opened, by their first old line
        self.expanded_gaps = set()
        self._gaps = []
        # The side of the diff the cursor is on, comments go there
        self.side = 'new'
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
        # (row index, height) of the comment box's room below that row, or
        # None, see DiffGap
        self.gap = None
        # Whether the diff has lines from below, and lines partly from below
        self.marks_from_below = (False, False)

    def show(self, core, change, compare, path, width, context):
        """Renders the diff of a file with context lines around the changes
        (None: the whole file), unless it is rendered already. The threads
        come with arrange(), call it after this.
        """
        if self._path != path:
            self.expanded_gaps = set()
        key = (path, core.versions_to_compare(change, *compare), width, context,
               frozenset(self.expanded_gaps))
        if key == self._key:
            return
        is_other_file = self._path != path
        self._key = key
        self._path = path
        old, new, paths = core.diff_endpoints(change, *compare, path=path)
        rows = core.file_diff_rows(change, *compare, path=path, context=context)
        from_below, partly = core.rebase_lines(change, *compare, path=path)
        marks = ((from_below[0], partly[0]), (from_below[1], partly[1]))
        if rows is None:
            rendered = [([('Binary file, not shown.', -1, -1, curses.A_DIM)], (None, None), False,
                          False, None)]
        elif not rows:
            rendered = [([('No differences in this file.', -1, -1, curses.A_DIM)], (None, None),
                          False, False, None)]
        else:
            if self.expanded_gaps and any(row.skipped for row in rows):
                rows = core.expand_gaps(rows, self.expanded_gaps,
                                        core.file_lines(old, paths[0]), core.file_lines(new, paths[0]))
            colours = [self._colours(core, version, paths[0]) for version in (old, new)]
            rendered = rendered_rows(core, rows, width, colours, marks)
        self._gaps = [gap for *_, gap in rendered]
        self._diff_lines = [segments for segments, *_ in rendered]
        previous_numbers = self._diff_numbers
        self._diff_numbers = [numbers for _, numbers, *_ in rendered]
        self._continues = [continues for _, _, continues, *_ in rendered]
        self._changed = [changed for _, _, _, changed, _ in rendered]
        marked = [any(numbers[side] in marks[side][kind]
                      for numbers in self._diff_numbers for side in (0, 1)) for kind in (0, 1)]
        self.marks_from_below = tuple(marked)
        if not is_other_file:
            if self.rows and not self._keep:
                self._keep = (self.rows[self.cursor], self._anchors[self.cursor])
            if self._keep:
                self._keep = self._renumbered(self._keep, previous_numbers)
        if is_other_file:
            first = next((index for index, numbers in enumerate(self._diff_numbers)
                          if any(numbers)), 0)
            self._keep = (('diff', first), first)
            # A new file starts at its top, file threads and all.
            self.cursor = self.scroll = 0
            self._from_top = True
        self._threads_key = None

    def _colours(self, core, version, path):
        """Syntax colours of a file's version, see highlighted(). Kept per
        version, so moving around doesn't run bat again.
        """
        key = (version, path)
        if key not in self._colour_cache:
            self._colour_cache[key] = highlighted(core.file_lines(version, path) or [], path)
        return self._colour_cache[key]

    def _renumbered(self, keep, previous_numbers):
        """Moves a kept row to the new diff lines, by its line numbers. With
        more or less context, the same line has another index.
        """
        def new_index(index):
            if not 0 <= index < len(previous_numbers):
                return index
            old, new = previous_numbers[index]
            if old is None and new is None:
                return min(index, max(len(self._diff_numbers) - 1, 0))
            column, line = (1, new) if new is not None else (0, old)
            return next((other for other, numbers in enumerate(self._diff_numbers)
                         if numbers[column] is not None and numbers[column] >= line),
                        max(len(self._diff_numbers) - 1, 0))

        row, anchor = keep
        if 'diff' == row[0]:
            row = ('diff', new_index(row[1]))
        return row, new_index(anchor) if anchor >= 0 else anchor

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
        # Threads on lines the diff doesn't show come next, so none gets lost.
        for place in sorted(place for place in blocks if place not in self.shown_places()):
            add_threads(place, -1)
        # A line's threads come below its last row, after the rows it
        # wrapped onto.
        line = None
        for index, (old, new) in enumerate(self._diff_numbers):
            if line and not self._continues[index]:
                add_threads(('new', line[1]), line[2])
                add_threads(('old', line[0]), line[2])
            if not self._continues[index]:
                line = (old, new, index)
            self.rows.append(('diff', index))
            self.lines.append(self._diff_lines[index])
            self.numbers.append((old, new))
            self._anchors.append(index)
        if line:
            add_threads(('new', line[1]), line[2])
            add_threads(('old', line[0]), line[2])
        self._place_cursor(offset)

    def shown_places(self):
        """The (side, line) places the rendered diff shows."""
        return ({('new', new) for _, new in self._diff_numbers if new is not None}
                | {('old', old) for old, _ in self._diff_numbers if old is not None})

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

    def gap_at(self, index):
        """The first old line of the gap row at index, None on other rows."""
        row = self.rows[index] if index < len(self.rows) else ('thread',)
        return self._gaps[row[1]] if 'diff' == row[0] and row[1] < len(self._gaps) else None

    def segments(self, index, highlighted):
        """The parts of a row as (text, curses attribute). The cursor row
        is highlighted with lighter backgrounds, on the cursor's side only.
        """
        result = []
        parts = self.lines[index]
        separator = next((number for number, part in enumerate(parts)
                          if HALF_SEPARATOR == part[0]), None)
        for number, (text, foreground, background, styles) in enumerate(parts):
            on_side = separator is None or (number < separator if 'old' == self.side
                                            else number > separator)
            if highlighted and on_side:
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

    def go_to_chunk(self, forward, height):
        """Puts the cursor on the first row of the next (or previous)
        changed lines, like Gerrit's n and p. Returns whether there is one.
        """
        starts = [index for index, row in enumerate(self.rows)
                  if 'diff' == row[0] and self._changed[row[1]]
                  and not (row[1] and self._changed[row[1] - 1])]
        return self._jump_to_one_of(starts, forward, height)

    def go_to_next_thread(self, forward, height):
        """Puts the cursor on the first row of the next (or previous)
        thread. Returns whether there is one.
        """
        starts = [index for index, row in enumerate(self.rows)
                  if 'thread' == row[0] and 0 == row[2]]
        return self._jump_to_one_of(starts, forward, height)

    def _jump_to_one_of(self, indexes, forward, height):
        """Jumps to the nearest of these rows after (or before) the cursor."""
        candidates = ([index for index in indexes if index > self.cursor] if forward
                      else [index for index in indexes if index < self.cursor])
        if not candidates:
            return False
        self._jump(candidates[0] if forward else candidates[-1], height)
        return True

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

    def line_end(self, index):
        """The last row of the line at row index, after the rows it wrapped
        onto.
        """
        while (index + 1 < len(self.rows) and 'diff' == self.rows[index + 1][0]
               and self._continues[self.rows[index + 1][1]]):
            index += 1
        return index

    def cursor_place(self):
        """(line, side) of the cursor line, on the cursor's side if the line
        exists there, else on the other side. None on rows without numbers.
        """
        index = self.cursor
        # A wrapped row belongs to the line above.
        while (0 < index < len(self.rows) and 'diff' == self.rows[index][0]
               and self._continues[self.rows[index][1]]):
            index -= 1
        old, new = self.numbers[index] if self.numbers else (None, None)
        if 'old' == self.side and old is not None:
            return old, 'old'
        if new is not None:
            return new, 'new'
        if old is not None:
            return old, 'old'
        return None


# Columns a thread is indented by in the diff
THREAD_INDENT = 3


def thread_rows(comment, width, expanded, core, note=''):
    """A thread as rows of the diff, each a list of (text, foreground,
    background, styles). Folded it is one row with the start of the first
    message. note goes into the title, e.g. that its line isn't shown.
    """
    label = comment_label(comment)
    colour = STATUS_COLOURS['draft' if comment.is_draft() else comment.status]
    pad = ' ' * THREAD_INDENT
    title = (f'#{comment.number} {label}' + (f'  {note}' if note else ''), colour, -1,
             curses.A_BOLD)
    if not expanded:
        first = core.message_text(comment.messages[0]).split('\n')[0] if comment.messages else ''
        return [[(pad + '▸ ', -1, -1, curses.A_DIM), title, (f'  {first}', -1, -1, curses.A_DIM)]]
    inner = max(width - THREAD_INDENT - 2, 20)
    rows = [[(pad + '┌ ', -1, -1, curses.A_DIM), title,
             (' ' + '─' * max(inner - len(title[0]) - 2, 0), -1, -1, curses.A_DIM)]]
    for message in comment.messages:
        name = ('Reviewer' if core.REVIEWER == message.author else 'Author') + (
            ' (draft)' if not message.sent else '')
        name_colour = STATUS_COLOURS['draft'] if not message.sent else -1
        prefix = f'{name}: '
        lines = [line for paragraph in core.message_text(message).split('\n')
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

PANE_FOOTERS = {
    FILES: ((('down', 'up'), 'file'), (('next-pane',), 'next pane'),
            (('comment',), 'comment on file'), (('back',), 'back')),
    COMMENTS: ((('down', 'up'), 'thread'), (('open',), 'go to thread'), (('reply',), 'reply'),
               (('done',), 'Done'), (('ack',), 'Ack'), (('resolve',), 'resolved'),
               (('back',), 'back')),
    DIFF: ((('down', 'up'), 'line'), (('top', 'bottom'), 'top/bottom'),
           (('page-down', 'page-up'), 'half page'), (('next-chunk', 'prev-chunk'), 'chunk'),
           (('next-thread', 'prev-thread'), 'thread'), (('next-file', 'prev-file'), 'file'),
           (('comment',), 'comment on line'), (('file-comment',), 'on file'),
           (('more-context', 'less-context'), 'context'), (('open',), 'fold thread'),
           (('fold-all',), 'fold all'), (('reply',), 'reply'), (('edit-draft',), 'edit draft'),
           (('done',), 'Done'), (('ack',), 'Ack'), (('resolve',), 'resolved'),
           (('back',), 'files')),
}
CHANGE_FOOTER = ((('send',), 'send'), (('vote',), 'vote'), (('verified',), 'needs Verified'),
                 (('patch-sets',), 'patch sets'), (('vimdiff',), 'vimdiff'),
                 (('rebase',), 'rebase'), (('submit',), 'submit'), (('help',), 'help'))


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

    def footer(self):
        """The focused pane's actions, then the ones of the whole change."""
        return PANE_FOOTERS[self._focus] + CHANGE_FOOTER

    def reload(self):
        """Reads the change again. Goes back if it is gone."""
        core = self._interface.core
        for change in core.changes_in_stack():
            if change.change_id == self._change_id:
                self._change = change
                # The commit message comes first, like in Gerrit.
                self._files = [core.COMMIT_MSG] + sorted(core.files_of(change.commit))
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
        header = f'Patch sets: {patch_sets}   {comparing}'
        out(1, 1, header, curses.A_DIM)
        self._draw_votes(4 + len(header))
        self._draw_files()
        self._draw_comments()
        self._draw_diff()

    def _draw_votes(self, column):
        """Draws the votes on the latest patch set after the header, like
        "Code-Review +2  Verified -1 ci: make check failed".
        """
        out = self._interface.text
        core = self._interface.core
        change = self._change
        for label in (core.CODE_REVIEW, core.VERIFIED):
            value = change.label_value(label)
            if not value and not (core.VERIFIED == label and change.verified_required):
                continue
            text = f'{label} {core.vote_label(change, label)}'
            out(1, column, text, vote_attribute(value) | (0 if value else curses.A_DIM))
            column += len(text) + 2
        # A failed check says why, in short.
        failed = [vote for vote in change.current_votes(core.VERIFIED) if vote.value < 0]
        if failed and failed[0].message:
            lines = failed[0].message.strip().split('\n')
            out(1, column, f'{failed[0].by}: {lines[-1]}', curses.A_DIM)

    def _draw_files(self):
        """Draws the file list."""
        out = self._interface.text
        core = self._interface.core
        reviewed = [core.is_reviewed(self._change, path) for path in self._files]
        self._frame(FILES, f'Files  {sum(reviewed)}/{len(self._files)} reviewed')
        top, left, height, width = self._layout[FILES]
        for index, path in enumerate(self._files[:height]):
            selected = index == self._file_index
            attributes = curses.A_REVERSE if selected and FILES == self._focus else (
                curses.A_BOLD if selected else 0)
            name = file_label(core, path)
            out(top + index, left, '✓ ' if reviewed[index] else '  ',
                attributes | curses.color_pair(2), left + width)
            out(top + index, left + 2, f'{name:<{width - 2}.{width - 2}}', attributes, left + width)

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
            place = ('Change' if self._interface.core.PATCHSET_LEVEL == comment.place
                     else comment.place_label().rsplit('/', 1)[-1])
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
        context = CONTEXT_STEPS[self._interface.context_step]
        context_label = 'whole file' if context is None else f'context {context}'
        if path:
            self._prepare_diff()
        below = ''.join(legend for legend, shown in zip(
            (', blue: from changes below', ', purple: partly from below'),
            self._diff.marks_from_below) if shown)
        self._frame(DIFF, f'{file_label(self._interface.core, path)}  ({context_label}{below})'
                    if path else 'Diff')
        top, left, height, width = self._layout[DIFF]
        if not path:
            return
        self._prepare_diff()
        markers = self._markers()
        gap_index, gap_height = self._diff.gap or (None, 0)
        # The comment box's room comes below the gap's row, where draw
        # leaves rows empty.
        row = gap_height if gap_index is not None and gap_index < self._diff.scroll else 0
        for index in range(self._diff.scroll, len(self._diff.lines)):
            if row >= height:
                break
            self._draw_diff_row(index, row, markers)
            row += 1 + (gap_height if index == gap_index else 0)

    def _draw_diff_row(self, index, row, markers):
        """Draws one row of the diff at a screen row of the pane."""
        out = self._interface.text
        top, left, _height, width = self._layout[DIFF]
        old, new = self._diff.numbers[index]
        has_thread = (('new', new) in markers) or (('old', old) in markers)
        if self._diff.thread_at(index) is not None:
            has_thread = False
        # The cursor line is highlighted in every pane, the > only shows
        # while the diff has the focus.
        is_cursor = index == self._diff.cursor
        gutter = '>' if is_cursor and DIFF == self._focus else ' '
        out(top + row, left, gutter, curses.A_BOLD)
        out(top + row, left + 1, '●' if has_thread else ' ', status_attribute('unresolved'))
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
        """Whether a thread shows all its messages in the diff. Unresolved
        ones and those with drafts do, unless folded by hand.
        """
        default = comment.is_unresolved() or comment.has_draft() or comment.is_draft()
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
        self._diff.show(core, self._change, self._compare, path, width,
                        CONTEXT_STEPS[self._interface.context_step])
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
            note = ''
            if place and place not in self._diff.shown_places():
                note = (f'on {"old " if "old" == comment.side else ""}line {line}, not in the '
                        f'diff ({self._interface.keymap.label("more-context")} shows more '
                        'context)')
            blocks.setdefault(place, []).append(
                (comment.number, thread_rows(comment, width, expanded, core, note)))
            signature.append((comment.number, comment.status, place, expanded,
                              tuple((message.author, message.text, message.sent, message.unresolved)
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
        return self._change.comment(numbers[0]) if numbers else None

    def _box_below_thread(self, comment):
        """Where the comment box goes for a reply: in a gap below the
        thread in the diff. None if the diff doesn't show the thread.
        """
        if not self._layout:
            return None
        end = self._diff.thread_end(comment.number)
        if end is None:
            return None
        return DiffGap(self._diff, self._layout[DIFF], end)

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
        comments = [self._change.comment(number) for number in numbers]
        expand = not any(self._is_expanded(comment) for comment in comments)
        for comment in comments:
            self._expanded[comment.number] = expand

    def _toggle_all_threads(self):
        """i: folds all threads if one is unfolded, else unfolds all."""
        comments = self._change.comments
        expand = not any(self._is_expanded(comment) for comment in comments)
        self._expanded = {comment.number: expand for comment in comments}

    # ── keys and mouse ────────────────────────────────────────────────────

    def action(self, action, count=None):
        """Handles an action of the change view. count is a typed count,
        e.g. the 15 of 15j, or None.
        """
        interface = self._interface
        core = interface.core
        change = self._change
        height = self._diff_height()
        diff_moves = {
            'down': lambda: self._diff.move_cursor(count or 1, height),
            'up': lambda: self._diff.move_cursor(-(count or 1), height),
            'top': lambda: (self._diff.go_to_new_line(count, height) if count
                            else self._diff.move_cursor(-self._diff.cursor, height)),
            'bottom': lambda: (self._diff.go_to_new_line(count, height) if count
                               else self._diff.move_cursor(len(self._diff.rows), height)),
            'page-down': lambda: self._diff.move_cursor(height // 2, height),
            'page-up': lambda: self._diff.move_cursor(-(height // 2), height),
        }
        if 'next-pane' == action:
            self._focus_pane(PANES[(PANES.index(self._focus) + 1) % len(PANES)])
        elif FILES == self._focus and action in LIST_ACTIONS:
            self._file_index = move(self._file_index, action, len(self._files), count)
        elif COMMENTS == self._focus and action in LIST_ACTIONS:
            self._comment_index = move(self._comment_index, action, len(change.comments), count)
            if self._selected_comment():
                self._show_comment_in_diff(self._selected_comment())
        elif DIFF == self._focus and action in diff_moves:
            diff_moves[action]()
        elif action in ('next-file', 'prev-file'):
            step = (count or 1) * (1 if 'next-file' == action else -1)
            self._file_index = max(0, min(self._file_index + step, len(self._files) - 1))
        elif action in ('next-chunk', 'prev-chunk', 'next-thread', 'prev-thread'):
            self._focus_pane(DIFF)
            # The diff may not be laid out for this file yet
            self._prepare_diff()
            forward = action.startswith('next')
            moved = (self._diff.go_to_chunk(forward, height) if action.endswith('chunk')
                     else self._diff.go_to_next_thread(forward, height))
            if not moved:
                interface.say('No more changes in this direction.' if action.endswith('chunk')
                              else 'No more threads in this direction.')
        elif 'open' == action:
            self._open()
        elif 'vimdiff' == action and self._selected_file():
            interface.run_outside(core.diff_command(change, *self._compare, tool=True,
                                                    path=self._selected_file()))
        elif 'patch-sets' == action:
            self._choose_patch_sets()
        elif 'fold-all' == action:
            self._toggle_all_threads()
        elif action in ('comment', 'open') and DIFF == self._focus and self._diff.gap_at(
                self._diff.cursor) is not None:
            # Like in Gerrit, a gap of common lines opens.
            self._diff.expanded_gaps.add(self._diff.gap_at(self._diff.cursor))
        elif action in ('side-old', 'side-new'):
            self._diff.side = 'old' if 'side-old' == action else 'new'
        elif 'reviewed' == action and self._selected_file():
            marked = core.toggle_reviewed(self._change_id, self._selected_file())
            interface.after_write()
            interface.say(f'{file_label(core, self._selected_file())} '
                          + ('reviewed.' if marked else 'not reviewed any more.'))
        elif 'approve' == action:
            approve(interface, change)
        elif 'comment' == action and DIFF == self._focus and self._diff.thread_at(
                self._diff.cursor):
            # A comment on a thread answers it
            self.action('reply')
        elif 'comment' == action:
            self._new_comment()
        elif 'file-comment' == action:
            self._new_comment(whole_file=True)
        elif action in ('more-context', 'less-context'):
            step = interface.context_step + (-1 if 'less-context' == action else 1)
            interface.context_step = max(0, min(step, len(CONTEXT_STEPS) - 1))
        elif action in ('vote', 'verified'):
            vote_action(interface, change, action)
        elif 'submit' == action:
            submit_with_confirmation(interface, change)
        elif 'rebase' == action:
            rebase_with_confirmation(interface, change)
        elif action in ('reply', 'edit-draft') and self._target_thread():
            comment = self._target_thread()
            anchor = self._box_below_thread(comment)
            if 'reply' == action:
                reply_to_thread(interface, self._change_id, comment, anchor)
            else:
                edit_draft(interface, self._change_id, comment, anchor)
        else:
            thread_action(interface, change, self._target_thread(), action)

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
        """l: in the files, shows the diff. In the comments, unfolds the
        thread and goes to it in the diff. In the diff, folds or unfolds
        the thread at the cursor.
        """
        comment = self._selected_comment()
        if FILES == self._focus:
            self._focus_pane(DIFF)
        elif COMMENTS == self._focus and comment:
            self._expanded[comment.number] = True
            self._show_comment_in_diff(comment)
            if self._diff.thread_at(self._diff.cursor) == comment.number:
                self._focus_pane(DIFF)
            else:
                self._interface.say(f'#{comment.number} is on a file this version doesn\'t have.')
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
                if wheel_action(kind):
                    steps = 3 if DIFF == pane else 1
                    for _ in range(steps):
                        self.action(wheel_action(kind))
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
                    # A click picks the half of the diff, too.
                    self._diff.side = 'old' if column < left + 2 + (width - 2) // 2 else 'new'
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

    def _new_comment(self, whole_file=False):
        """Starts a draft thread: on the diff's cursor line, or on the whole
        file from the file list, with C, or on diff rows without a line
        number (like the file's header).
        """
        interface = self._interface
        path = self._selected_file()
        if not path:
            return
        side = 'new'
        line = ''
        anchor = None
        place = self._diff.cursor_place() if DIFF == self._focus and not whole_file else None
        if place is None:
            if self._layout:
                # The box opens at the top of the diff, where file threads are.
                anchor = DiffGap(self._diff, self._layout[DIFF], -1)
        else:
            line, side = place
            line = str(line)
            # The box opens right below the commented line, the diff scrolls
            # if needed.
            self._diff.move_cursor(0, self._layout[DIFF][2])
            anchor = DiffGap(self._diff, self._layout[DIFF], self._diff.line_end(self._diff.cursor))
        name = file_label(interface.core, path).rsplit('/', 1)[-1]
        text = interface.write_text(f'Comment on {name}' + (f':{line}' if line else
                                                            ' (whole file)'), anchor=anchor)
        if not text:
            interface.say('Nothing saved.' if text is None else 'Empty comment, nothing saved.')
            return
        place = f'{path}:{line}' if line else path
        interface.core.add_comment(self._change_id, place, text, sent=False, side=side)
        interface.after_write()
        interface.say(f'Draft saved. {interface.keymap.label("send")} sends all drafts.')


def file_label(core, path):
    """A file's name for display. The commit message has no path."""
    return 'Commit message' if core.COMMIT_MSG == path else path


def reply_to_thread(interface, change_id, comment, anchor=None):
    """r: writes a reply as a draft, in the comment box at anchor."""
    if comment.has_draft():
        interface.say(f'You have a draft already. {interface.keymap.label("edit-draft")} edits it.')
        return
    text = interface.write_text(f'Reply to #{comment.number}', anchor=anchor)
    if text:
        core = interface.core
        core.add_message(change_id, comment.number, core.REVIEWER, text, sent=False)
        interface.after_write()
        interface.say(f'Reply saved as a draft. {interface.keymap.label("send")} sends all drafts.')


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


def thread_action(interface, change, comment, action):
    """Handles the actions on a thread or on all drafts.

    comment is the selected thread, or None while the files are selected.
    """
    core = interface.core
    if 'send' == action:
        # Like Gerrit's Reply dialog: a message on the whole change goes
        # along, if written.
        message = interface.write_text('Message on the whole change (optional, Enter sends)')
        if message is None:
            interface.say('Nothing sent.')
            return
        count = core.send_drafts(change.change_id, message)
        interface.after_write()
        interface.say(f'{count} draft(s) sent' + (' with a message.' if message else '.')
                      if count or message else 'No drafts to send.')
    elif comment is None:
        return
    elif action in ('done', 'ack'):
        if comment.has_draft():
            interface.say(f'#{comment.number} has a draft already. '
                          f'{interface.keymap.label("edit-draft")} edits it.')
            return
        text = core.DONE if 'done' == action else core.ACK
        core.add_message(change.change_id, comment.number, core.REVIEWER, text, sent=False,
                         status=core.RESOLVED)
        interface.after_write()
        interface.say(f'"{text}" saved as a draft, it resolves #{comment.number}. '
                      f'{interface.keymap.label("send")} sends.')
    elif 'resolve' == action:
        status = core.toggle_draft_status(change.change_id, comment.number)
        interface.after_write()
        interface.say(f'Sending the draft makes #{comment.number} {status}. '
                      f'{interface.keymap.label("send")} sends.')
