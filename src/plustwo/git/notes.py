# SPDX-License-Identifier: Zlib
"""The review notes: reading and writing the JSON note of a commit."""

import json
import subprocess
from typing import Any

from plustwo.git.refs import NOTES_REF
from plustwo.git.repository import Repository


def notes_version(repository: Repository) -> str:
    """Reads the version of all review notes.

    Args:
        repository: the repository.

    Returns:
        The object name of the notes ref, which changes with every write to
        any note. '' while there is no note.
    """
    return repository.git('rev-parse', '--verify', '--quiet', NOTES_REF, check=False)


def read_note(repository: Repository, commit: str) -> dict[str, Any]:
    """Reads the review note of a commit.

    Args:
        repository: the repository.
        commit: the commit, any name git understands.

    Returns:
        The note's JSON object, empty if the commit has no note.
    """
    text = repository.git('notes', f'--ref={NOTES_REF}', 'show', commit, check=False)
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


def read_notes(repository: Repository, commits: list[str]) -> dict[str, dict[str, Any]]:
    """Reads the review notes of several commits, with two git calls for all
    of them.

    Args:
        repository: the repository.
        commits: the commits' full object names.

    Returns:
        Each commit that has a note, mapped to the note's JSON object.
    """
    if not commits:
        return {}
    blobs = {}
    output = repository.git('notes', f'--ref={NOTES_REF}', 'list', check=False)
    for line in filter(None, output.split('\n')):
        blob, commit = line.split(' ')
        blobs[commit] = blob
    wanted = [(commit, blobs[commit]) for commit in commits if commit in blobs]
    if not wanted:
        return {}
    command = [*repository.git_command(), 'cat-file', '--batch']
    data = subprocess.run(command, input=''.join(f'{blob}\n' for _, blob in wanted).encode(),
                          capture_output=True, check=True).stdout
    notes = {}
    for commit, _ in wanted:
        header, data = data.split(b'\n', 1)
        size = int(header.split()[2])
        notes[commit] = parse_note(data[:size].decode('utf-8', errors='replace'))
        data = data[size + 1:]
    return notes


def write_note(repository: Repository, commit: str, note: dict[str, Any]) -> None:
    """Stores the review note of a commit, replacing the one it had.

    Args:
        repository: the repository.
        commit: the commit, any name git understands.
        note: the note's JSON object.
    """
    repository.git('notes', f'--ref={NOTES_REF}', 'add', '-f', '-F', '-', commit,
                   input_text=json.dumps(note, indent=2) + '\n')
