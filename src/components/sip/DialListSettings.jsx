import React, { useCallback, useEffect, useRef, useState } from 'react';
import FaIcon from '../common/FaIcon';
import { iosCard, iosInput, iosGroupLabel, iosBtnPrimary, iosBtnSecondary, iosBtnGhost, IosBadge, IosToggle, IosSegmented } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { buildPeriodOptions, monthLabel } from '../dial_list/dialListPeriods';

/*
 * Обзвон из телефона (раздел dial_list): панели настроек отдела, базы водителей
 * и переключатель у сотрудника. Используются в разделе «Обзвон из телефона» и в
 * карточке отдела на Binotel в «Настройках SIP».
 *
 * Как работает режим: оператор видит в iCORE Phone только ФИО водителей и кнопку
 * «Позвонить»; номер в телефон не уходит — звонок инициирует сервер через Binotel
 * API, а телефон принимает входящее плечо от АТС. У отдела свой список водителей.
 * Подключение к Binotel настраивается на сервере, в интерфейсе его нет.
 *
 * Компоненты самостоятельные: сами ходят в /api/dial_list/… и не зависят от
 * состояния «Настроек SIP».
 */

const OPERATOR_MODES = [
    { value: 'inherit', label: 'Как у отдела' },
    { value: 'on', label: 'Включён' },
    { value: 'off', label: 'Выключен' },
];

const modeFrom = (value) => (value == null ? 'inherit' : (value ? 'on' : 'off'));
const modeTo = (mode) => (mode === 'inherit' ? null : mode === 'on');

const numOr = (value, fallback) => {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
};

const readError = async (resp) => {
    const data = await resp.json().catch(() => ({}));
    return data?.error || `HTTP ${resp.status}`;
};

const fmtDate = (iso) => {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
};

/* Строка iOS-списка настроек: подпись слева, элемент управления справа. */
const Row = ({ label, hint, children, wrap = false }) => (
    <div className={`flex ${wrap ? 'flex-col gap-2' : 'items-center justify-between gap-3'} px-4 py-3`}>
        <div className="min-w-0">
            <div className="text-[13.5px] text-slate-800">{label}</div>
            {hint && <div className="text-[11.5px] leading-snug text-slate-500">{hint}</div>}
        </div>
        <div className={wrap ? '' : 'shrink-0'}>{children}</div>
    </div>
);

/* Загрузка состояния отдела: настройки + сводка базы за месяц (period — ISO
 * первого дня; пусто — обзваниваемый месяц). Один хук на все панели. */
export const useDialListDepartment = ({ apiBaseUrl, authHeaders, departmentId, period = '' }) => {
    const [settings, setSettings] = useState(null);
    const [summary, setSummary] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    // 403 — раздел не для этого человека (пилот или чужой отдел): встроенные
    // карточки в таком случае не показываются вовсе, а не пишут ошибку.
    const [forbidden, setForbidden] = useState(false);

    const load = useCallback(async () => {
        if (!departmentId) return;
        setLoading(true);
        setError('');
        try {
            const qs = period && period !== 'all' ? `?period=${encodeURIComponent(period)}` : '';
            const [sResp, lResp] = await Promise.all([
                fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/settings`, { credentials: 'include', headers: authHeaders() }),
                fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/leads/summary${qs}`, { credentials: 'include', headers: authHeaders() }),
            ]);
            if (sResp.status === 403) { setForbidden(true); return; }
            if (!sResp.ok) throw new Error(await readError(sResp));
            const sData = await sResp.json();
            setSettings(sData.settings || {});
            setSummary(lResp.ok ? await lResp.json() : null);
        } catch (e) {
            setError(e.message || 'Не удалось загрузить настройки обзвона');
        } finally {
            setLoading(false);
        }
    }, [apiBaseUrl, authHeaders, departmentId, period]);

    useEffect(() => { load(); }, [load]);

    return { settings, summary, loading, error, forbidden, reload: load };
};

