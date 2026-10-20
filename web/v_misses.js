import { h, toast, fmtDate, chips, status, fmtT, chipPicker, confirmBox } from './ui.js';
import { api, upload, videoUrl } from './api.js';

const head = (title, sub, ...actions) => h('div', { class: 'head' }, h('div', {}, h('h1', {}, title), sub ? h('div', { class: 'muted' }, sub) : null), h('div', { class: 'row' }, actions));
const FPS = 30;

// ------------------------------------------------------------------------------------------ list of reports
export async function reports() {
  const d = await api('GET', '/api/misses');
  const reload = () => reports().then((n) => document.querySelector('.main').replaceChildren(n));
  return h('div', {},
    head('Generation misses', 'Pick a run, open a video, say what went wrong, mark the moment. The right version is shown at the same moment and both go into one sample image for the QC phase.',
      h('a', { class: 'btn primary', href: '#/misses/new' }, 'New report')),
    d.misses.length ? d.misses.map((m) => h('div', { class: 'card' },
      h('div', { class: 'row', style: 'justify-content:space-between' },
        h('div', {}, h('b', {}, m.title), ' ', status(m.status), h('div', { class: 'muted small' }, `${m.run || 'no run'} · video ${m.video || '-'} · ${m.model || 'any model'} · ${fmtDate(m.created)}${!m.wrong_path && !m.fixed_path ? ' · no video' : (m.fixed_path ? '' : ' · no right version')}`)),
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
    real.map((r) => h('option', { value: r.run }, `${r.lib ? r.lib.toUpperCase() + ' · ' : ''}${r.run} · ${r.mode || ''}${r.submode ? '/' + r.submode : ''} · ${r.model || ''} · ${r.videos} videos`)));
  sel.addEventListener('change', async () => {
    if (!sel.value) { out.replaceChildren(); return; }
    const r = await api('GET', '/api/runs/' + encodeURIComponent(sel.value));
    out.replaceChildren(h('div', { class: 'grid g4' }, r.videos.map((v) => h('a', { class: 'card', style: 'color:inherit;text-decoration:none', href: `#/misses/work?run=${encodeURIComponent(r.run)}&video=${v.video}` },
      h('b', {}, 'Video ' + v.video), ' ', v.status ? status(v.status) : null,
      h('div', { class: 'muted small' }, `${v.versions.length} version(s)` + (v.capcut_project ? ` · ${v.capcut_project}` : '') + (v.credits ? ` · ${v.credits} cr` : '')),
      v.qc ? h('div', { class: 'small', style: 'margin-top:4px' }, v.qc.slice(0, 120)) : null))));
  });
  return h('div', {}, head('New report', 'Choose the run that was executed by the pipeline, then the video the report is about. A report can also be in words only: the idea, what could have been better, what the AI agents missed.',
      h('a', { class: 'btn', href: '#/misses/work?run=&video=' }, 'Report without a video'), h('a', { class: 'btn', href: '#/misses' }, '← Reports')),
    h('div', { class: 'card' }, sel), h('div', { style: 'margin-top:14px' }, out));
}

// ------------------------------------------------------------------------------------------ the workspace
// One page, one step: any number of frames side by side (two to start with), each from either video, each with its own time and called WRONG or RIGHT by the
// admin, marks drawn straight on any of them, and one button that saves the moment as a single image (the frames, the marks, a zoom, the explanation burned in).
function guessVersions(versions) {       // only the wrong version is guessed (the first attempt); the right one is always the user's choice
  const attempts = versions.filter((v) => v.kind === 'attempt');
  const clean = (v) => /with-original-audio\.mp4$|clean\.mp4$|trimmed\.mp4$/.test(v.label);
  const firstA = attempts.find((v) => /^a1 ·/.test(v.label) && clean(v)) || attempts.find((v) => /^a1 ·/.test(v.label)) || versions[0];
  return { wrong: firstA };
}

function drawShape(c, s, W, H, lw, col) {
  const stroke = (path) => { c.lineJoin = 'round'; c.lineCap = 'round'; c.strokeStyle = '#fff'; c.lineWidth = lw + 2; path(); c.stroke(); c.strokeStyle = col; c.lineWidth = lw; path(); c.stroke(); };
  if (s.type === 'arrow') {
    const x1 = s.x1 * W, y1 = s.y1 * H, x2 = s.x2 * W, y2 = s.y2 * H, a = Math.atan2(y2 - y1, x2 - x1), hl = Math.max(lw * 7, H / 45), hw = Math.PI / 7;
    const head = () => { c.beginPath(); c.moveTo(x2, y2); c.lineTo(x2 - hl * Math.cos(a - hw), y2 - hl * Math.sin(a - hw)); c.lineTo(x2 - hl * Math.cos(a + hw), y2 - hl * Math.sin(a + hw)); c.closePath(); };
    stroke(() => { c.beginPath(); c.moveTo(x1, y1); c.lineTo(x2 - hl * 0.6 * Math.cos(a), y2 - hl * 0.6 * Math.sin(a)); });
    c.fillStyle = '#fff'; c.strokeStyle = '#fff'; c.lineWidth = 1.5; head(); c.fill(); c.stroke(); c.fillStyle = col; head(); c.fill();
  } else if (s.type === 'circle') stroke(() => { c.beginPath(); c.ellipse(s.cx * W, s.cy * H, Math.max(1, Math.abs(s.rx) * W), Math.max(1, Math.abs(s.ry) * H), 0, 0, Math.PI * 2); });
  else if (s.type === 'rect') stroke(() => { c.beginPath(); c.rect(Math.min(s.x1, s.x2) * W, Math.min(s.y1, s.y2) * H, Math.abs(s.x2 - s.x1) * W, Math.abs(s.y2 - s.y1) * H); });
  else if (s.type === 'text') {
    const fs = Math.max(16, Math.round(H / 38)); c.font = `bold ${fs}px sans-serif`; const w = c.measureText(s.text).width;
    c.fillStyle = '#fff'; c.fillRect(s.x * W - 5, s.y * H - 3, w + 10, fs + 9); c.fillStyle = col; c.textBaseline = 'top'; c.fillText(s.text, s.x * W, s.y * H);
  }
}

export async function work(qs) {
  const [runInfo, settings] = await Promise.all([api('GET', '/api/runs/' + encodeURIComponent(qs.run || '_')).catch(() => null), api('GET', '/api/settings')]);
  let miss = qs.miss ? (await api('GET', '/api/misses/' + qs.miss)).miss : null;
  const vinfo = runInfo ? (runInfo.videos.find((v) => v.video === (qs.video || '')) || { versions: [] }) : { versions: [] };
  const versions = [...vinfo.versions];
  const g = guessVersions(versions);
  const state = { wrong: miss ? miss.wrong_path : (g.wrong ? g.wrong.path : ''), fixed: miss ? miss.fixed_path : '' };     // neither video is required: '' = none, else its path (the right version is never guessed)
  const addVersion = (v) => { if (!versions.some((x) => x.path === v.path)) versions.push(v); };
  if (miss) { if (miss.wrong_path) addVersion({ path: miss.wrong_path, label: miss.wrong_label || miss.wrong_path.split(/[\\/]/).slice(-2).join('/') }); if (miss.fixed_path) addVersion({ path: miss.fixed_path, label: miss.fixed_label || miss.fixed_path.split(/[\\/]/).slice(-2).join('/') }); }
  const ws = { shapes: [], tool: 'arrow', editing: null, draft: null, past: [], future: [] };      // every mark belongs to one frame (shape.fid); nothing is ever copied for you
  const addMark = (shape) => setMarks([...ws.shapes, shape]);
  const syncHist = () => { undoBtn.disabled = !ws.past.length; redoBtn.disabled = !ws.future.length; };
  const setMarks = (next) => { ws.past.push(ws.shapes); if (ws.past.length > 200) ws.past.shift(); ws.shapes = next; ws.future = []; redrawAll(); syncHist(); };   // a new mark or a clear: one undoable step
  const resetMarks = (next) => { ws.shapes = next; ws.past = []; ws.future = []; redrawAll(); syncHist(); };                                                          // a fresh start (after a save, or an edit loaded)
  const undo = () => { if (!ws.past.length) return; ws.future.push(ws.shapes); ws.shapes = ws.past.pop(); redrawAll(); syncHist(); };
  const redo = () => { if (!ws.future.length) return; ws.past.push(ws.shapes); ws.shapes = ws.future.pop(); redrawAll(); syncHist(); };

  // ---- the two versions
  const label = (v) => v.label + (v.kind === 'fixed-lib' ? '  (client fix)' : '');
  const mkSel = (key) => {
    const opts = versions.map((v) => h('option', { value: v.path, selected: v.path === state[key] }, label(v)));
    opts.unshift(h('option', { value: '', selected: !state[key] }, key === 'fixed' ? 'No right version (optional)' : 'No video (a report in words only)'));
    const sel = h('select', { style: 'max-width:100%' }, opts);
    sel.addEventListener('change', () => { state[key] = sel.value; setSources(); });
    return sel;
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
    try { await api('GET', '/api/probe?path=' + encodeURIComponent(pathIn.value)); addVersion({ path: pathIn.value, label: pathIn.value.split(/[\\/]/).slice(-2).join('/') + ' (path)' }); state.fixed = pathIn.value; rebuildSelects(); setSources(); }
    catch (_) { /* toasted: outside the folders Loupe may read? add it in Settings */ } } }, 'Use');

  // ---- the report fields (the title and the explanation are burned into the saved image)
  const title = h('input', { value: miss ? miss.title : '', placeholder: 'One line or a paragraph: what went wrong, or what this scenario is about (e.g. “the third capsule disappears after the hand leaves”)', style: 'width:100%' });
  const details = h('textarea', { placeholder: 'Explain this miss: what is wrong at the marked place and why. This text is burned into the bottom centre of the image, and the QC phase reads it.' });
  const correct = h('textarea', { placeholder: 'What the right frame shows here (optional, burned in under the explanation)', style: 'min-height:48px' });
  const idea = h('textarea', { placeholder: 'The idea: what was meant to happen here (optional)', style: 'min-height:48px' });
  const better = h('textarea', { placeholder: 'What could have been better (optional)', style: 'min-height:48px' });
  const missed = h('textarea', { placeholder: 'What the AI agents missed or could not understand while generating this (optional)', style: 'min-height:48px' });
  const kinds = chipPicker(settings.kinds, miss ? miss.kinds : []);
  const suggestBtn = h('button', { class: 'btn sm', type: 'button', title: 'ticks the kinds of the vocabulary whose words occur in what you wrote; untick any that do not fit', onclick: async () => {
    const text = [title.value, details.value, idea.value, better.value, missed.value].join(' ');
    try {
      const d = await api('GET', '/api/misses/suggest-kinds?text=' + encodeURIComponent(text));
      if (d.suggested && d.suggested.length) { kinds.set(d.suggested); toast('Suggested from your words: ' + d.suggested.join(', ') + ' (untick any that do not fit)'); }
      else toast('No kind of the vocabulary occurs in the words yet');
    } catch (e) { toast(e.message, true); }
  } }, 'Suggest kinds from the words');
  const model = h('input', { value: miss ? miss.model : (runInfo ? runInfo.model || '' : ''), size: 18 });
  const mode = h('input', { value: miss ? miss.mode : (runInfo ? [runInfo.mode, runInfo.submode].filter(Boolean).join('-') : ''), size: 18 });
  const product = h('input', { value: miss ? miss.product : ((runInfo && runInfo.products && runInfo.products[0]) || ''), size: 22, placeholder: 'the product and its details: form, size, opening, natural posture' });

  const useSample = h('input', { type: 'checkbox', checked: miss ? miss.product_sample !== false : true });
  const sampleBox = h('div', { class: 'small', style: 'margin-top:12px' });
  const loadSample = async () => {      // the product image(s) this video's request used, straight from the reference folder
    if (!qs.run || !qs.video) { sampleBox.replaceChildren(); return; }
    const wl = (versions.find((v) => v.path === state.wrong) || {}).label || state.wrong || '';
    try {
      const d = await api('GET', `/api/product-sample?run=${encodeURIComponent(qs.run)}&video=${encodeURIComponent(qs.video)}&wrong=${encodeURIComponent(wl)}`, undefined, true);
      sampleBox.replaceChildren(...(d.samples.length ? [h('div', { class: 'row', style: 'gap:12px;align-items:center' },
        ...d.samples.map((x) => h('img', { src: x.image_url, alt: x.label, style: 'height:92px;border-radius:6px;border:1px solid var(--line);background:#cdd0d6' })),
        h('div', {}, h('label', { class: 'small' }, useSample, ' Show the product sample in the saved image (right half of the bottom band)'),
          h('div', { class: 'muted small' }, 'Used in this video, taken from the reference folder: ' + d.samples.map((x) => `${x.label} (${x.asset_id}, ${x.name})`).join(' · '))))]
        : [h('span', { class: 'muted' }, 'No product sample was found for this video in its run, so the description takes the full width of the bottom band.')]));
    } catch (_) { sampleBox.replaceChildren(); }
  };

  async function saveReport() {
    const body = { run: qs.run || '', video: qs.video || '', title: title.value, explanation: miss ? miss.explanation : details.value, kinds: kinds.get(), model: model.value, mode: mode.value, product: product.value, product_sample: useSample.checked,
      wrong_path: state.wrong, fixed_path: state.fixed, wrong_label: (versions.find((v) => v.path === state.wrong) || {}).label || '', fixed_label: (versions.find((v) => v.path === state.fixed) || {}).label || '' };
    const wasNoRight = !!miss && !miss.fixed_path, nSaved = miss ? miss.moments.length : 0;
    miss = miss ? (await api('PATCH', '/api/misses/' + miss.id, body)).miss : (await api('POST', '/api/misses', body)).miss;
    if (wasNoRight && miss.fixed_path && nSaved) toast(`The right version was attached to ${nSaved} saved moment(s) at the same seconds: open Edit on each to line it up and mark the right side.`);
    history.replaceState(null, '', `#/misses/work?run=${encodeURIComponent(qs.run || '')}&video=${qs.video || ''}&miss=${miss.id}`);
    return miss;
  }

  // ---- the frames: any number (two to start with), each from either video, each with its own time, each called WRONG or RIGHT by the admin.
  // The marks are kept in the picture's own 0..1 coordinates and belong to one frame (shape.fid).
  const COLOR = { wrong: '#ff2d2d', right: '#16a34a', example: '#3b6edc' };
  const SRC_NAME = { wrong: 'Video A · as generated', fixed: 'Video B · correct version' };
  const MAX_FRAMES = 12;
  const frames = [];
  let fseq = 0;
  const playersBox = h('div', { class: 'players' });
  const marksOf = (f) => ws.shapes.filter((x) => x.fid === f.id);
  const pic = (f) => {                       // where the picture really is inside the (letterboxed) video element
    const cw = f.cv.clientWidth, ch = f.cv.clientHeight, vw = f.video.videoWidth, vh = f.video.videoHeight;
    if (!vw || !vh) return { x: 0, y: 0, w: cw, h: ch };
    const k = Math.min(cw / vw, ch / vh), w = vw * k, hh = vh * k;
    return { x: (cw - w) / 2, y: (ch - hh) / 2, w, h: hh };
  };
  const redraw = (f) => {
    const dpr = window.devicePixelRatio || 1, cw = f.cv.clientWidth, ch = f.cv.clientHeight;
    if (f.cv.width !== Math.round(cw * dpr) || f.cv.height !== Math.round(ch * dpr)) { f.cv.width = Math.round(cw * dpr); f.cv.height = Math.round(ch * dpr); }
    const c = f.cv.getContext('2d'); c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, cw, ch);
    const r = pic(f), col = COLOR[f.role], lw = Math.max(1.5, r.h / 480);
    const mine = marksOf(f);
    c.save(); c.translate(r.x, r.y);
    for (const m of ws.draft && ws.draft.fid === f.id ? [...mine, ws.draft] : mine) drawShape(c, m, r.w, r.h, lw, col);
    c.restore();
  };
  const redrawAll = () => frames.forEach(redraw);

  // ---- the transport: the top slider moves every frame together, keeping the gaps between them (a gap starts at zero: the same time);
  // the slider, the 1-frame buttons and the time box of a frame move only that frame, so each frame has its own timestamp
  const range = h('input', { type: 'range', min: 0, max: 10, step: 1 / FPS, value: 0, title: 'moves all the frames together, keeping the gaps between them; arrow keys step one frame' });
  const parseT = (v) => { v = String(v).trim(); const m = v.match(/^(\d+):(\d+(?:\.\d+)?)$/); return m ? (+m[1]) * 60 + (+m[2]) : parseFloat(v); };
  const clampT = (f, t) => Math.min(Math.max(0, +t || 0), f.video.duration || 1e9);
  const refresh = () => {
    if (frames[0]) range.value = frames[0].t;
    for (const f of frames) { f.slider.value = f.t; if (document.activeElement !== f.stamp) f.stamp.value = fmtT(f.t); }
  };
  const pauseAll = () => { frames.forEach((f) => f.video.pause()); playAll.textContent = 'Play all'; };
  const seekAll = (t) => {                 // every frame, keeping its gap to the first one
    if (!frames.length) return;
    const f0 = frames[0], t0 = clampT(f0, t);
    for (const f of frames) { f.t = f === f0 ? t0 : clampT(f, t0 + f.gap); f.video.currentTime = f.t; }
    refresh();
  };
  const moveFrame = (f, t) => {            // this frame only; the gaps follow from where the frames now are
    pauseAll(); f.t = clampT(f, t); f.video.currentTime = f.t;
    const f0 = frames[0];
    if (f === f0) frames.forEach((o) => { if (o !== f0) o.gap = o.t - f0.t; }); else f.gap = f.t - f0.t;
    refresh();
  };
  const sameTime = () => { pauseAll(); if (!frames.length) return; frames.forEach((f) => { f.gap = 0; }); seekAll(frames[0].t); };
  const step = (d) => { pauseAll(); if (frames.length) seekAll(frames[0].t + d); };
  const playAll = h('button', { class: 'btn sm', onclick: () => {
    if (!frames.length) return;
    if (frames[0].video.paused) { frames.forEach((f) => { f.video.currentTime = clampT(f, frames[0].t + f.gap); }); frames.forEach((f) => f.video.play()); playAll.textContent = 'Pause all'; } else pauseAll();
  } }, 'Play all');
  const tick = () => { if (frames.some((f) => !f.video.paused)) { frames.forEach((f) => { f.t = f.video.currentTime; }); refresh(); } };
  range.addEventListener('input', () => { pauseAll(); seekAll(range.value); });
  // ---- end of the transport

  const wireDraw = (f) => {
    f.ro = new ResizeObserver(() => redraw(f)); f.ro.observe(f.wrap);
    f.video.addEventListener('loadedmetadata', () => { redraw(f); f.slider.max = f.video.duration || 10; if (f === frames[0]) range.max = f.video.duration || 10; });
    f.video.addEventListener('timeupdate', tick);
    const norm = (ev) => { const b = f.cv.getBoundingClientRect(), r = pic(f); return [Math.min(1, Math.max(0, (ev.clientX - b.left - r.x) / r.w)), Math.min(1, Math.max(0, (ev.clientY - b.top - r.y) / r.h))]; };
    let start = null;
    f.cv.addEventListener('pointerdown', (ev) => {
      ev.preventDefault(); pauseAll();
      const [x, y] = norm(ev);
      if (ws.tool === 'text') {
        const r = pic(f), inp = h('input', { class: 'textin', placeholder: 'label…', style: `left:${r.x + x * r.w}px;top:${r.y + y * r.h}px;` });
        let done = false;
        const commit = () => { if (done) return; done = true; const v = inp.value.trim(); inp.remove(); if (v) addMark({ fid: f.id, type: 'text', x, y, text: v }); };
        inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') { done = true; inp.remove(); } });
        inp.addEventListener('blur', commit);
        f.wrap.append(inp); setTimeout(() => inp.focus(), 0); return;
      }
      start = [x, y]; f.cv.setPointerCapture(ev.pointerId);
    });
    f.cv.addEventListener('pointermove', (ev) => {
      if (!start) return; const [x, y] = norm(ev);
      if (ws.tool === 'arrow') ws.draft = { fid: f.id, type: 'arrow', x1: start[0], y1: start[1], x2: x, y2: y };
      else if (ws.tool === 'rect') ws.draft = { fid: f.id, type: 'rect', x1: start[0], y1: start[1], x2: x, y2: y };
      else ws.draft = { fid: f.id, type: 'circle', cx: (start[0] + x) / 2, cy: (start[1] + y) / 2, rx: Math.abs(x - start[0]) / 2, ry: Math.abs(y - start[1]) / 2 };
      redraw(f);
    });
    const finish = () => {
      const d = ws.draft;
      if (start && d) {
        const big = d.type === 'circle' ? (d.rx > 0.01 || d.ry > 0.01) : Math.hypot(d.x2 - d.x1, d.y2 - d.y1) > 0.015;
        if (big) { ws.draft = null; addMark(d); }
      }
      start = null; ws.draft = null; redrawAll();
    };
    f.cv.addEventListener('pointerup', finish); f.cv.addEventListener('pointercancel', finish);
  };

  const styleFrame = (f) => {
    f.btnWrong.classList.toggle('on', f.role === 'wrong'); f.btnRight.classList.toggle('on', f.role === 'right'); f.btnExample.classList.toggle('on', f.role === 'example');
    f.el.classList.toggle('role-wrong', f.role === 'wrong'); f.el.classList.toggle('role-right', f.role === 'right'); f.el.classList.toggle('role-example', f.role === 'example');
    f.srcSel.value = f.src; f.srcSel.options[0].disabled = !state.wrong; f.srcSel.options[1].disabled = !state.fixed;
  };
  const loadFrame = (f) => { const path = state[f.src]; if (path) { f.video.src = videoUrl(path); f.video.currentTime = f.t; } else f.video.removeAttribute('src'); };
  const mkFrame = (src, role, t) => {
    const f = { id: ++fseq, src, role, t, gap: 0 };
    f.video = h('video', { preload: 'auto', playsinline: true }); f.video.muted = true;
    f.cv = h('canvas', { class: 'pc', title: 'Draw here: red marks on a frame called Wrong, green on Right, blue on Example' });
    f.wrap = h('div', { class: 'vwrap' }, f.video, f.cv);
    f.num = h('b', { class: 'fnum' });
    f.btnWrong = h('button', { class: 'btn sm rolebtn wrong', title: 'This frame shows the miss', onclick: () => setRole(f, 'wrong') }, 'Wrong');
    f.btnRight = h('button', { class: 'btn sm rolebtn right', title: 'This frame shows how it should be', onclick: () => setRole(f, 'right') }, 'Right');
    f.btnExample = h('button', { class: 'btn sm rolebtn example', title: 'Keep this frame as an example for the QC keeper to understand and remember: neither a miss nor the right look (blue marks)', onclick: () => setRole(f, 'example') }, 'Example');
    f.srcSel = h('select', { class: 'srcsel', title: 'which video this frame is taken from' }, h('option', { value: 'wrong' }, SRC_NAME.wrong), h('option', { value: 'fixed' }, SRC_NAME.fixed));
    f.srcSel.addEventListener('change', () => setFrameSrc(f, f.srcSel.value));
    f.snd = h('button', { class: 'btn sm', onclick: () => { f.video.muted = !f.video.muted; f.snd.textContent = f.video.muted ? 'Sound off' : 'Sound on'; } }, 'Sound off');
    f.rm = h('button', { class: 'btn sm danger', title: 'Remove this frame', onclick: () => removeFrame(f) }, '✕');
    f.slider = h('input', { type: 'range', min: 0, max: 10, step: 1 / FPS, value: 0, title: 'moves only this frame', style: 'flex:1;min-width:60px' });
    f.slider.addEventListener('input', () => moveFrame(f, f.slider.value));
    f.stamp = h('input', { type: 'text', class: 'stamp mono', title: 'this frame’s own time: seconds or m:ss.ss, then Enter' });
    f.stamp.value = fmtT(t);
    f.stamp.addEventListener('change', () => { const v = parseT(f.stamp.value); if (isFinite(v)) moveFrame(f, v); else refresh(); });
    f.el = h('div', { class: 'player' },
      h('div', { class: 'row', style: 'justify-content:space-between;margin-bottom:6px' }, h('div', { class: 'row' }, f.num, f.btnWrong, f.btnRight, f.btnExample), h('div', { class: 'row' }, f.srcSel, f.snd, f.rm)),
      f.wrap,
      h('div', { class: 'row', style: 'flex-wrap:nowrap;margin-top:8px' },
        h('button', { class: 'btn sm', title: 'this frame one video frame back', onclick: () => moveFrame(f, f.t - 1 / FPS) }, '◀'), f.slider,
        h('button', { class: 'btn sm', title: 'this frame one video frame forward', onclick: () => moveFrame(f, f.t + 1 / FPS) }, '▶'), f.stamp));
    wireDraw(f);
    styleFrame(f);
    return f;
  };
  const syncCorrect = () => { const on = frames.some((x) => x.role === 'right'); correctLbl.style.display = on ? '' : 'none'; correct.style.display = on ? '' : 'none'; };
  const drawPlayers = () => {
    playersBox.replaceChildren(...frames.map((f) => f.el), noRightBox);
    const side = frames.length === 0 || (frames.length === 1 && frames[0].role === 'wrong');        // a card beside a lone miss frame, or alone when there are no frames
    noRightBox.style.display = side ? 'flex' : 'none';
    playersBox.style.setProperty('--cols', String(Math.min(Math.max(frames.length + (side ? 1 : 0), 1), 3)));
    frames.forEach((f, i) => { f.num.textContent = 'Frame ' + (i + 1); });
    addBtn.disabled = frames.length >= MAX_FRAMES || (!state.wrong && !state.fixed); sameBtn.style.display = frames.length > 1 ? '' : 'none';
    syncCorrect(); syncRight();
  };
  const addFrame = (src, role, t) => {
    const f = mkFrame(src, role, t);
    frames.push(f); f.gap = f.t - frames[0].t;
    loadFrame(f); drawPlayers(); refresh(); setTimeout(() => redraw(f), 250);
    return f;
  };
  const dispose = (f) => { f.video.pause(); f.ro.disconnect(); f.video.removeAttribute('src'); f.video.load(); };
  const removeFrame = (f) => {
    pauseAll();
    if (marksOf(f).length) setMarks(ws.shapes.filter((x) => x.fid !== f.id));
    dispose(f); frames.splice(frames.indexOf(f), 1);
    frames.forEach((o) => { o.gap = o.t - frames[0].t; });
    if (frames[0]) range.max = frames[0].video.duration || 10;
    drawPlayers(); refresh();
  };
  const setRole = (f, role) => { f.role = role; styleFrame(f); redraw(f); syncCorrect(); };
  const setFrameSrc = (f, src) => {
    if (src === 'fixed' && !state.fixed) { toast('This report has no right version video yet: choose one above first', true); f.srcSel.value = f.src; return; }
    f.src = src; loadFrame(f);
  };
  let syncRight = () => {};
  const setSources = () => {      // the version pickers changed
    for (const f of frames.filter((x) => !state[x.src])) removeFrame(f);      // frames of a video that is gone fall away
    if (state.fixed && frames.length === 1 && !ws.shapes.length && !ws.editing && frames[0].src === 'wrong' && frames[0].role === 'wrong') addFrame('fixed', 'right', frames[0].t);      // two frames to start with
    frames.forEach((f) => { styleFrame(f); loadFrame(f); });
    drawPlayers(); loadSample();
  };

  // ---- tools
  const undoBtn = h('button', { class: 'btn sm', title: 'Ctrl+Z', disabled: true, onclick: undo }, 'Undo'), redoBtn = h('button', { class: 'btn sm', title: 'Ctrl+Y or Ctrl+Shift+Z', disabled: true, onclick: redo }, 'Redo');
  const toolBtns = {};
  const tb = (id, text) => (toolBtns[id] = h('button', { class: 'btn sm tool' + (ws.tool === id ? ' on' : ''), onclick: () => { ws.tool = id; Object.entries(toolBtns).forEach(([k, b]) => b.classList.toggle('on', k === id)); } }, text));

  // ---- saving a moment: one image, nothing else to confirm
  const result = h('div');
  const saveBtn = h('button', { class: 'btn primary big', onclick: () => saveMoment() }, 'Mark this moment');
  const cancelEdit = h('button', { class: 'btn', style: 'display:none', onclick: () => leaveEdit() }, 'Cancel edit');
  const leaveEdit = () => { ws.editing = null; saveBtn.textContent = 'Mark this moment'; cancelEdit.style.display = 'none'; };
  async function saveMoment() {
    pauseAll();
    const text = details.value.trim();
    // never blocks: with no title it is cut from the explanation, and with neither it is named by the video and the second
    if (!title.value.trim()) title.value = text ? text.split(/(?<=[.!?])\s/)[0].slice(0, 90) : (frames.length ? `Video ${qs.video || ''} at ${fmtT(frames[0].t)}` : 'A scenario');
    saveBtn.disabled = true; saveBtn.textContent = 'Building the image…';
    try {
      if (!(await saveReport())) return;
      const idx = new Map(frames.map((f, i) => [f.id, i]));
      const body = { frames: frames.map((f) => ({ src: f.src, role: f.role, t: f.t })),
        shapes: ws.shapes.filter((x) => idx.has(x.fid)).map(({ fid, ...rest }) => ({ ...rest, frame: idx.get(fid) })),
        details: text, correct_note: frames.some((f) => f.role === 'right') ? correct.value : '', idea: idea.value.trim(), better: better.value.trim(), missed: missed.value.trim(), kinds: kinds.get() };
      const r = ws.editing ? await api('PATCH', '/api/moments/' + ws.editing.moment_id, body) : await api('POST', `/api/misses/${miss.id}/moments`, body);
      await refreshMiss();
      const src = r.moment.image_url + '&v=' + Date.now();
      result.replaceChildren(h('div', { class: 'card' }, h('div', { class: 'row', style: 'justify-content:space-between' },
        h('div', {}, h('b', {}, `${r.moment.id} saved`), h('span', { class: 'muted small' }, '  this is the one image the QC phase will see')),
        h('button', { class: 'btn sm', onclick: () => result.replaceChildren() }, 'Close')),
        h('img', { src, style: 'max-width:100%;max-height:78vh;display:block;margin:10px auto 0;border-radius:10px;border:1px solid var(--line);cursor:zoom-in', onclick: () => lightbox(src) })));
      resetMarks([]); details.value = ''; correct.value = ''; idea.value = ''; better.value = ''; missed.value = ''; leaveEdit();     // ready for the next moment; the frames, the title and kinds stay
      result.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch (_) { /* toasted */ } finally { saveBtn.disabled = false; saveBtn.textContent = ws.editing ? 'Update moment' : 'Mark this moment'; }
  }

  // ---- the moments saved so far
  const moments = h('div');
  const refreshMiss = async () => { miss = (await api('GET', '/api/misses/' + miss.id)).miss; drawMoments(); };
  const loadMoment = (m) => {              // an existing moment comes back into the same workspace: its frames, their own times and roles, its marks, its text
    pauseAll(); ws.editing = m;
    frames.splice(0).forEach(dispose);
    for (const fr of m.frames) if (state[fr.src]) frames.push(mkFrame(fr.src, fr.role, fr.t));
    frames.forEach((f) => { f.gap = f.t - frames[0].t; loadFrame(f); });
    if (frames[0]) range.max = frames[0].video.duration || 10;
    resetMarks(frames.length ? JSON.parse(JSON.stringify(m.shapes)).map((x) => ({ ...x, fid: frames[Math.min(x.frame || 0, frames.length - 1)].id })) : []);
    drawPlayers(); refresh(); setTimeout(redrawAll, 250);
    details.value = m.details || ''; correct.value = m.correct_note || ''; idea.value = m.idea || ''; better.value = m.better || ''; missed.value = m.missed || '';
    saveBtn.textContent = 'Update moment'; cancelEdit.style.display = ''; window.scrollTo({ top: 0, behavior: 'smooth' });
  };
  const framesText = (m) => (m.frames.length ? m.frames.map((f) => `${f.role} @ ${fmtT(f.t)}`).join(' · ') : 'words only')
    + (m.sample_kind === 'miss-only' ? ' · miss example, no right frame' : m.sample_kind === 'scenario' ? ' · scenario' : '');
  const drawMoments = () => {
    moments.replaceChildren(miss && miss.moments.length ? h('div', { class: 'grid g3' }, miss.moments.map((m) => h('div', { class: 'card' },
      h('img', { src: m.image_url + '&v=' + encodeURIComponent(m.updated), style: 'width:100%;height:240px;object-fit:cover;object-position:top;border-radius:8px;cursor:zoom-in', onclick: () => lightbox(m.image_url + '&v=' + encodeURIComponent(m.updated)) }),
      h('div', { class: 'small', style: 'margin-top:6px' }, h('b', {}, m.id), ' · ' + framesText(m)), h('div', { class: 'small' }, m.details || m.missed || m.idea || ''),
      h('div', { class: 'row', style: 'margin-top:6px' }, h('button', { class: 'btn sm', onclick: () => loadMoment(m) }, 'Edit'),
        h('button', { class: 'btn sm danger', onclick: async () => { if (confirmBox('Delete this sample?')) { await api('DELETE', '/api/moments/' + m.moment_id); await refreshMiss(); } } }, 'Delete'))))) : h('div', { class: 'muted' }, 'No moment saved yet. Pause on the frames that matter (a miss, the right look, or neither), draw on them if it helps, say what it is about, and press “Mark this moment”. Frames are optional: words alone make a report too.'));
  };
  drawMoments();

  const addBtn = h('button', { class: 'btn sm primary', title: 'adds a frame after the last one (0.2 s later, same video, same role); change any of that on the new frame', onclick: () => { const l = frames[frames.length - 1]; if (l) addFrame(l.src, l.role, l.t + 0.2); else if (state.wrong) addFrame('wrong', 'wrong', 0); else addFrame('fixed', 'example', 0); } }, '+ Add a frame');
  const sameBtn = h('button', { class: 'btn sm', title: 'puts every frame on the time of Frame 1', onclick: sameTime }, 'Same time on all frames');
  const correctLbl = h('label', { class: 'f' }, 'What the right frame shows (optional)');
  const noRightTitle = h('b', {}), noRightText = h('div', { style: 'margin-top:6px;color:#aab;font-size:13px;max-width:320px' });
  const noRightBox = h('div', { class: 'player', style: 'display:none;align-items:center;justify-content:center;text-align:center;min-height:220px' }, h('div', {}, noRightTitle, noRightText));
  const saveNote = h('span', { class: 'small', style: 'color:var(--accent);font-weight:600' });
  syncRight = () => {       // beside a lone miss frame, or alone when there is no frame: what is missing, and that it is optional
    noRightTitle.textContent = frames.length === 0 ? 'No frames' : 'No right frame';
    noRightText.textContent = frames.length === 0
      ? 'This report is in words only: write the idea, what could have been better and what the AI agents missed below. Add a frame if a picture helps.'
      : 'Optional. Add a frame and call it Right (from Video B, or from an earlier part of the same video) if a comparison helps; otherwise this stays a miss example.';
    saveBtn.disabled = false; saveNote.textContent = '';
  };
  const onKey = (e) => {       // Ctrl+Z undoes, Ctrl+Y or Ctrl+Shift+Z redoes, unless the cursor is in a text field
    if (!root.isConnected) { window.removeEventListener('keydown', onKey); return; }
    if (e.target && e.target.closest && e.target.closest('input,textarea,select')) return;
    if ((e.ctrlKey || e.metaKey) && !e.altKey) {
      const k = e.key.toLowerCase();
      if (k === 'z' && !e.shiftKey) { e.preventDefault(); undo(); } else if (k === 'y' || (k === 'z' && e.shiftKey)) { e.preventDefault(); redo(); }
    }
  };
  window.addEventListener('keydown', onKey);
  if (state.wrong) addFrame('wrong', 'wrong', 0);
  setSources();      // two frames when a right version is already chosen; none when there is no video
  const root = h('div', {},
    h('div', { class: 'head' }, h('div', {}, h('h1', {}, miss ? `Report: ${miss.title}` : 'New report'),
      h('div', { class: 'muted' }, `${qs.run || ''} · video ${qs.video || ''}${runInfo ? ` · ${runInfo.model || ''} · ${runInfo.mode || ''}${runInfo.submode ? '/' + runInfo.submode : ''}` : ''}`)),
      h('a', { class: 'btn', href: '#/misses' }, '← Reports')),
    h('div', { class: 'card' },
      h('div', { class: 'grid g2' },
        h('div', {}, h('label', { class: 'f', style: 'margin-top:0' }, 'Video A: the version as generated (optional)'), wrongSel),
        h('div', {}, h('label', { class: 'f', style: 'margin-top:0' }, 'Video B: the right version (optional: the corrected take, a QC fix, your feedback, or the client’s update)'), fixedSel,
          h('div', { class: 'row', style: 'margin-top:6px' }, h('button', { class: 'btn sm', onclick: () => fileIn.click() }, 'Upload the right video…'), fileIn, prog, pathIn, addPath)))),
    h('div', { class: 'card' },
      h('div', { class: 'tools' }, tb('arrow', 'Arrow'), tb('circle', 'Circle'), tb('rect', 'Box'), tb('text', 'Text'),
        undoBtn, redoBtn,
        h('button', { class: 'btn sm', onclick: () => { if (ws.shapes.length) setMarks([]); } }, 'Clear marks'),
        h('span', { class: 'muted small', style: 'margin-left:10px' }, 'You choose what each frame is: Wrong (red marks), Right (green) or Example (blue: something for the QC keeper to understand and remember), which video it comes from, and its own time. Everything here is optional: a report can have no right version, no wrong frame, even no frames. Nothing is marked or copied for you.')),
      playersBox,
      h('div', { class: 'scrub' }, playAll, h('button', { class: 'btn sm', onclick: () => step(-1 / FPS) }, '◀ frame'), range, h('button', { class: 'btn sm', onclick: () => step(1 / FPS) }, 'frame ▶'),
        h('span', { class: 'muted small' }, 'moves all frames together')),
      h('div', { class: 'row' }, addBtn, sameBtn, h('span', { class: 'muted small' }, 'Every frame has its own slider, 1-frame buttons and time box (type 3.25 or 0:03.25 and press Enter). Two frames to start with; add as many as you need, from either video.'))),
    h('div', { class: 'card' },
      h('label', { class: 'f', style: 'margin-top:0' }, 'What was the miss, or what is this scenario about?'), title,
      h('label', { class: 'f' }, 'The idea: what was meant to happen (optional)'), idea,
      h('label', { class: 'f' }, 'What could have been better (optional)'), better,
      h('label', { class: 'f' }, 'What the AI agents missed or could not understand (optional)'), missed,
      h('label', { class: 'f' }, 'Explain it, if there is more to say (burned into the bottom of the image)'), details,
      correctLbl, correct, sampleBox,
      h('label', { class: 'f' }, 'Kinds (for the QC search)'), kinds.el, suggestBtn,
      h('details', { style: 'margin-top:8px' }, h('summary', { class: 'muted small', style: 'cursor:pointer' }, 'Model, mode, product'),
        h('div', { class: 'row', style: 'margin-top:6px' }, h('span', { class: 'muted small' }, 'model'), model, h('span', { class: 'muted small' }, 'mode'), mode, h('span', { class: 'muted small' }, 'product'), product)),
      h('div', { class: 'row', style: 'margin-top:14px' }, saveBtn, cancelEdit, saveNote,
        h('span', { class: 'muted small' }, 'One click: saves the report, cuts every frame at its own time, and builds one image: the frames tagged WRONG or RIGHT with your marks, a zoom of each, and this text.'))),
    result,
    h('div', { class: 'card' }, h('h2', {}, 'Saved moments'), moments));
  return root;
}

function lightbox(src) {
  const ov = h('div', { class: 'overlay', onclick: () => ov.remove() }, h('img', { src, style: 'max-width:100%;border-radius:10px;margin:auto;background:#fff' }));
  document.body.append(ov);
}
