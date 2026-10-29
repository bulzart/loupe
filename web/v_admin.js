import { h, put, toast, fmtDate, chips, copyText, status, fmtBytes } from './ui.js';
import { api } from './api.js';

const head = (title, sub, ...actions) => h('div', { class: 'head' }, h('div', {}, h('h1', {}, title), sub ? h('div', { class: 'muted' }, sub) : null), h('div', { class: 'row' }, actions));
const stat = (n, label, href) => h('a', { href, class: 'card stat', style: 'color:inherit;text-decoration:none' }, h('b', {}, n ?? 0), h('span', {}, label));

export async function overview() {
  const [o, fl, ms] = await Promise.all([api('GET', '/api/overview'), api('GET', '/api/flags?limit=6'), api('GET', '/api/misses')]);
  const samples = ms.misses.flatMap((m) => m.moments.map((s) => ({ ...s, miss: m }))).slice(0, 6);
  return h('div', {},
    head('Overview', 'Flags, generation misses and the QC sample library of the pipeline'),
    h('div', { class: 'grid g4' }, stat(o.flags_open, 'open flags', '#/flags'), stat(o.flags, 'flags recorded', '#/flags'), stat(o.samples, 'QC samples', '#/samples'),
      stat(o.captures_unclaimed, 'unclaimed captures', '#/captures')),
    h('div', { class: 'grid g2', style: 'margin-top:14px' },
      h('div', { class: 'card' }, h('h2', {}, 'Latest flags'),
        fl.entries.length ? h('table', {}, h('tbody', {}, fl.entries.map((e) => h('tr', { class: 'click', onclick: () => (location.hash = '#/flags/' + e.id) },
          h('td', { class: 'mono' }, e.id), h('td', {}, status(e.status)), h('td', {}, e.title))))) : h('div', { class: 'empty' }, 'No flags yet.')),
      h('div', { class: 'card' }, h('h2', {}, 'Latest QC samples'),
        samples.length ? h('div', { class: 'grid g3' }, samples.map((s) => h('a', { href: '#/samples', title: s.title }, h('img', { src: s.image_url, style: 'width:100%;height:150px;object-fit:cover;object-position:top;border-radius:8px' }))))
          : h('div', { class: 'empty' }, 'No samples yet. Report a miss on a video and mark a moment.'))),
    h('p', { class: 'muted small', style: 'margin-top:18px' }, `${o.keys} active API key(s) · ${o.consults} keeper consultation(s)`));
}

