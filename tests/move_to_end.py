# SPDX-License-Identifier: Zlib
"""A sequence editor for git rebase -i in tests: moves the picks whose
line contains the first argument to the end of the todo list.
"""

import sys

word, path = sys.argv[1], sys.argv[2]
with open(path, encoding='utf-8') as todo:
    lines = todo.read().split('\n')
picks = [line for line in lines if line.startswith('pick ')]
moved = [line for line in picks if word in line]
rest = [line for line in lines if line not in moved]
with open(path, 'w', encoding='utf-8') as todo:
    todo.write('\n'.join([line for line in picks if line not in moved] + moved
                         + [line for line in rest if line not in picks]))
