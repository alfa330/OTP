import React, { useEffect, useReducer, useRef, useState } from 'react';
import './pumpkin-mascot.css';
import bodyUrl from './assets/mascot-body.webp';
import logoUrl from './assets/mascot-logo.webp';
import faceNormal from './assets/mascot-face-normal.webp';
import faceJoy from './assets/mascot-face-joy.webp';
import faceAnger from './assets/mascot-face-anger.webp';
import faceSad from './assets/mascot-face-sad.webp';
import faceSurprise from './assets/mascot-face-surprise.webp';
import faceWink from './assets/mascot-face-wink.webp';
import faceSleep from './assets/mascot-face-sleep.webp';
import faceConfusion from './assets/mascot-face-confusion.webp';
import faceLove from './assets/mascot-face-love.webp';
import faceFear from './assets/mascot-face-fear.webp';
import faceCool from './assets/mascot-face-cool.webp';
import faceNeutral from './assets/mascot-face-neutral.webp';
import {
    EMOTIONS,
    SLEEP_AFTER_MS,
    displayEmotion,
    initialMood,
    lookVector,
    moodReducer,
} from './mascotMood';

/* Хэллоуин-2026: шарик помощника становится тыквой из Blender (тело и 12 лиц
 * — scripts/halloween/halloween_models.py, сцена mascot). Лица лежат на теле
 * отдельными картинками: лицо смещается к курсору, тыква чуть наклоняется
 * следом — «следит», а эмоция меняется подменой лица.
 *
 * Помощника тыква не трогает: открытие, перетаскивание и окно делает кнопка
 * AssistantOrb, а тыква только слушает её (наведение, нажатие, класс
 * aorb-dragging, aria-expanded) — так ни один жест шарика не меняется.
 */

const FACE_URLS = {
    normal: faceNormal,
    joy: faceJoy,
    anger: faceAnger,
    sad: faceSad,
    surprise: faceSurprise,
    wink: faceWink,
    sleep: faceSleep,
    confusion: faceConfusion,
    love: faceLove,
    fear: faceFear,
    cool: faceCool,
    neutral: faceNeutral,
};

