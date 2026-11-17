# SPDX-License-Identifier: Zlib
"""plustwo: Gerrit-like review of local commits, with patch sets and notes.

A change is a commit under review. Like in Gerrit, it is identified by the
Change-Id trailer in its commit message, which a commit-msg hook adds and
which amends and rebases keep. So a commit prepared here can be uploaded to
a real Gerrit later, and Gerrit sees it as the same change.

Like with a Gerrit server, the review has its own place, refs/plustwo/,
apart from the author's branches. The author works on any local branch and
uploads its stack: the commits not on the base branch yet (main, or the
setting review.base), as refs/plustwo/stacks/<topic>. Like the target
branch of a Gerrit change, that base branch is recorded with the stack,
so stacks from worktrees with other bases are reviewed side by side. The
reviewer's actions (a rebase, a submit) change only that place and the
base branch, never a checkout. plustwo pull brings them into the
author's branch. Submitting a change moves the base branch forward to it,
so the base branch only ever gets reviewed commits. Several stacks can be
in review at the same time.

Every uploaded version of a change is kept as a patch set under
refs/plustwo/patchsets/<Change-Id>/<number>, so versions can be compared.
The review lives in a git note (refs/notes/review) on the latest patch set:
the comment threads and the votes.
The state of a change follows from git itself:
New without a patch set, In-Review with one, Submitted once it is on the
base branch. Nothing of this is pushed unless asked for explicitly.

Two roles take part: the reviewer, who comments and decides, and the author,
who answers and applies. The reviewer usually works in the terminal user
interface (`plustwo` without arguments), the author on the command line.
`git change` is the same program, for repositories set up before the
rename and for those who prefer it as a git command.

Usage:
    plustwo                             terminal user interface
    plustwo init                        installs the commit-msg hook and the
                                        notes setup in this repository and
                                        registers it
    plustwo register [PATH] [--name N] [--list L]
                                        shows a repository in the dashboard,
                                        L groups repositories into a list
    plustwo unregister NAME             forgets a registered repository
    plustwo repos                       the registered repositories
    plustwo list [--list L]             the dashboard: Your turn, Has drafts,
                                        Work in progress, Outgoing, Incoming
    plustwo abandon [REPO:]TOPIC        abandons a stack, restore brings it
    plustwo restore [REPO:]TOPIC        back
    plustwo show CHANGE [--ported]      comment threads of a change, also
                                        of a submitted one. --ported: only
                                        the threads not at their exact old
                                        place in the newest patch set,
                                        resolved ones too, with the old and
                                        the new lines around them.
    plustwo diff CHANGE [A [B]]         diff between patch sets, shown by
                                        git's pager. --tool opens vimdiff.
    plustwo rdiff CHANGE [A [B]]        range-diff between patch sets
    plustwo upload [CHANGE] [--topic TOPIC] [--wip [C] | --ready [C]]
                                        uploads the stack of the checked out
                                        branch (or of the branch with CHANGE):
                                        every changed commit becomes the next
                                        patch set. TOPIC defaults to the
                                        branch name without review/. --wip
                                        and --ready mark the whole stack, or
                                        C and the changes above it (--wip) or
                                        below it (--ready).
    plustwo pull                        brings the reviewer's rebase or submit
                                        into the checked out branch
    plustwo rebase CHANGE|--topic TOPIC
                                        rebases a stack onto its base branch,
                                        without a checkout. On a conflict the
                                        author is asked to do it.
    plustwo submit CHANGE               moves the base branch to the change,
                                        submitting the changes below it too
    plustwo comment CHANGE PLACE TEXT [--author] [--resolved]
                                        adds a sent comment, PLACE like
                                        src/main.cc:121, unresolved unless
                                        --resolved. With --author it is the
                                        author's, e.g. a question or a note,
                                        and the author's inbox and watch
                                        leave it out.
    plustwo reply CHANGE NUMBER TEXT [--resolve | --unresolve]
                                        adds an author message to a thread
    plustwo answer < ANSWERS            answers several threads, each answer
                                        starting like "I3f2a #1 done",
                                        "I3f2a #2: text" or "I3f2a #3
                                        resolve: text". Lines up to the
                                        next such line go on with its text.
                                        "I3f2a #4 move a.cc:12" moves the
                                        thread, see move.
    plustwo move CHANGE NUMBER PLACE [--patch-set N] [--old] [--author]
                                        moves a thread whose line got lost
                                        to PLACE on the newest patch set
                                        (or N), "a.cc:12 (old)" or --old
                                        for the old version. Older patch
                                        sets keep where it was. upload
                                        lists the threads that lost their
                                        line, show --ported all moved ones.
    plustwo done CHANGE NUMBER [TEXT]
                                        answers "Done" (or TEXT) and resolves
                                        the thread
    plustwo status CHANGE NUMBER STATUS [TEXT]
                                        resolves a thread (resolved) or makes
                                        it unresolved again (unresolved),
                                        TEXT becomes an author message
    plustwo vote CHANGE LABEL VALUE [--by NAME] [--patch-set N] [-m TEXT]
                                        votes on the latest patch set (or N):
                                        Code-Review -2..+2, by the reviewer,
                                        or Verified -1..+1, by ci. 0 removes
                                        the vote. TEXT, e.g. a test log's
                                        end, goes with it.
    plustwo require-verified CHANGE [on|off]
                                        whether the change needs Verified+1
                                        to be submitted
    plustwo inbox [REPO...] [--all]     what the author has to answer, in
                                        this repository (all registered ones
                                        outside of one or with --all), and
                                        new replies on resolved threads (fyi)
    plustwo watch [REPO...] [--since TIME] [--for MINUTES]
                                        prints one line per change with new
                                        sent messages, per vote, and per stack
                                        submitted, rebased or to rebase, and
                                        the text of replies on resolved
                                        threads (fyi), until stopped or for
                                        MINUTES. It starts with what happened
                                        since the user's last watch reported
                                        something (or since TIME, like
                                        2026-11-13T10:00 or 10:00), so a
                                        restart loses nothing.
    plustwo serve [--port N] [REPO...]  web interface for the reviewer, on
                                        127.0.0.1 only (port 8022)
    plustwo check [TOPIC]               runs the check (checks.command) of a
                                        stack's top change now. upload starts
                                        it in the background by itself.
    plustwo guide                       the guide for agents working as the
                                        author (AGENTS.md)
    plustwo config [--global] [--unset] [KEY [VALUE]]
                                        shows or changes a setting, all of
                                        them without KEY

CHANGE is a change's number in the dashboard (counted over all registered
repositories), a unique prefix of its Change-Id (like I3f2a) or one with
its repository, like prs2d:I3f2a. Reviewer commands work from anywhere,
author commands (upload, pull, inbox, watch) on the working directory's
repository. Registered repositories, lists and the dashboard's order and
folding are state, not settings: files in git config syntax below
$XDG_STATE_HOME/plustwo (~/.local/state/plustwo). Without any registered
repository, the working directory's is the only one. A and B are patch set
numbers or "current" for the commit as it is now. Without them, diff and
rdiff compare the latest patch set with the current commit, or the last
two patch sets if the commit is unchanged since. With a single patch set,
they show the change itself.

Like in Gerrit, submitting needs Code-Review+2 and no -2 on the latest patch
set. A change can also require Verified+1 and no -1, for which a check (a
script, a CI job) calls `plustwo vote CHANGE Verified +1 --by ci`. Votes
belong to a patch set. A new one keeps them like Gerrit does by default:
Code-Review after a trivial rebase (the same patch on another parent) or an
edit of only the commit message, Verified only while the code stays the
same, and a Code-Review-2 always.

Comment threads work like in Gerrit: a thread is unresolved or resolved,
and each message can change that. A new comment starts unresolved. The
author fixes what an unresolved thread asks for, uploads and answers
"Done", which resolves it. Submitting refuses while threads are unresolved.
Sending the reviewer's drafts is the author's signal to start.

Comments on a whole file have the file as their place. The commit message
is the file /COMMIT_MSG, shown with a header like in Gerrit, so it gets
comments the same way, e.g. on /COMMIT_MSG:7.

Settings are kept in files of their own, in git config syntax, not in
git's config: ~/.config/plustwo/config for the user ($XDG_CONFIG_HOME
moves it) and .git/plustwo/config for a repository, which wins.
plustwo config changes them, git config --file works too. Settings:
    review.base                         the base branch of uploads, default
                                        main. A stack keeps the one it was
                                        uploaded with.
                                        Older versions used the git config
                                        change.base, still read, which init
                                        moves out of .git/config.
    keys.preset                         the interface's keys: vim (the
                                        default), emacs or gerrit
    keys.ACTION                         keys of one action of the
                                        interface, like keys.reply = R or
                                        C-r,R. Its help (?) lists the actions.
    tui.sidebar                         the left column of the change
                                        screen: auto (the default, hidden
                                        below 100 columns), shown or hidden.
                                        The web page hides it on narrow
                                        windows.
    tui.colours                         auto (the default), true or 256: true
                                        colour needs a -direct terminfo
                                        entry like st-direct, auto uses it
                                        when COLORTERM says truecolor
    tui.background                      the terminal's background, like
                                        #1c1c1c, for the cursor's tint and
                                        the contrast of grey text. Without
                                        it the terminal is asked (OSC 11),
                                        else black.
    tui.theme                           the bat theme of the syntax colours,
                                        like zenburn, without it bat's own
                                        choice. Colours too faint on the
                                        diff's shades are made lighter (or
                                        darker) to a contrast of 4.5:1.
    tui.dim-colour                      the grey of secondary text like line
                                        numbers, a palette index, default
                                        246. Made lighter where a background
                                        needs it. Pick a darker one, like
                                        240, on a light terminal.
"""

