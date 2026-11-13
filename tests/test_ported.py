# SPDX-License-Identifier: Zlib
"""Checks of threads moved to newer patch sets, like Gerrit's ported
comments: unit cases in a scratch repository, the inbox note, and over
the history of real repositories (this one and TEST_REPOS) that a line
reported as the same or moved has the same text where it was moved to.
"""

import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import test_diff_rows
import test_stacks

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import plustwo_tui as tui  # pylint: disable=wrong-import-position
_loader = importlib.machinery.SourceFileLoader('plustwo', str(ROOT / 'plustwo'))
_spec = importlib.util.spec_from_loader('plustwo', _loader)
core = importlib.util.module_from_spec(_spec)
_loader.exec_module(core)

LINES = ''.join(f'line {number}\n' for number in range(1, 21))


class PortTest(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix='plustwo-port-')
        self.git('init', '--quiet', '--initial-branch=main')
        self.base = self.commit({'a.txt': LINES, 'b.txt': LINES}, 'Base')

    def tearDown(self):
        shutil.rmtree(self.directory)

    def git(self, *arguments):
        return subprocess.run(['git', *arguments], cwd=self.directory, check=True,
                              capture_output=True, text=True,
                              env=test_stacks.ENVIRONMENT).stdout.strip()

    def commit(self, files, subject, parent=None):
        """A commit with these file contents on parent (None removes a
        file), without touching a branch.
        """
        if parent:
            self.git('read-tree', parent)
        else:
            self.git('read-tree', '--empty')
        for name, text in files.items():
            if text is None:
                self.git('update-index', '--force-remove', name)
                continue
            (Path(self.directory) / name).write_text(text)
            self.git('add', name)
        tree = self.git('write-tree')
        return self.git('commit-tree', tree, *(['-p', parent] if parent else []), '-m', subject)

    def port(self, old, new, place, side=core.NEW_SIDE):
        """A place moved like port_thread() moves a thread."""
        if core.OLD_SIDE == side:
            old, new = f'{old}^', f'{new}^'
        path, line = core.split_place(place)
        path, number, state = core.moved_place(old, new, path, line, self.directory)
        return core.join_place(path, number), number, state

    def edited(self, text, *changes):
        lines = text.split('\n')
        for change in changes:
            change(lines)
        return '\n'.join(lines)

    def test_same_moved_edited_removed(self):
        first = self.commit({'a.txt': LINES.replace('line 15', 'line 15!')}, 'PS1', self.base)
        # Two lines more on top, line 10 edited, line 12 removed
        text = 'new 1\nnew 2\n' + LINES.replace('line 10\n', 'line ten\n').replace(
            'line 12\n', '').replace('line 15', 'line 15!')
        second = self.commit({'a.txt': text}, 'PS2', self.base)
        self.assertEqual(self.port(first, second, 'a.txt:5'), ('a.txt:7', 7, core.MOVED))
        self.assertEqual(self.port(first, second, 'a.txt:10'), ('a.txt:12', 12, core.EDITED))
        self.assertEqual(self.port(first, second, 'a.txt:12'), ('a.txt:13', 13, core.REMOVED))
        self.assertEqual(self.port(first, second, 'a.txt:15'), ('a.txt:16', 16, core.MOVED))
        self.assertEqual(self.port(first, second, 'b.txt:3'), ('b.txt:3', 3, core.SAME))
        self.assertEqual(self.port(first, second, 'a.txt'), ('a.txt', None, core.SAME))

    def test_renamed_and_gone(self):
        first = self.commit({'a.txt': LINES + 'end\n'}, 'PS1', self.base)
        second = self.commit({'a.txt': None, 'c.txt': 'top\n' + LINES + 'end\n', 'b.txt': None},
                             'PS2', self.base)
        self.assertEqual(self.port(first, second, 'a.txt:3'), ('c.txt:4', 4, core.MOVED))
        self.assertEqual(self.port(first, second, 'a.txt'), ('c.txt', None, core.MOVED))
        self.assertEqual(self.port(first, second, 'b.txt:3'), ('b.txt', None, core.FILE_GONE))
        self.assertEqual(self.port(first, second, core.PATCHSET_LEVEL),
                         (core.PATCHSET_LEVEL, None, core.SAME))

    def test_rebased_patch_set(self):
        # The base grew three lines on top: the direct diff moves the line.
        first = self.commit({'a.txt': LINES.replace('line 8', 'line eight')}, 'PS1', self.base)
        newer_base = self.commit({'a.txt': 'x\ny\nz\n' + LINES}, 'Newer base', self.base)
        second = self.commit({'a.txt': 'x\ny\nz\n' + LINES.replace('line 8', 'line eight')},
                             'PS2', newer_base)
        self.assertEqual(self.port(first, second, 'a.txt:8'), ('a.txt:11', 11, core.MOVED))

    def test_old_side(self):
        # The old side moves through the diff of the parents
        first = self.commit({'a.txt': LINES.replace('line 8\n', '')}, 'PS1', self.base)
        newer_base = self.commit({'a.txt': 'x\n' + LINES}, 'Newer base', self.base)
        second = self.commit({'a.txt': 'x\n' + LINES.replace('line 8\n', '')}, 'PS2', newer_base)
        self.assertEqual(self.port(first, second, 'a.txt:8', core.OLD_SIDE),
                         ('a.txt:9', 9, core.MOVED))
        self.assertEqual(self.port(first, second, 'b.txt:8', core.OLD_SIDE),
                         ('b.txt:8', 8, core.SAME))

    def test_commit_message(self):
        first = self.commit({'a.txt': LINES + 'x\n'}, 'Subject\n\nOne\nTwo', self.base)
        second = self.commit({'a.txt': LINES + 'y\n'}, 'Subject\n\nZero\nOne\nTwo', self.base)
        lines = core.commit_message_text(first, self.directory).split('\n')
        _place, line, state = self.port(first, second, f'{core.COMMIT_MSG}:{lines.index("Two") + 1}')
        self.assertEqual(state, core.MOVED)
        self.assertEqual(core.commit_message_text(second, self.directory).split('\n')[line - 1],
                         'Two')

    def test_panes_of_two_patch_sets(self):
        # Each pane gets the threads moved into the version it shows
        first = self.commit({'a.txt': LINES.replace('line 5\n', '')}, 'PS1', self.base)
        newer_base = self.commit({'a.txt': 'x\ny\n' + LINES}, 'Newer base', self.base)
        second = self.commit({'a.txt': 'x\ny\n' + LINES.replace('line 5\n', '')
                              .replace('line 9', 'line nine')}, 'PS2', newer_base)
        third = self.commit({'a.txt': 'x\ny\nz\n' + LINES.replace('line 5\n', '')}, 'PS3',
                            newer_base)

        def thread(number, place, patch_set, side=core.NEW_SIDE):
            return core.Comment(number=number, place=place, patch_set=patch_set, side=side)

        threads = [thread(1, 'a.txt:3', 1), thread(2, 'a.txt:9', 2),
                   thread(3, 'a.txt:9', 2, core.OLD_SIDE), thread(4, 'a.txt:12', 3)]
        change = core.Change(change_id='I' + '0' * 40, commit=third, subject='', topic='t',
                             comments=threads, patch_sets={1: first, 2: second, 3: third})

        def shown(number, *versions):
            return core.thread_place(change, threads[number - 1], *versions, self.directory)

        old, new = core.OLD_SIDE, core.NEW_SIDE
        # PS1 against PS2: PS1's thread stays on the left
        self.assertEqual(shown(1, '1', '2'), core.ShownThread('a.txt', 3, old))
        self.assertEqual(shown(2, '1', '2'), core.ShownThread('a.txt', 9, new))
        # PS2's parent line 9 is line 6 of PS1 (two lines less on top, one removed)
        self.assertEqual(shown(3, '1', '2'), core.ShownThread('a.txt', 6, old, 'PS2'))
        # PS2 against PS3: the edited line of PS2 is on the right, ported
        self.assertEqual(shown(2, '2', '3'), core.ShownThread('a.txt', 9, old))
        self.assertEqual(shown(1, '2', '3'), core.ShownThread('a.txt', 6, new, 'PS1'))
        # base against PS3: old side threads move into PS3's parent
        self.assertEqual(shown(3, 'base', '3'), core.ShownThread('a.txt', 9, old, 'PS2'))
        self.assertEqual(shown(4, 'base', '3'), core.ShownThread('a.txt', 12, new))
        # A newer thread shown on an older patch set moves back
        self.assertEqual(shown(4, 'base', '2'), core.ShownThread('a.txt', 11, new, 'PS3'))

    def test_touched_threads(self):
        first = self.commit({'a.txt': LINES.replace('line 5', 'line five')}, 'PS1', self.base)
        second = self.commit({'a.txt': LINES.replace('line 5', 'line five')
                              .replace('line 15', 'line fifteen')}, 'PS2', self.base)

        def thread(number, place, *messages):
            return core.Comment(number=number, place=place, patch_set=1,
                                messages=[core.Message(*message) for message in messages])

        reviewer, author = core.REVIEWER, core.AUTHOR
        threads = [thread(1, 'a.txt:5', (reviewer, 'x', True, None, 50)),
                   thread(2, 'a.txt:15', (reviewer, 'x', True, None, 50)),
                   # Answered after PS2's upload
                   thread(3, 'a.txt:3', (reviewer, 'x', True, None, 50),
                          (author, 'y', True, None, 250)),
                   thread(4, 'b.txt', (reviewer, 'x', True, None, 50)),
                   thread(5, core.PATCHSET_LEVEL, (reviewer, 'x', True, None, 50)),
                   # Waits for the reviewer, on an unchanged line
                   thread(6, 'a.txt:8', (reviewer, 'x', True, True, 50),
                          (author, 'y', True, None, 90)),
                   thread(7, 'a.txt', (reviewer, 'x', True, None, 50))]
        change = core.Change(change_id='I' + '0' * 40, commit=second, subject='', topic='t',
                             comments=threads, patch_sets={1: first, 2: second},
                             uploads={1: 100, 2: 200})
        self.assertEqual({2, 3, 7}, core.touched_threads(change, '1', '2', self.directory))
        self.assertEqual({2, 3, 6, 7}, core.relevant_threads(change, '1', '2',
                                                             repository=self.directory))
        # Against base: the change's code, the messages since PS1
        self.assertEqual({1, 2, 3, 7}, core.touched_threads(change, 'base', '2', self.directory))
        self.assertEqual({1, 2, 3, 4, 5, 6, 7}, core.touched_threads(change, 'base', '1',
                                                                     self.directory))
        self.assertEqual(set(), core.touched_threads(change, '2', '2', self.directory) - {3})
        groups = core.threads_by_file(change, [core.COMMIT_MSG, 'a.txt', 'b.txt'], '1', '2',
                                      self.directory)
        self.assertEqual([(core.PATCHSET_LEVEL, [5]), ('a.txt', [1, 2, 3, 6, 7]),
                          ('b.txt', [4])],
                         [(path, [comment.number for comment in comments])
                          for path, comments in groups])

    def test_new_thread_in_left_pane(self):
        first = self.commit({'a.txt': LINES + 'x\n'}, 'PS1', self.base)
        second = self.commit({'a.txt': LINES + 'y\n'}, 'PS2', self.base)
        change = core.Change(change_id='I' + '0' * 40, commit=second, subject='', topic='t',
                             comments=[], patch_sets={1: first, 2: second})
        # On PS1 itself when it is shown on the left, on PS2's parent against base
        self.assertEqual(core.comment_place(change, 'a.txt', 3, core.OLD_SIDE, '1', '2',
                                            self.directory), ('a.txt:3', core.NEW_SIDE, 1))
        self.assertEqual(core.comment_place(change, 'a.txt', 3, core.OLD_SIDE, 'base', '2',
                                            self.directory), ('a.txt:3', core.OLD_SIDE, 2))
        self.assertEqual(core.comment_place(change, 'a.txt', None, core.NEW_SIDE, '1', '2',
                                            self.directory), ('a.txt', core.NEW_SIDE, 2))

    def test_cache_only_for_hashes(self):
        # Results for hashes are kept, those for names that move are not.
        first = self.commit({'a.txt': 'one\n'}, 'PS1', self.base)
        self.assertIs(core.file_lines(first, 'a.txt', self.directory),
                      core.file_lines(first, 'a.txt', self.directory))
        self.assertTrue(core.IMMUTABLE_NAME.match(f'{first}^'))
        self.git('update-ref', 'refs/heads/moving', first)
        self.assertEqual(['one'], core.file_lines('moving', 'a.txt', self.directory))
        second = self.commit({'a.txt': 'two\n'}, 'PS2', self.base)
        self.git('update-ref', 'refs/heads/moving', second)
        self.assertEqual(['two'], core.file_lines('moving', 'a.txt', self.directory))

    def test_kind_context_only_is_trivial_rebase(self):
        # The base edited a line near the change: its diff's context moved,
        # the patch is the same. Like Gerrit, a trivial rebase.
        first = self.commit({'a.txt': LINES.replace('line 10\n', 'line ten\n')}, 'PS1', self.base)
        newer_base = self.commit({'a.txt': LINES.replace('line 8\n', 'line eight\n')},
                                 'Newer base', self.base)
        second = self.commit({'a.txt': LINES.replace('line 8\n', 'line eight\n')
                              .replace('line 10\n', 'line ten\n')}, 'PS2', newer_base)
        self.assertNotEqual(core.patch_id(first, self.directory),
                            core.patch_id(second, self.directory))
        self.assertEqual(core.TRIVIAL_REBASE, core.patch_set_kind(first, second, self.directory))

    def test_kind_real_edit_and_conflict_are_rework(self):
        first = self.commit({'a.txt': LINES.replace('line 10\n', 'line ten\n')}, 'PS1', self.base)
        edited = self.commit({'a.txt': LINES.replace('line 10\n', 'line TEN\n')}, 'PS2',
                             self.base)
        self.assertEqual(core.REWORK, core.patch_set_kind(first, edited, self.directory))
        # The base changed the same line: the rebase conflicts.
        newer_base = self.commit({'a.txt': LINES.replace('line 10\n', 'line 10!\n')
                                  .replace('line 1\n', 'line one\n')}, 'Newer base', self.base)
        rebased = self.commit({'a.txt': LINES.replace('line 10\n', 'line ten\n')
                               .replace('line 1\n', 'line one\n')}, 'PS2', newer_base)
        self.assertEqual('', core.rebased_tree(first, newer_base, self.directory))
        self.assertEqual(core.REWORK, core.patch_set_kind(first, rebased, self.directory))
        # Only a new message: no code change
        message = self.commit({'a.txt': LINES.replace('line 10\n', 'line ten\n')}, 'Other',
                              self.base)
        self.assertEqual(core.NO_CODE_CHANGE, core.patch_set_kind(first, message, self.directory))

    def test_patch_set_history(self):
        first = self.commit({'a.txt': LINES.replace('line 10\n', 'line ten\n')}, 'PS1', self.base)
        second = self.commit({'a.txt': LINES.replace('line 10\n', 'line ten\n')}, 'PS2 msg',
                             self.base)
        third = self.commit({'a.txt': LINES.replace('line 10\n', 'line TEN\n')}, 'PS3',
                            self.base)
        message = core.Message

        def thread(number, patch_set, *messages, status_place='a.txt:10'):
            return core.Comment(number=number, place=status_place, patch_set=patch_set,
                                messages=list(messages))

        comments = [
            # Started on PS1, answered while PS2 was the newest, resolved
            thread(1, 1, message('reviewer', 'Rename it', time=110),
                   message('author', 'Done', time=250, unresolved=False)),
            # Started on PS1, still open, a reply sent while PS3 was the newest
            thread(2, 1, message('reviewer', 'Why?\nSecond line', time=120),
                   message('author', 'Because', time=310)),
            # A draft thread on PS3, a draft reply that goes to the shown patch set
            thread(3, 3, message('reviewer', 'Draft', sent=False)),
            thread(4, 2, message('reviewer', 'On PS2', time=210),
                   message('reviewer', 'More', sent=False)),
        ]
        votes = [core.Vote('Code-Review', 1, 'reviewer', 1, time=150),
                 core.Vote('Code-Review', 1, 'reviewer', 2, copied_from=1, time=150),
                 core.Vote('Verified', 1, 'ci', 3, time=320)]
        change = core.Change(change_id='I' + '0' * 40, commit=third, subject='', topic='t',
                             comments=comments, votes=votes,
                             patch_sets={1: first, 2: second, 3: third},
                             uploads={1: 100, 2: 200, 3: 300}, uploaders={1: 'a', 2: 'a', 3: 'b'})
        history = core.patch_set_history(change, repository=self.directory)
        self.assertEqual([entry.kind for entry in history],
                         ['-', core.NO_CODE_CHANGE, core.REWORK])
        # Whose they are: "you" is the reviewer, drafts count as yours
        self.assertEqual([entry.comments_label() for entry in history],
                         ['you: 2 threads (1 open)', 'you: 1 thread (1 open) · author: 1 reply',
                          'you: 1 thread, 1 reply · author: 1 reply'])
        self.assertEqual([entry.comments_label(compact=True) for entry in history],
                         ['you 2 (1 open)', 'you 1 (1 open) · author 1', 'you 2 · author 1'])
        # The newest send ends with thread 4 on PS2, the author's reply after it
        # is newer. Copied votes and drafts are no sends.
        self.assertEqual(core.last_send(history), (2, 0))
        self.assertEqual([event.is_yours() for event in history[1].events], [True, False])
        nothing_sent = core.Change(change_id='I' + '0' * 40, commit=third, subject='', topic='t',
                                   comments=[comments[2]], patch_sets={1: first},
                                   uploads={1: 100})
        self.assertIsNone(core.last_send(core.patch_set_history(nothing_sent,
                                                                repository=self.directory)))
        self.assertEqual([(event.kind, event.number) for event in history[0].events],
                         [('thread', 1), ('thread', 2), ('vote', None)])
        self.assertEqual(history[0].events[1].text, 'Why?')
        self.assertEqual([(event.kind, event.number, event.draft) for event in history[2].events],
                         [('reply', 2, False), ('vote', None, False), ('thread', 3, True),
                          ('reply', 4, True)])
        self.assertEqual([entry.vote_label('Code-Review') for entry in history], ['+1', '(+1)', ''])
        self.assertEqual(history[2].vote_label('Verified'), '+1')
        # Each message's patch set, like Gerrit's "Patchset 3"
        self.assertEqual(core.message_patch_sets(change, comments[0]), [1, 2])
        self.assertEqual(core.message_patch_sets(change, comments[1]), [1, 3])
        self.assertEqual(core.message_patch_sets(change, comments[3], shown=2), [2, 2])
        self.assertEqual(core.message_patch_sets(change, comments[3]), [2, 3])
        # Draft replies follow the patch set shown, a draft thread stays on its own.
        shown = core.patch_set_history(change, shown=2, repository=self.directory)
        self.assertEqual([4], [event.number for event in shown[1].events if event.draft])
        self.assertEqual([3], [event.number for event in shown[2].events if event.draft])

    def test_commit_message_parts(self):
        commit = self.commit({'a.txt': 'x\n'}, 'Subject line\n\nFirst paragraph\ngoes on.\n\n'
                             '- a list\n\nChange-Id: I' + '1' * 40 + '\nSigned-off-by: A <a@b>',
                             self.base)
        self.assertEqual(core.commit_message_parts(commit, self.directory),
                         ('Subject line', 'First paragraph\ngoes on.\n\n- a list',
                          ['Change-Id: I' + '1' * 40, 'Signed-off-by: A <a@b>']))
        # Only a subject, or a last paragraph that is no trailer block
        self.assertEqual(core.commit_message_parts(self.base, self.directory), ('Base', '', []))
        plain = self.commit({'a.txt': 'y\n'}, 'Subject\n\nJust text: here and more', self.base)
        self.assertEqual(core.commit_message_parts(plain, self.directory)[2], [])

    def test_labels(self):
        ported = core.PortedPlace('a.txt:50', 50, core.MOVED, 4)
        self.assertEqual(ported.label('a.txt:44'), 'now :50')
        self.assertEqual(core.PortedPlace('a.txt:44', 44, core.SAME, 4).label('a.txt:44'), '')
        self.assertEqual(core.PortedPlace('c.txt:44', 44, core.MOVED, 4).label('a.txt:44'),
                         'now c.txt:44')
        self.assertEqual(core.PortedPlace('a.txt:44', None, core.REMOVED, 4).label('a.txt:44'),
                         'line removed')


