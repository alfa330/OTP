"""Хэллоуин-2026: модели для слоя украшений портала (src/components/seasonal/halloween).

Строит в Blender фоном, без окна, тыкву-фонарь рядом с маленькой тыквой,
летучую мышь (шесть кадров взмаха), привидение, паука и шляпу ведьмы и
рендерит каждую в PNG с прозрачным фоном (Cycles, на Mac — Metal). Затем
build_assets.py режет их по альфе и сжимает в WebP. Как собраны картинки
в assets/:

    B=/Applications/Blender.app/Contents/MacOS/Blender
    HW_SAMPLES=192 HW_SIZE=900 $B -b --factory-startup -P halloween_models.py -- out pumpkins ghost hat
    HW_SAMPLES=192 HW_SIZE=480 $B -b --factory-startup -P halloween_models.py -- out bat spider
    python build_assets.py out ../../src/components/seasonal/halloween/assets

Стиль — мягкие «эмодзи»-формы под iOS-вид портала; шляпа с лентой фирменного
индиго (#4f46e5), как кнопки сайта.
"""
import math
import os
import sys

import bmesh
import bpy
from mathutils import Euler, Matrix, Vector

ARGV = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
OUTDIR = ARGV[0] if ARGV else '/tmp/halloween'
ONLY = set(ARGV[1:])
SAMPLES = int(os.environ.get('HW_SAMPLES', '160'))


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
    scene.cycles.max_bounces = 8
    scene.render.film_transparent = True
    size = int(os.environ.get('HW_SIZE', '640'))
    scene.render.resolution_x = size
    scene.render.resolution_y = size
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
    bg.inputs['Color'].default_value = (0.72, 0.74, 0.86, 1.0)
    bg.inputs['Strength'].default_value = 0.32
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


def studio_lights(scale=1.0, energy=1.0):
    # Мягкий «эмодзи»-свет: большой ключ сверху слева, заполняющий справа, контровой сзади.
    add_area_light('Key', (-3.2 * scale, -4.2 * scale, 4.6 * scale), (0, 0, 0), 5.5 * scale, 720 * scale * scale * energy)
    add_area_light('Fill', (4.2 * scale, -3.0 * scale, 0.8 * scale), (0, 0, 0), 5.0 * scale, 260 * scale * scale * energy, (0.86, 0.9, 1.0))
    add_area_light('Rim', (1.2 * scale, 4.4 * scale, 3.4 * scale), (0, 0, 0), 3.0 * scale, 700 * scale * scale * energy, (0.9, 0.85, 1.0))


def look_camera(location, target, lens=70):
    data = bpy.data.cameras.new('Cam')
    data.lens = lens
    obj = bpy.data.objects.new('Cam', data)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    direction = Vector(target) - Vector(location)
    obj.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
    bpy.context.scene.camera = obj
    return obj


def frame_camera(cam, objects, margin=1.06):
    """Подбирает расстояние камеры по проекции габаритов объектов (несколько итераций)."""
    from bpy_extras.object_utils import world_to_camera_view
    scene = bpy.context.scene
    deps = bpy.context.evaluated_depsgraph_get()
    pts = []
    for obj in objects:
        ev = obj.evaluated_get(deps)
        for corner in ev.bound_box:
            pts.append(ev.matrix_world @ Vector(corner))
    center = sum(pts, Vector()) / len(pts)
    forward = cam.matrix_world.to_quaternion() @ Vector((0, 0, -1))
    dist = 10.0
    for _ in range(5):
        cam.location = center - forward * dist
        bpy.context.view_layer.update()
        uvs = [world_to_camera_view(scene, cam, p) for p in pts]
        half = max(max(u.x for u in uvs) - 0.5, 0.5 - min(u.x for u in uvs),
                   max(u.y for u in uvs) - 0.5, 0.5 - min(u.y for u in uvs))
        dist *= half / (0.5 / margin)


def render_to(name):
    scene = bpy.context.scene
    scene.render.filepath = f'{OUTDIR}/{name}.png'
    bpy.ops.render.render(write_still=True)
    print('RENDERED', name)


# ---------------------------------------------------------------- материалы

