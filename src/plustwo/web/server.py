# SPDX-License-Identifier: Zlib
"""The web interface: a local HTTP server for the page and the JSON API."""

import hashlib
import json
import re
import secrets
from collections.abc import Callable, Iterable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from plustwo.review import dashboard as review_dashboard
from plustwo.config.key_presets import ACTIONS, ACTION_NAMES, PRESETS
from plustwo.config.registry import registered_repositories, repository_name
from plustwo.config.settings import base_branch, settings_section
from plustwo.config.user import current_user
from plustwo.config.view_state import (
    SECTION_TITLES, folded_sections, follows_latest, set_follows_latest, set_section_order,
    toggle_section)
from plustwo.diff.contrast import readable_colours
from plustwo.diff.rows import Half
from plustwo.diff.sgr import Segment
from plustwo.diff.syntax import syntax_segments
from plustwo.git.commit_message import commit_message, commit_message_parts
from plustwo.git.errors import ChangeError
from plustwo.git.files import file_lines, files_of
from plustwo.git.repository import Repository
from plustwo.model.change import Change
from plustwo.model.comment import ACK, DONE, RESOLVED
from plustwo.model.place import COMMIT_MSG, NEW_SIDE, place_label, split_place
from plustwo.model.roles import REVIEWER
from plustwo.model.vote import CODE_REVIEW
from plustwo.review.renames import own_sources
from plustwo.review.changes import changes_in_stack, find_change
from plustwo.review.compare import (
    compare_label, compared_files, compared_renames, diff_endpoints, old_path_of)
from plustwo.review.file_diff import file_diff_rows
from plustwo.review.history import message_patch_sets, patch_set_history
from plustwo.review.moves import move_text, move_thread
from plustwo.review.porting import comment_place, thread_place
from plustwo.review.rebase_lines import rebase_lines
from plustwo.review.reply import quick_approve, reply
from plustwo.review.reviewed import is_approved, is_reviewed, toggle_approved, toggle_reviewed
from plustwo.review.stacks import needs_rebase, stack_version
from plustwo.review.threads import (
    add_comment, add_message, delete_draft, edit_draft, toggle_draft_status)
from plustwo.review.touched import touched_threads
from plustwo.review.votes import set_vote
from plustwo.web.json import change_summary, comment_json, half_json, history_list, vote_json


WEB_DIRECTORY = Path(__file__).resolve().parent / 'page'


STATIC_FILES = {'/app.js': 'application/javascript', '/style.css': 'text/css'}


TOKEN_HEADER = 'X-Plustwo-Token'


DEFAULT_PORT = 8022


# bat themes for the page's light and dark colours
THEMES = {'light': 'GitHub', 'dark': 'Monokai Extended'}


Query = dict[str, str]


