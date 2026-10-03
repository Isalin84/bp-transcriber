"""История транскриптов: ``data_dir/history/<id>/{meta.json, transcript.json, preview.m4a}``.

``transcript.json`` = ``TranscriptionResult.to_json()`` + ``"speakers": [{id, name, color}]``;
``segment.speaker`` хранит id спикера, отображаемые имена применяются при выдаче.
``index.json`` — кэш метаданных; при отсутствии или рассинхронизации восстанавливается
по ``meta.json``. Незавершённые записи живут в ``.pending-<id>`` и не видны в списке.

Все операции сериализуются одним RLock: они короткие (мелкие JSON-файлы), а модель
«один писатель» исключает гонки между API-потоками pywebview и воркером задач.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from collections.abc import Mapping

from . import paths
from .settings import atomic_write_json

logger = logging.getLogger(__name__)

PALETTE_SIZE = 8
PREVIEW_NAME = "preview.m4a"
PENDING_PREFIX = ".pending-"
INDEX_VERSION = 1
_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_PREVIEW_CHARS = 160


class HistoryError(Exception):
    """Ошибка работы с историей (сообщение по-русски)."""


@dataclass
class PendingEntry:
    """Зарезервированная запись: каталог для превью до появления результата."""

    id: str
    dir: Path

    @property
    def preview_path(self) -> Path:
        return self.dir / PREVIEW_NAME


def is_valid_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_ID_RE.match(value))


def _fold(text: str) -> str:
    return text.casefold().replace("ё", "е")


def _make_preview(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= _PREVIEW_CHARS:
        return text
    cut = text[:_PREVIEW_CHARS].rsplit(" ", 1)[0]
    return cut + "…"


def _full_text(segments: list[dict[str, Any]]) -> str:
    return " ".join(s.get("text", "").strip() for s in segments if s.get("text", "").strip())


class HistoryStore:
    def __init__(self, root: Path | None = None):
        self.root = root or (paths.data_dir() / "history")
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._index: dict[str, dict[str, Any]] = {}
        self._text_cache: dict[str, tuple[float, str]] = {}
        self._cleanup_pending()
        self._load_index()

    # --- создание ---------------------------------------------------------------

    def new_entry(self) -> PendingEntry:
        """Резервирует каталог под запись (сюда пайплайн пишет превью)."""
        with self._lock:
            while True:
                entry_id = uuid.uuid4().hex[:12]
                if entry_id not in self._index and not (self.root / entry_id).exists():
                    break
            pending_dir = self.root / f"{PENDING_PREFIX}{entry_id}"
            pending_dir.mkdir(parents=True, exist_ok=False)
            return PendingEntry(entry_id, pending_dir)

    def discard(self, pending: PendingEntry) -> None:
        shutil.rmtree(pending.dir, ignore_errors=True)

    def save_result(
        self,
        result: Any,
        source_path: str | Path,
        options: Mapping[str, Any] | None = None,
        preview_path: str | Path | None = None,
        *,
        pending: PendingEntry | None = None,
    ) -> str:
        """Сохраняет ``TranscriptionResult``; возвращает id записи."""
        pending = pending or self.new_entry()
        source = Path(source_path)
        try:
            data = json.loads(result.to_json())
            segments = data.get("segments", [])
            speakers, mapping = _build_speakers(segments)
            for seg in segments:
                if seg.get("speaker") is not None:
                    seg["speaker"] = mapping[seg["speaker"]]
            data["speakers"] = speakers
            data["full_text"] = _full_text(segments)

            if preview_path is not None:
                src = Path(preview_path)
                if src.is_file() and src.resolve() != pending.preview_path.resolve():
                    shutil.move(str(src), pending.preview_path)
            metadata = data.get("metadata", {})
            meta = {
                "version": INDEX_VERSION,
                "id": pending.id,
                "file_name": source.name,
                "source_path": str(source),
                "created_at": round(time.time(), 3),
                "duration": float(getattr(result, "duration", 0.0) or 0.0),
                "processing_time": float(getattr(result, "processing_time", 0.0) or 0.0),
                "device": str(metadata.get("device", "")),
                "diarization": str(metadata.get("diarization", "")),
                "speakers": len(speakers),
                "preview": _make_preview(data["full_text"]),
                "has_preview": pending.preview_path.is_file(),
                "options": dict(options or {}),
            }
            atomic_write_json(pending.dir / "transcript.json", data)
            atomic_write_json(pending.dir / "meta.json", meta)
            with self._lock:
                final_dir = self.root / pending.id
                pending.dir.rename(final_dir)
                pending.dir = final_dir
                self._index[pending.id] = meta
                self._write_index()
            return pending.id
        except BaseException:
            if pending.dir.name.startswith(PENDING_PREFIX):
                self.discard(pending)
            raise

    # --- чтение -----------------------------------------------------------------

    def list(self, query: str | None = None) -> list[dict[str, Any]]:
        """HistoryItem[], новые сверху; поиск по имени файла и тексту (без учёта регистра и ё)."""
        with self._lock:
            metas = sorted(self._index.values(), key=lambda m: m.get("created_at", 0), reverse=True)
            needle = _fold(query.strip()) if isinstance(query, str) and query.strip() else ""
            items = []
            for meta in metas:
                if needle and needle not in _fold(meta.get("file_name", "")):
                    if needle not in self._search_text(meta["id"]):
                        continue
                items.append(
                    {
                        "id": meta["id"],
                        "file_name": meta.get("file_name", ""),
                        "created_at": meta.get("created_at", 0),
                        "duration": meta.get("duration", 0.0),
                        "speakers": meta.get("speakers", 0),
                        "preview": meta.get("preview", ""),
                    }
                )
            return items

    def load(self, entry_id: str) -> dict[str, Any]:
        """Transcript (контракт §2.1) без media_url — его добавляет сервер/API."""
        with self._lock:
            meta, data = self._read(entry_id)
            return _to_transcript(meta, data)

    def preview_file(self, entry_id: str) -> Path | None:
        if not is_valid_id(entry_id):
            return None
        path = self.root / entry_id / PREVIEW_NAME
        return path if path.is_file() else None

    def entry_dir(self, entry_id: str) -> Path | None:
        if not is_valid_id(entry_id):
            return None
        path = self.root / entry_id
        return path if path.is_dir() else None

    # --- изменение --------------------------------------------------------------

    def rename_speaker(self, entry_id: str, speaker_id: str, name: str) -> dict[str, Any]:
        name = " ".join(str(name or "").split())
        if not name:
            raise HistoryError("Имя спикера не может быть пустым")
        if len(name) > 80:
            raise HistoryError("Имя спикера слишком длинное (максимум 80 символов)")
        with self._lock:
            meta, data = self._read(entry_id)
            for speaker in data.get("speakers", []):
                if speaker["id"] == speaker_id:
                    speaker["name"] = name
                    break
            else:
                raise HistoryError("Спикер не найден")
            atomic_write_json(self.root / entry_id / "transcript.json", data)
            return _to_transcript(meta, data)

    def edit_segment(self, entry_id: str, index: int, text: str) -> dict[str, Any]:
        if isinstance(index, bool) or not isinstance(index, int):
            raise HistoryError("Некорректный номер сегмента")
        text = " ".join(str(text or "").split())
        with self._lock:
            meta, data = self._read(entry_id)
            segments = data.get("segments", [])
            if not 0 <= index < len(segments):
                raise HistoryError("Сегмент не найден")
            segments[index]["text"] = text
            segments[index]["words"] = []
            data["full_text"] = _full_text(segments)
            meta["preview"] = _make_preview(data["full_text"])
            meta["edited"] = True
            entry_dir = self.root / entry_id
            atomic_write_json(entry_dir / "transcript.json", data)
            atomic_write_json(entry_dir / "meta.json", meta)
            self._index[entry_id] = meta
            self._text_cache.pop(entry_id, None)
            self._write_index()
            return _to_transcript(meta, data)

    def delete(self, entry_id: str) -> bool:
        with self._lock:
            if not is_valid_id(entry_id):
                return False
            entry_dir = self.root / entry_id
            existed = self._index.pop(entry_id, None) is not None or entry_dir.exists()
            self._text_cache.pop(entry_id, None)
            if entry_dir.exists():
                # Сначала метаданные: если превью держит другой процесс (Windows), каталог
                # без JSON будет дочищен при следующем запуске.
                for name in ("meta.json", "transcript.json"):
                    try:
                        (entry_dir / name).unlink()
                    except OSError:
                        pass
                shutil.rmtree(entry_dir, ignore_errors=True)
                if entry_dir.exists():
                    logger.warning("Каталог записи %s удалён не полностью", entry_id)
            self._write_index()
            return existed

    # --- внутреннее -------------------------------------------------------------

    def _read(self, entry_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        if not is_valid_id(entry_id) or entry_id not in self._index:
            raise HistoryError("Транскрипт не найден")
        entry_dir = self.root / entry_id
        try:
            meta = json.loads((entry_dir / "meta.json").read_text(encoding="utf-8"))
            data = json.loads((entry_dir / "transcript.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("Запись истории %s повреждена: %s", entry_id, exc)
            raise HistoryError("Транскрипт повреждён или удалён") from exc
        return meta, data

    def _search_text(self, entry_id: str) -> str:
        path = self.root / entry_id / "transcript.json"
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return ""
        cached = self._text_cache.get(entry_id)
        if cached and cached[0] == mtime:
            return cached[1]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            text = _fold(data.get("full_text") or _full_text(data.get("segments", [])))
        except (OSError, ValueError):
            text = ""
        self._text_cache[entry_id] = (mtime, text)
        return text

    def _cleanup_pending(self) -> None:
        for child in self.root.glob(f"{PENDING_PREFIX}*"):
            logger.info("Удаляю незавершённую запись истории: %s", child.name)
            shutil.rmtree(child, ignore_errors=True)

    def _load_index(self) -> None:
        index_path = self.root / "index.json"
        cached: dict[str, dict[str, Any]] = {}
        try:
            raw = json.loads(index_path.read_text(encoding="utf-8"))
            entries = raw.get("entries", []) if isinstance(raw, dict) else []
            cached = {e["id"]: e for e in entries if isinstance(e, dict) and is_valid_id(e.get("id"))}
        except FileNotFoundError:
            logger.info("index.json не найден, история будет восстановлена по meta.json")
        except (OSError, ValueError, KeyError, TypeError):
            logger.warning("index.json повреждён, история будет восстановлена по meta.json")

        index: dict[str, dict[str, Any]] = {}
        for child in self.root.iterdir():
            if not child.is_dir() or not is_valid_id(child.name):
                continue
            if child.name in cached and (child / "transcript.json").is_file():
                index[child.name] = cached[child.name]
                continue
            if not (child / "meta.json").exists() and not (child / "transcript.json").exists():
                logger.info("Удаляю остатки удалённой записи истории: %s", child.name)
                shutil.rmtree(child, ignore_errors=True)
                continue
            meta = self._read_meta(child)
            if meta is not None:
                index[child.name] = meta
        changed = set(index) != set(cached)
        self._index = index
        if changed or not index_path.exists():
            self._write_index()

    @staticmethod
    def _read_meta(entry_dir: Path) -> dict[str, Any] | None:
        try:
            meta = json.loads((entry_dir / "meta.json").read_text(encoding="utf-8"))
            if not isinstance(meta, dict) or meta.get("id") != entry_dir.name:
                raise ValueError("id mismatch")
            if not (entry_dir / "transcript.json").is_file():
                raise ValueError("transcript.json missing")
            return meta
        except (OSError, ValueError) as exc:
            logger.warning("Пропускаю повреждённую запись истории %s: %s", entry_dir.name, exc)
            return None

    def _write_index(self) -> None:
        entries = sorted(self._index.values(), key=lambda m: m.get("created_at", 0), reverse=True)
        try:
            atomic_write_json(self.root / "index.json", {"version": INDEX_VERSION, "entries": entries})
        except OSError as exc:
            logger.warning("Не удалось записать index.json: %s", exc)


def _build_speakers(segments: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Спикеры в порядке первого появления: id ``S1``, ``S2``…, имя — метка пайплайна."""
    speakers: list[dict[str, Any]] = []
    mapping: dict[str, str] = {}
    for seg in segments:
        label = seg.get("speaker")
        if label is None or label in mapping:
            continue
        n = len(speakers)
        mapping[label] = f"S{n + 1}"
        speakers.append({"id": mapping[label], "name": str(label), "color": n % PALETTE_SIZE})
    return speakers, mapping


def _to_transcript(meta: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    segments = []
    for i, seg in enumerate(data.get("segments", [])):
        words = [
            {"w": w.get("word", ""), "s": float(w.get("start", 0.0)), "e": float(w.get("end", 0.0))}
            for w in seg.get("words") or []
        ]
        segments.append(
            {
                "index": i,
                "start": float(seg.get("start", 0.0)),
                "end": float(seg.get("end", 0.0)),
                "speaker": seg.get("speaker"),
                "text": seg.get("text", ""),
                "words": words,
            }
        )
    return {
        "id": meta["id"],
        "file_name": meta.get("file_name", ""),
        "source_path": meta.get("source_path", ""),
        "duration": float(meta.get("duration", 0.0)),
        "created_at": meta.get("created_at", 0),
        "processing_time": float(meta.get("processing_time", 0.0)),
        "device": meta.get("device", ""),
        "diarization": meta.get("diarization", ""),
        "speakers": [dict(s) for s in data.get("speakers", [])],
        "segments": segments,
        "media_url": None,
    }
