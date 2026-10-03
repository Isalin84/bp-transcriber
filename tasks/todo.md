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
- [ ] device.py (cuda>mps>cpu, smoke-тест, фолбэк)
- [ ] audio_io.py (один декод в память, прогресс, отмена, preview m4a)
- [ ] vad.py (Silero, склейка 15–22 с, лимит 25 с)
- [ ] asr_engine.py (батчи, word timestamps, прогресс, отмена) + контракт-тест
- [ ] model_store.py (.part, md5, зеркало, reuse ~/.cache/gigaam)
- [ ] pipeline.py (этапы, события, отмена)
- [ ] диаризация: pyannote из памяти + hook, check_token, hybrid fix, assign_words/regroup
- [ ] баги аудита: batch ext, transcribe_stream, temp leak, get_speakers order, login()
- [ ] bench: старый vs новый, CPU vs MPS

## Трек B. Приложение (Фазы 3–4) — Opus + Fable
- [ ] API-контракт (docs/api-contract.md) + мок
- [ ] bp_transcriber: __main__, app, server, api, jobs, events, settings, history, exporters, paths
- [ ] импорт HF-токена (.env / env / HF cache), reuse кэшей моделей
- [ ] UI: tokens.css, экраны (главный, очередь, транскрипт+плеер, история, настройки, первый запуск)
- [ ] проверка UI в браузере с моком, обе темы

## Трек C. Графика (Фаза 5) — Sonnet
- [ ] SVG-иконка + .icns/.ico
- [ ] design/ASSETS_BRIEF_FOR_CHATGPT.md

## Фаза 6. Сборка macOS — Opus
- [ ] PyInstaller spec, ffmpeg LGPL, --selftest, DMG

## Фаза 7. Windows + CI — Sonnet/Opus
- [ ] ci.yml, release.yml, Inno Setup, README
- [ ] публикация — только после подтверждения пользователя

## Ревью
_(заполняется по ходу)_