// ---------------------------------------------------------------------------------------- keeper
export async function keeper() {
  const d = await api('GET', '/api/qc/keeper');
  const u = await api('GET', '/api/qc/keeper/understanding').catch(() => ({ status: null, readings: [] }));
  const k = d.keeper;
  const model = h('input', { value: k.model, size: 24 });
  const effort = h('select', {}, (k.efforts || []).map((e) => h('option', { value: e }, e)));
  effort.value = k.effort;
  const out = h('div');
  const ask = h('textarea', { placeholder: 'Ask as a QC agent would: "The product looks larger after the cut; which samples apply?"' });
  const run = async () => {
    out.replaceChildren(h('div', { class: 'muted' }, 'Asking the keeper…'));
    try {
      const r = await api('POST', '/api/qc/consult', { question: ask.value, run: 'admin-test', video: '' });
      put(out, h('div', {}, h('span', { class: 'chip' }, r.mode), h('span', { class: 'muted small' }, ` ${r.ms} ms`)),
        r.advice ? h('p', {}, r.advice) : null, r.text ? h('pre', {}, r.text) : null,
        (r.relevant || r.samples || []).map((s) => h('div', { class: 'card', style: 'margin-top:8px' }, h('b', {}, s.id), ' ', s.why || s.title || '', s.check ? h('div', { class: 'muted' }, 'Check: ' + s.check) : null,
          s.image_url ? h('img', { src: s.image_url, style: 'width:240px;margin-top:6px;border-radius:8px' }) : null)),
        r.not_covered ? h('p', { class: 'muted' }, 'Not covered: ' + r.not_covered) : null,
        (r.flags || []).length ? h('p', { class: 'muted' }, 'Flagged: ' + r.flags.map((f) => (f.id ? f.id + ': ' : '') + f.what + (f.effect ? ' (' + f.effect + ')' : '')).join(' · ')) : null, r.keeper_error ? h('p', { class: 'muted' }, 'Keeper unavailable, fallback used: ' + r.keeper_error) : null);
    } catch (e) { out.replaceChildren(h('div', { class: 'muted' }, e.message)); }
  };
  return h('div', {},
    head('QC keeper', 'One separate, read-only Claude session that holds the sample library and answers the QC agents'),
    h('div', { class: 'grid g2' },
      h('div', { class: 'card' }, h('h2', {}, 'Session'),
        h('dl', { class: 'kv' }, h('dt', {}, 'claude CLI'), h('dd', {}, k.claude_found ? 'found' : 'NOT FOUND (QC gets the plain brief)'), h('dt', {}, 'session'), h('dd', { class: 'mono' }, k.session_id || '— (starts on the first question)'),
          h('dt', {}, 'turns'), h('dd', {}, k.turns ?? 0), h('dt', {}, 'started'), h('dd', {}, fmtDate(k.created)), h('dt', {}, 'last used'), h('dd', {}, fmtDate(k.last_used)),
          h('dt', {}, 'library'), h('dd', { class: 'mono' }, k.library)),
        h('p', { class: 'muted small' }, 'Tools: Read only. It cannot write, edit or run anything; the library changes only here, by you. After 30 turns the session is replaced by a fresh one rebuilt from the library, so nothing is lost.'),
        h('label', { class: 'f' }, 'Model'), h('div', { class: 'row' }, model, h('button', { class: 'btn sm', onclick: async () => { await api('POST', '/api/settings', { keeper_model: model.value }); toast('Model saved'); } }, 'Save')),
        h('label', { class: 'f' }, 'Effort (the judge and the collector; never below high)'), h('div', { class: 'row' }, effort, h('button', { class: 'btn sm', onclick: async () => { await api('POST', '/api/settings', { keeper_effort: effort.value }); toast('Effort saved'); } }, 'Save')),
        h('div', { style: 'margin-top:12px' }, h('button', { class: 'btn danger', onclick: async () => { await api('POST', '/api/qc/keeper/reset', {}); toast('Keeper reset'); keeper().then((n) => document.querySelector('.main').replaceChildren(n)); } }, 'Reset the session'))),
      h('div', { class: 'card' }, h('h2', {}, 'Ask it'), ask, h('div', { style: 'margin-top:8px' }, h('button', { class: 'btn primary', onclick: run }, 'Ask')), out)),
    understandingCard(u),
    gapsCard(d.consults),
    h('div', { class: 'card', style: 'margin-top:14px' }, h('h2', {}, 'Consultations'),
      d.consults.length ? h('table', {}, h('thead', {}, h('tr', {}, ['When', 'Key', 'Run / video', 'Asked for', 'Question', 'Samples', 'Mode', 'The keeper asked, missed or held back', 'ms'].map((x) => h('th', {}, x)))),
        h('tbody', {}, d.consults.map((c) => h('tr', {}, h('td', {}, fmtDate(c.ts)), h('td', {}, c.key_name), h('td', { class: 'mono' }, `${c.run || ''} ${c.video || ''}`), h('td', {}, c.purpose === 'plan' ? 'planning hints' : c.purpose === 'template' ? 'prompt template' : 'QC'), h('td', {}, c.question.slice(0, 140)),
          h('td', { class: 'mono' }, c.sample_ids), h('td', {}, c.mode), h('td', { class: 'small' }, asksLines(c.asks || {})), h('td', {}, c.ms))))) : h('div', { class: 'empty' }, 'No consultation yet.')));
}

// What the keeper asked for, could not get, held back or found the library does not cover, in plain words (text nodes only).
function asksLines(a) {
  const out = [];
  (a.needs_asked || []).forEach((n) => out.push(h('div', {}, `asked for ${n.kind}${n.question ? ': ' + n.question : ''}${n.fulfilled ? ' (fetched)' : ' (NOT available' + (n.reason ? ': ' + n.reason : '') + ')'}`)));
  (a.needs_unmet || []).filter((n) => n.kind === 'more').forEach((n) => out.push(h('div', {}, 'still wanted: ' + n.why)));
  if (a.not_covered) out.push(h('div', {}, 'not covered by the library: ' + a.not_covered));
  (a.flags || []).forEach((f) => out.push(h('div', {}, `flagged ${f.id || 'the library'}: ${f.what}${f.effect ? ' (' + f.effect + ')' : ''}`)));
  if (a.not_read && a.not_read.length) out.push(h('div', {}, 'judged from your words only (no reading of the picture yet): ' + a.not_read.join(', ')));
  (a.held_back_why || []).forEach((x) => out.push(h('div', { class: 'muted' }, `held back ${x.id}: ${x.reason}`)));
  if (a.none_reason) out.push(h('div', { class: 'muted' }, 'no hint: ' + a.none_reason));
  if (a.keeper_error) out.push(h('div', {}, 'keeper unavailable: ' + a.keeper_error));
  return out;
}