/** Панель настроек отдела: режим, порция, повторы, обзваниваемый месяц, номер для линии оператора. */
export const DialListSettingsPanel = ({ apiBaseUrl, authHeaders, departmentId, settings, periods = [], onSaved, canEdit = true, showToast }) => {
    const [form, setForm] = useState(null);
    const [saving, setSaving] = useState(false);

    const toast = useCallback((msg, kind = 'success') => {
        if (typeof showToast === 'function') showToast(msg, kind);
    }, [showToast]);

    useEffect(() => {
        if (!settings) return;
        setForm({
            enabled: !!settings.enabled,
            portion_size: String(settings.portion_size ?? 20),
            max_attempts: String(settings.max_attempts ?? 3),
            retry_after_hours: String(settings.retry_after_hours ?? 24),
            caller_id_for_employee: settings.caller_id_for_employee || '',
            active_period: settings.active_period || '',
        });
    }, [settings]);

    const save = async () => {
        if (!form) return;
        setSaving(true);
        try {
            const payload = {
                enabled: form.enabled,
                portion_size: numOr(form.portion_size, 20),
                max_attempts: numOr(form.max_attempts, 3),
                retry_after_hours: numOr(form.retry_after_hours, 24),
                caller_id_for_employee: form.caller_id_for_employee.trim(),
                active_period: form.active_period || '',
            };
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/settings`, {
                method: 'PUT',
                credentials: 'include',
                headers: authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify(payload),
            });
            if (!resp.ok) throw new Error(await readError(resp));
            toast('Настройки обзвона сохранены', 'success');
            onSaved?.();
        } catch (e) {
            toast(e.message || 'Не удалось сохранить', 'error');
        } finally {
            setSaving(false);
        }
    };

    if (!form) {
        return <div className={`${iosCard} p-4 text-[13px] text-slate-500`}><FaIcon className="fas fa-spinner fa-spin" /> Загрузка…</div>;
    }

    const numberInput = (key, min, max) => (
        <input
            type="number"
            min={min}
            max={max}
            value={form[key]}
            onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
            disabled={!canEdit}
            className={`${iosInput} w-24 text-center font-mono`}
        />
    );

    return (
        <div className="space-y-4">
            <section className="space-y-1.5">
                <div className={iosGroupLabel}>Режим</div>
                <div className={`${iosCard} divide-y divide-slate-100`}>
                    <Row
                        label="Обзвон из телефона включён"
                        hint="У операторов отдела в iCORE Phone появляется вкладка «Обзвон». Применяется после перезапуска телефона."
                    >
                        <IosToggle checked={form.enabled} disabled={!canEdit} onChange={(v) => setForm((f) => ({ ...f, enabled: v }))} />
                    </Row>
                    <Row label="Строк за раз" hint="Сколько ФИО оператор получает одной порцией">{numberInput('portion_size', 1, 100)}</Row>
                    <Row label="Попыток на номер" hint="После этого водитель в список не возвращается">{numberInput('max_attempts', 1, 20)}</Row>
                    <Row label="Повтор через, часов" hint="Через сколько недозвон снова попадёт в порции">{numberInput('retry_after_hours', 0, 720)}</Row>
                </div>
            </section>

            <section className="space-y-1.5">
                <div className={iosGroupLabel}>База</div>
                <div className={`${iosCard} divide-y divide-slate-100`}>
                    <Row
                        label="Какой месяц обзванивается"
                        hint={`Операторы получают порции только из базы этого месяца. Сейчас: ${monthLabel(settings?.period) || 'текущий месяц'}. Номер может повторяться в базах разных месяцев.`}
                        wrap
                    >
                        <CustomSelect
                            value={form.active_period}
                            onChange={(v) => setForm((f) => ({ ...f, active_period: v }))}
                            disabled={!canEdit}
                            options={[{ value: '', label: 'Текущий календарный месяц (автоматически)' },
                                ...buildPeriodOptions(periods, { includeNext: true }).filter((o) => o.value !== 'all')]}
                            ariaLabel="Обзваниваемый месяц"
                        />
                    </Row>
                </div>
            </section>

            <section className="space-y-1.5">
                <div className={iosGroupLabel}>Звонок</div>
                <div className={`${iosCard} divide-y divide-slate-100`}>
                    <Row
                        label="Номер, который видит линия оператора"
                        hint="Что АТС подставляет как звонящего, когда соединяет оператора с водителем. Пусто — внутренний номер самого оператора. Номер водителя сюда не попадает никогда."
                        wrap
                    >
                        <input
                            type="text"
                            value={form.caller_id_for_employee}
                            onChange={(e) => setForm((f) => ({ ...f, caller_id_for_employee: e.target.value }))}
                            placeholder="например 100"
                            disabled={!canEdit}
                            className={`${iosInput} font-mono`}
                        />
                    </Row>
                </div>
            </section>

            {canEdit && (
                <div className="flex justify-end">
                    <button type="button" onClick={save} disabled={saving} className={iosBtnPrimary}>
                        <FaIcon className={saving ? 'fas fa-spinner fa-spin' : 'fas fa-check'} />
                        Сохранить
                    </button>
                </div>
            )}
        </div>
    );
};

/** Панель базы водителей отдела за месяц: цифры, загрузка файла, последние загрузки. */
export const DialListLeadsPanel = ({ apiBaseUrl, authHeaders, departmentId, summary, period = '', onPeriodChange, onChanged, canEdit = true, showToast }) => {
    const [uploading, setUploading] = useState(false);
    const [dragOver, setDragOver] = useState(false);
    const fileRef = useRef(null);

    const toast = useCallback((msg, kind = 'success') => {
        if (typeof showToast === 'function') showToast(msg, kind);
    }, [showToast]);

    const shownPeriod = period || summary?.period || '';
    const isActive = summary ? summary.is_active_period !== false : true;
    const periodOptions = buildPeriodOptions(summary?.periods || [], { includeNext: true, activePeriod: summary?.active_period });

    const upload = async (file) => {
        if (!file || !canEdit) return;
        setUploading(true);
        try {
            const body = new FormData();
            body.append('file', file);
            if (shownPeriod) body.append('period', shownPeriod);
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/leads/upload`, {
                method: 'POST',
                credentials: 'include',
                headers: authHeaders(),
                body,
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(data?.error || `HTTP ${resp.status}`);
            toast(`${monthLabel(data.period || shownPeriod)}: новых ${data.rows_new ?? 0}, повторов ${data.rows_duplicate ?? 0}, без номера ${data.rows_invalid ?? 0}`, 'success');
            onChanged?.();
        } catch (e) {
            toast(e.message || 'Не удалось загрузить список', 'error');
        } finally {
            setUploading(false);
            if (fileRef.current) fileRef.current.value = '';
        }
    };

    const onDrop = (e) => {
        e.preventDefault();
        setDragOver(false);
        upload(e.dataTransfer?.files?.[0]);
    };

    const tiles = [
        { label: 'Всего', value: summary?.total ?? 0, tone: 'text-slate-900' },
        { label: 'Не звонили', value: summary?.by_status?.new ?? 0, tone: 'text-slate-900' },
        { label: 'В работе', value: summary?.by_status?.in_progress ?? 0, tone: 'text-amber-600' },
        { label: 'Закрыто', value: summary?.by_status?.done ?? 0, tone: 'text-emerald-600' },
        { label: 'Доступно сейчас', value: isActive ? (summary?.pool_available ?? 0) : '—', tone: 'text-blue-600' },
    ];

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2 px-1">
                <div className="flex items-center gap-2">
                    <div className={iosGroupLabel}>База за месяц</div>
                    {summary && (
                        <IosBadge tone={isActive ? 'green' : 'slate'}>{isActive ? 'обзванивается' : 'не обзванивается'}</IosBadge>
                    )}
                </div>
                <CustomSelect
                    value={shownPeriod}
                    onChange={(v) => onPeriodChange?.(v)}
                    options={periodOptions}
                    className="w-full sm:w-72"
                    ariaLabel="Месяц базы"
                />
            </div>
            {summary && !isActive && (
                <div className="rounded-xl bg-amber-50 px-3 py-2 text-[12.5px] text-amber-800">
                    Операторы сейчас получают порции из базы за {monthLabel(summary.active_period)}. Чтобы обзванивать этот месяц, переключите его во вкладке «Настройки».
                </div>
            )}
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
                {tiles.map((t) => (
                    <div key={t.label} className={`${iosCard} px-3 py-3`}>
                        <div className={`text-[20px] font-semibold tabular-nums ${t.tone}`}>{t.value}</div>
                        <div className="text-[11.5px] text-slate-500">{t.label}</div>
                    </div>
                ))}
            </div>

            {canEdit && (
                <div
                    onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                    onDragLeave={() => setDragOver(false)}
                    onDrop={onDrop}
                    className={`rounded-2xl border-2 border-dashed px-6 py-8 text-center transition ${
                        dragOver ? 'border-blue-400 bg-blue-50/60' : 'border-slate-200 bg-white'
                    }`}
                >
                    <div className="mx-auto grid h-12 w-12 place-items-center rounded-2xl bg-blue-50 text-blue-600">
                        <FaIcon className={uploading ? 'fas fa-spinner fa-spin' : 'fas fa-file-arrow-up'} style={{ width: 18, height: 18 }} />
                    </div>
                    <div className="mt-3 text-[14px] font-semibold text-slate-800">Загрузить список водителей{shownPeriod ? ` за ${monthLabel(shownPeriod)}` : ''}</div>
                    <div className="mt-1 text-[12.5px] text-slate-500">
                        Перетащите сюда CSV или Excel с колонками <b>fio</b> и <b>phone</b>, или выберите файл.
                        Внутри месяца повторные номера не дублируются; в базе другого месяца тот же номер допускается.
                    </div>
                    <input
                        ref={fileRef}
                        type="file"
                        accept=".csv,.xlsx,.xlsm"
                        className="hidden"
                        onChange={(e) => upload(e.target.files?.[0])}
                    />
                    <button type="button" onClick={() => fileRef.current?.click()} disabled={uploading} className={`${iosBtnPrimary} mt-4`}>
                        <FaIcon className="fas fa-folder-open" />
                        Выбрать файл
                    </button>
                </div>
            )}

            <section className="space-y-1.5">
                <div className={iosGroupLabel}>Загрузки за {monthLabel(shownPeriod) || 'месяц'}</div>
                <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                    {!summary?.batches?.length ? (
                        <div className="px-4 py-5 text-center text-[13px] text-slate-500">В этот месяц файлов ещё не загружали</div>
                    ) : summary.batches.slice(0, 8).map((b) => (
                        <div key={b.id} className="flex items-center justify-between gap-3 px-4 py-3">
                            <div className="min-w-0">
                                <div className="truncate text-[13.5px] font-medium text-slate-800">{b.file_name || 'файл'}</div>
                                <div className="text-[11.5px] text-slate-500">{fmtDate(b.created_at)} · строк {b.rows_total}</div>
                            </div>
                            <div className="flex shrink-0 items-center gap-1.5">
                                <IosBadge tone="green">+{b.rows_new}</IosBadge>
                                {b.rows_duplicate > 0 && <IosBadge tone="slate">повтор {b.rows_duplicate}</IosBadge>}
                                {b.rows_invalid > 0 && <IosBadge tone="red">без номера {b.rows_invalid}</IosBadge>}
                            </div>
                        </div>
                    ))}
                </div>
            </section>
        </div>
    );
};

