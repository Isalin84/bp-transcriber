// Modal dialogs: confirm() returns a Promise<boolean>; open() for custom content.
// Esc closes, focus is trapped inside, the opener gets focus back.

import { el } from '../util/format.js';

let stack = [];

function overlays() { return document.getElementById('overlays'); }

/**
 * open({ title, body: Node|string, actions: [{label, kind:'primary'|'danger'|'ghost', value, autofocus}], dismissible })
 * Resolves with the clicked action's value, or null if dismissed.
 */
export function open({ title, body, actions = [], dismissible = true, className = '', onRender } = {}) {
  return new Promise((resolve) => {
    const opener = document.activeElement;
    const btns = actions.map((a) => el('button', {
      class: `btn ${a.kind === 'primary' ? 'btn--primary' : a.kind === 'danger' ? 'btn--danger' : 'btn--ghost'}`,
      type: 'button', 'data-autofocus': a.autofocus ? '' : null,
      onclick: () => close(a.value),
    }, a.label));

    const panel = el('div', { class: `dialog ${className}`, role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'dlg-title' },
      title ? el('h2', { class: 'dialog__title', id: 'dlg-title' }, title) : null,
      el('div', { class: 'dialog__body' }, body),
      btns.length ? el('div', { class: 'dialog__actions' }, ...btns) : null,
    );
    const backdrop = el('div', { class: 'overlay' }, panel);
    backdrop.addEventListener('mousedown', (e) => { if (dismissible && e.target === backdrop) close(null); });

    function onKey(e) {
      if (e.key === 'Escape' && dismissible) { e.preventDefault(); e.stopPropagation(); close(null); }
      if (e.key === 'Tab') trapTab(e, panel);
    }
    function close(value) {
      document.removeEventListener('keydown', onKey, true);
      backdrop.classList.remove('is-in');
      stack = stack.filter((s) => s !== backdrop);
      setTimeout(() => backdrop.remove(), 200);
      if (opener && opener.focus) opener.focus();
      resolve(value);
    }
    overlays().append(backdrop);
    stack.push(backdrop);
    document.addEventListener('keydown', onKey, true);
    requestAnimationFrame(() => {
      backdrop.classList.add('is-in');
      (panel.querySelector('[data-autofocus]') || panel.querySelector('input, button'))?.focus();
    });
    onRender?.({ panel, close });
  });
}

function trapTab(e, panel) {
  const f = [...panel.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')]
    .filter((n) => !n.disabled && n.offsetParent !== null);
  if (!f.length) return;
  const first = f[0], last = f[f.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
}

/** confirm('Удалить запись?', {detail, okLabel, danger}) → Promise<boolean> */
export function confirm(title, { detail = '', okLabel = 'OK', cancelLabel = 'Отмена', danger = false } = {}) {
  return open({
    title,
    body: detail ? el('p', { class: 'dialog__text' }, detail) : el('span'),
    actions: [
      { label: cancelLabel, kind: 'ghost', value: false, autofocus: danger },
      { label: okLabel, kind: danger ? 'danger' : 'primary', value: true, autofocus: !danger },
    ],
  }).then((v) => v === true);
}

export const isDialogOpen = () => stack.length > 0;
