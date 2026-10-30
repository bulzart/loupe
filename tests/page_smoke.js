// Loads the report page (web/v_misses.js) against a tiny fake DOM and fake API, with no browser and no login, and clicks through it:
// two frames to start with, add and remove a frame, call a frame Wrong or Right, draw on a frame, save, and check what would be sent.
// node tests/page_smoke.js
const fs = require('fs');
const path = require('path');
const web = path.join(__dirname, '..', 'web');
const read = (f) => fs.readFileSync(path.join(web, f), 'utf8').split('\r\n').join('\n');

const { El, walk, click } = require('./fakedom');

// ---- the page's own source, with its imports replaced by small stand-ins
const uiSrc = read('ui.js').replace(/^export /gm, '').replace(/^import .*$/gm, '');
const sent = [];
let withMoment = false;
let noRight = false;
let withOthers = false;      // the run also has video 04, and video 03 has an original
let withSample = false;      // the video's request names a product
let withSegs = false;        // the picked take was joined from two generations (clips)
let withSavedSegs = false;   // the report was saved with the flawed version's box written from a take of two generations
const savedMoment = { moment_id: 5, id: 'S5', image_url: '/x?a=5', updated: 'u', details: 'the pack is right early', correct_note: 'c', has_right: true,
  frames: [{ src: 'wrong', role: 'right', t: 0.5, desc: 'as generated' }, { src: 'wrong', role: 'wrong', t: 3.0, desc: 'as generated' }, { src: 'fixed', role: 'right', t: 3.25, desc: 'correct version' }],
  shapes: [{ type: 'rect', frame: 1, x1: 0.1, y1: 0.1, x2: 0.4, y2: 0.4 }] };
// the boxes endpoint, by the rule of misses.prompt_boxes: a frame called Wrong feeds the flawed version's box, Right the correct version's, an Example the
// box of its video; each box holds the generation each of its frames comes from (the take joined from c1 0-6.1 s and c2 6.1-12.433 s), and no other
const TAKE = () => (withSegs ? [{ clip: 'c1', attempt: 1, request: 'video-03-c1-request.json', t_from: 0, t_to: 6.1, spans: [[0, 6.1]], gen: 1, of: 2, prompt: 'P-C1 the first generation' },
  { clip: 'c2', attempt: 1, request: 'video-03-c2-request.json', t_from: 6.1, t_to: 12.433, spans: [[6.1, 12.433]], gen: 2, of: 2, prompt: 'P-C2 the second generation' }] : null);
