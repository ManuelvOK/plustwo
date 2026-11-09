# plustwo

`git change`, a Gerrit-like local review tool. See README.md for what it does
and the docstring at the top of `git-change` for every command.

## Files

| File                | Content                                                    |
|---------------------|------------------------------------------------------------|
| `git-change`        | core and command line (Python, no dependencies)            |
| `git_change_tui.py` | curses interface for the reviewer, imported by the core    |
| `AGENTS.md`         | guide for agents as authors, printed by `git change guide` |
| `tests/`            | `make test`: checks of the side by side diff rows          |
| `Makefile`          | `check`, `test`, `install`, `link`, `uninstall`            |

## Design rules

- All review logic lives in plain functions in `git-change`. The command
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
- The commit-msg hook calls `git-change` through PATH, so an install
  location can change without breaking repositories.

## Data

- `refs/plustwo/stacks/<topic>`: the uploaded stacks, the "server". The
  reviewer's actions change only these refs and the base branch, never a
  local branch or checkout. `git change pull` brings them to the author.
- `refs/plustwo/patchsets/<Change-Id>/<n>`: the patch sets.
- Older repositories (`refs/patchsets/`, local `review/*` branches) are
  migrated on first use, `git change init` removes the old patch set refs.
- `refs/notes/review`: one JSON note per commit with comment threads and
  votes. The latest patch set's note is the review, upload copies it to
  the next one. `rebase_requested` marks a stack the author has to rebase. A message's `unresolved` (true, false or null) sets its thread's
  state, like in Gerrit. Older notes (a `status` per thread, `requests`)
  are converted when read. `git change init` sets `notes.rewriteRef`, so
  notes follow amends and rebases.
- `git config change.base`: the base branch, default main.

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
  tmux session (`tmux new-session -d -x 130 -y 24 "... git change"`), drive
  it with `send-keys`, read it with `capture-pane -p` (`-e` for colours).
  Replace the editor with a script that writes fixed text (`VISUAL=...`).
- Mouse input: X10 sequences sent with `send-keys -l`, built with Python.
  SGR sequences are not recognised in tmux and arrive as Esc, and zsh's
  `printf %b` mangles octal escapes.

## Live use

The tool is used for real reviews in ~/projects/prosarius (prs2d, circles,
prs_devtools), with Claude as the author. A broken `git change` on PATH
breaks those reviews, so keep the installed copy working.
