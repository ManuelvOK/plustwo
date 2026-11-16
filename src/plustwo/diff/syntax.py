# SPDX-License-Identifier: Zlib
"""Syntax colours of lines, from bat."""

import functools
import os
import shutil
import subprocess

from plustwo.diff.sgr import sgr_segments


def highlighter():
    """The program that colours source code: bat (batcat on Debian), None
    without it.
    """
    return shutil.which('bat') or shutil.which('batcat')


def syntax_segments(lines, path, theme=None):
    """Syntax colours of a file's lines, from bat: for each line a list of
    (text, colour, styles), see sgr_segments(). None without bat, or if
    its output doesn't fit the lines. Both interfaces draw these. theme is
    a bat theme, None for bat's own choice. Kept by the text, so the same
    file isn't coloured twice.
    """
    if not lines:
        return None
    return _syntax_segments(tuple(lines), path, theme)


@functools.lru_cache(maxsize=64)
def _syntax_segments(lines, path, theme):
    """syntax_segments() of lines as a tuple."""
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