import argparse
import contextlib
import subprocess
import sys
from collections.abc import Callable
from typing import Any

from plustwo.author.inbox import inbox_lines, lost_threads
from plustwo.author.watch import parse_time, watch
from plustwo.cli.output import guide_text, print_comments, print_overview
from plustwo.config.registry import (
    register_repository, registered_repositories, repository_lists, review_repositories,
    unregister_repository)
from plustwo.config.settings import (
    list_settings, list_user_settings, set_setting, set_user_setting, setting, user_setting)
from plustwo.git.errors import ChangeError
from plustwo.git.refs import current_branch
from plustwo.git.repository import Repository
from plustwo.model.comment import DONE, OLD_STATUSES, RESOLVED, STATUSES, UNRESOLVED
from plustwo.model.place import OLD_SIDE, place_label
from plustwo.model.roles import AUTHOR, REVIEWER
from plustwo.model.vote import CODE_REVIEW, VERIFIED, label_name
from plustwo.review.abandon import abandon_stack, restore_stack
from plustwo.review.answers import answer
from plustwo.review.changes import (
    branch_with_change, find_change, find_change_anywhere)
from plustwo.review.checks import run_check, start_check
from plustwo.review.compare import diff_command, range_diff_command
from plustwo.review.moves import move_place, move_text, move_thread
from plustwo.review.pull import pull
from plustwo.review.rebase import rebase_stack
from plustwo.review.setup import add_change_id, ensure_layout, init_repository
from plustwo.review.stacks import stack_base, topic_of_branch
from plustwo.review.submit import submit
from plustwo.review.threads import add_comment, add_message, set_status, status_name
from plustwo.review.upload import upload
from plustwo.review.votes import require_verified, set_vote


