"""
Тесты для модуля vad.
"""

import random
from pathlib import Path

import numpy as np
import pytest

from gigaam_transcriber.audio_io import SAMPLE_RATE, DecodedAudio
from gigaam_transcriber.progress import Cancelled, CancelToken
from gigaam_transcriber.vad import Chunk, detect_speech, make_chunks


def _bounds(chunks: list[Chunk]) -> list[tuple[float, float]]:
    return [(round(c.start, 3), round(c.end, 3)) for c in chunks]


class TestMakeChunks:
    def test_empty(self):
        assert make_chunks([]) == []

    def test_merges_short_regions(self):
        chunks = make_chunks([(0, 5), (5.5, 10), (10.5, 16)])
        assert _bounds(chunks) == [(0, 16)]

    def test_closes_chunk_after_min_duration(self):
        chunks = make_chunks([(0, 16), (17, 20)])
        assert _bounds(chunks) == [(0, 16), (17, 20)]

    def test_closes_chunk_before_exceeding_max_duration(self):
        chunks = make_chunks([(0, 10), (11, 21), (22, 30)])
        assert _bounds(chunks) == [(0, 21), (22, 30)]

    def test_splits_long_region_evenly(self):
        chunks = make_chunks([(0, 70)])
        assert len(chunks) == 3
        assert all(c.duration == pytest.approx(70 / 3) for c in chunks)
        assert chunks[0].start == 0 and chunks[-1].end == 70
        assert all(a.end == b.start for a, b in zip(chunks, chunks[1:]))

    def test_region_at_limit_is_not_split(self):
        assert _bounds(make_chunks([(0, 25.0)])) == [(0, 25.0)]
        assert len(make_chunks([(0, 25.01)])) == 2

    def test_drops_tiny_tail(self):
        assert make_chunks([(0, 0.1)]) == []
        chunks = make_chunks([(0, 15.5), (30, 30.1)])
        assert _bounds(chunks) == [(0, 15.5)]

    def test_custom_limits(self):
        chunks = make_chunks([(0, 12)], strict_limit=5.0)
        assert len(chunks) == 3
        assert max(c.duration for c in chunks) <= 5.0

    def test_never_exceeds_strict_limit(self):
        rng = random.Random(7)
        for _ in range(200):
            speech, cursor = [], 0.0
            for _ in range(rng.randint(1, 30)):
                cursor += rng.uniform(0.0, 40.0)
                length = rng.uniform(0.05, 80.0)
                speech.append((cursor, cursor + length))
                cursor += length
            chunks = make_chunks(speech)
            assert all(0.2 < c.duration <= 25.0 for c in chunks)
            assert all(a.end <= b.start + 1e-9 for a, b in zip(chunks, chunks[1:]))


class TestDetectSpeech:
    @staticmethod
    def _audio(samples: np.ndarray) -> DecodedAudio:
        return DecodedAudio(pcm=samples.astype(np.int16), source=Path("synthetic.wav"))

    def test_empty_audio(self):
        assert detect_speech(self._audio(np.zeros(0))) == []

    def test_silence_has_no_speech(self):
        assert detect_speech(self._audio(np.zeros(5 * SAMPLE_RATE))) == []

    def test_noise_runs_and_reports_progress(self):
        rng = np.random.default_rng(0)
        noise = rng.normal(0, 3000, 5 * SAMPLE_RATE)
        values: list[float] = []
        result = detect_speech(self._audio(noise), on_progress=values.append)
        assert all(0.0 <= start < end <= 5.1 for start, end in result)
        assert values == sorted(values) and values[-1] == pytest.approx(1.0)

    def test_cancel_before_start(self):
        token = CancelToken()
        token.cancel()
        with pytest.raises(Cancelled):
            detect_speech(self._audio(np.zeros(2 * SAMPLE_RATE)), cancel=token)

    def test_cancel_from_progress_callback(self):
        token = CancelToken()
        seen: list[float] = []

        def on_progress(fraction: float) -> None:
            seen.append(fraction)
            if fraction >= 0.2:
                token.cancel()

        with pytest.raises(Cancelled):
            detect_speech(
                self._audio(np.zeros(30 * SAMPLE_RATE)), on_progress=on_progress, cancel=token
            )
        assert seen and max(seen) < 0.5  # прервано рано, а не на последнем окне
