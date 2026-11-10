# SPDX-License-Identifier: GPL-3.0-or-later
"""The format of `plustwo answer`, the author's answers in one batch."""

import importlib.machinery
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_loader = importlib.machinery.SourceFileLoader('plustwo', str(ROOT / 'plustwo'))
_spec = importlib.util.spec_from_loader('plustwo', _loader)
core = importlib.util.module_from_spec(_spec)
_loader.exec_module(core)


class AnswerFormatTest(unittest.TestCase):

    def test_lines(self):
        answers = core.parse_answers('I3f2a #1 done\n'
                                     'shop I3f2a #2: Kept the loop.\n'
                                     '\n'
                                     'I3f2a #3 resolve: Yes.\n'
                                     'I3f2a #4 unresolve: Not yet.\n'
                                     'I3f2a #5 done: Done, renamed too.\n')
        self.assertEqual(answers, [('I3f2a', 1, core.RESOLVED, core.DONE),
                                   ('I3f2a', 2, None, 'Kept the loop.'),
                                   ('I3f2a', 3, core.RESOLVED, 'Yes.'),
                                   ('I3f2a', 4, core.UNRESOLVED, 'Not yet.'),
                                   ('I3f2a', 5, core.RESOLVED, 'Done, renamed too.')])

    def test_inbox_heads(self):
        # The head of an inbox line, patch set note included, then the answer
        answers = core.parse_answers('claudel4 I996ea4f #5 (PS2, now :50) done\n'
                                     'claudel4 I996ea4f #7 (PS2, line removed): Gone.\n'
                                     'claudel4 I996ea4f #8 (PS3) resolve: Yes.\n')
        self.assertEqual(answers, [('I996ea4f', 5, core.RESOLVED, core.DONE),
                                   ('I996ea4f', 7, None, 'Gone.'),
                                   ('I996ea4f', 8, core.RESOLVED, 'Yes.')])

    def test_errors(self):
        for text in ('no answer here', 'I3f2a #2', 'I3f2a #2:'):
            with self.subTest(text), self.assertRaises(core.ChangeError):
                core.parse_answers(text)


if __name__ == '__main__':
    unittest.main()
