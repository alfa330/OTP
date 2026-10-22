import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    Ear, Headphones, Loader2, MessageSquareText, Phone, PhoneForwarded, PhoneIncoming, PhoneOff,
    PhoneOutgoing, Users,
} from 'lucide-react';

import { iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosInput, IosBadge, IosMenu, IosModal } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { prettyPhone } from './touchMeta';
import {
    callClock, callSubtitle, connectorStatus, DIRECTION_LABEL, endedNote, mergeLiveCalls, pickLine,
    reconcileLiveCalls, sortCalls, STATE_LABEL, stateTone, visibleCalls, wallSeconds,
} from './liveMeta';
import { runSseStream } from '../common/sseClient';

/* Панель «Сейчас на линии» раздела «Касания» — живые звонки станции от коннектора АТС
 * (cdr_bridge/pbx_connector.py) и три действия: позвонить клиенту со своего телефона, положить
 * трубку, подключиться к разговору оператора.
 *
 * Реальное время без опроса: поток /api/pbx/stream отдаёт первым кадром всё состояние (`state`),
 * дальше — каждое изменение звонка (`calls`) в момент, когда оно случилось на станции, и смену
 * связи коннектора (`connector`). Поток держит только видимая вкладка: скрытая отдаёт место на
 * сервере (каждый поток занимает нить waitress). Поток недоступен — мест нет, канал выключен,
 * сеть режет долгие ответы — панель возвращается к опросу /api/pbx/live раз в две секунды.
 * Ответ опроса, запрошенный до того, как поток ожил, отбрасывается: поток уже знает больше.
 *
 * Часы звонков тикают на клиенте от времени сервера (поле now в кадрах), чтобы не зависеть ни
 * от часов браузера, ни от его пояса.
 *
 * Звонок с портала — это Originate на станции: сначала звонит ВАШ телефон, вы снимаете
 * трубку, и станция набирает клиента. Поэтому у кнопки нет «идёт набор» — есть «ваш телефон
 * звонит», а итог (клиент ответил / не ответил) виден в самой панели как обычный звонок.
 */

const POLL_MS = 2000;
const COMMAND_WATCH_MS = 2000;
const COMMAND_WATCH_MAX = 20;

const errText = (error, fallback) => (error?.response?.data?.error || error?.message || fallback);

const isoFromSeconds = (seconds) => new Date(seconds * 1000).toISOString().slice(0, 19);

