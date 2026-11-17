# SPDX-License-Identifier: Zlib
"""Where a file of one version came from in another: the one answer the
file list, the diff, the counts and the porting of threads all use."""

from pathlib import PurePosixPath

from plustwo.diff.substitutions import BARE_LINES, kept_lines, name_variants
from plustwo.git.cache import cached_for_objects
from plustwo.git.files import file_lines
from plustwo.git.repository import Repository

# A file pair needs this many kept lines that aren't bare to be a rename
_MIN_KEPT_LINES = 3

# Above this many pairs of a gone and an added file, only git's rename
# detection counts: comparing all of them would take too long.
_MAX_PAIRS = 400


@cached_for_objects(1, 2)
def file_sources(repository: Repository, old: str, new: str) -> dict[str, str]:
    """Finds the files renamed between two versions.

    git's rename detection comes first. It measures bytes, so a file whose
    name is on most of its lines, like a class renamed with its file, is
    too different for it. Of the files git leaves gone and added, a pair
    is a rename too when the added file keeps more than half of the gone
    one's lines after the recurring substitutions between them, see
    kept_lines(). Each file is in one pair at most, the closest first.

    Args:
        repository: the repository.
        old: the old version, a commit or a parent like abc^.
        new: the new version.

    Returns:
        Each path in new, mapped to its path in old.
    """
    # check=False: the parent of a root commit has no files.
    fields = repository.git('diff-tree', '-M', '-r', '--name-status', '-z', old, new,
                            '--', check=False).split('\0')
    sources: dict[str, str] = {}
    added: list[str] = []
    gone: list[str] = []
    index = 0
    while index < len(fields) and fields[index]:
        status = fields[index]
        if status.startswith(('R', 'C')):
            sources[fields[index + 2]] = fields[index + 1]
            index += 3
            continue
        if 'A' == status:
            added.append(fields[index + 1])
        elif 'D' == status:
            gone.append(fields[index + 1])
        index += 2
    if not added or not gone or len(added) * len(gone) > _MAX_PAIRS:
        return sources
    pairs = []
    for gone_path in gone:
        old_lines = file_lines(repository, old, gone_path)
        n_real = sum(1 for line in old_lines or () if line.strip() not in BARE_LINES)
        if n_real < _MIN_KEPT_LINES:
            continue
        for added_path in added:
            new_lines = file_lines(repository, new, added_path)
            if new_lines is None:
                continue
            names = name_variants(PurePosixPath(gone_path).stem, PurePosixPath(added_path).stem)
            n_kept = kept_lines(old_lines or [], new_lines, names)
            if n_kept >= _MIN_KEPT_LINES and 2 * n_kept > n_real:
                pairs.append((n_kept / n_real, n_kept, gone_path, added_path))
    for _share, _n_kept, gone_path, added_path in sorted(pairs, reverse=True):
        if added_path not in sources and gone_path not in sources.values():
            sources[added_path] = gone_path
    return sources


def path_in(repository: Repository, old: str, new: str, path: str) -> str | None:
    """Finds the path a file of one version has in another.

    Args:
        repository: the repository.
        old: the version the file is in.
        new: the other version.
        path: the file's path in old.

    Returns:
        The same path if new has it, else where file_sources() finds it.
        None if the file is gone.
    """
    if repository.git('rev-parse', '--verify', '--quiet', f'{new}:{path}', check=False):
        return path
    return next((target for target, source in file_sources(repository, old, new).items()
                 if source == path), None)


def own_sources(repository: Repository, commit: str) -> dict[str, str]:
    """Finds the files a commit renamed against its parent.

    Args:
        repository: the repository.
        commit: the commit.

    Returns:
        Each path in commit, mapped to its path in the parent.
    """
    return file_sources(repository, f'{commit}^', commit)
