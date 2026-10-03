"""Точка входа PyInstaller-сборки BP Transcriber.

``bp_transcriber/__main__.py`` использует относительные импорты, поэтому
PyInstaller запускает этот модуль, а он — ``bp_transcriber.__main__.main()``.
"""

from __future__ import annotations

import multiprocessing
import os
import sys


def _prepare_frozen_env() -> None:
    """Окружение для frozen-сборки (до импорта приложения и тяжёлых библиотек)."""
    if not getattr(sys, "frozen", False):
        return
    import shutil

    from bp_transcriber import paths

    # matplotlib тянет pyannote/torchmetrics; GUI-бэкенд macosx конфликтовал бы с циклом Cocoa pywebview.
    os.environ.setdefault("MPLBACKEND", "Agg")
    # Runtime-хук PyInstaller кладёт MPLCONFIGDIR во временный каталог — тогда matplotlib
    # пересобирает кэш шрифтов при КАЖДОМ запуске (+10–15 с к импорту pyannote), а из-за
    # os._exit на выходе временные каталоги ещё и не удаляются. Кэш — в каталог приложения.
    temp_mpl = os.environ.get("MPLCONFIGDIR")
    mpl_dir = paths.cache_dir() / "matplotlib"
    try:
        mpl_dir.mkdir(parents=True, exist_ok=True)
        os.environ["MPLCONFIGDIR"] = str(mpl_dir)
        if temp_mpl and os.path.realpath(temp_mpl) != os.path.realpath(mpl_dir):
            shutil.rmtree(temp_mpl, ignore_errors=True)
    except OSError:
        pass
    # Из Finder приложение стартует с cwd = "/" (только чтение). Библиотеки, пишущие
    # по относительным путям, получат пользовательский каталог кэша, а не ошибку.
    try:
        writable = os.access(os.getcwd(), os.W_OK)
    except OSError:
        writable = False
    if not writable:
        try:
            os.chdir(paths.cache_dir())
        except OSError:
            pass


if __name__ == "__main__":
    # Дочерние процессы multiprocessing (spawn) должны выйти здесь, не запуская приложение.
    multiprocessing.freeze_support()
    _prepare_frozen_env()

    from bp_transcriber.__main__ import main

    sys.exit(main())
