# SPDX-License-Identifier: Zlib
"""The actions of the interfaces and their keys in the presets."""


# Every action of the interface: (name, group, short label for the footer,
# description for the help). The keys come from a preset.
ACTIONS = (
    ('quit', 'Everywhere', 'quit', 'quit, after asking'),
    ('quit-now', 'Everywhere', 'quit now', 'quit without asking (q q works too)'),
    ('help', 'Everywhere', 'help', 'this help'),
    ('reload', 'Everywhere', 'reload', 'reload now (it also reloads by itself)'),
    ('back', 'Everywhere', 'back', 'back (not on the first screen, quit quits). In the diff, '
     'back to the files or comments.'),
    ('down', 'Moving', 'down', 'down. A count before moves that far, e.g. 15 down.'),
    ('up', 'Moving', 'up', 'up'),
    ('top', 'Moving', 'top', 'to the top. With a count to that row.'),
    ('bottom', 'Moving', 'bottom', 'to the bottom. With a count to that row, in the diff to '
     'that line of the new version.'),
    ('page-down', 'Moving', 'half page down', 'half a page down'),
    ('page-up', 'Moving', 'half page up', 'half a page up'),
    ('scroll-down', 'Moving', 'scroll down', 'in the diff: the view one row down, the cursor stays. '
     'j and k scroll through a thread taller than the pane, too.'),
    ('scroll-up', 'Moving', 'scroll up', 'in the diff: the view one row up, the cursor stays'),
    ('open', 'Moving', 'open', 'open. In the stack (▶ is the change shown, › and the turn '
     'colour mark those where it is your turn, "(submittable)" those a submit would take '
     'now): shows that change. On a file: into the '
     'diff. In the comments: unfolds the thread and goes to it in the diff. In the diff: folds '
     'or unfolds the thread.'),
    ('diff', 'Stacks', 'diff in pager', 'the diff of the change in git\'s pager'),
    ('vote', 'Stacks and changes', 'vote', 'vote Code-Review (-2..+2) on the latest patch set'),
    ('verified', 'Stacks and changes', 'needs Verified', 'whether the change needs Verified+1 '
     '(it comes from a check)'),
    ('submit', 'Stacks and changes', 'submit', 'submit: moves the base branch to the change, '
     'which submits the changes below it too. Needs Code-Review+2 (and Verified+1 if '
     'required). Asks first and lists the unresolved comments.'),
    ('approve', 'Stacks and changes', '+2', 'vote Code-Review+2 on the latest patch set, like '
     'Gerrit\'s Quick approve. Asks first: the key again or y gives it. Offers to send your '
     'drafts along.'),
    ('next-turn', 'Stacks', 'to my turn', 'to the next change where it is your turn'),
    ('section-down', 'Stacks', 'section down', 'on a section heading: move the section down. '
     'open on a heading folds or unfolds it.'),
    ('section-up', 'Stacks', 'section up', 'on a section heading: move the section up'),
    ('rebase', 'Stacks and changes', 'rebase', 'rebase the stack onto the base branch, without '
     'touching any checkout. On a conflict the author is asked to rebase.'),
    ('next-pane', 'Change', 'next pane', 'next pane: stack, commit message, files, comments, '
     'diff'),
    ('last-pane', 'Change', 'last pane', 'back to the pane focused before, again returns, like '
     'Alt+Tab'),
    ('thread-patch-set', 'Threads', 'thread on its PS', 'show the thread on its own patch set: the diff '
     'against its parent, at the thread. Again (or back in the diff) returns to the '
     'comparison before.'),
    ('sidebar', 'Change', 'sidebar', 'hide or show the left column (stack, files, comments), '
     'so the diff gets the whole width. The setting tui.sidebar (auto, shown, hidden) picks the '
     'start, auto hides it below 100 columns.'),
    ('next-change', 'Change', 'next change', 'the next change of the stack (above)'),
    ('prev-change', 'Change', 'previous change', 'the previous change of the stack (below)'),
    ('next-file', 'Change', 'next file', 'next file'),
    ('prev-file', 'Change', 'previous file', 'previous file'),
    ('next-chunk', 'Change', 'next chunk', 'in the diff: to the next changed lines'),
    ('prev-chunk', 'Change', 'previous chunk', 'in the diff: to the previous changed lines'),
    ('next-thread', 'Change', 'next thread', 'in the diff: to the next thread, at the '
     'file\'s end in the next file with threads'),
    ('prev-thread', 'Change', 'previous thread', 'in the diff: to the previous thread'),
    ('next-unresolved', 'Change', 'next unresolved', 'in the diff: to the next unresolved '
     'thread, at the file\'s end in the next file with one'),
    ('prev-unresolved', 'Change', 'previous unresolved', 'in the diff: to the previous unresolved '
     'thread, also in the files before'),
    ('next-relevant', 'Change', 'next relevant', 'in the diff: to the next thread worth a look, '
     'also in the next files: one the comparison touches (new messages since the left side, or '
     'on changed lines) or an unresolved one waiting for you'),
    ('prev-relevant', 'Change', 'previous relevant', 'in the diff: to the previous thread worth '
     'a look'),
    ('comment', 'Change', 'comment', 'in the diff: comment on the cursor line, or on the whole '
     'file on rows without a line number. On a thread it replies. In the files: comment on '
     'the file. It stays a draft until sent.'),
    ('file-comment', 'Change', 'comment on file', 'comment on the whole file'),
    ('comment-above', 'Change', 'comment above', 'in the diff: comment on the line above the '
     'cursor line, like vim\'s O, its box opens between the two lines'),
    ('more-context', 'Change', 'more context', 'more context around the changes, up to the whole '
     'file'),
    ('less-context', 'Change', 'less context', 'less context around the changes'),
    ('vimdiff', 'Change', 'vimdiff', 'the file\'s diff in vimdiff'),
    ('patch-sets', 'Change', 'patch sets', 'the patch sets: when, by whom, their type, votes '
     'and comments, to choose the versions to compare. l unfolds a patch set, Enter shows the '
     'diff (on a thread: at the thread).'),
    ('compare-last-send', 'Change', 'last send -> latest', 'compare the patch set of your last '
     'send with the latest, to see what happened since'),
    ('follow-latest', 'Change', 'follow latest on/off', 'whether a right side on the latest '
     'patch set moves to a new upload. Off (the start), it stays and the header says when a '
     'newer one is there. Kept for later sessions, the web page shares it.'),
    ('set-a', 'Patch sets', 'pick left side', 'in the patch sets: the row\'s version on the left side (A), '
     '< too'),
    ('set-b', 'Patch sets', 'pick right side', 'in the patch sets: the row\'s patch set on the right side (B), '
     '> too'),
    ('compare-prompt', 'Patch sets', 'type versions', 'in the patch sets: type the versions to compare: '
     '"1 3", "2 current" or "base 2". Empty: the whole change, latest patch set.'),
    ('search', 'Change', 'search', 'in the diff: search the file for a text, from the cursor '
     'on, on both sides. Lower case finds any case. Up and Down in the prompt go through the '
     'texts searched before, empty searches the last one again. Tab in the prompt changes '
     'where it looks: the lines shown, the whole file (opens the gaps with matches), the file '
     'and the comments, only the comments. The setting tui.search (shown, file, all, '
     'comments) picks the start.'),
    ('search-next', 'Change', 'next match', 'in the diff: to the next match of the search, also '
     'after its marks were removed'),
    ('search-prev', 'Change', 'previous match', 'in the diff: to the previous match of the search'),
    ('search-clear', 'Change', 'unmark matches', 'remove the marks of the search. The next match key '
     'brings them back.'),
    ('fold-all', 'Change', 'fold/unfold all', 'fold all threads, or unfold all if all are folded'),
    ('unfold-every', 'Change', 'unfold all', 'unfold all threads'),
    ('fold-every', 'Change', 'fold all', 'fold all threads'),
    ('reviewed', 'Change', 'reviewed', 'mark the file reviewed, or unmark it. The mark stays while '
     'the file\'s diff stays the same.'),
    ('reviewed-next', 'Change', 'reviewed, next file', 'mark the file reviewed and open the next '
     'file of the list, reviewed or not. On the last file it only marks.'),
    ('whitespace', 'Change', 'whitespace', 'which whitespace changes the diff ignores, like '
     'Gerrit: none, trailing, changed, all'),
    ('side-old', 'Change', 'old side', 'in the diff: the cursor to the old side, comments go there'),
    ('side-new', 'Change', 'new side', 'in the diff: the cursor to the new side'),
    ('reply', 'Threads', 'reply', 'reply to the thread, as a draft'),
    ('edit-draft', 'Threads', 'edit draft', 'edit your draft in the thread. Empty deletes it.'),
    ('done', 'Threads', 'Done', 'reply "Done" as a draft, which resolves the thread'),
    ('ack', 'Threads', 'Ack', 'reply "Ack" as a draft, which resolves the thread'),
    ('resolve', 'Threads', 'resolved on/off', 'whether sending the draft resolves the thread or makes '
     'it unresolved again (Gerrit\'s Resolved checkbox). Without a draft, it makes an empty '
     'one.'),
    ('move-thread', 'Threads', 'move', 'move the thread to the line it belongs to, when it lost '
     'its place: go to the line, Enter drops it there, Esc cancels. On a row without a line '
     'it goes on the whole file. Older patch sets keep where it was.'),
    ('send', 'Threads', 'reply and send', 'send all drafts of this change, with an optional message on '
     'the whole change, which tells the author to start'),
)