# The parsed command line, see _parse_arguments()
Arguments = argparse.Namespace

# What runs a command: a function of the parsed command line
CommandRun = Callable[[Arguments], None]

# The port of plustwo serve without --port
_DEFAULT_PORT = 8022


# ── the commands ──────────────────────────────────────────────────────────────

def _working_repository() -> Repository:
    """Finds the repository the working directory is in, which the commands
    that name no change act on.

    Returns:
        The repository.

    Raises:
        ChangeError: the working directory is in no repository.
    """
    return Repository.of_working_directory()


def _optional_repository() -> Repository | None:
    """Finds the working directory's repository for commands that also work
    outside one.

    Returns:
        The repository, None outside one.
    """
    try:
        return Repository.of_working_directory()
    except ChangeError:
        return None


def _run_tui(arguments: Arguments) -> None:
    """Starts the terminal user interface.

    Args:
        arguments: the command line, with the list of repositories.
    """
    # Imported here, so other commands don't load curses.
    from plustwo.tui.interface import run  # pylint: disable=import-outside-toplevel
    run(_optional_repository(), arguments.list)


def _init_command(_arguments: Arguments) -> None:
    """Sets up the current repository and registers it.

    Args:
        _arguments: the command line, unused.
    """
    repository = _working_repository()
    moved = init_repository(repository)
    print('commit-msg hook installed, notes follow amends and rebases')
    print(f'registered as {register_repository(repository, do_number_on_clash=True)}')
    print("agents: add 'run plustwo guide' to your AGENTS.md or CLAUDE.md")
    for line in moved:
        print(f'moved setting {line}')