class Server:
    """The repositories served and the caches the API keeps."""

    def __init__(self, repositories: Iterable[Repository], port: int) -> None:
        """Starts serving repositories.

        Args:
            repositories: the repositories.
            port: the port the HTTP server listens on.
        """
        self.repositories = list(repositories)
        self.port = port
        self.token = secrets.token_hex(16)
        self._colour_cache: dict[tuple[Repository, str, str, str], list[list[Segment]] | None] = {}

    def _repository(self, query: Query | dict[str, Any]) -> Repository:
        """Finds the repository a request is about.

        Args:
            query: the request's parameters, "repo" is the index.

        Returns:
            The repository.

        Raises:
            ChangeError: if there is no such repository.
        """
        index = int(query.get('repo', '0'))
        if not 0 <= index < len(self.repositories):
            raise ChangeError(f'no repository {index}')
        return self.repositories[index]

    def _change(self, query: Query) -> tuple[Change, Repository]:
        """Finds the change a request is about.

        Args:
            query: the request's parameters, "id" and "repo".

        Returns:
            The change and its repository.
        """
        repository = self._repository(query)
        return find_change(repository, query['id']), repository

    def _syntax_colours(self, repository: Repository, version: str, path: str,
                        lines: list[str] | None, theme: str) -> list[list[Segment]] | None:
        """Syntax colours of a file's version, kept per version.

        Args:
            repository: the repository.
            version: the version's commit.
            path: the file's path.
            lines: the file's lines, None for none.
            theme: light or dark.

        Returns:
            The segments per line, None without colours.
        """
        key = (repository, version, path, theme)
        if key not in self._colour_cache:
            self._colour_cache[key] = syntax_segments(lines or [], path, THEMES[theme])
        return self._colour_cache[key]

    # ── GET ───────────────────────────────────────────────────────────────────

    def version(self, _query: Query) -> dict[str, Any]:
        """A hash of every repository's stack state, the page polls it.

        Args:
            _query: unused.

        Returns:
            The JSON object.
        """
        text = '\n'.join(stack_version(repository) for repository in self.repositories)
        return {'version': hashlib.sha1(text.encode()).hexdigest()}

    def repos(self, _query: Query) -> dict[str, Any]:
        """Lists the served repositories' names.

        Args:
            _query: unused.

        Returns:
            The JSON object.
        """
        return {'repositories': [repository_name(repository)
                                 for repository in self.repositories]}

    def stacks(self, query: Query) -> dict[str, Any]:
        """Lists the stacks of a repository.

        Args:
            query: "repo" is the repository index.

        Returns:
            The JSON object.
        """
        repository = self._repository(query)
        stacks: dict[str, list[dict[str, Any]]] = {}
        for change in changes_in_stack(repository):
            stacks.setdefault(change.topic, []).append(change_summary(change))
        return {'base': base_branch(repository),
                'stacks': [{'topic': topic, 'base': changes[0]['base'],
                            'needs_rebase': needs_rebase(repository, topic),
                            'changes': changes} for topic, changes in stacks.items()]}

    def dashboard(self, _query: Query) -> dict[str, Any]:
        """The dashboard over all served repositories: every section in the
        user's order, folded ones with their count only.

        Args:
            _query: unused.

        Returns:
            The JSON object.
        """
        changes = [change for repository in self.repositories
                   for change in changes_in_stack(repository)]
        names: dict[Repository, str] = {
            repository: name for name, repository in registered_repositories().items()}
        index: dict[Repository, int] = {
            repository: number for number, repository in enumerate(self.repositories)}
        folded = folded_sections()
        behind = {(change.repository, change.topic) for change in changes
                  if change.rebase_is_requested or needs_rebase(change.repository, change.topic)}
        sections = []
        for section, entries in review_dashboard.dashboard(changes):
            sections.append({
                'name': section, 'title': SECTION_TITLES[section], 'count': len(entries),
                'folded': section in folded,
                'changes': [] if section in folded else [
                    {**change_summary(change),
                     'number': number, 'repo': index.get(change.repository, 0),
                     'repo_name': names.get(change.repository, change.repository_name),
                     'needs_rebase': (change.repository, change.topic) in behind,
                     'your_turn': 'your-turn' in change.sections(current_user())}
                    for number, change in entries]})
        return {'sections': sections}

    def change_details(self, query: Query) -> dict[str, Any]:
        """Gives one change with its files, threads, history and votes.

        Args:
            query: "id", "repo", and "a" and "b", the versions compared.

        Returns:
            The JSON object.
        """
        change, repository = self._change(query)
        # Counts between the compared versions, unchanged files greyed,
        # renamed ones named with their path in the older version
        try:
            compared = compared_files(change, query.get('a') or 'base',
                                           query.get('b') or 'latest')
            renames = compared_renames(change, query.get('a') or 'base',
                                       query.get('b') or 'latest')
        except ChangeError:
            compared = None
            renames = own_sources(repository, change.commit)
        # Threads the comparison leaves as they were are quiet on the page.
        touched = touched_threads(change, query.get('a') or 'base',
                                       query.get('b') or 'latest')
        paths = [COMMIT_MSG] + sorted(files_of(repository, change.commit))
        files = []
        for path in paths:
            added, removed = (compared or {}).get(path, (0, 0))
            files.append({'path': path, 'added': added, 'removed': removed,
                          'changed': compared is None or path in compared,
                          'reviewed': is_reviewed(change, path),
                          'approved': is_approved(change, path),
                          'renamed_from': renames.get(path)})
        return {**change_summary(change),
                'message': commit_message(repository, change.commit),
                'message_parts': dict(zip(('subject', 'body', 'trailers'),
                                          commit_message_parts(repository, change.commit))),
                'patch_sets': sorted(change.patch_sets),
                'verified_required': change.needs_verified,
                'problems': change.submit_problems(),
                'files': files,
                'comments': [{**comment_json(comment, message_patch_sets(
                    change, comment, int(query['b']) if query.get('b', '').isdigit() else None)),
                    'touched': comment.number in touched} for comment in change.comments],
                # Drafts go with the patch set shown on the right
                'history': history_list(patch_set_history(
                    change, int(query['b']) if query.get('b', '').isdigit() else None)),
                'votes': [vote_json(vote) for vote in change.votes]}

    def diff(self, query: Query) -> dict[str, Any]:
        """Gives the diff rows of one file.

        Args:
            query: "id", "repo", "path", "a" and "b", "context", "whitespace",
                "theme" and "shades".

        Returns:
            The JSON object.
        """
        change, repository = self._change(query)
        path = query['path']
        first, second = query.get('a') or 'base', query.get('b') or 'latest'
        context = None if 'all' == query.get('context') else int(query.get('context', '10'))
        whitespace = query.get('whitespace', 'none')
        rows = file_diff_rows(change, first, second, path, context, whitespace)
        comparing = compare_label(change, first, second, does_follow_latest=follows_latest())
        if rows is None:
            return {'binary': True, 'rows': [], 'comparing': comparing}
        old, new, paths = diff_endpoints(change, first, second, path)
        old_path = old_path_of(change, first, second, paths[0]) or paths[0]
        theme = 'light' if 'light' == query.get('theme') else 'dark'
        old_colours = self._syntax_colours(repository, old, old_path,
                                           file_lines(repository, old, old_path), theme)
        new_colours = self._syntax_colours(repository, new, paths[0],
                                           file_lines(repository, new, paths[0]), theme)
        from_below, partly = rebase_lines(change, first, second, path)

        def colours_of(half: Half, colours: list[list[Segment]] | None) -> list[Segment] | None:
            """Finds the syntax segments of a diff half's line.

            Args:
                half: the half.
                colours: the file's segments per line, None without colours.

            Returns:
                The line's segments, None if there are none.
            """
            if colours is None or half.number is None or half.number > len(colours):
                return None
            return colours[half.number - 1]

        # Where this diff shows each thread: on a newer patch set than the
        # thread's, its place there
        threads: dict[int, dict[str, Any]] = {}
        for comment in change.comments:
            shown = thread_place(change, comment, first, second)
            threads[comment.number] = {'path': shown.path, 'line': shown.line, 'side': shown.side,
                                       'origin': shown.origin, 'note': shown.note}
        # Syntax colours lifted on the page's shades, see readable()
        used = {segment[1] for colours in (old_colours, new_colours) for line in colours or ()
                for segment in line if segment[1]}
        try:
            shades = {name: colour for name, colour in json.loads(query.get('shades', '{}')).items()
                      if re.fullmatch('#[0-9a-fA-F]{6}', colour)}
        except (ValueError, AttributeError, TypeError):
            shades = {}
        return {'binary': False, 'comparing': comparing,
                'readable': readable_colours(list(used), shades),
                'renamed_from': None if old_path == paths[0] else old_path,
                'threads': threads,
                'from_below': [sorted(lines) for lines in from_below],
                'partly': [sorted(lines) for lines in partly],
                'rows': [{'old': half_json(row.old, colours_of(row.old, old_colours)),
                          'new': half_json(row.new, colours_of(row.new, new_colours)),
                          'skipped': row.n_skipped} for row in rows]}

    def keys(self, _query: Query) -> dict[str, Any]:
        """Gives the key bindings and the follow-latest switch.

        Args:
            _query: unused.

        Returns:
            The JSON object.
        """
        overrides = settings_section(self.repositories[0], 'keys')
        preset = overrides.pop('preset', 'vim')
        if preset not in PRESETS:
            preset = 'vim'
        keys = {**PRESETS[preset],
                **{name: value for name, value in overrides.items()
                   if name in ACTION_NAMES}}
        return {'preset': preset, 'keys': keys, 'follow_latest': follows_latest(),
                'actions': [{'name': name, 'group': group, 'label': label,
                             'description': description}
                            for name, group, label, description in ACTIONS]}

    # ── POST ──────────────────────────────────────────────────────────────────

    def act(self, body: dict[str, Any]) -> dict[str, Any]:
        """One write, named by body['action'], on the change body['id'].

        Args:
            body: the request's JSON object.

        Returns:
            The JSON object to answer with.

        Raises:
            ChangeError: if the action is unknown.
        """
        if 'fold' == body.get('action'):
            return {'folded': toggle_section(body['section'])}
        if 'order' == body.get('action'):
            set_section_order(body['sections'])
            return {}
        if 'follow-latest' == body.get('action'):
            set_follows_latest(bool(body.get('on')))
            return {'on': follows_latest()}
        repository = self._repository(body)
        change = find_change(repository, body['id'])
        change_id = change.change_id
        action = body['action']
        number: Any = body.get('number')
        if 'comment' == action:
            path, line = split_place(body['place'])
            place, side, patch_set = comment_place(
                change, path, line, body.get('side', NEW_SIDE), body.get('a') or 'base',
                body.get('b') or 'latest')
            return {'number': add_comment(repository, change_id, place, body['text'], is_sent=False,
                                               side=side, patch_set=patch_set)}
        if 'move' == action:
            path, line = split_place(body['place'])
            place, side, patch_set = comment_place(
                change, path, line, body.get('side', NEW_SIDE), body.get('a') or 'base',
                body.get('b') or 'latest')
            move = move_thread(repository, change_id, number, place, side, patch_set)
            return {'shown': move_text(move) if move
                    else f'now on {place_label(place, side)}'}
        if 'reply' == action:
            add_message(repository, change_id, number, REVIEWER, body['text'], is_sent=False)
        elif action in ('done', 'ack'):
            add_message(repository, change_id, number, REVIEWER,
                             DONE if 'done' == action else ACK, is_sent=False,
                             status=RESOLVED)
        elif 'resolve' == action:
            return {'status': toggle_draft_status(repository, change_id, number)}
        elif 'edit-draft' == action:
            if body['text']:
                edit_draft(repository, change_id, number, body['text'])
            else:
                delete_draft(repository, change_id, number)
        elif 'delete-draft' == action:
            delete_draft(repository, change_id, number)
        elif 'send' == action:
            vote = body.get('vote')
            sent, _voted = reply(repository, change_id, body.get('message', ''),
                                      None if vote is None else int(vote))
            return {'sent': sent}
        elif 'vote' == action:
            set_vote(repository, change_id, body.get('label', CODE_REVIEW), int(body['value']))
        elif 'approve' == action:
            patch_set, sent = quick_approve(repository, change_id, bool(body.get('send')))
            return {'patch_set': patch_set, 'sent': sent}
        elif 'reviewed' == action:
            return {'reviewed': toggle_reviewed(repository, change_id, body['path'])}
        elif 'approve-file' == action:
            return {'approved': toggle_approved(repository, change_id, body['path'])}
        else:
            raise ChangeError(f'unknown action "{action}"')
        return {}