def principled(name, color, roughness=0.5, **inputs):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    node = next(n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    node.inputs['Base Color'].default_value = (*color, 1.0)
    node.inputs['Roughness'].default_value = roughness
    for key, value in inputs.items():
        socket = node.inputs[key.replace('_', ' ')]
        if isinstance(value, tuple) and len(value) == 3 and socket.type == 'RGBA':
            value = (*value, 1.0)
        socket.default_value = value
    return mat


def attribute_material(name, attr, roughness=0.5, **inputs):
    """Цвет из атрибута вершин (тёмные бороздки тыквы считаем в bmesh)."""
    mat = principled(name, (1, 1, 1), roughness, **inputs)
    nodes = mat.node_tree.nodes
    node = next(n for n in nodes if n.type == 'BSDF_PRINCIPLED')
    attr_node = nodes.new('ShaderNodeAttribute')
    attr_node.attribute_name = attr
    mat.node_tree.links.new(attr_node.outputs['Color'], node.inputs['Base Color'])
    return mat


def emission_material(name, color, strength):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    for n in list(nodes):
        nodes.remove(n)
    out = nodes.new('ShaderNodeOutputMaterial')
    emi = nodes.new('ShaderNodeEmission')
    emi.inputs['Color'].default_value = (*color, 1.0)
    emi.inputs['Strength'].default_value = strength
    mat.node_tree.links.new(emi.outputs['Emission'], out.inputs['Surface'])
    return mat


def srgb(hex_color):
    hex_color = hex_color.lstrip('#')
    vals = [int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in vals)


def mix(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


# ---------------------------------------------------------------- утилиты меша

def mesh_object(name, bm):
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    for poly in mesh.polygons:
        poly.use_smooth = True
    return obj


def add_subsurf(obj, levels=2):
    mod = obj.modifiers.new('Subsurf', 'SUBSURF')
    mod.levels = levels
    mod.render_levels = levels
    return mod


def apply_modifiers(obj):
    bpy.context.view_layer.objects.active = obj
    for mod in list(obj.modifiers):
        with bpy.context.temp_override(object=obj, active_object=obj):
            bpy.ops.object.modifier_apply(modifier=mod.name)


def uv_sphere(segments=64, rings=32, radius=1.0):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segments, v_segments=rings, radius=radius)
    return bm


def set_color_attr(obj, fn):
    mesh = obj.data
    attr = mesh.color_attributes.new('Col', 'FLOAT_COLOR', 'POINT')
    for i, v in enumerate(mesh.vertices):
        attr.data[i].color = (*fn(v.co), 1.0)


# ---------------------------------------------------------------- тыква

PUMPKIN_LIGHT = srgb('#ff7b0f')
PUMPKIN_DARK = srgb('#b33a07')


def groove(theta, lobes):
    """0 на середине дольки, 1 в бороздке."""
    step = 2 * math.pi / lobes
    d = abs(((theta + step / 2) % step) - step / 2) / (step / 2)
    return max(0.0, 1.0 - d) ** 6


def pumpkin_body(name, radius=1.0, lobes=9, squash=0.78, groove_depth=0.075, seed=0.0,
                 palette=None, skin=None):
    bm = uv_sphere(72, 40, radius)
    for v in bm.verts:
        x, y, z = v.co
        rho = math.hypot(x, y)
        theta = math.atan2(y, x) + seed
        g = groove(theta, lobes)
        # Дольки: бороздка вдавлена, у полюсов слабее.
        polar = rho / radius
        k = 1.0 - groove_depth * g * (0.35 + 0.65 * polar)
        # Лёгкая неровность долек — живая, не штампованная тыква.
        k *= 1.0 + 0.018 * math.sin(lobes * theta * 0.5 + 1.3 + seed * 3)
        x, y = x * k, y * k
        z = z * squash
        # Ямка у хвостика и снизу.
        dimple = 0.16 * radius * math.exp(-(polar / 0.32) ** 2)
        z -= math.copysign(dimple, z) if abs(z) > 0.2 * radius else 0.0
        v.co = (x, y, z)
    obj = mesh_object(name, bm)
    add_subsurf(obj, 1)

    def color(co):
        theta = math.atan2(co.y, co.x) + seed
        rho = math.hypot(co.x, co.y) / radius
        g = groove(theta, lobes)
        top = max(0.0, co.z / (radius * squash))
        t = min(1.0, 0.75 * g + 0.25 * (1 - rho) + 0.12 * top)
        light, dark = palette or (PUMPKIN_LIGHT, PUMPKIN_DARK)
        return mix(light, dark, t)

    set_color_attr(obj, color)
    obj.data.materials.append(attribute_material(f'{name}Skin', 'Col', 0.46,
                                                 **(skin or dict(Coat_Weight=0.14, Coat_Roughness=0.35,
                                                                 Subsurface_Weight=0.04))))
    return obj


def pumpkin_stem(name, base_z, height=0.42, radius=0.11, bend=0.35, twist=0.6, color='#6b5a2e', curl=0.0):
    bm = bmesh.new()
    rings, segs = 10, 10
    prev = None
    rows = []
    for r in range(rings + 1):
        t = r / rings
        ring = []
        cx = bend * height * (t ** 2)
        cz = base_z + height * t
        if curl:
            # Завиток на конце, как на набросках владельца: кончик загибается вниз.
            k = max(0.0, (t - 0.55) / 0.45)
            cx += curl * height * 0.55 * math.sin(k * math.pi * 0.9)
            cz -= curl * height * 0.5 * (1 - math.cos(k * math.pi * 0.9))
        rad = radius * (1.0 - 0.38 * t) * (1.25 - 0.25 * min(1.0, t * 4))
        for s in range(segs):
            a = 2 * math.pi * s / segs + twist * t
            ridge = 1.0 + 0.12 * (1 if s % 2 == 0 else -1)
            ring.append(bm.verts.new((cx + math.cos(a) * rad * ridge, math.sin(a) * rad * ridge, cz)))
        rows.append(ring)
    for r in range(rings):
        for s in range(segs):
            a, b = rows[r][s], rows[r][(s + 1) % segs]
            c, d = rows[r + 1][(s + 1) % segs], rows[r + 1][s]
            bm.faces.new((a, b, c, d))
    bm.faces.new(rows[-1])
    bm.faces.new(list(reversed(rows[0])))
    obj = mesh_object(name, bm)
    add_subsurf(obj, 2)
    obj.data.materials.append(principled(f'{name}Mat', srgb(color), 0.75,
                                         Coat_Weight=0.05))
    return obj


def tendril(name, origin, turns=2.3, length=0.55, radius=0.16, thickness=0.018, color='#4d7c0f', roughness=0.55):
    curve = bpy.data.curves.new(name, 'CURVE')
    curve.dimensions = '3D'
    curve.bevel_depth = thickness
    curve.bevel_resolution = 4
    spline = curve.splines.new('POLY')
    pts = 60
    spline.points.add(pts - 1)
    ox, oy, oz = origin
    for i in range(pts):
        t = i / (pts - 1)
        a = turns * 2 * math.pi * t
        r = radius * (1.0 - 0.6 * t)
        spline.points[i].co = (ox + length * t + math.cos(a) * r * 0.6 - 0.1,
                               oy - math.sin(a) * r, oz + 0.05 + 0.25 * t * (1 - t) + math.sin(a) * r * 0.5, 1)
    obj = bpy.data.objects.new(name, curve)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(principled(f'{name}Mat', srgb(color), roughness))
    return obj


def leaf(name, location, rotation, size=0.42, color='#3f7d20'):
    bm = bmesh.new()
    pts = []
    n = 28
    for i in range(n):
        a = 2 * math.pi * i / n
        # Лопасти листа тыквы.
        r = size * (0.62 + 0.38 * abs(math.cos(2.5 * a))) * (0.55 + 0.45 * math.sin(a / 2 + 0.2))
        pts.append(bm.verts.new((math.cos(a) * r, math.sin(a) * r * 0.85, 0.06 * math.sin(3 * a))))
    face = bm.faces.new(pts)
    bmesh.ops.triangulate(bm, faces=[face])
    obj = mesh_object(name, bm)
    sol = obj.modifiers.new('Solid', 'SOLIDIFY')
    sol.thickness = 0.015
    add_subsurf(obj, 2)
    obj.location = location
    obj.rotation_euler = rotation
    obj.data.materials.append(principled(f'{name}Mat', srgb(color), 0.5, Sheen_Weight=0.2))
    return obj


def smile_upper(x):
    return -0.18 - 0.12 * (1 - (x / 0.52) ** 2)


def smile_lower(x):
    return -0.18 - 0.36 * (1 - (x / 0.52) ** 2)


def lantern_face_cutters(radius, mat):
    """Глаза-треугольники, нос и улыбка с двумя зубами — по плоскости XZ, сквозь перед."""
    shapes = []
    for cx in (-0.33, 0.33):
        shapes.append([(cx - 0.17, 0.08), (cx + 0.17, 0.08), (cx + 0.02 * (1 if cx > 0 else -1), 0.36)])
    shapes.append([(-0.075, -0.07), (0.075, -0.07), (0.0, 0.05)])
    n = 22
    upper = [(-0.52 + 1.04 * i / n, smile_upper(-0.52 + 1.04 * i / n)) for i in range(n + 1)]
    lower = [(0.52 - 1.04 * i / n, smile_lower(0.52 - 1.04 * i / n)) for i in range(1, n)]
    shapes.append(upper + lower)
    cutters = []
    for idx, poly in enumerate(shapes):
        bm = bmesh.new()
        front = [bm.verts.new((x * radius, -2.0 * radius, z * radius)) for x, z in poly]
        back = [bm.verts.new((x * radius, 0.0, z * radius)) for x, z in poly]
        bm.faces.new(front)
        bm.faces.new(list(reversed(back)))
        k = len(poly)
        for i in range(k):
            bm.faces.new((front[i], front[(i + 1) % k], back[(i + 1) % k], back[i]))
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        obj = mesh_object(f'Cut{idx}', bm)
        obj.data.materials.append(mat)
        cutters.append(obj)
    # Зубы — отдельные бруски, которые ВЫЧИТАЮТСЯ из выреза (их добавляем обратно в тело).
    tooth_objs = []
    # Верхний зуб свисает с верхней кромки, нижний растёт от нижней.
    for tx, z_lo, z_hi in ((-0.17, smile_upper(-0.17) - 0.1, smile_upper(-0.17) + 0.04),
                           (0.18, smile_lower(0.18) - 0.04, smile_lower(0.18) + 0.1)):
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        for v in bm.verts:
            v.co.x *= 0.11 * radius
            v.co.y *= 2.4 * radius
            v.co.z *= (z_hi - z_lo) * radius
        obj = mesh_object(f'Tooth{tx}', bm)
        obj.location = (tx * radius, -1.0 * radius, (z_lo + z_hi) / 2 * radius)
        tooth_objs.append(obj)
    return cutters, tooth_objs


def jack_o_lantern(name, radius=1.0, location=(0, 0, 0), seed=0.0):
    body = pumpkin_body(name, radius, seed=seed)
    apply_modifiers(body)
    flesh = principled(f'{name}Flesh', srgb('#ffb547'), 0.6,
                       Emission_Color=srgb('#ff8a12'), Emission_Strength=1.6)
    body.data.materials.append(flesh)
    sol = body.modifiers.new('Shell', 'SOLIDIFY')
    sol.thickness = 0.09 * radius
    sol.offset = -1.0
    sol.material_offset = 1
    sol.material_offset_rim = 1
    apply_modifiers(body)

    cutters, teeth = lantern_face_cutters(radius, flesh)
    mouth = cutters[-1]
    # Зубы оставляем в мякоти: вычитаем их из выреза рта до вырезания.
    for tooth in teeth:
        mod = mouth.modifiers.new('TeethOut', 'BOOLEAN')
        mod.operation = 'DIFFERENCE'
        mod.solver = 'EXACT'
        mod.object = tooth
        apply_modifiers(mouth)
        bpy.data.objects.remove(tooth)
    for cutter in cutters:
        mod = body.modifiers.new('Carve', 'BOOLEAN')
        mod.operation = 'DIFFERENCE'
        mod.solver = 'EXACT'
        mod.object = cutter
        try:
            mod.material_mode = 'TRANSFER'
        except (AttributeError, TypeError):
            pass
        apply_modifiers(body)
        bpy.data.objects.remove(cutter)
    for poly in body.data.polygons:
        poly.use_smooth = True
    # Свет изнутри: тёплое ядро и точечный источник.
    core = mesh_object(f'{name}Core', uv_sphere(24, 12, 0.55 * radius))
    core.scale = (1.0, 1.0, 0.7)
    core.data.materials.append(emission_material(f'{name}CoreMat', srgb('#ffc53d'), 5.5))
    lamp = bpy.data.lights.new(f'{name}Lamp', 'POINT')
    lamp.energy = 260 * radius * radius
    lamp.color = (1.0, 0.62, 0.22)
    lamp.shadow_soft_size = 0.2 * radius
    lamp_obj = bpy.data.objects.new(f'{name}Lamp', lamp)
    bpy.context.scene.collection.objects.link(lamp_obj)
    lamp_obj.location = (0, 0.1 * radius, -0.05 * radius)

    stem = pumpkin_stem(f'{name}Stem', 0.58 * radius, height=0.36 * radius,
                        radius=0.1 * radius, bend=0.3)
    group = [body, core, lamp_obj, stem]
    root = bpy.data.objects.new(f'{name}Root', None)
    bpy.context.scene.collection.objects.link(root)
    for obj in group:
        obj.parent = root
    root.location = location
    return root, [body, stem]


def small_pumpkin(name, radius=0.62, location=(0, 0, 0), seed=0.7):
    body = pumpkin_body(name, radius, lobes=8, squash=0.74, seed=seed)
    stem = pumpkin_stem(f'{name}Stem', 0.5 * radius, height=0.42 * radius,
                        radius=0.11 * radius, bend=-0.5, twist=-0.8)
    vine = tendril(f'{name}Vine', (0.05 * radius, 0.0, 0.6 * radius), length=0.5 * radius,
                   radius=0.22 * radius, thickness=0.02 * radius)
    lf = leaf(f'{name}Leaf', (-0.32 * radius, -0.18 * radius, 0.62 * radius),
              Euler((math.radians(18), math.radians(-28), math.radians(30))), size=0.5 * radius)
    root = bpy.data.objects.new(f'{name}Root', None)
    bpy.context.scene.collection.objects.link(root)
    for obj in (body, stem, vine, lf):
        obj.parent = root
    root.location = location
    return root, [body, stem, vine, lf]


# ---------------------------------------------------------------- сцены

def scene_pumpkins():
    reset_scene()
    studio_lights()
    r1 = 1.0
    lantern_root, lantern_parts = jack_o_lantern('Lantern', r1, (0.0, 0.0, 0.0))
    lantern_root.rotation_euler = (0, 0, math.radians(-8))
    small_root, small_parts = small_pumpkin('Small', 0.6, (1.42, -0.42, 0.0), seed=0.4)
    small_root.rotation_euler = (0, 0, math.radians(25))
    bpy.context.view_layer.update()
    # Ставим обе на пол (z=0 — низ тел).
    for root, parts in ((lantern_root, lantern_parts), (small_root, small_parts)):
        deps = bpy.context.evaluated_depsgraph_get()
        zmin = min((p.evaluated_get(deps).matrix_world @ Vector(c)).z
                   for p in parts[:1] for c in p.evaluated_get(deps).bound_box)
        root.location.z -= zmin
    cam = look_camera((0.9, -7.0, 2.2), (0.55, 0, 0.55), lens=60)
    bpy.context.view_layer.update()
    frame_camera(cam, lantern_parts + small_parts, margin=1.1)
    render_to('pumpkins')


# ---------------------------------------------------------------- общие формы

def ellipsoid(name, radius, scale, location, mat, segments=32, rings=16):
    obj = mesh_object(name, uv_sphere(segments, rings, radius))
    obj.scale = scale
    obj.location = location
    obj.data.materials.append(mat)
    return obj


def tube(name, points, thickness, mat, taper_end=1.0):
    curve = bpy.data.curves.new(name, 'CURVE')
    curve.dimensions = '3D'
    curve.bevel_depth = thickness
    curve.bevel_resolution = 4
    curve.use_fill_caps = True
    spline = curve.splines.new('BEZIER')
    spline.bezier_points.add(len(points) - 1)
    for bp, co in zip(spline.bezier_points, points):
        bp.co = co
        bp.handle_left_type = 'AUTO'
        bp.handle_right_type = 'AUTO'
    for i, bp in enumerate(spline.bezier_points):
        bp.radius = 1.0 + (taper_end - 1.0) * (i / max(1, len(points) - 1))
    obj = bpy.data.objects.new(name, curve)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(mat)
    return obj


def parent_all(name, objects, location=(0, 0, 0)):
    root = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(root)
    for obj in objects:
        obj.parent = root
    root.location = location
    return root


def cute_eye(prefix, location, radius, look=(0.0, -1.0, 0.0), white=None, black=None, spark=None):
    """Белок, зрачок и блик — глаз «как в эмодзи»."""
    lx, ly, lz = location
    eye = ellipsoid(f'{prefix}White', radius, (1, 0.7, 1.15), location, white)
    lk = Vector(look).normalized()
    pupil_loc = (lx + lk.x * radius * 0.25, ly - radius * 0.62, lz + lk.z * radius * 0.25 - radius * 0.08)
    pupil = ellipsoid(f'{prefix}Pupil', radius * 0.62, (1, 0.6, 1.15), pupil_loc, black)
    spark_loc = (pupil_loc[0] - radius * 0.2, pupil_loc[1] - radius * 0.33, pupil_loc[2] + radius * 0.28)
    sp = ellipsoid(f'{prefix}Spark', radius * 0.2, (1, 0.6, 1), spark_loc, spark, 16, 8)
    return [eye, pupil, sp]


def eye_materials():
    white = principled('EyeWhite', srgb('#ffffff'), 0.2, Coat_Weight=0.6)
    black = principled('EyeBlack', srgb('#14111c'), 0.15, Coat_Weight=0.8)
    spark = emission_material('EyeSpark', (1, 1, 1), 3.0)
    return white, black, spark


# ---------------------------------------------------------------- летучая мышь

def quad_bezier(p0, p1, p2, n):
    pts = []
    for i in range(n):
        t = i / n
        a = (1 - t) ** 2
        b = 2 * (1 - t) * t
        c = t ** 2
        pts.append((a * p0[0] + b * p1[0] + c * p2[0], a * p0[1] + b * p1[1] + c * p2[1]))
    return pts


def bat_wing_outline():
    shoulder = (0.26, 0.16)
    wrist = (0.86, 0.5)
    tip0 = (1.5, 0.2)
    tip1 = (1.2, -0.2)
    tip2 = (0.82, -0.22)
    hip = (0.28, -0.16)
    pts = []
    pts += quad_bezier(shoulder, (0.5, 0.48), wrist, 8)
    pts += quad_bezier(wrist, (1.25, 0.5), tip0, 8)
    # Фестоны: кромка между пальцами выгибается внутрь.
    pts += quad_bezier(tip0, (1.22, 0.06), tip1, 8)
    pts += quad_bezier(tip1, (0.98, 0.02), tip2, 8)
    pts += quad_bezier(tip2, (0.56, 0.02), hip, 8)
    pts += quad_bezier(hip, (0.22, 0.0), shoulder, 6)
    return pts, wrist, (tip0, tip1, tip2)


def bat_wing(name, side, membrane, bone_mat):
    pts, wrist, tips = bat_wing_outline()
    bm = bmesh.new()
    verts = [bm.verts.new((side * x, 0.0, z)) for x, z in pts]
    edges = [bm.edges.new((verts[i], verts[(i + 1) % len(verts)])) for i in range(len(verts))]
    bmesh.ops.triangle_fill(bm, use_beauty=True, use_dissolve=False, edges=edges)
    # Лёгкий прогиб перепонки — не плоский лист.
    for v in bm.verts:
        x = abs(v.co.x)
        v.co.y += 0.06 * math.sin(min(1.0, (x - 0.26) / 1.24) * math.pi)
    wing = mesh_object(name, bm)
    sol = wing.modifiers.new('Solid', 'SOLIDIFY')
    sol.thickness = 0.03
    sol.offset = 0.0
    wing.data.materials.append(membrane)
    bones = [tube(f'{name}Arm', [(side * 0.26, -0.01, 0.16), (side * 0.55, -0.02, 0.42), (side * wrist[0], -0.02, wrist[1])], 0.028, bone_mat, 0.8)]
    for i, (tx, tz) in enumerate(tips):
        bones.append(tube(f'{name}Finger{i}', [(side * wrist[0], -0.02, wrist[1]),
                                               (side * (wrist[0] + tx) / 2, -0.03, (wrist[1] + tz) / 2 + 0.03),
                                               (side * tx, -0.02, tz)], 0.017, bone_mat, 0.5))
    pivot = bpy.data.objects.new(f'{name}Pivot', None)
    bpy.context.scene.collection.objects.link(pivot)
    pivot.location = (side * 0.26, 0.0, 0.05)
    inverse = Matrix.Translation(pivot.location).inverted()
    for obj in [wing] + bones:
        obj.parent = pivot
        obj.matrix_parent_inverse = inverse
    return pivot, [wing] + bones


def build_bat():
    fur = principled('BatFur', srgb('#3a3150'), 0.62, Sheen_Weight=0.7, Sheen_Tint=srgb('#c4b5fd'),
                     Sheen_Roughness=0.4, Coat_Weight=0.1)
    membrane = principled('BatWing', srgb('#4a3d66'), 0.55, Subsurface_Weight=0.25,
                          Subsurface_Radius=(0.6, 0.2, 0.8), Sheen_Weight=0.3, Sheen_Tint=srgb('#a78bfa'))
    bone = principled('BatBone', srgb('#2a2338'), 0.5)
    inner_ear = principled('BatEar', srgb('#c084fc'), 0.5, Sheen_Weight=0.3)
    white, black, spark = eye_materials()
    fang_mat = principled('BatFang', srgb('#ffffff'), 0.3)
    parts = []
    body = ellipsoid('BatBody', 0.42, (1.0, 0.86, 1.08), (0, 0, 0), fur, 48, 24)
    parts.append(body)
    for side in (-1, 1):
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, segments=24, radius1=0.15, radius2=0.0, depth=0.36)
        ear = mesh_object(f'BatEar{side}', bm)
        add_subsurf(ear, 1)
        ear.data.materials.append(fur)
        ear.location = (side * 0.2, 0.0, 0.48)
        ear.rotation_euler = (0, side * math.radians(-22), 0)
        parts.append(ear)
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=0.085, radius2=0.0, depth=0.22)
        inner = mesh_object(f'BatEarIn{side}', bm)
        add_subsurf(inner, 1)
        inner.data.materials.append(inner_ear)
        inner.location = (side * 0.205, -0.07, 0.45)
        inner.rotation_euler = (math.radians(-6), side * math.radians(-22), 0)
        parts.append(inner)
        parts += cute_eye(f'BatEye{side}', (side * 0.15, -0.3, 0.1), 0.1, white=white, black=black, spark=spark)
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, segments=12, radius1=0.028, radius2=0.0, depth=0.07)
        fang = mesh_object(f'BatFang{side}', bm)
        fang.data.materials.append(fang_mat)
        fang.location = (side * 0.055, -0.355, -0.11)
        fang.rotation_euler = (math.radians(180 - 12), 0, 0)
        parts.append(fang)
    pivots = {}
    wing_all = []
    for side in (-1, 1):
        pivot, wing_parts = bat_wing(f'BatWing{side}', side, membrane, bone)
        pivots[side] = pivot
        wing_all += wing_parts
    root = parent_all('Bat', parts + list(pivots.values()))
    return root, pivots, parts + wing_all