const PHRASE = { wrong: 'a prompt of the flawed version', right: 'a prompt of the correct version', example: 'an example prompt' };
const mockBoxes = (body) => {
  const out = { ok: true, notes: [] };
  const fr = (body.frames || []).map((f, i) => ({ ...f, n: i + 1 }));
  for (const col of ['wrong', 'fixed']) {
    const feed = fr.filter((f) => (f.role === 'wrong' ? 'wrong' : f.role === 'right' ? 'fixed' : f.src) === col);
    const role = [...new Set(feed.map((f) => ({ wrong: 'flawed', right: 'correct', example: 'example' })[f.role]))].join('+');
    if (!feed.length) { out[col] = { used: !fr.length, role: '', text: fr.length ? '' : 'P-ONE', segments: [], problems: [] }; continue; }
    const take = TAKE();
    if (!take) { out[col] = { used: true, role, text: 'P-ONE', segments: [], problems: [] }; continue; }
    const gens = [];
    for (const f of feed) {
      const s = take.find((x) => x.spans.some(([a, b]) => f.t >= a && f.t < b));
      if (!s) continue;
      let g = gens.find((x) => x.src === f.src && x.clip === s.clip);
      if (!g) { g = { ...s, src: f.src, frames: [] }; gens.push(g); }
      g.frames.push(f);
    }
    gens.sort((a, b) => (a.src !== b.src ? (a.src === 'wrong' ? -1 : 1) : a.gen - b.gen));
    const text = gens.map((g) => `This is ${PHRASE[g.frames[0].role]}: it generated second ${g.spans[0][0]} to second ${g.spans[0][1]} of ${g.src === 'wrong' ? 'Video A' : 'Video B'} (generation ${g.gen} of ${g.of} · clip ${g.clip}), and made ${g.frames.map((f) => 'Frame ' + f.n).join(' and ')} of this sample.\n${g.prompt}`).join('\n\n');
    out[col] = { used: true, role, text, segments: gens.map(({ frames: _f, ...s }) => s), problems: [] };
  }
  return out;
};
const api = async (method, url, body) => {
  sent.push({ method, url, body });
  if (url === '/api/runs/_') throw new Error('no run');
  if (url === '/api/moments/5' && method === 'PATCH') return { moment: savedMoment };
  if (withMoment && url.startsWith('/api/misses/1') && method === 'GET') return { miss: { id: 1, title: 'T', explanation: '', kinds: [], model: 'm', mode: 'x', product: '', wrong_path: 'A.mp4', fixed_path: noRight ? '' : 'B.mp4', moments: [savedMoment], product_sample: true } };
  if (url === '/api/runs') return { runs: [{ run: 'R', kind: 'run', lib: 'awl', videos: 2 }, { run: 'R2', kind: 'run', lib: 'atp', mode: 'analyze', submode: 'recreate', videos: 1 }] };
  if (url === '/api/runs/R2') return { run: 'R2', videos: [{ video: '07', versions: [{ path: 'R2-7.mp4', label: 'a1 · raw.mp4', kind: 'attempt', attempt: 1, role: 'generated' },
    { path: 'R2-7c.mp4', label: 'clean.mp4', kind: 'output', role: 'processed' }, { path: 'R2-o.mp4', label: 'original · x.mp4 (V02, the source clip as given)', kind: 'original', role: 'original' }] }] };
  if (url.startsWith('/api/runs/')) return { run: 'R', model: 'wan3_0_prime', mode: 'analyze', submode: 'recreate', products: [], videos: [{ video: '03', versions: [
    { path: 'A.mp4', label: 'a1 · raw.mp4', kind: 'attempt', attempt: 1, role: 'generated', delivered: false }, { path: 'A2.mp4', label: 'a2 · raw.mp4', kind: 'attempt', attempt: 2, role: 'generated', delivered: true },
    { path: 'A2c.mp4', label: 'a2 · with-original-audio.mp4', kind: 'attempt', attempt: 2, role: 'processed' }, { path: 'B.mp4', label: 'capcut-preview.mp4', kind: 'output', role: 'processed' },
    ...(withOthers ? [{ path: 'O.mp4', label: 'original · src.mp4 (V05, the source clip as given)', kind: 'original', role: 'original' }] : [])] },
    ...(withOthers ? [{ video: '04', versions: [{ path: 'D4.mp4', label: 'a1 · raw.mp4', kind: 'attempt', attempt: 1, role: 'generated' }, { path: 'D4c.mp4', label: 'clean.mp4', kind: 'output', role: 'processed' },
      { path: 'D4o.mp4', label: 'original · y.mp4 (V09, the source clip as given)', kind: 'original', role: 'original' }] }] : [])] };
  if (url === '/api/settings') return { kinds: [] };
  if (url.startsWith('/api/misses/1') && method === 'GET') return { miss: { id: 1, title: 'T', explanation: '', kinds: [], model: 'm', mode: 'x', product: '', wrong_path: 'A.mp4', fixed_path: noRight ? '' : 'B.mp4', moments: [], product_sample: true,
    ...(withSavedSegs ? { wrong_prompt: 'the text as saved', wrong_segments: [{ src: 'wrong', clip: 'c1', attempt: 1, request: 'video-03-c1-request.json', t_from: 0, t_to: 6.1, prompt: 'SAVED-C1' },
      { src: 'wrong', clip: 'c2', attempt: 1, request: 'video-03-c2-request.json', t_from: 6.1, t_to: 12.433, prompt: 'SAVED-C2' }] } : {}) } };
  if (url.startsWith('/api/misses/1') && method === 'PATCH') return { miss: { id: 1, title: 'T', explanation: '', kinds: [], wrong_path: 'A.mp4', fixed_path: noRight ? '' : 'B.mp4', moments: [], product_sample: true } };
  if (url.endsWith('/moments')) return { moment: { id: 'S1', moment_id: 1, image_url: '/x?a=1', updated: 'u' } };
  if (url === '/api/misses/prompt-boxes') return mockBoxes(body);
  if (url.startsWith('/api/request-prompt')) return withSegs ? { prompt: 'both prompts, each under its line', note: '2 generations joined in clip order', request: 'video-03-c1-request.json, video-03-c2-request.json', attempt: 0,
    segments: [{ clip: 'c1', attempt: 1, request: 'video-03-c1-request.json', file: 'c1/a1/trimmed.mp4', t_from: 0, t_to: 6.1, prompt: 'P-C1 the first generation' },
      { clip: 'c2', attempt: 1, request: 'video-03-c2-request.json', file: 'c2/a1/trimmed.mp4', t_from: 6.1, t_to: 12.433, prompt: 'P-C2 the second generation' }] } : { prompt: 'P-ONE', request: 'video-03-request.json', attempt: 1 };
  if (url.startsWith('/api/generation-assets')) return { assets: withSample ? [
    { path: 'P.png', name: 'x.png', label: 'Image 2 · the sealed test can', source: "the client's original", asset_id: 'A14', request: 'video-03-request.json', suggested: true, image_url: '/x.png' },
    { path: 'H.png', name: 'h.png', label: 'Image 1 · C04 head', source: 'as the uploads record names it', asset_id: '', request: 'video-03-request.json', suggested: false, image_url: '/h.png' }] : [] };
  return {};
};
const code = read('v_misses.js').replace(/^import .*$/gm, '').replace(/^export (async )?function/gm, '$1function') + '\nreturn { work };';
const toastStub = (m, err) => { if (err) console.log('  (toast) ' + m); };
const uiNoToast = uiSrc.replace(/function toast[\s\S]*?\n}\n/, '');
const { work } = new Function('api', 'upload', 'videoUrl', 'frameUrl', 'toastStub', uiNoToast + '\nconst toast = toastStub;\n' + code)(api, async () => ({}), (p) => '/video?p=' + p, (p, t) => '/frame?p=' + p + '&t=' + t, toastStub);

let fails = 0;
const ok = (name, cond, info = '') => { console.log((cond ? '  ok   ' : '  FAIL ') + name + (cond ? '' : '  ' + info)); if (!cond) fails++; };

