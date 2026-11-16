# SPDX-License-Identifier: Zlib
"""Built-in checks that vote Verified."""

import contextlib
import os
import signal
import subprocess
import sys

from plustwo.config.settings import setting
from plustwo.git.refs import git_path, toplevel
from plustwo.git.repository import git
from plustwo.model.vote import VERIFIED
from plustwo.paths import LAUNCHER
from plustwo.review.changes import changes_in_stack
from plustwo.review.stacks import stack_ref
from plustwo.review.votes import set_vote


CHECK_LOG_LINES = 20


def cancel_check(repository=None):
    """Stops a running check of the repository, if there is one."""
    pid_file = git_path('plustwo/check.pid', repository)
    if not pid_file.exists():
        return
    with contextlib.suppress(ValueError, ProcessLookupError, PermissionError):
        os.killpg(int(pid_file.read_text().split()[0]), signal.SIGTERM)
    pid_file.unlink(missing_ok=True)


def start_check(topic, repository=None):
    """Starts the check (setting checks.command) of a stack's top change in
    the background, after stopping one still running: a newer upload makes
    its result useless. Returns whether a check started.
    """
    if not setting('checks.command', repository=repository):
        return False
    cancel_check(repository)
    top = git('rev-parse', stack_ref(topic), repository=repository)
    root = toplevel(repository)
    process = subprocess.Popen([sys.executable, str(LAUNCHER), 'check-run',
                                topic, top], cwd=root, start_new_session=True,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    pid_file = git_path('plustwo/check.pid', repository)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(f'{process.pid} {top}\n')
    return True


def run_check(topic, commit=None, repository=None):
    """Runs the check of a stack's top change (or commit) in the worktree
    .git/plustwo/check, setting checks.setup first if there is one, and
    votes Verified by ci with the end of the log. The log stays in
    .git/plustwo/check.log. Returns the vote, None without checks.command.
    """
    command = setting('checks.command', repository=repository)
    if not command:
        return None
    commit = commit or git('rev-parse', stack_ref(topic), repository=repository)
    worktree = git_path('plustwo/check', repository)
    if not (worktree / '.git').exists():
        git('worktree', 'prune', repository=repository)
        git('worktree', 'add', '--detach', '--force', str(worktree), commit,
            repository=repository)
    else:
        git('checkout', '--quiet', '--detach', '--force', commit, repository=str(worktree))
    log_path = git_path('plustwo/check.log', repository)
    with open(log_path, 'w') as log:
        status = 0
        for step in filter(None, (setting('checks.setup', repository=repository), command)):
            log.write(f'$ {step}\n')
            log.flush()
            status = subprocess.run(step, shell=True, cwd=worktree, stdout=log,
                                    stderr=subprocess.STDOUT).returncode
            if status:
                break
    value = 1 if 0 == status else -1
    tail = '\n'.join(log_path.read_text(errors='replace').rstrip('\n').split('\n')
                     [-CHECK_LOG_LINES:])
    change = next((change for change in changes_in_stack(repository)
                   if change.topic == topic and commit in change.patch_sets.values()), None)
    if change is not None:
        number = next(number for number, other in change.patch_sets.items() if other == commit)
        set_vote(change.change_id, VERIFIED, value, by='ci', patch_set=number, message=tail,
                 repository=repository)
    return value
