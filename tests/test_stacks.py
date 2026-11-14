# SPDX-License-Identifier: Zlib
"""Checks of the review flow in scratch repositories: upload from any
branch, the reviewer's rebase and submit, the author's pull, the watch
lines and the move from the layout of older versions.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# State and settings of their own, so no test registers its scratch
# repositories with the user's dashboard
_HOME = tempfile.mkdtemp(prefix='plustwo-test-')
ENVIRONMENT = dict(os.environ, PATH=f'{ROOT}:{os.environ["PATH"]}',
                   XDG_STATE_HOME=f'{_HOME}/state', XDG_CONFIG_HOME=f'{_HOME}/config',
                   GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1',
                   GIT_AUTHOR_NAME='Author', GIT_AUTHOR_EMAIL='author@example.org',
                   GIT_COMMITTER_NAME='Author', GIT_COMMITTER_EMAIL='author@example.org')


# For watch: no catch-up with what earlier tests' watches left in the state
FUTURE = '9999999999'


class Repository:
    """A scratch repository with plustwo set up."""

    def __init__(self, directory):
        self.path = Path(directory)
        self.git('init', '--quiet', '--initial-branch=main')
        self.write('base.txt', 'one\ntwo\nthree\n')
        self.git('add', '-A')
        self.git('commit', '--quiet', '-m', 'Base')
        self.change('init')

    def run(self, *command, check=True):
        result = subprocess.run(command, cwd=self.path, env=ENVIRONMENT, capture_output=True,
                                text=True)
        if check and 0 != result.returncode:
            raise AssertionError(f'{command} failed: {result.stderr}{result.stdout}')
        return result

    def git(self, *arguments, check=True):
        return self.run('git', *arguments, check=check).stdout.strip()

    def change(self, *arguments, check=True):
        result = self.run(str(ROOT / 'plustwo'), *arguments, check=check)
        return (result.stdout + result.stderr).strip()

    def write(self, name, text):
        (self.path / name).write_text(text)

    def commit(self, name, text, subject):
        self.write(name, text)
        self.git('add', name)
        self.git('commit', '--quiet', '-m', subject)
        return self.git('rev-parse', 'HEAD')

    def stack(self, topic):
        return self.git('rev-parse', '--verify', '--quiet', f'refs/plustwo/stacks/{topic}',
                        check=False)

    def approve(self, change):
        self.change('vote', change, 'Code-Review', '2')


class FlowTest(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix='git-change-flow-')
        self.repo = Repository(self.directory)

    def tearDown(self):
        shutil.rmtree(self.directory)

    def two_changes(self, branch='feature'):
        repo = self.repo
        repo.git('switch', '--quiet', '-c', branch)
        repo.commit('a.txt', 'a\n', 'Add a')
        repo.commit('b.txt', 'b\n', 'Add b')

    def test_wip_per_change(self):
        # --ready CHANGE marks it and the changes below, --wip CHANGE it and
        # the changes above, the others stay as they are.
        repo = self.repo
        self.two_changes()
        repo.commit('c.txt', 'c\n', 'Add c')
        repo.change('upload', '--wip')
        commits = [repo.git('rev-parse', f'HEAD~{n}') for n in (2, 1, 0)]
        ids = [repo.git('log', '-1', '--format=%(trailers:key=Change-Id,valueonly)', commit)
               for commit in commits]

        def wip():
            return [json.loads(repo.git('notes', '--ref=review', 'show', commit)).get('wip')
                    for commit in commits]

        repo.git('switch', '--quiet', 'main')
        self.assertIn(f'{ids[1][:8]} and the changes below it ready for review',
                      repo.change('upload', '--ready', ids[1][:8]))
        self.assertEqual([False, False, True], wip())
        repo.git('switch', '--quiet', 'feature')
        repo.change('upload', '--wip', ids[0][:8])
        self.assertEqual([True, True, True], wip())
        repo.change('upload', '--ready', ids[0][:8])
        self.assertEqual([False, True, True], wip())
        repo.change('upload', '--wip', ids[2][:8], check=False)
        self.assertEqual([False, True, True], wip())
        self.assertIn('is not in the stack', repo.change('upload', ids[2][:8], '--ready',
                                                          'Ifffffff', check=False))
        repo.change('upload', '--ready')
        self.assertEqual([False, False, False], wip())

    def test_upload_from_any_branch(self):
        repo = self.repo
        self.two_changes('feature/x')
        output = repo.change('upload')
        self.assertIn('patch set 1', output)
        self.assertEqual(repo.stack('feature/x'), repo.git('rev-parse', 'HEAD'))
        self.assertIn('already', repo.change('upload', check=False))
        repo.git('commit', '--quiet', '--amend', '-m', 'Add b, better\n\n'
                 + repo.git('log', '-1', '--format=%B').split('\n\n', 1)[1])
        self.assertIn('patch set 2', repo.change('upload'))
        # The base branch can't be uploaded
        repo.git('switch', '--quiet', 'main')
        self.assertIn('base branch', repo.change('upload', check=False))

    def test_thread_places(self):
        # Like Gerrit, no thread on a file or line the patch set doesn't have
        repo = self.repo
        self.two_changes()
        repo.change('upload')
        self.assertIn('no file', repo.change('comment', '2', 'nope.txt:1', 'x', check=False))
        self.assertIn('not line 2', repo.change('comment', '2', 'b.txt:2', 'x', check=False))
        self.assertIn('#1 added', repo.change('comment', '2', 'b.txt:1', 'Fine line'))
        self.assertIn('#2 added', repo.change('comment', '2', 'b.txt', 'Whole file'))
        # A message on the whole change starts resolved, --status still wins
        repo.change('comment', '2', '/PATCHSET_LEVEL', 'Looks good')
        repo.change('comment', '2', '/PATCHSET_LEVEL', 'Explain?', '--status', 'unresolved')
        show = repo.change('show', '2')
        self.assertIn('#3 [resolved] Change', show)
        self.assertIn('#4 [unresolved] Change', show)
        self.assertNotIn('/PATCHSET_LEVEL', show)
        self.assertNotIn('/PATCHSET_LEVEL', repo.change('inbox'))
        # status says what it did
        self.assertIn('#1 resolved', repo.change('status', '2', '1', 'resolved'))

    def test_review_note_follows_upload(self):
        repo = self.repo
        self.two_changes()
        repo.change('upload')
        repo.change('comment', '2', 'b.txt:1', 'Rename it')
        # The author amends locally, the reviewer comments on the server meanwhile
        repo.write('b.txt', 'b2\n')
        repo.git('commit', '--quiet', '--amend', '--no-edit', '-a')
        repo.change('comment', '2', 'b.txt', 'And another thing')
        repo.change('upload')
        show = repo.change('show', '2')
        self.assertIn('Rename it', show)
        self.assertIn('And another thing', show)

    def test_submit_part_and_whole_stack_leaves_checkout_alone(self):
        repo = self.repo
        self.two_changes()
        repo.change('upload')
        repo.approve('1')
        repo.approve('2')
        repo.write('a.txt', 'local edit\n')
        repo.git('switch', '--quiet', 'feature')
        self.assertIn('Submitted', repo.change('submit', '1'))
        self.assertEqual(repo.git('rev-parse', 'main'), repo.git('rev-parse', 'HEAD~1'))
        self.assertTrue(repo.stack('feature'))
        self.assertEqual(repo.git('rev-parse', '--abbrev-ref', 'HEAD'), 'feature')
        self.assertIn('Submitted', repo.change('submit', '1'))
        self.assertFalse(repo.stack('feature'))
        self.assertEqual(repo.git('rev-parse', '--abbrev-ref', 'HEAD'), 'feature')
        self.assertEqual((repo.path / 'a.txt').read_text(), 'local edit\n')
        repo.git('checkout', '--quiet', '--', 'a.txt')
        self.assertIn('all is submitted', repo.change('pull'))

    def test_rebase_clean_keeps_review_and_pull_follows(self):
        repo = self.repo
        self.two_changes()
        repo.change('upload')
        repo.approve('1')
        repo.change('comment', '2', 'b.txt:1', 'Hm')
        repo.git('switch', '--quiet', 'main')
        repo.commit('other.txt', 'other\n', 'Other work')
        repo.git('switch', '--quiet', 'feature')
        self.assertIn('rebased, 2 new', repo.change('rebase', '--topic', 'feature'))
        main = repo.git('rev-parse', 'main')
        stack = repo.stack('feature')
        self.assertEqual(repo.git('rev-parse', f'{stack}~2'), main)
        show = repo.change('show', '1')
        self.assertIn('Code-Review+2', show)
        self.assertIn('copied from PS1', show)
        self.assertIn('Hm', repo.change('show', '2'))
        # The author's branch is untouched until pull
        self.assertNotEqual(repo.git('rev-parse', 'HEAD'), stack)
        self.assertIn('moved to the reviewed stack', repo.change('pull'))
        self.assertEqual(repo.git('rev-parse', 'HEAD'), stack)
        self.assertIn('up to date', repo.change('pull'))

    def test_pull_moves_newer_local_commits(self):
        repo = self.repo
        self.two_changes()
        repo.change('upload')
        repo.git('switch', '--quiet', 'main')
        repo.commit('other.txt', 'other\n', 'Other work')
        repo.git('switch', '--quiet', 'feature')
        repo.change('rebase', '--topic', 'feature')
        repo.commit('c.txt', 'c\n', 'Add c')
        self.assertIn('1 local commit(s) moved onto feature', repo.change('pull'))
        self.assertEqual(repo.git('rev-parse', 'HEAD~1'), repo.stack('feature'))

    def test_rebase_conflict_asks_author(self):
        repo = self.repo
        repo.git('switch', '--quiet', '-c', 'feature')
        repo.commit('base.txt', 'one\nTWO\nthree\n', 'Change two')
        repo.change('upload')
        before = repo.stack('feature')
        repo.git('switch', '--quiet', 'main')
        repo.commit('base.txt', 'one\nzwei\nthree\n', 'Change two differently')
        output = repo.change('rebase', '1')
        self.assertIn('conflicts', output)
        self.assertEqual(repo.stack('feature'), before)
        self.assertIn('rebase', repo.change('inbox'))
        # The author rebases locally and uploads, which clears the request
        repo.git('switch', '--quiet', 'feature')
        repo.run('git', 'rebase', 'main', check=False)
        repo.write('base.txt', 'one\nZWEI\nthree\n')
        repo.git('add', 'base.txt')
        repo.run('git', '-c', 'core.editor=true', 'rebase', '--continue')
        repo.change('upload')
        self.assertNotIn('rebase', repo.change('inbox'))

    def test_watch_lines(self):
        repo = self.repo
        self.two_changes()
        repo.change('upload')
        repo.approve('1')
        repo.approve('2')
        watch = subprocess.Popen([str(ROOT / 'plustwo'), 'watch', '.', '--since', FUTURE],
                                 cwd=repo.path,
                                 env=ENVIRONMENT, stdout=subprocess.PIPE, text=True)
        try:
            time.sleep(1.5)
            repo.git('switch', '--quiet', 'main')
            repo.commit('other.txt', 'other\n', 'Other work')
            time.sleep(1.5)
            repo.change('rebase', '1')
            time.sleep(1.5)
            repo.change('submit', '2')
            time.sleep(1.5)
        finally:
            watch.terminate()
        lines = watch.communicate()[0].split('\n')
        name = repo.path.name
        self.assertIn(f'rebased {name} feature', lines)
        self.assertIn(f'submitted {name} feature: 2 changes', lines)
        self.assertFalse(any(line.startswith('vote') for line in lines), lines)

    def watch(self, *arguments):
        """Runs watch until it ends by itself, returns its lines."""
        return self.repo.run(str(ROOT / 'plustwo'), 'watch', '.', *arguments).stdout.split('\n')

    def test_watch_restart_loses_nothing(self):
        repo = self.repo
        self.two_changes()
        repo.change('upload')
        name = repo.path.name
        ids = [repo.git('log', '-1', '--format=%(trailers:key=Change-Id,valueonly)', commit)[:8]
               for commit in ('HEAD~1', 'HEAD')]
        watch = subprocess.Popen([str(ROOT / 'plustwo'), 'watch', '.', '--for', '0.05'],
                                 cwd=repo.path, env=ENVIRONMENT, stdout=subprocess.PIPE, text=True)
        time.sleep(1)
        # One write, so the watch can't see the thread unresolved in between
        repo.change('comment', '1', 'a.txt:1', 'First', '--resolved')
        lines = watch.communicate()[0].split('\n')
        self.assertIn(f'fyi {name} {ids[0]} #1 (resolved) a.txt:1', lines)
        self.assertIn('watch ended, restart it to continue', lines)
        # While no watch runs
        time.sleep(1.1)
        repo.change('comment', '2', 'b.txt:1', 'Second')
        repo.approve('1')
        repo.change('submit', '1')
        # Times are in seconds: an event in the second a watch starts
        # comes again on the next start, rather than never
        time.sleep(1.1)
        lines = self.watch('--for', '0.01')
        self.assertIn(f'review {name} {ids[1]}: 1 new', lines)
        self.assertIn(f'vote {name} {ids[0]} Code-Review+2 PS1 by reviewer', lines)
        self.assertIn(f'submitted {name} feature: 1 change', lines)
        # Reported once: the next start has nothing new
        self.assertEqual(['watch ended, restart it to continue', ''], self.watch('--for', '0.01'))
        # --since goes further back
        self.assertIn(f'fyi {name} {ids[0]} #1 (resolved) a.txt:1',
                      self.watch('--since', '0', '--for', '0.01'))

    def test_watch_change_in_several_stacks(self):
        # An old commit of a change can stay in another stack, e.g. when a
        # base branch moved. Its copy there lacks the newer messages.
        repo = self.repo
        repo.git('switch', '--quiet', '-c', 'feature')
        old = repo.commit('a.txt', 'a\n', 'Add a')
        repo.change('upload')
        repo.write('a.txt', 'a2\n')
        repo.git('commit', '--quiet', '--all', '--amend', '--no-edit')
        repo.change('upload')
        repo.change('comment', '1', 'a.txt:1', 'First')
        repo.change('reply', '1', '1', 'Second')
        repo.git('update-ref', 'refs/plustwo/stacks/zcopy', old)
        lines = self.watch('--since', '0', '--for', '0.01')
        self.assertIn('watch ended, restart it to continue', lines)
        self.assertTrue(any(line.startswith('review ') for line in lines), lines)

    def test_resolved_reply_reaches_author(self):
        # A resolved reply sent together with the submit: watch prints it,
        # and show still finds the submitted change.
        repo = self.repo
        repo.git('switch', '--quiet', '-c', 'feature')
        repo.commit('a.txt', 'a\n', 'Add a')
        repo.change('upload')
        change_id = repo.git('log', '-1', '--format=%(trailers:key=Change-Id,valueonly)')
        time.sleep(1.1)  # news is newer than the upload, in seconds
        repo.change('comment', '1', 'a.txt:1', 'Noted for later', '--resolved')
        inbox = repo.change('inbox')
        self.assertIn('(resolved) a.txt:1', inbox)
        self.assertIn('Noted for later', inbox)
        time.sleep(1.1)
        repo.change('reply', change_id[:8], '1', 'Thanks.')
        self.assertNotIn('Noted for later', repo.change('inbox'))
        watch = subprocess.Popen([str(ROOT / 'plustwo'), 'watch', '.', '--since', FUTURE],
                                 cwd=repo.path,
                                 env=ENVIRONMENT, stdout=subprocess.PIPE, text=True)
        try:
            time.sleep(1.5)
            repo.change('comment', '1', 'a.txt', 'Keep the text as is', '--resolved')
            repo.approve('1')
            repo.change('submit', '1')
            time.sleep(1.5)
        finally:
            watch.terminate()
        lines = watch.communicate()[0].split('\n')
        name = repo.path.name
        self.assertIn(f'fyi {name} {change_id[:8]} #2 (resolved) a.txt', lines)
        self.assertIn('    Keep the text as is', lines)
        self.assertIn(f'submitted {name} feature: 1 change', lines)
        show = repo.change('show', change_id[:8])
        self.assertIn('(Submitted)', show)
        self.assertIn('Keep the text as is', show)

    def test_stack_keeps_its_base(self):
        # Uploaded from a worktree whose base is spec, reviewed from one
        # whose base is main
        repo = self.repo
        repo.git('switch', '--quiet', '-c', 'spec')
        repo.commit('spec.txt', 'spec\n', 'Spec')
        repo.git('switch', '--quiet', '-c', 'feature')
        repo.commit('a.txt', 'a\n', 'Add a')
        repo.commit('b.txt', 'b\n', 'Add b')
        repo.change('config', 'review.base', 'spec')
        repo.change('upload')
        self.assertEqual(repo.git('symbolic-ref', 'refs/plustwo/bases/feature'),
                         'refs/heads/spec')
        repo.change('config', '--unset', 'review.base')
        listing = repo.change('list')
        self.assertIn('→ spec', listing)
        self.assertNotIn('Spec', listing)
        # Without a recorded base, the setting's: the spec commit is in it
        repo.git('symbolic-ref', '--delete', 'refs/plustwo/bases/feature')
        self.assertIn('Spec', repo.change('list'))
        repo.change('config', 'review.base', 'spec')
        repo.change('upload')
        repo.change('config', '--unset', 'review.base')
        self.assertNotIn('Spec', repo.change('list'))
        # spec moves on: the stack needs a rebase onto spec, not main
        repo.git('switch', '--quiet', 'spec')
        repo.commit('spec.txt', 'spec 2\n', 'Spec 2')
        repo.git('switch', '--quiet', 'feature')
        self.assertIn('rebased, 2 new', repo.change('rebase', '--topic', 'feature'))
        self.assertEqual(repo.git('rev-parse', f'{repo.stack("feature")}~2'),
                         repo.git('rev-parse', 'spec'))
        repo.approve('1')
        repo.approve('2')
        main = repo.git('rev-parse', 'main')
        self.assertIn('Submitted', repo.change('submit', '2'))
        self.assertEqual(repo.git('rev-parse', 'main'), main)
        self.assertEqual(repo.git('log', '-1', '--format=%s', 'spec'), 'Add b')
        self.assertIn('(Submitted)', repo.change('show', repo.git(
            'log', '-1', '--format=%(trailers:key=Change-Id,valueonly)', 'spec')[:8]))

    def test_upload_moves_stack_to_new_base(self):
        repo = self.repo
        repo.git('switch', '--quiet', '-c', 'spec')
        repo.commit('spec.txt', 'spec\n', 'Spec')
        repo.git('switch', '--quiet', '-c', 'feature')
        repo.commit('a.txt', 'a\n', 'Add a')
        repo.change('config', 'review.base', 'spec')
        repo.change('upload')
        repo.change('config', '--unset', 'review.base')
        # Same commits, base main: the spec commit joins the stack
        output = repo.change('upload')
        self.assertIn('feature: moved from spec to main', output)
        self.assertIn('Spec', repo.change('list'))
        self.assertEqual(repo.git('symbolic-ref', 'refs/plustwo/bases/feature'),
                         'refs/heads/main')

    def test_old_layout_is_migrated(self):
        repo = self.repo
        self.two_changes('review/old')
        commits = repo.git('rev-list', '--reverse', 'main..HEAD').split()
        for commit in commits:
            message = repo.git('log', '-1', '--format=%B', commit)
            change_id = message.split('Change-Id: ')[1].strip()
            repo.git('update-ref', f'refs/patchsets/{change_id}/1', commit)
        note = {'comments': [{'number': 1, 'place': 'a.txt:1', 'patch_set': 1,
                              'status': 'done', 'side': 'new',
                              'messages': [{'author': 'reviewer', 'text': 'x', 'sent': True}]}],
                'requests': [{'type': 'apply', 'comment': 1, 'handled': False}]}
        repo.git('notes', '--ref=refs/notes/review', 'add', '-f', '-m', json.dumps(note),
                 commits[0])
        # A local commit not uploaded yet stays out of the stack
        repo.commit('c.txt', 'c\n', 'Add c')
        listing = repo.change('list')
        self.assertIn('old', listing)
        self.assertIn('[resolved]', repo.change('show', '1'))
        self.assertEqual(repo.stack('old'), commits[1])
        self.assertTrue(repo.git('for-each-ref', 'refs/patchsets/'))
        repo.change('init')
        self.assertFalse(repo.git('for-each-ref', 'refs/patchsets/'))
        self.assertIn('Add b', repo.change('list'))
        # Uploading from the old branch goes on with the same topic
        self.assertIn('patch set 1', repo.change('upload'))
        self.assertEqual(repo.stack('old'), repo.git('rev-parse', 'HEAD'))


if __name__ == '__main__':
    unittest.main()
