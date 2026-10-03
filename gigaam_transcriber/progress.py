"""
Общие типы прогресса и отмены для конвейера транскрипции.

Функции низкого уровня принимают ``on_progress: Callable[[float], None]``
(доля 0..1 собственного этапа) и ``cancel: CancelToken``. Взвешивание
в общий прогресс выполняет только конвейер.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

from .exceptions import TranscriberError

STAGES = ("load", "decode", "vad", "asr", "diarize", "finalize")


@dataclass
class ProgressEvent:
    """Событие прогресса конвейера."""

    stage: str  # один из STAGES
    stage_progress: float  # 0..1 внутри этапа
    progress: float  # 0..1 общий (взвешенный)
    message: str  # человекочитаемое сообщение по-русски
    eta_s: float | None = None


ProgressCallback = Callable[[ProgressEvent], None]


class Cancelled(TranscriberError):
    """Операция отменена пользователем."""


class CancelToken:
    """Потокобезопасный флаг отмены."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Запросить отмену."""
        self._event.set()

    @property
    def cancelled(self) -> bool:
        """Была ли запрошена отмена."""
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """Выбросить Cancelled, если отмена запрошена."""
        if self._event.is_set():
            raise Cancelled("Отменено пользователем")
