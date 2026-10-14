import { h, toast, fmtDate, chips, status, fmtT, chipPicker, confirmBox } from './ui.js';
import { api, upload, videoUrl, frameUrl } from './api.js';

const head = (title, sub, ...actions) => h('div', { class: 'head' }, h('div', {}, h('h1', {}, title), sub ? h('div', { class: 'muted' }, sub) : null), h('div', { class: 'row' }, actions));
const FPS = 30;

// ------------------------------------------------------------------------------------------ list of reports
export async function reports() {
  const d = await api('GET', '/api/misses');
  const reload = () => reports().then((n) => document.querySelector('.main').replaceChildren(n));
  return h('div', {},
    head('Generation misses', 'Pick a run, open a video, say what went wrong, mark the moment. The corrected version is found at the same moment and both go into one sample image for the QC phase.',
      h('a', { class: 'btn primary', href: '#/misses/new' }, 'New report')),
    d.misses.length ? d.misses.map((m) => h('div', { class: 'card' },
      h('div', { class: 'row', style: 'justify-content:space-between' },
        h('div', {}, h('b', {}, m.title), ' ', status(m.status), h('div', { class: 'muted small' }, `${m.run || 'no run'} · video ${m.video || '-'} · ${m.model || 'any model'} · ${fmtDate(m.created)}`)),
        h('div', { class: 'row' }, h('a', { class: 'btn sm', href: `#/misses/work?run=${encodeURIComponent(m.run)}&video=${m.video}&miss=${m.id}` }, 'Open'),
          h('button', { class: 'btn sm', onclick: async () => { await api('PATCH', '/api/misses/' + m.id, { status: m.status === 'active' ? 'archived' : 'active' }); reload(); } }, m.status === 'active' ? 'Archive' : 'Restore'),
          h('button', { class: 'btn sm danger', onclick: async () => { if (confirmBox(`Delete the report “${m.title}” and its ${m.moments.length} sample(s)?`)) { await api('DELETE', '/api/misses/' + m.id); reload(); } } }, 'Delete'))),
      m.explanation ? h('p', {}, m.explanation) : null, h('div', {}, chips(m.kinds)),
      m.moments.length ? h('div', { class: 'grid g4', style: 'margin-top:8px' }, m.moments.map((s) => h('a', { href: '#/samples', title: s.details }, h('img', { src: s.image_url, style: 'width:100%;height:170px;object-fit:cover;object-position:top;border-radius:8px' })))) : h('div', { class: 'muted small' }, 'No moment marked yet.')))
      : h('div', { class: 'card empty' }, 'No report yet. Start one with “New report”.'));
}

// ------------------------------------------------------------------------------------------ run -> video picker
export async function pick() {
  const d = await api('GET', '/api/runs');
  const real = d.runs.filter((r) => r.kind === 'run');
  const out = h('div');
  const sel = h('select', { style: 'min-width:360px' }, h('option', { value: '' }, 'Select a run…'),
    real.map((r) => h('option', { value: r.run }, `${r.run} · ${r.mode || ''}${r.submode ? '/' + r.submode : ''} · ${r.model || ''} · ${r.videos} videos`)));
  sel.addEventListener('change', async () => {
    if (!sel.value) { out.replaceChildren(); return; }
    const r = await api('GET', '/api/runs/' + encodeURIComponent(sel.value));
    out.replaceChildren(h('div', { class: 'grid g4' }, r.videos.map((v) => h('a', { class: 'card', style: 'color:inherit;text-decoration:none', href: `#/misses/work?run=${encodeURIComponent(r.run)}&video=${v.video}` },
      h('b', {}, 'Video ' + v.video), ' ', v.status ? status(v.status) : null,
      h('div', { class: 'muted small' }, `${v.versions.length} version(s)` + (v.capcut_project ? ` · ${v.capcut_project}` : '') + (v.credits ? ` · ${v.credits} cr` : '')),
      v.qc ? h('div', { class: 'small', style: 'margin-top:4px' }, v.qc.slice(0, 120)) : null))));
  });
  return h('div', {}, head('New report', 'Choose the run that was executed by the pipeline, then the video that had the miss', h('a', { class: 'btn', href: '#/misses' }, '← Reports')),
    h('div', { class: 'card' }, sel), h('div', { style: 'margin-top:14px' }, out));
}