export function usePbxLive(apiBaseUrl, headers, enabled, showToast) {
    const base = `${apiBaseUrl}/api/pbx`;
    const toastRef = useRef(showToast);
    toastRef.current = showToast;

    const [data, setData] = useState(null);
    const [closed, setClosed] = useState(false);
    const [dialog, setDialog] = useState(null);
    const [lastPrefix, setLastPrefix] = useState('');
    const [streamState, setStreamState] = useState('idle');
    const [, setTick] = useState(0);
    const fetchedAt = useRef(0);
    const serverNow = useRef(null);
    const watchers = useRef({});
    const streamLive = useRef(false);
    // Растёт каждый раз, когда поток оживает: ответ опроса из прошлой эпохи опоздал.
    const streamEpoch = useRef(0);
    const hasData = useRef(false);
    hasData.current = Boolean(data);

    const anchor = useCallback((now) => {
        const seconds = wallSeconds(now);
        if (seconds === null) return;
        fetchedAt.current = Date.now();
        serverNow.current = seconds;
    }, []);

    /* Опрос — только пока поток не живой: его ответ собран раньше последних кадров потока и
       целиком откатил бы их. */
    const load = useCallback(() => {
        if (streamLive.current) return Promise.resolve();
        const epoch = streamEpoch.current;
        return axios.get(`${base}/live`, { headers: headers() })
            .then((response) => {
                if (streamLive.current || epoch !== streamEpoch.current) return;
                anchor(response.data?.now);
                setData((prev) => ({ ...response.data, calls: reconcileLiveCalls(prev?.calls, response.data?.calls) }));
            })
            .catch((exc) => {
                if (exc?.response?.status === 403) setClosed(true);
                // Остальное — молча: панель вспомогательная, раздел работает и без неё.
            });
    }, [anchor, base, headers]);

    /* Живой поток: только у видимой вкладки. */
    useEffect(() => {
        if (!enabled || closed || typeof document === 'undefined') return undefined;
        let stream = null;
        const onEvent = (event, payload) => {
            if (!payload) return;
            if (payload.now) anchor(payload.now);
            if (event === 'state') {
                setData({ connector: payload.connector, calls: payload.calls || [], me: payload.me,
                          lines: payload.lines || [], now: payload.now });
            } else if (event === 'calls') {
                setData((prev) => (prev ? { ...prev, calls: mergeLiveCalls(prev.calls, payload.calls) } : prev));
            } else if (event === 'connector') {
                setData((prev) => (prev ? { ...prev, connector: payload.connector } : prev));
            }
        };
        const open = () => {
            if (stream || document.visibilityState !== 'visible') return;
            stream = runSseStream({
                url: `${base}/stream`, getHeaders: headers, onEvent,
                onState: (state) => {
                    if (state === 'denied') setClosed(true);
                    if (state === 'live' && !streamLive.current) streamEpoch.current += 1;
                    streamLive.current = state === 'live';
                    setStreamState(state);
                },
            });
        };
        const shut = () => {
            stream?.close();
            stream = null;
        };
        const onVisibility = () => (document.visibilityState === 'visible' ? open() : shut());
        open();
        document.addEventListener('visibilitychange', onVisibility);
        return () => {
            document.removeEventListener('visibilitychange', onVisibility);
            shut();
        };
    }, [anchor, base, closed, enabled, headers]);

    /* Запасной опрос — пока поток не живой (поднимается, мест нет, выключен, обрыв). Данные уже
       есть — первый запрос через паузу: поток переподключается раз в 10 минут и обычно оживает
       раньше, чем запрос понадобится. */
    useEffect(() => {
        if (!enabled || closed || streamState === 'live') return undefined;
        let timer = null;
        const tick = () => {
            load();
            timer = setTimeout(tick, POLL_MS);
        };
        const start = () => {
            clearTimeout(timer);
            if (document.visibilityState !== 'visible') return;
            timer = setTimeout(tick, hasData.current ? POLL_MS : 0);
        };
        start();
        document.addEventListener('visibilitychange', start);
        return () => {
            clearTimeout(timer);
            document.removeEventListener('visibilitychange', start);
        };
    }, [enabled, closed, load, streamState]);

    const nowIso = serverNow.current === null ? ''
        : isoFromSeconds(serverNow.current + Math.floor((Date.now() - fetchedAt.current) / 1000));

    const calls = useMemo(() => sortCalls(visibleCalls(data?.calls, nowIso)), [data, nowIso]);
    // Тикают и законченные: через 90 с после отбоя строка уходит сама.
    const active = calls.length > 0;

    useEffect(() => {
        if (!active) return undefined;
        const timer = setInterval(() => setTick((value) => value + 1), 1000);
        return () => clearInterval(timer);
    }, [active]);

    useEffect(() => () => {
        Object.values(watchers.current).forEach(clearInterval);
    }, []);

    /* Следим за командой до итога: станция отвечает не сразу (телефон звонит до 30 с). */
    const watchCommand = useCallback((commandId, onDone) => {
        let left = COMMAND_WATCH_MAX;
        const timer = setInterval(() => {
            left -= 1;
            axios.get(`${base}/commands/${commandId}`, { headers: headers() })
                .then((response) => {
                    const { status, message } = response.data || {};
                    if (status === 'failed' || status === 'expired') {
                        clearInterval(timer);
                        delete watchers.current[commandId];
                        toastRef.current?.(message || 'Станция не выполнила команду', 'error');
                    } else if (status === 'done') {
                        clearInterval(timer);
                        delete watchers.current[commandId];
                        onDone?.(response.data);
                    } else if (left <= 0) {
                        clearInterval(timer);
                        delete watchers.current[commandId];
                    }
                })
                .catch(() => {
                    if (left <= 0) { clearInterval(timer); delete watchers.current[commandId]; }
                });
        }, COMMAND_WATCH_MS);
        watchers.current[commandId] = timer;
    }, [base, headers]);

    const startCall = useCallback(({ phone = '', lineNumber = '', linkedid = '' } = {}) => {
        setDialog({ phone: phone ? prettyPhone(phone) : '', lineNumber, linkedid });
    }, []);

    const submitCall = useCallback((phone, prefix, linkedid) => axios.post(`${base}/call`,
        { phone, prefix, linkedid: linkedid || undefined }, { headers: headers() })
        .then((response) => {
            setDialog(null);
            if (prefix) setLastPrefix(prefix);
            toastRef.current?.(`Ваш телефон ${data?.me?.ext || ''} звонит — снимите трубку, станция наберёт клиента`, 'success');
            watchCommand(response.data?.command_id);
            load();
        })
        .catch((exc) => {
            toastRef.current?.(errText(exc, 'Не удалось позвонить'), 'error');
            throw exc;
        }), [base, headers, data, watchCommand, load]);

    const hangup = useCallback((linkedid) => axios.post(`${base}/hangup`, { linkedid }, { headers: headers() })
        .then(() => load())
        .catch((exc) => toastRef.current?.(errText(exc, 'Не удалось положить трубку'), 'error')), [base, headers, load]);

    const spy = useCallback((linkedid, mode) => axios.post(`${base}/spy`, { linkedid, mode }, { headers: headers() })
        .then((response) => {
            toastRef.current?.(`Ваш телефон ${data?.me?.ext || ''} звонит — снимите трубку`, 'success');
            watchCommand(response.data?.command_id);
        })
        .catch((exc) => toastRef.current?.(errText(exc, 'Не удалось подключиться'), 'error')), [base, headers, data, watchCommand]);

    return {
        data, closed, calls, nowIso, lastPrefix,
        me: data?.me || null, lines: data?.lines || [], connector: data?.connector || null,
        dialog, setDialog, startCall, submitCall, hangup, spy,
    };
}

