"""
Контракт с внутренностями GigaAM, на которые опирается asr_engine.py.

Если обновление GigaAM меняет сигнатуры ``load_model``, ``forward``, ``_decode``
или формат слов, эти тесты падают явно. Нужна скачанная модель (иначе пропуск).
"""

from __future__ import annotations

import inspect
from pathlib import Path

import numpy as np
import pytest

from gigaam_transcriber.model_store import DEFAULT_MODEL, is_model_available

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not is_model_available(DEFAULT_MODEL), reason="модель GigaAM не скачана"),
]


@pytest.fixture(scope="module")
def engine():
    from gigaam_transcriber.asr_engine import GigaAMEngine

    eng = GigaAMEngine(device="cpu")
    eng.load()
    yield eng
    eng.unload()


def test_module_tables():
    import gigaam

    assert DEFAULT_MODEL in gigaam._MODEL_HASHES
    assert isinstance(gigaam._URL_DIR, str)
    assert callable(gigaam.hash_path)


def test_load_model_signature():
    import gigaam

    params = inspect.signature(gigaam.load_model).parameters
    for name in ("model_name", "fp16_encoder", "device", "download_root"):
        assert name in params, name


def test_forward_and_decode_signatures(engine):
    model = engine.model
    assert list(inspect.signature(model.forward).parameters)[:2] == ["features", "feature_lengths"]
    decode_params = list(inspect.signature(model._decode).parameters)
    assert decode_params[:4] == ["encoded", "encoded_len", "wav_lens", "word_timestamps"]


def test_decode_return_shape(engine):
    import torch

    from gigaam_transcriber.asr_engine import _collate

    model = engine.model
    rng = np.random.default_rng(0)
    wavs = [torch.from_numpy((rng.standard_normal(n) * 0.05).astype(np.float32)) for n in (32000, 16000)]
    param = next(model.parameters())
    with torch.inference_mode():
        batch, lengths = _collate(wavs)
        encoded, encoded_len = model.forward(batch.to(param.device).to(param.dtype), lengths)
        decoded = model._decode(encoded, encoded_len, lengths, True)
    assert encoded.shape[0] == 2 and encoded_len.shape == (2,)
    assert isinstance(decoded, list) and len(decoded) == 2
    for text, words in decoded:
        assert isinstance(text, str)
        assert isinstance(words, list)
        for word in words:
            assert isinstance(word.text, str)
            assert isinstance(word.start, float) and isinstance(word.end, float)


def test_engine_end_to_end(engine):
    from gigaam_transcriber.audio_io import SAMPLE_RATE, DecodedAudio
    from gigaam_transcriber.vad import Chunk

    rng = np.random.default_rng(1)
    pcm = (rng.standard_normal(12 * SAMPLE_RATE) * 300).astype(np.int16)
    audio = DecodedAudio(pcm=pcm, source=Path("noise.wav"))
    segments = engine.transcribe(audio, [Chunk(0.5, 4.0), Chunk(5.0, 11.0)])
    for segment in segments:
        assert 0.0 <= segment.start <= segment.end <= audio.duration
        for word in segment.words:
            assert segment.start <= word.start <= word.end <= segment.end
