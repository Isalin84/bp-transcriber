"""Prepare the selected generated raster assets without stretching artwork.

Run with a Python environment containing Pillow and numpy. The original selected
images and exact generation prompts are retained alongside this script.
"""
from pathlib import Path
import json
import shutil

import numpy as np
from PIL import Image, ImageCms, ImageDraw, ImageFont, ImageOps

SOURCE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SOURCE_DIR.parent
RESAMPLE = Image.Resampling.LANCZOS
SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
SELECTION = json.loads((SOURCE_DIR / 'selection.json').read_text())


def original(name):
    local = SOURCE_DIR / f'{name}-source.png'
    if not local.exists():
        shutil.copy2(SELECTION[name], local)
    return Image.open(local)


def save(im, name):
    im.save(OUTPUT_DIR / name, 'PNG', optimize=True, icc_profile=SRGB)


def fit_rgb(name, size, output_name):
    result = ImageOps.fit(original(name).convert('RGB'), size, method=RESAMPLE)
    save(result, output_name)
    return result


def remove_green(im):
    """Extract generated gold artwork, recover antialiased edge colors.

    Estimate the foreground color from neighboring solid gold pixels and solve
    the green-matte blend. Unpremultiplication prevents a green edge fringe.
    """
    rgb = np.asarray(im.convert('RGB'), dtype=np.float32)
    r, g, b = np.moveaxis(rgb, -1, 0)
    solid = (r > g + 4) & (r > 60)
    known = solid.copy()
    foreground = np.where(known[..., None], rgb, 0)
    height, width = known.shape
    for _ in range(8):
        values = np.pad(foreground, ((1, 1), (1, 1), (0, 0)))
        flags = np.pad(known.astype(np.float32), 1)
        sums = np.zeros_like(rgb)
        counts = np.zeros_like(r)
        for dy in range(3):
            for dx in range(3):
                sums += values[dy:dy + height, dx:dx + width]
                counts += flags[dy:dy + height, dx:dx + width]
        new = (~known) & (counts > 0)
        foreground[new] = sums[new] / counts[new, None]
        known |= new
    key = np.array([0., 255., 0.], dtype=np.float32)
    direction = foreground - key
    alpha = np.sum((rgb - key) * direction, axis=-1) / np.maximum(
        np.sum(direction * direction, axis=-1), 1)
    alpha = np.clip(alpha, 0, 1)
    alpha[solid] = 1
    alpha[(~known) | (np.maximum(r, b) < 8) | (alpha < .015)] = 0
    recovered = (rgb - key * (1 - alpha[..., None])) / np.maximum(
        alpha[..., None], .001)
    edge = (alpha > 0) & (alpha < .99)
    recovered[..., 1][edge] = np.minimum(
        recovered[..., 1][edge], recovered[..., 0][edge] * .97)
    recovered[alpha == 0] = 0
    rgba = np.concatenate((np.clip(recovered, 0, 255),
                           alpha[..., None] * 255), axis=-1)
    return Image.fromarray(np.rint(rgba).astype(np.uint8))


