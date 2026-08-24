import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
    ArrowLeft, DoorOpen, Package, RefreshCw, SwitchCamera, X, Zap,
} from 'lucide-react';

import { createCarScene } from './carScene';
import { readFrame } from './carFraming';
import { SHOTS, nextShot, slotTap } from './photoShots';
import ShotOutline from './photoOutlines';
import { StatusIcons, StatusTime } from './PhoneChrome';

/* Экран фотоконтроля: человек стоит у машины с телефоном в руках.
 *
 * Раскладка выбрана по требованию владельца и отличается от остальных
 * тренажёров осознанно. В них главный объект — телефон, и мир существует
 * только на его экране. Здесь наоборот: главное — МАШИНА, вокруг которой надо
 * обойти, а телефон человек держит перед собой. Поэтому сцена занимает всё
 * поле, а телефон лежит поверх неё снизу — статично, как в руках.
 *
 * В телефоне два экрана, и они чередуются ровно как в Яндекс Про:
 *
 *   СПИСОК «Фотоконтроль машины» — сетка из семи плиток с подписями. Он
 *   непрозрачный: это обычный экран приложения, сквозь него двор не виден.
 *   Снятый кадр показывает в плитке НАСТОЯЩУЮ миниатюру из объектива — так же,
 *   как приложение показывает сделанный снимок.
 *
 *   ВИДОИСКАТЕЛЬ — прозрачный: через него видно ту же сцену, потому что это и
 *   есть камера. Поверх лежит силуэт нужного кадра, подпись и кнопки.
 *
 * Пять кузовных кадров приложение просит снимать ГОРИЗОНТАЛЬНО: силуэт и
 * подпись на экране развёрнуты на 90°, а сам интерфейс остаётся портретным.
 * Повторено буквально — эта развёрнутая подпись и есть единственная просьба
 * повернуть телефон, других слов про это фотоконтроль не говорит.
 */

/* Подписи ракурсов для учебной строки рядом со сценой — теми же словами, что и
   в репликах барса. Разойдутся, и человек будет искать «корму», читая про
   «зад». */
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

/* Что можно открыть руками. Порядок — как в списке кадров: сначала двери,
   потом багажник. Иконки разные, потому что подписи на узком экране прячутся. */
const OPENERS = [
    { key: 'doorFrontLeft', label: 'Водительская дверь', short: 'Перед.', Icon: DoorOpen },
    { key: 'doorRearLeft', label: 'Задняя дверь', short: 'Задняя', Icon: DoorOpen },
    { key: 'trunkOpen', label: 'Багажник', short: 'Багажник', Icon: Package },
];

const MODEL_URL = `${import.meta.env.BASE_URL || '/'}models/vento.glb`;

/* Часы телефона идут настоящие: на учебном экране это единственное место, где
   время вообще упоминается, и остановившиеся часы сразу выдают картинку. */
