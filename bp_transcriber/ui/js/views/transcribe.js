// Home view: drop zone, quick options, job queue.

import { api } from '../api.js';
import * as store from '../state.js';
import { el, html, fmtSpan, fmtTime, fmtRelativeDate, plural } from '../util/format.js';
import { icon } from '../util/icons.js';
import { toast } from '../components/toast.js';

export const STAGES = [
  { id: 'load', label: 'Загрузка модели' },
  { id: 'decode', label: 'Декодирование' },
  { id: 'vad', label: 'Поиск речи' },
  { id: 'asr', label: 'Распознавание' },
  { id: 'diarize', label: 'Спикеры' },
  { id: 'finalize', label: 'Готово' },
];

const DIARIZATION = [
  { id: 'auto', label: 'Авто', title: 'pyannote, если есть токен, иначе гибридный режим' },
  { id: 'pyannote', label: 'pyannote', title: 'Точное разделение, нужен токен HuggingFace' },
  { id: 'hybrid', label: 'Гибрид', title: 'Без токена, менее точно' },
  { id: 'none', label: 'Выключено', title: 'Только текст, без спикеров' },
];

// Options chosen on this screen (seeded from settings on first render).
let options = null;

/** Drop-zone illustration: concentric rings with nodes — the brand's "smart simplicity" motif. */
function ringsSvg() {
  return html(`
    <svg class="rings" viewBox="0 0 320 320" aria-hidden="true">
      <g class="rings__orbits" fill="none">
        <circle cx="160" cy="160" r="150"/>
        <circle cx="160" cy="160" r="118"/>
        <circle cx="160" cy="160" r="86"/>
        <circle cx="160" cy="160" r="54"/>
      </g>
      <g class="rings__links" fill="none">
        <path d="M160 42v64M42 160h64M160 278v-64M278 160h-64"/>
        <path d="M76.6 76.6 114 114M243.4 76.6 206 114M76.6 243.4 114 206M243.4 243.4 206 206"/>
      </g>
      <g class="rings__nodes">
        <circle cx="160" cy="42" r="4"/><circle cx="278" cy="160" r="4"/><circle cx="160" cy="278" r="4"/><circle cx="42" cy="160" r="4"/>
        <circle cx="76.6" cy="76.6" r="3"/><circle cx="243.4" cy="76.6" r="3"/><circle cx="76.6" cy="243.4" r="3"/><circle cx="243.4" cy="243.4" r="3"/>
        <circle cx="160" cy="106" r="2.5"/><circle cx="214" cy="160" r="2.5"/><circle cx="160" cy="214" r="2.5"/><circle cx="106" cy="160" r="2.5"/>
      </g>
      <g class="rings__wave" fill="none">
        <path d="M118 160c6-18 10-18 16 0s10 18 16 0 10-18 16 0 10 18 16 0 10-18 16 0"/>
      </g>
    </svg>`);
}

