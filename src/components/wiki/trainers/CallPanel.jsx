import React, { useEffect, useState } from 'react';

import { isLive, talkClock, talkMs } from './callMachine.js';

/* Плашка звонка — то, во что стажёр смотрит, пока говорит.
 *
 * Стоит ПОВЕРХ любой вкладки: во время разговора оператор сидит в
 * Диспетчерской, а не в Okapp, и плашка обязана ехать за ним. Иначе он теряет
 * кнопки «Удержание» и «Перевод» ровно тогда, когда они нужны.
 *
 * ЦИТАТЫ ВОДИТЕЛЯ ЗДЕСЬ НЕТ и быть не может. Стажёр обязан слушать, а не
 * читать: прочитанную реплику он не переспросит и не услышит интонацию, а
 * половина работы на линии — именно услышать.
 *
 * «Говорит ИИ» — не украшение: закон РК «Об искусственном интеллекте»
 * (ст. 21, действует с 18.01.2026) требует маркировать ИИ-контент. Плашка
 * висит всё время разговора.
 */

const Icon = ({ name }) => {
    const d = {
        phone: 'M6.5 3.5 9 8l-2 2a12 12 0 0 0 5 5l2-2 4.5 2.5-1 3a2 2 0 0 1-2 1.4C7.7 19.4 4.6 16.3 3.1 6.5A2 2 0 0 1 4.5 4.5Z',
        hangup: 'M3 10a14 14 0 0 1 18 0l-2.5 3-4-1.2V9a10 10 0 0 0-5 0v2.8L5.5 13Z',
        pause: 'M9 5v14M15 5v14',
        play: 'M7 4l12 8-12 8Z',
        transfer: 'M4 8h12l-3-3m3 11H4l3 3',
        mute: 'M4 9h4l5-4v14l-5-4H4Zm12 1 4 4m0-4-4 4',
        sound: 'M4 9h4l5-4v14l-5-4H4Zm12-1a5 5 0 0 1 0 8',
        mic: 'M12 3a3 3 0 0 1 3 3v5a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3ZM5 11a7 7 0 0 0 14 0M12 18v3',
    }[name];
    return (
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
            strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d={d} />
        </svg>
    );
};

/* Тон вызова — свой короткий звук, а не чужой рингтон: два синуса через
   WebAudio, включаются и гаснут по состоянию. Отключается кнопкой — в открытом
   кабинете звонящий тренажёр мешает соседям. */
const useRingTone = (active, muted) => {
    useEffect(() => {
        if (!active || muted) return undefined;
        if (typeof window === 'undefined' || !window.AudioContext) return undefined;
        let ctx;
        let stopped = false;
        const timers = [];
        try {
            ctx = new window.AudioContext();
        } catch {
            return undefined;
        }
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
        timers.push(loop);
        return () => {
            stopped = true;
            timers.forEach(clearInterval);
            try { ctx.close(); } catch { /* уже закрыт */ }
        };
    }, [active, muted]);
};

/** Тикающий таймер разговора. Отдельным состоянием, чтобы не дёргать мир. */
const useTick = (live) => {
    const [, setTick] = useState(0);
    useEffect(() => {
        if (!live) return undefined;
        const id = setInterval(() => setTick((n) => n + 1), 1000);
        return () => clearInterval(id);
    }, [live]);
};

const STATE_TEXT = {
    offline: 'Не на линии',
    ready: 'На линии, ждём звонок',
    ringing: 'Входящий звонок',
    talking: 'Разговор',
    held: 'На удержании',
    wrapup: 'Постобработка',
    ended: 'Звонок завершён',
};

