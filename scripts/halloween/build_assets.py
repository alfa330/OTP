"""Кадры из Blender -> WebP для слоя украшений (обрезка по альфе, общий размер, cwebp)."""
import subprocess
import sys
from pathlib import Path

from PIL import Image

SRC = Path(sys.argv[1])
DST = Path(sys.argv[2])
TMP = DST / '_tmp'
TMP.mkdir(parents=True, exist_ok=True)


def crop(im, pad=4):
    l, t, r, b = im.getbbox()
    return im.crop((max(0, l - pad), max(0, t - pad), min(im.width, r + pad), min(im.height, b + pad)))


def fit(im, width=None, height=None):
    if width:
        return im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    return im.resize((round(im.width * height / im.height), height), Image.LANCZOS)


def webp(im, name, q=84):
    png = TMP / f'{name}.png'
    im.save(png)
    out = DST / f'{name}.webp'
    subprocess.run(['cwebp', '-quiet', '-q', str(q), '-alpha_q', '100', '-m', '6', '-sharp_yuv', '-exact',
                    str(png), '-o', str(out)], check=True)
    print(f'{name}.webp', im.size, out.stat().st_size, 'bytes')


def load(name):
    return Image.open(SRC / f'{name}.png').convert('RGBA')


def union_box(images, pad=4):
    boxes = [im.getbbox() for im in images]
    return (min(b[0] for b in boxes) - pad, min(b[1] for b in boxes) - pad,
            max(b[2] for b in boxes) + pad, max(b[3] for b in boxes) + pad)


if (SRC / 'pumpkins.png').exists():
    webp(fit(crop(load('pumpkins')), width=440), 'pumpkins')
    webp(fit(crop(load('ghost')), height=240), 'ghost')
    webp(fit(crop(load('hat')), width=150), 'hat')
    webp(fit(crop(load('spider')), width=120), 'spider')

    frames = [load(f'bat-{i}') for i in range(6)]
    union = union_box(frames)
    frames = [fit(f.crop(union), width=128) for f in frames]
    strip = Image.new('RGBA', (128 * 6, frames[0].height), (0, 0, 0, 0))
    for i, f in enumerate(frames):
        strip.alpha_composite(f, (128 * i, 0))
    webp(strip, 'bat-strip', q=86)

# Тыква-талисман: тело и лица режутся ОДНОЙ рамкой — лицо ложится на тело пиксель в пиксель.
EMOTIONS = ['normal', 'joy', 'anger', 'sad', 'surprise', 'wink', 'sleep', 'confusion',
            'love', 'fear', 'cool', 'neutral']
if (SRC / 'mascot-body.png').exists():
    body, logo = load('mascot-body'), load('mascot-logo')
    box = union_box([body, logo])
    webp(fit(body.crop(box), width=216), 'mascot-body', q=86)
    webp(fit(logo.crop(box), width=216), 'mascot-logo', q=86)
    for emotion in EMOTIONS:
        webp(fit(load(f'mascot-face-{emotion}').crop(box), width=216), f'mascot-face-{emotion}', q=88)
    # Значок вкладки: тыква с лицом в квадрате 64×64.
    icon = crop(logo, pad=2)
    icon.thumbnail((62, 62), Image.LANCZOS)
    square = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    square.alpha_composite(icon, ((64 - icon.width) // 2, (64 - icon.height) // 2))
    square.save(DST / 'pumpkin-favicon.png', optimize=True)
    print('pumpkin-favicon.png', square.size, (DST / 'pumpkin-favicon.png').stat().st_size, 'bytes')

for f in TMP.iterdir():
    f.unlink()
TMP.rmdir()