ACTION_NAMES = tuple(action[0] for action in ACTIONS)


# The keys of each action, per preset. Alternatives are separated by
# commas, the keys of a sequence like gg by spaces. C-x is Ctrl, M-x Alt.
VIM_KEYS = {
    'quit': 'q', 'quit-now': 'Z Q', 'help': '?', 'reload': 'R', 'back': 'h,Esc',
    'down': 'j,Down', 'up': 'k,Up', 'top': 'g g,Home', 'bottom': 'G,End',
    'page-down': 'C-d,PageDown', 'page-up': 'C-u,PageUp', 'open': 'l,Enter',
    'scroll-down': 'C-e', 'scroll-up': 'C-y',
    'diff': 'd', 'vote': 'v', 'verified': 'V', 'submit': 'S', 'rebase': 'B',
    'next-pane': 'Tab', 'next-file': '] f', 'prev-file': '[ f',
    'next-change': '] s', 'prev-change': '[ s',
    'next-chunk': '] c', 'prev-chunk': '[ c', 'next-thread': '] t', 'prev-thread': '[ t',
    'next-unresolved': '] u', 'prev-unresolved': '[ u',
    'next-relevant': '] n', 'prev-relevant': '[ n', 'last-pane': 'S-Tab',
    # Like vim's o and O, which open a line below and above
    'comment': 'c,o', 'comment-above': 'O', 'file-comment': 'C', 'more-context': '+,=', 'less-context': '-',
    'vimdiff': 't', 'patch-sets': 'p', 'fold-all': 'i', 'unfold-every': 'z R',
    'fold-every': 'z M',
    'reply': 'r', 'edit-draft': 'e', 'done': 'y', 'ack': 'Y', 'resolve': 'x', 'send': 's',
    'whitespace': 'W', 'approve': 'A', 'next-turn': 'T', 'reviewed': 'm,Space',
    'reviewed-next': 'M', 'side-old': '<', 'side-new': '>',
    'section-down': 'J', 'section-up': 'K',
    # Like vim's C-w o, which keeps only the current window
    'sidebar': 'C-w o,z s',
    'set-a': 'a', 'set-b': 'b', 'compare-prompt': ':', 'thread-patch-set': 'g p',
    # Not g f, which is vim's "go to file". The interface has no f search.
    'compare-last-send': 'g l', 'follow-latest': 'F',
    'search': '/', 'search-next': 'n', 'search-prev': 'N',
    # Like the usual vim mapping of C-l to :nohlsearch
    'search-clear': 'C-l',
    # The leader key, which vim leaves to the user's own mappings
    'move-thread': '\\ m',
}


