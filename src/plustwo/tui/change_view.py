# SPDX-License-Identifier: Zlib
"""The change screen: files, threads and the diff."""

import curses
from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Any, TypeVar, cast

from plustwo.config.user import current_user
from plustwo.config.view_state import set_follows_latest
from plustwo.diff.edits import WHITESPACE_MODES
from plustwo.git.commit_message import commit_message_parts
from plustwo.git.errors import ChangeError
from plustwo.git.files import files_of
from plustwo.git.repository import Repository
from plustwo.model.change import Change
from plustwo.model.comment import Comment
from plustwo.model.place import COMMIT_MSG, PATCHSET_LEVEL, split_place
from plustwo.model.version import BASE, LATEST
from plustwo.model.vote import CODE_REVIEW, VERIFIED
from plustwo.review.renames import own_sources
from plustwo.review.changes import changes_in_stack
from plustwo.review.compare import (
    can_compare, compare_label, compared_renames, diff_command, versions_to_compare)
from plustwo.review.history import follow_latest, right_side
from plustwo.review.labels import version_label
from plustwo.review.last_send import last_send_compare, last_send_patch_set
from plustwo.review.porting import comment_place
from plustwo.review.reviewed import file_after, mark_reviewed, toggle_approved, toggle_reviewed
from plustwo.review.stacks import needs_rebase
from plustwo.review.submit import stack_marks
from plustwo.review.threads import add_comment
from plustwo.review.touched import relevant_threads, threads_by_file, touched_threads
from plustwo.tui.area import Area
from plustwo.tui.change_actions import (
    approve, rebase_with_confirmation, submit_with_confirmation, vote_action)
from plustwo.tui.colours import HEADER_BACKGROUND, STATUS_COLOURS, tone, vote_badge
from plustwo.tui.comments_pane import CommentsPane
from plustwo.tui.diff_cells import CONTEXT_STEPS
from plustwo.tui.diff_gap import DiffGap
from plustwo.tui.diff_pane import DiffPane, patch_set_count
from plustwo.tui.diff_search import DiffSearch
from plustwo.tui.diff_threads import DiffThreads
from plustwo.tui.file_names import file_label
from plustwo.tui.files_pane import FilesPane
from plustwo.tui.lists import LIST_ACTIONS, position_after, status_attribute, wheel_action
from plustwo.tui.message_pane import MessagePane
from plustwo.tui.messages import OK
from plustwo.tui.mouse import DOUBLE_CLICK
from plustwo.tui.pane_frame import PaneBox, draw_frame
from plustwo.tui.patch_set_view import PatchSetView
from plustwo.tui.sidebar import (
    COMMENTS, DIFF, FILES, MESSAGE, PANES, STACK, FooterEntry, sidebar_heights, sidebar_shown,
    thread_footer)
from plustwo.tui.stack_pane import StackPane
from plustwo.tui.thread_actions import THREAD_ACTIONS, edit_draft, reply_to_thread, thread_action

if TYPE_CHECKING:
    from plustwo.tui.interface import Interface

T = TypeVar('T')


# What the change screen compares at first: the whole change as of the
# latest patch set
DEFAULT_COMPARE = ('base', 'latest')


