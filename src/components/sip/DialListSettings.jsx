import React, { useCallback, useEffect, useRef, useState } from 'react';
import FaIcon from '../common/FaIcon';
import { iosCard, iosInput, iosGroupLabel, iosBtnPrimary, iosBtnSecondary, iosBtnGhost, IosBadge, IosToggle, IosSegmented } from '../ui/ios';

/*
 * Обзвон из телефона (раздел dial_list): панели настроек отдела, базы водителей
 * и переключатель у сотрудника. Используются в разделе «Обзвон из телефона» и в
 * карточке отдела на Binotel в «Настройках SIP».
 *
 * Как работает режим: оператор видит в iCORE Phone только ФИО водителей и кнопку
 * «Позвонить»; номер в телефон не уходит — звонок инициирует сервер через Binotel
 * API, а телефон принимает входящее плечо от АТС. Поэтому у отдела здесь свой ключ
 * Binotel API (у каждой компании Binotel он свой) и свой список водителей.
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

/** Секрет: показывается только признак «задан»; новое значение — заменой. */
const SecretRow = ({ label, hint, has, value, onChange, disabled, placeholder }) => {
    const [replacing, setReplacing] = useState(false);
    const showInput = !has || replacing;
    return (
        <Row label={label} hint={hint} wrap={showInput}>
            {showInput ? (
                <input
                    type="text"
                    value={value}
                    onChange={(e) => onChange(e.target.value)}
                    placeholder={has ? 'пусто — оставить прежний' : placeholder}
                    autoComplete="off"
                    disabled={disabled}
                    className={`${iosInput} font-mono`}
                />
            ) : (
                <div className="flex items-center gap-2">
                    <IosBadge tone="green"><FaIcon className="fas fa-check" /> Задан</IosBadge>
                    {!disabled && (
                        <button type="button" onClick={() => setReplacing(true)} className={iosBtnGhost}>
                            Заменить
                        </button>
                    )}
                </div>
            )}
        </Row>
    );
};

/* Загрузка состояния отдела: настройки + сводка базы. Один хук на обе панели. */
export const useDialListDepartment = ({ apiBaseUrl, authHeaders, departmentId }) => {
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
            const [sResp, lResp] = await Promise.all([
                fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/settings`, { credentials: 'include', headers: authHeaders() }),
                fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/leads/summary`, { credentials: 'include', headers: authHeaders() }),
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
    }, [apiBaseUrl, authHeaders, departmentId]);

    useEffect(() => { load(); }, [load]);

    return { settings, summary, loading, error, forbidden, reload: load };
};

