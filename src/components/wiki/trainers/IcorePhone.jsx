import React, { useEffect, useRef, useState } from 'react';

import { isLive, talkClock, talkMs } from './callMachine.js';

/* Учебный iCORE Phone — звонилка рабочего места оператора в среде Windows.
 *
 * Владелец, 25.11.2026: «звонилка наш айкорфон». Окно повторяет настоящий
 * iCORE Phone (форк MicroSIP, iCORE-Microsip/microsip-src): узкое окно с
 * плашкой статуса iCORE («Активный» — зелёный, как в ICoreStatus.h),
 * вкладками «Набор / Вызовы / Контакты», полем номера, клавиатурой 3×4 и
 * рядом R + C, кнопками «Вызов» и «Завершить» и регуляторами динамика и
 * микрофона внизу. Входящий звонок, как и там, — отдельное окошко «Входящий
 * вызов» с «Ответить» и «Отклонить».
 *
 * Логика звонка — та же машина (callMachine.js), что у плашки CallPanel:
 * меняется только вид. Пометка «Говорит ИИ» — всё время разговора: закон
 * РК «Об искусственном интеллекте» (ст. 21) требует маркировать ИИ.
 */

/* Глифы — тонкие линии в духе Segoe Fluent: сам шрифт есть только в Windows. */
const Glyph = ({ name, size = 16 }) => {
    const d = {
        phone: 'M6.5 3.5 9 8l-2 2a12 12 0 0 0 5 5l2-2 4.5 2.5-1 3a2 2 0 0 1-2 1.4C7.7 19.4 4.6 16.3 3.1 6.5A2 2 0 0 1 4.5 4.5Z',
        hangup: 'M3 10a14 14 0 0 1 18 0l-2.5 3-4-1.2V9a10 10 0 0 0-5 0v2.8L5.5 13Z',
        pause: 'M9 5v14M15 5v14',
        play: 'M7 4l12 8-12 8Z',
        transfer: 'M4 8h12l-3-3m3 11H4l3 3',
        mic: 'M12 3a3 3 0 0 1 3 3v5a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3ZM5 11a7 7 0 0 0 14 0M12 18v3',
        micoff: 'M12 3a3 3 0 0 1 3 3v5a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3ZM5 11a7 7 0 0 0 14 0M12 18v3M4 4l16 16',
        speaker: 'M4 9h4l5-4v14l-5-4H4Zm12-1a5 5 0 0 1 0 8',
        speakeroff: 'M4 9h4l5-4v14l-5-4H4Zm12 1 4 4m0-4-4 4',
        menu: 'M4 7h16M4 12h16M4 17h16',
        chat: 'M5 5h14v10H9l-4 4Z',
        min: 'M5 12h14',
        close: 'M6 6l12 12M18 6 6 18',
        caret: 'M7 10l5 5 5-5',
    }[name];
    return (
        <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor"
            strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d={d} />
        </svg>
    );
};

/* Значок приложения: синий квадрат iCORE с трубкой. */
export const IcoreIcon = ({ size = 16 }) => (
    <svg viewBox="0 0 20 20" width={size} height={size} aria-hidden="true">
        <rect width="20" height="20" rx="5" fill="#007aff" />
        <path d="M6.6 5.2 8.4 8.3 7 9.7a8.4 8.4 0 0 0 3.4 3.4l1.4-1.4 3.1 1.8-.7 2.1a1.4 1.4 0 0 1-1.4 1C8.4 16 4.9 12.5 4.3 7.2a1.4 1.4 0 0 1 1-1.4Z"
            fill="#fff" />
    </svg>
);

/* Тон вызова — два синуса WebAudio, как у плашки звонка. */
const useRingTone = (active, muted) => {
    useEffect(() => {
        if (!active || muted) return undefined;
        if (typeof window === 'undefined' || !window.AudioContext) return undefined;
        let ctx;
        try { ctx = new window.AudioContext(); } catch { return undefined; }
        let stopped = false;
        const beep = () => {
            if (stopped || !ctx) return;
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = 'sine';
            osc.frequency.value = 480;
            gain.gain.setValueAtTime(0.0001, ctx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.06, ctx.currentTime + 0.05);
            gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.9);
            osc.connect(gain).connect(ctx.destination);
            osc.start();
            osc.stop(ctx.currentTime + 1);
        };
        beep();
        const loop = setInterval(beep, 2500);
        return () => {
            stopped = true;
            clearInterval(loop);
            try { ctx.close(); } catch { /* уже закрыт */ }
        };
    }, [active, muted]);
};

const useTick = (live) => {
    const [, setTick] = useState(0);
    useEffect(() => {
        if (!live) return undefined;
        const id = setInterval(() => setTick((n) => n + 1), 1000);
        return () => clearInterval(id);
    }, [live]);
};

