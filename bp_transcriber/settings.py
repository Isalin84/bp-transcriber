"""Настройки приложения и хранение HF-токена.

Настройки — JSON в ``config_dir()/settings.json`` (атомарная запись).
Токен — keyring (service ``BP Transcriber``, user ``hf_token``); если keyring
недоступен — файл с правами 600 рядом с настройками. Значение токена
никогда не логируется и не возвращается в JS.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any
from collections.abc import Mapping

from . import paths

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

THEMES = ("system", "dark", "light")
DEVICES = ("auto", "cpu", "gpu")
DIARIZATION_MODES = ("auto", "pyannote", "hybrid", "none")
FORMATS = ("txt", "md", "docx", "srt", "vtt", "json")
MAX_SPEAKERS = 20

KEYRING_SERVICE = "BP Transcriber"
KEYRING_USER = "hf_token"
TOKEN_FILE_NAME = "hf_token"
TOKEN_ENV_VARS = ("HF_TOKEN", "HUGGINGFACE_TOKEN")


class SettingsError(ValueError):
    """Недопустимое значение настройки (сообщение по-русски)."""


@dataclass
class Settings:
    theme: str = "system"
    device: str = "auto"
    diarization: str = "auto"
    num_speakers: int | None = None
    autosave: bool = False
    autosave_dir: str | None = None
    autosave_formats: list[str] = field(default_factory=lambda: ["docx", "txt"])
    include_timestamps: bool = True
    onboarding_done: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Settings:
        """Мягкая загрузка: неизвестные ключи игнорируются, недопустимые значения → по умолчанию."""
        settings = cls()
        for f in fields(cls):
            if f.name not in data:
                continue
            try:
                setattr(settings, f.name, _validate(f.name, data[f.name]))
            except SettingsError as exc:
                logger.warning("Настройка %s сброшена по умолчанию: %s", f.name, exc)
        return settings

    def merged(self, partial: Mapping[str, Any]) -> Settings:
        """Строгое обновление: недопустимое значение → SettingsError."""
        data = self.to_dict()
        known = {f.name for f in fields(self)}
        for key, value in partial.items():
            if key not in known:
                logger.debug("Неизвестная настройка проигнорирована: %s", key)
                continue
            data[key] = _validate(key, value)
        return Settings(**data)


def _validate(name: str, value: Any) -> Any:
    def choice(options: tuple[str, ...]) -> str:
        if not isinstance(value, str) or value not in options:
            raise SettingsError(f"Недопустимое значение «{value}» для параметра {name}")
        return value

    if name == "theme":
        return choice(THEMES)
    if name == "device":
        return choice(DEVICES)
    if name == "diarization":
        return choice(DIARIZATION_MODES)
    if name in ("autosave", "include_timestamps", "onboarding_done"):
        if not isinstance(value, bool):
            raise SettingsError(f"Параметр {name} должен быть логическим значением")
        return value
    if name == "num_speakers":
        return validate_num_speakers(value)
    if name == "autosave_dir":
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if not isinstance(value, str):
            raise SettingsError("Папка автосохранения задана некорректно")
        return value.strip()
    if name == "autosave_formats":
        if not isinstance(value, (list, tuple)):
            raise SettingsError("Список форматов задан некорректно")
        result: list[str] = []
        for fmt in value:
            if fmt not in FORMATS:
                raise SettingsError(f"Неизвестный формат: {fmt}")
            if fmt not in result:
                result.append(fmt)
        return result
    raise SettingsError(f"Неизвестный параметр: {name}")


def validate_num_speakers(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise SettingsError("Число спикеров должно быть целым")
    value = int(value)
    if not 1 <= value <= MAX_SPEAKERS:
        raise SettingsError(f"Число спикеров должно быть от 1 до {MAX_SPEAKERS}")
    return value


def atomic_write_text(path: Path, text: str) -> None:
    """Запись через временный файл в том же каталоге + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        # На Windows замена может кратко падать, если файл держит антивирус/индексатор.
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


class TokenStore:
    """HF-токен: keyring с фолбэком на файл 0600. Значение кэшируется в памяти."""

    _UNSET = object()

    def __init__(self, fallback_path: Path | None = None, *, use_keyring: bool = True):
        self._fallback_path = fallback_path or (paths.config_dir() / TOKEN_FILE_NAME)
        self._use_keyring = use_keyring
        self._lock = threading.Lock()
        self._cached: Any = self._UNSET

    def get(self) -> str | None:
        with self._lock:
            if self._cached is self._UNSET:
                self._cached = self._load()
            return self._cached

    def is_set(self) -> bool:
        return bool(self.get())

    def set(self, token: str) -> None:
        token = normalize_token(token)
        if not token:
            raise SettingsError("Токен пустой")
        with self._lock:
            stored = False
            if self._use_keyring:
                try:
                    import keyring

                    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, token)
                    stored = True
                except Exception as exc:  # noqa: BLE001 — любой бэкенд keyring
                    logger.warning("keyring недоступен (%s), токен сохранён в файл", type(exc).__name__)
            if stored:
                self._remove_file()
            else:
                self._write_file(token)
            self._cached = token

    def clear(self) -> None:
        with self._lock:
            if self._use_keyring:
                try:
                    import keyring

                    if keyring.get_password(KEYRING_SERVICE, KEYRING_USER) is not None:
                        keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Не удалось удалить токен из keyring: %s", type(exc).__name__)
            self._remove_file()
            self._cached = None

    def _load(self) -> str | None:
        if self._use_keyring:
            try:
                import keyring

                value = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
                if value:
                    return normalize_token(value) or None
            except Exception as exc:  # noqa: BLE001
                logger.warning("keyring недоступен (%s), читаю токен из файла", type(exc).__name__)
        try:
            value = self._fallback_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning("Не удалось прочитать файл токена: %s", exc)
            return None
        return normalize_token(value) or None

    def _write_file(self, token: str) -> None:
        path = self._fallback_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(token)
        if sys.platform != "win32":
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)

    def _remove_file(self) -> None:
        try:
            self._fallback_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Не удалось удалить файл токена: %s", exc)


