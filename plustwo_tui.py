# SPDX-License-Identifier: Zlib
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
import select
import subprocess
import sys
import tempfile
import textwrap
import time

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
# Moved lines, like git diff --color-moved, in teal
MOVED_BACKGROUNDS = (23, 30)
MOVED_BASIC = 6

# ── keys ──────────────────────────────────────────────────────────────────

# Alt plus a key arrives as Esc and the key. It becomes the key plus this.
META = 1 << 24

# Every action of the interface: (name, group, short label for the footer,
# description for the help). The keys come from a preset.
ACTIONS = (
    ('quit', 'Everywhere', 'quit', 'quit, after asking'),
    ('quit-now', 'Everywhere', 'quit now', 'quit without asking (q q works too)'),
    ('help', 'Everywhere', 'help', 'this help'),
    ('reload', 'Everywhere', 'reload', 'reload now (it also reloads by itself)'),
    ('back', 'Everywhere', 'back', 'back (not on the first screen, quit quits). In the diff, '
     'back to the files or comments.'),
    ('down', 'Moving', 'down', 'down. A count before moves that far, e.g. 15 down.'),
    ('up', 'Moving', 'up', 'up'),
    ('top', 'Moving', 'top', 'to the top. With a count to that row.'),
    ('bottom', 'Moving', 'bottom', 'to the bottom. With a count to that row, in the diff to '
     'that line of the new version.'),
    ('page-down', 'Moving', 'half page down', 'half a page down'),
    ('page-up', 'Moving', 'half page up', 'half a page up'),
    ('scroll-down', 'Moving', 'scroll down', 'in the diff: the view one row down, the cursor stays. '
     'j and k scroll through a thread taller than the pane, too.'),
    ('scroll-up', 'Moving', 'scroll up', 'in the diff: the view one row up, the cursor stays'),
    ('open', 'Moving', 'open', 'open. In the stack (▶ is the change shown, › and the turn '
     'colour mark those where it is your turn, "(submittable)" those a submit would take '
     'now): shows that change. On a file: into the '
     'diff. In the comments: unfolds the thread and goes to it in the diff. In the diff: folds '
     'or unfolds the thread.'),
    ('diff', 'Stacks', 'diff in pager', 'the diff of the change in git\'s pager'),
    ('vote', 'Stacks and changes', 'vote', 'vote Code-Review (-2..+2) on the latest patch set'),
    ('verified', 'Stacks and changes', 'needs Verified', 'whether the change needs Verified+1 '
     '(it comes from a check)'),
    ('submit', 'Stacks and changes', 'submit', 'submit: moves the base branch to the change, '
     'which submits the changes below it too. Needs Code-Review+2 (and Verified+1 if '
     'required). Asks first and lists the unresolved comments.'),
    ('approve', 'Stacks and changes', '+2', 'vote Code-Review+2 on the latest patch set, like '
     'Gerrit\'s Quick approve. Asks first: the key again or y gives it. Offers to send your '
     'drafts along.'),
    ('next-turn', 'Stacks', 'to my turn', 'to the next change where it is your turn'),
    ('section-down', 'Stacks', 'section down', 'on a section heading: move the section down. '
     'open on a heading folds or unfolds it.'),
    ('section-up', 'Stacks', 'section up', 'on a section heading: move the section up'),
    ('rebase', 'Stacks and changes', 'rebase', 'rebase the stack onto the base branch, without '
     'touching any checkout. On a conflict the author is asked to rebase.'),
    ('next-pane', 'Change', 'next pane', 'next pane: stack, commit message, files, comments, '
     'diff'),
    ('thread-patch-set', 'Threads', 'thread on its PS', 'show the thread on its own patch set: the diff '
     'against its parent, at the thread. Again (or back in the diff) returns to the '
     'comparison before.'),
    ('sidebar', 'Change', 'sidebar', 'hide or show the left column (stack, files, comments), '
     'so the diff gets the whole width. The setting tui.sidebar (auto, shown, hidden) picks the '
     'start, auto hides it below 100 columns.'),
    ('next-change', 'Change', 'next change', 'the next change of the stack (above)'),
    ('prev-change', 'Change', 'previous change', 'the previous change of the stack (below)'),
    ('next-file', 'Change', 'next file', 'next file'),
    ('prev-file', 'Change', 'previous file', 'previous file'),
    ('next-chunk', 'Change', 'next chunk', 'in the diff: to the next changed lines'),
    ('prev-chunk', 'Change', 'previous chunk', 'in the diff: to the previous changed lines'),
    ('next-thread', 'Change', 'next thread', 'in the diff: to the next thread, at the '
     'file\'s end in the next file with threads'),
    ('prev-thread', 'Change', 'previous thread', 'in the diff: to the previous thread'),
    ('next-unresolved', 'Change', 'next unresolved', 'in the diff: to the next unresolved '
     'thread, at the file\'s end in the next file with one'),
    ('prev-unresolved', 'Change', 'previous unresolved', 'in the diff: to the previous unresolved '
     'thread, also in the files before'),
    ('comment', 'Change', 'comment', 'in the diff: comment on the cursor line, or on the whole '
     'file on rows without a line number. On a thread it replies. In the files: comment on '
     'the file. It stays a draft until sent.'),
    ('file-comment', 'Change', 'comment on file', 'comment on the whole file'),
    ('comment-above', 'Change', 'comment above', 'in the diff: comment on the line above the '
     'cursor line, like vim\'s O, its box opens between the two lines'),
    ('more-context', 'Change', 'more context', 'more context around the changes, up to the whole '
     'file'),
    ('less-context', 'Change', 'less context', 'less context around the changes'),
    ('vimdiff', 'Change', 'vimdiff', 'the file\'s diff in vimdiff'),
    ('patch-sets', 'Change', 'patch sets', 'the patch sets: when, by whom, their type, votes '
     'and comments, to choose the versions to compare. l unfolds a patch set, Enter shows the '
     'diff (on a thread: at the thread).'),
    ('compare-last-send', 'Change', 'last send -> latest', 'compare the patch set of your last '
     'send with the latest, to see what happened since'),
    ('follow-latest', 'Change', 'follow latest on/off', 'whether a right side on the latest '
     'patch set moves to a new upload. Off (the start), it stays and the header says when a '
     'newer one is there. Kept for later sessions, the web page shares it.'),
    ('set-a', 'Patch sets', 'pick left side', 'in the patch sets: the row\'s version on the left side (A), '
     '< too'),
    ('set-b', 'Patch sets', 'pick right side', 'in the patch sets: the row\'s patch set on the right side (B), '
     '> too'),
    ('compare-prompt', 'Patch sets', 'type versions', 'in the patch sets: type the versions to compare: '
     '"1 3", "2 current" or "base 2". Empty: the whole change, latest patch set.'),
    ('search', 'Change', 'search', 'in the diff: search the file for a text, from the cursor '
     'on, on both sides. Lower case finds any case. Up and Down in the prompt go through the '
     'texts searched before, empty searches the last one again. Tab in the prompt changes '
     'where it looks: the lines shown, the whole file (opens the gaps with matches), the file '
     'and the comments, only the comments. The setting tui.search (shown, file, all, '
     'comments) picks the start.'),
    ('search-next', 'Change', 'next match', 'in the diff: to the next match of the search, also '
     'after its marks were removed'),
    ('search-prev', 'Change', 'previous match', 'in the diff: to the previous match of the search'),
    ('search-clear', 'Change', 'unmark matches', 'remove the marks of the search. The next match key '
     'brings them back.'),
    ('fold-all', 'Change', 'fold/unfold all', 'fold all threads, or unfold all if all are folded'),
    ('unfold-every', 'Change', 'unfold all', 'unfold all threads'),
    ('fold-every', 'Change', 'fold all', 'fold all threads'),
    ('reviewed', 'Change', 'reviewed', 'mark the file reviewed, or unmark it. The mark stays while '
     'the file\'s diff stays the same.'),
    ('whitespace', 'Change', 'whitespace', 'which whitespace changes the diff ignores, like '
     'Gerrit: none, trailing, changed, all'),
    ('side-old', 'Change', 'old side', 'in the diff: the cursor to the old side, comments go there'),
    ('side-new', 'Change', 'new side', 'in the diff: the cursor to the new side'),
    ('reply', 'Threads', 'reply', 'reply to the thread, as a draft'),
    ('edit-draft', 'Threads', 'edit draft', 'edit your draft in the thread. Empty deletes it.'),
    ('done', 'Threads', 'Done', 'reply "Done" as a draft, which resolves the thread'),
    ('ack', 'Threads', 'Ack', 'reply "Ack" as a draft, which resolves the thread'),
    ('resolve', 'Threads', 'resolved on/off', 'whether sending the draft resolves the thread or makes '
     'it unresolved again (Gerrit\'s Resolved checkbox). Without a draft, it makes an empty '
     'one.'),
    ('send', 'Threads', 'reply and send', 'send all drafts of this change, with an optional message on '
     'the whole change, which tells the author to start'),
)
ACTION_NAMES = tuple(action[0] for action in ACTIONS)