// ------------------------------------------------------------------------------------------ the workspace
function guessVersions(versions) {
  const attempts = versions.filter((v) => v.kind === 'attempt');
  const clean = (v) => /with-original-audio\.mp4$|clean\.mp4$|trimmed\.mp4$/.test(v.label);
  const firstA = attempts.find((v) => /^a1 ·/.test(v.label) && clean(v)) || attempts.find((v) => /^a1 ·/.test(v.label)) || versions[0];
  const lib = versions.filter((v) => v.kind === 'fixed-lib').slice(-1)[0];
  const deliv = versions.filter((v) => v.kind === 'deliverable' && /clean/.test(v.label)).slice(-1)[0];
  const lastA = attempts.filter(clean).slice(-1)[0] || attempts.slice(-1)[0];
  return { wrong: firstA, fixed: lib || deliv || (lastA && lastA !== firstA ? lastA : versions[versions.length - 1]) };
}

export async function work(qs) {
  const [runInfo, settings] = await Promise.all([api('GET', '/api/runs/' + encodeURIComponent(qs.run || '_')).catch(() => null), api('GET', '/api/settings')]);
  let miss = qs.miss ? (await api('GET', '/api/misses/' + qs.miss)).miss : null;
  const vinfo = runInfo ? (runInfo.videos.find((v) => v.video === (qs.video || '')) || { versions: [] }) : { versions: [] };
  const versions = [...vinfo.versions];
  const g = guessVersions(versions);
  const state = { wrong: miss ? miss.wrong_path : (g.wrong ? g.wrong.path : ''), fixed: miss ? miss.fixed_path : (g.fixed ? g.fixed.path : '') };
  const addVersion = (v) => { if (!versions.some((x) => x.path === v.path)) versions.push(v); };
  if (miss) { addVersion({ path: miss.wrong_path, label: miss.wrong_label || miss.wrong_path.split(/[\\/]/).slice(-2).join('/') }); addVersion({ path: miss.fixed_path, label: miss.fixed_label || miss.fixed_path.split(/[\\/]/).slice(-2).join('/') }); }

  // ---- report form
  const title = h('input', { value: miss ? miss.title : '', placeholder: 'One line: what went wrong (e.g. “the third capsule disappears after the hand leaves”)', style: 'width:100%' });
  const expl = h('textarea', { placeholder: 'Explain the miss in your own words: what it is, where in the video, why it is wrong. The QC phase reads this.' }, miss ? miss.explanation : '');
  const kinds = chipPicker(settings.kinds, miss ? miss.kinds : []);
  const model = h('input', { value: miss ? miss.model : (runInfo ? runInfo.model || '' : ''), size: 18 });
  const mode = h('input', { value: miss ? miss.mode : (runInfo ? [runInfo.mode, runInfo.submode].filter(Boolean).join('-') : ''), size: 18 });
  const product = h('input', { value: miss ? miss.product : ((runInfo && runInfo.products && runInfo.products[0]) || ''), size: 22, placeholder: 'product / form, free text' });
  const label = (v) => v.label + (v.kind === 'fixed-lib' ? '  (client fix)' : '');
  const mkSel = (key) => {
    const s = h('select', { style: 'max-width:100%' }, versions.map((v) => h('option', { value: v.path, selected: v.path === state[key] }, label(v))));
    s.addEventListener('change', () => { state[key] = s.value; setSources(); });
    return s;
  };
  let wrongSel = mkSel('wrong'), fixedSel = mkSel('fixed');
  const rebuildSelects = () => { const a = mkSel('wrong'), b = mkSel('fixed'); wrongSel.replaceWith(a); fixedSel.replaceWith(b); wrongSel = a; fixedSel = b; };
  const fileIn = h('input', { type: 'file', accept: 'video/*,image/*', style: 'display:none' });
  const prog = h('span', { class: 'muted small' });
  fileIn.addEventListener('change', async () => {
    if (!fileIn.files[0]) return;
    try {
      const r = await upload(fileIn.files[0], (p) => (prog.textContent = Math.round(p * 100) + '%'));
      addVersion({ path: r.path, label: 'uploaded · ' + r.label, kind: 'upload' }); state.fixed = r.path; prog.textContent = ''; rebuildSelects(); setSources(); toast('Uploaded');
    } catch (e) { toast(e.message, true); prog.textContent = ''; }
  });
  const pathIn = h('input', { placeholder: 'or a path to a video file', size: 34 });
  const addPath = h('button', { class: 'btn sm', onclick: async () => {
    try { const p = await api('GET', '/api/probe?path=' + encodeURIComponent(pathIn.value)); addVersion({ path: pathIn.value, label: pathIn.value.split(/[\\/]/).slice(-2).join('/') + ' (path)' }); state.fixed = pathIn.value; rebuildSelects(); setSources(); }
    catch (_) { /* toasted: outside the folders Loupe may read? add it in Settings */ } } }, 'Use');

  async function saveReport() {
    if (!title.value.trim()) { toast('Give the report a title first', true); title.focus(); return null; }
    if (!state.wrong || !state.fixed) { toast('Choose both the wrong and the fixed version', true); return null; }
    const body = { run: qs.run || '', video: qs.video || '', title: title.value, explanation: expl.value, kinds: kinds.get(), model: model.value, mode: mode.value, product: product.value,
      wrong_path: state.wrong, fixed_path: state.fixed, wrong_label: (versions.find((v) => v.path === state.wrong) || {}).label || '', fixed_label: (versions.find((v) => v.path === state.fixed) || {}).label || '' };
    miss = miss ? (await api('PATCH', '/api/misses/' + miss.id, body)).miss : (await api('POST', '/api/misses', body)).miss;
    history.replaceState(null, '', `#/misses/work?run=${encodeURIComponent(qs.run || '')}&video=${qs.video || ''}&miss=${miss.id}`);
    return miss;
  }

  // ---- players and the shared scrubber
  const vW = h('video', { controls: true, preload: 'auto', playsinline: true }), vF = h('video', { controls: true, preload: 'auto', playsinline: true });
  const range = h('input', { type: 'range', min: 0, max: 10, step: 0.01, value: 0 });
  const tl = h('span', { class: 'mono' }, '0:00.00');
  const offset = h('input', { type: 'number', step: 0.01, value: 0, style: 'width:80px' });
  const setSources = () => { if (state.wrong) vW.src = videoUrl(state.wrong); if (state.fixed) vF.src = videoUrl(state.fixed); };
  const seek = (t) => { t = Math.max(0, +t || 0); vW.currentTime = t; vF.currentTime = Math.max(0, t + (+offset.value || 0)); range.value = t; tl.textContent = fmtT(t); };
  vW.addEventListener('loadedmetadata', () => { range.max = vW.duration || 10; });
  vW.addEventListener('timeupdate', () => { if (!vW.paused) { range.value = vW.currentTime; tl.textContent = fmtT(vW.currentTime); } });
  range.addEventListener('input', () => seek(range.value));
  const step = (d) => { vW.pause(); vF.pause(); seek(vW.currentTime + d); };
  const playBoth = h('button', { class: 'btn sm', onclick: () => { if (vW.paused) { vF.currentTime = vW.currentTime + (+offset.value || 0); vW.play(); vF.play(); playBoth.textContent = 'Pause both'; } else { vW.pause(); vF.pause(); playBoth.textContent = 'Play both'; } } }, 'Play both');
  setSources();

  const moments = h('div');
  const drawMoments = () => {
    moments.replaceChildren(miss && miss.moments.length ? h('div', { class: 'grid g3' }, miss.moments.map((m) => h('div', { class: 'card' },
      h('img', { src: m.image_url + '&v=' + encodeURIComponent(m.updated), style: 'width:100%;height:240px;object-fit:cover;object-position:top;border-radius:8px;cursor:zoom-in', onclick: () => lightbox(m.image_url + '&v=' + encodeURIComponent(m.updated)) }),
      h('div', { class: 'small', style: 'margin-top:6px' }, h('b', {}, m.id), ` · wrong @ ${fmtT(m.t_wrong)} · correct @ ${fmtT(m.t_fixed)}`), h('div', { class: 'small' }, m.details),
      h('div', { class: 'row', style: 'margin-top:6px' }, h('button', { class: 'btn sm', onclick: () => edit(m) }, 'Edit'),
        h('button', { class: 'btn sm danger', onclick: async () => { if (confirmBox('Delete this sample?')) { await api('DELETE', '/api/moments/' + m.moment_id); await refreshMiss(); } } }, 'Delete'))))) : h('div', { class: 'muted' }, 'No moment marked yet. Scrub to the place where the miss shows and press “Mark this moment”.'));
  };
  const refreshMiss = async () => { miss = (await api('GET', '/api/misses/' + miss.id)).miss; drawMoments(); };
  const edit = async (m) => { if (!(await saveReport())) return; openEditor({ miss, moment: m, kinds: kinds.get(), onSaved: refreshMiss }); };
  const mark = async () => {
    vW.pause(); vF.pause();
    if (!(await saveReport())) return;
    openEditor({ miss, t: vW.currentTime, offset: +offset.value || 0, kinds: kinds.get(), onSaved: refreshMiss });
  };
  drawMoments();

  return h('div', {},
    head(miss ? `Report: ${miss.title}` : 'New report', `${qs.run || ''} · video ${qs.video || ''}${runInfo ? ` · ${runInfo.model || ''} · ${runInfo.mode || ''}${runInfo.submode ? '/' + runInfo.submode : ''}` : ''}`, h('a', { class: 'btn', href: '#/misses' }, '← Reports')),
    h('div', { class: 'card' },
      h('label', { class: 'f', style: 'margin-top:0' }, 'What was the miss?'), title, h('label', { class: 'f' }, 'Explain it'), expl,
      h('label', { class: 'f' }, 'Kinds'), kinds.el,
      h('div', { class: 'row', style: 'margin-top:10px' }, h('span', { class: 'muted small' }, 'model'), model, h('span', { class: 'muted small' }, 'mode'), mode, h('span', { class: 'muted small' }, 'product'), product),
      h('div', { class: 'grid g2', style: 'margin-top:8px' },
        h('div', {}, h('label', { class: 'f' }, 'Wrong version (as generated)'), wrongSel),
        h('div', {}, h('label', { class: 'f' }, 'Fixed version (QC fix, your feedback, or the client’s update)'), fixedSel,
          h('div', { class: 'row', style: 'margin-top:6px' }, h('button', { class: 'btn sm', onclick: () => fileIn.click() }, 'Upload a fixed file…'), fileIn, prog, pathIn, addPath))),
      h('div', { style: 'margin-top:12px' }, h('button', { class: 'btn primary', onclick: async () => { if (await saveReport()) { toast('Report saved'); } } }, miss ? 'Save changes' : 'Save report'))),
    h('div', { class: 'card' },
      h('div', { class: 'players' }, h('div', { class: 'player' }, h('div', { class: 'lab wrong' }, 'WRONG'), vW), h('div', { class: 'player' }, h('div', { class: 'lab right' }, 'FIXED'), vF)),
      h('div', { class: 'scrub' }, playBoth, h('button', { class: 'btn sm', onclick: () => step(-1 / FPS) }, '◀ frame'), range, h('button', { class: 'btn sm', onclick: () => step(1 / FPS) }, 'frame ▶'), tl),
      h('div', { class: 'row' }, h('button', { class: 'btn primary', onclick: mark }, 'Mark this moment'),
        h('span', { class: 'muted small' }, 'Fixed version time offset (s)'), offset, h('span', { class: 'muted small' }, 'use it when the fixed take is shifted in time; the editor lets you fine-tune each side'))),
    h('div', { class: 'card' }, h('h2', {}, 'Marked moments'), moments));
}