def scene_bat():
    reset_scene()
    studio_lights()
    root, pivots, parts = build_bat()
    root.rotation_euler = (math.radians(8), 0, math.radians(18))
    cam = look_camera((0.0, -7.0, 0.9), (0, 0, 0.1), lens=70)
    angles = [38, 20, -14, -36, -18, 16]
    # Кадрируем по самому широкому положению крыльев, чтобы все кадры совпадали.
    for side in (-1, 1):
        pivots[side].rotation_euler = (0, 0, 0)
    bpy.context.view_layer.update()
    frame_camera(cam, parts, margin=1.18)
    for i, angle in enumerate(angles):
        for side in (-1, 1):
            # Правое крыло поднимается вращением вокруг -Y, левое — вокруг +Y; чуть вперёд по Z.
            pivots[side].rotation_euler = (0, side * math.radians(-angle), side * math.radians(-6))
        body_lift = -0.035 * math.sin(math.radians(angle))
        root.location.z = body_lift
        render_to(f'bat-{i}')


# ---------------------------------------------------------------- привидение

def build_ghost():
    sheet = principled('GhostSheet', srgb('#f7f7ff'), 0.32, Subsurface_Weight=0.45,
                       Subsurface_Radius=(0.7, 0.75, 1.0), Coat_Weight=0.25, Coat_Roughness=0.25)
    bm = uv_sphere(72, 44, 1.0)
    for v in bm.verts:
        x, y, z = v.co
        theta = math.atan2(y, x)
        if z < 0:
            depth = -z
            if depth <= 0.82:
                # Юбка: радиус не сходится к полюсу, а чуть расширяется книзу.
                u = depth / 0.82
                rad = 1.0 + 0.24 * u ** 1.5
                z = -1.45 * u
            else:
                # Дно подвёрнуто внутрь — снизу видно изнанку простыни.
                u2 = (depth - 0.82) / 0.18
                rad = 1.24 * (1.0 - u2)
                z = -1.45 + 0.3 * u2
            hem = max(0.0, (depth - 0.55) / 0.45)
            z += 0.2 * hem ** 1.5 * math.sin(6 * theta + 0.6)
            x, y = math.cos(theta) * rad, math.sin(theta) * rad
        v.co = (x, y, z)
    body = mesh_object('GhostBody', bm)
    add_subsurf(body, 1)
    body.data.materials.append(sheet)
    white, black, spark = eye_materials()
    parts = [body]
    for side in (-1, 1):
        eye = ellipsoid(f'GhostEye{side}', 0.11, (0.85, 0.5, 1.3), (side * 0.27, -0.93, 0.22), black, 32, 16)
        sp = ellipsoid(f'GhostSpark{side}', 0.03, (1, 0.6, 1), (side * 0.27 - 0.035, -0.995, 0.3), spark, 12, 6)
        blush = ellipsoid(f'GhostBlush{side}', 0.1, (1.3, 0.25, 0.7), (side * 0.5, -0.855, 0.02),
                          principled(f'Blush{side}', srgb('#fda4af'), 0.6), 24, 12)
        arm = ellipsoid(f'GhostArm{side}', 0.24, (1.35, 0.75, 0.62), (side * 1.02, -0.05, -0.42), sheet, 32, 16)
        arm.rotation_euler = (0, side * math.radians(-28), 0)
        parts += [eye, sp, blush, arm]
    mouth = ellipsoid('GhostMouth', 0.075, (1.0, 0.45, 1.25), (0.0, -0.985, -0.06), black, 24, 12)
    parts.append(mouth)
    root = parent_all('Ghost', parts)
    return root, parts