const clock = () => {
    const now = new Date();
    return `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
};

export default function CarStage({
    world, tap, toggle, target, screen = 'pc_camera',
    car = 'Volkswagen Vento', plate = '000 XXX 02',
}) {
    const canvasRef = useRef(null);
    const sceneRef = useRef(null);
    const screenRef = useRef(null);
    const dragRef = useRef(null);
    const [ready, setReady] = useState(false);
    const [failed, setFailed] = useState(false);
    const [frame, setFrame] = useState({ view: null, framing: 'ok' });

    const listMode = screen === 'pc_list';
    /* Какой кадр снимаем. Считаем по миру, а не по номеру шага: снятым кадр
       становится, только когда затвор ЗАЧТЁН, и список с видоискателем обязаны
       думать об этом одинаково. */
    const shot = nextShot(world);
    /* Кузовной кадр — значит телефон В РУКАХ повёрнут горизонтально.
     *
     * Раньше поворачивался только силуэт внутри вертикального корпуса — ровно
     * так, как это нарисовано в самом приложении. Но приложение показывает
     * развёрнутый силуэт, чтобы человек ПОВЕРНУЛ телефон, а в тренажёре
     * поворачивать было нечего: корпус оставался вертикальным, и силуэт стоял
     * поперёк машины в кадре. Теперь поворачивается корпус — и силуэт, и кадр
     * встают вдоль машины, как в руках у водителя. */
    const turned = !listMode && shot?.hold === 'landscape';

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
     * вместе с раскладкой, и разъехавшись на пиксель, кадр вылезет за рамку.
     *
     * На экране СПИСКА прямоугольник снимается вовсе: список непрозрачный, и
     * рисовать под ним второй кадр — платить за то, чего не видно. */
    const syncPhoneRect = useCallback(() => {
        const scene = sceneRef.current;
        const canvas = canvasRef.current;
        const inner = screenRef.current;
        if (!scene || !canvas) return;
        if (!inner || listMode) { scene.setPhoneRect(null); return; }
        const box = canvas.getBoundingClientRect();
        const rect = inner.getBoundingClientRect();
        scene.setPhoneRect({
            x: rect.left - box.left,
            y: rect.top - box.top,
            w: rect.width,
            h: rect.height,
        });
    }, [listMode]);

    /* Что сейчас в объективе — считает чистый модуль, сцена лишь сообщает, где
       стоит человек. Здесь только учебная строка рядом со сценой; в сценарий
       это же уходит вместе с нажатием затвора. */
    const refreshFrame = useCallback(() => {
        const scene = sceneRef.current;
        if (!scene?.isReady()) return;
        setFrame(readFrame(scene.camera(), world));
    }, [world]);

    /* Первый замер — после того, как раскладка встала: на монтировании корпус
       ещё не имеет размеров, и прямоугольник вышел бы нулевым. Пересчитываем и
       при смене экрана: список прямоугольник убирает, камера возвращает. */
    useEffect(() => {
        const id = requestAnimationFrame(syncPhoneRect);
        return () => cancelAnimationFrame(id);
        // turned в зависимостях обязателен: повёрнутый корпус — другой
        // прямоугольник экрана, и без пересчёта кадр рисовался бы в прежний.
    }, [syncPhoneRect, ready, turned]);

    // Мир поменялся (открыли дверь, переключили ширик) — сцена следом.
    useEffect(() => {
        const scene = sceneRef.current;
        if (!scene) return;
        OPENERS.forEach(({ key }) => scene.setOpen(key, Boolean(world[key])));
        scene.setPhoneWide(Boolean(world.wide));
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
       и подходят ближе. */
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

    /* Спуск. Вместе с кадром уезжает миниатюра: приложение кладёт в плитку
       снимок, и тренажёр обязан класть его же — по этим картинкам человек
       потом и видит, что именно он отправил. */
    const shoot = () => {
        const scene = sceneRef.current;
        if (!scene?.isReady()) return;
        tap('shutter', {
            ...readFrame(scene.camera(), world),
            ...scene.camera(),
            wide: Boolean(world.wide),
            thumb: scene.snapshot(),
        });
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

            {/* Учебная подсказка про кадр — НАША, не приложения, поэтому она
                снаружи корпуса: в 3D-сцене мышью не чувствуешь ни ракурса, ни
                дистанции, и без неё человек крутил бы машину наугад. */}
            {!listMode && (
                <div className={`wt-world__read${framingNote ? ' is-warn' : ''}`} aria-live="polite">
                    <b>В кадре: {viewName}</b>
                    {framingNote && <span>{framingNote}</span>}
                </div>
            )}

            {/* ── Телефон в руках ───────────────────────────────────────────
                Корпус лежит поверх сцены и не двигается: человек держит его
                перед собой, а обходит машину сам. */}
            <div className={`wt-hands${turned ? ' is-turned' : ''}`}>
                <span className="wt-hands__thumb wt-hands__thumb--left" aria-hidden="true" />
                <span className="wt-hands__thumb wt-hands__thumb--right" aria-hidden="true" />
                <div className="wt-hands__phone">
                    <div className="wt-phone-screen" ref={screenRef}>
                        {listMode ? (
                            <ShotList
                                world={world}
                                tap={tap}
                                target={target}
                                car={car}
                                plate={plate}
                                shot={shot}
                            />
                        ) : (
                            <Viewfinder
                                shot={shot}
                                world={world}
                                tap={tap}
                                toggle={toggle}
                                target={target}
                                ready={ready}
                                onShoot={shoot}
                            />
                        )}
                    </div>
                </div>
            </div>

            <p className="wt-world__hint">
                {listMode
                    ? 'Плитка открывает камеру · машину рядом можно обойти и открыть двери'
                    : 'Тяните мышью, чтобы обойти машину · колесо или тяга вверх-вниз — подойти ближе'}
            </p>

            {/* Атрибуция обязательна по лицензии модели (CC BY): её автора надо
                назвать везде, где модель показывается. */}
            <p className="wt-world__credit">3D-модель: CCamo · CC BY</p>
        </div>
    );
}

/* ── Экран «Фотоконтроль машины» ──────────────────────────────────────────
   Тот самый список, с которого фотоконтроль начинается и которым кончается.
   Здесь видно главное, чего прежний тренажёр не показывал вовсе: ЧТО ИМЕННО у
   водителя запрашивают — семь названных кадров, а не «фото машины». */
function ShotList({ world, tap, target, car, plate, shot }) {
    return (
        <div className="wt-pc">
            {/* Строка состояния — часть телефона, а не приложения, но без неё
                тёмный экран списка читается как макет, а не как «мой телефон». */}
            <div className="wt-pc__status" aria-hidden="true">
                <StatusTime time={clock()} />
                <StatusIcons battery={74} />
            </div>

            <div className="wt-pc__head">
                <button
                    type="button"
                    className="wt-pc__back"
                    onClick={() => tap('back')}
                    aria-label="Назад"
                >
                    <ArrowLeft size={19} />
                </button>
            </div>

            <div className="wt-pc__scroll">
                <h3 className="wt-pc__title">Фотоконтроль машины</h3>
                <p className="wt-pc__lead">
                    {`Отправьте фото машины ${car} ${plate}, чтобы получить доступ к заказам.`}
                </p>

                <div className="wt-pc__grid">
                    {SHOTS.map((item) => {
                        const done = (world.shots || {})[item.key];
                        const isNext = shot?.key === item.key;
                        const isTarget = target === slotTap(item.key);
                        return (
                            <div className="wt-pc__cell" key={item.key}>
                                <button
                                    type="button"
                                    className={`wt-pc__slot${done ? ' is-done' : ''}`
                                        + `${isNext ? ' is-next' : ''}${isTarget ? ' is-target' : ''}`}
                                    onClick={() => tap(slotTap(item.key))}
                                    aria-label={`${item.title}${done ? ' — снято' : ''}`}
                                >
                                    {typeof done === 'string' ? (
                                        /* Настоящая миниатюра из объектива —
                                           ровно то, что показывает приложение. */
                                        <img src={done} alt="" />
                                    ) : (
                                        <span className={`wt-pc__plus${done ? ' is-done' : ''}`}>
                                            {done ? '✓' : '+'}
                                        </span>
                                    )}
                                </button>
                                <small>{item.title}</small>
                            </div>
                        );
                    })}
                </div>
            </div>

            {/* «Далее» жёлтая и активная даже с пустыми плитками — как в
                приложении. Что комплект неполный, водитель узнаёт по нажатию,
                и в тренажёре это ловушка, а не заблокированная кнопка. */}
            <button
                type="button"
                className={`wt-pc__next${target === 'next' ? ' is-target' : ''}`}
                onClick={() => tap('next')}
            >
                Далее
            </button>
        </div>
    );
}

/* ── Видоискатель ─────────────────────────────────────────────────────────
   Прозрачный: сквозь него видно ту же сцену. Кузовные кадры приложение просит
   снимать горизонтально — силуэт и подпись развёрнуты, кнопки остаются внизу. */
function Viewfinder({ shot, world, tap, toggle, target, ready, onShoot }) {
    const landscape = shot ? shot.hold === 'landscape' : false;

    const zoom = (
        <button
            key="zoom"
            type="button"
            className={`wt-cam__pill${world.wide ? ' is-on' : ''}`}
            onClick={() => toggle('wide')}
            aria-pressed={Boolean(world.wide)}
            aria-label="Сверхширокоугольная камера 0,5x"
        >
            0.5x
        </button>
    );
    const flash = (
        <button
            key="flash"
            type="button"
            className={`wt-cam__pill${world.flash ? ' is-on' : ''}`}
            onClick={() => toggle('flash')}
            aria-pressed={Boolean(world.flash)}
            aria-label="Вспышка"
        >
            <Zap size={15} />
        </button>
    );
    const flip = (
        <button
            key="flip"
            type="button"
            className="wt-cam__pill"
            onClick={() => tap('switch_camera')}
            aria-label="Переключить на фронтальную камеру"
        >
            <SwitchCamera size={15} />
        </button>
    );
    const shutter = (
        <button
            key="shutter"
            type="button"
            className={`wt-shutter${target === 'shutter' ? ' is-target' : ''}`}
            onClick={onShoot}
            disabled={!ready}
            aria-label={shot ? `Снять кадр «${shot.title}»` : 'Снять кадр'}
        />
    );

    return (
        <div className={`wt-cam wt-cam--${landscape ? 'landscape' : 'portrait'}`}>
            {/* Крестик стоит там же, где в приложении: у кузовных кадров — в
                правом верхнем углу развёрнутого экрана, у салонных — слева от
                подписи. */}
            <div className="wt-cam__head">
                <button
                    type="button"
                    className="wt-cam__close"
                    onClick={() => tap('close_camera')}
                    aria-label="Закрыть камеру"
                >
                    <X size={19} />
                </button>
                {!landscape && shot && <span className="wt-cam__title">{shot.title}</span>}
            </div>

            {shot && (
                <ShotOutline kind={shot.outline} flip={shot.key === 'right'} rotate={landscape} />
            )}

            {/* Подпись вдоль края — она и есть просьба повернуть телефон. */}
            {landscape && shot && <span className="wt-cam__side">{shot.title}</span>}

            <div className="wt-cam__bar">
                {landscape ? [shutter, zoom, flash, flip] : [zoom, shutter, flash, flip]}
            </div>
        </div>
    );
}
