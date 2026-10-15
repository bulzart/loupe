import { h, put, toast, fmtDate, chips, highlight, fmtBytes, chipPicker, copyText } from './ui.js';
import { api } from './api.js';
import { STATUS, KIND, TAG, STAGE, statusInfo, kindInfo, tagInfo, stageInfo, ago, clip, whereLine, sentence } from './flag_words.js';

const head = (title, sub, ...actions) => h('div', { class: 'head' }, h('div', {}, h('h1', {}, title), sub ? h('div', { class: 'muted' }, sub) : null), h('div', { class: 'row' }, actions));
const STATUSES = ['open', 'cleared', 'persistent', 'archived'];
const GROUPS = { none: 'Nothing (newest first)', tag: 'Suspected cause', kind: 'What happened', status: 'Status', model: 'Model', run: 'Run', asset: 'Suspect input' };

// ---- small pieces that say things in plain words (the raw value is always in the tooltip)
const statusBadge = (s) => { const i = statusInfo(s); return h('span', { class: 'st ' + (s || ''), title: `${s}: ${i.hint}` }, i.label); };
const kindChip = (k) => { const i = kindInfo(k); return h('span', { class: 'chip kind', title: `${k}: ${i.hint}` }, i.label); };
const causeChips = (tags, onClick) => (tags && tags.length ? tags : ['unknown-trigger']).map((t) => {
  const i = tagInfo(t);
  return h(onClick ? 'button' : 'span', { class: 'chip', title: `${t}: ${i.hint}`, onclick: onClick ? (ev) => { ev.stopPropagation(); onClick(t); } : null }, i.label);
});

function groupKeys(e, by) {
  const t = e.trigger || {}, w = e.where || {};
  switch (by) {
    case 'tag': return (t.tags && t.tags.length) ? t.tags : ['unknown-trigger'];
    case 'model': return [w.model || '(unknown)'];
    case 'status': return [e.status];
    case 'kind': return [e.kind || 'other'];
    case 'run': return [w.run || '(none)'];
    case 'asset': { const a = (e.inputs || []).filter((i) => i.suspect).map((i) => `${i.label} ${i.name}`.trim()); return a.length ? a : ['(no suspect input)']; }
    default: return [''];
  }
}
const groupTitle = (by, k) => (by === 'tag' ? tagInfo(k).label : by === 'kind' ? kindInfo(k).label : by === 'status' ? statusInfo(k).label : k);

