import React, { useCallback, useEffect, useMemo, useState } from 'react';
import FaIcon from '../common/FaIcon';
import { APPLE_FONT, iosCard, iosGroupLabel, iosBtnSecondary, IosBadge, IosHint, IosSegmented } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import IosDatePicker from '../ui/DatePicker';
import { DialListLeadsPanel, DialListOperatorsPanel, DialListSettingsPanel, useDialListDepartment } from '../sip/DialListSettings';
import DialListLinesPanel from './DialListLinesPanel';

/*
 * Раздел «Обзвон из телефона» — экран руководителя удалённого колл-центра.
 *
 * Три вкладки: «Операторы» (кто сколько сделал за день), «База водителей»
 * (сколько загружено, загрузка файла) и «Настройки» (режим, порция, повторы,
 * номер для линии оператора). Отдел выбирается в шапке; если он один — выбора нет.
 *
 * Как считается: строка выдачи закрывается после первой попытки с исходом,
 * подтверждённым Binotel (вебхук или опрос по generalCallID) — телефон оператора
 * здесь ничего не «доказывает». Поэтому цифры могут догонять разговор на
 * несколько секунд.
 */

const TABS = [
    { value: 'operators', label: 'Операторы', icon: <FaIcon className="fas fa-users" /> },
    { value: 'lines', label: 'Линии', icon: <FaIcon className="fas fa-phone" /> },
    { value: 'leads', label: 'База водителей', icon: <FaIcon className="fas fa-address-book" /> },
    { value: 'settings', label: 'Настройки', icon: <FaIcon className="fas fa-sliders" /> },
];

const todayIso = () => {
    const d = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};

const fmtTalk = (sec) => {
    const s = Math.max(0, Number(sec) || 0);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (h) return `${h} ч ${m} мин`;
    if (m) return `${m} мин`;
    return s ? `${s} с` : '—';
};

const pct = (part, total) => (total ? Math.round((part / total) * 100) : null);

const initials = (name) => String(name || '')
    .split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('') || '•';

/* Строка оператора: имя, полоса «обработано из выданных», цифры справа. */
const OperatorRow = ({ row, showDepartment }) => {
    const progress = row.issued ? Math.min(100, Math.round((row.done / row.issued) * 100)) : 0;
    const answered = pct(row.answered, row.attempts);
    return (
        <div className="flex items-center gap-3 px-4 py-3">
            <div className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-slate-100 text-[12px] font-semibold text-slate-600">
                {initials(row.operator_name)}
            </div>
            <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                    <div className="truncate text-[13.5px] font-medium text-slate-800">{row.operator_name || `#${row.operator_id}`}</div>
                    {showDepartment && row.department_name && (
                        <span className="truncate text-[11.5px] text-slate-400">{row.department_name}</span>
                    )}
                </div>
                <div className="mt-1 flex items-center gap-2">
                    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100">
                        <div className="h-full rounded-full bg-blue-500 transition-all" style={{ width: `${progress}%` }} />
                    </div>
                    <div className="w-24 shrink-0 text-right text-[11.5px] tabular-nums text-slate-500">
                        {row.done} из {row.issued}
                    </div>
                </div>
            </div>
            <div className="hidden shrink-0 items-center gap-1.5 sm:flex">
                <IosBadge tone="blue" title="Попыток">{row.attempts} звон.</IosBadge>
                <IosBadge tone={answered == null ? 'slate' : answered >= 50 ? 'green' : 'amber'} title="Дозвонились">
                    {row.answered}{answered != null ? ` · ${answered}%` : ''}
                </IosBadge>
                <IosBadge tone="slate" title="Разговоры">{fmtTalk(row.talk_sec)}</IosBadge>
                {row.failed > 0 && <IosBadge tone="red" title="Отказы АТС">АТС {row.failed}</IosBadge>}
            </div>
        </div>
    );
};

