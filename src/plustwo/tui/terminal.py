# SPDX-License-Identifier: Zlib
"""What the terminal can do: its background, true colour, raw keys."""

import curses
import os
import re
import select
import sys
import time

from plustwo.diff.contrast import Rgb

# How often the screen checks for new commits or notes, in milliseconds
RELOAD_INTERVAL_MS = 700

# How long curses waits after Esc for more bytes of a key sequence. The
# default of one second makes Esc feel stuck.
ESC_DELAY_MS = 25


def keys_as_keys() -> None:
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
        attributes[6][termios.VINTR] = bytes([_termios_disabled()])
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, attributes)
    except termios.error:
        return
    curses.def_prog_mode()


def _termios_disabled() -> int:
    """Finds the value that turns a terminal key off, _POSIX_VDISABLE.

    Returns:
        0 on Linux, 0xff on the BSDs and macOS.
    """
    return 0 if sys.platform.startswith('linux') else 0xff


# The reply to OSC 11, the question for the terminal's background
_BACKGROUND_REPLY = re.compile(r'\x1b\]11;rgb:([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})/'
                              r'([0-9a-fA-F]{1,4})(?:\x1b\\|\x07)')


# The reply to DA1, which every terminal answers, after the one to OSC 11
_ATTRIBUTES_REPLY = re.compile(r'\x1b\[\?[0-9;]*c')


# How long to wait for the terminal's replies, in seconds
_QUERY_TIMEOUT = 0.1


def parse_background_reply(text: str) -> Rgb | None:
    """Reads the colour of a terminal's reply to OSC 11: ESC ]11, then
    rgb:1c1c/1c1c/1c1c after a separator, with 1 to 4 hex digits a
    channel, ended by ST or BEL.

    Args:
        text: what the terminal sent.

    Returns:
        (red, green, blue), None if the text has no such reply.
    """
    match = _BACKGROUND_REPLY.search(text)
    if match is None:
        return None
    red, green, blue = (round(int(value, 16) * 255 / (16 ** len(value) - 1))
                        for value in match.groups())
    return red, green, blue


def parse_colour(text: str) -> Rgb | None:
    """Reads a colour.

    Args:
        text: the colour, like #1c1c1c.

    Returns:
        (red, green, blue), None for anything else.
    """
    match = re.fullmatch(r'#?([0-9a-fA-F]{6})', text.strip())
    if match is None:
        return None
    digits = match.group(1)
    return int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16)


def query_background() -> Rgb | None:
    """Asks the terminal for its background with OSC 11, before curses
    starts. DA1 is asked after it: every terminal answers that, in order,
    so once its reply is there, no other one can come late and be read as
    keys. _QUERY_TIMEOUT bounds the wait for terminals that answer
    neither.

    Returns:
        (red, green, blue), None if the terminal doesn't say.
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
        deadline = time.monotonic() + _QUERY_TIMEOUT
        while not _ATTRIBUTES_REPLY.search(reply):
            seconds_left = deadline - time.monotonic()
            if seconds_left <= 0 or not select.select([descriptor], [], [], seconds_left)[0]:
                break
            reply += os.read(descriptor, 256).decode('latin-1')
    finally:
        # Whatever else came stays out of the keys.
        termios.tcsetattr(descriptor, termios.TCSAFLUSH, saved)
        os.close(descriptor)
    return parse_background_reply(reply)


def _has_terminfo(name: str) -> bool:
    """Tells whether the terminal database has an entry.

    Args:
        name: the entry's name, like st-direct.

    Returns:
        True if the entry exists.
    """
    try:
        curses.setupterm(name, sys.stdout.fileno())
    except (curses.error, OSError, ValueError):
        return False
    return True


def true_colour_terminal(mode: str) -> bool:
    """Decides whether to draw in true colour. ncurses draws RGB colours
    only with a terminfo entry for them, like xterm-direct: with auto or
    true, and COLORTERM saying the terminal can, TERM changes to its
    -direct entry if there is one, like st-256color to st-direct. The keys
    of both entries are the same. Without such an entry, it stays at 256
    colours.

    Args:
        mode: the setting tui.colours: auto, true or 256.

    Returns:
        True for true colour.
    """
    term = os.environ.get('TERM', '')
    if '256' == mode or not term:
        return False
    if 'true' != mode and os.environ.get('COLORTERM') not in ('truecolor', '24bit'):
        return False
    if term.endswith('-direct'):
        return True
    direct = re.sub(r'-256colou?r$', '', term) + '-direct'
    if not _has_terminfo(direct):
        return False
    os.environ['TERM'] = direct
    return True


def pager_environment() -> dict[str, str]:
    """Makes the environment for programs the interface hands over to. git
    starts less with LESS=FRX, and delta with --quit-if-one-screen. Both
    make the pager quit at once when the text fits on one screen, so a
    short diff would only flash. Here the pager stays until q is pressed.

    Returns:
        The environment.
    """
    environment = dict(os.environ)
    environment['LESS'] = '-R'
    environment['DELTA_PAGER'] = 'less -R'
    return environment
