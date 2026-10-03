"""Пути приложения: ресурсы сборки и пользовательские каталоги.

Переменная окружения ``BP_HOME`` переносит все пользовательские каталоги
(config/data/cache/logs) в одну папку — для тестов и портативного запуска.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import platformdirs

APP_NAME = "BP Transcriber"
APP_AUTHOR = "BestPractice"

_PACKAGE_DIR = Path(__file__).resolve().parent


def is_frozen() -> bool:
    """True внутри сборки PyInstaller."""
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """Корень ресурсов: ``sys._MEIPASS`` во frozen-сборке, иначе каталог пакета."""
    meipass = getattr(sys, "_MEIPASS", None)
    if is_frozen() and meipass:
        return Path(meipass)
    return _PACKAGE_DIR


def ui_dir() -> Path:
    """Каталог статики UI."""
    root = resource_dir()
    if root == _PACKAGE_DIR:
        return _PACKAGE_DIR / "ui"
    for candidate in (root / "bp_transcriber" / "ui", root / "ui"):
        if candidate.is_dir():
            return candidate
    return root / "ui"


def bin_dir() -> Path:
    """Каталог встроенных бинарников (ffmpeg) во frozen-сборке."""
    return resource_dir() / "bin"


def repo_root() -> Path | None:
    """Корень репозитория при запуске из исходников, иначе None."""
    return None if is_frozen() else _PACKAGE_DIR.parent


def _user_dir(kind: str) -> Path:
    home = os.environ.get("BP_HOME")
    if home:
        path = Path(home).expanduser() / kind
    else:
        func = {
            "config": platformdirs.user_config_dir,
            "data": platformdirs.user_data_dir,
            "cache": platformdirs.user_cache_dir,
            "logs": platformdirs.user_log_dir,
        }[kind]
        path = Path(func(APP_NAME, APP_AUTHOR))
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_dir() -> Path:
    return _user_dir("config")


def data_dir() -> Path:
    return _user_dir("data")


def cache_dir() -> Path:
    return _user_dir("cache")


def log_dir() -> Path:
    return _user_dir("logs")
