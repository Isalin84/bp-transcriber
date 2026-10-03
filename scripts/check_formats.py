#!/usr/bin/env python3
"""Проверка, что (встроенный) ffmpeg декодирует типичные аудио/видео форматы.

Фикстуры (синус 440 Гц, 2 с) создаёт «полный» ffmpeg (``--generator``, по умолчанию
первый ``ffmpeg`` из PATH), а декодирует ``gigaam_transcriber.audio_io.decode`` с
ffmpeg из ``--decoder`` (он подставляется в ``BP_FFMPEG``; по умолчанию
``vendor/ffmpeg/ffmpeg[.exe]``, если он есть, иначе ffmpeg из PATH). Формат, который генератор не умеет кодировать,
помечается SKIP; сбой декодирования - FAIL и ненулевой код выхода.

Коды выхода: 0 - всё декодировано, 1 - есть сбои, 2 - нет ffmpeg / ничего не проверено.

    python scripts/check_formats.py --decoder vendor/ffmpeg/ffmpeg
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DURATION_S = 2.0
DURATION_TOLERANCE_S = 0.35

SINE = ["-f", "lavfi", "-i", f"sine=frequency=440:duration={DURATION_S}:sample_rate=44100"]
VIDEO = ["-f", "lavfi", "-i", f"testsrc=duration={DURATION_S}:size=64x48:rate=10"]


@dataclass(frozen=True)
class Fixture:
    filename: str
    inputs: list[str]
    codec_args: list[str]


FIXTURES: list[Fixture] = [
    Fixture("tone.wav", SINE, ["-c:a", "pcm_s16le"]),
    Fixture("tone-stereo-48k.wav", SINE, ["-ar", "48000", "-ac", "2"]),
    Fixture("tone-float.wav", SINE, ["-c:a", "pcm_f32le"]),
    Fixture("tone.mp3", SINE, ["-c:a", "libmp3lame"]),
    Fixture("tone.mp2", SINE, ["-c:a", "mp2"]),
    Fixture("tone.m4a", SINE, ["-c:a", "aac"]),
    Fixture("tone.aac", SINE, ["-c:a", "aac"]),
    Fixture("tone.flac", SINE, ["-c:a", "flac"]),
    Fixture("tone.ogg", SINE, ["-c:a", "libvorbis"]),
    Fixture("tone.opus", SINE, ["-c:a", "libopus"]),
    Fixture("tone.wma", SINE, ["-c:a", "wmav2"]),
    Fixture("tone.aiff", SINE, ["-c:a", "pcm_s16be"]),
    Fixture("tone.ac3", SINE, ["-c:a", "ac3"]),
    Fixture("video.mp4", [*SINE, *VIDEO], ["-c:v", "mpeg4", "-c:a", "aac", "-shortest"]),
    Fixture("video.mov", [*SINE, *VIDEO], ["-c:v", "mpeg4", "-c:a", "aac", "-shortest"]),
    Fixture("video.mkv", [*SINE, *VIDEO], ["-c:v", "mpeg4", "-c:a", "libvorbis", "-shortest"]),
    Fixture("video.avi", [*SINE, *VIDEO], ["-c:v", "mpeg4", "-c:a", "libmp3lame", "-shortest"]),
    Fixture("video.webm", [*SINE, *VIDEO], ["-c:v", "libvpx", "-c:a", "libopus", "-shortest"]),
    # Путь с пробелом и кириллицей: типичный источник проблем на Windows.
    Fixture("запись разговора.m4a", SINE, ["-c:a", "aac"]),
]


def _same_file(a: str, b: str) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return a == b


def _generate(generator: str, fixture: Fixture, out: Path) -> str | None:
    """Создать файл. Возвращает None при успехе, иначе короткую причину."""
    cmd = [generator, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           *fixture.inputs, *fixture.codec_args, str(out)]  # fmt: skip
    proc = subprocess.run(cmd, capture_output=True, timeout=120)
    if proc.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["ошибка ffmpeg"]
        return tail[0][:120]
    return None


def _decode(path: Path) -> tuple[float, int]:
    """Декодировать через audio_io; вернуть (длительность, пиковая амплитуда int16)."""
    from gigaam_transcriber.audio_io import decode

    audio = decode(path)
    peak = int(abs(audio.pcm.astype("int32")).max()) if audio.pcm.size else 0
    return audio.duration, peak


def main(argv: list[str] | None = None) -> int:
    exe = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    vendored = REPO_ROOT / "vendor" / "ffmpeg" / exe
    default_decoder = os.environ.get("BP_FFMPEG") or (str(vendored) if vendored.is_file() else shutil.which("ffmpeg"))
    parser.add_argument("--decoder", default=default_decoder,
                        help="ffmpeg, которым декодирует приложение "
                             "(по умолчанию BP_FFMPEG, затем vendor/ffmpeg, затем ffmpeg из PATH)")
    parser.add_argument("--generator", default=os.environ.get("FIXTURE_FFMPEG") or shutil.which("ffmpeg"),
                        help="полный ffmpeg для создания фикстур (по умолчанию ffmpeg из PATH)")
    args = parser.parse_args(argv)

    decoder = Path(args.decoder or "")
    if not args.decoder or not decoder.is_file():
        print(f"ОШИБКА: декодер не найден: {decoder}", file=sys.stderr)
        return 2
    if not args.generator or not Path(args.generator).is_file():
        print("ОШИБКА: нет ffmpeg для создания фикстур (укажите --generator или добавьте ffmpeg в PATH)",
              file=sys.stderr)
        return 2
    os.environ["BP_FFMPEG"] = str(decoder)
    # find_ffmpeg() при отсутствии BP_FFMPEG-файла упал бы на PATH - убеждаемся, что берётся именно наш.
    sys.path.insert(0, str(REPO_ROOT))
    from gigaam_transcriber.audio_io import find_ffmpeg

    if not _same_file(find_ffmpeg(), str(decoder)):
        print(f"ОШИБКА: audio_io использует {find_ffmpeg()} вместо {decoder}", file=sys.stderr)
        return 2

    print(f"Генератор фикстур: {args.generator}")
    print(f"Декодер (BP_FFMPEG): {decoder}")
    if _same_file(args.generator, str(decoder)):
        print("Внимание: генератор и декодер - один файл; форматы без энкодера будут пропущены.")
    print()

    rows: list[tuple[str, str, str]] = []
    failures = checked = 0
    with tempfile.TemporaryDirectory(prefix="bp-formats-") as tmp:
        for fixture in FIXTURES:
            path = Path(tmp) / fixture.filename
            reason = _generate(args.generator, fixture, path)
            if reason:
                rows.append((fixture.filename, "SKIP", f"генератор не смог создать: {reason}"))
                continue
            checked += 1
            try:
                duration, peak = _decode(path)
            except Exception as exc:  # noqa: BLE001 - любая ошибка декодера = FAIL
                failures += 1
                rows.append((fixture.filename, "FAIL", f"{type(exc).__name__}: {exc}"[:120]))
                continue
            problems = []
            if abs(duration - DURATION_S) > DURATION_TOLERANCE_S:
                problems.append(f"длительность {duration:.2f} с вместо {DURATION_S:.1f} с")
            if peak < 1000:
                problems.append(f"тишина на выходе (пик {peak})")
            if problems:
                failures += 1
                rows.append((fixture.filename, "FAIL", "; ".join(problems)))
            else:
                rows.append((fixture.filename, "OK", f"{duration:.2f} с, пик {peak}"))

    width = max(len(r[0]) for r in rows)
    print(f"{'Файл'.ljust(width)}  Итог  Детали")
    print(f"{'-' * width}  ----  {'-' * 40}")
    for name, status, detail in rows:
        print(f"{name.ljust(width)}  {status.ljust(4)}  {detail}")
    skipped = sum(1 for r in rows if r[1] == "SKIP")
    print(f"\nПроверено: {checked}, сбоев: {failures}, пропущено: {skipped}")

    if checked == 0:
        print("ОШИБКА: не удалось создать ни одной фикстуры", file=sys.stderr)
        return 2
    return 1 if failures else 0


if __name__ == "__main__":
    # Windows-консоль не всегда UTF-8: не падаем на кириллице в выводе.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    sys.exit(main())