/**
 * Компактная карточка отдела для окна «Настройки SIP»: настройки и база одним
 * блоком. В разделе «Обзвон из телефона» те же панели разнесены по вкладкам.
 */
export const DialListDepartmentCard = ({ apiBaseUrl, authHeaders, departmentId, canEdit = true, showToast }) => {
    const dept = useDialListDepartment({ apiBaseUrl, authHeaders, departmentId });
    const [tab, setTab] = useState('settings');
    if (dept.forbidden) return null;
    return (
        <section className="space-y-2">
            <div className="flex items-center justify-between gap-3 px-1">
                <div className={iosGroupLabel}>Обзвон из телефона</div>
                {dept.settings && (
                    <IosBadge tone={dept.settings.enabled ? 'green' : 'slate'}>
                        {dept.settings.enabled ? 'Включён' : 'Выключен'}
                    </IosBadge>
                )}
            </div>
            <IosSegmented
                value={tab}
                onChange={setTab}
                stretch
                options={[
                    { value: 'settings', label: 'Настройки' },
                    { value: 'leads', label: 'База водителей', count: dept.summary?.pool_available },
                ]}
                ariaLabel="Обзвон из телефона"
            />
            {dept.error ? (
                <div className={`${iosCard} p-4 text-[13px] text-rose-600`}>{dept.error}</div>
            ) : tab === 'settings' ? (
                <DialListSettingsPanel
                    apiBaseUrl={apiBaseUrl}
                    authHeaders={authHeaders}
                    departmentId={departmentId}
                    settings={dept.settings}
                    onSaved={dept.reload}
                    canEdit={canEdit}
                    showToast={showToast}
                />
            ) : (
                <DialListLeadsPanel
                    apiBaseUrl={apiBaseUrl}
                    authHeaders={authHeaders}
                    departmentId={departmentId}
                    summary={dept.summary}
                    onChanged={dept.reload}
                    canEdit={canEdit}
                    showToast={showToast}
                />
            )}
        </section>
    );
};

