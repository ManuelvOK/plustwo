// SPDX-License-Identifier: GPL-3.0-or-later
// The web interface of plustwo: plain JavaScript over the JSON API of
// plustwo_web.py. The page only draws and sends, the core decides.
'use strict';

const TOKEN = document.body.dataset.token;
const COMMIT_MSG = '/COMMIT_MSG';
const PATCHSET_LEVEL = '/PATCHSET_LEVEL';
const CONTEXTS = ['3', '10', '25', '100', 'all'];
const WHITESPACE = ['none', 'trailing', 'changed', 'all'];

const state = {
  repo: 0, repos: [], version: null, keys: null,
  // the change page
  change: null, file: null, a: 'base', b: 'latest', context: '10', whitespace: 'none',
  diff: null, cursor: 0, side: 'new', editor: null, listIndex: 0, listIds: [],
};

// ── talking to the server ─────────────────────────────────────────────────

async function api(path, params = {}) {
  const query = new URLSearchParams({repo: state.repo, ...params});
  const response = await fetch(`/api/${path}?${query}`, {headers: {'X-Plustwo-Token': TOKEN}});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

async function act(action, fields = {}) {
  const body = {repo: state.repo, id: state.change.id, action, ...fields};
  const response = await fetch('/api/action', {
    method: 'POST', headers: {'X-Plustwo-Token': TOKEN, 'Content-Type': 'application/json'},
    body: JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) { say(data.error, true); throw new Error(data.error); }
  await render();
  return data;
}

function say(text, error = false) {
  const message = document.getElementById('message');
  message.textContent = text || '';
  message.className = error ? 'error' : '';
}

// ── small helpers for building elements ───────────────────────────────────

function el(tag, attributes = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (name.startsWith('on')) node.addEventListener(name.slice(2), value);
    else if (name === 'class') node.className = value;
    else if (value !== null && value !== undefined && value !== false) node.setAttribute(name, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function voteChip(label, value) {
  if (!value) return null;
  const kind = value === '?' ? 'warn' : value.startsWith('+') ? 'good' : 'bad';
  return el('span', {class: `chip ${kind}`}, `${label} ${value}`);
}

function turnChip(turn) {
  if (turn === 'submittable') return el('span', {class: 'chip good'}, 'submittable');
  if (turn === 'reviewer') return el('span', {class: 'chip'}, 'reviewer');
  return el('span', {class: 'chip'}, 'author');
}

// ── routing ───────────────────────────────────────────────────────────────

function route() {
  const parts = location.hash.replace(/^#\/?/, '').split('/');
  if (parts[0] === 'c' && parts.length >= 3) {
    return {view: 'change', repo: Number(parts[1]), id: parts[2],
            file: parts.length > 3 ? decodeURIComponent(parts.slice(3).join('/')) : null};
  }
  return {view: 'list', repo: Number(parts[0] || state.repo)};
}

function go(hash) { if (location.hash !== hash) location.hash = hash; else render(); }

async function render() {
  const where = route();
  if (where.repo !== state.repo && !Number.isNaN(where.repo)) state.repo = where.repo;
  document.getElementById('repo').value = String(state.repo);
  try {
    if (where.view === 'change') await renderChange(where.id, where.file);
    else await renderList();
  } catch (error) {
    say(error.message, true);
  }
}

// ── the dashboard ─────────────────────────────────────────────────────────

async function postDashboard(fields) {
  const response = await fetch('/api/action', {
    method: 'POST', headers: {'X-Plustwo-Token': TOKEN, 'Content-Type': 'application/json'},
    body: JSON.stringify(fields)});
  const data = await response.json();
  if (!response.ok) say(data.error, true);
  await render();
}

async function renderList() {
  const data = await api('dashboard');
  const view = document.getElementById('view');
  state.change = null;
  state.listIds = [];
  const order = data.sections.map((section) => section.name);
  const blocks = data.sections.map((section) => {
    const title = el('h2', {
      class: 'section-title', draggable: 'true', title: 'Click folds, drag moves',
      onclick: () => postDashboard({action: 'fold', section: section.name}),
      ondragstart: (event) => event.dataTransfer.setData('text/plain', section.name),
      ondragover: (event) => event.preventDefault(),
      ondrop: (event) => {
        event.preventDefault();
        const moved = event.dataTransfer.getData('text/plain');
        const names = order.filter((name) => name !== moved);
        names.splice(names.indexOf(section.name), 0, moved);
        postDashboard({action: 'order', sections: names});
      }},
      section.folded ? '▸ ' : '▾ ', section.title, ' ', el('span', {class: 'dim'}, `(${section.count})`));
    if (section.folded) return el('section', {}, title);
    if (!section.changes.length) return el('section', {}, title, el('p', {class: 'dim none'}, '(none)'));
    const rows = section.changes.map((change) => {
      const index = state.listIds.length;
      const link = `#/c/${change.repo}/${change.id}`;
      state.listIds.push(link);
      return el('tr', {class: (index === state.listIndex ? 'selected ' : '') + (change.wip ? 'wip' : ''),
                       onclick: () => go(link)},
        el('td', {class: 'dim'}, change.number),
        el('td', {}, el('a', {href: link}, change.subject)),
        el('td', {}, change.repo_name),
        el('td', {class: 'dim'}, change.topic, ' ',
           change.needs_rebase ? el('span', {class: 'chip warn'}, 'needs rebase') : null),
        el('td', {class: 'dim'}, change.short_id),
        el('td', {}, `PS${change.patch_set}`),
        el('td', {}, voteChip('CR', change.code_review)),
        el('td', {}, voteChip('V', change.verified)),
        el('td', {}, change.wip ? el('span', {class: 'chip'}, 'WIP')
                     : change.your_turn ? el('span', {class: 'chip warn'}, 'your turn') : turnChip(change.turn)),
        el('td', {class: 'dim'}, change.comments));
    });
    return el('section', {}, title, el('table', {class: 'list'}, el('tbody', {}, rows)));
  });
  state.listIndex = Math.min(state.listIndex, Math.max(state.listIds.length - 1, 0));
  view.replaceChildren(...blocks);
}

// ── the change page ───────────────────────────────────────────────────────

async function renderChange(id, file) {
  const change = await api('change', {id});
  if (!state.change || state.change.id !== change.id) {
    state.a = 'base'; state.b = 'latest'; state.cursor = 0; state.editor = null;
  }
  state.change = change;
  state.file = file && change.files.some((entry) => entry.path === file) ? file
    : (state.file && change.files.some((entry) => entry.path === state.file) ? state.file
       : (change.files[1] || change.files[0]).path);
  state.diff = await api('diff', {id, path: state.file, a: state.a, b: state.b,
                                  context: state.context, whitespace: state.whitespace,
                                  theme: matchMedia('(prefers-color-scheme: dark)').matches
                                    ? 'dark' : 'light'});
  const view = document.getElementById('view');
  view.replaceChildren(changeHeader(change), changeLevelThreads(change), toolbar(change),
                       el('div', {class: 'layout'}, fileList(change), diffView(change)));
  const cursorRow = document.querySelector('tr.cursor');
  if (cursorRow && state.scrollToCursor) {
    cursorRow.scrollIntoView({block: 'center'});
    state.scrollToCursor = false;
  }
  if (state.editor) document.querySelector('.editor textarea')?.focus();
}

function changeHeader(change) {
  return el('div', {},
    el('div', {class: 'change-head'},
      el('a', {href: `#/${state.repo}`}, '◂'),
      el('h1', {}, change.subject),
      el('span', {class: 'chip'}, change.state),
      el('span', {class: 'dim'}, `${change.short_id} · ${change.topic} · PS${change.patch_set}`),
      voteChip('Code-Review', change.code_review),
      voteChip('Verified', change.verified),
      turnChip(change.turn)),
    change.problems.length
      ? el('p', {class: 'dim'}, 'Not submittable: ', change.problems.join('; ')) : null);
}

function changeLevelThreads(change) {
  const threads = change.comments.filter((comment) => comment.path === PATCHSET_LEVEL);
  if (!threads.length) return el('div');
  return el('div', {}, el('h2', {}, 'On the whole change'), threads.map(threadView));
}

function toolbar(change) {
  const versions = ['base', ...change.patch_sets.map(String)];
  const select = (value, options, onchange, labels = options) => el('select', {onchange},
    options.map((option, index) => el('option', {value: option, selected: option === value},
                                      labels[index])));
  const b = state.b === 'latest' ? String(change.patch_set) : state.b;
  return el('div', {class: 'toolbar'},
    el('button', {class: 'primary', onclick: openReply}, 'Reply'),
    el('button', {onclick: quickApprove, title: 'Code-Review+2'}, '+2'),
    el('span', {class: 'dim'}, 'compare'),
    select(state.a, versions, (event) => { state.a = event.target.value; render(); },
           versions.map((version) => version === 'base' ? 'Base' : `PS${version}`)),
    el('span', {}, '→'),
    select(b, change.patch_sets.map(String), (event) => { state.b = event.target.value; render(); },
           change.patch_sets.map((number) => `PS${number}`)),
    el('span', {class: 'dim'}, 'context'),
    select(state.context, CONTEXTS, (event) => { state.context = event.target.value; render(); }),
    el('span', {class: 'dim'}, 'whitespace'),
    select(state.whitespace, WHITESPACE,
           (event) => { state.whitespace = event.target.value; render(); }));
}

function fileList(change) {
  const largest = Math.max(1, ...change.files.map((file) => file.added + file.removed));
  const rows = change.files.map((file) => {
    const threads = change.comments.filter((comment) => comment.path === file.path).length;
    const width = (count) => `${Math.ceil(40 * count / largest)}px`;
    return el('tr', {class: file.path === state.file ? 'current' : ''},
      el('td', {}, el('input', {type: 'checkbox', checked: file.reviewed, title: 'reviewed',
                                onchange: () => act('reviewed', {path: file.path})})),
      el('td', {class: 'path', onclick: () => openFile(file.path)},
         file.path === COMMIT_MSG ? 'Commit message' : file.path,
         file.renamed_from ? el('div', {class: 'dim'}, `from ${file.renamed_from}`) : null),
      el('td', {class: 'dim'}, threads ? `💬${threads}` : ''),
      el('td', {}, file.added ? el('span', {class: 'bar add', style: `width:${width(file.added)}`}) : null,
         file.removed ? el('span', {class: 'bar del', style: `width:${width(file.removed)}`}) : null));
  });
  const reviewed = change.files.filter((file) => file.reviewed).length;
  return el('div', {class: 'files'},
    el('div', {class: 'dim'}, `${reviewed}/${change.files.length} reviewed`),
    el('table', {}, el('tbody', {}, rows)));
}

function openFile(path) {
  state.cursor = 0; state.editor = null;
  go(`#/c/${state.repo}/${state.change.id}/${encodeURIComponent(path)}`);
}

// The threads of the shown file, by where they go: 'file' for the whole
// file and threads on lines the diff doesn't show, else 'old:12' or 'new:12'.
function threadsByPlace(change, rows) {
  const shown = new Set();
  for (const row of rows) {
    if (row.old.number) shown.add(`old:${row.old.number}`);
    if (row.new.number) shown.add(`new:${row.new.number}`);
  }
  const places = new Map();
  for (const comment of change.comments) {
    if (comment.path !== state.file) continue;
    let key = comment.line ? `${comment.side}:${comment.line}` : 'file';
    if (!shown.has(key)) key = 'file';
    if (!places.has(key)) places.set(key, []);
    places.get(key).push(comment);
  }
  return places;
}

function diffView(change) {
  const diff = state.diff;
  const wrap = el('div', {class: 'diff-wrap'});
  if (diff.binary) { wrap.append(el('p', {class: 'dim'}, 'Binary file.')); return wrap; }
  const places = threadsByPlace(change, diff.rows);
  const below = {old: new Set(diff.from_below[0]), new: new Set(diff.from_below[1])};
  const partly = {old: new Set(diff.partly[0]), new: new Set(diff.partly[1])};
  const body = el('tbody');
  const fileThreads = places.get('file') || [];
  if (fileThreads.length || (state.editor && state.editor.line === null)) {
    body.append(el('tr', {}, el('td', {colspan: 4},
      fileThreads.map(threadView), editorFor(null, 'new'))));
  }
  state.rowCount = diff.rows.length;
  state.cursor = Math.min(state.cursor, Math.max(diff.rows.length - 1, 0));
  diff.rows.forEach((row, index) => {
    if (row.skipped) {
      body.append(el('tr', {class: 'gap', onclick: () => { state.context = 'all'; render(); }},
        el('td', {colspan: 4}, `… ${row.skipped} common lines …`)));
      return;
    }
    const tr = el('tr', {class: index === state.cursor ? 'cursor' : ''});
    for (const side of ['old', 'new']) {
      const half = row[side];
      let kind = half.kind;
      if ((kind === 'removed' || kind === 'added') && half.number) {
        if (below[side].has(half.number)) kind = 'below';
        else if (partly[side].has(half.number)) kind = 'partly';
      }
      const isCursorSide = index === state.cursor && side === state.side;
      tr.append(el('td', {class: `num ${kind}`, title: 'comment',
                          onclick: () => half.number && openEditor(side, half.number, index)},
                   half.number || ''),
                el('td', {class: `text ${kind}${isCursorSide ? ' side' : ''}`,
                          onclick: () => { state.cursor = index; state.side = side; render(); }},
                   halfText(half)));
    }
    body.append(tr);
    const threads = [...(places.get(`old:${row.old.number}`) || []),
                     ...(places.get(`new:${row.new.number}`) || [])];
    const editors = ['old', 'new'].map((side) => row[side].number
      && state.editor && state.editor.side === side && state.editor.line === row[side].number
      ? editorFor(row[side].number, side) : null).filter(Boolean);
    if (threads.length || editors.length) {
      body.append(el('tr', {}, el('td', {colspan: 4}, threads.map(threadView), editors)));
    }
  });
  if (!diff.rows.length) body.append(el('tr', {}, el('td', {colspan: 4, class: 'dim'},
                                                    'No differences in this file.')));
  wrap.append(el('table', {class: 'diff'},
    el('colgroup', {}, el('col', {class: 'num'}), el('col'), el('col', {class: 'num'}), el('col')),
    body));
  return wrap;
}

// The text of a half with syntax colours and intraline edits as spans.
function halfText(half) {
  const text = half.text;
  if (!text) return '';
  const colours = new Array(text.length).fill(null);
  const styles = new Array(text.length).fill('');
  if (half.colours) {
    let position = 0;
    for (const [part, colour, partStyles] of half.colours) {
      for (let i = 0; i < part.length && position < text.length; i++, position++) {
        colours[position] = colour;
        styles[position] = partStyles.join(' ');
      }
    }
  }
  const emph = new Array(text.length).fill(false);
  for (const [begin, end] of half.edits) for (let i = begin; i < end; i++) emph[i] = true;
  const spans = [];
  let start = 0;
  for (let i = 1; i <= text.length; i++) {
    if (i < text.length && colours[i] === colours[start] && emph[i] === emph[start]
        && styles[i] === styles[start]) continue;
    const style = [colours[start] ? `color:${colours[start]}` : '',
                   styles[start].includes('bold') ? 'font-weight:600' : '',
                   styles[start].includes('italic') ? 'font-style:italic' : ''].filter(Boolean);
    spans.push(el('span', {class: emph[start] ? 'emph' : null,
                           style: style.length ? style.join(';') : null}, text.slice(start, i)));
    start = i;
  }
  return spans;
}

// ── threads and writing comments ──────────────────────────────────────────

function threadView(comment) {
  const status = comment.is_draft ? 'draft' : comment.status;
  const pending = comment.draft_status ? ` → ${comment.draft_status}` : '';
  const editing = state.editor && state.editor.number === comment.number;
  const messages = comment.messages.map((message, index) => {
    const last = index === comment.messages.length - 1;
    if (editing && last && !message.sent) return null;
    return el('div', {class: `msg${message.sent ? '' : ' draft'}`},
      el('span', {class: 'who'}, message.author === 'reviewer' ? 'Reviewer' : 'Author',
         message.sent ? '' : ' (draft)', ': '),
      message.text || (message.unresolved ? '(unresolved)' : '(resolved)'));
  });
  const number = comment.number;
  return el('div', {class: `thread ${comment.status}`},
    el('div', {}, el('span', {class: `chip ${status === 'unresolved' ? 'warn' : ''}`},
                     `#${number} ${status}${pending}`), ' ',
       el('span', {class: 'dim'}, comment.place + (comment.side === 'old' ? ' (old)' : ''))),
    messages,
    editing ? editorFor(null, null, comment) : el('div', {class: 'actions'},
      comment.has_draft
        ? [el('button', {onclick: () => { state.editor = {number, text: comment.messages.at(-1).text};
                                          render(); }}, 'Edit draft'),
           el('button', {onclick: () => act('delete-draft', {number})}, 'Delete draft')]
        : [el('button', {onclick: () => { state.editor = {number, text: ''}; render(); }}, 'Reply'),
           el('button', {onclick: () => act('done', {number})}, 'Done'),
           el('button', {onclick: () => act('ack', {number})}, 'Ack')],
      el('label', {}, el('input', {type: 'checkbox',
                                   checked: (comment.draft_status || comment.status) === 'resolved',
                                   onchange: () => act('resolve', {number})}), ' Resolved')));
}

function openEditor(side, line, index) {
  state.cursor = index; state.side = side;
  state.editor = {side, line, text: ''};
  render();
}

// The box for a new comment (line, side), or for a reply or draft edit of
// comment.
function editorFor(line, side, comment = null) {
  const editor = state.editor;
  if (!editor) return null;
  if (!comment && (editor.number !== undefined || editor.line !== line || editor.side !== side)) {
    return null;
  }
  const textarea = el('textarea', {rows: 3, placeholder: 'Comment, a draft until sent'});
  textarea.value = editor.text || '';
  textarea.addEventListener('input', () => { editor.text = textarea.value; });
  const save = async () => {
    const text = textarea.value.trim();
    state.editor = null;
    if (comment) {
      if (comment.has_draft) await act('edit-draft', {number: comment.number, text});
      else if (text) await act('reply', {number: comment.number, text});
      else render();
    } else if (text) {
      const place = line === null ? state.file : `${state.file}:${line}`;
      await act('comment', {place, side, text});
    } else render();
  };
  textarea.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); save(); }
    if (event.key === 'Escape') { state.editor = null; render(); }
  });
  return el('div', {class: 'editor'}, textarea,
    el('div', {class: 'actions'}, el('button', {class: 'primary', onclick: save}, 'Save'),
       el('button', {onclick: () => { state.editor = null; render(); }}, 'Cancel'),
       el('span', {class: 'dim'}, 'Ctrl-Enter saves, Esc cancels')));
}

// ── the reply dialog and quick approve ────────────────────────────────────

function draftCount() {
  return state.change.comments.reduce(
    (count, comment) => count + comment.messages.filter((message) => !message.sent).length, 0);
}

function openReply() {
  if (!state.change) return;
  const dialog = document.getElementById('reply-dialog');
  document.getElementById('reply-drafts').textContent = `${draftCount()} draft(s) will be sent.`;
  document.getElementById('reply-text').value = '';
  const votes = document.getElementById('reply-vote');
  votes.replaceChildren(el('legend', {}, 'Code-Review'),
    ...[['', 'no change'], ['-2', '-2'], ['-1', '-1'], ['0', '0'], ['1', '+1'], ['2', '+2']].map(
      ([value, label]) => el('label', {}, el('input', {type: 'radio', name: 'vote', value,
                                                       checked: value === ''}), ` ${label} `)));
  dialog.returnValue = '';
  dialog.showModal();
}

document.getElementById('reply-dialog').addEventListener('close', async (event) => {
  const dialog = event.target;
  if (dialog.returnValue !== 'send') return;
  const vote = dialog.querySelector('input[name=vote]:checked')?.value;
  const result = await act('send', {message: document.getElementById('reply-text').value.trim(),
                                    vote: vote ? Number(vote) : null});
  say(`${result.sent} draft(s) sent.`);
});

async function quickApprove() {
  const drafts = draftCount();
  const send = drafts > 0 && confirm(`${drafts} draft(s) unsent. Send them along?`);
  const result = await act('approve', {send});
  say(`Code-Review+2 on PS${result.patch_set}` + (result.sent ? `, ${result.sent} draft(s) sent.` : '.'));
}

// ── keys: the same presets as the terminal interface ──────────────────────

// A key event as the presets write keys: j, G, C-n, M-v, Enter, Esc, Tab.
function keyName(event) {
  const named = {Enter: 'Enter', Escape: 'Esc', Tab: 'Tab', ArrowUp: 'Up', ArrowDown: 'Down',
                 ArrowLeft: 'Left', ArrowRight: 'Right', PageUp: 'PageUp',
                 PageDown: 'PageDown', Home: 'Home', End: 'End', ' ': 'Space'};
  let name = named[event.key] || (event.key.length === 1 ? event.key : null);
  if (!name) return null;
  if (event.ctrlKey && name.length === 1) name = `C-${name.toLowerCase()}`;
  else if (event.altKey && name.length === 1) name = `M-${name}`;
  return name;
}

let pending = [];
let count = '';

function actionFor(sequence) {
  let prefix = false;
  for (const [action, spec] of Object.entries(state.keys.keys)) {
    for (const alternative of spec.split(',')) {
      const keys = alternative.trim().split(' ');
      if (keys.join(' ') === sequence.join(' ')) return {action};
      if (keys.length > sequence.length
          && keys.slice(0, sequence.length).join(' ') === sequence.join(' ')) prefix = true;
    }
  }
  return {prefix};
}

document.addEventListener('keydown', (event) => {
  if (!state.keys || event.target.closest('textarea, input, select, dialog')) return;
  const name = keyName(event);
  if (!name) return;
  if (/^[0-9]$/.test(name) && !pending.length && (name !== '0' || count)) {
    count += name; event.preventDefault(); return;
  }
  pending.push(name);
  const found = actionFor(pending);
  if (found.prefix) { event.preventDefault(); return; }
  pending = [];
  const times = Math.max(1, Number(count || '1'));
  count = '';
  if (found.action && runAction(found.action, times)) event.preventDefault();
});

function runAction(action, times) {
  const change = state.change;
  const rows = state.diff ? state.diff.rows : [];
  const moveCursor = (to) => {
    state.cursor = Math.max(0, Math.min(to, rows.length - 1));
    state.scrollToCursor = true; render();
  };
  const jump = (step, test) => {
    for (let i = state.cursor + step; i >= 0 && i < rows.length; i += step) {
      if (test(i)) { moveCursor(i); return; }
    }
  };
  const chunkStart = (i) => changed(rows[i]) && !changed(rows[i - 1]);
  const changed = (row) => row && !row.skipped
    && (row.old.kind !== 'context' || row.new.kind !== 'context');
  const hasThread = (row) => !row.skipped && change.comments.some((comment) => comment.path === state.file
    && ((comment.side === 'old' && comment.line === row.old.number)
        || (comment.side !== 'old' && comment.line === row.new.number)));
  const fileIndex = change ? change.files.findIndex((file) => file.path === state.file) : -1;
  const list = !change;
  switch (action) {
    case 'help': showHelp(); return true;
    case 'reload': render(); return true;
    case 'down': if (list) { state.listIndex += times; renderList(); } else moveCursor(state.cursor + times); return true;
    case 'up': if (list) { state.listIndex = Math.max(0, state.listIndex - times); renderList(); } else moveCursor(state.cursor - times); return true;
    case 'top': if (list) { state.listIndex = 0; renderList(); } else moveCursor(0); return true;
    case 'bottom': if (list) { state.listIndex = state.listIds.length - 1; renderList(); } else moveCursor(rows.length - 1); return true;
    case 'page-down': moveCursor(state.cursor + 20); return true;
    case 'page-up': moveCursor(state.cursor - 20); return true;
    case 'open':
      if (list && state.listIds.length) go(state.listIds[state.listIndex]);
      return true;
    case 'back': if (state.editor) { state.editor = null; render(); } else go(`#/${state.repo}`); return true;
    case 'quit': go(`#/${state.repo}`); return true;
  }
  if (list || !change) return false;
  const row = rows[state.cursor];
  switch (action) {
    case 'next-file': if (fileIndex < change.files.length - 1) openFile(change.files[fileIndex + 1].path); return true;
    case 'prev-file': if (fileIndex > 0) openFile(change.files[fileIndex - 1].path); return true;
    case 'next-chunk': jump(1, chunkStart); return true;
    case 'prev-chunk': jump(-1, chunkStart); return true;
    case 'next-thread': jump(1, (i) => hasThread(rows[i])); return true;
    case 'prev-thread': jump(-1, (i) => hasThread(rows[i])); return true;
    case 'side-old': state.side = 'old'; render(); return true;
    case 'side-new': state.side = 'new'; render(); return true;
    case 'comment':
      if (row && !row.skipped && row[state.side].number) openEditor(state.side, row[state.side].number, state.cursor);
      else if (row && row.skipped) { state.context = 'all'; render(); }
      return true;
    case 'file-comment': state.editor = {side: 'new', line: null, text: ''}; render(); return true;
    case 'send': openReply(); return true;
    case 'approve': quickApprove(); return true;
    case 'reviewed': act('reviewed', {path: state.file}); return true;
    case 'more-context': state.context = CONTEXTS[Math.min(CONTEXTS.indexOf(state.context) + 1, CONTEXTS.length - 1)]; render(); return true;
    case 'less-context': state.context = CONTEXTS[Math.max(CONTEXTS.indexOf(state.context) - 1, 0)]; render(); return true;
    case 'whitespace': state.whitespace = WHITESPACE[(WHITESPACE.indexOf(state.whitespace) + 1) % WHITESPACE.length]; render(); return true;
  }
  return false;
}

// Actions the page can do, for the help
const WEB_ACTIONS = new Set(['help', 'reload', 'down', 'up', 'top', 'bottom', 'page-down', 'page-up',
  'open', 'back', 'next-file', 'prev-file', 'next-chunk', 'prev-chunk', 'next-thread',
  'prev-thread', 'side-old', 'side-new', 'comment', 'file-comment', 'send', 'approve', 'reviewed',
  'more-context', 'less-context', 'whitespace']);

function showHelp() {
  const rows = state.keys.actions.filter((action) => WEB_ACTIONS.has(action.name)).map((action) =>
    el('tr', {}, el('td', {}, state.keys.keys[action.name].split(',').map(
      (keys) => el('kbd', {}, keys.trim()))), el('td', {}, action.description)));
  document.getElementById('help-keys').replaceChildren(
    el('p', {class: 'dim'}, `Preset ${state.keys.preset}. Click a line number to comment.`),
    el('table', {}, el('tbody', {}, rows)));
  document.getElementById('help-dialog').showModal();
}

// ── start and live updates ────────────────────────────────────────────────

async function poll() {
  try {
    const {version} = await api('version');
    if (state.version !== null && version !== state.version && !state.editor
        && !document.querySelector('dialog[open]')) {
      await render();
    }
    state.version = version;
  } catch (error) {
    say('The server is gone.', true);
  }
}

async function start() {
  const {repositories} = await api('repos');
  state.repos = repositories;
  const select = document.getElementById('repo');
  select.replaceChildren(...repositories.map((name, index) => el('option', {value: index}, name)));
  // The dashboard shows every repository, the change page names its own.
  select.hidden = true;
  select.addEventListener('change', () => go(`#/${select.value}`));
  state.keys = await api('keys');
  document.getElementById('help-button').addEventListener('click', showHelp);
  window.addEventListener('hashchange', render);
  await render();
  setInterval(poll, 1000);
}

start();
