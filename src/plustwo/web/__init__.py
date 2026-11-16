# SPDX-License-Identifier: Zlib
"""Web interface of plustwo, for the reviewer. Started by `plustwo serve`.

A thin frontend like the terminal interface: a small HTTP server on
127.0.0.1 that serves the page in page/ and a JSON API over the core's
functions. Nothing of the review logic lives here.

Any web page open in the browser could send requests to 127.0.0.1. So the
server makes a random token at start, puts it into the page it serves and
refuses API requests without it, and it refuses requests whose Host or
Origin is not its own address.
"""