// The open gaps across the recent consultations: what the keeper could not get and what the library does not cover. The admin closes a gap by adding a
// sample (Generation misses) or by answering in a report's words; nothing here changes the library.
function gapsCard(cons) {
  const rows = cons.filter((c) => c.asks && ((c.asks.needs_unmet || []).length || c.asks.not_covered || (c.asks.flags || []).length));
  return h('div', { class: 'card', style: 'margin-top:14px' }, h('h2', {}, 'What the keeper is missing'),
    h('p', { class: 'muted small' }, 'From the last 50 consultations: what the keeper asked for and could not get, what it flagged as missing or ambiguous in a sample’s record, and what it said the library does not cover. A new or corrected sample in Generation misses is how a gap gets closed.'),
    rows.length ? rows.map((c) => h('div', { class: 'card', style: 'margin-top:6px' }, h('div', { class: 'muted small' }, `${fmtDate(c.ts)} · ${c.purpose === 'plan' ? 'planning' : c.purpose === 'template' ? 'prompt template' : 'QC'} · ${c.run || ''} ${c.video || ''}`),
      (c.asks.needs_unmet || []).map((n) => h('div', {}, `could not get ${n.kind}${n.question ? ': ' + n.question : ''}${n.reason ? ' (' + n.reason + ')' : ''}`)),
      c.asks.not_covered ? h('div', {}, 'not covered: ' + c.asks.not_covered) : null,
      (c.asks.flags || []).map((f) => h('div', {}, `flagged ${f.id || 'the library'}: ${f.what}${f.effect ? ' (' + f.effect + ')' : ''}`)))) : h('div', { class: 'empty' }, 'Nothing missing in the recent consultations.'));
}

// What a data collector saw in each sample's picture: collected once by a session that opened it (no verdict, never the prompts), kept as one JSON file per
// sample, carried in the keeper's digest; the keeper (the judge) decides what it means.
const READING_LABELS = [['marked', 'What the marks point at'], ['right_version', 'Right frame'], ['product_and_scene', 'Product and scene'],
  ['admin_words', 'Your words'], ['unclear', 'Unclear in the picture'], ['prompts_saved', 'Prompts saved (the judge reads them whole)']];

function understandingCard(u) {
  const st = u.status;
  if (!st) return null;
  const reload = () => keeper().then((n) => document.querySelector('.main').replaceChildren(n));
  const pending = [...st.missing, ...st.stale];
  const failed = st.failed || [];
  const reader = h('input', { value: st.model, size: 24 });
  const state = (r) => (r.reading ? `read ${fmtDate(r.read_at)} by ${r.read_by}` + (r.images_read === false ? ' · WITHOUT opening the picture' : '')
    : r.failed ? `COULD NOT BE READ (${r.failed.attempts} attempt(s)): ${r.failed.error}` : 'not read yet');
  return h('div', { class: 'card', style: 'margin-top:14px' }, h('h2', {}, 'What the keeper understood of each sample'),
    h('p', { class: 'muted small' }, `Read from its picture: ${st.understood} of ${st.library_size} sample(s)` + (pending.length ? ` · not read yet or changed since: ${pending.join(', ')}` : ' · every sample has a current reading')
      + (failed.length ? ` · given up after failed readings (it waits for a change or your button): ${failed.join(', ')}` : '')
      + '. Each sample is read once; only a new or changed sample is read again, and a sample that comes back unchanged gets its earlier reading back. The keeper holds these readings with your words; your words win where they differ.'),
    h('label', { class: 'f' }, 'Collector model (opens each picture once and collects what it shows; no verdict, never the prompts)'), h('div', { class: 'row' }, reader,
      h('button', { class: 'btn sm', onclick: async () => { await api('POST', '/api/settings', { keeper_extract_model: reader.value }); toast('Collector model saved'); } }, 'Save'),
      h('button', { class: 'btn sm', onclick: async () => { await api('POST', '/api/qc/keeper/understand', {}); toast('Reading the new or changed samples in the background'); setTimeout(reload, 1500); } }, 'Read new or changed samples now')),
    (u.readings || []).map((r) => h('details', { class: 'card', style: 'margin-top:8px' },
      h('summary', {}, h('b', {}, r.id), ' ', r.title.slice(0, 160), ' ', h('span', { class: 'muted small' }, state(r))),
      r.image_url ? h('img', { src: r.image_url, style: 'width:320px;margin-top:6px;border-radius:8px' }) : null,
      r.reading ? h('div', {},
        (r.reading.frames_seen || []).map((f) => h('div', { class: 'small' }, h('b', {}, f.frame + ': '), f.seen)),
        READING_LABELS.filter(([key]) => r.reading[key]).map(([key, label]) => h('div', { style: 'margin-top:4px' }, h('b', {}, label + ': '), r.reading[key]))) : null,
      h('div', { style: 'margin-top:6px' }, h('button', { class: 'btn sm', onclick: async () => { await api('POST', '/api/qc/keeper/understand', { ids: [r.id] }); toast(`Reading ${r.id} in the background`); } },
        r.reading ? 'Read this sample again' : 'Read this sample now')))));
}

