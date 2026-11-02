"""Облик помощника «Котик»: вислоухий котёнок по наброскам владельца (02.11.2026).

Лист «эмоции» — девять поз (Happy, Excited, Sad, Angry, Surprised, Scared,
Sleepy, Confused, Mischievous) и отдельный набросок спящего клубочком котёнка.
Скрипт строит котёнка в Blender фоном, без окна, и рендерит каждую позу в PNG
с прозрачным фоном (Cycles, на Mac — Metal). Затем build_assets.py режет кадры
общей рамкой и сжимает в WebP для src/components/assistant/cat/assets.

    B=/Applications/Blender.app/Contents/MacOS/Blender
    CAT_SAMPLES=128 CAT_SIZE=1100 $B -b --factory-startup -P cat_models.py -- out    # ~6 мин на M4 Pro
    CAT_FUR=0 CAT_ZOOM=1.6 CAT_SIZE=640 $B ... -- out happy sad   # черновик: без шерсти, крупно, две позы
    python build_assets.py out

Кадры: спокойная поза (normal, голова к курсору look-left/right, моргание
blink), девять эмоций листа, спящий клубочек (asleep) и портрет (portrait —
только голова, для выбора облика и шапки панели).

Как устроен котёнок:
  * тело — метаболы (голова со щёчками, туловище, лапы, хвост цепочкой шаров):
    части сливаются мягко, как у плюшевой игрушки, а поза — это просто другие
    координаты частей, без скелета;
  * окрас (белая грудка и мордочка, серебристые полосы табби, кольца хвоста,
    румянец, подушечки) считается для каждой вершины в системе координат той
    части, к которой вершина ближе по полю метабола, — полосы едут вместе с
    лапой или головой;
  * шерсть — кривые (Curves) из numpy: корни по площади треугольников, длина и
    направление «причёсывания» — из тех же частей, цвет — от кожи под корнем;
    вокруг глаз, носа и рта шерсти нет;
  * мордочка — отдельные объекты, спроецированные на поверхность головы лучом
    изнутри: глаза-линзы с радужкой из атрибута eye_uv, веки — обрезкой
    контура, закрытые глаза, рот, нос и усы — трубками и пластинками.

Значки эмоций (сердечки, «!!», «?», «Zzz», пар, дрожь) рисует сайт в SVG —
так они анимируются и не стоят картинок; скрипт отдаёт для них опорные точки
в layout.json (где голова, лоб, мордочка в кадре).
"""
import json
import math
import os
import sys

import bmesh
import bpy
import numpy as np
from mathutils import Euler, Matrix, Quaternion, Vector
from mathutils.bvhtree import BVHTree

ARGV = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
OUTDIR = ARGV[0] if ARGV else '/tmp/cat'
ONLY = ARGV[1:]
SAMPLES = int(os.environ.get('CAT_SAMPLES', '128'))
SIZE = int(os.environ.get('CAT_SIZE', '800'))
FUR = float(os.environ.get('CAT_FUR', '1'))          # густота шерсти; 0 — черновик без шерсти
MB_RES = float(os.environ.get('CAT_MB_RES', '0.014'))
ZOOM = float(os.environ.get('CAT_ZOOM', '1'))          # крупнее для черновиков
FUR_PER_UNIT2 = 20000                                 # волосков на единицу площади при FUR=1

# Поле метабола Blender: s·(1 − d²/R²)³, поверхность там, где сумма = 0.6. У
# одиночного шара с жёсткостью 2 поверхность на 0.575·R, поэтому R = видимый / 0.575.
MB_THRESHOLD = 0.6
MB_VISIBLE = 0.575


# ---------------------------------------------------------------- сцена

def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    try:
        scene.render.engine = 'CYCLES'
    except TypeError as exc:
        print('engine error', exc)
    prefs = bpy.context.preferences.addons['cycles'].preferences
    try:
        prefs.compute_device_type = 'METAL'
        prefs.get_devices()
        for dev in prefs.devices:
            dev.use = dev.type == 'METAL'
        scene.cycles.device = 'GPU'
    except Exception as exc:  # noqa: BLE001 — без GPU рендерим процессором
        print('gpu error', exc)
    scene.cycles.samples = SAMPLES
    scene.cycles.use_denoising = True
    scene.cycles.max_bounces = 10
    scene.cycles.transparent_max_bounces = 24
    try:
        scene.cycles_curves.shape = 'THICK'
    except (AttributeError, TypeError) as exc:
        print('curves shape', exc)
    scene.render.film_transparent = True
    scene.render.resolution_x = SIZE
    scene.render.resolution_y = SIZE
    scene.render.resolution_percentage = 100
    for vt in ('Standard', 'AgX'):
        try:
            scene.view_settings.view_transform = vt
            break
        except TypeError:
            continue
    try:
        scene.view_settings.look = 'None'
    except TypeError:
        pass
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'
    scene.render.image_settings.color_depth = '8'

    world = bpy.data.worlds.new('World')
    scene.world = world
    world.use_nodes = True
    bg = next(n for n in world.node_tree.nodes if n.type == 'BACKGROUND')
    bg.inputs['Color'].default_value = (0.93, 0.92, 0.95, 1.0)
    bg.inputs['Strength'].default_value = 0.65
    return scene


def add_area_light(name, location, target, size, energy, color=(1, 1, 1)):
    data = bpy.data.lights.new(name, 'AREA')
    data.size = size
    data.energy = energy
    data.color = color
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    direction = Vector(target) - Vector(location)
    obj.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
    return obj


def studio_lights():
    # Мягкий «плюшевый» свет набросков: большой тёплый ключ сверху слева, холодный
    # заполняющий справа, контровой сзади — по нему читается пушистый край.
    add_area_light('Key', (-4.5, -6.5, 6.5), (0, 0, 1.0), 6.0, 640, (1.0, 0.97, 0.93))
    add_area_light('Fill', (6.0, -5.0, 2.2), (0, 0, 1.0), 6.0, 240, (0.92, 0.95, 1.0))
    add_area_light('Rim', (1.5, 6.0, 5.0), (0, 0, 1.0), 4.0, 420, (1.0, 1.0, 1.0))
    add_area_light('Low', (0.0, -6.0, -1.5), (0, 0, 0.8), 6.0, 120, (1.0, 0.98, 0.96))


def look_camera(location, target, lens=110):
    data = bpy.data.cameras.new('Cam')
    data.lens = lens
    obj = bpy.data.objects.new('Cam', data)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    direction = Vector(target) - Vector(location)
    obj.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
    bpy.context.scene.camera = obj
    return obj


def render_to(name):
    scene = bpy.context.scene
    scene.render.filepath = f'{OUTDIR}/{name}.png'
    bpy.ops.render.render(write_still=True)
    print('RENDERED', name, flush=True)


# ---------------------------------------------------------------- цвет и материалы