/* Значки вокруг тыквы — как на набросках: сердечки, «Zzz», «?», «!»… */
function Extras({ emotion }) {
    switch (emotion) {
        case 'joy':
            return (
                <svg className="hw-mascot__extras" viewBox="-20 -20 140 140" aria-hidden="true">
                    <path className="hw-x hw-x--float" d="M6 30c-3-4 2-8 5-4 3-4 8 0 5 4l-5 5z" />
                    <path className="hw-x hw-x--float hw-x--late" d="M90 16c-3-4 2-8 5-4 3-4 8 0 5 4l-5 5z" />
                </svg>
            );
        case 'love':
            return (
                <svg className="hw-mascot__extras" viewBox="-20 -20 140 140" aria-hidden="true">
                    <path className="hw-x hw-x--rise" d="M-2 36c-4-5 3-10 7-5 4-5 11 0 7 5l-7 7z" />
                    <path className="hw-x hw-x--rise hw-x--late" d="M92 26c-4-5 3-10 7-5 4-5 11 0 7 5l-7 7z" />
                    <path className="hw-x hw-x--rise hw-x--later" d="M78 -6c-3-4 2-8 5-4 3-4 8 0 5 4l-5 5z" />
                </svg>
            );
        case 'anger':
            return (
                <svg className="hw-mascot__extras" viewBox="-20 -20 140 140" aria-hidden="true">
                    <path className="hw-x hw-x--stroke hw-x--pulse" d="M-4 8l6-4 2 6 6-4M2 18l6-2" />
                    <path className="hw-x hw-x--stroke hw-x--pulse hw-x--late" d="M104 8l-6-4-2 6-6-4M98 18l-6-2" />
                </svg>
            );
        case 'sad':
            return (
                <svg className="hw-mascot__extras" viewBox="-20 -20 140 140" aria-hidden="true">
                    <path className="hw-x hw-x--tear" d="M22 50c3 5 5 8 5 11a5 5 0 0 1-10 0c0-3 2-6 5-11z" />
                </svg>
            );
        case 'surprise':
        case 'fear':
            return (
                <svg className="hw-mascot__extras" viewBox="-20 -20 140 140" aria-hidden="true">
                    {emotion === 'surprise' ? (
                        <>
                            <path className="hw-x hw-x--pop" d="M-8 22h6l-1 16h-4zM-6.5 42a3 3 0 1 0 .1 0z" />
                            <path className="hw-x hw-x--pop hw-x--late" d="M104 18h6l-1 16h-4zM105.5 38a3 3 0 1 0 .1 0z" />
                        </>
                    ) : (
                        <>
                            <path className="hw-x hw-x--stroke hw-x--jitter" d="M-6 30q3 4 0 8t0 8M-12 34q3 4 0 8" />
                            <path className="hw-x hw-x--stroke hw-x--jitter hw-x--late" d="M106 30q-3 4 0 8t0 8M112 34q-3 4 0 8" />
                        </>
                    )}
                </svg>
            );
        case 'wink':
        case 'cool':
            return (
                <svg className="hw-mascot__extras" viewBox="-20 -20 140 140" aria-hidden="true">
                    <path className="hw-x hw-x--twinkle" d="M96 4l2.6 6.4L105 13l-6.4 2.6L96 22l-2.6-6.4L87 13l6.4-2.6z" />
                </svg>
            );
        case 'sleep':
            return (
                <svg className="hw-mascot__extras" viewBox="-20 -20 140 140" aria-hidden="true">
                    <text className="hw-x hw-x--zzz" x="84" y="14">Z</text>
                    <text className="hw-x hw-x--zzz hw-x--late" x="98" y="0">z</text>
                    <text className="hw-x hw-x--zzz hw-x--later" x="108" y="-12">z</text>
                </svg>
            );
        case 'confusion':
            return (
                <svg className="hw-mascot__extras" viewBox="-20 -20 140 140" aria-hidden="true">
                    <text className="hw-x hw-x--wobble" x="88" y="8">?</text>
                    <text className="hw-x hw-x--wobble hw-x--late" x="102" y="24">?</text>
                </svg>
            );
        case 'neutral':
            return (
                <svg className="hw-mascot__extras" viewBox="-20 -20 140 140" aria-hidden="true">
                    <circle className="hw-x hw-x--dots" cx="100" cy="48" r="2.6" />
                    <circle className="hw-x hw-x--dots hw-x--late" cx="108" cy="48" r="2.6" />
                    <circle className="hw-x hw-x--dots hw-x--later" cx="116" cy="48" r="2.6" />
                </svg>
            );
        default:
            return null;
    }
}

