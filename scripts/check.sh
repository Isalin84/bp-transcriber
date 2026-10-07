#!/usr/bin/env bash
# Локальная проверка перед пушем в master (CI на GitHub нет — это единственный гейт).
#   bash scripts/check.sh
# Запускается автоматически хуком .githooks/pre-push при пуше в master.
set -euo pipefail

cd "$(dirname "$0")/.."
PY="${PYTHON:-.venv/bin/python}"
[ -x "$PY" ] || PY=python3

echo "== Тесты"
"$PY" -m pytest -p no:cacheprovider

echo "== Форматы (декодирование ffmpeg)"
"$PY" scripts/check_formats.py

echo "== Selftest (зависимости, ffmpeg, модели)"
out="$("$PY" -m bp_transcriber --selftest 2>&1)" || { echo "$out"; exit 1; }

echo "== Готово: можно пушить"
