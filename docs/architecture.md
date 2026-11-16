# plustwo architecture

How the code of plustwo is laid out and which part may use which. What
plustwo does is in the README, every command in `src/plustwo/cli/main.py`.

## Layers

One Python package, `src/plustwo/`, without dependencies beyond the standard
library and git. Each layer is a sub-package `src/plustwo/<layer>/`, and each
file in it holds one concept, named after it.

| Layer    | Contains                                                              | Uses                     |
|----------|-----------------------------------------------------------------------|--------------------------|
| `git`    | running git, the object cache, refs, commits, files, notes, the lock  | nothing                  |
| `config` | settings, state files, registered repositories, the user, key presets | git                      |
| `model`  | changes, threads, messages, moves, votes, places, roles               | git                      |
| `diff`   | edits, side by side rows, intraline edits, moved lines, colours       | git                      |
| `review` | finding, uploading, threads, answers, votes, porting, submit, checks  | git, config, model, diff |
| `author` | the inbox and watch                                                   | review                   |
| `tui`    | the curses interface                                                  | review                   |
| `web`    | the web interface: HTTP server, JSON API, the page in `page/`         | review                   |
| `cli`    | the command line, which also starts the two interfaces                | author, tui, web         |

"Uses" lists the direct dependencies. What those use may be used too.

Rules:

1. **Dependencies point downwards only.** A layer imports only what its
   "Uses" column allows. `make check` enforces it through
   `tools/check_layers.py`, which reads the table above.
2. **All review logic lives in plain functions below the interfaces.** The
   command line, the curses interface and the web interface are thin: they
   call `review` and the layers below it and show the results. Logic needed
   by two interfaces moves down, like the key presets into `config`.
3. Modules at the top of the package, like `paths.py`, use only the standard
   library. Every layer may use them.
4. `cli` imports `tui` and `web` inside the functions that start them, so a
   plain command loads neither curses nor the HTTP server.
5. Imports name the module a name lives in: `from plustwo.review.threads
   import add_message`. Package `__init__.py` files hold only the layer's
   description. A module global that is rebound at run time, like the
   terminal's background in `tui/colours.py`, is read through its module
   (`colours.TERMINAL_BACKGROUND`), so every reader sees the new value.

## Starting

`plustwo` at the top of the checkout is a small launcher. It puts the `src/`
next to its own real path on `sys.path`, so links to it (`~/.local/bin/plustwo`,
`git-change`) work, and calls `plustwo.cli.main.main()`. `make install` copies
the launcher, `src/` and `AGENTS.md` to one directory and links the launcher
into the bin directory under both names.
