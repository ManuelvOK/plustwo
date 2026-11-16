# SPDX-License-Identifier: Zlib
"""Dialogs: questions in the bottom line and boxes over the screen."""

import curses
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from plustwo.tui.choice import Choice
from plustwo.tui.terminal import RELOAD_INTERVAL_MS

if TYPE_CHECKING:
    from plustwo.tui.interface import Interface


class Dialogs:
    """Questions and boxes over the interface's screen, which wait for the
    reviewer's answer.
    """

    def __init__(self, interface: 'Interface', screen: Any) -> None:
        """Sets up the dialogs.

        Args:
            interface: the interface, which draws the screen below them.
            screen: the curses window of the whole screen.
        """
        self._interface = interface
        self._screen = screen

    def ask(self, question: str, history: Sequence[str] = (),
            on_tab: Callable[[], str] | None = None) -> str | None:
        """Asks for one line in the bottom line.

        Args:
            question: the question.
            history: the earlier answers, the latest last, which Up and
                Down go through.
            on_tab: what Tab calls, which returns the new question. None
                for nothing.

        Returns:
            The answer, stripped. None on Esc or C-g.
        """
        out = self._interface.text
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
                out(height - 1, 0, f'{question} {answer}', curses.A_BOLD, width)
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

    def choose(self, question: str, choice: Choice) -> int | None:
        """Lets the reviewer pick a value in the bottom line: Left, Right or
        a digit, Enter takes it.

        Args:
            question: the question.
            choice: the values to pick from.

        Returns:
            The value, None on Esc, C-g or q.
        """
        out = self._interface.text
        height, width = self._screen.getmaxyx()
        self._screen.timeout(-1)
        try:
            while True:
                self._interface.draw(does_refresh=False)
                self._screen.move(height - 1, 0)
                self._screen.clrtoeol()
                line = f'{question} {choice.text()}   ←/→ or a digit, Enter takes it'
                out(height - 1, 0, line, curses.A_BOLD, width)
                self._screen.refresh()
                key = self._screen.get_wch()
                if key in ('\r', '\n', curses.KEY_ENTER):
                    return choice.value
                if key in ('\x1b', '\x07', 'q'):
                    return None
                choice.key(key)
        finally:
            self._screen.timeout(RELOAD_INTERVAL_MS)

    def ask_key(self, question: str) -> int:
        """Shows a question in the bottom line over the screen.

        Args:
            question: the question.

        Returns:
            The key code of the key pressed.
        """
        out = self._interface.text
        height, width = self._screen.getmaxyx()
        self._interface.draw(does_refresh=False)
        self._screen.move(height - 1, 0)
        self._screen.clrtoeol()
        out(height - 1, 0, question, curses.A_BOLD, width)
        self._screen.refresh()
        self._screen.timeout(-1)
        try:
            return self._screen.getch()
        finally:
            self._screen.timeout(RELOAD_INTERVAL_MS)

    def dialog(self, lines: list[str], footer: str) -> int:
        """Shows lines in a box in the middle of the screen, over the
        current screen, and waits for a key.

        Args:
            lines: the lines.
            footer: the last line, in bold.

        Returns:
            The key code of the key pressed.
        """
        out = self._interface.text
        height, width = self._screen.getmaxyx()
        inner = min(max([len(line) for line in lines] + [len(footer), 30]) + 2, width - 4)
        shown = lines[:max(height - 6, 1)]
        top = max((height - len(shown) - 4) // 2, 0)
        left = max((width - inner - 2) // 2, 0)
        self._interface.draw(does_refresh=False)
        rows = shown + ['', footer]
        out(top, left, '┌' + '─' * inner + '┐', curses.A_BOLD)
        for offset, line in enumerate(rows, start=1):
            attributes = curses.A_BOLD if offset == len(rows) else 0
            out(top + offset, left, '│', curses.A_BOLD)
            out(top + offset, left + 1, f' {line:<{inner - 1}.{inner - 1}}', attributes)
            out(top + offset, left + inner + 1, '│', curses.A_BOLD)
        out(top + len(rows) + 1, left, '└' + '─' * inner + '┘', curses.A_BOLD)
        self._screen.refresh()
        self._screen.timeout(-1)
        try:
            return self._screen.getch()
        finally:
            self._screen.timeout(RELOAD_INTERVAL_MS)

    def show_lines(self, lines: list[str]) -> None:
        """Shows lines in a box until a key is pressed.

        Args:
            lines: the lines.
        """
        self.dialog(lines, 'Press any key.')

    def confirm(self, lines: list[str], question: str) -> bool:
        """Shows lines in a box and asks a yes/no question.

        Args:
            lines: the lines.
            question: the question.

        Returns:
            True for y.
        """
        return ord('y') == self.dialog(lines, f'{question} (y/N)')