class InboxTest(unittest.TestCase):

    def test_inbox_note(self):
        directory = tempfile.mkdtemp(prefix='plustwo-port-inbox-')
        try:
            repo = test_stacks.Repository(directory)
            repo.git('switch', '--quiet', '-c', 'feature')
            repo.write('b.txt', LINES)
            repo.git('add', 'b.txt')
            repo.commit('a.txt', LINES, 'Add a')
            repo.change('upload')
            for place in ('a.txt:3', 'a.txt:5', 'a.txt:7', 'b.txt:2'):
                repo.change('comment', '1', place, f'On {place}')
            repo.write('a.txt', 'top\n' + LINES.replace('line 7\n', ''))
            repo.git('rm', '--quiet', 'b.txt')
            repo.git('commit', '--quiet', '--amend', '--no-edit', '-a')
            repo.change('upload')
            repo.change('comment', '1', 'a.txt:1', 'New one')
            inbox = repo.change('inbox')
            self.assertIn('#1 (PS1, now :4) a.txt:3  | line 3', inbox)
            self.assertIn('#3 (PS1, line removed) a.txt:7  | line 7', inbox)
            self.assertIn('#4 (PS1, file gone) b.txt:2  | line 2', inbox)
            self.assertIn('#5 a.txt:1  | top', inbox)
            # Where the interfaces draw them: ported on the latest patch set,
            # as made on the first
            change = core.find_change(inbox.split()[1], directory)
            first, removed = core.comment_of(change, 1), core.comment_of(change, 3)
            new = core.NEW_SIDE
            self.assertEqual(core.thread_place(change, first, 'base', 'latest', directory),
                             core.ShownThread('a.txt', 4, new, 'PS1', ''))
            self.assertEqual(core.thread_place(change, removed, 'base', 'latest', directory),
                             core.ShownThread('a.txt', 7, new, 'PS1', 'line 7, removed: line 7'))
            self.assertEqual(core.thread_place(change, removed, 'base', '1', directory),
                             core.ShownThread('a.txt', 7, new))
            # PS1 against PS2: PS1's threads are on the left, as they were
            self.assertEqual(core.thread_place(change, first, '1', '2', directory),
                             core.ShownThread('a.txt', 3, core.OLD_SIDE))
            # Like in Gerrit, on the whole change when the file is gone
            gone = core.comment_of(change, 4)
            self.assertEqual(core.thread_place(change, gone, 'base', 'latest', directory),
                             core.ShownThread(core.PATCHSET_LEVEL, None, new, 'PS1',
                                              'file gone: b.txt:2'))
            # Answered by the heads of inbox lines
            heads = [line.split(') ')[0] + ') done' for line in inbox.split('\n')
                     if '(PS1' in line]
            subprocess.run([str(test_stacks.ROOT / 'plustwo'), 'answer'], cwd=directory,
                           input='\n'.join(heads), text=True, check=True, capture_output=True,
                           env=test_stacks.ENVIRONMENT)
            self.assertNotIn('(PS1', repo.change('inbox'))
        finally:
            shutil.rmtree(directory)


    def test_whole_change(self):
        # A message on the whole change is resolved, but asks all the same,
        # unless it says nothing: empty (only resolves) or an acknowledgement
        reviewer, author = core.REVIEWER, core.AUTHOR
        comment = core.Comment(1, core.PATCHSET_LEVEL, 1,
                               [core.Message(reviewer, 'Why?', unresolved=False)])
        self.assertTrue(comment.wants_answer())
        comment.messages.append(core.Message(author, 'Because.'))
        self.assertFalse(comment.wants_answer())
        for text in ('', '  ', 'Ack.', 'OK, thanks!'):
            comment.messages.append(core.Message(reviewer, text, unresolved=False))
            self.assertFalse(comment.wants_answer(), text)
            comment.messages.pop()
        comment.messages.append(core.Message(reviewer, '', unresolved=True))
        self.assertTrue(comment.wants_answer())
        # On a line, a resolved thread asks nothing
        on_line = core.Comment(2, 'a.txt:3', 1,
                               [core.Message(reviewer, 'Why?', unresolved=False)])
        self.assertFalse(on_line.wants_answer())