def scene_ghost():
    reset_scene()
    studio_lights()
    root, parts = build_ghost()
    root.rotation_euler = (0, math.radians(-9), math.radians(-14))
    cam = look_camera((0.0, -7.0, 1.2), (0, 0, -0.2), lens=70)
    bpy.context.view_layer.update()
    frame_camera(cam, parts, margin=1.12)
    render_to('ghost')


# ---------------------------------------------------------------- паук

def build_spider():
    shell = principled('SpiderShell', srgb('#231d33'), 0.3, Coat_Weight=0.7, Coat_Roughness=0.15,
                       Sheen_Weight=0.4, Sheen_Tint=srgb('#a78bfa'))
    leg_mat = principled('SpiderLeg', srgb('#1c1729'), 0.4, Coat_Weight=0.4)
    white, black, spark = eye_materials()
    parts = []
    abdomen = ellipsoid('SpiderAbdomen', 0.5, (1.0, 0.9, 1.06), (0, 0.08, 0.38), shell, 48, 24)
    head = ellipsoid('SpiderHead', 0.36, (1.0, 0.92, 0.95), (0, -0.12, -0.2), shell, 48, 24)
    parts += [abdomen, head]
    for side in (-1, 1):
        parts += cute_eye(f'SpiderEye{side}', (side * 0.13, -0.42, -0.12), 0.105,
                          look=(0, -1, -0.4), white=white, black=black, spark=spark)
        legs = (
            ((0.24, -0.26, -0.02), (0.6, -0.42, 0.26), (0.74, -0.62, -0.58)),
            ((0.3, -0.08, 0.04), (0.82, -0.14, 0.5), (1.06, -0.2, -0.34)),
            ((0.3, 0.1, 0.1), (0.86, 0.12, 0.6), (1.16, 0.16, -0.08)),
            ((0.26, 0.28, 0.16), (0.72, 0.42, 0.56), (1.0, 0.56, 0.18)),
        )
        for i, (hip, knee, foot) in enumerate(legs):
            pts = [(side * hip[0], hip[1], hip[2]), (side * knee[0], knee[1], knee[2]), (side * foot[0], foot[1], foot[2])]
            parts.append(tube(f'SpiderLeg{side}{i}', pts, 0.05, leg_mat, 0.5))
    # Улыбка — тонкая дуга.
    parts.append(tube('SpiderSmile', [(-0.07, -0.47, -0.3), (0.0, -0.49, -0.33), (0.07, -0.47, -0.3)], 0.012, black))
    root = parent_all('Spider', parts)
    return root, parts


