# SPDX-License-Identifier: Zlib
"""The commit message as a file, like Gerrit's /COMMIT_MSG."""

from plustwo.git.cache import cached_for_objects
from plustwo.git.repository import git


# The name of the commit message's file in the tree of commit_message_tree()
COMMIT_MSG_NAME = 'COMMIT_MSG'


@cached_for_objects(0)
def commit_message(commit: str, repository: str | None = None) -> str:
    """Reads a commit's whole message.

    Args:
        commit: the commit, any name git understands.
        repository: the repository's directory, None for the current one.

    Returns:
        The message as git log prints it.
    """
    return git('log', '-1', '--format=%B', commit, repository=repository)


def commit_message_text(commit: str, repository: str | None = None) -> str:
    """Builds the commit message as the file /COMMIT_MSG shows it.

    Args:
        commit: the commit, any name git understands.
        repository: the repository's directory, None for the current one.

    Returns:
        A header with parent, author and committer, like in Gerrit, then
        the message, ending with a newline.
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
def commit_message_parts(commit: str,
                         repository: str | None = None) -> tuple[str, str, list[str]]:
    """Splits a commit's message for reading, without the header that
    /COMMIT_MSG has.

    Args:
        commit: the commit, any name git understands.
        repository: the repository's directory, None for the current one.

    Returns:
        (subject, body, trailers). The body is the text between the subject
        and the trailers like Change-Id, '' if there is none. The trailers
        are the lines of the last paragraph if git reads them all as
        trailers, else empty.
    """
    message = commit_message(commit, repository).strip('\n')
    subject, _, rest = message.partition('\n')
    paragraphs = rest.strip('\n').split('\n\n') if rest.strip() else []
    trailers: list[str] = []
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
def commit_message_tree(commit: str | None, repository: str | None = None) -> str:
    """Writes a tree with only the file COMMIT_MSG, holding
    commit_message_text(). Two of these diff like two versions of a file,
    which git diff, delta and difftool all understand.

    Args:
        commit: the commit, any name git understands. None for the empty
            tree.
        repository: the repository's directory, None for the current one.

    Returns:
        The tree's object name.
    """
    if commit is None:
        return git('hash-object', '-t', 'tree', '-w', '--stdin', input_text='',
                   repository=repository)
    blob = git('hash-object', '-w', '--stdin', input_text=commit_message_text(commit, repository),
               repository=repository)
    return git('mktree', input_text=f'100644 blob {blob}\t{COMMIT_MSG_NAME}\n',
               repository=repository)
