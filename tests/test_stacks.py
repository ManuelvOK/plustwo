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
        watch = subprocess.Popen([str(ROOT / 'plustwo'), 'watch', '.'], cwd=repo.path,
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
        watch = subprocess.Popen([str(ROOT / 'plustwo'), 'watch', '.'], cwd=repo.path,
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
