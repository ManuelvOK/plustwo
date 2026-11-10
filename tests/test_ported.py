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
        return core._ported(old, new, place, side, self.directory)

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
        self.assertEqual(self.port(first, second, 'a.txt:12'), ('a.txt:12', None, core.REMOVED))
        self.assertEqual(self.port(first, second, 'a.txt:15'), ('a.txt:16', 16, core.MOVED))
        self.assertEqual(self.port(first, second, 'b.txt:3'), ('b.txt:3', 3, core.SAME))
        self.assertEqual(self.port(first, second, 'a.txt'), ('a.txt', None, core.SAME))

    def test_renamed_and_gone(self):
        first = self.commit({'a.txt': LINES + 'end\n'}, 'PS1', self.base)
        second = self.commit({'a.txt': None, 'c.txt': 'top\n' + LINES + 'end\n', 'b.txt': None},
                             'PS2', self.base)
        self.assertEqual(self.port(first, second, 'a.txt:3'), ('c.txt:4', 4, core.MOVED))
        self.assertEqual(self.port(first, second, 'a.txt'), ('c.txt', None, core.MOVED))
        self.assertEqual(self.port(first, second, 'b.txt:3'), ('b.txt:3', None, core.FILE_GONE))
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
            repo.commit('a.txt', LINES, 'Add a')
            repo.change('upload')
            for place in ('a.txt:3', 'a.txt:5', 'a.txt:7'):
                repo.change('comment', '1', place, f'On {place}')
            repo.write('a.txt', 'top\n' + LINES.replace('line 7\n', ''))
            repo.git('commit', '--quiet', '--amend', '--no-edit', '-a')
            repo.change('upload')
            repo.change('comment', '1', 'a.txt:1', 'New one')
            inbox = repo.change('inbox')
            self.assertIn('#1 (PS1, now :4) a.txt:3  | line 3', inbox)
            self.assertIn('#3 (PS1, line removed) a.txt:7  | line 7', inbox)
            self.assertIn('#4 a.txt:1  | top', inbox)
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