(async () => {
  const root = await work({ run: 'R', video: '03', miss: '1' });
  const players = () => walk(root, (e) => e.className === 'players')[0];
  const frameEls = () => players().children.filter((e) => e.className.split(/\s+/).includes('player') && e.style.display !== 'none' && walk(e, (x) => x.tag === 'canvas').length);
  const btn = (text, within = root) => walk(within, (e) => e.tag === 'button' && e.text === text);
  ok('the page loads and starts with two frames when a right version is chosen', frameEls().length === 2, String(frameEls().length));
  ok('frame 1 is Wrong (red) and frame 2 is Right (green) to start with', frameEls()[0].className.includes('role-wrong') && frameEls()[1].className.includes('role-right'), frameEls().map((e) => e.className).join(' | '));
  ok('each frame has its own time box', walk(root, (e) => e.tag === 'input' && e.className.includes('stamp')).length === 2);
  click(btn('+ Add a frame')[0]);
  ok('"Add a frame" adds a third frame', frameEls().length === 3);
  ok('the new frame copies the role of the last one and sits 0.2 s later', frameEls()[2].className.includes('role-right') && walk(frameEls()[2], (e) => e.tag === 'input' && e.className.includes('stamp'))[0].value === '0:00.20', walk(frameEls()[2], (e) => e.tag === 'input' && e.className.includes('stamp'))[0].value);
  click(btn('Wrong', frameEls()[2])[0]);
  ok('a frame can be called Wrong instead', frameEls()[2].className.includes('role-wrong') && !frameEls()[2].className.includes('role-right'));
  const stamp2 = walk(frameEls()[1], (e) => e.tag === 'input' && e.className.includes('stamp'))[0];
  stamp2.value = '0:01.50'; (stamp2.listeners.change || []).forEach((f) => f());
  ok('typing a time in a frame\'s box moves that frame only', stamp2.value === '0:01.50' && walk(frameEls()[0], (e) => e.tag === 'input' && e.className.includes('stamp'))[0].value === '0:00.00');
  // draw a box on frame 3 (index 2)
  const cv = walk(frameEls()[2], (e) => e.tag === 'canvas')[0];
  (cv.listeners.pointerdown || []).forEach((f) => f({ preventDefault() {}, clientX: 100, clientY: 140, pointerId: 1 }));
  click(btn('Box')[0]);
  (cv.listeners.pointerdown || []).forEach((f) => f({ preventDefault() {}, clientX: 100, clientY: 140, pointerId: 1 }));
  (cv.listeners.pointermove || []).forEach((f) => f({ clientX: 220, clientY: 350 }));
  (cv.listeners.pointerup || []).forEach((f) => f({}));
  const save = btn('Mark this moment')[0];
  save.disabled = false;
  await Promise.all((save.listeners.click || []).map((f) => f()));
  const post = sent.find((s) => s.method === 'POST' && s.url.endsWith('/moments'));
  ok('saving sends every frame with its source, role and own time', post && post.body.frames.length === 3
    && post.body.frames[0].src === 'wrong' && post.body.frames[0].role === 'wrong' && post.body.frames[1].src === 'fixed' && post.body.frames[1].role === 'right'
    && Math.abs(post.body.frames[1].t - 1.5) < 1e-6 && post.body.frames[2].role === 'wrong', JSON.stringify(post && post.body.frames));
  ok('the box drawn on frame 3 is sent as a mark of frame index 2, with no side and nothing copied to the others', post && post.body.shapes.length === 1 && post.body.shapes[0].frame === 2
    && post.body.shapes[0].type === 'rect' && !('side' in post.body.shapes[0]) && !('fid' in post.body.shapes[0]), JSON.stringify(post && post.body.shapes));
  click(btn('✕', frameEls()[2])[0]);
  ok('removing a frame takes it away', frameEls().length === 2);
  click(btn('✕', frameEls()[1])[0]);
  ok('one frame left shows the card beside it', frameEls().length === 1 && players().style['--cols'] === '2', String(players().style['--cols']));
  // editing a saved moment brings its frames back, each with its own time and role, and its marks on the right frame
  withMoment = true; sent.length = 0;
  const root2 = await work({ run: 'R', video: '03', miss: '1' });
  const frames2 = () => walk(root2, (e) => e.className === 'players')[0].children.filter((e) => e.className.split(/\s+/).includes('player') && e.style.display !== 'none' && walk(e, (x) => x.tag === 'canvas').length);
  click(walk(root2, (e) => e.tag === 'button' && e.text === 'Edit')[0]);
  const stamps = () => frames2().map((e) => walk(e, (x) => x.tag === 'input' && x.className.includes('stamp'))[0].value);
  ok('Edit brings back the three frames with their own times', frames2().length === 3 && stamps().join(' ') === '0:00.50 0:03.00 0:03.25', stamps().join(' '));
  ok('and their roles: the early frame of the video is Right, the later one Wrong', frames2().map((e) => e.className.includes('role-right') ? 'R' : 'W').join('') === 'RWR');
  await Promise.all(walk(root2, (e) => e.tag === 'button' && e.text === 'Update moment').flatMap((b) => (b.listeners.click || []).map((f) => f())));
  const patch = sent.find((x) => x.method === 'PATCH' && x.url === '/api/moments/5');
  ok('updating sends the same frames and the mark on frame index 1', patch && patch.body.frames.map((f) => f.role[0] + f.t).join(' ') === 'r0.5 w3 r3.25' && patch.body.shapes.length === 1 && patch.body.shapes[0].frame === 1, JSON.stringify(patch && patch.body));
  // nothing is required: no right version, an Example frame, no frame at all, the idea, what could be better and what the agents missed
  withMoment = false; noRight = true; sent.length = 0;
  const root3 = await work({ run: 'R', video: '03', miss: '1' });
  const visible3 = () => walk(root3, (e) => e.className === 'players')[0].children.filter((e) => e.className.split(/\s+/).includes('player') && e.style.display !== 'none');
  const frames3 = () => visible3().filter((e) => walk(e, (x) => x.tag === 'canvas').length);
  const b3 = (t) => walk(root3, (e) => e.tag === 'button' && e.text === t);
  ok('with no right version the page starts with one frame and a card that says the right frame is optional', frames3().length === 1 && visible3().length === 2 && visible3()[1].text.includes('Optional'), String(visible3().length));
  ok('and saving is never blocked', !b3('Mark this moment')[0].disabled);
  click(b3('Example')[0]);
  ok('a frame can be called Example (blue)', frames3()[0].className.includes('role-example') && !frames3()[0].className.includes('role-wrong'));
  const area = (start) => walk(root3, (e) => e.tag === 'textarea' && (e.attrs.placeholder || '').startsWith(start))[0];
  area('The idea').value = 'The hand holds the can to the end.'; area('What could have been better').value = 'State the grip at each beat.'; area('What the AI agents missed').value = 'They treated the hold as optional.';
  await Promise.all((b3('Mark this moment')[0].listeners.click || []).map((f) => f()));
  const postN = sent.find((x) => x.method === 'POST' && x.url.endsWith('/moments'));
  ok('an Example frame and the three scenario texts are sent', postN && postN.body.frames.length === 1 && postN.body.frames[0].role === 'example' && postN.body.idea.startsWith('The hand') && postN.body.better.startsWith('State') && postN.body.missed.startsWith('They treated'), JSON.stringify(postN && postN.body));
  click(btn('✕', frames3()[0])[0]);
  ok('the only frame can be removed: the report is then in words only', frames3().length === 0 && visible3().length === 1 && visible3()[0].text.includes('No frames'), String(frames3().length));
  sent.length = 0;
  area('The idea').value = 'Words only.';
  await Promise.all((b3('Mark this moment')[0].listeners.click || []).map((f) => f()));
  const postW = sent.find((x) => x.method === 'POST' && x.url.endsWith('/moments'));
  ok('and it saves with no frame at all', postW && Array.isArray(postW.body.frames) && postW.body.frames.length === 0 && postW.body.shapes.length === 0 && postW.body.idea === 'Words only.', JSON.stringify(postW && postW.body));
  const root4 = await work({ run: '', video: '' });
  const players4 = walk(root4, (e) => e.className === 'players')[0];
  ok('a report with no run and no video opens with no frame, a card, and nothing to add a frame from', players4.children.filter((e) => walk(e, (x) => x.tag === 'canvas').length).length === 0
    && walk(root4, (e) => e.tag === 'button' && e.text === '+ Add a frame')[0].disabled === true);
  // the picker lists the video each attempt generated at Higgsfield (its clean video and preview are made from it); every other file is one tick away
  const root5 = await work({ run: 'R', video: '03' });
  const selects5 = () => walk(root5, (e) => e.tag === 'select');
  const optTexts = (s) => s.options.map((o) => o.text).join(' | ');
  ok('a new report lists only the generated take of each attempt, the delivered one named', optTexts(selects5()[0]) === 'No video (a report in words only) | a1 · generated at Higgsfield | a2 · generated at Higgsfield · delivered (its clean video and preview)', optTexts(selects5()[0]));
  ok('Video A starts on the first attempt', selects5()[0].options.find((o) => o.selected).text.startsWith('a1 ·'));
  const allLabel = walk(root5, (e) => e.tag === 'label' && e.text.includes('Also list the other files'))[0];
  ok('a tick offers the other files of the video', !!allLabel);
  const allBox5 = walk(allLabel, (e) => e.tag === 'input')[0];
  allBox5.checked = true; (allBox5.listeners.change || []).forEach((f) => f());
  ok('ticking it lists every file, the generated ones still named as such', selects5()[0].options.length === 5 && optTexts(selects5()[0]).includes('a2 · with-original-audio.mp4') && optTexts(selects5()[0]).includes('capcut-preview.mp4'), optTexts(selects5()[0]));
  // choosing the right version after the first frame was marked still brings its frame, once; the mark stays on the first frame
  withMoment = false; noRight = true; sent.length = 0;
  const root6 = await work({ run: 'R', video: '03', miss: '1' });
  const frames6 = () => walk(root6, (e) => e.className === 'players')[0].children.filter((e) => e.className.split(/\s+/).includes('player') && e.style.display !== 'none' && walk(e, (x) => x.tag === 'canvas').length);
  const cv6 = walk(frames6()[0], (e) => e.tag === 'canvas')[0];
  (cv6.listeners.pointerdown || []).forEach((f) => f({ preventDefault() {}, clientX: 100, clientY: 140, pointerId: 1 }));
  click(walk(root6, (e) => e.tag === 'button' && e.text === 'Box')[0]);
  (cv6.listeners.pointerdown || []).forEach((f) => f({ preventDefault() {}, clientX: 100, clientY: 140, pointerId: 1 }));
  (cv6.listeners.pointermove || []).forEach((f) => f({ clientX: 220, clientY: 350 }));
  (cv6.listeners.pointerup || []).forEach((f) => f({}));
  ok('a report with no right version starts with one frame, and a box is drawn on it', frames6().length === 1);
  const fixedSel6 = walk(root6, (e) => e.tag === 'select')[1];
  fixedSel6.value = 'A2.mp4'; (fixedSel6.listeners.change || []).forEach((f) => f());
  ok('choosing the right version after marking the first frame still adds a Right frame from Video B', frames6().length === 2 && frames6()[1].className.includes('role-right') && walk(frames6()[1], (e) => e.tag === 'select')[0].value === 'fixed', String(frames6().length));
  fixedSel6.value = 'A.mp4'; (fixedSel6.listeners.change || []).forEach((f) => f());
  ok('choosing another right version afterwards does not add a third frame', frames6().length === 2);
  sent.length = 0;
  await Promise.all(walk(root6, (e) => e.tag === 'button' && e.text === 'Mark this moment').flatMap((b) => (b.listeners.click || []).map((f) => f())));
  const post6 = sent.find((x) => x.method === 'POST' && x.url.endsWith('/moments'));
  ok('the mark stays on the first frame, which stays Wrong; the new frame is Right from Video B', post6 && post6.body.frames.length === 2 && post6.body.frames[0].role === 'wrong' && post6.body.frames[0].src === 'wrong' && post6.body.frames[1].role === 'right' && post6.body.frames[1].src === 'fixed'
    && post6.body.shapes.length === 1 && post6.body.shapes[0].frame === 0, JSON.stringify(post6 && post6.body));
  // either side can be any video: the original video, a take of another video of the run, a take of another run's video
  withOthers = true; withMoment = false; noRight = true; sent.length = 0;
  const root7 = await work({ run: 'R', video: '03', miss: '1' });
  const sels7 = () => walk(root7, (e) => e.tag === 'select');
  const groupsOf = (s) => s.children.filter((c) => c.tag === 'optgroup').map((gr) => gr.attrs.label + ': ' + gr.children.map((o) => o.text).join(' | ')).join(' || ');
  ok('both pickers list the original video and the generated takes of the run’s other videos, each under its own heading', [0, 1].every((i) => groupsOf(sels7()[i]).includes('The original video (the source the generation was made from): original · src.mp4 (V05, the source clip as given)')
    && groupsOf(sels7()[i]).includes('Other generations: the other videos of this run: video 04 · a1 · generated at Higgsfield')), groupsOf(sels7()[0]));
  ok('another video’s other files wait for the tick, and its original is never listed', !optTexts(sels7()[0]).includes('video 04 · clean.mp4') && !optTexts(sels7()[0]).includes('y.mp4'), optTexts(sels7()[0]));
  ok('Video A still starts on the first attempt of this video', sels7()[0].options.find((o) => o.selected).text.startsWith('a1 ·'));
  const frames7 = () => walk(root7, (e) => e.className === 'players')[0].children.filter((e) => e.className.split(/\s+/).includes('player') && e.style.display !== 'none' && walk(e, (x) => x.tag === 'canvas').length);
  const srcOpts = (fe) => walk(fe, (e) => e.tag === 'select')[0].options.map((o) => o.text);
  const fixed7 = sels7()[1];
  fixed7.value = 'O.mp4'; (fixed7.listeners.change || []).forEach((f) => f());
  ok('the original can be Video B: its frame comes, and the frame’s own picker names it', frames7().length === 2 && srcOpts(frames7()[1])[1] === 'Video B · original video', frames7().length + ' ' + srcOpts(frames7()[frames7().length - 1]).join(' | '));
  const wrong7 = sels7()[0];
  wrong7.value = 'D4.mp4'; (wrong7.listeners.change || []).forEach((f) => f());
  ok('a take of another video can be Video A', srcOpts(frames7()[0])[0] === 'Video A · video 04', srcOpts(frames7()[0]).join(' | '));
  sent.length = 0;
  await Promise.all(walk(root7, (e) => e.tag === 'button' && e.text === 'Mark this moment').flatMap((b) => (b.listeners.click || []).map((f) => f())));
  const patch7 = sent.find((x) => x.method === 'PATCH' && x.url.startsWith('/api/misses/1'));
  ok('saving sends both picks with their labels', patch7 && patch7.body.wrong_path === 'D4.mp4' && patch7.body.fixed_path === 'O.mp4' && patch7.body.wrong_label === 'video 04 · a1 · raw.mp4'
    && patch7.body.fixed_label === 'original · src.mp4 (V05, the source clip as given)', JSON.stringify(patch7 && patch7.body));
  const otherBtn7 = walk(root7, (e) => e.tag === 'button' && e.text === 'Other generations from another run…')[0];
  await Promise.all((otherBtn7.listeners.click || []).map((f) => f()));
  const runSel7 = sels7()[2];
  ok('another run can be chosen (this run is not offered again)', runSel7.options.map((o) => o.value).join(',') === ',R2', runSel7.options.map((o) => o.value).join(','));
  runSel7.value = 'R2'; await Promise.all((runSel7.listeners.change || []).map((f) => f()));
  const vidSel7 = sels7()[3];
  vidSel7.value = '07'; (vidSel7.listeners.change || []).forEach((f) => f());
  ok('its video’s generated takes join both pickers under a heading of their own, its original does not', [0, 1].every((i) => groupsOf(sels7()[i]).includes('Other generations: R2 · video 07: R2 · video 07 · a1 · generated at Higgsfield') && !groupsOf(sels7()[i]).includes('x.mp4')), groupsOf(sels7()[1]));
  // a new report takes the product its request used as its product, until the admin types one
  withSample = true; withOthers = false; withMoment = false; noRight = false;
  const root8 = await work({ run: 'R', video: '03' });
  await new Promise((r) => setTimeout(r, 0));
  const prodIn = walk(root8, (e) => e.tag === 'input' && (e.attrs.placeholder || '').startsWith('the product and its details'))[0];
  ok('a new report takes the product its request used, in the request\'s own words', prodIn && prodIn.value === 'the sealed test can', prodIn && prodIn.value);
  ok('and shows its picture beside the words', walk(root8, (e) => e.tag === 'img' && e.attrs.alt === 'Image 2 · the sealed test can').length === 1);
  // the product sample is ticked from every image the generation used; the report's own fields are saved where they stand, with no new moment
  const boxes8 = walk(root8, (e) => e.tag === 'input' && e.attrs['data-asset']);
  ok('every image the generation used is offered, the automatic pick ticked', boxes8.length === 2 && boxes8[0].checked === true && !boxes8[1].checked, boxes8.map((b) => b.attrs['data-asset'] + ':' + b.checked).join(' '));
  boxes8[0].checked = false; (boxes8[0].listeners.change || []).forEach((f) => f());
  boxes8[1].checked = true; (boxes8[1].listeners.change || []).forEach((f) => f());
  sent.length = 0;
  await Promise.all(walk(root8, (e) => e.tag === 'button' && e.text === 'Save report changes').flatMap((b) => (b.listeners.click || []).map((f) => f())));
  const rep8 = sent.find((x) => (x.method === 'POST' || x.method === 'PATCH') && /^\/api\/misses(\/1)?$/.test(x.url));
  ok('“Save report changes” saves the report with the ticked image and marks no moment', rep8 && JSON.stringify(rep8.body.sample_files) === '["H.png"]' && !sent.some((x) => x.url.endsWith('/moments')), JSON.stringify(sent.map((x) => x.method + ' ' + x.url)));
  withSample = false;
  // a sample opened from the list (&moment=) comes back as it stands, with nothing to click
  withMoment = true; noRight = false; sent.length = 0;
  const root9 = await work({ run: 'R', video: '03', miss: '1', moment: '5' });
  await new Promise((r) => setTimeout(r, 0));
  const frames9 = walk(root9, (e) => e.className === 'players')[0].children.filter((e) => e.className.split(/\s+/).includes('player') && e.style.display !== 'none' && walk(e, (x) => x.tag === 'canvas').length);
  ok('a sample opened where it stands loads its frames, their times and its words, ready to update', frames9.length === 3
    && walk(root9, (e) => e.tag === 'button' && e.text === 'Update moment').length === 1
    && walk(root9, (e) => e.tag === 'textarea' && (e.attrs.placeholder || '').startsWith('Explain this miss'))[0].value === 'the pack is right early', String(frames9.length));
  withMoment = false;
  // the prompt boxes (the user, 2026-10-29): Loupe writes them (POST /api/misses/prompt-boxes, mocked below by the same rule): the flawed version's box
  // holds the prompt of each generation the frames called Wrong come from, the correct version's box each generation the frames called Right come from
  // (from either video), an Examples sample's one box each generation its Example frames come from, and no other; a box no frame feeds stays empty. The
  // form sends the frames on screen, follows what comes back until the admin types in a field, and saves the generations with each field Loupe wrote
  const settle = () => new Promise((r) => setTimeout(r, 220));
  withSegs = true; sent.length = 0;
  const root10 = await work({ run: 'R', video: '03', miss: '1' });
  await settle();
  const field = (start) => walk(root10, (e) => e.tag === 'textarea' && (e.attrs.placeholder || '').startsWith(start))[0];
  const fieldA = () => field('The flawed version’s prompt, or'), fieldB = () => field('The correct version’s prompt, or');
  const boxCalls = () => sent.filter((x) => x.method === 'POST' && x.url === '/api/misses/prompt-boxes');
  ok('one prompt field per side, none per generation', walk(root10, (e) => e.tag === 'textarea' && /prompt/i.test(e.attrs.placeholder || '')).length === 2 && !walk(root10, (e) => !!(e.attrs && e.attrs['data-seg'])).length);
  ok('the form asks Loupe for the boxes with the frames on screen, the report and both videos', boxCalls().length > 0 && boxCalls().slice(-1)[0].body.miss_id === 1
    && boxCalls().slice(-1)[0].body.wrong_path === 'A.mp4' && boxCalls().slice(-1)[0].body.frames.length === 2, JSON.stringify(boxCalls().slice(-1)[0] || {}).slice(0, 300));
  ok('the flawed version’s field fills itself with the prompt of the generation the Wrong frame comes from (frame 1 at 0 s: generation 1 only)',
    fieldA().value.startsWith('This is a prompt of the flawed version: it generated second 0 to second 6.1 of Video A (generation 1 of 2 · clip c1), and made Frame 1 of this sample.\nP-C1')
    && !fieldA().value.includes('P-C2'), fieldA().value);
  ok('the correct version’s field holds the prompt behind the Right frame, from Video B', fieldB().value.includes('of Video B (generation 1 of 2 · clip c1') && fieldB().value.startsWith('This is a prompt of the correct version'), fieldB().value);
  ok('each box is labelled by what it holds', !!walk(root10, (e) => e.tag === 'label' && (e.textContent || '').startsWith('The flawed version’s prompt (the prompt of each generation the frames called Wrong come from'))[0]
    && !!walk(root10, (e) => e.tag === 'label' && (e.textContent || '').startsWith('The correct version’s prompt (the prompt of each generation the frames called Right come from, from either video'))[0],
    walk(root10, (e) => e.tag === 'label').map((e) => e.textContent).filter((t) => /prompt/.test(t)).join(' | '));
  const fr10 = () => walk(root10, (e) => e.className === 'players')[0].children.filter((e) => e.className.split(/\s+/).includes('player') && walk(e, (x) => x.tag === 'canvas').length);
  click(walk(root10, (e) => e.tag === 'button' && e.text === '+ Add a frame')[0]);
  const f3 = fr10()[2];
  click(walk(f3, (e) => e.tag === 'button' && e.text === 'Wrong')[0]);
  const src3 = walk(f3, (e) => e.tag === 'select')[0]; src3.value = 'wrong'; (src3.listeners.change || []).forEach((f) => f());
  const st3 = walk(f3, (e) => e.tag === 'input' && e.className.includes('stamp'))[0]; st3.value = '0:07.00'; (st3.listeners.change || []).forEach((f) => f());
  await settle();
  const a2 = fieldA().value;
  ok('Wrong frames from two generations: both prompts in the flawed version’s field, each under the seconds it made and its frame',
    a2.includes('generated second 0 to second 6.1 of Video A (generation 1 of 2 · clip c1), and made Frame 1') && a2.includes('second 6.1 to second 12.433 of Video A (generation 2 of 2 · clip c2), and made Frame 3')
    && a2.indexOf('P-C1') < a2.indexOf('P-C2'), a2);
  fieldA().value = 'My own words for the prompt.'; (fieldA().listeners.input || []).forEach((f) => f());
  st3.value = '0:08.00'; (st3.listeners.change || []).forEach((f) => f());
  await settle();
  ok('a field the admin typed in stays as typed when a frame moves', fieldA().value === 'My own words for the prompt.', fieldA().value);
  sent.length = 0;
  await Promise.all(walk(root10, (e) => e.tag === 'button' && e.text === 'Save report changes').flatMap((b) => (b.listeners.click || []).map((f) => f())));
  const rep10 = sent.find((x) => x.method === 'PATCH' && x.url === '/api/misses/1');
  ok('a typed field is saved as typed, with no generations; the correct version’s, still Loupe’s, goes with its generation', rep10 && rep10.body.wrong_prompt === 'My own words for the prompt.' && rep10.body.wrong_segments.length === 0
    && rep10.body.fixed_segments.length === 1 && rep10.body.fixed_segments[0].src === 'fixed' && rep10.body.fixed_prompt.includes('of Video B'), JSON.stringify(rep10 && rep10.body).slice(0, 400));
  ok('the boxes are read again before the report is saved', sent.findIndex((x) => x.url === '/api/misses/prompt-boxes') > -1 && sent.findIndex((x) => x.url === '/api/misses/prompt-boxes') < sent.findIndex((x) => x.method === 'PATCH'),
    JSON.stringify(sent.map((x) => x.method + ' ' + x.url)));
  await Promise.all(walk(root10, (e) => e.tag === 'button' && e.text.startsWith('Fill from the run (the flawed version')).flatMap((b) => (b.listeners.click || []).map((f) => f())));
  ok('Fill writes the field again from the frames', fieldA().value.includes('made Frame 3') && fieldA().value.includes('P-C2'), fieldA().value);
  st3.value = '0:03.00'; (st3.listeners.change || []).forEach((f) => f());
  await settle();
  ok('and it follows them again: both Wrong frames in generation 1 leave only its prompt', fieldA().value.includes('made Frame 1 and Frame 3 of this sample') && !fieldA().value.includes('P-C2'), fieldA().value);
  const src2 = walk(fr10()[1], (e) => e.tag === 'select')[0]; src2.value = 'wrong'; (src2.listeners.change || []).forEach((f) => f());
  await settle();
  ok('a Right frame taken from Video A puts Video A’s generation in the correct version’s field', fieldB().value.includes('of Video A (generation 1 of 2') && fieldB().value.includes('made Frame 2'), fieldB().value);
  sent.length = 0;
  await Promise.all(walk(root10, (e) => e.tag === 'button' && e.text === 'Save report changes').flatMap((b) => (b.listeners.click || []).map((f) => f())));
  const rep10b = sent.find((x) => x.method === 'PATCH' && x.url === '/api/misses/1');
  ok('each field goes with its generations, each naming its video', rep10b && rep10b.body.wrong_segments.length === 1 && rep10b.body.wrong_segments[0].spans[0][1] === 6.1
    && rep10b.body.wrong_segments.every((x) => x.src === 'wrong') && rep10b.body.fixed_segments.length === 1 && rep10b.body.fixed_segments.every((x) => x.src === 'wrong'), JSON.stringify(rep10b && rep10b.body).slice(0, 400));
  // frames called Right only: the correct version's box alone, the flawed version's box neither filled nor shown
  click(walk(fr10()[0], (e) => e.tag === 'button' && e.text === 'Right')[0]);
  click(walk(fr10()[2], (e) => e.tag === 'button' && e.text === 'Right')[0]);
  await settle();
  const cellA10 = () => { const ta = fieldA(); return walk(root10, (e) => e.tag === 'div' && (e.children || []).includes(ta))[0]; };
  ok('frames called Right only: the correct version’s box alone holds a prompt; the flawed version’s, which Loupe wrote, is emptied and hidden',
    fieldA().value === '' && !!cellA10() && cellA10().style.display === 'none' && fieldB().value.startsWith('This is a prompt of the correct version') && !fieldB().value.includes('flawed'),
    fieldA().value.slice(0, 80) + ' / ' + (cellA10() ? cellA10().style.display : 'no cell') + ' / ' + fieldB().value.slice(0, 120));
  withSegs = false;
  // a field saved from a joined take comes back following the frames: Loupe writes it again for the frames on screen
  withSavedSegs = true; withSegs = true; sent.length = 0;
  const root11 = await work({ run: 'R', video: '03', miss: '1' });
  await settle();
  const fA11 = walk(root11, (e) => e.tag === 'textarea' && (e.attrs.placeholder || '').startsWith('The flawed version’s prompt, or'))[0];
  ok('a saved field of a joined take is written again for the frames on screen', fA11.value.includes('P-C1') && !fA11.value.includes('SAVED-C2') && fA11.value.includes('made Frame 1 of this sample'), fA11.value);
  withSavedSegs = false; withSegs = false;
  // Example frames: an Examples sample takes all its frames from one video and has one prompt box, that video's, with the prompt of each generation its
  // Example frames come from (the user, 2026-10-29); Example frames from two videos are warned about and never saved
  withSegs = true; sent.length = 0;
  const root12 = await work({ run: 'R', video: '03', miss: '1' });
  await settle();
  const fr12 = () => walk(root12, (e) => e.className === 'players')[0].children.filter((e) => e.className.split(/\s+/).includes('player') && walk(e, (x) => x.tag === 'canvas').length);
  const lab = (start) => walk(root12, (e) => e.tag === 'label' && (e.textContent || '').startsWith(start))[0];
  const f12 = (start) => walk(root12, (e) => e.tag === 'textarea' && (e.attrs.placeholder || '').startsWith(start))[0];
  const shown12 = (start) => walk(root12, (e) => e.tag === 'div' && (e.text || '').startsWith(start)).some((e) => e.style.display !== 'none');
  const mixShown = () => shown12('This sample mixes Example frames');
  const twoShown = () => shown12('This sample takes its Example frames from Video A and Video B');
  const markMoment = () => Promise.all(walk(root12, (e) => e.tag === 'button' && e.text === 'Mark this moment').flatMap((b) => (b.listeners.click || []).map((f) => f())));
  fr12().forEach((fe) => click(walk(fe, (e) => e.tag === 'button' && e.text === 'Example')[0]));
  await settle();
  ok('Example frames from Video A and Video B: the form says an Examples sample takes one video, and does not call it a mix', twoShown() && !mixShown());
  ok('while they stand, each box names the Example frame of its own video (an older two-video sample reads the same way)',
    !!lab('The example prompt of Video A') && !!lab('The example prompt of Video B')
    && f12('The flawed version’s prompt, or').value.startsWith('This is an example prompt: it generated second 0 to second 6.1 of Video A')
    && f12('The correct version’s prompt, or').value.startsWith('This is an example prompt: it generated second 0 to second 6.1 of Video B'),
    f12('The flawed version’s prompt, or').value.slice(0, 160) + ' / ' + f12('The correct version’s prompt, or').value.slice(0, 160));
  sent.length = 0;
  await markMoment();
  ok('and an Examples sample from two videos is never saved', !sent.some((x) => x.method === 'POST' && x.url.endsWith('/moments')), JSON.stringify(sent.map((x) => x.method + ' ' + x.url)));
  click(walk(fr12()[0], (e) => e.tag === 'button' && e.text === 'Wrong')[0]);
  await settle();
  ok('a Wrong frame beside an Example: the form warns that a sample is a miss with its correct version, or Examples only', mixShown() && !twoShown());
  sent.length = 0;
  await markMoment();
  ok('and a mixed sample is never saved', !sent.some((x) => x.method === 'POST' && x.url.endsWith('/moments')), JSON.stringify(sent.map((x) => x.method + ' ' + x.url)));
  ok('a frame called Wrong makes Video A’s box the flawed version’s, while Video B’s, behind an Example of its own video, stays an example',
    !!lab('The flawed version’s prompt (the prompt of each generation the frames called Wrong come from') && !!lab('The example prompt of Video B') && f12('The flawed version’s prompt, or').value.includes('made Frame 1'),
    walk(root12, (e) => e.tag === 'label').map((e) => e.textContent).join(' | '));
  click(walk(fr12()[0], (e) => e.tag === 'button' && e.text === 'Example')[0]);
  const src12 = walk(fr12()[1], (e) => e.tag === 'select')[0]; src12.value = 'wrong'; (src12.listeners.change || []).forEach((f) => f());
  await settle();
  const cellB = () => { const ta = f12('The correct version’s prompt, or'); return walk(root12, (e) => e.tag === 'div' && (e.children || []).includes(ta))[0]; };
  const labA = () => (walk(root12, (e) => e.tag === 'label' && (e.textContent || '').startsWith('The example prompt of Video A'))[0] || {}).textContent || '';
  ok('every frame an Example from Video A: no warning, and one prompt box, Video A’s example box; Video B’s is neither filled nor shown',
    !twoShown() && !mixShown() && !!cellB() && cellB().style.display === 'none' && f12('The correct version’s prompt, or').value === ''
    && labA().includes('an Examples sample has this one box'), labA() + ' / ' + (cellB() ? cellB().style.display : 'no cell'));
  const a12 = f12('The flawed version’s prompt, or').value;
  ok('the one box holds the prompt of the generation the Example frames come from, and not the one no frame comes from',
    a12.startsWith('This is an example prompt: it generated second 0 to second 6.1 of Video A (generation 1 of 2 · clip c1), and made Frame 1 and Frame 2 of this sample.\nP-C1')
    && !a12.includes('P-C2'), a12);
  sent.length = 0;
  await markMoment();
  const rep12 = sent.find((x) => x.method === 'PATCH' && x.url === '/api/misses/1');
  const mo12 = sent.find((x) => x.method === 'POST' && x.url.endsWith('/moments'));
  ok('the Examples sample is saved with Video A’s box and its one generation, and nothing in Video B’s',
    !!rep12 && !!mo12 && rep12.body.fixed_prompt === '' && rep12.body.fixed_segments.length === 0 && rep12.body.wrong_segments.length === 1 && rep12.body.wrong_prompt.includes('P-C1')
    && !rep12.body.wrong_prompt.includes('P-C2') && mo12.body.frames.every((f) => f.src === 'wrong' && f.role === 'example'), JSON.stringify(rep12 && rep12.body).slice(0, 300));
  withSegs = false;
  // the user, 2026-10-30: nothing out of sync. No label, placeholder, button, tooltip, note or comment of the pages names a prompt by the words of
  // before (the two takes, the correct take, Video A's prompt ...): a box is the flawed version's, the correct version's or an example
  const OLD_WORDS = ['two takes', 'both takes', 'correct take', 'flawed take', 'example take', 'corrected take', 'wrong take', 'the corrected prompt',
    'Video A\'s prompt', 'Video B\'s prompt', 'Video A’s prompt', 'Video B’s prompt', 'Prompt of Video A', 'Prompt of Video B', 'PROMPTS OF BOTH'];
  const staleWeb = fs.readdirSync(web).filter((f) => f.endsWith('.js')).flatMap((f) => OLD_WORDS.filter((w) => read(f).includes(w)).map((w) => `${f}: ${w}`));
  ok('no page names a prompt by the old words (the two takes, the correct take, Video A’s prompt ...)', !staleWeb.length, staleWeb.join('; '));
  console.log(fails ? `${fails} failed` : 'all passed');
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.log('FAIL the page threw: ' + (e && e.stack || e)); process.exit(1); });
