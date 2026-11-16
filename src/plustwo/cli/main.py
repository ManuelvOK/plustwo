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
    plustwo show CHANGE                 comment threads of a change, also
                                        of a submitted one
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
                                        line.
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
numbers or "current" for the commit as it is now. Without them, diff and rdiff compare the latest patch
set with the current commit, or the last two patch sets if the commit is
unchanged since. With a single patch set, they show the change itself.

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

from plustwo.author.inbox import inbox_lines, lost_threads
from plustwo.author.watch import parse_time, watch
from plustwo.cli.output import guide_text, print_comments, print_overview
from plustwo.config.registry import (
    register_repository, registered_repositories, repository_lists, review_repositories,
    unregister_repository)
from plustwo.config.settings import list_settings, set_setting, setting
from plustwo.git.errors import ChangeError
from plustwo.git.refs import current_branch, git_path, toplevel
from plustwo.model.comment import DONE, OLD_STATUSES, RESOLVED, STATUSES, UNRESOLVED
from plustwo.model.place import OLD_SIDE, place_label
from plustwo.model.roles import AUTHOR, REVIEWER
from plustwo.model.vote import CODE_REVIEW, VERIFIED, label_name
from plustwo.review.abandon import abandon_stack, restore_stack
from plustwo.review.answers import answer
from plustwo.review.changes import branch_with_change, find_change
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


def run_tui(arguments):
    """Starts the terminal user interface."""
    # Imported here, so other commands don't load curses.
    from plustwo.tui.interface import run  # pylint: disable=import-outside-toplevel
    run(arguments.list)


