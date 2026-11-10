# Issues

## Unordered

### `answer`: an answer over several lines fails the whole batch

`plustwo answer` reads one answer per line. When an answer text has a
line break, the next line is rejected ("line 3 is no answer: ...") and
none of the answers of the batch is posted, also not the valid ones
before it. Seen with a long answer that had a list in it.

Proposal: allow continuation lines (e.g. lines that do not start with a
change and thread number belong to the previous answer), or at least
post nothing but name all bad lines at once, so one retry is enough.

### `upload`: a change whose diff only differs in context is "rework"

On the spec stack, a section changed in change N. Change N+1 got its new
patch set labelled "rework", although `rdiff` shows that only the
context lines of its diff moved (e.g. `@@ SPEC.md: <other context>`),
the added lines are the same. The reviewer then looks at a change that
has nothing new.

Proposal: compare the changes without context (e.g. `git patch-id` or
`range-diff` ignoring context) and call such a patch set a "trivial
rebase".
