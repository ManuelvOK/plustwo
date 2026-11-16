# SPDX-License-Identifier: Zlib
"""The format of `plustwo answer`, the author's answers in one batch."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
from plustwo.git.errors import ChangeError
from plustwo.model.acknowledgement import is_acknowledgement
from plustwo.model.comment import DONE, RESOLVED, UNRESOLVED, Comment
from plustwo.model.message import Message
from plustwo.model.roles import AUTHOR, REVIEWER
from plustwo.review.answers import parse_answers


class AnswerFormatTest(unittest.TestCase):

    def test_lines(self):
        answers = parse_answers('I3f2a #1 done\n'
                                     'shop I3f2a #2: Kept the loop.\n'
                                     '\n'
                                     'I3f2a #3 resolve: Yes.\n'
                                     'I3f2a #4 unresolve: Not yet.\n'
                                     'I3f2a #5 done: Done, renamed too.\n')
        self.assertEqual(answers, [('I3f2a', 1, RESOLVED, DONE),
                                   ('I3f2a', 2, None, 'Kept the loop.'),
                                   ('I3f2a', 3, RESOLVED, 'Yes.'),
                                   ('I3f2a', 4, UNRESOLVED, 'Not yet.'),
                                   ('I3f2a', 5, RESOLVED, 'Done, renamed too.')])

    def test_inbox_heads(self):
        # The head of an inbox line, patch set note included, then the answer
        answers = parse_answers('claudel4 I996ea4f #5 (PS2, now :50) done\n'
                                     'claudel4 I996ea4f #7 (PS2, line removed): Gone.\n'
                                     'claudel4 I996ea4f #8 (PS3) resolve: Yes.\n')
        self.assertEqual(answers, [('I996ea4f', 5, RESOLVED, DONE),
                                   ('I996ea4f', 7, None, 'Gone.'),
                                   ('I996ea4f', 8, RESOLVED, 'Yes.')])

    def test_errors(self):
        for text in ('no answer here', 'I3f2a #2', 'I3f2a #2:'):
            with self.subTest(text), self.assertRaises(ChangeError):
                parse_answers(text)

    def test_several_lines(self):
        # A line that doesn't start an answer goes on with the one before.
        answers = parse_answers('I3f2a #1: Changed three things:\n'
                                     '- the loop\n'
                                     '- the name\n'
                                     '\n'
                                     '1. first step\n'
                                     '2. second step\n'
                                     'I3f2a #2 done\n'
                                     '\n')
        self.assertEqual(answers, [
            ('I3f2a', 1, None, 'Changed three things:\n- the loop\n- the name\n\n'
             '1. first step\n2. second step'),
            ('I3f2a', 2, RESOLVED, DONE)])
        # An answer can start on the line after its head
        self.assertEqual(parse_answers('I3f2a #3 resolve:\n  Yes, see below.'),
                         [('I3f2a', 3, RESOLVED, 'Yes, see below.')])

    def test_all_errors_at_once(self):
        with self.assertRaises(ChangeError) as caught:
            parse_answers('stray text\nmore stray\nI3f2a #1:\nI3f2a #2: fine\nI3f2a #3')
        message = str(caught.exception)
        for part in ('line 1 is no answer', 'line 2 is no answer', 'line 3 has no text',
                     'line 5 has no text'):
            self.assertIn(part, message)


class AcknowledgementTest(unittest.TestCase):

    def test_words(self):
        for text in ('Ack.', 'ack', 'OK, thanks!', 'Ack, thanks 👍', 'Got it. LGTM',
                     'Thanks, makes sense!', 'Okay thank you'):
            self.assertTrue(is_acknowledgement(text), text)
        for text in ('', '👍', 'why?', 'Ack, but rename it', 'OK, but not here'):
            self.assertFalse(is_acknowledgement(text), text)

    def test_old_note(self):
        # Written before typed acknowledgements resolved: no flag on it.
        comment = Comment(1, 'a.txt:3', 1, [
            Message(REVIEWER, 'Why?'), Message(AUTHOR, 'Kept it.'),
            Message(REVIEWER, 'Ack.')])
        self.assertEqual(RESOLVED, comment.status)
        self.assertFalse(comment.wants_answer())
        # The author's "ok" doesn't resolve.
        comment.messages[-1] = Message(AUTHOR, 'OK')
        self.assertEqual(UNRESOLVED, comment.status)


if __name__ == '__main__':
    unittest.main()
