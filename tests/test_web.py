# SPDX-License-Identifier: Zlib
"""Checks of the web interface's server: who may talk to it, and that its
API reads and writes through the core.
"""

import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_stacks import ENVIRONMENT, ROOT, Repository  # noqa: E402

sys.path.insert(0, str(ROOT))
_loader = importlib.machinery.SourceFileLoader('plustwo_core', str(ROOT / 'plustwo'))
_spec = importlib.util.spec_from_loader('plustwo_core', _loader)
core = importlib.util.module_from_spec(_spec)
_loader.exec_module(core)
import plustwo_web  # noqa: E402


class WebTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._environment = dict(os.environ)
        os.environ.update(ENVIRONMENT)
        cls._directory = tempfile.TemporaryDirectory()
        cls.repository = Repository(cls._directory.name)
        cls.repository.git('switch', '--quiet', '-c', 'feature')
        cls.repository.commit('base.txt', 'one\nTWO\nthree\n', 'Change two')
        cls.repository.change('upload')
        cls.server = plustwo_web.make_server(core, [cls._directory.name], port=0)
        cls.port = cls.server.plustwo.port
        cls.token = cls.server.plustwo.token
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls._directory.cleanup()
        os.environ.clear()
        os.environ.update(cls._environment)

    def request(self, path, body=None, token=True, headers=None):
        """(status, parsed JSON or text) of a request to the server."""
        request = urllib.request.Request(
            f'http://127.0.0.1:{self.port}{path}',
            data=None if body is None else json.dumps(body).encode(),
            headers={**({'X-Plustwo-Token': self.token} if token else {}),
                     **({'Content-Type': 'application/json'} if body is not None else {}),
                     **(headers or {})})
        try:
            with urllib.request.urlopen(request) as response:
                status, data = response.status, response.read()
        except urllib.error.HTTPError as error:
            with error:
                status, data = error.code, error.read()
        try:
            return status, json.loads(data)
        except ValueError:
            return status, data.decode()

    def change_id(self):
        _, data = self.request('/api/stacks')
        return data['stacks'][0]['changes'][0]['id']

    def test_page_has_the_token(self):
        status, page = self.request('/', token=False)
        self.assertEqual(200, status)
        self.assertIn(self.token, page)
        self.assertEqual(200, self.request('/app.js', token=False)[0])

    def test_refuses_strangers(self):
        self.assertEqual(403, self.request('/api/stacks', token=False)[0])
        self.assertEqual(403, self.request('/api/stacks', headers={'X-Plustwo-Token': 'x'})[0])
        self.assertEqual(403, self.request('/api/stacks',
                                           headers={'Origin': 'http://evil.example'})[0])
        self.assertEqual(403, self.request('/api/stacks', headers={'Host': 'evil.example'})[0])
        self.assertEqual(403, self.request('/api/action', body={}, token=False)[0])

    def test_reads(self):
        status, stacks = self.request('/api/stacks')
        self.assertEqual(200, status)
        self.assertEqual('feature', stacks['stacks'][0]['topic'])
        change_id = self.change_id()
        _, change = self.request(f'/api/change?id={change_id}')
        self.assertEqual('Change two', change['subject'])
        self.assertEqual('Change two', change['message_parts']['subject'])
        self.assertTrue(change['message_parts']['trailers'][0].startswith('Change-Id: I'))
        self.assertEqual(['/COMMIT_MSG', 'base.txt'], [file['path'] for file in change['files']])
        self.assertEqual([(1, '-')], [(entry['number'], entry['kind'])
                                      for entry in change['history']])
        _, diff = self.request(f'/api/diff?id={change_id}&path=base.txt&context=all')
        kinds = [(row['old']['kind'], row['new']['kind']) for row in diff['rows']]
        # two and TWO share no character, so they don't pair
        self.assertIn(('removed', 'empty'), kinds)
        self.assertIn(('empty', 'added'), kinds)
        self.assertIsInstance(diff['threads'], dict)
        self.assertEqual(200, self.request('/api/version')[0])
        _, keys = self.request('/api/keys')
        self.assertIn('send', keys['keys'])

    def test_comment_reply_send_vote(self):
        change_id = self.change_id()

        def act(action, **fields):
            status, data = self.request('/api/action',
                                        body={'repo': 0, 'id': change_id, 'action': action,
                                              **fields})
            self.assertEqual(200, status, data)
            return data

        number = act('comment', place='base.txt:2', side='old', text='Why?')['number']
        act('send', message='Looks fine otherwise', vote=1)
        self.repository.change('reply', change_id[:8], str(number), 'Because.')
        act('done', number=number)
        self.assertEqual('unresolved', act('resolve', number=number)['status'])
        act('resolve', number=number)
        act('reviewed', path='base.txt')
        self.assertEqual(1, act('approve', send=True)['sent'])
        _, change = self.request(f'/api/change?id={change_id}')
        thread = next(comment for comment in change['comments'] if comment['number'] == number)
        self.assertEqual('resolved', thread['status'])
        self.assertEqual('old', thread['side'])
        self.assertEqual('+2', change['code_review'])
        self.assertTrue(change['files'][1]['reviewed'])
        self.assertTrue(any(comment['path'] == '/PATCHSET_LEVEL'
                            for comment in change['comments']))
        status, data = self.request('/api/action', body={'repo': 0, 'id': change_id,
                                                         'action': 'nonsense'})
        self.assertEqual(400, status)

    def test_reply_votes_only_when_changed(self):
        # The reply box's step: drafts, message and the vote together
        change_id = self.change_id()
        directory = self._directory.name
        own = core.find_change(change_id, directory).own_vote()
        self.assertEqual((0, None), core.reply(change_id, '', own, directory))
        other = -1 if own != -1 else 1
        sent, voted = core.reply(change_id, '', other, directory)
        self.assertEqual(0, sent)
        self.assertIsNotNone(voted)
        self.assertEqual(other, core.find_change(change_id, directory).own_vote())
        core.reply(change_id, '', own, directory)
        self.assertEqual(own, core.find_change(change_id, directory).own_vote())

    def test_typed_ack_resolves(self):
        # A reply that only acknowledges resolves like the Ack key, and
        # leaves the author's inbox, also as a message on the whole change.
        change_id = self.change_id()

        def act(action, **fields):
            status, data = self.request('/api/action',
                                        body={'repo': 0, 'id': change_id, 'action': action,
                                              **fields})
            self.assertEqual(200, status, data)
            return data

        number = act('comment', place='base.txt:3', text='Three?')['number']
        act('send')
        self.repository.change('reply', change_id[:8], str(number), 'Kept it, see above.')
        act('reply', number=number, text='Ack.')
        act('send', message='OK, thanks!')
        _, change = self.request(f'/api/change?id={change_id}')
        thread = next(comment for comment in change['comments'] if comment['number'] == number)
        self.assertEqual('resolved', thread['status'])
        inbox = self.repository.change('inbox')
        self.assertNotIn(f'#{number} ', inbox)
        self.assertNotIn('OK, thanks!', inbox)
        self.assertTrue(core.is_acknowledgement(' Thanks! '))
        self.assertFalse(core.is_acknowledgement('Ack, but rename it'))

    def test_shown_text_from_core(self):
        # A message without text only resolves, the page shows it as the
        # core does.
        change_id = self.change_id()

        def act(action, **fields):
            status, data = self.request('/api/action',
                                        body={'repo': 0, 'id': change_id, 'action': action,
                                              **fields})
            self.assertEqual(200, status, data)
            return data

        number = act('comment', place='base.txt:2', text='Two?')['number']
        act('send')
        self.repository.change('reply', change_id[:8], str(number), 'Yes.')
        self.assertEqual('resolved', act('resolve', number=number)['status'])
        act('send')
        _, change = self.request(f'/api/change?id={change_id}')
        thread = next(comment for comment in change['comments'] if comment['number'] == number)
        last = thread['messages'][-1]
        self.assertEqual('', last['text'])
        self.assertEqual(core.message_text(core.Message('reviewer', '', unresolved=False)),
                         last['shown'])
        self.assertEqual('Two?', thread['messages'][0]['shown'])


if __name__ == '__main__':
    unittest.main()
