// Formatting helpers (Russian locale) and tiny DOM utilities.

/** "1:02:03" / "2:05" — clock-style duration. */
export function fmtTime(sec) {
  if (sec == null || !isFinite(sec)) return '–:––';
  sec = Math.max(0, Math.floor(sec));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  const mm = h ? String(m).padStart(2, '0') : String(m);
  return (h ? h + ':' : '') + mm + ':' + String(s).padStart(2, '0');
}

/** "1 мин 20 с" / "45 с" / "2 ч 10 мин" — human-readable span (for ETA, processing time). */
export function fmtSpan(sec) {
  if (sec == null || !isFinite(sec)) return '';
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h) return `${h} ч${m ? ' ' + m + ' мин' : ''}`;
  if (m) return `${m} мин${s ? ' ' + s + ' с' : ''}`;
  return `${s} с`;
}

/** "450 МБ", "1,2 ГБ", "320 КБ". */
export function fmtSize(bytes) {
  if (bytes == null || !isFinite(bytes)) return '';
  const units = ['Б', 'КБ', 'МБ', 'ГБ'];
  let i = 0, v = bytes;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  const str = i >= 2 && v < 10 ? v.toFixed(1).replace('.', ',') : String(Math.round(v));
  return `${str} ${units[i]}`;
}

/** Russian plural: plural(5, ['файл', 'файла', 'файлов']) → "5 файлов". */
export function plural(n, forms, withNumber = true) {
  const abs = Math.abs(n) % 100;
  const d = abs % 10;
  let form;
  if (abs > 10 && abs < 20) form = forms[2];
  else if (d > 1 && d < 5) form = forms[1];
  else if (d === 1) form = forms[0];
  else form = forms[2];
  return withNumber ? `${n} ${form}` : form;
}

const dateFmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' });
const timeFmt = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' });

/** "3 октября 2026, 14:05" from unix seconds. */
export function fmtDate(unix, withTime = true) {
  if (!unix) return '';
  const d = new Date(unix * 1000);
  const date = dateFmt.format(d).replace(/\s*г\.$/, ''); // "3 октября 2026 г." → без "г."
  return withTime ? `${date}, ${timeFmt.format(d)}` : date;
}

/** "Сегодня, 14:05" / "Вчера, 09:12" / full date — for history cards. */
export function fmtRelativeDate(unix) {
  if (!unix) return '';
  const d = new Date(unix * 1000);
  const now = new Date();
  const dayMs = 86400000;
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  if (d.getTime() >= startOfToday) return `Сегодня, ${timeFmt.format(d)}`;
  if (d.getTime() >= startOfToday - dayMs) return `Вчера, ${timeFmt.format(d)}`;
  return fmtDate(unix);
}

export function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/** Build an element from an HTML string (single root). */
export function html(str) {
  const t = document.createElement('template');
  t.innerHTML = str.trim();
  return t.content.firstElementChild;
}

/** Shorthand: el('button', {class: 'btn', onclick: fn}, 'Текст', childEl) */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else if (v === true) node.setAttribute(k, '');
    else node.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

/** Lower-cased, "ё"→"е" normalised string for search. */
export function norm(s) {
  return String(s ?? '').toLowerCase().replace(/ё/g, 'е');
}

export const prefersReducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