// ---------------------------------------------------------------------------------------- captures
export async function captures() {
  const d = await api('GET', '/api/captures?limit=200&full=1');
  const reload = () => captures().then((n) => document.querySelector('.main').replaceChildren(n));
  return h('div', {},
    head('Captures', 'Raw Higgsfield responses that look like a block, refusal or flag, saved by the hook the moment they came back'),
    h('p', { class: 'muted' }, 'A capture is evidence the agent cannot lose. When an agent records the flag (caught-flag add) the matching capture is attached to it. A capture nobody claimed is a flag that may have gone unrecorded: look at it, then dismiss it or record the flag.'),
    d.captures.length ? h('div', {}, d.captures.map((c) => h('details', { class: 'card' },
      h('summary', {}, h('span', { class: 'mono' }, fmtDate(c.ts)), '  ', h('b', {}, c.tool.replace('mcp__higgsfield__', '')), '  ', c.summary.slice(0, 120), '  ',
        c.claimed_by ? h('span', { class: 'chip on' }, 'claimed by ' + c.claimed_by) : (c.dismissed ? h('span', { class: 'chip' }, 'dismissed') : h('span', { class: 'chip' }, 'unclaimed'))),
      h('dl', { class: 'kv' }, h('dt', {}, 'run'), h('dd', {}, c.run || '—'), h('dt', {}, 'job'), h('dd', { class: 'mono' }, c.job || '—'), h('dt', {}, 'agent'), h('dd', { class: 'mono' }, c.agent || '—')),
      h('pre', {}, JSON.stringify(c.payload?.response ?? c.payload, null, 1)?.slice(0, 6000) || ''),
      !c.claimed_by && !c.dismissed ? h('button', { class: 'btn sm', onclick: async () => { await api('POST', `/api/captures/${c.id}/dismiss`, {}); reload(); } }, 'Dismiss') : null)))
      : h('div', { class: 'card empty' }, 'No captures.'));
}