const DialListView = ({ user, showToast, apiBaseUrl, withAccessTokenHeader, canEdit = true }) => {
    const authHeaders = useCallback(
        (extra = {}) => withAccessTokenHeader({ 'X-User-Id': String(user?.id ?? ''), ...extra }),
        [withAccessTokenHeader, user?.id]
    );

    const [departments, setDepartments] = useState(null); // null — ещё грузим
    const [candidates, setCandidates] = useState([]);     // отделы на Binotel, ещё не подключённые
    const [enrollPick, setEnrollPick] = useState('');
    const [enrolling, setEnrolling] = useState(false);
    const [departmentId, setDepartmentId] = useState('');
    const [tab, setTab] = useState('operators');
    const [date, setDate] = useState(todayIso);
    const [rows, setRows] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    // Отделы раздела — своим списком: бэкенд сам знает, какие отделы подключены
    // и какие из них в зоне запросившего. Кандидаты (на Binotel, но ещё не
    // подключённые) приходят только админу — для кнопки «Подключить отдел».
    const loadDepartments = useCallback(async (preferId = null) => {
        try {
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments`, { credentials: 'include', headers: authHeaders() });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(data?.error || `HTTP ${resp.status}`);
            const list = Array.isArray(data.departments) ? data.departments : [];
            const cands = Array.isArray(data.candidates) ? data.candidates : [];
            setDepartments(list);
            setCandidates(cands);
            setEnrollPick((cur) => cur || (cands[0] ? String(cands[0].department_id) : ''));
            setDepartmentId((current) => {
                if (preferId && list.some((d) => String(d.department_id) === String(preferId))) return String(preferId);
                if (current && list.some((d) => String(d.department_id) === current)) return current;
                return list[0] ? String(list[0].department_id) : '';
            });
        } catch (e) {
            setDepartments([]);
            setError(e.message || 'Не удалось загрузить отделы');
        }
    }, [apiBaseUrl, authHeaders]);

    useEffect(() => { loadDepartments(); }, [loadDepartments]);

    // Кандидаты приходят только админу: по ним же понимаем, что отключать отдел можно.
    const candidatesAdmin = Array.isArray(candidates);

    const unenroll = async () => {
        if (!departmentId || enrolling) return;
        const dep = (departments || []).find((d) => String(d.department_id) === String(departmentId));
        if (!window.confirm(`Отключить отдел «${dep?.department_name || ''}» от обзвона? Операторы вернутся к обычному телефону после перезапуска.`)) return;
        setEnrolling(true);
        try {
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/unenroll`, {
                method: 'POST', credentials: 'include', headers: authHeaders({ 'Content-Type': 'application/json' }), body: '{}',
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(data?.error || `HTTP ${resp.status}`);
            showToast?.('Отдел отключён от обзвона', 'success');
            setDepartmentId('');
            await loadDepartments();
            setTab('operators');
        } catch (e) {
            showToast?.(e.message || 'Не удалось отключить отдел', 'error');
        } finally {
            setEnrolling(false);
        }
    };

    const enroll = async () => {
        if (!enrollPick || enrolling) return;
        setEnrolling(true);
        try {
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${enrollPick}/enroll`, {
                method: 'POST', credentials: 'include', headers: authHeaders({ 'Content-Type': 'application/json' }), body: '{}',
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(data?.error || `HTTP ${resp.status}`);
            const picked = candidates.find((d) => String(d.department_id) === String(enrollPick));
            showToast?.(`Отдел «${picked?.department_name || ''}» подключён. Включите режим во вкладке «Настройки»`, 'success');
            await loadDepartments(enrollPick);
            setTab('settings');
        } catch (e) {
            showToast?.(e.message || 'Не удалось подключить отдел', 'error');
        } finally {
            setEnrolling(false);
        }
    };

    const loadOverview = useCallback(async () => {
        if (!departmentId) { setRows([]); setLoading(false); return; }
        setLoading(true);
        try {
            const qs = new URLSearchParams({ date, department_id: departmentId });
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/overview?${qs.toString()}`, { credentials: 'include', headers: authHeaders() });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(data?.error || `HTTP ${resp.status}`);
            setRows(Array.isArray(data.operators) ? data.operators : []);
            setError('');
        } catch (e) {
            setError(e.message || 'Не удалось загрузить сводку');
            setRows([]);
        } finally {
            setLoading(false);
        }
    }, [apiBaseUrl, authHeaders, date, departmentId]);

    useEffect(() => { loadOverview(); }, [loadOverview]);

    const dept = useDialListDepartment({ apiBaseUrl, authHeaders, departmentId: departmentId || null });
    const selected = (departments || []).find((d) => String(d.department_id) === String(departmentId));

    const totals = useMemo(() => rows.reduce((acc, r) => ({
        issued: acc.issued + (r.issued || 0),
        done: acc.done + (r.done || 0),
        attempts: acc.attempts + (r.attempts || 0),
        answered: acc.answered + (r.answered || 0),
        talk_sec: acc.talk_sec + (r.talk_sec || 0),
        failed: acc.failed + (r.failed || 0),
    }), { issued: 0, done: 0, attempts: 0, answered: 0, talk_sec: 0, failed: 0 }), [rows]);

    const sortedRows = useMemo(() => [...rows].sort((a, b) => (b.done - a.done) || (b.attempts - a.attempts)
        || String(a.operator_name || '').localeCompare(String(b.operator_name || ''), 'ru')), [rows]);

    const enabled = !!dept.settings?.enabled;
    const isToday = date === todayIso();

    const subtitle = !selected
        ? (candidates.length ? 'Подключите отдел, чтобы начать' : 'Отделов для обзвона пока нет')
        : enabled
            ? `Порции по ${dept.settings?.portion_size ?? selected.portion_size}, в базе ${selected.leads_total}${dept.summary ? `, доступно ${dept.summary.pool_available}` : ''}`
            : 'Режим выключен: операторы вкладку «Обзвон» не видят';

    return (
        <div style={{ fontFamily: APPLE_FONT }} className="space-y-4 pb-24">
            {/* Шапка */}
            <div className={`${iosCard} p-4`}>
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-3">
                        <div className="grid h-10 w-10 place-items-center rounded-2xl bg-blue-50 text-blue-600">
                            <FaIcon className="fas fa-list-check" style={{ width: 16, height: 16 }} />
                        </div>
                        <div>
                            <div className="flex items-center gap-2 text-[15px] font-semibold text-slate-900">
                                Обзвон из телефона
                                <IosHint text="Оператор видит в iCORE Phone только ФИО и кнопку «Позвонить». Звонок инициирует сервер через Binotel API, номер водителя телефону не передаётся. Исход каждого звонка подтверждает Binotel, а не телефон." />
                            </div>
                            <div className="text-[12.5px] text-slate-500">{subtitle}</div>
                        </div>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                        {selected && (
                            <IosBadge tone={enabled ? 'green' : 'slate'}>{enabled ? 'Включён' : 'Выключен'}</IosBadge>
                        )}
                        {departments && departments.length > 1 && (
                            <CustomSelect
                                className="w-56"
                                variant="ios"
                                value={departmentId}
                                onChange={(v) => setDepartmentId(String(v))}
                                ariaLabel="Отдел"
                                options={departments.map((d) => ({ value: String(d.department_id), label: d.department_name || `Отдел ${d.department_id}` }))}
                            />
                        )}
                        {departments && departments.length === 1 && (
                            <span className="rounded-xl bg-slate-100 px-3 py-2 text-[13px] font-medium text-slate-700">{selected?.department_name}</span>
                        )}
                    </div>
                </div>

                {/* Подключение отдела: только админу и только пока есть кандидаты
                    (отделы на Binotel, которых в разделе ещё нет). */}
                {canEdit && candidates.length > 0 && (
                    <div className="mt-4 flex flex-wrap items-center gap-2 rounded-xl bg-slate-50 px-3.5 py-2.5">
                        <span className="text-[12.5px] text-slate-600">Подключить отдел к обзвону:</span>
                        <CustomSelect
                            className="w-60"
                            variant="ios"
                            value={enrollPick}
                            onChange={(v) => setEnrollPick(String(v))}
                            ariaLabel="Отдел для подключения"
                            placeholder="Выберите отдел"
                            options={candidates.map((d) => ({ value: String(d.department_id), label: d.department_name || `Отдел ${d.department_id}` }))}
                        />
                        <button type="button" onClick={enroll} disabled={enrolling || !enrollPick} className={`${iosBtnSecondary} py-1.5`}>
                            <FaIcon className={enrolling ? 'fas fa-spinner fa-spin' : 'fas fa-plus'} />
                            Подключить
                        </button>
                    </div>
                )}

                <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
                    {[
                        { label: isToday ? 'Выдано сегодня' : 'Выдано', value: totals.issued },
                        { label: 'Обработано', value: totals.done },
                        { label: 'Попыток', value: totals.attempts },
                        { label: 'Дозвонились', value: pct(totals.answered, totals.attempts) == null ? totals.answered : `${totals.answered} · ${pct(totals.answered, totals.attempts)}%` },
                        { label: 'Разговоры', value: fmtTalk(totals.talk_sec) },
                        { label: 'В базе доступно', value: dept.summary?.pool_available ?? selected?.leads_open ?? '—' },
                    ].map((item) => (
                        <div key={item.label} className="rounded-xl bg-slate-50 px-3 py-2">
                            <div className="text-[11px] uppercase tracking-wide text-slate-500">{item.label}</div>
                            <div className="text-[15px] font-semibold tabular-nums text-slate-900">{item.value}</div>
                        </div>
                    ))}
                </div>
            </div>

            {error && <div className="rounded-xl bg-rose-50 px-4 py-3 text-[13px] text-rose-700">{error}</div>}

            {departments && departments.length === 0 && !error && (
                <div className={`${iosCard} p-8 text-center`}>
                    <div className="mx-auto grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                        <FaIcon className="fas fa-building" style={{ width: 18, height: 18 }} />
                    </div>
                    <div className="mt-3 text-[14px] font-semibold text-slate-800">Нет подключённых отделов</div>
                    <div className="mt-1 text-[12.5px] text-slate-500">
                        {candidates.length
                            ? 'Выберите отдел в шапке и нажмите «Подключить».'
                            : 'Заведите отдел удалённого колл-центра и переключите его телефонию на Binotel в «Настройках SIP» — тогда его можно будет подключить здесь.'}
                    </div>
                </div>
            )}

            {selected && (
                <>
                    <IosSegmented
                        value={tab}
                        onChange={setTab}
                        options={TABS.map((t) => (t.value === 'leads' ? { ...t, count: dept.summary?.pool_available } : t))}
                        stretch
                        size="lg"
                        ariaLabel="Разделы обзвона"
                    />

                    {tab === 'operators' && (
                        <section className="space-y-1.5">
                            <div className="flex items-center justify-between gap-3 px-1">
                                <div className={iosGroupLabel}>По операторам</div>
                                <div className="flex items-center gap-2">
                                    <IosDatePicker
                                        value={date}
                                        max={todayIso()}
                                        onChange={(iso) => setDate(iso || todayIso())}
                                        placeholder="Дата"
                                    />
                                    <button type="button" onClick={loadOverview} disabled={loading} className={`${iosBtnSecondary} py-1.5`}>
                                        <FaIcon className={loading ? 'fas fa-spinner fa-spin' : 'fas fa-rotate'} />
                                    </button>
                                </div>
                            </div>
                            <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                                {loading && rows.length === 0 ? (
                                    <div className="px-4 py-6 text-center text-[13px] text-slate-500"><FaIcon className="fas fa-spinner fa-spin" /> Загрузка…</div>
                                ) : sortedRows.length === 0 ? (
                                    <div className="px-4 py-8 text-center">
                                        <div className="mx-auto grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                                            <FaIcon className="fas fa-phone-slash" style={{ width: 18, height: 18 }} />
                                        </div>
                                        <div className="mt-3 text-[14px] font-semibold text-slate-800">
                                            {isToday ? 'Сегодня звонков из списка ещё не было' : 'В этот день звонков из списка не было'}
                                        </div>
                                        <div className="mt-1 text-[12.5px] text-slate-500">
                                            {!enabled
                                                ? 'Включите режим во вкладке «Настройки» и перезапустите телефоны операторов.'
                                                : (dept.summary?.pool_available ?? 0) === 0
                                                    ? 'База пуста — загрузите список водителей во вкладке «База водителей».'
                                                    : 'Операторы получают порции сами, нажимая «Ещё» в телефоне.'}
                                        </div>
                                    </div>
                                ) : sortedRows.map((r) => (
                                    <OperatorRow key={r.operator_id} row={r} showDepartment={false} />
                                ))}
                            </div>
                        </section>
                    )}

                    {tab === 'lines' && (
                        <DialListLinesPanel
                            apiBaseUrl={apiBaseUrl}
                            authHeaders={authHeaders}
                            departmentId={selected.department_id}
                            canEdit={canEdit}
                            showToast={showToast}
                        />
                    )}

                    {tab === 'leads' && (
                        <DialListLeadsPanel
                            apiBaseUrl={apiBaseUrl}
                            authHeaders={authHeaders}
                            departmentId={selected.department_id}
                            summary={dept.summary}
                            onChanged={dept.reload}
                            canEdit={canEdit}
                            showToast={showToast}
                        />
                    )}

                    {tab === 'settings' && (
                        dept.error
                            ? <div className={`${iosCard} p-4 text-[13px] text-rose-600`}>{dept.error}</div>
                            : (
                                <div className="space-y-4">
                                    <DialListSettingsPanel
                                        apiBaseUrl={apiBaseUrl}
                                        authHeaders={authHeaders}
                                        departmentId={selected.department_id}
                                        settings={dept.settings}
                                        onSaved={() => { dept.reload(); loadDepartments(selected.department_id); }}
                                        canEdit={canEdit}
                                        showToast={showToast}
                                    />
                                    <DialListOperatorsPanel
                                        apiBaseUrl={apiBaseUrl}
                                        authHeaders={authHeaders}
                                        departmentId={selected.department_id}
                                        departmentEnabled={enabled}
                                        canEdit={canEdit}
                                        showToast={showToast}
                                    />
                                    {canEdit && candidatesAdmin && (
                                        <div className="flex justify-end">
                                            <button type="button" onClick={unenroll} disabled={enrolling} className={`${iosBtnSecondary} text-rose-600`}>
                                                <FaIcon className="fas fa-link-slash" />
                                                Отключить отдел от обзвона
                                            </button>
                                        </div>
                                    )}
                                </div>
                            )
                    )}
                </>
            )}
        </div>
    );
};

export default DialListView;
