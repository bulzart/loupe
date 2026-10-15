// Runs the frame controls of web/v_misses.js (the slider that moves every frame together, and each frame's own time) against fake videos: no browser, no login.
// node tests/transport_check.js
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'web', 'v_misses.js'), 'utf8').split('\r\n').join('\n');
const a = src.indexOf('  // ---- the transport:');
const z = src.indexOf('  // ---- end of the transport');
if (a < 0 || z < 0) { console.log('FAIL the transport block was not found'); process.exit(1); }
const block = src.slice(a, z);

const mkEl = (tag, props, ...kids) => ({ tag, props, kids: kids.flat(), listeners: {}, style: {}, textContent: '', value: '0',
  addEventListener(n, f) { (this.listeners[n] = this.listeners[n] || []).push(f); } });
const mkFrame = (dur) => ({ t: 0, gap: 0, slider: { value: 0 }, stamp: { value: '' },
  video: { currentTime: 0, duration: dur, paused: true, pause() { this.paused = true; }, play() { this.paused = false; } } });
const fmtT = (t) => { t = Math.max(0, +t || 0); const m = Math.floor(t / 60); return `${m}:${(t - m * 60).toFixed(2).padStart(5, '0')}`; };

const run = (durations) => {
  const frames = durations.map(mkFrame);
  const body = block + '\n return { range, parseT, clampT, refresh, pauseAll, seekAll, moveFrame, sameTime, step, playAll, tick };';
  const fn = new Function('h', 'frames', 'FPS', 'fmtT', 'document', body);
  return { frames, ...fn(mkEl, frames, 30, fmtT, { activeElement: null }) };
};

let fails = 0;
const ok = (name, cond, info = '') => { console.log((cond ? '  ok   ' : '  FAIL ') + name + (cond ? '' : '  ' + info)); if (!cond) fails++; };
const near = (x, y) => Math.abs(x - y) < 1e-6;

let t = run([10, 8, 9]);
const [f1, f2, f3] = t.frames;
t.seekAll(2);
ok('the top slider moves every frame to the same time', [f1, f2, f3].every((f) => near(f.t, 2) && near(f.video.currentTime, 2)));
t.moveFrame(f2, 3.5);
ok('a frame moved on its own leaves the others where they were', near(f1.t, 2) && near(f2.t, 3.5) && near(f3.t, 2));
ok('and its gap to the first frame follows', near(f2.gap, 1.5) && near(f3.gap, 0));
t.seekAll(3);
ok('the top slider afterwards moves all of them and keeps each gap', near(f1.t, 3) && near(f2.t, 4.5) && near(f3.t, 3));
t.moveFrame(f1, 1);
ok('moving the first frame on its own leaves the others at their own times', near(f1.t, 1) && near(f2.t, 4.5) && near(f3.t, 3) && near(f2.gap, 3.5) && near(f3.gap, 2));
ok('each frame\'s slider and time box show its own time', f1.slider.value === 1 && f2.slider.value === 4.5 && f2.stamp.value === '0:04.50' && f3.stamp.value === '0:03.00', `${f1.slider.value} ${f2.stamp.value} ${f3.stamp.value}`);
t.sameTime();
ok('"same time" puts every frame on the time of the first', [f1, f2, f3].every((f) => near(f.t, 1) && f.gap === 0));
t.moveFrame(f2, 20);
ok('a time past the end is held at the end of that video', near(f2.t, 8));
t.moveFrame(f3, -5);
ok('a time before the start is held at zero', near(f3.t, 0));
ok('typed times are read as seconds or minutes:seconds', near(t.parseT('3.25'), 3.25) && near(t.parseT('1:03.25'), 63.25) && Number.isNaN(t.parseT('abc')));
t.moveFrame(f1, 0); t.step(1 / 30);
ok('one video frame forward moves all frames by one frame', near(f1.t, 1 / 30));
f1.video.paused = false; f1.video.currentTime = 2; f2.video.currentTime = 5;
t.tick();
ok('while playing, every frame\'s time follows its own video', near(f1.t, 2) && near(f2.t, 5));

t = run([6]);
t.seekAll(2); t.moveFrame(t.frames[0], 3);
ok('with one frame, nothing else moves and there is no gap to keep', near(t.frames[0].t, 3));
console.log(fails ? `${fails} failed` : 'all passed');
process.exit(fails ? 1 : 0);
