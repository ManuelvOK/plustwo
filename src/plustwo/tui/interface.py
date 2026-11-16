# SPDX-License-Identifier: Zlib
"""The interface: screens, the message line, dialogs and the loop."""

import curses
import subprocess
from typing import Any

from plustwo.config.registry import review_repositories
from plustwo.config.settings import setting, settings_section, user_setting, user_settings_section
from plustwo.config.view_state import follows_latest
from plustwo.diff.edits import WHITESPACE_MODES
from plustwo.diff.search import SEARCH_MODES
from plustwo.git.errors import ChangeError
from plustwo.git.repository import Repository
from plustwo.review.stacks import stack_version
from plustwo.tui import colours as tui_colours
from plustwo.tui.area import Area
from plustwo.tui.colour_pairs import ColourPairs
from plustwo.tui.colours import SECONDARY, curses_colour
from plustwo.tui.dialogs import Dialogs
from plustwo.tui.keymap import keymap_from_settings
from plustwo.tui.keys import META
from plustwo.tui.messages import ERROR, OK
from plustwo.tui.mouse import CLICK, DOUBLE_CLICK, WHEEL_DOWN, WHEEL_UP
from plustwo.tui.sidebar import SIDEBAR_MODES
from plustwo.tui.stack_view import StackView
from plustwo.tui.terminal import (
    ESC_DELAY_MS, RELOAD_INTERVAL_MS, keys_as_keys, pager_environment, parse_colour,
    query_background, true_colour_terminal)
from plustwo.tui.text_input import TextInput
from plustwo.tui.view import View


def read_setting(repository: Repository | None, key: str,
                 default: str | None = None) -> str | None:
    """Reads a setting of the interface: from the repository's file and the
    user's, or from the user's alone outside a repository.

    Args:
        repository: the working directory's repository, None outside one.
        key: the setting's key.
        default: the value when no file has the setting.

    Returns:
        The value, else default.
    """
    if repository is None:
        return user_setting(key, default)
    return setting(repository, key, default)


def run(repository: Repository | None, list_name: str | None = None) -> None:
    """Starts the interface.

    Args:
        repository: the working directory's repository, None outside one.
            Its settings count for the interface.
        list_name: the list of repositories the dashboard shows, None for
            all.
    """
    tui_colours.TERMINAL_BACKGROUND = (
        parse_colour(read_setting(repository, 'tui.background', '') or '')
        or query_background() or (0, 0, 0))
    wants_true_colour = true_colour_terminal(
        read_setting(repository, 'tui.colours', 'auto') or 'auto')

    def start(screen: Any) -> None:
        """Runs the interface on the screen curses set up.

        Args:
            screen: the curses window of the whole screen.
        """
        tui_colours.TRUE_COLOUR = wants_true_colour and curses.COLORS >= 1 << 24
        # Enter then arrives as \r and Ctrl+J as \n, so the comment box
        # tells them apart.
        curses.nonl()
        keys_as_keys()
        Interface(screen, repository, list_name).loop()

    curses.wrapper(start)


