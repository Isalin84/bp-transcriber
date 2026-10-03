// Minimal observable store. Views subscribe to slices and re-render on change.

const state = {
  app: null,          // AppState from get_state()
  jobs: [],           // Job[]
  view: 'transcribe', // 'transcribe' | 'history' | 'settings' | 'transcript'
  viewParams: {},     // e.g. {id} for the transcript view
  transcript: null,   // Transcript currently open
  modelDownload: null,// last model_download event
  tokenCheck: null,   // last TokenCheck
};

const subs = new Map(); // key → Set<fn>

export function get(key) { return key ? state[key] : state; }

export function set(key, value) {
  if (state[key] === value) return;
  state[key] = value;
  emit(key);
}

/** Merge a partial object into state.app.settings and notify "app". */
export function patchSettings(partial) {
  if (!state.app) return;
  state.app = { ...state.app, settings: { ...state.app.settings, ...partial } };
  emit('app');
}

export function patchApp(partial) {
  if (!state.app) return;
  state.app = { ...state.app, ...partial };
  emit('app');
}

/** Insert or replace a job by id; keeps newest first. */
export function upsertJob(job) {
  const i = state.jobs.findIndex((j) => j.id === job.id);
  const jobs = state.jobs.slice();
  if (i >= 0) jobs[i] = job; else jobs.unshift(job);
  state.jobs = jobs;
  emit('jobs');
}

export function removeJob(id) {
  state.jobs = state.jobs.filter((j) => j.id !== id);
  emit('jobs');
}

export function subscribe(key, fn) {
  if (!subs.has(key)) subs.set(key, new Set());
  subs.get(key).add(fn);
  return () => subs.get(key).delete(fn);
}

function emit(key) {
  for (const fn of subs.get(key) || []) {
    try { fn(state[key]); } catch (e) { console.error('[bp] subscriber failed', key, e); }
  }
}

/** Switch the main view: navigate('transcript', {id}). main.js listens to "view". */
export function navigate(name, params = {}) {
  state.viewParams = params;
  state.view = name;
  emit('view');
}

/** Keyboard shortcut modifier label for the current OS. */
export function modKey() {
  return state.app?.os === 'macos' ? '⌘' : 'Ctrl';
}

export const isMac = () => state.app?.os === 'macos';
