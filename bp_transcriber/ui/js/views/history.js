// History view: searchable list of saved transcripts.

import { api } from '../api.js';
import * as store from '../state.js';
import { el, fmtTime, fmtRelativeDate, plural, debounce } from '../util/format.js';
import { icon } from '../util/icons.js';
import { toast } from '../components/toast.js';
import { confirm } from '../components/dialog.js';

function emptyArt() {
  // Звуковая волна, превращающаяся в строки текста (прозрачный PNG, читается на обеих темах)
  return el('img', { class: 'empty__art', src: 'img/empty-state.png', alt: '', width: '800', height: '600' });
}

export function render(root) {
  let query = '';
  const input = el('input', { class: 'search__input', type: 'search', placeholder: 'Поиск по названию и тексту', 'aria-label': 'Поиск в истории', autocomplete: 'off' });
  const search = el('div', { class: 'search search--wide' }, icon('search', 'icon search__icon'), input);
  const list = el('div', { class: 'history' });
  const status = el('div', { class: 'history__status' });

  const view = el('div', { class: 'view view--history' },
    el('header', { class: 'view-head' },
      el('div', null, el('h1', { class: 'h1' }, 'История'), el('p', { class: 'view-head__sub' }, 'Все транскрипты хранятся на этом компьютере.')),
      search),
    status, list,
  );
  root.append(view);

  let seq = 0;
  async function load() {
    const my = ++seq;
    status.textContent = 'Загружаем…';
    const items = await api.list_history(query || undefined).catch(() => []);
    if (my !== seq) return;
    status.textContent = '';
    list.textContent = '';
    if (!items.length) {
      list.append(el('div', { class: 'empty' }, emptyArt(),
        el('h2', { class: 'h2' }, query ? 'Ничего не найдено' : 'Пока пусто'),
        el('p', null, query ? 'Попробуйте другое слово или имя файла.' : 'Готовые транскрипты появятся здесь. Начните с первого файла.'),
        query ? null : el('button', { class: 'btn btn--primary', type: 'button', onclick: () => store.navigate('transcribe') }, 'К транскрибации')));
      return;
    }
    status.textContent = plural(items.length, ['запись', 'записи', 'записей']);
    for (const it of items) list.append(card(it));
  }

  function card(it) {
    const open = () => store.navigate('transcript', { id: it.id });
    const c = el('article', { class: 'hcard', tabindex: '0', role: 'button',
      onclick: (e) => { if (!e.target.closest('button')) open(); },
      onkeydown: (e) => { if (e.key === 'Enter') open(); } },
      el('div', { class: 'hcard__icon' }, icon('mic')),
      el('div', { class: 'hcard__main' },
        el('div', { class: 'hcard__name' }, it.file_name),
        el('div', { class: 'hcard__meta' },
          el('span', null, fmtRelativeDate(it.created_at)), el('span', { class: 'dot' }),
          el('span', null, fmtTime(it.duration)), el('span', { class: 'dot' }),
          el('span', null, it.speakers ? plural(it.speakers, ['спикер', 'спикера', 'спикеров']) : 'без спикеров')),
        el('p', { class: 'hcard__preview' }, it.preview || '…')),
      el('div', { class: 'hcard__actions' },
        el('button', { class: 'btn btn--ghost btn--sm', type: 'button', onclick: open }, 'Открыть'),
        el('button', { class: 'btn btn--icon btn--sm', type: 'button', 'aria-label': 'Удалить', title: 'Удалить', onclick: () => remove(it, c) }, icon('trash'))),
    );
    return c;
  }

  async function remove(it, c) {
    const ok = await confirm('Удалить транскрипт?', {
      detail: `«${it.file_name}» будет удалён из истории вместе с аудио для прослушивания. Исходный файл останется на месте.`,
      okLabel: 'Удалить', danger: true,
    });
    if (!ok) return;
    const done = await api.delete_transcript(it.id).catch(() => false);
    if (done) { c.remove(); toast.success('Транскрипт удалён'); if (!list.querySelector('.hcard')) load(); }
  }

  input.addEventListener('input', debounce(() => { query = input.value.trim(); load(); }, 200));
  load();
  return () => { seq++; };
}
