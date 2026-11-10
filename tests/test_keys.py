# SPDX-License-Identifier: GPL-3.0-or-later
"""Checks of the interface's keys: the presets, counts, sequences and the
reviewer's own keys.
"""

import curses
import importlib.machinery
import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import plustwo_tui as tui  # pylint: disable=wrong-import-position

_loader = importlib.machinery.SourceFileLoader(
    'plustwo', str(Path(__file__).resolve().parent.parent / 'plustwo'))
_spec = importlib.util.spec_from_loader('plustwo', _loader)
core = importlib.util.module_from_spec(_spec)
_loader.exec_module(core)


def feed(keymap, *keys):
    """Feeds keys (characters or codes) and returns the last result."""
    result = None
    for key in keys:
        result = keymap.feed(ord(key) if isinstance(key, str) else key)
    return result


def ctrl(letter):
    return ord(letter) & 0x1f


class PresetTest(unittest.TestCase):
    """Every preset is complete and unambiguous."""

    def test_every_action_has_a_key(self):
        for preset in tui.PRESETS:
            keymap = tui.Keymap(preset)
            with self.subTest(preset):
                self.assertEqual([], keymap.errors)
                self.assertEqual([], [name for name in tui.ACTION_NAMES
                                      if not keymap.labels(name)])

    def test_no_key_is_both_a_binding_and_a_prefix(self):
        for preset in tui.PRESETS:
            keymap = tui.Keymap(preset)
            with self.subTest(preset):
                self.assertEqual(set(), set(keymap._sequences) & keymap._prefixes)

    def test_no_key_lost_between_actions(self):
        # Two actions of one preset must not share a key: the second would
        # take it from the first without anyone noticing.
        for preset, specs in tui.PRESETS.items():
            seen = {}
            for name, spec in specs.items():
                for sequence in tui.key_sequences(spec):
                    with self.subTest(preset=preset, action=name, sequence=sequence):
                        self.assertNotIn(sequence, seen, f'also {seen.get(sequence)}')
                    seen[sequence] = name


class VimTest(unittest.TestCase):

    def setUp(self):
        self.keymap = tui.Keymap('vim')

    def test_keys(self):
        self.assertEqual(('down', None), feed(self.keymap, 'j'))
        self.assertEqual(('back', None), feed(self.keymap, 27))
        self.assertEqual(('open', None), feed(self.keymap, 10))
        self.assertEqual(('page-up', None), feed(self.keymap, ctrl('u')))

    def test_count_and_gg(self):
        self.assertEqual(('down', 15), feed(self.keymap, '1', '5', 'j'))
        self.assertEqual(tui.PENDING, feed(self.keymap, 'g'))
        self.assertEqual('g', self.keymap.typed())
        self.assertEqual(('top', None), feed(self.keymap, 'g'))
        self.assertEqual(('bottom', 120), feed(self.keymap, '1', '2', '0', 'G'))

    def test_broken_sequence_and_esc(self):
        self.assertEqual((None, None), feed(self.keymap, 'g', 'x'))
        self.assertEqual(tui.PENDING, feed(self.keymap, '3', 27))
        self.assertEqual(('down', None), feed(self.keymap, 'j'))

    def test_brackets(self):
        self.assertEqual(('next-chunk', None), feed(self.keymap, ']', 'c'))
        self.assertEqual(('prev-file', None), feed(self.keymap, '[', 'f'))


class EmacsTest(unittest.TestCase):

    def setUp(self):
        self.keymap = tui.Keymap('emacs')

    def test_moving(self):
        self.assertEqual(('down', None), feed(self.keymap, ctrl('n')))
        self.assertEqual(('page-down', None), feed(self.keymap, ctrl('v')))
        self.assertEqual(('page-up', None), feed(self.keymap, tui.META | ord('v')))
        self.assertEqual(('top', None), feed(self.keymap, tui.META | ord('<')))
        self.assertEqual(('back', None), feed(self.keymap, ctrl('g')))
        self.assertEqual(('quit-now', None), feed(self.keymap, ctrl('x'), ctrl('c')))

    def test_count_needs_c_u(self):
        self.assertEqual(('down', 15), feed(self.keymap, ctrl('u'), '1', '5', ctrl('n')))
        self.assertEqual((None, None), feed(self.keymap, '5'))

    def test_diff_mode_keys(self):
        self.assertEqual(('next-chunk', None), feed(self.keymap, 'n'))
        self.assertEqual(('prev-file', None), feed(self.keymap, 'P'))
        self.assertEqual(('patch-sets', None), feed(self.keymap, 'b'))


