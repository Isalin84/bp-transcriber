#!/usr/bin/env python3
"""
Бенчмарк конвейера BP Transcriber.

Примеры:
    PYTHONPATH=. python scripts/bench.py meeting.m4a
    PYTHONPATH=. python scripts/bench.py meeting.m4a --device cpu --compare-device mps
    PYTHONPATH=. python scripts/bench.py dialog.m4a --diarization pyannote --truth truth.json --env-file .env
    PYTHONPATH=. python scripts/bench.py meeting.m4a --compare-legacy

Печатает времена этапов, RTF, число сегментов и спикеров. С ``--truth``
(JSON [{"speaker", "start", "end"}, ...]) — пословную точность спикеров и
ошибку границ реплик. С ``--compare-legacy`` — сравнение текста со старым
путём ``model.transcribe_longform`` (difflib по словам).
"""

from __future__ import annotations

import argparse
import difflib
import itertools
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gigaam_transcriber.data_models import TranscriptionResult  # noqa: E402
from gigaam_transcriber.pipeline import PipelineOptions, TranscriptionPipeline  # noqa: E402

_DEVICE_PREFS = {"cpu": "cpu", "mps": "gpu", "gpu": "gpu", "cuda": "gpu", "auto": "auto"}


def load_env_token(env_file: Path) -> bool:
    """Загрузить HF_TOKEN из .env в окружение (значение не печатается)."""
    for line in env_file.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key.strip() == "HF_TOKEN" and value.strip().strip("'\""):
            os.environ["HF_TOKEN"] = value.strip().strip("'\"")
            return True
    return False


def words_of(result: TranscriptionResult) -> list[str]:
    """Слова результата (пословно, если есть слова; иначе по тексту сегментов)."""
    words: list[str] = []
    for segment in result.segments:
        if segment.words:
            words.extend(w.word for w in segment.words)
        else:
            words.extend(segment.text.split())
    return words


def diff_ratio(a: list[str], b: list[str]) -> float:
    """Похожесть двух последовательностей слов (difflib)."""
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def run_pipeline(
    path: Path, device: str, diarization: str, num_speakers: int | None, repeat: int
) -> tuple[TranscriptionPipeline, TranscriptionResult]:
    """Запустить конвейер repeat раз (первый прогон включает прогрев)."""
    pipeline = TranscriptionPipeline(device=_DEVICE_PREFS[device], hf_token=os.environ.get("HF_TOKEN"))
    started = time.monotonic()
    pipeline.prepare()
    print(f"load: {time.monotonic() - started:.2f} s on {pipeline.engine.device_label}")
    result: TranscriptionResult | None = None
    for attempt in range(1, repeat + 1):
        result = pipeline.run(path, PipelineOptions(diarization=diarization, num_speakers=num_speakers))
        report(result, f"run {attempt}")
    assert result is not None
    return pipeline, result


def report(result: TranscriptionResult, title: str) -> None:
    """Печать времён и статистики."""
    meta = result.metadata
    timings = meta.get("timings", {})
    work = sum(v for k, v in timings.items() if k != "load")
    stages = "  ".join(f"{k}={v:.2f}s" for k, v in timings.items())
    print(
        f"[{title}] {stages}  total={work:.2f}s  RTF={work / result.duration:.4f}  "
        f"asr_RTF={timings.get('asr', 0.0) / result.duration:.4f}  "
        f"segments={len(result.segments)}  speakers={meta.get('num_speakers')}  "
        f"device={meta.get('device')}  diarization={meta.get('diarization')}"
        f"{' (' + str(meta['diarization_model']) + ')' if meta.get('diarization_model') else ''}"
    )
    for warning in meta.get("warnings", []):
        print(f"  warning: {warning}")