def _config_command(arguments: Arguments) -> None:
    """Shows, sets or removes a setting, or lists them all.

    Args:
        arguments: the command line, with key, value, unset and user.

    Raises:
        ChangeError: the setting to show is not set.
    """
    repository = _optional_repository()
    if arguments.key is None:
        print('\n'.join(list_user_settings() if repository is None
                        else list_settings(repository)))
    elif arguments.unset or arguments.value is not None:
        value = None if arguments.unset else arguments.value
        if arguments.user:
            set_user_setting(arguments.key, value)
        else:
            set_setting(_working_repository(), arguments.key, value)
    else:
        value = (user_setting(arguments.key) if repository is None
                 else setting(repository, arguments.key))
        if value is None:
            raise ChangeError(f'{arguments.key} is not set')
        print(value)


def _upload_command(arguments: Arguments) -> None:
    """Uploads a stack and says what happened: new patch sets, marks, a
    started check, and the threads that lost their line.

    Args:
        arguments: the command line, with change, topic, wip and ready.
    """
    is_wip = True if arguments.wip is not None else False if arguments.ready is not None \
        else None
    from_change = arguments.wip or arguments.ready or None
    reference = arguments.change or from_change
    repository = _working_repository()
    branch = branch_with_change(repository, reference) if reference else None
    uploaded, topic, has_grown, moved_from = upload(
        repository, branch, arguments.topic, is_wip=is_wip, from_change=from_change)
    if start_check(repository, topic):
        print(f'{topic}: check started, it votes Verified')
    if has_grown:
        print(f'added to stack {topic}')
    if moved_from:
        print(f'{topic}: moved from {moved_from} to {stack_base(repository, topic)}')
    if is_wip is not None:
        changes_text = (f'{from_change} and the changes ' + ('above' if is_wip else 'below')
                        + ' it ' if from_change else '')
        print(f'{topic}: ' + changes_text
              + ('work in progress' if is_wip else 'ready for review'))
    for other, number, patch_set_type in uploaded:
        kept = [f'{vote.label}{vote.value:+d}' for vote in other.current_votes(CODE_REVIEW)
                + other.current_votes(VERIFIED)]
        print(f'{other.short_id()}: patch set {number}'
              + (f', {patch_set_type}' if patch_set_type else '')
              + (f', keeps {" ".join(kept)}' if kept else ''))
    lost = lost_threads([find_change(repository, other.change_id) for other, _number,
                         _type in uploaded])
    if lost:
        print('threads that lost their line, move the ones you know with '
              '`plustwo answer` lines like "I3f2a #4 move a.cc:12":')
        print('\n'.join(f'  {line}' for line in lost))
        print('`plustwo show --ported CHANGE` shows them with the lines around them')


def _rebase_command(arguments: Arguments) -> None:
    """Rebases a stack onto its base branch.

    Args:
        arguments: the command line, with topic or change.

    Raises:
        ChangeError: neither a topic nor a change names the stack.
    """
    topic = arguments.topic
    repository = None
    if topic is None and arguments.change:
        change = find_change_anywhere(arguments.change)
        topic, repository = change.topic, change.repository
    if topic is None:
        raise ChangeError('name the stack: --topic TOPIC or a CHANGE of it')
    rebased = rebase_stack(repository or _working_repository(), topic)
    print(f'{topic}: rebased, {len(rebased)} new patch set(s)' if rebased is not None
          else f'{topic}: conflicts, the author is asked to rebase')


