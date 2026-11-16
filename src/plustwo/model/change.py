# SPDX-License-Identifier: Zlib
"""A change, its patch sets, its review and whose turn it is."""

from dataclasses import dataclass, field

from plustwo.model.acknowledgement import says_nothing
from plustwo.model.comment import Comment
from plustwo.model.place import PATCHSET_LEVEL
from plustwo.model.roles import AUTHOR, REVIEWER
from plustwo.model.vote import CODE_REVIEW, DEFAULT_VOTERS, LABEL_RANGES, VERIFIED, Vote, label_name


# Types of new patch sets, named like in Gerrit. They decide which votes a
# new patch set keeps.
REWORK = 'rework'
TRIVIAL_REBASE = 'trivial rebase'
NO_CODE_CHANGE = 'no code change'

# Whose turn it is on a change, like Gerrit's attention set, see
# Change.turn()
REVIEWER_TURN = 'reviewer'
AUTHOR_TURN = 'author'
SUBMITTABLE = 'submittable'
# A change in work, which gives nobody a turn, like Gerrit's WIP state
WIP_TURN = 'wip'


@dataclass
class Change:
    """A commit with a Change-Id: its review state, threads and patch sets.

    Attributes:
        change_id: the Change-Id, like I3f2a....
        commit: the change's commit as it is now.
        subject: the commit message's first line.
        topic: the topic of the change's stack.
        comments: the threads, by number.
        patch_sets: each patch set's number, mapped to its commit.
        votes: the votes on all patch sets.
        needs_verified: whether submitting needs Verified+1 (note key
            verified_required).
        rebase_is_requested: whether the reviewer's rebase failed with
            conflicts, so the author has to do it. Kept on the top change of
            the stack (note key rebase_requested).
        rebased_patch_set: the patch set the reviewer's last rebase made, 0
            if none.
        uploads: each patch set's number, mapped to its upload time in
            seconds since 1970.
        reviewed: each file the reviewer marked reviewed, mapped to a hash
            of the file's diff.
        uploaders: each patch set's number, mapped to who uploaded it, see
            current_user().
        is_submitted: whether the change was found on the base branch by
            submitted_change(), not in a stack.
        is_wip: whether the change is work in progress, like Gerrit's WIP:
            shown, but not to be reviewed (note key wip).
        repository: the repository's working tree, None for the current
            one.
        repository_name: the repository's registered name, for showing
            changes of several repositories.
        base: the base branch of the change's stack, see stack_base().
    """
    change_id: str
    commit: str
    subject: str
    topic: str
    comments: list[Comment]
    patch_sets: dict[int, str]
    votes: list[Vote] = field(default_factory=list)
    needs_verified: bool = False
    rebase_is_requested: bool = False
    rebased_patch_set: int = 0
    uploads: dict[int, int] = field(default_factory=dict)
    reviewed: dict[str, str] = field(default_factory=dict)
    uploaders: dict[int, str] = field(default_factory=dict)
    is_submitted: bool = False
    is_wip: bool = False
    repository: str | None = None
    repository_name: str = ''
    base: str = ''

    def uploader(self) -> str:
        """Finds who uploaded the latest patch set.

        Returns:
            The uploader's name, '' if unknown.
        """
        return self.uploaders.get(self.latest_patch_set(), '')

    def sections(self, user: str) -> list[str]:
        """Finds the dashboard sections the change belongs to.

        Args:
            user: the user the dashboard is for, see current_user().

        Returns:
            The sections' names, see SECTION_TITLES.
        """
        is_users_change = user == self.uploader()
        turn = self.turn()
        sections = []
        is_users_turn = (REVIEWER_TURN == turn and not is_users_change) \
            or (AUTHOR_TURN == turn and is_users_change) \
            or (SUBMITTABLE == turn and not is_users_change)
        if is_users_turn:
            sections.append('your-turn')
        if any(comment.has_draft() for comment in self.comments):
            sections.append('drafts')
        if self.is_wip:
            sections.append('wip')
        sections.append('outgoing' if is_users_change else 'incoming')
        return sections

    def comment(self, number: int) -> Comment | None:
        """Finds a thread by its number.

        Args:
            number: the thread's number.

        Returns:
            The thread, None if there is none with that number.
        """
        return next((comment for comment in self.comments if number == comment.number), None)

    def turn(self) -> str:
        """Finds whose turn it is, like Gerrit's attention set: whoever acted
        last hands it to the other side. The reviewer acts by sending and
        voting, the author by uploading and answering.

        Returns:
            REVIEWER_TURN, AUTHOR_TURN, WIP_TURN, or SUBMITTABLE when the
            change is approved and nothing is unresolved.
        """
        if not self.patch_sets:
            return AUTHOR_TURN
        if self.is_wip:
            return WIP_TURN
        if not self.submit_problems() and not self.unfinished_comments():
            return SUBMITTABLE
        if self.rebase_is_requested:
            return AUTHOR_TURN
        sent = [message for comment in self.comments for message in comment.messages
                if message.sent]
        reviewer_time = max([message.time for message in sent if REVIEWER == message.author]
                            + [vote.time for vote in self.votes
                               if REVIEWER == vote.by and not vote.copied_from], default=0)
        author_time = max([message.time for message in sent if AUTHOR == message.author]
                          + list(self.uploads.values()), default=0)
        if reviewer_time > author_time:
            return AUTHOR_TURN
        # Old notes have no times: unanswered threads still ask the author.
        if 0 == reviewer_time == author_time and any(comment.is_unanswered()
                                                     for comment in self.unfinished_comments()):
            return AUTHOR_TURN
        return REVIEWER_TURN

    def latest_patch_set(self) -> int:
        """Finds the newest patch set.

        Returns:
            The patch set's number, 0 if none is uploaded yet.
        """
        return max(self.patch_sets, default=0)

    def is_uploaded(self) -> bool:
        """Tells whether the current commit is the newest patch set.

        Returns:
            True if the change is uploaded as it is.
        """
        latest = self.latest_patch_set()
        return 0 != latest and self.patch_sets[latest] == self.commit

    def short_id(self) -> str:
        """Shortens the Change-Id for display.

        Returns:
            The first 8 characters, like I3f2a9c1.
        """
        return self.change_id[:8]

    def state(self) -> str:
        """Names the review state for display, like Gerrit. Changes in a
        stack are never submitted, they leave the stack when they are.

        Returns:
            New, In-Review or Submitted.
        """
        if self.is_submitted:
            return 'Submitted'
        return 'In-Review' if self.patch_sets else 'New'

    def last_author_action(self) -> int:
        """Finds when the author last uploaded or answered.

        Returns:
            The time in seconds since 1970, 0 if never.
        """
        return max([message.time for comment in self.comments for message in comment.messages
                    if message.sent and AUTHOR == message.author]
                   + list(self.uploads.values()), default=0)

    def news(self) -> list[Comment]:
        """Finds the threads the author has nothing to answer in, but should
        read once.

        Returns:
            Resolved threads whose last message is the reviewer's, newer
            than the author's last upload or answer, and more than an
            acknowledgement.
        """
        since = self.last_author_action()
        news = []
        for comment in self.comments:
            last_sent = comment.last_sent()
            if last_sent is None or not comment.is_unanswered() or comment.is_unresolved():
                continue
            if PATCHSET_LEVEL != comment.place and last_sent.time > since \
                    and not says_nothing(last_sent.text):
                news.append(comment)
        return news

    def unfinished_comments(self) -> list[Comment]:
        """Finds the sent threads that are still unresolved.

        Returns:
            The threads.
        """
        return [comment for comment in self.comments
                if comment.is_unresolved() and not comment.is_draft()]

    def current_votes(self, label: str) -> list[Vote]:
        """Finds the votes of a label on the latest patch set.

        Args:
            label: the label, CODE_REVIEW or VERIFIED.

        Returns:
            The votes.
        """
        latest = self.latest_patch_set()
        return [vote for vote in self.votes
                if label == vote.label and latest and latest == vote.patch_set]

    def own_vote(self, label: str = CODE_REVIEW) -> int:
        """Finds the vote the label's default voter (the reviewer for
        Code-Review) gave on the latest patch set.

        Args:
            label: the label or a name for it, see label_name().

        Returns:
            The vote's value, 0 without a vote.
        """
        voter = DEFAULT_VOTERS[label_name(label)]
        return next((vote.value for vote in self.current_votes(label_name(label))
                     if voter == vote.by), 0)

    def n_drafts(self) -> int:
        """Counts the reviewer's drafts, which sending would send.

        Returns:
            The number of drafts.
        """
        return sum(not message.sent for comment in self.comments for message in comment.messages)

    def label_value(self, label: str) -> int:
        """Sums up a label's votes for display, as Gerrit shows them.

        Args:
            label: the label, CODE_REVIEW or VERIFIED.

        Returns:
            The lowest value if someone gave it, else the highest if someone
            gave it, else a negative vote before a positive one. 0 without
            votes.
        """
        values = [vote.value for vote in self.current_votes(label)]
        lowest, highest = LABEL_RANGES[label]
        if not values:
            return 0
        if lowest in values:
            return lowest
        if highest in values:
            return highest
        return min(values) if min(values) < 0 else max(values)

    def is_approved(self, label: str) -> bool:
        """Tells whether the latest patch set has the label's highest vote
        and not its lowest, which submitting needs.

        Args:
            label: the label, CODE_REVIEW or VERIFIED.

        Returns:
            True if the label approves the change.
        """
        values = [vote.value for vote in self.current_votes(label)]
        lowest, highest = LABEL_RANGES[label]
        return highest in values and lowest not in values

    def submit_problems(self) -> list[str]:
        """Finds what keeps this change from being submitted.

        Returns:
            A message per problem, empty if the change can be submitted.
        """
        name = self.short_id()
        if not self.patch_sets:
            return [f'{name} has no patch set yet, waiting for the author']
        problems = []
        if self.is_wip:
            problems.append(f'{name} is work in progress, waiting for the author')
        if not self.is_uploaded():
            problems.append(f'{name} changed since its last patch set, waiting for the author')
        labels = [CODE_REVIEW] + ([VERIFIED] if self.needs_verified else [])
        for label in labels:
            if not self.is_approved(label):
                value = self.label_value(label)
                problems.append(f'{name} needs {label}{LABEL_RANGES[label][1]:+d}'
                                + (f', it has {label}{value:+d}' if value else '')
                                + (', from a check' if VERIFIED == label else ''))
        return problems