class PollTest(unittest.TestCase):

    def test_one_git_call_per_poll(self):
        # The interfaces poll stack_version(): settings, registry and the
        # repository's place are read once, not on every poll
        directory = tempfile.mkdtemp(prefix='plustwo-poll-')
        try:
            subprocess.run(['git', 'init', '--quiet', directory], check=True)
            core.stack_version(directory)
            core.review_repositories()
            calls = []
            run = core.subprocess.run

            def counted(*arguments, **keywords):
                calls.append(arguments[0])
                return run(*arguments, **keywords)

            core.subprocess.run = counted
            try:
                core.stack_version(directory)
                core.review_repositories()
            finally:
                core.subprocess.run = run
            self.assertEqual(1, len(calls), calls)
        finally:
            shutil.rmtree(directory)

    def test_patch_set_count(self):
        self.assertEqual('1 patch set', tui.patch_set_count(1))
        self.assertEqual('3 patch sets', tui.patch_set_count(3))


class CompareTest(unittest.TestCase):
    """The patch sets compared belong to one change: another change opens
    with its default, and a choice the change doesn't have never breaks.
    """

    class Interface:
        """What a ChangeView needs of the interface, without a terminal."""

        def __init__(self):
            self.core = core
            self.colour_pairs = None
            self.keymap = tui.Keymap('vim', {})
            self.messages = []
            self.follow_latest = False

        def say(self, text, *_):
            self.messages.append(text)

        def back(self):
            pass

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix='plustwo-compare-')
        self.repo = test_stacks.Repository(self.directory)

    def tearDown(self):
        shutil.rmtree(self.directory)

    def test_compare(self):
        repo = self.repo
        repo.git('switch', '--quiet', '-c', 'topic')
        repo.commit('a.txt', 'a\n', 'Add a')
        repo.change('upload')
        repo.git('commit', '--quiet', '--amend', '-m', 'Add a, better\n\n'
                 + repo.git('log', '-1', '--format=%B').split('\n\n', 1)[1])
        repo.commit('b.txt', 'b\n', 'Add b')
        repo.change('upload')
        ids = [line.split(': ')[1] for line in repo.git(
            'log', '--reverse', '--format=%(trailers:key=Change-Id)', 'main..').split('\n')
            if line]
        two, one = ids
        changes = {change.change_id: change
                   for change in core.changes_in_stack(str(repo.path))}
        self.assertTrue(core.can_compare(changes[two], '1', '2'))
        self.assertFalse(core.can_compare(changes[one], '1', '2'))
        self.assertTrue(core.can_compare(changes[one], 'base', 'latest'))
        # The latest patch set is tagged when it is one side
        label = core.compare_label
        self.assertEqual(label(changes[two], 'base', 'latest'), 'comparing base -> 2 [latest]')
        self.assertEqual(label(changes[two], '1', '2'), 'comparing 1 -> 2 [latest]')
        self.assertEqual(label(changes[two], '2', '1'), 'comparing 2 [latest] -> 1')
        self.assertEqual(label(changes[two], 'base', '1'), 'comparing base -> 1')

        # Before a send, nothing to compare since it
        with self.assertRaisesRegex(core.ChangeError, 'Nothing sent yet'):
            core.last_send_compare(changes[one])

        def fresh(change_id):
            return next(change for change in core.changes_in_stack(str(repo.path))
                        if change.change_id == change_id)

        repo.change('comment', one, 'b.txt:1', 'Why b?')
        self.assertEqual(core.last_send_patch_set(fresh(one)), 1)
        self.assertEqual(core.compare_label(fresh(one), 'base', 'latest'),
                         'comparing base -> 1 [latest, your last send]')
        with self.assertRaisesRegex(core.ChangeError, 'on the latest patch set'):
            core.last_send_compare(fresh(one))
        repo.git('commit', '--quiet', '--amend', '--no-edit', '--allow-empty')
        repo.write('b.txt', 'bb\n')
        repo.git('commit', '--quiet', '--amend', '-a', '--no-edit')
        repo.change('upload')
        self.assertEqual(core.last_send_compare(fresh(one)), ('1', core.LATEST))
        self.assertEqual(core.compare_label(fresh(one), '1', 'latest'),
                         'comparing 1 [your last send] -> 2 [latest]')
        self.assertEqual(core.compare_label(fresh(one), '1', 'latest', short=True),
                         'comparing 1 [last send] -> 2 [latest]')
        # Without follow, a newer patch set than the right side is mentioned
        self.assertEqual(core.compare_label(fresh(one), 'base', '1', follow=False),
                         'comparing base -> 1 [your last send]   PS2 is newer')

        # Follow latest: a symbolic right side moves along, else it stays
        self.assertEqual(core.follow_latest(('base', 'latest'), 1, 2, True),
                         (('base', 'latest'), True))
        self.assertEqual(core.follow_latest(('base', 'latest'), 1, 2, False),
                         (('base', '1'), False))
        self.assertEqual(core.follow_latest(('base', '1'), 1, 2, True), (('base', '1'), False))
        self.assertEqual(core.follow_latest(('base', 'latest'), 2, 2, True),
                         (('base', 'latest'), False))
        self.assertEqual(core.right_side(fresh(one), '2'), core.LATEST)
        self.assertEqual(core.right_side(fresh(one), '2', follow=False), '2')
        self.assertEqual(core.right_side(fresh(one), '1'), '1')
        # The switch is state, off by default
        self.assertFalse(core.follows_latest())
        try:
            core.set_follows_latest(True)
            self.assertTrue(core.follows_latest())
        finally:
            core.set_follows_latest(False)
        self.assertFalse(core.follows_latest())

        interface = self.Interface()
        view = tui.ChangeView(interface, two, str(repo.path))
        view.reload()
        view.compare = ('1', '2')
        # Another change opens with its default
        view._switch_to(one)  # pylint: disable=protected-access
        self.assertEqual(view.compare, tui.DEFAULT_COMPARE)
        # A choice the change doesn't have falls back, with a message
        view._compare = ('1', '3')  # pylint: disable=protected-access
        view.reload()
        self.assertEqual(view.compare, tui.DEFAULT_COMPARE)
        self.assertIn('gone', interface.messages[-1])