def _pull_command(_arguments: Arguments) -> None:
    """Brings the reviewer's rebase or submit into the checked out branch.

    Args:
        _arguments: the command line, unused.
    """
    print(pull(_working_repository()))


def _check_command(arguments: Arguments) -> None:
    """Runs the check of a stack now, which votes Verified.

    Args:
        arguments: the command line, with topic.

    Raises:
        ChangeError: no check is set up.
    """
    repository = _working_repository()
    topic = arguments.topic or topic_of_branch(current_branch(repository) or '')
    value = run_check(repository, topic)
    if value is None:
        raise ChangeError('no check set up: plustwo config checks.command "make check"')
    print(f'{topic}: Verified{value:+d}, log in {repository.git_path("plustwo/check.log")}')


def _check_run_command(arguments: Arguments) -> None:
    """Runs a check started in the background by an upload, see
    start_check().

    Args:
        arguments: the command line, with topic and commit.
    """
    repository = _working_repository()
    try:
        run_check(repository, arguments.topic, arguments.commit)
    finally:
        pid_file = repository.git_path('plustwo/check.pid')
        if pid_file.exists() and arguments.commit in pid_file.read_text():
            pid_file.unlink()


def _submit_command(arguments: Arguments) -> None:
    """Submits a change and the ones below it.

    Args:
        arguments: the command line, with change.
    """
    change = find_change_anywhere(arguments.change)
    submitted = submit(change.repository, change.change_id)
    print('Submitted: ' + ', '.join(change.short_id() for change in submitted))


def _comment_command(arguments: Arguments) -> None:
    """Starts a thread.

    Args:
        arguments: the command line, with change, place, text, author and
            status.
    """
    change = find_change_anywhere(arguments.change)
    number = add_comment(change.repository, change.change_id, arguments.place, arguments.text,
                         author=AUTHOR if arguments.author else REVIEWER,
                         status=arguments.status)
    print(f'{change.short_id()}: comment #{number} added')


def _reply_command(arguments: Arguments) -> None:
    """Answers a thread as the author.

    Args:
        arguments: the command line, with change, number, text, resolve and
            unresolve.
    """
    status = RESOLVED if arguments.resolve else UNRESOLVED if arguments.unresolve else None
    change = find_change_anywhere(arguments.change)
    add_message(change.repository, change.change_id, arguments.number, AUTHOR,
                arguments.text, status=status)


def _done_command(arguments: Arguments) -> None:
    """Answers "Done" as the author and resolves the thread.

    Args:
        arguments: the command line, with change, number and text.
    """
    change = find_change_anywhere(arguments.change)
    add_message(change.repository, change.change_id, arguments.number, AUTHOR,
                arguments.text or DONE, status=RESOLVED)


def _move_command(arguments: Arguments) -> None:
    """Moves a thread to where it belongs.

    Args:
        arguments: the command line, with change, number, place, patch_set,
            old and author.
    """
    change = find_change_anywhere(arguments.change)
    place, side = move_place(arguments.place)
    move = move_thread(change.repository, change.change_id, arguments.number, place,
                       OLD_SIDE if arguments.old else side, arguments.patch_set or 0,
                       AUTHOR if arguments.author else REVIEWER)
    print(f'{change.short_id()}: comment #{arguments.number} '
          + (move_text(move) if move else f'now on {place_label(place, side)}'))


def _answer_command(_arguments: Arguments) -> None:
    """Adds the author's answers read from the standard input.

    Args:
        _arguments: the command line, unused.
    """
    n_answers = answer(_working_repository(), sys.stdin.read())
    print(f'{n_answers} answer(s) added')


def _status_command(arguments: Arguments) -> None:
    """Resolves a thread as the author or makes it unresolved again.

    Args:
        arguments: the command line, with change, number, status and text.
    """
    change = find_change_anywhere(arguments.change)
    set_status(change.repository, change.change_id, arguments.number, arguments.status,
               arguments.text)
    print(f'{change.short_id()}: comment #{arguments.number} {status_name(arguments.status)}')


