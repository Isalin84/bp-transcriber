# BP Transcriber — план работ

Полный план: `~/.claude/plans/elegant-strolling-phoenix.md`. Ветка: `feature/bp-desktop`.
Базовая линия: 78 тестов зелёные (коммит b3f64e8).

## Фаза 0. Гигиена репозитория — Haiku ✅ (4af8fba: torch 2.11, win +cpu, 78 passed)
- [x] pyproject: GigaAM pin 85558932, extras [app]/[diarization], согласованные torch/torchaudio
- [x] requirements/lock-macos-arm64.txt, lock-win-x64.txt (CPU index)
- [x] убрать sys.path-хак (transcriber.py:47-50), .gitignore `!packaging/*.spec`
- [x] LICENSE (MIT) + NOTICE
- [x] проверка: импорты + pytest

## Трек A. Ядро (Фазы 1–2) — Opus + Sonnet
- [x] device.py (cuda>mps>cpu, smoke-тест, фолбэк)
- [x] audio_io.py (один декод в память, прогресс, отмена, preview m4a)
- [x] vad.py (Silero, склейка 15–22 с, лимит 25 с)
- [ ] asr_engine.py (батчи, word timestamps, прогресс, отмена) + контракт-тест
- [x] model_store.py (.part, md5, зеркало, reuse ~/.cache/gigaam)
- [ ] pipeline.py (этапы, события, отмена)
- [ ] диаризация: pyannote из памяти + hook, check_token, hybrid fix, assign_words/regroup
- [x] баги аудита: batch ext, transcribe_stream, temp leak, get_speakers order (A1)
- [ ] login() в diarization (A2)
- [ ] bench: старый vs новый, CPU vs MPS

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
- [ ] PyInstaller spec, ffmpeg LGPL, --selftest, DMG

## Фаза 7. Windows + CI — Sonnet/Opus
- [ ] ci.yml, release.yml, Inno Setup, README
- [ ] публикация — только после подтверждения пользователя

## Ревью
- A1 (ea87bd1): 167 passed; silero без onnxruntime; отмена VAD через callback.
- B1: 251 passed; свой threaded-сервер на 127.0.0.1:47815 (Range 206, ключ ?k=, защита от DNS rebinding); os._exit при выходе из-за зависающих потоков pywebview.
- B2: 22 скриншота, Playwright без ошибок консоли; big-транскрипт 1520 сегментов — p95 кадра 9 мс.
- Интеграция B1+B2: complete_onboarding, герметичный тест импорта токена (видел реальный .env), ui в package-data.
- C (7ef71f9): иконка проверена визуально на 512/32 px.
