// Runs the frame controls of web/v_misses.js (both-together slider, one slider per video, the gap) against fake videos: no browser, no login.  node tests/transport_check.js
const fs = require('fs');
const src = fs.readFileSync(require('path').join(__dirname, '..', 'web', 'v_misses.js'), 'utf8').split('\r\n').join('\n');
const a = src.indexOf("  // ---- the transport:");
const z = src.indexOf("  // ---- tools\n");
const block = src.slice(a, z);

const mkEl = (tag, props, ...kids) => {
  const el = { tag, props, kids: kids.flat(), listeners: {}, style: {}, textContent: '', value: '0',
    addEventListener(n, f) { (this.listeners[n] = this.listeners[n] || []).push(f); },
    append(...k) { this.kids.push(...k); }, removeAttribute() {} };
  return el;
};
const mkVideo = (dur) => ({ currentTime: 0, duration: dur, paused: true, listeners: {}, addEventListener(n, f) { this.listeners[n] = f; }, pause() { this.paused = true; }, play() { this.paused = false; }, removeAttribute() {} });

const run = (hasRight) => {
  const vW = mkVideo(10), vF = mkVideo(8);
  const state = { wrong: 'w', fixed: hasRight ? 'f' : null };
  const P = { wrong: { el: mkEl('div') }, fixed: { el: mkEl('div') } };
  const body = block + "\n return { range, rangeW, rangeR, tW, tR, tl, gapLbl, getGap: () => gap, seek, moveW, moveR, sameFrame, step, setSources };";
  const fn = new Function('h', 'vW', 'vF', 'state', 'P', 'FPS', 'fmtT', 'videoUrl', 'loadSample', 'api', body);
  return { vW, vF, ...fn(mkEl, vW, vF, state, P, 30, (t) => t.toFixed(2), (x) => x, () => {}, null) };
};

let fails = 0;
const ok = (name, cond, info = '') => { console.log((cond ? '  ok   ' : '  FAIL ') + name + (cond ? '' : '  ' + info)); if (!cond) fails++; };
const near = (x, y) => Math.abs(x - y) < 1e-6;

let t = run(true);
ok('both start on the same frame', t.getGap() === 0 && /same frame/.test(t.gapLbl.textContent), t.gapLbl.textContent);
t.seek(2);
ok('the top slider moves both to the same time', near(t.vW.currentTime, 2) && near(t.vF.currentTime, 2));
t.moveR(3.5);
ok('moving only the right video leaves the wrong one', near(t.vW.currentTime, 2) && near(t.vF.currentTime, 3.5));
ok('the gap is the difference', near(t.getGap(), 1.5) && /1\.50 s later/.test(t.gapLbl.textContent), t.gapLbl.textContent);
t.seek(3);
ok('the top slider afterwards moves both and keeps the gap', near(t.vW.currentTime, 3) && near(t.vF.currentTime, 4.5));
t.moveW(1);
ok('moving only the wrong video leaves the right one', near(t.vW.currentTime, 1) && near(t.vF.currentTime, 4.5) && near(t.getGap(), 3.5));
ok('the labels show each side\'s own time', t.tW.textContent === '1.00' && t.tR.textContent === '4.50', t.tW.textContent + ' ' + t.tR.textContent);
t.sameFrame();
ok('"same frame" puts the right video on the wrong video\'s frame', near(t.vF.currentTime, 1) && t.getGap() === 0 && /same frame/.test(t.gapLbl.textContent));
t.moveR(20);
ok('a time past the end is held at the end of that video', near(t.vF.currentTime, 8));
t.moveR(-5);
ok('a time before the start is held at zero', near(t.vF.currentTime, 0));
t.setSources();
ok('picking another version resets to the same frame', t.getGap() === 0 && near(t.vW.currentTime, t.vW.currentTime));

t = run(false);
t.seek(2); t.moveW(3);
ok('with no right version only the wrong video moves and the gap stays zero', near(t.vW.currentTime, 3) && t.getGap() === 0);
console.log(fails ? `${fails} failed` : 'all passed');
process.exit(fails ? 1 : 0);
