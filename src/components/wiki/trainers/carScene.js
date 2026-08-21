import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';

/* Трёхмерная машина для тренажёра фотоконтроля.
 *
 * Модуль императивный и без React намеренно: сцена живёт своей жизнью между
 * рендерами, а перерисовывать её через состояние компонента значит гонять
 * дерево React на каждый кадр вращения. React здесь только монтирует холст и
 * получает готовые события.
 *
 * Что важно знать про исходную модель (Volkswagen Vento, CC-BY, автор CCamo):
 *   • она рассчитана на внешние ракурсы — под крышкой багажника пусто, поэтому
 *     отсек мы достраиваем сами;
 *   • анимаций в ней нет: двери — отдельные объекты, и петли мы вычисляем по
 *     габаритам, а не берём готовыми;
 *   • имена деталей повторяются с суффиксом (door_rf_hi_ok.001 — это СТЕКЛО, а
 *     не дверь), поэтому сравнение имён только точное;
 *   • приезжает она в сантиметрах и уже развёрнутой в Y-вверх (так её отдаёт
 *     конвертер), а локальные вершины остались Z-вверх — отсюда возня с осями
 *     ниже.
 */

/* Детали, которые открываются. Ключ — флаг мира сценария, значение — имя меша
   в модели. Имена ТОЧНЫЕ: по префиксу «door_» сюда попали бы стёкла. */
/* Ось вращения задаётся в МИРОВЫХ координатах, а не именем локальной оси.
 *
 * Так было не сразу: сначала дверь вращали вокруг локальной «z», считая её
 * вертикалью модели. На деле локальные оси меша после конвертера смотрят иначе,
 * и дверь поднималась вверх вокруг продольной оси машины — знак угла при этом
 * не менял вообще ничего (габариты при +1 и −1 совпадали до сотых). Мировая ось
 * от внутренней ориентации модели не зависит: 'yaw' — вертикаль, вокруг неё
 * распахиваются двери; 'pitch' — поперечная ось, вокруг неё поднимаются крышка
 * багажника и капот.
 */
const OPENABLE = {
    doorFrontLeft: { mesh: 'door_lf_hi_ok', axis: 'yaw', edge: 'min', angle: -1.15 },
    doorRearLeft: { mesh: 'door_lr_hi_ok', axis: 'yaw', edge: 'min', angle: -1.15 },
    /* Крышка не просто поворачивается: в её меш входит и задняя панель с
       номером, и на чистом повороте вокруг петли панель описывает дугу прямо
       через кузов и заднее стекло. У настоящей крышки петли четырёхзвенные —
       поднимаясь, она ОТХОДИТ назад. Это смещение и задаёт lift. */
    trunkOpen: {
        mesh: 'boot_hi_ok', axis: 'pitch', edge: 'min', angle: -0.8,
        lift: { y: 0.1, z: 0.16 }, thickness: 0.03,
    },
};

/** Мировые оси вращения: вертикаль для дверей, поперечная для крышек. */
const WORLD_AXIS = {
    yaw: new THREE.Vector3(0, 1, 0),
    pitch: new THREE.Vector3(1, 0, 0),
};

/* Длина реальной машины. Нормализуем по ней, а не делим на сто: следующая
   модель может приехать в дюймах, и деление молча промахнётся. */
const CAR_LENGTH = 4.4;

const EYE_HEIGHT = 1.55;      // рост человека с телефоном в руках
const LOOK_HEIGHT = 0.75;     // смотрим в середину борта, а не в крышу
export const MIN_DISTANCE = 1.6;
export const MAX_DISTANCE = 9;

/* Где в атласе кузова лежит номерной знак.
 *
 * У модели один атлас на весь кузов, и номер в нём нарисован ОДИН — его
 * показывают и передний бампер, и крышка багажника. Плашка повёрнута на 90°:
 * в текстуре она вертикальная, 22 на 88 пикселей.
 *
 * Числа получены разметкой самой картинки сеткой, а не на глаз: сместись на
 * пару пикселей — и поверх штатного номера ляжет полоса не на своём месте.
 */
