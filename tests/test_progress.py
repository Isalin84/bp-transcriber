"""
Тесты для модуля progress.
"""

import threading

import pytest

from gigaam_transcriber import TranscriberError
from gigaam_transcriber.progress import STAGES, Cancelled, CancelToken, ProgressEvent


class TestCancelToken:
    """Тесты CancelToken."""

    def test_initial_state(self):
        token = CancelToken()
        assert token.cancelled is False
        token.raise_if_cancelled()  # не должно бросать

    def test_cancel_sets_flag(self):
        token = CancelToken()
        token.cancel()
        assert token.cancelled is True

    def test_cancel_is_idempotent(self):
        token = CancelToken()
        token.cancel()
        token.cancel()
        assert token.cancelled is True

    def test_raise_if_cancelled(self):
        token = CancelToken()
        token.cancel()
        with pytest.raises(Cancelled, match="Отменено пользователем"):
            token.raise_if_cancelled()

    def test_cancel_from_other_thread(self):
        token = CancelToken()
        thread = threading.Thread(target=token.cancel)
        thread.start()
        thread.join()
        assert token.cancelled is True

    def test_tokens_are_independent(self):
        first, second = CancelToken(), CancelToken()
        first.cancel()
        assert second.cancelled is False


def test_cancelled_is_transcriber_error():
    assert issubclass(Cancelled, TranscriberError)


def test_progress_event_defaults():
    event = ProgressEvent(stage="asr", stage_progress=0.5, progress=0.4, message="Распознавание")
    assert event.eta_s is None
    assert event.stage in STAGES
