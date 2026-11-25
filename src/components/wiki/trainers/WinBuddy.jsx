import React, { useEffect, useRef, useState } from 'react';

/* Барс в среде Windows — помощник, который можно переставить.
 *
 * Владелец, 25.11.2026: «маскот должен провожать — в месте, где он не будет
 * мешать, и его можно перемещать как помощника». Поэтому:
 *   — стоит слева внизу над панелью задач (там у CRM низ меню, не работа);
 *   — перетаскивается мышью за себя или за заголовок облачка, место
 *     запоминается (localStorage — удобство одного человека, без него просто
 *     встаёт на место по умолчанию);
 *   — щелчок по Барсу сворачивает и раскрывает облачко;
 *   — говорит по ходу ЗВОНКА (tipForCall): шагов урока в смене нет.
 */

const STORE = 'wt-buddy-pos';
const readPos = () => {
    try {
        const raw = window.localStorage.getItem(STORE);
        const pos = raw ? JSON.parse(raw) : null;
        return pos && Number.isFinite(pos.left) && Number.isFinite(pos.top) ? pos : null;
    } catch {
        return null;
    }
};
const savePos = (pos) => {
    try { window.localStorage.setItem(STORE, JSON.stringify(pos)); } catch { /* без памяти — по умолчанию */ }
};

/** Что сказать по ходу звонка. call — состояние машины звонка, phone — номер звонящего. */
export const tipForCall = (call = {}) => {
    const phone = call.phone || 'из звонка';
    switch (call.state) {
    case 'ringing':
        return {
            title: 'Звонит водитель',
            text: `Ответь в окне «Входящий вызов». Номер ${phone} — по нему найдёшь водителя в CRM.`,
        };
    case 'talking':
        return {
            title: 'Разговор',
            text: `Представься и выслушай. Найди водителя: «Водители» → «Учетные записи водителей», поиск по номеру ${phone}. `
                + 'Главное водитель сам не скажет — уточняй и смотри карточку, ведомость и Диспетчерскую.',
        };
    case 'held':
        return { title: 'Удержание', text: 'Водитель ждёт. Не держи дольше минуты и скажи, когда вернёшься.' };
    case 'wrapup':
        return {
            title: 'Постобработка',
            text: 'Оформи обращение во вкладке «Обращения». Если вопрос решает только Яндекс — вкладка «Обращения в Яндекс». '
                + 'Потом — «Завершить постобработку» в iCORE Phone.',
        };
    case 'ended':
        return call.reason === 'rejected'
            ? { title: 'Вызов отклонён', text: 'На смене так нельзя. Следующий звонок — «Новый звонок…» в меню «Учебная смена».' }
            : { title: 'Звонок завершён', text: 'Разбор — в окне поверх. Следующий водитель — «Новый звонок…» в меню «Учебная смена».' };
    default:
        return {
            title: 'Ты на линии',
            text: 'Звонок придёт в iCORE Phone через несколько секунд — ответь и выслушай водителя. Меня можно перетащить.',
        };
    }
};

export default function WinBuddy({ leo, tip }) {
    const boxRef = useRef(null);
    const drag = useRef(null);
    const [pos, setPos] = useState(() => (typeof window === 'undefined' ? null : readPos()));
    const [open, setOpen] = useState(true);

    // Новая подсказка — облачко раскрывается: её надо увидеть.
    useEffect(() => { setOpen(true); }, [tip?.title]);

    // Сохранённое место могло остаться от экрана побольше — не уходим за край.
    useEffect(() => {
        const box = boxRef.current;
        const parent = box?.offsetParent;
        if (!box || !parent || !pos) return;
        const left = Math.min(Math.max(0, pos.left), Math.max(0, parent.clientWidth - box.offsetWidth));
        const top = Math.min(Math.max(0, pos.top), Math.max(0, parent.clientHeight - box.offsetHeight));
        if (left !== pos.left || top !== pos.top) setPos({ left, top });
    }, [pos]);

    const onPointerDown = (event) => {
        if (event.button !== 0) return;
        const box = boxRef.current;
        if (!box) return;
        drag.current = {
            x: event.clientX, y: event.clientY, left: box.offsetLeft, top: box.offsetTop, moved: false,
        };
        event.currentTarget.setPointerCapture?.(event.pointerId);
    };
    const onPointerMove = (event) => {
        const d = drag.current;
        const box = boxRef.current;
        if (!d || !box) return;
        const dx = event.clientX - d.x;
        const dy = event.clientY - d.y;
        if (!d.moved && Math.hypot(dx, dy) < 4) return;
        d.moved = true;
        const parent = box.offsetParent;
        const maxX = parent ? parent.clientWidth - box.offsetWidth : Infinity;
        const maxY = parent ? parent.clientHeight - box.offsetHeight : Infinity;
        setPos({
            left: Math.min(Math.max(0, d.left + dx), Math.max(0, maxX)),
            top: Math.min(Math.max(0, d.top + dy), Math.max(0, maxY)),
        });
    };
    const onPointerUp = (toggle) => () => {
        const d = drag.current;
        drag.current = null;
        if (!d) return;
        if (d.moved) {
            const box = boxRef.current;
            if (box) savePos({ left: box.offsetLeft, top: box.offsetTop });
        } else if (toggle) {
            setOpen((value) => !value);
        }
    };

    const handle = (toggle) => ({
        onPointerDown,
        onPointerMove,
        onPointerUp: onPointerUp(toggle),
        onPointerCancel: () => { drag.current = null; },
    });

    return (
        <aside ref={boxRef} className={`wt-buddy${open ? '' : ' is-folded'}${pos ? ' is-placed' : ''}`}
            style={pos ? { left: pos.left, top: pos.top } : null} aria-label="Барс — помощник"
            data-tip={tip?.title || ''}>
            {open && tip ? (
                <div className="wt-buddy__cloud" role="status">
                    <div className="wt-buddy__head" {...handle(false)}>
                        <span>Барс</span>
                        <i aria-hidden="true">⠿</i>
                    </div>
                    <b>{tip.title}</b>
                    <p>{tip.text}</p>
                </div>
            ) : null}
            <button type="button" className="wt-buddy__leo" {...handle(true)}
                aria-expanded={open} title={open ? 'Свернуть подсказку · перетащите, чтобы переставить'
                    : 'Показать подсказку · перетащите, чтобы переставить'}>
                {leo}
            </button>
        </aside>
    );
}
