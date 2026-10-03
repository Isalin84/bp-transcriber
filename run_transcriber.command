#!/bin/bash
# Лаунчер для запуска GUI транскрибатора двойным кликом из Finder.
cd "$(dirname "$0")"

source .venv/bin/activate

if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

python gui_app.py
status=$?

if [ $status -ne 0 ]; then
    echo ""
    echo "Приложение завершилось с ошибкой (код $status)."
    echo "Нажмите Enter, чтобы закрыть окно."
    read -r
fi
