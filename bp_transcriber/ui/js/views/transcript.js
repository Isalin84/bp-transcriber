// Transcript view: header actions, speaker chips, search, segment list with inline
// editing, sticky player with word-level highlighting.
//
// Performance notes (2 h ≈ 1500 segments / 20k words):
// - segments are rendered in batches (first batch sync, the rest in idle time);
// - .seg uses content-visibility:auto (css) so off-screen segments are cheap;
// - playback highlight only toggles classes on the previous/next word & segment,
//   found by binary search over flat start-time arrays.

import { api } from '../api.js';
import * as store from '../state.js';
import { el, fmtTime, fmtDate, fmtSpan, norm, prefersReducedMotion, debounce } from '../util/format.js';
import { icon } from '../util/icons.js';
import { toast } from '../components/toast.js';
import { openMenu } from '../components/dropdown.js';
import { createPlayer } from '../components/player.js';

const FORMATS = [
  { id: 'txt', label: 'Текст (.txt)' },
  { id: 'md', label: 'Markdown (.md)' },
  { id: 'docx', label: 'Word (.docx)' },
  { id: 'srt', label: 'Субтитры SRT' },
  { id: 'vtt', label: 'Субтитры VTT' },
  { id: 'json', label: 'JSON с таймингами' },
];
const DIARIZATION_LABEL = { pyannote: 'pyannote', hybrid: 'гибридный режим', none: 'без спикеров', auto: 'авто' };
const FIRST_BATCH = 120;
const BATCH = 200;

let activePlayer = null;
let searchInput = null;

/** Used by main.js for the Space shortcut. */
export const getPlayer = () => activePlayer;
export const focusSearch = () => { searchInput?.focus(); searchInput?.select(); };

export function render(root, { id } = {}) {
  let destroyed = false;
  const view = el('div', { class: 'view view--transcript' },
    el('div', { class: 'loading' }, el('div', { class: 'spinner' }), 'Открываем транскрипт…'));
  root.append(view);

  const cleanup = { fns: [] };
  api.load_transcript(id).then((t) => {
    if (destroyed) return;
    store.set('transcript', t);
    view.textContent = '';
    mount(view, t, cleanup);
  }).catch((e) => {
    console.error('[bp] transcript view failed', e);
    if (destroyed) return;
    view.textContent = '';
    view.append(el('div', { class: 'empty' },
      el('h2', { class: 'h2' }, 'Не удалось открыть транскрипт'),
      el('button', { class: 'btn btn--ghost', type: 'button', onclick: () => store.navigate('history') }, 'К истории')));
  });

  return () => {
    destroyed = true;
    cleanup.fns.forEach((f) => f());
    activePlayer?.destroy(); activePlayer = null; searchInput = null;
    store.set('transcript', null);
  };
}

