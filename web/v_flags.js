import { h, put, toast, fmtDate, chips, status, highlight, fmtBytes, chipPicker } from './ui.js';
import { api } from './api.js';

const head = (title, sub, ...actions) => h('div', { class: 'head' }, h('div', {}, h('h1', {}, title), sub ? h('div', { class: 'muted' }, sub) : null), h('div', { class: 'row' }, actions));
const STATUSES = ['open', 'cleared', 'persistent', 'archived'];
const GROUPS = { none: 'No grouping', tag: 'Trigger tag', model: 'Model', status: 'Status', kind: 'Kind', run: 'Run', asset: 'Suspect asset' };

function groupKeys(e, by) {
  const t = e.trigger || {}, w = e.where || {};
  switch (by) {
    case 'tag': return (t.tags && t.tags.length) ? t.tags : ['(no tag)'];
    case 'model': return [w.model || '(unknown)'];
    case 'status': return [e.status];
    case 'kind': return [e.kind || 'other'];
    case 'run': return [w.run || '(none)'];
    case 'asset': { const a = (e.inputs || []).filter((i) => i.suspect).map((i) => `${i.label} ${i.name}`.trim()); return a.length ? a : ['(no suspect asset)']; }
    default: return [''];
  }
}

export async function list() {
  const params = new URLSearchParams(location.hash.split('?')[1] || '');
  const search = h('input', { type: 'search', placeholder: 'Search words, or status:open tag:real-person-face model:wan run:… video:03 asset:…', value: params.get('q') || '', style: 'flex:1;min-width:280px' });
  const groupSel = h('select', {}, Object.entries(GROUPS).map(([k, v]) => h('option', { value: k, selected: k === (params.get('g') || 'tag') }, v)));
  const stat = await api('GET', '/api/flags-stats');
  const body = h('div');
  const strip = h('div', { class: 'card' });
  const load = async () => {
    const d = await api('GET', '/api/flags?limit=1000&q=' + encodeURIComponent(search.value));
    history.replaceState(null, '', '#/flags?q=' + encodeURIComponent(search.value) + '&g=' + groupSel.value);
    const by = groupSel.value;
    const groups = new Map();
    for (const e of d.entries) for (const k of groupKeys(e, by)) { if (!groups.has(k)) groups.set(k, []); groups.get(k).push(e); }
    const sorted = [...groups.entries()].sort((a, b) => b[1].length - a[1].length);
    put(body, d.entries.length ? sorted.map(([k, es]) => h('div', { class: 'card' },
      by !== 'none' ? h('h2', {}, k, ' ', h('span', { class: 'muted' }, `(${es.length})`)) : null,
      h('table', {}, h('thead', {}, h('tr', {}, ['', 'Status', 'Title', 'Trigger tags', 'Model', 'Run · video', 'When'].map((x) => h('th', {}, x)))),
        h('tbody', {}, es.map((e) => h('tr', { class: 'click', onclick: () => (location.hash = '#/flags/' + e.id) },
          h('td', { class: 'mono' }, e.id), h('td', {}, status(e.status)), h('td', {}, e.title), h('td', {}, chips((e.trigger || {}).tags)), h('td', {}, (e.where || {}).model || ''),
          h('td', { class: 'mono' }, `${((e.where || {}).run || '').slice(-12)} · ${(e.where || {}).video || ''}${(e.where || {}).attempt ? ' ' + e.where.attempt : ''}`), h('td', { class: 'small' }, fmtDate(e.created)))))))
    ) : h('div', { class: 'card empty' }, 'No flags match.'));
  };
  let timer;
  search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(load, 220); });
  groupSel.addEventListener('change', load);
  const tagPick = (t) => { search.value = (search.value + ' tag:' + t).trim(); load(); };
  strip.append(h('div', { class: 'row' }, h('b', {}, `${stat.total} flags`), ...Object.entries(stat.by_status).map(([k, v]) => h('button', { class: 'chip', onclick: () => { search.value = (search.value + ' status:' + k).trim(); load(); } }, `${k} ${v}`))),
    Object.keys(stat.tags).length ? h('div', { style: 'margin-top:6px' }, h('span', { class: 'muted small' }, 'Triggers: '), Object.entries(stat.tags).slice(0, 14).map(([k, v]) => h('button', { class: 'chip', onclick: () => tagPick(k) }, `${k} ${v}`))) : null,
    stat.suspect_assets.length ? h('div', { style: 'margin-top:6px' }, h('span', { class: 'muted small' }, 'Most-suspected inputs: '), stat.suspect_assets.slice(0, 6).map((a) => h('button', { class: 'chip', onclick: () => { search.value = (search.value + ' asset:' + (a.name || '')).trim(); load(); } }, `${a.name || a.sha1.slice(0, 8)} ×${a.flags}`))) : null);
  await load();
  return h('div', {}, head('Flags', 'Every prompt and video that Higgsfield flagged, refused, withheld or rerouted, with the evidence gathered when it happened'),
    strip, h('div', { class: 'row', style: 'margin:12px 0' }, search, groupSel, h('button', { class: 'btn', onclick: () => { search.value = ''; load(); } }, 'Clear')), body);
}