def parse_arguments(argv):
    """Parses the command line."""
    parser = argparse.ArgumentParser(prog='plustwo', description=__doc__.split('\n')[0])
    parser.set_defaults(run=run_tui, list=None)
    parser.add_argument('--list', help='the dashboard of this list of repositories')
    commands = parser.add_subparsers()

    def command(name, run, help_text, *arguments):
        """Adds a sub-command. Arguments are names or (name, options)."""
        sub = commands.add_parser(name, help=help_text)
        for argument in arguments:
            if isinstance(argument, tuple):
                sub.add_argument(argument[0], **argument[1])
            else:
                sub.add_argument(argument)
        sub.set_defaults(run=run)
        return sub

    def init_command(_arguments):
        moved = init_repository()
        print('commit-msg hook installed, notes follow amends and rebases')
        print(f'registered as {register_repository(unique=True)}')
        print("agents: add 'run plustwo guide' to your AGENTS.md or CLAUDE.md")
        for line in moved:
            print(f'moved setting {line}')

    def config_command(arguments):
        if arguments.key is None:
            print('\n'.join(list_settings()))
        elif arguments.unset:
            set_setting(arguments.key, None, arguments.user)
        elif arguments.value is None:
            value = setting(arguments.key)
            if value is None:
                raise ChangeError(f'{arguments.key} is not set')
            print(value)
        else:
            set_setting(arguments.key, arguments.value, arguments.user)

    def upload_command(arguments):
        wip = True if arguments.wip is not None else False if arguments.ready is not None \
            else None
        at = arguments.wip or arguments.ready or None
        reference = arguments.change or at
        branch = branch_with_change(reference) if reference else None
        uploaded, topic, grown, moved = upload(branch, arguments.topic, wip=wip, at=at)
        if start_check(topic):
            print(f'{topic}: check started, it votes Verified')
        if grown:
            print(f'added to stack {topic}')
        if moved:
            print(f'{topic}: moved from {moved} to {stack_base(topic)}')
        if wip is not None:
            print(f'{topic}: ' + (f'{at} and the changes ' + ('above' if wip else 'below')
                                  + ' it ' if at else '')
                  + ('work in progress' if wip else 'ready for review'))
        for other, number, kind in uploaded:
            kept = [f'{vote.label}{vote.value:+d}' for vote in other.current_votes(CODE_REVIEW)
                    + other.current_votes(VERIFIED)]
            print(f'{other.short_id()}: patch set {number}' + (f', {kind}' if kind else '')
                  + (f', keeps {" ".join(kept)}' if kept else ''))
        lost = lost_threads([find_change(other.change_id) for other, _number, _kind in uploaded])
        if lost:
            print('threads that lost their line, move the ones you know with '
                  '`plustwo answer` lines like "I3f2a #4 move a.cc:12":')
            print('\n'.join(f'  {line}' for line in lost))

    def rebase_command(arguments):
        topic = arguments.topic
        if topic is None:
            topic = find_change(arguments.change).topic if arguments.change else None
        if topic is None:
            raise ChangeError('name the stack: --topic TOPIC or a CHANGE of it')
        rebased = rebase_stack(topic)
        print(f'{topic}: rebased, {len(rebased)} new patch set(s)' if rebased is not None
              else f'{topic}: conflicts, the author is asked to rebase')

    def pull_command(_arguments):
        print(pull())

    def check_command(arguments):
        topic = arguments.topic or topic_of_branch(current_branch() or '')
        value = run_check(topic)
        if value is None:
            raise ChangeError('no check set up: plustwo config checks.command "make check"')
        print(f'{topic}: Verified{value:+d}, log in {git_path("plustwo/check.log")}')

    def check_run_command(arguments):
        try:
            run_check(arguments.topic, arguments.commit)
        finally:
            pid_file = git_path('plustwo/check.pid')
            if pid_file.exists() and arguments.commit in pid_file.read_text():
                pid_file.unlink()

    def submit_command(arguments):
        submitted = submit(find_change(arguments.change).change_id)
        print('Submitted: ' + ', '.join(change.short_id() for change in submitted))

    def comment_command(arguments):
        change = find_change(arguments.change)
        number = add_comment(change.change_id, arguments.place, arguments.text,
                             author=AUTHOR if arguments.author else REVIEWER,
                             status=arguments.status)
        print(f'{change.short_id()}: comment #{number} added')

    def reply_command(arguments):
        status = RESOLVED if arguments.resolve else UNRESOLVED if arguments.unresolve else None
        add_message(find_change(arguments.change).change_id, arguments.number, AUTHOR,
                    arguments.text, status=status)

    def done_command(arguments):
        add_message(find_change(arguments.change).change_id, arguments.number, AUTHOR,
                    arguments.text or DONE, status=RESOLVED)

    def move_command(arguments):
        change = find_change(arguments.change)
        place, side = move_place(arguments.place)
        move = move_thread(change.change_id, arguments.number, place,
                           OLD_SIDE if arguments.old else side, arguments.patch_set or 0,
                           AUTHOR if arguments.author else REVIEWER)
        print(f'{change.short_id()}: comment #{arguments.number} '
              + (move_text(move) if move else f'now on {place_label(place, side)}'))

    def answer_command(_arguments):
        count = answer(sys.stdin.read())
        print(f'{count} answer(s) added')

    def status_command(arguments):
        change = find_change(arguments.change)
        set_status(change.change_id, arguments.number, arguments.status, arguments.text)
        print(f'{change.short_id()}: comment #{arguments.number} {status_name(arguments.status)}')

    def vote_command(arguments):
        change = find_change(arguments.change)
        number = set_vote(change.change_id, arguments.label, arguments.value, arguments.by,
                          arguments.patch_set, arguments.message or '')
        print(f'{change.short_id()}: {label_name(arguments.label)}{arguments.value:+d} on PS{number}')

    def require_verified_command(arguments):
        require_verified(find_change(arguments.change).change_id, 'off' != arguments.state)

    def diff_run(arguments):
        change = find_change(arguments.change)
        subprocess.run(diff_command(change, arguments.first, arguments.second, arguments.tool))

    def range_diff_run(arguments):
        change = find_change(arguments.change)
        subprocess.run(range_diff_command(change, arguments.first, arguments.second))

    def author_repositories(arguments):
        # The working directory's repository, all registered ones outside a
        # repository or with --all
        if arguments.repositories:
            return arguments.repositories
        if arguments.all or toplevel() is None:
            return review_repositories(arguments.list)
        return ['.']

    def inbox_command(arguments):
        print('\n'.join(inbox_lines(author_repositories(arguments), arguments.topic))
              or 'Nothing to answer.')

    def watch_command(arguments):
        since = parse_time(arguments.since) if arguments.since else None
        with contextlib.suppress(KeyboardInterrupt):
            watch(author_repositories(arguments), topic=arguments.topic, since=since,
                  minutes=arguments.minutes)

    def register_command(arguments):
        print(f'registered as {register_repository(arguments.path, arguments.name, arguments.list)}')

    def unregister_command(arguments):
        unregister_repository(arguments.name)

    def repos_command(_arguments):
        lists = repository_lists()
        for name, path in registered_repositories().items():
            member = [list_name for list_name, names in lists.items() if name in names]
            print(f'{name:<16} {path}' + (f'  ({", ".join(member)})' if member else ''))

    def stack_command(run):
        def stack_run(arguments):
            name, colon, topic = arguments.topic.rpartition(':')
            repository = registered_repositories().get(name) if colon else None
            if colon and repository is None:
                raise ChangeError(f'no registered repository {name}')
            run(topic, repository)
            print(f'{topic}: ' + ('abandoned' if abandon_stack == run else 'restored'))
        return stack_run

    def guide_command(_arguments):
        print(guide_text(), end='')

    def serve_command(arguments):
        # Imported here, so other commands don't load the HTTP server.
        from plustwo.web.server import serve  # pylint: disable=import-outside-toplevel
        repositories = arguments.repositories or review_repositories(arguments.list)
        for repository in repositories:
            ensure_layout(repository)
        serve(repositories, arguments.port)

    def hook_command(arguments):
        add_change_id(arguments.message_file)

    optional = {'nargs': '?'}
    number = ('number', {'type': int})
    repositories = ('repositories', {'nargs': '*'})
    command('init', init_command, 'set up this repository')
    command('list', print_overview, 'the dashboard').add_argument(
        '--list', help='only the repositories of this list', default=argparse.SUPPRESS)
    command('show', print_comments, 'threads of a change', 'change')
    upload_parser = command('upload', upload_command, 'upload the stack of a branch',
                            ('change', optional))
    upload_parser.add_argument('--topic')
    upload_parser.add_argument('--wip', nargs='?', const='', metavar='CHANGE',
                               help='mark it (or CHANGE and the ones above) work in progress')
    upload_parser.add_argument('--ready', nargs='?', const='', metavar='CHANGE',
                               help='mark it (or CHANGE and the ones below) ready for review')
    command('pull', pull_command, "bring the reviewer's rebase or submit into the branch")
    command('rebase', rebase_command, 'rebase a stack onto the base branch',
            ('change', optional)).add_argument('--topic')
    command('submit', submit_command, 'end the review', 'change')
    command('diff', diff_run, 'diff between patch sets', 'change', ('first', optional),
            ('second', optional)).add_argument('--tool', action='store_true')
    command('rdiff', range_diff_run, 'range-diff between patch sets', 'change',
            ('first', optional), ('second', optional))
    comment = command('comment', comment_command, 'add a comment', 'change', 'place', 'text')
    comment.add_argument('--author', action='store_true', help="the author's comment")
    status_choices = {'choices': STATUSES + tuple(OLD_STATUSES)}
    comment.add_argument('--status', default=None, **status_choices)
    comment.add_argument('--resolved', action='store_const', const=RESOLVED, dest='status',
                         help='start the thread resolved, e.g. a note')
    reply = command('reply', reply_command, 'answer a thread', 'change', number, 'text')
    reply.add_argument('--resolve', action='store_true', help='and resolve the thread')
    reply.add_argument('--unresolve', action='store_true', help='and make it unresolved again')
    command('answer', answer_command, 'answer several threads at once, from stdin')
    move = command('move', move_command, 'move a thread to where it belongs', 'change',
                   ('number', {'type': lambda text: int(text.lstrip('#'))}), 'place')
    move.add_argument('--patch-set', type=int, help='the patch set PLACE is on, else the newest')
    move.add_argument('--old', action='store_true', help='PLACE is in the old version')
    move.add_argument('--author', action='store_true', help="the author moves it")
    command('done', done_command, 'answer "Done" and resolve the thread', 'change', number,
            ('text', optional))
    command('status', status_command, 'resolve a thread or make it unresolved again', 'change',
            number, ('status', status_choices), ('text', optional))
    vote = command('vote', vote_command, 'vote on a patch set', 'change', 'label',
                   ('value', {'type': int}))
    vote.add_argument('--by')
    vote.add_argument('--patch-set', type=int)
    vote.add_argument('-m', '--message')
    command('require-verified', require_verified_command, 'whether Verified+1 is needed',
            'change', ('state', {'nargs': '?', 'choices': ('on', 'off'), 'default': 'on'}))
    for name, run, help_text in (('inbox', inbox_command, 'what to answer'),
                                 ('watch', watch_command, 'print new messages and votes')):
        sub = command(name, run, help_text, repositories)
        sub.add_argument('--topic', help='only this stack, e.g. for one of several agents')
        sub.add_argument('--all', action='store_true', help='all registered repositories')
        sub.add_argument('--list', help='the repositories of this list', default=argparse.SUPPRESS)
        if 'watch' == name:
            sub.add_argument('--since', help='report events from this time on (seconds since '
                             '1970, 2026-11-13T10:00 or 10:00), instead of since the last watch')
            sub.add_argument('--for', dest='minutes', type=float,
                             help='end after this many minutes')
    register = command('register', register_command, 'show a repository in the dashboard',
                       ('path', {'nargs': '?', 'default': '.'}))
    register.add_argument('--name')
    register.add_argument('--list', help='add it to this list of repositories')
    command('unregister', unregister_command, 'forget a registered repository', 'name')
    command('repos', repos_command, 'the registered repositories')
    command('check', check_command, 'run the check of a stack now, votes Verified',
            ('topic', optional))
    check_run = commands.add_parser('check-run', help=argparse.SUPPRESS)
    check_run.add_argument('topic')
    check_run.add_argument('commit')
    check_run.set_defaults(run=check_run_command)
    command('abandon', stack_command(abandon_stack), 'abandon a stack, [REPO:]TOPIC', 'topic')
    command('restore', stack_command(restore_stack), 'bring an abandoned stack back', 'topic')
    command('guide', guide_command, 'the guide for agents as authors')
    serve = command('serve', serve_command, 'web interface', repositories)
    serve.add_argument('--port', type=int, default=8022)
    serve.add_argument('--list', help='the repositories of this list', default=argparse.SUPPRESS)
    config = command('config', config_command, 'show or change a setting', ('key', optional),
                     ('value', optional))
    config.add_argument('--global', dest='user', action='store_true',
                        help="the user's settings instead of the repository's")
    config.add_argument('--unset', action='store_true', help='remove the setting')
    hook = commands.add_parser('commit-msg-hook', help=argparse.SUPPRESS)
    hook.add_argument('message_file')
    hook.set_defaults(run=hook_command)

    return parser.parse_args(argv)


def main(argv):
    """Runs one command. Returns the exit code."""
    arguments = parse_arguments(argv)
    try:
        arguments.run(arguments)
    except ChangeError as error:
        print(f'plustwo: {error}', file=sys.stderr)
        return 1
    return 0