def lin(hex_color):
    hex_color = hex_color.lstrip('#')
    vals = [int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return np.array([v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in vals])


WHITE = lin('#eeecea')
GREY = lin('#c4c7cd')
GREY_MID = lin('#a8abb3')
STRIPE = lin('#575a62')
STRIPE_DARK = lin('#46494f')
BLUSH = lin('#f4a3a8')
PAD_PINK = lin('#f39aa4')
EAR_INNER = lin('#efc3c6')


def _nodes(mat):
    mat.use_nodes = True
    return mat.node_tree.nodes, mat.node_tree.links


def sock(sockets, name, kind=None):
    """Гнездо по имени и типу: у узла Mix одноимённые входы A/B для чисел, векторов и цвета."""
    for s in sockets:
        if s.name == name and (kind is None or s.type == kind):
            return s
    raise KeyError(name)


def principled(name, color, roughness=0.5, **inputs):
    mat = bpy.data.materials.new(name)
    nodes, _ = _nodes(mat)
    node = next(n for n in nodes if n.type == 'BSDF_PRINCIPLED')
    node.inputs['Base Color'].default_value = (*color, 1.0)
    node.inputs['Roughness'].default_value = roughness
    for key, value in inputs.items():
        socket = node.inputs[key.replace('_', ' ')]
        if isinstance(value, np.ndarray):
            value = tuple(float(x) for x in value)
        if isinstance(value, tuple) and len(value) == 3 and socket.type == 'RGBA':
            value = (*value, 1.0)
        socket.default_value = value
    return mat


def emission_material(name, color, strength=1.0):
    mat = bpy.data.materials.new(name)
    nodes, links = _nodes(mat)
    for n in list(nodes):
        nodes.remove(n)
    out = nodes.new('ShaderNodeOutputMaterial')
    emi = nodes.new('ShaderNodeEmission')
    emi.inputs['Color'].default_value = (*color, 1.0)
    emi.inputs['Strength'].default_value = strength
    links.new(emi.outputs['Emission'], out.inputs['Surface'])
    return mat


def skin_material():
    """Кожа под шерстью: цвет вершин чуть темнее — просвет между волосками даёт глубину."""
    mat = principled('Skin', (1, 1, 1), 0.85, Sheen_Weight=0.4, Sheen_Roughness=0.4)
    nodes, links = _nodes(mat)
    bsdf = next(n for n in nodes if n.type == 'BSDF_PRINCIPLED')
    attr = nodes.new('ShaderNodeAttribute')
    attr.attribute_name = 'col'
    shade = nodes.new('ShaderNodeMix')
    shade.data_type = 'RGBA'
    shade.blend_type = 'MULTIPLY'
    sock(shade.inputs, 'Factor', 'VALUE').default_value = 1.0
    links.new(attr.outputs['Color'], sock(shade.inputs, 'A', 'RGBA'))
    sock(shade.inputs, 'B', 'RGBA').default_value = (0.9, 0.9, 0.9, 1.0)
    links.new(sock(shade.outputs, 'Result', 'RGBA'), bsdf.inputs['Base Color'])
    return mat


def fur_material():
    """Шерсть: Principled Hair с прямым цветом от кожи под корнем, кончики светлее."""
    mat = bpy.data.materials.new('Fur')
    nodes, links = _nodes(mat)
    for n in list(nodes):
        nodes.remove(n)
    out = nodes.new('ShaderNodeOutputMaterial')
    hair = nodes.new('ShaderNodeBsdfHairPrincipled')
    try:
        hair.parametrization = 'COLOR'
    except TypeError as exc:
        print('hair parametrization', exc)
    for key, value in (('Roughness', 0.42), ('Radial Roughness', 0.62), ('Coat', 0.05), ('IOR', 1.5),
                       ('Random Roughness', 0.2)):
        if key in hair.inputs:
            hair.inputs[key].default_value = value
    attr = nodes.new('ShaderNodeAttribute')
    attr.attribute_name = 'fur_col'
    info = nodes.new('ShaderNodeHairInfo')
    tip = nodes.new('ShaderNodeMapRange')
    tip.inputs['To Min'].default_value = 0.9
    tip.inputs['To Max'].default_value = 1.08
    links.new(info.outputs['Intercept'], tip.inputs['Value'])
    jitter = nodes.new('ShaderNodeMapRange')
    jitter.inputs['To Min'].default_value = 0.94
    jitter.inputs['To Max'].default_value = 1.04
    links.new(info.outputs['Random'], jitter.inputs['Value'])
    gain = nodes.new('ShaderNodeMath')
    gain.operation = 'MULTIPLY'
    links.new(tip.outputs['Result'], gain.inputs[0])
    links.new(jitter.outputs['Result'], gain.inputs[1])
    scale = nodes.new('ShaderNodeVectorMath')
    scale.operation = 'SCALE'
    links.new(attr.outputs['Vector'], scale.inputs[0])
    links.new(gain.outputs['Value'], scale.inputs['Scale'])
    links.new(scale.outputs['Vector'], hair.inputs['Color'])
    links.new(hair.outputs[0], out.inputs['Surface'])
    return mat


IRIS = 0.78           # радиус радужки в долях глаза


def eye_material():
    """Глаз как на набросках: белок, крупная карая радужка (светлее книзу), чёрный зрачок.

    eye_uv — координаты относительно центра радужки (сдвинута взглядом), eye_xy — относительно
    центра глаза: по ней белок чуть тенится под верхним веком."""
    mat = principled('Eye', (0.05, 0.03, 0.02), 0.25, Coat_Weight=0.35, Coat_Roughness=0.12,
                     Specular_IOR_Level=0.3)
    nodes, links = _nodes(mat)
    bsdf = next(n for n in nodes if n.type == 'BSDF_PRINCIPLED')
    attr = nodes.new('ShaderNodeAttribute')
    attr.attribute_name = 'eye_uv'
    xy = nodes.new('ShaderNodeAttribute')
    xy.attribute_name = 'eye_xy'
    length = nodes.new('ShaderNodeVectorMath')
    length.operation = 'LENGTH'
    links.new(attr.outputs['Vector'], length.inputs[0])
    ring = nodes.new('ShaderNodeValToRGB')
    k = IRIS
    stops = ((0.0, '#0a0705'), (0.38 * k, '#0a0705'), (0.43 * k, '#2b190f'), (0.72 * k, '#4d301f'),
             (0.9 * k, '#382115'), (0.98 * k, '#1b100a'), (1.0 * k, '#20140e'), (1.03 * k, '#fbfbfd'),
             (1.0, '#f3f4f8'))
    ramp = ring.color_ramp
    while len(ramp.elements) < len(stops):
        ramp.elements.new(0.5)
    for el, (pos, col) in zip(ramp.elements, stops):
        el.position = pos
        el.color = (*lin(col), 1.0)
    links.new(length.outputs['Value'], ring.inputs['Fac'])
    # Нижняя половина радужки светлее — тёплый отсвет, как на набросках.
    sep = nodes.new('ShaderNodeSeparateXYZ')
    links.new(attr.outputs['Vector'], sep.inputs[0])
    low = nodes.new('ShaderNodeMapRange')
    low.inputs['From Min'].default_value = 0.05 * k
    low.inputs['From Max'].default_value = -0.9 * k
    links.new(sep.outputs['Y'], low.inputs['Value'])
    iris_band = nodes.new('ShaderNodeMapRange')       # 1 только на радужке, не на зрачке и ободке
    iris_band.interpolation_type = 'SMOOTHSTEP'
    iris_band.inputs['From Min'].default_value = 0.42 * k
    iris_band.inputs['From Max'].default_value = 0.52 * k
    links.new(length.outputs['Value'], iris_band.inputs['Value'])
    rim_cut = nodes.new('ShaderNodeMapRange')
    rim_cut.interpolation_type = 'SMOOTHSTEP'
    rim_cut.inputs['From Min'].default_value = 0.93 * k
    rim_cut.inputs['From Max'].default_value = 0.82 * k
    links.new(length.outputs['Value'], rim_cut.inputs['Value'])
    m1 = nodes.new('ShaderNodeMath')
    m1.operation = 'MULTIPLY'
    links.new(low.outputs['Result'], m1.inputs[0])
    links.new(iris_band.outputs['Result'], m1.inputs[1])
    m2 = nodes.new('ShaderNodeMath')
    m2.operation = 'MULTIPLY'
    links.new(m1.outputs['Value'], m2.inputs[0])
    links.new(rim_cut.outputs['Result'], m2.inputs[1])
    warm = nodes.new('ShaderNodeMix')
    warm.data_type = 'RGBA'
    links.new(m2.outputs['Value'], sock(warm.inputs, 'Factor', 'VALUE'))
    links.new(ring.outputs['Color'], sock(warm.inputs, 'A', 'RGBA'))
    sock(warm.inputs, 'B', 'RGBA').default_value = (*lin('#a87650'), 1.0)
    # Белок под верхним веком в лёгкой тени.
    sep_xy = nodes.new('ShaderNodeSeparateXYZ')
    links.new(xy.outputs['Vector'], sep_xy.inputs[0])
    lid = nodes.new('ShaderNodeMapRange')
    lid.interpolation_type = 'SMOOTHSTEP'
    lid.inputs['From Min'].default_value = 0.25
    lid.inputs['From Max'].default_value = 1.0
    lid.inputs['To Max'].default_value = 0.42
    links.new(sep_xy.outputs['Y'], lid.inputs['Value'])
    sclera = nodes.new('ShaderNodeMapRange')            # тень только на белке
    sclera.interpolation_type = 'SMOOTHSTEP'
    sclera.inputs['From Min'].default_value = 0.98 * k
    sclera.inputs['From Max'].default_value = 1.06 * k
    links.new(length.outputs['Value'], sclera.inputs['Value'])
    m3 = nodes.new('ShaderNodeMath')
    m3.operation = 'MULTIPLY'
    links.new(lid.outputs['Result'], m3.inputs[0])
    links.new(sclera.outputs['Result'], m3.inputs[1])
    shade = nodes.new('ShaderNodeMix')
    shade.data_type = 'RGBA'
    links.new(m3.outputs['Value'], sock(shade.inputs, 'Factor', 'VALUE'))
    links.new(sock(warm.outputs, 'Result', 'RGBA'), sock(shade.inputs, 'A', 'RGBA'))
    sock(shade.inputs, 'B', 'RGBA').default_value = (*lin('#b9bdca'), 1.0)
    links.new(sock(shade.outputs, 'Result', 'RGBA'), bsdf.inputs['Base Color'])
    return mat


# ---------------------------------------------------------------- numpy-помощники

def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def bands(x, width=0.42, soft=0.14):
    """Полосы: 1 в середине полосы (x = k + 0.5), 0 в просвете; width — доля периода."""
    d = np.abs(np.mod(x, 1.0) - 0.5)
    return 1.0 - smoothstep(width * 0.5 - soft * 0.5, width * 0.5 + soft * 0.5, d)


def _lattice(ix, iy, iz, seed):
    h = (ix * 374761393 + iy * 668265263 + iz * 1274126177 + seed * 144665) & 0xffffffff
    h = ((h ^ (h >> 13)) * 1103515245) & 0xffffffff
    h = h ^ (h >> 16)
    return (h & 0xffff) / 65535.0


def noise3(P, scale=1.0, seed=0):
    """Гладкий шум значений в [-1, 1] — неровность полос, чтобы они не были «по линейке»."""
    X = P * scale
    i = np.floor(X).astype(np.int64)
    f = X - i
    f = f * f * (3.0 - 2.0 * f)
    out = np.zeros(len(P))
    for dx in (0, 1):
        wx = f[:, 0] if dx else 1.0 - f[:, 0]
        for dy in (0, 1):
            wy = f[:, 1] if dy else 1.0 - f[:, 1]
            for dz in (0, 1):
                wz = f[:, 2] if dz else 1.0 - f[:, 2]
                out += wx * wy * wz * _lattice(i[:, 0] + dx, i[:, 1] + dy, i[:, 2] + dz, seed)
    return out * 2.0 - 1.0


def fbm(P, scale, seed=0, octaves=3):
    total = np.zeros(len(P))
    amp = 0.6
    for k in range(octaves):
        total += amp * noise3(P, scale * (2 ** k), seed + k * 17)
        amp *= 0.5
    return total


def normalize_rows(V):
    n = np.linalg.norm(V, axis=1, keepdims=True)
    return V / np.maximum(n, 1e-9)


def mix_rows(a, b, t):
    t = np.asarray(t)[:, None]
    return a * (1.0 - t) + b * t


# ---------------------------------------------------------------- части котёнка

def quat(rot_deg):
    return Euler(tuple(math.radians(a) for a in rot_deg), 'XYZ').to_quaternion()


class Part:
    """Часть тела: элементы метабола + своя система координат для окраса и шерсти."""

    def __init__(self, name, kind, frame, **params):
        self.name = name
        self.kind = kind            # head, torso, leg, haunch, paw, tail
        self.frame = frame          # Matrix 4×4: мир ← локальные координаты части
        self.params = params
        self.elems = []

    def ellipsoid(self, co, radii, rot=(0, 0, 0), stiffness=2.0, t=0.0, negative=False):
        self.elems.append(dict(type='ELLIPSOID', co=Vector(co), rot=quat(rot), radii=tuple(radii),
                               stiffness=stiffness, t=t, negative=negative))
        return self

    def local(self, co, radii, rot=(0, 0, 0), stiffness=2.0):
        """Эллипсоид в системе координат части: туловище целиком поворачивается с позой."""
        self.elems.append(dict(type='ELLIPSOID', co=self.frame @ Vector(co),
                               rot=self.frame.to_quaternion() @ quat(rot), radii=tuple(radii),
                               stiffness=stiffness, t=0.0, negative=False))
        return self

    def chain(self, points, radii, stiffness=2.0, step=0.32):
        """Цепочка шаров вдоль сглаженной ломаной: лапа или хвост. t — 0 у корня, 1 на конце."""
        pts = [Vector(p) for p in points]
        dense = catmull(pts, 24)
        lengths = [0.0]
        for a, b in zip(dense, dense[1:]):
            lengths.append(lengths[-1] + (b - a).length)
        total = lengths[-1]
        s = 0.0
        while True:
            t = min(s / total, 1.0)
            pos = sample_polyline(dense, lengths, t * total)
            radius = interp_list(radii, t)
            self.elems.append(dict(type='BALL', co=pos, rot=Quaternion(), radii=(radius,) * 3,
                                   stiffness=stiffness, t=t, negative=False))
            if t >= 1.0:
                break
            s += max(step * radius, 0.012)
        self.params.setdefault('path', dense)
        self.params.setdefault('path_len', lengths)
        return self


def catmull(points, per_segment):
    if len(points) < 3:
        return [points[0].lerp(points[-1], i / per_segment) for i in range(per_segment + 1)]
    pts = [points[0] * 2 - points[1]] + points + [points[-1] * 2 - points[-2]]
    out = []
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        for k in range(per_segment):
            t = k / per_segment
            t2, t3 = t * t, t * t * t
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2
                              + (-p0 + 3 * p1 - 3 * p2 + p3) * t3))
    out.append(points[-1].copy())
    return out


def sample_polyline(pts, lengths, s):
    for i in range(1, len(pts)):
        if lengths[i] >= s:
            span = lengths[i] - lengths[i - 1]
            k = 0.0 if span <= 0 else (s - lengths[i - 1]) / span
            return pts[i - 1].lerp(pts[i], k)
    return pts[-1].copy()