function promptView(e) {
  const span = ((e.trigger || {}).prompt_span || '').trim();
  const text = e.prompt || '';
  return h('pre', {}, ...(span ? highlight(text, span) : [text]));
}

function inputCard(i) {
  const f = i.facts || {};
  return h('div', { class: 'thumb' + (i.suspect ? ' sus' : '') },
    i.thumb_url ? h('img', { src: i.thumb_url, alt: i.label }) : h('div', { class: 'muted', style: 'height:130px;display:flex;align-items:center;justify-content:center;background:var(--code)' }, 'no preview'),
    h('div', {}, h('b', {}, i.label), i.suspect ? h('span', { class: 'chip on', style: 'margin-left:6px' }, 'suspect') : null),
    h('div', { class: 'muted' }, i.name || i.media_id || ''),
    h('div', { class: 'muted small' }, [i.role, f.width ? `${f.width}×${f.height}` : '', f.duration ? `${(+f.duration).toFixed(1)} s` : '', f.size ? fmtBytes(f.size) : ''].filter(Boolean).join(' · ')),
    f.sha1 ? h('div', { class: 'mono muted' }, 'sha1 ' + f.sha1.slice(0, 10)) : null,
    f.text_seen && f.text_seen.length ? h('div', { class: 'small' }, 'text on screen: ' + f.text_seen.slice(0, 6).join(' · ')) : null);
}

