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

Once per repository: `plustwo init`. Once per session:
`export PLUSTWO_USER=agent`, so the reviewer's dashboard lists your uploads
as incoming. Run author commands inside the repository: they act on the
working directory's repository and branch. Then work on a branch, one per
topic. Its name is the stack's topic (a leading `review/` is dropped):

    git switch -c <topic>
    git commit ...                  # gets a Change-Id
    plustwo upload               # PS1 of every change in the stack
    plustwo upload --wip         # or: not ready for review yet, --ready later

Keep one logical change per commit. The reviewer sees the stack commit by
commit.

Uploading copies your stack to the review's own refs, like a push to a
Gerrit server. What the reviewer does there (a rebase, a submit) never
touches your branch. `plustwo pull` brings it in.

## The loop

1. Run `plustwo watch <REPO>...` as a background monitor. It prints one
   short line per event and nothing else:
   - `review <repo> <change>: <n> new` when the reviewer sent comments
   - `fyi <repo> <change> #<n> (resolved) <place>` and the message,
     indented, when the reviewer answered and resolved a thread. Nothing
     to answer, but take it into account.
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
   message. `fyi` lines are the reviewer's replies on resolved threads,
   newer than your last upload or answer: read them, don't answer them.
   `plustwo show <CHANGE>` gives all threads of a change, also of a
   submitted one.
   A thread made on an older patch set than the latest says so, with
   where its line is now. The place and the quote are still the old
   patch set's:

       repo I3f2a #5 (PS2) a.py:44  | same line in the latest patch set
       repo I3f2a #6 (PS2, now :50) a.py:44  | moved to line 50
       repo I3f2a #7 (PS2, edited, now :52) a.py:46  | changed since
       repo I3f2a #8 (PS2, line removed) a.py:59  | gone (also: file gone)
3. Work through all threads of the batch before answering (see below).
4. Fold each fix into the commit it belongs to, not into a new commit:

       git commit --fixup=<commit> ...
       GIT_SEQUENCE_EDITOR=true git rebase -i --autosquash <base>

5. Upload once: `plustwo upload` in the branch. Every changed change of
   the stack gets a new patch set.
6. Then answer all threads in one call with `plustwo answer` (below). Your
   messages are sent immediately, so answering after the upload makes
   "Done" refer to a patch set the reviewer can already see.

## Answering threads

How to answer is your judgment, not a fixed rule. The usual cases:

| Case                         | Code change | Answer line                     |
|------------------------------|-------------|---------------------------------|
| You agree                    | yes         | `I3f2a #1 done`, resolves       |
| You disagree, but it's minor | maybe       | `I3f2a #2: your opinion`        |
| You disagree and it matters  | no          | `I3f2a #3: your reasons`        |
| A question                   | rarely      | `I3f2a #4: the answer`          |

Answer a whole batch in one call, one answer per thread, in inbox's format:

    plustwo answer <<'EOF'
    I3f2a #1 done
    I3f2a #2: Kept the loop, the generator is slower here.
    I3f2a #4 resolve: Yes, it is only called from main.
    EOF

An answer can go over several lines: every line up to the next one that
starts like an answer (Change-Id and `#n`) belongs to it, lists and
blank lines included. Don't start a continuation line with a Change-Id
and `#n` of its own. If a line is wrong, nothing is posted and the error
names every bad line, so fix them all and send the batch again:

    plustwo answer <<'EOF'
    I3f2a #5: Changed it in three places:
    - the parser
    - the tests
    I3f2a #6 done
    EOF

`done: text` resolves with your own text, `resolve: text` too,
`unresolve: text` makes a thread unresolved again. The single commands
`plustwo reply`, `plustwo done` and `plustwo status` still work for one
thread.

- In the second case you may make the change anyway and still say what you
  think. Leave the thread unresolved, so the reviewer decides.
- When you disagree, give reasons: what breaks, what it costs, what the
  alternative is. The reviewer resolves the thread or answers again.
- Answer questions without resolving, unless the answer clearly settles the
  thread.
- Comments on `/COMMIT_MSG:<line>` are about the commit message. Fix them
  with an amend (or `git commit --fixup=amend:<commit>`).

To leave a note or a question of your own for the reviewer, e.g. on a
decision you are unsure about:

    plustwo comment <CHANGE> <path>:<line> "..." --author
    plustwo comment <CHANGE> <path> "..." --author --resolved

## Votes

- If the repository has `checks.command` set (`plustwo config
  checks.command`), every upload starts the check by itself in the
  background and it votes Verified on the top change. Don't start checks
  yourself then, react to a `vote ... Verified-1` line from `watch`: the
  log's end is in `inbox`, the whole log in `.git/plustwo/check.log`.
- Without it, checks (tests, builds) vote Verified on the top change:
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
