"""Точка входа: ``python -m bp_transcriber [--fake] [--debug] [--selftest] [--selftest-ui]``."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import multiprocessing
import os
import re
import sys
import threading
from pathlib import Path
from typing import TextIO

LOG_MAX_BYTES = 5 * 1024 * 1024
_TOKEN_RE = re.compile(r"hf_[A-Za-z0-9]{10,}")

# Исходный stdout (до перенаправления во frozen-сборке) — для вывода --selftest.
_CONSOLE: TextIO | None = sys.stdout


def _setup_env() -> None:
    os.environ.setdefault("PYANNOTE_METRICS_ENABLED", "false")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


class _RedactTokens(logging.Filter):
    """Страховка: маскирует всё, похожее на HF-токен, в любых логах (в т.ч. сторонних)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        if "hf_" in message and _TOKEN_RE.search(message):
            record.msg = _TOKEN_RE.sub("hf_***", message)
            record.args = None
        return True


def _rotate(path: Path, max_bytes: int = LOG_MAX_BYTES) -> None:
    try:
        if path.exists() and path.stat().st_size > max_bytes:
            os.replace(path, path.with_suffix(path.suffix + ".1"))
    except OSError:
        pass


def _redirect_stdio(log_dir: Path) -> None:
    """Во frozen-сборке stdout/stderr (на Windows = None) пишутся в отдельный лог-файл."""
    stdio_log = log_dir / "stdio.log"
    _rotate(stdio_log)
    stream = open(stdio_log, "a", encoding="utf-8", errors="backslashreplace", buffering=1)  # noqa: SIM115
    sys.stdout = stream
    sys.stderr = stream


def _setup_logging(log_dir: Path, debug: bool, frozen: bool) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s")
    redact = _RedactTokens()

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "bp-transcriber.log", maxBytes=LOG_MAX_BYTES, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    file_handler.addFilter(redact)
    root.addHandler(file_handler)

    if not frozen and sys.stderr is not None:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(fmt)
        console.addFilter(redact)
        root.addHandler(console)

    for noisy in ("urllib3", "httpx", "httpcore", "PIL", "matplotlib", "numba", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if not debug:
        logging.getLogger("pywebview").setLevel(logging.WARNING)

    def excepthook(exc_type, exc, tb):  # type: ignore[no-untyped-def]
        logging.getLogger("bp_transcriber").critical("Необработанное исключение", exc_info=(exc_type, exc, tb))

    def thread_excepthook(args: threading.ExceptHookArgs) -> None:
        logging.getLogger("bp_transcriber").error(
            "Необработанное исключение в потоке %s",
            args.thread.name if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = excepthook
    threading.excepthook = thread_excepthook


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="bp_transcriber", description="BP Transcriber")
    parser.add_argument("--selftest", action="store_true", help="проверка зависимостей без окна")
    parser.add_argument("--selftest-ui", action="store_true", help="открыть скрытое окно и проверить JS API")
    parser.add_argument("--fake", action="store_true", help="имитация пайплайна (разработка UI)")
    parser.add_argument("--debug", action="store_true", help="режим отладки, DevTools")
    # PyInstaller/macOS могут добавить свои аргументы (например, -psn_…) — игнорируем их.
    args, _unknown = parser.parse_known_args(argv)
    return args


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    _setup_env()

    from . import paths

    frozen = paths.is_frozen()
    if frozen:
        bundled_bin = paths.bin_dir()
        if bundled_bin.is_dir():
            os.environ["PATH"] = str(bundled_bin) + os.pathsep + os.environ.get("PATH", "")
    log_dir = paths.log_dir()
    if frozen:
        _redirect_stdio(log_dir)

    args = _parse_args(argv)
    if args.fake:
        os.environ["BP_FAKE_PIPELINE"] = "1"
    _setup_logging(log_dir, args.debug, frozen)

    from . import __version__

    log = logging.getLogger("bp_transcriber")
    log.info("BP Transcriber %s (frozen=%s, python %s, %s)", __version__, frozen, sys.version.split()[0], sys.platform)

    if args.selftest:
        from .selftest import run

        return run(_CONSOLE)

    from .app import run_app

    code = run_app(debug=args.debug, selftest_ui=args.selftest_ui, console=_CONSOLE)
    _hard_exit(code)
    return code


def _hard_exit(code: int) -> None:
    """Выход после закрытия окна, не дожидаясь «зависших» потоков pywebview.

    pywebview выполняет каждый вызов JS API в НЕ-daemon потоке, который в конце ждёт
    ``evaluate_js`` (на macOS — семафор без таймаута). Если окно закрыли, пока такой
    вызов шёл, интерпретатор при обычном выходе ждал бы этот поток вечно.
    Все наши ресурсы к этому моменту уже остановлены в ``run_app``.
    """
    lingering = [t.name for t in threading.enumerate() if t is not threading.main_thread() and not t.daemon]
    if lingering:
        logging.getLogger("bp_transcriber").info("Принудительный выход, незавершённые потоки: %s", lingering)
    logging.shutdown()
    for stream in (sys.stdout, sys.stderr, _CONSOLE):
        try:
            if stream is not None:
                stream.flush()
        except (OSError, ValueError):
            pass
    os._exit(code)


if __name__ == "__main__":
    sys.exit(main())
