// Settings view. Every control saves immediately via save_settings(partial).

import { api } from '../api.js';
import * as store from '../state.js';
import { applyTheme } from '../theme.js';
import { el } from '../util/format.js';
import { icon } from '../util/icons.js';
import { createTokenPanel, link } from '../components/token.js';
import { createModelPanel } from '../components/model.js';

const THEMES = [['system', 'Системная'], ['dark', 'Тёмная'], ['light', 'Светлая']];
const DIARIZATION = [['auto', 'Авто'], ['pyannote', 'pyannote'], ['hybrid', 'Гибрид'], ['none', 'Выключено']];
const FORMATS = [['txt', 'TXT'], ['md', 'Markdown'], ['docx', 'Word'], ['srt', 'SRT'], ['vtt', 'VTT'], ['json', 'JSON']];

export function render(root, { section } = {}) {
  const app = store.get('app');
  const s = { ...app.settings };

  async function save(partial) {
    Object.assign(s, partial);
    store.patchSettings(partial);
    const saved = await api.save_settings(partial).catch(() => null);
    if (saved) store.patchSettings(saved);
  }

  function segmented(items, value, onChange, label) {
    const g = el('div', { class: 'segmented', role: 'radiogroup', 'aria-label': label });
    const paint = (v) => g.querySelectorAll('.segmented__item').forEach((b) => { const on = b.dataset.id === v; b.classList.toggle('is-active', on); b.setAttribute('aria-checked', String(on)); });
    for (const [id, text] of items) {
      g.append(el('button', { class: 'segmented__item', type: 'button', role: 'radio', dataset: { id }, onclick: () => { paint(id); onChange(id); } }, text));
    }
    paint(value);
    return g;
  }
  function row(label, control, hint) {
    return el('div', { class: 'srow' }, el('div', { class: 'srow__label' }, el('div', null, label), hint ? el('div', { class: 'srow__hint' }, hint) : null), el('div', { class: 'srow__control' }, control));
  }
  function toggle(checked, onChange, label) {
    const b = el('button', { class: 'switch' + (checked ? ' is-on' : ''), type: 'button', role: 'switch', 'aria-checked': String(checked), 'aria-label': label }, el('span', { class: 'switch__knob' }));
    b.addEventListener('click', () => { const on = !b.classList.contains('is-on'); b.classList.toggle('is-on', on); b.setAttribute('aria-checked', String(on)); onChange(on); });
    return b;
  }

  // ---- Внешний вид / обработка
  const themeSeg = segmented(THEMES, s.theme || 'system', (v) => { applyTheme(v); save({ theme: v }); }, 'Тема');
  const deviceSel = el('select', { class: 'select', 'aria-label': 'Устройство' });
  for (const d of app.devices || []) deviceSel.append(el('option', { value: d.id, disabled: d.available === false ? '' : null }, d.label + (d.available === false ? ' — недоступно' : '')));
  deviceSel.value = s.device || 'auto';
  deviceSel.addEventListener('change', () => save({ device: deviceSel.value }));
  const diarSeg = segmented(DIARIZATION, s.diarization || 'auto', (v) => save({ diarization: v }), 'Спикеры по умолчанию');

  // ---- Автосохранение
  const dirLabel = el('span', { class: 'folder__path' }, s.autosave_dir || 'Рядом с исходным файлом');
  const chooseBtn = el('button', { class: 'btn btn--ghost btn--sm', type: 'button', onclick: async () => {
    const dir = await api.choose_folder().catch(() => null);
    if (dir) { dirLabel.textContent = dir; resetBtn.hidden = false; save({ autosave_dir: dir }); }
  } }, icon('folder'), 'Выбрать папку');
  const resetBtn = el('button', { class: 'btn btn--ghost btn--sm', type: 'button', hidden: !s.autosave_dir, onclick: () => { dirLabel.textContent = 'Рядом с исходным файлом'; resetBtn.hidden = true; save({ autosave_dir: null }); } }, 'Рядом с файлом');
  const folderRow = el('div', { class: 'folder' }, dirLabel, chooseBtn, resetBtn);
  const fmts = el('div', { class: 'checks', role: 'group', 'aria-label': 'Форматы автосохранения' });
  const selected = new Set(s.autosave_formats || ['docx']);
  for (const [id, label] of FORMATS) {
    const cb = el('input', { type: 'checkbox', checked: selected.has(id) ? '' : null });
    cb.addEventListener('change', () => { cb.checked ? selected.add(id) : selected.delete(id); save({ autosave_formats: [...selected] }); });
    fmts.append(el('label', { class: 'check' }, cb, el('span', null, label)));
  }
  const autosaveBody = el('div', { class: 'autosave' + (s.autosave ? '' : ' is-off') }, folderRow, fmts);
  const autosaveToggle = toggle(!!s.autosave, (on) => { autosaveBody.classList.toggle('is-off', !on); save({ autosave: on }); }, 'Автосохранение');
  const tsToggle = toggle(!!s.include_timestamps, (on) => save({ include_timestamps: on }), 'Таймкоды в экспорте');

  // ---- Токен / модели / о программе
  const token = createTokenPanel();
  const model = createModelPanel();
  const py = app.models?.pyannote || {};
  const pyStatus = el('div', { class: 'model model--compact' },
    el('div', { class: 'model__head' }, el('div', { class: 'model__title' }, el('strong', null, 'pyannote'), el('span', { class: 'muted' }, ' — разделение по спикерам')),
      el('span', { class: 'pill ' + (py.cached ? 'pill--success' : 'pill--muted') }, py.cached ? 'В кэше' : 'Скачается при первом запуске')),
    el('div', { class: 'model__text' }, py.cached ? 'Модели pyannote уже на диске.' : 'Загружаются автоматически (≈ 30 МБ) при первой транскрибации с токеном.'));

  const about = el('div', { class: 'about' },
    el('div', { class: 'about__brand' },
      el('img', { src: 'img/logo-mark.svg', alt: '', width: '48', height: '48' }),
      el('div', null, el('div', { class: 'about__name' }, 'BP Transcriber'), el('div', { class: 'muted' }, `Версия ${app.version || '—'} · Транскрибатор русской речи`),
        el('div', { class: 'about__tagline' }, 'Экспертиза. Инновации. Результат.'))),
    el('img', { class: 'about__art', src: 'img/about-illustration.png', alt: '', width: '1200', height: '800', loading: 'lazy' }),
    el('p', { class: 'muted' }, 'Всё — распознавание, разделение по спикерам, экспорт — работает локально на вашем компьютере. Файлы никуда не отправляются.'),
    el('ul', { class: 'licenses' },
      el('li', null, link('https://github.com/salute-developers/GigaAM', 'GigaAM'), ' — MIT'),
      el('li', null, link('https://github.com/pyannote/pyannote-audio', 'pyannote.audio'), ' — MIT, модели CC-BY-4.0'),
      el('li', null, link('https://github.com/snakers4/silero-vad', 'Silero VAD'), ' — MIT'),
      el('li', null, link('https://ffmpeg.org', 'FFmpeg'), ' — LGPL 2.1'),
      el('li', null, link('https://github.com/yaruslove/DialogScribe', 'DialogScribe'), ' — исходная обёртка над GigaAM'),
      el('li', null, 'Шрифты Montserrat и Inter — SIL OFL 1.1')),
    el('div', { class: 'about__links' },
      link('https://github.com/Isalin84/bp-transcriber', 'Исходный код на GitHub'),
      link('https://bestpracticeai.ru', 'Создано с Best Practice AI')));

  const sections = [
    ['look', 'Внешний вид', [row('Тема', themeSeg)]],
    ['processing', 'Обработка', [
      row('Устройство', deviceSel, 'GPU ускоряет распознавание в несколько раз'),
      row('Спикеры по умолчанию', diarSeg, 'Можно поменять перед каждой транскрибацией'),
    ]],
    ['autosave', 'Автосохранение', [
      row('Сохранять транскрипт автоматически', autosaveToggle, 'Сразу после обработки, без диалога'),
      autosaveBody,
      row('Таймкоды в экспорте', tsToggle, 'Время начала каждой реплики в TXT, Markdown и Word'),
    ]],
    ['token', 'Разделение по спикерам (HuggingFace)', [token.el]],
    ['models', 'Модели', [model.el, pyStatus]],
    ['about', 'О программе', [about]],
  ];

  const view = el('div', { class: 'view view--settings' },
    el('header', { class: 'view-head' }, el('div', null, el('h1', { class: 'h1' }, 'Настройки'), el('p', { class: 'view-head__sub' }, 'Изменения сохраняются сразу.'))),
    el('div', { class: 'settings' }, sections.map(([id, title, body]) => el('section', { class: 'card', id: `s-${id}` }, el('h2', { class: 'h2 card__title' }, title), ...body))),
  );
  root.append(view);

  if (section) requestAnimationFrame(() => {
    const target = view.querySelector(`#s-${section}`);
    target?.scrollIntoView({ block: 'start' });
    target?.classList.add('is-highlight');
    if (section === 'token') token.focus();
  });

  return () => { model.destroy(); };
}
