# plustwo

Gerrit-like code review of local commits, without a server. The name is
Gerrit's top Code-Review vote, +2, the one that lets a change be submitted.

The command is `plustwo`, also available as `git change`. It keeps
everything in the repository itself:

- **Changes** are commits, identified by a Gerrit `Change-Id:` trailer. A
  commit reviewed here can later be uploaded to a real Gerrit as the same
  change.
- **Stacks** are the commits of a local branch that are not on the base
  branch yet. Uploading copies them to `refs/plustwo/stacks/<topic>`, which
  plays the part of the Gerrit server: the reviewer's rebase and submit
  change only that, never a checkout. Several stacks can be in review at
  once.
- **Patch sets** are the uploaded versions of a change, kept under
  `refs/plustwo/patchsets/<Change-Id>/<n>`, so versions can be compared.
- **Comment threads and votes** live in git notes (`refs/notes/review`) on
  the latest patch set.

Two roles take part. The reviewer usually works in the terminal user
interface (`plustwo` without arguments), with vim-style keys, side by side
diffs and inline threads. The author answers and applies on the command line
(`plustwo inbox`, `reply`, `upload`, and `pull` for what the reviewer
changed). Submitting fast-forwards the base branch, so it only ever gets
reviewed commits.

Threads work like in Gerrit: unresolved or resolved, and the author answers
"Done" after fixing. AI agents working as the author find their guide in
[AGENTS.md](AGENTS.md), also printed by `plustwo guide`, so a project's
agent instructions only need to point to that command.

## Requirements

| Program  | Used for                                                  |
|----------|-----------------------------------------------------------|
| Python 3 | everything (tested with 3.14)                             |
| git      | everything                                                |
| bat      | optional: syntax colours in the interface's diff          |
| vim      | `t` in the interface and `diff --tool`                    |

The interface draws its side by side diff itself, like Gerrit's: git
computes the edits (histogram algorithm, as JGit), the interface lays them
out. `make test` checks that layout against git and, if installed, delta.

## Install

    make install                      # below /usr/local, needs root
    make install PREFIX=~/.local      # for one user

For development, `make link PREFIX=~/.local` links the checkout instead.
`make uninstall` (with the same PREFIX) removes either.

## Getting started

    cd some/repository
    plustwo init                      # commit-msg hook and notes setup
    git switch -c my-topic
    git commit ...                    # gets a Change-Id
    plustwo upload                    # first patch sets, now In-Review
    plustwo                           # review it

`plustwo --help` lists all commands.

## Settings

Settings live in files of their own, in git config syntax, so nothing goes
into git's config: `~/.config/plustwo/config` for you and
`.git/plustwo/config` for one repository, which wins.

    plustwo config review.base develop        # this repository's base branch
    plustwo config --global keys.preset emacs # vim (default), emacs, gerrit
    plustwo config                            # lists all settings

| Key             | Meaning                                              |
|-----------------|------------------------------------------------------|
| `review.base`   | the base branch, default `main`                      |
| `keys.preset`   | the interface's keys: `vim`, `emacs` or `gerrit`     |
| `keys.<action>` | keys of one action, e.g. `keys.reply = R`, see `?`   |