/** Панель настроек отдела: режим, порция, повторы, ключ Binotel API, вебхук. */
export const DialListSettingsPanel = ({ apiBaseUrl, authHeaders, departmentId, settings, onSaved, canEdit = true, showToast }) => {
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
            binotel_api_key: '',
            binotel_api_secret: '',
            webhook_token: '',
            caller_id_for_employee: settings.caller_id_for_employee || '',
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
            };
            // Секреты уходят только если ввели новое значение: пустое — «не менять».
            if (form.binotel_api_key.trim()) payload.binotel_api_key = form.binotel_api_key.trim();
            if (form.binotel_api_secret.trim()) payload.binotel_api_secret = form.binotel_api_secret.trim();
            if (form.webhook_token.trim()) payload.webhook_token = form.webhook_token.trim();
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

    const webhookUrl = `${apiBaseUrl}/api/dial_list/webhook/binotel?token=ВАШ_ТОКЕН`;
    const copyWebhook = async () => {
        try {
            await navigator.clipboard.writeText(webhookUrl);
            toast('Адрес вебхука скопирован', 'success');
        } catch {
            toast('Не удалось скопировать — выделите адрес вручную', 'error');
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
                <div className={iosGroupLabel}>Binotel API</div>
                <div className={`${iosCard} divide-y divide-slate-100`}>
                    <SecretRow
                        label="Ключ API компании"
                        hint="Выдаётся на компанию Binotel в разделе API кабинета или поддержкой"
                        has={!!settings?.has_binotel_api_key}
                        value={form.binotel_api_key}
                        onChange={(v) => setForm((f) => ({ ...f, binotel_api_key: v }))}
                        disabled={!canEdit}
                        placeholder="key"
                    />
                    <SecretRow
                        label="Секрет API"
                        has={!!settings?.has_binotel_api_secret}
                        value={form.binotel_api_secret}
                        onChange={(v) => setForm((f) => ({ ...f, binotel_api_secret: v }))}
                        disabled={!canEdit}
                        placeholder="secret"
                    />
                    <Row
                        label="Номер, который видит линия оператора"
                        hint="Подмена номера во входящем плече (callerIdForEmployee). Пусто — как решит АТС."
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
                <p className="px-1 text-[11.5px] leading-relaxed text-slate-500">
                    Ключ должен быть допущен к дозвону по API. Если АТС отвечает кодом 104 — попросите
                    поддержку Binotel включить дозвон для линий отдела.
                </p>
            </section>

            <section className="space-y-1.5">
                <div className={iosGroupLabel}>Исход звонка от Binotel</div>
                <div className={`${iosCard} divide-y divide-slate-100`}>
                    <SecretRow
                        label="Токен вебхука"
                        hint="Любая длинная случайная строка — защищает адрес от посторонних"
                        has={!!settings?.has_webhook_token}
                        value={form.webhook_token}
                        onChange={(v) => setForm((f) => ({ ...f, webhook_token: v }))}
                        disabled={!canEdit}
                        placeholder="придумайте токен"
                    />
                    <Row label="Адрес для кабинета Binotel" hint="Вставьте в настройку «API Call Completed», подставив токен" wrap>
                        <div className="flex items-center gap-2">
                            <code className="min-w-0 flex-1 truncate rounded-xl bg-slate-100 px-3 py-2 text-[12px] text-slate-700">{webhookUrl}</code>
                            <button type="button" onClick={copyWebhook} className={iosBtnSecondary} title="Скопировать">
                                <FaIcon className="fas fa-copy" />
                            </button>
                        </div>
                    </Row>
                </div>
                <p className="px-1 text-[11.5px] leading-relaxed text-slate-500">
                    Без вебхука исход всё равно придёт: сервер сам опрашивает Binotel по каждому звонку,
                    просто на несколько секунд позже.
                </p>
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

/** Панель базы водителей отдела: цифры, загрузка файла, последние загрузки. */
export const DialListLeadsPanel = ({ apiBaseUrl, authHeaders, departmentId, summary, onChanged, canEdit = true, showToast }) => {
    const [uploading, setUploading] = useState(false);
    const [dragOver, setDragOver] = useState(false);
    const fileRef = useRef(null);

    const toast = useCallback((msg, kind = 'success') => {
        if (typeof showToast === 'function') showToast(msg, kind);
    }, [showToast]);

    const upload = async (file) => {
        if (!file || !canEdit) return;
        setUploading(true);
        try {
            const body = new FormData();
            body.append('file', file);
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/leads/upload`, {
                method: 'POST',
                credentials: 'include',
                headers: authHeaders(),
                body,
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(data?.error || `HTTP ${resp.status}`);
            toast(`Загружено: новых ${data.rows_new ?? 0}, повторов ${data.rows_duplicate ?? 0}, без номера ${data.rows_invalid ?? 0}`, 'success');
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
        { label: 'Доступно сейчас', value: summary?.pool_available ?? 0, tone: 'text-blue-600' },
    ];

    return (
        <div className="space-y-4">
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
                    <div className="mt-3 text-[14px] font-semibold text-slate-800">Загрузить список водителей</div>
                    <div className="mt-1 text-[12.5px] text-slate-500">
                        Перетащите сюда CSV или Excel с колонками <b>fio</b> и <b>phone</b>, или выберите файл.
                        Повторные номера не дублируются.
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
                <div className={iosGroupLabel}>Последние загрузки</div>
                <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                    {!summary?.batches?.length ? (
                        <div className="px-4 py-5 text-center text-[13px] text-slate-500">Файлов ещё не загружали</div>
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

/** Переключатель у сотрудника: как у отдела / включён / выключен. Сохраняется сразу. */
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
