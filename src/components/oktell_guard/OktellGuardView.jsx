import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import FaIcon from '../common/FaIcon';
import useStableCallback from '../wiki/useStableCallback';
import {
    APPLE_FONT, iosCard, iosInput, iosGroupLabel,
    iosBtnPrimary, iosBtnSecondary, iosBtnGhost,
    IosBadge, IosHint, IosModal, IosSection, IosToggle,
} from '../ui/ios';

/*
 * Раздел «Ограничитель Перезвона».
 *
 * Три вкладки:
 *   Сотрудники — личный порог у каждого и состояние его агента.
 *   Общие      — адрес Oktell, порог по умолчанию, режим обкатки, версия агента.
 *   Отчёт      — за какую дату сколько раз кого выкинуло.
 *
 * Считает время не сервер и не этот раздел, а правило внутри окна Oktell на
 * машине сотрудника: клиент и так получает от АТС событие статуса, поэтому
 * опрашивать базу не нужно вовсе. Здесь только настройки, раздача агента и
 * то, что агенты прислали постфактум.
 */

const TABS = [
    { id: 'employees', label: 'Сотрудники' },
    { id: 'common', label: 'Общие' },
    { id: 'report', label: 'Отчёт' },
];

// Пороги списком, а не свободным вводом: это решение про людей, и «187 секунд»
// здесь не значит ничего, кроме опечатки.
const THRESHOLD_PRESETS = [120, 180, 240, 300, 600];

const fmtMinutes = (seconds) => {
    const value = Number(seconds || 0);
    if (!value) return '—';
    if (value % 60 === 0) return `${value / 60} мин`;
    return `${Math.floor(value / 60)} мин ${value % 60} с`;
};

/**
 * Время из базы приходит МЕСТНЫМ (Алматы), а Flask отдаёт его строкой с
 * пометкой GMT. Браузер читает такую строку как UTC и уводит момент на +5 часов
 * вперёд. Последствия были не косметические: «Молчит с …» не загоралось вообще
 * никогда (возраст отметки получался отрицательным, порог 15 минут не
 * срабатывал), то есть намертво замолчавший агент выглядел живым.
 */
const parseServerTime = (raw) => {
    if (!raw) return null;
    const parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) return null;
    if (!/GMT|UTC|Z$|\+00:?00$/i.test(String(raw))) return parsed;
    // Возвращаем момент туда, где он был записан: пометку GMT поставил
    // сериализатор, а не база.
    return new Date(parsed.getTime() + parsed.getTimezoneOffset() * 60000);
};

const fmtDateTime = (raw) => {
    if (!raw) return '';
    const parsed = parseServerTime(raw);
    if (!parsed) return String(raw).slice(0, 16).replace('T', ' ');
    return parsed.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
};

const fmtDay = (raw) => {
    if (!raw) return '';
    const parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) return String(raw).slice(0, 10);
    return parsed.toLocaleDateString('ru-RU', { day: '2-digit', month: 'long' });
};

const fmtSize = (bytes) => {
    const value = Number(bytes || 0);
    if (!value) return '';
    return `${(value / (1024 * 1024)).toFixed(1)} МБ`;
};

const isoDaysAgo = (days) => {
    const d = new Date();
    d.setDate(d.getDate() - days);
    return d.toISOString().slice(0, 10);
};

/**
 * Состояние агента одной строкой.
 *
 * «Нет агента» и «агент есть, но окно закрыто» — разные вещи: в первом случае
 * ограничитель на машине не работает вовсе, во втором работать ему просто не с
 * чем. Смешивать их в одно «не в порядке» нельзя, иначе непонятно, что чинить.
 */
const agentState = (row) => {
    if (!row.agent_seen_at) return { tone: 'slate', text: 'Не установлен' };
    const seen = parseServerTime(row.agent_seen_at);
    const minutesAgo = seen ? (Date.now() - seen.getTime()) / 60000 : NaN;
    if (Number.isNaN(minutesAgo) || minutesAgo > 15) {
        return { tone: 'red', text: `Молчит с ${fmtDateTime(row.agent_seen_at)}` };
    }
    // Программа жива, своего окна нет, а чужое окно Oktell она видит — это и
    // есть обход, ради ловли которого всё писалось. Раньше число уезжало в
    // базу и там умирало: раздел о нём не говорил ни слова.
    if (!row.agent_window && Number(row.unmanaged_count || 0) > 0) {
        return { tone: 'red', text: 'Oktell мимо программы' };
    }
    if (!row.agent_window) return { tone: 'amber', text: 'Окно Oktell закрыто' };
    // «Агент на связи» ещё не значит «человек под ограничителем»: правило живёт
    // внутри окна и может стоять там слепым — тогда время в «Перезвоне» не
    // считается вовсе. Показывать такую машину зелёной нельзя: именно так
    // ограничитель две недели выглядел рабочим при пустом отчёте.
    if (row.agent_session && row.rule_alive === false) {
        return { tone: 'red', text: 'Правило не считает' };
    }
    if (row.agent_session && row.rule_alive === true) {
        return { tone: 'green', text: 'Считает' };
    }
    return { tone: 'green', text: 'На связи' };
};

