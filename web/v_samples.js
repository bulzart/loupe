import { h, toast, fmtDate, chips, status, fmtT, chipPicker } from './ui.js';
import { api } from './api.js';

export async function gallery() {
  const d = await api('GET', '/api/misses');
  const all = d.misses.flatMap((m) => m.moments.map((s) => ({ ...s, miss: m })));
  const search = h('input', { type: 'search', placeholder: 'Filter by words, kind or model…', style: 'min-width:280px' });
  const showArchived = h('input', { type: 'checkbox' });
  const grid = h('div', { class: 'grid g3' });
  const draw = () => {
    const w = search.value.toLowerCase().split(/\s+/).filter(Boolean);
    const rows = all.filter((s) => (showArchived.checked || (s.status === 'active' && s.miss_status === 'active')) &&
      w.every((x) => (s.title + ' ' + s.explanation + ' ' + s.details + ' ' + s.correct_note + ' ' + (s.idea || '') + ' ' + (s.better || '') + ' ' + (s.missed || '') + ' ' + s.kinds.join(' ') + ' ' + s.model + ' ' + s.run + ' ' + s.id).toLowerCase().includes(x)));
    grid.replaceChildren(...(rows.length ? rows.map(card) : [h('div', { class: 'card empty', style: 'grid-column:1/-1' }, 'No sample matches.')]));
  };
  const reload = () => gallery().then((n) => document.querySelector('.main').replaceChildren(n));
  const card = (s) => h('div', { class: 'card sample-card', onclick: () => open(s) },
    h('img', { src: s.image_url + '&v=' + encodeURIComponent(s.updated), alt: s.title, loading: 'lazy' }),
    h('div', { style: 'margin-top:8px' }, h('b', {}, s.id), ' ', s.title, ' ', s.status !== 'active' || s.miss_status !== 'active' ? status('archived') : null),
    h('div', { class: 'small muted' }, `${s.model || 'any model'} · ${s.run ? s.run.slice(-14) : ''} video ${s.video || '-'} · ${(s.frames || []).length ? s.frames.map((f) => (f.role === 'right' ? 'right ' : f.role === 'wrong' ? 'wrong ' : 'example ') + fmtT(f.t)).join(' / ') : 'words only'}`), h('div', {}, s.sample_kind === 'miss-only' ? h('span', { class: 'chip' }, 'miss example only') : (s.sample_kind === 'scenario' ? h('span', { class: 'chip' }, 'scenario') : null), chips(s.kinds)));
  const open = (s) => {
    const details = h('textarea', {}, s.details), correct = h('textarea', {}, s.correct_note), idea = h('textarea', {}, s.idea || ''), better = h('textarea', {}, s.better || ''), missed = h('textarea', {}, s.missed || '');
    const kp = chipPicker(['counts', 'openings-use', 'permanence', 'scale-size', 'label-orientation', 'solidity', 'blend-paste', 'posture-place', 'identity', 'continuity', 'other'], s.kinds);
    const ov = h('div', { class: 'overlay' }, h('div', { class: 'modal' },
      h('div', { class: 'head' }, h('div', {}, h('h1', {}, `${s.id} · ${s.title}`), h('div', { class: 'muted' }, `${s.model || 'any model'} · ${s.mode || ''} · ${s.product || ''}`)),
        h('div', { class: 'row' }, h('a', { class: 'btn', href: `#/misses/work?run=${encodeURIComponent(s.run)}&video=${s.video}&miss=${s.miss_id}`, onclick: () => ov.remove() }, 'Open the report'),
          h('button', { class: 'btn', onclick: () => ov.remove() }, 'Close'))),
      h('div', { class: 'split' }, h('img', { src: s.image_url + '&v=' + encodeURIComponent(s.updated), style: 'width:100%;border-radius:10px;border:1px solid var(--line)' }),
        h('div', {}, s.explanation ? h('p', {}, h('b', {}, 'The miss: '), s.explanation) : null,
          h('label', { class: 'f' }, 'What is wrong here'), details, h('label', { class: 'f' }, 'What the right frame shows'), correct,
          h('label', { class: 'f' }, 'The idea'), idea, h('label', { class: 'f' }, 'What could have been better'), better, h('label', { class: 'f' }, 'What the AI agents missed'), missed, h('label', { class: 'f' }, 'Kinds'), kp.el,
          h('div', { class: 'row', style: 'margin-top:12px' },
            h('button', { class: 'btn primary', onclick: async () => { await api('PATCH', '/api/moments/' + s.moment_id, { details: details.value, correct_note: correct.value, idea: idea.value, better: better.value, missed: missed.value, kinds: kp.get() }); toast('Saved and the image was rebuilt'); ov.remove(); reload(); } }, 'Save'),
            h('button', { class: 'btn', onclick: async () => { await api('PATCH', '/api/moments/' + s.moment_id, { status: s.status === 'active' ? 'archived' : 'active' }); ov.remove(); reload(); } }, s.status === 'active' ? 'Archive (hide from QC)' : 'Restore'),
            h('button', { class: 'btn danger', onclick: async () => { if (confirm('Delete this sample?')) { await api('DELETE', '/api/moments/' + s.moment_id); ov.remove(); reload(); } } }, 'Delete')),
          h('p', { class: 'muted small' }, `Created ${fmtDate(s.created)}. The QC phase sees the sample only while it is active.`)))));
    document.body.append(ov);
  };
  search.addEventListener('input', draw); showArchived.addEventListener('change', draw);
  draw();
  return h('div', {},
    h('div', { class: 'head' }, h('div', {}, h('h1', {}, 'QC samples'), h('div', { class: 'muted' }, `${all.filter((s) => s.status === 'active' && s.miss_status === 'active').length} active · what the QC agents and the keeper read`)),
      h('div', { class: 'row' }, search, h('label', { class: 'small' }, showArchived, ' show archived'))), grid);
}