function evidenceView(e, refresh) {
  const ev = e.evidence || {};
  const t = ev.timing || {};
  const hs = ev.history || {};
  const d = ev.diff_to_cleared;
  return h('div', { class: 'card' },
    h('div', { class: 'row', style: 'justify-content:space-between' }, h('h2', { style: 'margin:0' }, 'Evidence'),
      h('button', { class: 'btn sm', onclick: refresh, title: 'Re-hash the input files, re-read their on-screen text and recompute everything' }, 'Refresh facts')),
    h('p', { class: 'muted small' }, 'Facts gathered by script when the flag was recorded: [E] means read off the run, the files or the history. They show where to look; the trigger above is the agent’s or your judgement.'),
    (ev.signals && ev.signals.length) ? h('ul', { class: 'sig' }, ev.signals.map((s) => h('li', {}, h('span', { class: 'e' }, s.basis), s.text))) : h('div', { class: 'muted' }, 'No signals yet.'),
    t.seconds_to_fail != null || t.submitted_at ? h('dl', { class: 'kv' }, h('dt', {}, 'Timing'), h('dd', {}, `submitted ${fmtDate(t.submitted_at)} → ${t.failed_at ? 'failed ' + fmtDate(t.failed_at) + (t.seconds_to_fail != null ? ` (${t.seconds_to_fail} s)` : '') : t.ledger_status}`)) : null,
    (hs.inputs && hs.inputs.length) ? h('div', {}, h('h3', {}, 'The same inputs elsewhere'),
      h('table', {}, h('thead', {}, h('tr', {}, ['Input', 'Matched by', 'Ran', 'Flagged', 'Failed (other)', 'Other flags', 'Runs'].map((x) => h('th', {}, x)))),
        h('tbody', {}, hs.inputs.map((i) => h('tr', {}, h('td', {}, `${i.label || ''} ${i.name}`), h('td', {}, i.matched_by), h('td', {}, i.jobs_ran), h('td', {}, i.jobs_flagged), h('td', {}, i.jobs_failed_other),
          h('td', { class: 'mono' }, (i.other_flags || []).map((f) => h('a', { href: '#/flags/' + f, style: 'margin-right:6px' }, f))), h('td', { class: 'small mono' }, (i.runs || []).map((r) => r.slice(-6)).join(' ')))))))
      : null,
    (hs.similar_flags && hs.similar_flags.length) ? h('div', {}, h('h3', {}, 'Similar flagged prompts'), hs.similar_flags.map((s) => h('div', {}, h('a', { href: '#/flags/' + s.id }, s.id), ` ${s.status} · similarity ${s.similarity}`, s.shared_inputs.length ? ` · shared: ${s.shared_inputs.join(', ')}` : ''))) : null,
    (hs.prompt_terms && hs.prompt_terms.terms && hs.prompt_terms.terms.length) ? h('div', {}, h('h3', {}, `Wording found in several flagged prompts and in no prompt that ran (${hs.prompt_terms.flagged_prompts} flagged, ${hs.prompt_terms.ran_prompts} ran)`),
      chips(hs.prompt_terms.terms.map((x) => `${x.term} ×${x.flagged_prompts}`))) : null,
    ev.prompt ? h('dl', { class: 'kv' }, h('dt', {}, 'Prompt facts'), h('dd', {}, `${ev.prompt.words} words, ${ev.prompt.sentences} sentences` + (ev.prompt.capitalised_terms?.length ? ` · capitalised terms: ${ev.prompt.capitalised_terms.slice(0, 12).join(', ')}` : '') + (ev.prompt.handles_or_urls?.length ? ` · handles/urls: ${ev.prompt.handles_or_urls.join(', ')}` : ''))) : null,
    d ? h('div', {}, h('h3', {}, 'What changed in the request that was accepted'),
      h('div', { class: 'diff' }, (d.sentences || []).map((l) => h('div', { class: l.startsWith('+') ? 'add' : 'del' }, l))),
      d.inputs_removed?.length ? h('div', {}, 'Inputs removed: ', chips(d.inputs_removed)) : null, d.inputs_added?.length ? h('div', {}, 'Inputs added: ', chips(d.inputs_added)) : null,
      d.words_removed?.length ? h('div', {}, 'Words removed: ', chips(d.words_removed.slice(0, 30))) : null,
      Object.keys(d.params_changed || {}).length ? h('pre', {}, JSON.stringify(d.params_changed, null, 1)) : null) : null,
    ev.reference_card ? h('details', {}, h('summary', {}, 'Reference card excerpt (from the run’s analysis)'), h('pre', {}, ev.reference_card)) : null);
}

