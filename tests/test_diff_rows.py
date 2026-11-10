# SPDX-License-Identifier: GPL-3.0-or-later
"""Checks of the side by side diff rows that plustwo renders itself.

The diff itself comes from git. These tests make sure the layout loses or
misplaces nothing:

- every line of both versions shows up once, in order, with its number
- the changed rows are exactly git's edits
- the line numbers per row equal those delta shows for the same diff

They run over fixtures with edge cases and over the history of real
repositories: this one, and those in TEST_REPOS (separated by colons).
TEST_COMMITS limits how many commits per repository are checked.
"""

import importlib.machinery
import importlib.util
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_loader = importlib.machinery.SourceFileLoader('plustwo', str(ROOT / 'plustwo'))
_spec = importlib.util.spec_from_loader('plustwo', _loader)
core = importlib.util.module_from_spec(_spec)
_loader.exec_module(core)

COMMITS_PER_REPOSITORY = int(os.environ.get('TEST_COMMITS', '200'))
ANSI = re.compile(r'\x1b\[[0-9;]*m')
DELTA_NUMBERS = re.compile(r'^│\s*(\d*)\s*│.*?│\s*(\d*)\s*│')


def run(repository, *arguments, input_bytes=None):
    """Runs git in a repository, returns its output as bytes."""
    return subprocess.run(['git', '-C', str(repository), *arguments], input=input_bytes,
                          capture_output=True, check=True).stdout


def changed_files(repository, commit):
    """Paths a commit changes, renames counted as delete and add."""
    output = run(repository, 'diff-tree', '--no-commit-id', '--name-only', '-r', '--root',
                 '--no-renames', '-z', commit).decode('utf-8', 'replace')
    return [path for path in output.split('\0') if path]


def parent_of(repository, commit):
    """The first parent, or the empty tree for a root commit."""
    result = subprocess.run(['git', '-C', str(repository), 'rev-parse', '--verify', '--quiet',
                             f'{commit}^'], capture_output=True, text=True)
    if result.stdout.strip():
        return result.stdout.strip()
    return run(repository, 'hash-object', '-t', 'tree', '--stdin', input_bytes=b'').decode().strip()


def file_pairs(repository, limit=COMMITS_PER_REPOSITORY):
    """(old version, new version, path) for the files of recent commits."""
    commits = run(repository, 'rev-list', '--no-merges', f'--max-count={limit}',
                  'HEAD').decode().split()
    for commit in commits:
        parent = parent_of(repository, commit)
        for path in changed_files(repository, commit):
            yield parent, commit, path


def test_repositories():
    """This repository and the ones TEST_REPOS names."""
    names = [str(ROOT)] + [name for name in os.environ.get('TEST_REPOS', '').split(':') if name]
    return [Path(name) for name in names if (Path(name) / '.git').exists()]


class Checks:
    """The checks of one file's rows, shared by the test cases."""

    def check_whole_file(self, old_lines, new_lines, edits, rows, label):
        """Both versions come back complete and in order, and the changed
        rows are git's edits.
        """
        old_back = [row.old for row in rows if row.old.number]
        new_back = [row.new for row in rows if row.new.number]
        self.assertEqual([half.number for half in old_back], list(range(1, len(old_lines) + 1)),
                         label)
        self.assertEqual([half.number for half in new_back], list(range(1, len(new_lines) + 1)),
                         label)
        self.assertEqual([half.text for half in old_back], old_lines, label)
        self.assertEqual([half.text for half in new_back], new_lines, label)
        self.assertFalse(any(row.skipped for row in rows), label)
        removed = {half.number for half in old_back if core.REMOVED == half.kind}
        added = {half.number for half in new_back if core.ADDED == half.kind}
        self.assertEqual(removed, {line for edit in edits for line in range(edit[0], edit[1])},
                         label)
        self.assertEqual(added, {line for edit in edits for line in range(edit[2], edit[3])},
                         label)
        for row in rows:
            for half in (row.old, row.new):
                for begin, end in half.edits:
                    self.assertTrue(0 <= begin < end <= len(half.text), label)

    def check_context(self, old_lines, new_lines, edits, context, label):
        """With context, gaps account for exactly the lines left out, and
        every changed line is still there.
        """
        rows = core.side_by_side_rows(old_lines, new_lines, edits, context)
        old_seen = new_seen = 0
        for row in rows:
            if row.skipped:
                self.assertEqual(row.old.number, old_seen + 1, label)
                self.assertEqual(row.new.number, new_seen + 1, label)
                old_seen += row.skipped
                new_seen += row.skipped
                continue
            if row.old.number:
                self.assertEqual(row.old.number, old_seen + 1, label)
                old_seen += 1
            if row.new.number:
                self.assertEqual(row.new.number, new_seen + 1, label)
                new_seen += 1
        self.assertEqual((old_seen, new_seen), (len(old_lines), len(new_lines)), label)
        whole = core.side_by_side_rows(old_lines, new_lines, edits)
        changed = [row for row in whole if CONTEXT_KINDS != (row.old.kind, row.new.kind)]
        shown = [row for row in rows if CONTEXT_KINDS != (row.old.kind, row.new.kind)
                 and not row.skipped]
        self.assertEqual([(row.old.number, row.new.number) for row in changed],
                         [(row.old.number, row.new.number) for row in shown], label)

    def check_version_pair(self, repository, old, new, path):
        """All checks of one file between two versions."""
        old_lines = core.file_lines(old, path, repository)
        new_lines = core.file_lines(new, path, repository)
        if old_lines is None or new_lines is None:
            return False
        label = f'{repository.name} {new[:8]} {path}'
        edits = core.edits_of(old, new, path, repository)
        rows = core.side_by_side_rows(old_lines, new_lines, edits)
        self.check_whole_file(old_lines, new_lines, edits, rows, label)
        for context in (0, 3, 10):
            self.check_context(old_lines, new_lines, edits, context, label)
        return True