def interp_list(values, t):
    if len(values) == 1:
        return values[0]
    x = t * (len(values) - 1)
    i = min(int(x), len(values) - 2)
    return values[i] + (values[i + 1] - values[i]) * (x - i)


def frame_matrix(origin, rot_deg=(0, 0, 0)):
    return Matrix.Translation(Vector(origin)) @ quat(rot_deg).to_matrix().to_4x4()


def elem_density(elem, P):
    """То же поле, что считает Blender: s·(1 − d²·0.575²)³ в нормированных координатах."""
    R = np.array(elem['rot'].to_matrix())
    d = (P - np.array(elem['co'])) @ R
    d = d / np.array(elem['radii'])
    dist2 = 1.0 - np.sum(d * d, axis=1) * (MB_VISIBLE ** 2)
    val = elem['stiffness'] * np.clip(dist2, 0.0, None) ** 3
    return -val if elem['negative'] else val


# ---------------------------------------------------------------- окрас частей

def local_coords(part, P):
    M = np.array(part.frame)
    R = M[:3, :3]
    return (P - M[:3, 3]) @ R


def pattern_head(part, P, Nn, t):
    """Голова: светло-серая «шапочка» с полосами-«М» на лбу, пара полос по бокам, белая мордочка, румянец."""
    L = local_coords(part, P)
    radii = np.array(part.params['radii'])
    d = normalize_rows(L / radii)
    theta = np.arctan2(d[:, 0], -d[:, 1])           # 0 — середина мордочки, ±π/2 — бока
    z = d[:, 2]
    n = len(P)
    wob = fbm(P, 3.2, seed=11) * 0.06
    edge = 0.34 - 0.8 * smoothstep(0.3, 1.3, np.abs(theta)) - 0.2 * smoothstep(1.6, 2.8, np.abs(theta)) + wob
    cap = smoothstep(edge - 0.05, edge + 0.12, z)
    col = mix_rows(np.tile(WHITE, (n, 1)), np.tile(GREY, (n, 1)), cap * 0.9)
    warp = fbm(P, 3.0, seed=3) * 0.28
    # «М» на лбу: пять полос вдоль меридианов, к бровям тоньше и обрываются.
    taper = smoothstep(edge + 0.02, edge + 0.4, z)
    fore = bands(theta / 0.3 + 0.5 + warp * 1.25, width=0.4 + 0.22 * taper, soft=0.12)
    fore *= smoothstep(edge + 0.03, edge + 0.16, z) * smoothstep(0.78, 0.5, np.abs(theta))
    fore *= smoothstep(-0.35, 0.1, fbm(P, 5.5, seed=17) + 0.25 - 0.3 * smoothstep(0.75, 0.95, z))
    # По бокам головы — две поперечные полосы, рваные.
    side = bands(z / 0.24 + 0.2 + warp * 1.2, width=0.46, soft=0.16)
    side *= smoothstep(0.62, 0.95, np.abs(theta)) * smoothstep(2.5, 1.9, np.abs(theta)) * cap
    side *= smoothstep(-0.3, 0.2, fbm(P, 4.5, seed=13) + 0.2)
    back = bands(theta / 0.32 + warp, width=0.3, soft=0.18) * smoothstep(2.1, 2.6, np.abs(theta)) * cap
    stripes = np.clip(np.maximum(np.maximum(fore, side), back), 0, 1) * (0.8 + 0.2 * fbm(P, 7.0, seed=5))
    col = mix_rows(col, np.tile(STRIPE, (n, 1)), np.clip(stripes * 1.1, 0, 1) * cap)
    # Румянец на щёчках под глазами.
    face = part.params['face']
    u, v = L[:, 0], L[:, 2]
    front = smoothstep(0.1, -0.35, d[:, 1])
    for sx in (-1, 1):
        cu, cv, r = face['blush']
        dist2 = ((u - sx * cu) ** 2 + (v - cv) ** 2) / (r * r)
        col = mix_rows(col, np.tile(BLUSH, (n, 1)), 0.75 * np.exp(-dist2 * 1.6) * front)
    # Шерсть: на мордочке короткая, на щёчках пышнее, на макушке средняя.
    length = 0.04 + 0.024 * smoothstep(0.1, -0.45, z) * smoothstep(0.55, 1.2, np.abs(theta))
    length = length * (1.0 - 0.45 * front * smoothstep(-0.6, 0.1, z) * smoothstep(1.0, 0.4, np.abs(theta)))
    # Причёсана от носа назад и в стороны.
    nose = np.array(part.frame @ Vector((0.0, -radii[1] * 0.9, -0.12)))
    comb = normalize_rows(P - nose[None, :])
    return col, length, comb


def pattern_torso(part, P, Nn, t):
    """Туловище: белые грудка и живот спереди, серая спина и бока в поперечных полосах."""
    L = local_coords(part, P)
    radii = np.array(part.params['radii'])
    d = normalize_rows(L / radii)
    front = smoothstep(-0.5, -0.85, d[:, 1] + 0.1 * fbm(P, 3.0, seed=21)) * smoothstep(0.62, 0.3, np.abs(d[:, 0]))
    grey_amt = 1.0 - front
    col = mix_rows(np.tile(WHITE, (len(P), 1)), np.tile(GREY, (len(P), 1)), grey_amt * 0.95)
    warp = fbm(P, 3.5, seed=8) * 0.35
    axis_coord = L @ np.array(part.params.get('stripe_axis', (0.0, 0.0, 1.0)))
    stripes = bands(axis_coord / 0.17 + warp, width=0.34, soft=0.2)
    dashes = smoothstep(-0.2, 0.25, fbm(P, 6.0, seed=9) + 0.1)
    spine = smoothstep(0.55, 0.95, d[:, 1]) * 0.35
    s = np.clip(stripes * dashes + spine, 0, 1) * grey_amt
    col = mix_rows(col, np.tile(STRIPE, (len(P), 1)), np.clip(s * 1.05, 0, 1))
    length = 0.05 + 0.016 * front
    comb_dir = np.array(part.frame.to_3x3() @ Vector(part.params.get('comb', (0.0, 0.35, -1.0))))
    comb = np.tile(comb_dir, (len(P), 1))
    return col, length, comb


def pattern_leg(part, P, Nn, t):
    """Лапа: снаружи серая в поперечных полосах, внутри и спереди белая; ниже — белый «носочек»."""
    outward = np.array(part.params['outward'])
    side = Nn @ outward
    grey_amt = smoothstep(0.15, 0.6, side + 0.1 * fbm(P, 4.0, seed=31))
    grey_amt *= smoothstep(part.params.get('sock', 0.72), part.params.get('sock', 0.72) - 0.18, t)
    col = mix_rows(np.tile(WHITE, (len(P), 1)), np.tile(GREY, (len(P), 1)), grey_amt * 0.9)
    warp = fbm(P, 5.0, seed=33) * 0.25
    stripes = bands(t * part.params.get('stripes', 3.2) + 0.2 + warp, width=0.45, soft=0.22)
    col = mix_rows(col, np.tile(STRIPE, (len(P), 1)), stripes * grey_amt * 0.95)
    length = np.full(len(P), 0.042)
    path = part.params['path']
    tangent = np.array((path[-1] - path[0]).normalized())
    comb = np.tile(tangent, (len(P), 1))
    return col, length, comb


def pattern_haunch(part, P, Nn, t):
    """Бедро задней лапы: серое с дугами полос, к низу светлее."""
    L = local_coords(part, P)
    radii = np.array(part.params['radii'])
    d = L / radii
    r = np.sqrt(d[:, 1] ** 2 + d[:, 2] ** 2)
    warp = fbm(P, 4.5, seed=41) * 0.18
    outward = np.array(part.params['outward'])
    side = smoothstep(-0.6, 0.2, Nn @ outward)
    under = smoothstep(-0.55, -0.85, d[:, 2])
    grey_amt = side * (1.0 - under)
    col = mix_rows(np.tile(WHITE, (len(P), 1)), np.tile(GREY, (len(P), 1)), grey_amt * 0.92)
    arcs = bands(r / 0.34 + warp, width=0.42, soft=0.2) * smoothstep(0.15, 0.4, r)
    col = mix_rows(col, np.tile(STRIPE, (len(P), 1)), arcs * grey_amt * 0.95)
    comb_dir = np.array(part.frame.to_3x3() @ Vector((0.0, 0.5, -1.0)))
    return col, np.full(len(P), 0.05), np.tile(comb_dir, (len(P), 1))


def pattern_paw(part, P, Nn, t):
    """Лапка: белая, на пальчиках две складочки, на подушечке — розовые «бобы»."""
    L = local_coords(part, P)
    radii = np.array(part.params['radii'])
    d = L / radii
    col = np.tile(WHITE, (len(P), 1))
    # Складочки между пальцами: спереди-сверху, x = ±0.33.
    toes = part.params.get('toes', True)
    density = np.ones(len(P))
    if toes:
        crease = np.zeros(len(P))
        for cx in (-0.34, 0.34):
            crease = np.maximum(crease, np.exp(-((d[:, 0] - cx) / 0.07) ** 2))
        crease *= smoothstep(0.0, -0.55, d[:, 1]) * smoothstep(-0.3, 0.35, d[:, 2])
        col = mix_rows(col, np.tile(lin('#a39d9d'), (len(P), 1)), crease * 0.75)
    if part.params.get('pads'):
        # Подушечки на стопе: стопа — локальная −Z, пальцы к −Y.
        sole = smoothstep(-0.2, -0.55, d[:, 2])
        x, y = d[:, 0], d[:, 1]
        pad = np.exp(-(((x) / 0.36) ** 2 + ((y - 0.18) / 0.32) ** 2) ** 2)
        for bx, by in ((-0.42, -0.42), (-0.15, -0.6), (0.15, -0.6), (0.42, -0.42)):
            pad = np.maximum(pad, np.exp(-(((x - bx) / 0.15) ** 2 + ((y - by) / 0.15) ** 2) ** 2))
        pad = smoothstep(0.35, 0.6, pad) * sole
        col = mix_rows(col, np.tile(PAD_PINK, (len(P), 1)), pad)
        density *= 1.0 - pad
    comb_dir = np.array(part.frame.to_3x3() @ Vector((0.0, -1.0, -0.4)))
    return col, np.full(len(P), 0.028) * density, np.tile(comb_dir, (len(P), 1))


def pattern_tail(part, P, Nn, t):
    """Хвост: серый в тёмных кольцах, кончик тёмный, шерсть длиннее — пушистый."""
    warp = fbm(P, 4.0, seed=51) * 0.08
    rings = bands(t * part.params.get('rings', 4.2) + 0.1 + warp, width=0.5, soft=0.16)
    col = mix_rows(np.tile(lin('#d0d2d7'), (len(P), 1)), np.tile(STRIPE_DARK, (len(P), 1)), rings)
    tip = smoothstep(0.86, 0.97, t)
    col = mix_rows(col, np.tile(STRIPE_DARK, (len(P), 1)), tip * 0.9)
    base = smoothstep(0.12, 0.0, t)
    col = mix_rows(col, np.tile(GREY, (len(P), 1)), base)
    path = part.params['path']
    pts = np.array([tuple(p) for p in path])
    idx = np.clip((t * (len(pts) - 1)).astype(int), 0, len(pts) - 2)
    tangent = normalize_rows(pts[idx + 1] - pts[idx])
    return col, np.full(len(P), 0.075), tangent


