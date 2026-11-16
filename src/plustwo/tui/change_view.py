# SPDX-License-Identifier: Zlib
"""The change screen: files, threads and the diff."""

import curses
import textwrap
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar, cast

from plustwo.tui import colours as tui_colours
from plustwo.review import reply as review_reply
from plustwo.review import threads as review_threads
from plustwo.config.user import current_user
from plustwo.config.view_state import set_follows_latest
from plustwo.diff.edits import WHITESPACE_MODES
from plustwo.diff.search import SEARCH_MODES, comment_matches
from plustwo.git.commit_message import commit_message_parts
from plustwo.git.errors import ChangeError
from plustwo.git.files import files_of, renames_of
from plustwo.git.repository import use_repository
from plustwo.model.change import Change
from plustwo.model.comment import ACK, DONE, RESOLVED, Comment
from plustwo.model.place import (
    CHANGE_PLACE, COMMIT_MSG, OLD_SIDE, PATCHSET_LEVEL, WHOLE_CHANGE, place_label, split_place)
from plustwo.model.roles import REVIEWER
from plustwo.model.version import BASE, LATEST
from plustwo.model.vote import CODE_REVIEW, VERIFIED
from plustwo.review.changes import changes_in_stack
from plustwo.review.compare import (
    can_compare, compare_label, compared_files, diff_command, versions_to_compare)
from plustwo.review.history import follow_latest, message_patch_sets, right_side
from plustwo.review.labels import answered_by_author, thread_file_after, version_label
from plustwo.review.last_send import last_send_compare, last_send_patch_set
from plustwo.review.moves import move_text, move_thread
from plustwo.review.porting import ShownThread, comment_place, thread_place
from plustwo.review.reviewed import file_after, is_reviewed, mark_reviewed, toggle_reviewed
from plustwo.review.stacks import needs_rebase
from plustwo.review.submit import stack_marks
from plustwo.review.threads import add_comment, add_message, delete_draft, toggle_draft_status
from plustwo.review.touched import relevant_threads, threads_by_file, touched_threads
from plustwo.review.votes import vote_values
from plustwo.tui.area import Area
from plustwo.tui.change_actions import (
    approve, rebase_with_confirmation, submit_with_confirmation, vote_action)
from plustwo.tui.choice import Choice
from plustwo.tui.colours import (
    CURSOR_LINE_GREY, HEADER_BACKGROUND, SECONDARY, STATUS_COLOURS, Colour, cursor_tint, tone,
    vote_badge)
from plustwo.tui.diff_cells import (
    CONTEXT_STEPS, HALF_SEPARATOR, SEARCH_PLACES, marked_segments, search_question)
from plustwo.tui.diff_gap import DiffGap
from plustwo.tui.diff_pane import DiffPane, ThreadBlocks, in_half, patch_set_count, thread_rows
from plustwo.tui.lists import (
    LIST_ACTIONS, comment_label, keyed_cursor, position_after, status_attribute, wheel_action)
from plustwo.tui.messages import ERROR, OK
from plustwo.tui.mouse import DOUBLE_CLICK
from plustwo.tui.patch_set_view import PatchSetView
from plustwo.tui.sidebar import (
    COMMENTS, DIFF, FILES, MESSAGE, PANES, STACK, FooterEntry, sidebar_heights, sidebar_shown,
    thread_footer)

if TYPE_CHECKING:
    from plustwo.tui.interface import BoxAnchor, Interface

T = TypeVar('T')


# What the change screen compares at first: the whole change as of the
# latest patch set
DEFAULT_COMPARE = ('base', 'latest')


