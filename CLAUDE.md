# BP Transcriber — инструкции для агента

Десктоп-приложение (pywebview + локальный HTTP-сервер) для офлайн-расшифровки русской речи.
`gigaam_transcriber/` — ядро (декод, VAD, GigaAM, диаризация, пайплайн), `bp_transcriber/` — приложение
(API для JS, очередь задач, история, настройки, экспорт, UI в `ui/`). План и уроки: `tasks/todo.md`, `tasks/lessons.md`.
Remotes: `bp` (Isalin84/bp-transcriber, наш) и `origin` (upstream DialogScribe) — пушить только в `bp`.

## Проверки локальные, в GitHub Actions — только сборка Windows

- Тестового CI нет намеренно (2026-10-07): не добавлять тесты в Actions, dependabot и т.п.
- Единственный workflow — `.github/workflows/windows-release.yml` (2026-10-21): по тегу `v*` собирает
  setup.exe, гоняет `--selftest` и публикует релиз. Windows-сборку на Mac не сделать.
- Перед каждым пушем в `master`: `bash scripts/check.sh` (pytest, `scripts/check_formats.py`, `--selftest`, ~30 с).
  Хук `.githooks/pre-push` запускает его сам (`git config core.hooksPath .githooks`). `--no-verify` — только по просьбе пользователя.
- Красная проверка = не пушить. Релиз — раздел «Релиз» в `packaging/README.md` (DMG собирается локально).

## Политика тестов

- Приоритет — интеграционные тесты бэкенда: реальные `HistoryStore`/`SettingsStore`/`JobQueue`/`AppServer`/ffmpeg
  на временных каталогах, подделывается только тяжёлое (модели, сеть).
- E2E — короткие и только для критических сценариев (сейчас один: речь → очередь → GigaAM → история → экспорт, `tests/test_e2e.py`).
- Юнит-тесты — минимум: только для алгоритмов, где ошибка тихо портит результат (разбиение на чанки ≤25 с,
  слова → спикеры, сборка сегментов).
- Не писать тесты на конструкторы, геттеры, константы, тексты исключений и сообщений, точные веса прогресса,
  стили DOCX, тайминги потоков. Регрессионный тест — только на реальный баг, который может вернуться.
- Тесты герметичны: `tests/app/conftest.py` изолирует HOME/BP_HOME/.env; реальный keyring и сеть не трогать.
- Тесты с моделью помечать `slow` и пропускать, если модель не скачана.
- Легаси CLI (`transcriber.py`, `audio_processor.py`, `formatters.py`, `segment_merger.py`, `cli.py`) тестами не покрыт;
  при правках проверять вручную командой `gigaam-transcribe`.
