# SPDX-License-Identifier: Zlib
"""Syntax colours of lines, from bat."""

import functools
import os
import shutil
import subprocess
from collections.abc import Sequence

from plustwo.diff.sgr import Segment, sgr_segments


def highlighter() -> str | None:
    """Finds the program that colours source code.

    Returns:
        The path of bat (batcat on Debian), None without it.
    """
    return shutil.which('bat') or shutil.which('batcat')


def syntax_segments(lines: Sequence[str], path: str,
                    theme: str | None = None) -> list[list[Segment]] | None:
    """Colours a file's lines with bat. Both interfaces draw the result.
    Kept by the text, so the same file isn't coloured twice.

    Args:
        lines: the file's lines.
        path: the file's path, from which bat picks the language.
        theme: a bat theme, None for bat's own choice.

    Returns:
        A list of segments per line, see sgr_segments(). None without bat,
        or if bat's output doesn't fit the lines.
    """
    if not lines:
        return None
    return _syntax_segments(tuple(lines), path, theme)


@functools.lru_cache(maxsize=64)
def _syntax_segments(lines: tuple[str, ...], path: str,
                     theme: str | None) -> list[list[Segment]] | None:
    """Colours lines with bat, see syntax_segments().

    Args:
        lines: the file's lines.
        path: the file's path, from which bat picks the language.
        theme: a bat theme, None for bat's own choice.

    Returns:
        A list of segments per line, None without bat or on a mismatch.
    """
    program = highlighter()
    if not program:
        return None
    result = subprocess.run([program, '--color=always', '--style=plain', '--paging=never',
                             '--wrap=never', f'--file-name={path}',
                             *([f'--theme={theme}'] if theme else [])],
                            input='\n'.join(lines) + '\n', capture_output=True, text=True,
                            errors='replace', env={**os.environ, 'COLORTERM': 'truecolor'})
    output = result.stdout.split('\n')
    if 0 != result.returncode or len(output) < len(lines):
        return None
    highlighted = []
    for line, rendered in zip(lines, output):
        segments = sgr_segments(rendered)
        if ''.join(segment[0] for segment in segments) != line:
            return None
        highlighted.append(segments)
    return highlighted
