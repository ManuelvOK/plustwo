# SPDX-License-Identifier: Zlib
"""The error of every refused action, raised from git upwards."""


class ChangeError(Exception):
    """An error the user can fix, shown without a traceback."""
