import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Camera, DoorOpen, Images, Package, RefreshCw, SwitchCamera } from 'lucide-react';

import { createCarScene, MAX_DISTANCE, MIN_DISTANCE } from './carScene';
import { readFrame } from './carFraming';

/* Экран фотоконтроля: человек стоит у машины с телефоном в руках.
 *
 * Раскладка выбрана по требованию владельца и отличается от остальных
 * тренажёров осознанно. В них главный объект — телефон, и мир существует
 * только на его экране. Здесь наоборот: главное — МАШИНА, вокруг которой надо
 * обойти, а телефон человек держит перед собой. Поэтому сцена занимает всё
 * поле, а телефон лежит поверх неё снизу — статично, как в руках.
 *
 * Экран телефона намеренно ПРОЗРАЧНЫЙ: через него видно ту же сцену. Рисовать
 * во второй раз то же самое (через RenderTarget) значило бы удвоить работу
 * видеокарты ради эффекта, которого никто не заметит, — а так «смотрю на
 * машину через телефон» получается само собой.
 */

/* Подписи ракурсов — теми же словами, что и в репликах барса. Разойдутся —
   и человек будет искать «корму», читая про «зад». */
const VIEW_LABEL = {
    front: 'перёд',
    rear: 'корма',
    left: 'левый борт',
    right: 'правый борт',
    inside_front: 'передний ряд',
    inside_rear: 'задний ряд',
    trunk: 'багажник',
};

const FRAMING_LABEL = {
    close: 'слишком близко',
    far: 'слишком далеко',
    ok: null,
};

/* Что можно открыть руками. Порядок — как в сценарии: сначала двери, потом
   багажник. Иконки разные, потому что подписи на узком экране прячутся. */
const OPENERS = [
    { key: 'doorFrontLeft', label: 'Водительская дверь', short: 'Перед.', Icon: DoorOpen },
    { key: 'doorRearLeft', label: 'Задняя дверь', short: 'Задняя', Icon: DoorOpen },
    { key: 'trunkOpen', label: 'Багажник', short: 'Багажник', Icon: Package },
];

const MODEL_URL = `${import.meta.env.BASE_URL || '/'}models/vento.glb`;

