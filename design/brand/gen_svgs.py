#!/usr/bin/env python3
"""Generate BP Transcriber brand SVGs (logo-mark, logo-mark-mono, app-icon, app-icon-windows).

Geometry is computed once here and emitted as plain, hand-readable SVG (no filters,
no rasters, no fonts) so any renderer (cairosvg, rsvg, browsers) draws it identically.
Run:  python design/brand/gen_svgs.py
"""
import math
from pathlib import Path

OUT = Path(__file__).resolve().parent

NAVY, GOLD, STEEL, LSTEEL = "#0B1D3A", "#D4AF37", "#1E3A5F", "#2A4F7A"
SOFT, MEDGOLD = "#E8D48B", "#C4A032"


def f(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def pt(r, deg):
    a = math.radians(deg - 90)  # 0 deg = up
    return 128 + r * math.cos(a), 128 + r * math.sin(a)


def gear_path(teeth=10, r_out=90, r_root=75, r_hole=54, tip_deg=7.0, base_deg=11.0):
    """Gear around (128,128) in a 256 box: trapezoid teeth, root arcs, evenodd hole."""
    pitch = 360 / teeth
    d = []
    for i in range(teeth):
        c = i * pitch
        p = [pt(r_root, c - base_deg), pt(r_out, c - tip_deg), pt(r_out, c + tip_deg), pt(r_root, c + base_deg)]
        d.append(("M" if i == 0 else "L") + f"{f(p[0][0])} {f(p[0][1])}")
        d.append(f"L{f(p[1][0])} {f(p[1][1])}")
        d.append(f"A{r_out} {r_out} 0 0 1 {f(p[2][0])} {f(p[2][1])}")
        d.append(f"L{f(p[3][0])} {f(p[3][1])}")
        n = pt(r_root, (i + 1) * pitch - base_deg)
        d.append(f"A{r_root} {r_root} 0 0 1 {f(n[0])} {f(n[1])}")
    d.append("Z")
    # hole (counter-direction circle)
    d.append(f"M{128 - r_hole} 128a{r_hole} {r_hole} 0 1 0 {2 * r_hole} 0a{r_hole} {r_hole} 0 1 0 {-2 * r_hole} 0Z")
    return "".join(d)


def wave_bars(n=4, pitch=10.5, width=7, hmax=68):
    """2n+1 bars whose heights follow the lemniscate envelope y = x*sqrt(1-x^2):
    a waveform whose outline traces an infinity sign (two swelling lobes, pinched centre)."""
    out = []
    for k in range(-n, n + 1):
        x = 0.95 * abs(k) / n
        h = hmax * 2 * x * math.sqrt(1 - x * x) if k else 0
        h = max(width, h)
        out.append((128 + k * pitch - width / 2, 128 - h / 2, width, h))
    return out


def bars_svg(fill):
    return "".join(
        f'<rect x="{f(x)}" y="{f(y)}" width="{f(w)}" height="{f(h)}" rx="{f(w / 2)}"/>' for x, y, w, h in wave_bars()
    )


GEAR = gear_path()


HOLE_R = 54


def mark_core(fill_ref, hole=False):
    """gear + waveform, 256 box, filled with fill_ref (color or url()).
    hole=True paints a deep-navy disc behind the waveform (for coloured backgrounds)."""
    hole_disc = (
        f'<circle cx="128" cy="128" r="{HOLE_R}" fill="url(#hole)"/>'
        if hole else ""
    )
    return hole_disc + (
        f'<path fill="{fill_ref}" stroke="{fill_ref}" stroke-width="4" stroke-linejoin="round" '
        f'fill-rule="evenodd" d="{GEAR}"/>'
        f'<g fill="{fill_ref}">{bars_svg(fill_ref)}</g>'
    )


def gold_grad(gid, x1="0", y1="40", x2="0", y2="216"):
    return (
        f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}">'
        f'<stop offset="0" stop-color="{SOFT}"/><stop offset=".5" stop-color="{GOLD}"/>'
        f'<stop offset="1" stop-color="{MEDGOLD}"/></linearGradient>'
    )


def logo_mark():
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">
<title>BP Transcriber mark</title>
<defs>
<radialGradient id="bg" cx="50%" cy="38%" r="75%"><stop offset="0" stop-color="{LSTEEL}"/><stop offset=".55" stop-color="{STEEL}"/><stop offset="1" stop-color="{NAVY}"/></radialGradient>
<radialGradient id="gl" cx="50%" cy="50%" r="50%"><stop offset="0" stop-color="{GOLD}" stop-opacity=".38"/><stop offset="1" stop-color="{GOLD}" stop-opacity="0"/></radialGradient>
<radialGradient id="hole" cx="50%" cy="40%" r="70%"><stop offset="0" stop-color="#16315a"/><stop offset="1" stop-color="#0B1D3A"/></radialGradient>
{gold_grad("g")}
</defs>
<circle cx="128" cy="128" r="128" fill="url(#bg)"/>
<circle cx="128" cy="128" r="116" fill="none" stroke="url(#g)" stroke-width="7"/>
<circle cx="128" cy="128" r="106" fill="url(#gl)"/>
{mark_core("url(#g)", hole=True)}
</svg>
'''


def logo_mark_mono():
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">
<title>BP Transcriber mark (mono gold)</title>
<circle cx="128" cy="128" r="124.5" fill="none" stroke="{GOLD}" stroke-width="7"/>
{mark_core(GOLD)}
</svg>
'''