const PLATE_IN_ATLAS = { x: 96, y: 34, w: 24, h: 90 };

/** Перерисовать номерной знак прямо в атласе кузова. */
const plateInTexture = (source, text) => {
    const image = source?.image;
    if (!image?.width) return null;

    const canvas = document.createElement('canvas');
    canvas.width = image.width;
    canvas.height = image.height;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(image, 0, 0);

    /* Рисуем в повёрнутой системе координат: так номер задаётся как обычный
       горизонтальный, а в атлас ложится вертикально, как там и было. */
    const { x, y, w, h } = PLATE_IN_ATLAS;
    ctx.save();
    ctx.translate(x + w / 2, y + h / 2);
    ctx.rotate(Math.PI / 2);
    const plateW = h, plateH = w;          // после поворота стороны меняются
    ctx.fillStyle = '#f2f3f5';
    ctx.fillRect(-plateW / 2, -plateH / 2, plateW, plateH);
    ctx.strokeStyle = '#15171a';
    ctx.lineWidth = 1.5;
    ctx.strokeRect(-plateW / 2 + 1, -plateH / 2 + 1, plateW - 2, plateH - 2);
    // Синий блок с кодом страны — справа, как на казахстанских номерах.
    const blue = plateH * 0.7;
    const gap = 2;
    ctx.fillStyle = '#1c4fd8';
    ctx.fillRect(plateW / 2 - blue - gap, -plateH / 2 + gap, blue, plateH - gap * 2);
    ctx.fillStyle = '#fff';
    ctx.font = `bold ${Math.round(plateH * 0.34)}px Arial, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('KZ', plateW / 2 - blue / 2 - gap, 0);

    /* Кегль подбираем по фактической ширине строки, а не на глаз: «000 XXX 02»
       не влезало в оставшееся поле, и у номера срезался первый знак. */
    const room = plateW - blue - gap * 4;
    let size = Math.round(plateH * 0.6);
    ctx.fillStyle = '#15171a';
    for (; size > 4; size -= 1) {
        ctx.font = `bold ${size}px Arial, sans-serif`;
        if (ctx.measureText(text).width <= room) break;
    }
    ctx.fillText(text, -plateW / 2 + gap * 2 + room / 2, 1);
    ctx.restore();

    const texture = new THREE.CanvasTexture(canvas);
    // Параметры копируем с исходной: у glTF-текстур flipY выключен, и без этого
    // весь кузов покрылся бы перевёрнутым атласом.
    texture.flipY = source.flipY;
    texture.colorSpace = source.colorSpace;
    texture.wrapS = source.wrapS;
    texture.wrapT = source.wrapT;
    texture.repeat.copy(source.repeat);
    texture.offset.copy(source.offset);
    texture.anisotropy = 4;
    texture.needsUpdate = true;
    return texture;
};

/** Мягкая круглая тень под машиной — вместо теневой карты. */
const shadowTexture = () => {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 256;
    const ctx = canvas.getContext('2d');
    const grad = ctx.createRadialGradient(128, 128, 8, 128, 128, 126);
    grad.addColorStop(0, 'rgba(0,0,0,.5)');
    grad.addColorStop(0.5, 'rgba(0,0,0,.26)');
    grad.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = grad; ctx.fillRect(0, 0, 256, 256);
    return new THREE.CanvasTexture(canvas);
};

/**
 * Собрать сцену на готовом холсте.
 * @returns объект управления; всё общение с React идёт через него.
 */
export function createCarScene(canvas, { modelUrl, bodyColor = 0xeef0f3, plate = '000 XXX 02',
    onReady, onError } = {}) {
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xdfe5ec);
    scene.fog = new THREE.Fog(0xdfe5ec, 26, 64);

    /* Свет: полусфера как небо и одно направленное «солнце». Теней в реальном
       времени нет вовсе — они тут не нужны, а стоят дороже всей остальной
       сцены вместе взятой. */
    scene.add(new THREE.HemisphereLight(0xffffff, 0x9aa3ad, 2.1));
    const sun = new THREE.DirectionalLight(0xffffff, 1.9);
    sun.position.set(5, 9, 6);
    scene.add(sun);
    const fill = new THREE.DirectionalLight(0xffffff, 0.7);
    fill.position.set(-6, 4, -5);
    scene.add(fill);

    const camera = new THREE.PerspectiveCamera(52, 1, 0.1, 120);

    /* Вторая камера — та, что «в телефоне».
     *
     * Экран не дыра в мире: у телефона свой объектив, шире человеческого
     * взгляда, поэтому машина в кадре выглядит МЕЛЬЧЕ, чем вокруг корпуса.
     * Рисуем её вторым проходом в прямоугольник экрана через scissor: это
     * дешевле отдельного холста (один контекст WebGL вместо двух) и дешевле
     * рендера в текстуру с последующей выдачей её в DOM. */
    const phoneCamera = new THREE.PerspectiveCamera(64, 0.46, 0.1, 120);
    let phoneRect = null;
    const root = new THREE.Group();
    scene.add(root);

    const state = {
        azimuth: 205,          // не строго сзади: сразу видно, что машину обходят
        distance: 5.4,
        ready: false,
        disposed: false,
    };
    const hinges = {};                 // ключ мира → группа-шарнир
    const opened = {};                 // ключ мира → открыто ли (целевое)
    const angles = {};                 // ключ мира → текущий угол (для плавности)
    const pickTargets = [];            // меши, по которым ловим клик

    /* Рендер по требованию.
     *
     * Бесконечный requestAnimationFrame крутил бы видеокарту и батарею всё
     * время, пока открыт тренажёр, — даже когда человек читает реплику барса и
     * ничего не трогает. Поэтому кадр рисуется только когда что-то изменилось,
     * а на время анимации дверей цикл включается сам и гаснет по её окончании.
     */
    let frame = 0;
    let needsRender = true;
    const invalidate = () => {
        needsRender = true;
        if (!frame) frame = requestAnimationFrame(tick);
    };

    function applyCamera() {
        const a = THREE.MathUtils.degToRad(state.azimuth);
        camera.position.set(
            Math.sin(a) * state.distance,
            EYE_HEIGHT,
            Math.cos(a) * state.distance,
        );
        camera.lookAt(0, LOOK_HEIGHT, 0);
        /* Телефон человек держит перед собой, опустив руки от глаз. Целится
           объектив чуть НИЖЕ середины борта: так машина поднимается к середине
           экрана и попадает в рамку-подсказку, а не сползает под неё. */
        phoneCamera.position.copy(camera.position);
        phoneCamera.position.y -= 0.14;
        phoneCamera.lookAt(0, LOOK_HEIGHT - 0.55, 0);
    }

    function tick() {
        frame = 0;
        if (state.disposed) return;

        // Двери доводятся к цели плавно: рывком они выглядят сломанными.
        let animating = false;
        Object.keys(hinges).forEach((key) => {
            const spec = OPENABLE[key];
            const goal = opened[key] ? spec.angle : 0;
            const now = angles[key] || 0;
            const diff = goal - now;
            const place = (value) => {
                const hinge = hinges[key];
                hinge.setRotationFromAxisAngle(hinge.userData.axis, value);
                if (spec.lift) {
                    // Доля открытия: на закрытой крышке смещения нет вовсе.
                    const part = spec.angle === 0 ? 0 : value / spec.angle;
                    hinge.position.copy(hinge.userData.home);
                    hinge.position.y += spec.lift.y * part;
                    hinge.position.z += spec.lift.z * part;
                }
            };
            if (Math.abs(diff) > 0.002) {
                angles[key] = now + diff * 0.18;
                place(angles[key]);
                animating = true;
            } else if (now !== goal) {
                angles[key] = goal;
                place(goal);
            }
        });

        if (needsRender || animating) {
            needsRender = false;
            applyCamera();

            const width = canvas.clientWidth || 1;
            const height = canvas.clientHeight || 1;
            renderer.setScissorTest(false);
            renderer.setViewport(0, 0, width, height);
            renderer.render(scene, camera);

            /* Кадр внутри телефона. Прямоугольник приходит из разметки, а
               WebGL считает Y снизу — отсюда переворот. */
            if (phoneRect && phoneRect.w > 8 && phoneRect.h > 8) {
                const bottom = height - (phoneRect.y + phoneRect.h);
                renderer.setScissorTest(true);
                renderer.setViewport(phoneRect.x, bottom, phoneRect.w, phoneRect.h);
                renderer.setScissor(phoneRect.x, bottom, phoneRect.w, phoneRect.h);
                phoneCamera.aspect = phoneRect.w / phoneRect.h;
                phoneCamera.updateProjectionMatrix();
                renderer.render(scene, phoneCamera);
                renderer.setScissorTest(false);
            }
        }
        if (animating) frame = requestAnimationFrame(tick);
    }

    function resize() {
        const width = canvas.clientWidth || 640;
        const height = canvas.clientHeight || 480;
        renderer.setSize(width, height, false);
        camera.aspect = width / Math.max(1, height);
        camera.updateProjectionMatrix();
        invalidate();
    }

    /* Петля детали: группа встаёт в точку шарнира, меш сдвигается на столько
       же — тогда поворот группы крутит дверь вокруг кромки, а не вокруг центра.
       Край выбирается в МИРОВЫХ координатах (нос машины смотрит в -Z), а сама
       точка переводится в локальные: там у модели вертикаль всё ещё Z. */
    function makeHinge(mesh, edge, axisName) {
        const box = new THREE.Box3().setFromObject(mesh);
        const centre = box.getCenter(new THREE.Vector3());
        const worldPivot = new THREE.Vector3(
            centre.x, centre.y, edge === 'min' ? box.min.z : box.max.z,
        );
        const pivot = mesh.parent.worldToLocal(worldPivot.clone());
        const hinge = new THREE.Group();
        hinge.position.copy(pivot);
        mesh.parent.add(hinge);
        mesh.position.sub(pivot);
        hinge.add(mesh);

        // Мировую ось переводим в систему шарнира: крутить надо вокруг
        // вертикали ДВОРА, а не вокруг того, что модель считает вертикалью.
        const parentQuaternion = new THREE.Quaternion();
        hinge.parent.getWorldQuaternion(parentQuaternion);
        hinge.userData.axis = WORLD_AXIS[axisName].clone()
            .applyQuaternion(parentQuaternion.clone().invert())
            .normalize();
        // Домашняя точка: смещение при открытии считается от неё, иначе крышка
        // уползала бы всё дальше на каждой анимации.
        hinge.userData.home = hinge.position.clone();
        return hinge;
    }

    new GLTFLoader().load(modelUrl, (gltf) => {
        if (state.disposed) return;
        root.add(gltf.scene);

        const parts = {};
        const glassMeshes = [];
        const bodyMeshes = [];
        gltf.scene.traverse((node) => {
            if (!node.isMesh) return;
            parts[node.name] = node;
            const material = node.material?.name || '';
            /* Кузов приезжает кислотно-зелёным, молдинг — розовым: это цвета
               автора модели, а не окраска машины. Весь кузов сидит на одном
               материале, поэтому перекраска — одна строка. */
            if (material.startsWith('primary')) {
                node.material = node.material.clone();
                node.material.color = new THREE.Color(bodyColor);
                node.material.metalness = 0.3;
                node.material.roughness = 0.35;
                bodyMeshes.push(node);
            } else if (material.startsWith('secondary')) {
                node.material = node.material.clone();
                node.material.color = new THREE.Color(0x2b2f36);
                node.material.roughness = 0.75;
            } else if (/glass|windscreen/i.test(material)) {
                // Текстуры стекла в архиве модели нет — без своего материала
                // машина выглядит заклеенной белым наглухо.
                node.material = new THREE.MeshPhysicalMaterial({
                    color: 0x1e2a3a, transparent: true, opacity: 0.36,
                    roughness: 0.06, metalness: 0,
                });
                glassMeshes.push(node);
            }
        });

        // Масштаб и посадка на землю. updateMatrixWorld обязателен: Box3 читает
        // matrixWorld, а three обновляет его только на рендере — без этого
        // габариты считаются по единичным матрицам.
        root.updateMatrixWorld(true);
        const rawSize = new THREE.Box3().setFromObject(root).getSize(new THREE.Vector3());
        root.scale.setScalar(CAR_LENGTH / Math.max(rawSize.x, rawSize.y, rawSize.z));
        root.updateMatrixWorld(true);
        const box = new THREE.Box3().setFromObject(root);
        const centre = box.getCenter(new THREE.Vector3());
        root.position.sub(new THREE.Vector3(centre.x, box.min.y, centre.z));
        root.updateMatrixWorld(true);
        const size = new THREE.Box3().setFromObject(root).getSize(new THREE.Vector3());

        Object.entries(OPENABLE).forEach(([key, spec]) => {
            const mesh = parts[spec.mesh];
            if (!mesh) return;
            hinges[key] = makeHinge(mesh, spec.edge, spec.axis);
            opened[key] = false;
            angles[key] = 0;
            mesh.userData.openKey = key;
            pickTargets.push(mesh);

            /* Толщина. Крышка и двери в модели — одна оболочка без изнанки: с
               ребра они выглядят листом бумаги, а изнутри салона исчезают
               вовсе. Двусторонний материал показывает изнанку, а копия,
               сдвинутая на пару сантиметров, даёт видимую толщину кромки. */
            mesh.material = mesh.material.clone();
            mesh.material.side = THREE.DoubleSide;
            if (spec.thickness) {
                const inner = new THREE.Mesh(mesh.geometry, mesh.material);
                /* Смещаем вдоль МИРОВОЙ вертикали, переведённой в систему меша.
                   По локальной «y» копия уезжала на землю: у этой модели
                   локальные оси не вертикальны — та же грабля, что с петлями. */
                const meshQuaternion = new THREE.Quaternion();
                mesh.getWorldQuaternion(meshQuaternion);
                const down = new THREE.Vector3(0, -1, 0)
                    .applyQuaternion(meshQuaternion.invert())
                    .normalize();
                /* Делим на МИРОВОЙ масштаб меша, а не корня: между ними ещё
                   один масштаб от конвертера, и по масштабу корня толщина
                   выходила в сотни раз больше — крышка уезжала под землю. */
                const worldScale = new THREE.Vector3();
                mesh.getWorldScale(worldScale);
                inner.position.copy(down)
                    .multiplyScalar(spec.thickness / (worldScale.x || 1));
                mesh.add(inner);
            }
        });

        /* Стёкла: разрезаем и раздаём дверям.
         *
         * В модели ВСЕ стёкла — один меш на всю машину (лобовое, боковые,
         * заднее вместе). Пока он висел целиком, открытая дверь уезжала, а её
         * стекло оставалось в воздухе на прежнем месте — первое, что бросается
         * в глаза. Поэтому треугольники, попавшие в габарит двери, переносим в
         * её шарнир, а остальное стекло оставляем кузову.
         *
         * Требуем, чтобы в габарит двери попали ВСЕ ТРИ вершины треугольника,
         * а не его центр. По центру дверь забирала длинные треугольники, которые
         * тянутся к соседним стёклам: при открытии такой кусок улетал вместе с
         * дверью и оставался висеть над землёй.
         */
        const giveGlassToDoors = () => {
            /* Матрицы обязаны быть свежими: двери только что переехали внутрь
               шарниров, и Box3 без пересчёта считает их габариты по прежним
               матрицам. Именно из-за этого в «зону двери» попадали случайные
               треугольники и уезжали вместе с ней на другой конец двора. */
            root.updateMatrixWorld(true);

            const zones = Object.entries(OPENABLE)
                .filter(([, spec]) => spec.axis === 'yaw')
                .map(([key, spec]) => {
                    const mesh = parts[spec.mesh];
                    if (!mesh || !hinges[key]) return null;
                    const box = new THREE.Box3().setFromObject(mesh);
                    // Небольшой запас: стекло стоит чуть внутрь от кромки двери.
                    box.expandByScalar(0.05);
                    return { key, box };
                })
                .filter(Boolean);
            if (!zones.length) return;

            glassMeshes.forEach((glass) => {
                const geo = glass.geometry;
                const pos = geo.attributes.position;
                const nor = geo.attributes.normal;
                const uv = geo.attributes.uv;
                const idx = geo.index;
                const count = idx ? idx.count : pos.count;
                const buckets = new Map();          // ключ двери → массивы атрибутов
                const rest = { position: [], normal: [], uv: [] };
                const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();

                const push = (target, i0, i1, i2) => {
                    [i0, i1, i2].forEach((i) => {
                        target.position.push(pos.getX(i), pos.getY(i), pos.getZ(i));
                        if (nor) target.normal.push(nor.getX(i), nor.getY(i), nor.getZ(i));
                        if (uv) target.uv.push(uv.getX(i), uv.getY(i));
                    });
                };

                for (let i = 0; i < count; i += 3) {
                    const i0 = idx ? idx.getX(i) : i;
                    const i1 = idx ? idx.getX(i + 1) : i + 1;
                    const i2 = idx ? idx.getX(i + 2) : i + 2;
                    a.fromBufferAttribute(pos, i0).applyMatrix4(glass.matrixWorld);
                    b.fromBufferAttribute(pos, i1).applyMatrix4(glass.matrixWorld);
                    c.fromBufferAttribute(pos, i2).applyMatrix4(glass.matrixWorld);
                    const zone = zones.find((z) => z.box.containsPoint(a)
                        && z.box.containsPoint(b) && z.box.containsPoint(c));
                    if (!zone) { push(rest, i0, i1, i2); continue; }
                    if (!buckets.has(zone.key)) {
                        buckets.set(zone.key, { position: [], normal: [], uv: [] });
                    }
                    push(buckets.get(zone.key), i0, i1, i2);
                }

                const build = (data) => {
                    const out = new THREE.BufferGeometry();
                    out.setAttribute('position',
                        new THREE.Float32BufferAttribute(data.position, 3));
                    if (nor && data.normal.length) {
                        out.setAttribute('normal', new THREE.Float32BufferAttribute(data.normal, 3));
                    }
                    if (uv && data.uv.length) {
                        out.setAttribute('uv', new THREE.Float32BufferAttribute(data.uv, 2));
                    }
                    return out;
                };

                buckets.forEach((data, key) => {
                    const piece = new THREE.Mesh(build(data), glass.material);
                    /* Кусок делаем РЕБЁНКОМ стекла: его вершины уже в системе
                       координат стекла, поэтому никаких пересчётов не нужно.
                       Попытка сдвинуть его матрицей родителя вручную кончилась
                       тем, что матрица на этот момент ещё не пересчитана, и
                       осколки разъезжались по двору. */
                    glass.add(piece);
                    // attach сохраняет мировое положение: стекло остаётся ровно
                    // там, где было, но теперь поворачивается вместе с дверью.
                    hinges[key].attach(piece);
                });

                glass.geometry = build(rest);
                geo.dispose();
            });
        };
        giveGlassToDoors();

        /* Багажный отсек. У модели под крышкой пусто, и кадр «открытый
           багажник» показывал бы сквозную дыру. Коробка вывернута наизнанку
           (BackSide): снаружи её нет вовсе, изнутри она читается стенками. */
        const boot = parts[OPENABLE.trunkOpen.mesh];
        if (boot) {
            const bootBox = new THREE.Box3().setFromObject(boot);
            const bootSize = bootBox.getSize(new THREE.Vector3());
            const bootMid = bootBox.getCenter(new THREE.Vector3());
            const depth = 0.3;
            const well = new THREE.Mesh(
                new THREE.BoxGeometry(bootSize.x * 0.86, depth, bootSize.z * 0.78),
                new THREE.MeshBasicMaterial({ color: 0x2c2f35, side: THREE.BackSide }),
            );
            well.position.set(bootMid.x, bootBox.min.y - depth / 2 + 0.1, bootMid.z - 0.06);
            scene.add(well);
        }

        // Земля и запечённая тень.
        const ground = new THREE.Mesh(
            new THREE.CircleGeometry(40, 56),
            new THREE.MeshStandardMaterial({ color: 0x8f959e, roughness: 1, metalness: 0 }),
        );
        ground.rotation.x = -Math.PI / 2;
        scene.add(ground);

        const shadow = new THREE.Mesh(
            new THREE.PlaneGeometry(size.z * 1.2, size.x * 1.8),
            new THREE.MeshBasicMaterial({
                map: shadowTexture(), transparent: true, depthWrite: false,
            }),
        );
        shadow.rotation.x = -Math.PI / 2;
        shadow.position.y = 0.012;
        scene.add(shadow);

        /* Учебный госномер. Вписываем его в АТЛАС кузова, а не вешаем плоскость
           перед бампером: плоскость «парит» над кривой поверхностью и с ребра
           видно, что она отдельная деталь. В текстуре номер один на машину,
           поэтому правка сразу меняет и передний, и задний знак. */
        const atlas = bodyMeshes[0]?.material?.map;
        const withPlate = atlas ? plateInTexture(atlas, plate) : null;
        if (withPlate) {
            bodyMeshes.forEach((node) => { node.material.map = withPlate; });
        }

        state.ready = true;
        resize();
        onReady?.({ size: size.toArray() });
    }, undefined, (error) => onError?.(error));

    /* ── Управление ───────────────────────────────────────────────────── */

    const api = {
        /** Обойти машину: сдвиг в градусах. */
        orbit(deltaDeg) {
            state.azimuth = ((state.azimuth + deltaDeg) % 360 + 360) % 360;
            invalidate();
        },
        /** Подойти или отойти, в метрах. */
        dolly(deltaMeters) {
            state.distance = THREE.MathUtils.clamp(
                state.distance + deltaMeters, MIN_DISTANCE, MAX_DISTANCE,
            );
            invalidate();
        },
        /** Открыть или закрыть деталь. Ключи те же, что у мира сценария. */
        setOpen(key, isOpen) {
            if (!(key in OPENABLE)) return;
            opened[key] = Boolean(isOpen);
            invalidate();
        },
        /** Что под курсором: ключ открывающейся детали или null. */
        pick(clientX, clientY) {
            if (!state.ready) return null;
            const rect = canvas.getBoundingClientRect();
            const point = new THREE.Vector2(
                ((clientX - rect.left) / rect.width) * 2 - 1,
                -((clientY - rect.top) / rect.height) * 2 + 1,
            );
            const caster = new THREE.Raycaster();
            caster.setFromCamera(point, camera);
            const hit = caster.intersectObjects(pickTargets, false)[0];
            return hit ? hit.object.userData.openKey || null : null;
        },
        /** Прямоугольник экрана телефона в координатах холста (CSS-пиксели).
         *  В него вторым проходом рисуется кадр телефонного объектива. */
        setPhoneRect(rect) {
            phoneRect = rect && rect.w > 8 && rect.h > 8
                ? { x: rect.x, y: rect.y, w: rect.w, h: rect.h } : null;
            invalidate();
        },
        /** Где стоит человек — это уходит в правила кадра. */
        camera: () => ({ azimuth: state.azimuth, distance: state.distance }),
        isReady: () => state.ready,
        resize,
        dispose() {
            state.disposed = true;
            if (frame) cancelAnimationFrame(frame);
            scene.traverse((node) => {
                if (node.isMesh) {
                    node.geometry?.dispose?.();
                    const materials = Array.isArray(node.material) ? node.material : [node.material];
                    materials.forEach((m) => {
                        m?.map?.dispose?.();
                        m?.dispose?.();
                    });
                }
            });
            renderer.dispose();
        },
    };

    return api;
}
