import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import FaIcon from '../common/FaIcon';
import useStableCallback from '../wiki/useStableCallback';
import {
    iosCard, iosInput,
    IosBadge, IosHint, IosSection, IosSegmented, IosToggle,
} from '../ui/ios';
import { fmtDateTime, fmtDay, fmtMinutes, fmtTime, isoDaysAgo } from './oktellGuardFormat.js';
import {
    PHONE_DEPARTMENT_CODE, PHONE_GROUP_ALL, PHONE_IDLE_GROUPS, PHONE_THRESHOLD_PRESETS,
    filterPhoneEmployees, phoneEmployeeStats, phoneGroupLabel,
    phoneGroupOptions, phoneGroupTone, phoneRuleSummary, phoneWarnBlurDecision, phoneWarnUpperBound,
    togglePhoneGroup,
} from './oktellGuardPhone.js';

/*
 * «Ограничитель Перезвона» — часть отдела продаж (ТЗ 05.10.2026).
 *
 * Здесь не агент Oktell, а сам iCORE Phone: у групп ЯР и Поток он копит простой
 * в «Исходе» (без инициации звонка) и на пороге сам ставит «Офлайн» — это и
 * есть выброс. Таймер считает только в «Исходе», обнуляется только звонком, в
 * других статусах замирает. Вернуться в работу оператор может только сам,
 * выбрав «Исход»; время в «Офлайне» в отработанные часы не идёт.
 *
 * Три вкладки, как у СЗоВ:
 *   Сотрудники — кто в какой группе, участвует ли, сколько раз выкидывало.
 *   Общие      — правило: включено, порог, предупреждение, группы.
 *   Отчёт      — выбросы по дням.
 * Правило одно на весь отдел: личных порогов, агента и версий программы здесь
 * нет (телефон обновляется сам, а правило забирает вместе с настройками SIP).
 *
 * Права решает сервер: can_manage — у глобального админа и главы ОП, у СВ
 * раздел на просмотр.
 */

const TABS = [
    { id: 'employees', label: 'Сотрудники' },
    { id: 'common', label: 'Общие' },
    { id: 'report', label: 'Отчёт' },
];

const DEPARTMENT = `department=${PHONE_DEPARTMENT_CODE}`;