class GerritTest(unittest.TestCase):

    def test_keys(self):
        keymap = tui.Keymap('gerrit')
        for key, action in (('n', 'next-chunk'), ('p', 'prev-chunk'), ('N', 'next-thread'),
                            ('P', 'prev-thread'), (']', 'next-file'), ('[', 'prev-file'),
                            ('a', 'send'), ('u', 'back'), ('o', 'open'), ('c', 'comment'),
                            ('j', 'down')):
            with self.subTest(key):
                self.assertEqual((action, None), feed(keymap, key))


class OverrideTest(unittest.TestCase):

    def test_own_key_wins(self):
        keymap = tui.Keymap('vim', {'reply': 'R'})
        self.assertEqual([], keymap.errors)
        self.assertEqual(('reply', None), feed(keymap, 'R'))
        # reload lost R, so it isn't shown with it any more
        self.assertEqual([], keymap.labels('reload'))
        self.assertEqual(('reply', None), feed(keymap, 'R'))
        self.assertEqual((None, None), feed(keymap, 'r'))

    def test_several_and_named_keys(self):
        keymap = tui.Keymap('vim', {'send': 'C-s,F', 'down': 'Down,Space'})
        self.assertEqual(('send', None), feed(keymap, ctrl('s')))
        self.assertEqual(('send', None), feed(keymap, 'F'))
        self.assertEqual(('down', None), feed(keymap, 32))
        self.assertEqual(('down', None), feed(keymap, curses.KEY_DOWN))

    def test_errors(self):
        keymap = tui.Keymap('nano', {'fly': 'f', 'reply': 'Hyper-x'})
        self.assertEqual('vim', keymap.preset)
        self.assertEqual(3, len(keymap.errors))

    def test_footer_and_help_show_the_keys(self):
        keymap = tui.Keymap('emacs')
        self.assertEqual('C-n/C-p move', keymap.footer(((('down', 'up'), 'move'),)))
        help_text = '\n'.join(keymap.help_lines(80))
        self.assertIn('M-v', help_text)
        self.assertIn('C-x C-c', help_text)


class TextBoxTest(unittest.TestCase):

    def test_emacs_editing(self):
        box = tui.TextBox('hello world', emacs=True)
        box.key('\x01')
        self.assertEqual(0, box.position)
        box.key('\x06')
        box.key('\x0b')
        self.assertEqual('h', box.text)
        box.key('\x02')
        box.key('\x04')
        self.assertEqual('', box.text)

    def test_vim_mode_leaves_control_keys(self):
        box = tui.TextBox('abc')
        box.key('\x06')
        self.assertEqual('abc', box.text)
        self.assertEqual(3, box.position)



class CursorTest(unittest.TestCase):
    """A thread in the diff is one cursor stop."""

    def pane(self):
        diff = tui.DiffPane(None)
        # Two diff rows, a thread of 30 rows, two diff rows
        diff.rows = ([('diff', 0), ('diff', 1)] + [('thread', 7, row) for row in range(30)]
                     + [('diff', 2), ('diff', 3)])
        return diff

    def test_steps_over_a_thread(self):
        diff = self.pane()
        diff.move_cursor(2, 40)
        self.assertEqual((2, 7), (diff.cursor, diff.thread_at(diff.cursor)))
        self.assertTrue(diff.is_on_cursor(31) and not diff.is_on_cursor(32))
        diff.move_cursor(1, 40)
        self.assertEqual(32, diff.cursor)
        diff.move_cursor(-1, 40)
        self.assertEqual(2, diff.cursor)
        diff.move_cursor(3, 40)
        self.assertEqual(33, diff.cursor)

    def test_reads_a_tall_thread_row_by_row(self):
        # Taller than the pane: j scrolls through it before going on.
        diff = self.pane()
        diff.move_cursor(2, 10)
        self.assertEqual((2, 2), (diff.cursor, diff.scroll))
        diff.move_cursor(5, 10)
        self.assertEqual((2, 7), (diff.cursor, diff.scroll))
        diff.move_cursor(15, 10)
        self.assertEqual((2, 22), (diff.cursor, diff.scroll))
        diff.move_cursor(1, 10)
        self.assertEqual(32, diff.cursor)
        # Back up: its end shows first, k scrolls up through it.
        diff.move_cursor(-1, 10)
        self.assertEqual((2, 22), (diff.cursor, diff.scroll))
        diff.move_cursor(-3, 10)
        self.assertEqual((2, 19), (diff.cursor, diff.scroll))
        # C-e and C-y scroll without moving the cursor
        diff.scroll_rows(-1, 10)
        self.assertEqual((2, 18), (diff.cursor, diff.scroll))

    def test_scrolling_reads_a_tall_thread(self):
        diff = self.pane()
        diff.move_cursor(2, 10)
        seen = set()
        # The cursor stays on the thread until all of it was shown.
        while 2 == diff.cursor:
            seen.update(range(diff.scroll, diff.scroll + 10))
            diff.scroll_by(5, 10)
        self.assertTrue(set(range(2, 32)) <= seen)
        self.assertEqual(32, diff.cursor)


