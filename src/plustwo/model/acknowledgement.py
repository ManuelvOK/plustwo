# SPDX-License-Identifier: Zlib
"""Messages that only acknowledge."""

import re


# Replies that only acknowledge, compared without case and end punctuation.
# A reviewer's reply like this resolves its thread, as the Ack key does.
ACKNOWLEDGEMENTS = {'ack', 'acked', 'acknowledged', 'ok', 'okay', 'k', 'thanks', 'thank you',
                    'thx', 'ty', 'done', 'lgtm', 'fine', 'good', 'great', 'perfect', 'sure',
                    'got it', 'makes sense', 'ok thanks', 'ok, thanks', 'sounds good'}


def is_acknowledgement(text):
    """Whether a message only acknowledges, like "Ack." or "OK, thanks!":
    nothing but acknowledgements, punctuation and emoji, "Ack, thanks 👍"
    and "Got it. LGTM" too.
    """
    words = re.findall(r"[\w']+", text.lower())
    # Which starts of the words can be split into acknowledgements
    can_end = [True] + [False] * len(words)
    for end in range(1, len(words) + 1):
        can_end[end] = any(can_end[begin] and ' '.join(words[begin:end]) in ACKNOWLEDGEMENTS
                           for begin in range(max(0, end - 3), end))
    return bool(words) and can_end[-1]


def says_nothing(text):
    """Whether a message has nothing to read: empty, as when it only
    resolves its thread, or only an acknowledgement.
    """
    return not text.strip() or is_acknowledgement(text)
