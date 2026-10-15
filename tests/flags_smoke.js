// Loads the Flags page (web/v_flags.js) against the fake DOM and a fake API, with no browser and no login: the list reads in plain words, groups and filters,
// and one flag opens as a story (in short, what happened, suspected cause, what fixed it, what was sent, the evidence, the technical record).
// node tests/flags_smoke.js
const fs = require('fs');
const path = require('path');
const { walk, click } = require('./fakedom');
const web = path.join(__dirname, '..', 'web');
const read = (f) => fs.readFileSync(path.join(web, f), 'utf8').split('\r\n').join('\n');
global.location = { hash: '#/flags' };
global.confirm = () => false;

const now = Date.now();
const iso = (msAgo) => new Date(now - msAgo).toISOString();
const FLAGS = {
  'CF-003': { id: 'CF-003', created: iso(3 * 3600e3), updated: iso(3600e3), status: 'open', kind: 'refused-no-reason', title: 'Job failed 26 s after submit, no reason given',
    where: { run: '20261014-220106-13', video: '10', attempt: 'a3', model: 'wan3_0_prime', mode: 'analyze-recreate', stage: 'job', product: 'Cherry Cola can' }, response: 'none given (failed, no fail_reason)', credits: 'refunded 27',
    job: 'job-3', prompt: 'A woman at a desk. Keep the camera still.', params: { model: 'wan3_0_prime', duration: 9 }, trigger: { tags: [], text: '' }, inputs: [], log: [] },
  'CF-002': { id: 'CF-002', created: iso(30 * 3600e3), updated: iso(29 * 3600e3), status: 'cleared', kind: 'preset-notice', title: 'Preset notice IN THE DARK on the preflight of video 1',
    where: { run: '20261014-220030-13', video: '01', model: 'wan3_0_prime', stage: 'preflight' }, response: 'The preset declined the scene.', changed: 'a2: the dark wording was removed from the beat.', outcome: 'cleared: a changed request was accepted',
    trigger: { tags: ['preset-notice', 'prompt-wording'], text: 'The word dark triggers the preset.', evidence: 'I', prompt_span: 'dark' }, prompt: 'A dark room with a desk.', inputs: [], log: [{ at: iso(29 * 3600e3), note: 'accepted', by: 'agent' }] },
  'CF-001': { id: 'CF-001', created: iso(50 * 3600e3), updated: iso(49 * 3600e3), status: 'persistent', kind: 'moderation-block', title: 'Real face in the reference',
    where: { run: '20261014-220030-13', video: '02', model: 'wan3_0_prime', stage: 'submit' }, response: 'Blocked.', trigger: { tags: ['real-person-face'] }, inputs: [{ label: 'Image 1', name: 'person.png', suspect: true, facts: {} }], log: [] },
};
const api = async (method, url, body) => {
  api.sent.push({ method, url, body });
  if (url === '/api/flags-stats') return { total: 3, by_status: { open: 1, cleared: 1, persistent: 1 }, by_kind: { 'refused-no-reason': 1, 'preset-notice': 1, 'moderation-block': 1 }, tags: { 'preset-notice': 1, 'prompt-wording': 1, 'real-person-face': 1 }, suspect_assets: [{ name: 'person.png', flags: 1 }] };
  if (url.startsWith('/api/flags?')) return { entries: Object.values(FLAGS) };
  const m = url.match(/^\/api\/flags\/(CF-\d+)$/);
  if (m && method === 'GET') return { entry: FLAGS[m[1]] };
  return { ok: true };
};
api.sent = [];

const uiSrc = read('ui.js').replace(/^export /gm, '').replace(/^import .*$/gm, '').replace(/function toast[\s\S]*?\n}\n/, '');
const wordsSrc = read('flag_words.js').replace(/^export /gm, '');
const code = read('v_flags.js').replace(/^import .*$/gm, '').replace(/^export (async )?function/gm, '$1function') + '\nreturn { list, detail };';
const { list, detail } = new Function('api', 'toastStub', uiSrc + '\nconst toast = toastStub;\n' + wordsSrc + '\n' + code)(api, () => {});

let fails = 0;
const ok = (name, cond, info = '') => { console.log((cond ? '  ok   ' : '  FAIL ') + name + (cond ? '' : '  ' + info)); if (!cond) fails++; };
const has = (root, text) => walk(root, (e) => (e.text || '').includes(text)).length > 0;