export default function OktellGuardPhonePanel({
    request: requestProp, toast: toastProp, tab, onTabChange, initialSettings, initialEmployees,
}) {
    // Те же стабильные обёртки, что в OktellGuardView: колбэк, новый на каждый
    // рендер родителя, в зависимостях загрузки давал бесконечные перезапросы.
    const request = useStableCallback(requestProp);
    const toast = useStableCallback(toastProp);

    const [rule, setRule] = useState(() => initialSettings?.phone_settings || null);
    const [canManage, setCanManage] = useState(() => Boolean(initialSettings?.can_manage));
    const [groupLabels, setGroupLabels] = useState(() => initialSettings?.group_labels || {});
    const [idleGroups, setIdleGroups] = useState(() => (
        Array.isArray(initialSettings?.idle_groups) ? initialSettings.idle_groups : PHONE_IDLE_GROUPS
    ));
    const [employees, setEmployees] = useState(() => (Array.isArray(initialEmployees) ? initialEmployees : []));
    const [saving, setSaving] = useState(false);

    const [search, setSearch] = useState('');
    const [group, setGroup] = useState(PHONE_GROUP_ALL);

    const [reportRows, setReportRows] = useState([]);
    const [reportTotal, setReportTotal] = useState(0);
    const [reportFrom, setReportFrom] = useState(isoDaysAgo(13));
    const [reportTo, setReportTo] = useState(isoDaysAgo(0));

    // «Предупреждать за», подтверждённое сервером. В rule это поле живёт как
    // набираемый текст, и по нему не понять, менял ли человек что-нибудь —
    // а сохранять при уходе из поля можно только настоящую правку.
    const savedWarnRef = useRef(initialSettings?.phone_settings?.warn_before_s ?? 60);
    // Номер запроса отчёта: каждая промежуточная дата в поле — свой запрос, и
    // медленный ответ за старый период не должен лечь под новые даты.
    const reportSeq = useRef(0);

    // Правило от сервера кладём только здесь — вместе с отметкой «сохранено».
    const applyServerRule = useCallback((next) => {
        if (next) savedWarnRef.current = next.warn_before_s ?? 60;
        setRule(next || null);
    }, []);

    /* ─── загрузка ─── */

    const loadEmployees = useCallback(async () => {
        try {
            const data = await request(`/employees?${DEPARTMENT}`);
            setEmployees(Array.isArray(data.employees) ? data.employees : []);
        } catch (error) {
            toast(`Список сотрудников не обновился: ${error.message}`, 'error');
        }
    }, [request, toast]);

    const loadSettings = useCallback(async () => {
        try {
            const data = await request(`/settings?${DEPARTMENT}`);
            applyServerRule(data.phone_settings);
            setCanManage(Boolean(data.can_manage));
            setGroupLabels(data.group_labels || {});
            if (Array.isArray(data.idle_groups)) setIdleGroups(data.idle_groups);
        } catch (error) {
            toast(`Правило не загрузилось: ${error.message}`, 'error');
        }
    }, [request, toast, applyServerRule]);

    const loadReport = useCallback(async () => {
        const seq = ++reportSeq.current;
        try {
            const data = await request(`/report?${DEPARTMENT}&from=${reportFrom}&to=${reportTo}`);
            if (seq !== reportSeq.current) return;
            setReportRows(Array.isArray(data.rows) ? data.rows : []);
            setReportTotal(Number(data.total || 0));
        } catch (error) {
            // Ошибка устаревшего запроса тоже не нужна: даты уже другие.
            if (seq !== reportSeq.current) return;
            toast(`Отчёт не загрузился: ${error.message}`, 'error');
        }
    }, [request, toast, reportFrom, reportTo]);

    useEffect(() => {
        if (tab === 'report') loadReport();
    }, [tab, loadReport]);

    /* ─── сохранение ─── */

    const patchRule = useCallback(async (patch) => {
        setRule((prev) => ({ ...(prev || {}), ...patch }));   // сразу в интерфейсе, без ожидания сети
        setSaving(true);
        try {
            const data = await request(`/phone/settings?${DEPARTMENT}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(patch),
            });
            applyServerRule(data.phone_settings);
            // «Участвует» у сотрудника считает сервер из правила (включено и
            // группа в списке) — после правки список надо перечитать, иначе
            // вкладка «Сотрудники» показывала бы участие по старому правилу.
            loadEmployees();
        } catch (error) {
            toast(`Не сохранилось: ${error.message}`, 'error');
            loadSettings();
        } finally {
            setSaving(false);
        }
    }, [request, toast, loadEmployees, loadSettings, applyServerRule]);

    /* ─── производные ─── */

    const stats = useMemo(() => phoneEmployeeStats(employees), [employees]);
    const groupOptions = useMemo(() => phoneGroupOptions(employees, groupLabels), [employees, groupLabels]);
    const filtered = useMemo(
        () => filterPhoneEmployees(employees, { search, group }),
        [employees, search, group]
    );

    const threshold = Number(rule?.threshold_s || 300);
    const warnMax = phoneWarnUpperBound(threshold);
    const ruleGroups = Array.isArray(rule?.groups) ? rule.groups : [];
    const reportSum = reportTotal || reportRows.reduce((sum, row) => sum + Number(row.kicks || 0), 0);

    const onWarnBlur = (event) => {
        const decision = phoneWarnBlurDecision(event.target.value, savedWarnRef.current, threshold);
        if (!decision.save) {
            setRule((prev) => ({ ...prev, warn_before_s: decision.value }));
            return;
        }
        patchRule({ warn_before_s: decision.value });
    };

    /* ─── разметка ─── */

    return (
        <>
            {/* Шапка */}
            <div className={`${iosCard} p-4`}>
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-3">
                        <div className="grid h-10 w-10 place-items-center rounded-2xl bg-orange-50 text-orange-600">
                            <FaIcon className="fas fa-hourglass-half" style={{ width: 16, height: 16 }} />
                        </div>
                        <div>
                            <div className="flex items-center gap-2 text-[15px] font-semibold text-slate-900">
                                Ограничитель «Перезвона» · Отдел продаж
                                <IosHint text="Здесь считает сам iCORE Phone у оператора, а не агент Oktell. Простой копится только в «Исходе» и обнуляется только инициацией звонка; в других статусах таймер замирает. На пороге телефон сам ставит «Офлайн» — время в нём не идёт в отработанные часы. Вернуться в работу оператор может только сам, выбрав «Исход»." />
                            </div>
                            <div className="text-[12.5px] text-slate-500">
                                {phoneRuleSummary(rule, groupLabels)}
                            </div>
                        </div>
                    </div>
                    <IosBadge tone={rule?.enabled ? 'green' : 'slate'}>
                        {rule?.enabled ? 'Работает' : 'Выключен'}
                    </IosBadge>
                </div>

                <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
                    {[
                        { label: 'Сотрудников', value: stats.total },
                        // Основа в правиле не участвует никогда: у неё нет «Исхода».
                        { label: 'Под правилом', value: `${stats.participating} из ${stats.total}` },
                        { label: 'Выкидывало за 30 дней', value: stats.kickedPeople },
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
                        onClick={() => onTabChange(item.id)}
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
                    hint="Под правилом — группы, выбранные в «Общих», пока правило включено. Справа — сколько раз выкидывало за 30 дней и когда в последний раз."
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
                    {groupOptions.length > 2 && (
                        <IosSegmented
                            value={group}
                            options={groupOptions}
                            onChange={setGroup}
                            size="xs"
                            ariaLabel="Группа"
                        />
                    )}
                    {filtered.length === 0 ? (
                        <div className="py-8 text-center text-[13px] text-slate-500">
                            Никого не нашлось.
                        </div>
                    ) : (
                        <div className="divide-y divide-slate-100">
                            {filtered.map((row) => {
                                const label = row.group_label || phoneGroupLabel(row.status_group, groupLabels);
                                return (
                                    <div key={row.id} className="flex items-center gap-3 px-1 py-2.5 hover:bg-slate-50">
                                        <div className="min-w-0 flex-1">
                                            <div className="truncate text-[14px] font-medium text-slate-900">{row.name}</div>
                                            <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-slate-500">
                                                {row.sip_number
                                                    ? <span className="tabular-nums">SIP {row.sip_number}</span>
                                                    : <IosBadge tone="amber">нет SIP-номера</IosBadge>}
                                                {label
                                                    ? <IosBadge tone={phoneGroupTone(row.status_group)}>{label}</IosBadge>
                                                    // Без группы ОП телефон работает на прежнем наборе
                                                    // статусов, и правило его не касается.
                                                    : <IosBadge tone="slate">без группы</IosBadge>}
                                                {row.group_name && row.group_name !== label && (
                                                    <span>· {row.group_name}</span>
                                                )}
                                                {/* На узком экране колонок справа нет (они sm:block) —
                                                    участие и последний выброс переезжают сюда, иначе
                                                    с телефона не видно, кто под правилом. */}
                                                <span className="sm:hidden">
                                                    {row.participates
                                                        ? <IosBadge tone="blue">участвует</IosBadge>
                                                        : <IosBadge tone="slate">не участвует</IosBadge>}
                                                </span>
                                                {row.last_kick_at && (
                                                    <span className="sm:hidden">· последний {fmtDateTime(row.last_kick_at)}</span>
                                                )}
                                            </div>
                                        </div>
                                        <div className="hidden shrink-0 sm:block">
                                            {row.participates
                                                ? <IosBadge tone="blue">участвует</IosBadge>
                                                : <IosBadge tone="slate">не участвует</IosBadge>}
                                        </div>
                                        <div className="hidden w-[130px] shrink-0 text-right text-[12px] text-slate-500 sm:block">
                                            {row.last_kick_at ? `последний ${fmtDateTime(row.last_kick_at)}` : ''}
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
            {tab === 'common' && rule && (
                <div className="space-y-4">
                    {/* Погашенное поле без объяснения читается как поломка. */}
                    {!canManage && (
                        <div className={`${iosCard} px-3.5 py-2.5 text-[12.5px] leading-relaxed text-slate-600`}>
                            Раздел открыт вам на просмотр. Правило меняют глава отдела продаж и
                            администраторы — оно действует сразу на весь отдел.
                        </div>
                    )}
                    <IosSection
                        title="Правило"
                        hint="Телефоны забирают правило вместе с настройками SIP — в течение 10 минут, перезапускать их не нужно."
                    >
                        <label className="flex items-center justify-between gap-3">
                            <span className="flex items-center gap-2 text-[13.5px] text-slate-700">
                                Автоофлайн включён
                                <IosHint text="Выключен — телефоны не считают простой и никого не переводят в «Офлайн». Настройки при этом сохраняются." />
                            </span>
                            <IosToggle
                                checked={Boolean(rule.enabled)}
                                disabled={!canManage || saving}
                                onChange={(value) => patchRule({ enabled: value })}
                            />
                        </label>

                        <div className="h-px bg-slate-100" />

                        <div className="space-y-1.5">
                            <div className="flex items-center gap-2 text-[13.5px] text-slate-700">
                                Порог простоя
                                <IosHint text="Таймер считает только в «Исходе» и обнуляется только звонком; в других статусах замирает. Дошёл до порога — телефон сам ставит «Офлайн»." />
                            </div>
                            <div className="flex flex-wrap gap-1.5">
                                {PHONE_THRESHOLD_PRESETS.map((value) => (
                                    <button
                                        key={value}
                                        type="button"
                                        disabled={!canManage || saving}
                                        onClick={() => patchRule({ threshold_s: value })}
                                        className={`rounded-xl px-3 py-2 text-[13px] font-semibold transition ${
                                            threshold === value
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
                                Предупреждать за, секунд
                                <IosHint text={`За сколько секунд до «Офлайна» плашка статуса в телефоне покраснеет с обратным отсчётом и всплывёт уведомление. Ноль — без предупреждения. При пороге ${fmtMinutes(threshold)} — не больше ${warnMax} с.`} />
                            </div>
                            <input
                                type="number"
                                min="0"
                                max={warnMax}
                                disabled={!canManage || saving}
                                value={rule.warn_before_s ?? 60}
                                onChange={(event) => setRule((prev) => ({ ...prev, warn_before_s: event.target.value }))}
                                onBlur={onWarnBlur}
                                className={`${iosInput} max-w-[140px]`}
                            />
                        </div>

                        <div className="space-y-1.5">
                            <div className="flex items-center gap-2 text-[13.5px] text-slate-700">
                                Группы
                                <IosHint text="Основы здесь нет: у неё нет «Исхода», считать нечего. Снятая группа остаётся на своих статусах, только без автоофлайна." />
                            </div>
                            <div className="flex flex-wrap gap-4">
                                {idleGroups.map((code) => (
                                    <label key={code} className="flex items-center gap-2 text-[13.5px] text-slate-700">
                                        <input
                                            type="checkbox"
                                            checked={ruleGroups.includes(code)}
                                            disabled={!canManage || saving}
                                            onChange={() => patchRule({ groups: togglePhoneGroup(ruleGroups, code, idleGroups) })}
                                            className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500 disabled:opacity-50"
                                        />
                                        {phoneGroupLabel(code, groupLabels) || code}
                                    </label>
                                ))}
                            </div>
                        </div>
                    </IosSection>

                    {(rule.updated_at || rule.updated_by_name) && (
                        <div className="px-1 text-[11px] text-slate-500">
                            Изменено {fmtDateTime(rule.updated_at)}
                            {rule.updated_by_name ? ` · ${rule.updated_by_name}` : ''}
                        </div>
                    )}
                </div>
            )}

            {/* ── Отчёт ── */}
            {tab === 'report' && (
                <IosSection
                    title="Кого и когда выкинуло в «Офлайн»"
                    hint={`Всего за период: ${reportSum}. Выброс — переход в «Офлайн», который телефон сделал сам после простоя в «Исходе»; ручной выбор статуса сюда не попадает.`}
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
                            <table className="w-full min-w-[640px] border-separate border-spacing-y-1">
                                <thead>
                                    <tr className="text-left text-[11px] uppercase tracking-wide text-slate-500">
                                        <th className="px-2 py-1">Дата</th>
                                        <th className="px-2 py-1">Сотрудник</th>
                                        <th className="px-2 py-1">Группа</th>
                                        <th className="px-2 py-1 text-right">Выбросов</th>
                                        <th className="px-2 py-1 text-right">Первый</th>
                                        <th className="px-2 py-1 text-right">Последний</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {reportRows.map((row, index) => {
                                        const label = row.group_label || phoneGroupLabel(row.status_group, groupLabels);
                                        return (
                                            <tr key={`${row.user_id}-${row.day}-${index}`} className="bg-slate-50/70 text-[13.5px]">
                                                <td className="rounded-l-xl px-2 py-2 text-slate-600">{fmtDay(row.day)}</td>
                                                <td className="px-2 py-2">
                                                    <span className="font-medium text-slate-900">{row.name}</span>
                                                    <span className="ml-2 text-[12px] text-slate-500">{row.sip_number}</span>
                                                </td>
                                                <td className="px-2 py-2">
                                                    {label
                                                        ? <IosBadge tone={phoneGroupTone(row.status_group)}>{label}</IosBadge>
                                                        : <span className="text-slate-300">—</span>}
                                                </td>
                                                <td className="px-2 py-2 text-right tabular-nums font-semibold text-slate-900">{row.kicks || 0}</td>
                                                <td className="px-2 py-2 text-right tabular-nums text-slate-600">{fmtTime(row.first_at)}</td>
                                                <td className="rounded-r-xl px-2 py-2 text-right tabular-nums text-slate-600">{fmtTime(row.last_at)}</td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    )}
                </IosSection>
            )}
        </>
    );
}
