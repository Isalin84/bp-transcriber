#!/usr/bin/env python3
"""Конвертирует картинки мастера установки (PNG -> BMP) для Inno Setup.

Ищет ``installer-wizard[@2x].png`` (164x314) и ``installer-small[@2x].png`` (55x58)
в ``design/incoming`` и ``packaging/windows/assets``. BMP кладёт в
``packaging/windows/build`` и печатает готовые аргументы для ISCC, по одному на строку
(ничего не печатает, если картинок нет - установщик тогда собирается без них):

    /DWizardImage=...\\installer-wizard.bmp,...\\installer-wizard@2x.bmp
    /DWizardSmallImage=...\\installer-small.bmp,...\\installer-small@2x.bmp

Прозрачность заливается белым (BMP без альфа-канала), размер приводится к точному
через ImageOps.fit (кроп по центру, без растяжения).
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageOps

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
SEARCH_DIRS = [HERE / "assets", REPO_ROOT / "design" / "incoming"]
OUT_DIR = HERE / "build"

# (имя в ISCC, базовое имя файла, размер 1x)
ASSETS = [
    ("WizardImage", "installer-wizard", (164, 314)),
    ("WizardSmallImage", "installer-small", (55, 58)),
]


def _find(name: str) -> Path | None:
    for directory in SEARCH_DIRS:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def _convert(src: Path, size: tuple[int, int], dst: Path) -> None:
    with Image.open(src) as img:
        img = img.convert("RGBA")
        background = Image.new("RGBA", img.size, (255, 255, 255, 255))
        flat = Image.alpha_composite(background, img).convert("RGB")
    ImageOps.fit(flat, size, Image.LANCZOS).save(dst, "BMP")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for define, stem, (width, height) in ASSETS:
        outputs: list[str] = []
        for suffix, scale in (("", 1), ("@2x", 2)):
            src = _find(f"{stem}{suffix}.png")
            if src is None:
                continue
            dst = OUT_DIR / f"{stem}{suffix}.bmp"
            _convert(src, (width * scale, height * scale), dst)
            outputs.append(str(dst))
            print(f"{src.name} -> {dst.name} ({width * scale}x{height * scale})", file=sys.stderr)
        # Без 1x-версии пропускаем картинку целиком: Inno ждёт список от меньшего к большему.
        if outputs and outputs[0].endswith(f"{stem}.bmp"):
            print(f"/D{define}={','.join(outputs)}")
        elif outputs:
            print(f"предупреждение: {stem}.png (1x) не найден, {define} пропущен", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