(async () => {
  const root = await list();
  const cards = walk(root, (e) => e.className === 'fcard');
  ok('the list shows one card per flag', cards.length === 3, String(cards.length));
  ok('a card says what happened in plain words, not in slugs', has(cards[0], 'Failed, no reason given') && has(cards[1], 'A preset warned instead of running') && has(cards[2], 'Blocked by moderation'));
  ok('and its status in plain words, with the meaning as the tooltip', has(cards[1], 'Cleared') && walk(cards[1], (e) => e.className === 'st cleared' && (e.attrs.title || '').includes('changed request was accepted')).length === 1);
  ok('a flag with no suspected cause says so', has(cards[0], 'Cause not known yet'));
  ok('a cleared flag shows what fixed it, an open one what Higgsfield said', has(cards[1], 'What fixed it:') && has(cards[0], 'Higgsfield said:'));
  ok('the time is relative, the exact time is in the tooltip', has(cards[0], '3 h ago') && walk(cards[0], (e) => (e.className || '').includes('fwhen') && ((e.attrs || {}).title || '').length > 8).length === 1);
  ok('a help card explains what a flag is and what each status means', has(root, 'What is a flag?') && has(root, 'Not solved yet') && has(root, 'Kept for the record'));
  ok('the counts are filters in plain words', walk(root, (e) => e.tag === 'button' && e.text === 'Cleared 1').length === 1 && walk(root, (e) => e.tag === 'button' && e.text.startsWith('Failed, no reason given')).length >= 1);
  const filterBtn = walk(root, (e) => e.tag === 'button' && e.text === 'Cleared 1')[0];
  click(filterBtn);
  const searchBox = walk(root, (e) => e.tag === 'input' && e.attrs.type === 'search')[0];
  ok('clicking a count filters the list by that status', searchBox.value === 'status:cleared', searchBox.value);
  const sel = walk(root, (e) => e.tag === 'select')[0];
  ok('the default grouping is none, newest first', sel.options.length === 7 && sel.value !== 'tag');
  sel.value = 'tag'; (sel.listeners.change || []).forEach((f) => f());
  await new Promise((r) => setTimeout(r, 20));
  ok('grouping by the suspected cause titles each group in plain words', has(root, 'A preset notice') || has(root, 'Cause not known yet'));

  const d = await detail('CF-003');
  ok('a flag opens with an in-short sentence', has(d, 'Failed, no reason given, while the job ran. Video 10 · attempt a3 · wan3_0_prime · Cherry Cola can. Credits: refunded 27.'));
  ok('and the three questions answered: what happened, the suspected cause, what fixed it', has(d, 'What happened') && has(d, 'Suspected cause') && has(d, 'What fixed it') && has(d, 'Nothing recorded yet.') && has(d, 'Not known yet'));
  ok('the pipeline stage is in plain words', has(d, 'while the job ran') && has(d, 'Where in the pipeline'));
  ok('what Higgsfield said is kept word for word', has(d, 'What Higgsfield said (word for word)') && has(d, 'none given (failed, no fail_reason)'));
  ok('the sent prompt and settings are shown', has(d, 'What was sent') && has(d, 'A woman at a desk.') && has(d, 'Settings sent') && has(d, 'duration: 9'));
  ok('the technical record is there, collapsed, with every raw value and the whole JSON', has(d, 'Technical details') && has(d, 'Everything recorded (raw JSON)') && has(d, 'refused-no-reason') && has(d, 'job-3'));
  const tags = walk(d, (e) => e.tag === 'button' && e.className.includes('chip') && (e.attrs.title || '').startsWith('real-person-face'));
  ok('the cause tags can be edited in plain words with the raw tag as the tooltip', tags.length === 1 && tags[0].text === 'A real person’s face');
  const save = walk(d, (e) => e.tag === 'button' && e.text === 'Save')[0];
  api.sent.length = 0;
  await Promise.all((save.listeners.click || []).map((f) => f()));
  const patch = api.sent.find((x) => x.method === 'PATCH');
  ok('saving still sends the status, the cause and the tags', patch && patch.url === '/api/flags/CF-003' && patch.body.status === 'open' && patch.body.trigger && Array.isArray(patch.body.trigger.tags), JSON.stringify(patch));

  const d2 = await detail('CF-002');
  ok('a cleared flag says what was changed and what the result was', has(d2, 'What was changed:') && has(d2, 'a2: the dark wording was removed') && has(d2, 'Confirmed by evidence') === false && has(d2, 'Our judgement, not proven'));
  ok('the words suspected are marked in the prompt', walk(d2, (e) => e.tag === 'mark').length >= 1);
  console.log(fails ? `${fails} failed` : 'all passed');
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.log('FAIL the page threw: ' + (e && e.stack || e)); process.exit(1); });