PRESETS = {
    'vim': VIM_KEYS,
    # Emacs moves with C-n, C-p, C-v, M-v, M-< and M->, and C-g cancels.
    # Like in its diff-mode, n and p go to the next and previous changes,
    # N and P to the next and previous file. Counts start with C-u.
    'emacs': {**VIM_KEYS,
              'quit': 'q', 'quit-now': 'C-x C-c', 'reload': 'g,R',
              'next-change': 'M-n', 'prev-change': 'M-p', 'back': 'C-g,Esc,C-b',
              'down': 'C-n,Down', 'up': 'C-p,Up', 'top': 'M-<,Home', 'bottom': 'M->,End',
              'page-down': 'C-v,PageDown', 'page-up': 'M-v,PageUp', 'open': 'Enter,C-f',
              'next-chunk': 'n', 'prev-chunk': 'p', 'next-file': 'N', 'prev-file': 'P',
              'next-thread': ']', 'prev-thread': '[', 'patch-sets': 'b',
              'next-unresolved': 'M-}', 'prev-unresolved': 'M-{',
              'next-relevant': 'M-]', 'prev-relevant': 'M-[',
              # C-o like Emacs' open-line
              'comment': 'c', 'comment-above': 'C-o',
              # Like delete-other-windows
              'sidebar': 'C-x 1', 'set-b': 'M-b', 'thread-patch-set': 'C-c p',
              # g reloads here
              'compare-last-send': 'C-c l', 'follow-latest': 'C-c f',
              # Emacs' M-s holds its search commands
              'search': 'C-s', 'search-next': 'M-s n', 'search-prev': 'M-s p',
              # Like Emacs' unhighlight-regexp
              'search-clear': 'M-s h u', 'move-thread': 'C-c m'},
    # Gerrit's web interface: j and k move, n and p go to the next and
    # previous changes, N and P to the next and previous thread, ] and [ to
    # the next and previous file, u goes up, o opens, c comments, and a
    # opens the reply dialog, which sends the drafts.
    'gerrit': {**VIM_KEYS,
               'back': 'u,h,Esc', 'open': 'o,l,Enter',
               'next-chunk': 'n', 'prev-chunk': 'p', 'next-thread': 'N', 'prev-thread': 'P',
               'next-file': ']', 'prev-file': '[', 'send': 'a', 'patch-sets': 'b',
               # o opens here
               'comment': 'c', 'comment-above': 'O',
               'next-unresolved': 'C-n', 'prev-unresolved': 'C-p',
               # Like N and P for threads: Alt goes to the relevant ones
               'next-relevant': 'M-N', 'prev-relevant': 'M-P',
               # Gerrit's r marks the file reviewed, its M marks it and goes on
               'reviewed': 'r,m,Space', 'reviewed-next': 'M', 'reply': 'R', 'reload': 'C-r',
               'next-change': 'M-n', 'prev-change': 'M-p',
               # e and E unfold and fold all threads, so drafts are edited with w
               'unfold-every': 'e', 'fold-every': 'E', 'edit-draft': 'w',
               # a sends and b chooses patch sets here
               'set-a': '{', 'set-b': '}',
               'compare-last-send': 'L', 'follow-latest': 'F',
               # / is Gerrit's search, C-g goes on like a browser's find
               'search-next': 'C-g', 'search-prev': 'M-g'},
}