export default function OktellGuardView({ user, showToast, apiBaseUrl, withAccessTokenHeader }) {
    const [tab, setTab] = useState('employees');
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [canManage, setCanManage] = useState(false);
    const [fatal, setFatal] = useState('');

    const [settings, setSettings] = useState(null);
    const [release, setRelease] = useState(null);
    const [employees, setEmployees] = useState([]);
    const [reportRows, setReportRows] = useState([]);
    const [reportRejected, setReportRejected] = useState(0);
    const [reportFrom, setReportFrom] = useState(isoDaysAgo(13));
    const [reportTo, setReportTo] = useState(isoDaysAgo(0));

    const [search, setSearch] = useState('');
    const [selected, setSelected] = useState(new Set());
    const [bulkOpen, setBulkOpen] = useState(false);
    const [bulkThreshold, setBulkThreshold] = useState('180');
    const [bulkEnabled, setBulkEnabled] = useState('keep');

    const [uploadOpen, setUploadOpen] = useState(false);
    const [uploadVersion, setUploadVersion] = useState('');
    const [uploadNotes, setUploadNotes] = useState('');
    const uploadFileRef = useRef(null);

    // showToast объявлен обычной функцией в теле App и НОВЫЙ на каждый рендер.
    // Попав в зависимости загрузки, он заставлял раздел перезапрашивать данные
    // без остановки — при отказе доступа это выглядело как лента одинаковых
    // красных плашек. Та же мина описана в useStableCallback.
    const toast = useStableCallback(showToast);
    const headerFactory = useStableCallback(withAccessTokenHeader);

    const authHeaders = useCallback(
        (extra = {}) => headerFactory({ 'X-User-Id': String(user?.id ?? ''), ...extra }),
        [headerFactory, user?.id]
    );

    const request = useCallback(async (path, options = {}) => {
        const response = await fetch(`${apiBaseUrl}/api/oktell_guard${path}`, {
            credentials: 'include',
            ...options,
            headers: authHeaders(options.headers || {}),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data?.error || `HTTP ${response.status}`);
        return data;
    }, [apiBaseUrl, authHeaders]);

    /* ─── загрузка ─── */

    const loadAll = useCallback(async () => {
        setLoading(true);
        try {
            const [settingsData, employeesData] = await Promise.all([
                request('/settings'),
                request('/employees'),
            ]);
            setSettings(settingsData.settings || {});
            setRelease(settingsData.release || null);
            setCanManage(Boolean(settingsData.can_manage));
            setEmployees(Array.isArray(employeesData.employees) ? employeesData.employees : []);
        } catch (error) {
            // Отказ в доступе — не повод пытаться снова: раздел закрыт, и
            // повтор даст только ещё одну такую же плашку.
            setFatal(error.message || 'Раздел недоступен');
            toast(`Не удалось загрузить раздел: ${error.message}`, 'error');
        } finally {
            setLoading(false);
        }
    }, [request]);

    // Ровно один раз на открытие раздела. Перезагрузка — по действию человека.
    useEffect(() => { loadAll(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

    const loadReport = useCallback(async () => {
        try {
            const data = await request(`/report?from=${reportFrom}&to=${reportTo}`);
            setReportRows(Array.isArray(data.rows) ? data.rows : []);
            setReportRejected(Number(data.rejected || 0));
        } catch (error) {
            toast(`Отчёт не загрузился: ${error.message}`, 'error');
        }
    }, [request, reportFrom, reportTo]);

    useEffect(() => {
        if (tab === 'report' && !fatal) loadReport();
        /* eslint-disable-next-line react-hooks/exhaustive-deps */
    }, [tab, reportFrom, reportTo, fatal]);

    /* ─── сохранение ─── */

    const patchSettings = useCallback(async (patch) => {
        setSettings((prev) => ({ ...(prev || {}), ...patch }));   // сразу в интерфейсе, без ожидания сети
        setSaving(true);
        try {
            const data = await request('/settings', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(patch),
            });
            setSettings(data.settings || {});
        } catch (error) {
            toast(`Не сохранилось: ${error.message}`, 'error');
            loadAll();
        } finally {
            setSaving(false);
        }
    }, [request, loadAll]);

    const applyBulk = useCallback(async () => {
        const payload = { user_ids: Array.from(selected) };
        // Три состояния порога: задать, сбросить к общему, не трогать. Без
        // явного «не трогать» изменение одного лишь статуса обнулило бы всем
        // персональные пороги.
        if (bulkThreshold === 'default') payload.threshold = 'default';
        else if (bulkThreshold !== 'keep') payload.threshold = Number(bulkThreshold);
        if (bulkEnabled !== 'keep') payload.enabled = bulkEnabled === 'on';

        try {
            const data = await request('/employees/bulk', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            toast(`Изменено сотрудников: ${data.changed}`, 'success');
            setBulkOpen(false);
            setSelected(new Set());
            loadAll();
        } catch (error) {
            toast(`Не применилось: ${error.message}`, 'error');
        }
    }, [request, selected, bulkThreshold, bulkEnabled, loadAll]);

    const downloadAgent = useCallback(async () => {
        try {
            const data = await request('/download');
            window.open(data.url, '_blank', 'noopener');
        } catch (error) {
            toast(error.message, 'error');
        }
    }, [request]);

    const uploadRelease = useCallback(async () => {
        const file = uploadFileRef.current?.files?.[0];
        if (!file || !uploadVersion.trim()) {
            toast('Нужны файл и номер версии', 'error');
            return;
        }
        const form = new FormData();
        form.append('file', file);
        form.append('version', uploadVersion.trim());
        form.append('notes', uploadNotes.trim());
        setSaving(true);
        try {
            const response = await fetch(`${apiBaseUrl}/api/oktell_guard/release`, {
                method: 'POST',
                credentials: 'include',
                headers: authHeaders(),          // Content-Type ставит сам браузер: с multipart вручную нельзя
                body: form,
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data?.error || `HTTP ${response.status}`);
            toast(`Версия ${data.version} загружена — агенты обновятся сами`, 'success');
            setUploadOpen(false);
            setUploadVersion('');
            setUploadNotes('');
            loadAll();
        } catch (error) {
            toast(`Не загрузилось: ${error.message}`, 'error');
        } finally {
            setSaving(false);
        }
    }, [apiBaseUrl, authHeaders, uploadVersion, uploadNotes, loadAll]);

    /* ─── производные ─── */

    const filtered = useMemo(() => {
        const needle = search.trim().toLowerCase();
        if (!needle) return employees;
        return employees.filter((row) => (
            String(row.name || '').toLowerCase().includes(needle)
            || String(row.sip_number || '').includes(needle)
        ));
    }, [employees, search]);

    const stats = useMemo(() => ({
        total: employees.length,
        withAgent: employees.filter((row) => row.agent_seen_at).length,
        kicks: employees.reduce((sum, row) => sum + Number(row.kicks_30d || 0), 0),
        personal: employees.filter((row) => row.personal_threshold_s).length,
        // Считаем от тех, кто сейчас В Oktell: сравнивать с общим списком
        // бессмысленно — половина смены просто не на работе.
        inOktell: employees.filter((row) => row.agent_session).length,
        counting: employees.filter((row) => row.rule_alive === true).length,
    }), [employees]);

    const toggleRow = (id) => setSelected((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id); else next.add(id);
        return next;
    });

    const defaultThreshold = Number(settings?.threshold_s || 180);

    /* ─── разметка ─── */

    if (loading) {
        return (
            <div style={{ fontFamily: APPLE_FONT }} className="p-6 text-[13.5px] text-slate-500">
                Загрузка раздела…
            </div>
        );
    }

    if (fatal) {
        return (
            <div style={{ fontFamily: APPLE_FONT }} className={`${iosCard} p-6 text-center`}>
                <div className="text-[15px] font-semibold text-slate-900">Раздел недоступен</div>
                <div className="mt-1 text-[13px] text-slate-500">{fatal}</div>
                <button type="button" className={`${iosBtnSecondary} mt-4`} onClick={() => { setFatal(''); loadAll(); }}>
                    Попробовать снова
                </button>
            </div>
        );
    }

    return (
        <div style={{ fontFamily: APPLE_FONT }} className="space-y-4 pb-24">
            {/* Шапка */}
            <div className={`${iosCard} p-4`}>
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-3">
                        <div className="grid h-10 w-10 place-items-center rounded-2xl bg-blue-50 text-blue-600">
                            <FaIcon className="fas fa-hourglass-half" style={{ width: 16, height: 16 }} />
                        </div>
                        <div>
                            <div className="flex items-center gap-2 text-[15px] font-semibold text-slate-900">
                                Ограничитель «Перезвона»
                                <IosHint text="Правило считается прямо в окне Oktell на машине сотрудника: клиент и так получает от АТС событие смены статуса, поэтому база не опрашивается. Каждый присланный выброс сервер сверяет с историей статусов самого Oktell — в отчёт попадает только подтверждённое." />
                            </div>
                            {/* Состояние показано бейджем справа — здесь только суть. */}
                            <div className="text-[12.5px] text-slate-500">
                                {settings?.enabled
                                    ? `Порог ${fmtMinutes(defaultThreshold)}, обнуляет звонок`
                                    : 'Агенты на машинах ничего не делают'}
                            </div>
                        </div>
                    </div>
                    <div className="flex items-center gap-2">
                        {/* Состояние — словами. Голый тумблер рядом с кнопкой читался
                            как её часть, и было неясно, что именно он включает;
                            сам переключатель живёт в «Общих», с подписью. */}
                        <IosBadge tone={settings?.enabled ? (settings?.dry_run ? 'amber' : 'green') : 'slate'}>
                            {settings?.enabled ? (settings?.dry_run ? 'Обкатка' : 'Работает') : 'Выключен'}
                        </IosBadge>
                        <button type="button" className={iosBtnSecondary} onClick={downloadAgent}>
                            <FaIcon className="fas fa-download" style={{ width: 12, height: 12 }} />
                            Скачать агента
                        </button>
                    </div>
                </div>

                <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
                    {[
                        { label: 'Сотрудников', value: stats.total },
                        { label: 'С агентом', value: `${stats.withAgent} из ${stats.total}` },
                        // Главное число раздела: сколько человек ограничитель
                        // СЕЙЧАС реально считает. «С агентом» этого не говорит.
                        { label: 'Под правилом', value: `${stats.counting} из ${stats.inOktell}` },
                        { label: 'Выбросов за 30 дней', value: stats.kicks },
                    ].map((item) => (
                        <div key={item.label} className="rounded-xl bg-slate-50 px-3 py-2">
                            <div className="text-[11px] uppercase tracking-wide text-slate-500">{item.label}</div>
                            <div className="text-[15px] font-semibold text-slate-900">{item.value}</div>
                        </div>
                    ))}
                </div>
            </div>

            {/* Вкладки */}
            <div className="flex gap-1 rounded-2xl bg-slate-100 p-1">
                {TABS.map((item) => (
                    <button
                        key={item.id}
                        type="button"
                        onClick={() => setTab(item.id)}
                        className={`flex-1 rounded-xl px-3 py-2 text-[13px] font-semibold transition ${
                            tab === item.id ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'
                        }`}
                    >
                        {item.label}
                    </button>
                ))}
            </div>

            {/* ── Сотрудники ── */}
            {tab === 'employees' && (
                <IosSection
                    title="Сотрудники"
                    hint="Справа — состояние программы на машине и число выбросов за 30 дней."
                    right={(
                        <input
                            value={search}
                            onChange={(event) => setSearch(event.target.value)}
                            placeholder="Имя или SIP-номер"
                            // Поле стоит НАД карточкой, на фоне страницы: серый фон
                            // iosInput там сливается и читается как простой текст.
                            className={`${iosInput} max-w-[220px] bg-white ring-1 ring-slate-200/70`}
                        />
                    )}
                >
                    {filtered.length === 0 ? (
                        <div className="py-8 text-center text-[13px] text-slate-500">
                            Никого не нашлось.
                        </div>
                    ) : (
                        <div className="divide-y divide-slate-100">
                            {filtered.map((row) => {
                                const state = agentState(row);
                                const personal = row.personal_threshold_s;
                                const checked = selected.has(row.id);
                                return (
                                    <div
                                        key={row.id}
                                        className={`flex items-center gap-3 px-1 py-2.5 transition-colors ${
                                            checked ? 'bg-blue-50/60' : 'hover:bg-slate-50'
                                        }`}
                                    >
                                        {/* Галочки — только тем, кто может править:
                                            единственное действие над выбранными это
                                            массовая правка порогов, и она под canManage.
                                            Иначе СВ выделял бы людей и упирался в
                                            «Выбрано: 3» без единой кнопки. */}
                                        {canManage && (
                                            <input
                                                type="checkbox"
                                                checked={checked}
                                                onChange={() => toggleRow(row.id)}
                                                className="h-4 w-4 shrink-0 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                                            />
                                        )}
                                        <div className="min-w-0 flex-1">
                                            <div className="truncate text-[14px] font-medium text-slate-900">{row.name}</div>
                                            <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-slate-500">
                                                {row.sip_number
                                                    ? <span className="tabular-nums">SIP {row.sip_number}</span>
                                                    : <IosBadge tone="amber">нет SIP-номера</IosBadge>}
                                                {row.department_name && <span>· {row.department_name}</span>}
                                                {row.managed_today && (
                                                    <span className="text-emerald-600">· вход через приложение</span>
                                                )}
                                            </div>
                                        </div>
                                        <div className="hidden shrink-0 sm:block">
                                            {row.rule_enabled === false ? (
                                                <IosBadge tone="slate">не участвует</IosBadge>
                                            ) : personal ? (
                                                <IosBadge tone="blue">{fmtMinutes(personal)}</IosBadge>
                                            ) : (
                                                <span className="text-[12.5px] text-slate-400">{fmtMinutes(defaultThreshold)}</span>
                                            )}
                                        </div>
                                        <div className="w-[150px] shrink-0 text-right">
                                            <IosBadge tone={state.tone}>{state.text}</IosBadge>
                                        </div>
                                        <div className="w-[52px] shrink-0 text-right tabular-nums text-[14px] font-semibold text-slate-900">
                                            {row.kicks_30d || 0}
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    )}
                </IosSection>
            )}

            {/* ── Общие ── */}
            {tab === 'common' && settings && (
                <div className="space-y-4">
                    {/* Заблокированное поле без объяснения читается как поломка.
                        СВ раздел открыт на просмотр — так и говорим, прямо над
                        погашенными тумблерами. */}
                    {!canManage && (
                        <div className={`${iosCard} px-3.5 py-2.5 text-[12.5px] leading-relaxed text-slate-600`}>
                            Раздел открыт вам на просмотр. Порог, режим обкатки и версию программы
                            меняют глава отдела и администраторы — они действуют сразу на весь отдел.
                        </div>
                    )}
                    <IosSection title="Правило">
                        <label className="flex items-center justify-between gap-3">
                            <span className="flex items-center gap-2 text-[13.5px] text-slate-700">
                                Ограничитель включён
                                <IosHint text="Выключен — агенты на машинах ничего не делают: не предупреждают и не разлогинивают. Настройки при этом сохраняются." />
                            </span>
                            <IosToggle
                                checked={Boolean(settings.enabled)}
                                disabled={!canManage}
                                onChange={(value) => patchSettings({ enabled: value })}
                            />
                        </label>
                        <div className="h-px bg-slate-100" />
                        <label className="flex items-center justify-between gap-3">
                            <span className="flex items-center gap-2 text-[13.5px] text-slate-700">
                                Режим обкатки
                                <IosHint text="Агент считает время и записывает нарушения, но никого не выкидывает. Так первую неделю видно, кого и как часто задело бы, до того как это почувствуют люди." />
                            </span>
                            <IosToggle
                                checked={Boolean(settings.dry_run)}
                                disabled={!canManage}
                                onChange={(value) => patchSettings({ dry_run: value })}
                            />
                        </label>

                        <div className="space-y-1.5">
                            <div className="flex items-center gap-2 text-[13.5px] text-slate-700">
                                Порог по умолчанию
                                <IosHint text="Сколько всего можно пробыть в «Перезвоне» между звонками. Время накапливается: переключение статуса туда-обратно и перезагрузка страницы счётчик не обнуляют — обнуляет только состоявшийся звонок." />
                            </div>
                            <div className="flex flex-wrap gap-1.5">
                                {THRESHOLD_PRESETS.map((value) => (
                                    <button
                                        key={value}
                                        type="button"
                                        disabled={!canManage}
                                        onClick={() => patchSettings({ threshold_s: value })}
                                        className={`rounded-xl px-3 py-2 text-[13px] font-semibold transition ${
                                            defaultThreshold === value
                                                ? 'bg-blue-600 text-white shadow-sm'
                                                : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                                        } disabled:opacity-50`}
                                    >
                                        {fmtMinutes(value)}
                                    </button>
                                ))}
                            </div>
                        </div>

                        <div className="space-y-1.5">
                            <div className="flex items-center gap-2 text-[13.5px] text-slate-700">
                                Предупреждать за
                                <IosHint text="За сколько секунд до конца поверх окна Oktell появится плашка с обратным отсчётом. Ноль — выкидывать без предупреждения." />
                            </div>
                            <input
                                type="number"
                                min="0"
                                max="300"
                                disabled={!canManage}
                                value={settings.warn_before_s ?? 30}
                                onChange={(event) => setSettings((prev) => ({ ...prev, warn_before_s: event.target.value }))}
                                onBlur={(event) => patchSettings({ warn_before_s: Number(event.target.value || 0) })}
                                className={`${iosInput} max-w-[140px]`}
                            />
                        </div>
                    </IosSection>

                    <IosSection
                        title="Подключение к Oktell"
                        hint="Эти значения агент забирает сам при запуске — на машине сотрудника ничего настраивать не нужно."
                    >
                        <div className="space-y-1.5">
                            <div className="text-[13.5px] text-slate-700">Адрес веб-клиента</div>
                            <input
                                disabled={!canManage}
                                value={settings.oktell_url || ''}
                                onChange={(event) => setSettings((prev) => ({ ...prev, oktell_url: event.target.value }))}
                                onBlur={(event) => patchSettings({ oktell_url: event.target.value.trim() })}
                                placeholder="https://…"
                                className={iosInput}
                            />
                        </div>
                        <div className="space-y-1.5">
                            <div className="flex items-center gap-2 text-[13.5px] text-slate-700">
                                Отпечаток сертификата
                                <IosHint text="Сертификат Oktell выписан внутренним центром, и на чистом профиле браузер показывает «Подключение не защищено». Отпечаток разрешает ровно этот сертификат, не отключая проверки вообще. Пусто — если сертификат уже в доверенных на машинах." />
                            </div>
                            <input
                                disabled={!canManage}
                                value={settings.cert_spki || ''}
                                onChange={(event) => setSettings((prev) => ({ ...prev, cert_spki: event.target.value }))}
                                onBlur={(event) => patchSettings({ cert_spki: event.target.value.trim() })}
                                placeholder="base64-отпечаток"
                                className={`${iosInput} font-mono text-[12.5px]`}
                            />
                        </div>
                    </IosSection>

                    <IosSection
                        title="Программа на компьютере"
                        right={canManage ? (
                            <button type="button" className={iosBtnGhost} onClick={() => setUploadOpen(true)}>
                                <FaIcon className="fas fa-upload" style={{ width: 12, height: 12 }} />
                                Загрузить версию
                            </button>
                        ) : null}
                        hint="Сотрудник скачивает файл один раз: дальше программа ставит себя сама и сама обновляется до новой версии. Windows покажет «Неизвестный издатель» — это про отсутствие подписи, а не про содержимое: нужно нажать «Подробнее» и «Выполнить в любом случае», один раз на компьютер."
                    >
                        {release ? (
                            <div className="flex flex-wrap items-center justify-between gap-3">
                                <div>
                                    <div className="text-[14px] font-semibold text-slate-900">Версия {release.version}</div>
                                    <div className="text-[12px] text-slate-500">
                                        {fmtSize(release.size)} · загружена {fmtDateTime(release.uploaded_at)}
                                    </div>
                                    {release.notes && <div className="mt-1 text-[12.5px] text-slate-600">{release.notes}</div>}
                                </div>
                                <button type="button" className={iosBtnPrimary} onClick={downloadAgent}>
                                    <FaIcon className="fas fa-download" style={{ width: 12, height: 12 }} />
                                    Скачать
                                </button>
                            </div>
                        ) : (
                            <div className="py-4 text-center text-[13px] text-slate-500">
                                Версия ещё не загружена — сотрудникам нечего скачивать.
                            </div>
                        )}
                    </IosSection>
                </div>
            )}

            {/* ── Отчёт ── */}
            {tab === 'report' && (
                <IosSection
                    title="Кого и когда выкинуло"
                    hint={reportRejected > 0
                        ? `Не подтверждено историей Oktell: ${reportRejected}. В отчёт такие не попадают.`
                        : 'В отчёте только то, что подтвердилось историей статусов самого Oktell.'}
                    right={(
                        <div className="flex items-center gap-1.5">
                            <input
                                type="date"
                                value={reportFrom}
                                onChange={(event) => setReportFrom(event.target.value)}
                                className={`${iosInput} max-w-[150px]`}
                            />
                            <span className="text-slate-400">—</span>
                            <input
                                type="date"
                                value={reportTo}
                                onChange={(event) => setReportTo(event.target.value)}
                                className={`${iosInput} max-w-[150px]`}
                            />
                        </div>
                    )}
                >
                    {reportRows.length === 0 ? (
                        <div className="py-8 text-center text-[13px] text-slate-500">
                            За выбранные дни никого не выкидывало.
                        </div>
                    ) : (
                        <div className="overflow-x-auto">
                            <table className="w-full min-w-[600px] border-separate border-spacing-y-1">
                                <thead>
                                    <tr className="text-left text-[11px] uppercase tracking-wide text-slate-500">
                                        <th className="px-2 py-1">Дата</th>
                                        <th className="px-2 py-1">Сотрудник</th>
                                        <th className="px-2 py-1 text-right">Выбросов</th>
                                        {/* Пересиженное — не выброс, а его отсутствие: по истории
                                            АТС человек просидел дольше нормы, а ограничитель до
                                            него не доехал. Складывать с выбросами нельзя. */}
                                        <th className="px-2 py-1 text-right">Пересидел без выброса</th>
                                        <th className="px-2 py-1 text-right">Дольше всего</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {reportRows.map((row, index) => (
                                        <tr key={`${row.user_id}-${row.day}-${index}`} className="bg-slate-50/70 text-[13.5px]">
                                            <td className="rounded-l-xl px-2 py-2 text-slate-600">{fmtDay(row.day)}</td>
                                            <td className="px-2 py-2">
                                                <span className="font-medium text-slate-900">{row.name}</span>
                                                <span className="ml-2 text-[12px] text-slate-500">{row.sip_number}</span>
                                                {row.had_dry_run && (
                                                    <IosBadge tone="slate" className="ml-2">обкатка</IosBadge>
                                                )}
                                            </td>
                                            <td className="px-2 py-2 text-right tabular-nums font-semibold text-slate-900">{row.kicks || 0}</td>
                                            <td className="px-2 py-2 text-right tabular-nums font-semibold">
                                                {row.missed
                                                    ? <span className="text-amber-600">{row.missed}</span>
                                                    : <span className="text-slate-300">—</span>}
                                            </td>
                                            <td className="rounded-r-xl px-2 py-2 text-right tabular-nums text-slate-600">
                                                {fmtMinutes(row.max_seconds)}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </IosSection>
            )}

            {/* Полоска выбранных */}
            {tab === 'employees' && selected.size > 0 && (
                <div className="pointer-events-none fixed inset-x-0 bottom-6 z-30 flex justify-center px-4">
                    <div className="pointer-events-auto flex items-center gap-1.5 rounded-2xl bg-slate-900/90 px-3 py-2 text-white shadow-2xl ring-1 ring-white/10 backdrop-blur-xl">
                        <span className="px-1.5 text-[13px] font-medium">Выбрано: {selected.size}</span>
                        {canManage && (
                            <button
                                type="button"
                                onClick={() => setBulkOpen(true)}
                                className="inline-flex items-center gap-1.5 rounded-xl bg-blue-600 px-3 py-1.5 text-[13px] font-semibold transition hover:bg-blue-500 active:scale-[0.98]"
                            >
                                <FaIcon className="fas fa-sliders-h" style={{ width: 12, height: 12 }} />
                                Изменить
                            </button>
                        )}
                        <button
                            type="button"
                            onClick={() => setSelected(new Set(filtered.map((row) => row.id)))}
                            className="rounded-xl px-3 py-1.5 text-[13px] font-medium text-slate-300 transition hover:bg-white/10 hover:text-white"
                        >
                            Все ({filtered.length})
                        </button>
                        <button
                            type="button"
                            onClick={() => setSelected(new Set())}
                            className="rounded-xl px-3 py-1.5 text-[13px] font-medium text-slate-300 transition hover:bg-white/10 hover:text-white"
                        >
                            Снять
                        </button>
                    </div>
                </div>
            )}

            {/* Массовое изменение */}
            <IosModal
                open={bulkOpen}
                onClose={() => setBulkOpen(false)}
                title="Изменить выбранным"
                subtitle={`Сотрудников: ${selected.size}`}
                footer={(
                    <div className="flex justify-end gap-2">
                        <button type="button" className={iosBtnSecondary} onClick={() => setBulkOpen(false)}>Отмена</button>
                        <button type="button" className={iosBtnPrimary} onClick={applyBulk}>Применить</button>
                    </div>
                )}
            >
                <div className="space-y-4">
                    <div className="space-y-1.5">
                        <div className="flex items-center gap-2 text-[13.5px] text-slate-700">
                            Порог
                            <IosHint text="«Не трогать» и «как у всех» — разные вещи: первое оставит личные пороги как есть, второе сотрёт их и вернёт общий." />
                        </div>
                        <div className="flex flex-wrap gap-1.5">
                            {[
                                { value: 'keep', label: 'Не трогать' },
                                { value: 'default', label: 'Как у всех' },
                                ...THRESHOLD_PRESETS.map((value) => ({ value: String(value), label: fmtMinutes(value) })),
                            ].map((option) => (
                                <button
                                    key={option.value}
                                    type="button"
                                    onClick={() => setBulkThreshold(option.value)}
                                    className={`rounded-xl px-3 py-2 text-[13px] font-semibold transition ${
                                        bulkThreshold === option.value
                                            ? 'bg-blue-600 text-white shadow-sm'
                                            : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                                    }`}
                                >
                                    {option.label}
                                </button>
                            ))}
                        </div>
                    </div>

                    <div className="space-y-1.5">
                        <div className="text-[13.5px] text-slate-700">Участие</div>
                        <div className="flex flex-wrap gap-1.5">
                            {[
                                { value: 'keep', label: 'Не трогать' },
                                { value: 'on', label: 'Участвует' },
                                { value: 'off', label: 'Не участвует' },
                            ].map((option) => (
                                <button
                                    key={option.value}
                                    type="button"
                                    onClick={() => setBulkEnabled(option.value)}
                                    className={`rounded-xl px-3 py-2 text-[13px] font-semibold transition ${
                                        bulkEnabled === option.value
                                            ? 'bg-blue-600 text-white shadow-sm'
                                            : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                                    }`}
                                >
                                    {option.label}
                                </button>
                            ))}
                        </div>
                    </div>
                </div>
            </IosModal>

            {/* Загрузка версии */}
            <IosModal
                open={uploadOpen}
                onClose={() => setUploadOpen(false)}
                title="Загрузить версию агента"
                subtitle="Файл уедет в хранилище, агенты обновятся сами"
                footer={(
                    <div className="flex justify-end gap-2">
                        <button type="button" className={iosBtnSecondary} onClick={() => setUploadOpen(false)}>Отмена</button>
                        <button type="button" className={iosBtnPrimary} disabled={saving} onClick={uploadRelease}>
                            {saving ? 'Загрузка…' : 'Загрузить'}
                        </button>
                    </div>
                )}
            >
                <div className="space-y-3">
                    <div className="space-y-1.5">
                        <div className="flex items-center gap-2 text-[13.5px] text-slate-700">
                            Номер версии
                            <IosHint text="Агенты сравнивают его со своим и ставят только более новую: откат назад по ошибке в номере недопустим, иначе все машины разом уедут на старую версию." />
                        </div>
                        <input
                            value={uploadVersion}
                            onChange={(event) => setUploadVersion(event.target.value)}
                            placeholder="1.1.0"
                            className={iosInput}
                        />
                    </div>
                    <div className="space-y-1.5">
                        <div className="text-[13.5px] text-slate-700">Файл</div>
                        <input ref={uploadFileRef} type="file" accept=".exe" className="text-[13px] text-slate-600" />
                    </div>
                    <div className="space-y-1.5">
                        <div className="text-[13.5px] text-slate-700">Что изменилось</div>
                        <input
                            value={uploadNotes}
                            onChange={(event) => setUploadNotes(event.target.value)}
                            placeholder="Необязательно"
                            className={iosInput}
                        />
                    </div>
                </div>
            </IosModal>
        </div>
    );
}
