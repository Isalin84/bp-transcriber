"""
Хранилище весов GigaAM: поиск, скачивание с проверкой md5 и зеркалом.

Файлы скачиваются в ``<файл>.part`` с потоковым подсчётом md5 и атомарно
переименовываются. После успешной проверки рядом создаётся маркер
``<имя>.verified`` с md5, чтобы не пересчитывать хеш (≈850 МБ) при каждом запуске.
"""

from __future__ import annotations

import hashlib
import http.client
import logging
import os
import urllib.request
from pathlib import Path
from typing import Callable

import platformdirs

from .exceptions import ModelLoadError
from .progress import CancelToken

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "v3_e2e_rnnt"
MIRROR_URL = "https://github.com/Isalin84/bp-transcriber/releases/download/models-v1"

_LEGACY_ROOT = Path("~/.cache/gigaam").expanduser()
_APP_NAME = "BP Transcriber"
_APP_AUTHOR = "BestPractice"
_USER_AGENT = "BP-Transcriber/1.0 (+https://github.com/Isalin84/bp-transcriber)"
_TIMEOUT_S = 30.0
_DOWNLOAD_BLOCK = 1 << 20
_HASH_BLOCK = 8 << 20


class _DownloadError(Exception):
    """Сбой скачивания одного файла (текст — по-русски, для пользователя)."""


def _gigaam_tables() -> tuple[str, dict[str, str]]:
    """URL-каталог и таблица md5 из GigaAM (ленивый импорт: gigaam тянет torch)."""
    import gigaam

    return gigaam._URL_DIR, gigaam._MODEL_HASHES


def _tokenizer_name(name: str) -> str | None:
    """Имя файла токенайзера, если модели он нужен (как в gigaam._download_tokenizer)."""
    if name == "v1_rnnt" or "e2e" in name:
        return f"{name}_tokenizer.model"
    return None


def models_root(name: str = DEFAULT_MODEL) -> Path:
    """
    Каталог весов (``download_root`` для ``gigaam.load_model``).

    Использует ``~/.cache/gigaam``, если там уже лежит чекпоинт модели,
    иначе каталог кэша приложения.
    """
    if (_LEGACY_ROOT / f"{name}.ckpt").is_file():
        return _LEGACY_ROOT
    return Path(platformdirs.user_cache_dir(_APP_NAME, _APP_AUTHOR)) / "gigaam"


def is_model_available(name: str = DEFAULT_MODEL) -> bool:
    """Есть ли чекпоинт и токенайзер (md5 здесь не считается — это дорого)."""
    root = models_root(name)
    files = [f"{name}.ckpt"]
    tokenizer = _tokenizer_name(name)
    if tokenizer:
        files.append(tokenizer)
    return all((root / file).is_file() for file in files)


def _file_md5(path: Path, cancel: CancelToken | None) -> str:
    """Потоковый md5 файла блоками по 8 МБ."""
    digest = hashlib.md5()
    with path.open("rb") as stream:
        while block := stream.read(_HASH_BLOCK):
            if cancel is not None:
                cancel.raise_if_cancelled()
            digest.update(block)
    return digest.hexdigest()


def _fetch(
    url: str,
    dest: Path,
    md5: str | None,
    on_progress: Callable[[int, int], None] | None,
    cancel: CancelToken | None,
) -> None:
    """Скачать url в dest через ``dest.part``; при любом сбое .part удаляется."""
    part = dest.with_name(dest.name + ".part")
    digest = hashlib.md5()
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response, part.open("wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            downloaded = 0
            while block := response.read(_DOWNLOAD_BLOCK):
                if cancel is not None:
                    cancel.raise_if_cancelled()
                out.write(block)
                digest.update(block)
                downloaded += len(block)
                if on_progress:
                    on_progress(downloaded, total)
        if total and downloaded != total:
            raise _DownloadError("файл скачан не полностью")
        if md5 is not None and digest.hexdigest() != md5:
            raise _DownloadError("контрольная сумма файла не совпала")
        os.replace(part, dest)
    except (OSError, http.client.HTTPException) as exc:
        part.unlink(missing_ok=True)
        logger.warning("Не удалось скачать %s: %s", url, exc)
        raise _DownloadError("нет соединения с сервером или файл недоступен") from exc
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def _download(
    name: str,
    filename: str,
    url_dir: str,
    md5: str | None,
    on_progress: Callable[[int, int], None] | None,
    cancel: CancelToken | None,
    dest: Path,
) -> None:
    """Скачать файл с основного URL, при сбое — с зеркала."""
    last_error: _DownloadError | None = None
    for base in (url_dir, MIRROR_URL):
        try:
            _fetch(f"{base}/{filename}", dest, md5, on_progress, cancel)
            return
        except _DownloadError as exc:
            logger.warning("Источник %s не подошёл для %s: %s", base, filename, exc)
            last_error = exc
    raise ModelLoadError(name, last_error)


def _ensure_checkpoint(
    name: str,
    root: Path,
    url_dir: str,
    md5: str,
    on_progress: Callable[[int, int], None] | None,
    cancel: CancelToken | None,
) -> None:
    """Гарантировать наличие проверенного чекпоинта в root."""
    ckpt = root / f"{name}.ckpt"
    marker = root / f"{name}.verified"
    if ckpt.is_file():
        if marker.is_file() and marker.read_text(encoding="utf-8").strip() == md5:
            return
        if _file_md5(ckpt, cancel) == md5:
            _write_marker(marker, md5)
            return
        logger.warning("Контрольная сумма %s не совпала, файл будет скачан заново", ckpt)
        ckpt.unlink()
    marker.unlink(missing_ok=True)
    _download(name, ckpt.name, url_dir, md5, on_progress, cancel, ckpt)
    _write_marker(marker, md5)


def _write_marker(marker: Path, md5: str) -> None:
    """Записать маркер проверки; его отсутствие не критично."""
    try:
        marker.write_text(md5, encoding="utf-8")
    except OSError as exc:
        logger.warning("Не удалось записать маркер %s: %s", marker, exc)


def ensure_model(
    name: str = DEFAULT_MODEL,
    *,
    on_progress: Callable[[int, int], None] | None = None,
    cancel: CancelToken | None = None,
) -> Path:
    """
    Убедиться, что веса модели есть и целы; при необходимости скачать.

    Args:
        name: Имя модели GigaAM (например, "v3_e2e_rnnt")
        on_progress: Callback ``(скачано_байт, всего_байт)`` для чекпоинта
        cancel: Токен отмены

    Returns:
        Каталог для ``download_root`` в ``gigaam.load_model``.

    Raises:
        ModelLoadError: неизвестная модель или не удалось скачать
        Cancelled: загрузка отменена
    """
    url_dir, hashes = _gigaam_tables()
    if name not in hashes:
        raise ModelLoadError(name, ValueError("неизвестная модель"))

    root = models_root(name)
    root.mkdir(parents=True, exist_ok=True)

    tokenizer = _tokenizer_name(name)
    if tokenizer and not (root / tokenizer).is_file():
        _download(name, tokenizer, url_dir, None, None, cancel, root / tokenizer)
    _ensure_checkpoint(name, root, url_dir, hashes[name], on_progress, cancel)
    return root
