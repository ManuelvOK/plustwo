#!/usr/bin/env python3
# SPDX-License-Identifier: Zlib
"""Checks that the layers of plustwo import only what they may.

Usage: check_layers.py ARCHITECTURE SOURCE_DIRECTORY

ARCHITECTURE is a Markdown file with a table of the layers whose columns
include "Layer" and "Uses". SOURCE_DIRECTORY is the package, with one
sub-package per layer. A layer may import itself, the layers in its "Uses"
column, the layers those use (transitively), and the modules at the top of
the package. Those top modules may import no layer. Prints one line per
finding ("file:line: message") and exits with 1 if there is any, else 0.
"""

import ast
import re
import sys
from pathlib import Path


def read_layers(path):
    """The layers of the table in a Markdown file.

    Returns a dict of each layer's name to the set of layers it uses
    directly. Raises SystemExit if the file has no such table.
    """
    columns = None
    layers = {}
    for line in Path(path).read_text().split('\n'):
        if not line.startswith('|'):
            if layers:
                break
            columns = None
            continue
        cells = [cell.strip() for cell in line.strip('|').split('|')]
        if columns is None:
            columns = cells
            continue
        if set(''.join(cells)) <= set('-: '):
            continue
        if 'Layer' not in columns or 'Uses' not in columns:
            continue
        name = cells[columns.index('Layer')].strip('`')
        uses = cells[columns.index('Uses')]
        layers[name] = set() if 'nothing' == uses else {
            use.strip().strip('`') for use in uses.split(',')}
    if not layers:
        raise SystemExit(f'{path}: no table with the columns Layer and Uses')
    return layers


def allowed_layers(layers):
    """Each layer's name to every layer it may import: itself and all it
    uses, directly or through other layers.
    """
    allowed = {}
    for name in layers:
        reached, todo = {name}, [name]
        while todo:
            for use in layers.get(todo.pop(), ()):
                if use not in reached:
                    reached.add(use)
                    todo.append(use)
        allowed[name] = reached
    return allowed


def imported_layers(path, package):
    """The layers and top modules a file imports: (line, name) pairs."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(), str(path))):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split('.')
            if package == parts[0] and len(parts) > 1:
                found.append((node.lineno, parts[1]))
            elif package == node.module:
                found += [(node.lineno, alias.name) for alias in node.names]
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split('.')
                if package == parts[0] and len(parts) > 1:
                    found.append((node.lineno, parts[1]))
    return found


def main():
    """Checks the package against the table. Returns the exit code."""
    if 3 != len(sys.argv):
        print(__doc__.split('\n\n')[1], file=sys.stderr)
        return 2
    layers = read_layers(sys.argv[1])
    allowed = allowed_layers(layers)
    root = Path(sys.argv[2])
    package = root.name
    top_modules = {path.stem for path in root.glob('*.py') if '__init__' != path.stem}
    findings = []
    for path in sorted(root.rglob('*.py')):
        parts = path.relative_to(root).parts
        layer = parts[0] if len(parts) > 1 else None
        if layer is not None and layer not in layers:
            findings.append(f'{path}:1: layer "{layer}" is not in the table')
            continue
        for line, name in imported_layers(path, package):
            if name in top_modules:
                continue
            if layer is None:
                findings.append(f'{path}:{line}: a top module imports the layer "{name}"')
            elif name not in allowed[layer]:
                findings.append(f'{path}:{line}: "{layer}" may not import "{name}"')
    for finding in findings:
        print(finding)
    return 1 if findings else 0


if __name__ == '__main__':
    sys.exit(main())
