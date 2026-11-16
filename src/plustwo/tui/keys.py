# SPDX-License-Identifier: Zlib
"""Key codes of the keys the presets name, like C-n or M-v."""

import curses


# Alt plus a key arrives as Esc and the key. It becomes the key plus this.
META = 1 << 24

# Presets whose counts start with a key, like Emacs' C-u 15 C-n. The
# others take digits right away, like vim's 15j.
COUNT_PREFIXES = {'emacs': 'C-u'}

# The names of keys without a character, mapped to their key codes
_NAMED_KEYS = {
    'Enter': (10, 13, curses.KEY_ENTER), 'Esc': (27,), 'Tab': (9,), 'S-Tab': (curses.KEY_BTAB,),
    'Space': (32,), 'Comma': (ord(','),), 'Up': (curses.KEY_UP,), 'Down': (curses.KEY_DOWN,),
    'Left': (curses.KEY_LEFT,), 'Right': (curses.KEY_RIGHT,), 'PageUp': (curses.KEY_PPAGE,),
    'PageDown': (curses.KEY_NPAGE,), 'Home': (curses.KEY_HOME,), 'End': (curses.KEY_END,),
    'Backspace': (curses.KEY_BACKSPACE, 127, 8),
}


def key_codes(token: str) -> tuple[int, ...]:
    """Finds the key codes a key stands for.

    Args:
        token: the key, like j, C-n, M-v or Enter.

    Returns:
        The codes, any of which is the key.

    Raises:
        ValueError: the key is unknown.
    """
    if token in _NAMED_KEYS:
        return _NAMED_KEYS[token]
    if token.startswith('M-') and len(token) > 2:
        return tuple(META | code for code in key_codes(token[2:]))
    if token.startswith('C-') and 3 == len(token):
        return ((ord(token[2].lower()) & 0x1f) if token[2].isalpha() else ord(token[2]) & 0x1f,)
    if 1 == len(token):
        return (ord(token),)
    raise ValueError(f'unknown key "{token}"')


def key_sequences(spec: str) -> list[tuple[int, ...]]:
    """Finds the key code sequences of a preset's keys for an action.

    Args:
        spec: the keys, alternatives separated by commas, the keys of a
            sequence by spaces, like "g g,Home".

    Returns:
        Every sequence of key codes that runs the action.
    """
    sequences = []
    for alternative in filter(None, (part.strip() for part in spec.split(','))):
        options: list[tuple[int, ...]] = [()]
        for token in alternative.split():
            options = [sequence + (code,) for sequence in options for code in key_codes(token)]
        sequences += options
    return sequences


def key_label(alternative: str) -> str:
    """Writes a key alternative for display.

    Args:
        alternative: the keys, separated by spaces, like "g g".

    Returns:
        gg for "g g". Keys with longer names, like C-x C-c, stay apart.
    """
    tokens = alternative.split()
    return ''.join(tokens) if all(1 == len(token) for token in tokens) else ' '.join(tokens)