def evaluate_speakers(result: TranscriptionResult, truth: list[dict]) -> None:
    """Пословная точность спикеров и ошибка границ относительно эталона."""
    truth = sorted(truth, key=lambda t: t["start"])

    def truth_speaker(t: float) -> str | None:
        best, best_dist = None, 1e9
        for turn in truth:
            if turn["start"] <= t <= turn["end"]:
                return turn["speaker"]
            dist = min(abs(t - turn["start"]), abs(t - turn["end"]))
            if dist < best_dist:
                best, best_dist = turn["speaker"], dist
        return best if best_dist <= 1.0 else None

    pairs: list[tuple[str, str]] = []
    for segment in result.segments:
        for word in segment.words or []:
            ref = truth_speaker((word.start + word.end) / 2)
            if ref is not None:
                pairs.append((segment.speaker or "?", ref))
    if not pairs:
        print("speaker eval: нет слов для сравнения")
        return
    hyp_labels = sorted({h for h, _ in pairs})
    ref_labels = sorted({r for _, r in pairs})
    best_correct = 0
    if len(hyp_labels) <= 7:
        for perm in itertools.permutations(ref_labels + [None] * max(0, len(hyp_labels) - len(ref_labels)), len(hyp_labels)):
            mapping = dict(zip(hyp_labels, perm))
            best_correct = max(best_correct, sum(mapping[h] == r for h, r in pairs))
    else:  # жадно: каждой гипотезе — самый частый эталон
        for h in hyp_labels:
            counts = {r: sum(1 for hh, rr in pairs if hh == h and rr == r) for r in ref_labels}
            best_correct += max(counts.values())
    print(f"speaker eval: word accuracy={best_correct / len(pairs):.3f} ({best_correct}/{len(pairs)} words), "
          f"hyp speakers={len(hyp_labels)}, ref speakers={len(ref_labels)}")

    ref_changes = [b["start"] for a, b in zip(truth, truth[1:]) if a["speaker"] != b["speaker"]]
    hyp_changes = [
        b.start for a, b in zip(result.segments, result.segments[1:]) if a.speaker != b.speaker
    ]
    if not ref_changes:
        return
    if not hyp_changes:
        print(f"boundary eval: найдено 0 смен спикера из {len(ref_changes)}")
        return
    errors = [min(abs(c - h) for h in hyp_changes) for c in ref_changes]
    within = sum(e <= 0.5 for e in errors)
    errors_sorted = sorted(errors)
    print(
        f"boundary eval: ref changes={len(ref_changes)}, hyp changes={len(hyp_changes)}, "
        f"within 0.5s={within}/{len(ref_changes)}, median err={errors_sorted[len(errors) // 2]:.2f}s, "
        f"max err={errors_sorted[-1]:.2f}s"
    )


def run_legacy(path: Path, pipeline: TranscriptionPipeline) -> list[str]:
    """Старый путь: gigaam transcribe_longform (pyannote VAD внутри GigaAM)."""
    import gigaam

    from gigaam_transcriber.model_store import models_root

    device = pipeline.engine.device
    assert device is not None
    model = gigaam.load_model(
        pipeline.model_name,
        fp16_encoder=device.type != "cpu",
        device=device,
        download_root=str(models_root(pipeline.model_name)),
    )
    started = time.monotonic()
    legacy = model.transcribe_longform(str(path), word_timestamps=True)
    elapsed = time.monotonic() - started
    words = [w.text for w in legacy.words]
    print(f"[legacy] transcribe_longform={elapsed:.2f}s  segments={len(legacy.segments)}  words={len(words)}")
    return words


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", type=Path)
    parser.add_argument("--device", choices=sorted(_DEVICE_PREFS), default="auto")
    parser.add_argument("--diarization", choices=["none", "auto", "pyannote", "hybrid"], default="none")
    parser.add_argument("--num-speakers", type=int, default=None)
    parser.add_argument("--repeat", type=int, default=1, help="прогонов конвейера (первый — с прогревом)")
    parser.add_argument("--compare-device", choices=sorted(_DEVICE_PREFS), default=None,
                        help="второе устройство для сравнения скорости и текста")
    parser.add_argument("--compare-legacy", action="store_true")
    parser.add_argument("--truth", type=Path, default=None, help="эталон реплик (JSON)")
    parser.add_argument("--env-file", type=Path, default=None, help=".env с HF_TOKEN")
    parser.add_argument("--save", type=Path, default=None, help="сохранить результат в JSON")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    if args.env_file is not None:
        print(f"HF_TOKEN from env file: {'yes' if load_env_token(args.env_file) else 'no'}")

    pipeline, result = run_pipeline(args.file, args.device, args.diarization, args.num_speakers, args.repeat)
    print(f"duration={result.duration:.1f}s  words={len(words_of(result))}")
    if args.truth is not None:
        evaluate_speakers(result, json.loads(args.truth.read_text(encoding="utf-8")))
    if args.save is not None:
        args.save.write_text(result.to_json(), encoding="utf-8")

    if args.compare_legacy:
        legacy = run_legacy(args.file, pipeline)
        ratio = diff_ratio(legacy, words_of(result))
        print(f"[legacy vs new] word diff ratio={ratio:.4f}  legacy words={len(legacy)}  new words={len(words_of(result))}")
    if args.compare_device:
        pipeline.close()
        other_pipeline, other = run_pipeline(args.file, args.compare_device, "none", None, args.repeat)
        ratio = diff_ratio(words_of(result), words_of(other))
        print(f"[{args.device} vs {args.compare_device}] word diff ratio={ratio:.4f}  "
              f"identical text={result.text == other.text}")
        other_pipeline.close()

    pipeline.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
