# SPDX-License-Identifier: GPL-3.0-or-later
"""Web interface of plustwo, for the reviewer. Started by `plustwo serve`.

A thin frontend like the terminal interface: a small HTTP server on
127.0.0.1 that serves the page in web/ and a JSON API over the core's
functions. Nothing of the review logic lives here.

Any web page open in the browser could send requests to 127.0.0.1. So the
server makes a random token at start, puts it into the page it serves and
refuses API requests without it, and it refuses requests whose Host or
Origin is not its own address.
"""

import hashlib
import json
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

WEB_DIRECTORY = Path(__file__).resolve().parent / 'web'
STATIC_FILES = {'/app.js': 'application/javascript', '/style.css': 'text/css'}
TOKEN_HEADER = 'X-Plustwo-Token'
DEFAULT_PORT = 8022
# bat themes for the page's light and dark colours
THEMES = {'light': 'GitHub', 'dark': 'Monokai Extended'}


# ── what the API sends: the core's objects as JSON ────────────────────────

def message_json(message):
    return {'author': message.author, 'text': message.text, 'sent': message.sent,
            'unresolved': message.unresolved}


def comment_json(comment):
    path, _, line = comment.place.rpartition(':')
    if not path or not line.isdigit():
        path, line = comment.place, ''
    return {'number': comment.number, 'id': comment.id, 'place': comment.place, 'path': path,
            'line': int(line) if line else None, 'side': comment.side,
            'patch_set': comment.patch_set, 'status': comment.status,
            'draft_status': comment.draft_status(), 'is_draft': comment.is_draft(),
            'has_draft': comment.has_draft(),
            'messages': [message_json(message) for message in comment.messages]}


def vote_json(vote):
    return {'label': vote.label, 'value': vote.value, 'by': vote.by,
            'patch_set': vote.patch_set, 'message': vote.message,
            'copied_from': vote.copied_from}


def change_summary(core, change, repository):
    return {'id': change.change_id, 'short_id': change.short_id(), 'subject': change.subject,
            'topic': change.topic, 'state': change.state(),
            'patch_set': change.latest_patch_set(), 'turn': change.turn(),
            'code_review': core.vote_label(change, core.CODE_REVIEW),
            'verified': core.vote_label(change, core.VERIFIED),
            'comments': core.comment_counts(change), 'wip': change.wip,
            'repo_name': change.repo}


def half_json(half, colours):
    return {'kind': half.kind, 'number': half.number, 'text': half.text, 'edits': half.edits,
            'moved': half.moved, 'whole': half.whole, 'colours': colours}