def normalize_token(value: str | None) -> str:
    if not value:
        return ""
    value = value.strip().strip("\"'").strip()
    return value if value and not any(ch.isspace() for ch in value) else ""


class SettingsStore:
    """Потокобезопасное хранилище настроек + доступ к токену."""

    def __init__(self, path: Path | None = None, token_store: TokenStore | None = None):
        self.path = path or (paths.config_dir() / "settings.json")
        self.tokens = token_store or TokenStore(self.path.parent / TOKEN_FILE_NAME)
        self._lock = threading.Lock()
        self._settings = self._load()

    @property
    def first_run(self) -> bool:
        """Онбординг ещё не пройден (см. ``complete_onboarding``)."""
        with self._lock:
            return not self._settings.onboarding_done

    def get(self) -> Settings:
        with self._lock:
            return Settings(**self._settings.to_dict())

    def update(self, partial: Mapping[str, Any]) -> Settings:
        if not isinstance(partial, Mapping):
            raise SettingsError("Ожидался объект с настройками")
        with self._lock:
            new = self._settings.merged(partial)
            atomic_write_json(self.path, {"version": SCHEMA_VERSION, **new.to_dict()})
            self._settings = new
            return Settings(**new.to_dict())

    def public_dict(self) -> dict[str, Any]:
        return {**self.get().to_dict(), "hf_token_set": self.tokens.is_set()}

    def _load(self) -> Settings:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return Settings()
        except OSError as exc:
            logger.warning("Не удалось прочитать настройки: %s", exc)
            return Settings()
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except ValueError:
            logger.warning("Файл настроек повреждён, используются значения по умолчанию")
            return Settings()
        version = data.get("version")
        if version is not None and version != SCHEMA_VERSION:
            logger.info("Версия схемы настроек %s, текущая %s", version, SCHEMA_VERSION)
        return Settings.from_dict(data)


# --- импорт существующего токена -------------------------------------------------


def _parse_dotenv(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, _, value = line.partition("=")
        key = key.strip()
        if key not in TOKEN_ENV_VARS:
            continue
        value = value.strip()
        if value[:1] in ("'", '"') and value[-1:] == value[:1] and len(value) >= 2:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0]
        token = normalize_token(value)
        if token:
            values[key] = token
    for key in TOKEN_ENV_VARS:
        if key in values:
            return values[key]
    return None


def _dotenv_dirs() -> list[Path]:
    dirs = [Path.cwd()]
    if paths.is_frozen():
        exe_dir = Path(sys.executable).resolve().parent
        dirs.append(exe_dir)
        # macOS: …/BP Transcriber.app/Contents/MacOS → каталог, где лежит .app
        for parent in exe_dir.parents:
            if parent.suffix == ".app":
                dirs.append(parent.parent)
                break
    else:
        root = paths.repo_root()
        if root is not None:
            dirs.append(root)
    unique: list[Path] = []
    for d in dirs:
        try:
            d = d.resolve()
        except OSError:
            continue
        if d not in unique:
            unique.append(d)
    return unique


def _hf_cache_token_paths() -> list[Path]:
    result: list[Path] = []
    if os.environ.get("HF_TOKEN_PATH"):
        result.append(Path(os.environ["HF_TOKEN_PATH"]).expanduser())
    if os.environ.get("HF_HOME"):
        result.append(Path(os.environ["HF_HOME"]).expanduser() / "token")
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        result.append(Path(xdg).expanduser() / "huggingface" / "token")
    result.append(Path.home() / ".cache" / "huggingface" / "token")
    return result


def find_token_candidate(dotenv_dirs: list[Path] | None = None) -> tuple[str, str] | None:
    """(источник, токен) — только для внутреннего использования, значение не уходит в JS."""
    for var in TOKEN_ENV_VARS:
        token = normalize_token(os.environ.get(var))
        if token:
            return "env", token
    for directory in dotenv_dirs if dotenv_dirs is not None else _dotenv_dirs():
        env_file = directory / ".env"
        if env_file.is_file():
            token = _parse_dotenv(env_file)
            if token:
                return ".env", token
    for path in _hf_cache_token_paths():
        try:
            token = normalize_token(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if token:
            return "hf_cache", token
    return None


def find_token_import_candidate(dotenv_dirs: list[Path] | None = None) -> dict[str, str] | None:
    """``{"source": ".env" | "env" | "hf_cache"}`` или None. Значение токена не возвращается."""
    found = find_token_candidate(dotenv_dirs)
    return {"source": found[0]} if found else None