export function render(root) {
  const app = store.get('app');
  const settings = app?.settings || {};
  if (!options) options = { diarization: settings.diarization || 'auto', num_speakers: settings.num_speakers ?? null };
  const mod = store.modKey();
  const tokenSet = !!settings.hf_token_set;

  const pickBtn = el('button', { class: 'btn btn--primary btn--lg', type: 'button', onclick: pickFiles },
    icon('file'), 'Выбрать файлы', el('kbd', { class: 'kbd kbd--on-accent' }, `${mod} O`));

  const drop = el('div', { class: 'dropzone', id: 'dropzone', role: 'button', tabindex: '0', 'aria-label': 'Перетащите файлы или выберите их',
    onclick: (e) => { if (!e.target.closest('button')) pickFiles(); },
    onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pickFiles(); } } },
    el('div', { class: 'dropzone__art' }, ringsSvg()),
    el('div', { class: 'dropzone__text' },
      el('h1', { class: 'dropzone__title' }, 'Перетащите аудио или видео'),
      el('p', { class: 'dropzone__hint' }, 'Запись совещания, интервью, лекция, подкаст — MP3, WAV, M4A, MP4, MOV и другие форматы.'),
      el('div', { class: 'dropzone__actions' }, pickBtn),
    ),
  );

  // --- quick options --------------------------------------------------------
  const seg = el('div', { class: 'segmented', role: 'radiogroup', 'aria-label': 'Разделение по спикерам' });
  for (const d of DIARIZATION) {
    const needsToken = d.id === 'pyannote' && !tokenSet;
    seg.append(el('button', {
      class: 'segmented__item' + (options.diarization === d.id ? ' is-active' : ''), type: 'button', role: 'radio',
      'aria-checked': String(options.diarization === d.id), title: d.title, dataset: { id: d.id },
      onclick: () => {
        options.diarization = d.id;
        seg.querySelectorAll('.segmented__item').forEach((b) => {
          const on = b.dataset.id === d.id; b.classList.toggle('is-active', on); b.setAttribute('aria-checked', String(on));
        });
        updateHint();
      },
    }, d.label, needsToken ? el('span', { class: 'segmented__dot', title: 'Нужен токен HuggingFace' }) : null));
  }
  const hint = el('div', { class: 'options__hint' });
  function updateHint() {
    let text = '';
    if (!tokenSet && (options.diarization === 'auto' || options.diarization === 'pyannote')) {
      text = options.diarization === 'pyannote'
        ? 'Для pyannote нужен токен HuggingFace — добавьте его в настройках.'
        : 'Токен HuggingFace не задан: спикеры будут разделены гибридным способом, менее точно.';
    } else if (options.diarization === 'hybrid') {
      text = 'Гибридный режим работает без токена, но может путать похожие голоса.';
    }
    hint.textContent = text;
    hint.hidden = !text;
    if (text && !tokenSet && options.diarization !== 'hybrid') {
      hint.append(' ', el('button', { class: 'link', type: 'button', onclick: () => store.navigate('settings', { section: 'token' }) }, 'Открыть настройки'));
    }
  }
  updateHint();

  // Speaker count: "Авто" toggle + stepper 1–10
  const countVal = el('span', { class: 'stepper__value', 'aria-live': 'polite' });
  const autoBtn = el('button', { class: 'chip-toggle', type: 'button', 'aria-pressed': 'false' }, 'Авто');
  const minus = el('button', { class: 'stepper__btn', type: 'button', 'aria-label': 'Меньше', onclick: () => setCount((options.num_speakers ?? 2) - 1) }, icon('minus'));
  const plus = el('button', { class: 'stepper__btn', type: 'button', 'aria-label': 'Больше', onclick: () => setCount((options.num_speakers ?? 1) + 1) }, icon('plus'));
  const stepper = el('div', { class: 'stepper' }, minus, countVal, plus);
  function setCount(n) {
    options.num_speakers = n == null ? null : Math.max(1, Math.min(10, n));
    const auto = options.num_speakers == null;
    autoBtn.classList.toggle('is-active', auto); autoBtn.setAttribute('aria-pressed', String(auto));
    stepper.classList.toggle('is-disabled', auto);
    countVal.textContent = auto ? '–' : String(options.num_speakers);
    minus.disabled = auto || options.num_speakers <= 1; plus.disabled = auto || options.num_speakers >= 10;
  }
  autoBtn.addEventListener('click', () => setCount(options.num_speakers == null ? 2 : null));
  setCount(options.num_speakers);

  const optionsRow = el('div', { class: 'options' },
    el('div', { class: 'options__group' }, el('label', { class: 'options__label' }, 'Спикеры'), seg),
    el('div', { class: 'options__group' }, el('label', { class: 'options__label' }, 'Кол-во спикеров'), el('div', { class: 'options__count' }, autoBtn, stepper)),
  );

  // --- queue ------------------------------------------------------------------
  const queue = el('section', { class: 'queue', 'aria-label': 'Очередь' });
  const queueTitle = el('div', { class: 'queue__head' }, el('h2', { class: 'h2' }, 'Очередь'), el('span', { class: 'queue__count' }));
  const list = el('div', { class: 'queue__list' });
  queue.append(queueTitle, list);

  const cards = new Map(); // job.id → element
  function renderQueue(jobs) {
    const active = jobs.filter((j) => j.status === 'queued' || j.status === 'running').length;
    queueTitle.querySelector('.queue__count').textContent = active ? `${plural(active, ['файл', 'файла', 'файлов'])} в работе` : '';
    queue.hidden = jobs.length === 0;
    const seen = new Set();
    jobs.forEach((job, i) => {
      seen.add(job.id);
      let card = cards.get(job.id);
      if (!card) { card = buildCard(job); cards.set(job.id, card); }
      updateCard(card, job);
      if (list.children[i] !== card) list.insertBefore(card, list.children[i] || null);
    });
    for (const [id, card] of cards) if (!seen.has(id)) { card.remove(); cards.delete(id); }
  }

  // --- recent transcripts (shown while the queue is empty) ------------------
  const recent = el('section', { class: 'recent', 'aria-label': 'Недавние', hidden: true });
  async function loadRecent() {
    const items = await api.list_history().catch(() => []);
    recent.textContent = '';
    if (!items.length) return;
    recent.append(
      el('div', { class: 'queue__head' }, el('h2', { class: 'h2' }, 'Недавние'),
        el('button', { class: 'link', type: 'button', onclick: () => store.navigate('history') }, 'Вся история')),
      el('div', { class: 'recent__list' }, items.slice(0, 4).map((it) => el('button', { class: 'recent__item', type: 'button', onclick: () => store.navigate('transcript', { id: it.id }) },
        el('span', { class: 'recent__icon' }, icon('mic')),
        el('span', { class: 'recent__main' }, el('span', { class: 'recent__name' }, it.file_name),
          el('span', { class: 'recent__meta' }, `${fmtRelativeDate(it.created_at)} · ${fmtTime(it.duration)}`))))),
    );
    recent.hidden = store.get('jobs').length > 0;
  }

  root.append(
    el('div', { class: 'view view--transcribe' },
      el('div', { class: 'hero' }, drop, hint, optionsRow),
      queue, recent,
    ),
  );
  renderQueue(store.get('jobs'));
  loadRecent();
  const unsub = store.subscribe('jobs', (jobs) => { renderQueue(jobs); recent.hidden = jobs.length > 0 || !recent.children.length; });
  return () => unsub();

  async function pickFiles() {
    const paths = await api.pick_files().catch(() => null);
    if (paths && paths.length) enqueue(paths);
  }
}

