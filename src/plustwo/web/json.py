# SPDX-License-Identifier: Zlib
"""What the API sends: the core's objects as JSON."""

from plustwo.model.message import Message
from plustwo.model.place import place_label
from plustwo.model.vote import CODE_REVIEW, VERIFIED
from plustwo.review.labels import comment_counts, message_text, vote_label
from plustwo.review.last_send import last_send
from plustwo.review.moves import move_text


def message_json(message, patch_set=None):
    """A message for the page: text to edit, shown as the core shows it."""
    return {'author': message.author, 'text': message.text, 'shown': message_text(message),
            'sent': message.sent, 'unresolved': message.sets_unresolved(), 'patch_set': patch_set}


def comment_json(comment, patch_sets=()):
    """A thread for the page. patch_sets are its messages' patch sets, see
    message_patch_sets().
    """
    path, _, line = comment.place.rpartition(':')
    if not path or not line.isdigit():
        path, line = comment.place, ''
    return {'number': comment.number, 'id': comment.id, 'place': comment.place,
            'place_label': comment.place_label(), 'path': path,
            'line': int(line) if line else None, 'side': comment.side,
            'patch_set': comment.patch_set, 'status': comment.status,
            'draft_status': comment.draft_status(), 'is_draft': comment.is_draft(),
            'has_draft': comment.has_draft(),
            'messages': [message_json(message,
                                      patch_sets[index] if index < len(patch_sets) else None)
                         for index, message in enumerate(comment.messages)],
            'moves': moves_json(comment)}


def moves_json(comment):
    """A thread's moves by hand for the page: the text and how many of its
    messages come before it, see Comment.entries().
    """
    moves, before = [], 0
    for entry in comment.entries():
        if isinstance(entry, Message):
            before += 1
        else:
            moves.append({'shown': move_text(entry), 'after': before})
    return moves


def history_list(history):
    """patch_set_history() for the page, the reviewer's newest send
    marked on its last event (last_send), see last_send().
    """
    last = last_send(history)
    result = [history_json(entry) for entry in history]
    for entry in result:
        for index, event in enumerate(entry['events']):
            event['last_send'] = (entry['number'], index) == last
        entry['last_send'] = bool(last) and last[0] == entry['number']
    return result


def history_json(entry):
    """One patch set of patch_set_history() for the page."""
    return {'number': entry.number, 'uploaded': entry.uploaded, 'uploader': entry.uploader,
            'kind': entry.kind, 'code_review': entry.vote_label(CODE_REVIEW),
            'verified': entry.vote_label(VERIFIED), 'comments': entry.comments_label(),
            'open': len(entry.open_threads()),
            'events': [{'kind': event.kind, 'time': event.time, 'author': event.author,
                        'text': event.text, 'number': event.number, 'place': event.place,
                        'place_label': place_label(event.place),
                        'status': event.status, 'draft': event.draft, 'open': event.is_open(),
                        'yours': event.is_yours()}
                       for event in entry.events]}


def vote_json(vote):
    return {'label': vote.label, 'value': vote.value, 'by': vote.by,
            'patch_set': vote.patch_set, 'message': vote.message,
            'copied_from': vote.copied_from}


def change_summary(change, repository):
    return {'id': change.change_id, 'short_id': change.short_id(), 'subject': change.subject,
            'topic': change.topic, 'base': change.base, 'state': change.state(),
            'patch_set': change.latest_patch_set(), 'turn': change.turn(),
            'code_review': vote_label(change, CODE_REVIEW),
            'verified': vote_label(change, VERIFIED),
            'comments': comment_counts(change), 'wip': change.is_wip,
            'repo_name': change.repository_name}


def half_json(half, colours):
    return {'kind': half.kind, 'number': half.number, 'text': half.text, 'edits': half.edits,
            'moved': half.moved, 'whole': half.whole, 'colours': colours}