class SearchTest(unittest.TestCase):
    """Search in the diff: matches like vim's smartcase, n and N go from
    match to match over both sides and go on from the other end.
    """

    def test_matches(self):
        self.assertEqual([(0, 3), (8, 11)], core.search_matches('Foo bar foo', 'foo'))
        self.assertEqual([(0, 3)], core.search_matches('Foo bar foo', 'Foo'))
        self.assertEqual([(0, 2), (2, 4)], core.search_matches('aaaa', 'aa'))
        self.assertEqual([], core.search_matches('abc', ''))

    def test_goes_from_match_to_match(self):
        diff = tui.DiffPane(None)
        # Line 0 has the text on the new side, line 2 on both, a thread between
        diff._texts = [('a', 'x one'), None, ('two x', 'x three'), ('b', 'b')]
        diff._continues = [False, True, False, False]
        diff.rows = [('diff', 0), ('diff', 1), ('thread', 4, 0), ('diff', 2), ('diff', 3)]
        found = []
        for _ in range(4):
            wrapped = diff.go_to_match(core, 'x', True, 10)
            found.append((diff.cursor, diff.side, wrapped))
        self.assertEqual([(3, 'old', False), (3, 'new', False), (0, 'new', True),
                          (3, 'old', False)], found)
        self.assertFalse(diff.go_to_match(core, 'x', False, 10))
        self.assertEqual((0, 'new'), (diff.cursor, diff.side))
        self.assertIsNone(diff.go_to_match(core, 'nowhere', True, 10))


class BackgroundReplyTest(unittest.TestCase):
    """The terminal's answer to OSC 11, its background."""

    def test_replies(self):
        parse = tui.parse_background_reply
        self.assertEqual((0x1c, 0x1c, 0x1c), parse('\x1b]11;rgb:1c1c/1c1c/1c1c\x1b\\'))
        self.assertEqual((255, 0, 128), parse('\x1b]11;rgb:ff/00/80\x07'))
        self.assertEqual((255, 136, 0), parse('\x1b]11;rgb:f/8/0\x07\x1b[?62;22c'))
        self.assertEqual((0x1c, 0x1c, 0x1c), parse('junk\x1b]11;rgb:1c1c/1c1c/1c1c\x07\x1b[?1;2c'))
        for garbage in ('', '\x1b[?1;2c', '\x1b]11;rgb:zz/00/00\x07', '\x1b]11;rgb:ff/00/00'):
            self.assertIsNone(parse(garbage), garbage)
        self.assertEqual((0x1c, 0x1c, 0x1c), tui.parse_colour('#1c1c1c'))
        self.assertIsNone(tui.parse_colour('dark'))

    def test_tint_follows_the_background(self):
        tui.TRUE_COLOUR = True
        try:
            tui.TERMINAL_BACKGROUND = (0, 0, 0)
            self.assertEqual((18, 18, 18), tui.cursor_tint(-1))
            tui.TERMINAL_BACKGROUND = (250, 250, 250)
            self.assertEqual((232, 232, 232), tui.cursor_tint(-1))
        finally:
            tui.TRUE_COLOUR = False
            tui.TERMINAL_BACKGROUND = (0, 0, 0)

if __name__ == '__main__':
    unittest.main()