/** Shared by pick_files, files_dropped and the browser drop fallback. */
export async function enqueue(paths) {
  if (!options) {
    const s = store.get('app')?.settings || {};
    options = { diarization: s.diarization || 'auto', num_speakers: s.num_speakers ?? null };
  }
  const jobs = await api.enqueue(paths, { ...options }).catch(() => null);
  if (!jobs) return;
  jobs.forEach(store.upsertJob);
  toast.success(`${plural(jobs.length, ['файл добавлен', 'файла добавлено', 'файлов добавлено'])} в очередь`);
}

// ---------------------------------------------------------------------------
// Job cards

function buildCard(job) {
  const stages = el('ol', { class: 'stages', 'aria-label': 'Этапы' });
  for (const s of STAGES) {
    stages.append(el('li', { class: 'stage', dataset: { stage: s.id } },
      el('span', { class: 'stage__node' }), el('span', { class: 'stage__label' }, s.label)));
  }
  const card = el('article', { class: 'job', dataset: { id: job.id } },
    el('div', { class: 'job__head' },
      el('div', { class: 'job__icon' }, icon('mic')),
      el('div', { class: 'job__meta' },
        el('div', { class: 'job__name', title: job.path }, job.file_name),
        el('div', { class: 'job__sub' })),
      el('div', { class: 'job__status' }),
      el('div', { class: 'job__actions' }),
    ),
    stages,
    el('div', { class: 'job__bar', role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': '100' }, el('div', { class: 'job__fill' })),
    el('div', { class: 'job__error', hidden: true }),
  );
  return card;
}

function updateCard(card, job) {
  const status = card.querySelector('.job__status');
  const sub = card.querySelector('.job__sub');
  const actions = card.querySelector('.job__actions');
  const fill = card.querySelector('.job__fill');
  const bar = card.querySelector('.job__bar');
  const err = card.querySelector('.job__error');
  const pct = Math.round((job.progress || 0) * 100);
  card.dataset.status = job.status;

  // Stage pipeline: done → active → pending; diarize skipped when disabled.
  const idx = STAGES.findIndex((s) => s.id === job.stage);
  card.querySelectorAll('.stage').forEach((li, i) => {
    li.classList.remove('is-done', 'is-active', 'is-skipped');
    if (li.dataset.stage === 'diarize' && job.options?.diarization === 'none') li.classList.add('is-skipped');
    if (job.status === 'done') li.classList.add('is-done');
    else if (job.status === 'running' && idx >= 0) {
      if (i < idx) li.classList.add('is-done');
      else if (i === idx) li.classList.add('is-active');
    }
  });

  fill.style.width = (job.status === 'done' ? 100 : pct) + '%';
  bar.setAttribute('aria-valuenow', String(pct));
  err.hidden = job.status !== 'error';
  if (job.status === 'error') err.textContent = job.error || 'Не удалось обработать файл.';

  const dur = job.duration ? fmtTime(job.duration) : null;
  const parts = [];
  if (job.status === 'running') {
    parts.push(`${pct}%`);
    if (job.eta_s != null && job.eta_s > 0) parts.push(`≈ ${fmtSpan(job.eta_s)}`);
    if (job.message) parts.push(job.message);
  } else if (job.status === 'queued') parts.push('В очереди');
  else if (job.status === 'done') {
    const took = job.started_at && job.finished_at ? fmtSpan(job.finished_at - job.started_at) : null;
    parts.push('Готово' + (took ? ` за ${took}` : ''));
  } else if (job.status === 'cancelled') parts.push('Отменено');
  else if (job.status === 'error') parts.push('Ошибка');
  if (dur) parts.unshift(dur);
  sub.textContent = parts.join(' · ');

  status.textContent = '';
  if (job.status === 'running') status.append(el('span', { class: 'pill pill--active' }, el('span', { class: 'pill__pulse' }), `${pct}%`));
  else if (job.status === 'done') status.append(el('span', { class: 'pill pill--success' }, icon('check'), 'Готово'));
  else if (job.status === 'error') status.append(el('span', { class: 'pill pill--danger' }, icon('warning'), 'Ошибка'));
  else if (job.status === 'cancelled') status.append(el('span', { class: 'pill pill--muted' }, 'Отменено'));
  else status.append(el('span', { class: 'pill pill--muted' }, icon('clock'), 'Ожидает'));

  actions.textContent = '';
  if (job.status === 'queued' || job.status === 'running') {
    actions.append(el('button', { class: 'btn btn--ghost btn--sm', type: 'button', onclick: async (e) => {
      e.currentTarget.disabled = true;
      await api.cancel_job(job.id).catch(() => {});
    } }, 'Отменить'));
  } else {
    if (job.status === 'done' && job.transcript_id) {
      actions.append(el('button', { class: 'btn btn--primary btn--sm', type: 'button', onclick: () => store.navigate('transcript', { id: job.transcript_id }) }, 'Открыть'));
    }
    actions.append(el('button', { class: 'btn btn--icon btn--sm', type: 'button', 'aria-label': 'Убрать из списка', title: 'Убрать из списка', onclick: async () => {
      const ok = await api.remove_job(job.id).catch(() => false);
      if (ok) store.removeJob(job.id);
    } }, icon('close')));
  }
}
