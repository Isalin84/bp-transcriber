// Dropdown menu anchored to a trigger button. Keyboard: arrows, Enter, Esc.

import { el } from '../util/format.js';

let current = null;

/**
 * openMenu(trigger, items) — items: [{label, hint?, onSelect, disabled?, separator?}]
 * Only one menu is open at a time; clicking outside or Esc closes it.
 */
export function openMenu(trigger, items, { align = 'left' } = {}) {
  closeMenu();
  const menu = el('div', { class: `menu menu--${align}`, role: 'menu' });
  for (const it of items) {
    if (it.separator) { menu.append(el('div', { class: 'menu__sep', role: 'separator' })); continue; }
    const b = el('button', {
      class: 'menu__item', type: 'button', role: 'menuitem', disabled: it.disabled || null,
      onclick: () => { closeMenu(); it.onSelect?.(); },
    }, el('span', { class: 'menu__label' }, it.label), it.hint ? el('span', { class: 'menu__hint' }, it.hint) : null);
    menu.append(b);
  }
  const wrap = trigger.closest('.dropdown') || trigger.parentElement;
  wrap.append(menu);
  trigger.setAttribute('aria-expanded', 'true');
  current = { menu, trigger, onDoc, onKey };
  requestAnimationFrame(() => {
    menu.classList.add('is-in');
    // Keep inside the viewport.
    const r = menu.getBoundingClientRect();
    if (r.right > innerWidth - 8) menu.classList.add('menu--right');
    if (r.bottom > innerHeight - 8) menu.classList.add('menu--up');
    document.addEventListener('mousedown', onDoc, true);
    document.addEventListener('keydown', onKey, true);
    menu.querySelector('.menu__item:not([disabled])')?.focus();
  });

  function onDoc(e) { if (!menu.contains(e.target) && e.target !== trigger) closeMenu(); }
  function onKey(e) {
    const items = [...menu.querySelectorAll('.menu__item:not([disabled])')];
    const i = items.indexOf(document.activeElement);
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeMenu(); trigger.focus(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length]?.focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length]?.focus(); }
    else if (e.key === 'Tab') closeMenu();
  }
}

export function closeMenu() {
  if (!current) return;
  const { menu, trigger, onDoc, onKey } = current;
  document.removeEventListener('mousedown', onDoc, true);
  document.removeEventListener('keydown', onKey, true);
  trigger.setAttribute('aria-expanded', 'false');
  menu.remove();
  current = null;
}

export const isMenuOpen = () => current != null;
