import { toast } from './ui.js';

export const session = { signedIn: false, adminSet: true, csrf: '' };

export async function api(method, path, body, quiet = false) {
  const headers = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (method !== 'GET') headers['X-Loupe-CSRF'] = session.csrf;
  let r;
  try {
    r = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), credentials: 'same-origin' });
  } catch (e) {
    if (!quiet) toast('Loupe is not reachable', true);
    throw e;
  }
  let d = {};
  try { d = await r.json(); } catch (_) { /* not json */ }
  if (r.status === 401) { session.signedIn = false; window.dispatchEvent(new Event('loupe-unauth')); }
  if (!r.ok) {
    const msg = d.error || ('HTTP ' + r.status);
    if (!quiet) toast(msg, true);
    const err = new Error(msg); err.status = r.status; throw err;
  }
  return d;
}

export async function loadSession() {
  const r = await fetch('/api/session', { credentials: 'same-origin' });
  const d = await r.json();
  session.signedIn = !!d.signed_in && d.kind === 'admin';
  session.adminSet = d.admin_set;
  session.csrf = d.csrf || '';
  return session;
}

export async function signIn(password, setup) {
  const r = await fetch(setup ? '/api/setup' : '/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password }), credentials: 'same-origin' });
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || 'sign-in failed');
  session.csrf = d.csrf; session.signedIn = true; session.adminSet = true;
}

export async function signOut() { await api('POST', '/api/logout', {}); session.signedIn = false; }

export function upload(file, onProgress) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open('PUT', '/api/upload?name=' + encodeURIComponent(file.name));
    x.setRequestHeader('X-Loupe-CSRF', session.csrf);
    x.upload.onprogress = (e) => onProgress && e.lengthComputable && onProgress(e.loaded / e.total);
    x.onload = () => { let d = {}; try { d = JSON.parse(x.responseText); } catch (_) { /* */ } x.status < 300 ? resolve(d) : reject(new Error(d.error || 'upload failed')); };
    x.onerror = () => reject(new Error('upload failed'));
    x.send(file);
  });
}

export const videoUrl = (p) => '/api/video?path=' + encodeURIComponent(p);
export const frameUrl = (p, t) => '/api/frame?path=' + encodeURIComponent(p) + '&t=' + (+t).toFixed(3);