def scene_spider():
    reset_scene()
    studio_lights()
    root, parts = build_spider()
    root.rotation_euler = (math.radians(6), 0, 0)
    cam = look_camera((0.0, -7.0, 1.6), (0, 0, 0.0), lens=70)
    bpy.context.view_layer.update()
    frame_camera(cam, parts, margin=1.1)
    render_to('spider')


# ---------------------------------------------------------------- шляпа ведьмы

def build_hat():
    felt = principled('HatFelt', srgb('#2b2140'), 0.62, Sheen_Weight=0.5, Sheen_Tint=srgb('#8b5cf6'),
                      Sheen_Roughness=0.5)
    band = principled('HatBand', srgb('#4f46e5'), 0.45, Sheen_Weight=0.3, Coat_Weight=0.15)
    gold = principled('HatGold', srgb('#f5c542'), 0.28, Metallic=1.0)
    parts = []
    # Поля: диск с подвёрнутой волной кромкой.
    bm = bmesh.new()
    rings, segs = 10, 72
    rows = []
    for r in range(rings + 1):
        rad = 0.2 + 0.85 * r / rings
        row = []
        for s in range(segs):
            a = 2 * math.pi * s / segs
            t = r / rings
            z = 0.12 * t ** 3 + 0.035 * math.sin(3 * a + 0.4) * t ** 2
            row.append(bm.verts.new((math.cos(a) * rad, math.sin(a) * rad, z)))
        rows.append(row)
    for r in range(rings):
        for s in range(segs):
            bm.faces.new((rows[r][s], rows[r][(s + 1) % segs], rows[r + 1][(s + 1) % segs], rows[r + 1][s]))
    brim = mesh_object('HatBrim', bm)
    sol = brim.modifiers.new('Solid', 'SOLIDIFY')
    sol.thickness = 0.05
    add_subsurf(brim, 2)
    brim.data.materials.append(felt)
    parts.append(brim)
    # Тулья: конус с заломом, макушка свисает вбок.
    bm = bmesh.new()
    rings, segs = 26, 48
    rows = []
    height = 1.55
    for r in range(rings + 1):
        t = r / rings
        rad = 0.52 * (1 - t) ** 0.95 + 0.012
        bendt = max(0.0, (t - 0.45) / 0.55)
        cx = 0.42 * bendt ** 2
        cz = height * t - 0.32 * bendt ** 2.2
        row = []
        for s in range(segs):
            a = 2 * math.pi * s / segs
            wob = 1.0 + 0.045 * math.sin(5 * a + 7 * t) * t
            row.append(bm.verts.new((cx + math.cos(a) * rad * wob, math.sin(a) * rad * wob, cz)))
        rows.append(row)
    for r in range(rings):
        for s in range(segs):
            bm.faces.new((rows[r][s], rows[r][(s + 1) % segs], rows[r + 1][(s + 1) % segs], rows[r + 1][s]))
    bm.faces.new(list(reversed(rows[0])))
    tip = bm.verts.new((rows[-1][0].co.x - 0.01, 0.0, rows[-1][0].co.z))
    for s in range(segs):
        bm.faces.new((rows[-1][s], rows[-1][(s + 1) % segs], tip))
    crown = mesh_object('HatCrown', bm)
    add_subsurf(crown, 1)
    crown.data.materials.append(felt)
    parts.append(crown)
    # Лента и пряжка.
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=False, segments=64, radius1=0.535, radius2=0.47, depth=0.17)
    ribbon = mesh_object('HatRibbon', bm)
    sol = ribbon.modifiers.new('Solid', 'SOLIDIFY')
    sol.thickness = 0.03
    add_subsurf(ribbon, 1)
    ribbon.location = (0, 0, 0.13)
    ribbon.data.materials.append(band)
    parts.append(ribbon)
    bm = bmesh.new()
    outer, inner = 0.12, 0.068
    o = [bm.verts.new(p) for p in ((-outer, 0, -outer * 0.9), (outer, 0, -outer * 0.9), (outer, 0, outer * 0.9), (-outer, 0, outer * 0.9))]
    i = [bm.verts.new(p) for p in ((-inner, 0, -inner * 0.8), (inner, 0, -inner * 0.8), (inner, 0, inner * 0.8), (-inner, 0, inner * 0.8))]
    for k in range(4):
        bm.faces.new((o[k], o[(k + 1) % 4], i[(k + 1) % 4], i[k]))
    buckle = mesh_object('HatBuckle', bm)
    sol = buckle.modifiers.new('Solid', 'SOLIDIFY')
    sol.thickness = 0.035
    buckle.location = (0.0, -0.53, 0.13)
    buckle.rotation_euler = (math.radians(-4), 0, 0)
    buckle.data.materials.append(gold)
    parts.append(buckle)
    root = parent_all('Hat', parts)
    return root, parts


