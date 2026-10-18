import { h, q, toast } from './ui.js';
import { api, loadSession, session, signIn, signOut } from './api.js';
import * as flags from './v_flags.js';
import * as misses from './v_misses.js';
import * as samples from './v_samples.js';
import * as admin from './v_admin.js';

const ROUTES = [
  [/^#\/?$/, () => admin.overview(), 'overview'],
  [/^#\/flags\/(CF-\d+)$/, (id) => flags.detail(id), 'flags'],
  [/^#\/flags$/, () => flags.list(), 'flags'],
  [/^#\/misses\/work$/, (qs) => misses.work(qs), 'misses'],
  [/^#\/misses\/new$/, () => misses.pick(), 'misses'],
  [/^#\/misses$/, () => misses.reports(), 'misses'],
  [/^#\/samples$/, () => samples.gallery(), 'samples'],
  [/^#\/keeper$/, () => admin.keeper(), 'keeper'],
  [/^#\/captures$/, () => admin.captures(), 'captures'],
  [/^#\/keys$/, () => admin.keys(), 'keys'],
  [/^#\/settings$/, () => admin.settings(), 'settings'],
];
const NAV = [['overview', '#/', 'Overview'], ['flags', '#/flags', 'Flags'], ['misses', '#/misses', 'Generation misses'], ['samples', '#/samples', 'QC samples'],
  ['keeper', '#/keeper', 'QC keeper'], ['captures', '#/captures', 'Captures'], ['keys', '#/keys', 'API keys'], ['settings', '#/settings', 'Settings']];

const LOGO = () => {
  const NS = 'http://www.w3.org/2000/svg';
  const s = document.createElementNS(NS, 'svg'); s.setAttribute('viewBox', '0 0 32 32');
  const c = document.createElementNS(NS, 'circle'); [['cx', 13], ['cy', 13], ['r', 8], ['fill', 'none'], ['stroke', '#e5484d'], ['stroke-width', 3]].forEach(([k, v]) => c.setAttribute(k, v));
  const p = document.createElementNS(NS, 'path'); [['d', 'M19 19l8 8'], ['stroke', '#e5484d'], ['stroke-width', 3.5], ['stroke-linecap', 'round']].forEach(([k, v]) => p.setAttribute(k, v));
  s.append(c, p);
  return s;
};

function loginView() {
  const setup = !session.adminSet;
  const pw = h('input', { type: 'password', placeholder: setup ? 'New passphrase (8+ characters)' : 'Passphrase', autofocus: true, style: 'width:100%' });
  const go = async () => {
    try { await signIn(pw.value, setup); location.hash = '#/'; boot(); }
    catch (e) { toast(e.message, true); }
  };
  pw.addEventListener('keydown', (e) => e.key === 'Enter' && go());
  return h('div', { class: 'login' }, h('div', { class: 'card' },
    h('div', { class: 'brand' }, LOGO(), 'Loupe'),
    h('p', { class: 'muted' }, setup ? 'First run: choose the admin passphrase. It protects this app on your machine; API keys for the agents are created inside.'
      : 'Flags, generation misses and the QC sample library of the video pipelines.'),
    pw, h('div', { style: 'margin-top:12px' }, h('button', { class: 'btn primary', onclick: go }, setup ? 'Set passphrase and sign in' : 'Sign in'))));
}

async function counts() {
  try { return await api('GET', '/api/overview', undefined, true); } catch (_) { return {}; }
}

async function render() {
  document.querySelectorAll('.overlay').forEach((o) => o.remove());   // a modal never outlives its page
  const app = q('#app');
  if (!session.signedIn) { app.replaceChildren(loginView()); return; }
  const hash = location.hash || '#/';
  const [path, qs] = hash.split('?');
  const params = Object.fromEntries(new URLSearchParams(qs || ''));
  let match = null;
  for (const [rx, fn, nav] of ROUTES) { const m = path.match(rx); if (m) { match = [fn, m.slice(1), nav]; break; } }
  const c = await counts();
  const badge = { flags: c.flags_open, captures: c.captures_unclaimed };
  const main = h('main', { class: 'main' }, h('div', { class: 'empty' }, 'Loading…'));
  app.replaceChildren(h('div', { class: 'shell' },
    h('aside', { class: 'side' }, h('div', { class: 'brand' }, LOGO(), 'Loupe'),
      h('nav', { class: 'nav' }, NAV.map(([k, href, label]) => h('a', { href, class: match && match[2] === k ? 'on' : '' }, label, badge[k] ? h('span', { class: 'badge' }, badge[k]) : null))),
      h('div', { class: 'grow' }), h('button', { class: 'btn sm', onclick: async () => { await signOut(); boot(); } }, 'Sign out')),
    main));
  if (!match) { main.replaceChildren(h('div', { class: 'empty' }, 'Nothing here.')); return; }
  try {
    const node = await match[0](...match[1], params);
    main.replaceChildren(node);
  } catch (e) { main.replaceChildren(h('div', { class: 'empty' }, 'Could not load this page: ' + e.message)); }
}

async function boot() {
  await loadSession();
  render();
}
window.addEventListener('hashchange', () => { if (session.signedIn) render(); });
window.addEventListener('loupe-unauth', () => boot());
boot();
