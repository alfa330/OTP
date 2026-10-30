"""Кадры из Blender -> WebP: украшения Хэллоуина и тыква-помощник.

    python build_assets.py <папка с рендерами> [корень репозитория]

Украшения ложатся в src/components/seasonal/halloween/assets, тыква — в
src/components/assistant/pumpkin/assets (она остаётся обликом помощника и после
праздника). Всё режется по альфе; кадры мыши и слои тыквы — общей рамкой, чтобы
ничего не прыгало между кадрами.

Свечение лица тыквы «запекается» в саму картинку: на слабом компьютере два CSS
drop-shadow на мерцающем лице пересчитывались бы на каждом кадре.
"""
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageFilter

SRC = Path(sys.argv[1])
ROOT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parents[2]
DECOR = ROOT / 'src' / 'components' / 'seasonal' / 'halloween' / 'assets'
PUMPKIN = ROOT / 'src' / 'components' / 'assistant' / 'pumpkin' / 'assets'
TMP = SRC / '_webp_tmp'
TMP.mkdir(parents=True, exist_ok=True)

EMOTIONS = ['normal', 'joy', 'anger', 'sad', 'surprise', 'wink', 'sleep', 'confusion',
            'love', 'fear', 'cool', 'neutral']


def crop(im, pad=4):
    l, t, r, b = im.getbbox()
    return im.crop((max(0, l - pad), max(0, t - pad), min(im.width, r + pad), min(im.height, b + pad)))


def fit(im, width=None, height=None):
    if width:
        return im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    return im.resize((round(im.width * height / im.height), height), Image.LANCZOS)


def union_box(images, pad=4):
    boxes = [im.getbbox() for im in images]
    return (min(b[0] for b in boxes) - pad, min(b[1] for b in boxes) - pad,
            max(b[2] for b in boxes) + pad, max(b[3] for b in boxes) + pad)


def webp(im, dst, name, q=84):
    dst.mkdir(parents=True, exist_ok=True)
    png = TMP / f'{name}.png'
    im.save(png)
    out = dst / f'{name}.webp'
    subprocess.run(['cwebp', '-quiet', '-q', str(q), '-alpha_q', '100', '-m', '6', '-sharp_yuv', '-exact',
                    str(png), '-o', str(out)], check=True)
    print(out.relative_to(ROOT), im.size, out.stat().st_size, 'bytes')


def load(name):
    return Image.open(SRC / f'{name}.png').convert('RGBA')


def glowing(face):
    """Тёплый отсвет вокруг прорезей — две мягкие каймы под самим лицом."""
    alpha = face.getchannel('A')
    out = Image.new('RGBA', face.size, (0, 0, 0, 0))
    for radius, strength, color in ((14, 0.55, (255, 104, 20)), (5, 1.0, (255, 150, 42))):
        halo = alpha.filter(ImageFilter.GaussianBlur(radius)).point(lambda a, s=strength: min(255, int(a * s * 1.7)))
        layer = Image.new('RGBA', face.size, color + (0,))
        layer.putalpha(halo)
        out.alpha_composite(layer)
    out.alpha_composite(face)
    return out


if (SRC / 'pumpkins.png').exists():
    webp(fit(crop(load('pumpkins')), width=440), DECOR, 'pumpkins')
    webp(fit(crop(load('ghost')), height=240), DECOR, 'ghost')
    webp(fit(crop(load('hat')), width=150), DECOR, 'hat')
    webp(fit(crop(load('spider')), width=120), DECOR, 'spider')

    frames = [load(f'bat-{i}') for i in range(6)]
    union = union_box(frames)
    frames = [fit(f.crop(union), width=128) for f in frames]
    strip = Image.new('RGBA', (128 * 6, frames[0].height), (0, 0, 0, 0))
    for i, f in enumerate(frames):
        strip.alpha_composite(f, (128 * i, 0))
    webp(strip, DECOR, 'bat-strip', q=86)

if (SRC / 'mascot-body.png').exists():
    body = load('mascot-body')
    faces = {emotion: load(f'mascot-face-{emotion}') for emotion in EMOTIONS}
    box = union_box([body] + list(faces.values()), pad=14)    # запас под отсвет
    body_small = fit(body.crop(box), width=216)
    webp(body_small, PUMPKIN, 'mascot-body', q=86)
    for emotion, face in faces.items():
        webp(glowing(fit(face.crop(box), width=216)), PUMPKIN, f'mascot-face-{emotion}', q=88)
    # Логотип — то же тело с обычным лицом: в сайдбаре, на входе и в выборе облика
    # тыква выглядит так же, как в шарике.
    logo = body_small.copy()
    logo.alpha_composite(glowing(fit(faces['normal'].crop(box), width=216)))
    webp(logo, PUMPKIN, 'mascot-logo', q=86)
    # Значок вкладки: тыква с лицом в квадрате 64×64.
    icon = crop(logo, pad=1)
    icon.thumbnail((62, 62), Image.LANCZOS)
    square = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    square.alpha_composite(icon, ((64 - icon.width) // 2, (64 - icon.height) // 2))
    square.save(DECOR / 'pumpkin-favicon.png', optimize=True)
    print((DECOR / 'pumpkin-favicon.png').relative_to(ROOT), square.size)

for f in TMP.iterdir():
    f.unlink()
TMP.rmdir()