def superellipse(cx, cy, a, n=5.0, steps=160):
    pts = []
    for i in range(steps):
        t = 2 * math.pi * i / steps
        c, s = math.cos(t), math.sin(t)
        x = cx + a * math.copysign(abs(c) ** (2 / n), c)
        y = cy + a * math.copysign(abs(s) ** (2 / n), s)
        pts.append(f"{f(x)} {f(y)}")
    return "M" + "L".join(pts) + "Z"


def app_icon(size_a, scale, name, corner_n):
    """size_a = half width of the squircle in a 1024 canvas."""
    sq = superellipse(512, 512, size_a, corner_n)
    # concentric rings + hexagon nodes live in 1024 space
    rings = "".join(
        f'<circle cx="512" cy="512" r="{r}" fill="none" stroke="{GOLD}" stroke-opacity="{o}" stroke-width="{w}"/>'
        for r, o, w in ((330, .30, 3), (400, .18, 2.5), (470, .12, 2), (540, .08, 2))
    )
    hex_pts = [(512 + 330 * math.cos(math.radians(60 * i - 90)), 512 + 330 * math.sin(math.radians(60 * i - 90))) for i in range(6)]
    hexline = "M" + "L".join(f"{f(x)} {f(y)}" for x, y in hex_pts) + "Z"
    nodes = "".join(f'<circle cx="{f(x)}" cy="{f(y)}" r="9" fill="{SOFT}" fill-opacity=".55"/>' for x, y in hex_pts)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">
<title>{name}</title>
<defs>
<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{LSTEEL}"/><stop offset=".4" stop-color="{STEEL}"/><stop offset="1" stop-color="{NAVY}"/></linearGradient>
<radialGradient id="gl" cx="512" cy="512" r="440" gradientUnits="userSpaceOnUse"><stop offset=".5" stop-color="{GOLD}" stop-opacity=".30"/><stop offset=".66" stop-color="{GOLD}" stop-opacity=".16"/><stop offset=".85" stop-color="{GOLD}" stop-opacity=".03"/><stop offset="1" stop-color="{GOLD}" stop-opacity="0"/></radialGradient>
<linearGradient id="sheen" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".16"/><stop offset=".35" stop-color="#fff" stop-opacity="0"/></linearGradient>
<linearGradient id="rim" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".38"/><stop offset=".3" stop-color="#fff" stop-opacity=".04"/><stop offset=".75" stop-color="#fff" stop-opacity="0"/><stop offset="1" stop-color="{GOLD}" stop-opacity=".25"/></linearGradient>
<linearGradient id="vig" x1="0" y1="0" x2="0" y2="1"><stop offset=".6" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".28"/></linearGradient>
<radialGradient id="hole" cx="50%" cy="40%" r="70%"><stop offset="0" stop-color="#16315a"/><stop offset="1" stop-color="#0B1D3A"/></radialGradient>
{gold_grad("g", "0", "40", "0", "216")}
<clipPath id="sq"><path d="{sq}"/></clipPath>
</defs>
<path d="{sq}" fill="url(#bg)"/>
<g clip-path="url(#sq)">
<circle cx="512" cy="512" r="440" fill="url(#gl)"/>
{rings}
<path d="{hexline}" fill="none" stroke="{GOLD}" stroke-opacity=".16" stroke-width="2.5" stroke-linejoin="round"/>
{nodes}
<rect width="1024" height="1024" fill="url(#vig)"/>
<rect width="1024" height="1024" fill="url(#sheen)"/>
</g>
<path d="{sq}" fill="none" stroke="url(#rim)" stroke-width="3"/>
<g transform="translate(512 512) scale({scale}) translate(-128 -128)">
<g fill="#050e1f" fill-rule="evenodd">
<path fill-opacity=".10" transform="translate(0 7)" d="{GEAR}"/>
<path fill-opacity=".14" transform="translate(0 4)" d="{GEAR}"/>
<path fill-opacity=".20" transform="translate(0 2)" d="{GEAR}"/>
</g>
{mark_core("url(#g)", hole=True)}
<circle cx="128" cy="128" r="64.5" fill="none" stroke="{NAVY}" stroke-opacity=".22" stroke-width="1.4"/>
</g>
</svg>
'''


if __name__ == "__main__":
    (OUT / "logo-mark.svg").write_text(logo_mark())
    (OUT / "logo-mark-mono.svg").write_text(logo_mark_mono())
    (OUT / "app-icon.svg").write_text(app_icon(412, 2.95, "BP Transcriber app icon", 5.0))
    (OUT / "app-icon-windows.svg").write_text(app_icon(488, 3.5, "BP Transcriber app icon (Windows)", 5.0))
    print("ok")
