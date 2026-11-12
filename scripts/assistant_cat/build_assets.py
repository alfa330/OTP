"""Кадры котика из Blender → WebP для облика помощника «Котик».

    python build_assets.py <папка с рендерами> [корень репозитория]

Нужны Pillow и cwebp (brew install webp). Папка — ЧИСТОВЫЕ кадры одного
размера (команда в шапке cat_models.py); черновики туда не класть — сборщик
падает, если кадры разного размера или в layout.json не хватает поз.

Все кадры режутся одной квадратной рамкой — объединением по альфе всех поз: при
смене эмоции котик не прыгает и не меняет размер, лапы стоят на одной линии.
Тень запечена в картинку (мягкая снизу и у лап), а не CSS-фильтром: белый котик
читается на белом разделе, а слабому компьютеру не приходится пересчитывать
фильтр на каждом кадре дыхания.

Опорные точки значков эмоций (layout.json рендера) переводятся в проценты кадра
и ложатся в src/components/assistant/cat/catLayout.js.

Спокойная поза ещё и слоями (голова к курсору, 09.11.2026): тело cat-calm-body,
головы cat-head-<столбец>-<строка> под сеткой углов (CAT_HEAD_GRID) и накладки
моргания cat-head-…-blink — только то, чем закрытые глаза отличаются от открытых.
Под головой — своя мягкая тень на грудку и фон: она едет вместе с головой.
"""
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

SRC = Path(sys.argv[1])
ROOT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parents[2]
DST = ROOT / 'src' / 'components' / 'assistant' / 'cat' / 'assets'
LAYOUT_JS = ROOT / 'src' / 'components' / 'assistant' / 'cat' / 'catLayout.js'
TMP = SRC / '_webp_tmp'

FRAMES = ['normal', 'happy', 'excited', 'sad', 'angry', 'surprised', 'scared', 'sleepy', 'confused',
          'mischievous', 'asleep']
SIZE = 256          # сторона кадра: шарик 56 px × 1.3, с запасом на экраны 2–3×
LOGO = 96           # голова для выбора облика и шапки панели (26–34 px)
SHADOW = (58, 40, 36)       # тёплая, как на листе: сине-серая тень холодила котика (разбор 04.11)


def alpha_box(im, threshold=8):
    return im.getchannel('A').point(lambda a: 255 if a > threshold else 0).getbbox()


def head_names(grid):
    """Головы сетки: столбец — поворот слева направо, строка — наклон сверху вниз."""
    return [f'head-{c}-{r}' for r in range(len(grid['pitch'])) for c in range(len(grid['yaw']))]


def webp(im, name, q=84, exact=True):
    """exact — цвет под прозрачным сохраняется; слоям головы он не нужен, без него легче."""
    TMP.mkdir(parents=True, exist_ok=True)
    png = TMP / f'{name}.png'
    im.save(png)
    out = DST / f'{name}.webp'
    subprocess.run(['cwebp', '-quiet', '-q', str(q), '-alpha_q', '100', '-m', '6', '-sharp_yuv']
                   + (['-exact'] if exact else []) + [str(png), '-o', str(out)], check=True)
    print(out.relative_to(ROOT), im.size, out.stat().st_size, 'bytes')
    return out.stat().st_size


def with_shadow(im):
    """Мягкая тень по силуэту (чуть вниз) и пятно под лапами — как на набросках."""
    alpha = im.getchannel('A')
    w, h = im.size
    drop = Image.new('L', im.size, 0)
    drop.paste(alpha, (0, max(1, round(h * 0.012))))
    drop = drop.filter(ImageFilter.GaussianBlur(h * 0.016)).point(lambda a: int(a * 0.2))
    # Пятно под лапами: ширина — по нижней полосе силуэта.
    l, t, r, b = alpha_box(im)
    band = alpha.crop((0, max(t, b - round(h * 0.05)), w, b))
    bl, _, br, _ = band.getbbox() or (l, 0, r, 0)
    rx, ry, cx = (br - bl) * 0.56, h * 0.028, (bl + br) / 2
    # Пятно не шире кадра с запасом на размытие: у «Испуга» вполоборота хвост лежит на полу,
    # полоса у лап широкая, и пятно на 12 % шире неё упиралось в край (04.11.2026).
    blur = h * 0.022
    rx = max(min(rx, cx - 3 * blur, w - 3 * blur - cx), (br - bl) * 0.3)
    ground = Image.new('L', im.size, 0)
    ImageDraw.Draw(ground).ellipse((cx - rx, b - ry * 1.2, cx + rx, b + ry * 0.8), fill=90)
    ground = ground.filter(ImageFilter.GaussianBlur(blur))
    canvas = Image.new('RGBA', im.size, SHADOW + (0,))
    canvas.putalpha(ImageChops.lighter(drop, ground))
    canvas.alpha_composite(im)
    return canvas