export default function CarStage({ world, tap, toggle, target, plate = '000 XXX 02' }) {
    const canvasRef = useRef(null);
    const sceneRef = useRef(null);
    const screenRef = useRef(null);
    const dragRef = useRef(null);
    const [ready, setReady] = useState(false);
    const [failed, setFailed] = useState(false);
    const [frame, setFrame] = useState({ view: null, framing: 'ok' });

    /* Сцена создаётся один раз на всё время экрана. Пересоздавать её на каждое
       изменение мира нельзя: загрузка модели занимает секунды, и человек видел
       бы пустое поле после каждого открытия двери. */
    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return undefined;
        const scene = createCarScene(canvas, {
            modelUrl: MODEL_URL,
            plate,
            onReady: () => { setReady(true); refreshFrame(); syncPhoneRect(); },
            onError: () => setFailed(true),
        });
        sceneRef.current = scene;
        const onResize = () => { scene.resize(); syncPhoneRect(); };
        window.addEventListener('resize', onResize);
        return () => {
            window.removeEventListener('resize', onResize);
            scene.dispose();
            sceneRef.current = null;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    /* Где на холсте лежит экран телефона.
     *
     * Сцена рисует в этот прямоугольник второй кадр — тот, что «в телефоне».
     * Координаты меряются по факту, а не задаются числами: корпус двигается
     * вместе с раскладкой, и разъехавшись на пиксель, кадр вылезет за рамку. */
    const syncPhoneRect = useCallback(() => {
        const scene = sceneRef.current;
        const canvas = canvasRef.current;
        const screen = screenRef.current;
        if (!scene || !canvas || !screen) return;
        const box = canvas.getBoundingClientRect();
        const inner = screen.getBoundingClientRect();
        scene.setPhoneRect({
            x: inner.left - box.left,
            y: inner.top - box.top,
            w: inner.width,
            h: inner.height,
        });
    }, []);

    /* Что сейчас в объективе — считает чистый модуль, сцена лишь сообщает, где
       стоит человек. Здесь только подпись под рамкой; в сценарий это же уходит
       вместе с нажатием затвора. */
    const refreshFrame = useCallback(() => {
        const scene = sceneRef.current;
        if (!scene?.isReady()) return;
        setFrame(readFrame(scene.camera(), world));
    }, [world]);

    /* Первый замер — после того, как раскладка встала: на монтировании корпус
       ещё не имеет размеров, и прямоугольник вышел бы нулевым. */
    useEffect(() => {
        const frame = requestAnimationFrame(syncPhoneRect);
        return () => cancelAnimationFrame(frame);
    }, [syncPhoneRect, ready]);

    // Мир поменялся (открыли дверь) — двери в сцене и подпись под рамкой следом.
    useEffect(() => {
        const scene = sceneRef.current;
        if (!scene) return;
        OPENERS.forEach(({ key }) => scene.setOpen(key, Boolean(world[key])));
        refreshFrame();
    }, [world, refreshFrame]);

    /* Обход машины перетаскиванием. Порог в пять пикселей отделяет «обхожу» от
       «ткнул в дверь»: без него любое нажатие с дрожью руки открывало бы дверь
       вместо поворота. */
    const onPointerDown = (event) => {
        if (!sceneRef.current?.isReady()) return;
        event.currentTarget.setPointerCapture?.(event.pointerId);
        dragRef.current = { x: event.clientX, y: event.clientY, moved: 0 };
    };

    const onPointerMove = (event) => {
        const drag = dragRef.current;
        const scene = sceneRef.current;
        if (!drag || !scene) return;
        const dx = event.clientX - drag.x;
        const dy = event.clientY - drag.y;
        drag.moved += Math.abs(dx) + Math.abs(dy);
        drag.x = event.clientX;
        drag.y = event.clientY;
        scene.orbit(-dx * 0.32);
        // Движение вверх-вниз — шаг вперёд и назад: так человек подходит ближе,
        // не отпуская машину из виду.
        if (Math.abs(dy) > 0) scene.dolly(dy * 0.012);
        refreshFrame();
    };

    const onPointerUp = (event) => {
        const drag = dragRef.current;
        dragRef.current = null;
        if (!drag || drag.moved > 6) return;
        const key = sceneRef.current?.pick(event.clientX, event.clientY);
        if (key) toggle(key);
    };

    const onWheel = (event) => {
        const scene = sceneRef.current;
        if (!scene) return;
        scene.dolly(event.deltaY * 0.0022);
        refreshFrame();
    };

    /* Клавиатура: тренажёр обязан проходиться без мыши. Стрелки обходят машину
       и подходят ближе, пробел на затворе снимает. */
    const onKeyDown = (event) => {
        const scene = sceneRef.current;
        if (!scene) return;
        const step = event.shiftKey ? 15 : 5;
        if (event.key === 'ArrowLeft') scene.orbit(-step);
        else if (event.key === 'ArrowRight') scene.orbit(step);
        else if (event.key === 'ArrowUp') scene.dolly(-0.25);
        else if (event.key === 'ArrowDown') scene.dolly(0.25);
        else return;
        event.preventDefault();
        refreshFrame();
    };

    const shoot = () => {
        const scene = sceneRef.current;
        if (!scene?.isReady()) return;
        tap('shutter', { ...readFrame(scene.camera(), world), ...scene.camera() });
    };

    const viewName = frame.view ? VIEW_LABEL[frame.view] : 'три четверти';
    const framingNote = FRAMING_LABEL[frame.framing];

    return (
        <div className="wt-world">
            <canvas
                ref={canvasRef}
                className="wt-world__canvas"
                tabIndex={0}
                role="application"
                aria-label="Машина: тяните, чтобы обойти её кругом, нажмите на дверь, чтобы открыть"
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
                onPointerCancel={onPointerUp}
                onWheel={onWheel}
                onKeyDown={onKeyDown}
            />

            {!ready && !failed && (
                <div className="wt-world__load"><RefreshCw size={18} /> Подъезжает машина…</div>
            )}
            {failed && (
                <div className="wt-world__load wt-world__load--fail">
                    Не удалось загрузить трёхмерную машину. Обновите страницу.
                </div>
            )}

            {/* Панель управления машиной. Дублирует клик по двери в сцене:
                пальцем по маленькой двери попадают не всегда, а с клавиатуры
                в трёхмерную сцену не ткнуть вовсе. */}
            <div className="wt-world__tools">
                {OPENERS.map(({ key, label, short, Icon }) => (
                    <button
                        key={key}
                        type="button"
                        className={`wt-tool${world[key] ? ' is-on' : ''}`}
                        onClick={() => toggle(key)}
                        aria-pressed={Boolean(world[key])}
                    >
                        <Icon size={15} />
                        <span className="wt-tool__full">{label}</span>
                        <span className="wt-tool__short">{short}</span>
                    </button>
                ))}
            </div>

            {/* ── Телефон в руках ───────────────────────────────────────────
                Корпус лежит поверх сцены и не двигается: человек держит его
                перед собой, а обходит машину сам. Экран прозрачный — сквозь
                него видно ту же сцену. */}
            <div className="wt-hands">
                <span className="wt-hands__thumb wt-hands__thumb--left" aria-hidden="true" />
                <span className="wt-hands__thumb wt-hands__thumb--right" aria-hidden="true" />
                <div className="wt-hands__phone">
                    <div className="wt-cam" ref={screenRef}>
                        <div className="wt-cam__top">
                            <span className="wt-cam__app">Яндекс Про · Фотоконтроль</span>
                        </div>

                        {/* Рамка-подсказка, как в Таксометре: она и говорит, что
                            машина должна попадать в кадр целиком. */}
                        <div className={`wt-cam__frame${framingNote ? ' is-warn' : ''}`}
                            aria-hidden="true">
                            <i /><i /><i /><i />
                        </div>

                        <div className="wt-cam__read" aria-live="polite">
                            <b>В кадре: {viewName}</b>
                            {framingNote && <span>{framingNote}</span>}
                        </div>

                        {/* Галерея и фронтальная камера — настоящие кнопки, а не
                            украшение: на них висят ловушки сценария. Оба действия
                            фотоконтроль заворачивает, и узнать об этом человек
                            должен здесь, а не на реальной проверке. */}
                        <div className="wt-cam__bar">
                            <button
                                type="button"
                                className="wt-cam__icon"
                                onClick={() => tap('gallery')}
                                aria-label="Выбрать фото из галереи"
                            >
                                <Images size={17} />
                            </button>
                            <button
                                type="button"
                                className={`wt-shutter${target === 'shutter' ? ' is-target' : ''}`}
                                onClick={shoot}
                                disabled={!ready}
                                aria-label="Снять кадр"
                            >
                                <Camera size={20} />
                            </button>
                            <button
                                type="button"
                                className="wt-cam__icon"
                                onClick={() => tap('switch_camera')}
                                aria-label="Переключить на фронтальную камеру"
                            >
                                <SwitchCamera size={17} />
                            </button>
                        </div>
                    </div>
                </div>
            </div>

            <p className="wt-world__hint">
                Тяните мышью, чтобы обойти машину · колесо или тяга вверх-вниз — подойти ближе
            </p>

            {/* Атрибуция обязательна по лицензии модели (CC BY): её автора надо
                назвать везде, где модель показывается. */}
            <p className="wt-world__credit">3D-модель: CCamo · CC BY</p>
        </div>
    );
}
