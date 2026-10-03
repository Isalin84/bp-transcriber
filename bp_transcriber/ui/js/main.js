// Bootstrap: wait for the API, load state, wire navigation, events, shortcuts, drag&drop.

import './mock-api.js'; // installs itself only when there is no real backend (or ?mock=1)
import { api, on, ready, openUrl } from './api.js';
import * as store from './state.js';
import { applyTheme } from './theme.js';
import { toast } from './components/toast.js';
import { isDialogOpen } from './components/dialog.js';
import { closeMenu, isMenuOpen } from './components/dropdown.js';
import * as transcribe from './views/transcribe.js';
import * as transcript from './views/transcript.js';
import * as history from './views/history.js';
import * as settings from './views/settings.js';
import * as onboarding from './views/onboarding.js';

const VIEWS = { transcribe, transcript, history, settings };
const NAV_FOR = { transcribe: 'transcribe', transcript: 'transcribe', history: 'history', settings: 'settings' };

const root = document.getElementById('view');
const nav = document.getElementById('nav');
let unmount = null;

function renderView() {
  const name = store.get('view');
  const params = store.get('viewParams') || {};
  closeMenu();
  if (unmount) { try { unmount(); } catch (e) { console.error(e); } unmount = null; }
  root.textContent = '';
  root.dataset.view = name;
  unmount = VIEWS[name].render(root, params) || null;
  nav.querySelectorAll('.nav__item').forEach((b) => b.classList.toggle('is-active', b.dataset.view === NAV_FOR[name]));
  root.scrollTop = 0;
}

function paintJobsBadge(jobs) {
  const n = jobs.filter((j) => j.status === 'queued' || j.status === 'running').length;
  const badge = document.getElementById('nav-badge-jobs');
  badge.hidden = !n;
  badge.textContent = String(n);
}

function isTyping() {
  const a = document.activeElement;
  return !!a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA' || a.tagName === 'SELECT' || a.isContentEditable);
}

function wireShortcuts() {
  document.addEventListener('keydown', (e) => {
    const mod = store.isMac() ? e.metaKey : e.ctrlKey;
    if (mod && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'o') {
      e.preventDefault();
      if (isDialogOpen()) return;
      api.pick_files().then((paths) => { if (paths && paths.length) transcribe.enqueue(paths); }).catch(() => {});
    } else if (mod && e.key.toLowerCase() === 'f') {
      if (store.get('view') === 'transcript') { e.preventDefault(); transcript.focusSearch(); }
    } else if (e.key === ' ' && !isTyping() && !isDialogOpen() && !isMenuOpen()) {
      const p = transcript.getPlayer();
      if (p && store.get('view') === 'transcript') { e.preventDefault(); p.toggle(); }
    } else if (e.key === 'Escape' && isMenuOpen()) {
      closeMenu();
    }
  });
}

function wireDragDrop() {
  // The backend handles the OS drop natively and sends `files_dropped`; here we only
  // prevent navigation and show the gold glow. In the browser mock, dropped files are enqueued by name.
  let depth = 0;
  const html = document.documentElement;
  const setDragging = (v) => html.classList.toggle('is-dragging', v);
  document.addEventListener('dragenter', (e) => { e.preventDefault(); if (depth++ === 0) setDragging(true); });
  document.addEventListener('dragover', (e) => { e.preventDefault(); if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy'; });
  document.addEventListener('dragleave', (e) => { e.preventDefault(); if (--depth <= 0) { depth = 0; setDragging(false); } });
  document.addEventListener('drop', (e) => {
    e.preventDefault(); depth = 0; setDragging(false);
    if (window.__bpMock && e.dataTransfer?.files?.length) {
      transcribe.enqueue([...e.dataTransfer.files].map((f) => '/mock/' + f.name));
    }
    if (store.get('view') !== 'transcribe') store.navigate('transcribe');
  });
}

function wireEvents() {
  on('job_update', (e) => store.upsertJob(e.job));
  on('job_done', (e) => {
    store.upsertJob(e.job);
    toast.success(`Готово: ${e.job.file_name}`, { action: { label: 'Открыть', onClick: () => store.navigate('transcript', { id: e.transcript_id }) } });
  });
  on('files_dropped', (e) => { if (e.paths?.length) { transcribe.enqueue(e.paths); if (store.get('view') !== 'transcribe') store.navigate('transcribe'); } });
  on('toast', (e) => toast.show(e.message, { level: e.level || 'info' }));
  on('model_download', (e) => { if (e.status === 'error') toast.error(e.message || 'Ошибка загрузки модели'); });
  store.subscribe('jobs', paintJobsBadge);
}

async function boot() {
  await ready;
  const app = await api.get_state();
  store.set('app', app);
  applyTheme(app.settings?.theme || 'system');
  const jobs = await api.list_jobs().catch(() => []);
  store.set('jobs', jobs);
  paintJobsBadge(jobs);

  wireEvents();
  wireShortcuts();
  wireDragDrop();
  nav.addEventListener('click', (e) => { const b = e.target.closest('.nav__item'); if (b) store.navigate(b.dataset.view); });
  document.querySelectorAll('[data-url]').forEach((a) => a.addEventListener('click', (e) => { e.preventDefault(); openUrl(a.dataset.url); }));
  store.subscribe('view', renderView);

  document.getElementById('app').removeAttribute('aria-busy');
  renderView();
  if (onboarding.shouldShow(app)) await onboarding.show();
}

boot().catch((e) => {
  console.error('[bp] boot failed', e);
  root.textContent = '';
  root.append(Object.assign(document.createElement('div'), { className: 'empty', textContent: 'Не удалось запустить интерфейс. Перезапустите приложение.' }));
});