// ------------------------------------------------------------------------------------------ the list
export async function list() {
  const params = new URLSearchParams(location.hash.split('?')[1] || '');
  const search = h('input', { type: 'search', placeholder: 'Search words, or filter: status:open  kind:refused-no-reason  tag:video-reference  model:wan  video:03  asset:…', value: params.get('q') || '', style: 'flex:1;min-width:280px' });
  const groupSel = h('select', { title: 'Group the flags below' }, Object.entries(GROUPS).map(([k, v]) => h('option', { value: k, selected: k === (params.get('g') || 'none') }, v)));
  const stat = await api('GET', '/api/flags-stats');
  const body = h('div');
  const strip = h('div', { class: 'card' });
  const filterBy = (op, v) => { search.value = (search.value + ` ${op}:${v}`).trim(); load(); };

  const card = (e) => {
    const w = e.where || {};
    const fixed = e.changed ? h('div', { class: 'fmore' }, h('b', {}, 'What fixed it: '), clip(e.changed, 190))
      : (e.response ? h('div', { class: 'fmore' }, h('b', {}, 'Higgsfield said: '), clip(e.response, 190)) : null);
    return h('div', { class: 'fcard', role: 'link', tabindex: '0', onclick: () => (location.hash = '#/flags/' + e.id), onkeydown: (ev) => { if (ev.key === 'Enter') location.hash = '#/flags/' + e.id; } },
      h('div', { class: 'frow' }, h('span', { class: 'mono fid' }, e.id), statusBadge(e.status), kindChip(e.kind),
        w.stage ? h('span', { class: 'muted small', title: stageInfo(w.stage).hint }, stageInfo(w.stage).label) : null,
        h('span', { class: 'muted small fwhen', title: fmtDate(e.created) }, ago(e.created))),
      h('div', { class: 'ftitle' }, e.title),
      h('div', { class: 'muted small', title: w.run || '' }, whereLine(e) || 'no video recorded', w.run ? ` · run …${w.run.slice(-12)}` : ''),
      fixed,
      h('div', { class: 'small fcause' }, h('span', { class: 'muted' }, 'Suspected cause: '), ...causeChips((e.trigger || {}).tags)));
  };

  const load = async () => {
    const d = await api('GET', '/api/flags?limit=1000&q=' + encodeURIComponent(search.value));
    history.replaceState(null, '', '#/flags?q=' + encodeURIComponent(search.value) + '&g=' + groupSel.value);
    const by = groupSel.value;
    const groups = new Map();
    for (const e of d.entries) for (const k of groupKeys(e, by)) { if (!groups.has(k)) groups.set(k, []); groups.get(k).push(e); }
    const sorted = by === 'none' ? [...groups.entries()] : [...groups.entries()].sort((a, b) => b[1].length - a[1].length);
    put(body, d.entries.length ? sorted.map(([k, es]) => h('div', { class: by === 'none' ? '' : 'fgroup' },
      by !== 'none' ? h('h2', { title: k }, groupTitle(by, k), ' ', h('span', { class: 'muted' }, `(${es.length})`)) : null, es.map(card)))
      : h('div', { class: 'card empty' }, 'No flags match. Clear the search to see them all.'));
  };
  let timer;
  search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(load, 220); });
  groupSel.addEventListener('change', load);

  strip.append(h('div', { class: 'row' }, h('b', {}, `${stat.total} flag${stat.total === 1 ? '' : 's'}`), h('span', { class: 'muted small' }, 'click a count to filter'),
    ...Object.entries(stat.by_status).map(([k, v]) => h('button', { class: 'chip', title: statusInfo(k).hint, onclick: () => filterBy('status', k) }, `${statusInfo(k).label} ${v}`))),
  Object.keys(stat.by_kind || {}).length ? h('div', { style: 'margin-top:6px' }, h('span', { class: 'muted small' }, 'What happened: '),
    Object.entries(stat.by_kind).map(([k, v]) => h('button', { class: 'chip', title: `${k}: ${kindInfo(k).hint}`, onclick: () => filterBy('kind', k) }, `${kindInfo(k).label} ${v}`))) : null,
  Object.keys(stat.tags).length ? h('div', { style: 'margin-top:6px' }, h('span', { class: 'muted small' }, 'Suspected causes: '),
    Object.entries(stat.tags).slice(0, 14).map(([k, v]) => h('button', { class: 'chip', title: `${k}: ${tagInfo(k).hint}`, onclick: () => filterBy('tag', k) }, `${tagInfo(k).label} ${v}`))) : null,
  stat.suspect_assets.length ? h('div', { style: 'margin-top:6px' }, h('span', { class: 'muted small' }, 'Most-suspected inputs: '),
    stat.suspect_assets.slice(0, 6).map((a) => h('button', { class: 'chip', title: a.name, onclick: () => filterBy('asset', a.name) }, `${clip(a.name, 48)} ×${a.flags}`))) : null);

  let open = true;
  try { open = localStorage.getItem('loupe-flags-help') !== 'closed'; } catch (_) { /* private mode */ }
  const help = h('details', { class: 'card fhelp', open }, h('summary', {}, h('b', {}, 'What is a flag?'), h('span', { class: 'muted small' }, '  how to read this page')),
    h('p', {}, 'A flag is recorded every time Higgsfield refuses, blocks, withholds or silently fails a generation. It keeps what was sent, what Higgsfield said, what is suspected to have caused it, and what finally worked, so the same problem is not repeated. Click a flag to see all of it.'),
    h('div', { class: 'legend' }, STATUSES.map((s) => h('div', {}, statusBadge(s), h('div', { class: 'muted small' }, STATUS[s][1])))));
  help.addEventListener('toggle', () => { try { localStorage.setItem('loupe-flags-help', help.open ? 'open' : 'closed'); } catch (_) { /* private mode */ } });

  await load();
  return h('div', {}, head('Flags', 'Every time Higgsfield refused, blocked, withheld or silently failed a generation, with what was known at that moment'),
    help, strip, h('div', { class: 'row', style: 'margin:12px 0' }, search, h('span', { class: 'muted small' }, 'Group by'), groupSel, h('button', { class: 'btn', onclick: () => { search.value = ''; load(); } }, 'Clear')), body);
}

