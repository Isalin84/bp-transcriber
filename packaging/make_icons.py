#!/usr/bin/env python3
"""Render BP Transcriber brand SVGs to PNG / .icns / .ico.

Requirements
------------
* Python with Pillow and cairosvg   (pip install pillow cairosvg)
* cairosvg needs the native Cairo library. On macOS: `brew install cairo`
  (the script sets DYLD_FALLBACK_LIBRARY_PATH to /opt/homebrew/lib or /usr/local/lib itself).
* `iconutil` (ships with macOS) for the .icns. On other OSes the .icns step is skipped.

Rasteriser choice: cairosvg (no filters are used in the SVGs, so output matches browsers).
Fallbacks if cairosvg is unavailable: `rsvg-convert` (brew install librsvg) is used automatically.

Usage:  python packaging/make_icons.py
Inputs:  design/brand/app-icon.svg (macOS), design/brand/app-icon-windows.svg (Windows)
Outputs: packaging/icons/bp.icns, packaging/icons/bp.ico, packaging/icons/png/icon-<size>.png
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "design" / "brand"
OUT = ROOT / "packaging" / "icons"
PNG_DIR = OUT / "png"

MAC_SVG = BRAND / "app-icon.svg"
WIN_SVG = BRAND / "app-icon-windows.svg"

ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
PNG_SIZES = [16, 24, 32, 48, 64, 128, 256, 512, 1024]
# (iconset file name, pixel size)
ICONSET = [
    ("icon_16x16.png", 16), ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32), ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256), ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512), ("icon_512x512@2x.png", 1024),
]


def _ensure_cairo_path() -> None:
    """Make Homebrew's libcairo discoverable and re-exec once if cairosvg can't load it."""
    try:
        import cairosvg  # noqa: F401
        return
    except OSError:
        pass
    except ImportError:
        return  # handled later (rsvg fallback)
    if os.environ.get("_BP_ICONS_REEXEC"):
        return
    for lib in ("/opt/homebrew/lib", "/usr/local/lib"):
        if Path(lib, "libcairo.2.dylib").exists():
            env = dict(os.environ, DYLD_FALLBACK_LIBRARY_PATH=lib, _BP_ICONS_REEXEC="1")
            os.execve(sys.executable, [sys.executable, *sys.argv], env)


_ensure_cairo_path()

from PIL import Image, ImageFilter  # noqa: E402

try:
    import cairosvg  # noqa: E402
except (ImportError, OSError):
    cairosvg = None


def render_svg(svg: Path, size: int) -> Image.Image:
    """Rasterise an SVG to an RGBA image of size x size."""
    if cairosvg is not None:
        data = cairosvg.svg2png(url=str(svg), output_width=size, output_height=size)
    elif shutil.which("rsvg-convert"):
        data = subprocess.run(
            ["rsvg-convert", "-w", str(size), "-h", str(size), str(svg)], check=True, capture_output=True
        ).stdout
    else:
        sys.exit("No SVG rasteriser: pip install cairosvg (and brew install cairo) or brew install librsvg")
    return Image.open(io.BytesIO(data)).convert("RGBA")


def add_shadow(img: Image.Image) -> Image.Image:
    """macOS-style soft drop shadow baked into the margin of a 1024-grid icon."""
    size = img.width
    k = size / 1024
    alpha = img.getchannel("A")
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    shadow.putalpha(alpha.point(lambda a: int(a * 0.42)))
    shadow = Image.alpha_composite(Image.new("RGBA", img.size, (0, 0, 0, 0)), shadow)
    offset = Image.new("RGBA", img.size, (0, 0, 0, 0))
    offset.paste(shadow, (0, int(round(12 * k))))
    offset = offset.filter(ImageFilter.GaussianBlur(max(0.0, 20 * k)))
    return Image.alpha_composite(offset, img)


def mac_icon(size: int) -> Image.Image:
    img = render_svg(MAC_SVG, size)
    return add_shadow(img) if size >= 64 else img


def main() -> None:
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    # 1) loose PNGs (macOS look)
    for s in PNG_SIZES:
        mac_icon(s).save(PNG_DIR / f"icon-{s}.png", optimize=True)

    # 2) .icns via iconutil
    if shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as tmp:
            iconset = Path(tmp) / "bp.iconset"
            iconset.mkdir()
            cache: dict[int, Image.Image] = {}
            for name, s in ICONSET:
                cache.setdefault(s, mac_icon(s)).save(iconset / name)
            subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(OUT / "bp.icns")], check=True)
    else:
        print("iconutil not found (not macOS): skipping bp.icns", file=sys.stderr)

    # 3) .ico (Windows variant, each size rendered natively from the SVG)
    frames = [render_svg(WIN_SVG, s) for s in ICO_SIZES]
    frames[-1].save(
        OUT / "bp.ico", format="ICO", sizes=[(s, s) for s in ICO_SIZES], append_images=frames[:-1]
    )

    # sanity: report the sizes actually stored in the ico
    with Image.open(OUT / "bp.ico") as ico:
        print("bp.ico sizes:", sorted(ico.info.get("sizes", [])))
    print("done:", OUT)


if __name__ == "__main__":
    main()
