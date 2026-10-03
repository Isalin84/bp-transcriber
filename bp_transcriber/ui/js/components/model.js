// GigaAM model status + download panel (shared by Settings and Onboarding).
// Progress comes from "model_download" events; the panel subscribes to store.modelDownload.

import { api, on } from '../api.js';
import * as store from '../state.js';
import { el, fmtSize } from '../util/format.js';
import { icon } from '../util/icons.js';
import { toast } from '../components/toast.js';

const APPROX_SIZE = 450 * 1024 * 1024;

/** createModelPanel({ onDone }) → { el, destroy } */
export function createModelPanel({ onDone } = {}) {
  const pill = el('span', { class: 'pill pill--muted' });
  const text = el('div', { class: 'model__text' });
  const bar = el('div', { class: 'progress', role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': '100', hidden: true }, el('div', { class: 'progress__fill' }));
  const btn = el('button', { class: 'btn btn--primary', type: 'button', onclick: start }, icon('download'), 'Скачать модель');
  const root = el('div', { class: 'model' },
    el('div', { class: 'model__head' },
      el('div', { class: 'model__title' }, el('strong', null, 'GigaAM v3'), el('span', { class: 'muted' }, ' — модель распознавания русской речи')),
      pill),
    text, bar, el('div', { class: 'model__actions' }, btn),
  );

  function paint() {
    const m = store.get('app')?.models?.gigaam || {};
    const dl = store.get('modelDownload');
    const size = m.size_bytes || APPROX_SIZE;
    const downloading = m.downloading || dl?.status === 'downloading';
    btn.hidden = m.available || downloading;
    bar.hidden = !downloading;
    if (m.available) {
      pill.className = 'pill pill--success'; pill.textContent = 'Загружена';
      text.textContent = `Модель на диске, ${fmtSize(size)}. Работает полностью офлайн.`;
    } else if (downloading) {
      const p = dl?.progress ?? m.progress ?? 0;
      pill.className = 'pill pill--active'; pill.textContent = `${Math.round(p * 100)}%`;
      bar.querySelector('.progress__fill').style.width = (p * 100).toFixed(1) + '%';
      bar.setAttribute('aria-valuenow', String(Math.round(p * 100)));
      text.textContent = dl?.total ? `${fmtSize(dl.downloaded)} из ${fmtSize(dl.total)}${dl.message ? ' · ' + dl.message : ''}` : 'Скачиваем…';
    } else if (dl?.status === 'error') {
      pill.className = 'pill pill--danger'; pill.textContent = 'Ошибка';
      text.textContent = dl.message || 'Не удалось скачать модель. Проверьте соединение и попробуйте ещё раз.';
      btn.textContent = ''; btn.append(icon('download'), 'Повторить');
    } else {
      pill.className = 'pill pill--warning'; pill.textContent = 'Не загружена';
      text.textContent = `Нужно скачать один раз, ≈ ${fmtSize(size)}. Дальше всё работает без интернета.`;
    }
  }

  async function start() {
    btn.disabled = true;
    store.set('modelDownload', { status: 'downloading', progress: 0, downloaded: 0, total: 0 });
    paint();
    const ok = await api.download_models().catch(() => false);
    btn.disabled = false;
    if (!ok && store.get('modelDownload')?.status === 'downloading') {
      store.set('modelDownload', { status: 'error', progress: 0, message: 'Не удалось начать загрузку.' });
      paint();
    }
  }

  const offEvt = on('model_download', (e) => {
    store.set('modelDownload', e);
    if (e.status === 'done') {
      const app = store.get('app');
      store.patchApp({ models: { ...app.models, gigaam: { ...app.models.gigaam, available: true, downloading: false, progress: 1 } } });
      toast.success('Модель GigaAM загружена');
      onDone?.();
    }
    paint();
  });
  const offApp = store.subscribe('app', paint);
  paint();
  return { el: root, destroy: () => { offEvt(); offApp(); } };
}