function mount(view, t, cleanup) {
  const speakersById = new Map(t.speakers.map((s) => [s.id, s]));
  const segEls = []; // index → element (filled as batches render)

  // ---------------------------------------------------------------- header
  const copyBtn = el('button', { class: 'btn btn--ghost', type: 'button', 'aria-haspopup': 'menu', 'aria-expanded': 'false' }, icon('copy'), 'Копировать', icon('chevronDown', 'icon icon--caret'));
  copyBtn.addEventListener('click', () => openMenu(copyBtn, [
    { label: 'С таймкодами', onSelect: () => copy(true) },
    { label: 'Без таймкодов', onSelect: () => copy(false) },
  ]));
  const exportBtn = el('button', { class: 'btn btn--primary', type: 'button', 'aria-haspopup': 'menu', 'aria-expanded': 'false' }, icon('export'), 'Экспорт', icon('chevronDown', 'icon icon--caret'));
  exportBtn.addEventListener('click', () => openMenu(exportBtn, FORMATS.map((f) => ({ label: f.label, onSelect: () => doExport(f.id) })), { align: 'right' }));

  const meta = [
    fmtTime(t.duration), fmtDate(t.created_at), t.device,
    DIARIZATION_LABEL[t.diarization] || t.diarization,
    t.processing_time ? `обработано за ${fmtSpan(t.processing_time)}` : null,
  ].filter(Boolean);

  const head = el('header', { class: 'tr-head' },
    el('button', { class: 'btn btn--icon', type: 'button', 'aria-label': 'Назад', title: 'Назад', onclick: () => store.navigate(store.get('jobs').length ? 'transcribe' : 'history') }, icon('back')),
    el('div', { class: 'tr-head__title' },
      el('h1', { class: 'h1 tr-head__name', title: t.source_path }, t.file_name),
      el('div', { class: 'tr-head__meta' }, meta.map((m, i) => [i ? el('span', { class: 'dot' }) : null, el('span', null, m)]))),
    el('div', { class: 'tr-head__actions' }, el('div', { class: 'dropdown' }, copyBtn), el('div', { class: 'dropdown' }, exportBtn)),
  );

  // ---------------------------------------------------------------- speakers
  const chips = el('div', { class: 'speakers', 'aria-label': 'Спикеры' });
  const counts = new Map();
  for (const s of t.segments) counts.set(s.speaker, (counts.get(s.speaker) || 0) + 1);
  for (const sp of t.speakers) chips.append(buildChip(sp, counts.get(sp.id) || 0));

  function buildChip(sp, n) {
    const chip = el('button', { class: 'spk-chip', type: 'button', style: `--c: var(--spk-${sp.color % 8})`, dataset: { spk: sp.id }, title: 'Переименовать' },
      el('span', { class: 'spk-chip__dot' }), el('span', { class: 'spk-chip__name' }, sp.name),
      n ? el('span', { class: 'spk-chip__n' }, String(n)) : null, icon('edit', 'icon icon--edit'));
    chip.addEventListener('click', () => renameInline(chip, sp));
    return chip;
  }
  function renameInline(chip, sp) {
    const input = el('input', { class: 'spk-chip__input', type: 'text', value: sp.name, maxlength: '40', 'aria-label': 'Имя спикера', style: `--c: var(--spk-${sp.color % 8})` });
    chip.replaceWith(input);
    input.focus(); input.select();
    let done = false;
    const finish = async (save) => {
      if (done) return; done = true;
      const name = input.value.trim();
      if (save && name && name !== sp.name) {
        const updated = await api.rename_speaker(t.id, sp.id, name).catch(() => null);
        if (updated) {
          sp.name = name;
          store.set('transcript', updated);
          view.querySelectorAll(`.seg__spk[data-spk="${CSS.escape(sp.id)}"]`).forEach((b) => { b.textContent = name; });
          toast.success('Спикер переименован');
        }
      }
      input.replaceWith(buildChip(sp, counts.get(sp.id) || 0));
    };
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); finish(true); }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); finish(false); }
    });
    input.addEventListener('blur', () => finish(true));
  }

  // ---------------------------------------------------------------- search
  const sInput = el('input', { class: 'search__input', type: 'search', placeholder: `Поиск по тексту  ${store.modKey()} F`, 'aria-label': 'Поиск по транскрипту', autocomplete: 'off', spellcheck: 'false' });
  searchInput = sInput;
  const sCount = el('span', { class: 'search__count', 'aria-live': 'polite' });
  const sPrev = el('button', { class: 'btn btn--icon btn--sm', type: 'button', 'aria-label': 'Предыдущее совпадение', disabled: true }, icon('chevronUp'));
  const sNext = el('button', { class: 'btn btn--icon btn--sm', type: 'button', 'aria-label': 'Следующее совпадение', disabled: true }, icon('chevronDown'));
  const search = el('div', { class: 'search' }, icon('search', 'icon search__icon'), sInput, sCount, sPrev, sNext);

  let matches = [];     // [{seg, start, end}] in text offsets
  let matchIdx = -1;
  let hitSegs = new Set();
  const segText = (s) => (s.words && s.words.length ? s.words.map((w) => w.w).join(' ') : s.text);

  function runSearch() {
    const q = norm(sInput.value.trim());
    clearHits();
    matches = []; matchIdx = -1;
    if (q.length >= 1) {
      t.segments.forEach((s, si) => {
        const text = norm(segText(s));
        let p = text.indexOf(q);
        while (p >= 0) { matches.push({ seg: si, start: p, end: p + q.length }); p = text.indexOf(q, p + q.length); }
      });
      for (const m of matches) { const e = segEls[m.seg]; if (e) markSeg(e, m.seg); }
      if (matches.length) gotoMatch(0);
    }
    updateCount();
  }
  function updateCount() {
    const q = sInput.value.trim();
    sCount.textContent = !q ? '' : matches.length ? `${matchIdx + 1} из ${matches.length}` : 'Нет совпадений';
    sPrev.disabled = sNext.disabled = matches.length < 2;
    search.classList.toggle('is-empty', !!q && !matches.length);
  }
  function clearHits() {
    for (const i of hitSegs) {
      const e = segEls[i]; if (!e) continue;
      e.querySelectorAll('.w.hit, .w.hit-current').forEach((w) => w.classList.remove('hit', 'hit-current'));
      if (e.querySelector('mark')) renderText(e.querySelector('.seg__text'), t.segments[i]);
    }
    hitSegs = new Set();
  }
  /** Mark search hits inside one segment element (word spans get .hit; plain text gets <mark>). */
  function markSeg(e, si, current = -1) {
    const s = t.segments[si];
    const ms = matches.filter((m) => m.seg === si);
    if (!ms.length) return;
    hitSegs.add(si);
    const textEl = e.querySelector('.seg__text');
    const words = textEl.querySelectorAll('.w');
    if (words.length) {
      let off = 0;
      words.forEach((w) => {
        const len = w.textContent.length;
        const a = off, b = off + len;
        for (const m of ms) if (m.start < b && m.end > a) { w.classList.add('hit'); if (matches[current] === m) w.classList.add('hit-current'); }
        off = b + 1;
      });
    } else {
      textEl.textContent = '';
      const text = s.text; let pos = 0;
      for (const m of ms) {
        if (m.start > pos) textEl.append(text.slice(pos, m.start));
        textEl.append(el('mark', { class: matches[current] === m ? 'hit-current' : null }, text.slice(m.start, m.end)));
        pos = m.end;
      }
      if (pos < text.length) textEl.append(text.slice(pos));
    }
  }
  function gotoMatch(i) {
    if (!matches.length) return;
    const prev = matchIdx >= 0 ? matches[matchIdx] : null;
    matchIdx = (i + matches.length) % matches.length;
    const m = matches[matchIdx];
    if (prev && segEls[prev.seg]) segEls[prev.seg].querySelectorAll('.hit-current').forEach((n) => n.classList.remove('hit-current'));
    const e = segEls[m.seg];
    if (e) {
      e.querySelectorAll('.w.hit, mark').forEach((w) => w.classList.remove('hit', 'hit-current'));
      if (e.querySelector('mark')) renderText(e.querySelector('.seg__text'), t.segments[m.seg]);
      markSeg(e, m.seg, matchIdx);
      scrollTo(e, true);
    }
    updateCount();
  }
  sInput.addEventListener('input', debounce(runSearch, 120));
  sInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); gotoMatch(matchIdx + (e.shiftKey ? -1 : 1)); }
    else if (e.key === 'Escape') { e.preventDefault(); if (sInput.value) { sInput.value = ''; runSearch(); } else sInput.blur(); }
  });
  sPrev.addEventListener('click', () => gotoMatch(matchIdx - 1));
  sNext.addEventListener('click', () => gotoMatch(matchIdx + 1));

  const toolbar = el('div', { class: 'tr-toolbar' }, chips, search);

  // ---------------------------------------------------------------- segments
  const body = el('div', { class: 'tr-body', tabindex: '-1' });
  const list = el('div', { class: 'segments' });
  body.append(list);
  if (!t.segments.length) {
    list.append(el('div', { class: 'empty empty--inline' }, el('p', null, 'В записи не найдено речи.')));
  }

  function renderText(textEl, s) {
    textEl.textContent = '';
    if (s.words && s.words.length) {
      const frag = document.createDocumentFragment();
      s.words.forEach((w, i) => {
        if (i) frag.append(' ');
        frag.append(el('span', { class: 'w' }, w.w));
      });
      textEl.append(frag);
    } else textEl.append(s.text);
  }
  function buildSeg(s, i) {
    const sp = s.speaker ? speakersById.get(s.speaker) : null;
    const prev = t.segments[i - 1];
    const cont = prev && prev.speaker === s.speaker;
    const textEl = el('div', { class: 'seg__text' });
    renderText(textEl, s);
    const timeBtn = el('button', { class: 'seg__time', type: 'button', title: 'Перейти к этому месту' }, fmtTime(s.start));
    timeBtn.addEventListener('click', () => { if (activePlayer) { activePlayer.seek(s.start); activePlayer.play(); } });
    const spkBtn = el('span', { class: 'seg__spk', dataset: { spk: s.speaker || '' }, style: sp ? `--c: var(--spk-${sp.color % 8})` : null }, sp ? sp.name : (s.speaker ? s.speaker : ''));
    const editBtn = el('button', { class: 'seg__edit', type: 'button', 'aria-label': 'Редактировать', title: 'Редактировать (двойной клик по тексту)' }, icon('edit'));
    const e = el('article', { class: 'seg' + (cont ? ' seg--cont' : ''), dataset: { i: String(i) } },
      el('div', { class: 'seg__side' }, spkBtn, timeBtn), textEl, editBtn);
    editBtn.addEventListener('click', () => startEdit(e, i));
    textEl.addEventListener('dblclick', (ev) => { if (!textEl.isContentEditable) { ev.preventDefault(); startEdit(e, i); } });
    return e;
  }
  function renderBatch(from, to) {
    const frag = document.createDocumentFragment();
    for (let i = from; i < Math.min(to, t.segments.length); i++) {
      const e = buildSeg(t.segments[i], i);
      segEls[i] = e; frag.append(e);
      if (matches.length) markSeg(e, i, matchIdx);
    }
    list.append(frag);
  }
  renderBatch(0, FIRST_BATCH);
  let next = FIRST_BATCH;
  let batchTimer = 0;
  const idle = window.requestIdleCallback || ((fn) => setTimeout(fn, 16));
  const more = () => {
    if (next >= t.segments.length) return;
    renderBatch(next, next + BATCH); next += BATCH;
    batchTimer = idle(more);
  };
  batchTimer = idle(more);
  cleanup.fns.push(() => (window.cancelIdleCallback || clearTimeout)(batchTimer));

  // ---------------------------------------------------------------- editing
  let editing = null; // {el, i, original}
  function startEdit(e, i) {
    if (editing) cancelEdit();
    const textEl = e.querySelector('.seg__text');
    const s = t.segments[i];
    editing = { el: e, i, original: s.text };
    textEl.textContent = s.text;
    textEl.setAttribute('contenteditable', 'plaintext-only');
    if (!textEl.isContentEditable) textEl.setAttribute('contenteditable', 'true');
    e.classList.add('is-editing');
    textEl.focus();
    const sel = getSelection(); const r = document.createRange(); r.selectNodeContents(textEl); r.collapse(false); sel.removeAllRanges(); sel.addRange(r);
    textEl.addEventListener('keydown', onEditKey);
    textEl.addEventListener('blur', onEditBlur);
  }
  function onEditKey(ev) {
    if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); saveEdit(); }
    else if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); cancelEdit(); }
  }
  function onEditBlur() { saveEdit(); }
  function endEdit() {
    if (!editing) return null;
    const { el: e, i } = editing;
    const textEl = e.querySelector('.seg__text');
    textEl.removeEventListener('keydown', onEditKey);
    textEl.removeEventListener('blur', onEditBlur);
    textEl.removeAttribute('contenteditable');
    e.classList.remove('is-editing');
    const st = editing; editing = null;
    return { ...st, textEl, i };
  }
  function cancelEdit() {
    const st = endEdit(); if (!st) return;
    renderText(st.textEl, t.segments[st.i]);
  }
  async function saveEdit() {
    const st = endEdit(); if (!st) return;
    const text = st.textEl.textContent.replace(/\s+/g, ' ').trim();
    if (!text || text === st.original) { renderText(st.textEl, t.segments[st.i]); return; }
    st.el.classList.add('is-saving');
    const updated = await api.edit_segment(t.id, st.i, text).catch(() => null);
    st.el.classList.remove('is-saving');
    if (updated) {
      t.segments[st.i] = updated.segments[st.i] || { ...t.segments[st.i], text, words: [] };
      store.set('transcript', updated);
      buildIndex();
      toast.success('Изменения сохранены');
    }
    renderText(st.textEl, t.segments[st.i]);
    if (matches.length) runSearch();
  }
  cleanup.fns.push(() => { if (editing) cancelEdit(); });

  // ---------------------------------------------------------------- playback index
  let wS = [], wE = [], wSeg = [], wIdx = [], segS = [];
  function buildIndex() {
    wS = []; wE = []; wSeg = []; wIdx = []; segS = t.segments.map((s) => s.start);
    t.segments.forEach((s, si) => (s.words || []).forEach((w, wi) => { wS.push(w.s); wE.push(w.e); wSeg.push(si); wIdx.push(wi); }));
  }
  buildIndex();
  /** Index of the last element of arr that is <= v (binary search), or -1. */
  function lowerIdx(arr, v) {
    let lo = 0, hi = arr.length - 1, ans = -1;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (arr[mid] <= v) { ans = mid; lo = mid + 1; } else hi = mid - 1; }
    return ans;
  }
  let curWord = null, curSeg = null, curSegIdx = -1;
  let userScrolledAt = 0;
  function onTime(time, isSeek) {
    // Segment
    const si = lowerIdx(segS, time);
    if (si !== curSegIdx) {
      curSeg?.classList.remove('is-active');
      curSegIdx = si; curSeg = si >= 0 ? segEls[si] : null;
      curSeg?.classList.add('is-active');
      if (curSeg && (isSeek || performance.now() - userScrolledAt > 3000)) scrollTo(curSeg, false);
    }
    // Word
    const wi = lowerIdx(wS, time);
    const inWord = wi >= 0 && time <= wE[wi] + 0.15;
    const target = inWord ? segEls[wSeg[wi]]?.querySelectorAll('.w')[wIdx[wi]] || null : null;
    if (target !== curWord) {
      curWord?.classList.remove('is-current');
      curWord = target;
      curWord?.classList.add('is-current');
    }
  }
  let programmaticUntil = 0;
  function scrollTo(e, center) {
    programmaticUntil = performance.now() + 800;
    e.scrollIntoView({ block: center ? 'center' : 'nearest', behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
  }
  const markUserScroll = () => { if (performance.now() > programmaticUntil) userScrolledAt = performance.now(); };
  body.addEventListener('wheel', markUserScroll, { passive: true });
  body.addEventListener('touchmove', markUserScroll, { passive: true });
  body.addEventListener('scroll', markUserScroll, { passive: true });

  // ---------------------------------------------------------------- player
  let playerEl = null;
  if (t.media_url) {
    activePlayer = createPlayer({ src: t.media_url, duration: t.duration, onTime });
    playerEl = activePlayer.el;
    view.classList.add('has-player');
  } else {
    playerEl = el('div', { class: 'player player--none' }, icon('info'), el('span', null, 'Аудио для прослушивания недоступно — текст можно читать, искать и редактировать.'));
  }

  view.append(head, toolbar, body, playerEl);

  // ---------------------------------------------------------------- actions
  async function copy(withTs) {
    const ok = await api.copy_text(t.id, withTs).catch(() => false);
    if (ok) toast.success(withTs ? 'Скопировано с таймкодами' : 'Текст скопирован');
  }
  async function doExport(format) {
    const res = await api.export(t.id, format).catch(() => null);
    if (!res || !res.path) return;
    const label = store.isMac() ? 'Показать в Finder' : 'Показать в Проводнике';
    toast.success(`Сохранено: ${res.path.split(/[\\/]/).pop()}`, { action: { label, onClick: () => api.reveal(res.path) } });
  }
}
