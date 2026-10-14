// Loupe UI helpers. No innerHTML anywhere: every value reaches the page as a text node or an attribute.
export function h(tag, props, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === false || v == null) continue;
    if (k === 'class') e.className = v;
    else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'value') e.value = v;
    else if (k === 'checked' || k === 'disabled' || k === 'selected' || k === 'open') e[k] = !!v;
    else e.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    e.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return e;
}
// Replace an element's children; arrays are flattened and null/false skipped (replaceChildren alone would print them).
export function put(el, ...kids) {
  el.replaceChildren(...kids.flat(Infinity).filter((k) => k != null && k !== false).map((k) => (k.nodeType ? k : document.createTextNode(String(k)))));
  return el;
}
export const q = (sel, root = document) => root.querySelector(sel);
export const enc = encodeURIComponent;

export function toast(msg, err = false) {
  const t = h('div', { class: 'toast' + (err ? ' err' : '') }, msg);
  document.getElementById('toasts').append(t);
  setTimeout(() => t.remove(), err ? 7000 : 3200);
}

export function fmtDate(s) {
  if (!s) return '';
  const d = new Date(s);
  return isNaN(d) ? s : d.toLocaleString([], { year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}
export function fmtBytes(n) { return n > 1e9 ? (n / 1e9).toFixed(1) + ' GB' : n > 1e6 ? (n / 1e6).toFixed(1) + ' MB' : Math.round(n / 1e3) + ' KB'; }
export function fmtT(t) { t = Math.max(0, t || 0); const m = Math.floor(t / 60); return m + ':' + (t - m * 60).toFixed(2).padStart(5, '0'); }

export const status = (s) => h('span', { class: 'st ' + (s || '') }, s || '');
export const chips = (arr, onClick) => (arr || []).map((t) => h(onClick ? 'button' : 'span', { class: 'chip', onclick: onClick ? () => onClick(t) : null }, t));

// A chip picker: toggles values in a Set; returns the element and a getter.
export function chipPicker(options, selected, onChange) {
  const sel = new Set(selected || []);
  const box = h('div');
  const draw = () => {
    box.replaceChildren(...[...new Set([...options, ...sel])].map((o) => h('button', { type: 'button', class: 'chip' + (sel.has(o) ? ' on' : ''),
      onclick: () => { sel.has(o) ? sel.delete(o) : sel.add(o); draw(); onChange && onChange([...sel]); } }, o)));
    const add = h('input', { placeholder: 'add a kind…', size: 12, onkeydown: (e) => {
      if (e.key === 'Enter') { e.preventDefault(); const v = e.target.value.trim().toLowerCase().replace(/\s+/g, '-'); if (v) { sel.add(v); draw(); onChange && onChange([...sel]); } } } });
    box.append(add);
  };
  draw();
  return { el: box, get: () => [...sel] };
}

export function copyText(text) {
  navigator.clipboard.writeText(text).then(() => toast('Copied'), () => toast('Copy failed', true));
}

export function highlight(text, needle) {
  if (!needle) return [text];
  const i = text.toLowerCase().indexOf(needle.toLowerCase());
  if (i < 0) return [text];
  return [text.slice(0, i), h('mark', {}, text.slice(i, i + needle.length)), text.slice(i + needle.length)];
}

export function confirmBox(msg) { return window.confirm(msg); }
