import { h, toast, fmtDate, chips, status, fmtT, chipPicker, confirmBox } from './ui.js';
import { api, upload, videoUrl, frameUrl } from './api.js';

const head = (title, sub, ...actions) => h('div', { class: 'head' }, h('div', {}, h('h1', {}, title), sub ? h('div', { class: 'muted' }, sub) : null), h('div', { class: 'row' }, actions));
const FPS = 30;

// ------------------------------------------------------------------------------------------ list of reports
// what a report keeps in its prompt boxes, as the database keeps it (prompt_roles): the flawed version's box (the generations the frames called Wrong come
// from), the correct version's (those the frames called Right come from, from either video),
// and a side whose prompt is an Example's in every sample of the report is a suggestion, neither bad nor correct
const promptsKept = (m) => {
    const ex = (k) => ((m.prompt_roles || {})[k] === 'example');
    const name = (k) => (ex(k) ? `the example prompt of Video ${k === 'wrong' ? 'A' : 'B'} (a suggestion)` : (k === 'wrong' ? 'the flawed version' : 'the correct version'));
    if (m.prompts === 'pair') return ex('wrong') && ex('fixed') ? ' · example prompts of both videos kept (suggestions)' : ` · prompts of ${name('wrong')} and ${name('fixed')} kept`;
    return m.prompts === 'wrong' || m.prompts === 'fixed' ? ` · prompt of ${name(m.prompts)} kept` : '';
};
export async function reports() {
  const d = await api('GET', '/api/misses');
  const reload = () => reports().then((n) => document.querySelector('.main').replaceChildren(n));
  return h('div', {},
    head('Generation misses', 'Pick a run, open a video, say what went wrong, mark the moment. The right version is shown at the same moment and both go into one sample image for the QC phase.',
      h('a', { class: 'btn primary', href: '#/misses/new' }, 'New report')),
    d.misses.length ? d.misses.map((m) => h('div', { class: 'card' },
      h('div', { class: 'row', style: 'justify-content:space-between' },
        h('div', {}, h('b', {}, m.title), ' ', status(m.status), h('div', { class: 'muted small' }, `${m.run || 'no run'} · video ${m.video || '-'} · ${m.model || 'any model'} · ${fmtDate(m.created)}${!m.wrong_path && !m.fixed_path ? ' · no video' : (m.fixed_path ? '' : ' · no right version')}${promptsKept(m)}`)),
        h('div', { class: 'row' }, h('a', { class: 'btn sm', href: `#/misses/work?run=${encodeURIComponent(m.run)}&video=${m.video}&miss=${m.id}` }, 'Open'),
          h('button', { class: 'btn sm', onclick: async () => { await api('PATCH', '/api/misses/' + m.id, { status: m.status === 'active' ? 'archived' : 'active' }); reload(); } }, m.status === 'active' ? 'Archive' : 'Restore'),
          h('button', { class: 'btn sm danger', onclick: async () => { if (confirmBox(`Delete the report “${m.title}” and its ${m.moments.length} sample(s)?`)) { await api('DELETE', '/api/misses/' + m.id); reload(); } } }, 'Delete'))),
      m.explanation ? h('p', {}, m.explanation) : null, h('div', {}, chips(m.kinds)),
      m.moments.length ? h('div', { class: 'grid g4', style: 'margin-top:8px' }, m.moments.map((s) => h('a', { href: `#/misses/work?run=${encodeURIComponent(m.run)}&video=${m.video}&miss=${m.id}&moment=${s.moment_id}`, title: 'Edit this sample where it stands' + (s.details ? ': ' + s.details : '') }, h('img', { src: s.image_url, style: 'width:100%;height:170px;object-fit:cover;object-position:top;border-radius:8px' })))) : h('div', { class: 'muted small' }, 'No moment marked yet.')))
      : h('div', { class: 'card empty' }, 'No report yet. Start one with “New report”.'));
}

// ------------------------------------------------------------------------------------------ run -> video picker
// The run picked stays in the page's address (#/misses/new?run=…), so going back from one of its videos returns to its video list instead of an
// empty picker (the admin, 2026-10-30); "New report" opens on the run picked last on this browser.
const LAST_RUN = 'loupe-misses-last-run';
export async function pick(params = {}) {
  const d = await api('GET', '/api/runs');
  const real = d.runs.filter((r) => r.kind === 'run');
  const out = h('div');
  const sel = h('select', { style: 'min-width:360px' }, h('option', { value: '' }, 'Select a run…'),
    real.map((r) => h('option', { value: r.run }, `${r.lib ? r.lib.toUpperCase() + ' · ' : ''}${r.run} · ${r.mode || ''}${r.submode ? '/' + r.submode : ''} · ${r.model || ''} · ${r.videos} videos`)));
  const showRun = async () => {
    try { history.replaceState(null, '', sel.value ? '#/misses/new?run=' + encodeURIComponent(sel.value) : '#/misses/new'); } catch (_) { /* no history */ }
    try { if (sel.value) localStorage.setItem(LAST_RUN, sel.value); } catch (_) { /* private mode */ }
    if (!sel.value) { out.replaceChildren(); return; }
    const r = await api('GET', '/api/runs/' + encodeURIComponent(sel.value));
    out.replaceChildren(h('div', { class: 'grid g4' }, r.videos.map((v) => h('a', { class: 'card', style: 'color:inherit;text-decoration:none', href: `#/misses/work?run=${encodeURIComponent(r.run)}&video=${v.video}` },
      h('b', {}, 'Video ' + v.video), ' ', v.status ? status(v.status) : null,
      h('div', { class: 'muted small' }, `${v.versions.some((x) => x.role === 'generated') ? v.versions.filter((x) => x.role === 'generated').length + ' generated take(s)' : v.versions.filter((x) => x.role !== 'original').length + ' version(s)'}` + (v.capcut_project ? ` · ${v.capcut_project}` : '') + (v.credits ? ` · ${v.credits} cr` : '')),
      v.qc ? h('div', { class: 'small', style: 'margin-top:4px' }, v.qc.slice(0, 120)) : null))));
  };
  sel.addEventListener('change', showRun);
  let last = '';
  try { last = localStorage.getItem(LAST_RUN) || ''; } catch (_) { /* private mode */ }
  const start = [params.run, last].find((x) => x && real.some((r) => r.run === x));
  if (start) { sel.value = start; showRun(); }
  return h('div', {}, head('New report', 'Choose the run that was executed by the pipeline, then the video the report is about. A report can also be in words only: the idea, what could have been better, what the AI agents missed.',
      h('a', { class: 'btn', href: '#/misses/work?run=&video=' }, 'Report without a video'), h('a', { class: 'btn', href: '#/misses' }, '← Reports')),
    h('div', { class: 'card' }, sel), h('div', { style: 'margin-top:14px' }, out));
}