CONTEXT_KINDS = (core.CONTEXT, core.CONTEXT)


class FixtureTest(Checks, unittest.TestCase):
    """Edge cases, each a commit in a scratch repository."""

    CASES = {
        'no-final-newline.txt': (b'a\nb\nc', b'a\nB\nc'),
        'gains-final-newline.txt': (b'a\nb', b'a\nb\n'),
        'empty-to-text.txt': (b'', b'one\ntwo\n'),
        'text-to-empty.txt': (b'one\ntwo\n', b''),
        'added.txt': (None, b'new\nfile\n'),
        'deleted.txt': (b'old\nfile\n', None),
        'crlf.txt': (b'a\r\nb\r\nc\r\n', b'a\r\nX\r\nc\r\n'),
        'tabs.txt': (b'\tif x:\n\t\treturn 1\n', b'\tif x:\n\t\treturn 2\n'),
        'unicode.txt': ('äöü 漢字\nemoji 🎉\n'.encode(), 'äöü 漢字!\nemoji 🎉\n'.encode()),
        'not-utf8.txt': (b'caf\xe9\n', b'caf\xe9!\n'),
        'long-line.txt': (b'x' * 5000 + b'\n', b'x' * 4000 + b'y' * 1000 + b'\n'),
        'many-hunks.txt': (b''.join(b'%d\n' % n for n in range(200)),
                           b''.join(b'%d\n' % (n if n % 37 else -n) for n in range(200))),
        'insert-only.txt': (b'a\nb\n', b'a\nnew 1\nnew 2\nb\n'),
        'unequal-pairs.txt': (b'1\n2\n3\n4\n', b'1\nx\n4\n5\n6\n'),
        'binary.bin': (b'\0\1\2', b'\0\1\3'),
    }

    @classmethod
    def setUpClass(cls):
        cls.directory = Path(tempfile.mkdtemp(prefix='git-change-test-'))
        run(cls.directory, 'init', '--quiet')
        for commit, side in enumerate((0, 1)):
            for name, versions in cls.CASES.items():
                path = cls.directory / name
                if versions[side] is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(versions[side])
            run(cls.directory, 'add', '-A')
            run(cls.directory, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '--quiet',
                '--allow-empty', '-m', f'version {commit}')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.directory)

    def test_cases(self):
        for name in self.CASES:
            with self.subTest(name):
                checked = self.check_version_pair(self.directory, 'HEAD^', 'HEAD', name)
                self.assertEqual(checked, not name.endswith('.bin'))

    def test_pairs_lines_in_order(self):
        rows = core.side_by_side_rows(['1', '2', '3', '4'], ['1', 'x', '4', '5', '6'],
                                      [(2, 4, 2, 3), (5, 5, 4, 6)])
        self.assertEqual([(row.old.number, row.new.number) for row in rows],
                         [(1, 1), (2, None), (3, None), (None, 2), (4, 3), (None, 4),
                          (None, 5)])

    def test_pairs_by_similarity(self):
        # A rewritten block: the similar lines pair, the others stand alone
        pairs = core.paired_lines(['first = 1', 'second = 2', 'third = 3'],
                                  ['completely new', 'second = 22', 'third = 33'])
        self.assertEqual(pairs, [(0, None), (None, 0), (1, 1), (2, 2)])
        self.assertEqual(core.paired_lines(['a = 1'], ['b = 2', 'a = 10']),
                         [(None, 0), (0, 1)])
        self.assertEqual(core.paired_lines(['x'], []), [(0, None)])

    def test_moved_lines(self):
        block = ['def helper(value):', '    return value * 2']
        old_lines = ['import os'] + block + ['', 'def main():', '    pass']
        new_lines = ['import os', '', 'def main():', '    pass'] + block
        edits = [(2, 4, 2, 2), (7, 7, 5, 7)]
        self.assertEqual(core.moved_lines(old_lines, new_lines, edits), ({2, 3}, {5, 6}))
        rows = core.side_by_side_rows(old_lines, new_lines, edits)
        self.assertEqual([half.number for row in rows for half in (row.old, row.new)
                          if half.moved], [2, 3, 5, 6])
        # Too few letters and digits to count as moved, like in git
        self.assertEqual(core.moved_lines(['}', 'a', '}'], ['a', '}', '}'],
                                          [(1, 2, 1, 1), (4, 4, 3, 4)]), (set(), set()))

    def test_intraline(self):
        self.assertEqual(core.intraline_edits('return 1', 'return 2'), ([(7, 8)], [(7, 8)]))
        self.assertEqual(core.intraline_edits('abc', 'xyz'), ([], []))
        # Whole words, not scattered letters
        self.assertEqual(core.intraline_edits('total = count + 1', 'total = counter + 1'),
                         ([], [(8, 15)]))
        self.assertEqual(core.intraline_edits('value = compute(alpha)', 'value = compete(alpha)'),
                         ([(8, 15)], [(8, 15)]))
        # Edits a few characters apart become one, like in Gerrit
        self.assertEqual(core.intraline_edits('f(a, b, c)', 'f(x, b, y)'),
                         ([(2, 9)], [(2, 9)]))


