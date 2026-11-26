import React, { useCallback, useEffect, useMemo, useState } from 'react';
import FaIcon from '../common/FaIcon';
import { iosCard, iosGroupLabel, iosBtnPrimary, iosBtnSecondary, iosBtnGhost, IosBadge, IosHint, IosSegmented } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { assignedToast, buildLinePickerOptions, canAssignLine, canReleaseHolder, defaultPickedUser, isRobotLine } from './linePicker';
import { MODE_ACTIVATION, MODE_HOST, lineModeOptions, modeCounts, modeResultToast } from './lineModes';

/*
 * Линии Binotel отдела: какие внутренние номера есть у компании, кто из них
 * онлайн, кто из сотрудников iCORE на какой линии сидит, и кнопка «Назначить».
 *
 * Назначение — одна кнопка: сервер сам связывает линию с сотрудником, дальше тот
 * входит в iCORE Phone логином iCORE и регистрируется на этой линии.
 *
 * Сидеть на линии может сотрудник любого отдела: глава и СВ СЗоВ и суперадмины выбирают
 * его по ФИО из всей компании (право и список считает сервер — can_seat_anyone,
 * candidates), остальные руководители раздела — только из сотрудников отдела линии.
 * Что кому предлагать — в linePicker.js.
 *
 * Режим линий (владелец 26.11.2026: «линии общие, а что отображается в iCORE Phone, зависит от включённого
 * режима»): когда к линиям удалённого КЦ подключена «Активация водителей», у каждой занятой линии — переключатель
 * «Удалённый КЦ / Активация», а над списком — «всем сразу». Сотрудник и линия при этом не меняются. В разделе
 * «Активация водителей» (hosted) — те же линии, но только с режимом: сажают и снимают в «Удаленном КЦ».
 * Логика подписей — lineModes.js.
 */

const readError = async (resp) => {
    const data = await resp.json().catch(() => ({}));
    return data?.error || `HTTP ${resp.status}`;
};

const fmtAgo = (unix) => {
    if (!unix) return '';
    const sec = Math.max(0, Math.floor(Date.now() / 1000) - Number(unix));
    if (sec < 90) return 'только что';
    if (sec < 3600) return `${Math.floor(sec / 60)} мин назад`;
    if (sec < 86400) return `${Math.floor(sec / 3600)} ч назад`;
    return `${Math.floor(sec / 86400)} дн назад`;
};

