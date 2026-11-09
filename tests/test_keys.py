# SPDX-License-Identifier: GPL-3.0-or-later
"""Checks of the interface's keys: the presets, counts, sequences and the
reviewer's own keys.
"""

import curses
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import plustwo_tui as tui  # pylint: disable=wrong-import-position


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


if __name__ == '__main__':
    unittest.main()
