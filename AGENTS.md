# plustwo for agents: working as the author

`plustwo` is a Gerrit-like review of local commits. A human reviews your
commits in its terminal interface, and you answer and fix on the command
line. This guide is printed by `plustwo guide`. `git change` is the same
program under its older name, use it if `plustwo` is not on PATH.

## The model in short

| Term         | Meaning                                                                    |
|--------------|----------------------------------------------------------------------------|
| change       | a commit with a `Change-Id:` trailer, which the commit-msg hook adds        |
| stack        | the commits of your branch that are not on the base branch, once uploaded   |
| patch set    | an uploaded version of a change, `PS1`, `PS2`, …                            |
| thread       | comments on a line, a file or `/COMMIT_MSG`, unresolved or resolved         |
| vote         | Code-Review -2..+2 (the reviewer's), Verified -1..+1 (from checks)          |

CHANGE in a command is the change's number in `plustwo list` or a prefix
of its Change-Id like `I3f2a`. Prefer the Change-Id prefix: numbers shift
when changes are submitted.

## Setup

Once per repository: `plustwo init`. Then work on a branch, one per
topic. Its name is the stack's topic (a leading `review/` is dropped):

    git switch -c <topic>
    git commit ...                  # gets a Change-Id
    plustwo upload               # PS1 of every change in the stack

Keep one logical change per commit. The reviewer sees the stack commit by
commit.

Uploading copies your stack to the review's own refs, like a push to a
Gerrit server. What the reviewer does there (a rebase, a submit) never
touches your branch. `plustwo pull` brings it in.

## The loop

1. Run `plustwo watch <REPO>...` as a background monitor. It prints one
   short line per event and nothing else:
   - `review <repo> <change>: <n> new` when the reviewer sent comments
   - `vote <repo> <change> <label><value> PS<n> by <who>` for a vote
   - `submitted <repo> <topic>: <n> changes` after a submit
   - `rebased <repo> <topic>` after the reviewer rebased the stack
   - `rebase <repo> <topic>` when that rebase had conflicts

   After `submitted` or `rebased`, run `plustwo pull` in the branch
   before working on. After `rebase`, rebase the branch onto the base
   yourself, resolve the conflicts and upload. With several agents on one
   repository, each watches only its own stack: `--topic <topic>`.
2. On an event, run `plustwo inbox <REPO>...` (also takes `--topic`). It
   lists every unresolved thread whose last message is the reviewer's, with
   the quoted source line, the reviewer's message on the whole change
   (place `/PATCHSET_LEVEL`), and negative votes with the end of their
   message. `plustwo show <CHANGE>` gives all threads of a change.
3. Work through all threads of the batch before answering (see below).
4. Fold each fix into the commit it belongs to, not into a new commit:

       git commit --fixup=<commit> ...
       GIT_SEQUENCE_EDITOR=true git rebase -i --autosquash <base>

5. Upload once: `plustwo upload` in the branch. Every changed change of
   the stack gets a new patch set.
6. Then answer the threads. Your messages are sent immediately, so
   answering after the upload makes "Done" refer to a patch set the reviewer
   can already see.

## Answering threads

How to answer is your judgment, not a fixed rule. The usual cases:

| Case                                   | Code change | Answer                                   |
|----------------------------------------|-------------|------------------------------------------|
| You agree                              | yes         | `plustwo done <CHANGE> <N>`, resolves |
| You disagree, but it's minor           | maybe       | `plustwo reply <CHANGE> <N> "..."`    |
| You disagree and it matters            | no          | `plustwo reply <CHANGE> <N> "..."`    |
| A question                             | rarely      | `plustwo reply <CHANGE> <N> "..."`    |

- In the second case you may make the change anyway and still say what you
  think. Leave the thread unresolved, so the reviewer decides.
- When you disagree, give reasons: what breaks, what it costs, what the
  alternative is. The reviewer resolves the thread or answers again.
- Answer questions without resolving, unless the answer clearly settles the
  thread. `plustwo reply ... --resolve` resolves with a text.
- `plustwo done <CHANGE> <N> "text"` resolves with your own text, e.g.
  "Done, also renamed the caller".
- Comments on `/COMMIT_MSG:<line>` are about the commit message. Fix them
  with an amend (or `git commit --fixup=amend:<commit>`).

To leave a note or a question of your own for the reviewer, e.g. on a
decision you are unsure about:

    plustwo comment <CHANGE> <path>:<line> "..." --author
    plustwo comment <CHANGE> <path> "..." --author --resolved

## Votes

- Checks (tests, builds) vote Verified on the top change of the stack:
  `plustwo vote <CHANGE> Verified +1 --by ci -m "<end of the log>"`, or
  `-1` when they fail.
- A negative vote shows up in `watch` and `inbox`. Treat Code-Review-1 or -2
  like comments: read its message and the threads.
- Votes belong to a patch set. A new one keeps Code-Review after a trivial
  rebase or a message-only change, Verified only without code change.

## Never

- Submit (`plustwo submit`). Submitting is the reviewer's decision.
- Vote Code-Review. It is the reviewer's label.
- Resolve a thread that asks for something without doing it or saying why.
- Answer review comments elsewhere (chat, commit messages). Answer in the
  thread.
- Delete your branch on your own after `submitted`. Run `plustwo pull`,
  which moves what is left onto the base branch. Whether an empty branch
  goes is the user's call.
