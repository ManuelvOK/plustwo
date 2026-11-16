# SPDX-License-Identifier: Zlib
"""The keys in use: a preset and the user's own keys."""

from collections.abc import Mapping, Sequence

from plustwo.config.key_presets import ACTIONS, ACTION_NAMES, PRESETS
from plustwo.config.settings import settings_section
from plustwo.tui.keys import COUNT_PREFIXES, key_codes, key_label, key_sequences
from plustwo.tui.messages import PENDING
from plustwo.tui.sidebar import FooterEntry


# What Keymap.feed() returns after a whole key: (action or None, count or
# None)
FedKey = tuple[str | None, int | None]


class Keymap:
    """The keys of a preset, with the reviewer's own on top, turned into
    actions. Counts and key sequences like gg are collected here, so the
    views only see actions.

    Attributes:
        errors: what was wrong with the settings, for the message line.
        last: the key codes that gave the last action, like Enter or l for
            open.
        preset: the preset in use.
    """

    def __init__(self, preset: str = 'vim', overrides: Mapping[str, str] | None = None) -> None:
        """Binds the keys.

        Args:
            preset: the preset, one of PRESETS. vim for an unknown one.
            overrides: the user's own keys: each action's name, mapped to
                its keys like "g g,Home". None for none.
        """
        self.errors: list[str] = []
        self.last: tuple[int, ...] = ()
        if preset not in PRESETS:
            self.errors.append(f'unknown keys.preset "{preset}", using vim')
            preset = 'vim'
        self.preset = preset
        specs = dict(PRESETS[preset])
        overrides = dict(overrides or {})
        for name in list(overrides):
            if name not in ACTION_NAMES:
                self.errors.append(f'unknown action keys.{name}')
                del overrides[name]
        specs.update(overrides)
        # Each key code sequence, mapped to its action
        self._sequences: dict[tuple[int, ...], str] = {}
        # The reviewer's own keys are bound last, so they win over the
        # preset's.
        for name in sorted(specs, key=lambda name: name in overrides):
            try:
                sequences = key_sequences(specs[name])
            except ValueError as error:
                self.errors.append(f'keys.{name}: {error}')
                continue
            for sequence in sequences:
                self._sequences[sequence] = name
        self._prefixes = {sequence[:end] for sequence in self._sequences
                          for end in range(1, len(sequence))}
        # Each action, mapped to its bound keys for display
        self._labels: dict[str, list[str]] = {}
        for name, spec in specs.items():
            alternatives = [part.strip() for part in spec.split(',') if part.strip()]
            self._labels[name] = [alternative for alternative in alternatives
                                  if self._is_bound(alternative, name)]
        prefix = COUNT_PREFIXES.get(preset)
        self._count_key = key_codes(prefix)[0] if prefix else None
        self._pending: tuple[int, ...] = ()
        self._count = ''
        self._is_counting = False

    def _is_bound(self, alternative: str, name: str) -> bool:
        """Tells whether keys stand for an action, not overridden by
        another.

        Args:
            alternative: the keys, like "g g".
            name: the action's name.

        Returns:
            True if every sequence of the keys runs the action.
        """
        try:
            return all(name == self._sequences.get(sequence)
                       for sequence in key_sequences(alternative))
        except ValueError:
            return False

    def single_keys(self, name: str) -> set[int]:
        """Finds the keys that alone stand for an action.

        Args:
            name: the action's name.

        Returns:
            The key codes.
        """
        return {sequence[0] for sequence, bound in self._sequences.items()
                if name == bound and 1 == len(sequence)}

    def labels(self, name: str) -> list[str]:
        """Lists the keys of an action for display.

        Args:
            name: the action's name.

        Returns:
            The keys, like ['j', 'Down'].
        """
        return self._labels.get(name, [])

    def label(self, name: str) -> str:
        """Writes the first key of an action for display.

        Args:
            name: the action's name.

        Returns:
            The key, like gg. '' if the action has none.
        """
        labels = self.labels(name)
        return key_label(labels[0]) if labels else ''

    def typed(self) -> str:
        """Writes what is typed so far of a count or sequence, for display.

        Returns:
            The digits and the printable keys.
        """
        return self._count + ''.join(chr(code) if code < 0x110000 and chr(code).isprintable()
                                     else '' for code in self._pending)

    def reset(self) -> None:
        """Forgets a count or sequence typed so far."""
        self._pending, self._count, self._is_counting = (), '', False

    def feed(self, code: int) -> FedKey | str:
        """Takes one key code.

        Args:
            code: the key code.

        Returns:
            PENDING while a count or sequence goes on, else (action or None,
            count or None).
        """
        if 27 == code and (self._pending or self._count or self._is_counting):
            # Esc cancels a count or sequence, like in vim
            self.reset()
            return PENDING
        takes_digits = self._count_key is None or self._is_counting
        if takes_digits and not self._pending and (
                ord('1') <= code <= ord('9') or (ord('0') == code and self._count)):
            self._count += chr(code)
            return PENDING
        if code == self._count_key and not self._pending and not self._is_counting:
            self._is_counting = True
            return PENDING
        self._is_counting = False
        sequence = self._pending + (code,)
        if sequence in self._prefixes:
            self._pending = sequence
            return PENDING
        self._pending = ()
        count = int(self._count) if self._count else None
        self._count = ''
        # Which keys gave the action, e.g. Enter or l for open
        self.last = sequence
        return self._sequences.get(sequence), count

    def footer(self, entries: Sequence[FooterEntry]) -> str:
        """Writes the footer.

        Args:
            entries: the footer's entries, see FooterEntry.

        Returns:
            The text, like "j/k move  ? help". Actions without keys are
            left out.
        """
        parts = []
        for names, label in entries:
            keys = '/'.join(filter(None, (self.label(name) for name in names)))
            if keys:
                parts.append(f'{keys} {label}')
        return '  '.join(parts)

    def help_lines(self, width: int) -> list[str]:
        """Writes the help screen: the keys of every action with a few
        words, one section per group, in two columns when the screen is
        wide enough.

        Args:
            width: the screen's width.

        Returns:
            The lines.
        """
        groups: dict[str, list[tuple[str, str]]] = {}
        for name, group, label, _description in ACTIONS:
            keys = ' '.join(key_label(alternative) for alternative in self.labels(name))
            if keys:
                groups.setdefault(group, []).append((keys, label))
        lines = [f'plustwo keys ({self.preset})   mouse: click, double click, wheel', '']
        cell = min(max(len(f'  {keys:<9.9} {label}') for entries in groups.values()
                       for keys, label in entries) + 2, 36)
        n_columns = 2 if width >= 2 * cell + 2 else 1
        for group, entries in groups.items():
            lines.append(group)
            for start in range(0, len(entries), n_columns):
                lines.append(''.join(f'  {keys:<9.9} {label}'[:cell].ljust(cell)
                                     for keys, label in entries[start:start + n_columns]
                                     ).rstrip())
        lines += ['', 'Comment box: Enter saves. Alt+Enter, Ctrl+J or \\ at the end start a new '
                  'line. Esc on text asks once, again discards it.',
                  'Blue lines come from changes below, purple partly.']
        return lines


def keymap_from_settings() -> Keymap:
    """Makes the keymap the settings ask for.

    Returns:
        The keymap of keys.preset and the other keys settings.
    """
    overrides = settings_section('keys')
    preset = overrides.pop('preset', 'vim')
    return Keymap(preset, overrides)
