# Issues

## Unordered

### Work in progress is set per stack, not per change

`upload --wip` and `upload --ready` mark every change of the stack. The
author cannot mark the lower changes of a stack as ready for review and
keep the changes above them in progress.

Seen with the topic `bid-perf`: the 4 lowest changes were ready, the 15
changes above them were still being measured. To get the 4 changes
reviewed, the author moved the branch to the 4th change and uploaded
only those. The other 15 changes left the uploaded stack, so the
reviewer no longer sees them at all.

Proposal: `upload --ready <CHANGE>` marks the given change and the
changes below it as ready and leaves the changes above it as they are.
`--wip <CHANGE>` marks the given change and the changes above it.

### `watch` crashes when a change is in several stacks

`plustwo watch` stops with `IndexError: list index out of range` in
`event_time()` (and in `report_events()`).

The same commit of a change can be the base of several branches, and
each branch is read as its own stack. Only one of these copies has the
messages of the threads. `watch()` builds `by_id` as
`{change.change_id: change}` over all of them, so the last copy wins.
An event from `events_of()` then names a message index the copy in
`by_id` doesn't have.

Seen in claudel4: private/master was rebased, the old commit of
I5e74fd9 stayed the base of `bid-perf-rev`, `spectest` and 18 `j8/*`
branches. Thread #6 has 3 messages in one copy and 0 in the others.

Proposal: `by_id` keeps one entry per Change-Id with the messages, or
`events_of()` returns events per change object instead of per
Change-Id.