function lightbox(src) {
  const ov = h('div', { class: 'overlay', onclick: () => ov.remove() }, h('img', { src, style: 'max-width:100%;border-radius:10px;margin:auto;background:#fff' }));
  document.body.append(ov);
}

// ------------------------------------------------------------------------------------------ the moment editor (arrow / circle / box / text on the wrong frame)
function regionNorm(shapes, pad = 0.07) {
  const xs = [], ys = [];
  for (const s of shapes) {
    if (s.type === 'arrow' || s.type === 'rect') { xs.push(s.x1, s.x2); ys.push(s.y1, s.y2); }
    else if (s.type === 'circle') { xs.push(s.cx - Math.abs(s.rx), s.cx + Math.abs(s.rx)); ys.push(s.cy - Math.abs(s.ry), s.cy + Math.abs(s.ry)); }
    else if (s.type === 'text') { xs.push(s.x, s.x + 0.25); ys.push(s.y, s.y + 0.04); }
  }
  if (!xs.length) return null;
  let x0 = Math.max(0, Math.min(...xs) - pad), x1 = Math.min(1, Math.max(...xs) + pad), y0 = Math.max(0, Math.min(...ys) - pad), y1 = Math.min(1, Math.max(...ys) + pad);
  if (x1 - x0 < 0.38) { const c = (x0 + x1) / 2; x0 = Math.max(0, c - 0.19); x1 = Math.min(1, c + 0.19); }
  if (y1 - y0 < 0.24) { const c = (y0 + y1) / 2; y0 = Math.max(0, c - 0.12); y1 = Math.min(1, c + 0.12); }
  return [x0, y0, x1, y1];
}