class Interface:
    """The running interface: the stack of screens, drawing and keys.

    Attributes:
        list_name: the list of repositories shown, None for all.
        keymap: the keys in use.
        text_input: the comment box and the editor.
        dialogs: the questions and boxes over the screen.
        colour_pairs: the curses colour pairs of the diff.
        context_step: the index into CONTEXT_STEPS, kept for all changes.
        whitespace: Gerrit's whitespace mode of the diff, one of
            WHITESPACE_MODES.
        follow_latest: whether a right side on the latest patch set moves
            to a new upload, state that the follow-latest key toggles.
        search: the text searched last in the diff.
        marking: whether the search's matches are marked.
        search_history: the texts searched before, the latest last.
        search_mode: where the search looks, one of SEARCH_MODES.
        sidebar: the left column of the change screen, one of
            SIDEBAR_MODES. The sidebar key sets it for the rest of the
            session.
    """

    def __init__(self, screen: Any, repository: Repository | None,
                 list_name: str | None = None) -> None:
        """Sets up curses and the settings, with the dashboard as the first
        screen.

        Args:
            screen: the curses window of the whole screen.
            repository: the working directory's repository, None outside
                one. Its settings count.
            list_name: the list of repositories shown, None for all.
        """
        self._screen = screen
        self._repository = repository
        self.list_name = list_name
        self._views: list[View] = [StackView(self)]
        self._message = ''
        self._message_type: str | None = None
        self._version: str | None = None
        self.text_input = TextInput(self, screen)
        self.dialogs = Dialogs(self, screen)
        self.keymap = keymap_from_settings(self._settings_section('keys'))
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
        dim_colour = self.setting('tui.dim-colour', str(tui_colours.DIM_COLOUR)) or ''
        if dim_colour.isdigit() and int(dim_colour) < 256:
            tui_colours.DIM_COLOUR = int(dim_colour)
        self.context_step = 0
        self.whitespace = self.setting('diff.whitespace', 'none') or 'none'
        self.follow_latest = follows_latest()
        if self.whitespace not in WHITESPACE_MODES:
            self.whitespace = 'none'
        self.search = ''
        self.marking = False
        self.search_history: list[str] = []
        self.search_mode = self.setting('tui.search', 'shown') or 'shown'
        if self.search_mode not in SEARCH_MODES:
            self.search_mode = 'shown'
        self.sidebar = self.setting('tui.sidebar', 'auto') or 'auto'
        if self.sidebar not in SIDEBAR_MODES:
            self.sidebar = 'auto'

    # ── state shared by the views ─────────────────────────────────────────────

    def setting(self, key: str, default: str | None = None,
                repository: Repository | None = None) -> str | None:
        """Reads a setting, see read_setting().

        Args:
            key: the setting's key.
            default: the value when no file has the setting.
            repository: the repository whose settings count, like the one of
                the change shown. None for the working directory's.

        Returns:
            The value, else default.
        """
        return read_setting(repository or self._repository, key, default)

    def theme(self, repository: Repository | None = None) -> str | None:
        """Reads bat's theme for the diff's syntax colours.

        Args:
            repository: the repository whose settings count, the one of the
                change shown. None for the working directory's.

        Returns:
            The setting tui.theme, None for bat's default.
        """
        return self.setting('tui.theme', repository=repository)

    def _settings_section(self, section: str) -> dict[str, str]:
        """Reads all settings of a section, like settings_section().

        Args:
            section: the section's name.

        Returns:
            Each name in the section, mapped to its value.
        """
        if self._repository is None:
            return user_settings_section(section)
        return settings_section(self._repository, section)

    def say(self, message: str, message_type: str | None = None) -> None:
        """Shows a message in the bottom line until the next key.

        Args:
            message: the message.
            message_type: OK (green), ERROR (red) or None for neutral
                information.
        """
        self._message = message
        self._message_type = message_type

    def open(self, view: View) -> None:
        """Shows a screen on top of the current one.

        Args:
            view: the screen.
        """
        self._views.append(view)
        view.reload()

    def back(self) -> bool:
        """Goes back to the previous screen.

        Returns:
            False at the first screen, else True.
        """
        if len(self._views) > 1:
            note = getattr(self._views.pop(), 'leaving_note', lambda: '')()
            self._views[-1].reload()
            if note:
                self.say(note)
            return True
        return False

    # ── main loop ─────────────────────────────────────────────────────────────

    def loop(self) -> None:
        """Draws, waits for a key or the reload interval, repeats. Keys
        typed while one was handled, like a held j, are all handled before
        the next draw: over a slow connection the screen skips steps
        instead of lagging behind.
        """
        self._views[-1].reload()
        self._version = self.version()
        while True:
            self.draw()
            key = self._screen.getch()
            if -1 == key:
                self._reload_if_changed()
                continue
            while -1 != key:
                self._handle_key(key)
                if not self._views:
                    return
                key = self._typed_ahead()

    def _typed_ahead(self) -> int:
        """Reads the next key if one is waiting already.

        Returns:
            The key code, -1 if none is waiting.
        """
        self._screen.timeout(0)
        try:
            return self._screen.getch()
        finally:
            self._screen.timeout(RELOAD_INTERVAL_MS)

    def _handle_key(self, key: int) -> None:
        """Does what a key asks for.

        Args:
            key: the key code, as getch() returns it.
        """
        self._message = ''
        if curses.KEY_MOUSE == key:
            self.keymap.reset()
            self._handle_mouse()
            return
        result = self.keymap.feed(self._with_meta(key))
        if isinstance(result, str):
            # PENDING: a count or sequence goes on
            return
        action, count = result
        if action is None:
            return
        if not self._handle_global(action):
            try:
                self._views[-1].action(action, count)
            except ChangeError as error:
                self.say(str(error), ERROR)

    def _with_meta(self, key: int) -> int:
        """Reads Alt plus a key, which arrives as Esc and the key.

        Args:
            key: the key code just read.

        Returns:
            The following key plus META after Esc, a lone Esc as Esc, any
            other key as it is.
        """
        if 27 != key:
            return key
        self._screen.timeout(ESC_DELAY_MS)
        try:
            following = self._screen.getch()
        finally:
            self._screen.timeout(RELOAD_INTERVAL_MS)
        return key if following in (-1, 27) else META | following

    def _handle_global(self, action: str) -> bool:
        """Handles the actions that work on every screen.

        Args:
            action: the action's name.

        Returns:
            Whether the action was one of them.
        """
        if 'quit-now' == action:
            self._views.clear()
            return True
        if 'quit' == action:
            # Quitting is easy by mistake, e.g. q instead of a tab
            # q twice quits too, like qq
            if self.dialogs.ask_key('Quit plustwo? (y or q quits)') in (ord('y'), ord('q')):
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

    def _handle_mouse(self) -> None:
        """Passes a click, double click or wheel turn to the current screen."""
        try:
            _id, column, row, _z, buttons = curses.getmouse()
        except curses.error:
            return
        if buttons & curses.BUTTON1_DOUBLE_CLICKED:
            mouse_event = DOUBLE_CLICK
        elif buttons & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED):
            mouse_event = CLICK
        elif buttons & curses.BUTTON4_PRESSED:
            mouse_event = WHEEL_UP
        elif buttons & getattr(curses, 'BUTTON5_PRESSED', 0):
            mouse_event = WHEEL_DOWN
        else:
            return
        try:
            self._views[-1].mouse(row, column, mouse_event)
        except ChangeError as error:
            self.say(str(error), ERROR)

    def _reload_if_changed(self) -> None:
        """Reloads the current view when commits or notes changed."""
        version = self.version()
        if version != self._version:
            self._version = version
            self._views[-1].reload()

    def version(self) -> str:
        """Reads the refs of every shown repository, see stack_version().

        Returns:
            The refs, which change with every write.
        """
        return '\n'.join(stack_version(repository)
                         for repository in review_repositories(self.list_name))

    def after_write(self) -> None:
        """Reloads after the interface itself wrote, without waiting."""
        self._version = self.version()
        self._views[-1].reload()

    # ── drawing ───────────────────────────────────────────────────────────────

    def draw(self, does_refresh: bool = True) -> None:
        """Draws the current screen, its keys and the message line.

        Args:
            does_refresh: whether to show it on the terminal at once, else
                something is drawn over it first.
        """
        self._screen.erase()
        height, width = self._screen.getmaxyx()
        view = self._views[-1]
        view.draw(Area(self._screen, 0, height - 2, width))
        # The keys in bold, what they do dimmed. The help key stays, the
        # other hints go from the end when the line is too narrow.
        entries: list[tuple[tuple[str, ...], str, str]] = []
        if self.text_input.footer is not None:
            # A text box is open: its keys
            entries = [((), keys, label) for keys, label in self.text_input.footer()]
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
            self._message_type or '', 0)
        self.text(height - 1, 0, self._message, curses.A_BOLD | colour, width)
        # The typed prefix in the bottom right corner, like vim's showcmd
        prefix = self.keymap.typed()
        if prefix:
            self.text(height - 1, width - len(prefix) - 2, prefix, curses.A_BOLD)
        if does_refresh:
            self._screen.refresh()

    def dim(self, attributes: int = 0, background: int = -1) -> int:
        """Finds the look of secondary text, see secondary().

        Args:
            attributes: the row's curses attributes.
            background: the background, -1 for the terminal's.

        Returns:
            The curses attribute. Nothing on a reversed row, whose text is
            plain there.
        """
        if attributes & curses.A_REVERSE:
            return 0
        return self.colour_pairs.attribute(SECONDARY, background)

    def text(self, row: int, column: int, text: str, attributes: int = 0,
             width: int | None = None) -> None:
        """Writes text, cut at the screen's edge. Never raises for space.

        Args:
            row: the screen row.
            column: the screen column.
            text: the text.
            attributes: the curses attributes.
            width: the column to cut at, None for the screen's edge.
        """
        height, screen_width = self._screen.getmaxyx()
        if not 0 <= row < height or column >= screen_width:
            return
        limit = (width or screen_width) - column
        try:
            self._screen.addnstr(row, column, text, max(0, min(limit, screen_width - column - 1)),
                                 attributes)
        except curses.error:
            pass

    def _show_help(self) -> None:
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
                if isinstance(result, str):
                    # PENDING: a count or sequence goes on
                    continue
                step = {'down': 1, 'up': -1, 'page-down': height // 2,
                        'page-up': -(height // 2)}.get(result[0] or '')
                if step is None:
                    return
                first = max(0, min(first + step, max(len(lines) - height + 1, 0)))
        finally:
            self._screen.timeout(RELOAD_INTERVAL_MS)

    # ── leaving the screen for other programs ─────────────────────────────────

    def run_outside(self, command: list[str]) -> None:
        """Runs a program on the real terminal, like git diff with delta.

        Args:
            command: the command line.
        """
        curses.endwin()
        subprocess.run(command, env=pager_environment())
        # A refresh after endwin switches back to the alternate screen and
        # curses' modes. reset_prog_mode alone would not switch the screen,
        # and the interface would be left on the terminal after quitting.
        self._screen.clearok(True)
        self._screen.refresh()
