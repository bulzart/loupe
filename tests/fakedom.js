// A tiny fake DOM, just enough to run Loupe's page modules in node with no browser: elements, a canvas, events, classList, a few globals.
// Used by page_smoke.js and flags_smoke.js.
class El {
  constructor(tag) {
    this.tag = tag; this.children = []; this.attrs = {}; this.listeners = {}; this.className = ''; this._text = ''; this.value = '';
    this.clientWidth = 400; this.clientHeight = 700; this.videoWidth = 0; this.videoHeight = 0; this.width = 0; this.height = 0;
    this.currentTime = 0; this.duration = NaN; this.paused = true; this.muted = false; this.isConnected = true; this.parentNode = null;
    const self = this;
    this.style = { setProperty(k, v) { this[k] = v; } };
    this.classList = {
      toggle(c, on) { const s = new Set(self.className.split(/\s+/).filter(Boolean)); (on === undefined ? !s.has(c) : on) ? s.add(c) : s.delete(c); self.className = [...s].join(' '); },
      add(c) { this.toggle(c, true); }, remove(c) { this.toggle(c, false); }, contains(c) { return self.className.split(/\s+/).includes(c); },
    };
  }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text; }
  get options() { return this.children.filter((c) => c.tag === 'option'); }
  setAttribute(k, v) { this.attrs[k] = v; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(n, f) { (this.listeners[n] = this.listeners[n] || []).push(f); }
  append(...k) { for (const c of k) { if (c && typeof c === 'object') c.parentNode = this; this.children.push(c); } }
  replaceChildren(...k) { this.children = []; this.append(...k); }
  remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter((c) => c !== this); }
  insertBefore(n, ref) { n.parentNode = this; const i = this.children.indexOf(ref); i < 0 ? this.children.push(n) : this.children.splice(i, 0, n); }
  getContext() { return new Proxy({}, { get: (_t, k) => (k === 'measureText' ? () => ({ width: 10 }) : () => {}), set: () => true }); }
  getBoundingClientRect() { return { left: 0, top: 0, width: 400, height: 700 }; }
  setPointerCapture() {}
  pause() { this.paused = true; } play() { this.paused = false; } load() {}
  scrollIntoView() {} focus() {} closest() { return null; }
  get nodeType() { return 1; }
  get text() { return this._text + this.children.map((c) => (typeof c === 'string' ? c : c.text || '')).join(''); }
}
const walk = (e, f, out = []) => { if (f(e)) out.push(e); for (const c of e.children || []) if (c && typeof c === 'object') walk(c, f, out); return out; };
const click = (e, ev = {}) => (e.listeners.click || []).forEach((f) => f(ev));
global.document = {
  createElement: (t) => new El(t), createTextNode: (s) => ({ nodeType: 3, textContent: String(s), text: String(s), children: [] }), activeElement: null,
  getElementById: () => new El('div'), querySelector: () => new El('div'),
};
global.window = { devicePixelRatio: 1, addEventListener() {}, removeEventListener() {}, scrollTo() {} };
global.ResizeObserver = class { observe() {} disconnect() {} };
global.history = { replaceState() {} };


module.exports = { El, walk, click };