PATTERNS = {
    'head': pattern_head,
    'torso': pattern_torso,
    'leg': pattern_leg,
    'haunch': pattern_haunch,
    'paw': pattern_paw,
    'tail': pattern_tail,
}


# ---------------------------------------------------------------- тело: метабол → меш

def build_body(parts, name='Kitten'):
    mb = bpy.data.metaballs.new(name)
    mb.resolution = MB_RES
    mb.render_resolution = MB_RES
    mb.threshold = MB_THRESHOLD
    for part in parts:
        for e in part.elems:
            el = mb.elements.new(type=e['type'])
            el.co = e['co']
            el.rotation = e['rot']
            el.stiffness = e['stiffness']
            el.use_negative = e['negative']
            if e['type'] == 'ELLIPSOID':
                el.radius = 1.0 / MB_VISIBLE
                el.size_x, el.size_y, el.size_z = e['radii']
            else:
                el.radius = e['radii'][0] / MB_VISIBLE
    mb_obj = bpy.data.objects.new(name + 'MB', mb)
    bpy.context.scene.collection.objects.link(mb_obj)
    deps = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(mb_obj.evaluated_get(deps))
    bpy.data.objects.remove(mb_obj)
    bpy.data.metaballs.remove(mb)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    mesh.shade_smooth()
    return obj


def mesh_arrays(obj):
    mesh = obj.data
    n = len(mesh.vertices)
    P = np.empty(n * 3)
    mesh.vertices.foreach_get('co', P)
    Nn = np.empty(n * 3)
    mesh.vertex_normals.foreach_get('vector', Nn)
    M = np.array(obj.matrix_world)
    P = P.reshape(-1, 3) @ M[:3, :3].T + M[:3, 3]
    Nn = normalize_rows(Nn.reshape(-1, 3) @ M[:3, :3].T)
    return P, Nn


def paint_body(obj, parts):
    """Окрас, длина и направление шерсти для каждой вершины — смесь частей по полю метабола."""
    P, Nn = mesh_arrays(obj)
    n = len(P)
    weights = []
    for part in parts:
        w = np.zeros(n)
        tw = np.zeros(n)
        for e in part.elems:
            c = np.clip(elem_density(e, P), 0.0, None)
            w += c
            tw += c * e['t']
        weights.append((w, np.where(w > 1e-9, tw / np.maximum(w, 1e-9), 0.0)))
    total = sum(w for w, _ in weights) + 1e-9
    col = np.zeros((n, 3))
    length = np.zeros(n)
    comb = np.zeros((n, 3))
    owner = np.zeros(n, dtype=np.int64)
    best = np.zeros(n)
    for k, (part, (w, t)) in enumerate(zip(parts, weights)):
        share = w / total
        mask = share > 0.002
        if not mask.any():
            continue
        c, l, cb = PATTERNS[part.kind](part, P[mask], Nn[mask], t[mask])
        col[mask] += c * share[mask, None]
        length[mask] += l * share[mask]
        comb[mask] += cb * share[mask, None]
        better = share > best
        owner[better] = k
        best[better] = share[better]
    attr = obj.data.color_attributes.new('col', 'FLOAT_COLOR', 'POINT')
    rgba = np.concatenate([col, np.ones((n, 1))], axis=1).astype(np.float32)
    attr.data.foreach_set('color', rgba.ravel())
    return dict(P=P, N=Nn, col=col, length=length, comb=normalize_rows(comb), owner=owner,
                density=np.ones(n))


# ---------------------------------------------------------------- шерсть

def grow_fur(name, obj, fields, material, per_unit2=FUR_PER_UNIT2, seed=1, radius=0.0036, points=5):
    if FUR <= 0:
        return None
    rng = np.random.default_rng(seed)
    mesh = obj.data
    mesh.calc_loop_triangles()
    tris = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int64)
    mesh.loop_triangles.foreach_get('vertices', tris)
    tris = tris.reshape(-1, 3)
    P, Nn = fields['P'], fields['N']
    a, b, c = P[tris[:, 0]], P[tris[:, 1]], P[tris[:, 2]]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    dens = fields['density'][tris].mean(axis=1) * (fields['length'][tris].min(axis=1) > 0.004)
    weight = area * dens
    count = int(weight.sum() * per_unit2 * FUR)
    if count <= 0:
        return None
    pick = rng.choice(len(tris), size=count, p=weight / weight.sum())
    r1 = np.sqrt(rng.random(count))
    r2 = rng.random(count)
    bw = np.stack([1.0 - r1, r1 * (1.0 - r2), r1 * r2], axis=1)
    tv = tris[pick]

    def interp(arr):
        return (arr[tv] * bw[:, :, None]).sum(axis=1) if arr.ndim == 2 else (arr[tv] * bw).sum(axis=1)

    root = interp(P)
    normal = normalize_rows(interp(Nn))
    color = interp(fields['col'])
    length = interp(fields['length']) * (0.72 + 0.5 * rng.random(count))
    comb = interp(fields['comb'])
    comb = comb - normal * np.sum(comb * normal, axis=1, keepdims=True)
    comb = normalize_rows(comb + rng.normal(0, 0.35, (count, 3)) * 0.5)
    comb = comb - normal * np.sum(comb * normal, axis=1, keepdims=True)
    comb = normalize_rows(comb)
    lift = 0.32 + 0.22 * rng.random(count)
    d0 = normalize_rows(normal * lift[:, None] + comb * (1.0 - lift[:, None]))
    ts = np.linspace(0.0, 1.0, points)
    frizz = rng.normal(0, 1.0, (count, 3)) * 0.06
    pos = np.empty((count, points, 3))
    for k, tt in enumerate(ts):
        bend = comb * (0.55 * tt * tt) - normal * (0.14 * tt * tt)
        pos[:, k] = root + length[:, None] * (d0 * tt + bend + frizz * tt * tt)
    rad = np.empty((count, points))
    for k, tt in enumerate(ts):
        rad[:, k] = radius * (1.0 - 0.82 * tt)
    curves = bpy.data.hair_curves.new(name)
    curves.add_curves([points] * count)
    curves.position_data.foreach_set('vector', pos.astype(np.float32).ravel())
    radius_attr = curves.attributes.get('radius') or curves.attributes.new('radius', 'FLOAT', 'POINT')
    radius_attr.data.foreach_set('value', rad.astype(np.float32).ravel())
    col_attr = curves.attributes.new('fur_col', 'FLOAT_VECTOR', 'CURVE')
    col_attr.data.foreach_set('vector', color.astype(np.float32).ravel())
    curves.materials.append(material)
    fur = bpy.data.objects.new(name, curves)
    bpy.context.scene.collection.objects.link(fur)
    print('FUR', name, count, flush=True)
    return fur


# ---------------------------------------------------------------- мордочка

class Face:
    """Проекция плоских рисунков мордочки на голову: луч изнутри головы вперёд."""

    def __init__(self, body, head_part):
        self.head = head_part
        self.M = head_part.frame
        self.R = self.M.to_3x3()
        deps = bpy.context.evaluated_depsgraph_get()
        self.bvh = BVHTree.FromObject(body, deps)

    def hit(self, u, v):
        origin = self.M @ Vector((u, 0.0, v))
        direction = (self.R @ Vector((0.0, -1.0, 0.0))).normalized()
        loc, normal, _, _ = self.bvh.ray_cast(origin, direction, 5.0)
        if loc is None:
            loc = origin + direction * 0.6
            normal = direction
        if normal.dot(direction) < 0:
            normal = -normal
        return loc, normal

    def point(self, u, v, lift):
        loc, normal = self.hit(u, v)
        return loc + normal * lift


def link_mesh(name, verts, faces, material, attrs=None, smooth=True):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([tuple(v) for v in verts], [], faces)
    mesh.update()
    if smooth:
        mesh.shade_smooth()
    if attrs:
        for key, (kind, values) in attrs.items():
            attr = mesh.attributes.new(key, kind, 'POINT')
            flat = np.asarray(values, dtype=np.float32).ravel()
            attr.data.foreach_set('vector' if kind == 'FLOAT_VECTOR' else 'value', flat)
    mesh.materials.append(material)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def tube(name, points, radius, material, radii=None, cyclic=False):
    curve = bpy.data.curves.new(name, 'CURVE')
    curve.dimensions = '3D'
    curve.bevel_depth = radius
    curve.bevel_resolution = 4
    curve.use_fill_caps = True
    spline = curve.splines.new('BEZIER')
    spline.bezier_points.add(len(points) - 1)
    spline.use_cyclic_u = cyclic
    for i, (bp, co) in enumerate(zip(spline.bezier_points, points)):
        bp.co = co
        bp.handle_left_type = 'AUTO'
        bp.handle_right_type = 'AUTO'
        bp.radius = radii[i] if radii else 1.0
    obj = bpy.data.objects.new(name, curve)
    bpy.context.scene.collection.objects.link(obj)
    curve.materials.append(material)
    return obj


def ray_from(origin, direction, center_r):
    """Расстояние от origin вдоль direction до единичной окружности (origin внутри)."""
    od = origin[0] * direction[0] + origin[1] * direction[1]
    oo = origin[0] ** 2 + origin[1] ** 2
    return -od + math.sqrt(max(od * od - oo + center_r * center_r, 0.0))


def eye_outline(lids, origin=(0.0, -0.25), n=72):
    """Видимый контур глаза в нормированных координатах (x — к носу): круг, срезанный веками.

    lids — список прямых (x1, y1, x2, y2): видно то, что ниже прямой (для нижнего века —
    кортеж с пятым элементом 'below' — видно выше)."""
    pts = []
    for k in range(n):
        a = 2 * math.pi * k / n
        e = (math.cos(a), math.sin(a))
        r = ray_from(origin, e, 1.0)
        for lid in lids:
            x1, y1, x2, y2 = lid[:4]
            keep_above = len(lid) > 4 and lid[4] == 'below'
            nx, ny = -(y2 - y1), (x2 - x1)
            if ny < 0:
                nx, ny = -nx, -ny
            if keep_above:
                nx, ny = -nx, -ny
            norm = math.hypot(nx, ny)
            nx, ny = nx / norm, ny / norm
            k_line = nx * x1 + ny * y1
            denom = nx * e[0] + ny * e[1]
            if denom > 1e-6:
                r_lid = (k_line - (nx * origin[0] + ny * origin[1])) / denom
                if r_lid > 0:
                    r = min(r, r_lid)
        pts.append((origin[0] + e[0] * r, origin[1] + e[1] * r))
    return pts


