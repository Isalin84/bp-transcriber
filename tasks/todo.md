# BP Transcriber — план работ

Полный план: `~/.claude/plans/elegant-strolling-phoenix.md`. Ветка: `feature/bp-desktop`.
Базовая линия: 78 тестов зелёные (коммит b3f64e8).

## Фаза 0. Гигиена репозитория — Haiku ✅ (4af8fba: torch 2.11, win +cpu, 78 passed)
- [x] pyproject: GigaAM pin 85558932, extras [app]/[diarization], согласованные torch/torchaudio
- [x] requirements/lock-macos-arm64.txt, lock-win-x64.txt (CPU index)
- [x] убрать sys.path-хак (transcriber.py:47-50), .gitignore `!packaging/*.spec`
- [x] LICENSE (MIT) + NOTICE
- [x] проверка: импорты + pytest

## Трек A. Ядро (Фазы 1–2) — Opus + Sonnet ✅ (A1 ea87bd1, A2 5cfc87c)
- [x] device.py (cuda>mps>cpu, smoke-тест, фолбэк)
- [x] audio_io.py (один декод в память, прогресс, отмена, preview m4a)
- [x] vad.py (Silero, склейка 15–22 с, лимит 25 с)
- [x] asr_engine.py (батчи, word timestamps, прогресс, отмена) + контракт-тест
- [x] model_store.py (.part, md5, зеркало, reuse ~/.cache/gigaam)
- [x] pipeline.py (этапы, события, отмена)
- [x] диаризация: pyannote из памяти + hook, check_token, hybrid fix, assign_words/regroup
- [x] баги аудита: batch ext, transcribe_stream, temp leak, get_speakers order (A1)
- [x] login() в diarization (A2)
- [x] bench: старый vs новый, CPU vs MPS

## Трек B. Приложение (Фазы 3–4) — Opus + Fable ✅ (B1 2e3c090, B2 5229b6e)
- [x] API-контракт (docs/api-contract.md) + мок
- [x] bp_transcriber: __main__, app, server, api, jobs, events, settings, history, exporters, paths
- [x] импорт HF-токена (.env / env / HF cache), reuse кэшей моделей
- [x] UI: tokens.css, экраны (главный, очередь, транскрипт+плеер, история, настройки, первый запуск)
- [x] проверка UI в браузере с моком, обе темы

## Трек C. Графика (Фаза 5) — Sonnet ✅ (7ef71f9)
- [x] SVG-иконка + .icns/.ico
- [x] design/ASSETS_BRIEF_FOR_CHATGPT.md

## Фаза 6. Сборка macOS — Opus
- [x] PyInstaller spec, ffmpeg LGPL 7.1.3 (4.7 МБ), --selftest, DMG (146 МБ, .app 534 МБ) — 949cb5d
- [x] пересобрать и прогнать реальную расшифровку после A2 (frozen: pyannote 21.9 с, hybrid 6.7 с на 2:43, 2 спикера)

## Фаза 7. Windows + CI — Sonnet/Opus
- [x] ci.yml, release.yml, Inno Setup, README — 43fbc1a (Windows-часть проверяется только в CI)
- [x] публикация (разрешена пользователем после проверки): github.com/Isalin84/bp-transcriber, зеркало модели models-v1
- [x] release v1.0.0 опубликован: DMG проверен (sha256, codesign, selftest, расшифровка из образа), Windows selftest в CI зелёный

## Ревью
- A1 (ea87bd1): 167 passed; silero без onnxruntime; отмена VAD через callback.
- B1: 251 passed; свой threaded-сервер на 127.0.0.1:47815 (Range 206, ключ ?k=, защита от DNS rebinding); os._exit при выходе из-за зависающих потоков pywebview.
- B2: 22 скриншота, Playwright без ошибок консоли; big-транскрипт 1520 сегментов — p95 кадра 9 мс.
- Интеграция B1+B2: complete_onboarding, герметичный тест импорта токена (видел реальный .env), ui в package-data.
- Ассеты ChatGPT встроены (b5ad5f0); иконку оставили векторную (чётче на 16–32 px).
- P1: найдено torch.set_num_threads(1) при импорте silero_vad — передано A2 на исправление.
- A2: новый пайплайн 10.7 с vs 106.6 с старый (643 с аудио, CPU); MPS быстрее и текст совпадает ≥99.7%; диаризация по словам 24/24 границ на тестовом диалоге.
- Финальное ревью (Fable): блокеров нет; исправлены гонка загрузки модели, проверка WebView2, README/NOTICE.
- Первый release-прогон: Inno OOM (ultra64×4 потока) и MPS OOM на виртуальном раннере → MPS-проверки необязательные, hybrid повторяет на CPU, сжатие max + отдельный процесс.
- C (7ef71f9): иконка проверена визуально на 512/32 px.
