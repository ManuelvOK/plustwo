# SPDX-License-Identifier: Zlib
"""The commit message as a file, like Gerrit's /COMMIT_MSG."""

from plustwo.git.cache import cached_for_objects
from plustwo.git.repository import git


COMMIT_MSG_NAME = 'COMMIT_MSG'


@cached_for_objects(0)
def commit_message(commit, repository=None):
    """A commit's whole message, as git log prints it."""
    return git('log', '-1', '--format=%B', commit, repository=repository)


def commit_message_text(commit, repository=None):
    """The commit message as the file /COMMIT_MSG shows it: a header with
    parent, author and committer, like in Gerrit, then the message.
    """
    parents, author, author_date, committer, commit_date = git(
        'log', '-1', '--date=format:%Y-%m-%d %H:%M:%S %z',
        '--format=%P%n%an <%ae>%n%ad%n%cn <%ce>%n%cd', commit,
        repository=repository).split('\n')
    lines = [f'Parent:     {parent[:8]} '
             f'({git("log", "-1", "--format=%s", parent, repository=repository)})'
             for parent in parents.split()]
    lines += [f'Author:     {author}', f'AuthorDate: {author_date}',
              f'Commit:     {committer}', f'CommitDate: {commit_date}', '']
    message = commit_message(commit, repository)
    return '\n'.join(lines) + '\n' + message.rstrip('\n') + '\n'


@cached_for_objects(0)
def commit_message_parts(commit, repository=None):
    """A commit's message for reading: (subject, body, trailers), without
    the header /COMMIT_MSG has. body is the text between the subject and
    the trailers like Change-Id, '' without one. trailers are the lines
    of the last paragraph if git reads them all as trailers.
    """
    message = commit_message(commit, repository).strip('\n')
    subject, _, rest = message.partition('\n')
    paragraphs = rest.strip('\n').split('\n\n') if rest.strip() else []
    trailers = []
    if paragraphs:
        last = paragraphs[-1].split('\n')
        # A lone paragraph would be the subject to git, so one goes first.
        parsed = git('interpret-trailers', '--parse', input_text='-\n\n' + paragraphs[-1] + '\n',
                     repository=repository).split('\n')
        if last and len([line for line in parsed if line]) == len(last):
            trailers = last
            paragraphs = paragraphs[:-1]
    return subject.strip(), '\n\n'.join(paragraphs).strip('\n'), trailers


@cached_for_objects(0)
def commit_message_tree(commit, repository=None):
    """A tree with only the file COMMIT_MSG, holding commit_message_text().
    Without a commit, the empty tree. Two of these diff like two versions
    of a file, which git diff, delta and difftool all understand.
    """
    if commit is None:
        return git('hash-object', '-t', 'tree', '-w', '--stdin', input_text='',
                   repository=repository)
    blob = git('hash-object', '-w', '--stdin', input_text=commit_message_text(commit, repository),
               repository=repository)
    return git('mktree', input_text=f'100644 blob {blob}\t{COMMIT_MSG_NAME}\n',
               repository=repository)