class Server:
    """The repositories served and the caches the API keeps."""

    def __init__(self, core, repositories, port):
        self.core = core
        self.repositories = [str(Path(repository).resolve()) for repository in repositories]
        self.port = port
        self.token = secrets.token_hex(16)
        self._colours = {}

    def repository(self, query):
        index = int(query.get('repo', '0'))
        if not 0 <= index < len(self.repositories):
            raise self.core.ChangeError(f'no repository {index}')
        return self.repositories[index]

    def change(self, query):
        repository = self.repository(query)
        return self.core.find_change(query['id'], repository), repository

    def colours(self, version, path, lines, repository, theme):
        """Syntax colours of a file's version, kept per version."""
        key = (repository, version, path, theme)
        if key not in self._colours:
            self._colours[key] = self.core.syntax_segments(lines or [], path, THEMES[theme])
        return self._colours[key]

    # ── GET ───────────────────────────────────────────────────────────────

    def version(self, _query):
        text = '\n'.join(self.core.stack_version(repository) for repository in self.repositories)
        return {'version': hashlib.sha1(text.encode()).hexdigest()}

    def repos(self, _query):
        return {'repositories': [self.core.repository_name(repository)
                                 for repository in self.repositories]}

    def stacks(self, query):
        core = self.core
        repository = self.repository(query)
        stacks = {}
        for change in core.changes_in_stack(repository):
            stacks.setdefault(change.topic, []).append(change_summary(core, change, repository))
        return {'base': core.base_branch(repository),
                'stacks': [{'topic': topic, 'needs_rebase': core.needs_rebase(topic, repository),
                            'changes': changes} for topic, changes in stacks.items()]}

    def dashboard(self, _query):
        """The dashboard over all served repositories: every section in the
        user's order, folded ones with their count only.
        """
        core = self.core
        changes = [change for repository in self.repositories
                   for change in core.changes_in_stack(repository)]
        names = {path: name for name, path in core.registered_repositories().items()}
        index = {repository: number for number, repository in enumerate(self.repositories)}
        folded = core.folded_sections()
        behind = {(change.repository, change.topic) for change in changes
                  if change.rebase_requested or core.needs_rebase(change.topic, change.repository)}
        sections = []
        for section, entries in core.dashboard(changes):
            sections.append({
                'name': section, 'title': core.SECTION_TITLES[section], 'count': len(entries),
                'folded': section in folded,
                'changes': [] if section in folded else [
                    {**change_summary(core, change, change.repository),
                     'number': number, 'repo': index.get(change.repository, 0),
                     'repo_name': names.get(change.repository, change.repo),
                     'needs_rebase': (change.repository, change.topic) in behind,
                     'your_turn': 'your-turn' in change.sections(core.current_user())}
                    for number, change in entries]})
        return {'sections': sections}

    def change_details(self, query):
        core = self.core
        change, repository = self.change(query)
        renames = core.renames_of(change.commit, repository)
        files = [{'path': core.COMMIT_MSG, 'added': 0, 'removed': 0,
                  'reviewed': core.is_reviewed(change, core.COMMIT_MSG, repository),
                  'renamed_from': None}]
        for path, (added, removed) in sorted(core.files_of(change.commit, repository).items()):
            files.append({'path': path, 'added': added, 'removed': removed,
                          'reviewed': core.is_reviewed(change, path, repository),
                          'renamed_from': renames.get(path)})
        return {**change_summary(core, change, repository),
                'message': core.git('log', '-1', '--format=%B', change.commit,
                                    repository=repository),
                'patch_sets': sorted(change.patch_sets),
                'verified_required': change.verified_required,
                'problems': change.submit_problems(),
                'files': files,
                'comments': [comment_json(comment) for comment in change.comments],
                'votes': [vote_json(vote) for vote in change.votes]}

    def diff(self, query):
        core = self.core
        change, repository = self.change(query)
        path = query['path']
        first, second = query.get('a') or 'base', query.get('b') or 'latest'
        context = None if 'all' == query.get('context') else int(query.get('context', '10'))
        whitespace = query.get('whitespace', 'none')
        rows = core.file_diff_rows(change, first, second, path, context, repository, whitespace)
        if rows is None:
            return {'binary': True, 'rows': []}
        old, new, paths = core.diff_endpoints(change, first, second, path, repository)
        old_path = core.old_path_of(change, first, second, paths[0], repository) or paths[0]
        theme = 'light' if 'light' == query.get('theme') else 'dark'
        old_colours = self.colours(old, old_path, core.file_lines(old, old_path, repository),
                                   repository, theme)
        new_colours = self.colours(new, paths[0], core.file_lines(new, paths[0], repository),
                                   repository, theme)
        from_below, partly = core.rebase_lines(change, first, second, path, repository)

        def colours_of(half, colours):
            if colours is None or half.number is None or half.number > len(colours):
                return None
            return colours[half.number - 1]

        # Where this diff shows each thread: on a newer patch set than the
        # thread's, its place there
        threads = {}
        for comment in change.comments:
            shown = core.thread_place(change, comment, first, second, repository)
            threads[comment.number] = {'path': shown.path, 'line': shown.line, 'side': shown.side,
                                       'origin': shown.origin, 'note': shown.note}
        return {'binary': False, 'renamed_from': None if old_path == paths[0] else old_path,
                'threads': threads,
                'from_below': [sorted(lines) for lines in from_below],
                'partly': [sorted(lines) for lines in partly],
                'rows': [{'old': half_json(row.old, colours_of(row.old, old_colours)),
                          'new': half_json(row.new, colours_of(row.new, new_colours)),
                          'skipped': row.skipped} for row in rows]}

    def keys(self, _query):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import plustwo_tui  # pylint: disable=import-outside-toplevel
        overrides = self.core.settings_section('keys', self.repositories[0])
        preset = overrides.pop('preset', 'vim')
        if preset not in plustwo_tui.PRESETS:
            preset = 'vim'
        keys = {**plustwo_tui.PRESETS[preset],
                **{name: value for name, value in overrides.items()
                   if name in plustwo_tui.ACTION_NAMES}}
        return {'preset': preset, 'keys': keys,
                'actions': [{'name': name, 'group': group, 'label': label,
                             'description': description}
                            for name, group, label, description in plustwo_tui.ACTIONS]}

    # ── POST ──────────────────────────────────────────────────────────────

    def act(self, body):
        """One write, named by body['action'], on the change body['id']."""
        core = self.core
        if 'fold' == body.get('action'):
            return {'folded': core.toggle_section(body['section'])}
        if 'order' == body.get('action'):
            core.set_section_order(body['sections'])
            return {}
        repository = self.repository(body)
        change = core.find_change(body['id'], repository)
        change_id = change.change_id
        action = body['action']
        number = body.get('number')
        if 'comment' == action:
            path, line = core.split_place(body['place'])
            place, side, patch_set = core.comment_place(
                change, path, line, body.get('side', core.NEW_SIDE), body.get('a') or 'base',
                body.get('b') or 'latest', repository)
            return {'number': core.add_comment(change_id, place, body['text'], sent=False,
                                               side=side, repository=repository,
                                               patch_set=patch_set)}
        if 'reply' == action:
            core.add_message(change_id, number, core.REVIEWER, body['text'], sent=False,
                             repository=repository)
        elif action in ('done', 'ack'):
            core.add_message(change_id, number, core.REVIEWER,
                             core.DONE if 'done' == action else core.ACK, sent=False,
                             status=core.RESOLVED, repository=repository)
        elif 'resolve' == action:
            return {'status': core.toggle_draft_status(change_id, number, repository)}
        elif 'edit-draft' == action:
            if body['text']:
                core.edit_draft(change_id, number, body['text'], repository)
            else:
                core.delete_draft(change_id, number, repository)
        elif 'delete-draft' == action:
            core.delete_draft(change_id, number, repository)
        elif 'send' == action:
            if body.get('vote') is not None:
                core.set_vote(change_id, core.CODE_REVIEW, int(body['vote']),
                              repository=repository)
            return {'sent': core.send_drafts(change_id, body.get('message', ''), repository)}
        elif 'vote' == action:
            core.set_vote(change_id, body.get('label', core.CODE_REVIEW), int(body['value']),
                          repository=repository)
        elif 'approve' == action:
            patch_set, sent = core.quick_approve(change_id, bool(body.get('send')), repository)
            return {'patch_set': patch_set, 'sent': sent}
        elif 'reviewed' == action:
            return {'reviewed': core.toggle_reviewed(change_id, body['path'], repository)}
        else:
            raise core.ChangeError(f'unknown action "{action}"')
        return {}


