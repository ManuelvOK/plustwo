# SPDX-License-Identifier: Zlib
"""Searching the change screen's diff."""

from typing import TYPE_CHECKING

from plustwo.diff.search import SEARCH_MODES, comment_matches
from plustwo.tui.diff_cells import SEARCH_PLACES, search_question
from plustwo.tui.file_names import diff_path
from plustwo.tui.messages import ERROR
from plustwo.tui.sidebar import DIFF

if TYPE_CHECKING:
    from plustwo.tui.change_view import ChangeView
    from plustwo.tui.interface import Interface


class DiffSearch:
    """The search in the diff. The searched text, its history and the mode
    are the interface's, kept for all changes.
    """

    def __init__(self, interface: 'Interface', view: 'ChangeView') -> None:
        """Sets up the search.

        Args:
            interface: the interface.
            view: the change screen, whose diff it searches.
        """
        self._interface = interface
        self._view = view

    def marked(self, is_searched_here: bool) -> str:
        """Finds the search whose matches to mark.

        Args:
            is_searched_here: whether the search looks at the place drawn.

        Returns:
            The searched text if its matches are marked and the search
            looks here, else ''.
        """
        return self._interface.search if self._interface.marking and is_searched_here else ''

    def _ask(self) -> bool:
        """Asks for the text to search in the bottom line. Tab switches to
        the next search mode.

        Returns:
            False if cancelled.
        """
        interface = self._interface

        def next_mode() -> str:
            """Switches to the next search mode.

            Returns:
                The new prompt.
            """
            modes = SEARCH_MODES
            interface.search_mode = modes[(modes.index(interface.search_mode) + 1) % len(modes)]
            return search_question(interface.search_mode)

        text = interface.dialogs.ask(search_question(interface.search_mode),
                                     interface.search_history, next_mode)
        if text is None:
            return False
        if text:
            interface.search = text
            if text in interface.search_history:
                interface.search_history.remove(text)
            interface.search_history.append(text)
        return True

    def run(self, action: str) -> None:
        """Searches the diff for a text typed in the bottom line, or for the
        last one again, and goes to the next (or previous) match. Opens the
        gaps and unfolds the threads with matches where the mode looks.

        Args:
            action: search, search-next, search-prev or search-clear.
        """
        interface = self._interface
        view = self._view
        if 'search-clear' == action:
            interface.marking = False
            return
        if 'search' == action and not self._ask():
            return
        if not interface.search:
            interface.say('Nothing searched yet.')
            return
        interface.marking = True
        mode = interface.search_mode
        view.focus_pane(DIFF)
        view.prepare_diff()
        if mode in ('file', 'all'):
            view.diff.open_gaps_with(interface.search)
        if mode in ('all', 'comments'):
            for comment in view.change.comments:
                if diff_path(view.threads.place_of(comment).path) == view.files_pane.selected() \
                        and comment_matches(comment, interface.search):
                    view.threads.set_expanded(comment.number, True)
        view.prepare_diff()
        wrapped = view.diff.go_to_match(interface.search, 'search-prev' != action,
                                        view.diff_height(), mode)
        if wrapped is None:
            interface.say(f'"{interface.search}" is not in {SEARCH_PLACES[mode]}.', ERROR)
        elif wrapped:
            interface.say('Went on from the ' + ('bottom.' if 'search-prev' == action
                                                 else 'top.'))
