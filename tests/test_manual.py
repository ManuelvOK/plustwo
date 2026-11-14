# SPDX-License-Identifier: Zlib
"""Checks that plustwo survives changes made by hand, without plustwo:
rewritten base branches, moved or deleted refs of our own, broken notes.
Agents and users fiddle with the repository to get around a missing
feature, so none of this may crash the watch or the dashboard. A clean
error message is fine, a traceback is not.
"""

import json
import shutil
import subprocess
import tempfile
import time
import unittest

from test_stacks import ENVIRONMENT, ROOT, Repository

# Long enough for the watch (which looks once a second) to see a change
SETTLE_SECONDS = 1.3


class ManualChangesTest(unittest.TestCase):
    """A stack of three changes with a resolved thread and votes, a watch
    running on it. Each test changes the repository by hand and probes.
    """

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix='plustwo-manual-')
        repo = self.repo = Repository(self.directory)
        repo.git('switch', '--quiet', '-c', 'feature')
        for name in 'abc':
            repo.commit(f'{name}.txt', f'{name}\n', f'Add {name}')
        repo.change('upload')
        self.ids = [repo.git('log', '-1', '--format=%(trailers:key=Change-Id,valueonly)',
                             f'HEAD~{n}') for n in (2, 1, 0)]
        # Change-Ids, not numbers: those count over every registered repository
        repo.change('comment', self.ids[0][:8], 'a.txt:1', 'Fix a', '--resolved')
        repo.approve(self.ids[0][:8])
        repo.approve(self.ids[1][:8])
        self.watch = subprocess.Popen([str(ROOT / 'plustwo'), 'watch', '.', '--since',
                                       '9999999999'], cwd=repo.path, env=ENVIRONMENT,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        time.sleep(SETTLE_SECONDS)

    def tearDown(self):
        shutil.rmtree(self.directory)
        if self.watch.poll() is None:
            self.watch.terminate()
            self.assertNotIn('Traceback', self.watch.communicate()[0])

    def probe(self, step):
        """Checks after a manual change: the running watch lives on, and
        the commands that read every change end without a traceback.
        """
        time.sleep(SETTLE_SECONDS)
        if self.watch.poll() is not None:
            self.fail(f'watch died after {step}:\n{self.watch.stdout.read()}')
        for command in (('list',), ('inbox',), ('show', self.ids[0][:8]),
                        ('show', self.ids[2][:8]),
                        ('watch', '.', '--since', '0', '--for', '0.01')):
            output = self.repo.change(*command, check=False)
            self.assertNotIn('Traceback', output, f'{" ".join(command)} after {step}')

    def submit_two(self):
        self.repo.change('submit', self.ids[1][:8])
        self.probe('submit')

    def rewrite_main(self, *rebase):
        """Rebases main by hand, with git rebase arguments."""
        repo = self.repo
        repo.git('switch', '--quiet', 'main')
        repo.run('git', 'rebase', '--quiet', *rebase)

    # 1 The base branch is rewritten after a submit

    def test_base_reordered_after_submit(self):
        repo = self.repo
        repo.git('switch', '--quiet', 'main')
        repo.commit('m.txt', 'm\n', 'Main work')
        repo.change('rebase', self.ids[2][:8])
        self.submit_two()
        # Main work moves above the submitted changes, which get new hashes
        editor = ROOT / 'tests' / 'move_to_end.py'
        repo.run('git', '-c', f'sequence.editor=python3 {editor} Main', 'rebase', '--quiet',
                 '--interactive', 'HEAD~3')
        self.probe('reordering main')
        repo.git('switch', '--quiet', 'feature')
        repo.change('pull', check=False)
        repo.change('upload', check=False)
        self.probe('pull and upload')

    def test_base_reordered_under_two_stacks(self):
        # Two stacks on top of submitted changes. Rewriting main takes those
        # off it, so both stacks have them again: two copies of a change.
        repo = self.repo
        repo.change('reply', self.ids[0][:8], '1', 'Done')
        repo.git('switch', '--quiet', 'main')
        repo.commit('m.txt', 'm\n', 'Main work')
        repo.change('rebase', self.ids[2][:8])
        self.submit_two()
        repo.git('switch', '--quiet', '-c', 'other', 'main')
        repo.commit('o.txt', 'o\n', 'Add o')
        repo.change('upload')
        self.probe('another stack on top')
        repo.git('switch', '--quiet', 'main')
        editor = ROOT / 'tests' / 'move_to_end.py'
        repo.run('git', '-c', f'sequence.editor=python3 {editor} Main', 'rebase', '--quiet',
                 '--interactive', 'HEAD~3')
        self.probe('reordering main')
        rows = {line for line in repo.change('list').split('\n') if 'Add a' in line}
        self.assertTrue(rows and all('1 resolved' in row for row in rows), rows)

    def test_base_reset_before_submit(self):
        self.submit_two()
        self.repo.git('switch', '--quiet', 'main')
        self.repo.git('reset', '--quiet', '--hard', 'HEAD~2')
        self.probe('resetting main')
        self.repo.approve(self.ids[2][:8])
        self.probe('a vote')

    def test_submitted_commit_amended(self):
        self.submit_two()
        self.repo.git('switch', '--quiet', 'main')
        self.repo.git('commit', '--quiet', '--amend', '-m', 'Add b, amended')
        self.probe('amending main')

    def test_submitted_commit_dropped(self):
        self.submit_two()
        self.rewrite_main('--onto', 'HEAD~2', 'HEAD~1', 'main')
        self.probe('dropping a commit from main')

    # 2 Branches and refs of our own are moved or deleted

    def test_stack_base_renamed_and_deleted(self):
        repo = self.repo
        repo.git('switch', '--quiet', '-c', 'dev', 'main')
        repo.commit('d.txt', 'd\n', 'Dev')
        repo.git('switch', '--quiet', '-c', 'devfeat')
        repo.commit('e.txt', 'e\n', 'Add e')
        repo.change('config', 'review.base', 'dev')
        repo.change('upload')
        repo.change('config', '--unset', 'review.base')
        self.probe('upload on dev')
        repo.git('switch', '--quiet', 'main')
        repo.git('branch', '--quiet', '-m', 'dev', 'dev2')
        self.probe('renaming dev')
        repo.git('branch', '--quiet', '-D', 'dev2')
        self.probe('deleting dev')

    def test_base_ref_dangling(self):
        self.repo.git('symbolic-ref', 'refs/plustwo/bases/feature', 'refs/heads/nowhere')
        self.probe('pointing the base at no branch')
        self.repo.git('update-ref', '--no-deref', 'refs/plustwo/bases/feature', 'HEAD')
        self.probe('making the base a plain ref')

    def test_stack_ref_moved_and_deleted(self):
        self.repo.git('update-ref', 'refs/plustwo/stacks/feature', 'HEAD~2')
        self.probe('moving the stack back')
        self.repo.git('update-ref', '-d', 'refs/plustwo/stacks/feature')
        self.probe('deleting the stack')

    def test_patch_set_ref_deleted(self):
        self.repo.git('update-ref', '-d', f'refs/plustwo/patchsets/{self.ids[0]}/1')
        self.probe('deleting a patch set')

    def test_same_change_in_two_stacks(self):
        self.repo.change('comment', self.ids[0][:8], 'a.txt:1', 'Still open')
        self.repo.git('update-ref', 'refs/plustwo/stacks/zcopy', 'HEAD~1')
        self.probe('copying a stack')
        # Both copies have the thread, one note is read for both
        # A change shows in several sections
        rows = {line for line in self.repo.change('list').split('\n') if 'Add a' in line}
        self.assertEqual(2, len(rows), rows)
        self.assertTrue(all('1 unresolved' in row for row in rows), rows)
        self.repo.change('reply', self.ids[0][:8], '1', 'Done', check=False)
        self.probe('a reply')
        self.repo.change('submit', self.ids[1][:8], check=False)
        self.probe('a submit')

    def test_commit_without_change_id_in_stack(self):
        repo = self.repo
        repo.commit('x.txt', 'x\n', 'No Change-Id')
        repo.git('commit', '--quiet', '--amend', '-m', 'No Change-Id', '--no-verify')
        repo.git('update-ref', 'refs/plustwo/stacks/feature', 'HEAD')
        self.probe('a commit without Change-Id')

    def test_change_id_twice_in_stack(self):
        repo = self.repo
        # A split by hand: both halves keep the message and its Change-Id
        repo.commit('c2.txt', 'c2\n', 'placeholder')
        repo.git('commit', '--quiet', '--amend', '-C', 'HEAD~1')
        repo.git('update-ref', 'refs/plustwo/stacks/feature', 'HEAD')
        self.probe('a Change-Id twice')

    # 3 Notes are removed or broken

    def test_note_removed(self):
        self.repo.git('notes', '--ref=review', 'remove', 'HEAD~2')
        self.probe('removing a note')

    def test_notes_ref_deleted(self):
        self.repo.git('update-ref', '-d', 'refs/notes/review')
        self.probe('deleting the notes')

    def test_note_broken(self):
        repo = self.repo
        for commit, text in (('HEAD~2', 'not json'), ('HEAD~1', '[1, 2]'),
                             ('HEAD', json.dumps({'comments': [{'number': 'x'}],
                                                  'votes': [{'label': 'Code-Review'}]}))):
            repo.git('notes', '--ref=review', 'add', '--force', '-m', text, commit)
        self.probe('breaking notes')


if __name__ == '__main__':
    unittest.main()