function drawShape(c, s, W, H, lw) {
  const stroke = (path) => { c.lineJoin = 'round'; c.lineCap = 'round'; c.strokeStyle = '#fff'; c.lineWidth = lw + 5; path(); c.stroke(); c.strokeStyle = '#ff2d2d'; c.lineWidth = lw; path(); c.stroke(); };
  if (s.type === 'arrow') {
    const x1 = s.x1 * W, y1 = s.y1 * H, x2 = s.x2 * W, y2 = s.y2 * H, a = Math.atan2(y2 - y1, x2 - x1), hl = lw * 5.5, hw = Math.PI / 7;
    const head = () => { c.beginPath(); c.moveTo(x2, y2); c.lineTo(x2 - hl * Math.cos(a - hw), y2 - hl * Math.sin(a - hw)); c.lineTo(x2 - hl * Math.cos(a + hw), y2 - hl * Math.sin(a + hw)); c.closePath(); };
    stroke(() => { c.beginPath(); c.moveTo(x1, y1); c.lineTo(x2 - hl * 0.6 * Math.cos(a), y2 - hl * 0.6 * Math.sin(a)); });
    c.fillStyle = '#fff'; c.strokeStyle = '#fff'; c.lineWidth = 3; head(); c.fill(); c.stroke(); c.fillStyle = '#ff2d2d'; head(); c.fill();
  } else if (s.type === 'circle') stroke(() => { c.beginPath(); c.ellipse(s.cx * W, s.cy * H, Math.max(1, Math.abs(s.rx) * W), Math.max(1, Math.abs(s.ry) * H), 0, 0, Math.PI * 2); });
  else if (s.type === 'rect') stroke(() => { c.beginPath(); c.rect(Math.min(s.x1, s.x2) * W, Math.min(s.y1, s.y2) * H, Math.abs(s.x2 - s.x1) * W, Math.abs(s.y2 - s.y1) * H); });
  else if (s.type === 'text') {
    const fs = Math.max(16, Math.round(H / 38)); c.font = `bold ${fs}px sans-serif`; const w = c.measureText(s.text).width;
    c.fillStyle = '#fff'; c.fillRect(s.x * W - 5, s.y * H - 3, w + 10, fs + 9); c.fillStyle = '#ff2d2d'; c.textBaseline = 'top'; c.fillText(s.text, s.x * W, s.y * H);
  }
}

