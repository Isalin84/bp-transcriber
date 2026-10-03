// Thin wrapper over window.pywebview.api + event bus for window.bp.onEvent.
// Every call returns a Promise; rejected calls surface a toast (unless {silent: true}).

import { toast } from './components/toast.js';

const listeners = new Map(); // event type → Set<fn>

/** Subscribe to backend events ("job_update", "model_download", … or "*"). Returns unsubscribe(). */
export function on(type, fn) {
  if (!listeners.has(type)) listeners.set(type, new Set());
  listeners.get(type).add(fn);
  return () => listeners.get(type)?.delete(fn);
}

function dispatch(evt) {
  if (!evt || typeof evt !== 'object') return;
  for (const t of [evt.type, '*']) {
    const set = listeners.get(t);
    if (!set) continue;
    for (const fn of set) {
      try { fn(evt); } catch (e) { console.error('[bp] event handler failed', t, e); }
    }
  }
}

// Take over window.bp.onEvent and drain anything queued before modules loaded.
const queued = (window.bp && window.bp._queue) || [];
window.bp = Object.assign(window.bp || {}, { onEvent: dispatch });
queueMicrotask(() => queued.splice(0).forEach(dispatch));

/** Resolves when window.pywebview.api is callable (real backend or mock). */
export const ready = new Promise((resolve) => {
  const check = () => (window.pywebview && window.pywebview.api) ? (resolve(), true) : false;
  if (window.bp._ready && check()) return;
  window.addEventListener('pywebviewready', () => check(), { once: true });
  // Safety net: if the event already fired before this module ran.
  const poll = setInterval(() => { if (check()) clearInterval(poll); }, 100);
  setTimeout(() => clearInterval(poll), 15000);
});

async function call(name, args = [], opts = {}) {
  await ready;
  const fn = window.pywebview?.api?.[name];
  if (typeof fn !== 'function') {
    const err = new Error(`Метод ${name} недоступен`);
    if (!opts.silent) toast.error(err.message);
    throw err;
  }
  try {
    return await fn(...args);
  } catch (e) {
    const message = (e && (e.message || e.detail || e.msg)) || String(e) || 'Неизвестная ошибка';
    if (!opts.silent) toast.error(message);
    throw Object.assign(new Error(message), { cause: e });
  }
}

const METHODS = [
  'get_state', 'pick_files', 'choose_folder', 'enqueue', 'list_jobs', 'cancel_job', 'remove_job',
  'get_settings', 'save_settings', 'set_hf_token', 'import_hf_token', 'clear_hf_token', 'check_hf_token',
  'models_status', 'download_models', 'list_history', 'load_transcript', 'rename_speaker', 'edit_segment',
  'delete_transcript', 'export', 'copy_text', 'reveal', 'open_url',
];

/** api.get_state(), api.enqueue(paths, options)… Last argument may be {silent: true} via api.quiet.* */
export const api = {};
export const quiet = {};
for (const m of METHODS) {
  api[m] = (...args) => call(m, args);
  quiet[m] = (...args) => call(m, args, { silent: true });
}

/** Open an external link through the backend; falls back to window.open in a plain browser. */
export function openUrl(url) {
  return api.open_url(url).catch(() => {});
}