export default function CallPanel({
    call, onCall, directory = [], voice = null, aiSpeaking = false, micLevel = 0,
    micError = '', onRing = null, devMode = false,
}) {
    const [transferOpen, setTransferOpen] = useState(false);
    const live = isLive(call);
    useRingTone(call.state === 'ringing', call.muted);
    useTick(live);

    // Не на линии и звонка не было — плашки нет вовсе, экран не занимаем.
    if (call.state === 'offline' && !onRing) return null;

    return (
        <div className={`wt-cp is-${call.state}`} role="region" aria-label="Панель звонка">
            <div className="wt-cp__left">
                <span className="wt-cp__mark">Учебная среда</span>
                <span className="wt-cp__state">{STATE_TEXT[call.state] || call.state}</span>
                {call.phone ? <b className="wt-cp__phone">{call.phone}</b> : null}
                {call.queue ? <span className="wt-cp__queue">{call.queue}</span> : null}
                {call.state === 'ringing' && call.waitedSec
                    ? <span className="wt-cp__waited">ждёт {call.waitedSec} с</span> : null}
                {live ? <span className="wt-cp__timer">{talkClock(talkMs(call))}</span> : null}
                {call.transferredTo ? <span className="wt-cp__queue">→ {call.transferredTo}</span> : null}
            </div>

            {/* Маркировка ИИ — всё время разговора, по закону РК. */}
            {live ? (
                <div className="wt-cp__ai">
                    <span className="wt-cp__ai-badge">Говорит ИИ</span>
                    <span className={`wt-cp__who${aiSpeaking ? ' is-them' : ''}`}>
                        {aiSpeaking ? 'говорит собеседник' : 'слушаю вас'}
                    </span>
                    <span className="wt-cp__mic" aria-hidden="true">
                        <Icon name="mic" />
                        <i style={{ width: `${Math.round(Math.min(1, Math.max(0, micLevel)) * 100)}%` }} />
                    </span>
                    {voice ? null : <span className="wt-cp__manual">без голоса</span>}
                </div>
            ) : null}

            <div className="wt-cp__buttons">
                {call.state === 'ringing' ? (
                    <>
                        <button type="button" className="wt-cp__btn is-answer" onClick={() => onCall('answer')}>
                            <Icon name="phone" /> Ответить
                        </button>
                        <button type="button" className="wt-cp__btn is-reject" onClick={() => onCall('reject')}>
                            <Icon name="hangup" /> Отклонить
                        </button>
                        <button type="button" className="wt-cp__btn" onClick={() => onCall('mute')}
                            aria-label={call.muted ? 'Включить звук' : 'Отключить звук'}>
                            <Icon name={call.muted ? 'mute' : 'sound'} />
                        </button>
                    </>
                ) : null}

                {live ? (
                    <>
                        <button type="button" className="wt-cp__btn"
                            onClick={() => onCall(call.state === 'held' ? 'unhold' : 'hold')}>
                            <Icon name={call.state === 'held' ? 'play' : 'pause'} />
                            {call.state === 'held' ? 'Снять' : 'Удержание'}
                        </button>
                        <span className="wt-cp__transfer">
                            <button type="button" className="wt-cp__btn"
                                onClick={() => setTransferOpen((v) => !v)}>
                                <Icon name="transfer" /> Перевод
                            </button>
                            {transferOpen ? (
                                <div className="wt-cp__dir">
                                    {directory.map(([dept, people]) => (
                                        <div key={dept}>
                                            <div className="wt-cp__dir-head">{dept}</div>
                                            {people.map(([name, ext]) => (
                                                <button key={ext} type="button" onClick={() => {
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
                        <button type="button" className="wt-cp__btn is-reject"
                            onClick={() => onCall('end', { by: 'operator' })}>
                            <Icon name="hangup" /> Завершить
                        </button>
                    </>
                ) : null}

                {call.state === 'wrapup' ? (
                    <span className="wt-cp__wrap">Звонок завершён, оформите обращение в CRM</span>
                ) : null}

                {/* Отладочная кнопка: пока ИИ нет, звонок надо чем-то запускать.
                    Видна только в dev-сборке. */}
                {devMode && onRing && (call.state === 'ready' || call.state === 'offline') ? (
                    <button type="button" className="wt-cp__btn is-dev" onClick={onRing}>
                        Позвонить (отладка)
                    </button>
                ) : null}
            </div>

            {micError ? <div className="wt-cp__micerr">{micError}</div> : null}
        </div>
    );
}
