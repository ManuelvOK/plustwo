# SPDX-License-Identifier: GPL-3.0-or-later
"""Checks of threads moved to newer patch sets, like Gerrit's ported
comments: unit cases in a scratch repository, the inbox note, and over
the history of real repositories (this one and TEST_REPOS) that a line
reported as the same or moved has the same text where it was moved to.
"""

import importlib.machinery
import importlib.util
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import test_diff_rows
import test_stacks

ROOT = Path(__file__).resolve().parent.parent
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
        self.assertEqual(self.port(first, second, 'a.txt:12'), ('a.txt', None, core.REMOVED))
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
                             core.ShownThread('a.txt', None, new, 'PS1', 'line removed'))
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
