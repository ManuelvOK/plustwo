# SPDX-License-Identifier: Zlib
"""What the terminal can do: its background, true colour, raw keys."""

import curses
import os
import re
import select
import sys
import time


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