/* Кнопка «позвонить» у телефона в таблице касаний: только тем, кому есть чем звонить. */
export function CallButton({ pbx, phone, lineNumber, linkedid }) {
    if (!pbx?.me?.can_call || !phone) return null;
    return (
        <button type="button" onClick={() => pbx.startCall({ phone, lineNumber, linkedid })}
                className="ml-1.5 inline-grid h-6 w-6 place-items-center rounded-full text-slate-400 transition hover:bg-blue-50 hover:text-blue-600 active:scale-95"
                title="Позвонить с вашего телефона" aria-label="Позвонить">
            <Phone size={13} />
        </button>
    );
}

const DirectionIcon = ({ call }) => {
    const cls = 'shrink-0 text-slate-400';
    if (call.state === 'ended' && !call.answered_at) return <PhoneOff size={15} className={cls} />;
    if (call.direction === 'out') return <PhoneOutgoing size={15} className={cls} />;
    if (call.direction === 'autodial') return <PhoneForwarded size={15} className={cls} />;
    if (call.direction === 'internal') return <Users size={15} className={cls} />;
    return <PhoneIncoming size={15} className={cls} />;
};

const DOT = { green: 'bg-emerald-500', amber: 'bg-amber-400', slate: 'bg-slate-300' };

function CallDialog({ pbx }) {
    const { dialog, setDialog, lines, me, lastPrefix } = pbx;
    const [phone, setPhone] = useState('');
    const [prefix, setPrefix] = useState('');
    const [busy, setBusy] = useState(false);
    const open = Boolean(dialog);

    useEffect(() => {
        if (!dialog) return;
        setPhone(dialog.phone || '');
        setPrefix(pickLine(lines, dialog.lineNumber, lastPrefix));
        setBusy(false);
    }, [dialog, lines, lastPrefix]);

    const digits = phone.replace(/\D/g, '').slice(-10);
    const ready = digits.length === 10 && Boolean(prefix) && !busy;
    const options = useMemo(() => lines.map((line) => ({
        value: line.prefix, label: `${line.label} · ${prettyPhone(line.number)}`,
    })), [lines]);

    const submit = () => {
        if (!ready) return;
        setBusy(true);
        pbx.submitCall(digits, prefix, dialog?.linkedid).catch(() => setBusy(false));
    };

    return (
        <IosModal open={open} onClose={() => setDialog(null)} title="Позвонить"
                  subtitle={me?.ext ? `С вашего телефона ${me.ext}: он зазвонит первым, после ответа станция наберёт клиента` : ''}
                  maxWidth="max-w-md"
                  footer={(
                      <div className="flex justify-end gap-2">
                          <button type="button" className={iosBtnSecondary} onClick={() => setDialog(null)}>Отмена</button>
                          <button type="button" className={iosBtnPrimary} onClick={submit} disabled={!ready}>
                              {busy ? <Loader2 size={15} className="animate-spin" /> : <Phone size={15} />}
                              Позвонить
                          </button>
                      </div>
                  )}>
            <div className="flex flex-col gap-3">
                <label className="block">
                    <span className="mb-1 block text-[12px] font-medium text-slate-500">Номер клиента</span>
                    <input type="tel" className={`${iosInput} tabular-nums`} value={phone} autoFocus
                           onChange={(event) => setPhone(event.target.value)}
                           onKeyDown={(event) => { if (event.key === 'Enter') submit(); }}
                           placeholder="+7 7XX XXX-XX-XX" />
                </label>
                <label className="block">
                    <span className="mb-1 block text-[12px] font-medium text-slate-500">Линия</span>
                    {options.length ? (
                        <CustomSelect value={prefix} onChange={setPrefix} options={options}
                                      placeholder="С какого номера звонить" />
                    ) : (
                        <div className="text-[12.5px] text-slate-500">
                            Коннектор ещё не прислал линии станции — подождите минуту.
                        </div>
                    )}
                </label>
            </div>
        </IosModal>
    );
}

