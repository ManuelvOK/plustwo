"""Checks of the settings files: where they are read from, which one wins,
and the move of the old git config change.base.

XDG_CONFIG_HOME points into a scratch directory, so the user's real
settings are never read or written.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class SettingsTest(unittest.TestCase):
    """plustwo config and review.base in a scratch repository."""

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix='plustwo-settings-'))
        self.repo = self.directory / 'repo'
        self.environment = dict(os.environ, XDG_CONFIG_HOME=str(self.directory / 'config'),
                                GIT_CONFIG_GLOBAL=str(self.directory / 'gitconfig'),
                                GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t',
                                GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@t')
        self.git('init', '--quiet', '-b', 'main', str(self.repo), cwd=self.directory)

    def tearDown(self):
        shutil.rmtree(self.directory)

    def git(self, *arguments, cwd=None):
        return subprocess.run(['git', *arguments], cwd=cwd or self.repo, env=self.environment,
                              capture_output=True, text=True, check=True).stdout.strip()

    def plustwo(self, *arguments, check=True):
        result = subprocess.run([str(ROOT / 'plustwo'), *arguments], cwd=self.repo,
                                env=self.environment, capture_output=True, text=True)
        if check:
            self.assertEqual(0, result.returncode, result.stderr)
        return result

    def test_repository_wins_over_user(self):
        self.plustwo('config', '--global', 'review.base', 'develop')
        self.assertEqual('develop', self.plustwo('config', 'review.base').stdout.strip())
        self.plustwo('config', 'review.base', 'trunk')
        self.assertEqual('trunk', self.plustwo('config', 'review.base').stdout.strip())
        self.assertTrue((self.directory / 'config' / 'plustwo' / 'config').exists())
        self.assertTrue((self.repo / '.git' / 'plustwo' / 'config').exists())
        self.plustwo('config', '--unset', 'review.base')
        self.assertEqual('develop', self.plustwo('config', 'review.base').stdout.strip())
        listed = self.plustwo('config').stdout
        self.assertIn('review.base=develop', listed)

    def test_nothing_goes_into_git_config(self):
        self.plustwo('config', 'review.base', 'trunk')
        self.plustwo('config', '--global', 'keys.preset', 'emacs')
        local = subprocess.run(['git', 'config', '--local', '--list'], cwd=self.repo,
                               env=self.environment, capture_output=True, text=True).stdout
        self.assertNotIn('review.base', local)
        self.assertFalse((self.directory / 'gitconfig').exists())

    def test_unset_setting_fails(self):
        result = self.plustwo('config', 'review.base', check=False)
        self.assertEqual(1, result.returncode)

    def test_old_change_base_is_read_and_moved(self):
        self.git('config', 'change.base', 'legacy')
        self.assertEqual('legacy', self.plustwo('config', 'review.base').stdout.strip())
        output = self.plustwo('init').stdout
        self.assertIn('change.base -> review.base', output)
        self.assertEqual('', subprocess.run(['git', 'config', '--local', 'change.base'],
                                            cwd=self.repo, env=self.environment,
                                            capture_output=True, text=True).stdout)
        self.assertEqual('legacy', self.plustwo('config', 'review.base').stdout.strip())


if __name__ == '__main__':
    unittest.main()
