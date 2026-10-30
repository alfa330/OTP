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


webp(fit(crop(load('pumpkins')), width=440), 'pumpkins')
webp(fit(crop(load('ghost')), height=240), 'ghost')
webp(fit(crop(load('hat')), width=150), 'hat')
webp(fit(crop(load('spider')), width=120), 'spider')

frames = [load(f'bat-{i}') for i in range(6)]
boxes = [f.getbbox() for f in frames]
union = (min(b[0] for b in boxes) - 4, min(b[1] for b in boxes) - 4,
         max(b[2] for b in boxes) + 4, max(b[3] for b in boxes) + 4)
frames = [fit(f.crop(union), width=128) for f in frames]
strip = Image.new('RGBA', (128 * 6, frames[0].height), (0, 0, 0, 0))
for i, f in enumerate(frames):
    strip.alpha_composite(f, (128 * i, 0))
webp(strip, 'bat-strip', q=86)

for f in TMP.iterdir():
    f.unlink()
TMP.rmdir()