GET_ROUTES = {'/api/version': Server.version, '/api/repos': Server.repos,
              '/api/stacks': Server.stacks, '/api/dashboard': Server.dashboard, '/api/change': Server.change_details,
              '/api/diff': Server.diff, '/api/keys': Server.keys}


class Handler(BaseHTTPRequestHandler):
    """One request. The Server is in self.server.plustwo."""

    def log_message(self, *_arguments):
        """Quiet: the terminal only shows the URL."""

    def _send(self, status, body, content_type='application/json'):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', f'{content_type}; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def _allowed(self, needs_token):
        """Whether the request comes from our own page."""
        plustwo = self.server.plustwo
        hosts = {f'127.0.0.1:{plustwo.port}', f'localhost:{plustwo.port}'}
        origin = self.headers.get('Origin')
        if self.headers.get('Host') not in hosts or (
                origin is not None and origin not in {f'http://{host}' for host in hosts}):
            return False
        return not needs_token or secrets.compare_digest(
            self.headers.get(TOKEN_HEADER, ''), plustwo.token)

    def _run(self, call):
        plustwo = self.server.plustwo
        try:
            self._send(200, call())
        except plustwo.core.ChangeError as error:
            self._send(400, {'error': str(error)})
        except (KeyError, ValueError, TypeError) as error:
            self._send(400, {'error': f'bad request: {error}'})

    def do_GET(self):  # pylint: disable=invalid-name
        url = urlparse(self.path)
        plustwo = self.server.plustwo
        if url.path.startswith('/api/'):
            if not self._allowed(True):
                self._send(403, {'error': 'forbidden'})
                return
            route = GET_ROUTES.get(url.path)
            if route is None:
                self._send(404, {'error': 'not found'})
                return
            query = {key: values[-1] for key, values in parse_qs(url.query).items()}
            self._run(lambda: route(plustwo, query))
            return
        if not self._allowed(False):
            self._send(403, b'forbidden', 'text/plain')
            return
        if url.path in STATIC_FILES:
            self._send(200, (WEB_DIRECTORY / url.path[1:]).read_bytes(), STATIC_FILES[url.path])
        elif url.path in ('/', '/index.html'):
            page = (WEB_DIRECTORY / 'index.html').read_text()
            self._send(200, page.replace('{{TOKEN}}', plustwo.token).encode(), 'text/html')
        else:
            self._send(404, b'not found', 'text/plain')

    def do_POST(self):  # pylint: disable=invalid-name
        plustwo = self.server.plustwo
        if '/api/action' != urlparse(self.path).path:
            self._send(404, {'error': 'not found'})
            return
        if not self._allowed(True):
            self._send(403, {'error': 'forbidden'})
            return
        length = int(self.headers.get('Content-Length', '0'))
        try:
            body = json.loads(self.rfile.read(length) or b'{}')
        except ValueError:
            self._send(400, {'error': 'bad JSON'})
            return
        self._run(lambda: plustwo.act(body))


def make_server(core, repositories, port=DEFAULT_PORT):
    """The HTTP server, bound to 127.0.0.1, not started yet. Port 0 picks
    a free one.
    """
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.plustwo = Server(core, repositories, server.server_address[1])
    return server


def serve(core, repositories, port=DEFAULT_PORT):
    """Serves until stopped with Ctrl-C."""
    server = make_server(core, repositories, port)
    print(f'plustwo: http://127.0.0.1:{server.plustwo.port}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