class ComparedFilesTest(unittest.TestCase):
    """The file box's counts follow the compared versions."""

    def test_counts_between_patch_sets(self):
        directory = tempfile.mkdtemp(prefix='plustwo-compared-')
        try:
            repo = test_stacks.Repository(directory)
            repo.git('switch', '--quiet', '-c', 'feature')
            repo.write('a.txt', 'a\n')
            repo.write('b.txt', 'b\n')
            repo.git('add', 'a.txt', 'b.txt')
            repo.git('commit', '--quiet', '-m', 'Add a and b')
            repo.change('upload')
            repo.write('b.txt', 'b\nmore\nlines\n')
            repo.git('commit', '--quiet', '--amend', '--no-edit', '-a')
            repo.change('upload')
            change = core.changes_in_stack(directory)[0]
            self.assertEqual({'b.txt': (2, 0)}, core.compared_files(change, '1', '2', directory))
            self.assertEqual({'a.txt': (1, 0), 'b.txt': (3, 0), core.COMMIT_MSG: (0, 0)},
                             core.compared_files(change, 'base', 'latest', directory))
        finally:
            shutil.rmtree(directory)


class SubmittableTest(unittest.TestCase):

    def test_submittable_in_stack(self):
        directory = tempfile.mkdtemp(prefix='plustwo-submittable-')
        try:
            repo = test_stacks.Repository(directory)
            repo.git('switch', '--quiet', '-c', 'feature')
            repo.commit('a.txt', 'a\n', 'Add a')
            repo.commit('b.txt', 'b\n', 'Add b')
            repo.change('upload')

            def submittable():
                stack = core.changes_in_stack(directory)
                return [change.subject for change in stack
                        if change.change_id in core.submittable_in(stack, directory)]

            def marks():
                stack = core.changes_in_stack(directory)
                found = core.stack_marks(stack, directory)
                return [found.get(change.change_id, '') for change in stack]

            self.assertEqual([], submittable())
            # The upper one alone isn't, the one below lacks its +2.
            repo.approve('2')
            self.assertEqual([], submittable())
            self.assertEqual(['', '+2'], marks())
            repo.approve('1')
            self.assertEqual(['Add a', 'Add b'], submittable())
            self.assertEqual(['✓', '✓'], marks())
            # Not after the base moved on, but the review stays finished
            repo.git('switch', '--quiet', 'main')
            repo.commit('c.txt', 'c\n', 'Other work')
            self.assertEqual([], submittable())
            self.assertEqual(['+2', '+2'], marks())
        finally:
            shutil.rmtree(directory)