function CallRow({ call, pbx }) {
    const { me, nowIso } = pbx;
    const clock = callClock(call, nowIso);
    const ended = call.state === 'ended';
    const talking = call.state === 'talking';
    const note = endedNote(call);
    const items = [
        ended && me?.can_call && call.phone && {
            key: 'back', label: 'Перезвонить', icon: Phone,
            onSelect: () => pbx.startCall({ phone: call.phone, lineNumber: call.line_number, linkedid: call.linkedid }),
        },
        !ended && (call.mine || me?.can_spy) && call.has_agent_channel && {
            key: 'hangup', label: 'Положить трубку', icon: PhoneOff, danger: true,
            onSelect: () => pbx.hangup(call.linkedid),
        },
        talking && me?.can_spy && !call.mine && call.has_agent_channel && {
            key: 'listen', label: 'Слушать', icon: Ear, separatorBefore: true,
            hint: 'Вас не слышат ни оператор, ни клиент',
            onSelect: () => pbx.spy(call.linkedid, 'listen'),
        },
        talking && me?.can_spy && !call.mine && call.has_agent_channel && {
            key: 'whisper', label: 'Подсказать', icon: MessageSquareText,
            hint: 'Слышит только оператор',
            onSelect: () => pbx.spy(call.linkedid, 'whisper'),
        },
        talking && me?.can_spy && !call.mine && call.has_agent_channel && {
            key: 'barge', label: 'Войти в разговор', icon: Headphones,
            hint: 'Слышат оба',
            onSelect: () => pbx.spy(call.linkedid, 'barge'),
        },
    ].filter(Boolean);
    const title = call.phone ? prettyPhone(call.phone) : (DIRECTION_LABEL[call.direction] || 'Звонок');
    const subtitle = [
        callSubtitle(call),
        call.operator && call.park ? call.park : '',
        ended ? note : '',
        call.origin === 'portal' ? 'набрано с портала' : '',
    ].filter(Boolean).join(' · ');

    return (
        <li className={`flex items-center gap-3 py-2 ${ended ? 'opacity-60' : ''}`}>
            <DirectionIcon call={call} />
            <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="text-[13.5px] font-medium tabular-nums text-slate-900">{title}</span>
                    <IosBadge tone={stateTone(call.state)}>{STATE_LABEL[call.state] || call.state}</IosBadge>
                    {clock ? <span className="text-[12.5px] tabular-nums text-slate-500">{clock}</span> : null}
                    {call.mine && !ended ? <span className="text-[11.5px] font-medium text-blue-600">ваш</span> : null}
                </div>
                <div className="truncate text-[12px] text-slate-500" title={subtitle}>{subtitle}</div>
            </div>
            {items.length ? <IosMenu items={items} label="Действия со звонком" /> : null}
        </li>
    );
}

export default function LivePanel({ pbx }) {
    const { data, closed, calls, connector, me } = pbx;
    if (closed || !data) return null;
    const status = connectorStatus(connector);
    const activeCount = calls.filter((call) => call.state !== 'ended').length;

    return (
        <section className={`${iosCard} mt-4 px-4 py-3`}>
            <div className="flex items-center gap-2">
                <span className={`h-2 w-2 shrink-0 rounded-full ${DOT[status.tone] || DOT.slate}`} aria-hidden="true" />
                <h2 className="text-[13.5px] font-semibold text-slate-900">Сейчас на линии</h2>
                <span className="text-[12.5px] text-slate-500">
                    {status.online ? (activeCount ? `${activeCount}` : 'тихо') : status.label}
                </span>
                {me?.can_call ? (
                    <button type="button" className={`${iosBtnGhost} ml-auto`} onClick={() => pbx.startCall()}
                            title="Позвонить клиенту с вашего телефона">
                        <Phone size={14} /> Позвонить
                    </button>
                ) : null}
            </div>
            {!status.online && connector?.ami_error ? (
                <div className="mt-1.5 text-[12px] text-amber-700">{connector.ami_error}</div>
            ) : null}
            {calls.length ? (
                <ul className="mt-1 divide-y divide-slate-100">
                    {calls.map((call) => <CallRow key={call.linkedid} call={call} pbx={pbx} />)}
                </ul>
            ) : null}
            <CallDialog pbx={pbx} />
        </section>
    );
}
