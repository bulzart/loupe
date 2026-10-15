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
const savedMoment = { moment_id: 5, id: 'S5', image_url: '/x?a=5', updated: 'u', details: 'the pack is right early', correct_note: 'c', has_right: true,
  frames: [{ src: 'wrong', role: 'right', t: 0.5, desc: 'as generated' }, { src: 'wrong', role: 'wrong', t: 3.0, desc: 'as generated' }, { src: 'fixed', role: 'right', t: 3.25, desc: 'correct version' }],
  shapes: [{ type: 'rect', frame: 1, x1: 0.1, y1: 0.1, x2: 0.4, y2: 0.4 }] };
const api = async (method, url, body) => {
  sent.push({ method, url, body });
  if (url === '/api/runs/_') throw new Error('no run');
  if (url === '/api/moments/5' && method === 'PATCH') return { moment: savedMoment };
  if (withMoment && url.startsWith('/api/misses/1') && method === 'GET') return { miss: { id: 1, title: 'T', explanation: '', kinds: [], model: 'm', mode: 'x', product: '', wrong_path: 'A.mp4', fixed_path: noRight ? '' : 'B.mp4', moments: [savedMoment], product_sample: true } };
  if (url.startsWith('/api/runs/')) return { model: 'wan3_0_prime', mode: 'analyze', submode: 'recreate', products: [], videos: [{ video: '03', versions: [
    { path: 'A.mp4', label: 'a1 · raw.mp4', kind: 'attempt' }, { path: 'B.mp4', label: 'capcut-preview.mp4', kind: 'fixed' }] }] };
  if (url === '/api/settings') return { kinds: [] };
  if (url.startsWith('/api/misses/1') && method === 'GET') return { miss: { id: 1, title: 'T', explanation: '', kinds: [], model: 'm', mode: 'x', product: '', wrong_path: 'A.mp4', fixed_path: noRight ? '' : 'B.mp4', moments: [], product_sample: true } };
  if (url.startsWith('/api/misses/1') && method === 'PATCH') return { miss: { id: 1, title: 'T', explanation: '', kinds: [], wrong_path: 'A.mp4', fixed_path: noRight ? '' : 'B.mp4', moments: [], product_sample: true } };
  if (url.endsWith('/moments')) return { moment: { id: 'S1', moment_id: 1, image_url: '/x?a=1', updated: 'u' } };
  if (url.startsWith('/api/product-sample')) return { samples: [] };
  return {};
};
const code = read('v_misses.js').replace(/^import .*$/gm, '').replace(/^export (async )?function/gm, '$1function') + '\nreturn { work };';
const toastStub = (m, err) => { if (err) console.log('  (toast) ' + m); };
const uiNoToast = uiSrc.replace(/function toast[\s\S]*?\n}\n/, '');
const { work } = new Function('api', 'upload', 'videoUrl', 'toastStub', uiNoToast + '\nconst toast = toastStub;\n' + code)(api, async () => ({}), (p) => '/video?p=' + p, toastStub);

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
  // nothing is required: no right version, a neutral frame, no frame at all, the idea, what could be better and what the agents missed
  withMoment = false; noRight = true; sent.length = 0;
  const root3 = await work({ run: 'R', video: '03', miss: '1' });
  const visible3 = () => walk(root3, (e) => e.className === 'players')[0].children.filter((e) => e.className.split(/\s+/).includes('player') && e.style.display !== 'none');
  const frames3 = () => visible3().filter((e) => walk(e, (x) => x.tag === 'canvas').length);
  const b3 = (t) => walk(root3, (e) => e.tag === 'button' && e.text === t);
  ok('with no right version the page starts with one frame and a card that says the right frame is optional', frames3().length === 1 && visible3().length === 2 && visible3()[1].text.includes('Optional'), String(visible3().length));
  ok('and saving is never blocked', !b3('Mark this moment')[0].disabled);
  click(b3('Neutral')[0]);
  ok('a frame can be called Neutral (blue)', frames3()[0].className.includes('role-neutral') && !frames3()[0].className.includes('role-wrong'));
  const area = (start) => walk(root3, (e) => e.tag === 'textarea' && (e.attrs.placeholder || '').startsWith(start))[0];
  area('The idea').value = 'The hand holds the can to the end.'; area('What could have been better').value = 'State the grip at each beat.'; area('What the AI agents missed').value = 'They treated the hold as optional.';
  await Promise.all((b3('Mark this moment')[0].listeners.click || []).map((f) => f()));
  const postN = sent.find((x) => x.method === 'POST' && x.url.endsWith('/moments'));
  ok('a neutral frame and the three scenario texts are sent', postN && postN.body.frames.length === 1 && postN.body.frames[0].role === 'neutral' && postN.body.idea.startsWith('The hand') && postN.body.better.startsWith('State') && postN.body.missed.startsWith('They treated'), JSON.stringify(postN && postN.body));
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
  console.log(fails ? `${fails} failed` : 'all passed');
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.log('FAIL the page threw: ' + (e && e.stack || e)); process.exit(1); });
