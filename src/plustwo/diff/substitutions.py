# SPDX-License-Identifier: Zlib
"""Recurring substitutions between two versions of a file, like a class
renamed together with its file: the same word replaced the same way on
several lines. Research calls them systematic edits [Meng 2013]."""

import difflib
import re

# Lines that say nothing about a file's identity
BARE_LINES = ('', '{', '}', '};')

# A word replaced on fewer lines is no recurring substitution
_MIN_SUBSTITUTED_LINES = 2

_TOKEN = re.compile(r'\w+|\S')
_WORD = re.compile(r'\w+')
_CAMEL_BOUNDARY = re.compile(r'(?<=[a-z0-9])(?=[A-Z])')


def name_variants(old_stem: str, new_stem: str) -> dict[str, str]:
    """Spells a renamed file's name in the usual styles of code, old and
    new alike: queue -> pending_input gives Queue -> PendingInput, QUEUE ->
    PENDING_INPUT and more.

    Args:
        old_stem: the old file name without directory and extension.
        new_stem: the new one.

    Returns:
        Each old spelling, mapped to the new one in the same style.
    """
    def words(stem: str) -> list[str]:
        """Splits a name into its lower case words.

        Args:
            stem: the name, in any style.

        Returns:
            The words.
        """
        return [word.lower() for word in re.split(r'[_\-.\s]+', _CAMEL_BOUNDARY.sub('_', stem))
                if word]

    old_words, new_words = words(old_stem), words(new_stem)
    if not old_words or not new_words or old_words == new_words:
        return {}
    styles = (lambda w: '_'.join(w), lambda w: '_'.join(w).upper(),
              lambda w: ''.join(word.capitalize() for word in w),
              lambda w: w[0] + ''.join(word.capitalize() for word in w[1:]),
              lambda w: '-'.join(w))
    variants = {style(old_words): style(new_words) for style in styles}
    variants.setdefault(old_stem, new_stem)
    return variants


def _replaced(lines: list[str], substitutions: dict[str, str], whole_words: bool) -> list[str]:
    """Applies substitutions to lines.

    Args:
        lines: the lines.
        substitutions: each old text, mapped to the new one.
        whole_words: whether only whole words are replaced, else any text,
            longest first.

    Returns:
        The lines with the substitutions made.
    """
    if not substitutions:
        return lines
    if whole_words:
        return [_WORD.sub(lambda match: substitutions.get(match.group(), match.group()), line)
                for line in lines]
    pattern = re.compile('|'.join(re.escape(old) for old in
                                  sorted(substitutions, key=len, reverse=True)))
    return [pattern.sub(lambda match: substitutions[match.group()], line) for line in lines]


def recurring_substitutions(old_lines: list[str], new_lines: list[str]) -> dict[str, str]:
    """Finds the words that the lines of a file replace the same way on
    several lines. The lines are aligned by the ones left as they were. A
    changed block of as many lines in both versions pairs its lines in
    order, and a pair of lines with as many tokens tells the words replaced.

    Args:
        old_lines: the file's lines in the old version.
        new_lines: the file's lines in the new version.

    Returns:
        Each old word, mapped to the word replacing it. A word replaced by
        different words is left out.
    """
    counts: dict[tuple[str, str], int] = {}
    matcher = difflib.SequenceMatcher(None, _without_spaces(old_lines),
                                      _without_spaces(new_lines), autojunk=False)
    for tag, old_begin, old_end, new_begin, new_end in matcher.get_opcodes():
        if 'replace' != tag or old_end - old_begin != new_end - new_begin:
            continue
        for old_line, new_line in zip(old_lines[old_begin:old_end], new_lines[new_begin:new_end]):
            old_tokens, new_tokens = _TOKEN.findall(old_line), _TOKEN.findall(new_line)
            if len(old_tokens) != len(new_tokens):
                continue
            pairs = {(old, new) for old, new in zip(old_tokens, new_tokens)
                     if old != new and _WORD.fullmatch(old) and _WORD.fullmatch(new)}
            # Only lines that differ in replaced words
            if len(pairs) != sum(1 for old, new in zip(old_tokens, new_tokens) if old != new):
                continue
            for pair in pairs:
                counts[pair] = counts.get(pair, 0) + 1
    targets: dict[str, set[str]] = {}
    for old, new in counts:
        targets.setdefault(old, set()).add(new)
    return {old: new for (old, new), count in counts.items()
            if count >= _MIN_SUBSTITUTED_LINES and 1 == len(targets[old])}


def kept_lines(old_lines: list[str], new_lines: list[str],
               names: dict[str, str] | None = None) -> int:
    """Counts the lines of a file another one keeps, after the recurring
    substitutions between them: the renamed file's name in its spellings
    (see name_variants()), then the words replaced on several lines.

    Args:
        old_lines: the old file's lines.
        new_lines: the new file's lines.
        names: the name spellings, see name_variants(). None for none.

    Returns:
        The number of old lines, other than BARE_LINES, that the new file
        has in the same order.
    """
    old_lines = _replaced(old_lines, names or {}, whole_words=False)
    old_lines = _replaced(old_lines, recurring_substitutions(old_lines, new_lines),
                          whole_words=True)
    # Whitespace doesn't count: indentation often changes when code moves.
    old_keys, new_keys = _without_spaces(old_lines), _without_spaces(new_lines)
    matcher = difflib.SequenceMatcher(None, old_keys, new_keys, autojunk=False)
    return sum(1 for block in matcher.get_matching_blocks()
               for line in old_lines[block.a:block.a + block.size]
               if line.strip() not in BARE_LINES)


def _without_spaces(lines: list[str]) -> list[str]:
    """Removes all whitespace from lines.

    Args:
        lines: the lines.

    Returns:
        The lines without whitespace.
    """
    return [''.join(line.split()) for line in lines]
