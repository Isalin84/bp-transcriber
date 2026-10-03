// Sticky audio player for the transcript view.
// Emits onTime(sec) on each animation frame while playing (and on seek) — the view
// decides what to highlight; the player never touches the transcript DOM.

import { el, fmtTime } from '../util/format.js';
import { openMenu } from './dropdown.js';

const SPEEDS = [0.75, 1, 1.25, 1.5, 2];

const ICON_PLAY = '<svg viewBox="0 0 20 20"><path d="M6 4.5v11l9-5.5-9-5.5Z"/></svg>';
const ICON_PAUSE = '<svg viewBox="0 0 20 20"><path d="M5.5 4.5h3v11h-3zM11.5 4.5h3v11h-3z"/></svg>';
const ICON_BACK = '<svg viewBox="0 0 20 20"><path d="M9.5 4.5 4 10l5.5 5.5" fill="none"/><path d="M4 10h12" fill="none"/></svg>';
const ICON_FWD = '<svg viewBox="0 0 20 20"><path d="m10.5 4.5 5.5 5.5-5.5 5.5" fill="none"/><path d="M16 10H4" fill="none"/></svg>';

export function createPlayer({ src, duration = 0, onTime, onState }) {
  const audio = new Audio();
  audio.preload = 'metadata';
  audio.src = src;
  let playing = false;
  let raf = 0;
  let scrubbing = false;
  let total = duration || 0;

  const playBtn = el('button', { class: 'player__btn player__btn--main', type: 'button', 'aria-label': 'Воспроизвести' });
  playBtn.innerHTML = ICON_PLAY;
  const backBtn = el('button', { class: 'player__btn', type: 'button', 'aria-label': 'Назад на 5 секунд', title: '−5 с' });
  backBtn.innerHTML = ICON_BACK + '<span class="player__skip">5</span>';
  const fwdBtn = el('button', { class: 'player__btn', type: 'button', 'aria-label': 'Вперёд на 5 секунд', title: '+5 с' });
  fwdBtn.innerHTML = '<span class="player__skip">5</span>' + ICON_FWD;
  const cur = el('span', { class: 'player__time' }, '0:00');
  const tot = el('span', { class: 'player__time player__time--total' }, fmtTime(total));
  const range = el('input', { class: 'player__range', type: 'range', min: '0', max: '1000', value: '0', step: '1', 'aria-label': 'Позиция воспроизведения' });
  const fill = el('div', { class: 'player__fill' });
  const track = el('div', { class: 'player__track' }, fill, range);
  const speedBtn = el('button', { class: 'player__btn player__speed', type: 'button', 'aria-haspopup': 'menu', 'aria-expanded': 'false', title: 'Скорость' }, '1×');
  const speedWrap = el('div', { class: 'dropdown' }, speedBtn);

  const root = el('div', { class: 'player', role: 'region', 'aria-label': 'Аудиоплеер' },
    el('div', { class: 'player__controls' }, backBtn, playBtn, fwdBtn),
    cur, track, tot, speedWrap,
  );

  function setPlaying(v) {
    playing = v;
    playBtn.innerHTML = v ? ICON_PAUSE : ICON_PLAY;
    playBtn.setAttribute('aria-label', v ? 'Пауза' : 'Воспроизвести');
    root.classList.toggle('is-playing', v);
    onState?.(v);
    if (v) tick(); else cancelAnimationFrame(raf);
  }
  function paint(t) {
    if (!scrubbing) {
      const p = total ? Math.min(1, t / total) : 0;
      range.value = String(Math.round(p * 1000));
      fill.style.width = (p * 100).toFixed(2) + '%';
    }
    cur.textContent = fmtTime(t);
  }
  function tick() {
    const t = audio.currentTime;
    paint(t);
    onTime?.(t);
    if (playing) raf = requestAnimationFrame(tick);
  }
  let pendingSeek = null; // seek requested before metadata is available
  function seek(t) {
    t = Math.max(0, Math.min(total || t, t));
    if (audio.readyState < 1) { pendingSeek = t; audio.load(); }
    else audio.currentTime = t;
    paint(t);
    onTime?.(t, true);
  }

  playBtn.addEventListener('click', () => toggle());
  backBtn.addEventListener('click', () => seek(audio.currentTime - 5));
  fwdBtn.addEventListener('click', () => seek(audio.currentTime + 5));
  range.addEventListener('pointerdown', () => { scrubbing = true; });
  range.addEventListener('input', () => {
    const t = (Number(range.value) / 1000) * total;
    fill.style.width = (Number(range.value) / 10).toFixed(2) + '%';
    cur.textContent = fmtTime(t);
  });
  const commit = () => { if (!scrubbing) return; scrubbing = false; seek((Number(range.value) / 1000) * total); };
  range.addEventListener('pointerup', commit);
  range.addEventListener('change', commit);
  speedBtn.addEventListener('click', () => openMenu(speedBtn, SPEEDS.map((s) => ({
    label: s === 1 ? 'Обычная' : `${s}×`, hint: s === audio.playbackRate ? '✓' : '',
    onSelect: () => { audio.playbackRate = s; speedBtn.textContent = `${s}×`; },
  })), { align: 'right' }));

  audio.addEventListener('loadedmetadata', () => {
    if (isFinite(audio.duration) && audio.duration > 0) { total = audio.duration; tot.textContent = fmtTime(total); }
    if (pendingSeek != null) { audio.currentTime = Math.min(pendingSeek, total || pendingSeek); pendingSeek = null; }
  });
  audio.addEventListener('play', () => setPlaying(true));
  audio.addEventListener('pause', () => setPlaying(false));
  audio.addEventListener('ended', () => setPlaying(false));
  audio.addEventListener('error', () => {
    root.classList.add('is-error');
    root.title = 'Не удалось загрузить аудио';
    for (const b of [playBtn, backBtn, fwdBtn, range]) b.disabled = true;
  });

  function toggle() { if (audio.paused) audio.play().catch(() => {}); else audio.pause(); }

  return {
    el: root,
    toggle,
    play: () => audio.play().catch(() => {}),
    pause: () => audio.pause(),
    seek,
    skip: (dt) => seek(audio.currentTime + dt),
    get currentTime() { return audio.currentTime; },
    get playing() { return playing; },
    destroy() { cancelAnimationFrame(raf); audio.pause(); audio.removeAttribute('src'); audio.load(); root.remove(); },
  };
}