def build_eye(face, prefix, spec, mats):
    """Открытый глаз: линза-купол по контуру, тёмная обводка (толще сверху), блики."""
    side = spec['side']                     # −1 — левый на картинке, +1 — правый
    cu, cv, rx, ry = spec['cx'] * side, spec['cy'], spec['rx'], spec['ry']
    inner = -side                           # к носу: для левого глаза это +x
    lids = spec.get('lids', [])
    origin = spec.get('origin', (0.0, -0.25))
    outline = eye_outline(lids, origin)
    rings = 8
    verts2 = [origin]
    for ring in range(1, rings + 1):
        f = ring / rings
        for (x, y) in outline:
            verts2.append((origin[0] + (x - origin[0]) * f, origin[1] + (y - origin[1]) * f))
    n = len(outline)
    faces = [(0, 1 + k, 1 + (k + 1) % n) for k in range(n)]
    for ring in range(1, rings):
        a0, a1 = 1 + (ring - 1) * n, 1 + ring * n
        faces += [(a0 + k, a1 + k, a1 + (k + 1) % n, a0 + (k + 1) % n) for k in range(n)]
    look = spec.get('look', (0.0, 0.0))
    bulge = spec.get('bulge', 0.03)
    verts3, uvs, xys = [], [], []
    for (x, y) in verts2:
        u = cu + x * rx * inner
        v = cv + y * ry
        rho2 = min(x * x + y * y, 1.0)
        verts3.append(face.point(u, v, 0.006 + bulge * (1.0 - rho2) ** 0.8))
        xi = x * inner                       # в координатах картинки
        uvs.append((xi - look[0], y - look[1], 0.0))
        xys.append((xi, y, 0.0))
    eye = link_mesh(f'{prefix}Eye', verts3, faces, mats['eye'],
                    {'eye_uv': ('FLOAT_VECTOR', uvs), 'eye_xy': ('FLOAT_VECTOR', xys)})
    sub = eye.modifiers.new('Smooth', 'SUBSURF')
    sub.levels = sub.render_levels = 1
    # Обводка по видимому контуру — сверху толще («стрелка»), как на набросках.
    ring_pts = verts3[1 + (rings - 1) * n: 1 + rings * n]
    radii = []
    for (x, y) in outline:
        radii.append(0.5 + 1.25 * smooth01((y + 0.1) / 0.9))
    tube(f'{prefix}Line', ring_pts, spec.get('line', 0.012), mats['line'], radii, cyclic=True)
    # Блики — на роговице над радужкой: едут вместе со взглядом.
    for k, (hx, hy, hr) in enumerate(spec.get('sparks', [(0.2, 0.22, 0.17), (-0.22, -0.24, 0.075)])):
        disc(face, f'{prefix}Spark{k}', cu, cv, rx, ry, inner, hx + look[0], hy + look[1], hr, bulge,
             mats['spark'], outline)
    if spec.get('star'):
        sx, sy, sr = spec['star']
        star(face, f'{prefix}Star', cu, cv, rx, ry, inner, sx + look[0], sy + look[1], sr, bulge, mats['spark'])
    return eye


def smooth01(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def eye_surface(face, cu, cv, rx, ry, inner, x, y, bulge, extra):
    rho2 = min(x * x + y * y, 1.0)
    return face.point(cu + x * inner * rx, cv + y * ry, 0.006 + bulge * (1.0 - rho2) ** 0.8 + extra)


def inside_outline(x, y, outline):
    # точка внутри выпуклого многоугольника контура
    sign = 0
    for (x1, y1), (x2, y2) in zip(outline, outline[1:] + outline[:1]):
        cross = (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)
        if abs(cross) < 1e-12:
            continue
        s = 1 if cross > 0 else -1
        if sign == 0:
            sign = s
        elif s != sign:
            return False
    return True


def disc(face, name, cu, cv, rx, ry, inner, hx, hy, hr, bulge, mat, outline, n=20):
    """Блик: эллипс на поверхности глаза; x — в координатах картинки."""
    hx_i = hx * inner                         # в координатах «к носу»
    pts = [(hx_i + math.cos(2 * math.pi * k / n) * hr, hy + math.sin(2 * math.pi * k / n) * hr * 1.05)
           for k in range(n)]
    pts = [p for p in pts if inside_outline(p[0], p[1], outline)]
    if len(pts) < 3 or not inside_outline(hx_i, hy, outline):
        return None
    verts = [eye_surface(face, cu, cv, rx, ry, inner, hx_i, hy, bulge, 0.0035)]
    verts += [eye_surface(face, cu, cv, rx, ry, inner, x, y, bulge, 0.0035) for (x, y) in pts]
    faces = [(0, 1 + k, 1 + (k + 1) % len(pts)) for k in range(len(pts))]
    return link_mesh(name, verts, faces, mat)


def star(face, name, cu, cv, rx, ry, inner, sx, sy, sr, bulge, mat, n=4):
    """Четырёхлучевая искра в глазу (Excited)."""
    pts = []
    for k in range(n * 2):
        a = math.pi / 2 + math.pi * k / n
        r = sr if k % 2 == 0 else sr * 0.3
        pts.append((sx * inner + math.cos(a) * r * 0.9, sy + math.sin(a) * r))
    verts = [eye_surface(face, cu, cv, rx, ry, inner, sx * inner, sy, bulge, 0.004)]
    verts += [eye_surface(face, cu, cv, rx, ry, inner, x, y, bulge, 0.004) for (x, y) in pts]
    faces = [(0, 1 + k, 1 + (k + 1) % len(pts)) for k in range(len(pts))]
    return link_mesh(name, verts, faces, mat)


def face_curve(face, name, pts2, radius, mat, lift=0.012, taper=True, radii=None):
    """Линия на мордочке: закрытый глаз, рот, складка — трубка по поверхности."""
    dense = catmull([Vector((x, y, 0)) for x, y in pts2], 6)
    verts = [face.point(p.x, p.y, lift) for p in dense]
    if radii is None:
        count = len(verts)
        radii = []
        for i in range(count):
            t = i / max(count - 1, 1)
            radii.append(0.35 + 0.65 * math.sin(math.pi * t) ** 0.5 if taper else 1.0)
    return tube(name, verts, radius, mat, radii)


def face_patch(face, name, outline2, mat, lift=0.006, dome=0.0, center=None):
    """Пластинка на мордочке по контуру (рот, язык, нос): веер от центра, купол."""
    if center is None:
        center = (sum(p[0] for p in outline2) / len(outline2), sum(p[1] for p in outline2) / len(outline2))
    rings = 5
    verts = [face.point(center[0], center[1], lift + dome)]
    n = len(outline2)
    for ring in range(1, rings + 1):
        f = ring / rings
        for (x, y) in outline2:
            px = center[0] + (x - center[0]) * f
            py = center[1] + (y - center[1]) * f
            verts.append(face.point(px, py, lift + dome * (1.0 - f * f)))
    faces = [(0, 1 + k, 1 + (k + 1) % n) for k in range(n)]
    for ring in range(1, rings):
        a0, a1 = 1 + (ring - 1) * n, 1 + ring * n
        faces += [(a0 + k, a1 + k, a1 + (k + 1) % n, a0 + (k + 1) % n) for k in range(n)]
    return link_mesh(name, verts, faces, mat)


def ellipse2(cx, cy, rx, ry, n=36, a0=0.0, a1=2 * math.pi, closed=True):
    count = n if closed else n + 1
    return [(cx + math.cos(a0 + (a1 - a0) * k / n) * rx, cy + math.sin(a0 + (a1 - a0) * k / n) * ry)
            for k in range(count)]


def rounded_triangle(cx, cy, w, h, r=0.35, n=12):
    """Носик: перевёрнутый треугольник со скруглёнными углами."""
    corners = [(cx - w / 2, cy + h / 2), (cx + w / 2, cy + h / 2), (cx, cy - h / 2)]
    pts = []
    for i in range(3):
        p0 = Vector(corners[i - 1])
        p1 = Vector(corners[i])
        p2 = Vector(corners[(i + 1) % 3])
        a = p1.lerp(p0, r * 0.5)
        b = p1.lerp(p2, r * 0.5)
        for k in range(n + 1):
            t = k / n
            q = (1 - t) ** 2 * a + 2 * (1 - t) * t * p1 + t * t * b
            pts.append((q.x, q.y))
    return pts


# ---------------------------------------------------------------- уши

def build_ear(name, head, side, droop, mats):
    """Вислое ушко: подушечка-треугольник лежит на голове, кончик смотрит вперёд-вниз-наружу."""
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=40, v_segments=20, radius=1.0)
    hp = head.params
    w_half, h_half, thick = hp.get('ear_size', (0.27, 0.21, 0.065))
    for v in bm.verts:
        x, y, z = v.co
        w = 0.5 + 0.5 * (z + 1) / 2                     # широкий сгиб сверху, узкий кончик внизу
        v.co = Vector((x * w_half * w, y * thick, z * h_half))
        v.co.y += 1.1 * (v.co.x ** 2) + 0.8 * (v.co.z ** 2)   # края загнуты к голове (+y)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.shade_smooth()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    radii = Vector(hp['radii'])
    az = math.radians(hp.get('ear_az', 56)) * side
    el = math.radians(hp.get('ear_el', 45) - droop * 10)
    d = Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))
    surface = Vector((d.x * radii.x, d.y * radii.y, d.z * radii.z))
    normal = Vector((d.x / radii.x, d.y / radii.y, d.z / radii.z)).normalized()
    up = Vector((0, 0, 1))
    z_axis = (up - normal * up.dot(normal)).normalized()
    y_axis = -normal
    x_axis = y_axis.cross(z_axis).normalized()
    basis = Matrix((x_axis, y_axis, z_axis)).transposed()
    tilt = Matrix.Rotation(math.radians((hp.get('ear_tilt', 30) + droop * 16) * side), 3, normal)
    basis = tilt @ basis
    local = Matrix.Translation(surface + normal * hp.get('ear_lift', 0.05)) @ basis.to_4x4()
    obj.matrix_world = head.frame @ local
    # окрас уха: светло-серое, полоса поперёк, внутренняя сторона прижата к голове
    P, Nn = mesh_arrays(obj)
    n = len(P)
    co = np.empty(n * 3)
    mesh.vertices.foreach_get('co', co)
    co = co.reshape(-1, 3)
    col = np.tile(lin('#d7d9de'), (n, 1))
    stripe = bands(co[:, 2] / 0.12 + 0.3 + 0.5 * co[:, 0], width=0.36, soft=0.25)
    col = mix_rows(col, np.tile(GREY_MID, (n, 1)), stripe * 0.75)
    attr = mesh.color_attributes.new('col', 'FLOAT_COLOR', 'POINT')
    attr.data.foreach_set('color', np.concatenate([col, np.ones((n, 1))], axis=1).astype(np.float32).ravel())
    mesh.materials.append(mats['skin'])
    comb = np.tile(np.array(obj.matrix_world.to_3x3() @ Vector((0, 0, -1))), (n, 1))
    return obj, dict(P=P, N=Nn, col=col, length=np.full(n, 0.022), comb=comb, density=np.ones(n))


# ---------------------------------------------------------------- позы

HEAD_RADII = (0.68, 0.6, 0.6)