def _vote_command(arguments: Arguments) -> None:
    """Votes on a patch set.

    Args:
        arguments: the command line, with change, label, value, by,
            patch_set and message.
    """
    change = find_change_anywhere(arguments.change)
    number = set_vote(change.repository, change.change_id, arguments.label, arguments.value,
                      arguments.by, arguments.patch_set, arguments.message or '')
    print(f'{change.short_id()}: {label_name(arguments.label)}{arguments.value:+d} '
          f'on PS{number}')


def _require_verified_command(arguments: Arguments) -> None:
    """Sets whether a change needs Verified+1.

    Args:
        arguments: the command line, with change and state.
    """
    change = find_change_anywhere(arguments.change)
    require_verified(change.repository, change.change_id, 'off' != arguments.state)


def _diff_command(arguments: Arguments) -> None:
    """Shows the diff between two versions of a change.

    Args:
        arguments: the command line, with change, first, second and tool.
    """
    change = find_change_anywhere(arguments.change)
    subprocess.run(diff_command(change, arguments.first, arguments.second, arguments.tool))


def _range_diff_command(arguments: Arguments) -> None:
    """Shows the range-diff between two patch sets of a change.

    Args:
        arguments: the command line, with change, first and second.
    """
    change = find_change_anywhere(arguments.change)
    subprocess.run(range_diff_command(change, arguments.first, arguments.second))


def _author_repositories(arguments: Arguments) -> list[Repository]:
    """Finds the repositories an author's command looks at.

    Args:
        arguments: the command line, with repositories, all and list.

    Returns:
        The named ones, else all registered ones outside a repository or
        with --all, else the working directory's.
    """
    if arguments.repositories:
        return [Repository.of_directory(path) for path in arguments.repositories]
    here = _optional_repository()
    if arguments.all or here is None:
        return review_repositories(arguments.list)
    return [here]


def _inbox_command(arguments: Arguments) -> None:
    """Prints what the author has to answer.

    Args:
        arguments: the command line, see _author_repositories(), and topic.
    """
    repositories = _author_repositories(arguments)
    print('\n'.join(inbox_lines(repositories, arguments.topic)) or 'Nothing to answer.')


def _watch_command(arguments: Arguments) -> None:
    """Prints new messages and votes until stopped.

    Args:
        arguments: the command line, see _author_repositories(), and topic,
            since and minutes.
    """
    since = parse_time(arguments.since) if arguments.since else None
    repositories = _author_repositories(arguments)
    with contextlib.suppress(KeyboardInterrupt):
        watch(repositories, topic=arguments.topic, since=since, minutes=arguments.minutes)


def _register_command(arguments: Arguments) -> None:
    """Registers a repository for the dashboard.

    Args:
        arguments: the command line, with path, name and list.
    """
    name = register_repository(Repository.of_directory(arguments.path), arguments.name,
                               arguments.list)
    print(f'registered as {name}')


def _unregister_command(arguments: Arguments) -> None:
    """Forgets a registered repository.

    Args:
        arguments: the command line, with name.
    """
    unregister_repository(arguments.name)


def _repos_command(_arguments: Arguments) -> None:
    """Prints the registered repositories and their lists.

    Args:
        _arguments: the command line, unused.
    """
    lists = repository_lists()
    for name, repository in registered_repositories().items():
        member_of = [list_name for list_name, names in lists.items() if name in names]
        print(f'{name:<16} {repository.path}'
              + (f'  ({", ".join(member_of)})' if member_of else ''))


def _stack_command(run: Callable[[Repository, str], None]) -> CommandRun:
    """Makes the command of an action on a whole stack, named [REPO:]TOPIC.

    Args:
        run: the action, abandon_stack() or restore_stack().

    Returns:
        The command.
    """
    def stack_run(arguments: Arguments) -> None:
        """Runs the action on the stack.

        Args:
            arguments: the command line, with topic.

        Raises:
            ChangeError: the repository is not registered.
        """
        name, colon, topic = arguments.topic.rpartition(':')
        repository = registered_repositories().get(name) if colon else _working_repository()
        if repository is None:
            raise ChangeError(f'no registered repository {name}')
        run(repository, topic)
        print(f'{topic}: ' + ('abandoned' if abandon_stack == run else 'restored'))
    return stack_run


