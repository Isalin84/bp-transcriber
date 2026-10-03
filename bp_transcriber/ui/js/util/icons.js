// Shared inline SVG icons (20×20 grid, stroke inherits currentColor via CSS).

export const icons = {
  search: '<svg viewBox="0 0 20 20"><circle cx="9" cy="9" r="5.5" fill="none"/><path d="m13.2 13.2 3.3 3.3" fill="none"/></svg>',
  close: '<svg viewBox="0 0 20 20"><path d="m5.5 5.5 9 9M14.5 5.5l-9 9" fill="none"/></svg>',
  chevronDown: '<svg viewBox="0 0 20 20"><path d="m5.5 8 4.5 4.5L14.5 8" fill="none"/></svg>',
  chevronUp: '<svg viewBox="0 0 20 20"><path d="m5.5 12 4.5-4.5 4.5 4.5" fill="none"/></svg>',
  back: '<svg viewBox="0 0 20 20"><path d="M12.5 4.5 7 10l5.5 5.5" fill="none"/></svg>',
  copy: '<svg viewBox="0 0 20 20"><rect x="7" y="7" width="9" height="9" rx="1.5" fill="none"/><path d="M13 7V5.5A1.5 1.5 0 0 0 11.5 4h-6A1.5 1.5 0 0 0 4 5.5v6A1.5 1.5 0 0 0 5.5 13H7" fill="none"/></svg>',
  export: '<svg viewBox="0 0 20 20"><path d="M10 12.5v-9M6.5 7 10 3.5 13.5 7" fill="none"/><path d="M4 11.5v3A1.5 1.5 0 0 0 5.5 16h9a1.5 1.5 0 0 0 1.5-1.5v-3" fill="none"/></svg>',
  edit: '<svg viewBox="0 0 20 20"><path d="m4 16 .9-3.6L13.6 3.7a1.4 1.4 0 0 1 2 0l.7.7a1.4 1.4 0 0 1 0 2L7.6 15.1 4 16Z" fill="none"/></svg>',
  trash: '<svg viewBox="0 0 20 20"><path d="M4.5 6h11M8 6V4.5h4V6M6 6l.7 9.5h6.6L14 6" fill="none"/></svg>',
  check: '<svg viewBox="0 0 20 20"><path d="m4.5 10.5 3.5 3.5 7.5-8" fill="none"/></svg>',
  file: '<svg viewBox="0 0 20 20"><path d="M5.5 3h6l4 4v10a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z" fill="none"/><path d="M11.5 3v4h4" fill="none"/></svg>',
  folder: '<svg viewBox="0 0 20 20"><path d="M3 5.5A1.5 1.5 0 0 1 4.5 4h3.3l1.7 2h6A1.5 1.5 0 0 1 17 7.5v7a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 3 14.5v-9Z" fill="none"/></svg>',
  external: '<svg viewBox="0 0 20 20"><path d="M11 4h5v5M16 4l-7 7" fill="none"/><path d="M14 11.5v3A1.5 1.5 0 0 1 12.5 16h-7A1.5 1.5 0 0 1 4 14.5v-7A1.5 1.5 0 0 1 5.5 6h3" fill="none"/></svg>',
  plus: '<svg viewBox="0 0 20 20"><path d="M10 4.5v11M4.5 10h11" fill="none"/></svg>',
  minus: '<svg viewBox="0 0 20 20"><path d="M4.5 10h11" fill="none"/></svg>',
  eye: '<svg viewBox="0 0 20 20"><path d="M2.5 10s3-5 7.5-5 7.5 5 7.5 5-3 5-7.5 5-7.5-5-7.5-5Z" fill="none"/><circle cx="10" cy="10" r="2.2" fill="none"/></svg>',
  eyeOff: '<svg viewBox="0 0 20 20"><path d="M3 3l14 14M8.3 8.4A2.2 2.2 0 0 0 11.6 11.6M6.3 6.3C4 7.8 2.5 10 2.5 10s3 5 7.5 5c1.3 0 2.5-.4 3.5-.9M9 5.1c.3 0 .7-.1 1-.1 4.5 0 7.5 5 7.5 5s-.7 1.2-2 2.4" fill="none"/></svg>',
  clock: '<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="7" fill="none"/><path d="M10 6v4l2.5 1.5" fill="none"/></svg>',
  users: '<svg viewBox="0 0 20 20"><circle cx="7.5" cy="7" r="2.5" fill="none"/><circle cx="13.5" cy="8" r="2" fill="none"/><path d="M2.5 16a5 5 0 0 1 10 0M11.5 12.5a4 4 0 0 1 6 3.5" fill="none"/></svg>',
  mic: '<svg viewBox="0 0 20 20"><rect x="7.5" y="2.5" width="5" height="9" rx="2.5" fill="none"/><path d="M5 9.5a5 5 0 0 0 10 0M10 14.5v3" fill="none"/></svg>',
  warning: '<svg viewBox="0 0 20 20"><path d="M10 3 2.5 16.5h15L10 3Z" fill="none"/><path d="M10 8v4M10 14v.5" fill="none"/></svg>',
  info: '<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="7.5" fill="none"/><path d="M10 9v5M10 6.5v.5" fill="none"/></svg>',
  download: '<svg viewBox="0 0 20 20"><path d="M10 3.5v9M6.5 9 10 12.5 13.5 9" fill="none"/><path d="M4 12.5v2A1.5 1.5 0 0 0 5.5 16h9a1.5 1.5 0 0 0 1.5-1.5v-2" fill="none"/></svg>',
  key: '<svg viewBox="0 0 20 20"><circle cx="7" cy="10" r="3.5" fill="none"/><path d="M10.5 10H17M14.5 10v2.5M16.5 10v2" fill="none"/></svg>',
  play: '<svg viewBox="0 0 20 20"><path d="M6 4.5v11l9-5.5-9-5.5Z"/></svg>',
};

/** Returns an element containing the icon markup. */
export function icon(name, cls = 'icon') {
  const span = document.createElement('span');
  span.className = cls;
  span.setAttribute('aria-hidden', 'true');
  span.innerHTML = icons[name] || '';
  return span;
}
