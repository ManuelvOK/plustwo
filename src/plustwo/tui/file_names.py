# SPDX-License-Identifier: Zlib
"""The change screen's names of files, and the file a thread's diff is in."""

from plustwo.model.place import COMMIT_MSG, PATCHSET_LEVEL


def file_label(path: str, renames: dict[str, str] | None = None) -> str:
    """Names a file for display.

    Args:
        path: the file's path, COMMIT_MSG for the commit message.
        renames: each renamed file's new path, mapped to its old one. None
            for none.

    Returns:
        The path, the old path too for a renamed file, "Commit message" for
        the commit message.
    """
    if COMMIT_MSG == path:
        return 'Commit message'
    return f'{renames[path]} → {path}' if renames and path in renames else path


def diff_path(path: str) -> str:
    """Finds the file whose diff shows the threads on a path: threads on the
    whole change go on top of the commit message, as threads on a whole file
    go on top of their file.

    Args:
        path: the threads' path.

    Returns:
        The file's path.
    """
    return COMMIT_MSG if PATCHSET_LEVEL == path else path
