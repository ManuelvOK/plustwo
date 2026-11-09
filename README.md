# plustwo

Gerrit-like code review of local commits, without a server. Made for
reviewing what an AI agent (or a colleague) commits: you comment and vote in
a terminal interface, the author answers and fixes on the command line, and
only reviewed commits reach your base branch. The name is Gerrit's top
Code-Review vote, +2.

The command is `plustwo`, also available as `git change`. Everything lives
in the repository itself:

| #   | Thing         | Where                                                           |
|-----|---------------|-----------------------------------------------------------------|
| 1   | changes       | commits with a Gerrit `Change-Id:` trailer                      |
| 2   | stacks        | uploaded branches, `refs/plustwo/stacks/<topic>`                |
| 3   | patch sets    | each uploaded version, `refs/plustwo/patchsets/<Change-Id>/<n>` |
| 4   | threads, votes | git notes, `refs/notes/review`                                 |

Commits keep their `Change-Id`, so they can go to a real Gerrit later. An
existing Gerrit commit-msg hook works too.

## Requirements

| #   | Program  | Used for                                         |
|-----|----------|--------------------------------------------------|
| 1   | Python 3 | everything (tested with 3.14)                    |
| 2   | git      | everything                                       |
| 3   | bat      | optional: syntax colours in the diff             |
| 4   | vim      | optional: `t` opens a file's diff in vimdiff     |

## Install

    make install PREFIX=~/.local      # for one user, or without PREFIX
                                      # below /usr/local (needs root)
    make link PREFIX=~/.local         # for development: links the checkout

`make uninstall` with the same PREFIX removes either. `make test` runs the
tests.

## A first review

The author (an agent, for example) prepares a stack:

    plustwo init                      # once: commit-msg hook, notes setup
    git switch -c my-topic
    git commit ...                    # each commit gets a Change-Id
    plustwo upload                    # patch set 1 of every change

You review it with `plustwo`:

1. The list shows every stack. `l` opens a change, `T` jumps to the next
   change where it is your turn.
2. In a change: files and threads on the left, the side by side diff on
   the right. `Tab` switches panes, `]c` jumps to the next change.
3. `c` comments on the cursor line, `<` and `>` choose the old or new side.
   `m` marks a file reviewed.
4. On a thread: `r` replies, `y` answers Done, `Y` Ack, `x` toggles
   resolved. Everything stays a draft.
5. `s` sends all drafts, with an optional message on the whole change.
   That tells the author to start.
6. `A` votes Code-Review+2, `S` submits: the base branch moves forward to
   the change.

`?` lists all keys. The author's side is in [AGENTS.md](AGENTS.md), which
`plustwo guide` prints. A project's agent instructions only need to point
to that command. [TESTING.md](TESTING.md) walks through everything by hand.

## Settings

Settings are files in git config syntax, so nothing goes into git's own
config: `~/.config/plustwo/config` for you, `.git/plustwo/config` for one
repository (it wins).

    plustwo config --global keys.preset emacs
    plustwo config review.base develop
    plustwo config                    # lists all settings

| #   | Key               | Meaning                                                  |
|-----|-------------------|----------------------------------------------------------|
| 1   | `review.base`     | the base branch, default `main`                          |
| 2   | `keys.preset`     | `vim` (default), `emacs` or `gerrit`                     |
| 3   | `keys.<action>`   | keys of one action, e.g. `keys.reply = R`, see `?`       |
| 4   | `diff.whitespace` | `none`, `trailing`, `changed` or `all`, `W` cycles them  |

## License

GPL-3.0-or-later, see [LICENSE](LICENSE).
