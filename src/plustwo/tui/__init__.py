# SPDX-License-Identifier: Zlib
"""Terminal user interface of plustwo, for the reviewer.

Started by `plustwo` without arguments. Two screens: the stacks in
review (one per topic), and one change with its files, its comment
threads and the diff, which shows the threads under their lines.
Keys are shown at the bottom of every screen, `?` lists them all. Every
key is an action, see ACTIONS. The setting keys.preset picks vim (the
default), emacs or gerrit keys, keys.<action> sets single ones.

The change screen shows the diff side by side like Gerrit, drawn here from
the rows file_diff_rows() lays out, with syntax colours from bat if it
is installed. t hands over to vimdiff. Comments are written in a small box that opens inside the
diff and pushes the lines below down, ^E continues in the reviewer's
editor. The screen reloads by itself when commits or notes change, e.g.
when the author answers.

The mouse works too: a click selects, a double click opens, the wheel
scrolls.
"""
