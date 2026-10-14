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
        h('div', {}, h('b', {}, m.title), ' ', status(m.status), h('div', { class: 'muted small' }, `${m.run || 'no run'} · video ${m.video || '-'} · ${m.model || 'any model'} · ${fmtDate(m.created)}${m.fixed_path ? '' : ' · miss example (no right version)'}`)),
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
// One page, one step: the wrong and the right video side by side, marks drawn straight on either of them, and one button that saves
// the moment as a single image (wrong | right, the marks, a zoom, and the explanation burned in the bottom centre).
function guessVersions(versions) {       // only the wrong version is guessed (the first attempt); the right one is always the user's choice
  const attempts = versions.filter((v) => v.kind === 'attempt');
  const clean = (v) => /with-original-audio\.mp4$|clean\.mp4$|trimmed\.mp4$/.test(v.label);
  const firstA = attempts.find((v) => /^a1 ·/.test(v.label) && clean(v)) || attempts.find((v) => /^a1 ·/.test(v.label)) || versions[0];
  return { wrong: firstA };
}

const COLORS = { wrong: '#ff2d2d', fixed: '#16a34a' };

function regionNorm(shapes, pad = 0.07) {        // the same box the server computes: where the marks are, padded, never tiny
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
  const state = { wrong: miss ? miss.wrong_path : (g.wrong ? g.wrong.path : ''), fixed: miss ? miss.fixed_path : null };     // fixed: null = not chosen yet, '' = there is none, else its path
  const addVersion = (v) => { if (!versions.some((x) => x.path === v.path)) versions.push(v); };
  if (miss) { addVersion({ path: miss.wrong_path, label: miss.wrong_label || miss.wrong_path.split(/[\\/]/).slice(-2).join('/') }); if (miss.fixed_path) addVersion({ path: miss.fixed_path, label: miss.fixed_label || miss.fixed_path.split(/[\\/]/).slice(-2).join('/') }); }
  const ws = { shapes: [], tool: 'arrow', editing: null, draft: null, past: [], future: [], mirror: true };
  const addMark = (shape) => setMarks([...ws.shapes, shape, ...(ws.mirror && state.fixed ? [{ ...shape, side: shape.side === 'wrong' ? 'fixed' : 'wrong' }] : [])]);
  const syncHist = () => { undoBtn.disabled = !ws.past.length; redoBtn.disabled = !ws.future.length; };
  const setMarks = (next) => { ws.past.push(ws.shapes); if (ws.past.length > 200) ws.past.shift(); ws.shapes = next; ws.future = []; redrawAll(); syncHist(); };   // a new mark or a clear: one undoable step
  const resetMarks = (next) => { ws.shapes = next; ws.past = []; ws.future = []; redrawAll(); syncHist(); };                                                          // a fresh start (after a save, or an edit loaded)
  const undo = () => { if (!ws.past.length) return; ws.future.push(ws.shapes); ws.shapes = ws.past.pop(); redrawAll(); syncHist(); };
  const redo = () => { if (!ws.future.length) return; ws.past.push(ws.shapes); ws.shapes = ws.future.pop(); redrawAll(); syncHist(); };

  // ---- the two versions
  const label = (v) => v.label + (v.kind === 'fixed-lib' ? '  (client fix)' : '');
  const mkSel = (key) => {
    const opts = versions.map((v) => h('option', { value: v.path, selected: v.path === state[key] }, label(v)));
    if (key === 'fixed') {
      opts.unshift(h('option', { value: '__choose__', selected: state.fixed === null }, 'Choose the right version…'));
      opts.push(h('option', { value: '', selected: state.fixed === '' }, 'No right version exists: save this miss as an example'));
    }
    const s = h('select', { style: 'max-width:100%', class: key === 'fixed' && state.fixed === null ? 'need' : '' }, opts);
    s.addEventListener('change', () => { state[key] = key === 'fixed' && s.value === '__choose__' ? null : s.value; s.classList.toggle('need', key === 'fixed' && state.fixed === null); setSources(); });
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
    try { await api('GET', '/api/probe?path=' + encodeURIComponent(pathIn.value)); addVersion({ path: pathIn.value, label: pathIn.value.split(/[\\/]/).slice(-2).join('/') + ' (path)' }); state.fixed = pathIn.value; rebuildSelects(); setSources(); }
    catch (_) { /* toasted: outside the folders Loupe may read? add it in Settings */ } } }, 'Use');

  // ---- the report fields (the title and the explanation are burned into the saved image)
  const title = h('input', { value: miss ? miss.title : '', placeholder: 'One line: what went wrong (e.g. “the third capsule disappears after the hand leaves”)', style: 'width:100%' });
  const details = h('textarea', { placeholder: 'Explain this miss: what is wrong at the marked place and why. This text is burned into the bottom centre of the image, and the QC phase reads it.' });
  const correct = h('textarea', { placeholder: 'What the right version shows here (optional, burned in under the explanation)', style: 'min-height:48px' });
  const kinds = chipPicker(settings.kinds, miss ? miss.kinds : []);
  const model = h('input', { value: miss ? miss.model : (runInfo ? runInfo.model || '' : ''), size: 18 });
  const mode = h('input', { value: miss ? miss.mode : (runInfo ? [runInfo.mode, runInfo.submode].filter(Boolean).join('-') : ''), size: 18 });
  const product = h('input', { value: miss ? miss.product : ((runInfo && runInfo.products && runInfo.products[0]) || ''), size: 22, placeholder: 'the product and its details: form, size, opening, natural posture' });

  async function saveReport() {
    if (!state.wrong) { toast('Choose the wrong version', true); return null; }
    if (state.fixed === null) { toast('Choose the right version first, or say that there is none', true); fixedSel.focus(); return null; }
    const body = { run: qs.run || '', video: qs.video || '', title: title.value, explanation: miss ? miss.explanation : details.value, kinds: kinds.get(), model: model.value, mode: mode.value, product: product.value,
      wrong_path: state.wrong, fixed_path: state.fixed, wrong_label: (versions.find((v) => v.path === state.wrong) || {}).label || '', fixed_label: (versions.find((v) => v.path === state.fixed) || {}).label || '' };
    const wasNoRight = !!miss && !miss.fixed_path, nSaved = miss ? miss.moments.length : 0;
    miss = miss ? (await api('PATCH', '/api/misses/' + miss.id, body)).miss : (await api('POST', '/api/misses', body)).miss;
    if (wasNoRight && miss.fixed_path && nSaved) toast(`The right version was attached to ${nSaved} saved moment(s) at the same seconds: open Edit on each to line it up and mark the right side.`);
    history.replaceState(null, '', `#/misses/work?run=${encodeURIComponent(qs.run || '')}&video=${qs.video || ''}&miss=${miss.id}`);
    return miss;
  }

  // ---- a player: the video with a canvas on top; marks are kept in the picture's own 0..1 coordinates
  const mkPlayer = (side, text) => {
    const video = h('video', { preload: 'auto', playsinline: true });
    video.muted = true;
    const cv = h('canvas', { class: 'pc', title: 'Draw here: ' + (side === 'wrong' ? 'red marks on the wrong video' : 'green marks on the right video') });
    const wrap = h('div', { class: 'vwrap' }, video, cv);
    const snd = h('button', { class: 'btn sm', onclick: () => { video.muted = !video.muted; snd.textContent = video.muted ? 'Sound off' : 'Sound on'; } }, 'Sound off');
    const el = h('div', { class: 'player' }, h('div', { class: 'row', style: 'justify-content:space-between' }, h('div', { class: 'lab ' + (side === 'wrong' ? 'wrong' : 'right') }, text), snd), wrap);
    return { side, video, cv, wrap, el };
  };
  const P = { wrong: mkPlayer('wrong', 'WRONG'), fixed: mkPlayer('fixed', 'RIGHT') };
  const vW = P.wrong.video, vF = P.fixed.video;
  const pic = (p) => {                       // where the picture really is inside the (letterboxed) video element
    const cw = p.cv.clientWidth, ch = p.cv.clientHeight, vw = p.video.videoWidth, vh = p.video.videoHeight;
    if (!vw || !vh) return { x: 0, y: 0, w: cw, h: ch };
    const s = Math.min(cw / vw, ch / vh), w = vw * s, hh = vh * s;
    return { x: (cw - w) / 2, y: (ch - hh) / 2, w, h: hh };
  };
  const redraw = (p) => {
    const dpr = window.devicePixelRatio || 1, cw = p.cv.clientWidth, ch = p.cv.clientHeight;
    if (p.cv.width !== Math.round(cw * dpr) || p.cv.height !== Math.round(ch * dpr)) { p.cv.width = Math.round(cw * dpr); p.cv.height = Math.round(ch * dpr); }
    const c = p.cv.getContext('2d'); c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, cw, ch);
    const r = pic(p), col = COLORS[p.side], lw = Math.max(1.5, r.h / 480);
    const mine = ws.shapes.filter((s) => s.side === p.side), other = ws.shapes.filter((s) => s.side !== p.side);
    c.save(); c.translate(r.x, r.y);
    for (const s of ws.draft && ws.draft.side === p.side ? [...mine, ws.draft] : mine) drawShape(c, s, r.w, r.h, lw, col);
    if (!mine.length && other.length) {      // the same place on this side, dashed: where to compare (the saved image does the same)
      const b = regionNorm(other); c.setLineDash([12, 8]); c.strokeStyle = col; c.lineWidth = lw; c.strokeRect(b[0] * r.w, b[1] * r.h, (b[2] - b[0]) * r.w, (b[3] - b[1]) * r.h);
    }
    c.restore();
  };
  const redrawAll = () => { redraw(P.wrong); redraw(P.fixed); };
  for (const p of Object.values(P)) {
    new ResizeObserver(() => redraw(p)).observe(p.wrap);
    p.video.addEventListener('loadedmetadata', () => { redraw(p); if (p.side === 'wrong') range.max = vW.duration || 10; });
    const norm = (ev) => { const b = p.cv.getBoundingClientRect(), r = pic(p); return [Math.min(1, Math.max(0, (ev.clientX - b.left - r.x) / r.w)), Math.min(1, Math.max(0, (ev.clientY - b.top - r.y) / r.h))]; };
    let start = null;
    p.cv.addEventListener('pointerdown', (ev) => {
      ev.preventDefault(); pauseBoth();
      const [x, y] = norm(ev);
      if (ws.tool === 'text') {
        const r = pic(p), inp = h('input', { class: 'textin', placeholder: 'label…', style: `left:${r.x + x * r.w}px;top:${r.y + y * r.h}px;` });
        let done = false;
        const commit = () => { if (done) return; done = true; const v = inp.value.trim(); inp.remove(); if (v) addMark({ side: p.side, type: 'text', x, y, text: v }); };
        inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') { done = true; inp.remove(); } });
        inp.addEventListener('blur', commit);
        p.wrap.append(inp); setTimeout(() => inp.focus(), 0); return;
      }
      start = [x, y]; p.cv.setPointerCapture(ev.pointerId);
    });
    p.cv.addEventListener('pointermove', (ev) => {
      if (!start) return; const [x, y] = norm(ev);
      if (ws.tool === 'arrow') ws.draft = { side: p.side, type: 'arrow', x1: start[0], y1: start[1], x2: x, y2: y };
      else if (ws.tool === 'rect') ws.draft = { side: p.side, type: 'rect', x1: start[0], y1: start[1], x2: x, y2: y };
      else ws.draft = { side: p.side, type: 'circle', cx: (start[0] + x) / 2, cy: (start[1] + y) / 2, rx: Math.abs(x - start[0]) / 2, ry: Math.abs(y - start[1]) / 2 };
      redraw(p);
    });
    const finish = () => {
      const d = ws.draft;
      if (start && d) {
        const big = d.type === 'circle' ? (d.rx > 0.01 || d.ry > 0.01) : Math.hypot(d.x2 - d.x1, d.y2 - d.y1) > 0.015;
        if (big) { ws.draft = null; addMark(d); }
      }
      start = null; ws.draft = null; redrawAll();
    };
    p.cv.addEventListener('pointerup', finish); p.cv.addEventListener('pointercancel', finish);
  }

  // ---- the shared transport: one scrubber moves both videos; the offset shifts only the fixed one
  const range = h('input', { type: 'range', min: 0, max: 10, step: 1 / FPS, value: 0, title: 'arrow keys step one frame' });
  const tl = h('span', { class: 'mono' }, '0:00.00');
  const offset = h('input', { type: 'number', step: 1 / FPS, value: 0, style: 'width:84px' });
  let syncRight = () => {};
  const setSources = () => { if (state.wrong) vW.src = videoUrl(state.wrong); if (state.fixed) vF.src = videoUrl(state.fixed); else vF.removeAttribute('src'); syncRight(); };
  const showTime = () => { tl.textContent = fmtT(vW.currentTime) + '  ·  right ' + fmtT(vF.currentTime); };
  const seek = (t) => { t = Math.max(0, +t || 0); vW.currentTime = t; vF.currentTime = Math.max(0, t + (+offset.value || 0)); range.value = t; tl.textContent = fmtT(t) + '  ·  right ' + fmtT(Math.max(0, t + (+offset.value || 0))); };
  vW.addEventListener('timeupdate', () => { if (!vW.paused) { range.value = vW.currentTime; showTime(); } });
  range.addEventListener('input', () => seek(range.value));
  offset.addEventListener('change', () => seek(vW.currentTime));
  const pauseBoth = () => { vW.pause(); vF.pause(); playBoth.textContent = 'Play both'; };
  const step = (d) => { pauseBoth(); seek(vW.currentTime + d); };
  const nudgeFixed = (d) => { pauseBoth(); offset.value = ((+offset.value || 0) + d).toFixed(3); seek(vW.currentTime); };
  const playBoth = h('button', { class: 'btn sm', onclick: () => { if (vW.paused) { vF.currentTime = vW.currentTime + (+offset.value || 0); vW.play(); if (state.fixed) vF.play(); playBoth.textContent = 'Pause both'; } else pauseBoth(); } }, 'Play both');
  setSources();

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
    pauseBoth();
    const text = details.value.trim();
    // never blocks: with no title it is cut from the explanation, and with neither it is named by the video and the second
    if (!title.value.trim()) title.value = text ? text.split(/(?<=[.!?])\s/)[0].slice(0, 90) : `Video ${qs.video || ''} at ${fmtT(vW.currentTime)}`;
    saveBtn.disabled = true; saveBtn.textContent = 'Building the image…';
    try {
      if (!(await saveReport())) return;
      const body = { t_wrong: vW.currentTime, t_fixed: state.fixed ? vF.currentTime : vW.currentTime, shapes: ws.shapes, details: text, correct_note: correct.value, kinds: kinds.get() };
      const r = ws.editing ? await api('PATCH', '/api/moments/' + ws.editing.moment_id, body) : await api('POST', `/api/misses/${miss.id}/moments`, body);
      await refreshMiss();
      const src = r.moment.image_url + '&v=' + Date.now();
      result.replaceChildren(h('div', { class: 'card' }, h('div', { class: 'row', style: 'justify-content:space-between' },
        h('div', {}, h('b', {}, `${r.moment.id} saved`), h('span', { class: 'muted small' }, '  this is the one image the QC phase will see')),
        h('button', { class: 'btn sm', onclick: () => result.replaceChildren() }, 'Close')),
        h('img', { src, style: 'max-width:100%;max-height:78vh;display:block;margin:10px auto 0;border-radius:10px;border:1px solid var(--line);cursor:zoom-in', onclick: () => lightbox(src) })));
      resetMarks([]); details.value = ''; correct.value = ''; leaveEdit();     // ready for the next moment; the title and kinds stay
      result.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch (_) { /* toasted */ } finally { saveBtn.disabled = state.fixed === null; saveBtn.textContent = ws.editing ? 'Update moment' : 'Mark this moment'; }
  }

  // ---- the moments saved so far
  const moments = h('div');
  const refreshMiss = async () => { miss = (await api('GET', '/api/misses/' + miss.id)).miss; drawMoments(); };
  const loadMoment = (m) => {              // an existing moment comes back into the same workspace
    pauseBoth(); ws.editing = m; resetMarks(JSON.parse(JSON.stringify(m.shapes)).map((s) => ({ side: 'wrong', ...s })));
    offset.value = m.has_right ? (m.t_fixed - m.t_wrong).toFixed(3) : 0; seek(m.t_wrong); details.value = m.details || ''; correct.value = m.correct_note || '';
    saveBtn.textContent = 'Update moment'; cancelEdit.style.display = ''; setTimeout(redrawAll, 250); window.scrollTo({ top: 0, behavior: 'smooth' });
  };
  const drawMoments = () => {
    moments.replaceChildren(miss && miss.moments.length ? h('div', { class: 'grid g3' }, miss.moments.map((m) => h('div', { class: 'card' },
      h('img', { src: m.image_url + '&v=' + encodeURIComponent(m.updated), style: 'width:100%;height:240px;object-fit:cover;object-position:top;border-radius:8px;cursor:zoom-in', onclick: () => lightbox(m.image_url + '&v=' + encodeURIComponent(m.updated)) }),
      h('div', { class: 'small', style: 'margin-top:6px' }, h('b', {}, m.id), m.has_right ? ` · wrong @ ${fmtT(m.t_wrong)} · right @ ${fmtT(m.t_fixed)}` : ` · wrong @ ${fmtT(m.t_wrong)} · miss example, no right version`), h('div', { class: 'small' }, m.details),
      h('div', { class: 'row', style: 'margin-top:6px' }, h('button', { class: 'btn sm', onclick: () => loadMoment(m) }, 'Edit'),
        h('button', { class: 'btn sm danger', onclick: async () => { if (confirmBox('Delete this sample?')) { await api('DELETE', '/api/moments/' + m.moment_id); await refreshMiss(); } } }, 'Delete'))))) : h('div', { class: 'muted' }, 'No moment saved yet. Pause on the place where the miss shows, draw on the videos, explain it, and press “Mark this moment”.'));
  };
  drawMoments();

  const mirrorLbl = h('label', { class: 'small', style: 'display:inline-flex;gap:4px;align-items:center;margin-left:6px' },
    h('input', { type: 'checkbox', checked: true, onchange: (e) => { ws.mirror = e.target.checked; } }), 'same mark on both videos');
  const offsetRow = h('div', { class: 'row' }, h('span', { class: 'muted small' }, 'Right video is shifted by (s)'), offset,
    h('button', { class: 'btn sm', onclick: () => nudgeFixed(-1 / FPS) }, 'right ◀ 1f'), h('button', { class: 'btn sm', onclick: () => nudgeFixed(1 / FPS) }, 'right 1f ▶'),
    h('span', { class: 'muted small' }, 'use it when the right take is a little ahead or behind, so both show the same moment'));
  const correctLbl = h('label', { class: 'f' }, 'What the right version shows (optional)');
  const noRightTitle = h('b', {}), noRightText = h('div', { style: 'margin-top:6px;color:#aab;font-size:13px;max-width:320px' });
  const noRightBox = h('div', { class: 'player', style: 'display:none;align-items:center;justify-content:center;text-align:center;min-height:220px' }, h('div', {}, noRightTitle, noRightText));
  const saveNote = h('span', { class: 'small', style: 'color:var(--accent);font-weight:600' });
  syncRight = () => {       // what needs a right version is shown only when one is chosen; nothing is saved before the user has chosen (a version, or none)
    const decided = state.fixed !== null, on = !!state.fixed;
    noRightTitle.textContent = decided ? 'No right version' : 'Right version not chosen yet';
    noRightText.textContent = decided ? 'This miss stays as an example of what goes wrong. Mark it on the left. If a right version exists later, pick it above and the saved moments are rebuilt with it.'
      : 'Pick the right version above, or say that none exists. The moment cannot be saved before you choose.';
    saveBtn.disabled = !decided; saveNote.textContent = decided ? '' : 'Choose the right version above first (or say there is none).';
    P.fixed.el.style.display = on ? '' : 'none'; noRightBox.style.display = on ? 'none' : 'flex';
    mirrorLbl.style.display = on ? 'inline-flex' : 'none'; offsetRow.style.display = on ? '' : 'none';
    correctLbl.style.display = on ? '' : 'none'; correct.style.display = on ? '' : 'none';
    redrawAll();
  };
  syncRight();
  const onKey = (e) => {       // Ctrl+Z undoes, Ctrl+Y or Ctrl+Shift+Z redoes, unless the cursor is in a text field
    if (!root.isConnected) { window.removeEventListener('keydown', onKey); return; }
    if (e.target && e.target.closest && e.target.closest('input,textarea,select')) return;
    if ((e.ctrlKey || e.metaKey) && !e.altKey) {
      const k = e.key.toLowerCase();
      if (k === 'z' && !e.shiftKey) { e.preventDefault(); undo(); } else if (k === 'y' || (k === 'z' && e.shiftKey)) { e.preventDefault(); redo(); }
    }
  };
  window.addEventListener('keydown', onKey);
  const root = h('div', {},
    h('div', { class: 'head' }, h('div', {}, h('h1', {}, miss ? `Report: ${miss.title}` : 'New report'),
      h('div', { class: 'muted' }, `${qs.run || ''} · video ${qs.video || ''}${runInfo ? ` · ${runInfo.model || ''} · ${runInfo.mode || ''}${runInfo.submode ? '/' + runInfo.submode : ''}` : ''}`)),
      h('a', { class: 'btn', href: '#/misses' }, '← Reports')),
    h('div', { class: 'card' },
      h('div', { class: 'grid g2' },
        h('div', {}, h('label', { class: 'f', style: 'margin-top:0' }, 'Wrong version (as generated)'), wrongSel),
        h('div', {}, h('label', { class: 'f', style: 'margin-top:0' }, 'Right version (the corrected take: QC fix, your feedback, or the client’s update)'), fixedSel,
          h('div', { class: 'row', style: 'margin-top:6px' }, h('button', { class: 'btn sm', onclick: () => fileIn.click() }, 'Upload the right video…'), fileIn, prog, pathIn, addPath)))),
    h('div', { class: 'card' },
      h('div', { class: 'tools' }, tb('arrow', 'Arrow'), tb('circle', 'Circle'), tb('rect', 'Box'), tb('text', 'Text'),
        undoBtn, redoBtn, mirrorLbl,
        h('button', { class: 'btn sm', onclick: () => { if (ws.shapes.length) setMarks([]); } }, 'Clear marks'),
        h('span', { class: 'muted small', style: 'margin-left:10px' }, 'Pause on the frame, then draw straight on either video: red on the wrong one, green on the right one; the same mark appears on both so the place and size match. Untick “same mark on both videos” to mark one side only.')),
      h('div', { class: 'players' }, P.wrong.el, P.fixed.el, noRightBox),
      h('div', { class: 'scrub' }, playBoth, h('button', { class: 'btn sm', onclick: () => step(-1 / FPS) }, '◀ frame'), range, h('button', { class: 'btn sm', onclick: () => step(1 / FPS) }, 'frame ▶'), tl),
      offsetRow),
    h('div', { class: 'card' },
      h('label', { class: 'f', style: 'margin-top:0' }, 'What was the miss?'), title,
      h('label', { class: 'f' }, 'Explain it (burned into the bottom centre of the image)'), details,
      correctLbl, correct,
      h('label', { class: 'f' }, 'Kinds (for the QC search)'), kinds.el,
      h('details', { style: 'margin-top:8px' }, h('summary', { class: 'muted small', style: 'cursor:pointer' }, 'Model, mode, product'),
        h('div', { class: 'row', style: 'margin-top:6px' }, h('span', { class: 'muted small' }, 'model'), model, h('span', { class: 'muted small' }, 'mode'), mode, h('span', { class: 'muted small' }, 'product'), product)),
      h('div', { class: 'row', style: 'margin-top:14px' }, saveBtn, cancelEdit, saveNote,
        h('span', { class: 'muted small' }, 'One click: saves the report, cuts both frames at these seconds, and builds one image: wrong | right with your marks, a zoom, and this text.'))),
    result,
    h('div', { class: 'card' }, h('h2', {}, 'Saved moments'), moments));
  return root;
}

function lightbox(src) {
  const ov = h('div', { class: 'overlay', onclick: () => ov.remove() }, h('img', { src, style: 'max-width:100%;border-radius:10px;margin:auto;background:#fff' }));
  document.body.append(ov);
}