def scene_hat():
    reset_scene()
    studio_lights()
    root, parts = build_hat()
    root.rotation_euler = (math.radians(4), math.radians(-12), math.radians(8))
    cam = look_camera((0.0, -7.0, 2.4), (0, 0, 0.5), lens=70)
    bpy.context.view_layer.update()
    frame_camera(cam, parts, margin=1.08)
    render_to('hat')


# ---------------------------------------------------------------- тыква-талисман

# По наброскам владельца (30.10.2026, «Тыква (эмоции)»): тёмно-оранжевая тыква с
# глубокими дольками, тёмный хвостик с завитком и листьями, лицо светится изнутри.
# Тело и лица рендерятся отдельно: лицо — вырезы со светящимся нутром на прозрачном
# фоне (кожура — holdout), чтобы в браузере лицо смещалось к курсору и менялось.
MASCOT_SKIN = (srgb('#d0560f'), srgb('#451303'))
MASCOT_STEM = '#2b1f17'
MASCOT_LEAF = '#3b3a2a'
MASCOT_FACE_SCALE = 1.12


def poly_ellipse(cx, cz, rx, rz, rot=0.0, n=32):
    c, s_ = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        x, z = math.cos(a) * rx, math.sin(a) * rz
        pts.append((cx + x * c - z * s_, cz + x * s_ + z * c))
    return pts


def poly_arc_band(cx, cz, r_out, r_in, a0, a1, n=20):
    """Дуга-полоска (закрытый глаз): внешняя дуга туда, внутренняя обратно."""
    outer = [(cx + math.cos(math.radians(a0 + (a1 - a0) * i / n)) * r_out,
              cz + math.sin(math.radians(a0 + (a1 - a0) * i / n)) * r_out) for i in range(n + 1)]
    inner = [(cx + math.cos(math.radians(a1 - (a1 - a0) * i / n)) * r_in,
              cz + math.sin(math.radians(a1 - (a1 - a0) * i / n)) * r_in) for i in range(n + 1)]
    return outer + inner


def poly_crescent(x0, x1, z0, z1, a_top, a_bot, n=24):
    """Улыбка-серп: верх и низ сходятся в уголках; z0/z1 — высота уголков."""
    top, bottom = [], []
    for i in range(n + 1):
        t = i / n
        x = x0 + (x1 - x0) * t
        mid = z0 + (z1 - z0) * t
        top.append((x, mid - a_top * math.sin(math.pi * t)))
    for i in range(n - 1, 0, -1):
        t = i / n
        x = x0 + (x1 - x0) * t
        mid = z0 + (z1 - z0) * t
        bottom.append((x, mid - a_bot * math.sin(math.pi * t)))
    return top + bottom


def poly_band(fn, x0, x1, half, n=28):
    """Полоска толщиной 2*half вдоль кривой z = fn(x) (волнистый рот, бровь)."""
    upper = [(x0 + (x1 - x0) * i / n, fn(x0 + (x1 - x0) * i / n) + half) for i in range(n + 1)]
    lower = [(x1 - (x1 - x0) * i / n, fn(x1 - (x1 - x0) * i / n) - half) for i in range(n + 1)]
    return upper + lower


def poly_heart(cx, cz, size, n=48):
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        x = 16 * math.sin(t) ** 3
        z = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append((cx + x / 17 * size, cz + z / 17 * size))
    return pts