/* Перетаскивание окна за заголовок — как в Windows; окно не уходит за край. */
export const useDrag = (initial) => {
    const [pos, setPos] = useState(initial);
    const drag = useRef(null);
    const onPointerDown = (event) => {
        if (event.button !== 0 || event.target.closest('button')) return;
        const box = event.currentTarget.closest('[data-win]');
        const parent = box && box.offsetParent;
        if (!box || !parent) return;
        drag.current = {
            dx: event.clientX - box.offsetLeft,
            dy: event.clientY - box.offsetTop,
            maxX: parent.clientWidth - box.offsetWidth,
            maxY: parent.clientHeight - 40,
        };
        event.currentTarget.setPointerCapture?.(event.pointerId);
    };
    const onPointerMove = (event) => {
        const d = drag.current;
        if (!d) return;
        setPos({
            left: Math.min(Math.max(0, event.clientX - d.dx), Math.max(0, d.maxX)),
            top: Math.min(Math.max(0, event.clientY - d.dy), Math.max(0, d.maxY)),
        });
    };
    const onPointerUp = () => { drag.current = null; };
    return { pos, handlers: { onPointerDown, onPointerMove, onPointerUp, onPointerCancel: onPointerUp } };
};

const KEYS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '*', '0', '#', 'R', '+', 'C'];
const STATE_TEXT = {
    offline: 'Не на линии',
    ready: 'Линия свободна',
    ringing: 'Входящий вызов',
    talking: 'Разговор',
    held: 'На удержании',
    wrapup: 'Постобработка',
    ended: 'Вызов завершён',
};

/** Окно «Входящий вызов» — отдельное, как IDD_RINGIN у MicroSIP. */
export const IncomingCall = ({ call, onCall, style = null }) => (
    <div className="wt-icp-ring" data-win="ring" role="dialog" aria-label="Входящий вызов" style={style}>
        <div className="wt-winbar">
            <span className="wt-winbar__icon"><IcoreIcon size={14} /></span>
            <span className="wt-winbar__title">Входящий вызов</span>
            <button type="button" className="wt-winbar__btn is-close" aria-label="Закрыть — не отвечать"
                onClick={() => onCall('reject')}>
                <Glyph name="close" size={12} />
            </button>
        </div>
        <div className="wt-icp-ring__body">
            <div className="wt-icp-ring__pulse" aria-hidden="true"><Glyph name="phone" size={20} /></div>
            <b className="wt-icp-ring__number">{call.phone || 'Номер скрыт'}</b>
            <span className="wt-icp-ring__line">{call.queue || 'Линия водителей'}</span>
            {call.waitedSec ? <span className="wt-icp-ring__wait">в очереди {call.waitedSec} с</span> : null}
            <div className="wt-icp-ring__buttons">
                <button type="button" className="wt-icp-btn is-answer" onClick={() => onCall('answer')}>
                    <Glyph name="phone" /> Ответить
                </button>
                <button type="button" className="wt-icp-btn is-decline" onClick={() => onCall('reject')}>
                    <Glyph name="hangup" /> Отклонить
                </button>
            </div>
        </div>
    </div>
);

/**
 * Окно iCORE Phone.
 *
 * onFinishAttempt — «Завершить постобработку»: попытка закончена, дальше
 * разбор разговора. onMinimize — «свернуть» (значок на панели задач вернёт).
 */
