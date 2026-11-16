# SPDX-License-Identifier: Zlib
"""Paths of the installation: the launcher and the files next to it.

The package lives in src/plustwo below the directory of the launcher, in
the checkout and in an installation alike. Links to the launcher are
resolved, so the files are found from wherever it is called.
"""

from pathlib import Path

# The directory of the launcher: src/plustwo/paths.py is three levels below.
INSTALL_DIRECTORY = Path(__file__).resolve().parent.parent.parent
# The launcher, which checks run in a process of their own
LAUNCHER = INSTALL_DIRECTORY / 'plustwo'
# The guide for agents, printed by plustwo guide
GUIDE = INSTALL_DIRECTORY / 'AGENTS.md'
