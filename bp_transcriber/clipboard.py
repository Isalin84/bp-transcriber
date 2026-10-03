"""Копирование текста в системный буфер обмена без GUI-зависимостей."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time

logger = logging.getLogger(__name__)


def copy(text: str) -> bool:
    """Кладёт ``text`` в буфер обмена. True при успехе."""
    try:
        if sys.platform == "darwin":
            return _copy_macos(text)
        if sys.platform == "win32":
            return _copy_windows(text)
        return _copy_linux(text)
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось скопировать текст в буфер обмена")
        return False


def _copy_macos(text: str) -> bool:
    env = {**os.environ, "LANG": "en_US.UTF-8", "LC_CTYPE": "UTF-8"}
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), env=env, check=True, timeout=10)
    return True


def _copy_linux(text: str) -> bool:
    for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
        if shutil.which(cmd[0]):
            subprocess.run(cmd, input=text.encode("utf-8"), check=True, timeout=10)
            return True
    logger.warning("Не найден ни wl-copy, ни xclip, ни xsel")
    return False


def _copy_windows(text: str) -> bool:
    import ctypes
    from ctypes import wintypes

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL

    normalized = text.replace("\r\n", "\n").replace("\n", "\r\n")
    data = normalized.encode("utf-16-le") + b"\x00\x00"

    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    ptr = kernel32.GlobalLock(handle)
    if not ptr:
        kernel32.GlobalFree(handle)
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        ctypes.memmove(ptr, data, len(data))
    finally:
        kernel32.GlobalUnlock(handle)

    # Буфер может быть кратко занят другим процессом — несколько попыток.
    for _ in range(10):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.02)
    else:
        kernel32.GlobalFree(handle)
        logger.warning("Буфер обмена занят другим приложением")
        return False

    try:
        if not user32.EmptyClipboard():
            kernel32.GlobalFree(handle)
            raise ctypes.WinError(ctypes.get_last_error())
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            raise ctypes.WinError(ctypes.get_last_error())
        # После успешного SetClipboardData памятью владеет система — не освобождаем.
        return True
    finally:
        user32.CloseClipboard()