class UserTest(unittest.TestCase):

    def test_user_name_read_once(self):
        # The dashboard asks for every change, git must run only once.
        runs = []
        real = core.git
        core.git_user_name.cache_clear()
        core.git = lambda *arguments, **keywords: runs.append(arguments) or real(
            *arguments, **keywords)
        environment = os.environ.pop('PLUSTWO_USER', None)
        try:
            names = {core.current_user() for _ in range(5)}
        finally:
            core.git = real
            if environment is not None:
                os.environ['PLUSTWO_USER'] = environment
        self.assertEqual(1, len(names))
        self.assertEqual(1, len(runs))


class HistoryTest(unittest.TestCase):
    """A line kept as the same or moved has its text where it moved to."""

    def test_history(self):
        for repository in test_diff_rows.test_repositories():
            for old, new, path in test_diff_rows.file_pairs(repository):
                old_lines = core.file_lines(old, path, repository)
                new_lines = core.file_lines(new, path, repository)
                if not old_lines or new_lines is None:
                    continue
                edits = core.edits_of(old, new, path, repository)
                for line in range(1, len(old_lines) + 1):
                    number, state = core.ported_line(line, edits)
                    if state in (core.SAME, core.MOVED):
                        self.assertEqual(new_lines[number - 1], old_lines[line - 1],
                                         f'{repository} {new} {path}:{line}')
                    elif core.EDITED == state:
                        self.assertTrue(1 <= number <= len(new_lines))


if __name__ == '__main__':
    unittest.main()
