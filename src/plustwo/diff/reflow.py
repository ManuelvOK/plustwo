# SPDX-License-Identifier: Zlib
"""Reflowed paragraphs: blocks whose words stay, only the line breaks move."""

import collections
import difflib
import functools
import re
from collections.abc import Sequence
from dataclasses import dataclass

from plustwo.diff.intraline import CharacterRange
from plustwo.diff.pairing import LinePair
from plustwo.git.cache import CACHE_SIZE


# A block counts as reflowed when at least this share of its tokens is the
# same on both sides, in order
REFLOW_MIN_RATIO = 0.75

# Blocks with more tokens than this are not checked, the word diff would
# take too long
_REFLOW_MAX_TOKENS = 20000

# The tokens compared: words and single other characters. Whitespace and
# line breaks don't count, so rebreaking text or code changes no token.
_TOKEN = re.compile(r'\w+|[^\w\s]')

# Comment and quote markers at the start of a line, after the indentation
_MARKER = re.compile(r'\s*(//+|#+|--|;+|%+|>+|\*)(?=\s|$)')


@dataclass
class Reflow:
    """A reflowed block: how its lines pair and what really changed.

    Attributes:
        pairs: the pairs of lines, top to bottom, see LinePair.
        old_edits: per removed line, the character ranges of changed words.
        new_edits: per added line, the character ranges of changed words.
    """
    pairs: list[LinePair]
    old_edits: list[list[CharacterRange]]
    new_edits: list[list[CharacterRange]]


# A token of a block: (line index, begin, end, text)
_Token = tuple[int, int, int, str]


def _block_tokens(lines: Sequence[str]) -> list[_Token]:
    """Splits a block into its tokens. A comment or quote marker that
    starts every line is left out, it is part of the line, not of the text.

    Args:
        lines: the block's lines.

    Returns:
        The tokens, in order.
    """
    markers = {match.group(1) if (match := _MARKER.match(line)) else None
               for line in lines if line.strip()}
    marker = markers.pop() if 1 == len(markers) else None
    tokens = []
    for index, line in enumerate(lines):
        start = 0
        if marker is not None and (match := _MARKER.match(line)):
            start = match.end()
        tokens += [(index, match.start(), match.end(), match.group())
                   for match in _TOKEN.finditer(line, start)]
    return tokens


def _bag_ratio(old: list[_Token], new: list[_Token]) -> float:
    """Measures how many tokens both blocks have, in any order. Cheap, so
    blocks that are no reflow are left early.

    Args:
        old: the old block's tokens.
        new: the new block's tokens.

    Returns:
        0 to 1, like difflib's ratio().
    """
    common = collections.Counter(token[3] for token in old) \
        & collections.Counter(token[3] for token in new)
    return 2 * sum(common.values()) / (len(old) + len(new))


def reflowed(removed: Sequence[str], added: Sequence[str]) -> Reflow | None:
    """Checks whether an edit's lines are a reflowed block: mostly the same
    tokens in the same order, but broken into lines elsewhere. Then the
    line pairs, and the edits are only the tokens that really changed.
    Like git diff --word-diff, which doesn't see line breaks either. The
    result is kept for the same lines.

    Args:
        removed: the removed lines.
        added: the added lines.

    Returns:
        The reflow, None when the lines aren't one.
    """
    return _reflowed(tuple(removed), tuple(added))


@functools.lru_cache(maxsize=CACHE_SIZE)
def _reflowed(removed: tuple[str, ...], added: tuple[str, ...]) -> Reflow | None:
    """Checks for a reflowed block, see reflowed().

    Args:
        removed: the removed lines.
        added: the added lines.

    Returns:
        The reflow, None when the lines aren't one.
    """
    if max(len(removed), len(added)) < 2 or not removed or not added:
        return None
    old, new = _block_tokens(removed), _block_tokens(added)
    if not old or not new or len(old) + len(new) > _REFLOW_MAX_TOKENS \
            or _bag_ratio(old, new) < REFLOW_MIN_RATIO:
        return None
    matcher = difflib.SequenceMatcher(None, [token[3] for token in old],
                                      [token[3] for token in new], autojunk=False)
    if matcher.ratio() < REFLOW_MIN_RATIO:
        return None
    # Where the equal tokens went, and whether a line break moved
    new_of_old: dict[int, int] = {}
    for tag, old_begin, old_end, new_begin, _ in matcher.get_opcodes():
        if 'equal' == tag:
            new_of_old.update((index, new_begin + index - old_begin)
                              for index in range(old_begin, old_end))
    if not _breaks_moved(old, new, new_of_old):
        return None
    old_edits: list[list[CharacterRange]] = [[] for _ in removed]
    new_edits: list[list[CharacterRange]] = [[] for _ in added]
    for tag, old_begin, old_end, new_begin, new_end in matcher.get_opcodes():
        if 'equal' != tag:
            _mark(old[old_begin:old_end], old_edits)
            _mark(new[new_begin:new_end], new_edits)
    return Reflow(_pairs(old, new, new_of_old, len(removed), len(added)),
                  old_edits, new_edits)


def _breaks_moved(old: list[_Token], new: list[_Token], new_of_old: dict[int, int]) -> bool:
    """Tells whether a line break sits between other tokens on the two
    sides. Else the edit is an ordinary one, line by line.

    Args:
        old: the old block's tokens.
        new: the new block's tokens.
        new_of_old: the new token of each equal old token.

    Returns:
        True when a break between two equal tokens is on one side only.
    """
    for index in range(len(old) - 1):
        if index in new_of_old and index + 1 in new_of_old \
                and new_of_old[index + 1] == new_of_old[index] + 1:
            old_break = old[index][0] != old[index + 1][0]
            new_break = new[new_of_old[index]][0] != new[new_of_old[index + 1]][0]
            if old_break != new_break:
                return True
    return False


def _mark(tokens: list[_Token], edits: list[list[CharacterRange]]) -> None:
    """Adds changed tokens to their lines' edits, a range per line.

    Args:
        tokens: a run of changed tokens.
        edits: the edits per line, changed in place.
    """
    by_line: dict[int, CharacterRange] = {}
    for line, begin, end, _ in tokens:
        first = by_line.get(line, (begin, end))
        by_line[line] = (min(first[0], begin), max(first[1], end))
    for line, character_range in by_line.items():
        edits[line].append(character_range)


def _pairs(old: list[_Token], new: list[_Token], new_of_old: dict[int, int],
           n_removed: int, n_added: int) -> list[LinePair]:
    """Pairs each old line with the new line most of its kept tokens went
    to, in order. The other lines stand alone.

    Args:
        old: the old block's tokens.
        new: the new block's tokens.
        new_of_old: the new token of each equal old token.
        n_removed: the number of removed lines.
        n_added: the number of added lines.

    Returns:
        The pairs, top to bottom, see LinePair.
    """
    went: dict[int, collections.Counter[int]] = collections.defaultdict(collections.Counter)
    for index, token in enumerate(old):
        if index in new_of_old:
            went[token[0]][new[new_of_old[index]][0]] += 1
    # Most common() keeps the first of equal counts, the upper line
    target = {line: counts.most_common(1)[0][0] for line, counts in went.items()}
    result: list[LinePair] = []
    new_at = 0
    for line in range(n_removed):
        partner = target.get(line)
        if partner is None or partner < new_at:
            result.append((line, None))
            continue
        result += [(None, index) for index in range(new_at, partner)]
        result.append((line, partner))
        new_at = partner + 1
    result += [(None, index) for index in range(new_at, n_added)]
    return result
