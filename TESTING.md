# Trying plustwo by hand

A walkthrough in a throwaway repository: you as the reviewer in the
interface, a second terminal as the author. If `plustwo` isn't on PATH,
run `make link PREFIX=~/.local` here first, or use `git change` instead.

## Setup

In terminal 2 (the author), paste:

    cd "$(mktemp -d)" && git init -q -b main && pwd
    seq 1 60 | sed 's/^/line /' > a.txt && printf 'x = 1\n' > b.py
    git add . && git commit -qm 'Initial commit'
    plustwo init
    git switch -qc feature
    sed -i 's/^line 30$/line thirty/' a.txt && git commit -qam 'Spell out thirty'
    printf 'x = 2\ny = 3\n' > b.py && git commit -qam 'Change b.py'
    plustwo upload
    plustwo watch . &

In terminal 1 (you), `cd` to the printed directory and start `plustwo`.

## As the reviewer

| #   | Do                                                     | Expect                                           |
|-----|--------------------------------------------------------|--------------------------------------------------|
| 1   | look at the list                                       | one stack `feature`, two changes, Turn `▶ you`   |
| 2   | `l` on the first change                                | files left, side by side diff right              |
| 3   | `j` to a.txt, `l` into the diff                        | a gap row `26 common lines`, then line 30        |
| 4   | `c` on the gap row                                     | the gap opens                                    |
| 5   | `]c`, then `c`, write "Why words?", Enter              | a draft thread under line 30                     |
| 6   | `<`, `c`, write "old side", Enter                      | a second draft, marked `(old)`                   |
| 7   | `m`                                                    | `✓` before a.txt, title `1/2 reviewed`           |
| 8   | `W` a few times                                        | the title shows the whitespace mode              |
| 9   | `s`, write "Looks good otherwise", Enter               | drafts sent. Terminal 2 prints `review ...`      |
| 9a  | `]u` a few times                                       | goes through the unresolved threads of all files |
| 10  | `h`, `h` back to the list                              | Turn of the first change is now `author`         |

## As the author

In terminal 2:

| #   | Do                                                         | Expect                                     |
|-----|------------------------------------------------------------|--------------------------------------------|
| 11  | `plustwo inbox`                                            | both threads and the change message        |
| 12  | `plustwo reply 1 1 "The spec says so."`                    | the thread stays unresolved                |
| 13  | `plustwo done 1 2`                                         | the second thread is resolved              |
| 14  | `plustwo guide \| head -20`                                | the agent guide                            |

## Back as the reviewer

| #   | Do                                                     | Expect                                           |
|-----|--------------------------------------------------------|--------------------------------------------------|
| 15  | wait a second                                          | the list reloads, Turn `▶ you` again             |
| 15a | open the change, `p`, look, `h`                        | `── your last send ──`, author's replies below   |
| 15b | `g l`                                                  | `Your last send is on the latest patch set.`     |
| 15c | `F`                                                    | `Follow latest: on, ...`                         |
| 16  | open the change, go to thread #1, `Y`, `s`, Enter      | Ack sent, the thread is resolved                 |
| 17  | `s`, `Tab`, `2`, Enter                                 | `CR +2` badge, Turn `▶ submit`                   |
| 18  | `S`, `y`                                               | submitted. Terminal 2 prints `submitted ...`     |
| 19  | in terminal 2: `plustwo pull`                          | the branch now starts after the submitted change |
| 20  | `?` in the interface                                   | all keys, `q` quits                              |

To try other keys: `plustwo config --global keys.preset emacs` (or
`gerrit`), then restart the interface. `plustwo config --global --unset
keys.preset` goes back to vim keys. Stop the watcher with `kill %1`.

## In the browser

Repeat the setup in a new throwaway repository, then:

| #   | Do                                                  | Expect                                       |
|-----|-----------------------------------------------------|----------------------------------------------|
| 21  | terminal 1: `plustwo serve .`, open the address     | the dashboard with both changes              |
| 22  | click "Spell out thirty"                            | file list left, side by side diff right      |
| 23  | click the new side's number of line 30, write, Save | a draft thread under the line                |
| 24  | Reply, pick `+1`, Send                              | the draft is sent, `CR +1` badge             |
| 25  | terminal 2: `plustwo reply 1 1 'ok'`                | within a second the answer shows in the page |
| 26  | Done on the thread, Reply, Send                     | the thread is resolved                       |
| 27  | tick the file's checkbox, then +2                   | `1/2 reviewed`, `CR +2`, `submittable`       |
| 28  | `?`                                                 | the keys of your preset                      |

Stop the server with Ctrl-C.

## Several repositories and checks

Make a second throwaway repository like the first one, then:

| #   | Do                                                      | Expect                                       |
|-----|---------------------------------------------------------|----------------------------------------------|
| 29  | `plustwo repos`                                         | both repositories are registered (by `init`) |
| 30  | in the second: `PLUSTWO_USER=agent plustwo upload --wip` | "work in progress"                          |
| 31  | `cd ~ && plustwo`                                       | changes of both, the WIP one dimmed          |
| 32  | Enter on a section heading, then `J`                    | it folds, then moves down. Kept on restart   |
| 33  | `plustwo config checks.command true`, upload again      | a moment later the change shows `V +1`       |
