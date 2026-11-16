# SPDX-License-Identifier: Zlib
"""The review notes: reading and writing the JSON note of a commit."""

import json
import subprocess

from plustwo.git.refs import NOTES_REF
from plustwo.git.repository import git, git_command


def notes_version(repository=None):
    """Hash of the notes ref. It changes with every write to any note."""
    return git('rev-parse', '--verify', '--quiet', NOTES_REF, check=False, repository=repository)


def read_note(commit, repository=None):
    """The review note of a commit as a dict, empty if it has none."""
    text = git('notes', f'--ref={NOTES_REF}', 'show', commit, check=False, repository=repository)
    return parse_note(text)


def parse_note(text):
    """A review note's text as a dict. A note broken by hand (no JSON, or
    no object) counts as empty, so it can't stop the review of the rest.
    """
    try:
        note = json.loads(text) if text.strip() else {}
    except ValueError:
        return {}
    return note if isinstance(note, dict) else {}


def read_notes(commits, repository=None):
    """The review notes of several commits: commit -> dict, with two git
    calls for all of them.
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


def write_note(commit, note, repository=None):
    """Stores the review note of a commit."""
    git('notes', f'--ref={NOTES_REF}', 'add', '-f', '-F', '-', commit,
        input_text=json.dumps(note, indent=2) + '\n', repository=repository)
