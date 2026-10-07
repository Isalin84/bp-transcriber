"""
Тесты для модуля vad.
"""

import os
import random
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from gigaam_transcriber.audio_io import SAMPLE_RATE, DecodedAudio
from gigaam_transcriber.progress import Cancelled, CancelToken
from gigaam_transcriber.vad import detect_speech, make_chunks


class TestMakeChunks:
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

    def test_silence_has_no_speech(self):
        assert detect_speech(self._audio(np.zeros(5 * SAMPLE_RATE))) == []

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


class TestTorchThreads:
    """silero_vad при импорте ставит torch.set_num_threads(1) — vad.py это откатывает."""

    def test_thread_count_unchanged_after_model_load(self):
        # отдельный процесс: в текущем silero_vad уже мог быть импортирован
        code = (
            "import torch\n"
            "torch.set_num_threads(3)\n"
            "import numpy as np\n"
            "from pathlib import Path\n"
            "from gigaam_transcriber import vad\n"
            "from gigaam_transcriber.audio_io import DecodedAudio\n"
            "vad._get_model()\n"
            "vad.detect_speech(DecodedAudio(pcm=np.zeros(16000, dtype=np.int16), source=Path('x')))\n"
            "print(torch.get_num_threads())\n"
        )
        root = Path(__file__).resolve().parent.parent
        out = subprocess.run(
            [sys.executable, "-c", code],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "PYTHONPATH": str(root)},
        )
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip().splitlines()[-1] == "3"
