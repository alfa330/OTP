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
    trunkOpen: { mesh: 'boot_hi_ok', axis: 'pitch', edge: 'min', angle: -0.95 },
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

/** Казахстанский учебный номер вместо немецкого из модели. */
const plateTexture = (text) => {
    const canvas = document.createElement('canvas');
    canvas.width = 512; canvas.height = 112;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#f4f5f6'; ctx.fillRect(0, 0, 512, 112);
    ctx.strokeStyle = '#15171a'; ctx.lineWidth = 5;
    ctx.strokeRect(3, 3, 506, 106);
    // Синяя полоса с кодом страны — по ней номер и читается как казахстанский.
    ctx.fillStyle = '#1c4fd8'; ctx.fillRect(432, 6, 74, 100);
    ctx.fillStyle = '#fff'; ctx.font = 'bold 34px -apple-system, Arial, sans-serif';
    ctx.textAlign = 'center'; ctx.fillText('KZ', 469, 76);
    ctx.fillStyle = '#15171a'; ctx.font = 'bold 62px -apple-system, Arial, sans-serif';
    ctx.fillText(text, 212, 78);
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.anisotropy = 4;
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
            if (Math.abs(diff) > 0.002) {
                angles[key] = now + diff * 0.18;
                hinges[key].setRotationFromAxisAngle(hinges[key].userData.axis, angles[key]);
                animating = true;
            } else if (now !== goal) {
                angles[key] = goal;
                hinges[key].setRotationFromAxisAngle(hinges[key].userData.axis, goal);
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
        return hinge;
    }

    new GLTFLoader().load(modelUrl, (gltf) => {
        if (state.disposed) return;
        root.add(gltf.scene);

        const parts = {};
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
        });

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

        /* Учебный госномер поверх штатного немецкого: в тексте шага назван
           казахстанский, и расхождение с тем, что человек видит в кадре, —
           первое, на чём тренажёр теряет доверие. */
        const plateMap = plateTexture(plate);
        const plateGeometry = new THREE.PlaneGeometry(0.52, 0.125);
        const plateMaterial = new THREE.MeshBasicMaterial({ map: plateMap, toneMapped: false });

        /* Задний номер крепится К КРЫШКЕ багажника, а не к воздуху за машиной:
           у Vento он там и стоит, и при открытии обязан уезжать вверх вместе с
           ней. Плашка, оставленная в сцене, повисала бы в проёме, а рядом с
           поднятой крышкой был бы виден второй, заводской номер. */
        const rear = new THREE.Mesh(plateGeometry, plateMaterial);
        const bootHinge = hinges.trunkOpen;
        if (bootHinge) {
            bootHinge.add(rear);
            // Внутри модели свой масштаб (она приехала в сантиметрах) — плашку
            // возвращаем к метрам, иначе номер станет размером с машину.
            const scale = root.scale.x || 1;
            rear.scale.setScalar(1 / scale);
            rear.position.copy(bootHinge.worldToLocal(
                new THREE.Vector3(0, 0.74, size.z / 2 - 0.043),
            ));
        } else {
            rear.position.set(0, 0.74, size.z / 2 - 0.055);
            scene.add(rear);
        }

        const front = new THREE.Mesh(plateGeometry, plateMaterial);
        /* Спереди плашка стоит ЧУТЬ ПЕРЕД носом: в центре бампера у модели
           углубление, и вровень с габаритом номер тонет в геометрии. */
        front.position.set(0, 0.37, -size.z / 2 - 0.02);
        front.rotation.y = Math.PI;
        scene.add(front);

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