export default function IcorePhone({
    call, onCall, directory = [], aiSpeaking = false, micLevel = 0, micError = '',
    voice = null, onFinishAttempt = null, onMinimize = null, style = null, dragHandlers = null,
}) {
    const [transferOpen, setTransferOpen] = useState(false);
    const [micMuted, setMicMuted] = useState(false);
    const live = isLive(call);
    useRingTone(call.state === 'ringing', call.muted);
    useTick(live);

    return (
        <div className={`wt-icp is-${call.state}`} data-win="phone" role="region" aria-label="iCORE Phone" style={style}>
            <div className="wt-winbar" {...(dragHandlers || {})}>
                <span className="wt-winbar__icon"><IcoreIcon size={14} /></span>
                <span className="wt-winbar__title">iCORE Phone</span>
                {onMinimize ? (
                    <>
                        <button type="button" className="wt-winbar__btn" aria-label="Свернуть" onClick={onMinimize}>
                            <Glyph name="min" size={12} />
                        </button>
                        <button type="button" className="wt-winbar__btn is-close" aria-label="Скрыть в трей"
                            onClick={onMinimize}>
                            <Glyph name="close" size={12} />
                        </button>
                    </>
                ) : null}
            </div>

            {/* Плашка статуса iCORE: «Активный» — на линии, звонки идут. */}
            <div className="wt-icp__status">
                <span className="wt-icp__pill">
                    <i className={`wt-icp__dot is-${live || call.state === 'ringing' ? 'busy' : 'ready'}`} />
                    {live || call.state === 'ringing' ? 'Активный · занят' : 'Активный'}
                    <Glyph name="caret" size={12} />
                </span>
                <span className="wt-icp__menu" aria-hidden="true"><Glyph name="menu" size={15} /></span>
            </div>

            <div className="wt-icp__tabs" role="tablist" aria-label="Разделы iCORE Phone">
                <span className="is-active" role="tab" aria-selected="true">Набор</span>
                <span role="tab" aria-selected="false">Вызовы</span>
                <span role="tab" aria-selected="false">Контакты</span>
            </div>

            <div className="wt-icp__number">
                <span>{call.phone || ''}</span>
                <small>{STATE_TEXT[call.state] || ''}{live ? ` · ${talkClock(talkMs(call))}` : ''}</small>
            </div>

            {live ? (
                <div className="wt-icp__talk">
                    {/* Маркировка ИИ — всё время разговора, по закону РК. */}
                    <span className="wt-icp__ai">Говорит ИИ</span>
                    <div className={`wt-icp__wave${aiSpeaking ? ' is-on' : ''}`} aria-hidden="true">
                        {[0, 1, 2, 3, 4, 5, 6].map((n) => <i key={n} style={{ animationDelay: `${n * 0.09}s` }} />)}
                    </div>
                    <span className="wt-icp__who">
                        {call.state === 'held' ? 'Водитель ждёт на удержании'
                            : aiSpeaking ? 'Говорит водитель' : (voice ? 'Слушаю вас' : 'Голос не подключён')}
                    </span>
                    <span className="wt-icp__mic" aria-hidden="true">
                        <Glyph name={micMuted ? 'micoff' : 'mic'} size={14} />
                        <i style={{ width: `${Math.round(Math.min(1, Math.max(0, micMuted ? 0 : micLevel)) * 100)}%` }} />
                    </span>
                    <div className="wt-icp__actions">
                        <button type="button" className={`wt-icp-btn${call.state === 'held' ? ' is-on' : ''}`}
                            onClick={() => onCall(call.state === 'held' ? 'unhold' : 'hold')}>
                            <Glyph name={call.state === 'held' ? 'play' : 'pause'} />
                            {call.state === 'held' ? 'Снять' : 'Удержание'}
                        </button>
                        <span className="wt-icp__transfer">
                            <button type="button" className="wt-icp-btn" onClick={() => setTransferOpen((v) => !v)}>
                                <Glyph name="transfer" /> Перевод
                            </button>
                            {transferOpen ? (
                                <div className="wt-icp__dir" role="menu">
                                    {directory.map(([dept, people]) => (
                                        <div key={dept}>
                                            <div className="wt-icp__dir-head">{dept}</div>
                                            {people.map(([name, ext]) => (
                                                <button key={ext} type="button" role="menuitem" onClick={() => {
                                                    setTransferOpen(false);
                                                    onCall('transfer', { to: `${name} (${ext})` });
                                                }}>
                                                    {name}<b>{ext}</b>
                                                </button>
                                            ))}
                                        </div>
                                    ))}
                                </div>
                            ) : null}
                        </span>
                        <button type="button" className={`wt-icp-btn${micMuted ? ' is-on' : ''}`}
                            aria-pressed={micMuted} onClick={() => {
                                const next = !micMuted;
                                setMicMuted(next);
                                // Голосу: сказанное без микрофона водителю не уходит.
                                onCall('mic', { muted: next });
                            }}>
                            <Glyph name={micMuted ? 'micoff' : 'mic'} /> {micMuted ? 'Микр. выкл.' : 'Микрофон'}
                        </button>
                    </div>
                    <button type="button" className="wt-icp-btn is-end" onClick={() => onCall('end', { by: 'operator' })}>
                        <Glyph name="hangup" /> Завершить
                    </button>
                </div>
            ) : call.state === 'wrapup' ? (
                <div className="wt-icp__wrap">
                    <b>Постобработка</b>
                    <span>
                        Вызов завершён{call.transferredTo ? ` — переведён: ${call.transferredTo}` : ''}.
                        Оформите обращение в CRM, затем завершите постобработку — придёт разбор звонка.
                    </span>
                    {onFinishAttempt ? (
                        <button type="button" className="wt-icp-btn is-primary" onClick={onFinishAttempt}>
                            Завершить постобработку
                        </button>
                    ) : null}
                </div>
            ) : (
                <>
                    <div className="wt-icp__keys" aria-hidden="true">
                        {KEYS.map((key) => <span key={key}>{key}</span>)}
                    </div>
                    <div className="wt-icp__dial">
                        <span className="wt-icp-btn is-call is-off"><Glyph name="phone" /> Вызов</span>
                        <span className="wt-icp-btn is-icon is-off"><Glyph name="chat" /></span>
                    </div>
                    <p className="wt-icp__note">
                        {call.state === 'ended'
                            ? (call.reason === 'rejected' ? 'Вызов отклонён.' : 'Вызов завершён.')
                            : call.state === 'ringing'
                                ? 'Входящий вызов — ответьте в окне справа внизу.'
                                : 'Ждём входящий: он придёт сам.'}
                    </p>
                </>
            )}

            <div className="wt-icp__levels" aria-hidden="true">
                <span><Glyph name="speaker" size={14} /><i><b style={{ width: '72%' }} /></i></span>
                <span><Glyph name={micMuted ? 'micoff' : 'mic'} size={14} /><i><b style={{ width: micMuted ? '0%' : '64%' }} /></i></span>
            </div>
            <div className="wt-icp__foot">
                <span>Линия водителей</span>
                <span>доб. 1042</span>
            </div>
            {micError ? <div className="wt-icp__err">{micError}</div> : null}
        </div>
    );
}