// ------------------------------------------------------------------------------------------ one flag
function promptView(e) {
  const span = ((e.trigger || {}).prompt_span || '').trim();
  const text = e.prompt || '';
  return h('pre', {}, ...(span ? highlight(text, span) : [text]));
}

function inputCard(i) {
  const f = i.facts || {};
  return h('div', { class: 'thumb' + (i.suspect ? ' sus' : '') },
    i.thumb_url ? h('img', { src: i.thumb_url, alt: i.label }) : h('div', { class: 'muted', style: 'height:130px;display:flex;align-items:center;justify-content:center;background:var(--code)' }, 'no preview'),
    h('div', {}, h('b', {}, i.label), i.suspect ? h('span', { class: 'chip on', style: 'margin-left:6px', title: 'The agent or you suspect this input of causing the flag' }, 'suspected') : null),
    h('div', { class: 'muted' }, i.name || i.media_id || ''),
    h('div', { class: 'muted small' }, [i.role, f.width ? `${f.width}×${f.height}` : '', f.duration ? `${(+f.duration).toFixed(1)} s` : '', f.size ? fmtBytes(f.size) : ''].filter(Boolean).join(' · ')),
    f.sha1 ? h('div', { class: 'mono muted', title: 'a fingerprint of the file: the same file elsewhere has the same one' }, 'fingerprint ' + f.sha1.slice(0, 10)) : null,
    f.text_seen && f.text_seen.length ? h('div', { class: 'small' }, 'text on screen: ' + f.text_seen.slice(0, 6).join(' · ')) : null);
}