// ---------------------------------------------------------------------------------------- keys
export async function keys() {
  const d = await api('GET', '/api/keys');
  const name = h('input', { placeholder: 'name, e.g. qc-agents', size: 26 });
  const boxes = d.scopes.map((s) => h('label', { style: 'margin-right:12px' }, h('input', { type: 'checkbox', value: s }), ' ', s));
  const shown = h('div');
  const reload = () => keys().then((n) => document.querySelector('.main').replaceChildren(n));
  const create = async () => {
    const scopes = boxes.map((b) => b.querySelector('input')).filter((i) => i.checked).map((i) => i.value);
    try {
      const r = await api('POST', '/api/keys', { name: name.value, scopes });
      shown.replaceChildren(h('div', { class: 'card', style: 'border-color:var(--ok)' }, h('b', {}, 'Copy this key now. It is shown once and cannot be recovered.'),
        h('pre', {}, r.key), h('div', { class: 'row' }, h('button', { class: 'btn sm', onclick: () => copyText(r.key) }, 'Copy'),
          h('span', { class: 'muted small' }, 'The pipeline reads its keys from data/keys/<name>.key (python loupe.py key-create --file <name>) or the LOUPE_API_KEY variable.'))));
    } catch (_) { /* toasted */ }
  };
  const doc = h('pre', {}, `curl -H "Authorization: Bearer lk_..." http://127.0.0.1:8787/api/qc/samples?model=wan3_0_prime&kinds=counts
curl -H "Authorization: Bearer lk_..." -H "Content-Type: application/json" -d '{"question":"..."}' http://127.0.0.1:8787/api/qc/consult`);
  return h('div', {},
    head('API keys', 'What lets the pipeline and its agents call Loupe. Scopes keep each key to its job.'),
    h('div', { class: 'card' }, h('h2', {}, 'New key'), h('div', { class: 'row' }, name), h('div', { style: 'margin:10px 0' }, boxes),
      h('div', { class: 'muted small' }, 'flags:write / flags:read — record and read flags (the pipeline) · qc:read — the QC samples and the keeper (QC agents) · runs:read — browse runs and videos · admin — everything'),
      h('div', { style: 'margin-top:10px' }, h('button', { class: 'btn primary', onclick: create }, 'Create key')), shown),
    h('div', { class: 'card' }, h('h2', {}, 'Keys'),
      h('table', {}, h('thead', {}, h('tr', {}, ['Name', 'Prefix', 'Scopes', 'Last used', 'Uses', ''].map((x) => h('th', {}, x)))),
        h('tbody', {}, d.keys.map((k) => h('tr', {}, h('td', {}, k.name), h('td', { class: 'mono' }, k.prefix + '…'), h('td', {}, chips(k.scopes)), h('td', {}, fmtDate(k.last_used) || 'never'), h('td', {}, k.uses),
          h('td', {}, k.revoked ? h('span', { class: 'st archived' }, 'revoked') : h('button', { class: 'btn sm danger', onclick: async () => { if (confirm(`Revoke "${k.name}"? Agents using it lose access immediately.`)) { await api('POST', `/api/keys/${k.id}/revoke`, {}); reload(); } } }, 'Revoke'))))))),
    h('div', { class: 'card' }, h('h2', {}, 'Calling Loupe'), doc));
}

// ---------------------------------------------------------------------------------------- settings
export async function settings() {
  const [s, a] = await Promise.all([api('GET', '/api/settings'), api('GET', '/api/audit')]);
  const add = h('input', { placeholder: 'C:\\path\\to\\a\\folder with videos', size: 50 });
  const reload = () => settings().then((n) => document.querySelector('.main').replaceChildren(n));
  const save = async (roots) => { await api('POST', '/api/settings', { allowed_roots: roots }); toast('Saved'); reload(); };
  return h('div', {},
    head('Settings'),
    h('div', { class: 'card' }, h('h2', {}, 'Folders Loupe may read videos and images from'),
      h('p', { class: 'muted' }, 'Pipeline runs and Loupe\'s own data are always allowed. Add the folders where finished or client-fixed generations live.'),
      h('div', {}, s.default_roots.map((r) => h('div', { class: 'mono muted' }, r + '   (built in)'))),
      h('div', {}, s.allowed_roots.map((r) => h('div', { class: 'row' }, h('span', { class: 'mono' }, r), h('button', { class: 'btn sm danger', onclick: () => save(s.allowed_roots.filter((x) => x !== r)) }, 'Remove')))),
      h('div', { class: 'row', style: 'margin-top:10px' }, add, h('button', { class: 'btn', onclick: () => add.value && save([...s.allowed_roots, add.value]) }, 'Add folder'))),
    h('div', { class: 'card' }, h('h2', {}, 'Where things are'), h('dl', { class: 'kv' }, h('dt', {}, 'Pipeline library'), h('dd', { class: 'mono' }, s.atp), h('dt', {}, 'Loupe data'), h('dd', { class: 'mono' }, s.data))),
    h('div', { class: 'card' }, h('h2', {}, 'Audit log'),
      h('table', {}, h('thead', {}, h('tr', {}, ['When', 'Who', 'Action', 'Target', 'Detail'].map((x) => h('th', {}, x)))),
        h('tbody', {}, a.audit.slice(0, 80).map((r) => h('tr', {}, h('td', {}, fmtDate(r.ts)), h('td', {}, r.actor), h('td', {}, r.action), h('td', { class: 'mono' }, r.target), h('td', {}, (r.detail || '').slice(0, 100))))))));
}