def poly_jagged(x0, x1, top, bottom, teeth, depth):
    """Рот с острыми зубами сверху и снизу: зубья смещены на полшага."""
    pts = []
    for i in range(2 * teeth + 1):
        t = i / (2 * teeth)
        pts.append((x0 + (x1 - x0) * t, top - (depth if i % 2 else 0.0) * math.sin(math.pi * t) ** 0.3))
    for i in range(2 * teeth, -1, -1):
        t = i / (2 * teeth)
        pts.append((x0 + (x1 - x0) * t, bottom + (depth if i % 2 == 0 else 0.0) * math.sin(math.pi * t) ** 0.3))
    # убираем дубли уголков
    clean = []
    for p in pts:
        if not clean or (abs(clean[-1][0] - p[0]) > 1e-6 or abs(clean[-1][1] - p[1]) > 1e-6):
            clean.append(p)
    if abs(clean[0][0] - clean[-1][0]) < 1e-6 and abs(clean[0][1] - clean[-1][1]) < 1e-6:
        clean.pop()
    return clean


def mirror(poly):
    return [(-x, z) for x, z in reversed(poly)]


def tooth(x, z_lo, z_hi, w=0.1):
    return (x, z_lo, z_hi, w)


# Лица: (вырезы, зубы). Координаты — доли радиуса в плоскости XZ, лицо смотрит на -Y.
def face_shapes(emotion):
    polys, teeth = _face_shapes(emotion)
    k, oz = MASCOT_FACE_SCALE, -0.05
    polys = [[(x * k, oz + (z - oz) * k) for x, z in poly] for poly in polys]
    teeth = [(x * k, oz + (lo - oz) * k, oz + (hi - oz) * k, w * k) for x, lo, hi, w in teeth]
    return polys, teeth


def _face_shapes(emotion):
    tri_l = [(-0.5, 0.08), (-0.13, 0.1), (-0.27, 0.37)]
    if emotion == 'normal':
        mouth = poly_crescent(-0.52, 0.52, -0.14, -0.14, 0.1, 0.38)
        return [tri_l, mirror(tri_l), mouth], [tooth(-0.2, -0.33, -0.2), tooth(0.2, -0.33, -0.2), tooth(0.0, -0.53, -0.4)]
    if emotion == 'joy':
        eye = poly_arc_band(-0.3, 0.12, 0.18, 0.075, 15, 165)
        mouth = poly_crescent(-0.48, 0.48, -0.12, -0.12, 0.03, 0.44)
        return [eye, mirror(eye), mouth], [tooth(-0.13, -0.24, -0.12), tooth(0.13, -0.24, -0.12)]
    if emotion == 'anger':
        eye = [(-0.52, 0.32), (-0.12, 0.13), (-0.44, 0.05)]
        mouth = poly_jagged(-0.48, 0.48, -0.18, -0.46, 5, 0.1)
        return [eye, mirror(eye), mouth], []
    if emotion == 'sad':
        eye = [(x, z) for x, z in poly_ellipse(-0.3, 0.16, 0.15, 0.1, rot=14, n=32) if True]
        eye = [p for p in eye if p[1] <= 0.16 + (p[0] + 0.3) * math.tan(math.radians(14)) + 1e-9]
        mouth = poly_band(lambda x: -0.36 + 0.11 * (1 - (x / 0.36) ** 2) + 0.022 * math.sin(x * 26), -0.36, 0.36, 0.035)
        return [eye, mirror(eye), mouth], []
    if emotion == 'surprise':
        eye = poly_ellipse(-0.27, 0.2, 0.1, 0.14)
        return [eye, mirror(eye), poly_ellipse(0.0, -0.31, 0.09, 0.12)], []
    if emotion == 'wink':
        open_eye = poly_ellipse(-0.28, 0.2, 0.1, 0.13)
        closed = mirror(poly_arc_band(-0.28, 0.12, 0.16, 0.06, 20, 160))
        mouth = poly_crescent(-0.4, 0.4, -0.16, -0.16, 0.06, 0.3)
        return [open_eye, closed, mouth], [tooth(0.12, -0.27, -0.16)]
    if emotion == 'sleep':
        eye = poly_arc_band(-0.28, 0.24, 0.17, 0.08, 200, 340)
        mouth = poly_crescent(-0.12, 0.12, -0.29, -0.29, 0.02, 0.13)
        return [eye, mirror(eye), mouth], []
    if emotion == 'confusion':
        left = poly_ellipse(-0.28, 0.18, 0.09, 0.12)
        right = poly_ellipse(0.28, 0.15, 0.11, 0.065)
        brow = poly_band(lambda x: 0.34 - 0.22 * (x - 0.16), 0.16, 0.42, 0.028)
        mouth = poly_band(lambda x: -0.32 + 0.045 * math.sin(x * 14), -0.3, 0.3, 0.032)
        return [left, right, brow, mouth], []
    if emotion == 'love':
        heart = poly_heart(-0.28, 0.17, 0.16)
        mouth = poly_crescent(-0.36, 0.36, -0.18, -0.18, 0.05, 0.3)
        return [heart, mirror(heart), mouth], [tooth(0.0, -0.28, -0.17)]
    if emotion == 'fear':
        eye = poly_ellipse(-0.27, 0.19, 0.11, 0.15)
        mouth = poly_jagged(-0.32, 0.32, -0.22, -0.47, 4, 0.08)
        return [eye, mirror(eye), mouth], []
    if emotion == 'cool':
        eye = poly_ellipse(-0.3, 0.18, 0.17, 0.06, rot=-14)
        mouth = poly_crescent(-0.3, 0.38, -0.32, -0.18, 0.02, 0.16)
        return [eye, mirror(eye), mouth], []
    if emotion == 'neutral':
        eye = poly_ellipse(-0.28, 0.16, 0.15, 0.06)
        mouth = poly_band(lambda x: -0.3, -0.18, 0.18, 0.04)
        return [eye, mirror(eye), mouth], []
    raise ValueError(emotion)


MASCOT_EMOTIONS = ['normal', 'joy', 'anger', 'sad', 'surprise', 'wink', 'sleep', 'confusion',
                   'love', 'fear', 'cool', 'neutral']


def mascot_stem(name, points, radii, thickness, color):
    """Хвостик кривой с фаской: сечение идёт поперёк изгиба, завиток не сплющивается."""
    curve = bpy.data.curves.new(name, 'CURVE')
    curve.dimensions = '3D'
    curve.bevel_depth = thickness
    curve.bevel_resolution = 6
    curve.use_fill_caps = True
    spline = curve.splines.new('BEZIER')
    spline.bezier_points.add(len(points) - 1)
    for bp, co, r in zip(spline.bezier_points, points, radii):
        bp.co = co
        bp.handle_left_type = 'AUTO'
        bp.handle_right_type = 'AUTO'
        bp.radius = r
    obj = bpy.data.objects.new(name, curve)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(principled(f'{name}Mat', srgb(color), 0.72, Coat_Weight=0.05))
    return obj


