# SPDX-License-Identifier: Zlib
"""Messages that only acknowledge."""

import re


# Replies that only acknowledge, compared without case and punctuation. A
# reviewer's reply like this resolves its thread, as the Ack key does.
ACKNOWLEDGEMENTS = {'ack', 'acked', 'acknowledged', 'ok', 'okay', 'k', 'thanks', 'thank you',
                    'thx', 'ty', 'done', 'lgtm', 'fine', 'good', 'great', 'perfect', 'sure',
                    'got it', 'makes sense', 'ok thanks', 'ok, thanks', 'sounds good'}

# The most words an acknowledgement in ACKNOWLEDGEMENTS has
_MAX_ACKNOWLEDGEMENT_WORDS = 3


def is_acknowledgement(text: str) -> bool:
    """Tells whether a message only acknowledges, like "Ack." or "OK,
    thanks!": nothing but acknowledgements, punctuation and emoji, so "Ack,
    thanks 👍" and "Got it. LGTM" count too.

    Args:
        text: the message's text.

    Returns:
        True if the text is a sequence of acknowledgements, False for an
        empty text.
    """
    words = re.findall(r"[\w']+", text.lower())
    # Whether the first n words can be split into acknowledgements, for
    # each n
    can_end = [True] + [False] * len(words)
    for end in range(1, len(words) + 1):
        can_end[end] = any(can_end[begin] and ' '.join(words[begin:end]) in ACKNOWLEDGEMENTS
                           for begin in range(max(0, end - _MAX_ACKNOWLEDGEMENT_WORDS), end))
    return bool(words) and can_end[-1]


def says_nothing(text: str) -> bool:
    """Tells whether a message has nothing to read.

    Args:
        text: the message's text.

    Returns:
        True for an empty text, as when a message only resolves its thread,
        and for an acknowledgement.
    """
    return not text.strip() or is_acknowledgement(text)
