// HuggingFace token panel — shared by Settings and Onboarding.
// Shows the TokenCheck status, the token field with actions, the ".env import" callout
// and a 3-step guide. Never displays the token itself (backend only reports a flag).

import { api, openUrl } from '../api.js';
import * as store from '../state.js';
import { el } from '../util/format.js';
import { icon } from '../util/icons.js';
import { toast } from '../components/toast.js';
import { confirm } from '../components/dialog.js';

const DEFAULT_REPOS = [
  { id: 'pyannote/speaker-diarization-community-1', url: 'https://huggingface.co/pyannote/speaker-diarization-community-1', ok: null },
  { id: 'pyannote/segmentation-3.0', url: 'https://huggingface.co/pyannote/segmentation-3.0', ok: null },
];
const STATE_LABEL = {
  ok: ['Токен работает', 'success'],
  missing: ['Токен не задан', 'muted'],
  invalid: ['Токен недействителен', 'danger'],
  terms_not_accepted: ['Нужно принять условия моделей', 'warning'],
  network: ['Нет связи с HuggingFace', 'warning'],
};
const IMPORT_SOURCE = { '.env': 'файле .env', env: 'переменных окружения', hf_cache: 'кэше HuggingFace' };

export function link(url, text) {
  return el('a', { href: url, class: 'link', onclick: (e) => { e.preventDefault(); openUrl(url); } }, text ?? url, icon('external', 'icon icon--ext'));
}

/**
 * createTokenPanel({ compact }) → { el, refresh() }
 * compact = true hides the long explanation (used in onboarding).
 */