def head_part(center, rot, face_params, droop=0.0):
    frame = frame_matrix(center, rot)
    head = Part('head', 'head', frame, radii=HEAD_RADII, face=face_params, droop=droop)
    # Голова — широкий эллипсоид, щёчки и мордочка — элементы в её системе координат.
    head.local((0, 0, 0), HEAD_RADII)
    for s in (-1, 1):
        head.local((0.32 * s, -0.18, -0.26), (0.42, 0.34, 0.32), stiffness=1.9)
    head.local((0, -0.42, -0.16), (0.26, 0.17, 0.17), stiffness=1.3)
    return head


def default_face():
    return dict(blush=(0.43, -0.14, 0.12))


def add_leg(parts, s, path, radii, outward, sock=0.72, stripes=3.2):
    leg = Part(f'leg{s}', 'leg', Matrix.Identity(4), outward=outward, sock=sock, stripes=stripes)
    leg.chain(path, radii)
    parts.append(leg)
    return leg


def add_paw(parts, name, center, radii, rot=(0, 0, 0), pads=False, toes=True):
    paw = Part(name, 'paw', frame_matrix(center, rot), radii=radii, pads=pads, toes=toes)
    paw.ellipsoid(center, radii, rot, stiffness=1.8)
    parts.append(paw)
    return paw


def add_haunch(parts, s, center, radii, rot=(0, 0, 0), outward=None):
    haunch = Part(f'haunch{s}', 'haunch', frame_matrix(center, rot), radii=radii,
                  outward=outward or (s, 0.1, 0.0))
    haunch.ellipsoid(center, radii, rot, stiffness=1.8)
    parts.append(haunch)
    return haunch


def add_tail(parts, points, radii, rings=4.2):
    tail = Part('tail', 'tail', Matrix.Identity(4), rings=rings)
    tail.chain(points, radii, stiffness=2.0)
    parts.append(tail)
    return tail


def sit_torso(parts, lean=0.0):
    torso = Part('torso', 'torso', frame_matrix((0, 0.08, 0.55), (lean, 0, 0)), radii=(0.58, 0.5, 0.58))
    torso.local((0, 0.02, 0.01), (0.56, 0.48, 0.56))
    torso.local((0, 0.04, -0.25), (0.66, 0.54, 0.3))
    torso.local((0, -0.18, 0.25), (0.42, 0.32, 0.36), stiffness=1.5)
    torso.local((0, -0.25, -0.13), (0.38, 0.3, 0.32), stiffness=1.4)
    parts.append(torso)
    return torso


SIT_TAIL = ([(-0.26, 0.45, 0.16), (-0.68, 0.36, 0.2), (-0.94, 0.1, 0.32), (-0.98, -0.12, 0.52),
             (-0.86, -0.2, 0.7)], [0.18, 0.2, 0.21, 0.2, 0.17])


def pose_sit(head_rot=(0, 0, 0), head_shift=(0, 0, 0), lean=0.0, paw_up=None, droop=0.0, face=None):
    """Сидит прямо: спокойный, Happy, Sad, Surprised, Confused, Sleepy (лапка у рта)."""
    parts = []
    head = head_part(Vector((0.0, -0.1, 1.56)) + Vector(head_shift), head_rot, face or default_face(), droop)
    parts.append(head)
    sit_torso(parts, lean)
    for s in (-1, 1):
        if paw_up is not None and s == paw_up['side']:
            add_leg(parts, s, paw_up['path'], [0.165, 0.155, 0.15], (s * 0.8, -0.6, 0.0), sock=0.75)
            add_paw(parts, f'paw{s}', paw_up['paw'], (0.15, 0.14, 0.16), paw_up['paw_rot'])
        else:
            add_leg(parts, s, [(0.2 * s, -0.3, 0.78), (0.21 * s, -0.4, 0.45), (0.21 * s, -0.45, 0.17)],
                    [0.165, 0.16, 0.155], (s * 0.8, -0.6, 0.0))
            add_paw(parts, f'paw{s}', (0.21 * s, -0.53, 0.1), (0.17, 0.2, 0.105))
        add_haunch(parts, s, (0.48 * s, 0.05, 0.32), (0.28, 0.38, 0.3), (0, 0, -12 * s))
        add_paw(parts, f'foot{s}', (0.57 * s, -0.3, 0.085), (0.15, 0.2, 0.09))
    add_tail(parts, *SIT_TAIL)
    return parts, head


def pose_excited(face=None):
    """Excited: откинулся назад, задние лапки вперёд розовыми подушечками, передние — у щёк."""
    parts = []
    head = head_part((0.0, 0.0, 1.52), (-8, 0, 0), face or default_face())
    parts.append(head)
    torso = Part('torso', 'torso', frame_matrix((0, 0.12, 0.55), (-16, 0, 0)), radii=(0.58, 0.5, 0.58))
    torso.local((0, 0.02, 0.01), (0.56, 0.48, 0.56))
    torso.local((0, 0.0, -0.24), (0.66, 0.56, 0.32))
    torso.local((0, -0.2, 0.2), (0.44, 0.32, 0.38), stiffness=1.5)
    torso.local((0, -0.28, -0.12), (0.42, 0.32, 0.34), stiffness=1.5)
    parts.append(torso)
    for s in (-1, 1):
        add_leg(parts, s, [(0.27 * s, -0.16, 0.86), (0.33 * s, -0.52, 0.76), (0.24 * s, -0.78, 0.9)],
                [0.165, 0.155, 0.15], (s * 0.9, -0.4, 0.0), sock=0.84)
        add_paw(parts, f'paw{s}', (0.23 * s, -0.84, 0.94), (0.15, 0.14, 0.15), (-90, 0, 180))
        add_haunch(parts, s, (0.46 * s, 0.04, 0.3), (0.28, 0.38, 0.3), (0, 0, -12 * s))
        add_leg(parts, 10 + s, [(0.46 * s, -0.1, 0.26), (0.43 * s, -0.42, 0.2)], [0.17, 0.16],
                (s, 0.0, 0.0), sock=0.55, stripes=1.6)
        add_paw(parts, f'foot{s}', (0.43 * s, -0.6, 0.23), (0.165, 0.22, 0.095), (-90, 0, 0), pads=True, toes=False)
    add_tail(parts, [(-0.26, 0.5, 0.16), (-0.7, 0.4, 0.2), (-0.96, 0.12, 0.3), (-1.0, -0.12, 0.48),
                     (-0.88, -0.22, 0.64)], [0.18, 0.2, 0.21, 0.2, 0.17])
    return parts, head


def pose_stand(yaw=55.0, head_rot=(8, 0, 18), tail=None, paw_lift=False, droop=0.0, face=None):
    """Angry и Mischievous: стоит на четырёх лапах вполоборота, хвост трубой."""
    parts = []
    psi = math.radians(yaw)
    f = Vector((math.sin(psi), -math.cos(psi), 0.0))       # вперёд, к голове
    l = Vector((math.cos(psi), math.sin(psi), 0.0))        # вбок; +l — дальний от камеры бок
    c = Vector((-0.05, 0.12, 0.62))
    head = head_part(c + f * 0.6 + Vector((0, 0, 0.64)), head_rot, face or default_face(), droop)
    parts.append(head)
    torso = Part('torso', 'torso', frame_matrix(c, (90, 0, yaw)), radii=(0.46, 0.44, 0.7),
                 comb=(0.0, 0.3, -1.0))
    torso.local((0, 0, 0), (0.46, 0.44, 0.66))
    torso.local((0, -0.02, 0.42), (0.44, 0.44, 0.38), stiffness=1.8)
    torso.local((0, 0.02, -0.42), (0.47, 0.44, 0.4), stiffness=1.8)
    torso.local((0, -0.14, 0.0), (0.38, 0.32, 0.5), stiffness=1.3)
    parts.append(torso)
    for s in (-1, 1):
        side = l * (0.25 * s)
        shoulder = c + f * 0.44 + side + Vector((0, 0, -0.02))
        if paw_lift and s == -1:
            elbow = shoulder + f * 0.3 + Vector((0, 0, -0.18))
            paw_at = shoulder + f * 0.5 + Vector((0, 0, -0.26))
            add_leg(parts, s, [shoulder, elbow, paw_at], [0.165, 0.155, 0.15], tuple(l * s + f * 0.3), sock=0.78)
            add_paw(parts, f'paw{s}', tuple(paw_at + f * 0.08 + Vector((0, 0, -0.04))), (0.16, 0.2, 0.11),
                    (-28, 0, yaw))
        else:
            paw_at = c + f * 0.5 + side
            add_leg(parts, s, [shoulder, paw_at + Vector((0, 0, -0.3)), paw_at + Vector((0, 0, -0.46))],
                    [0.165, 0.16, 0.155], tuple(l * s + f * 0.3), sock=0.78)
            add_paw(parts, f'paw{s}', tuple(paw_at + f * 0.06 + Vector((0, 0, -0.52))), (0.16, 0.2, 0.1), (0, 0, yaw))
        hip = c - f * 0.42 + l * (0.27 * s)
        add_haunch(parts, s, tuple(hip + Vector((0, 0, -0.02))), (0.26, 0.34, 0.32), (0, 0, yaw),
                   outward=tuple(l * s))
        foot_at = c - f * 0.38 + l * (0.27 * s)
        add_leg(parts, 10 + s, [hip + Vector((0, 0, -0.1)), foot_at + Vector((0, 0, -0.4))], [0.16, 0.15],
                tuple(l * s), sock=0.75, stripes=2.0)
        add_paw(parts, f'foot{s}', tuple(foot_at + f * 0.05 + Vector((0, 0, -0.52))), (0.155, 0.2, 0.1), (0, 0, yaw))
    rear = c - f * 0.6
    pts = tail or [rear + Vector((0, 0, 0.12)), rear - f * 0.22 + Vector((0, 0, 0.42)),
                   rear - f * 0.3 + Vector((0, 0, 0.82)), rear - f * 0.16 + Vector((0, 0, 1.12)),
                   rear + f * 0.06 + Vector((0, 0, 1.2))]
    add_tail(parts, pts, [0.17, 0.2, 0.22, 0.21, 0.18])
    return parts, head


def pose_crouch(face=None):
    """Scared: припал к полу, лапки вперёд, голова низко, ушки прижаты."""
    parts = []
    head = head_part((0.0, -0.26, 0.86), (6, 0, 0), face or default_face(), droop=1.0)
    parts.append(head)
    torso = Part('torso', 'torso', frame_matrix((0, 0.36, 0.36), (90, 0, 0)), radii=(0.58, 0.36, 0.62))
    torso.local((0, 0, 0), (0.58, 0.36, 0.62))
    torso.local((0, -0.02, 0.42), (0.48, 0.34, 0.36), stiffness=1.8)
    torso.local((0, 0.04, -0.38), (0.62, 0.36, 0.38), stiffness=1.8)
    parts.append(torso)
    for s in (-1, 1):
        add_leg(parts, s, [(0.27 * s, -0.02, 0.26), (0.28 * s, -0.4, 0.13)], [0.165, 0.155],
                (s * 0.8, -0.6, 0.0), sock=0.6)
        add_paw(parts, f'paw{s}', (0.28 * s, -0.58, 0.1), (0.18, 0.21, 0.105))
        add_haunch(parts, s, (0.54 * s, 0.5, 0.26), (0.28, 0.36, 0.26), (0, 0, -8 * s))
        add_paw(parts, f'foot{s}', (0.62 * s, 0.16, 0.08), (0.14, 0.19, 0.085))
    add_tail(parts, [(-0.3, 0.92, 0.14), (-0.76, 0.82, 0.15), (-1.02, 0.48, 0.15), (-1.06, 0.12, 0.16),
                     (-0.92, -0.12, 0.18)], [0.17, 0.19, 0.2, 0.19, 0.16])
    return parts, head