// ------------------------------------------------------------------------------------------ the workspace
// One page, one step: any number of frames side by side (two to start with), each from either video, each with its own time and called WRONG or RIGHT by the
// admin, marks drawn straight on any of them, and one button that saves the moment as a single image (the frames, a zoom of each marked place carrying its marks while its frame stays clean, the explanation burned in).
function guessVersions(versions) {       // only the wrong version is guessed (the first attempt's generated take of this video); the right one is always the user's choice
  const own = versions.filter((v) => v.role !== 'original');      // the original video is never the default
  const attempts = own.filter((v) => v.kind === 'attempt');
  const clean = (v) => /with-original-audio\.mp4$|clean\.mp4$|trimmed\.mp4$/.test(v.label);
  const firstA = own.find((v) => v.role === 'generated' && v.attempt === 1) || attempts.find((v) => /^a1 ·/.test(v.label) && clean(v)) || attempts.find((v) => /^a1 ·/.test(v.label)) || own[0];
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
  // other generations can stand on either side: the generated takes of the run's other videos (and, from the chooser below, of another run's videos)
  for (const ov of runInfo ? runInfo.videos.filter((v) => v.video !== (qs.video || '')) : []) {
    for (const v of ov.versions) if (v.role !== 'original') versions.push({ ...v, group: 'other', video: ov.video, label: `video ${ov.video} · ${v.label}` });
  }
  const g = guessVersions(vinfo.versions);
  const state = { wrong: miss ? miss.wrong_path : (g.wrong ? g.wrong.path : ''), fixed: miss ? miss.fixed_path : '' };     // neither video is required: '' = none, else its path (the right version is never guessed)
  const addVersion = (v) => { if (!versions.some((x) => x.path === v.path)) versions.push(v); };
  if (miss) { if (miss.wrong_path) addVersion({ path: miss.wrong_path, label: miss.wrong_label || miss.wrong_path.split(/[\\/]/).slice(-2).join('/') }); if (miss.fixed_path) addVersion({ path: miss.fixed_path, label: miss.fixed_label || miss.fixed_path.split(/[\\/]/).slice(-2).join('/') }); }
  // By default only the video each attempt generated at Higgsfield is listed: its clean video and preview are made from it, and the prompt is filled from that
  // attempt's request. Every other file of the video (conformed, trimmed, copies, previews, deliverables) is one tick away. The original video the generation
  // was made from (as the run recorded it) is always listed, and so are the generated takes of the run's other videos and of another run's video added below.
  const hasGenerated = vinfo.versions.some((v) => v.role === 'generated');
  let showAll = !hasGenerated;      // a video with no recognisable generated take (an older run, a folder of files) lists every file
  const isShown = (v) => {
    if (v.path === state.wrong || v.path === state.fixed || v.role === 'original') return true;
    if (v.group === 'other') return v.role === 'generated' || (showAll && hasGenerated);      // the run's other videos: their generated takes, every file with the tick
    if (v.group) return true;                                                                 // taken from another run on purpose
    return showAll || !v.role || v.role === 'generated' || v.role === 'client-fix';
  };
  const ws = { shapes: [], tool: 'arrow', editing: null, draft: null, past: [], future: [] };      // every mark belongs to one frame (shape.fid); nothing is ever copied for you
  const addMark = (shape) => setMarks([...ws.shapes, shape]);
  const syncHist = () => { undoBtn.disabled = !ws.past.length; redoBtn.disabled = !ws.future.length; };
  const setMarks = (next) => { ws.past.push(ws.shapes); if (ws.past.length > 200) ws.past.shift(); ws.shapes = next; ws.future = []; redrawAll(); syncHist(); };   // a new mark or a clear: one undoable step
  const resetMarks = (next) => { ws.shapes = next; ws.past = []; ws.future = []; redrawAll(); syncHist(); };                                                          // a fresh start (after a save, or an edit loaded)
  const undo = () => { if (!ws.past.length) return; ws.future.push(ws.shapes); ws.shapes = ws.past.pop(); redrawAll(); syncHist(); };
  const redo = () => { if (!ws.future.length) return; ws.past.push(ws.shapes); ws.shapes = ws.future.pop(); redrawAll(); syncHist(); };

  // ---- the two versions
  const label = (v) => v.role === 'generated'
    ? `${v.label.replace(/\s*[·/]?\s*raw\.mp4$/, '')} · generated at Higgsfield${v.delivered ? ' · delivered (its clean video and preview)' : ''}`
    : v.label + (v.kind === 'fixed-lib' ? '  (client fix)' : '');
  const mkSel = (key) => {       // this video's takes first, then the original video, then the other generations, each group under its own heading
    const opt = (v) => h('option', { value: v.path, selected: v.path === state[key] }, label(v));
    const shown = versions.filter(isShown);
    const groups = [...new Set(shown.filter((v) => v.group).map((v) => v.group))];
    const sel = h('select', { style: 'max-width:100%' },
      h('option', { value: '', selected: !state[key] }, key === 'fixed' ? 'No right version (optional)' : 'No video (a report in words only)'),
      shown.filter((v) => !v.group && v.role !== 'original').map(opt),
      shown.some((v) => v.role === 'original') ? h('optgroup', { label: 'The original video (the source the generation was made from)' }, shown.filter((v) => v.role === 'original').map(opt)) : null,
      groups.map((gr) => h('optgroup', { label: gr === 'other' ? 'Other generations: the other videos of this run' : 'Other generations: ' + gr }, shown.filter((v) => v.group === gr).map(opt))));
    sel.addEventListener('change', () => { state[key] = sel.value; setSources(); });
    return sel;
  };
  let wrongSel = mkSel('wrong'), fixedSel = mkSel('fixed');
  const rebuildSelects = () => { const a = mkSel('wrong'), b = mkSel('fixed'); wrongSel.replaceWith(a); fixedSel.replaceWith(b); wrongSel = a; fixedSel = b; };
  const allBox = h('input', { type: 'checkbox', checked: showAll, disabled: !hasGenerated });
  allBox.addEventListener('change', () => { showAll = allBox.checked; rebuildSelects(); });
  const mkAdd = (key) => {       // a file uploaded or typed can stand on either side
    const fileIn = h('input', { type: 'file', accept: 'video/*,image/*', style: 'display:none' });
    const prog = h('span', { class: 'muted small' });
    fileIn.addEventListener('change', async () => {
      if (!fileIn.files[0]) return;
      try {
        const r = await upload(fileIn.files[0], (p) => (prog.textContent = Math.round(p * 100) + '%'));
        addVersion({ path: r.path, label: 'uploaded · ' + r.label, kind: 'upload' }); state[key] = r.path; prog.textContent = ''; rebuildSelects(); setSources(); toast('Uploaded');
      } catch (e) { toast(e.message, true); prog.textContent = ''; }
    });
    const pathIn = h('input', { placeholder: 'or a path to a video file', size: 34 });
    const use = h('button', { class: 'btn sm', onclick: async () => {
      try { await api('GET', '/api/probe?path=' + encodeURIComponent(pathIn.value)); addVersion({ path: pathIn.value, label: pathIn.value.split(/[\\/]/).slice(-2).join('/') + ' (path)' }); state[key] = pathIn.value; rebuildSelects(); setSources(); }
      catch (_) { /* toasted: outside the folders Loupe may read? add it in Settings */ } } }, 'Use');
    return h('div', { class: 'row', style: 'margin-top:6px' }, h('button', { class: 'btn sm', onclick: () => fileIn.click() }, key === 'fixed' ? 'Upload the right video…' : 'Upload a video…'), fileIn, prog, pathIn, use);
  };

  // ---- other generations from another run: the generated takes of one of its videos join both pickers
  const runSel = h('select', { style: 'max-width:100%' }, h('option', { value: '' }, 'Another run…'));
  const vidSel = h('select', { disabled: true }, h('option', { value: '' }, 'its video…'));
  const otherBox = h('div', { class: 'row', style: 'margin-top:6px;display:none' }, runSel, vidSel);
  let otherRun = null;
  const otherBtn = h('button', { class: 'btn sm', type: 'button', title: 'lists the other runs; the generated takes of the video you choose there are added to both pickers', onclick: async () => {
    otherBox.style.display = otherBox.style.display === 'none' ? '' : 'none';
    if (runSel.options.length > 1) return;
    try {
      const d = await api('GET', '/api/runs');
      d.runs.filter((r) => r.kind === 'run' && r.run !== qs.run).forEach((r) => runSel.append(h('option', { value: r.run }, `${r.lib ? r.lib.toUpperCase() + ' · ' : ''}${r.run} · ${r.mode || ''}${r.submode ? '/' + r.submode : ''} · ${r.videos} videos`)));
    } catch (e) { toast(e.message, true); }
  } }, 'Other generations from another run…');
  runSel.addEventListener('change', async () => {
    vidSel.replaceChildren(h('option', { value: '' }, 'its video…')); vidSel.disabled = true; otherRun = null;
    if (!runSel.value) return;
    try {
      otherRun = await api('GET', '/api/runs/' + encodeURIComponent(runSel.value));
      otherRun.videos.forEach((v) => { const n = v.versions.filter((x) => x.role === 'generated').length; vidSel.append(h('option', { value: v.video }, `video ${v.video}` + (n ? ` · ${n} generated take(s)` : ''))); });
      vidSel.disabled = false;
    } catch (e) { toast(e.message, true); }
  });
  vidSel.addEventListener('change', () => {
    const v = otherRun && otherRun.videos.find((x) => x.video === vidSel.value);
    if (!v) return;
    const gr = `${otherRun.run} · video ${v.video}`;
    const gen = v.versions.filter((x) => x.role === 'generated');
    let n = 0;
    for (const x of gen.length ? gen : v.versions.filter((y) => y.role !== 'original')) {
      if (!versions.some((y) => y.path === x.path)) { versions.push({ ...x, group: gr, video: v.video, label: `${gr} · ${x.label}` }); n++; }
    }
    rebuildSelects();
    toast(n ? `Added ${n} take(s) of ${gr} to both pickers` : `Nothing new to add from ${gr}`);
  });

  // ---- the prompts of the two boxes (the flawed version's, the correct version's, or an Examples sample's one box): kept in the record and read whole by the keeper and the prompt writers on request; never burned into the image, never
  // compared by the app (the agents judge what changed). Each is filled from the run's request files of the generations its frames come from (POST /api/misses/prompt-boxes), or pasted.
  const mono = 'min-height:110px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px';
  const wrongPrompt = h('textarea', { placeholder: 'The flawed version’s prompt, or Video A’s example prompt (filled from the generations its frames come from). Optional.', style: mono, value: miss ? miss.wrong_prompt || '' : '' });
  const fixedPrompt = h('textarea', { placeholder: 'The correct version’s prompt, or Video B’s example prompt (filled from the generations its frames come from). Optional.', style: mono, value: miss ? miss.fixed_prompt || '' : '' });
  const promptNote = h('input', { value: miss ? miss.prompt_note || '' : '', placeholder: 'What in the prompt made the difference, in your words (optional, one line)', style: 'width:100%' });
  // The prompt boxes (the user, 2026-10-29): the flawed version's box holds the prompt of each generation the frames called Wrong come from, the correct
  // version's box the prompt of each generation the frames called Right come from (from whichever video), and an Examples sample's one box the prompt of
  // each generation its Example frames come from; every sample of the report counts, a box holds no other generation, and a box no frame feeds stays
  // empty ("if there's only 3 right frames or 3 wrong frames, or 3 examples then only fill in one prompt"). Loupe writes them (POST
  // /api/misses/prompt-boxes, the same code that keeps the saved boxes in step and that the judge's records are made from): each prompt under the line
  // that says what it is, which seconds of which video it generated (read from how the take was joined) and which frames it made. A field fills itself
  // while it is empty or still holds what Loupe wrote, and follows the frames; once you type in it, it is yours until you press Fill. Saved with the
  // field: its generations (wrong_segments / fixed_segments), so the judge is told, sample by sample, which prompt made which frames.
  const PROMPT_FIELD = { wrong: wrongPrompt, fixed: fixedPrompt };
  const VNAME = { wrong: 'Video A', fixed: 'Video B' };
  const secs = (x) => String(+(+x).toFixed(3));
  const gens = { wrong: [], fixed: [] };       // the generations behind each box Loupe wrote: saved with it
  const typed = { wrong: false, fixed: false };
  const autoText = { wrong: null, fixed: null };      // what Loupe last wrote into each field
  for (const key of ['wrong', 'fixed']) {      // a saved box Loupe wrote from a joined take keeps following the frames
    const list = (miss && miss[key + '_segments']) || [];
    if (list.length) { autoText[key] = miss[key + '_prompt'] || ''; gens[key] = list; }
  }
  const onScreen = () => frames.filter((f) => state[f.src]);
  const examplesOnly = () => { const fr = onScreen(); return fr.length > 0 && fr.every((f) => f.role === 'example'); };
  const oneBox = () => { if (!examplesOnly()) return ''; const v = ['wrong', 'fixed'].filter((s) => onScreen().some((f) => f.src === s)); return v.length === 1 ? v[0] : ''; };
  const twoVideoExamples = () => frames.length > 0 && frames.every((f) => f.role === 'example') && new Set(frames.map((f) => f.src)).size > 1;
  const otherFrames = () => (miss ? (miss.moments || []) : []).filter((m) => !ws.editing || m.moment_id !== ws.editing.moment_id).flatMap((m) => m.frames || []);
  const columnOf = (f) => (f.role === 'wrong' ? 'wrong' : f.role === 'right' ? 'fixed' : f.src);      // the box a frame feeds
  // what a box holds, from the frames that feed it (the same rule names it for the judge): 'flawed', 'correct', 'example' (or several), '' with no
  // frame at all (a report in words only: each box its own video's whole take), 'unused' when other frames exist but none feeds it
  const fieldRole = (key) => {
    const all = [...otherFrames(), ...onScreen()];
    if (!all.length) return '';
    const roles = [...new Set(all.filter((f) => columnOf(f) === key).map((f) => ({ wrong: 'flawed', right: 'correct', example: 'example' })[f.role]))];
    return roles.length ? roles.join('+') : 'unused';
  };
  const LABEL = { wrong: 'The flawed version’s prompt (no frame yet: the prompt of each generation the frames called Wrong come from; in an Examples sample of Video A, its example prompt)', fixed: 'The correct version’s prompt (no frame yet: the prompt of each generation the frames called Right come from, from either video; in an Examples sample of Video B, its example prompt)' };
  const ROLE_LABEL = {
    flawed: 'The flawed version’s prompt (the prompt of each generation the frames called Wrong come from: they generated the flawed version)',
    correct: 'The correct version’s prompt (the prompt of each generation the frames called Right come from, from either video: they generated the correct version)',
    example: (key) => `The example prompt of ${VNAME[key]} (the prompt of each generation of ${VNAME[key]} the Example frames come from: a suggestion of what could have changed or been done another way, neither a bad generation nor a correct one; an Examples sample has this one box)`,
  };
  const UNUSED_LABEL = { wrong: 'The flawed version’s prompt: not used (no frame of this report’s samples is called Wrong, so no generation feeds this box; text saved or typed here is kept as it is: empty it if no sample needs it)', fixed: 'The correct version’s prompt: not used (no frame of this report’s samples is called Right, so no generation feeds this box; text saved or typed here is kept as it is: empty it if no sample needs it)' };
  const labelFor = (key) => {
    const role = fieldRole(key);
    if (role === 'unused') return UNUSED_LABEL[key];
    if (!role) return LABEL[key];
    return role.split('+').map((r) => (r === 'example' ? ROLE_LABEL.example(key) : ROLE_LABEL[r])).join(' + ');
  };
  const promptLabel = { wrong: h('label', { class: 'f', style: 'margin-top:0' }, LABEL.wrong), fixed: h('label', { class: 'f', style: 'margin-top:0' }, LABEL.fixed) };      // named by what they hold once the frames are drawn
  const promptCell = { wrong: null, fixed: null };      // the two boxes (built below): a box no frame feeds is hidden while it holds no text
  let promptGrid = null;
  const syncPromptLabels = () => {
    for (const key of ['wrong', 'fixed']) {
      const t = labelFor(key);
      if (promptLabel[key].textContent !== t) promptLabel[key].textContent = t;
      if (promptCell[key]) promptCell[key].style.display = fieldRole(key) === 'unused' && !PROMPT_FIELD[key].value.trim() ? 'none' : '';
    }
    if (promptGrid) promptGrid.style.gridTemplateColumns = promptCell.wrong.style.display === 'none' || promptCell.fixed.style.display === 'none' ? '1fr' : '';
  };
  let boxes = null, boxesSeq = 0, followTimer = null;
  const readBoxes = async (loud) => {      // what Loupe writes in each box for the frames of every sample of the report (nothing is saved)
    const seq = ++boxesSeq;
    const body = { miss_id: miss ? miss.id : null, run: qs.run || '', video: qs.video || '', wrong_path: state.wrong || '', fixed_path: state.fixed || '',
      frames: onScreen().map((f) => ({ src: f.src, role: f.role, t: f.t })), editing: ws.editing ? ws.editing.moment_id : null };
    let d;
    try { d = await api('POST', '/api/misses/prompt-boxes', body, !loud); } catch (e) { d = { error: e.message }; }
    if (seq === boxesSeq) boxes = d;
    return d;
  };
  const follows = (key) => !typed[key] && (!PROMPT_FIELD[key].value.trim() || PROMPT_FIELD[key].value === autoText[key]);
  const applyBoxes = (d) => {       // the fields that are empty or still hold what Loupe wrote follow the frames; the labels say what each holds
    if (d && !d.error) {
      for (const key of ['wrong', 'fixed']) {
        const b = d[key] || {};
        if (autoText[key] === null && PROMPT_FIELD[key].value.trim() && PROMPT_FIELD[key].value.trim() === (b.text || '').trim()) autoText[key] = PROMPT_FIELD[key].value;
        if (!follows(key)) continue;
        if (!b.used) {        // no frame feeds this box: what Loupe wrote there goes
          if (PROMPT_FIELD[key].value) PROMPT_FIELD[key].value = '';
          autoText[key] = ''; gens[key] = [];
          continue;
        }
        if (!b.text) continue;        // a frame whose generation cannot be told: the field is left as it is (Fill says why)
        gens[key] = b.segments || []; autoText[key] = b.text;
        if (PROMPT_FIELD[key].value !== b.text) PROMPT_FIELD[key].value = b.text;
      }
    }
    syncPromptLabels();
  };
  const followFrames = () => { syncPromptLabels(); clearTimeout(followTimer); followTimer = setTimeout(() => { readBoxes(false).then(applyBoxes); }, 150); };
  const followNow = async () => { clearTimeout(followTimer); applyBoxes(await readBoxes(false)); };
  for (const key of ['wrong', 'fixed']) PROMPT_FIELD[key].addEventListener('input', () => { typed[key] = true; });
  const genWords = (s) => `${s.clip || VNAME[s.src]}${s.attempt ? ' a' + s.attempt : ''} (generation ${s.gen || 1} of ${s.of || 1}, ${(s.spans && s.spans.length ? s.spans : [[s.t_from, s.t_to]]).map(([a, b]) => `${secs(a)}–${secs(b)} s`).join(' and ')} of ${VNAME[s.src] || 'its video'})`;
  const fillPrompt = (key) => h('button', { class: 'btn sm', type: 'button', title: `writes the prompt of each generation the frames feeding this box come from (the flawed version’s box: the frames called Wrong; the correct version’s box: the frames called Right, from either video; an Examples sample’s one box: its Example frames), read from the request files in the run and from how the take was joined, every sample of the report counted, and no other generation; a box no frame feeds stays empty; the original video has no prompt. The field then follows the frames again until you type in it.`, onclick: async () => {
    if (!state.wrong && !state.fixed) { toast('Pick a video first (Video A or Video B): each box is filled from the generations its frames come from', true); return; }
    const d = await readBoxes(true);
    if (!d || d.error) { toast((d && d.error) || 'The run could not be read: paste the prompt', true); return; }
    const b = d[key] || {};
    if (!b.used) { toast(key === 'wrong' ? 'No frame is called Wrong, so no generation feeds the flawed version’s box: it stays empty (only the boxes the frames feed are filled)' : 'No frame is called Right, so no generation feeds the correct version’s box: it stays empty (only the boxes the frames feed are filled)', true); return; }
    if (!b.text) { toast((b.problems || []).join(' · ') || (qs.run && qs.video ? 'No request file with a prompt was found for that version: paste it' : 'This report has no run and video: paste the prompt'), true); return; }
    typed[key] = false; gens[key] = b.segments || []; autoText[key] = b.text; PROMPT_FIELD[key].value = b.text; syncPromptLabels();
    const what = { flawed: 'the frames called Wrong', correct: 'the frames called Right', example: 'the Example frames' };
    const who = (b.role || '').split('+').map((r) => what[r]).filter(Boolean).join(' and ') || 'the frames';
    const segs = b.segments || [];
    toast((segs.length ? `Filled the prompt of each generation ${who} come from: ${segs.map(genWords).join('; ')}` : 'Filled the prompt of the generation ' + who + ' come from')
      + ((d.notes || []).length ? '. ' + d.notes.join(' · ') : '') + ((b.problems || []).length ? '. Not tied to a prompt: ' + b.problems.join(' · ') : ''), !!(b.problems || []).length);
  } }, key === 'wrong' ? 'Fill from the run (the flawed version, or Video A’s examples)' : 'Fill from the run (the correct version, or Video B’s examples)');
  const promptsBox = h('details', { style: 'margin-top:10px', open: !!(miss && (miss.wrong_prompt || miss.fixed_prompt || miss.prompt_note)) },
    h('summary', { class: 'muted small', style: 'cursor:pointer' }, 'The prompts (optional: the flawed version’s, the correct version’s, or an Examples sample’s one box; kept in the record for the QC keeper and the prompt writers, never burned into the image)'),
    (promptGrid = h('div', { class: 'grid g2', style: 'margin-top:6px' },
      (promptCell.wrong = h('div', {}, promptLabel.wrong, wrongPrompt, h('div', { class: 'row', style: 'margin-top:4px' }, fillPrompt('wrong')))),
      (promptCell.fixed = h('div', {}, promptLabel.fixed, fixedPrompt, h('div', { class: 'row', style: 'margin-top:4px' }, fillPrompt('fixed')))))),
    h('label', { class: 'f' }, 'What in the prompt made the difference (optional, your words)'), promptNote,
    h('div', { class: 'muted small', style: 'margin-top:4px' }, 'The flawed version’s box holds the prompt of each generation the frames called Wrong come from, the correct version’s box the prompt of each generation the frames called Right come from (from either video: a right frame can come from another generation of the same video), and an Examples sample’s one box the prompt of each generation its Example frames come from, an example: a suggestion of what could have changed or been done another way, neither a bad generation nor a correct one. Every sample of the report counts, a box holds no other generation, and a box no frame feeds stays empty: frames called Right only fill the correct version’s box alone, frames called Wrong only the flawed version’s alone. Which seconds each generation made is read from how the take was joined (a clip cut around another one, a trim, an insert); each prompt sits under the line that says what it is, which seconds of which video it generated and which frames it made. The fields fill themselves and follow the frames until you type in them; Fill writes them again. The database keeps what each prompt is, and the judge and the agents read it so, sample by sample. Nothing is compared or diffed by the app: the keeper reads the prompts whole when it asks for them, the prompt writers read them for a sample they judged relevant, and each decides what changed.'));

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
  // a new report's product is the words its request gives its product images (below) or what the admin types; never a registry heading (the admin,
  // 2026-11-03: S44 kept "Celsius products", the first heading of its run's registry, beside a Neuro carton)
  const product = h('input', { value: miss ? miss.product : '', size: 22, placeholder: 'the product and its details: form, size, opening, natural posture' });
  let productTyped = false;      // a new report takes the product its request used (below) until the admin types one
  product.addEventListener('input', () => { productTyped = true; });

  const useSample = h('input', { type: 'checkbox', checked: miss ? miss.product_sample !== false : true });
  const sampleBox = h('div', { class: 'small', style: 'margin-top:12px' });
  // the band's images (the admin, 2026-10-30): exactly the images sent with the generation of Video A's take, verified from the run's records, every
  // one ticked by default; what the admin ticks is kept with the report (sample_files) and goes into its images (a tick kept on a client's original
  // stands for the file sent from it).  The admin, 2026-11-03: when Video B is another generation, the images sent with it too, each naming the video
  // it was sent with; each sample's band shows those of the videos its frames come from
  let assetsTouched = false;
  let assetBoxes = [];      // {cb, path} of every image offered
  const pickedAssets = () => assetBoxes.filter((x) => x.cb.checked).map((x) => x.path);
  const loadSample = async () => {
    if (!qs.run || !qs.video) { sampleBox.replaceChildren(); return; }
    const wl = state.wrong || '', fl = state.fixed || '';      // the paths: the server reads each take's attempt from it
    try {
      const d = await api('GET', `/api/generation-assets?run=${encodeURIComponent(qs.run)}&video=${encodeURIComponent(qs.video)}&wrong=${encodeURIComponent(wl)}${fl ? '&fixed=' + encodeURIComponent(fl) : ''}`, undefined, true);
      const two = d.assets.some((a) => (a.videos || []).length > 0);      // Video B was made by another generation: each image names the video it was sent with
      const chosen = new Set(((miss && miss.sample_files) || []).map((x) => x.path));
      const isChosen = (a) => chosen.has(a.path) || (!!a.original && chosen.has(a.original)) || (a.paths || []).some((p) => chosen.has(p));
      const anyChosen = d.assets.some(isChosen);
      if (chosen.size && !anyChosen) assetsTouched = true;      // ticked for another take: this take's automatic pick stands until you tick
      const ticked = (a) => (anyChosen ? isChosen(a) : a.suggested);
      if (!miss && !productTyped) {      // a new report's product: the words of Video A's product images (Video B's when Video A was sent none)
        const pa = d.assets.filter((a) => a.kind === 'product' && a.words), own = pa.filter((a) => !(a.videos || []).length || a.videos.includes('A'));
        const w = [...new Set((own.length ? own : pa).map((a) => a.words))]; if (w.length) product.value = w.join(' + ');
      }
      assetBoxes = [];
      if (!d.assets.length) { sampleBox.replaceChildren(h('span', { class: 'muted' }, (fl ? 'No image sent with the generation of either video is recorded in its run' : 'No image sent with this generation is recorded in its run') + ' (its request is not in the ledger or changed since it was submitted, its uploads name no file, or which attempt this version is cannot be told), so the description takes the full width of the bottom band.')); return; }
      const cells = d.assets.map((a) => {
        const cb = h('input', { type: 'checkbox', checked: ticked(a), 'data-asset': a.path });
        assetBoxes.push({ cb, path: a.path });
        cb.addEventListener('change', () => {
          assetsTouched = true;
          if (pickedAssets().length > 9) { cb.checked = false; toast('At most nine images in the band', true); }
          useSample.checked = pickedAssets().length > 0;
        });
        return h('label', { class: 'card', style: 'padding:6px;width:150px;margin:0;cursor:pointer', title: `${a.kind ? a.kind + ' · ' : ''}${a.source} · the file sent: ${a.name}${a.original_name ? ' · made from ' + a.original_name : ' · its original file is not recorded'}${a.prepared ? ' · prepared: ' + a.prepared : ''}${a.request ? ' · ' + a.request : ''}` },
          h('img', { src: a.image_url, alt: a.label, style: 'width:100%;height:110px;object-fit:contain;border-radius:6px;background:#cdd0d6' }),
          h('div', { class: 'row', style: 'gap:4px;margin-top:4px;align-items:flex-start;flex-wrap:nowrap' }, cb, h('span', { class: 'small' }, a.label)),
          h('div', { class: 'muted small' }, (a.kind ? a.kind + ' · ' : '') + a.source));
      });
      sampleBox.replaceChildren(h('div', {},
        h('label', { class: 'small' }, useSample, two ? ' Show the images sent with the generations of Video A and Video B in the saved images (right half of the bottom band)' : ' Show the images sent with this generation in the saved image (right half of the bottom band)'),
        h('div', { class: 'muted small', style: 'margin:4px 0 6px' }, two
          ? 'The images each video’s generation was sent with its job, as its run recorded them (the request the ledger submitted, unchanged since, and the file uploaded under each media id); nothing else is offered. Each names the video it was sent with, and a picture both were sent is one tile (Videos A and B). Each sample shows those of the videos its frames come from. All are ticked: untick one to leave it out of the saved images, tick it again to bring it back.'
          : 'The images this generation was sent with its job, as the run recorded them (the request the ledger submitted, unchanged since, and the file uploaded under each media id); nothing else is offered. All are ticked: untick one to leave it out of the saved image, tick it again to bring it back.'),
        h('div', { class: 'row', style: 'gap:10px;flex-wrap:wrap;align-items:stretch' }, cells)));
    } catch (_) { sampleBox.replaceChildren(); }
  };

  async function saveReport() {
    await followNow();          // the boxes Loupe writes for the frames on screen and every other sample of the report, before they are saved
    const body = { run: qs.run || '', video: qs.video || '', title: title.value, explanation: miss ? miss.explanation : details.value, kinds: kinds.get(), model: model.value, mode: mode.value, product: product.value, product_sample: useSample.checked,
      wrong_path: state.wrong, fixed_path: state.fixed, wrong_label: (versions.find((v) => v.path === state.wrong) || {}).label || '', fixed_label: (versions.find((v) => v.path === state.fixed) || {}).label || '',
      wrong_prompt: wrongPrompt.value, fixed_prompt: fixedPrompt.value, prompt_note: promptNote.value,
      wrong_segments: follows('wrong') ? gens.wrong : [], fixed_segments: follows('fixed') ? gens.fixed : [], ...(assetsTouched ? { sample_files: pickedAssets() } : {}) };
    const wasNoRight = !!miss && !miss.fixed_path, nSaved = miss ? miss.moments.length : 0;
    miss = miss ? (await api('PATCH', '/api/misses/' + miss.id, body)).miss : (await api('POST', '/api/misses', body)).miss;
    assetsTouched = false;
    if (wasNoRight && miss.fixed_path && nSaved) toast(`The right version was attached to ${nSaved} saved moment(s) at the same seconds: open Edit on each to line it up and mark the right side.`);
    history.replaceState(null, '', `#/misses/work?run=${encodeURIComponent(qs.run || '')}&video=${qs.video || ''}&miss=${miss.id}`);
    return miss;
  }

  // ---- the frames: any number (two to start with), each from either video, each with its own time, each called WRONG or RIGHT by the admin.
  // The marks are kept in the picture's own 0..1 coordinates and belong to one frame (shape.fid).
  const COLOR = { wrong: '#ff2d2d', right: '#16a34a', example: '#3b6edc' };
  const SRC_NAME = { wrong: 'Video A · as generated', fixed: 'Video B · correct version' };
  const srcName = (key) => {       // what each video is, on a frame's own picker: the original video, a take of another video or run, else its side's own name
    const v = versions.find((x) => x.path === state[key]);
    const what = !v ? '' : v.role === 'original' ? 'original video' : v.group === 'other' ? 'video ' + v.video : v.group || '';
    return what ? (key === 'wrong' ? 'Video A · ' : 'Video B · ') + what : SRC_NAME[key];
  };
  const MAX_FRAMES = 12;
  const frames = [];
  let fseq = 0;
  const playersBox = h('div', { class: 'players' });
  const marksOf = (f) => ws.shapes.filter((x) => x.fid === f.id);
  const pic = (f) => {                       // where the picture really is inside the (letterboxed) video element, or the still that stands in for it
    const cw = f.cv.clientWidth, ch = f.cv.clientHeight, vw = f.failed ? f.still.naturalWidth : f.video.videoWidth, vh = f.failed ? f.still.naturalHeight : f.video.videoHeight;
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
  const clampT = (f, t) => Math.min(Math.max(0, +t || 0), (f.failed ? f.dur : f.video.duration) || 1e9);
  const showStill = (f) => {       // a frame whose video cannot play: the still at its time, cut by Loupe (a short wait while a slider moves)
    clearTimeout(f.stillTimer);
    f.stillTimer = setTimeout(() => { if (f.failed && state[f.src]) f.still.src = frameUrl(state[f.src], f.t); }, 120);
  };
  const refresh = () => {
    if (frames[0]) range.value = frames[0].t;
    for (const f of frames) { f.slider.value = f.t; if (document.activeElement !== f.stamp) f.stamp.value = fmtT(f.t); if (f.failed) showStill(f); }
    followFrames();
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
    if (frames[0].video.paused) { frames.forEach((f) => { f.video.currentTime = clampT(f, frames[0].t + f.gap); }); frames.forEach((f) => { if (!f.failed) f.video.play().catch(() => {}); }); playAll.textContent = 'Pause all'; } else pauseAll();
  } }, 'Play all');
  const tick = () => { if (frames.some((f) => !f.video.paused)) { frames.forEach((f) => { f.t = f.video.currentTime; }); refresh(); } };
  range.addEventListener('input', () => { pauseAll(); seekAll(range.value); });
  // ---- end of the transport

  const failFrame = (f) => {       // the browser cannot play this file (a codec, a damaged file): Loupe cuts its frames as stills, so it can still be moved, marked and saved
    if (f.failed || !state[f.src]) return;
    f.failed = true; f.video.style.display = 'none'; f.still.style.display = 'block';
    api('GET', '/api/probe?path=' + encodeURIComponent(state[f.src]), undefined, true)
      .then((p) => { f.dur = +p.duration || 0; f.slider.max = f.dur || 10; if (f === frames[0]) range.max = f.dur || 10; f.t = clampT(f, f.t); refresh(); })
      .catch(() => showStill(f));
    showStill(f);
    toast('This video does not play in the browser: its frames are shown as stills cut by Loupe; you can still move, mark and save them', true);
  };
  const wireDraw = (f) => {
    f.ro = new ResizeObserver(() => redraw(f)); f.ro.observe(f.wrap);
    f.video.addEventListener('loadedmetadata', () => {
      if (!f.video.videoWidth) { failFrame(f); return; }       // sound only: the picture cannot be decoded here
      redraw(f); f.slider.max = f.video.duration || 10; if (f === frames[0]) range.max = f.video.duration || 10;
      const t = clampT(f, f.t);      // a time past the end of a shorter video (a take of another video or run) is brought back to its last frame
      if (t !== f.t) { f.t = t; f.video.currentTime = t; refresh(); }
    });
    f.video.addEventListener('error', () => failFrame(f));
    f.still.addEventListener('load', () => redraw(f));
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
    f.srcSel.options[0].textContent = srcName('wrong'); f.srcSel.options[1].textContent = srcName('fixed');
  };
  const loadFrame = (f) => {
    f.failed = false; f.video.style.display = ''; f.still.style.display = 'none'; f.still.removeAttribute('src'); clearTimeout(f.stillTimer);
    const path = state[f.src]; if (path) { f.video.src = videoUrl(path); f.video.currentTime = f.t; } else f.video.removeAttribute('src');
  };
  const mkFrame = (src, role, t) => {
    const f = { id: ++fseq, src, role, t, gap: 0 };
    f.video = h('video', { preload: 'auto', playsinline: true }); f.video.muted = true;
    f.cv = h('canvas', { class: 'pc', title: 'Draw here: red marks on a frame called Wrong, green on Right, blue on Example' });
    f.still = h('img', { alt: '', style: 'display:none;width:100%;max-height:62vh;object-fit:contain;background:#000' });
    f.wrap = h('div', { class: 'vwrap' }, f.video, f.still, f.cv);
    f.num = h('b', { class: 'fnum' });
    f.btnWrong = h('button', { class: 'btn sm rolebtn wrong', title: 'This frame shows the miss', onclick: () => setRole(f, 'wrong') }, 'Wrong');
    f.btnRight = h('button', { class: 'btn sm rolebtn right', title: 'This frame shows how it should be', onclick: () => setRole(f, 'right') }, 'Right');
    f.btnExample = h('button', { class: 'btn sm rolebtn example', title: 'An Example: a suggestion of what could have changed or how it could have been done another way, neither a bad generation nor a correct one (blue marks). A sample with Example frames has only Examples.', onclick: () => setRole(f, 'example') }, 'Example');
    f.srcSel = h('select', { class: 'srcsel', title: 'which video this frame is taken from' }, h('option', { value: 'wrong' }, srcName('wrong')), h('option', { value: 'fixed' }, srcName('fixed')));
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
    syncCorrect(); syncRight(); syncMix(); followFrames();
  };
  const addFrame = (src, role, t) => {
    const f = mkFrame(src, role, t);
    frames.push(f); f.gap = f.t - frames[0].t;
    loadFrame(f); drawPlayers(); refresh(); setTimeout(() => redraw(f), 250);
    return f;
  };
  const dispose = (f) => { clearTimeout(f.stillTimer); f.video.pause(); f.ro.disconnect(); f.video.removeAttribute('src'); f.video.load(); };
  const removeFrame = (f) => {
    pauseAll();
    if (marksOf(f).length) setMarks(ws.shapes.filter((x) => x.fid !== f.id));
    dispose(f); frames.splice(frames.indexOf(f), 1);
    frames.forEach((o) => { o.gap = o.t - frames[0].t; });
    if (frames[0]) range.max = frames[0].video.duration || 10;
    drawPlayers(); refresh();
  };
  const setRole = (f, role) => { f.role = role; styleFrame(f); redraw(f); syncCorrect(); syncMix(); followFrames(); };
  const setFrameSrc = (f, src) => {
    if (src === 'fixed' && !state.fixed) { toast('This report has no right version video yet: choose one above first', true); f.srcSel.value = f.src; return; }
    f.src = src; loadFrame(f); syncMix(); followFrames();
  };
  let syncRight = () => {};
  let hadFixed = false;      // a right version was chosen before: its frame is offered once, not again after the admin removed it
  const setSources = () => {      // the version pickers changed
    for (const f of frames.filter((x) => !state[x.src])) removeFrame(f);      // frames of a video that is gone fall away
    const justChosen = !!state.fixed && !hadFixed;      // chosen just now: its frame comes even when the first frame was marked already (marks belong to their frame and stay)
    hadFixed = !!state.fixed;
    if (state.fixed && frames.length === 1 && !ws.editing && frames[0].src === 'wrong' && frames[0].role === 'wrong' && (justChosen || !ws.shapes.length)) addFrame('fixed', 'right', frames[0].t);      // two frames to start with
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
  const saveReportBtn = h('button', { class: 'btn', title: 'saves the report’s own fields where they stand (what the miss or scenario is about, the kinds, the videos, the product sample, the prompts, model, mode, product) and rebuilds every saved sample image of it, without marking a new moment; a moment’s frames, marks and texts are saved with “Update moment”', onclick: async () => {
    saveReportBtn.disabled = true;
    try {
      const n = miss ? miss.moments.length : 0;
      await saveReport(); await refreshMiss();
      toast(n ? `Report saved where it stands; its ${n} sample image(s) were rebuilt` : 'Report saved');
    } catch (_) { /* toasted */ } finally { saveReportBtn.disabled = false; }
  } }, 'Save report changes');
  const leaveEdit = () => { ws.editing = null; saveBtn.textContent = 'Mark this moment'; cancelEdit.style.display = 'none'; };
  async function saveMoment() {
    pauseAll();
    if (mixed()) { toast('A sample is either a miss with its correct version, or Examples only: call every frame Example, or none', true); syncMix(); return; }
    if (twoVideoExamples()) { toast('An Examples sample takes all its frames from one video, so it has one prompt box: take every frame from Video A (or every one from Video B)', true); syncMix(); return; }
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
    + (m.sample_kind === 'miss-only' ? ' · miss example, no right frame' : m.sample_kind === 'scenario'
      ? (m.frames.length && m.frames.every((f) => f.role === 'right') ? ' · the correct version only' : ' · scenario') : '');
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
  // the user, 2026-10-28: a sample is a miss with its correct version, or Examples only; never both (the server refuses a mix too)
  const mixed = () => frames.some((f) => f.role === 'example') && frames.some((f) => f.role !== 'example');
  const mixNote = h('div', { class: 'small', style: 'display:none;margin-top:6px;color:#b45309;font-weight:600' }, 'This sample mixes Example frames with Wrong or Right frames: A sample is either a miss with its correct version (frames called Wrong and Right) or Examples only, never both: call every frame Example, or none (an Example is a suggestion of what could have changed or how it could have been done another way, neither a bad generation nor a correct one).');
  const oneVideoNote = h('div', { class: 'small', style: 'display:none;margin-top:6px;color:#b45309;font-weight:600' }, 'This sample takes its Example frames from Video A and Video B: an Examples sample takes all its frames from one video and has one prompt box, that video’s (the prompt of each generation of it its Example frames come from, each under the seconds it made). Take every frame from one video; only a sample of frames called Wrong and Right may take them from both videos: each frame’s generation feeds the flawed or the correct version’s box by what the frame is called.');
  const syncMix = () => { mixNote.style.display = mixed() ? '' : 'none'; oneVideoNote.style.display = twoVideoExamples() ? '' : 'none'; };
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
      h('div', { class: 'row' }, qs.run ? h('a', { class: 'btn', href: '#/misses/new?run=' + encodeURIComponent(qs.run) }, '← Videos of this run') : null,
        h('a', { class: 'btn', href: '#/misses' }, '← Reports'))),
    h('div', { class: 'card' },
      h('div', { class: 'grid g2' },
        h('div', {}, h('label', { class: 'f', style: 'margin-top:0' }, 'Video A: the version as generated (optional; or the original video, or another generation)'), wrongSel, mkAdd('wrong')),
        h('div', {}, h('label', { class: 'f', style: 'margin-top:0' }, 'Video B: the right version (optional: a regeneration that came out right, a QC fix, your feedback, the client’s update, the original video, or another generation)'), fixedSel, mkAdd('fixed'))),
      hasGenerated ? h('div', { class: 'muted small', style: 'margin-top:6px' }, h('label', {}, allBox, ' Also list the other files of this video and of the run’s other videos (conformed, trimmed, copies, previews, deliverables). Normally only the video each attempt generated at Higgsfield is listed: its clean video and preview are made from it, and the prompt below is filled from that attempt’s request.')) : null,
      h('div', { class: 'row', style: 'margin-top:6px' }, otherBtn, h('span', { class: 'muted small' }, 'Either side can be any video: the pickers also list the original video the generation was made from (as the run recorded it) and the generated takes of the run’s other videos; another run’s takes are one choice away. Each frame’s tag in the saved image says which it is.')),
      otherBox,
      promptsBox),
    h('div', { class: 'card' },
      h('div', { class: 'tools' }, tb('arrow', 'Arrow'), tb('circle', 'Circle'), tb('rect', 'Box'), tb('text', 'Text'),
        undoBtn, redoBtn,
        h('button', { class: 'btn sm', onclick: () => { if (ws.shapes.length) setMarks([]); } }, 'Clear marks'),
        h('span', { class: 'muted small', style: 'margin-left:10px' }, 'You choose what each frame is: Wrong (red marks), Right (green) or Example (blue: a suggestion of what could have changed or been done another way, neither a bad generation nor a correct one; a sample is either Wrong and Right frames or Examples only), which video it comes from, and its own time. Everything here is optional: a report can have no right version, no wrong frame, even no frames. Nothing is marked or copied for you.')),
      playersBox, mixNote, oneVideoNote,
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
      h('div', { class: 'row', style: 'margin-top:14px' }, saveBtn, cancelEdit, saveReportBtn, saveNote,
        h('span', { class: 'muted small' }, 'One click: saves the report, cuts every frame at its own time, and builds one image: the frames tagged WRONG, RIGHT or EXAMPLE, a zoom of each frame you marked with a circle, arrow or box that carries your marks (the frame above it stays clean; a frame with no zoom keeps its marks), and this text.'))),
    result,
    h('div', { class: 'card' }, h('h2', {}, 'Saved moments'), moments));
  // a report opened from one of its samples (&moment=), or a report with a single sample, comes back as it stands: that sample's frames, their times and
  // roles, its marks and its words, loaded to be changed in place (“Update moment”), never started again from scratch
  if (miss && miss.moments.length) {
    const want = qs.moment ? String(qs.moment).replace(/^S/i, '') : (miss.moments.length === 1 ? String(miss.moments[0].moment_id) : '');
    const m0 = want && miss.moments.find((m) => String(m.moment_id) === want);
    if (m0) setTimeout(() => loadMoment(m0), 0);
  }
  return root;
}

function lightbox(src) {
  const ov = h('div', { class: 'overlay', onclick: () => ov.remove() }, h('img', { src, style: 'max-width:100%;border-radius:10px;margin:auto;background:#fff' }));
  document.body.append(ov);
}