class ChangeView:
    """One change: files and threads on the left, the diff on the right."""

    def __init__(self, interface: 'Interface', change_id: str,
                 repository: str | None = None) -> None:
        """Starts the screen on a change, see reload().

        Args:
            interface: the interface.
            change_id: the change's full Change-Id.
            repository: the repository's directory, None for the current one.
        """
        self._interface = interface
        self._change_id = change_id
        self._repository = repository
        # None until the first reload(), then the change
        self._change: Any = None
        self._files: list[str] = []
        self._compare: tuple[str, str] = DEFAULT_COMPARE
        self._focus = FILES
        # The pane h returns to from the diff
        self._left_pane = FILES
        # None until the change is read: then the first file after the
        # commit message, see reload()
        self._file_index = None
        self._comment_index = 0
        self._diff = DiffPane(interface.colour_pairs)
        self._layout: dict[str, tuple[int, int, int, int]] = {}
        # Threads folded or unfolded by hand: number -> expanded
        self._expanded: dict[int, bool] = {}
        # The number of the thread being moved by hand, see _drop_thread()
        self._moving: int | None = None
        self._renames: dict[str, str] = {}
        self._counts: dict[str, tuple[int, int]] = {}
        # The changes of the stack, oldest first, and whether it needs a
        # rebase: None, 'base moved' or 'conflicts'
        self._stack: list[Change] = []
        # The cursor in the stack pane, counted from its top (the newest)
        self._stack_index = 0
        # (compare, file index, thread number) to return to after a thread
        # was shown on its own patch set, and the file shown there if this
        # version doesn't have it
        self._previous_compare: tuple[tuple[str, str], str | None, int] | None = None
        self._extra_file: str | None = None
        # The latest patch set at the last reload, to see a new upload
        self._latest_seen: int | None = None
        # The commit message (subject, body, trailers) and how far its box
        # is scrolled
        self._message: tuple[str, str, list[str]] = ('', '', [])
        self._message_scroll = 0
        # The marks of the stack's changes (stack_marks()), and the
        # Change-Ids where it is the user's turn
        self._marks: dict[str, str] = {}
        self._yours: set[str] = set()
        self._behind: str | None = None
        self._file_position: int | None = None
        self._file_key: str | None = None
        self._cache: tuple[Change | None, dict[Any, Any]] = (None, {})

    # The cursors of the stack, files and comments panes stick to their
    # item (Change-Id, path, thread id), so they stay on it when the list
    # changes, e.g. when a thread gets resolved. See keyed_cursor().

    @property
    def _stack_index(self) -> int:
        """The cursor's row in the stack pane, counted from its top, the
        newest change.
        """
        keys = [other.change_id for other in reversed(self._stack)]
        self._stack_position, self._stack_key = keyed_cursor(
            keys, getattr(self, '_stack_key', None), getattr(self, '_stack_position', 0))
        return self._stack_position

    @_stack_index.setter
    def _stack_index(self, index: int) -> None:
        """Puts the stack pane's cursor on a row.

        Args:
            index: the row.
        """
        keys = [other.change_id for other in reversed(self._stack)]
        self._stack_position = index
        self._stack_key = keys[index] if 0 <= index < len(keys) else None

    @property
    def _file_index(self) -> int:
        """The cursor's row in the file list, 0 until the change is read."""
        if self._file_position is None:
            return 0
        self._file_position, self._file_key = keyed_cursor(self._files, self._file_key,
                                                           self._file_position)
        return self._file_position

    @_file_index.setter
    def _file_index(self, index: int | None) -> None:
        """Puts the file list's cursor on a row.

        Args:
            index: the row, None before the change is read.
        """
        self._file_position = index
        self._file_key = self._files[index] if index is not None and \
            0 <= index < len(self._files) else None

    @property
    def _comment_index(self) -> int:
        """The cursor's thread in the comments pane, an index into
        _comments().
        """
        keys = [comment.id for comment in self._comments()]
        self._comment_position, self._comment_key = keyed_cursor(
            keys, getattr(self, '_comment_key', None), getattr(self, '_comment_position', 0))
        return self._comment_position

    @_comment_index.setter
    def _comment_index(self, index: int) -> None:
        """Puts the comments pane's cursor on a thread.

        Args:
            index: the index into _comments().
        """
        keys = [comment.id for comment in self._comments()]
        self._comment_position = index
        self._comment_key = keys[index] if 0 <= index < len(keys) else None

    def footer(self) -> list[FooterEntry]:
        """Lists only the actions that work right now: those of the focused
        pane and what the cursor is on, then those the change's state
        allows.

        Returns:
            The footer's entries.
        """
        change = self._change
        if self._moving is not None:
            return [(('down', 'up'), f'to the line of #{self._moving}'),
                    (('side-old', 'side-new'), 'side'), (('open',), 'drop here'),
                    (('back',), 'cancel')]
        if STACK == self._focus:
            entries = [(('down', 'up'), 'move'), (('open',), 'show change')]
        elif MESSAGE == self._focus:
            entries = [(('down', 'up'), 'scroll'), (('open',), 'comment on it')]
        elif FILES == self._focus:
            entries = [(('open',), 'diff'), (('reviewed',), 'reviewed'),
                       (('reviewed-next',), 'reviewed, next'), (('file-comment',), 'comment')]
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
            if self._cached('relevant', lambda: relevant_threads(change, *self._compare)):
                entries.append((('next-relevant', 'prev-relevant'), 'relevant'))
            entries.append((('reviewed-next',), 'reviewed, next'))
            if self._interface.marking:
                entries += [(('search-next', 'search-prev'), 'match'),
                            (('search-clear',), 'unmark')]
            if self._threads_at_cursor():
                entries.append((('open',), 'unfold'))
        if any(not message.sent for comment in change.comments for message in comment.messages):
            entries.append((('send',), 'send'))
        sent = last_send_patch_set(change)
        if sent is not None and sent != change.latest_patch_set() and \
                (self._compare[0], right_side(change, self._compare[1])) != (
                    str(sent), LATEST):
            entries.append((('compare-last-send',), 'since last send'))
        if not change.submit_problems():
            entries.append((('submit',), 'submit'))
        elif not change.is_approved(CODE_REVIEW):
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

    def reload(self) -> None:
        """Reads the change again. Goes back if it is gone."""
        use_repository(self._repository)
        for change in changes_in_stack():
            if change.change_id == self._change_id:
                self._change = change
                if not can_compare(change, *self._compare):
                    # E.g. a patch set chosen before is gone after a reload.
                    self._compare = DEFAULT_COMPARE
                    self._previous_compare = None
                    self._interface.say('The patch sets compared before are gone, '
                                        'showing base -> latest.')
                now = change.latest_patch_set()
                if self._latest_seen is not None:
                    self._compare, moved = follow_latest(
                        self._compare, self._latest_seen, now, self._interface.follow_latest)
                    if moved:
                        self._interface.say(f'PS{now} uploaded, now ' + compare_label(
                            change, *self._compare))
                self._latest_seen = now
                # The commit message comes first, like in Gerrit.
                # A renamed file is listed once, under its new name.
                self._renames = renames_of(change.commit)
                self._counts = files_of(change.commit)
                self._files = [COMMIT_MSG] + sorted(
                    set(files_of(change.commit)) - set(self._renames.values()))
                if self._extra_file and self._extra_file not in self._files:
                    self._files.append(self._extra_file)
                if self._file_position is None:
                    # The commit message stays first, the first real file is shown.
                    self._file_index = 1 if len(self._files) > 1 else 0
                self._stack = [other for other in changes_in_stack()
                               if other.topic == change.topic]
                if STACK != self._focus:
                    self._stack_index = self._current_in_stack()
                self._marks = stack_marks(self._stack)
                self._message = commit_message_parts(change.commit)
                # Where it is the user's turn, like the dashboard's "▶ you"
                user = current_user()
                self._yours = {other.change_id for other in self._stack
                               if 'your-turn' in other.sections(user)}
                self._behind = (
                    'conflicts' if any(other.rebase_is_requested for other in self._stack)
                    else 'base moved' if needs_rebase(change.topic) else None)
                self._diff.forget()
                return
        self._interface.back()
        self._interface.say('The change is not in review any more.')

    # ── layout and drawing ────────────────────────────────────────────────────

    def _current_in_stack(self) -> int:
        """Finds the shown change's row in the stack pane, newest on top.

        Returns:
            The row, 0 if the stack hasn't it.
        """
        shown = list(reversed(self._stack))
        return next((index for index, other in enumerate(shown)
                     if other.change_id == self._change_id), 0)

    def _stack_first(self, height: int) -> int:
        """Finds the first row of the stack the pane shows: the cursor stays
        visible, in the middle if it can.

        Args:
            height: the pane's height.

        Returns:
            The row.
        """
        return max(0, min(self._stack_index - height // 2, len(self._stack) - height))

    def _files_first(self, height: int) -> int:
        """Finds the first row of the file list the pane shows, like
        _stack_first().

        Args:
            height: the pane's height.

        Returns:
            The row.
        """
        index = self._file_index or 0
        return max(0, min(index - height // 2, len(self._files) - height))

    def _compute_layout(self, area: Area) -> dict[str, tuple[int, int, int, int]]:
        """Places the panes.

        Args:
            area: the part of the screen the change screen has.

        Returns:
            Each shown pane, mapped to (top, left, height, width) of its
            inside, without the frame.
        """
        if not sidebar_shown(self._interface.sidebar, area.width):
            if DIFF != self._focus:
                self._focus = DIFF
            return {DIFF: (3, 1, area.height - 4, area.width - 3)}
        left_width = min(max(area.width * 3 // 10, 28), 50)
        # From the top: the stack like Gerrit's relation chain, the commit
        # message, the files and the comments, sized by their content. The
        # four frames take 8 of the rows below the two header rows.
        stack_rows, message_rows, files_height, comments_height = sidebar_heights(
            area.height - 2 - 8, [len(self._stack), len(self._message_lines(left_width - 2)),
                                  len(self._files), len(self._comment_rows()) or 1])
        message_top = 3 + stack_rows + 2
        files_top = message_top + message_rows + 2
        comments_top = files_top + files_height + 2
        return {STACK: (3, 1, stack_rows, left_width - 2),
                MESSAGE: (message_top, 1, message_rows, left_width - 2),
                FILES: (files_top, 1, files_height, left_width - 2),
                COMMENTS: (comments_top, 1, comments_height, left_width - 2),
                DIFF: (3, left_width + 1, area.height - 4, area.width - left_width - 3)}

    def _frame(self, pane: str, title: str, n_rows_above: int = 0,
               n_rows_below: int = 0) -> None:
        """Draws the frame around a pane, with its title. When the content
        doesn't fit, ↑N at the top border's end says N rows are above, ↓N at
        the bottom border's end N rows below. The title is cut for ↑N.

        Args:
            pane: the pane, one of PANES.
            title: the title.
            n_rows_above: the rows of content above the shown ones.
            n_rows_below: the rows of content below the shown ones.
        """
        out = self._interface.text
        top, left, height, width = self._layout[pane]
        attributes = curses.A_BOLD if pane == self._focus else self._interface.dim()
        grey = self._interface.dim()
        up = f' ↑{n_rows_above} ' if n_rows_above > 0 else ''
        down = f' ↓{n_rows_below} ' if n_rows_below > 0 else ''
        out(top - 1, left - 1, '┌' + '─' * width + '┐', attributes)
        title_room = width - 2 - (len(up) + 1 if up else 0)
        out(top - 1, left + 1, f' {title} '[:max(title_room, 0)], attributes)
        if up:
            out(top - 1, left + width - len(up), up, grey)
        for row in range(top, top + height):
            out(row, left - 1, '│', attributes)
            out(row, left + width, '│', attributes)
        out(top + height, left - 1, '└' + '─' * width + '┘', attributes)
        if down:
            out(top + height, left + width - len(down), down, grey)

    def draw(self, area: Area) -> None:
        """Draws the header and the panes.

        Args:
            area: the part of the screen to draw into.
        """
        out = self._interface.text
        change = self._change
        self._layout = self._compute_layout(area)
        bar = self._interface.colour_pairs.attribute(15, tone(HEADER_BACKGROUND))
        header_dim = self._interface.dim(background=tone(HEADER_BACKGROUND))
        out(0, 0, ' ' * area.width, bar)
        place = ((f'{change.repository_name} › ' if change.repository_name else '')
                 + f'{change.topic} on {change.base} ›')
        out(0, 1, place, header_dim)
        out(0, 2 + len(place), change.subject, bar | curses.A_BOLD)
        state = f' {change.state()} '
        chip = self._interface.colour_pairs.attribute(
            0, {'New': 8, 'In-Review': 3, 'Submitted': 2}.get(change.state(), 8)) | curses.A_BOLD
        version = f'{change.short_id()}  {version_label(change)} '
        out(0, max(area.width - len(version) - len(state) - 1, 1), state, chip)
        if self._behind:
            behind = (' Needs rebase ' if 'base moved' == self._behind
                      else ' Conflicts, the author rebases ')
            out(0, max(area.width - len(version) - len(state) - len(behind) - 2, 1), behind,
                self._interface.colour_pairs.attribute(0, 1 if 'conflicts' == self._behind
                                                       else 3) | curses.A_BOLD)
        drafts = change.n_drafts()
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
            comparing = compare_label(change, *self._compare, does_follow_latest=follow)
            if len(patch_sets) + 3 + len(comparing) > area.width - 2:
                comparing = compare_label(change, *self._compare, does_follow_latest=follow,
                                          is_short=True)
        except ChangeError as error:
            comparing = str(error)
        header = f'{patch_sets}   {comparing}'
        if FILES not in self._layout:
            # Without the files pane, the header says which file it is,
            # with fewer words when the line is narrow.
            label = file_label(self._selected_file() or '', self._renames)
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

    def _draw_stack(self) -> None:
        """Draws the changes of the stack, newest on top like Gerrit's
        relation chain: a green mark for submittable (✓) or finished (+2),
        grey where it isn't the user's turn, this one bold. The cursor row
        is highlighted while focused, else this one.
        """
        out = self._interface.text
        base = self._change.base
        top, left, height, width = self._layout[STACK]
        shown = list(reversed(self._stack))
        first = self._stack_first(height)
        self._frame(STACK, f'Stack ({len(self._stack)}) on {base}', first,
                    len(shown) - first - height)
        pairs = self._interface.colour_pairs
        focused = STACK == self._focus
        # The one whole-line highlight: the cursor while the pane has the
        # focus, else the shown change, tinted like the diff's cursor line
        tint = (cursor_tint(-1) if tui_colours.TRUE_COLOUR else CURSOR_LINE_GREY) \
            if curses.COLORS >= 256 else None
        for row, other in enumerate(shown[first:first + height]):
            is_current = other.change_id == self._change_id
            mine = other.change_id in self._yours
            background: Colour = -1
            if focused and first + row == self._stack_index:
                attributes = curses.A_REVERSE
                mark_attributes = attributes | curses.color_pair(2)
            else:
                if is_current and not focused and tint is not None:
                    background = tint
                # Grey where it is not the user's turn
                attributes = pairs.attribute(-1 if mine else SECONDARY, background)
                mark_attributes = pairs.attribute(2, background)
            if is_current:
                attributes |= curses.A_BOLD
                mark_attributes |= curses.A_BOLD
            out(top + row, left, ' ' * width, attributes, left + width)
            # Like the file box's ✓: submittable, or +2 for a finished
            # review that can't be submitted yet
            mark = self._marks.get(other.change_id, '')
            out(top + row, left, f'{mark:<2}', mark_attributes, left + width)
            out(top + row, left + 3, other.subject[:max(width - 3, 0)], attributes, left + width)

    def _message_lines(self, width: int) -> list[tuple[str, int]]:
        """Wraps the commit message into the rows of its box, the subject
        bold, the trailers dimmed.

        Args:
            width: the box's width.

        Returns:
            (text, curses attributes) per row.
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

    def _draw_message(self) -> None:
        """Draws the box with the commit message, scrolled while focused."""
        out = self._interface.text
        top, left, height, width = self._layout[MESSAGE]
        lines = self._message_lines(width)
        self._message_scroll = max(0, min(self._message_scroll, len(lines) - height))
        self._frame(MESSAGE, 'Commit message', self._message_scroll,
                    len(lines) - self._message_scroll - height)
        for row, (text, attributes) in enumerate(
                lines[self._message_scroll:self._message_scroll + height]):
            out(top + row, left, text, attributes, left + width)

    def leaving_note(self) -> str:
        """Writes what the reviewer should know when leaving the change.

        Returns:
            A note on drafts not sent yet, '' without.
        """
        drafts = self._change.n_drafts() if self._change else 0
        if not drafts:
            return ''
        return (f'{drafts} draft{"" if 1 == drafts else "s"} of {self._change.short_id()} '
                f'not sent, {self._interface.keymap.label("send")} sends.')

    def _switch_to(self, change_id: str) -> None:
        """Shows another change of the stack.

        Args:
            change_id: the change's full Change-Id.
        """
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

    def _draw_votes(self, column: int) -> None:
        """Draws the votes on the latest patch set after the header, like
        "Code-Review +2  Verified -1 ci: make check failed".

        Args:
            column: the screen column to start at.
        """
        out = self._interface.text
        change = self._change
        for label in (CODE_REVIEW, VERIFIED):
            badge = vote_badge(self._interface, label, change.label_value(label),
                               is_required=VERIFIED == label and change.needs_verified)
            if badge:
                out(1, column, badge[0], badge[1])
                column += len(badge[0]) + 1
        # A failed check says why, in short.
        failed = [vote for vote in change.current_votes(VERIFIED) if vote.value < 0]
        if failed and failed[0].message:
            lines = failed[0].message.strip().split('\n')
            out(1, column, f'{failed[0].by}: {lines[-1]}', self._interface.dim())

    def _draw_files(self) -> None:
        """Draws the file list."""
        out = self._interface.text
        reviewed = [is_reviewed(self._change, path) for path in self._files]
        top, left, height, width = self._layout[FILES]
        first = self._files_first(height)
        self._frame(FILES, f'Files  {sum(reviewed)}/{len(self._files)} reviewed', first,
                    len(self._files) - first - height)
        # The counts between the compared versions. Files the comparison
        # doesn't change are grey.
        try:
            compared = compared_files(self._change, *self._compare)
        except ChangeError:
            compared = self._counts
        # Threads per file, as the diff shows them: N● with N unresolved
        # ones, grey with the number of threads when all are resolved
        counts: dict[str, tuple[int, int]] = {}
        for path, threads in self._groups():
            unresolved = sum(comment.is_unresolved() for comment in threads)
            so_far = counts.get(self._diff_path(path), (0, 0))
            counts[self._diff_path(path)] = (so_far[0] + unresolved, so_far[1] + len(threads))
        count_width = max((len(str(unresolved or total)) + 2
                           for unresolved, total in counts.values()), default=0)
        # Like Gerrit's size column: a short bar of added and removed lines
        bar_width = 6 if any(sum(compared.get(path, (0, 0))) for path in self._files) else 0
        name_width = max(width - 2 - count_width - bar_width, 1)
        for row, path in enumerate(self._files[first:first + height]):
            index = first + row
            selected = index == self._file_index
            attributes = curses.A_REVERSE if selected and FILES == self._focus else (
                curses.A_BOLD if selected else 0)
            name = file_label(path, self._renames)
            out(top + row, left, '✓ ' if reviewed[index] else '  ',
                attributes | curses.color_pair(2), left + width)
            out(top + row, left + 2, ' ' * (width - 2), attributes, left + width)
            changed = path in compared
            out(top + row, left + 2, f'{name:<{name_width}.{name_width}}',
                attributes | (0 if changed else self._interface.dim(attributes)), left + width)
            unresolved, total = counts.get(path, (0, 0))
            if total:
                label = f'{unresolved or total}●'.rjust(count_width)
                out(top + row, left + 2 + name_width, label,
                    attributes | (0 if unresolved else self._interface.dim(attributes)),
                    left + width)
            added, removed = compared.get(path, (0, 0))
            if added + removed:
                scale = max(added + removed, 1) / 5 if added + removed > 5 else 1
                plus, minus = round(added / scale), round(removed / scale)
                out(top + row, left + width - 5, '+' * plus, curses.color_pair(2), left + width)
                out(top + row, left + width - 5 + plus, '-' * minus, curses.color_pair(1),
                    left + width)

    def _draw_comments(self) -> None:
        """Draws the thread list, grouped by file in the file list's order,
        each group under a header (▶ for the file in the diff). A thread
        has ↩ when the author answered last and the start of its last
        message. Threads the comparison leaves as they were (see
        touched_threads()) get a · and a grey place, resolved ones go grey
        all over. Unresolved ones keep their text.
        """
        out = self._interface.text
        top, left, height, width = self._layout[COMMENTS]
        rows = self._comment_rows()
        if not rows:
            out(top, left, 'none yet', self._interface.dim(), left + width)
        cursor = self._comment_index
        selected_row = next((row for row, (_path, index) in enumerate(rows)
                             if index == cursor), 0)
        first = max(0, min(selected_row - height + 1, len(rows) - height))
        # For clicks
        self._comments_first = first
        self._frame(COMMENTS, 'Comments', first, len(rows) - first - height)
        touched = self._touched()
        selected_path = self._selected_file()
        open_group = self._diff_path(selected_path) if selected_path else None
        comments = self._comments()
        for row, (path, index) in enumerate(rows[first:first + height]):
            if index is None:
                current = self._diff_path(path) == open_group
                name = CHANGE_PLACE if PATCHSET_LEVEL == path else file_label(
                    path, self._renames)
                out(top + row, left, f'{"▶" if current else " "} {name}'[:width],
                    curses.A_BOLD if current else self._interface.dim(), left + width)
                continue
            comment = comments[index]
            selected = index == cursor
            attributes = curses.A_REVERSE if selected and COMMENTS == self._focus else 0
            unchanged = comment.number not in touched
            # Grey all over: resolved and left as it was
            quiet = unchanged and not comment.is_unresolved()
            grey = self._interface.dim(attributes)
            plain = attributes | grey if quiet else attributes
            out(top + row, left, ' ' * width, attributes, left + width)
            column = left
            answered = answered_by_author(comment)
            status = 'draft' if comment.is_draft() else comment.status
            parts = [(f' #{comment.number}'.ljust(4), plain),
                     ('·' if unchanged else ' ', attributes | grey),
                     ('↩ ' if answered else '  ',
                      attributes | curses.color_pair(3) if answered and not quiet else plain),
                     (f'[{comment_label(comment)}]',
                      plain if quiet else attributes | status_attribute(status))]
            shown = self._place_of(comment)
            if PATCHSET_LEVEL == shown.path != comment.place:
                # Like in Gerrit, a thread on a gone file is on the whole change.
                place = f' ({shown.origin}, {shown.note})'
            elif shown.line is not None:
                place = f' {"old " if OLD_SIDE == comment.side else ""}:{shown.line}'
            else:
                place = '' if PATCHSET_LEVEL == shown.path else ' file'
            parts.append((place, attributes | grey if unchanged else attributes))
            # The last message with text, not one that only changed the status
            text = next((' '.join(message.text.split()) for message in reversed(comment.messages)
                         if message.text.strip()), '')
            if text:
                parts.append((f'  {text}', attributes | grey))
            for text, attribute in parts:
                out(top + row, column, text, attribute, left + width)
                column += len(text)

    def _cached(self, name: str, compute: Callable[[], T]) -> T:
        """Computes a value of the shown change and comparison once for
        them: the drawing needs it on every frame.

        Args:
            name: the value's name.
            compute: the function that computes it.

        Returns:
            The value.
        """
        key = (name, self._compare, tuple(self._files))
        change, cache = self._cache
        if change is not self._change:
            cache = {}
            self._cache = (self._change, cache)
        if key not in cache:
            cache[key] = compute()
        return cache[key]

    def _touched(self) -> set[int]:
        """Finds the threads the comparison touches.

        Returns:
            The threads' numbers.
        """
        return self._cached('touched', lambda: touched_threads(
            self._change, *self._compare))

    def _groups(self) -> list[tuple[str, list[Comment]]]:
        """Groups the threads by file, see threads_by_file().

        Returns:
            (path, threads) per file.
        """
        if not self._change:
            return []
        return self._cached('groups', lambda: threads_by_file(
            self._change, self._files, *self._compare))

    def _comment_rows(self) -> list[tuple[str, int | None]]:
        """Lists the rows of the comments pane.

        Returns:
            (path, None) for a file's header, (path, index into
            _comments()) for a thread.
        """
        rows: list[tuple[str, int | None]] = []
        index = 0
        for path, threads in self._groups():
            rows.append((path, None))
            for _comment in threads:
                rows.append((path, index))
                index += 1
        return rows

    def _comments(self) -> list[Comment]:
        """Lists the threads in the comments pane's order: by file, see
        _groups().

        Returns:
            The threads.
        """
        return [comment for _path, threads in self._groups() for comment in threads]

    def _markers(self) -> dict[tuple[str, int | None], list[int]]:
        """Finds the threads on the shown file.

        Returns:
            Each (side, line), mapped to the numbers of its threads.
        """
        path = self._selected_file()
        markers: dict[tuple[str, int | None], list[int]] = {}
        for comment in self._change.comments:
            shown = self._place_of(comment)
            if shown.path == path and shown.line is not None:
                markers.setdefault((shown.side, shown.line), []).append(comment.number)
        return markers

    def _shown_patch_set(self) -> int | None:
        """Finds the patch set on the right side of the diff.

        Returns:
            The patch set's number, None for the current commit.
        """
        (_first, _), (second, _) = versions_to_compare(self._change, *self._compare)
        return int(second) if second.isdigit() else None

    def _place_of(self, comment: Comment) -> ShownThread:
        """Finds where the shown diff has a thread, see thread_place().

        Args:
            comment: the thread.

        Returns:
            The thread's place.
        """
        return thread_place(self._change, comment, *self._compare)

    def _draw_diff(self) -> None:
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
        self._frame(DIFF, f'{file_label(path, self._renames)}  '
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

    def _draw_diff_row(self, index: int, row: int,
                       markers: dict[tuple[str, int | None], list[int]]) -> None:
        """Draws one row of the diff at a screen row of the pane.

        Args:
            index: the diff pane's row.
            row: the row in the pane.
            markers: the threads by place, see _markers().
        """
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

    # ── selection ─────────────────────────────────────────────────────────────

    def _selected_file(self) -> str | None:
        """Finds the file at the file list's cursor.

        Returns:
            The file's path, None without files.
        """
        return self._files[self._file_index] if self._files else None

    def _selected_comment(self) -> Comment | None:
        """Finds the thread at the comments pane's cursor.

        Returns:
            The thread, None without threads.
        """
        comments = self._comments()
        return comments[min(self._comment_index, len(comments) - 1)] if comments else None

    def _diff_height(self) -> int:
        """Finds the diff pane's height.

        Returns:
            The height, 20 before the first layout.
        """
        return self._layout[DIFF][2] if self._layout else 20

    def _is_expanded(self, comment: Comment | None) -> bool:
        """Tells whether a thread shows all its messages in the diff.
        Unresolved ones and those with drafts do, unless folded by hand.

        Args:
            comment: the thread, None for none.

        Returns:
            True if the thread is unfolded.
        """
        if comment is None:
            return False
        default = comment.is_unresolved() or comment.has_draft() or comment.is_draft()
        return self._expanded.get(comment.number, default)

    def _prepare_diff(self) -> bool:
        """Renders the selected file's diff and lays out its threads.

        Returns:
            False if there is nothing to show.
        """
        path = self._selected_file()
        if not path or not self._layout:
            return False
        width = self._layout[DIFF][3] - 2
        self._diff.show(self._change, self._compare, path, width,
                        CONTEXT_STEPS[self._interface.context_step], self._interface.whitespace,
                        self._marked_search('comments' != self._interface.search_mode))
        # The matches in threads are marked when the search looks there.
        thread_search = self._marked_search(self._interface.search_mode in ('all', 'comments'))
        blocks: ThreadBlocks = {}
        signature = [path, width, thread_search]
        for comment in self._change.comments:
            shown = self._place_of(comment)
            if self._diff_path(shown.path) != path:
                continue
            expanded = self._is_expanded(comment)
            place = (shown.side, shown.line) if shown.line is not None else None
            note, origin = shown.note, shown.origin
            if PATCHSET_LEVEL == shown.path:
                note = note or WHOLE_CHANGE
            if place and place not in self._diff.shown_places():
                note = ((f'{note}, ' if note else '')
                        + f'on {"old " if "old" == shown.side else ""}line {shown.line}, '
                        'not in the diff '
                        f'({self._interface.keymap.label("more-context")} shows more context)')
            # Like in Gerrit, a thread on a line goes into its side's half.
            # Threads on lines not shown go on top, as wide as the diff.
            side = self._thread_half(comment)
            patch_sets = message_patch_sets(self._change, comment, self._shown_patch_set())
            rows = thread_rows(comment, self._half(side)[1], expanded, note, origin,
                               patch_sets)
            if side:
                rows = [in_half(parts, side, self._half('old')[1]) for parts in rows]
            if thread_search:
                rows = [marked_segments(parts, thread_search) for parts in rows]
            blocks.setdefault(place, []).append((comment.number, rows))
            signature.append((comment.number, comment.status, place, expanded, tuple(patch_sets),
                              tuple((message.author, message.text, message.sent, message.unresolved)
                                    for message in comment.messages),
                              tuple((move.patch_set, move.place, move.side)
                                    for move in comment.moves)))
        self._diff.arrange(blocks, tuple(signature))
        return True

    def _show_comment_in_diff(self, comment: Comment) -> None:
        """Selects the thread's file and puts the diff cursor on the thread,
        or on its line if the thread isn't in the diff.

        Args:
            comment: the thread.
        """
        shown = self._place_of(comment)
        path = self._diff_path(shown.path)
        if path in self._files:
            self._file_index = self._files.index(path)
            if self._prepare_diff():
                height = self._diff_height()
                if not self._diff.go_to_thread(comment.number, height) and shown.line is not None:
                    self._diff.go_to_line(shown.line, shown.side, height)

    def _go_to_thread(self, is_forward: bool, thread_type: str = 'thread') -> None:
        """Goes to the next or previous thread, in the next file with
        threads at the file's end, like going through all of them (]t and
        [t).

        Args:
            is_forward: whether to go down, else up.
            thread_type: thread for all threads, unresolved for only those
                (]u and [u), relevant for those worth a look (]n and [n),
                see relevant_threads().
        """
        interface = self._interface
        self._focus_pane(DIFF)
        # The diff may not be laid out for this file yet
        self._prepare_diff()
        relevant = self._cached('relevant', lambda: relevant_threads(
            self._change, *self._compare)) if 'relevant' == thread_type else None
        threads = [comment for comment in self._change.comments
                   if ('unresolved' != thread_type or comment.is_unresolved())
                   and (relevant is None or comment.number in relevant)]
        numbers = {comment.number for comment in threads}
        height = self._diff_height()
        if self._diff.go_to_next_thread(is_forward, height, numbers):
            return
        paths = {self._diff_path(self._place_of(comment).path) for comment in threads}
        path = thread_file_after(self._files, self._selected_file(), paths,
                                                1 if is_forward else -1)
        if path is None:
            what = 'threads' if 'thread' == thread_type else thread_type + ' threads'
            interface.say(f'No more {what} in this direction.')
            return
        self._file_index = self._files.index(path)
        self._prepare_diff()
        self._diff.go_to_next_thread(is_forward, self._diff_height(), numbers, starts_at_edge=True)
        interface.say(f'In {file_label(path, self._renames)}.')

    def _diff_path(self, path: str) -> str:
        """Finds the file whose diff shows the threads on a path: threads on
        the whole change go on top of the commit message, as threads on a
        whole file go on top of their file.

        Args:
            path: the threads' path.

        Returns:
            The file's path.
        """
        return COMMIT_MSG if PATCHSET_LEVEL == path else path

    def _threads_at_cursor(self) -> list[int]:
        """Finds the threads on the diff's cursor row: the thread the row
        belongs to, or the threads on the cursor line.

        Returns:
            The threads' numbers.
        """
        if not self._diff.numbers:
            return []
        number = self._diff.thread_at(self._diff.cursor)
        if number is not None:
            return [number]
        old, new = self._diff.numbers[self._diff.cursor]
        markers = self._markers()
        return markers.get(('new', new), []) + markers.get(('old', old), [])

    def _thread_at_cursor(self) -> Comment | None:
        """Finds the thread the diff's cursor is on, or the first on its
        line.

        Returns:
            The thread, None without.
        """
        numbers = self._threads_at_cursor()
        return self._change.comment(numbers[0]) if numbers else None

    def _box_below_thread(self, comment: Comment) -> DiffGap | None:
        """Finds where the comment box goes for a reply: in a gap below the
        thread in the diff.

        Args:
            comment: the thread.

        Returns:
            The gap, None if the diff doesn't show the thread.
        """
        if not self._layout:
            return None
        end = self._diff.thread_end(comment.number)
        if end is None:
            return None
        return self._gap(end, self._thread_half(comment))

    def _thread_half(self, comment: Comment) -> str | None:
        """Finds the half a thread is drawn in. Its boxes take the same
        width.

        Args:
            comment: the thread.

        Returns:
            The side, None when the thread is as wide as the diff: on the
            whole file, or on a line the diff doesn't show, which go on top.
        """
        shown = self._place_of(comment)
        place = (shown.side, shown.line)
        return shown.side if shown.line is not None and place in self._diff.shown_places() \
            else None

    def _half(self, side: str | None) -> tuple[int, int]:
        """Finds the columns of one half of the diff's text.

        Args:
            side: old or new, None for the whole width.

        Returns:
            (offset, width).
        """
        width = self._layout[DIFF][3] - 2
        half = (width - len(HALF_SEPARATOR)) // 2
        if side is None:
            return 0, width
        if 'old' == side:
            return 0, half
        return half + len(HALF_SEPARATOR), width - half - len(HALF_SEPARATOR)

    def _gap(self, index: int, side: str | None) -> DiffGap:
        """Makes the comment box's room below a row, in the half of a side
        like the threads there.

        Args:
            index: the diff pane's row.
            side: old or new, None for the whole width.

        Returns:
            The gap.
        """
        if side is None:
            return DiffGap(self._diff, self._layout[DIFF], index)
        top, left, height, _width = self._layout[DIFF]
        offset, width = self._half(side)
        return DiffGap(self._diff, (top, left + 2 + offset, height, width), index)

    def _target_thread(self) -> Comment | None:
        """Finds the thread keys like y or r act on: the selected one in the
        comments, the one at the cursor in the diff.

        Returns:
            The thread, None elsewhere.
        """
        if COMMENTS == self._focus:
            return self._selected_comment()
        if DIFF == self._focus:
            return self._thread_at_cursor()
        return None

    def _thread_patch_set(self) -> None:
        """Shows the thread at the cursor on its own patch set, the diff
        against its parent, so a thread that lost its line is seen where it
        was written (g p). Again, the comparison before comes back.
        """
        if self._previous_compare is not None:
            compare, path, number = self._previous_compare
            self._previous_compare = self._extra_file = None
            self._compare = compare
            self.reload()
            if path in self._files:
                self._file_index = self._files.index(path)
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
        (first, _), (second, _) = versions_to_compare(self._change, *self._compare)
        if BASE == first and str(comment.patch_set) == second:
            self._interface.say(f'#{comment.number} is shown on its PS{comment.patch_set}.')
            return
        current = self._files[self._file_index] if self._files else None
        self._previous_compare = (self._compare, current, comment.number)
        self.compare = (BASE, str(comment.patch_set))
        path, _line = split_place(comment.place)
        if path not in self._files and PATCHSET_LEVEL != path:
            # A file the change doesn't have any more
            self._extra_file = path
            self._files.append(path)
        self.show_thread(comment.number)
        self._interface.say(f'#{comment.number} on PS{comment.patch_set}. '
                            f'{self._interface.keymap.label("thread-patch-set")} goes back.')

    def _toggle_threads(self) -> None:
        """Folds or unfolds the threads at the cursor (l in the diff)."""
        numbers = self._threads_at_cursor()
        if not numbers:
            self._interface.say('No thread on this line. c starts one.')
            return
        comments = [self._change.comment(number) for number in numbers]
        expand = not any(self._is_expanded(comment) for comment in comments)
        for comment in comments:
            self._expanded[comment.number] = expand

    def _toggle_all_threads(self) -> None:
        """Folds all threads if one is unfolded, else unfolds all (i)."""
        comments = self._change.comments
        expand = not any(self._is_expanded(comment) for comment in comments)
        self._expanded = {comment.number: expand for comment in comments}

    # ── keys and mouse ────────────────────────────────────────────────────────

    def action(self, action: str, count: int | None = None) -> None:
        """Handles an action of the change screen.

        Args:
            action: the action's name.
            count: the count typed before it, like the 15 of 15j. None for
                none.
        """
        use_repository(self._repository)
        interface = self._interface
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
        if self._moving is not None and 'open' == action:
            self._drop_thread()
            return
        if 'move-thread' == action:
            comment = self._target_thread()
            if comment is None:
                interface.say('No thread here to move.')
                return
            self._moving = comment.number
            self._focus_pane(DIFF)
            interface.say(f'Moving #{comment.number}: go to its line, '
                          f'{interface.keymap.label("open")} drops it, '
                          f'{interface.keymap.label("back")} cancels.')
            return
        if 'next-pane' == action:
            # Only the panes shown
            panes = [pane for pane in PANES if not self._layout or pane in self._layout]
            self._focus_pane(panes[(panes.index(self._focus) + 1) % len(panes)]
                             if self._focus in panes else DIFF)
        elif 'last-pane' == action:
            last = getattr(self, '_last_pane', None)
            if last is None or (self._layout and last not in self._layout) or last == self._focus:
                interface.say('No pane focused before.')
            else:
                self._focus_pane(last)
        elif 'sidebar' == action:
            interface.sidebar = 'hidden' if self._layout and FILES in self._layout else 'shown'
        elif action in ('next-change', 'prev-change'):
            ids = [other.change_id for other in self._stack]
            step = 1 if 'next-change' == action else -1
            index = ids.index(self._change_id) + step * (count or 1)
            if 0 <= index < len(ids):
                self._switch_to(ids[index])
            else:
                interface.say('No more changes in this direction.')
        elif MESSAGE == self._focus and action in LIST_ACTIONS:
            _top, _left, box_height, width = self._layout[MESSAGE]
            self._message_scroll = position_after(self._message_scroll, action,
                                        max(len(self._message_lines(width)) - box_height + 1, 1),
                                        count)
        elif STACK == self._focus and action in LIST_ACTIONS:
            self._stack_index = position_after(self._stack_index, action, len(self._stack), count)
        elif FILES == self._focus and action in LIST_ACTIONS:
            self._file_index = position_after(self._file_index, action, len(self._files), count)
        elif COMMENTS == self._focus and action in LIST_ACTIONS:
            self._comment_index = position_after(self._comment_index, action,
                                                 len(change.comments), count)
            selected_comment = self._selected_comment()
            if selected_comment:
                self._show_comment_in_diff(selected_comment)
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
        elif action in ('next-thread', 'prev-thread', 'next-unresolved', 'prev-unresolved',
                        'next-relevant', 'prev-relevant'):
            self._go_to_thread(action.startswith('next'), action.split('-')[1])
        elif action in ('search', 'search-next', 'search-prev', 'search-clear'):
            self._search(action)
        elif 'open' == action:
            self._open()
        elif 'vimdiff' == action and self._selected_file():
            interface.run_outside(diff_command(change, *self._compare, uses_difftool=True,
                                                    path=self._selected_file()))
        elif 'patch-sets' == action:
            interface.open(PatchSetView(interface, self))
        elif 'compare-prompt' == action:
            self._choose_patch_sets()
        elif 'compare-last-send' == action:
            try:
                self.compare = last_send_compare(change)
            except ChangeError as error:
                interface.say(str(error))
            else:
                interface.say('Now ' + compare_label(change, *self._compare))
        elif 'follow-latest' == action:
            interface.follow_latest = not interface.follow_latest
            set_follows_latest(interface.follow_latest)
            if interface.follow_latest:
                self._compare = (self._compare[0],
                                 right_side(change, self._compare[1]))
            interface.say('Follow latest: ' + ('on, a new upload moves the right side.'
                                               if interface.follow_latest
                                               else 'off, the right side stays.'))
        elif action in ('unfold-every', 'fold-every'):
            expand = 'unfold-every' == action
            self._expanded = {comment.number: expand for comment in self._change.comments}
        elif 'fold-all' == action:
            self._toggle_all_threads()
        elif 'comment' == action and DIFF == self._focus and (
                gap := self._diff.gap_at(self._diff.cursor)) is not None:
            # Like in Gerrit, a gap of common lines opens.
            self._diff.expanded_gaps.add(gap)
        elif 'whitespace' == action:
            modes = WHITESPACE_MODES
            interface.whitespace = modes[(modes.index(interface.whitespace) + 1) % len(modes)]
            interface.say(f'Whitespace: ignore {interface.whitespace}.')
        elif action in ('side-old', 'side-new'):
            self._diff.side = 'old' if 'side-old' == action else 'new'
        elif 'reviewed' == action and self._selected_file():
            path = self._selected_file() or ''
            marked = toggle_reviewed(self._change_id, path)
            interface.after_write()
            interface.say(f'{file_label(path)} '
                          + ('reviewed.' if marked else 'not reviewed any more.'))
        elif 'reviewed-next' == action and self._selected_file():
            path = self._selected_file() or ''
            mark_reviewed(self._change_id, path)
            interface.after_write()
            following = file_after(self._files, path)
            if following is None:
                interface.say(f'{file_label(path)} reviewed, the last file.')
            else:
                self._file_index = self._files.index(following)
                interface.say(f'{file_label(path)} reviewed, now '
                              f'{file_label(following, self._renames)}.')
        elif 'approve' == action:
            approve(interface, change)
        elif 'comment' == action and DIFF == self._focus and self._diff.thread_at(
                self._diff.cursor) is not None:
            # Several threads on one line are fine: c starts another one.
            self._new_comment(on_thread=self._thread_at_cursor())
        elif 'comment' == action:
            self._new_comment()
        elif 'file-comment' == action:
            self._new_comment(is_whole_file=True)
        elif 'comment-above' == action:
            if DIFF != self._focus:
                interface.say(f'{interface.keymap.label("comment-above")} comments on the line '
                              'above the cursor, in the diff.')
                return
            above = self._diff.line_above(self._diff.cursor)
            if above is None:
                interface.say('No line above to comment on.')
                return
            self._diff.set_cursor(above, height)
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
            assert comment is not None
            anchor = self._box_below_thread(comment)
            if 'reply' == action:
                reply_to_thread(interface, self._change_id, comment, anchor)
            else:
                edit_draft(interface, self._change_id, comment, anchor)
        elif 'thread-patch-set' == action:
            self._thread_patch_set()
        else:
            thread_action(interface, change, self._target_thread(), action)

    def _drop_thread(self) -> None:
        """Moves the thread being moved to the cursor's line, or to the
        whole file on a row without a line (Enter while moving).
        """
        interface = self._interface
        number, self._moving = self._moving, None
        assert number is not None
        path = self._selected_file()
        if DIFF != self._focus or path is None:
            interface.say(f'#{number} stays where it was: no file shown.', ERROR)
            return
        cursor = self._diff.cursor_place()
        line, side = cursor if cursor is not None else (None, 'new')
        try:
            place, side, patch_set = comment_place(self._change, path, line, side,
                                                   *self._compare)
            move = move_thread(self._change_id, number, place, side, patch_set)
        except ChangeError as error:
            interface.say(str(error), ERROR)
            return
        interface.after_write()
        interface.say(f'#{number} ' + (move_text(move) if move
                                       else f'now on {place_label(place, side)}') + '.', OK)

    def _marked_search(self, is_searched_here: bool) -> str:
        """Finds the search whose matches to mark.

        Args:
            is_searched_here: whether the search looks at the place drawn.

        Returns:
            The searched text if its matches are marked and the search
            looks here, else ''.
        """
        return self._interface.search if self._interface.marking and is_searched_here else ''

    def _search(self, action: str) -> None:
        """Searches the diff for a text typed in the bottom line, or for the
        last one again, and goes to the next (or previous) match. Opens the
        gaps and unfolds the threads with matches where the mode looks.

        Args:
            action: search, search-next, search-prev or search-clear.
        """
        interface = self._interface
        if 'search-clear' == action:
            interface.marking = False
            return
        if 'search' == action:
            def next_mode() -> str:
                """Switches to the next search mode.

                Returns:
                    The new prompt.
                """
                modes = SEARCH_MODES
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
            self._diff.open_gaps_with(interface.search)
        if mode in ('all', 'comments'):
            for comment in self._change.comments:
                if self._diff_path(self._place_of(comment).path) == self._selected_file() and \
                        comment_matches(comment, interface.search):
                    self._expanded[comment.number] = True
        self._prepare_diff()
        wrapped = self._diff.go_to_match(interface.search, 'search-prev' != action,
                                         self._diff_height(), mode)
        if wrapped is None:
            interface.say(f'"{interface.search}" is not in {SEARCH_PLACES[mode]}.', ERROR)
        elif wrapped:
            interface.say('Went on from the ' + ('bottom.' if 'search-prev' == action
                                                 else 'top.'))

    def _focus_pane(self, pane: str) -> None:
        """Moves the focus. Remembers the pane left for the diff, h goes
        back there.

        Args:
            pane: the pane, one of PANES.
        """
        if DIFF == pane and DIFF != self._focus:
            self._left_pane = self._focus
        if pane != self._focus:
            # For last-pane, like Alt+Tab
            self._last_pane = self._focus
        self._focus = pane

    def go_left(self) -> bool:
        """Goes back to the files or comments (h in the diff).

        Returns:
            Whether it did. Elsewhere h goes back to the previous screen.
        """
        if self._moving is not None:
            self._interface.say(f'#{self._moving} stays where it was.')
            self._moving = None
            return True
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
            self._focus_pane(self._left_pane)
        return True

    def _open(self) -> None:
        """Opens what the cursor is on (l): in the stack, shows the change
        under the cursor. In the files, shows the diff. In the comments,
        unfolds the thread and goes to it in the diff. In the diff, folds
        or unfolds the thread at the cursor.
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
        elif DIFF == self._focus and (gap := self._diff.gap_at(self._diff.cursor)) is not None:
            self._diff.expanded_gaps.add(gap)
        elif DIFF == self._focus:
            self._toggle_threads()

    def mouse(self, row: int, column: int, mouse_event: str) -> None:
        """Handles the mouse: a click focuses a pane and selects the row
        under it. A double click opens, the wheel scrolls the pane under
        the pointer.

        Args:
            row: the screen row.
            column: the screen column.
            mouse_event: the event, see the module mouse.
        """
        if not self._layout:
            return
        for pane, (top, left, height, width) in self._layout.items():
            if top <= row < top + height and left <= column < left + width:
                self._focus_pane(pane)
                wheel = wheel_action(mouse_event)
                if wheel:
                    if DIFF == pane:
                        # The wheel scrolls, so a tall thread can be read.
                        self._diff.scroll_by(3 if 'down' == wheel else -3, height)
                    else:
                        self.action(wheel)
                    return
                offset = row - top
                if STACK == pane and self._stack_first(height) + offset < len(self._stack):
                    self._stack_index = self._stack_first(height) + offset
                elif FILES == pane and self._files_first(height) + offset < len(self._files):
                    self._file_index = self._files_first(height) + offset
                elif COMMENTS == pane:
                    rows = self._comment_rows()
                    row = offset + getattr(self, '_comments_first', 0)
                    if row >= len(rows):
                        return
                    # A header picks its first thread
                    picked = rows[row][1]
                    if picked is None:
                        picked = rows[min(row + 1, len(rows) - 1)][1]
                    self._comment_index = picked or 0
                    selected_comment = self._selected_comment()
                    if selected_comment:
                        self._show_comment_in_diff(selected_comment)
                elif DIFF == pane:
                    self._diff.cursor = self._diff.stop_of(min(self._diff.scroll + offset,
                                                               max(len(self._diff.rows) - 1, 0)))
                    # A click picks the half of the diff, too.
                    self._diff.side = 'old' if column < left + 2 + (width - 2) // 2 else 'new'
                if DOUBLE_CLICK == mouse_event:
                    self._open()
                return

    @property
    def change(self) -> Change:
        """The change shown, None before the first reload."""
        return self._change

    @property
    def compare(self) -> tuple[str, str]:
        """The two versions compared, see versions_to_compare()."""
        return self._compare

    @compare.setter
    def compare(self, versions: tuple[str, str | None]) -> None:
        """Compares two other versions, checked here: a wrong choice must
        not break drawing the diff.

        Args:
            versions: (left, right), see versions_to_compare().
        """
        versions_to_compare(self._change, *versions)
        second = versions[1]
        if second is not None:
            second = right_side(self._change, second, self._interface.follow_latest)
        # A right side of None stands for the current commit.
        self._compare = cast(tuple[str, str], (versions[0], second))

    def show_thread(self, number: int) -> None:
        """Goes to a thread in the diff, unfolded.

        Args:
            number: the thread's number.
        """
        comment = self._change.comment(number)
        if comment is None:
            return
        self._expanded[number] = True
        self._show_comment_in_diff(comment)
        if self._diff.thread_at(self._diff.cursor) == number:
            self._focus_pane(DIFF)

    def _choose_patch_sets(self) -> None:
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

    def _new_comment(self, is_whole_file: bool = False, on_thread: Comment | None = None) -> None:
        """Starts a draft thread: on the diff's cursor line, or on the whole
        file from the file list, with C, or on diff rows without a line
        number (like the file's header).

        Args:
            is_whole_file: whether the thread is on the whole file.
            on_thread: a thread whose place the new one gets, its box below
                it. None for none.
        """
        interface = self._interface
        path = self._selected_file()
        if not path:
            return
        side = 'new'
        line = ''
        anchor = None
        place = self._diff.cursor_place() if DIFF == self._focus and not is_whole_file else None
        if on_thread is not None:
            # A new thread on the same place, its box below the thread
            shown = self._place_of(on_thread)
            place = (shown.line, shown.side) if shown.line is not None else None
            end = self._diff.thread_end(on_thread.number)
            anchor = self._gap(end, self._thread_half(on_thread)) if end is not None else None
            if place is None:
                is_whole_file = True
        if on_thread is not None and place is not None:
            line, side = str(place[0]), place[1]
        elif place is None:
            if self._layout:
                # The box opens at the top of the diff, where file threads are.
                anchor = DiffGap(self._diff, self._layout[DIFF], -1)
        else:
            line, side = str(place[0]), place[1]
            # The box opens right below the commented line, the diff scrolls
            # if needed.
            self._diff.move_cursor(0, self._layout[DIFF][2])
            anchor = self._gap(self._diff.line_end(self._diff.cursor), side)
        name = file_label(path).rsplit('/', 1)[-1]
        text = interface.write_text(f'Comment on {name}' + (f':{line}' if line else
                                                            ' (whole file)'), anchor=anchor)
        if not text:
            interface.say('Nothing saved.' if text is None else 'Empty comment, nothing saved.')
            return
        thread_place, side, patch_set = comment_place(
            self._change, path, int(line) if line else None, side, *self._compare)
        add_comment(self._change_id, thread_place, text, is_sent=False, side=side,
                    patch_set=patch_set)
        interface.after_write()
        interface.say(f'Draft saved. {interface.keymap.label("send")} sends all drafts.', OK)


def file_label(path: str, renames: dict[str, str] | None = None) -> str:
    """Names a file for display.

    Args:
        path: the file's path, COMMIT_MSG for the commit message.
        renames: each renamed file's new path, mapped to its old one. None
            for none.

    Returns:
        The path, the old path too for a renamed file, "Commit message" for
        the commit message.
    """
    if COMMIT_MSG == path:
        return 'Commit message'
    return f'{renames[path]} → {path}' if renames and path in renames else path


def reply_to_thread(interface: 'Interface', change_id: str, comment: Comment,
                    anchor: BoxAnchor = None) -> None:
    """Writes a reply as a draft (r), in the comment box.

    Args:
        interface: the interface.
        change_id: the change's full Change-Id.
        comment: the thread.
        anchor: where the box goes, see write_text().
    """
    if comment.has_draft():
        interface.say(f'You have a draft already. {interface.keymap.label("edit-draft")} '
                      'edits it.', ERROR)
        return
    text = interface.write_text(f'Reply to #{comment.number}', anchor=anchor)
    if text:
        add_message(change_id, comment.number, REVIEWER, text, is_sent=False)
        interface.after_write()
        interface.say(f'Reply saved as a draft. {interface.keymap.label("send")} sends all '
                      'drafts.', OK)


def edit_draft(interface: 'Interface', change_id: str, comment: Comment,
               anchor: BoxAnchor = None) -> None:
    """Edits a thread's draft (e). Saving it empty deletes it.

    Args:
        interface: the interface.
        change_id: the change's full Change-Id.
        comment: the thread.
        anchor: where the box goes, see write_text().
    """
    if not comment.has_draft():
        interface.say('No draft to edit in this thread.', ERROR)
        return
    text = interface.write_text('Edit draft (empty deletes it)', comment.messages[-1].text,
                                anchor)
    if text is None:
        return
    if text:
        review_threads.edit_draft(change_id, comment.number, text)
    else:
        delete_draft(change_id, comment.number)
    interface.after_write()


def thread_action(interface: 'Interface', change: Change, comment: Comment | None,
                  action: str) -> None:
    """Handles the actions on a thread or on all drafts.

    Args:
        interface: the interface.
        change: the change.
        comment: the selected thread, None while the files are selected.
        action: send, done, ack or resolve. Others do nothing.
    """
    if 'send' == action:
        # Like Gerrit's Reply dialog: the drafts, a message on the whole
        # change and the vote go in one step.
        drafts = change.n_drafts()
        choice: tuple[str, Choice] | None = None
        if change.patch_sets:
            choice = (f'Code-Review on PS{change.latest_patch_set()}',
                      Choice(vote_values(), change.own_vote()))
        message = interface.write_text(
            'Reply', save_label='send', choice=choice,
            lines=[(f'{drafts} draft{"" if 1 == drafts else "s"} to send. '
                    'A message on the whole change, optional:' if drafts
                    else 'No drafts. A message on the whole change, optional:', False)])
        if message is None:
            interface.say('Nothing sent.')
            return
        count, voted = review_reply.reply(change.change_id, message,
                                  choice[1].value if choice else None)
        interface.after_write()
        done: list[str] = []
        if count:
            done.append(f'{count} draft{"" if 1 == count else "s"} sent'
                        + (' with a message' if message else ''))
        elif message:
            done.append('message sent')
        if voted:
            value = choice[1].value if choice else 0
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
        text = DONE if 'done' == action else ACK
        add_message(change.change_id, comment.number, REVIEWER, text, is_sent=False,
                         status=RESOLVED)
        interface.after_write()
        interface.say(f'"{text}" saved as a draft, it resolves #{comment.number}. '
                      f'{interface.keymap.label("send")} sends.', OK)
    elif 'resolve' == action:
        status = toggle_draft_status(change.change_id, comment.number)
        interface.after_write()
        interface.say(f'Sending the draft makes #{comment.number} {status}. '
                      f'{interface.keymap.label("send")} sends.')
