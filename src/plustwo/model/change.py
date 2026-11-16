# SPDX-License-Identifier: Zlib
"""A change, its patch sets, its review and whose turn it is."""

from dataclasses import dataclass, field

from plustwo.model.acknowledgement import says_nothing
from plustwo.model.comment import Comment
from plustwo.model.place import PATCHSET_LEVEL
from plustwo.model.roles import AUTHOR, REVIEWER
from plustwo.model.vote import CODE_REVIEW, DEFAULT_VOTERS, LABEL_RANGES, VERIFIED, Vote, label_name


# Kinds of new patch sets, named like in Gerrit. They decide which votes a
# new patch set keeps.
REWORK = 'rework'


TRIVIAL_REBASE = 'trivial rebase'


NO_CODE_CHANGE = 'no code change'


# Whose turn it is on a change, like Gerrit's attention set
REVIEWER_TURN = 'reviewer'


AUTHOR_TURN = 'author'


SUBMITTABLE = 'submittable'


# A change in work, which gives nobody a turn, like Gerrit's WIP state
WIP_TURN = 'wip'


@dataclass
class Change:
    """A commit with a Change-Id: its review state, threads and patch sets."""
    change_id: str
    commit: str
    subject: str
    topic: str
    comments: list[Comment]
    patch_sets: dict[int, str]
    votes: list[Vote] = field(default_factory=list)
    verified_required: bool = False
    # The reviewer's rebase failed with conflicts, the author has to do it.
    # Kept on the top change of the stack.
    rebase_requested: bool = False
    # The patch set the reviewer's last rebase made, 0 if none
    rebased_patch_set: int = 0
    # Upload times of the patch sets: number -> seconds since 1970
    uploads: dict[int, int] = field(default_factory=dict)
    # Files the reviewer marked reviewed: path -> hash of the file's diff
    reviewed: dict[str, str] = field(default_factory=dict)
    # Who uploaded the patch sets: number -> name, see current_user()
    uploaders: dict[int, str] = field(default_factory=dict)
    # Only for a change found on the base branch by submitted_change()
    submitted: bool = False
    # Work in progress, like Gerrit's WIP: shown, but not to be reviewed
    wip: bool = False
    # The repository's working tree and its name, for showing several
    repository: str | None = None
    repo: str = ''
    # The base branch of the change's stack, see stack_base()
    base: str = ''

    def uploader(self):
        """Who uploaded the latest patch set, '' if unknown."""
        return self.uploaders.get(self.latest_patch_set(), '')

    def sections(self, user):
        """The dashboard sections the change belongs to, for this user."""
        mine = user == self.uploader()
        turn = self.turn()
        sections = []
        if (REVIEWER_TURN == turn and not mine) or (AUTHOR_TURN == turn and mine) or (
                SUBMITTABLE == turn and not mine):
            sections.append('your-turn')
        if any(comment.has_draft() for comment in self.comments):
            sections.append('drafts')
        if self.wip:
            sections.append('wip')
        sections.append('outgoing' if mine else 'incoming')
        return sections

    def comment(self, number):
        """The thread with this number, None if there is none."""
        return next((comment for comment in self.comments if number == comment.number), None)

    def turn(self):
        """Whose turn it is, like Gerrit's attention set: whoever acted last
        hands it to the other side. The reviewer acts by sending and voting,
        the author by uploading and answering. SUBMITTABLE when approved and
        nothing is unresolved.
        """
        if not self.patch_sets:
            return AUTHOR_TURN
        if self.wip:
            return WIP_TURN
        if not self.submit_problems() and not self.unfinished_comments():
            return SUBMITTABLE
        if self.rebase_requested:
            return AUTHOR_TURN
        sent = [message for comment in self.comments for message in comment.messages
                if message.sent]
        reviewer = max([message.time for message in sent if REVIEWER == message.author]
                       + [vote.time for vote in self.votes
                          if REVIEWER == vote.by and not vote.copied_from], default=0)
        author = max([message.time for message in sent if AUTHOR == message.author]
                     + list(self.uploads.values()), default=0)
        if reviewer > author:
            return AUTHOR_TURN
        if reviewer == author == 0 and any(comment.is_unanswered()
                                           for comment in self.unfinished_comments()):
            return AUTHOR_TURN
        return REVIEWER_TURN

    def latest_patch_set(self):
        """Number of the newest patch set, 0 if none is uploaded yet."""
        return max(self.patch_sets, default=0)

    def is_uploaded(self):
        """Whether the current commit is the newest patch set."""
        latest = self.latest_patch_set()
        return 0 != latest and self.patch_sets[latest] == self.commit

    def short_id(self):
        """The Change-Id shortened for display, like I3f2a9c1."""
        return self.change_id[:8]

    def state(self):
        """Review state for display, named like in Gerrit: New, In-Review or
        Submitted. Changes in a stack are never submitted, they leave the
        stack when they are.
        """
        if self.submitted:
            return 'Submitted'
        return 'In-Review' if self.patch_sets else 'New'

    def last_author_action(self):
        """When the author last uploaded or answered, 0 if never."""
        return max([message.time for comment in self.comments for message in comment.messages
                    if message.sent and AUTHOR == message.author]
                   + list(self.uploads.values()), default=0)

    def news(self):
        """Resolved threads whose last message is the reviewer's, newer than
        the author's last upload or answer, and more than an acknowledgement.
        Nothing to answer, but to read once.
        """
        since = self.last_author_action()
        return [comment for comment in self.comments
                if comment.is_unanswered() and not comment.is_unresolved()
                and PATCHSET_LEVEL != comment.place
                and comment.last_sent().time > since
                and not says_nothing(comment.last_sent().text)]

    def unfinished_comments(self):
        """Sent threads that are still unresolved."""
        return [comment for comment in self.comments
                if comment.is_unresolved() and not comment.is_draft()]

    def current_votes(self, label):
        """The votes of a label on the latest patch set."""
        latest = self.latest_patch_set()
        return [vote for vote in self.votes
                if label == vote.label and latest and latest == vote.patch_set]

    def own_vote(self, label=CODE_REVIEW):
        """The value the label's default voter (the reviewer for
        Code-Review) gave on the latest patch set, 0 without a vote.
        """
        by = DEFAULT_VOTERS[label_name(label)]
        return next((vote.value for vote in self.current_votes(label_name(label))
                     if by == vote.by), 0)

    def draft_count(self):
        """How many of the reviewer's drafts sending would send."""
        return sum(not message.sent for comment in self.comments for message in comment.messages)

    def label_value(self, label):
        """A label's summary for display, as Gerrit shows it: the lowest
        value if someone gave it, else the highest if someone gave it, else
        a negative vote before a positive one. 0 without votes.
        """
        values = [vote.value for vote in self.current_votes(label)]
        low, high = LABEL_RANGES[label]
        if not values:
            return 0
        if low in values or high in values:
            return low if low in values else high
        return min(values) if min(values) < 0 else max(values)

    def is_approved(self, label):
        """Whether the latest patch set has the label's highest vote and
        not its lowest, which submitting needs.
        """
        values = [vote.value for vote in self.current_votes(label)]
        low, high = LABEL_RANGES[label]
        return high in values and low not in values

    def submit_problems(self):
        """What keeps this change from being submitted, as messages."""
        name = self.short_id()
        if not self.patch_sets:
            return [f'{name} has no patch set yet, waiting for the author']
        problems = []
        if self.wip:
            problems.append(f'{name} is work in progress, waiting for the author')
        if not self.is_uploaded():
            problems.append(f'{name} changed since its last patch set, waiting for the author')
        labels = [CODE_REVIEW] + ([VERIFIED] if self.verified_required else [])
        for label in labels:
            if not self.is_approved(label):
                value = self.label_value(label)
                problems.append(f'{name} needs {label}{LABEL_RANGES[label][1]:+d}'
                                + (f', it has {label}{value:+d}' if value else '')
                                + (', from a check' if VERIFIED == label else ''))
        return problems