def pose_curl(face=None):
    """Спит клубочком (второй набросок): голова на лапках, хвост обнимает спереди."""
    parts = []
    head = head_part((0.42, -0.28, 0.6), (12, 8, 14), face or default_face(), droop=0.35)
    parts.append(head)
    torso = Part('torso', 'torso', frame_matrix((-0.32, 0.24, 0.42), (90, 0, 90)), radii=(0.56, 0.42, 0.78))
    torso.local((0, 0, 0), (0.56, 0.42, 0.72))
    torso.local((0, 0.04, -0.42), (0.62, 0.44, 0.42), stiffness=1.8)
    torso.local((0, -0.02, 0.4), (0.5, 0.38, 0.4), stiffness=1.8)
    parts.append(torso)
    add_leg(parts, -1, [(0.1, -0.12, 0.24), (0.2, -0.46, 0.12)], [0.15, 0.145], (0.0, -1.0, 0.0), sock=0.55)
    add_paw(parts, 'paw-1', (0.22, -0.62, 0.1), (0.15, 0.19, 0.095), (0, 0, -10))
    add_leg(parts, 1, [(0.36, -0.08, 0.24), (0.58, -0.4, 0.12)], [0.15, 0.145], (0.0, -1.0, 0.0), sock=0.55)
    add_paw(parts, 'paw1', (0.66, -0.56, 0.1), (0.15, 0.19, 0.095), (0, 0, 18))
    add_haunch(parts, -1, (-0.6, -0.04, 0.42), (0.34, 0.4, 0.34), (0, 0, 10), outward=(0.0, -1.0, 0.2))
    add_paw(parts, 'foot-1', (-0.3, -0.44, 0.09), (0.14, 0.19, 0.085), (0, 0, -30))
    add_tail(parts, [(-1.02, 0.36, 0.2), (-1.18, -0.02, 0.19), (-0.92, -0.44, 0.16), (-0.42, -0.64, 0.14),
                     (0.02, -0.7, 0.13)], [0.17, 0.19, 0.2, 0.19, 0.16])
    return parts, head


# ---------------------------------------------------------------- мордочки

EYE_CX, EYE_CY, EYE_RX, EYE_RY = 0.32, 0.0, 0.168, 0.18
NOSE_Y = -0.052
MOUTH_Y = NOSE_Y - 0.033            # от низа носа

# Закрытые глаза — линии в нормированных координатах глаза (x — к носу).
CLOSED = {
    'happy': [(-0.88, -0.22), (-0.5, 0.22), (0.0, 0.4), (0.5, 0.22), (0.88, -0.22)],
    'wink': [(-0.86, -0.12), (-0.42, 0.28), (0.08, 0.36), (0.55, 0.16), (0.88, -0.16)],
    'sleepy': [(-0.92, 0.1), (-0.48, -0.06), (0.0, -0.1), (0.46, -0.05), (0.88, 0.06)],
    'asleep': [(-0.88, 0.14), (-0.45, -0.1), (0.0, -0.16), (0.45, -0.1), (0.86, 0.1)],
}


def eye_open(side, **spec):
    base = dict(side=side, cx=EYE_CX, cy=EYE_CY, rx=EYE_RX, ry=EYE_RY)
    base.update(spec)
    base['rx'] *= spec.get('scale', 1.0)
    base['ry'] *= spec.get('scale', 1.0)
    return base


def closed_eye(face, mats, side, shape, width=0.021):
    inner = -side
    cu = EYE_CX * side
    pts = [(cu + x * EYE_RX * inner, EYE_CY + y * EYE_RY) for (x, y) in CLOSED[shape]]
    face_curve(face, f'Closed{side}', pts, width, mats['line'], lift=0.014)


# На набросках рты крупнее, чем «по анатомии»: открытый — почти с глаз шириной.
LINE_SCALE = 1.3
OPEN_SCALE = 1.6


def mouth_box(points, pad=(1.3, 1.35)):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2,
            max((max(xs) - min(xs)) / 2 * pad[0], 0.05), max((max(ys) - min(ys)) / 2 * pad[1], 0.04))


def mouth_line(face, mats, name, pts, width=0.0085):
    pts = [(x * LINE_SCALE, MOUTH_Y + y * LINE_SCALE) for (x, y) in pts]
    face_curve(face, name, pts, width * 1.1, mats['line'])
    return pts


def mouth_open(face, mats, outline, tongue=None, fangs=False, philtrum=True):
    k = OPEN_SCALE
    poly = [(x * k, MOUTH_Y + y * k) for (x, y) in outline]
    face_patch(face, 'Mouth', poly, mats['mouth'], lift=0.009, dome=-0.004)
    if tongue:
        cx, cy, rx, ry = tongue
        face_patch(face, 'Tongue', ellipse2(cx * k, MOUTH_Y + cy * k, rx * k, ry * k, n=28), mats['tongue'],
                   lift=0.012, dome=0.005)
    if fangs:
        for s in (-1, 1):
            tip = [(0.068 * s * k, MOUTH_Y - 0.046 * k), (0.04 * s * k, MOUTH_Y - 0.046 * k),
                   (0.054 * s * k, MOUTH_Y - 0.078 * k)]
            face_patch(face, f'Fang{s}', tip, mats['tooth'], lift=0.0135)
    verts = [face.point(x, y, 0.013) for (x, y) in poly]
    tube('MouthLine', verts, 0.0062, mats['line'], cyclic=True)
    if philtrum:
        top = max(y for (x, y) in poly if abs(x) < 0.03 * k) if any(abs(x) < 0.03 * k for (x, y) in poly) else MOUTH_Y - 0.03
        face_curve(face, 'Philtrum', [(0.0, MOUTH_Y + 0.004), (0.0, top)], 0.0075, mats['line'], taper=False)
    return mouth_box(poly)


def smile_outline(scale=1.0):
    top = [(-0.085, -0.032), (-0.045, -0.05), (0.0, -0.03), (0.045, -0.05), (0.085, -0.032)]
    bottom = [(0.08, -0.09), (0.05, -0.138), (0.0, -0.156), (-0.05, -0.138), (-0.08, -0.09)]
    return [(x * scale, y * scale) for (x, y) in top + bottom]


MOUTHS = {}


def mouth(kind):
    def register(fn):
        MOUTHS[kind] = fn
        return fn
    return register


@mouth('omega')
def _m_omega(face, mats):
    pts = mouth_line(face, mats, 'Philtrum', [(0.0, 0.005), (0.0, -0.04)], 0.0075)
    for s in (-1, 1):
        pts += mouth_line(face, mats, f'Lip{s}', [(0.0, -0.04), (0.03 * s, -0.068), (0.066 * s, -0.06),
                                                  (0.085 * s, -0.04)])
    return mouth_box(pts)


@mouth('tiny')
def _m_tiny(face, mats):
    pts = mouth_line(face, mats, 'Philtrum', [(0.0, 0.005), (0.0, -0.03)], 0.0072)
    for s in (-1, 1):
        pts += mouth_line(face, mats, f'Lip{s}', [(0.0, -0.03), (0.024 * s, -0.05), (0.05 * s, -0.044),
                                                  (0.064 * s, -0.03)], 0.008)
    return mouth_box(pts)


@mouth('smile')
def _m_smile(face, mats):
    return mouth_open(face, mats, smile_outline(), tongue=(0.0, -0.118, 0.05, 0.032))


@mouth('grin')
def _m_grin(face, mats):
    return mouth_open(face, mats, smile_outline(1.12), tongue=(0.0, -0.132, 0.056, 0.036), fangs=True)


@mouth('frown')
def _m_frown(face, mats):
    pts = mouth_line(face, mats, 'Philtrum', [(0.0, 0.005), (0.0, -0.032)], 0.0075)
    for s in (-1, 1):
        pts += mouth_line(face, mats, f'Lip{s}', [(0.0, -0.032), (0.03 * s, -0.036), (0.056 * s, -0.052),
                                                  (0.07 * s, -0.07)])
    return mouth_box(pts)


@mouth('pout')
def _m_pout(face, mats):
    pts = mouth_line(face, mats, 'Philtrum', [(0.0, 0.005), (0.0, -0.03)], 0.0078)
    for s in (-1, 1):
        pts += mouth_line(face, mats, f'Lip{s}', [(0.0, -0.03), (0.036 * s, -0.036), (0.062 * s, -0.06),
                                                  (0.074 * s, -0.08)], 0.0095)
    return mouth_box(pts)


@mouth('o')
def _m_o(face, mats):
    return mouth_open(face, mats, ellipse2(0.0, -0.075, 0.038, 0.048, n=28), tongue=(0.0, -0.1, 0.026, 0.016),
                      philtrum=False)


@mouth('o_small')
def _m_o_small(face, mats):
    return mouth_open(face, mats, ellipse2(0.0, -0.05, 0.022, 0.03, n=24), tongue=(0.0, -0.066, 0.015, 0.009),
                      philtrum=False)


@mouth('wavy')
def _m_wavy(face, mats):
    top = [(-0.08, -0.05), (-0.055, -0.035), (-0.027, -0.05), (0.0, -0.036), (0.027, -0.05), (0.055, -0.035),
           (0.08, -0.05)]
    bottom = [(0.076, -0.08), (0.048, -0.102), (0.0, -0.11), (-0.048, -0.102), (-0.076, -0.08)]
    return mouth_open(face, mats, top + bottom, tongue=(0.0, -0.092, 0.04, 0.014))


@mouth('yawn')
def _m_yawn(face, mats):
    return mouth_open(face, mats, ellipse2(0.0, -0.088, 0.062, 0.078, n=32), tongue=(0.0, -0.134, 0.044, 0.026),
                      philtrum=True)


@mouth('smirk')
def _m_smirk(face, mats):
    pts = mouth_line(face, mats, 'Philtrum', [(0.0, 0.005), (0.0, -0.036)], 0.0075)
    pts += mouth_line(face, mats, 'Lip-1', [(0.0, -0.036), (-0.03, -0.062), (-0.064, -0.056), (-0.082, -0.036)])
    pts += mouth_line(face, mats, 'Lip1', [(0.0, -0.036), (0.03, -0.06), (0.068, -0.048), (0.09, -0.018)])
    k = LINE_SCALE
    tongue = ellipse2(0.03 * k, MOUTH_Y - 0.076 * k, 0.022 * k, 0.019 * k, n=24)
    face_patch(face, 'Tongue', tongue, mats['tongue'], lift=0.015, dome=0.006)
    return mouth_box(pts + tongue)


