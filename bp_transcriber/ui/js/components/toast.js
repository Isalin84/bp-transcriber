// Toasts: top-right stack, auto-dismiss, optional action button.

import { el } from '../util/format.js';

const ICONS = {
  info: '<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="7.5" fill="none"/><path d="M10 9v5M10 6.5v.5" fill="none"/></svg>',
  success: '<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="7.5" fill="none"/><path d="m6.5 10.3 2.3 2.3 4.7-5" fill="none"/></svg>',
  warning: '<svg viewBox="0 0 20 20"><path d="M10 3 2.5 16.5h15L10 3Z" fill="none"/><path d="M10 8v4M10 14v.5" fill="none"/></svg>',
  error: '<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="7.5" fill="none"/><path d="m7.5 7.5 5 5M12.5 7.5l-5 5" fill="none"/></svg>',
};

function container() { return document.getElementById('toasts'); }

/**
 * show('Готово', {level, action: {label, onClick}, timeout})
 * Returns a handle with .close().
 */
export function show(message, { level = 'info', action = null, timeout = null } = {}) {
  const root = container();
  if (!root) return { close() {} };
  const icon = el('span', { class: 'toast__icon', 'aria-hidden': 'true' });
  icon.innerHTML = ICONS[level] || ICONS.info;
  const node = el('div', { class: `toast toast--${level}`, role: level === 'error' ? 'alert' : 'status' },
    icon,
    el('div', { class: 'toast__body' }, el('div', { class: 'toast__msg' }, message),
      action ? el('button', { class: 'toast__action', type: 'button', onclick: () => { close(); action.onClick?.(); } }, action.label) : null),
    el('button', { class: 'toast__close', type: 'button', 'aria-label': 'Закрыть', onclick: () => close() }, '×'),
  );
  root.append(node);
  requestAnimationFrame(() => node.classList.add('is-in'));
  const ms = timeout ?? (action ? 9000 : level === 'error' ? 7000 : 4000);
  let timer = setTimeout(close, ms);
  node.addEventListener('mouseenter', () => clearTimeout(timer));
  node.addEventListener('mouseleave', () => { timer = setTimeout(close, 2500); });
  let closed = false;
  function close() {
    if (closed) return; closed = true;
    clearTimeout(timer);
    node.classList.remove('is-in');
    node.classList.add('is-out');
    setTimeout(() => node.remove(), 240);
  }
  // Keep at most 4 visible.
  const all = root.querySelectorAll('.toast');
  if (all.length > 4) all[0].remove();
  return { close };
}

export const toast = {
  show,
  info: (m, o) => show(m, { ...o, level: 'info' }),
  success: (m, o) => show(m, { ...o, level: 'success' }),
  warning: (m, o) => show(m, { ...o, level: 'warning' }),
  error: (m, o) => show(m, { ...o, level: 'error' }),
};