/**
 * Сотрудники отдела с персональным включением обзвора: как у отдела / включён /
 * выключен. Сохраняется сразу. Живёт во вкладке «Настройки» раздела — в
 * «Настройках SIP» этих переключателей нет (решение владельца 23.09.2026).
 */
export const DialListOperatorsPanel = ({ apiBaseUrl, authHeaders, departmentId, departmentEnabled, canEdit = true, showToast }) => {
    const [users, setUsers] = useState(null);
    const [error, setError] = useState('');
    const [busyId, setBusyId] = useState(null);

    const toast = useCallback((msg, kind = 'success') => {
        if (typeof showToast === 'function') showToast(msg, kind);
    }, [showToast]);

    const load = useCallback(async () => {
        try {
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/users`, { credentials: 'include', headers: authHeaders() });
            if (!resp.ok) throw new Error(await readError(resp));
            const data = await resp.json();
            setUsers(Array.isArray(data.users) ? data.users : []);
            setError('');
        } catch (e) {
            setError(e.message || 'Не удалось загрузить сотрудников');
        }
    }, [apiBaseUrl, authHeaders, departmentId]);

    useEffect(() => { load(); }, [load]);

    const change = async (user, mode) => {
        if (!canEdit || busyId) return;
        setBusyId(user.id);
        try {
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/operators/${user.id}/settings`, {
                method: 'PUT',
                credentials: 'include',
                headers: authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ enabled: modeTo(mode) }),
            });
            if (!resp.ok) throw new Error(await readError(resp));
            const data = await resp.json();
            setUsers((list) => (list || []).map((u) => (u.id === user.id ? { ...u, dial_list_enabled: data.enabled } : u)));
            toast(data.effective
                ? `${user.name}: обзвон включён, вкладка появится после перезапуска телефона`
                : `${user.name}: обзвон выключен`, 'success');
        } catch (e) {
            toast(e.message || 'Не удалось сохранить', 'error');
        } finally {
            setBusyId(null);
        }
    };

    return (
        <section className="space-y-1.5">
            <div className="flex items-center justify-between gap-3 px-1">
                <div className={iosGroupLabel}>Сотрудники</div>
                <span className="text-[11.5px] text-slate-500">
                    «Как у отдела» сейчас значит {departmentEnabled ? 'включён' : 'выключен'}
                </span>
            </div>
            <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                {error ? (
                    <div className="px-4 py-4 text-[13px] text-rose-600">{error}</div>
                ) : users === null ? (
                    <div className="px-4 py-4 text-[13px] text-slate-500"><FaIcon className="fas fa-spinner fa-spin" /> Загрузка…</div>
                ) : users.length === 0 ? (
                    <div className="px-4 py-5 text-center text-[13px] text-slate-500">В отделе пока нет сотрудников</div>
                ) : users.map((u) => {
                    const effective = u.dial_list_enabled == null ? departmentEnabled : u.dial_list_enabled;
                    return (
                        <div key={u.id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
                            <div className="min-w-0">
                                <div className="flex items-center gap-2">
                                    <span className="truncate text-[13.5px] font-medium text-slate-800">{u.name || `#${u.id}`}</span>
                                    {u.login && <span className="text-[11.5px] text-slate-400">@{u.login}</span>}
                                    <IosBadge tone={effective ? 'green' : 'slate'}>{effective ? 'обзвон' : 'обычный телефон'}</IosBadge>
                                </div>
                                <div className="text-[11.5px] text-slate-500">
                                    {u.sip_number ? `линия ${u.sip_number}` : 'линия не назначена — см. вкладку «Линии»'}
                                </div>
                            </div>
                            <div className={canEdit && busyId !== u.id ? '' : 'pointer-events-none opacity-60'}>
                                <IosSegmented
                                    value={modeFrom(u.dial_list_enabled)}
                                    options={OPERATOR_MODES}
                                    onChange={(mode) => change(u, mode)}
                                    ariaLabel={`Обзвон: ${u.name}`}
                                />
                            </div>
                        </div>
                    );
                })}
            </div>
        </section>
    );
};