class ChangeView:
    """One change: the stack, the commit message, the files and the threads
    on the left, the diff on the right.

    The screen loads the change, keeps the compared versions, lays out the
    panes, moves the focus, routes the mouse and draws. Each pane of the
    sidebar is a class of its own, which holds its cursor and draws itself
    from what the screen hands it. DiffThreads and DiffSearch act on the
    diff and read the change, the comparison and the diff from the screen.
    """

    def __init__(self, repository: Repository, interface: 'Interface', change_id: str) -> None:
        """Starts the screen on a change, see reload().

        Args:
            repository: the repository the change is in.
            interface: the interface.
            change_id: the change's full Change-Id.
        """
        self._interface = interface
        self._change_id = change_id
        self._repository = repository
        # None until the first reload(), then the change
        self._change: Any = None
        self._compare: tuple[str, str] = DEFAULT_COMPARE
        self._focus = FILES
        # The pane h returns to from the diff, and the one focused before
        # the current one, for last-pane
        self._left_pane = FILES
        self._last_pane: str | None = None
        self._stack_pane = StackPane(interface)
        self._message_pane = MessagePane(interface)
        self._files_pane = FilesPane(interface)
        self._comments_pane = CommentsPane(interface, self._groups)
        self._diff = DiffPane(interface.colour_pairs, self._theme)
        self._threads = DiffThreads(interface, self)
        self._search = DiffSearch(interface, self)
        self._layout: dict[str, PaneBox] = {}
        # (compare, file, thread number) to return to after a thread was
        # shown on its own patch set, and the file shown there if this
        # version doesn't have it
        self._previous_compare: tuple[tuple[str, str], str | None, int] | None = None
        self._extra_file: str | None = None
        # The latest patch set at the last reload, to see a new upload
        self._latest_seen: int | None = None
        # Whether the stack needs a rebase: None, 'base moved' or
        # 'conflicts'
        self._behind: str | None = None
        self._cache: tuple[Change | None, dict[Any, Any]] = (None, {})
        self._actions = self._action_table()

    # ── what the held classes read ────────────────────────────────────────────

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

    @property
    def diff(self) -> DiffPane:
        """The diff of the selected file."""
        return self._diff

    @property
    def diff_box(self) -> PaneBox:
        """The diff pane's inside on the screen, once laid out."""
        return self._layout[DIFF]

    @property
    def is_laid_out(self) -> bool:
        """Whether the panes were placed, see draw()."""
        return bool(self._layout)

    @property
    def files_pane(self) -> FilesPane:
        """The file list, whose cursor selects the file in the diff."""
        return self._files_pane

    @property
    def comments_pane(self) -> CommentsPane:
        """The thread list."""
        return self._comments_pane

    @property
    def threads(self) -> DiffThreads:
        """The threads in the diff."""
        return self._threads

    @property
    def focus(self) -> str:
        """The focused pane, one of PANES."""
        return self._focus

    def diff_height(self) -> int:
        """Finds the diff pane's height.

        Returns:
            The height, 20 before the first layout.
        """
        return self._layout[DIFF][2] if self._layout else 20

    def relevant(self) -> set[int]:
        """Finds the threads worth a look, see relevant_threads().

        Returns:
            The threads' numbers.
        """
        return self._cached('relevant', lambda: relevant_threads(self._change, *self._compare))

    def focus_pane(self, pane: str) -> None:
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

    def prepare_diff(self) -> bool:
        """Renders the selected file's diff and lays out its threads.

        Returns:
            False if there is nothing to show.
        """
        path = self._files_pane.selected()
        if not path or not self._layout:
            return False
        width = self._layout[DIFF][3] - 2
        self._diff.height = self._layout[DIFF][2]
        search_mode = self._interface.search_mode
        self._diff.show(self._change, self._compare, path, width,
                        CONTEXT_STEPS[self._interface.context_step], self._interface.whitespace,
                        self._search.marked('comments' != search_mode))
        # The matches in threads are marked when the search looks there.
        self._threads.arrange(path, width, self._search.marked(search_mode in ('all', 'comments')))
        return True

    # ── loading ───────────────────────────────────────────────────────────────

    def _theme(self) -> str | None:
        """Reads bat's theme for the diff from the change's repository.

        Returns:
            The theme, None for bat's default.
        """
        return self._interface.theme(self._repository)

    def reload(self) -> None:
        """Reads the change again. Goes back if it is gone."""
        for change in changes_in_stack(self._repository):
            if change.change_id == self._change_id:
                self._load(change)
                return
        self._interface.back()
        self._interface.say('The change is not in review any more.')

    def _load(self, change: Change) -> None:
        """Shows the change as read again, see reload().

        Args:
            change: the change.
        """
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
        files = self._files_pane
        files.counts = files_of(self._repository, change.commit)
        files.files = [COMMIT_MSG] + sorted(
            set(files.counts) - set(own_sources(self._repository, change.commit).values()))
        # Named with the path in the older compared version, which may come
        # from a rename between patch sets
        files.renames = compared_renames(change, *self._compare)
        if self._extra_file and self._extra_file not in files.files:
            files.files.append(self._extra_file)
        if not files.has_cursor:
            # The commit message stays first, the first real file is shown.
            files.index = 1 if len(files.files) > 1 else 0
        stack = self._stack_pane
        stack.changes = [other for other in changes_in_stack(self._repository)
                         if other.topic == change.topic]
        if STACK != self._focus:
            stack.index = stack.row_of(self._change_id)
        stack.marks = stack_marks(stack.changes)
        self._message_pane.message = commit_message_parts(self._repository, change.commit)
        # Where it is the user's turn, like the dashboard's "▶ you"
        user = current_user()
        stack.yours = {other.change_id for other in stack.changes
                       if 'your-turn' in other.sections(user)}
        self._behind = (
            'conflicts' if any(other.rebase_is_requested for other in stack.changes)
            else 'base moved' if needs_rebase(self._repository, change.topic) else None)
        self._diff.forget()

    def _switch_to(self, change_id: str) -> None:
        """Shows another change of the stack.

        Args:
            change_id: the change's full Change-Id.
        """
        note = self.leaving_note() if change_id != self._change_id else ''
        if note:
            self._interface.say(note)
        self._change_id = change_id
        self._files_pane.index = None
        self._comments_pane.index = 0
        self._threads.forget()
        # Like in Gerrit, every change opens with its default comparison.
        self._compare = DEFAULT_COMPARE
        self._previous_compare = self._extra_file = None
        self._latest_seen = None
        # From the stack pane, the focus stays there to go on.
        if STACK != self._focus:
            self._focus = self._left_pane = FILES
        self._diff = DiffPane(self._interface.colour_pairs, self._theme)
        self.reload()
        self._stack_pane.index = self._stack_pane.row_of(self._change_id)

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

    def _cached(self, name: str, compute: Callable[[], T]) -> T:
        """Computes a value of the shown change and comparison once for
        them: the drawing needs it on every frame.

        Args:
            name: the value's name.
            compute: the function that computes it.

        Returns:
            The value.
        """
        key = (name, self._compare, tuple(self._files_pane.files))
        change, cache = self._cache
        if change is not self._change:
            cache = {}
            self._cache = (self._change, cache)
        if key not in cache:
            cache[key] = compute()
        return cast(T, cache[key])

    def _touched(self) -> set[int]:
        """Finds the threads the comparison touches.

        Returns:
            The threads' numbers.
        """
        return self._cached('touched', lambda: touched_threads(self._change, *self._compare))

    def _groups(self) -> list[tuple[str, list[Comment]]]:
        """Groups the threads by file, see threads_by_file().

        Returns:
            (path, threads) per file.
        """
        if not self._change:
            return []
        return self._cached('groups', lambda: threads_by_file(
            self._change, self._files_pane.files, *self._compare))

    # ── layout and drawing ────────────────────────────────────────────────────

    def footer(self) -> list[FooterEntry]:
        """Lists only the actions that work right now: those of the focused
        pane and what the cursor is on, then those the change's state
        allows.

        Returns:
            The footer's entries.
        """
        change = self._change
        threads = self._threads
        if threads.moving is not None:
            return [(('down', 'up'), f'to the line of #{threads.moving}'),
                    (('side-old', 'side-new'), 'side'), (('open',), 'drop here'),
                    (('back',), 'cancel')]
        if STACK == self._focus:
            entries = [(('down', 'up'), 'move'), (('open',), 'show change')]
        elif MESSAGE == self._focus:
            entries = [(('down', 'up'), 'scroll'), (('open',), 'comment on it')]
        elif FILES == self._focus:
            entries = [(('open',), 'diff'), (('reviewed',), 'reviewed'),
                       (('reviewed-next',), 'reviewed, next'), (('approve-file',), '+2 file'),
                       (('file-comment',), 'comment')]
        elif COMMENTS == self._focus:
            entries = [(('open',), 'go to')] + thread_footer(self._comments_pane.selected())
        elif self._diff.rows and self._diff.thread_at(self._diff.cursor) is not None:
            entries = thread_footer(threads.thread_at_cursor()) + [
                (('comment',), 'new thread'),
                (('open',), 'fold' if threads.is_expanded(threads.thread_at_cursor())
                 else 'unfold')]
            if self._diff.is_tall(self.diff_height()):
                entries.insert(0, (('down', 'up'), 'read on'))
        elif self._diff.rows and self._diff.gap_at(self._diff.cursor) is not None:
            entries = [(('open',), 'expand')]
        else:
            entries = [(('comment',), 'comment'), (('side-old', 'side-new'), 'side'),
                       (('next-chunk', 'prev-chunk'), 'chunk')]
            if change.unfinished_comments():
                entries.append((('next-unresolved', 'prev-unresolved'), 'unresolved'))
            if self.relevant():
                entries.append((('next-relevant', 'prev-relevant'), 'relevant'))
            entries.append((('reviewed-next',), 'reviewed, next'))
            if self._interface.marking:
                entries += [(('search-next', 'search-prev'), 'match'),
                            (('search-clear',), 'unmark')]
            if threads.threads_at_cursor():
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
        if len(self._stack_pane.changes) > 1:
            entries.append((('next-change', 'prev-change'), 'stack'))
        thread = threads.target_thread()
        if self._previous_compare is not None:
            entries.insert(0, (('thread-patch-set',), 'back'))
        elif thread is not None and threads.place_of(thread).origin:
            entries.insert(0, (('thread-patch-set',), f'on PS{thread.patch_set}'))
        return entries + [(('sidebar',), 'sidebar'), (('help',), 'all keys')]

    def _compute_layout(self, area: Area) -> dict[str, PaneBox]:
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
            area.height - 2 - 8, [len(self._stack_pane.changes),
                                  len(self._message_pane.lines(left_width - 2)),
                                  len(self._files_pane.files),
                                  len(self._comments_pane.rows()) or 1])
        message_top = 3 + stack_rows + 2
        files_top = message_top + message_rows + 2
        comments_top = files_top + files_height + 2
        return {STACK: (3, 1, stack_rows, left_width - 2),
                MESSAGE: (message_top, 1, message_rows, left_width - 2),
                FILES: (files_top, 1, files_height, left_width - 2),
                COMMENTS: (comments_top, 1, comments_height, left_width - 2),
                DIFF: (3, left_width + 1, area.height - 4, area.width - left_width - 3)}

    def draw(self, area: Area) -> None:
        """Draws the header and the panes.

        Args:
            area: the part of the screen to draw into.
        """
        change = self._change
        self._layout = self._compute_layout(area)
        self._draw_header(area.width)
        if FILES in self._layout:
            self._stack_pane.draw(self._layout[STACK], STACK == self._focus, self._change_id,
                                  change.base)
            self._message_pane.draw(self._layout[MESSAGE], MESSAGE == self._focus)
            self._files_pane.draw(self._layout[FILES], FILES == self._focus, change,
                                  self._compare, self._groups())
            self._comments_pane.draw(self._layout[COMMENTS], COMMENTS == self._focus,
                                     self._touched(), self._files_pane.selected(),
                                     self._threads.place_of, self._files_pane.renames)
        self._draw_diff()

    def _draw_header(self, width: int) -> None:
        """Draws the two header rows: where the change is, its subject and
        state, then the patch sets compared and the votes.

        Args:
            width: the screen's width.
        """
        out = self._interface.text
        change = self._change
        bar = self._interface.colour_pairs.attribute(15, tone(HEADER_BACKGROUND))
        header_dim = self._interface.dim(background=tone(HEADER_BACKGROUND))
        out(0, 0, ' ' * width, bar)
        place = ((f'{change.repository_name} › ' if change.repository_name else '')
                 + f'{change.topic} on {change.base} ›')
        out(0, 1, place, header_dim)
        out(0, 2 + len(place), change.subject, bar | curses.A_BOLD)
        state = f' {change.state()} '
        chip = self._interface.colour_pairs.attribute(
            0, {'New': 8, 'In-Review': 3, 'Submitted': 2}.get(change.state(), 8)) | curses.A_BOLD
        version = f'{change.short_id()}  {version_label(change)} '
        out(0, max(width - len(version) - len(state) - 1, 1), state, chip)
        behind = ''
        if self._behind:
            behind = (' Needs rebase ' if 'base moved' == self._behind
                      else ' Conflicts, the author rebases ')
            out(0, max(width - len(version) - len(state) - len(behind) - 2, 1), behind,
                self._interface.colour_pairs.attribute(0, 1 if 'conflicts' == self._behind
                                                       else 3) | curses.A_BOLD)
        drafts = change.n_drafts()
        if drafts:
            # Unsent drafts, in the drafts' colour, so they aren't forgotten
            label = f' {drafts} draft{"" if 1 == drafts else "s"} '
            behind_width = len(behind) + 1 if behind else 0
            out(0, max(width - len(version) - len(state) - behind_width - len(label) - 2, 1),
                label,
                self._interface.colour_pairs.attribute(0, STATUS_COLOURS['draft'])
                | curses.A_BOLD)
        out(0, max(width - len(version), 1), version, header_dim)
        patch_sets = patch_set_count(len(change.patch_sets))
        try:
            follow = self._interface.follow_latest
            comparing = compare_label(change, *self._compare, does_follow_latest=follow)
            if len(patch_sets) + 3 + len(comparing) > width - 2:
                comparing = compare_label(change, *self._compare, does_follow_latest=follow,
                                          is_short=True)
        except ChangeError as error:
            comparing = str(error)
        header = f'{patch_sets}   {comparing}'
        if FILES not in self._layout:
            # Without the files pane, the header says which file it is,
            # with fewer words when the line is narrow.
            files = self._files_pane
            label = files.label(files.selected() or '')
            where = f'file {files.index + 1}/{len(files.files)}: '
            sidebar = f'sidebar hidden ({self._interface.keymap.label("sidebar")}), '
            for extra in (sidebar + where + label, where + label,
                          where + label.rsplit('/', 1)[-1]):
                if len(header) + 3 + len(extra) <= width - 2:
                    break
            header += '   ' + extra
            header = header[:max(width - 2, 0)]
        out(1, 1, header, self._interface.dim())
        self._draw_votes(4 + len(header))

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

    def _draw_diff(self) -> None:
        """Draws the diff of the selected file, with cursor and thread marks."""
        path = self._files_pane.selected()
        context = CONTEXT_STEPS[self._interface.context_step]
        context_label = 'whole file' if context is None else f'context {context}'
        if 'none' != self._interface.whitespace:
            context_label += f', whitespace: ignore {self._interface.whitespace}'
        if path:
            self.prepare_diff()
        below = ''.join(legend for legend, shown in zip(
            (', blue: from changes below', ', purple: partly from below'),
            self._diff.marks_from_below) if shown)
        if self._diff.has_moved:
            below += ', teal: moved'
        title = f'{self._files_pane.label(path)}  ({context_label}{below})' if path else 'Diff'
        draw_frame(self._interface, self._layout[DIFF], title, DIFF == self._focus)
        height = self._layout[DIFF][2]
        if not path:
            return
        self.prepare_diff()
        markers = self._threads.markers()
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
            markers: the threads by place, see DiffThreads.markers().
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

    # ── keys ──────────────────────────────────────────────────────────────────

    def action(self, action: str, count: int | None = None) -> None:
        """Handles an action of the change screen: moving the cursor of the
        focused pane, else what the action table names.

        Args:
            action: the action's name.
            count: the count typed before it, like the 15 of 15j. None for
                none.
        """
        if self._threads.moving is not None and 'open' == action:
            self._threads.drop()
            return
        if self._move_cursor(action, count):
            return
        handler = self._actions.get(action)
        if handler is not None:
            handler(count)

    def _action_table(self) -> dict[str, Callable[[int | None], None]]:
        """Maps the actions to what they do. Each gets the count typed
        before the key.

        Returns:
            Each action's name, mapped to its handler.
        """
        interface = self._interface
        threads = self._threads
        search = self._search
        table: dict[str, Callable[[int | None], None]] = {
            'move-thread': lambda _count: threads.start_moving(),
            'next-pane': lambda _count: self._next_pane(),
            'last-pane': lambda _count: self._focus_last_pane(),
            'sidebar': lambda _count: self._toggle_sidebar(),
            'next-change': lambda count: self._step_change(count or 1),
            'prev-change': lambda count: self._step_change(-(count or 1)),
            'next-file': lambda count: self._files_pane.step(count or 1),
            'prev-file': lambda count: self._files_pane.step(-(count or 1)),
            'next-chunk': lambda _count: self._go_to_chunk(True),
            'prev-chunk': lambda _count: self._go_to_chunk(False),
            'next-thread': lambda _count: threads.go_to(True),
            'prev-thread': lambda _count: threads.go_to(False),
            'next-unresolved': lambda _count: threads.go_to(True, 'unresolved'),
            'prev-unresolved': lambda _count: threads.go_to(False, 'unresolved'),
            'next-relevant': lambda _count: threads.go_to(True, 'relevant'),
            'prev-relevant': lambda _count: threads.go_to(False, 'relevant'),
            'search': lambda _count: search.run('search'),
            'search-next': lambda _count: search.run('search-next'),
            'search-prev': lambda _count: search.run('search-prev'),
            'search-clear': lambda _count: search.run('search-clear'),
            'open': lambda _count: self._open(),
            'vimdiff': lambda _count: self._vimdiff(),
            'patch-sets': lambda _count: interface.open(PatchSetView(interface, self)),
            'compare-prompt': lambda _count: self._choose_patch_sets(),
            'compare-last-send': lambda _count: self._compare_last_send(),
            'follow-latest': lambda _count: self._toggle_follow_latest(),
            'unfold-every': lambda _count: threads.set_all_expanded(True),
            'fold-every': lambda _count: threads.set_all_expanded(False),
            'fold-all': lambda _count: threads.toggle_all(),
            'whitespace': lambda _count: self._next_whitespace(),
            'side-old': lambda _count: self._set_side('old'),
            'side-new': lambda _count: self._set_side('new'),
            'reviewed': lambda _count: self._toggle_reviewed(),
            'reviewed-next': lambda _count: self._reviewed_next(),
            'approve-file': lambda _count: self._toggle_approved(),
            'approve': lambda _count: approve(interface, self._change),
            'comment': lambda _count: self._comment(),
            'file-comment': lambda _count: self._new_comment(is_whole_file=True),
            'comment-above': lambda _count: self._comment_above(),
            'more-context': lambda _count: self._step_context(1),
            'less-context': lambda _count: self._step_context(-1),
            'vote': lambda _count: vote_action(interface, self._change, 'vote'),
            'verified': lambda _count: vote_action(interface, self._change, 'verified'),
            'submit': lambda _count: submit_with_confirmation(interface, self._change),
            'rebase': lambda _count: rebase_with_confirmation(interface, self._change),
            'reply': lambda _count: self._reply(),
            'edit-draft': lambda _count: self._edit_draft(),
            'thread-patch-set': lambda _count: self._thread_patch_set(),
        }
        for name in THREAD_ACTIONS:
            table[name] = partial(self._thread_action, name)
        return table

    def _thread_action(self, action: str, _count: int | None) -> None:
        """Handles an action on the target thread or on all drafts, see
        thread_action().

        Args:
            action: one of THREAD_ACTIONS.
            _count: the count typed before the key, unused.
        """
        thread_action(self._interface, self._change, self._threads.target_thread(), action)

    def _move_cursor(self, action: str, count: int | None) -> bool:
        """Moves the cursor of the focused pane, or scrolls it.

        Args:
            action: the action's name.
            count: the count typed before the key, None for none.

        Returns:
            Whether the action moved a cursor.
        """
        if DIFF == self._focus:
            return self._move_in_diff(action, count)
        if action not in LIST_ACTIONS:
            return False
        if MESSAGE == self._focus:
            self._message_pane.move(action, self._layout[MESSAGE], count)
        elif STACK == self._focus:
            self._stack_pane.move(action, count)
        elif FILES == self._focus:
            self._files_pane.move(action, count)
        elif COMMENTS == self._focus:
            self._move_in_comments(action, count)
        return True

    def _move_in_comments(self, action: str, count: int | None) -> None:
        """Moves the comments pane's cursor and shows its thread in the diff.

        Args:
            action: one of LIST_ACTIONS.
            count: the count typed before the key, None for none.
        """
        pane = self._comments_pane
        pane.index = position_after(pane.index, action, len(self._change.comments), count)
        selected = pane.selected()
        if selected:
            self._threads.show(selected)

    def _move_in_diff(self, action: str, count: int | None) -> bool:
        """Moves the diff's cursor, or scrolls the diff.

        Args:
            action: the action's name.
            count: the count typed before the key, None for none.

        Returns:
            Whether the action was one of the diff's moves.
        """
        diff = self._diff
        height = self.diff_height()
        moves: dict[str, Callable[[], None]] = {
            'down': lambda: diff.move_cursor(count or 1, height),
            'up': lambda: diff.move_cursor(-(count or 1), height),
            'top': lambda: (diff.go_to_new_line(count, height) if count
                            else diff.set_cursor(0, height)),
            'bottom': lambda: (diff.go_to_new_line(count, height) if count
                               else diff.set_cursor(len(diff.rows) - 1, height)),
            'page-down': lambda: diff.scroll_by(height // 2, height),
            'page-up': lambda: diff.scroll_by(-(height // 2), height),
            'scroll-down': lambda: diff.scroll_rows(count or 1, height),
            'scroll-up': lambda: diff.scroll_rows(-(count or 1), height),
        }
        move = moves.get(action)
        if move is None:
            return False
        move()
        return True

    # ── the actions ───────────────────────────────────────────────────────────

    def _next_pane(self) -> None:
        """Focuses the next pane shown (Tab)."""
        panes = [pane for pane in PANES if not self._layout or pane in self._layout]
        self.focus_pane(panes[(panes.index(self._focus) + 1) % len(panes)]
                        if self._focus in panes else DIFF)

    def _focus_last_pane(self) -> None:
        """Focuses the pane focused before, like Alt+Tab."""
        last = self._last_pane
        if last is None or (self._layout and last not in self._layout) or last == self._focus:
            self._interface.say('No pane focused before.')
        else:
            self.focus_pane(last)

    def _toggle_sidebar(self) -> None:
        """Hides or shows the sidebar for the rest of the session."""
        self._interface.sidebar = 'hidden' if self._layout and FILES in self._layout \
            else 'shown'

    def _step_change(self, n_changes: int) -> None:
        """Shows a change further up or down the stack.

        Args:
            n_changes: how far, negative for older changes.
        """
        ids = [other.change_id for other in self._stack_pane.changes]
        index = ids.index(self._change_id) + n_changes
        if 0 <= index < len(ids):
            self._switch_to(ids[index])
        else:
            self._interface.say('No more changes in this direction.')

    def _go_to_chunk(self, is_forward: bool) -> None:
        """Goes to the next or previous chunk of changed lines.

        Args:
            is_forward: whether to go down, else up.
        """
        self.focus_pane(DIFF)
        # The diff may not be laid out for this file yet
        self.prepare_diff()
        if not self._diff.go_to_chunk(is_forward, self.diff_height()):
            self._interface.say('No more changes in this direction.')

    def _open(self) -> None:
        """Opens what the cursor is on (l): in the stack, shows the change
        under the cursor. In the files, shows the diff. In the comments,
        unfolds the thread and goes to it in the diff. In the diff, folds
        or unfolds the thread at the cursor.
        """
        comment = self._comments_pane.selected()
        if STACK == self._focus:
            selected = self._stack_pane.selected()
            if selected is not None:
                self._switch_to(selected.change_id)
        elif MESSAGE == self._focus:
            # /COMMIT_MSG in the diff, to comment on it
            self._files_pane.index = 0
            self.focus_pane(DIFF)
        elif FILES == self._focus:
            self.focus_pane(DIFF)
        elif COMMENTS == self._focus and comment:
            self._threads.set_expanded(comment.number, True)
            self._threads.show(comment)
            if self._diff.thread_at(self._diff.cursor) == comment.number:
                self.focus_pane(DIFF)
            else:
                self._interface.say(f'#{comment.number} is on a file this version doesn\'t have.')
        elif DIFF == self._focus and (gap := self._diff.gap_at(self._diff.cursor)) is not None:
            self._diff.expanded_gaps.add(gap)
        elif DIFF == self._focus:
            self._threads.toggle_at_cursor()

    def _vimdiff(self) -> None:
        """Shows the selected file's diff in the difftool, on the terminal."""
        path = self._files_pane.selected()
        if path:
            self._interface.run_outside(diff_command(self._change, *self._compare,
                                                     uses_difftool=True, path=path))

    def _choose_patch_sets(self) -> None:
        """Asks which two versions to compare."""
        answer = self._interface.dialogs.ask('Compare (e.g. "1 3", "2 current", "base 2", '
                                             'empty for base latest):')
        if answer is None:
            return
        parts = answer.split()
        if not parts:
            self._compare = DEFAULT_COMPARE
        elif len(parts) <= 2 and all(part.isdigit() or part in ('current', 'base', 'latest')
                                     for part in parts):
            self.compare = (parts[0], parts[1] if 2 == len(parts) else None)
        else:
            self._interface.say('Give one or two of: patch set numbers, "base", "latest", '
                                '"current".')

    def _compare_last_send(self) -> None:
        """Compares the patch set of the last send with the latest."""
        try:
            self.compare = last_send_compare(self._change)
        except ChangeError as error:
            self._interface.say(str(error))
        else:
            self._interface.say('Now ' + compare_label(self._change, *self._compare))

    def _toggle_follow_latest(self) -> None:
        """Switches whether a new upload moves the right side along."""
        interface = self._interface
        interface.follow_latest = not interface.follow_latest
        set_follows_latest(interface.follow_latest)
        if interface.follow_latest:
            self._compare = (self._compare[0], right_side(self._change, self._compare[1]))
        interface.say('Follow latest: ' + ('on, a new upload moves the right side.'
                                           if interface.follow_latest
                                           else 'off, the right side stays.'))

    def _next_whitespace(self) -> None:
        """Switches to the next whitespace mode of the diff."""
        interface = self._interface
        modes = WHITESPACE_MODES
        interface.whitespace = modes[(modes.index(interface.whitespace) + 1) % len(modes)]
        interface.say(f'Whitespace: ignore {interface.whitespace}.')

    def _set_side(self, side: str) -> None:
        """Puts the diff's cursor on a side, which comments go to.

        Args:
            side: old or new.
        """
        self._diff.side = side

    def _step_context(self, n_steps: int) -> None:
        """Shows more or less context around the changed lines.

        Args:
            n_steps: how many steps of CONTEXT_STEPS, negative for less.
        """
        step = self._interface.context_step + n_steps
        self._interface.context_step = max(0, min(step, len(CONTEXT_STEPS) - 1))

    def _toggle_reviewed(self) -> None:
        """Marks the selected file as reviewed, or not any more."""
        path = self._files_pane.selected()
        if not path:
            return
        marked = toggle_reviewed(self._repository, self._change_id, path)
        self._interface.after_write()
        self._interface.say(f'{file_label(path)} '
                            + ('reviewed.' if marked else 'not reviewed any more.'))

    def _toggle_approved(self) -> None:
        """Marks the selected file as approved, or not any more."""
        path = self._files_pane.selected()
        if not path:
            return
        marked = toggle_approved(self._repository, self._change_id, path)
        self._interface.after_write()
        self._interface.say(f'{file_label(path)} '
                            + ('approved, also reviewed.' if marked else 'not approved any more.'))

    def _reviewed_next(self) -> None:
        """Marks the selected file as reviewed and goes to the next one."""
        path = self._files_pane.selected()
        if not path:
            return
        mark_reviewed(self._repository, self._change_id, path)
        self._interface.after_write()
        following = file_after(self._files_pane.files, path)
        if following is None:
            self._interface.say(f'{file_label(path)} reviewed, the last file.')
        else:
            self._files_pane.select(following)
            self._interface.say(f'{file_label(path)} reviewed, now '
                                f'{self._files_pane.label(following)}.')

    def _comment(self) -> None:
        """Comments where the cursor is (c): in the diff, a gap of common
        lines opens, like in Gerrit, and on a thread another one starts on
        its place. Elsewhere a new thread starts.
        """
        if DIFF == self._focus:
            gap = self._diff.gap_at(self._diff.cursor)
            if gap is not None:
                self._diff.expanded_gaps.add(gap)
                return
            if self._diff.thread_at(self._diff.cursor) is not None:
                # Several threads on one line are fine: c starts another one.
                self._new_comment(on_thread=self._threads.thread_at_cursor())
                return
        self._new_comment()

    def _comment_above(self) -> None:
        """Comments on the line above the diff's cursor."""
        interface = self._interface
        if DIFF != self._focus:
            interface.say(f'{interface.keymap.label("comment-above")} comments on the line '
                          'above the cursor, in the diff.')
            return
        above = self._diff.line_above(self._diff.cursor)
        if above is None:
            interface.say('No line above to comment on.')
            return
        self._diff.set_cursor(above, self.diff_height())
        self._new_comment()

    def _reply(self) -> None:
        """Replies to the target thread, in a box below it."""
        comment = self._threads.target_thread()
        if comment is not None:
            reply_to_thread(self._repository, self._interface, self._change_id, comment,
                            self._threads.gap_below(comment))

    def _edit_draft(self) -> None:
        """Edits the target thread's draft, in a box below it."""
        comment = self._threads.target_thread()
        if comment is not None:
            edit_draft(self._repository, self._interface, self._change_id, comment,
                       self._threads.gap_below(comment))

    def _thread_patch_set(self) -> None:
        """Shows the thread at the cursor on its own patch set, the diff
        against its parent, so a thread that lost its line is seen where it
        was written (g p). Again, the comparison before comes back.
        """
        files = self._files_pane
        if self._previous_compare is not None:
            compare, path, number = self._previous_compare
            self._previous_compare = self._extra_file = None
            self._compare = compare
            self.reload()
            if path in files.files:
                files.select(path)
            self.show_thread(number)
            self._interface.say('Back to the comparison before.')
            return
        comment = self._threads.target_thread()
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
        self._previous_compare = (self._compare, files.selected(), comment.number)
        self.compare = (BASE, str(comment.patch_set))
        path, _line = split_place(comment.place)
        if path not in files.files and PATCHSET_LEVEL != path:
            # A file the change doesn't have any more
            self._extra_file = path
            files.files.append(path)
        self.show_thread(comment.number)
        self._interface.say(f'#{comment.number} on PS{comment.patch_set}. '
                            f'{self._interface.keymap.label("thread-patch-set")} goes back.')

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
        threads = self._threads
        path = self._files_pane.selected()
        if not path:
            return
        side = 'new'
        line = ''
        anchor = None
        place = self._diff.cursor_place() if DIFF == self._focus and not is_whole_file else None
        if on_thread is not None:
            # A new thread on the same place, its box below the thread
            shown = threads.place_of(on_thread)
            place = (shown.line, shown.side) if shown.line is not None else None
            end = self._diff.thread_end(on_thread.number)
            anchor = threads.gap(end, threads.thread_half(on_thread)) if end is not None \
                else None
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
            anchor = threads.gap(self._diff.line_end(self._diff.cursor), side)
        name = file_label(path).rsplit('/', 1)[-1]
        text = interface.text_input.write_text(
            f'Comment on {name}' + (f':{line}' if line else ' (whole file)'), anchor=anchor)
        if not text:
            interface.say('Nothing saved.' if text is None else 'Empty comment, nothing saved.')
            return
        thread_place, side, patch_set = comment_place(
            self._change, path, int(line) if line else None, side, *self._compare)
        add_comment(self._repository, self._change_id, thread_place, text, is_sent=False,
                    side=side, patch_set=patch_set)
        interface.after_write()
        interface.say(f'Draft saved. {interface.keymap.label("send")} sends all drafts.', OK)

    # ── focus and mouse ───────────────────────────────────────────────────────

    def go_left(self) -> bool:
        """Goes back to the files or comments (h in the diff).

        Returns:
            Whether it did. Elsewhere h goes back to the previous screen.
        """
        if self._threads.moving is not None:
            self._threads.cancel_moving()
            return True
        if DIFF != self._focus:
            return False
        number = self._diff.thread_at(self._diff.cursor)
        if (ord('h'),) == self._interface.keymap.last and number is not None \
                and self._threads.is_expanded(self._change.comment(number)):
            # Like vim: l unfolds the thread, h folds it again
            self._threads.set_expanded(number, False)
            return True
        if self._previous_compare is not None:
            self._thread_patch_set()
            return True
        if self._layout and self._left_pane in self._layout:
            self.focus_pane(self._left_pane)
        return True

    def show_thread(self, number: int) -> None:
        """Goes to a thread in the diff, unfolded.

        Args:
            number: the thread's number.
        """
        comment = self._change.comment(number)
        if comment is None:
            return
        self._threads.set_expanded(number, True)
        self._threads.show(comment)
        if self._diff.thread_at(self._diff.cursor) == number:
            self.focus_pane(DIFF)

    def mouse(self, row: int, column: int, mouse_event: str) -> None:
        """Handles the mouse: a click focuses a pane and selects the row
        under it. A double click opens, the wheel scrolls the pane under
        the pointer.

        Args:
            row: the screen row.
            column: the screen column.
            mouse_event: the event, see the module mouse.
        """
        for pane, (top, left, height, width) in self._layout.items():
            if top <= row < top + height and left <= column < left + width:
                self.focus_pane(pane)
                wheel = wheel_action(mouse_event)
                if wheel:
                    if DIFF == pane:
                        # The wheel scrolls, so a tall thread can be read.
                        self._diff.scroll_by(3 if 'down' == wheel else -3, height)
                    else:
                        self.action(wheel)
                    return
                offset = row - top
                if STACK == pane:
                    self._stack_pane.click(offset, height)
                elif FILES == pane:
                    self._files_pane.click(offset, height)
                elif COMMENTS == pane:
                    if not self._comments_pane.click(offset):
                        return
                    selected = self._comments_pane.selected()
                    if selected:
                        self._threads.show(selected)
                elif DIFF == pane:
                    self._diff.cursor = self._diff.stop_of(min(self._diff.scroll + offset,
                                                               max(len(self._diff.rows) - 1, 0)))
                    # A click picks the half of the diff, too.
                    self._diff.side = 'old' if column < left + 2 + (width - 2) // 2 else 'new'
                if DOUBLE_CLICK == mouse_event:
                    self._open()
                return
