// Theme: "system" | "dark" | "light" → <html data-theme="dark|light">.

const mq = matchMedia('(prefers-color-scheme: light)');
let current = 'system';

function resolve(theme) {
  return theme === 'system' ? (mq.matches ? 'light' : 'dark') : theme;
}

export function applyTheme(theme) {
  current = theme || 'system';
  document.documentElement.setAttribute('data-theme', resolve(current));
  try { localStorage.setItem('bp.theme', current); } catch (_) { /* private mode */ }
}

export const currentTheme = () => current;

mq.addEventListener('change', () => { if (current === 'system') applyTheme('system'); });