def _guide_command(_arguments: Arguments) -> None:
    """Prints the guide for agents.

    Args:
        _arguments: the command line, unused.
    """
    print(guide_text(), end='')


def _serve_command(arguments: Arguments) -> None:
    """Runs the web interface.

    Args:
        arguments: the command line, with repositories, list and port.
    """
    # Imported here, so other commands don't load the HTTP server.
    from plustwo.web.server import serve  # pylint: disable=import-outside-toplevel
    repositories = ([Repository.of_directory(path) for path in arguments.repositories]
                    or review_repositories(arguments.list))
    for repository in repositories:
        ensure_layout(repository)
    serve(repositories, arguments.port)


def _hook_command(arguments: Arguments) -> None:
    """Adds a Change-Id to a commit message, for the commit-msg hook.

    Args:
        arguments: the command line, with message_file.
    """
    add_change_id(arguments.message_file)


# ── the command line ──────────────────────────────────────────────────────────

def _parse_arguments(argv: list[str]) -> Arguments:
    """Parses the command line.

    Args:
        argv: the arguments, without the program's name.

    Returns:
        The parsed command line. Its run is the command's function.
    """
    parser = argparse.ArgumentParser(prog='plustwo', description=(__doc__ or '').split('\n')[0])
    parser.set_defaults(run=_run_tui, list=None)
    parser.add_argument('--list', help='the dashboard of this list of repositories')
    commands = parser.add_subparsers()

    def command(name: str, run: CommandRun, help_text: str,
                *arguments: str | tuple[str, dict[str, Any]]) -> argparse.ArgumentParser:
        """Adds a sub-command.

        Args:
            name: the command's name.
            run: the function that runs it.
            help_text: the help line.
            *arguments: the positional arguments: names, or (name, options of
                add_argument()).

        Returns:
            The sub-command's parser, for more options.
        """
        sub = commands.add_parser(name, help=help_text)
        for argument in arguments:
            if isinstance(argument, tuple):
                sub.add_argument(argument[0], **argument[1])
            else:
                sub.add_argument(argument)
        sub.set_defaults(run=run)
        return sub

    optional = {'nargs': '?'}
    number = ('number', {'type': int})
    repositories = ('repositories', {'nargs': '*'})
    command('init', _init_command, 'set up this repository')
    command('list', print_overview, 'the dashboard').add_argument(
        '--list', help='only the repositories of this list', default=argparse.SUPPRESS)
    command('show', print_comments, 'threads of a change', 'change').add_argument(
        '--ported', action='store_true',
        help='only threads not at their old place, with the lines around them')
    upload_parser = command('upload', _upload_command, 'upload the stack of a branch',
                            ('change', optional))
    upload_parser.add_argument('--topic')
    upload_parser.add_argument('--wip', nargs='?', const='', metavar='CHANGE',
                               help='mark it (or CHANGE and the ones above) work in progress')
    upload_parser.add_argument('--ready', nargs='?', const='', metavar='CHANGE',
                               help='mark it (or CHANGE and the ones below) ready for review')
    command('pull', _pull_command, "bring the reviewer's rebase or submit into the branch")
    command('rebase', _rebase_command, 'rebase a stack onto the base branch',
            ('change', optional)).add_argument('--topic')
    command('submit', _submit_command, 'end the review', 'change')
    command('diff', _diff_command, 'diff between patch sets', 'change', ('first', optional),
            ('second', optional)).add_argument('--tool', action='store_true')
    command('rdiff', _range_diff_command, 'range-diff between patch sets', 'change',
            ('first', optional), ('second', optional))
    comment = command('comment', _comment_command, 'add a comment', 'change', 'place', 'text')
    comment.add_argument('--author', action='store_true', help="the author's comment")
    status_choices: dict[str, Any] = {'choices': STATUSES + tuple(OLD_STATUSES)}
    comment.add_argument('--status', default=None, **status_choices)
    comment.add_argument('--resolved', action='store_const', const=RESOLVED, dest='status',
                         help='start the thread resolved, e.g. a note')
    reply = command('reply', _reply_command, 'answer a thread', 'change', number, 'text')
    reply.add_argument('--resolve', action='store_true', help='and resolve the thread')
    reply.add_argument('--unresolve', action='store_true', help='and make it unresolved again')
    command('answer', _answer_command, 'answer several threads at once, from stdin')
    move = command('move', _move_command, 'move a thread to where it belongs', 'change',
                   ('number', {'type': lambda text: int(text.lstrip('#'))}), 'place')
    move.add_argument('--patch-set', type=int, help='the patch set PLACE is on, else the newest')
    move.add_argument('--old', action='store_true', help='PLACE is in the old version')
    move.add_argument('--author', action='store_true', help="the author moves it")
    command('done', _done_command, 'answer "Done" and resolve the thread', 'change', number,
            ('text', optional))
    command('status', _status_command, 'resolve a thread or make it unresolved again',
            'change', number, ('status', status_choices), ('text', optional))
    vote = command('vote', _vote_command, 'vote on a patch set', 'change', 'label',
                   ('value', {'type': int}))
    vote.add_argument('--by')
    vote.add_argument('--patch-set', type=int)
    vote.add_argument('-m', '--message')
    command('require-verified', _require_verified_command, 'whether Verified+1 is needed',
            'change', ('state', {'nargs': '?', 'choices': ('on', 'off'), 'default': 'on'}))
    for name, run, help_text in (('inbox', _inbox_command, 'what to answer'),
                                 ('watch', _watch_command, 'print new messages and votes')):
        sub = command(name, run, help_text, repositories)
        sub.add_argument('--topic', help='only this stack, e.g. for one of several agents')
        sub.add_argument('--all', action='store_true', help='all registered repositories')
        sub.add_argument('--list', help='the repositories of this list', default=argparse.SUPPRESS)
        if 'watch' == name:
            sub.add_argument('--since', help='report events from this time on (seconds since '
                             '1970, 2026-11-13T10:00 or 10:00), instead of since the last watch')
            sub.add_argument('--for', dest='minutes', type=float,
                             help='end after this many minutes')
    register = command('register', _register_command, 'show a repository in the dashboard',
                       ('path', {'nargs': '?', 'default': '.'}))
    register.add_argument('--name')
    register.add_argument('--list', help='add it to this list of repositories')
    command('unregister', _unregister_command, 'forget a registered repository', 'name')
    command('repos', _repos_command, 'the registered repositories')
    command('check', _check_command, 'run the check of a stack now, votes Verified',
            ('topic', optional))
    check_run = commands.add_parser('check-run', help=argparse.SUPPRESS)
    check_run.add_argument('topic')
    check_run.add_argument('commit')
    check_run.set_defaults(run=_check_run_command)
    command('abandon', _stack_command(abandon_stack), 'abandon a stack, [REPO:]TOPIC', 'topic')
    command('restore', _stack_command(restore_stack), 'bring an abandoned stack back', 'topic')
    command('guide', _guide_command, 'the guide for agents as authors')
    serve = command('serve', _serve_command, 'web interface', repositories)
    serve.add_argument('--port', type=int, default=_DEFAULT_PORT)
    serve.add_argument('--list', help='the repositories of this list', default=argparse.SUPPRESS)
    config = command('config', _config_command, 'show or change a setting', ('key', optional),
                     ('value', optional))
    config.add_argument('--global', dest='user', action='store_true',
                        help="the user's settings instead of the repository's")
    config.add_argument('--unset', action='store_true', help='remove the setting')
    hook = commands.add_parser('commit-msg-hook', help=argparse.SUPPRESS)
    hook.add_argument('message_file')
    hook.set_defaults(run=_hook_command)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    """Runs one command.

    Args:
        argv: the command line's arguments, without the program's name.

    Returns:
        The exit code: 1 after a refused action, else 0.
    """
    arguments = _parse_arguments(argv)
    try:
        arguments.run(arguments)
    except ChangeError as error:
        print(f'plustwo: {error}', file=sys.stderr)
        return 1
    return 0
