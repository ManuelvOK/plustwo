# SPDX-License-Identifier: Zlib
"""The commit message as a file, like Gerrit's /COMMIT_MSG."""

from plustwo.git.cache import cached_for_objects
from plustwo.git.repository import Repository


# The name of the commit message's file in the tree of commit_message_tree()
COMMIT_MSG_NAME = 'COMMIT_MSG'


@cached_for_objects(1)
def commit_message(repository: Repository, commit: str) -> str:
    """Reads a commit's whole message.

    Args:
        repository: the repository.
        commit: the commit, any name git understands.

    Returns:
        The message as git log prints it.
    """
    return repository.git('log', '-1', '--format=%B', commit)


def commit_message_text(repository: Repository, commit: str) -> str:
    """Builds the commit message as the file /COMMIT_MSG shows it.

    Args:
        repository: the repository.
        commit: the commit, any name git understands.

    Returns:
        A header with parent, author and committer, like in Gerrit, then
        the message, ending with a newline.
    """
    parents, author, author_date, committer, commit_date = repository.git(
        'log', '-1', '--date=format:%Y-%m-%d %H:%M:%S %z',
        '--format=%P%n%an <%ae>%n%ad%n%cn <%ce>%n%cd', commit).split('\n')
    lines = [f'Parent:     {parent[:8]} '
             f'({repository.git("log", "-1", "--format=%s", parent)})'
             for parent in parents.split()]
    lines += [f'Author:     {author}', f'AuthorDate: {author_date}',
              f'Commit:     {committer}', f'CommitDate: {commit_date}', '']
    message = commit_message(repository, commit)
    return '\n'.join(lines) + '\n' + message.rstrip('\n') + '\n'


@cached_for_objects(1)
def commit_message_parts(repository: Repository, commit: str) -> tuple[str, str, list[str]]:
    """Splits a commit's message for reading, without the header that
    /COMMIT_MSG has.

    Args:
        repository: the repository.
        commit: the commit, any name git understands.

    Returns:
        (subject, body, trailers). The body is the text between the subject
        and the trailers like Change-Id, '' if there is none. The trailers
        are the lines of the last paragraph if git reads them all as
        trailers, else empty.
    """
    message = commit_message(repository, commit).strip('\n')
    subject, _, rest = message.partition('\n')
    paragraphs = rest.strip('\n').split('\n\n') if rest.strip() else []
    trailers: list[str] = []
    if paragraphs:
        last = paragraphs[-1].split('\n')
        # A lone paragraph would be the subject to git, so one goes first.
        parsed = repository.git('interpret-trailers', '--parse',
                                input_text='-\n\n' + paragraphs[-1] + '\n').split('\n')
        if last and len([line for line in parsed if line]) == len(last):
            trailers = last
            paragraphs = paragraphs[:-1]
    return subject.strip(), '\n\n'.join(paragraphs).strip('\n'), trailers


@cached_for_objects(1)
def commit_message_tree(repository: Repository, commit: str | None) -> str:
    """Writes a tree with only the file COMMIT_MSG, holding
    commit_message_text(). Two of these diff like two versions of a file,
    which git diff, delta and difftool all understand.

    Args:
        repository: the repository.
        commit: the commit, any name git understands. None for the empty
            tree.

    Returns:
        The tree's object name.
    """
    if commit is None:
        return repository.git('hash-object', '-t', 'tree', '-w', '--stdin', input_text='')
    blob = repository.git('hash-object', '-w', '--stdin',
                          input_text=commit_message_text(repository, commit))
    return repository.git('mktree', input_text=f'100644 blob {blob}\t{COMMIT_MSG_NAME}\n')