def prism_cutter(name, poly, radius, mat):
    bm = bmesh.new()
    front = [bm.verts.new((x * radius, -2.0 * radius, z * radius)) for x, z in poly]
    back = [bm.verts.new((x * radius, 0.0, z * radius)) for x, z in poly]
    bm.faces.new(front)
    bm.faces.new(list(reversed(back)))
    k = len(poly)
    for i in range(k):
        bm.faces.new((front[i], front[(i + 1) % k], back[(i + 1) % k], back[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    obj = mesh_object(name, bm)
    obj.data.materials.append(mat)
    return obj


def boolean_apply(target, other, operation='DIFFERENCE'):
    mod = target.modifiers.new('Bool', 'BOOLEAN')
    mod.operation = operation
    mod.solver = 'EXACT'
    mod.object = other
    try:
        mod.material_mode = 'TRANSFER'
    except (AttributeError, TypeError):
        pass
    apply_modifiers(target)


def carve_face(shell, emotion, radius, flesh):
    cutters_polys, teeth = face_shapes(emotion)
    cutters = [prism_cutter(f'Cut{i}', poly, radius, flesh) for i, poly in enumerate(cutters_polys)]
    mouth = cutters[-1]
    for x, z_lo, z_hi, w in teeth:
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        for v in bm.verts:
            v.co.x *= w * radius
            v.co.y *= 2.4 * radius
            v.co.z *= (z_hi - z_lo) * radius
        tooth_obj = mesh_object('Tooth', bm)
        tooth_obj.location = (x * radius, -1.0 * radius, (z_lo + z_hi) / 2 * radius)
        boolean_apply(mouth, tooth_obj)
        bpy.data.objects.remove(tooth_obj)
    for cutter in cutters:
        boolean_apply(shell, cutter)
        bpy.data.objects.remove(cutter)
    for poly in shell.data.polygons:
        poly.use_smooth = True


def mascot_scene():
    """Тело (без лица), хвостик с листьями, ядро света. Возвращает всё нужное для рендеров."""
    reset_scene()
    studio_lights(energy=0.62)
    bpy.context.scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value = 0.2
    # Холодный контровой сверху-сзади: тёмная тыква не тонет на тёмном фоне портала.
    add_area_light('Moon', (-2.0, 3.6, 4.2), (0, 0, 0.2), 3.0, 620, (0.78, 0.72, 1.0))
    radius = 1.0
    body = pumpkin_body('Mascot', radius, lobes=8, squash=0.84, groove_depth=0.11,
                        palette=MASCOT_SKIN, skin=dict(Coat_Weight=0.08, Coat_Roughness=0.45,
                                                       Subsurface_Weight=0.03))
    body.modifiers['Subsurf'].levels = 2
    body.modifiers['Subsurf'].render_levels = 2
    apply_modifiers(body)
    flesh = principled('MascotFlesh', srgb('#ff9a2e'), 0.6,
                       Emission_Color=srgb('#ff7a0a'), Emission_Strength=1.5)
    body.data.materials.append(flesh)
    sol = body.modifiers.new('Shell', 'SOLIDIFY')
    sol.thickness = 0.09 * radius
    sol.offset = -1.0
    sol.material_offset = 1
    sol.material_offset_rim = 1
    apply_modifiers(body)
    core = mesh_object('MascotCore', uv_sphere(24, 12, 0.36 * radius))
    core.scale = (1.0, 1.0, 0.8)
    core.location = (0, 0.25, -0.02)
    core.data.materials.append(emission_material('MascotCoreMat', srgb('#ffd04a'), 4.0))
    lamp = bpy.data.lights.new('MascotLamp', 'POINT')
    lamp.energy = 420
    lamp.color = (1.0, 0.6, 0.2)
    lamp.shadow_soft_size = 0.2
    lamp_obj = bpy.data.objects.new('MascotLamp', lamp)
    bpy.context.scene.collection.objects.link(lamp_obj)
    lamp_obj.location = (0, 0.12, -0.05)
    stem = mascot_stem('MascotStem',
                       [(0.0, 0.0, 0.56), (0.02, 0.0, 0.82), (0.1, 0.0, 1.0), (0.24, 0.0, 1.07),
                        (0.32, 0.0, 0.99), (0.27, 0.0, 0.92)],
                       [1.0, 0.86, 0.7, 0.56, 0.44, 0.34], 0.12, MASCOT_STEM)
    # Листья развёрнуты к камере (иначе плоский лист сверху виден ребром).
    leaves = [
        leaf('MascotLeafL', (-0.3, -0.12, 0.74), Euler((math.radians(58), math.radians(-28), math.radians(8))),
             size=0.34, color=MASCOT_LEAF),
        leaf('MascotLeafR', (0.32, -0.08, 0.76), Euler((math.radians(62), math.radians(24), math.radians(-10))),
             size=0.3, color=MASCOT_LEAF),
    ]
    vine = tendril('MascotVine', (-0.12, -0.1, 0.78), turns=1.6, length=0.4, radius=0.12,
                   thickness=0.016, color=MASCOT_LEAF, roughness=0.88)
    cam = look_camera((0.0, -8.0, 1.25), (0, 0, 0.12), lens=85)
    bpy.context.view_layer.update()
    frame_camera(cam, [body, stem, vine] + leaves, margin=1.08)
    return body, flesh, [stem, vine] + leaves, core, lamp_obj


def holdout_material():
    mat = bpy.data.materials.new('Holdout')
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    for n in list(nodes):
        nodes.remove(n)
    out = nodes.new('ShaderNodeOutputMaterial')
    hold = nodes.new('ShaderNodeHoldout')
    mat.node_tree.links.new(hold.outputs['Holdout'], out.inputs['Surface'])
    return mat


def scene_mascot():
    body, flesh, decor, core, lamp = mascot_scene()
    # 1. Тело без лица — подложка для всех эмоций.
    render_to('mascot-body')
    # 2. Логотип: тело с обычным лицом, всё целиком.
    logo = body.copy()
    logo.data = body.data.copy()
    bpy.context.scene.collection.objects.link(logo)
    body.hide_render = True
    carve_face(logo, 'normal', 1.0, flesh)
    render_to('mascot-logo')
    bpy.data.objects.remove(logo)
    # 3. Лица: только вырезы с нутром — кожура и хвостик прозрачны.
    hold = holdout_material()
    for obj in decor:
        obj.hide_render = True
    for emotion in MASCOT_EMOTIONS:
        shell = body.copy()
        shell.data = body.data.copy()
        bpy.context.scene.collection.objects.link(shell)
        shell.hide_render = False
        carve_face(shell, emotion, 1.0, flesh)
        shell.data.materials[0] = hold
        render_to(f'mascot-face-{emotion}')
        bpy.data.objects.remove(shell)


SCENES = {
    'mascot': scene_mascot,
    'pumpkins': scene_pumpkins,
    'bat': scene_bat,
    'ghost': scene_ghost,
    'spider': scene_spider,
    'hat': scene_hat,
}

if __name__ == '__main__':
    for key, fn in SCENES.items():
        if ONLY and key not in ONLY:
            continue
        fn()