export default function PumpkinMascot({ variant = 'orb', animated = true }) {
    const rootRef = useRef(null);
    const [mood, dispatch] = useReducer(moodReducer, undefined, initialMood);
    const [, setTick] = useState(0);
    const interactive = variant !== 'mini';

    /* Подмена лица по истечении временной эмоции — перерисовка ровно в срок. */
    useEffect(() => {
        const until = mood.override?.until;
        if (!until || !Number.isFinite(until)) return undefined;
        const timer = window.setTimeout(() => setTick((n) => n + 1), Math.max(0, until - Date.now()) + 16);
        return () => window.clearTimeout(timer);
    }, [mood.override]);

    useEffect(() => {
        const root = rootRef.current;
        if (!root || !interactive) return undefined;
        const target = root.closest('.aorb-button') || root;
        const now = () => Date.now();
        let lastDragEnd = 0;
        let dragging = target.classList.contains('aorb-dragging');
        let expanded = target.getAttribute('aria-expanded') === 'true';

        const onEnter = () => dispatch({ type: 'enter', now: now() });
        const onLeave = () => dispatch({ type: 'leave', now: now() });
        const onDown = () => dispatch({ type: 'press', now: now() });
        const onClick = () => {
            if (now() - lastDragEnd < 150) return;  // это был конец перетаскивания
            dispatch({ type: 'click', now: now() });
        };
        target.addEventListener('pointerenter', onEnter);
        target.addEventListener('pointerleave', onLeave);
        target.addEventListener('pointerdown', onDown);
        target.addEventListener('click', onClick);

        const observer = new MutationObserver(() => {
            const nextDragging = target.classList.contains('aorb-dragging');
            if (nextDragging !== dragging) {
                dragging = nextDragging;
                if (dragging) dispatch({ type: 'dragStart', now: now() });
                else {
                    lastDragEnd = now();
                    dispatch({ type: 'dragEnd', now: now() });
                }
            }
            const nextExpanded = target.getAttribute('aria-expanded') === 'true';
            if (nextExpanded !== expanded) {
                if (expanded && !nextExpanded) dispatch({ type: 'close', now: now() });
                expanded = nextExpanded;
            }
        });
        if (target !== root) observer.observe(target, { attributes: true, attributeFilter: ['class', 'aria-expanded'] });

        /* Взгляд — мимо React: две переменные CSS раз в кадр. */
        let frame = 0;
        let pointer = null;
        const applyLook = () => {
            frame = 0;
            if (!pointer) return;
            const rect = root.getBoundingClientRect();
            const look = lookVector(pointer.x - (rect.left + rect.width / 2), pointer.y - (rect.top + rect.height / 2));
            root.style.setProperty('--hw-look-x', look.x.toFixed(3));
            root.style.setProperty('--hw-look-y', look.y.toFixed(3));
        };
        let idleTimer = 0;
        const armSleep = () => {
            window.clearTimeout(idleTimer);
            idleTimer = window.setTimeout(() => dispatch({ type: 'sleep', now: now() }), SLEEP_AFTER_MS);
        };
        const onMove = (event) => {
            pointer = { x: event.clientX, y: event.clientY };
            if (!frame) frame = window.requestAnimationFrame(applyLook);
            dispatch({ type: 'wake', now: now() });
            armSleep();
        };
        const onOut = (event) => {
            if (event.relatedTarget) return;      // ушли не за окно, а на другой элемент
            pointer = null;
            root.style.setProperty('--hw-look-x', '0');
            root.style.setProperty('--hw-look-y', '0');
        };
        const onKey = () => {
            dispatch({ type: 'wake', now: now() });
            armSleep();
        };
        window.addEventListener('pointermove', onMove, { passive: true });
        document.addEventListener('pointerout', onOut);
        window.addEventListener('keydown', onKey, { passive: true });
        armSleep();

        return () => {
            target.removeEventListener('pointerenter', onEnter);
            target.removeEventListener('pointerleave', onLeave);
            target.removeEventListener('pointerdown', onDown);
            target.removeEventListener('click', onClick);
            observer.disconnect();
            window.removeEventListener('pointermove', onMove);
            document.removeEventListener('pointerout', onOut);
            window.removeEventListener('keydown', onKey);
            window.cancelAnimationFrame(frame);
            window.clearTimeout(idleTimer);
        };
    }, [interactive]);

    if (!interactive) {
        return (
            <span className="hw-mascot hw-mascot--mini" aria-hidden="true">
                <img className="hw-mascot__body" src={logoUrl} alt="" draggable={false} />
            </span>
        );
    }

    const emotion = displayEmotion(mood, Date.now());
    return (
        <span
            ref={rootRef}
            className={`hw-mascot hw-mascot--${variant} is-${emotion}${animated ? '' : ' is-paused'}`}
            aria-hidden="true"
        >
            <span className="hw-mascot__breathe">
                <span className="hw-mascot__tilt">
                    <img className="hw-mascot__body" src={bodyUrl} alt="" draggable={false} />
                    <span className="hw-mascot__face">
                        {EMOTIONS.map((name) => (
                            <img
                                key={name}
                                className={name === emotion ? 'is-on' : undefined}
                                src={FACE_URLS[name]}
                                alt=""
                                draggable={false}
                            />
                        ))}
                    </span>
                </span>
            </span>
            <Extras key={emotion} emotion={emotion} />
        </span>
    );
}