class HistoryTest(Checks, unittest.TestCase):
    """The checks over the history of real repositories."""

    def test_history(self):
        count = 0
        for repository in test_repositories():
            for old, new, path in file_pairs(repository):
                with self.subTest(f'{repository.name} {new[:8]} {path}'):
                    count += self.check_version_pair(repository, old, new, path)
        self.assertGreater(count, 0)


@unittest.skipUnless(shutil.which('delta'), 'delta is not installed')
class DeltaTest(unittest.TestCase):
    """Our rows carry the same line numbers as delta's rows of the same
    diff, delta being the established tool to compare with.
    """

    def delta_numbers(self, repository, old, new, path):
        """(old, new) line numbers of delta's rows, wrapped rows left out."""
        diff = run(repository, 'diff', '--no-color', '--no-ext-diff', '--no-textconv',
                   '--no-renames', '--histogram', '--unified=1000000', old, new, '--', path)
        rendered = subprocess.run(['delta', '--no-gitconfig', '--side-by-side', '--line-numbers',
                                   '--width=400', '--wrap-max-lines=0', '--paging=never'],
                                  input=diff, capture_output=True, check=True).stdout
        numbers = []
        for line in rendered.decode('utf-8', 'replace').split('\n'):
            match = DELTA_NUMBERS.match(ANSI.sub('', line))
            if match and any(match.groups()):
                numbers.append(tuple(int(value) if value else None for value in match.groups()))
        return numbers

    def test_same_numbers_as_delta(self):
        # delta and we pair removed and added lines by similarity, maybe
        # not the same ones. So the rows themselves may differ, but each
        # side must show the same lines in the same order.
        count = 0
        for repository in test_repositories():
            for old, new, path in file_pairs(repository, limit=min(COMMITS_PER_REPOSITORY, 60)):
                old_lines = core.file_lines(old, path, repository)
                new_lines = core.file_lines(new, path, repository)
                if old_lines is None or new_lines is None:
                    continue
                # delta shows a submodule as a header without line numbers
                if any(lines[:1] and lines[0].startswith('Subproject commit ')
                       for lines in (old_lines, new_lines)):
                    continue
                rows = core.side_by_side_rows(old_lines, new_lines,
                                              core.edits_of(old, new, path, repository))
                theirs = self.delta_numbers(repository, old, new, path)
                with self.subTest(f'{repository.name} {new[:8]} {path}'):
                    for side in (0, 1):
                        ours = [(row.old, row.new)[side].number for row in rows]
                        self.assertEqual([number for number in ours if number],
                                         [numbers[side] for numbers in theirs if numbers[side]])
                    count += 1
        self.assertGreater(count, 0)

if __name__ == '__main__':
    unittest.main()