def with_head_shadow(im):
    """Тень головы: силуэт чуть ниже и размытый — ложится на грудку под подбородком и на фон.

    В слое тела тени головы нет (верх грудки внутри головы выходил чёрным), поэтому её
    несёт слой головы — и она поворачивается вместе с головой."""
    alpha = im.getchannel('A')
    w, h = im.size
    drop = Image.new('L', im.size, 0)
    drop.paste(alpha, (round(w * 0.004), max(1, round(h * 0.02))))
    drop = drop.filter(ImageFilter.GaussianBlur(h * 0.018)).point(lambda a: int(a * 0.3))
    canvas = Image.new('RGBA', im.size, SHADOW + (0,))
    canvas.putalpha(drop)
    canvas.alpha_composite(im)
    return canvas


def eye_overlay(open_im, blink_im, threshold=18):
    """Накладка моргания: из кадра с закрытыми глазами — только места, где он отличается."""
    r, g, b, a = ImageChops.difference(open_im, blink_im).split()
    diff = ImageChops.lighter(ImageChops.lighter(r, g), ImageChops.lighter(b, a))
    mask = diff.point(lambda v: 255 if v > threshold else 0)
    # Глаза — внутри головы: шумовые расхождения шерсти по краю силуэта в накладку не берём.
    inside = open_im.getchannel('A').point(lambda v: 255 if v > 250 else 0).filter(ImageFilter.MinFilter(9))
    mask = ImageChops.multiply(mask, inside)
    mask = mask.filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.GaussianBlur(1.5))
    out = blink_im.copy()
    out.putalpha(ImageChops.multiply(blink_im.getchannel('A'), mask))
    return out


def edges_clear(im):
    """Края кадра прозрачны: тень и шерсть не срезаны рамкой."""
    alpha = im.getchannel('A')
    w, h = im.size
    strips = (alpha.crop((0, 0, w, 1)), alpha.crop((0, h - 1, w, h)),
              alpha.crop((0, 0, 1, h)), alpha.crop((w - 1, 0, w, h)))
    return all(strip.getextrema()[1] <= 2 for strip in strips)


