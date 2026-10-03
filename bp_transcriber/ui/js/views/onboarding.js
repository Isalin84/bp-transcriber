// First-run overlay: value proposition → model download → optional token → start.

import { api } from '../api.js';
import * as store from '../state.js';
import { el, html } from '../util/format.js';
import { icon } from '../util/icons.js';
import { createTokenPanel } from '../components/token.js';
import { createModelPanel } from '../components/model.js';

const ONBOARDED_KEY = 'bp.onboarded';

export function shouldShow(app) {
  let done = false;
  try { done = localStorage.getItem(ONBOARDED_KEY) === '1'; } catch (_) { /* ignore */ }
  return app.first_run || (!app.models?.gigaam?.available && !done);
}

function heroArt() {
  return html(`
    <svg class="onb__art" viewBox="0 0 240 240" aria-hidden="true">
      <g fill="none" class="art-lines">
        <circle cx="120" cy="120" r="108"/><circle cx="120" cy="120" r="80"/><circle cx="120" cy="120" r="52"/>
        <path d="M120 12v40M120 188v40M12 120h40M188 120h40"/>
        <path d="M44 44l28 28M196 44l-28 28M44 196l28-28M196 196l-28-28"/>
      </g>
      <g class="art-nodes">
        <circle cx="120" cy="12" r="4"/><circle cx="120" cy="228" r="4"/><circle cx="12" cy="120" r="4"/><circle cx="228" cy="120" r="4"/>
        <circle cx="44" cy="44" r="3"/><circle cx="196" cy="44" r="3"/><circle cx="44" cy="196" r="3"/><circle cx="196" cy="196" r="3"/>
      </g>
      <g fill="none" class="art-wave"><path d="M80 120c6-22 12-22 18 0s12 22 18 0 12-22 18 0 12 22 18 0 12-22 18 0"/></g>
    </svg>`);
}

/** Show the overlay; resolves when the user presses «Начать». */
export function show() {
  return new Promise((resolve) => {
    const app = store.get('app');
    let step = 0;
    const model = createModelPanel({ onDone: () => paintNav() });
    const token = createTokenPanel({ compact: true });

    const steps = [
      {
        title: 'Речь — в текст. На вашем компьютере.',
        body: el('div', { class: 'onb__intro' },
          heroArt(),
          el('div', null,
            el('p', { class: 'onb__lead' }, 'BP Transcriber распознаёт русскую речь из аудио и видео, разделяет реплики по спикерам и отдаёт готовый документ — Word, субтитры или просто текст.'),
            el('ul', { class: 'onb__list' },
              el('li', null, icon('check'), 'Работает офлайн: записи не покидают компьютер'),
              el('li', null, icon('check'), 'Точность GigaAM — одной из лучших моделей для русского языка'),
              el('li', null, icon('check'), 'Таймкоды по словам, поиск и правка прямо в приложении')))),
      },
      { title: 'Шаг 1 из 2 — модель распознавания', body: el('div', null, el('p', { class: 'muted onb__p' }, 'Нужна один раз, потом всё работает без интернета.'), model.el) },
      { title: 'Шаг 2 из 2 — спикеры (по желанию)', body: el('div', null, el('p', { class: 'muted onb__p' }, 'С токеном HuggingFace реплики разделяются точнее. Этот шаг можно пропустить и вернуться к нему в настройках.'), token.el) },
    ];

    const title = el('h1', { class: 'onb__title' });
    const body = el('div', { class: 'onb__body' });
    const back = el('button', { class: 'btn btn--ghost', type: 'button', onclick: () => go(step - 1) }, 'Назад');
    const next = el('button', { class: 'btn btn--primary btn--lg', type: 'button', onclick: () => go(step + 1) });
    const dots = el('div', { class: 'onb__dots', 'aria-hidden': 'true' }, steps.map(() => el('span', { class: 'onb__dot' })));
    const panel = el('div', { class: 'onb', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'onb-title' },
      el('div', { class: 'onb__brand' }, el('img', { src: 'img/logo-mark.svg', alt: '', width: '40', height: '40' }), el('div', null, el('div', { class: 'brand__name' }, 'BP Transcriber'), el('div', { class: 'brand__sub' }, 'Транскрибатор русской речи'))),
      title, body,
      el('div', { class: 'onb__nav' }, back, dots, next),
      el('div', { class: 'onb__footer' }, 'Создано с Best Practice AI · Экспертиза. Инновации. Результат.'),
    );
    title.id = 'onb-title';
    const overlay = el('div', { class: 'overlay overlay--onb' }, panel);
    document.getElementById('overlays').append(overlay);
    requestAnimationFrame(() => overlay.classList.add('is-in'));

    function paintNav() {
      const modelReady = !!store.get('app')?.models?.gigaam?.available;
      back.classList.toggle('is-invisible', step === 0);
      const last = step === steps.length - 1;
      next.textContent = step === 0 ? 'Продолжить' : last ? 'Начать' : (modelReady ? 'Дальше' : 'Пропустить пока');
      dots.querySelectorAll('.onb__dot').forEach((d, i) => d.classList.toggle('is-active', i === step));
    }
    function go(i) {
      if (i >= steps.length) return finish();
      step = Math.max(0, i);
      title.textContent = steps[step].title;
      body.textContent = ''; body.append(steps[step].body);
      panel.dataset.step = String(step);
      paintNav();
      next.focus();
    }
    function finish() {
      try { localStorage.setItem(ONBOARDED_KEY, '1'); } catch (_) { /* ignore */ }
      // The backend persists onboarding_done; mirror it locally so the overlay does not return.
      store.patchApp({ first_run: false });
      api.complete_onboarding().catch(() => {});
      model.destroy();
      overlay.classList.remove('is-in');
      setTimeout(() => overlay.remove(), 240);
      resolve();
    }
    go(0);
  });
}
