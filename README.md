# plustwo

Gerrit-like code review of local commits, without a server. The name is
Gerrit's top Code-Review vote, +2, the one that lets a change be submitted.

The command is `git change`. It keeps everything in the repository itself:

- **Changes** are commits, identified by a Gerrit `Change-Id:` trailer. A
  commit reviewed here can later be uploaded to a real Gerrit as the same
  change.
- **Stacks** are the commits of a `review/<topic>` branch that are not on the
  base branch yet. Several stacks can be in review at once.
- **Patch sets** are the uploaded versions of a change, kept under
  `refs/patchsets/<Change-Id>/<n>`, so versions can be compared.
- **Comment threads and votes** live in git notes (`refs/notes/review`) and
  follow the commit through amends and rebases.

Two roles take part. The reviewer usually works in the terminal user
interface (`git change` without arguments), with vim-style keys, side by side
diffs and inline threads. The author answers and applies on the command line
(`git change inbox`, `reply`, `upload`). Submitting fast-forwards the base
branch, so it only ever gets reviewed commits.

## Requirements

| Program  | Used for                                    |
|----------|---------------------------------------------|
| Python 3 | everything (tested with 3.14)               |
| git      | everything                                  |
| delta    | side by side diffs (`git-delta` on Arch)    |
| vim      | `t` in the interface and `diff --tool`      |

## Install

    make install                      # below /usr/local, needs root
    make install PREFIX=~/.local      # for one user

For development, `make link PREFIX=~/.local` links the checkout instead.
`make uninstall` (with the same PREFIX) removes either.

## Getting started

    cd some/repository
    git change init                   # commit-msg hook and notes setup
    git switch -c review/my-topic
    git commit ...                    # gets a Change-Id
    git change upload 1               # first patch set, now In-Review
    git change                        # review it

`git change --help` lists all commands.
