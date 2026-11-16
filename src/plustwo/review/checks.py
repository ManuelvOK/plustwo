# SPDX-License-Identifier: Zlib
"""Built-in checks that vote Verified."""

import contextlib
import os
import signal
import subprocess
import sys

from plustwo.config.settings import setting
from plustwo.git.repository import Repository
from plustwo.model.vote import VERIFIED
from plustwo.paths import LAUNCHER
from plustwo.review.changes import changes_in_stack
from plustwo.review.stacks import stack_ref
from plustwo.review.votes import set_vote


# How many lines of the check's log end the Verified vote's message
_N_LOG_LINES_IN_VOTE = 20


def _cancel_check(repository: Repository) -> None:
    """Stops a running check of the repository, if there is one.

    Args:
        repository: the repository.
    """
    pid_file = repository.git_path('plustwo/check.pid')
    if not pid_file.exists():
        return
    with contextlib.suppress(ValueError, ProcessLookupError, PermissionError):
        os.killpg(int(pid_file.read_text().split()[0]), signal.SIGTERM)
    pid_file.unlink(missing_ok=True)


def start_check(repository: Repository, topic: str) -> bool:
    """Starts the check (setting checks.command) of a stack's top change in
    the background, after stopping one still running: a newer upload makes
    its result useless.

    Args:
        repository: the repository.
        topic: the stack's topic.

    Returns:
        Whether a check started, False without checks.command.
    """
    if not setting(repository, 'checks.command'):
        return False
    _cancel_check(repository)
    top = repository.git('rev-parse', stack_ref(topic))
    process = subprocess.Popen([sys.executable, str(LAUNCHER), 'check-run',
                                topic, top], cwd=repository.path, start_new_session=True,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    pid_file = repository.git_path('plustwo/check.pid')
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(f'{process.pid} {top}\n')
    return True


def run_check(repository: Repository, topic: str, commit: str | None = None) -> int | None:
    """Runs the check of a stack's top change in the worktree
    .git/plustwo/check, the setting checks.setup first if there is one, and
    votes Verified by ci with the end of the log. The log stays in
    .git/plustwo/check.log.

    Args:
        repository: the repository.
        topic: the stack's topic.
        commit: the commit to check, None for the stack's top.

    Returns:
        The vote, None without checks.command.
    """
    command = setting(repository, 'checks.command')
    if not command:
        return None
    commit = commit or repository.git('rev-parse', stack_ref(topic))
    # The worktree at the commit
    worktree = repository.git_path('plustwo/check')
    if not (worktree / '.git').exists():
        repository.git('worktree', 'prune')
        repository.git('worktree', 'add', '--detach', '--force', str(worktree), commit)
    else:
        Repository(worktree).git('checkout', '--quiet', '--detach', '--force', commit)
    # The steps, logged
    log_path = repository.git_path('plustwo/check.log')
    with open(log_path, 'w') as log:
        status = 0
        for step in filter(None, (setting(repository, 'checks.setup'), command)):
            log.write(f'$ {step}\n')
            log.flush()
            status = subprocess.run(step, shell=True, cwd=worktree, stdout=log,
                                    stderr=subprocess.STDOUT).returncode
            if status:
                break
    # The vote on the checked patch set
    value = 1 if 0 == status else -1
    tail = '\n'.join(log_path.read_text(errors='replace').rstrip('\n').split('\n')
                     [-_N_LOG_LINES_IN_VOTE:])
    change = next((change for change in changes_in_stack(repository)
                   if change.topic == topic and commit in change.patch_sets.values()), None)
    if change is not None:
        number = next(number for number, other in change.patch_sets.items() if other == commit)
        set_vote(repository, change.change_id, VERIFIED, value, by='ci', patch_set=number,
                 message=tail)
    return value