def main():
    layout = json.loads((SRC / 'layout.json').read_text())
    grid = layout.get('_head_grid')
    if not grid:
        sys.exit('в layout.json нет сетки головы (_head_grid) — отрендерите слои: cat_models.py … calm')
    heads = head_names(grid)
    calm = ['calm-body'] + heads
    blinks = [f'{name}-blink' for name in heads]
    missing = [name for name in FRAMES + calm + blinks + ['portrait'] if not (SRC / f'{name}.png').exists()]
    if missing:
        sys.exit(f'нет кадров: {missing}')
    renders = {name: Image.open(SRC / f'{name}.png').convert('RGBA') for name in FRAMES + calm + blinks}
    sizes = {im.size for im in renders.values()}
    if len(sizes) != 1:
        sys.exit(f'кадры разного размера {sizes} — черновик в папке чистовика?')
    if set(FRAMES) - set(layout):
        sys.exit(f'в layout.json нет поз: {sorted(set(FRAMES) - set(layout))}')
    W, H = sizes.pop()
    boxes = [alpha_box(renders[name]) for name in FRAMES + calm]
    left = min(b[0] for b in boxes)
    top = min(b[1] for b in boxes)
    right = max(b[2] for b in boxes)
    bottom = max(b[3] for b in boxes)
    pad = round(max(right - left, bottom - top) * 0.05)
    floor = round(pad * 2.4)                       # снизу — запас под тень у лап
    side = max(right - left + 2 * pad, bottom - top + pad + floor)
    cx = (left + right) / 2
    x0 = round(cx - side / 2)
    y0 = bottom + floor - side                     # низ общий: лапы на одной линии
    box = (x0, y0, x0 + side, y0 + side)
    print('рамка', box, 'сторона', side, 'запас', pad, 'снизу', floor)
    DST.mkdir(parents=True, exist_ok=True)

    def to_pct(x, y):
        return [round((x * W - x0) / side * 100, 1), round((y * H - y0) / side * 100, 1)]

    def cut(name):
        frame = Image.new('RGBA', (side, side), (0, 0, 0, 0))
        frame.alpha_composite(renders[name].crop(box))
        return frame.resize((SIZE, SIZE), Image.LANCZOS)

    def checked(name, im):
        if not edges_clear(im):
            sys.exit(f'{name}: тень или шерсть упирается в край кадра — поднимите запас')
        return im

    total = 0
    anchors = {}
    for name in FRAMES:
        frame = cut(name)
        fb = alpha_box(frame)
        total += webp(checked(name, with_shadow(frame)), f'cat-{name}')
        points = {key: to_pct(*xy) for key, xy in layout[name].items()}
        points['box'] = [round(v / SIZE * 100, 1) for v in fb]
        anchors[name] = points

    # Спокойная поза слоями: тело с тенью у лап, головы со своей тенью, накладки моргания.
    total += webp(checked('calm-body', with_shadow(cut('calm-body'))), 'cat-calm-body')
    for name in heads:
        frame = cut(name)
        total += webp(checked(name, with_head_shadow(frame)), f'cat-{name}', exact=False)
        total += webp(eye_overlay(frame, cut(f'{name}-blink')), f'cat-{name}-blink', exact=False)

    # Портрет для выбора облика и шапки панели: голова отдельным кадром, без хвоста и лап.
    portrait = Image.open(SRC / 'portrait.png').convert('RGBA')
    l, t, r, b = alpha_box(portrait)
    edge = max(r - l, b - t) + 8
    square = Image.new('RGBA', (edge, edge), (0, 0, 0, 0))
    square.alpha_composite(portrait.crop((l, t, r, b)), ((edge - (r - l)) // 2, (edge - (b - t)) // 2))
    total += webp(square.resize((LOGO, LOGO), Image.LANCZOS), 'cat-logo', q=88)
    print('total', total, 'bytes')

    lines = [
        '/* Собрано scripts/assistant_cat/build_assets.py из рендеров Blender — руками не править.',
        ' * Опорные точки значков эмоций в процентах квадратного кадра (x вправо, y вниз):',
        ' * head — центр головы, top — макушка, forehead — лоб, left/right — бока головы,',
        ' * mouth — рот, box — рамка силуэта [слева, сверху, справа, снизу]. */',
        'export const CAT_LAYOUT = {',
    ]
    for name in FRAMES:
        pts = ', '.join(f"{key}: [{', '.join(str(v) for v in val)}]" for key, val in sorted(anchors[name].items()))
        key = f"'{name}'" if '-' in name else name
        lines.append(f'    {key}: {{ {pts} }},')
    lines.append('};')
    lines += [
        '',
        '/* Сетка головы спокойной позы: углы поворота (+ — к правому краю) и наклона (− — вверх),',
        ' * градусы. Кадр cat-head-<столбец>-<строка>.webp — yaw[столбец], pitch[строка]. */',
        f"export const CAT_HEAD_GRID = {{ yaw: [{', '.join(str(v) for v in grid['yaw'])}], "
        f"pitch: [{', '.join(str(v) for v in grid['pitch'])}] }};",
    ]
    LAYOUT_JS.write_text('\n'.join(lines) + '\n')
    print(LAYOUT_JS.relative_to(ROOT))
    for f in TMP.iterdir():
        f.unlink()
    TMP.rmdir()


if __name__ == '__main__':
    main()