# The keys of each action, per preset. Alternatives are separated by
# commas, the keys of a sequence like gg by spaces. C-x is Ctrl, M-x Alt.
VIM_KEYS = {
    'quit': 'q', 'quit-now': 'Z Q', 'help': '?', 'reload': 'R', 'back': 'h,Esc',
    'down': 'j,Down', 'up': 'k,Up', 'top': 'g g,Home', 'bottom': 'G,End',
    'page-down': 'C-d,PageDown', 'page-up': 'C-u,PageUp', 'open': 'l,Enter',
    'scroll-down': 'C-e', 'scroll-up': 'C-y',
    'diff': 'd', 'vote': 'v', 'verified': 'V', 'submit': 'S', 'rebase': 'B',
    'next-pane': 'Tab', 'next-file': '] f', 'prev-file': '[ f',
    'next-change': '] s', 'prev-change': '[ s',
    'next-chunk': '] c', 'prev-chunk': '[ c', 'next-thread': '] t', 'prev-thread': '[ t',
    'next-unresolved': '] u', 'prev-unresolved': '[ u',
    # Like vim's o and O, which open a line below and above
    'comment': 'c,o', 'comment-above': 'O', 'file-comment': 'C', 'more-context': '+,=', 'less-context': '-',
    'vimdiff': 't', 'patch-sets': 'p', 'fold-all': 'i', 'unfold-every': 'z R',
    'fold-every': 'z M',
    'reply': 'r', 'edit-draft': 'e', 'done': 'y', 'ack': 'Y', 'resolve': 'x', 'send': 's',
    'whitespace': 'W', 'approve': 'A', 'next-turn': 'T', 'reviewed': 'm,Space', 'side-old': '<', 'side-new': '>',
    'section-down': 'J', 'section-up': 'K',
    # Like vim's C-w o, which keeps only the current window
    'sidebar': 'C-w o,z s',
    'set-a': 'a', 'set-b': 'b', 'compare-prompt': ':', 'thread-patch-set': 'g p',
    # Not g f, which is vim's "go to file". The interface has no f search.
    'compare-last-send': 'g l', 'follow-latest': 'F',
    'search': '/', 'search-next': 'n', 'search-prev': 'N',
    # Like the usual vim mapping of C-l to :nohlsearch
    'search-clear': 'C-l',
}
PRESETS = {
    'vim': VIM_KEYS,
    # Emacs moves with C-n, C-p, C-v, M-v, M-< and M->, and C-g cancels.
    # Like in its diff-mode, n and p go to the next and previous changes,
    # N and P to the next and previous file. Counts start with C-u.
    'emacs': {**VIM_KEYS,
              'quit': 'q', 'quit-now': 'C-x C-c', 'reload': 'g,R',
              'next-change': 'M-n', 'prev-change': 'M-p', 'back': 'C-g,Esc,C-b',
              'down': 'C-n,Down', 'up': 'C-p,Up', 'top': 'M-<,Home', 'bottom': 'M->,End',
              'page-down': 'C-v,PageDown', 'page-up': 'M-v,PageUp', 'open': 'Enter,C-f',
              'next-chunk': 'n', 'prev-chunk': 'p', 'next-file': 'N', 'prev-file': 'P',
              'next-thread': ']', 'prev-thread': '[', 'patch-sets': 'b',
              'next-unresolved': 'M-}', 'prev-unresolved': 'M-{',
              # C-o like Emacs' open-line
              'comment': 'c', 'comment-above': 'C-o',
              # Like delete-other-windows
              'sidebar': 'C-x 1', 'set-b': 'M-b', 'thread-patch-set': 'C-c p',
              # g reloads here
              'compare-last-send': 'C-c l', 'follow-latest': 'C-c f',
              # Emacs' M-s holds its search commands
              'search': 'C-s', 'search-next': 'M-s n', 'search-prev': 'M-s p',
              # Like Emacs' unhighlight-regexp
              'search-clear': 'M-s h u'},
    # Gerrit's web interface: j and k move, n and p go to the next and
    # previous changes, N and P to the next and previous thread, ] and [ to
    # the next and previous file, u goes up, o opens, c comments, and a
    # opens the reply dialog, which sends the drafts.
    'gerrit': {**VIM_KEYS,
               'back': 'u,h,Esc', 'open': 'o,l,Enter',
               'next-chunk': 'n', 'prev-chunk': 'p', 'next-thread': 'N', 'prev-thread': 'P',
               'next-file': ']', 'prev-file': '[', 'send': 'a', 'patch-sets': 'b',
               # o opens here
               'comment': 'c', 'comment-above': 'O',
               'next-unresolved': 'C-n', 'prev-unresolved': 'C-p',
               # Gerrit's r marks the file reviewed
               'reviewed': 'r,m,Space', 'reply': 'R', 'reload': 'C-r',
               'next-change': 'M-n', 'prev-change': 'M-p',
               # e and E unfold and fold all threads, so drafts are edited with w
               'unfold-every': 'e', 'fold-every': 'E', 'edit-draft': 'w',
               # a sends and b chooses patch sets here
               'set-a': '{', 'set-b': '}',
               'compare-last-send': 'L', 'follow-latest': 'F',
               # / is Gerrit's search, C-g goes on like a browser's find
               'search-next': 'C-g', 'search-prev': 'M-g'},
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


# Kinds of messages in the bottom line
OK = 'ok'
ERROR = 'error'

# What Keymap.feed() returns while a count or a key sequence goes on
PENDING = 'pending'


class Keymap:
    """The keys of a preset, with the reviewer's own on top, turned into
    actions. Counts and key sequences like gg are collected here, so the
    views only see actions.
    """

    def __init__(self, preset='vim', overrides=None):
        self.errors = []
        self.last = ()
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

    def single_keys(self, name):
        """The codes of the keys that alone stand for an action."""
        return {sequence[0] for sequence, bound in self._sequences.items()
                if name == bound and 1 == len(sequence)}

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
        # Which keys gave the action, e.g. Enter or l for open
        self.last = sequence
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
        """The help screen: the keys of every action with a few words, one
        section per group, in two columns when the screen is wide enough.
        """
        groups = {}
        for name, group, label, _description in ACTIONS:
            keys = ' '.join(key_label(alternative) for alternative in self.labels(name))
            if keys:
                groups.setdefault(group, []).append((keys, label))
        lines = [f'plustwo keys ({self.preset})   mouse: click, double click, wheel', '']
        cell = min(max(len(f'  {keys:<9.9} {label}') for entries in groups.values()
                       for keys, label in entries) + 2, 36)
        columns = 2 if width >= 2 * cell + 2 else 1
        for group, entries in groups.items():
            lines.append(group)
            for start in range(0, len(entries), columns):
                lines.append(''.join(f'  {keys:<9.9} {label}'[:cell].ljust(cell)
                                     for keys, label in entries[start:start + columns]).rstrip())
        lines += ['', 'Comment box: Enter saves. Alt+Enter, Ctrl+J or \\ at the end start a new '
                  'line. Esc on text asks once, again discards it.',
                  'Blue lines come from changes below, purple partly.']
        return lines


def keymap_from_settings(core):
    """The keymap the settings ask for."""
    overrides = core.settings_section('keys')
    preset = overrides.pop('preset', 'vim')
    return Keymap(preset, overrides)


# Colours of vote values
VOTE_COLOURS = {2: 2, 1: 2, -1: 1, -2: 1}
# Background of thread boxes, and the names' colours: (256 colours, 8 colours)
THREAD_TINT = 238
REVIEWER_COLOURS = (81, 6)
AUTHOR_COLOURS = (214, 3)
HEADER_BACKGROUND = (237, 0)


def tone(colours):
    """The colour of a (256 colours, 8 colours) pair for this terminal."""
    return colours[0] if curses.COLORS >= 256 else colours[1]


def vote_badge(interface, label, value, required=False):
    """A vote as a coloured badge, like "CR +2" on green. None without
    a vote, unless the label is required.
    """
    if not value and not required:
        return None
    text = f' {label} {value:+d} ' if value else f' {label} ? '
    background = {2: 2, 1: 2, -1: 1, -2: 1}.get(value, 8)
    return text, interface.colour_pairs.attribute(0 if value else 15, background) | curses.A_BOLD



def run(core, list_name=None):
    """Starts the interface. core is the plustwo module. list_name limits
    the dashboard to a list of repositories.
    """
    global TERMINAL_BACKGROUND, CORE  # pylint: disable=global-statement
    CORE = core
    TERMINAL_BACKGROUND = (parse_colour(core.setting('tui.background', '')) or query_background()
                           or (0, 0, 0))
    wanted = true_colour_terminal(core.setting('tui.colours', 'auto'))

    def start(screen):
        global TRUE_COLOUR  # pylint: disable=global-statement
        TRUE_COLOUR = wanted and curses.COLORS >= 1 << 24
        # Enter then arrives as \r and Ctrl+J as \n, so the comment box
        # tells them apart.
        curses.nonl()
        keys_as_keys()
        Interface(screen, core, list_name).loop()

    curses.wrapper(start)


def keys_as_keys():
    """Lets C-s, C-q and C-c arrive as keys: turns off the terminal's flow
    control (XON/XOFF), which stops the output, and its interrupt key,
    which ends the program (the emacs keys use C-c). C-z still suspends.
    curses keeps this mode after programs run outside and restores the old
    one at the end.
    """
    import termios  # pylint: disable=import-outside-toplevel
    try:
        attributes = termios.tcgetattr(sys.stdin.fileno())
        attributes[0] &= ~(termios.IXON | termios.IXOFF)
        attributes[6] = list(attributes[6])
        # No character is the interrupt key, like stty intr undef
        attributes[6][termios.VINTR] = bytes([termios_disabled()])
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, attributes)
    except termios.error:
        return
    curses.def_prog_mode()


def termios_disabled():
    """The value that turns a terminal key off, _POSIX_VDISABLE: 0 on
    Linux, 0xff on the BSDs and macOS.
    """
    return 0 if sys.platform.startswith('linux') else 0xff


# The reply to OSC 11, the question for the terminal's background
BACKGROUND_REPLY = re.compile(r'\x1b\]11;rgb:([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})/'
                              r'([0-9a-fA-F]{1,4})(?:\x1b\\|\x07)')
# The reply to DA1, which every terminal answers, after the one to OSC 11
ATTRIBUTES_REPLY = re.compile(r'\x1b\[\?[0-9;]*c')
# How long to wait for the terminal's replies, in seconds
QUERY_TIMEOUT = 0.1


def parse_background_reply(text):
    """(red, green, blue) from a terminal's reply to OSC 11, like
    "\\e]11;rgb:1c1c/1c1c/1c1c\\e\\", 1 to 4 hex digits a channel, ended by
    ST or BEL. None if text has none.
    """
    match = BACKGROUND_REPLY.search(text)
    if match is None:
        return None
    return tuple(round(int(value, 16) * 255 / (16 ** len(value) - 1)) for value in match.groups())


def parse_colour(text):
    """(red, green, blue) of a colour like #1c1c1c, None for anything else."""
    match = re.fullmatch(r'#?([0-9a-fA-F]{6})', text.strip())
    if match is None:
        return None
    return tuple(int(match.group(1)[index:index + 2], 16) for index in (0, 2, 4))


def query_background():
    """The terminal's background, asked with OSC 11 before curses starts,
    None if it doesn't say. DA1 is asked after it: every terminal answers
    that, in order, so once its reply is there, no other one can come late
    and be read as keys. QUERY_TIMEOUT bounds the wait for terminals that
    answer neither.
    """
    try:
        import termios  # pylint: disable=import-outside-toplevel
        import tty  # pylint: disable=import-outside-toplevel
        descriptor = os.open('/dev/tty', os.O_RDWR | os.O_NOCTTY)
    except (ImportError, OSError):
        return None
    try:
        saved = termios.tcgetattr(descriptor)
    except termios.error:
        os.close(descriptor)
        return None
    reply = ''
    try:
        tty.setraw(descriptor)
        os.write(descriptor, b'\x1b]11;?\x1b\\\x1b[c')
        deadline = time.monotonic() + QUERY_TIMEOUT
        while not ATTRIBUTES_REPLY.search(reply):
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([descriptor], [], [], left)[0]:
                break
            reply += os.read(descriptor, 256).decode('latin-1')
    finally:
        # Whatever else came stays out of the keys.
        termios.tcsetattr(descriptor, termios.TCSAFLUSH, saved)
        os.close(descriptor)
    return parse_background_reply(reply)


def has_terminfo(name):
    """Whether the terminal database has an entry of this name."""
    try:
        curses.setupterm(name, sys.stdout.fileno())
    except (curses.error, OSError, ValueError):
        return False
    return True


def true_colour_terminal(mode):
    """Whether to draw in true colour, for the setting tui.colours: auto,
    true or 256. ncurses draws RGB colours only with a terminfo entry for
    them, like xterm-direct: with auto or true, and COLORTERM saying the
    terminal can, TERM changes to its -direct entry if there is one, like
    st-256color to st-direct. The keys of both entries are the same.
    Without such an entry, it stays at 256 colours.
    """
    term = os.environ.get('TERM', '')
    if '256' == mode or not term:
        return False
    if 'true' != mode and os.environ.get('COLORTERM') not in ('truecolor', '24bit'):
        return False
    if term.endswith('-direct'):
        return True
    direct = re.sub(r'-256colou?r$', '', term) + '-direct'
    if not has_terminfo(direct):
        return False
    os.environ['TERM'] = direct
    return True


class Interface:
    """The running interface: screen stack, drawing and keys."""

    def __init__(self, screen, core, list_name=None):
        self._screen = screen
        self._core = core
        self.list_name = list_name
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
            curses.init_pair(number, curses_colour(number if number < 8 else 8), -1)
        screen.timeout(RELOAD_INTERVAL_MS)
        self.colour_pairs = ColourPairs()
        global DIM_COLOUR  # pylint: disable=global-statement
        dim_colour = core.setting('tui.dim-colour', str(DIM_COLOUR))
        if dim_colour.isdigit() and int(dim_colour) < 256:
            DIM_COLOUR = int(dim_colour)
        # Index into CONTEXT_STEPS, kept for all changes
        self.context_step = 0
        # Gerrit's whitespace mode of the diff, one of core.WHITESPACE_MODES
        self.whitespace = core.setting('diff.whitespace', 'none')
        # Whether a right side on the latest patch set moves to a new upload,
        # state that the follow-latest key toggles
        self.follow_latest = core.follows_latest()
        if self.whitespace not in core.WHITESPACE_MODES:
            self.whitespace = 'none'
        # The text searched last in the diff, whether its matches are
        # marked, the texts searched before (the latest last) and where it
        # looks, one of core.SEARCH_MODES
        self.search = ''
        self.marking = False
        self.search_history = []
        self.search_mode = core.setting('tui.search', 'shown')
        if self.search_mode not in core.SEARCH_MODES:
            self.search_mode = 'shown'
        # The left column of the change screen: auto, shown or hidden. The
        # sidebar key sets it for the rest of the session.
        self.sidebar = core.setting('tui.sidebar', 'auto')
        if self.sidebar not in SIDEBAR_MODES:
            self.sidebar = 'auto'

    # ── state shared by the views ─────────────────────────────────────────

    @property
    def core(self):
        """The plustwo module with the review logic."""
        return self._core

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
        """Draws, waits for a key or the reload interval, repeats."""
        self._views[-1].reload()
        self._version = self.version()
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
                    self.say(str(error), ERROR)
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
        except self._core.ChangeError as error:
            self.say(str(error), ERROR)

    def _reload_if_changed(self):
        """Reloads the current view when commits or notes changed."""
        version = self.version()
        if version != self._version:
            self._version = version
            self._views[-1].reload()

    def version(self):
        """The refs of every shown repository, see core.stack_version()."""
        return '\n'.join(self._core.stack_version(repository)
                         for repository in self._core.review_repositories(self.list_name))

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


def box_keys(emacs, save='save', discarding=False):
    """The keys of the comment box for its frame and the footer: (keys,
    what they do).
    """
    cancel = 'C-g' if emacs else 'Esc'
    keys = [('⏎', save), ('Alt-⏎', 'new line'),
            (cancel, 'again discards' if discarding else 'cancel'),
            ('C-x C-e' if emacs else '^E', 'editor')]
    return keys