export function createTokenPanel({ compact = false } = {}) {
  const app = store.get('app');
  const pill = el('span', { class: 'pill pill--muted' });
  const pillText = el('span', { class: 'token__status-text' });
  const input = el('input', { class: 'input token__input', type: 'password', placeholder: 'hf_…', 'aria-label': 'Токен HuggingFace', autocomplete: 'off', spellcheck: 'false' });
  const eye = el('button', { class: 'btn btn--icon btn--sm token__eye', type: 'button', 'aria-label': 'Показать токен', title: 'Показать', onclick: () => {
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    eye.textContent = ''; eye.append(icon(show ? 'eyeOff' : 'eye'));
    eye.setAttribute('aria-label', show ? 'Скрыть токен' : 'Показать токен');
  } }, icon('eye'));
  const saveBtn = el('button', { class: 'btn btn--primary', type: 'button', onclick: save }, 'Сохранить и проверить');
  const checkBtn = el('button', { class: 'btn btn--ghost', type: 'button', onclick: check }, 'Проверить');
  const clearBtn = el('button', { class: 'btn btn--ghost btn--danger-text', type: 'button', onclick: clear }, 'Удалить токен');
  const message = el('p', { class: 'token__message', 'aria-live': 'polite' });
  const repos = el('ul', { class: 'token__repos' });

  const importBox = el('div', { class: 'callout callout--gold', hidden: true },
    icon('key', 'icon callout__icon'),
    el('div', { class: 'callout__body' }, el('strong', null, 'Найден токен'), el('span', { class: 'callout__text' })),
    el('button', { class: 'btn btn--primary btn--sm', type: 'button', onclick: doImport }, 'Импортировать'));

  const guide = el('ol', { class: 'steps' },
    el('li', null, el('span', { class: 'steps__n' }, '1'), el('div', null, 'Создайте бесплатный токен с правами ', el('b', null, 'Read'), ' на странице ', link('https://huggingface.co/settings/tokens', 'huggingface.co/settings/tokens'), '.')),
    el('li', null, el('span', { class: 'steps__n' }, '2'), el('div', null, 'Примите условия использования моделей (это бесплатно, нужно нажать «Agree» на каждой странице):', repos)),
    el('li', null, el('span', { class: 'steps__n' }, '3'), el('div', null, 'Вставьте токен в поле выше и нажмите «Сохранить и проверить».')),
  );

  const root = el('div', { class: 'token' },
    el('div', { class: 'token__status' }, pill, pillText),
    importBox,
    el('div', { class: 'token__row' }, el('div', { class: 'input-wrap' }, input, eye), saveBtn, checkBtn, clearBtn),
    message,
    compact ? null : el('p', { class: 'muted' }, 'Без токена транскрибация тоже работает: режим «Гибрид» разделяет спикеров без pyannote, но менее точно. Токен хранится в системном хранилище паролей и никуда не передаётся, кроме HuggingFace.'),
    el('details', { class: 'token__guide', open: compact ? null : '' }, el('summary', null, 'Как получить токен — 3 шага'), guide),
  );

  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); save(); } });

  function setBusy(b) { for (const x of [saveBtn, checkBtn, clearBtn]) x.disabled = b; root.classList.toggle('is-busy', b); }

  function paint(tc) {
    const settings = store.get('app')?.settings || {};
    const state = tc?.state || (settings.hf_token_set ? 'ok' : 'missing');
    const [label, kind] = STATE_LABEL[state] || STATE_LABEL.missing;
    pill.className = `pill pill--${kind}`;
    pill.textContent = label;
    pillText.textContent = tc?.user ? `аккаунт ${tc.user}` : '';
    message.textContent = tc?.message || '';
    message.className = `token__message token__message--${kind}`;
    clearBtn.hidden = !settings.hf_token_set;
    checkBtn.hidden = !settings.hf_token_set;
    const list = (tc?.repos && tc.repos.length ? tc.repos : DEFAULT_REPOS);
    repos.textContent = '';
    for (const r of list) {
      repos.append(el('li', { class: 'token__repo' },
        r.ok === true ? icon('check', 'icon icon--ok') : r.ok === false ? icon('warning', 'icon icon--warn') : el('span', { class: 'icon icon--dot' }),
        link(r.url, r.id)));
    }
    const imp = store.get('app')?.token_import;
    importBox.hidden = !imp || settings.hf_token_set;
    if (imp) importBox.querySelector('.callout__text').textContent = ` в ${IMPORT_SOURCE[imp.source] || imp.source} — можно использовать его.`;
  }

  async function applyResult(tc, successMsg) {
    store.set('tokenCheck', tc);
    store.patchSettings({ hf_token_set: tc.state !== 'missing' });
    paint(tc);
    if (tc.ok) toast.success(successMsg || tc.message || 'Токен работает');
    else if (tc.state !== 'missing') toast.warning(tc.message || 'Проверьте токен');
  }
  async function save() {
    const v = input.value.trim();
    if (!v) { input.focus(); toast.warning('Вставьте токен'); return; }
    setBusy(true);
    const tc = await api.set_hf_token(v).catch(() => null);
    setBusy(false);
    if (tc) { input.value = ''; applyResult(tc, 'Токен сохранён и проверен'); }
  }
  async function check() {
    setBusy(true);
    const tc = await api.check_hf_token().catch(() => null);
    setBusy(false);
    if (tc) applyResult(tc);
  }
  async function clear() {
    const ok = await confirm('Удалить токен?', { detail: 'Разделение по спикерам через pyannote станет недоступно, пока вы не добавите токен снова.', okLabel: 'Удалить', danger: true });
    if (!ok) return;
    setBusy(true);
    const settings = await api.clear_hf_token().catch(() => null);
    setBusy(false);
    if (settings) { store.patchSettings(settings); store.set('tokenCheck', null); paint(null); toast.info('Токен удалён'); }
  }
  async function doImport() {
    setBusy(true);
    const tc = await api.import_hf_token().catch(() => null);
    setBusy(false);
    if (tc) { store.patchApp({ token_import: null }); applyResult(tc, 'Токен импортирован'); }
  }

  paint(store.get('tokenCheck'));
  if (app?.settings?.hf_token_set && !store.get('tokenCheck')) {
    // Validate quietly in the background so the pill is accurate.
    api.check_hf_token().then((tc) => { store.set('tokenCheck', tc); paint(tc); }).catch(() => {});
  }
  return { el: root, refresh: () => paint(store.get('tokenCheck')), focus: () => input.focus() };
}
