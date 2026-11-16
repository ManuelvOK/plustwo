# SPDX-License-Identifier: Zlib
"""Actions and the keys of the presets vim, emacs and gerrit."""

import curses


# Alt plus a key arrives as Esc and the key. It becomes the key plus this.
META = 1 << 24


# Presets whose counts start with a key, like Emacs' C-u 15 C-n. The
# others take digits right away, like vim's 15j.
COUNT_PREFIXES = {'emacs': 'C-u'}


NAMED_KEYS = {
    'Enter': (10, 13, curses.KEY_ENTER), 'Esc': (27,), 'Tab': (9,), 'S-Tab': (curses.KEY_BTAB,),
    'Space': (32,), 'Comma': (ord(','),), 'Up': (curses.KEY_UP,), 'Down': (curses.KEY_DOWN,),
    'Left': (curses.KEY_LEFT,), 'Right': (curses.KEY_RIGHT,), 'PageUp': (curses.KEY_PPAGE,),
    'PageDown': (curses.KEY_NPAGE,), 'Home': (curses.KEY_HOME,), 'End': (curses.KEY_END,),
    'Backspace': (curses.KEY_BACKSPACE, 127, 8),
}


def key_codes(token):
    """The key codes a key like j, C-n, M-v or Enter stands for."""
    if token in NAMED_KEYS:
        return NAMED_KEYS[token]
    if token.startswith('M-') and len(token) > 2:
        return tuple(META | code for code in key_codes(token[2:]))
    if token.startswith('C-') and 3 == len(token):
        return ((ord(token[2].lower()) & 0x1f) if token[2].isalpha() else ord(token[2]) & 0x1f,)
    if 1 == len(token):
        return (ord(token),)
    raise ValueError(f'unknown key "{token}"')


def key_sequences(spec):
    """The key code sequences of a spec like "g g,Home"."""
    sequences = []
    for alternative in filter(None, (part.strip() for part in spec.split(','))):
        options = [()]
        for token in alternative.split():
            options = [sequence + (code,) for sequence in options for code in key_codes(token)]
        sequences += options
    return sequences


def key_label(alternative):
    """A key alternative for display: gg for "g g", C-x C-c stays."""
    tokens = alternative.split()
    return ''.join(tokens) if all(1 == len(token) for token in tokens) else ' '.join(tokens)
