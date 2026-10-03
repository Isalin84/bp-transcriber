"""Сборка BP Transcriber через PyInstaller.

    python packaging/build_app.py [--clean] [--allow-missing-ffmpeg] [--selftest]

Результат:
    macOS:   dist/BP Transcriber.app                (ad-hoc подпись, проверка codesign)
    Windows: dist/BP Transcriber/BP Transcriber.exe (onedir)

Версия берётся из ``bp_transcriber.__version__``. Без ``vendor/ffmpeg/ffmpeg[.exe]``
сборка падает, если не указан ``--allow-missing-ffmpeg``.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "packaging" / "bp_transcriber.spec"
DIST = ROOT / "dist"
WORK = ROOT / "build" / "pyinstaller"
APP_NAME = "BP Transcriber"
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"


def read_version() -> str:
    text = (ROOT / "bp_transcriber" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.M)
    if not match:
        raise SystemExit("Не найден __version__ в bp_transcriber/__init__.py")
    return match.group(1)


def ffmpeg_path() -> Path:
    return ROOT / "vendor" / "ffmpeg" / ("ffmpeg.exe" if IS_WIN else "ffmpeg")


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("+", " ".join(f'"{c}"' if " " in c else c for c in cmd), flush=True)
    return subprocess.run(cmd, **kwargs)


def dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file() and not p.is_symlink():
            total += p.stat().st_size
    return total


def human(n: float) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} ТБ"


def output_path() -> Path:
    return DIST / f"{APP_NAME}.app" if IS_MAC else DIST / APP_NAME / f"{APP_NAME}.exe"


def executable_path() -> Path:
    return DIST / f"{APP_NAME}.app" / "Contents" / "MacOS" / APP_NAME if IS_MAC else output_path()


def sign_and_verify_mac(app: Path) -> None:
    """Проверить подпись .app; при сбое переподписать ad-hoc целиком (или Developer ID из BP_CODESIGN_IDENTITY)."""
    # Расширенные атрибуты (com.apple.FinderInfo, quarantine) ломают codesign --strict.
    run(["xattr", "-cr", str(app)], check=False)
    verify = ["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)]
    if run(verify, capture_output=True, text=True).returncode == 0:
        print("Подпись .app в порядке")
    else:
        identity = os.environ.get("BP_CODESIGN_IDENTITY") or "-"
        print(f"Подпись не прошла проверку — переподписываю (identity: {identity})")
        cmd = ["codesign", "--force", "--deep", "--timestamp=none" if identity == "-" else "--timestamp",
               "-s", identity]
        if identity != "-":
            cmd += ["--options", "runtime"]
            entitlements = os.environ.get("BP_ENTITLEMENTS")
            if entitlements:
                cmd += ["--entitlements", entitlements]
        run([*cmd, str(app)], check=True)
        result = run(verify, capture_output=True, text=True)
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            raise SystemExit("codesign --verify --deep --strict не прошёл после переподписи")
    run(["codesign", "-dv", "--verbose=2", str(app)], check=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сборка BP Transcriber (PyInstaller)")
    parser.add_argument("--clean", action="store_true", help="удалить build/ и dist/ и кэш PyInstaller")
    parser.add_argument("--allow-missing-ffmpeg", action="store_true", help="собрать без встроенного ffmpeg")
    parser.add_argument("--selftest", action="store_true", help="после сборки запустить --selftest")
    args = parser.parse_args(argv)

    version = read_version()
    print(f"BP Transcriber {version}: сборка для {sys.platform}, Python {sys.version.split()[0]}")

    env = dict(os.environ)
    # Подпроцессы анализа PyInstaller должны видеть пакеты этой рабочей копии.
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    env["BP_VERSION"] = version

    ffmpeg = ffmpeg_path()
    if not ffmpeg.is_file():
        if not args.allow_missing_ffmpeg:
            hint = "packaging/macos/build_ffmpeg.sh" if IS_MAC else "packaging/windows/ (скрипт сборки ffmpeg)"
            print(f"Ошибка: нет {ffmpeg}. Соберите ffmpeg ({hint}) или укажите --allow-missing-ffmpeg.",
                  file=sys.stderr)
            return 2
        print(f"ВНИМАНИЕ: {ffmpeg} отсутствует — приложение будет искать ffmpeg в PATH")
        env["BP_ALLOW_MISSING_FFMPEG"] = "1"

    if args.clean:
        for path in (WORK, DIST / APP_NAME, DIST / f"{APP_NAME}.app"):
            if path.exists():
                print(f"Удаляю {path}")
                shutil.rmtree(path)

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--log-level", "WARN",
           "--distpath", str(DIST), "--workpath", str(WORK)]
    if args.clean:
        cmd.append("--clean")
    cmd.append(str(SPEC))
    started = time.monotonic()
    result = run(cmd, cwd=ROOT, env=env)
    if result.returncode != 0:
        print("PyInstaller завершился с ошибкой", file=sys.stderr)
        return result.returncode

    out = output_path()
    if not out.exists():
        print(f"Ошибка: не найден результат {out}", file=sys.stderr)
        return 1
    if IS_MAC:
        # BUNDLE копирует содержимое COLLECT в .app; промежуточная папка не нужна.
        onedir = DIST / APP_NAME
        if onedir.is_dir():
            shutil.rmtree(onedir)
        sign_and_verify_mac(out)

    size_root = out if IS_MAC else out.parent
    print(f"\nГотово за {time.monotonic() - started:.0f} с: {out} ({human(dir_size(size_root))})")

    if args.selftest:
        exe = executable_path()
        print(f"Самопроверка: {exe} --selftest")
        code = run([str(exe), "--selftest"], env={k: v for k, v in os.environ.items() if k != "BP_FFMPEG"}).returncode
        if code != 0:
            print(f"--selftest завершился с кодом {code}", file=sys.stderr)
            return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