export async function detail(id) {
  const { entry: e } = await api('GET', '/api/flags/' + id);
  const w = e.where || {}, t = e.trigger || {};
  const reload = () => detail(id).then((n) => document.querySelector('.main').replaceChildren(n));
  const st = h('select', {}, STATUSES.map((s) => h('option', { value: s, selected: s === e.status }, s)));
  const note = h('textarea', { placeholder: 'Add a note to this flag (what you found, what was tried)…' });
  const tagsPick = chipPicker(['real-person-face', 'celebrity-name', 'brand-name', 'protected-ip', 'nsfw-image', 'video-reference', 'element', 'prompt-wording', 'preset-notice', 'output-withheld', 'unknown-trigger'], t.tags || []);
  const trig = h('textarea', {}, t.text || '');
  const save = async () => {
    await api('PATCH', '/api/flags/' + id, { status: st.value, trigger: { text: trig.value, tags: tagsPick.get() }, note: note.value || undefined });
    toast('Saved'); reload();
  };
  return h('div', {},
    head(`${e.id} · ${e.title}`, `${fmtDate(e.created)}${e.imported_from ? ' · imported from the old flag page' : ''}`, h('a', { class: 'btn', href: '#/flags' }, '← All flags'),
      (w.run && w.video) ? h('a', { class: 'btn', href: `#/misses/work?run=${encodeURIComponent(w.run)}&video=${w.video}` }, 'Open this video') : null),
    h('div', { class: 'split' },
      h('div', {},
        h('div', { class: 'card' }, h('dl', { class: 'kv' }, h('dt', {}, 'Status'), h('dd', {}, status(e.status)), h('dt', {}, 'Kind'), h('dd', {}, e.kind), h('dt', {}, 'Run'), h('dd', { class: 'mono' }, w.run || ''),
          h('dt', {}, 'Video · attempt'), h('dd', {}, `${w.video || ''} ${w.attempt || ''} ${w.clip ? 'clip ' + w.clip : ''}`), h('dt', {}, 'Model · mode'), h('dd', {}, `${w.model || ''} · ${w.mode || ''}`),
          h('dt', {}, 'Tool · stage'), h('dd', {}, `${w.tool || ''} · ${w.stage || ''}`), h('dt', {}, 'Product'), h('dd', {}, w.product || ''), h('dt', {}, 'Job'), h('dd', { class: 'mono' }, e.job || ''),
          h('dt', {}, 'Credits'), h('dd', {}, e.credits || ''), h('dt', {}, 'Reference'), h('dd', { class: 'mono small' }, w.reference || ''))),
        h('div', { class: 'card' }, h('h2', {}, 'Higgsfield’s response (verbatim)'), h('pre', {}, e.response || '(none)')),
        h('div', { class: 'card' }, h('h2', {}, 'Suspected trigger ', h('span', { class: 'muted small' }, t.evidence ? `[${t.evidence}]` : '')),
          t.assets?.length ? h('div', {}, 'Inputs: ', chips(t.assets)) : null, t.prompt_span ? h('div', {}, 'Words: ', h('mark', {}, t.prompt_span)) : null,
          t.part ? h('div', {}, `Part: ${t.part.of || ''} ${t.part.from ?? ''}–${t.part.to ?? ''} ${t.part.note || ''}`, t.part.frame_url ? h('img', { src: t.part.frame_url, style: 'display:block;margin-top:6px;border-radius:6px;max-width:260px' }) : null) : null,
          t.cause ? h('div', { class: 'muted' }, 'Cause: ' + t.cause) : null,
          h('label', { class: 'f' }, 'Trigger (edit)'), trig, h('label', { class: 'f' }, 'Tags'), tagsPick.el),
        h('div', { class: 'card' }, h('h2', {}, 'Outcome'),
          e.changed ? h('div', {}, h('b', {}, 'Changed: '), e.changed) : null, e.outcome ? h('div', {}, h('b', {}, 'Outcome: '), e.outcome) : null,
          e.cleared?.job ? h('div', { class: 'mono' }, 'Accepted job ' + e.cleared.job) : null, e.rule ? h('div', {}, h('b', {}, 'Rule: '), e.rule) : null,
          e.promoted_to ? h('div', {}, h('b', {}, 'Promoted to: '), e.promoted_to) : null,
          (e.log || []).map((n) => h('div', { class: 'muted small' }, fmtDate(n.at), ' — ', n.note)),
          h('label', { class: 'f' }, 'Status'), st, h('label', { class: 'f' }, 'Note'), note,
          h('div', { class: 'row', style: 'margin-top:10px' }, h('button', { class: 'btn primary', onclick: save }, 'Save'),
            h('button', { class: 'btn danger', onclick: async () => { if (confirm(`Delete ${e.id}? This cannot be undone.`)) { await api('DELETE', '/api/flags/' + id); location.hash = '#/flags'; } } }, 'Delete')))),
      h('div', {},
        h('div', { class: 'card' }, h('h2', {}, 'Inputs of the flagged request'), (e.inputs || []).length ? h('div', { class: 'thumbs' }, e.inputs.map(inputCard)) : h('div', { class: 'muted' }, 'none recorded')),
        evidenceView(e, async () => { toast('Recomputing…'); await api('POST', `/api/flags/${id}/refresh`, {}); reload(); }),
        h('div', { class: 'card' }, h('h2', {}, 'Prompt', t.prompt_span ? h('span', { class: 'muted small' }, '  (suspect words marked)') : null), promptView(e),
          e.params && Object.keys(e.params).length ? h('pre', {}, Object.entries(e.params).map(([k, v]) => `${k}: ${v}`).join('\n')) : null,
          e.cleared?.prompt ? h('details', {}, h('summary', {}, 'The accepted prompt'), h('pre', {}, e.cleared.prompt)) : null))));
}