const DialListLinesPanel = ({ apiBaseUrl, authHeaders, departmentId, departmentName = '', canEdit = true, showToast }) => {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [picking, setPicking] = useState(null);   // номер линии, для которой открыт выбор
    const [pickedUser, setPickedUser] = useState('');
    const [busyLine, setBusyLine] = useState(null);
    // Режим линий: какая строка переключается (номер линии) или 'all' — все сразу.
    const [modeBusy, setModeBusy] = useState(null);

    const toast = useCallback((msg, kind = 'success') => {
        if (typeof showToast === 'function') showToast(msg, kind);
    }, [showToast]);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/lines`, { credentials: 'include', headers: authHeaders() });
            if (!resp.ok) throw new Error(await readError(resp));
            setData(await resp.json());
        } catch (e) {
            setError(e.message || 'Не удалось загрузить линии');
        } finally {
            setLoading(false);
        }
    }, [apiBaseUrl, authHeaders, departmentId]);

    useEffect(() => { load(); }, [load]);

    const post = async (path, body) => {
        const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/lines/${path}`, {
            method: 'POST',
            credentials: 'include',
            headers: authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(body),
        });
        if (!resp.ok) throw new Error(await readError(resp));
        return resp.json();
    };

    const assign = async (line) => {
        if (!pickedUser) return;
        setBusyLine(line.internal_number);
        try {
            const name = pickerOptions.find((o) => o.value === String(pickedUser))?.name || '';
            const result = await post('assign', { user_id: Number(pickedUser), internal_number: line.internal_number });
            // guest — сервер посадил сотрудника другого отдела (см. assignedToast).
            toast(assignedToast(line.internal_number, name, result?.guest === true), 'success');
            setPicking(null);
            setPickedUser('');
            await load();
        } catch (e) {
            toast(e.message || 'Не удалось назначить линию', 'error');
        } finally {
            setBusyLine(null);
        }
    };

    const switchMode = async (body, busyKey) => {
        setModeBusy(busyKey);
        try {
            const result = await post('mode', body);
            const { text, kind } = modeResultToast(result, data?.line_modes);
            toast(text, kind);
            await load();
        } catch (e) {
            toast(e.message || 'Не удалось переключить режим', 'error');
        } finally {
            setModeBusy(null);
        }
    };

    const switchAll = (mode) => {
        const modes = data?.line_modes;
        const name = mode === MODE_ACTIVATION ? 'Активация водителей' : (modes?.host?.name || 'Удаленный КЦ');
        if (!window.confirm(`Перевести все занятые линии в режим «${name}»? Кто сейчас в разговоре или не поставил итог, останется в прежнем режиме.`)) return;
        switchMode({ mode, all: true }, 'all');
    };

    const release = async (line) => {
        if (!line.icore_user) return;
        setBusyLine(line.internal_number);
        try {
            await post('release', { user_id: line.icore_user.id });
            toast(`Линия ${line.internal_number} освобождена`, 'success');
            await load();
        } catch (e) {
            toast(e.message || 'Не удалось снять линию', 'error');
        } finally {
            setBusyLine(null);
        }
    };

    const users = data?.users || [];
    // Сажать на линию сотрудника другого отдела — глава и СВ СЗоВ и суперадмины; признак
    // и список таких сотрудников (candidates) приходят с сервера.
    const canSeatAnyone = data?.can_seat_anyone === true;
    // Посадить человека на линию робота — только суперадмин (признак с сервера, см. canAssignLine).
    const canSeatRobot = data?.can_seat_robot === true;
    const pickerOptions = useMemo(() => buildLinePickerOptions({
        users: data?.users, candidates: data?.candidates, canSeatAnyone, departmentName,
    }), [data, canSeatAnyone, departmentName]);
    const lines = data?.lines || [];
    const online = lines.filter((l) => l.online).length;
    const lineModes = data?.line_modes || null;
    // Вкладка «Активации» без своих линий: сажать и снимать здесь нельзя — только режим.
    const hosted = Boolean(lineModes?.hosted);
    const seatEdit = canEdit && !hosted;
    const modeOptions = lineModeOptions(lineModes);
    const counts = modeCounts(lines);

    return (
        <div className="space-y-4">
            {data && !data.sip_server && (
                <div className="flex items-start gap-3 rounded-2xl bg-amber-50 px-4 py-3 text-[13px] text-amber-800 ring-1 ring-amber-100">
                    <FaIcon className="fas fa-triangle-exclamation mt-0.5" />
                    <div>
                        <div className="font-semibold">У отдела не задан SIP-сервер Binotel</div>
                        <div className="text-[12.5px]">
                            Откройте «Настройки SIP» → отдел и впишите сервер (например <span className="font-mono">sip52.binotel.com</span>),
                            иначе телефон не сможет зарегистрироваться на линии.
                        </div>
                    </div>
                </div>
            )}

            {lineModes && (
                <section className="space-y-1.5">
                    <div className="flex items-center gap-1.5 px-1">
                        <span className={iosGroupLabel}>Режим линий</span>
                        <IosHint text={`Линии общие: сотрудник и линия остаются прежними, меняется только то, что он видит в iCORE Phone. «${lineModes.host?.name || 'Удаленный КЦ'}» — обзвон удалённого КЦ, «Активация водителей» — обзвон новичков до цели по поездкам и перевод на Sapar. Телефон переключится сам со следующим обновлением списка; автообзвон в «Активном» — в течение 10 минут или после перезапуска. Кто сейчас в разговоре или не поставил итог, переключается после этого. Выданные в прежнем режиме водители, которым ещё не звонили, возвращаются в общий список.`} />
                    </div>
                    <div className={`${iosCard} flex flex-wrap items-center justify-between gap-3 px-4 py-3`}>
                        <div className="text-[13px] text-slate-600">
                            В «Активации» — <span className="font-semibold tabular-nums text-slate-900">{counts.activation}</span>
                            {' '}из <span className="tabular-nums">{counts.taken}</span> занятых линий
                            {hosted && (
                                <div className="text-[12px] text-slate-500">
                                    Это линии отдела «{lineModes.host?.name || 'Удаленный КЦ'}»: сажают на них и снимают в его разделе.
                                </div>
                            )}
                        </div>
                        {canEdit && counts.taken > 0 && (
                            <div className="flex flex-wrap items-center gap-2">
                                <span className="text-[12.5px] text-slate-500">Всем:</span>
                                {modeOptions.map((o) => (
                                    <button
                                        key={o.value}
                                        type="button"
                                        onClick={() => switchAll(o.value)}
                                        disabled={modeBusy !== null}
                                        className={iosBtnSecondary}
                                    >
                                        {modeBusy === 'all' ? <FaIcon className="fas fa-spinner fa-spin" /> : null}
                                        {o.label}
                                    </button>
                                ))}
                            </div>
                        )}
                    </div>
                </section>
            )}

            <section className="space-y-1.5">
                <div className="flex items-center justify-between gap-3 px-1">
                    <div className={iosGroupLabel}>Линии компании Binotel</div>
                    <div className="flex items-center gap-2 text-[12px] text-slate-500">
                        {data?.sip_server && <span className="font-mono">{data.sip_server}</span>}
                        {lines.length > 0 && <IosBadge tone="slate">онлайн {online} из {lines.length}</IosBadge>}
                        <button type="button" onClick={load} disabled={loading} className={`${iosBtnGhost} py-1`} title="Обновить">
                            <FaIcon className={loading ? 'fas fa-spinner fa-spin' : 'fas fa-rotate'} />
                        </button>
                    </div>
                </div>
                <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                    {error ? (
                        <div className="px-4 py-5 text-[13px] text-rose-600">{error}</div>
                    ) : loading && !data ? (
                        <div className="px-4 py-6 text-center text-[13px] text-slate-500"><FaIcon className="fas fa-spinner fa-spin" /> Загрузка…</div>
                    ) : lines.length === 0 ? (
                        <div className="px-4 py-6 text-center text-[13px] text-slate-500">Binotel не вернул ни одной линии</div>
                    ) : lines.map((line) => {
                        const isPicking = picking === line.internal_number;
                        const busy = busyLine === line.internal_number;
                        // С линии звонит автоинформатор: строка серая, «Назначить» нет (см. isRobotLine).
                        const robot = isRobotLine(line);
                        return (
                            <div key={line.internal_number} className={`px-4 py-3 ${robot ? 'bg-slate-50' : ''}`}>
                                <div className="flex items-center gap-3">
                                    <div className={`grid h-9 w-14 shrink-0 place-items-center rounded-xl font-mono text-[14px] font-semibold ${
                                        robot ? 'bg-slate-200/70 text-slate-400'
                                            : line.online ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-600'
                                    }`}>
                                        {line.internal_number}
                                    </div>
                                    <div className="min-w-0 flex-1">
                                        <div className="flex flex-wrap items-center gap-1.5">
                                            {robot ? (
                                                /* Робот на линии не регистрируется: «офлайн» и имя из Binotel здесь ничего не значат. */
                                                <IosBadge tone="slate"><FaIcon className="fas fa-robot" />автоинформатор</IosBadge>
                                            ) : (
                                                <>
                                                    <IosBadge tone={line.online ? 'green' : 'slate'}>{line.online ? 'онлайн' : 'офлайн'}</IosBadge>
                                                    {line.tls && <IosBadge tone="blue">TLS</IosBadge>}
                                                    {line.binotel_employee?.name && (
                                                        <span className="text-[12px] text-slate-500">в Binotel: {line.binotel_employee.name}</span>
                                                    )}
                                                    {!line.online && line.was_online_at && (
                                                        <span className="text-[11.5px] text-slate-400">был онлайн {fmtAgo(line.was_online_at)}</span>
                                                    )}
                                                </>
                                            )}
                                        </div>
                                        <div className="mt-0.5 text-[13.5px]">
                                            {robot && !line.icore_user ? (
                                                <span className="text-slate-400">{canSeatRobot ? 'звонит робот' : 'звонит робот — назначить нельзя'}</span>
                                            ) : line.icore_user ? (
                                                <span className="font-medium text-slate-800">
                                                    {line.icore_user.name}
                                                    {line.icore_user.login && <span className="ml-1 text-[12px] font-normal text-slate-400">@{line.icore_user.login}</span>}
                                                    {/* Сотрудник другого отдела: чей он, видно сразу. */}
                                                    {line.icore_user.guest && line.icore_user.department_name && (
                                                        <span className="ml-1 text-[12px] font-normal text-slate-400">· {line.icore_user.department_name}</span>
                                                    )}
                                                </span>
                                            ) : (
                                                <span className="text-slate-400">никому в iCORE не назначена</span>
                                            )}
                                        </div>
                                    </div>
                                    {lineModes && line.icore_user && (
                                        <IosSegmented
                                            value={line.mode || MODE_HOST}
                                            onChange={(mode) => {
                                                if (!canEdit || modeBusy !== null || mode === (line.mode || MODE_HOST)) return;
                                                switchMode({ mode, user_ids: [line.icore_user.id] }, line.internal_number);
                                            }}
                                            options={modeOptions}
                                            size="xs"
                                            ariaLabel={`Режим линии ${line.internal_number}`}
                                        />
                                    )}
                                    {seatEdit && !isPicking && (
                                        line.icore_user ? (
                                            /* Сотрудника другого отдела снимает тот же, кто вправе его сажать. */
                                            canReleaseHolder(line.icore_user, canSeatAnyone) && (
                                                <button type="button" onClick={() => release(line)} disabled={busy} className={iosBtnGhost}>
                                                    <FaIcon className={busy ? 'fas fa-spinner fa-spin' : 'fas fa-link-slash'} />
                                                    Снять
                                                </button>
                                            )
                                        ) : canAssignLine(line, canSeatRobot) && (
                                            <button
                                                type="button"
                                                onClick={() => { setPicking(line.internal_number); setPickedUser(defaultPickedUser(users)); }}
                                                disabled={busy}
                                                className={iosBtnSecondary}
                                            >
                                                <FaIcon className="fas fa-user-plus" />
                                                Назначить
                                            </button>
                                        )
                                    )}
                                </div>
                                {isPicking && (
                                    <div className="mt-3 flex flex-wrap items-center gap-2 rounded-xl bg-slate-50 px-3 py-2.5">
                                        <span className="flex items-center gap-1 text-[12.5px] text-slate-600">
                                            Кому:
                                            {canSeatAnyone && (
                                                <IosHint text={`Можно выбрать сотрудника любого отдела. Он остаётся в своём отделе: пока сидит на линии, его iCORE Phone работает на ней и показывает вкладку «Обзвон», а после «Снять» возвращается к настройкам своего отдела. Показатели считаются как у сотрудников удалённого КЦ.${isRobotLine(line) ? ' С этой линии звонит автоинформатор: пока на ней сидит человек, робот с неё не звонит, а после «Снять» линия снова его.' : ''}`} />
                                            )}
                                        </span>
                                        <CustomSelect
                                            className="w-80 max-w-full"
                                            variant="ios"
                                            value={pickedUser}
                                            onChange={(v) => setPickedUser(String(v))}
                                            ariaLabel="Сотрудник"
                                            placeholder={pickerOptions.length === 0 ? 'В отделе нет сотрудников' : 'Выберите сотрудника'}
                                            disabled={pickerOptions.length === 0}
                                            searchable={pickerOptions.length > 8}
                                            searchPlaceholder="Поиск по ФИО"
                                            options={pickerOptions}
                                        />
                                        <button type="button" onClick={() => assign(line)} disabled={busy || !pickedUser} className={iosBtnPrimary}>
                                            <FaIcon className={busy ? 'fas fa-spinner fa-spin' : 'fas fa-check'} />
                                            Назначить
                                        </button>
                                        <button type="button" onClick={() => { setPicking(null); setPickedUser(''); }} className={iosBtnGhost}>Отмена</button>
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
                {!hosted && (
                    <p className="px-1 text-[11.5px] leading-relaxed text-slate-500">
                        Сотрудник входит в iCORE Phone своим логином iCORE и оказывается на назначенной линии.
                        Один сотрудник — одна линия.
                    </p>
                )}
            </section>
        </div>
    );
};

export default DialListLinesPanel;
