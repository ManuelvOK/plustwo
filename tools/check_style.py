#!/usr/bin/env python3
# SPDX-License-Identifier: Zlib
"""Checks the style rules of plustwo that the compiler and mypy can't check.

Usage: check_style.py FILE...

Prints one line per finding ("file:line: message") and exits with 1 if there
is any finding, else with 0. The checks for Python files:

- lines are at most MAX_COLUMNS columns long
- no Unicode ellipsis and no em dash, anywhere
- comments and docstrings contain no semicolon
- section comments ("# ── name ───") end at column SECTION_WIDTH
- constants first in == and != comparisons: a literal or an ALL_CAPS name
  belongs on the left
- every module, class and function has a docstring. A function's names
  each parameter under "Args:", has "Returns:" when the function returns a
  value or yields (not for a property), and "Raises:" when it raises

Other files (JavaScript, CSS, HTML, Markdown) get the first two checks.
"""

import ast
import io
import re
import sys
import tokenize
from pathlib import Path

MAX_COLUMNS = 100

# Section comments all end at this column, so they line up in every file.
SECTION_WIDTH = 80

SECTION_PATTERN = re.compile(r'^\s*# ── .* ─+$')

FORBIDDEN_CHARACTERS = {
    '\u2026': 'Unicode ellipsis, write "..." instead',
    '\u2014': 'em dash, rephrase or use ":" or "-"',
}

# An ALL_CAPS name: a constant by convention
CONSTANT_NAME = re.compile(r'^_?[A-Z][A-Z0-9_]*$')


def line_findings(text: str) -> list[tuple[int, str]]:
    """Finds over-long lines and forbidden characters.

    Args:
        text: the content of a file.

    Returns:
        (line number, message) pairs, one per finding.
    """
    findings: list[tuple[int, str]] = []
    for number, line in enumerate(text.split('\n'), 1):
        if MAX_COLUMNS < len(line):
            findings.append((number, f'line has {len(line)} columns, more than {MAX_COLUMNS}'))
        for character, message in FORBIDDEN_CHARACTERS.items():
            if character in line:
                findings.append((number, message))
    return findings


def comment_findings(text: str) -> list[tuple[int, str]]:
    """Finds semicolons in comments and section comments of the wrong width.

    Args:
        text: the content of a Python file.

    Returns:
        (line number, message) pairs, one per finding.
    """
    findings: list[tuple[int, str]] = []
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if tokenize.COMMENT != token.type:
            continue
        number = token.start[0]
        if ';' in token.string:
            findings.append((number, 'semicolon in a comment, write two sentences'))
        line = text.split('\n')[number - 1]
        if SECTION_PATTERN.match(line) and SECTION_WIDTH != len(line):
            findings.append((number, f'section comment ends at column {len(line)}, '
                                     f'not {SECTION_WIDTH}'))
    return findings


def is_constant(node: ast.expr) -> bool:
    """Whether an expression is a constant: a literal or an ALL_CAPS name.

    Args:
        node: an expression of the syntax tree.

    Returns:
        True for a literal (None included), a name or an attribute in
        ALL_CAPS, else False.
    """
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Name):
        return bool(CONSTANT_NAME.match(node.id))
    if isinstance(node, ast.Attribute):
        return bool(CONSTANT_NAME.match(node.attr))
    return False


def own_nodes(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.AST]:
    """The nodes of a function's body, without nested functions and classes.

    Args:
        function: a function of the syntax tree.

    Returns:
        The nodes, in no particular order.
    """
    nodes: list[ast.AST] = []
    pending: list[ast.AST] = list(function.body)
    while pending:
        node = pending.pop()
        nodes.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        pending.extend(ast.iter_child_nodes(node))
    return nodes


def docstring_section_findings(function: ast.FunctionDef | ast.AsyncFunctionDef,
                               docstring: str) -> list[tuple[int, str]]:
    """Finds parameters, return values and raised errors that a function's
    docstring doesn't document.

    Args:
        function: a function of the syntax tree.
        docstring: the function's docstring.

    Returns:
        (line number, message) pairs, one per finding.
    """
    findings: list[tuple[int, str]] = []
    arguments = function.args
    names = [argument.arg for argument in arguments.posonlyargs + arguments.args
             + arguments.kwonlyargs]
    names += [argument.arg for argument in (arguments.vararg, arguments.kwarg) if argument]
    names = [name for name in names if name not in ('self', 'cls')]
    documented = set(re.findall(r'^\s+\**(\w+)(?: \(.*?\))?:', docstring.partition('Args:')[2]
                                .split('Returns:')[0].split('Raises:')[0], re.MULTILINE))
    for name in names:
        if name not in documented:
            findings.append((function.lineno, f'{function.name}: parameter {name} not under Args:'))
    nodes = own_nodes(function)
    returns = any(isinstance(node, ast.Return) and node.value is not None
                  and not (isinstance(node.value, ast.Constant) and node.value.value is None)
                  for node in nodes)
    yields = any(isinstance(node, (ast.Yield, ast.YieldFrom)) for node in nodes)
    # A property's docstring describes the value, like an attribute's.
    is_property = any(isinstance(decorator, ast.Name) and 'property' == decorator.id
                      for decorator in function.decorator_list)
    if (returns or yields) and not is_property and 'Returns:' not in docstring \
            and 'Yields:' not in docstring:
        findings.append((function.lineno, f'{function.name}: no Returns: section'))
    if any(isinstance(node, ast.Raise) and node.exc is not None for node in nodes) \
            and 'Raises:' not in docstring:
        findings.append((function.lineno, f'{function.name}: no Raises: section'))
    return findings


def tree_findings(tree: ast.Module) -> list[tuple[int, str]]:
    """Finds missing docstrings, docstrings with semicolons and
    comparisons with the constant on the right.

    Args:
        tree: the syntax tree of a Python module.

    Returns:
        (line number, message) pairs, one per finding.
    """
    findings: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            docstring = ast.get_docstring(node, clean=False)
            number = getattr(node, 'lineno', 1)
            if docstring is None:
                name = getattr(node, 'name', 'module')
                findings.append((number, f'{name} has no docstring'))
            else:
                if ';' in docstring:
                    findings.append((number, 'semicolon in a docstring, write two sentences'))
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    findings += docstring_section_findings(node, docstring)
        if isinstance(node, ast.Compare) and 1 == len(node.ops) \
                and isinstance(node.ops[0], (ast.Eq, ast.NotEq)) \
                and is_constant(node.comparators[0]) and not is_constant(node.left):
            findings.append((node.lineno, 'constant on the right of a comparison, put it first'))
    return findings


def check_file(path: str) -> list[tuple[int, str]]:
    """Checks one file.

    Args:
        path: the path of the file.

    Returns:
        (line number, message) pairs, one per finding.
    """
    text = Path(path).read_text()
    findings = line_findings(text)
    if path.endswith('.py') or 'python' in text.split('\n', 1)[0]:
        findings += comment_findings(text)
        findings += tree_findings(ast.parse(text, path))
    return sorted(findings)


def main() -> int:
    """Checks the files named on the command line and prints the findings.

    Returns:
        The exit code: 1 if there is any finding, else 0.
    """
    n_findings = 0
    for path in sys.argv[1:]:
        for number, message in check_file(path):
            print(f'{path}:{number}: {message}')
            n_findings += 1
    return 1 if n_findings else 0


if '__main__' == __name__:
    sys.exit(main())
