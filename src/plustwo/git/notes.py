# SPDX-License-Identifier: Zlib
"""The review notes: reading and writing the JSON note of a commit."""

import json
import subprocess
from typing import Any

from plustwo.git.refs import NOTES_REF
from plustwo.git.repository import git, git_command


def notes_version(repository: str | None = None) -> str:
    """Reads the version of all review notes.

    Args:
        repository: the repository's directory, None for the current one.

    Returns:
        The object name of the notes ref, which changes with every write to
        any note. '' while there is no note.
    """
    return git('rev-parse', '--verify', '--quiet', NOTES_REF, check=False, repository=repository)


def read_note(commit: str, repository: str | None = None) -> dict[str, Any]:
    """Reads the review note of a commit.

    Args:
        commit: the commit, any name git understands.
        repository: the repository's directory, None for the current one.

    Returns:
        The note's JSON object, empty if the commit has no note.
    """
    text = git('notes', f'--ref={NOTES_REF}', 'show', commit, check=False, repository=repository)
    return parse_note(text)


def parse_note(text: str) -> dict[str, Any]:
    """Parses the text of a review note.

    Args:
        text: the note's text, JSON.

    Returns:
        The note's JSON object. A note broken by hand (no JSON, or no
        object) counts as empty, so it can't stop the review of the rest.
    """
    try:
        note = json.loads(text) if text.strip() else {}
    except ValueError:
        return {}
    return note if isinstance(note, dict) else {}


def read_notes(commits: list[str], repository: str | None = None) -> dict[str, dict[str, Any]]:
    """Reads the review notes of several commits, with two git calls for all
    of them.

    Args:
        commits: the commits' full object names.
        repository: the repository's directory, None for the current one.

    Returns:
        Each commit that has a note, mapped to the note's JSON object.
    """
    if not commits:
        return {}
    blobs = {}
    output = git('notes', f'--ref={NOTES_REF}', 'list', check=False, repository=repository)
    for line in filter(None, output.split('\n')):
        blob, commit = line.split(' ')
        blobs[commit] = blob
    wanted = [(commit, blobs[commit]) for commit in commits if commit in blobs]
    if not wanted:
        return {}
    command = [*git_command(repository), 'cat-file', '--batch']
    data = subprocess.run(command, input=''.join(f'{blob}\n' for _, blob in wanted).encode(),
                          capture_output=True, check=True).stdout
    notes = {}
    for commit, _ in wanted:
        header, data = data.split(b'\n', 1)
        size = int(header.split()[2])
        notes[commit] = parse_note(data[:size].decode('utf-8', errors='replace'))
        data = data[size + 1:]
    return notes


def write_note(commit: str, note: dict[str, Any], repository: str | None = None) -> None:
    """Stores the review note of a commit, replacing the one it had.

    Args:
        commit: the commit, any name git understands.
        note: the note's JSON object.
        repository: the repository's directory, None for the current one.
    """
    git('notes', f'--ref={NOTES_REF}', 'add', '-f', '-F', '-', commit,
        input_text=json.dumps(note, indent=2) + '\n', repository=repository)
