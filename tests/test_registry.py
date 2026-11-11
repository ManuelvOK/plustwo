# SPDX-License-Identifier: Zlib
"""Checks of the registry of repositories and the dashboard: changes of
several repositories, sections, WIP, the uploader and abandoned stacks.
"""

import os
import tempfile
import unittest
from pathlib import Path

from test_stacks import ENVIRONMENT, Repository


class RegistryTest(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        state = Path(self.directory.name) / 'state'
        ENVIRONMENT['XDG_STATE_HOME'] = str(state)
        self.repos = []
        for name in ('alpha', 'beta'):
            path = Path(self.directory.name) / name
            path.mkdir()
            repo = Repository(path)
            repo.git('switch', '--quiet', '-c', 'feat')
            repo.commit('f.txt', f'{name}\n', f'Change in {name}')
            self.repos.append(repo)

    def tearDown(self):
        self.directory.cleanup()

    def upload(self, repo, *arguments, user='agent'):
        ENVIRONMENT['PLUSTWO_USER'] = user
        try:
            return repo.change('upload', *arguments)
        finally:
            del ENVIRONMENT['PLUSTWO_USER']

    def test_dashboard_over_repositories(self):
        alpha, beta = self.repos
        self.upload(alpha)
        self.upload(beta, '--wip')
        self.assertIn('alpha', alpha.change('repos'))
        dashboard = alpha.change('list')
        # Every section shows, empty ones say so
        for title in ('Your turn (1)', 'Has drafts (0)', 'Work in progress (1)',
                      'Outgoing (0)', 'Incoming (2)'):
            self.assertIn(title, dashboard)
        self.assertIn('(none)', dashboard)
        # Started in alpha, a change of beta is found by repo:prefix
        self.assertIn('Change in beta', alpha.change('show', 'beta:I'))
        # WIP can't be submitted
        alpha.change('vote', 'beta:I', 'cr', '2')
        self.assertIn('work in progress', alpha.change('submit', 'beta:I', check=False))
        self.upload(beta, '--ready')
        self.assertIn('Work in progress (0)', alpha.change('list'))

    def test_outgoing_and_abandon(self):
        alpha, _beta = self.repos
        self.upload(alpha, user='Author')
        ENVIRONMENT['PLUSTWO_USER'] = 'Author'
        try:
            self.assertIn('Outgoing (1)', alpha.change('list'))
        finally:
            del ENVIRONMENT['PLUSTWO_USER']
        alpha.change('abandon', 'feat')
        self.assertFalse(alpha.stack('feat'))
        alpha.change('restore', 'feat')
        self.assertTrue(alpha.stack('feat'))

    def test_folding_and_order(self):
        alpha, _beta = self.repos
        self.upload(alpha)
        self.assertNotIn('[folded]', alpha.change('list'))
        state = Path(ENVIRONMENT['XDG_STATE_HOME']) / 'plustwo' / 'dashboard'
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text('[dashboard]\n\tsections = incoming,wip\n\tfolded = wip\n')
        dashboard = alpha.change('list')
        self.assertTrue(dashboard.startswith('Incoming'))
        self.assertIn('Work in progress (0) [folded]', dashboard)

    def test_check_votes_verified(self):
        alpha, _beta = self.repos
        self.upload(alpha)
        alpha.change('config', 'checks.command', 'test -f f.txt')
        self.assertIn('Verified+1', alpha.change('check'))
        alpha.change('config', 'checks.command', 'exit 3')
        self.assertIn('Verified-1', alpha.change('check'))
        self.assertIn('Verified-1 by ci', alpha.change('show', 'alpha:I'))


if __name__ == '__main__':
    unittest.main()