class Choice:
    """A value picked from a few, like a vote from -2 to +2: Left and Right
    move, a digit picks, - before it a negative one.
    """

    def __init__(self, values, current=0):
        self.values = tuple(values)
        self.index = self.values.index(current) if current in self.values else 0
        self._minus = False

    @property
    def value(self):
        return self.values[self.index]

    def key(self, key):
        """Handles a key from get_wch(). Returns whether it was one of the
        choice's keys.
        """
        if key in (curses.KEY_LEFT, curses.KEY_RIGHT):
            step = 1 if curses.KEY_RIGHT == key else -1
            self.index = max(0, min(self.index + step, len(self.values) - 1))
        elif '-' == key:
            self._minus = True
            return True
        elif '+' == key:
            pass
        elif isinstance(key, str) and key.isdigit():
            value = -int(key) if self._minus else int(key)
            if value in self.values:
                self.index = self.values.index(value)
        else:
            self._minus = False
            return False
        self._minus = False
        return True

    def text(self):
        """The values in a row, the chosen one in brackets: -2 -1 [0] +1 +2."""
        return ' '.join(f'[{value:+d}]' if index == self.index else f' {value:+d} '
                        for index, value in enumerate(self.values)).replace('+0', ' 0')


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
        # Whether the cancel key was pressed once with text in the box
        self.discarding = False

    def cancel(self):
        """Esc (C-g with the emacs keys): whether the box closes. An empty
        box closes at once. With text in it, the first press only warns,
        the next one discards. Any other key ends the warning, see keep().
        """
        if not self.text.strip() or self.discarding:
            return True
        self.discarding = True
        return False

    def keep(self):
        """A key other than the cancel key: the text stays."""
        self.discarding = False

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
                core = self._interface.core
                if not change.submit_problems():
                    entries.append((('submit',), 'submit'))
                elif not change.is_approved(core.CODE_REVIEW):
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
        core = self._interface.core
        core.use_repository(None)
        selected = self._rows[self._selected] if self._selected < len(self._rows) else None
        self._changes = core.all_changes(self._interface.list_name)
        # Stacks the base branch moved away from, marked in the topic column
        self._behind = {(change.repository, change.topic) for change in self._changes
                        if change.rebase_requested or core.needs_rebase(change.topic,
                                                                         change.repository)}
        # What draw() needs, so drawing runs no git command
        self._folded = folded = core.folded_sections()
        user = core.current_user()
        self._sections = [change.sections(user) for change in self._changes]
        self._rows = []
        for section, entries in core.dashboard(self._changes, user):
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
        core = self._interface.core
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
                out(row, 1, f'{mark} {core.SECTION_TITLES[section]} ({count})',
                    attributes | curses.A_BOLD)
                continue
            if 'none' == row_content[0]:
                out(row, 6, '(none)', self._interface.dim())
                continue
            change = self._changes[row_content[1]]
            # Plain text on the reversed cursor row
            secondary_text = self._interface.dim(attributes)
            dim = secondary_text if change.wip else 0
            out(row, 1, ' ' * (area.width - 2), attributes)
            out(row, 1, f'{row_content[1] + 1:>3}  ', attributes | dim)
            out(row, 6, f'{change.short_id():<9} ', attributes | secondary_text)
            out(row, 16, f'{change.repo:<12.12} ', attributes | dim)
            behind = (change.repository, change.topic) in self._behind
            out(row, 29, f'{("↻ " if behind else "") + change.topic:<14.14} ',
                attributes | (curses.color_pair(3) if behind else secondary_text))
            out(row, 44, f'{change.base:<10.10} ', attributes | secondary_text)
            out(row, 55, f'{change.subject:<{subject_width}.{subject_width}} ', attributes | dim)
            column = 56 + subject_width
            out(row, column, f'{core.version_label(change):<5} ', attributes | secondary_text)
            column += 6
            for label, short in ((core.CODE_REVIEW, 'CR'), (core.VERIFIED, 'V')):
                badge = vote_badge(self._interface, short, change.label_value(label),
                                   core.VERIFIED == label and change.verified_required)
                if badge:
                    out(row, column, badge[0], badge[1])
                column += 8
            turn = change.turn()
            yours = 'your-turn' in self._sections[row_content[1]]
            # The same rule as the Your turn section: "you" there and here
            mine = ('▶ submit' if core.SUBMITTABLE == turn else '▶ you') if yours else None
            out(row, column, f'{mine or TURN_LABELS.get(turn, turn):<10} ',
                attributes | (curses.A_BOLD | curses.color_pair(3) if yours
                              else secondary_text))
            out(row, column + 11, core.comment_counts(change, short=True),
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
        core = self._interface.core
        selectable = self._selectable()
        if action in LIST_ACTIONS and selectable:
            position = selectable.index(self._selected) if self._selected in selectable else 0
            self._selected = selectable[move(position, action, len(selectable), count)]
            return
        row = self._rows[self._selected] if self._selected < len(self._rows) else None
        if row and 'section' == row[0]:
            if 'open' == action:
                core.toggle_section(row[1])
            elif action in ('section-down', 'section-up'):
                core.move_section(row[1], 1 if 'section-down' == action else -1)
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
        core.use_repository(change.repository)
        if 'open' == action:
            self._interface.open(ChangeView(self._interface, change.change_id,
                                            change.repository))
        elif 'diff' == action:
            self._interface.run_outside(core.diff_command(change))
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


def approve(interface, change):
    """Code-Review+2 on the latest patch set, after asking like quitting:
    the approve key again or y gives it. Then the drafts go along if the
    reviewer wants.
    """
    core = interface.core
    key = interface.ask_key(f'Code-Review+2 on PS{change.latest_patch_set()} of '
                            f'{change.short_id()} {change.subject}? '
                            f'({interface.keymap.label("approve")} again or y)')
    if key not in interface.keymap.single_keys('approve') | {ord('y')}:
        interface.say('No vote given.')
        return
    drafts = sum(not message.sent for comment in change.comments for message in comment.messages)
    send = bool(drafts) and ord('y') == interface.ask_key(
        f'{drafts} draft(s) unsent. Send them along? (y/N)')
    number, sent = core.quick_approve(change.change_id, send)
    interface.after_write()
    interface.say(f'Code-Review+2 on PS{number}' + (f', {sent} draft(s) sent.' if sent else '.'), OK)


def rebase_with_confirmation(interface, change):
    """B: rebases the change's stack onto the base branch, after asking.
    On a conflict the author is asked to do it.
    """
    core = interface.core
    if not core.needs_rebase(change.topic):
        interface.say(f'{change.topic} is on {change.base} already.')
        return
    if not interface.confirm([f'Rebase the stack {change.topic} onto {change.base}.',
                              'Every change gets a new patch set.'], 'Rebase?'):
        return
    rebased = core.rebase_stack(change.topic)
    interface.after_write()
    interface.say(f'Rebased, {len(rebased)} new patch set(s).' if rebased is not None
                  else 'Conflicts: the author is asked to rebase.',
                  OK if rebased is not None else ERROR)


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
            interface.say(f'{change.short_id()} has no patch set yet, nothing to vote on.', ERROR)
            return
        value = interface.choose(f'Code-Review on PS{change.latest_patch_set()}:',
                                 Choice(core.vote_values(), change.own_vote()))
        if value is None or value == change.own_vote():
            return
        number = core.set_vote(change.change_id, core.CODE_REVIEW, value)
        interface.after_write()
        interface.say(f'Code-Review{value:+d} on PS{number}.' if value
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
                             + ['', f'{interface.keymap.label("approve")} gives Code-Review+2. '
                                'Verified comes from a check.'])
        return
    lines = [f'Submit to {change.base}:']
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
        interface.say('Submitted: ' + ', '.join(other.short_id() for other in submitted), OK)


# ── the diff pane ─────────────────────────────────────────────────────────

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


# The foreground of secondary text, like line numbers and ids, in segments
# and colour pairs: a grey readable on the background, see secondary().
# Not curses' A_DIM, which many terminals draw almost invisible.
SECONDARY = -2
# The grey of secondary text, the setting tui.dim-colour
DIM_COLOUR = 246
# The core (the plustwo module), set by run(), for its colour math
CORE = None


def contrast(first, second):
    """The contrast ratio of two colours (palette or RGB), 1 to 21."""
    return CORE.contrast(rgb_of(first), rgb_of(second))


def secondary(background=-1):
    """The colour of secondary text on a background (-1 the default):
    DIM_COLOUR, lighter on the ramp of greys where the background needs
    it. -1, normal text, with fewer than 256 colours.
    """
    if curses.COLORS < 256:
        return -1
    colour = DIM_COLOUR
    if -1 == background:
        background = TERMINAL_BACKGROUND
    # Lighter on a dark background, darker on a light one
    step = -1 if is_light(background) else 1
    while 232 < colour + step <= 255 and 232 <= colour <= 255 \
            and contrast(colour, background) < CORE.MIN_CONTRAST:
        colour += step
    return colour


class ColourPairs:
    """Hands out curses colour pairs for foreground/background combinations.

    curses can only draw colours through numbered pairs, so every
    combination the diff uses gets one, the first time it shows up.
    """

    def __init__(self):
        self._pairs = {}
        self._next = FIRST_DIFF_PAIR

    def attribute(self, foreground, background):
        """The curses attribute for two palette colours (-1 is default).
        SECONDARY as foreground is the grey of secondary text.
        """
        if SECONDARY == foreground:
            foreground = secondary(background)
        key = (curses_colour(foreground), curses_colour(background))
        if key not in self._pairs:
            if self._next >= curses.COLOR_PAIRS or max(key) >= curses.COLORS:
                return 0
            curses.init_pair(self._next, *key)
            self._pairs[key] = self._next
            self._next += 1
        return curses.color_pair(self._pairs[key])


# Approximate colours of the 16 basic palette entries (xterm's defaults).
# The terminal may use others, good enough for lightening them a bit.
BASIC_COLOURS = ((0, 0, 0), (205, 0, 0), (0, 205, 0), (205, 205, 0), (0, 0, 238), (205, 0, 205),
                 (0, 205, 205), (229, 229, 229), (127, 127, 127), (255, 0, 0), (0, 255, 0),
                 (255, 255, 0), (92, 92, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255))

# Background of the cursor line where the diff has none: a dark grey
CURSOR_LINE_GREY = 236
# The cursor's frame: thin lines left and right of its half's code, in
# columns every row reserves, so nothing moves with the cursor. Segments
# mark those columns with these foregrounds, the line number with
# LINE_NUMBER, which the cursor row highlights.
LEFT_MARK = -3
RIGHT_MARK = -4
LINE_NUMBER = -5
FRAME_COLOUR = 250
LINE_NUMBER_BACKGROUND = 237


# Whether the interface draws in true colour, see true_colour_terminal().
# Colours are palette indexes or (red, green, blue): in true colour all go
# to the terminal as RGB, else as the nearest of the 256.
TRUE_COLOUR = False
# The terminal's own background, for the cursor's tint and the contrast of
# secondary text on it: the setting tui.background, else what the terminal
# says (see query_background()), else black
TERMINAL_BACKGROUND = (0, 0, 0)
# The cursor's tint: this share of white mixed into a row's background
CURSOR_TINT = 0.07


def rgb_of(colour):
    """(red, green, blue) of a palette index or an RGB colour."""
    return colour if isinstance(colour, tuple) else palette_colour(colour)


def curses_colour(colour):
    """A colour as curses takes it: -1 is the default. In true colour an
    RGB number, where a direct colour terminal reads 0 to 7 as the basic
    colours, which stay. Else a palette index.
    """
    if -1 == colour:
        return -1
    if not TRUE_COLOUR:
        return palette_index(*colour) if isinstance(colour, tuple) else colour
    if not isinstance(colour, tuple) and colour < 8:
        return colour
    red, green, blue = rgb_of(colour)
    # Below 8 would be a basic colour: almost black is near enough.
    return max(red << 16 | green << 8 | blue, 8)


def is_light(colour):
    """Whether dark text reads better on a colour than light text."""
    return contrast(colour, (0, 0, 0)) > contrast(colour, (255, 255, 255))


def cursor_tint(background):
    """The cursor's background over a row's: CURSOR_TINT towards white in
    true colour, towards black on a light one. The same without true
    colour, where only the side lines mark it.
    """
    if not TRUE_COLOUR:
        return background
    colour = TERMINAL_BACKGROUND if -1 == background else rgb_of(background)
    if is_light(colour):
        return tuple(round(value * (1 - CURSOR_TINT)) for value in colour)
    return tuple(round(value + (255 - value) * CURSOR_TINT) for value in colour)


def palette_colour(index):
    """(red, green, blue) of a palette colour."""
    if index < 16:
        return BASIC_COLOURS[index]
    if index >= 232:
        grey = 8 + 10 * (index - 232)
        return grey, grey, grey
    index -= 16
    return tuple(CUBE_LEVELS[level] for level in (index // 36, index // 6 % 6, index % 6))


# Backgrounds of removed and added lines: (line, changed characters). The
# greens are the ones the reviewer's delta used, the reds delta's defaults.
REMOVED_BACKGROUNDS = (palette_index(0x3f, 0x00, 0x01), palette_index(0x90, 0x10, 0x11))
# The shades of the diff in true colour, light and strong, the exact ones
# of delta and the web page
TRUE_SHADES = {'removed': ((0x4b, 0x11, 0x13), (0xa3, 0x26, 0x2a)),
               'added': ((0x1f, 0x4d, 0x2a), (0x2f, 0x7a, 0x40)),
               'below': ((0x0c, 0x2d, 0x6b), (0x1f, 0x4f, 0x9e)),
               'partly': ((0x2e, 0x14, 0x52), (0x4f, 0x2a, 0x85)),
               'moved': ((0x0f, 0x3b, 0x3a), (0x1d, 0x6b, 0x68))}
ADDED_BACKGROUNDS = (palette_index(0x1f, 0x4d, 0x2a), palette_index(0x2f, 0x7a, 0x40))
# The same for terminals with fewer colours: red and green
REMOVED_BASIC = 1
ADDED_BASIC = 2
TAB_WIDTH = 4
# Columns between the halves of a row: a line
HALF_SEPARATOR = '│'


STYLE_ATTRIBUTES = {'bold': curses.A_BOLD, 'italic': curses.A_ITALIC,
                    'underline': curses.A_UNDERLINE}


def highlighted(core, lines, path):
    """Colours of the lines of a file as syntax highlighting gives them:
    for each line a list of (foreground, styles) per character. None
    without bat, or if its output doesn't fit the lines.
    """
    segments = core.syntax_segments(lines, path, core.setting('tui.theme'))
    if segments is None:
        return None
    colours = []
    for line in segments:
        cells = []
        for text, colour, styles in line:
            # Kept as RGB, curses_colour() rounds them without true colour
            foreground = -1 if colour is None else tuple(
                int(colour[index:index + 2], 16) for index in (1, 3, 5))
            attributes = 0
            for style in styles:
                attributes |= STYLE_ATTRIBUTES[style]
            cells += [(foreground, attributes)] * len(text)
        colours.append(cells)
    return colours


def half_cells(core, half, colours, marks, search=''):
    """The characters of one half of a row as (character, foreground,
    background, styles), tabs expanded and control characters made visible.
    colours are the line's syntax colours or None. marks are the sets of
    line numbers (from below, partly from below) on this side. The matches
    of a search are shown reversed.
    """
    if half.kind in (core.REMOVED, core.ADDED):
        if half.number in marks[0] or half.number in marks[1]:
            partly = half.number not in marks[0]
            backgrounds = ((PARTLY_BASIC,) * 2 if partly else (FROM_BELOW_BASIC,) * 2) \
                if curses.COLORS < 256 else (PARTLY_BACKGROUNDS if partly
                                             else FROM_BELOW_BACKGROUNDS)
        elif half.moved:
            backgrounds = (MOVED_BASIC,) * 2 if curses.COLORS < 256 else MOVED_BACKGROUNDS
        elif curses.COLORS < 256:
            backgrounds = ((REMOVED_BASIC,) if core.REMOVED == half.kind else (ADDED_BASIC,)) * 2
        else:
            backgrounds = REMOVED_BACKGROUNDS if core.REMOVED == half.kind else ADDED_BACKGROUNDS
        if TRUE_COLOUR:
            backgrounds = TRUE_SHADES[
                'partly' if half.number in marks[1] and half.number not in marks[0]
                else 'below' if half.number in marks[0] else 'moved' if half.moved
                else half.kind]
    else:
        backgrounds = (-1, -1)
    if half.whole:
        # New text all over gets the strong shade, see core.Half.
        backgrounds = (backgrounds[1],) * 2
    changed = set()
    for begin, end in half.edits:
        changed.update(range(begin, end))
    cells = []
    text = half.text[:-1] if half.text.endswith('\r') else half.text
    found = set()
    for begin, end in core.search_matches(text, search):
        found.update(range(begin, end))
    for index, character in enumerate(text):
        foreground, styles = colours[index] if colours and index < len(colours) else (-1, 0)
        if index in found:
            styles |= curses.A_REVERSE
        background = backgrounds[index in changed]
        if isinstance(foreground, tuple) and -1 != background:
            # Syntax colours readable on the diff's shades
            foreground = core.readable(foreground, rgb_of(background))
        if '\t' == character:
            cells += [(' ', foreground, background, styles)] * (TAB_WIDTH - len(cells) % TAB_WIDTH)
        elif ord(character) < 32 or 127 == ord(character):
            cells.append(('·', SECONDARY, background, styles))
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


# Where a search looks, for the prompt and the messages, by core.SEARCH_MODES
SEARCH_PLACES = {'shown': 'the lines shown', 'file': 'the whole file',
                 'all': 'the file and its comments', 'comments': 'the comments'}


def search_question(mode):
    """The search prompt, with where it looks."""
    return f'Search {SEARCH_PLACES[mode]} (Tab: elsewhere):'


def marked_segments(core, segments, search):
    """Segments (text, foreground, background, styles) of a row with the
    matches of a search reversed.
    """
    text = ''.join(segment[0] for segment in segments)
    found = set()
    for begin, end in core.search_matches(text, search):
        found.update(range(begin, end))
    if not found:
        return segments
    cells = [(character, foreground, background, styles | (curses.A_REVERSE if index in found
                                                            else 0))
             for index, (character, foreground, background, styles) in enumerate(
                 (character, *rest) for part, *rest in segments for character in part)]
    return grouped(cells)


def rendered_rows(core, rows, width, colours, marks, search=''):
    """Draws side by side rows into screen rows like Gerrit's diff: old
    number, old text, new number, new text. A long line wraps onto more
    rows. Returns [(segments, (old number, new number), continues,
    changed, gap, texts)], where continues says that the row goes on with
    the line of the row above, changed that it has removed or added lines,
    gap is the first old line of a gap row, else None, and texts the (old,
    new) text of the line on its first row, else None. Matches of a search
    are marked.
    """
    digits = max([4] + [len(str(half.number)) for row in rows for half in (row.old, row.new)
                        if half.number])
    half_width = max((width - len(HALF_SEPARATOR)) // 2, digits + 4)
    # Two columns for the cursor's frame, see LEFT_MARK
    text_width = half_width - digits - 1 - 2
    result = []
    for row in rows:
        if row.skipped:
            label = f' {row.skipped} common line{"s" if row.skipped > 1 else ""} '
            result.append(([(label.center(width, '┄'), SECONDARY, -1, 0)],
                           (None, None), False, False, row.old.number, None))
            continue
        changed = (core.CONTEXT, core.CONTEXT) != (row.old.kind, row.new.kind)
        halves = []
        for half, side in ((row.old, 0), (row.new, 1)):
            line_colours = (colours[side][half.number - 1]
                            if half.number and colours[side] else None)
            cells, background = half_cells(core, half, line_colours, marks[side], search)
            pieces = [cells[start:start + text_width]
                      for start in range(0, len(cells), text_width)] or [[]]
            halves.append((half, pieces, background))
        for piece in range(max(len(pieces) for _, pieces, _ in halves)):
            segments = []
            for side, (half, pieces, background) in enumerate(halves):
                number = f'{half.number:>{digits}} ' if half.number and 0 == piece else \
                    ' ' * (digits + 1)
                segments.append((number, LINE_NUMBER if 0 == piece and half.number else SECONDARY,
                                 -1, 0))
                segments.append((' ', LEFT_MARK, -1, 0))
                cells = pieces[piece] if piece < len(pieces) else []
                padding = text_width - len(cells)
                fill = background if piece < len(pieces) else -1
                segments += grouped(cells + [(' ', -1, fill, 0)] * padding)
                segments.append((' ', RIGHT_MARK, -1, 0))
                if 0 == side:
                    segments.append((HALF_SEPARATOR, SECONDARY, -1, 0))
            numbers = (row.old.number, row.new.number) if 0 == piece else (None, None)
            texts = (row.old.text, row.new.text) if 0 == piece else None
            result.append((segments, numbers, 0 != piece, changed, None, texts))
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
        # The (old, new) text of each diff line on its first row, and the
        # gaps left (first old line, first new line, lines), for search
        self._texts = []
        self._closed_gaps = []
        self._sources = None
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
        # Whether the diff has moved lines, see core.moved_lines()
        self.has_moved = False

    def show(self, core, change, compare, path, width, context, whitespace='none', search=''):
        """Renders the diff of a file with context lines around the changes
        (None: the whole file), unless it is rendered already. The threads
        come with arrange(), call it after this. The matches of a search are
        marked.
        """
        if self._path != path:
            self.expanded_gaps = set()
        # The lines with threads stay visible, see core.file_diff_rows().
        threads = core.thread_lines(change, *compare, path)
        key = (path, core.versions_to_compare(change, *compare), width, context,
               frozenset(self.expanded_gaps), whitespace, threads, search)
        if key == self._key:
            return
        is_other_file = self._path != path
        self._key = key
        self._path = path
        old, new, paths = core.diff_endpoints(change, *compare, path=path)
        rows = core.file_diff_rows(change, *compare, path=path, context=context,
                                   whitespace=whitespace, shown=threads)
        old_path = core.old_path_of(change, *compare, paths[0]) or paths[0]
        from_below, partly = core.rebase_lines(change, *compare, path=path)
        marks = ((from_below[0], partly[0]), (from_below[1], partly[1]))
        if rows is None:
            rendered = [([('Binary file, not shown.', SECONDARY, -1, 0)], (None, None), False,
                          False, None, None)]
        elif not rows:
            rendered = [([('No differences in this file.', SECONDARY, -1, 0)], (None, None),
                          False, False, None, None)]
        else:
            if self.expanded_gaps and any(row.skipped for row in rows):
                rows = core.expand_gaps(rows, self.expanded_gaps, core.file_lines(old, old_path),
                                        core.file_lines(new, paths[0]))
            colours = [self._colours(core, old, old_path), self._colours(core, new, paths[0])]
            rendered = rendered_rows(core, rows, width, colours, marks, search)
        self._closed_gaps = [(row.old.number, row.new.number, row.skipped)
                             for row in rows or () if row.skipped]
        self._sources = (old, old_path, new, paths[0])
        self._gaps = [entry[4] for entry in rendered]
        self._texts = [entry[5] for entry in rendered]
        self._diff_lines = [segments for segments, *_ in rendered]
        previous_numbers = self._diff_numbers
        self._diff_numbers = [numbers for _, numbers, *_ in rendered]
        self._continues = [continues for _, _, continues, *_ in rendered]
        self._changed = [entry[3] for entry in rendered]
        marked = [any(numbers[side] in marks[side][kind]
                      for numbers in self._diff_numbers for side in (0, 1)) for kind in (0, 1)]
        self.marks_from_below = tuple(marked)
        self.has_moved = any(row.old.moved or row.new.moved for row in rows)
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
            self._colour_cache[key] = highlighted(core, core.file_lines(version, path) or [], path)
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
            self.cursor = self.stop_of(min(self.cursor, max(len(self.rows) - 1, 0)))
            return
        row, anchor = self._keep
        self._keep = None
        candidates = [row, ('diff', max(anchor, 0))]
        if 'thread' == row[0]:
            candidates.insert(1, ('thread', row[1], 0))
        index = next((self.rows.index(candidate) for candidate in candidates
                      if candidate in self.rows), 0)
        self.cursor = index = self.stop_of(index)
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
        gets thin lines in the columns reserved left and right of the code,
        a highlighted line number and a subtle tint, on the cursor's side
        only.
        """
        result = []
        parts = self.lines[index]
        separator = next((number for number, part in enumerate(parts)
                          if HALF_SEPARATOR == part[0]), None)
        is_thread = index < len(self.rows) and 'thread' == self.rows[index][0]
        for number, (text, foreground, background, styles) in enumerate(parts):
            on_side = separator is None or (number < separator if 'old' == self.side
                                            else number > separator)
            mark = highlighted and on_side
            if foreground in (LEFT_MARK, RIGHT_MARK):
                result.append((('▏' if LEFT_MARK == foreground else '▕') if mark else ' ',
                               self._pairs.attribute(FRAME_COLOUR if curses.COLORS >= 256
                                                     else -1, -1)))
                continue
            if LINE_NUMBER == foreground:
                result.append((text, (self._pairs.attribute(255, LINE_NUMBER_BACKGROUND)
                                      if curses.COLORS >= 256 else curses.A_REVERSE)
                               | curses.A_BOLD if mark
                               else self._pairs.attribute(SECONDARY, -1)))
                continue
            if is_thread and not text.strip(HALF_SEPARATOR + ' '):
                # A thread is tinted, not the blanks around it.
                on_side = False
            if highlighted and on_side:
                background = cursor_tint(background)
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
        self.set_cursor(index, height)

    def stop_of(self, index):
        """The cursor stop of a row: a thread is one stop, at its first
        row. Every other row is a stop of its own.
        """
        while 0 < index < len(self.rows) and 'thread' == self.rows[index][0] \
                and self.rows[index][2] > 0:
            index -= 1
        return index

    def stop_end(self, index):
        """The last row of the stop at row index."""
        row = self.rows[index] if index < len(self.rows) else ('diff', 0)
        if 'thread' != row[0]:
            return index
        while index + 1 < len(self.rows) and self.rows[index + 1][:2] == row[:2]:
            index += 1
        return index

    def _stops(self):
        """The rows the cursor stops on."""
        return [index for index, row in enumerate(self.rows) if 'thread' != row[0] or 0 == row[2]]

    def set_cursor(self, index, height, upwards=False):
        """Puts the cursor on the stop of a row and scrolls it into view,
        all of it if it fits. A taller thread shows its start, or its end
        when the cursor came from below (upwards), to read on from there.
        """
        self.cursor = self.stop_of(max(0, min(index, len(self.rows) - 1)))
        end = self.stop_end(self.cursor)
        if upwards and end - self.cursor + 1 > height:
            self.scroll = max(end - height + 1, 0)
        elif self.cursor < self.scroll:
            self.scroll = self.cursor
        elif end >= self.scroll + height:
            self.scroll = max(min(self.cursor, end - height + 1), 0)

    def move_cursor(self, steps, height):
        """Moves the cursor by stops, a whole thread being one, and
        scrolls so it stays visible. In a thread taller than the pane, each
        step first scrolls one row through it, until its end (or start)
        shows.
        """
        if not self.rows:
            self.cursor = 0
            return
        if not steps:
            self.set_cursor(self.cursor, height)
            return
        direction = 1 if steps > 0 else -1
        for _ in range(abs(steps)):
            start = self.stop_of(min(self.cursor, len(self.rows) - 1))
            end = self.stop_end(start)
            if end - start + 1 > height:
                if direction > 0 and end >= self.scroll + height:
                    self.scroll += 1
                    continue
                if direction < 0 and start < self.scroll:
                    self.scroll -= 1
                    continue
            stops = self._stops()
            position = stops.index(start) if start in stops else 0
            self.set_cursor(stops[max(0, min(position + direction, len(stops) - 1))], height,
                            direction < 0)

    def scroll_rows(self, rows, height):
        """Scrolls the view by rows without moving the cursor, unless it
        would leave the pane: then it goes to the nearest stop shown.
        """
        if not self.rows:
            return
        self.scroll = max(0, min(self.scroll + rows, max(len(self.rows) - height, 0)))
        if self.stop_end(self.cursor) < self.scroll:
            self.cursor = next((stop for stop in self._stops() if stop >= self.scroll),
                               self.cursor)
        elif self.cursor >= self.scroll + height:
            self.cursor = self.stop_of(self.scroll + height - 1)

    def is_tall(self, height):
        """Whether the cursor's stop is a thread taller than the pane."""
        return self.stop_end(self.cursor) - self.cursor + 1 > height

    def scroll_by(self, rows, height):
        """Scrolls the view by rows, the cursor along at the same place of
        the pane (^d, ^u, the wheel). A thread taller than the pane is read
        this way, the cursor stays on it while any of it is shown.
        """
        if not self.rows:
            return
        offset = max(self.cursor - self.scroll, 0)
        scroll = max(0, min(self.scroll + rows, max(len(self.rows) - height, 0)))
        if scroll == self.scroll:
            # At the end: the cursor moves instead.
            self.move_cursor(1 if rows > 0 else -1, height)
            return
        self.scroll = scroll
        self.cursor = self.stop_of(max(0, min(scroll + offset, len(self.rows) - 1)))

    def is_on_cursor(self, index):
        """Whether a row belongs to the cursor's stop."""
        return self.cursor <= index <= self.stop_end(self.cursor)

    def _jump(self, index, height):
        """Puts the cursor on a row, a third down the pane."""
        self.set_cursor(index, height)
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

    def open_gaps_with(self, core, search):
        """Opens the gaps of common lines with a match of a search. show()
        renders them next time.
        """
        if not self._closed_gaps:
            return
        old, old_path, new, path = self._sources
        self.expanded_gaps |= core.gaps_with_matches(
            self._closed_gaps, core.file_lines(old, old_path) or [],
            core.file_lines(new, path) or [], search)

    def go_to_match(self, core, search, forward, height, mode='shown'):
        """Puts the cursor on the next (or previous) line with a match of
        a search, on the side of the match, after the cursor's side of its
        line. Searches the diff lines, the threads or both, by mode (see
        core.SEARCH_MODES), a thread is one place. Goes on from the other
        end. Returns None if nothing matches, else whether it went on from
        the other end.
        """
        line = self.cursor
        while 0 < line and 'diff' == self.rows[line][0] and self._continues[self.rows[line][1]]:
            line -= 1
        here = (line, 'old' != self.side)
        places = []
        if 'comments' != mode:
            places += [(index, side) for index, row in enumerate(self.rows)
                       if 'diff' == row[0] and self._texts[row[1]]
                       for side in (0, 1)
                       if core.search_matches(self._texts[row[1]][side], search)]
        if mode in ('all', 'comments'):
            places += sorted({(self.stop_of(index), 1) for index, row in enumerate(self.rows)
                              if 'thread' == row[0] and core.search_matches(
                                  ''.join(part[0] for part in self.lines[index]), search)})
            places.sort()
        if not places:
            return None
        after = ([place for place in places if place > here] if forward
                 else [place for place in places if place < here][::-1])
        index, side = after[0] if after else places[0 if forward else -1]
        self._jump(index, height)
        if 'diff' == self.rows[index][0]:
            self.side = ('old', 'new')[side]
        return not after

    def go_to_next_thread(self, forward, height, numbers=None, from_edge=False):
        """Puts the cursor on the first row of the next (or previous)
        thread, only of the threads with these numbers if given. from_edge
        starts from the top (or the bottom). Returns whether there is one.
        """
        starts = [index for index, row in enumerate(self.rows)
                  if 'thread' == row[0] and 0 == row[2] and (numbers is None or row[1] in numbers)]
        if from_edge and starts:
            self._jump(starts[0] if forward else starts[-1], height)
            return True
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
            self.cursor = self.stop_of(index)

    def line_end(self, index):
        """The last row of the line at row index, after the rows it wrapped
        onto.
        """
        while (index + 1 < len(self.rows) and 'diff' == self.rows[index + 1][0]
               and self._continues[self.rows[index + 1][1]]):
            index += 1
        return index

    def line_above(self, index):
        """The first row of the line above the line at row index: the
        nearest one with a line number, over threads, gaps and the rows a
        line wrapped onto. None at the top.
        """
        while (0 < index < len(self.rows) and 'diff' == self.rows[index][0]
               and self._continues[self.rows[index][1]]):
            index -= 1
        index -= 1
        while index >= 0:
            if 'diff' == self.rows[index][0] and self.numbers[index] != (None, None):
                while (0 < index and 'diff' == self.rows[index][0]
                       and self._continues[self.rows[index][1]]):
                    index -= 1
                return index
            index -= 1
        return None

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


def in_half(parts, side, left_width):
    """The parts of a thread's row moved into one half of the diff: the
    other half blank, the halves' separator between them. left_width is
    the width of the old half.
    """
    # The separator is joined to the blanks, so the row counts as one whole
    # for the cursor's side.
    if 'old' == side:
        used = sum(len(part[0]) for part in parts)
        return parts + [(' ' * max(left_width - used, 0) + HALF_SEPARATOR, SECONDARY, -1, 0)]
    return [(' ' * left_width + HALF_SEPARATOR, SECONDARY, -1, 0)] + parts


def patch_set_count(count):
    """The header's "3 patch sets"; the p key lists them."""
    return f'{count} patch set{"" if 1 == count else "s"}'


def thread_rows(comment, width, expanded, core, note='', origin='', patch_sets=()):
    """A thread as rows of the diff, each a list of (text, foreground,
    background, styles): a box with a border in the status' colour and a
    tinted inside, the reviewer's and the author's names in two colours.
    Folded it is one row with the start of the first message. note goes
    into the title, e.g. that its line isn't shown. origin is the older
    patch set the thread was made on, like PS2, also in the folded title.
    """
    status = comment_label(comment)
    origin = f' {origin}' if origin else ''
    colour = STATUS_COLOURS['draft' if comment.is_draft() else comment.status]
    tint = THREAD_TINT if curses.COLORS >= 256 else -1
    # The first column and the last are the cursor's frame, see LEFT_MARK.
    pad = [(' ', LEFT_MARK, -1, 0), (' ' * (THREAD_INDENT - 1), -1, -1, 0)]
    # Indent, the two borders and one space: the rows are as wide as the pane
    inner = max(width - THREAD_INDENT - 4, 20)
    title = f' #{comment.number} {status}{origin} '

    def title_parts(background):
        # Only the status is bold: if everything is bold, nothing is.
        return [(f' #{comment.number} ', colour, background, 0),
                (status, colour, background, curses.A_BOLD),
                (origin, SECONDARY, background, 0), (' ', colour, background, 0)]

    if not expanded:
        first = core.message_text(comment.messages[0]).split('\n')[0] if comment.messages else ''
        rest = f' {first}'[:max(inner - len(title), 0)]
        return [[*pad, ('▸', colour, tint, 0), *title_parts(tint),
                 (rest.ljust(inner + 1 - len(title)), SECONDARY, tint, 0),
                 (' ', RIGHT_MARK, -1, 0)]]
    border = (colour, -1, 0)
    # A long note, like the line the thread was on, goes on below the
    # title, so it can always be read whole.
    room = max(inner - len(title) - 3, 0)
    note_rows = []
    if note and len(note) > room:
        # Whole onto rows of its own, never split between title and body
        note_rows = textwrap.wrap(note, inner)
        note = ''
    extra = f' {note} ' if note else ''
    top = '─' * max(inner - len(title) - len(extra), 0)
    rows = [[*pad, ('┌─', *border), *title_parts(-1),
             (extra, SECONDARY, -1, 0), (top + '┐', *border)]]
    for line in note_rows:
        rows.append([*pad, ('│', *border), (' ' + line.ljust(inner), SECONDARY, -1, 0),
                     ('│', *border)])
    for message_index, message in enumerate(comment.messages):
        reviewer = core.REVIEWER == message.author
        name = ('Reviewer' if reviewer else 'Author') + (' (draft)' if not message.sent else '')
        name_colour = (STATUS_COLOURS['draft'] if not message.sent
                       else tone(REVIEWER_COLOURS if reviewer else AUTHOR_COLOURS))
        # The patch set the message belongs to, like Gerrit's "Patchset 3"
        patch_set = (f' · PS{patch_sets[message_index]}'
                     if message_index < len(patch_sets) and patch_sets[message_index] else '')
        # The reviewer's own messages, the viewer's, get a bar in their
        # colour on the left, so they stand out from the author's.
        bar = ('▎', name_colour, tint, 0) if reviewer else (' ', -1, tint, 0)
        # The name on a row of its own, like Gerrit, the text below it at
        # the box's whole width
        rows.append([*pad, ('│', *border), bar, (name, name_colour, tint, 0),
                     (patch_set.ljust(inner - len(name)), SECONDARY, tint, 0), ('│', *border)])
        lines = [line for paragraph in core.message_text(message).split('\n')
                 for line in textwrap.wrap(paragraph, max(inner - 1, 10)) or ['']]
        for line in lines:
            rows.append([*pad, ('│', *border), bar, (' ' + line.ljust(inner - 1), -1, tint, 0),
                         ('│', *border)])
    rows.append([*pad, ('└' + '─' * (inner + 1) + '┘', *border)])
    return [row + [(' ', RIGHT_MARK, -1, 0)] for row in rows]


# ── the change screen ─────────────────────────────────────────────────────

STACK = 'stack'
MESSAGE = 'message'
FILES = 'files'
COMMENTS = 'comments'
DIFF = 'diff'
PANES = (STACK, MESSAGE, FILES, COMMENTS, DIFF)
# The commit message box shows at most this many rows
MESSAGE_ROWS = 6

def thread_footer(comment):
    """The footer's actions on a thread: reply or edit the draft, Done,
    Ack, Resolved.
    """
    if comment is None:
        return []
    if comment.has_draft():
        return [(('edit-draft',), 'edit draft'), (('resolve',), 'resolved')]
    return [(('reply',), 'reply'), (('done',), 'Done'), (('ack',), 'Ack'),
            (('resolve',), 'resolved')]


# The modes of the setting tui.sidebar. auto hides the left column on
# screens narrower than this.
SIDEBAR_MODES = ('auto', 'shown', 'hidden')
SIDEBAR_MIN_WIDTH = 100


def sidebar_shown(mode, width):
    """Whether the change screen shows its left column."""
    return 'shown' == mode or ('auto' == mode and width >= SIDEBAR_MIN_WIDTH)


class PatchSetView:
    """The patch sets of a change, like Gerrit's patch set list: when, by
    whom, their type, votes and comments. A row unfolds into what happened
    on it. Chooses the versions the change screen compares. The history
    comes from core.patch_set_history().
    """

    TOP = 3

    def __init__(self, interface, change_view):
        self._interface = interface
        self._view = change_view
        core = interface.core
        (first, _), (second, _) = core.versions_to_compare(change_view.change,
                                                           *change_view.compare)
        # The chosen sides: base or a patch set number, CURRENT on the right
        self._a, self._b = first, second
        self._expanded = set()
        self._history = []
        # Where the reviewer's newest send ends, see core.last_send(). The
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
        core = self._interface.core
        self._history = core.patch_set_history(change, shown)
        self._last_send = core.last_send(self._history)
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
        core = self._interface.core
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
            when = time.strftime('%Y-%m-%d %H:%M', time.localtime(entry.uploaded)) \
                if entry.uploaded else '-'
            fold = '▾' if entry.number in self._expanded else '▸'
            out(row, 6, f'{fold} PS{entry.number:<3} {when:<17} {entry.uploader:<12.12} '
                f'{entry.kind:<15} {entry.vote_label(core.CODE_REVIEW):<6} '
                f'{entry.vote_label(core.VERIFIED):<6} ', attributes)
            # A folded patch set holding the newest send says so after its
            # counts, which get shorter first when the row is narrow.
            holds_send = (self._last_send and self._last_send[0] == entry.number
                          and entry.number not in self._expanded)
            marks = (' ◂ your last send', ' ◂ last send', ' ◂') if holds_send else ('',)
            labels = (entry.comments_label(), entry.comments_label(compact=True))
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
        who = 'Reviewer' if self._interface.core.REVIEWER == event.author else (
            'Author' if self._interface.core.AUTHOR == event.author else event.author)
        if 'vote' == event.kind:
            text = f'{when:<11} {who}: {event.text}'
            out_attributes = attributes | self._interface.dim(attributes)
        else:
            label = ('draft' if event.draft else event.status)
            what = 'reply on' if 'reply' == event.kind else 'thread'
            text = (f'{when:<11} {what} #{event.number} [{label}] '
                    f'{self._interface.core.place_label(event.place)}  {who}: '
                    f'{event.text}')
            out_attributes = attributes | (status_attribute('unresolved') | curses.A_BOLD
                                           if event.is_open() else
                                           status_attribute('draft') if event.draft else 0)
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


# What the change screen compares at first: the whole change as of the
# latest patch set
DEFAULT_COMPARE = ('base', 'latest')


class ChangeView:
    """One change: files and threads on the left, the diff on the right."""

    def __init__(self, interface, change_id, repository=None):
        self._interface = interface
        self._change_id = change_id
        self._repository = repository
        self._change = None
        self._files = []
        self._compare = DEFAULT_COMPARE
        self._focus = FILES
        # The pane h returns to from the diff
        self._left_pane = FILES
        # None until the change is read: then the first file after the
        # commit message, see reload()
        self._file_index = None
        self._comment_index = 0
        self._diff = DiffPane(interface.colour_pairs)
        self._layout = None
        # Threads folded or unfolded by hand: number -> expanded
        self._expanded = {}
        self._renames = {}
        self._counts = {}
        # The changes of the stack, oldest first, and whether it needs a
        # rebase: None, 'base moved' or 'conflicts'
        self._stack = []
        # The cursor in the stack pane, counted from its top (the newest)
        self._stack_index = 0
        # (compare, file index, thread number) to return to after a thread
        # was shown on its own patch set, and the file shown there if this
        # version doesn't have it
        self._previous_compare = None
        self._extra_file = None
        # The latest patch set at the last reload, to see a new upload
        self._latest_seen = None
        # The commit message (subject, body, trailers) and how far its box
        # is scrolled
        self._message = ('', '', [])
        self._message_scroll = 0
        # The Change-Ids of the stack that could be submitted now, and those
        # where it is the user's turn
        self._submittable = set()
        self._yours = set()
        self._behind = None

    def footer(self):
        """Only the actions that work right now: those of the focused pane
        and what the cursor is on, then those the change's state allows.
        """
        change = self._change
        core = self._interface.core
        if STACK == self._focus:
            entries = [(('down', 'up'), 'move'), (('open',), 'show change')]
        elif MESSAGE == self._focus:
            entries = [(('down', 'up'), 'scroll'), (('open',), 'comment on it')]
        elif FILES == self._focus:
            entries = [(('open',), 'diff'), (('reviewed',), 'reviewed'),
                       (('file-comment',), 'comment')]
        elif COMMENTS == self._focus:
            entries = [(('open',), 'go to')] + thread_footer(self._selected_comment())
        elif self._diff.rows and self._diff.thread_at(self._diff.cursor) is not None:
            entries = thread_footer(self._thread_at_cursor()) + [
                (('comment',), 'new thread'),
                (('open',), 'fold' if self._is_expanded(self._thread_at_cursor()) else 'unfold')]
            if self._diff.is_tall(self._diff_height()):
                entries.insert(0, (('down', 'up'), 'read on'))
        elif self._diff.rows and self._diff.gap_at(self._diff.cursor) is not None:
            entries = [(('open',), 'expand')]
        else:
            entries = [(('comment',), 'comment'), (('side-old', 'side-new'), 'side'),
                       (('next-chunk', 'prev-chunk'), 'chunk')]
            if change.unfinished_comments():
                entries.append((('next-unresolved', 'prev-unresolved'), 'unresolved'))
            if self._interface.marking:
                entries += [(('search-next', 'search-prev'), 'match'), (('search-clear',), 'unmark')]
            if self._threads_at_cursor():
                entries.append((('open',), 'unfold'))
        if any(not message.sent for comment in change.comments for message in comment.messages):
            entries.append((('send',), 'send'))
        sent = core.last_send_patch_set(change)
        if sent is not None and sent != change.latest_patch_set() and \
                (self._compare[0], core.right_side(change, self._compare[1])) != (
                    str(sent), core.LATEST):
            entries.append((('compare-last-send',), 'since last send'))
        if not change.submit_problems():
            entries.append((('submit',), 'submit'))
        elif not change.is_approved(core.CODE_REVIEW):
            entries.append((('approve',), '+2'))
        if self._behind:
            entries.append((('rebase',), 'rebase'))
        if len(self._stack) > 1:
            entries.append((('next-change', 'prev-change'), 'stack'))
        thread = self._target_thread()
        if self._previous_compare is not None:
            entries.insert(0, (('thread-patch-set',), 'back'))
        elif thread is not None and self._place_of(thread).origin:
            entries.insert(0, (('thread-patch-set',), f'on PS{thread.patch_set}'))
        return entries + [(('sidebar',), 'sidebar'), (('help',), 'all keys')]

    def reload(self):
        """Reads the change again. Goes back if it is gone."""
        self._interface.core.use_repository(self._repository)
        core = self._interface.core
        for change in core.changes_in_stack():
            if change.change_id == self._change_id:
                self._change = change
                if not core.can_compare(change, *self._compare):
                    # E.g. a patch set chosen before is gone after a reload.
                    self._compare = DEFAULT_COMPARE
                    self._previous_compare = None
                    self._interface.say('The patch sets compared before are gone, '
                                        'showing base -> latest.')
                now = change.latest_patch_set()
                if self._latest_seen is not None:
                    self._compare, moved = core.follow_latest(
                        self._compare, self._latest_seen, now, self._interface.follow_latest)
                    if moved:
                        self._interface.say(f'PS{now} uploaded, now ' + core.compare_label(
                            change, *self._compare))
                self._latest_seen = now
                # The commit message comes first, like in Gerrit.
                # A renamed file is listed once, under its new name.
                self._renames = core.renames_of(change.commit)
                self._counts = core.files_of(change.commit)
                self._files = [core.COMMIT_MSG] + sorted(
                    set(core.files_of(change.commit)) - set(self._renames.values()))
                if self._extra_file and self._extra_file not in self._files:
                    self._files.append(self._extra_file)
                if self._file_index is None:
                    # The commit message stays first, the first real file is shown.
                    self._file_index = 1 if len(self._files) > 1 else 0
                self._file_index = min(self._file_index, max(len(self._files) - 1, 0))
                self._comment_index = min(self._comment_index, max(len(change.comments) - 1, 0))
                self._stack = [other for other in core.changes_in_stack()
                               if other.topic == change.topic]
                if STACK != self._focus:
                    self._stack_index = self._current_in_stack()
                self._submittable = core.submittable_in(self._stack)
                self._message = core.commit_message_parts(change.commit)
                # Where it is the user's turn, like the dashboard's "▶ you"
                user = core.current_user()
                self._yours = {other.change_id for other in self._stack
                               if 'your-turn' in other.sections(user)}
                self._behind = ('conflicts' if any(other.rebase_requested for other in self._stack)
                                else 'base moved' if core.needs_rebase(change.topic) else None)
                self._diff.forget()
                return
        self._interface.back()
        self._interface.say('The change is not in review any more.')

    # ── layout and drawing ────────────────────────────────────────────────

    def _current_in_stack(self):
        """The row of the shown change in the stack pane, newest on top."""
        shown = list(reversed(self._stack))
        return next((index for index, other in enumerate(shown)
                     if other.change_id == self._change_id), 0)

    def _stack_first(self, height):
        """The first row of the stack the pane shows: the cursor stays
        visible, in the middle if it can.
        """
        return max(0, min(self._stack_index - height // 2, len(self._stack) - height))

    def _compute_layout(self, area):
        """Positions of the four panes: (top, left, height, width) each,
        the inside without the frame.
        """
        if not sidebar_shown(self._interface.sidebar, area.width):
            if DIFF != self._focus:
                self._focus = DIFF
            return {DIFF: (3, 1, area.height - 4, area.width - 3)}
        left_width = min(max(area.width * 3 // 10, 28), 50)
        # The stack on top, like Gerrit's relation chain, at most 5 rows
        stack_rows = min(max(len(self._stack), 1), 5)
        # The commit message below, as tall as it is up to MESSAGE_ROWS. The
        # files and comments come first when there is little room.
        message_top = 3 + stack_rows + 2
        room = area.height - message_top - 1 - 2 - 2 * (4 + 2)
        message_rows = max(1, min(MESSAGE_ROWS, len(self._message_lines(left_width - 2)), room))
        files_top = message_top + message_rows + 2
        files_height = max((area.height - files_top - 2) // 2, 2)
        comments_top = files_top + files_height + 2
        return {STACK: (3, 1, stack_rows, left_width - 2),
                MESSAGE: (message_top, 1, message_rows, left_width - 2),
                FILES: (files_top, 1, files_height, left_width - 2),
                COMMENTS: (comments_top, 1, max(area.height - comments_top - 1, 1),
                           left_width - 2),
                DIFF: (3, left_width + 1, area.height - 4, area.width - left_width - 3)}

    def _frame(self, pane, title):
        """Draws the frame around a pane, with its title."""
        out = self._interface.text
        top, left, height, width = self._layout[pane]
        attributes = curses.A_BOLD if pane == self._focus else self._interface.dim()
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
        bar = self._interface.colour_pairs.attribute(15, tone(HEADER_BACKGROUND))
        header_dim = self._interface.dim(background=tone(HEADER_BACKGROUND))
        out(0, 0, ' ' * area.width, bar)
        place = (f'{change.repo} › ' if change.repo else '') + f'{change.topic} on {change.base} ›'
        out(0, 1, place, header_dim)
        out(0, 2 + len(place), change.subject, bar | curses.A_BOLD)
        state = f' {change.state()} '
        chip = self._interface.colour_pairs.attribute(
            0, {'New': 8, 'In-Review': 3, 'Submitted': 2}.get(change.state(), 8)) | curses.A_BOLD
        version = f'{change.short_id()}  {core.version_label(change)} '
        out(0, max(area.width - len(version) - len(state) - 1, 1), state, chip)
        if self._behind:
            behind = (' Needs rebase ' if 'base moved' == self._behind
                      else ' Conflicts, the author rebases ')
            out(0, max(area.width - len(version) - len(state) - len(behind) - 2, 1), behind,
                self._interface.colour_pairs.attribute(0, 1 if 'conflicts' == self._behind
                                                       else 3) | curses.A_BOLD)
        drafts = change.draft_count()
        if drafts:
            # Unsent drafts, in the drafts' colour, so they aren't forgotten
            label = f' {drafts} draft{"" if 1 == drafts else "s"} '
            behind_width = len(behind) + 1 if self._behind else 0
            out(0, max(area.width - len(version) - len(state) - behind_width - len(label) - 2,
                       1), label,
                self._interface.colour_pairs.attribute(0, STATUS_COLOURS['draft'])
                | curses.A_BOLD)
        out(0, max(area.width - len(version), 1), version, header_dim)
        patch_sets = patch_set_count(len(change.patch_sets))
        try:
            follow = self._interface.follow_latest
            comparing = core.compare_label(change, *self._compare, follow=follow)
            if len(patch_sets) + 3 + len(comparing) > area.width - 2:
                comparing = core.compare_label(change, *self._compare, follow=follow, short=True)
        except core.ChangeError as error:
            comparing = str(error)
        header = f'{patch_sets}   {comparing}'
        if FILES not in self._layout:
            # Without the files pane, the header says which file it is,
            # with fewer words when the line is narrow.
            label = file_label(core, self._selected_file(), self._renames)
            where = f'file {self._file_index + 1}/{len(self._files)}: '
            sidebar = f'sidebar hidden ({self._interface.keymap.label("sidebar")}), '
            for extra in (sidebar + where + label, where + label,
                          where + label.rsplit('/', 1)[-1]):
                if len(header) + 3 + len(extra) <= area.width - 2:
                    break
            header += '   ' + extra
            header = header[:max(area.width - 2, 0)]
        out(1, 1, header, self._interface.dim())
        self._draw_votes(4 + len(header))
        if FILES in self._layout:
            self._draw_stack()
            self._draw_message()
            self._draw_files()
            self._draw_comments()
        self._draw_diff()

    def _draw_stack(self):
        """The changes of the stack, newest on top like Gerrit's relation
        chain, this one marked, the cursor row highlighted while focused.
        """
        out = self._interface.text
        base = self._change.base
        self._frame(STACK, f'Stack ({len(self._stack)}) on {base}')
        top, left, height, width = self._layout[STACK]
        shown = list(reversed(self._stack))
        first = self._stack_first(height)
        for row, other in enumerate(shown[first:first + height]):
            is_current = other.change_id == self._change_id
            attributes = curses.A_BOLD if is_current else self._interface.dim()
            if other.change_id in self._yours:
                # In the turn colour of the dashboard
                attributes = curses.A_BOLD | curses.color_pair(3)
            if first + row == self._stack_index and STACK == self._focus:
                attributes = curses.A_REVERSE | (curses.A_BOLD if is_current else 0)
                out(top + row, left, ' ' * width, attributes, left + width)
            marker = ''
            if other.change_id in self._submittable:
                # The subject gets shorter, not the marker
                marker = (' (submittable)' if width >= 30 else ' (sub)' if width >= 16 else ' ✓')
            subject = other.subject[:max(width - 2 - len(marker), 0)]
            mark = '▶ ' if is_current else '› ' if other.change_id in self._yours else '  '
            out(top + row, left, mark + subject, attributes, left + width)
            if marker:
                # Always green, at the right end. Its own colour pair, not
                # added to the row's, which would mix the two.
                own = (attributes & (curses.A_BOLD | curses.A_REVERSE)) | curses.color_pair(2)
                out(top + row, left + width - len(marker), marker, own, left + width)

    def _message_lines(self, width):
        """The commit message as rows of the box: (text, attributes), the
        subject bold, the trailers dimmed.
        """
        subject, body, trailers = self._message
        width = max(width, 10)
        lines = [(line, curses.A_BOLD) for line in textwrap.wrap(subject, width) or ['']]
        if body:
            lines.append(('', 0))
            lines += [(line, 0) for paragraph in body.split('\n')
                      for line in textwrap.wrap(paragraph, width) or ['']]
        if trailers:
            lines.append(('', 0))
            lines += [(line[:width], self._interface.dim()) for line in trailers]
        return lines

    def _draw_message(self):
        """The box with the commit message, scrolled while focused."""
        out = self._interface.text
        top, left, height, width = self._layout[MESSAGE]
        lines = self._message_lines(width)
        self._message_scroll = max(0, min(self._message_scroll, len(lines) - height))
        more = len(lines) - height
        self._frame(MESSAGE, 'Commit message' + (f'  ({self._message_scroll + height}/'
                                                 f'{len(lines)})' if more > 0 else ''))
        for row, (text, attributes) in enumerate(
                lines[self._message_scroll:self._message_scroll + height]):
            out(top + row, left, text, attributes, left + width)

    def leaving_note(self):
        """What the reviewer should know when leaving the change: drafts
        not sent yet.
        """
        drafts = self._change.draft_count() if self._change else 0
        if not drafts:
            return ''
        return (f'{drafts} draft{"" if 1 == drafts else "s"} of {self._change.short_id()} '
                f'not sent, {self._interface.keymap.label("send")} sends.')

    def _switch_to(self, change_id):
        """Shows another change of the stack."""
        note = self.leaving_note() if change_id != self._change_id else ''
        if note:
            self._interface.say(note)
        self._change_id = change_id
        self._file_index = None
        self._comment_index = 0
        self._expanded = {}
        # Like in Gerrit, every change opens with its default comparison.
        self._compare = DEFAULT_COMPARE
        self._previous_compare = self._extra_file = None
        self._latest_seen = None
        # From the stack pane, the focus stays there to go on.
        if STACK != self._focus:
            self._focus = self._left_pane = FILES
        self._diff = DiffPane(self._interface.colour_pairs)
        self.reload()
        self._stack_index = self._current_in_stack()

    def _draw_votes(self, column):
        """Draws the votes on the latest patch set after the header, like
        "Code-Review +2  Verified -1 ci: make check failed".
        """
        out = self._interface.text
        core = self._interface.core
        change = self._change
        for label in (core.CODE_REVIEW, core.VERIFIED):
            badge = vote_badge(self._interface, label, change.label_value(label),
                               core.VERIFIED == label and change.verified_required)
            if badge:
                out(1, column, badge[0], badge[1])
                column += len(badge[0]) + 1
        # A failed check says why, in short.
        failed = [vote for vote in change.current_votes(core.VERIFIED) if vote.value < 0]
        if failed and failed[0].message:
            lines = failed[0].message.strip().split('\n')
            out(1, column, f'{failed[0].by}: {lines[-1]}', self._interface.dim())

    def _draw_files(self):
        """Draws the file list."""
        out = self._interface.text
        core = self._interface.core
        reviewed = [core.is_reviewed(self._change, path) for path in self._files]
        self._frame(FILES, f'Files  {sum(reviewed)}/{len(self._files)} reviewed')
        top, left, height, width = self._layout[FILES]
        # The counts between the compared versions. Files the comparison
        # doesn't change are grey.
        try:
            compared = core.compared_files(self._change, *self._compare)
        except core.ChangeError:
            compared = self._counts
        for index, path in enumerate(self._files[:height]):
            selected = index == self._file_index
            attributes = curses.A_REVERSE if selected and FILES == self._focus else (
                curses.A_BOLD if selected else 0)
            name = file_label(core, path, self._renames)
            out(top + index, left, '✓ ' if reviewed[index] else '  ',
                attributes | curses.color_pair(2), left + width)
            if path not in compared:
                out(top + index, left + 2, f'{name:<{width - 2}.{width - 2}}',
                    attributes | self._interface.dim(attributes), left + width)
                continue
            # Like Gerrit's size column: a short bar of added and removed lines
            added, removed = compared[path]
            scale = max(added + removed, 1) / 5 if added + removed > 5 else 1
            plus, minus = round(added / scale), round(removed / scale)
            bar_width = 6 if added + removed else 0
            out(top + index, left + 2, f'{name:<{width - 2 - bar_width}.{width - 2 - bar_width}}',
                attributes, left + width)
            if bar_width:
                out(top + index, left + width - 5, '+' * plus, curses.color_pair(2), left + width)
                out(top + index, left + width - 5 + plus, '-' * minus, curses.color_pair(1),
                    left + width)

    def _draw_comments(self):
        """Draws the thread list: unresolved first, each with ↩ when the
        author answered last and the start of its last message.
        """
        out = self._interface.text
        core = self._interface.core
        self._frame(COMMENTS, 'Comments')
        top, left, height, width = self._layout[COMMENTS]
        comments = self._comments()
        if not comments:
            out(top, left, 'none yet', self._interface.dim(), left + width)
        first = max(0, min(self._comment_index - height + 1, len(comments) - height))
        # For clicks
        self._comments_first = first
        for row, comment in enumerate(comments[first:first + height]):
            index = first + row
            selected = index == self._comment_index
            attributes = curses.A_REVERSE if selected and COMMENTS == self._focus else 0
            label = comment_label(comment)
            out(top + row, left, ' ' * width, attributes, left + width)
            column = left
            answered = core.answered_by_author(comment)
            parts = [(f'#{comment.number}'.ljust(4), attributes),
                     ('↩ ' if answered else '  ',
                      attributes | curses.color_pair(3)
                      if answered else attributes),
                     (f'[{label}]', attributes | status_attribute(
                         'draft' if comment.is_draft() else comment.status))]
            shown = self._place_of(comment)
            place = comment.place_label().rsplit('/', 1)[-1]
            if core.PATCHSET_LEVEL == shown.path != comment.place:
                # Like in Gerrit, a thread on a gone file is on the whole change.
                place = f'{core.CHANGE_PLACE} ({shown.origin}, {shown.note})'
            parts.append((f' {place}', attributes))
            # The last message with text, not one that only changed the status
            text = next((' '.join(message.text.split()) for message in reversed(comment.messages)
                         if message.text.strip()), '')
            if text:
                parts.append((f'  {text}', attributes | self._interface.dim(attributes)))
            for text, attribute in parts:
                out(top + row, column, text, attribute, left + width)
                column += len(text)

    def _comments(self):
        """The threads in the comments pane's order."""
        return self._interface.core.threads_in_list_order(self._change.comments) \
            if self._change else []

    def _markers(self):
        """Threads on the shown file: (side, line) -> numbers of threads."""
        path = self._selected_file()
        markers = {}
        for comment in self._change.comments:
            shown = self._place_of(comment)
            if shown.path == path and shown.line is not None:
                markers.setdefault((shown.side, shown.line), []).append(comment.number)
        return markers

    def _shown_patch_set(self):
        """The patch set on the right side of the diff, None for the
        current commit.
        """
        (_first, _), (second, _) = self._interface.core.versions_to_compare(self._change,
                                                                           *self._compare)
        return int(second) if second.isdigit() else None

    def _place_of(self, comment):
        """Where the shown diff has a thread, see core.thread_place()."""
        return self._interface.core.thread_place(self._change, comment, *self._compare)

    def _draw_diff(self):
        """Draws the diff of the selected file, with cursor and thread marks."""
        out = self._interface.text
        path = self._selected_file()
        context = CONTEXT_STEPS[self._interface.context_step]
        context_label = 'whole file' if context is None else f'context {context}'
        if 'none' != self._interface.whitespace:
            context_label += f', whitespace: ignore {self._interface.whitespace}'
        if path:
            self._prepare_diff()
        below = ''.join(legend for legend, shown in zip(
            (', blue: from changes below', ', purple: partly from below'),
            self._diff.marks_from_below) if shown)
        if self._diff.has_moved:
            below += ', teal: moved'
        self._frame(DIFF, f'{file_label(self._interface.core, path, self._renames)}  '
                    f'({context_label}{below})'
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
        # A whole thread is the cursor when the cursor is on it.
        is_cursor = self._diff.is_on_cursor(index)
        gutter = ('◀' if 'old' == self._diff.side else '▶') if is_cursor and DIFF == self._focus \
            else ' '
        out(top + row, left, gutter, curses.A_BOLD | curses.color_pair(3))
        out(top + row, left + 1, '●' if has_thread else ' ', status_attribute('unresolved'))
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
        comments = self._comments()
        return comments[min(self._comment_index, len(comments) - 1)] if comments else None

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
                        CONTEXT_STEPS[self._interface.context_step], self._interface.whitespace,
                        self._marked_search('comments' != self._interface.search_mode))
        # The matches in threads are marked when the search looks there.
        thread_search = self._marked_search(self._interface.search_mode in ('all', 'comments'))
        blocks = {}
        signature = [path, width, thread_search]
        for comment in self._change.comments:
            shown = self._place_of(comment)
            if self._diff_path(shown.path) != path:
                continue
            expanded = self._is_expanded(comment)
            place = (shown.side, shown.line) if shown.line is not None else None
            note, origin = shown.note, shown.origin
            if core.PATCHSET_LEVEL == shown.path:
                note = note or core.WHOLE_CHANGE
            if place and place not in self._diff.shown_places():
                note = ((f'{note}, ' if note else '')
                        + f'on {"old " if "old" == shown.side else ""}line {shown.line}, not in the '
                        f'diff ({self._interface.keymap.label("more-context")} shows more '
                        'context)')
            # Like in Gerrit, a thread on a line goes into its side's half.
            # Threads on lines not shown go on top, as wide as the diff.
            side = self._thread_half(comment)
            patch_sets = core.message_patch_sets(self._change, comment, self._shown_patch_set())
            rows = thread_rows(comment, self._half(side)[1], expanded, core, note, origin,
                               patch_sets)
            if side:
                rows = [in_half(parts, side, self._half('old')[1]) for parts in rows]
            if thread_search:
                rows = [marked_segments(core, parts, thread_search) for parts in rows]
            blocks.setdefault(place, []).append((comment.number, rows))
            signature.append((comment.number, comment.status, place, expanded, tuple(patch_sets),
                              tuple((message.author, message.text, message.sent, message.unresolved)
                                    for message in comment.messages)))
        self._diff.arrange(blocks, tuple(signature))
        return True

    def _show_comment_in_diff(self, comment):
        """Selects the thread's file and puts the diff cursor on the thread,
        or on its line if the thread isn't in the diff.
        """
        shown = self._place_of(comment)
        path = self._diff_path(shown.path)
        if path in self._files:
            self._file_index = self._files.index(path)
            if self._prepare_diff():
                height = self._diff_height()
                if not self._diff.go_to_thread(comment.number, height) and shown.line is not None:
                    self._diff.go_to_line(shown.line, shown.side, height)

    def _go_to_thread(self, forward, unresolved):
        """]t and [t: to the next (previous) thread, in the next file with
        threads at the file's end, like going through all of them. ]u and
        [u only to unresolved ones.
        """
        interface = self._interface
        self._focus_pane(DIFF)
        # The diff may not be laid out for this file yet
        self._prepare_diff()
        threads = [comment for comment in self._change.comments
                   if not unresolved or comment.is_unresolved()]
        numbers = {comment.number for comment in threads}
        height = self._diff_height()
        if self._diff.go_to_next_thread(forward, height, numbers):
            return
        paths = {self._diff_path(self._place_of(comment).path) for comment in threads}
        path = interface.core.thread_file_after(self._files, self._selected_file(), paths,
                                                1 if forward else -1)
        kind = 'unresolved threads' if unresolved else 'threads'
        if path is None:
            interface.say(f'No more {kind} in this direction.')
            return
        self._file_index = self._files.index(path)
        self._prepare_diff()
        self._diff.go_to_next_thread(forward, self._diff_height(), numbers, from_edge=True)
        interface.say(f'In {file_label(interface.core, path, self._renames)}.')

    def _diff_path(self, path):
        """The file whose diff shows threads on path: threads on the whole
        change go on top of the commit message, as threads on a whole file
        go on top of their file.
        """
        core = self._interface.core
        return core.COMMIT_MSG if core.PATCHSET_LEVEL == path else path

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
        return self._gap(end, self._thread_half(comment))

    def _thread_half(self, comment):
        """The side whose half a thread is drawn in, None when it is as
        wide as the diff: on the whole file, or on a line the diff doesn't
        show, which go on top. Its boxes take the same width.
        """
        shown = self._place_of(comment)
        place = (shown.side, shown.line)
        return shown.side if shown.line is not None and place in self._diff.shown_places() \
            else None

    def _half(self, side):
        """The columns of one half of the diff's text: (offset, width).
        side None is the whole width.
        """
        width = self._layout[DIFF][3] - 2
        half = (width - len(HALF_SEPARATOR)) // 2
        if side is None:
            return 0, width
        if 'old' == side:
            return 0, half
        return half + len(HALF_SEPARATOR), width - half - len(HALF_SEPARATOR)

    def _gap(self, index, side):
        """The comment box's room below row index, in the half of side
        like the threads there, the whole width for side None.
        """
        if side is None:
            return DiffGap(self._diff, self._layout[DIFF], index)
        top, left, height, _width = self._layout[DIFF]
        offset, width = self._half(side)
        return DiffGap(self._diff, (top, left + 2 + offset, height, width), index)

    def _target_thread(self):
        """The thread keys like y or r act on: the selected one in the
        comments, the one at the cursor in the diff.
        """
        if COMMENTS == self._focus:
            return self._selected_comment()
        if DIFF == self._focus:
            return self._thread_at_cursor()
        return None

    def _thread_patch_set(self):
        """g p: shows the thread at the cursor on its own patch set, the
        diff against its parent, so a thread that lost its line is seen
        where it was written. Again, the comparison before comes back.
        """
        core = self._interface.core
        if self._previous_compare is not None:
            compare, file_index, number = self._previous_compare
            self._previous_compare = self._extra_file = None
            self._compare = compare
            self.reload()
            self._file_index = min(file_index, len(self._files) - 1)
            self.show_thread(number)
            self._interface.say('Back to the comparison before.')
            return
        comment = self._target_thread()
        if comment is None:
            self._interface.say('No thread here.')
            return
        if comment.patch_set not in self._change.patch_sets:
            self._interface.say(f'#{comment.number} is on no uploaded patch set.')
            return
        (first, _), (second, _) = core.versions_to_compare(self._change, *self._compare)
        if core.BASE == first and str(comment.patch_set) == second:
            self._interface.say(f'#{comment.number} is shown on its PS{comment.patch_set}.')
            return
        self._previous_compare = (self._compare, self._file_index, comment.number)
        self.compare = (core.BASE, str(comment.patch_set))
        path, _line = core.split_place(comment.place)
        if path not in self._files and core.PATCHSET_LEVEL != path:
            # A file the change doesn't have any more
            self._extra_file = path
            self._files.append(path)
        self.show_thread(comment.number)
        self._interface.say(f'#{comment.number} on PS{comment.patch_set}. '
                            f'{self._interface.keymap.label("thread-patch-set")} goes back.')

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
        self._interface.core.use_repository(self._repository)
        interface = self._interface
        core = interface.core
        change = self._change
        height = self._diff_height()
        diff_moves = {
            'down': lambda: self._diff.move_cursor(count or 1, height),
            'up': lambda: self._diff.move_cursor(-(count or 1), height),
            'top': lambda: (self._diff.go_to_new_line(count, height) if count
                            else self._diff.set_cursor(0, height)),
            'bottom': lambda: (self._diff.go_to_new_line(count, height) if count
                               else self._diff.set_cursor(len(self._diff.rows) - 1, height)),
            'page-down': lambda: self._diff.scroll_by(height // 2, height),
            'page-up': lambda: self._diff.scroll_by(-(height // 2), height),
            'scroll-down': lambda: self._diff.scroll_rows(count or 1, height),
            'scroll-up': lambda: self._diff.scroll_rows(-(count or 1), height),
        }
        if 'next-pane' == action:
            # Only the panes shown
            panes = [pane for pane in PANES if not self._layout or pane in self._layout]
            self._focus_pane(panes[(panes.index(self._focus) + 1) % len(panes)]
                             if self._focus in panes else DIFF)
        elif 'sidebar' == action:
            interface.sidebar = 'hidden' if self._layout and FILES in self._layout else 'shown'
        elif action in ('next-change', 'prev-change'):
            ids = [other.change_id for other in self._stack]
            index = ids.index(self._change_id) + (1 if 'next-change' == action else -1) * (count or 1)
            if 0 <= index < len(ids):
                self._switch_to(ids[index])
            else:
                interface.say('No more changes in this direction.')
        elif MESSAGE == self._focus and action in LIST_ACTIONS:
            _top, _left, box_height, width = self._layout[MESSAGE]
            self._message_scroll = move(self._message_scroll, action,
                                        max(len(self._message_lines(width)) - box_height + 1, 1),
                                        count)
        elif STACK == self._focus and action in LIST_ACTIONS:
            self._stack_index = move(self._stack_index, action, len(self._stack), count)
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
        elif action in ('next-chunk', 'prev-chunk'):
            self._focus_pane(DIFF)
            # The diff may not be laid out for this file yet
            self._prepare_diff()
            if not self._diff.go_to_chunk('next-chunk' == action, height):
                interface.say('No more changes in this direction.')
        elif action in ('next-thread', 'prev-thread', 'next-unresolved', 'prev-unresolved'):
            self._go_to_thread(action.startswith('next'), action.endswith('unresolved'))
        elif action in ('search', 'search-next', 'search-prev', 'search-clear'):
            self._search(action)
        elif 'open' == action:
            self._open()
        elif 'vimdiff' == action and self._selected_file():
            interface.run_outside(core.diff_command(change, *self._compare, tool=True,
                                                    path=self._selected_file()))
        elif 'patch-sets' == action:
            interface.open(PatchSetView(interface, self))
        elif 'compare-prompt' == action:
            self._choose_patch_sets()
        elif 'compare-last-send' == action:
            try:
                self.compare = core.last_send_compare(change)
            except core.ChangeError as error:
                interface.say(str(error))
            else:
                interface.say('Now ' + core.compare_label(change, *self._compare))
        elif 'follow-latest' == action:
            interface.follow_latest = not interface.follow_latest
            core.set_follows_latest(interface.follow_latest)
            if interface.follow_latest:
                self._compare = (self._compare[0],
                                 core.right_side(change, self._compare[1]))
            interface.say('Follow latest: ' + ('on, a new upload moves the right side.'
                                               if interface.follow_latest
                                               else 'off, the right side stays.'))
        elif action in ('unfold-every', 'fold-every'):
            expand = 'unfold-every' == action
            self._expanded = {comment.number: expand for comment in self._change.comments}
        elif 'fold-all' == action:
            self._toggle_all_threads()
        elif 'comment' == action and DIFF == self._focus and self._diff.gap_at(
                self._diff.cursor) is not None:
            # Like in Gerrit, a gap of common lines opens.
            self._diff.expanded_gaps.add(self._diff.gap_at(self._diff.cursor))
        elif 'whitespace' == action:
            modes = core.WHITESPACE_MODES
            interface.whitespace = modes[(modes.index(interface.whitespace) + 1) % len(modes)]
            interface.say(f'Whitespace: ignore {interface.whitespace}.')
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
                self._diff.cursor) is not None:
            # Several threads on one line are fine: c starts another one.
            self._new_comment(on_thread=self._thread_at_cursor())
        elif 'comment' == action:
            self._new_comment()
        elif 'file-comment' == action:
            self._new_comment(whole_file=True)
        elif 'comment-above' == action:
            if DIFF != self._focus:
                interface.say(f'{interface.keymap.label("comment-above")} comments on the line '
                              'above the cursor, in the diff.')
                return
            index = self._diff.line_above(self._diff.cursor)
            if index is None:
                interface.say('No line above to comment on.')
                return
            self._diff.set_cursor(index, height)
            self._new_comment()
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
        elif 'thread-patch-set' == action:
            self._thread_patch_set()
        else:
            thread_action(interface, change, self._target_thread(), action)

    def _marked_search(self, here):
        """The search whose matches to mark, if they are marked and here is
        a place the search looks, else ''.
        """
        return self._interface.search if self._interface.marking and here else ''

    def _search(self, action):
        """Searches the diff for a text typed in the bottom line, or for the
        last one again, and goes to the next (or previous) match. Opens the
        gaps and unfolds the threads with matches where the mode looks.
        """
        interface = self._interface
        core = interface.core
        if 'search-clear' == action:
            interface.marking = False
            return
        if 'search' == action:
            def next_mode():
                modes = core.SEARCH_MODES
                interface.search_mode = modes[(modes.index(interface.search_mode) + 1)
                                              % len(modes)]
                return search_question(interface.search_mode)

            text = interface.ask(search_question(interface.search_mode),
                                 interface.search_history, next_mode)
            if text is None:
                return
            if text:
                interface.search = text
                if text in interface.search_history:
                    interface.search_history.remove(text)
                interface.search_history.append(text)
        if not interface.search:
            interface.say('Nothing searched yet.')
            return
        interface.marking = True
        mode = interface.search_mode
        self._focus_pane(DIFF)
        self._prepare_diff()
        if mode in ('file', 'all'):
            self._diff.open_gaps_with(core, interface.search)
        if mode in ('all', 'comments'):
            for comment in self._change.comments:
                if self._diff_path(self._place_of(comment).path) == self._selected_file() and \
                        core.comment_matches(comment, interface.search):
                    self._expanded[comment.number] = True
        self._prepare_diff()
        wrapped = self._diff.go_to_match(core, interface.search, 'search-prev' != action,
                                         self._diff_height(), mode)
        if wrapped is None:
            interface.say(f'"{interface.search}" is not in {SEARCH_PLACES[mode]}.', ERROR)
        elif wrapped:
            interface.say('Went on from the ' + ('bottom.' if 'search-prev' == action
                                                 else 'top.'))

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
        number = self._diff.thread_at(self._diff.cursor)
        if (ord('h'),) == self._interface.keymap.last and number is not None \
                and self._is_expanded(self._change.comment(number)):
            # Like vim: l unfolds the thread, h folds it again
            self._expanded[number] = False
            return True
        if self._previous_compare is not None:
            self._thread_patch_set()
            return True
        if self._layout and self._left_pane in self._layout:
            self._focus = self._left_pane
        return True

    def _open(self):
        """l: in the stack, shows the change under the cursor. In the files,
        shows the diff. In the comments, unfolds the thread and goes to it
        in the diff. In the diff, folds or unfolds the thread at the
        cursor.
        """
        comment = self._selected_comment()
        if STACK == self._focus:
            shown = list(reversed(self._stack))
            if self._stack_index < len(shown):
                self._switch_to(shown[self._stack_index].change_id)
        elif MESSAGE == self._focus:
            # /COMMIT_MSG in the diff, to comment on it
            self._file_index = 0
            self._focus_pane(DIFF)
        elif FILES == self._focus:
            self._focus_pane(DIFF)
        elif COMMENTS == self._focus and comment:
            self._expanded[comment.number] = True
            self._show_comment_in_diff(comment)
            if self._diff.thread_at(self._diff.cursor) == comment.number:
                self._focus_pane(DIFF)
            else:
                self._interface.say(f'#{comment.number} is on a file this version doesn\'t have.')
        elif DIFF == self._focus and self._diff.gap_at(self._diff.cursor) is not None:
            self._diff.expanded_gaps.add(self._diff.gap_at(self._diff.cursor))
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
                    if DIFF == pane:
                        # The wheel scrolls, so a tall thread can be read.
                        self._diff.scroll_by(3 if 'down' == wheel_action(kind) else -3, height)
                    else:
                        self.action(wheel_action(kind))
                    return
                offset = row - top
                if STACK == pane and self._stack_first(height) + offset < len(self._stack):
                    self._stack_index = self._stack_first(height) + offset
                elif FILES == pane and offset < len(self._files):
                    self._file_index = offset
                elif COMMENTS == pane and offset + getattr(self, '_comments_first', 0) < len(
                        self._change.comments):
                    self._comment_index = offset + getattr(self, '_comments_first', 0)
                    self._show_comment_in_diff(self._selected_comment())
                elif DIFF == pane:
                    self._diff.cursor = self._diff.stop_of(min(self._diff.scroll + offset,
                                                               max(len(self._diff.rows) - 1, 0)))
                    # A click picks the half of the diff, too.
                    self._diff.side = 'old' if column < left + 2 + (width - 2) // 2 else 'new'
                if DOUBLE_CLICK == kind:
                    self._open()
                return

    @property
    def change(self):
        """The change shown, None before the first reload."""
        return self._change

    @property
    def compare(self):
        """The two versions compared, see core.versions_to_compare()."""
        return self._compare

    @compare.setter
    def compare(self, versions):
        # Checked here: a wrong choice must not break drawing the diff.
        core = self._interface.core
        core.versions_to_compare(self._change, *versions)
        self._compare = (versions[0], core.right_side(self._change, versions[1],
                                                      self._interface.follow_latest))

    def show_thread(self, number):
        """Goes to a thread in the diff, unfolded."""
        comment = self._change.comment(number)
        if comment is None:
            return
        self._expanded[number] = True
        self._show_comment_in_diff(comment)
        if self._diff.thread_at(self._diff.cursor) == number:
            self._focus_pane(DIFF)

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
            self.compare = compare
        else:
            self._interface.say('Give one or two of: patch set numbers, "base", "latest", '
                                '"current".')

    def _new_comment(self, whole_file=False, on_thread=None):
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
        if on_thread is not None:
            # A new thread on the same place, its box below the thread
            shown = self._place_of(on_thread)
            place = (shown.line, shown.side) if shown.line is not None else None
            end = self._diff.thread_end(on_thread.number)
            anchor = self._gap(end, self._thread_half(on_thread)) if end is not None else None
            if place is None:
                whole_file = True
        if on_thread is not None and place is not None:
            line, side = str(place[0]), place[1]
        elif place is None:
            if self._layout:
                # The box opens at the top of the diff, where file threads are.
                anchor = DiffGap(self._diff, self._layout[DIFF], -1)
        else:
            line, side = place
            line = str(line)
            # The box opens right below the commented line, the diff scrolls
            # if needed.
            self._diff.move_cursor(0, self._layout[DIFF][2])
            anchor = self._gap(self._diff.line_end(self._diff.cursor), side)
        name = file_label(interface.core, path).rsplit('/', 1)[-1]
        text = interface.write_text(f'Comment on {name}' + (f':{line}' if line else
                                                            ' (whole file)'), anchor=anchor)
        if not text:
            interface.say('Nothing saved.' if text is None else 'Empty comment, nothing saved.')
            return
        place, side, patch_set = interface.core.comment_place(
            self._change, path, int(line) if line else None, side, *self._compare)
        interface.core.add_comment(self._change_id, place, text, sent=False, side=side,
                                   patch_set=patch_set)
        interface.after_write()
        interface.say(f'Draft saved. {interface.keymap.label("send")} sends all drafts.', OK)


def file_label(core, path, renames=None):
    """A file's name for display. The commit message has no path, a renamed
    file shows its old path too.
    """
    if core.COMMIT_MSG == path:
        return 'Commit message'
    return f'{renames[path]} → {path}' if renames and path in renames else path


def reply_to_thread(interface, change_id, comment, anchor=None):
    """r: writes a reply as a draft, in the comment box at anchor."""
    if comment.has_draft():
        interface.say(f'You have a draft already. {interface.keymap.label("edit-draft")} edits it.', ERROR)
        return
    text = interface.write_text(f'Reply to #{comment.number}', anchor=anchor)
    if text:
        core = interface.core
        core.add_message(change_id, comment.number, core.REVIEWER, text, sent=False)
        interface.after_write()
        interface.say(f'Reply saved as a draft. {interface.keymap.label("send")} sends all drafts.', OK)


def edit_draft(interface, change_id, comment, anchor=None):
    """e: edits the thread's draft. Saving it empty deletes it."""
    if not comment.has_draft():
        interface.say('No draft to edit in this thread.', ERROR)
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
        # Like Gerrit's Reply dialog: the drafts, a message on the whole
        # change and the vote go in one step.
        drafts = change.draft_count()
        choice = None
        if change.patch_sets:
            choice = (f'Code-Review on PS{change.latest_patch_set()}',
                      Choice(core.vote_values(), change.own_vote()))
        message = interface.write_text(
            'Reply', save='send', choice=choice,
            lines=[(f'{drafts} draft{"" if 1 == drafts else "s"} to send. '
                    'A message on the whole change, optional:' if drafts
                    else 'No drafts. A message on the whole change, optional:', False)])
        if message is None:
            interface.say('Nothing sent.')
            return
        count, voted = core.reply(change.change_id, message,
                                  choice[1].value if choice else None)
        interface.after_write()
        done = []
        if count:
            done.append(f'{count} draft{"" if 1 == count else "s"} sent'
                        + (' with a message' if message else ''))
        elif message:
            done.append('message sent')
        if voted:
            value = choice[1].value
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
        text = core.DONE if 'done' == action else core.ACK
        core.add_message(change.change_id, comment.number, core.REVIEWER, text, sent=False,
                         status=core.RESOLVED)
        interface.after_write()
        interface.say(f'"{text}" saved as a draft, it resolves #{comment.number}. '
                      f'{interface.keymap.label("send")} sends.', OK)
    elif 'resolve' == action:
        status = core.toggle_draft_status(change.change_id, comment.number)
        interface.after_write()
        interface.say(f'Sending the draft makes #{comment.number} {status}. '
                      f'{interface.keymap.label("send")} sends.')