function openEditor({ miss, moment, t, offset, kinds, onSaved }) {
  let tW = moment ? moment.t_wrong : (t || 0);
  let tF = moment ? moment.t_fixed : tW + (offset || 0);
  let delta = tF - tW, linked = true, tool = 'arrow', draft = null, shapes = moment ? JSON.parse(JSON.stringify(moment.shapes)) : [];
  const imgW = h('img', { alt: 'wrong frame', draggable: 'false' }), cv = h('canvas'), cmp = h('div', { class: 'cmp', style: 'display:none' });
  const imgF = h('img', { alt: 'fixed frame', draggable: 'false' });
  const stageW = h('div', { class: 'stage' }, imgW, cv), stageF = h('div', { class: 'stage' }, imgF, cmp);
  const inW = h('input', { type: 'number', step: 0.01, value: tW.toFixed(2), style: 'width:92px' }), inF = h('input', { type: 'number', step: 0.01, value: tF.toFixed(2), style: 'width:92px' });
  const details = h('textarea', { placeholder: 'What is wrong at this place (the QC phase compares exactly this)…' }, moment ? moment.details : '');
  const correct = h('textarea', { placeholder: 'What the corrected version shows here (optional but useful)…' }, moment ? moment.correct_note : '');
  const kp = chipPicker(['counts', 'openings-use', 'permanence', 'scale-size', 'label-orientation', 'solidity', 'blend-paste', 'posture-place', 'identity', 'continuity', 'other'], moment ? moment.kinds : kinds);
  const modal = h('div', { class: 'modal' });
  const ov = h('div', { class: 'overlay' }, modal);
  let timer;
  const sizeCanvas = () => { cv.width = imgW.clientWidth; cv.height = imgW.clientHeight; redraw(); updateCmp(); };
  imgW.addEventListener('load', sizeCanvas);
  window.addEventListener('resize', sizeCanvas);
  const loadFrames = () => { clearTimeout(timer); timer = setTimeout(() => { imgW.src = frameUrl(miss.wrong_path, tW); imgF.src = frameUrl(miss.fixed_path, tF); }, 120); };
  const setTimes = (w, f) => { tW = Math.max(0, w); tF = Math.max(0, f); inW.value = tW.toFixed(2); inF.value = tF.toFixed(2); loadFrames(); };
  const nudge = (which, d) => { if (which === 'w') setTimes(tW + d, linked ? tF + d : tF); else { setTimes(tW, tF + d); delta = tF - tW; } };
  inW.addEventListener('change', () => setTimes(+inW.value, linked ? +inW.value + delta : tF));
  inF.addEventListener('change', () => { setTimes(tW, +inF.value); delta = tF - tW; });
  const redraw = () => {
    const c = cv.getContext('2d'); c.clearRect(0, 0, cv.width, cv.height);
    const lw = Math.max(3, cv.height / 190);
    for (const s of draft ? [...shapes, draft] : shapes) drawShape(c, s, cv.width, cv.height, lw);
  };
  const updateCmp = () => {
    const r = regionNorm(shapes);
    if (!r) { cmp.style.display = 'none'; return; }
    cmp.style.display = 'block'; cmp.style.left = (r[0] * 100) + '%'; cmp.style.top = (r[1] * 100) + '%'; cmp.style.width = ((r[2] - r[0]) * 100) + '%'; cmp.style.height = ((r[3] - r[1]) * 100) + '%';
  };
  const norm = (ev) => { const r = cv.getBoundingClientRect(); return [Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (ev.clientY - r.top) / r.height))]; };
  let start = null;
  cv.addEventListener('pointerdown', (ev) => {
    const [x, y] = norm(ev);
    if (tool === 'text') {
      const inp = h('input', { class: 'textin', placeholder: 'label…', style: `left:${x * 100}%;top:${y * 100}%;` });
      let done = false;
      const commit = () => { if (done) return; done = true; const v = inp.value.trim(); inp.remove(); if (v) { shapes.push({ type: 'text', x, y, text: v }); redraw(); updateCmp(); } };
      inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') { done = true; inp.remove(); } });
      inp.addEventListener('blur', commit);
      stageW.append(inp); setTimeout(() => inp.focus(), 0); return;
    }
    start = [x, y]; cv.setPointerCapture(ev.pointerId);
  });
  cv.addEventListener('pointermove', (ev) => {
    if (!start) return; const [x, y] = norm(ev);
    if (tool === 'arrow') draft = { type: 'arrow', x1: start[0], y1: start[1], x2: x, y2: y };
    else if (tool === 'rect') draft = { type: 'rect', x1: start[0], y1: start[1], x2: x, y2: y };
    else draft = { type: 'circle', cx: (start[0] + x) / 2, cy: (start[1] + y) / 2, rx: Math.abs(x - start[0]) / 2, ry: Math.abs(y - start[1]) / 2 };
    redraw();
  });
  const finish = () => {
    if (start && draft) {
      const big = draft.type === 'circle' ? (draft.rx > 0.01 || draft.ry > 0.01) : Math.hypot(draft.x2 - draft.x1, draft.y2 - draft.y1) > 0.015;
      if (big) shapes.push(draft);
    }
    start = null; draft = null; redraw(); updateCmp();
  };
  cv.addEventListener('pointerup', finish); cv.addEventListener('pointercancel', finish);
  const toolBtns = {};
  const tb = (id, label) => (toolBtns[id] = h('button', { class: 'btn sm tool' + (tool === id ? ' on' : ''), onclick: () => { tool = id; Object.entries(toolBtns).forEach(([k, b]) => b.classList.toggle('on', k === id)); } }, label));
  const close = () => { window.removeEventListener('resize', sizeCanvas); ov.remove(); };
  const save = async () => {
    const body = { t_wrong: tW, t_fixed: tF, shapes, details: details.value, correct_note: correct.value, kinds: kp.get() };
    try {
      saveBtn.disabled = true; saveBtn.textContent = 'Building the sample image…';
      const r = moment ? await api('PATCH', '/api/moments/' + moment.moment_id, body) : await api('POST', `/api/misses/${miss.id}/moments`, body);
      onSaved && (await onSaved());
      modal.replaceChildren(h('div', { class: 'head' }, h('div', {}, h('h1', {}, `Sample ${r.moment.id} saved`), h('div', { class: 'muted' }, 'This is the image the QC phase will see.')), h('button', { class: 'btn primary', onclick: close }, 'Done')),
        h('img', { src: r.moment.image_url + '&v=' + Date.now(), style: 'max-width:100%;display:block;margin:0 auto;border-radius:10px;border:1px solid var(--line)' }));
    } catch (_) { saveBtn.disabled = false; saveBtn.textContent = moment ? 'Save changes' : 'Save sample'; }
  };
  const saveBtn = h('button', { class: 'btn primary', onclick: save }, moment ? 'Save changes' : 'Save sample');
  modal.append(
    h('div', { class: 'head' }, h('div', {}, h('h1', {}, moment ? `Edit ${moment.id}` : 'Mark the moment'), h('div', { class: 'muted' }, 'Draw on the wrong frame where the miss is. The correct frame at the same moment is taken from the fixed version.')),
      h('div', { class: 'row' }, saveBtn, h('button', { class: 'btn', onclick: close }, 'Cancel'))),
    h('div', { class: 'tools' }, tb('arrow', 'Arrow'), tb('circle', 'Circle'), tb('rect', 'Box'), tb('text', 'Text'),
      h('button', { class: 'btn sm', onclick: () => { shapes.pop(); redraw(); updateCmp(); } }, 'Undo'), h('button', { class: 'btn sm', onclick: () => { shapes = []; redraw(); updateCmp(); } }, 'Clear marks'),
      h('span', { class: 'muted small', style: 'margin-left:10px' }, 'drag to draw · the dashed green box on the right shows the same place in the fixed version')),
    h('div', { class: 'stagewrap' },
      h('div', {}, h('div', { class: 'row', style: 'margin-bottom:6px' }, h('b', { style: 'color:#d92d20' }, 'WRONG'), h('span', { class: 'muted small' }, 'time'), inW,
        h('button', { class: 'btn sm', onclick: () => nudge('w', -1 / FPS) }, '◀ 1f'), h('button', { class: 'btn sm', onclick: () => nudge('w', 1 / FPS) }, '1f ▶'),
        h('label', { class: 'small' }, h('input', { type: 'checkbox', checked: true, onchange: (e) => { linked = e.target.checked; delta = tF - tW; } }), ' keep the fixed time linked')), stageW),
      h('div', {}, h('div', { class: 'row', style: 'margin-bottom:6px' }, h('b', { style: 'color:#1f9d55' }, 'CORRECT'), h('span', { class: 'muted small' }, 'time'), inF,
        h('button', { class: 'btn sm', onclick: () => nudge('f', -1 / FPS) }, '◀ 1f'), h('button', { class: 'btn sm', onclick: () => nudge('f', 1 / FPS) }, '1f ▶')), stageF)),
    h('div', { class: 'grid g2' }, h('div', {}, h('label', { class: 'f' }, 'What is wrong here'), details), h('div', {}, h('label', { class: 'f' }, 'What the correct version shows'), correct)),
    h('label', { class: 'f' }, 'Kinds (for the QC search)'), kp.el);
  document.body.append(ov);
  loadFrames();
}