def tears(face, mats):
    for side in (-1, 1):
        inner = -side
        u = EYE_CX * side + (-0.62) * EYE_RX * inner
        v = EYE_CY - EYE_RY * 0.78
        drop = [(u + math.cos(a) * 0.04, v - 0.012 + math.sin(a) * 0.036) for a in
                [2 * math.pi * k / 24 for k in range(24)]]
        drop = [(x, y + (0.03 if y > v + 0.01 and abs(x - u) < 0.012 else 0.0)) for (x, y) in drop]
        face_patch(face, f'Tear{side}', drop, mats['tear'], lift=0.03, dome=0.018)
        face_patch(face, f'TearSpark{side}', ellipse2(u - 0.012, v + 0.002, 0.009, 0.008, n=12), mats['spark'],
                   lift=0.052)


def build_face(face, mats, spec):
    """Мордочка по описанию эмоции: глаза (открытые/закрытые), рот, нос, усы, слёзы."""
    footprints = []
    for side in (-1, 1):
        eye = spec['eyes'](side) if callable(spec['eyes']) else spec['eyes']
        if isinstance(eye, str):
            closed_eye(face, mats, side, eye)
            footprints.append((EYE_CX * side, EYE_CY, EYE_RX * 1.15, EYE_RY * 0.9))
        else:
            spec_eye = eye_open(side, **eye)
            build_eye(face, f'E{side}', spec_eye, mats)
            footprints.append((spec_eye['cx'] * side, spec_eye['cy'], spec_eye['rx'] * 1.2, spec_eye['ry'] * 1.18))
    face_patch(face, 'Nose', rounded_triangle(0.0, NOSE_Y, 0.1, 0.066), mats['nose'], lift=0.01, dome=0.016)
    footprints.append((0.0, NOSE_Y, 0.075, 0.05))
    footprints.append(MOUTHS[spec['mouth']](face, mats))
    if spec.get('tears'):
        tears(face, mats)
    whiskers(face, mats)
    return footprints


def whiskers(face, mats):
    for side in (-1, 1):
        for k, (v, tip_z, back) in enumerate(((-0.08, 0.12, 0.12), (-0.115, 0.0, 0.16), (-0.15, -0.13, 0.2))):
            root = face.point(0.27 * side, v, 0.0)
            R = face.R
            pts = [root,
                   root + R @ Vector((0.26 * side, back * 0.4, tip_z * 0.45 + 0.03)),
                   root + R @ Vector((0.55 * side, back, tip_z * 0.95)),
                   root + R @ Vector((0.78 * side, back * 1.3, tip_z * 1.25 - 0.06))]
            tube(f'Whisker{side}{k}', pts, 0.0042, mats['whisker'], [1.0, 0.8, 0.5, 0.18])


def mask_face_fur(fields, head, features):
    """Шерсть у глаз, носа и рта не растёт, вокруг — короче."""
    P = fields['P']
    L = local_coords(head, P)
    front = L[:, 1] < 0
    u, v = L[:, 0], L[:, 2]
    keep = np.ones(len(P))
    shorten = np.ones(len(P))
    for (cu, cv, ru, rv) in features:
        d = np.sqrt(((u - cu) / ru) ** 2 + ((v - cv) / rv) ** 2)
        keep = np.minimum(keep, np.where(front, smoothstep(1.0, 1.12, d), 1.0))
        shorten = np.minimum(shorten, np.where(front, 0.35 + 0.65 * smoothstep(1.0, 1.8, d), 1.0))
    fields['density'] *= keep
    fields['length'] *= shorten


# ---------------------------------------------------------------- эмоции

# Веки — прямые в нормированных координатах глаза (x — к носу): видно то, что ниже.
LID_ANGRY = [(-1.1, 0.62), (1.1, -0.02)]
LID_SAD = [(-1.1, 0.18), (1.1, 0.78)]
LID_WORRY = [(-1.1, 0.7), (1.1, 1.02)]
LID_SLY = [(-1.1, 0.32), (1.1, 0.16)]


def lid(points):
    (x1, y1), (x2, y2) = points
    return [(x1, y1, x2, y2)]


EMOTIONS = {
    # имя: (поза, мордочка)
    'normal': (lambda f: pose_sit(face=f), dict(eyes=dict(look=(0.0, 0.12)), mouth='omega')),
    'look-left': (lambda f: pose_sit(head_rot=(0, 0, -14), face=f),
                  dict(eyes=dict(look=(-0.3, 0.1)), mouth='omega')),
    'look-right': (lambda f: pose_sit(head_rot=(0, 0, 14), face=f),
                   dict(eyes=dict(look=(0.3, 0.1)), mouth='omega')),
    'blink': (lambda f: pose_sit(face=f), dict(eyes='asleep', mouth='omega')),
    'happy': (lambda f: pose_sit(head_rot=(-9, 5, 0), head_shift=(0, 0, 0.02), face=f),
              dict(eyes='happy', mouth='smile')),
    'excited': (lambda f: pose_excited(face=f),
                dict(eyes=dict(look=(0.0, 0.06), scale=1.06, star=(0.06, 0.16, 0.36),
                               sparks=[(-0.26, -0.28, 0.08), (0.3, -0.12, 0.05)]), mouth='grin')),
    'sad': (lambda f: pose_sit(head_rot=(10, 0, 0), head_shift=(0, -0.02, -0.04), lean=4, droop=0.8, face=f),
            dict(eyes=dict(look=(0.0, -0.02), lids=lid(LID_SAD)), mouth='frown', tears=True)),
    'angry': (lambda f: pose_stand(yaw=55, head_rot=(9, 0, 16), face=f),
              dict(eyes=dict(look=(0.06, 0.02), lids=lid(LID_ANGRY)), mouth='pout')),
    'surprised': (lambda f: pose_sit(head_rot=(-6, 0, 0), face=f),
                  dict(eyes=dict(look=(0.06, 0.2), scale=1.08), mouth='o')),
    'scared': (lambda f: pose_crouch(face=f),
               dict(eyes=dict(look=(0.0, 0.24), scale=1.06, lids=lid(LID_WORRY)), mouth='wavy')),
    'sleepy': (lambda f: pose_sit(head_rot=(-4, 7, 0), face=f, paw_up=dict(
                   side=1, path=[(0.22, -0.3, 0.78), (0.34, -0.64, 0.7), (0.24, -0.76, 0.96)],
                   paw=(0.22, -0.82, 1.0), paw_rot=(-90, 0, 180))),
               dict(eyes='sleepy', mouth='yawn')),
    'confused': (lambda f: pose_sit(head_rot=(-6, -12, 6), face=f),
                 dict(eyes=dict(look=(0.24, 0.22)), mouth='o_small')),
    'mischievous': (lambda f: pose_stand(yaw=55, head_rot=(2, 8, 20), paw_lift=True, face=f),
                    dict(eyes=lambda side: dict(look=(0.28, 0.0), lids=lid(LID_SLY)) if side < 0 else 'wink',
                         mouth='smirk')),
    'asleep': (lambda f: pose_curl(face=f), dict(eyes='asleep', mouth='tiny')),
    # Портрет для выбора облика и шапки панели (26–34 px): только голова спокойной позы.
    'portrait': (lambda f: pose_sit(face=f), dict(eyes=dict(look=(0.0, 0.12)), mouth='omega')),
}


# ---------------------------------------------------------------- сборка позы и рендер

def materials():
    return dict(
        skin=skin_material(),
        fur=fur_material(),
        eye=eye_material(),
        line=principled('Line', lin('#221714'), 0.45),
        spark=emission_material('Spark', (1.0, 1.0, 1.0), 1.0),
        nose=principled('Nose', lin('#f2a0ab'), 0.32, Coat_Weight=0.4, Coat_Roughness=0.15),
        mouth=principled('Mouth', lin('#7e2f36'), 0.5),
        tongue=principled('Tongue', lin('#f28d98'), 0.42, Coat_Weight=0.3),
        tooth=principled('Tooth', lin('#ffffff'), 0.3),
        whisker=principled('Whisker', lin('#eef0f4'), 0.35),
        tear=principled('Tear', lin('#8fd0f5'), 0.08, Coat_Weight=1.0, Coat_Roughness=0.02,
                        Emission_Color=lin('#bfe6ff'), Emission_Strength=0.15),
    )


def build_pose(parts, head, mats, face_spec):
    # Голова — отдельный меш поверх тела: щёчки нависают над грудкой, под подбородком
    # ложится тень, как на набросках (слитые метаболом, они давали «столбик»).
    body_parts = [p for p in parts if p is not head]
    body = build_body(body_parts, 'Body')
    fields = paint_body(body, body_parts)
    body.data.materials.append(mats['skin'])
    grow_fur('BodyFur', body, fields, mats['fur'])
    skull = build_body([head], 'Head')
    head_fields = paint_body(skull, [head])
    skull.data.materials.append(mats['skin'])
    face = Face(skull, head)
    footprint = build_face(face, mats, face_spec)
    mask_face_fur(head_fields, head, footprint)
    grow_fur('HeadFur', skull, head_fields, mats['fur'], seed=3)
    for side in (-1, 1):
        ear, ear_fields = build_ear(f'Ear{side}', head, side, head.params.get('droop', 0.0), mats)
        grow_fur(f'EarFur{side}', ear, ear_fields, mats['fur'], per_unit2=FUR_PER_UNIT2 * 0.8, seed=5 + side,
                 radius=0.0034)
    return body


def anchors(cam, head):
    """Опорные точки для значков эмоций на сайте: доли кадра (x вправо, y вниз)."""
    from bpy_extras.object_utils import world_to_camera_view
    scene = bpy.context.scene
    points = {
        'head': (0.0, 0.0, 0.0),
        'top': (0.0, 0.0, 0.66),
        'forehead': (0.26, -0.42, 0.36),
        'left': (-0.86, 0.0, 0.3),
        'right': (0.86, 0.0, 0.3),
        'mouth': (0.0, -0.6, -0.12),
    }
    out = {}
    for key, local in points.items():
        p = world_to_camera_view(scene, cam, head.frame @ Vector(local))
        out[key] = [round(p.x, 4), round(1.0 - p.y, 4)]
    return out


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    layout = {}
    layout_path = os.path.join(OUTDIR, 'layout.json')
    if os.path.exists(layout_path):
        with open(layout_path) as fh:
            layout = json.load(fh)
    for key, (pose_fn, face_spec) in EMOTIONS.items():
        if ONLY and key not in ONLY:
            continue
        reset_scene()
        studio_lights()
        cam = look_camera((0.0, -14.0, 3.2), (0.0, 0.0, 1.1), lens=105 * ZOOM)
        mats = materials()
        parts, head = pose_fn(default_face())
        body = build_pose(parts, head, mats, face_spec)
        if key == 'portrait':
            body.hide_render = True
            bpy.data.objects['BodyFur'].hide_render = True
            centre = head.frame @ Vector((0.0, 0.0, 0.08))
            cam.data.lens = 105 * 2.3
            cam.location = centre + (cam.location - Vector((0.0, 0.0, 1.1))).normalized() * 14.0
            render_to(key)
            continue
        render_to(key)
        layout[key] = anchors(cam, head)
        with open(layout_path, 'w') as fh:
            json.dump(layout, fh, indent=1, sort_keys=True)


if __name__ == '__main__':
    main()