def transparent_illustration(name, canvas_size, safe_size):
    im = remove_green(original(name))
    artwork = im.crop(im.getchannel('A').getbbox())
    artwork = ImageOps.contain(artwork, safe_size, method=RESAMPLE)
    canvas = Image.new('RGBA', canvas_size, (0, 0, 0, 0))
    position = tuple((a - b) // 2 for a, b in zip(canvas_size, artwork.size))
    canvas.alpha_composite(artwork, position)
    save(canvas, name + '.png')


# Alpha cutoff removes stray near-transparent speckles in the generated margin.
icon = original('app-icon-1024').convert('RGBA')
alpha = icon.getchannel('A').point(lambda value: value if value > 8 else 0)
icon.putalpha(alpha)
icon = ImageOps.fit(icon.crop(alpha.getbbox()), (824, 824), method=RESAMPLE)
icon_canvas = Image.new('RGBA', (1024, 1024), (0, 0, 0, 0))
icon_canvas.alpha_composite(icon, ((1024 - icon.width) // 2,
                                 (1024 - icon.height) // 2))
save(icon_canvas, 'app-icon-1024.png')

for name, large, small in [('dmg-background', (1320, 800), (660, 400)),
                           ('installer-wizard', (328, 628), (164, 314))]:
    high = fit_rgb(name, large, name + '@2x.png')
    save(high.resize(small, RESAMPLE), name + '.png')

transparent_illustration('empty-state', (800, 600), (640, 480))
transparent_illustration('about-illustration', (1200, 800), (1000, 640))
fit_rgb('readme-hero', (1600, 600), 'readme-hero.png')
fit_rgb('github-social-preview', (1280, 640), 'github-social-preview.png')

# Crop the navy tile out of its generated white field, then add exact white padding.
small_source = original('installer-small').convert('RGB')
small_pixels = np.asarray(small_source)
navy = (small_pixels[..., 2].astype(float) >
        small_pixels[..., 0].astype(float) * 1.3) & (small_pixels[..., 0] < 100)
yy, xx = np.where(navy)
tile = small_source.crop((int(xx.min()), int(yy.min()),
                         int(xx.max()) + 1, int(yy.max()) + 1))
tile = ImageOps.contain(tile, (98, 104), method=RESAMPLE)
small_canvas = Image.new('RGB', (110, 116), '#FFFFFF')
small_canvas.paste(tile, ((110 - tile.width) // 2, (116 - tile.height) // 2))
save(small_canvas, 'installer-small@2x.png')
save(small_canvas.resize((55, 58), RESAMPLE), 'installer-small.png')

expected = [
    ('app-icon-1024.png', (1024, 1024), 'RGBA'),
    ('dmg-background.png', (660, 400), 'RGB'),
    ('dmg-background@2x.png', (1320, 800), 'RGB'),
    ('empty-state.png', (800, 600), 'RGBA'),
    ('readme-hero.png', (1600, 600), 'RGB'),
    ('github-social-preview.png', (1280, 640), 'RGB'),
    ('installer-wizard.png', (164, 314), 'RGB'),
    ('installer-wizard@2x.png', (328, 628), 'RGB'),
    ('installer-small.png', (55, 58), 'RGB'),
    ('installer-small@2x.png', (110, 116), 'RGB'),
    ('about-illustration.png', (1200, 800), 'RGBA'),
]
report = []
for name, size, mode in expected:
    path = OUTPUT_DIR / name
    with Image.open(path) as im:
        assert im.size == size, (name, im.size)
        assert im.mode == mode, (name, im.mode)
        alpha_range = im.getchannel('A').getextrema() if mode == 'RGBA' else None
        if mode == 'RGBA':
            assert alpha_range == (0, 255), (name, alpha_range)
        report.append(dict(file=name, size=list(size), mode=mode,
                           bytes=path.stat().st_size, alpha_range=alpha_range))

for name, margins in [('app-icon-1024.png', (100, 100)),
                      ('empty-state.png', (80, 60)),
                      ('about-illustration.png', (100, 80))]:
    im = Image.open(OUTPUT_DIR / name)
    a = im.getchannel('A')
    mx, my = margins
    for box in [(0, 0, mx, im.height), (im.width-mx, 0, im.width, im.height),
                (0, 0, im.width, my), (0, im.height-my, im.width, im.height)]:
        assert a.crop(box).getextrema() == (0, 0), (name, box)

assert (OUTPUT_DIR / 'github-social-preview.png').stat().st_size < 1_000_000
for stem in ['dmg-background', 'installer-wizard', 'installer-small']:
    lo = Image.open(OUTPUT_DIR / f'{stem}.png')
    hi = Image.open(OUTPUT_DIR / f'{stem}@2x.png')
    assert np.array_equal(np.asarray(lo), np.asarray(hi.resize(lo.size, RESAMPLE)))

(OUTPUT_DIR / 'validation.json').write_text(
    json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(report, ensure_ascii=False, indent=2))

# Review canvas: show transparent artwork on both themes; not a production asset.
preview = Image.new('RGB', (1600, 1610), '#FAF9F6')
draw = ImageDraw.Draw(preview)
font = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf', 22)


def panel(name, box, background, label, contain=True):
    x, y, w, h = box
    draw.rounded_rectangle((x, y, x+w, y+h), 16, fill=background)
    im = Image.open(OUTPUT_DIR / name).convert('RGBA')
    im = ImageOps.contain(im, (w-40, h-65), RESAMPLE)
    canvas = Image.new('RGBA', im.size, background)
    canvas.alpha_composite(im)
    preview.paste(canvas.convert('RGB'), (x+(w-im.width)//2, y+42+(h-65-im.height)//2))
    draw.text((x+18, y+12), label, fill='#E8D48B' if background=='#0B1D3A' else '#0B1D3A', font=font)


panel('readme-hero.png', (20, 20, 1560, 440), '#0B1D3A', 'README hero')
panel('github-social-preview.png', (20, 480, 990, 530), '#0B1D3A', 'GitHub social preview')
panel('app-icon-1024.png', (1030, 480, 350, 360), '#FAF9F6', 'App icon')
panel('installer-wizard@2x.png', (1400, 480, 180, 530), '#0B1D3A', 'Wizard')
panel('installer-small.png', (1030, 860, 350, 150), '#FAF9F6', 'Small installer icon / 55x58')
panel('dmg-background.png', (20, 1030, 650, 560), '#0B1D3A', 'DMG installer background')
panel('empty-state.png', (690, 1030, 435, 260), '#FAF9F6', 'Empty state / light')
panel('empty-state.png', (1145, 1030, 435, 260), '#0B1D3A', 'Empty state / dark')
panel('about-illustration.png', (690, 1310, 435, 280), '#FAF9F6', 'About / light')
panel('about-illustration.png', (1145, 1310, 435, 280), '#0B1D3A', 'About / dark')
preview.save(OUTPUT_DIR / 'assets-preview.jpg', quality=92)