/** Переключатель у одного сотрудника: как у отдела / включён / выключен. Сохраняется сразу. */
export const DialListOperatorToggle = ({ apiBaseUrl, authHeaders, operatorId, canEdit = true, showToast }) => {
    const [state, setState] = useState(null);
    const [saving, setSaving] = useState(false);

    const toast = useCallback((msg, kind = 'success') => {
        if (typeof showToast === 'function') showToast(msg, kind);
    }, [showToast]);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const resp = await fetch(`${apiBaseUrl}/api/dial_list/operators/${operatorId}/settings`, { credentials: 'include', headers: authHeaders() });
                if (!resp.ok) throw new Error(await readError(resp));
                const data = await resp.json();
                if (!cancelled) setState(data);
            } catch (e) {
                if (!cancelled) setState({ error: e.message });
            }
        })();
        return () => { cancelled = true; };
    }, [apiBaseUrl, authHeaders, operatorId]);

    const change = async (mode) => {
        if (!canEdit || saving) return;
        setSaving(true);
        try {
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/operators/${operatorId}/settings`, {
                method: 'PUT',
                credentials: 'include',
                headers: authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ enabled: modeTo(mode) }),
            });
            if (!resp.ok) throw new Error(await readError(resp));
            const data = await resp.json();
            setState(data);
            toast(data.effective
                ? 'Обзвон включён: вкладка появится после перезапуска телефона'
                : 'Обзвон выключен для сотрудника', 'success');
        } catch (e) {
            toast(e.message || 'Не удалось сохранить', 'error');
        } finally {
            setSaving(false);
        }
    };

    // Раздел не про этот отдел (403) — переключатель не показываем вовсе.
    if (state?.error) return null;

    return (
        <section className="space-y-1.5">
            <div className={iosGroupLabel}>Обзвон из телефона</div>
            <div className={`${iosCard} space-y-2 p-4`}>
                {!state ? (
                    <div className="text-[13px] text-slate-500"><FaIcon className="fas fa-spinner fa-spin" /> Загрузка…</div>
                ) : (
                    <>
                        <div className={canEdit && !saving ? '' : 'pointer-events-none opacity-60'}>
                            <IosSegmented
                                value={modeFrom(state.enabled)}
                                options={OPERATOR_MODES}
                                onChange={change}
                                stretch
                                ariaLabel="Обзвон из телефона"
                            />
                        </div>
                        <div className="text-[12px] text-slate-500">
                            Сейчас: {state.effective ? 'включён' : 'выключен'}
                            {state.enabled == null ? ` (по отделу: ${state.department_enabled ? 'включён' : 'выключен'})` : ''}.
                            Меняет набор вкладок телефона — применяется после его перезапуска.
                        </div>
                    </>
                )}
            </div>
        </section>
    );
};