GET_ROUTES: dict[str, Callable[[Server, Query], dict[str, Any]]] = {
    '/api/version': Server.version, '/api/repos': Server.repos, '/api/stacks': Server.stacks,
    '/api/dashboard': Server.dashboard, '/api/change': Server.change_details,
    '/api/diff': Server.diff, '/api/keys': Server.keys}


class PlustwoServer(ThreadingHTTPServer):
    """The HTTP server, which carries the Server the handlers use."""

    plustwo: Server


class Handler(BaseHTTPRequestHandler):
    """One request. The Server is in self.server.plustwo."""

    server: PlustwoServer

    def log_message(self, format: str,  # pylint: disable=redefined-builtin
                    *_arguments: Any) -> None:
        """Quiet: the terminal only shows the URL.

        Args:
            format: unused.
            _arguments: unused.
        """

    def _send(self, status: int, body: bytes | dict[str, Any],
              content_type: str = 'application/json') -> None:
        """Sends a response.

        Args:
            status: the HTTP status.
            body: the bytes to send, or an object to send as JSON.
            content_type: the content type.
        """
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', f'{content_type}; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def _allowed(self, needs_token: bool) -> bool:
        """Whether the request comes from our own page.

        Args:
            needs_token: whether the request has to carry the token.

        Returns:
            True if it may be answered.
        """
        plustwo = self.server.plustwo
        hosts = {f'127.0.0.1:{plustwo.port}', f'localhost:{plustwo.port}'}
        origin = self.headers.get('Origin')
        if self.headers.get('Host') not in hosts or (
                origin is not None and origin not in {f'http://{host}' for host in hosts}):
            return False
        return not needs_token or secrets.compare_digest(
            self.headers.get(TOKEN_HEADER, ''), plustwo.token)

    def _run(self, call: Callable[[], dict[str, Any]]) -> None:
        """Answers with what a call returns, or its error as status 400.

        Args:
            call: the function that makes the answer.
        """
        try:
            self._send(200, call())
        except ChangeError as error:
            self._send(400, {'error': str(error)})
        except (KeyError, ValueError, TypeError) as error:
            self._send(400, {'error': f'bad request: {error}'})

    def do_GET(self) -> None:  # pylint: disable=invalid-name
        """Answers a GET: the page, its files or an API call."""
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

    def do_POST(self) -> None:  # pylint: disable=invalid-name
        """Answers a POST: an API action."""
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


def make_server(repositories: Iterable[Repository], port: int = DEFAULT_PORT) -> PlustwoServer:
    """The HTTP server, bound to 127.0.0.1, not started yet. Port 0 picks
    a free one.

    Args:
        repositories: the repositories.
        port: the port.

    Returns:
        The server.
    """
    server = PlustwoServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.plustwo = Server(repositories, server.server_address[1])
    return server


def serve(repositories: Iterable[Repository], port: int = DEFAULT_PORT) -> None:
    """Serves until stopped with Ctrl-C.

    Args:
        repositories: the repositories.
        port: the port.
    """
    server = make_server(repositories, port)
    print(f'plustwo: http://127.0.0.1:{server.plustwo.port}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