function evidenceView(e, refresh) {
  const ev = e.evidence || {};
  const t = ev.timing || {};
  const hs = ev.history || {};
  const d = ev.diff_to_cleared;
  return h('div', { class: 'card' },
    h('div', { class: 'row', style: 'justify-content:space-between' }, h('h2', { style: 'margin:0' }, 'What the files and history show'),
      h('button', { class: 'btn sm', onclick: refresh, title: 'Re-read the input files and the history, and recompute everything below' }, 'Refresh')),
    h('p', { class: 'muted small' }, 'Facts a script gathered when the flag was recorded, read off the run, the files and the history. They show where to look; the suspected cause above is a judgement.'),
    (ev.signals && ev.signals.length) ? h('ul', { class: 'sig' }, ev.signals.map((s) => h('li', {}, h('span', { class: 'e', title: s.basis === 'E' ? 'read off the run, the files or the history' : 'a judgement' }, s.basis === 'E' ? 'checked' : 'judgement'), s.text))) : h('div', { class: 'muted' }, 'Nothing noted yet.'),
    t.seconds_to_fail != null || t.submitted_at ? h('dl', { class: 'kv' }, h('dt', {}, 'Timing'), h('dd', {}, `submitted ${fmtDate(t.submitted_at)} → ${t.failed_at ? 'failed ' + fmtDate(t.failed_at) + (t.seconds_to_fail != null ? ` (${t.seconds_to_fail} s later)` : '') : 'no failure time recorded'}`)) : null,
    (hs.inputs && hs.inputs.length) ? h('div', {}, h('h3', {}, 'Do these inputs work elsewhere?'),
      h('p', { class: 'muted small' }, 'An input that ran fine in other jobs is unlikely to be the cause on its own.'),
      h('table', {}, h('thead', {}, h('tr', {}, ['Input', 'Found by', 'Ran in', 'Flagged in', 'Failed otherwise in', 'Other flags', 'Runs'].map((x) => h('th', {}, x)))),
        h('tbody', {}, hs.inputs.map((i) => h('tr', {}, h('td', {}, `${i.label || ''} ${i.name}`), h('td', {}, i.matched_by), h('td', {}, i.jobs_ran), h('td', {}, i.jobs_flagged), h('td', {}, i.jobs_failed_other),
          h('td', { class: 'mono' }, (i.other_flags || []).map((f) => h('a', { href: '#/flags/' + f, style: 'margin-right:6px' }, f))), h('td', { class: 'small mono' }, (i.runs || []).map((r) => r.slice(-6)).join(' ')))))))
      : null,
    (hs.similar_flags && hs.similar_flags.length) ? h('div', {}, h('h3', {}, 'Similar flagged prompts'), hs.similar_flags.map((s) => h('div', {}, h('a', { href: '#/flags/' + s.id }, s.id), ` ${statusInfo(s.status).label} · similarity ${s.similarity}`, s.shared_inputs?.length ? ` · same inputs: ${s.shared_inputs.join(', ')}` : ''))) : null,
    (hs.prompt_terms && hs.prompt_terms.terms && hs.prompt_terms.terms.length) ? h('div', {}, h('h3', {}, `Wording found in several flagged prompts and in no prompt that ran (${hs.prompt_terms.flagged_prompts} flagged, ${hs.prompt_terms.ran_prompts} that ran)`),
      chips(hs.prompt_terms.terms.map((x) => `${x.term} ×${x.flagged_prompts}`))) : null,
    ev.prompt ? h('dl', { class: 'kv' }, h('dt', {}, 'The prompt'), h('dd', {}, `${ev.prompt.words} words, ${ev.prompt.sentences} sentences` + (ev.prompt.capitalised_terms?.length ? ` · capitalised terms: ${ev.prompt.capitalised_terms.slice(0, 12).join(', ')}` : ''))) : null,
    d ? h('div', {}, h('h3', {}, 'The request that was accepted, compared with the flagged one'),
      h('p', { class: 'muted small' }, '+ was added, − was removed.'),
      h('div', { class: 'diff' }, (d.sentences || []).map((l) => h('div', { class: l.startsWith('+') ? 'add' : 'del' }, l))),
      d.inputs_removed?.length ? h('div', {}, 'Inputs removed: ', chips(d.inputs_removed)) : null, d.inputs_added?.length ? h('div', {}, 'Inputs added: ', chips(d.inputs_added)) : null,
      d.words_removed?.length ? h('div', {}, 'Words removed: ', chips(d.words_removed.slice(0, 30))) : null,
      Object.keys(d.params_changed || {}).length ? h('pre', {}, JSON.stringify(d.params_changed, null, 1)) : null) : null,
    ev.reference_card ? h('details', {}, h('summary', {}, 'Excerpt of the reference’s analysis card'), h('pre', {}, ev.reference_card)) : null);
}

