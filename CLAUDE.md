# plustwo

`plustwo` (also `git change`), a Gerrit-like local review tool. See
README.md for what it does and the docstring at the top of `plustwo` for
every command.

## Files

| File             | Content                                                       |
|------------------|---------------------------------------------------------------|
| `plustwo`        | core and command line (Python, no dependencies)               |
| `git-change`     | link to `plustwo`, the old name, which older hooks call       |
| `plustwo_tui.py` | curses interface for the reviewer, imported by the core       |
| `AGENTS.md`      | guide for agents as authors, printed by `plustwo guide`       |
| `tests/`         | `make test`: diff rows, flows, settings and keys              |
| `Makefile`       | `check`, `test`, `install`, `link`, `uninstall`               |

## Design rules

- All review logic lives in plain functions in `plustwo`. The command
  line, the curses interface and a planned web interface are thin frontends
  that call them. Never put logic into argparse handlers or curses code.
- Gerrit compatibility is a goal: the `Change-Id:` trailer, patch set refs,
  label names and vote rules follow Gerrit, so commits can go to a real
  Gerrit later. Check how Gerrit does something before inventing a variant.
- The state of a change comes from git, not from a flag: New (no patch set),
  In-Review (has one), Submitted (on the base branch, then gone from the
  list).
- Every write of a review note holds `.git/git-change.lock`, because the
  interface and the author write at the same time.
- The commit-msg hook calls `plustwo` through PATH, else `git-change`, so
  an install location can change without breaking repositories. Hooks
  from before the rename call `git-change`, so that name must stay.

## Data

- `refs/plustwo/stacks/<topic>`: the uploaded stacks, the "server". The
  reviewer's actions change only these refs and the base branch, never a
  local branch or checkout. `plustwo pull` brings them to the author.
- `refs/plustwo/patchsets/<Change-Id>/<n>`: the patch sets.
- Older repositories (`refs/patchsets/`, local `review/*` branches) are
  migrated on first use, `plustwo init` removes the old patch set refs.
- `refs/notes/review`: one JSON note per commit with comment threads and
  votes. The latest patch set's note is the review, upload copies it to
  the next one. `rebase_requested` marks a stack the author has to rebase. A message's `unresolved` (true, false or null) sets its thread's
  state, like in Gerrit. Older notes (a `status` per thread, `requests`)
  are converted when read. `plustwo init` sets `notes.rewriteRef`, so
  notes follow amends and rebases.
- Settings: own files in git config syntax, read with `git config --file`:
  `~/.config/plustwo/config` (user) and `.git/plustwo/config` (repository,
  wins). `review.base` is the base branch, default main. The old git
  config `change.base` is still read, `plustwo init` moves it. Never put
  settings into git's own config (the user's wish).

## Checking changes

- `make check` only compiles. pylint crashes on Python 3.14 (astroid on
  `import curses`), and pyflakes/flake8 are not installed.
- `make test` checks the diff rows the interface draws: against git (every
  line once, in order, the changed ones exactly git's edits) and delta (same
  lines per side). `TEST_REPOS=a:b` adds the history of other repositories,
  which are only read.
- Test in a scratch repository in the session scratchpad, never in a real
  one under review.
- The interface is tested without opening windows: run it in a detached
  tmux session (`tmux new-session -d -x 130 -y 24 "... plustwo"`), drive
  it with `send-keys`, read it with `capture-pane -p` (`-e` for colours).
  Replace the editor with a script that writes fixed text (`VISUAL=...`).
- Mouse input: X10 sequences sent with `send-keys -l`, built with Python.
  SGR sequences are not recognised in tmux and arrive as Esc, and zsh's
  `printf %b` mangles octal escapes.

## Live use

The tool is used for real reviews in ~/projects/prosarius (prs2d, circles,
prs_devtools), with Claude as the author. A broken `git change` or
`plustwo` on PATH breaks those reviews, so keep the installed copy working.