export async function detail(id) {
  const { entry: e } = await api('GET', '/api/flags/' + id);
  const w = e.where || {}, t = e.trigger || {};
  const reload = () => detail(id).then((n) => document.querySelector('.main').replaceChildren(n));
  const st = h('select', {}, STATUSES.map((s) => h('option', { value: s, selected: s === e.status }, `${statusInfo(s).label}: ${statusInfo(s).hint}`)));
  st.value = e.status;
  const note = h('textarea', { placeholder: 'A note for this flag: what you found, what was tried…' });
  const tagsPick = chipPicker(Object.keys(TAG), t.tags || [], undefined, (o) => ({ label: tagInfo(o).label, title: `${o}: ${tagInfo(o).hint}` }));
  const trig = h('textarea', { placeholder: 'What you think caused it, in a sentence' }, t.text || '');
  const save = async () => {
    await api('PATCH', '/api/flags/' + id, { status: st.value, trigger: { text: trig.value, tags: tagsPick.get() }, note: note.value || undefined });
    toast('Saved'); reload();
  };
  const kv = (rows) => h('dl', { class: 'kv' }, rows.filter(([, v]) => v !== '' && v != null && v !== false).flatMap(([k, v, cls]) => [h('dt', {}, k), h('dd', { class: cls || '' }, v)]));
  const certainty = t.evidence === 'E' ? 'Confirmed by evidence' : (t.text || (t.tags || []).length ? 'Our judgement, not proven' : 'Not known yet');
  const fixedBy = e.changed || e.outcome || '';

  const inShort = h('div', { class: 'card' },
    h('div', { class: 'row' }, statusBadge(e.status), kindChip(e.kind), h('span', { class: 'muted' }, statusInfo(e.status).hint)),
    h('p', { class: 'flead' }, sentence(e)),
    h('div', { class: 'brief3' },
      h('div', {}, h('h3', {}, 'What happened'), h('div', {}, e.response ? clip(e.response, 260) : 'Higgsfield gave no message.')),
      h('div', {}, h('h3', {}, 'Suspected cause'), h('div', {}, ...causeChips(t.tags)), t.text || t.cause ? h('div', { class: 'small', style: 'margin-top:4px' }, clip(t.text || t.cause, 240)) : null, h('div', { class: 'muted small', style: 'margin-top:4px' }, certainty)),
      h('div', {}, h('h3', {}, 'What fixed it'), h('div', {}, fixedBy ? clip(fixedBy, 300) : 'Nothing recorded yet.'))));

  const whatHappened = h('div', { class: 'card' }, h('h2', {}, 'What happened'),
    kv([['When', `${fmtDate(e.created)} (${ago(e.created)})`], ['Where in the pipeline', w.stage ? stageInfo(w.stage).label : ''], ['Video', w.video ? `${w.video}${w.attempt ? ' · attempt ' + w.attempt : ''}${w.clip ? ' · clip ' + w.clip : ''}` : ''],
      ['Model · mode', [w.model, w.mode].filter(Boolean).join(' · ')], ['Product', w.product], ['Credits', e.credits], ['Run', w.run, 'mono small']]),
    h('h3', {}, 'What Higgsfield said (word for word)'), h('pre', {}, e.response || '(nothing)'));

  const cause = h('div', { class: 'card' }, h('h2', {}, 'Suspected cause ', h('span', { class: 'muted small' }, certainty)),
    h('div', {}, ...causeChips(t.tags)),
    t.text ? h('p', {}, t.text) : null,
    t.assets?.length ? h('div', {}, 'Inputs suspected: ', chips(t.assets)) : null, t.prompt_span ? h('div', {}, 'Words suspected: ', h('mark', {}, t.prompt_span)) : null,
    t.part ? h('div', {}, `Part of the video: ${t.part.of || ''} ${t.part.from ?? ''}–${t.part.to ?? ''} ${t.part.note || ''}`, t.part.frame_url ? h('img', { src: t.part.frame_url, style: 'display:block;margin-top:6px;border-radius:6px;max-width:260px' }) : null) : null,
    t.cause ? h('div', { class: 'muted' }, 'Cause: ' + t.cause) : null,
    !t.text && !t.cause && !(t.tags || []).length ? h('div', { class: 'muted' }, 'Not known yet. Add what you think under “Update this flag”.') : null);

  const outcome = h('div', { class: 'card' }, h('h2', {}, 'What fixed it'),
    e.changed ? h('div', {}, h('b', {}, 'What was changed: '), e.changed) : null, e.outcome ? h('div', {}, h('b', {}, 'Result: '), e.outcome) : null,
    e.cleared?.job ? h('div', {}, h('b', {}, 'Accepted job: '), h('span', { class: 'mono' }, e.cleared.job)) : null, e.rule ? h('div', {}, h('b', {}, 'Rule learned: '), e.rule) : null,
    e.promoted_to ? h('div', {}, h('b', {}, 'Added to the pipeline as: '), e.promoted_to) : null,
    !e.changed && !e.outcome && !e.rule ? h('div', { class: 'muted' }, 'Nothing recorded yet.') : null,
    (e.log || []).length ? h('div', { style: 'margin-top:8px' }, h('h3', {}, 'Notes'), e.log.map((n) => h('div', { class: 'muted small' }, fmtDate(n.at), ' — ', n.note))) : null);

  const update = h('div', { class: 'card' }, h('h2', {}, 'Update this flag'),
    h('label', { class: 'f' }, 'Status'), st, h('label', { class: 'f' }, 'What you think caused it'), trig, h('label', { class: 'f' }, 'Cause tags'), tagsPick.el, h('label', { class: 'f' }, 'Add a note'), note,
    h('div', { class: 'row', style: 'margin-top:10px' }, h('button', { class: 'btn primary', onclick: save }, 'Save'),
      h('button', { class: 'btn danger', onclick: async () => { if (confirm(`Delete ${e.id}? This cannot be undone.`)) { await api('DELETE', '/api/flags/' + id); location.hash = '#/flags'; } } }, 'Delete')));

  const sent = h('div', { class: 'card' }, h('h2', {}, 'What was sent'),
    h('h3', {}, 'Inputs'), (e.inputs || []).length ? h('div', { class: 'thumbs' }, e.inputs.map(inputCard)) : h('div', { class: 'muted' }, 'none recorded'),
    h('h3', {}, 'Prompt', t.prompt_span ? h('span', { class: 'muted small' }, '  (suspected words marked)') : null), promptView(e),
    e.params && Object.keys(e.params).length ? h('div', {}, h('h3', {}, 'Settings sent'), h('pre', {}, Object.entries(e.params).map(([k, v]) => `${k}: ${v}`).join('\n'))) : null,
    e.cleared?.prompt ? h('details', {}, h('summary', {}, 'The prompt that was accepted'), h('pre', {}, e.cleared.prompt)) : null);

  const technical = h('details', { class: 'card' }, h('summary', {}, h('b', {}, 'Technical details'), h('span', { class: 'muted small' }, '  ids, files and the whole record, word for word')),
    kv([['Flag', e.id, 'mono'], ['Kind (raw)', e.kind, 'mono'], ['Status (raw)', e.status, 'mono'], ['Tool · stage', [w.tool, w.stage].filter(Boolean).join(' · '), 'mono'], ['Job', e.job, 'mono'], ['Fingerprint', e.fingerprint, 'mono'],
      ['Reference', w.reference, 'mono small'], ['Segment', w.segment, 'mono small'], ['Plan file', w.plan, 'mono small'], ['Brief file', w.brief, 'mono small'], ['Request file', w.request, 'mono small'],
      ['Recorded by', e.source], ['Imported from', e.imported_from], ['Last changed', fmtDate(e.updated)]]),
    h('div', { class: 'row', style: 'margin:8px 0' }, h('button', { class: 'btn sm', onclick: () => copyText(JSON.stringify(e, null, 2)) }, 'Copy the whole record')),
    h('details', {}, h('summary', {}, 'Everything recorded (raw JSON)'), h('pre', {}, JSON.stringify(e, null, 1))));

  return h('div', {},
    head(`${e.id} · ${e.title}`, `${fmtDate(e.created)} · ${ago(e.created)}${e.imported_from ? ' · imported from the old flag page' : ''}`, h('a', { class: 'btn', href: '#/flags' }, '← All flags'),
      (w.run && w.video) ? h('a', { class: 'btn', href: `#/misses/work?run=${encodeURIComponent(w.run)}&video=${w.video}` }, 'Open this video') : null),
    inShort,
    h('div', { class: 'split', style: 'margin-top:14px' },
      h('div', {}, whatHappened, cause, outcome, update),
      h('div', {}, sent, evidenceView(e, async () => { toast('Recomputing…'); await api('POST', `/api/flags/${id}/refresh`, {}); reload(); }))),
    h('div', { style: 'margin-top:14px' }, technical));
}
