import React, { useCallback, useEffect, useRef, useState } from 'react';
import FaIcon from '../common/FaIcon';
import { iosCard, iosInput, iosGroupLabel, iosBtnPrimary, iosBtnSecondary, iosBtnGhost, IosBadge, IosHint, IosToggle, IosSegmented } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import useStableCallback from '../wiki/useStableCallback';
import { buildPeriodOptions, monthLabel } from '../dial_list/dialListPeriods';
import {
    LIMITS, PROFILE_REMOTE_CC, PROFILE_TEZ, RETRY_MINUTES_MAX, RETRY_SCHEDULE_MAX, formatMinutes, formatSchedule,
    formatSeconds, mirrorSummary, normalizeProfile, rateLabel, retryPlanText, scheduleFromInputs, tezSettingsForm,
    tezSettingsPayload, tezUploadSummary, validateTezSettings, whenText, wrapupText,
} from '../dial_list/tezAutodial';

/*
 * Обзвон из телефона (раздел dial_list): панели настроек отдела, базы водителей
 * и переключатель у сотрудника. Используются в разделе «Удаленный КЦ» (до 07.10.2026
 * он назывался «Обзвон из телефона») и в карточке отдела на Binotel в «Настройках SIP».
 *
 * Как работает режим: оператор видит в iCORE Phone только ФИО водителей и кнопку
 * «Позвонить»; номер в телефон не уходит — звонок инициирует сервер через Binotel
 * API, а телефон принимает входящее плечо от АТС. У отдела свой список водителей.
 * Подключение к Binotel настраивается на сервере, в интерфейсе его нет.
 *
 * Компоненты самостоятельные: сами ходят в /api/dial_list/… и не зависят от
 * состояния «Настроек SIP».
 *
 * С 15.10.2026 те же панели работают и в разделе «Автообзвон Тез» — с параметром
 * profile='tez' (контракт сервер ↔ сайт ↔ телефон): там нет ИИН и Sapar, цель лида —
 * выход водителя на линию, а в настройках — автообзвон, постобработка и расписание
 * недозвонов. Без параметра (profile='remote_cc') панели те же, что были.
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

/* «строки 5, 17, 23…» — чтобы ошибку можно было найти в файле. */
const rowsText = (rows = [], total = 0) => {
    if (!rows.length) return '';
    const more = total > rows.length ? '…' : '';
    return `${rows.length === 1 && total <= 1 ? 'строка' : 'строки'} ${rows.join(', ')}${more}`;
};

/* Итог загрузки одной фразой: что добавилось и что не загрузилось и почему. */
const uploadSummary = (data, period) => {
    const parts = [`новых ${data.rows_new ?? 0}`];
    if (data.rows_duplicate) parts.push(`повторов ${data.rows_duplicate}`);
    if (data.rows_bad_iin) {
        const where = rowsText(data.bad_iin_rows, data.rows_bad_iin);
        parts.push(`без ИИН или с ошибкой в нём ${data.rows_bad_iin}${where ? ` (${where})` : ''}`);
    }
    if (data.rows_invalid) {
        const where = rowsText(data.invalid_rows, data.rows_invalid);
        parts.push(`без номера ${data.rows_invalid}${where ? ` (${where})` : ''}`);
    }
    return `${monthLabel(data.period || period)}: ${parts.join(', ')}`;
};

const fmtDate = (iso) => {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
};

/* Строка iOS-списка настроек: подпись слева, элемент управления справа.
   info — пояснение под «i» рядом с подписью (правило владельца: на экране числа и
   названия); им пользуются строки автообзвона Тез, у прежних строк — видимый hint. */
const Row = ({ label, hint, info, children, wrap = false }) => (
    <div className={`flex ${wrap ? 'flex-col gap-2' : 'items-center justify-between gap-3'} px-4 py-3`}>
        <div className="min-w-0">
            <div className="flex items-center gap-1.5 text-[13.5px] text-slate-800">
                {label}
                {info && <IosHint text={info} label={`Что значит «${label}»`} />}
            </div>
            {hint && <div className="text-[11.5px] leading-snug text-slate-500">{hint}</div>}
        </div>
        <div className={wrap ? '' : 'shrink-0'}>{children}</div>
    </div>
);

/* Загрузка состояния отдела: настройки + сводка базы за месяц (period — ISO
 * первого дня; пусто — обзваниваемый месяц). Один хук на все панели.
 *
 * profile — какого профиля отделы показывает раздел ('remote_cc' — «Удаленный КЦ»,
 * 'tez' — «Автообзвон Тез»). Профиль самого отдела приходит в его настройках
 * (сервер до профилей ключа не присылал — значит, удалённый КЦ); mismatch — отдел
 * чужого профиля: раздел его не показывает, а не правит чужими панелями. */
export const useDialListDepartment = ({ apiBaseUrl, authHeaders, departmentId, period = '', profile = PROFILE_REMOTE_CC }) => {
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

    const departmentProfile = settings ? normalizeProfile(settings.profile) : null;
    return {
        settings, summary, loading, error, forbidden, reload: load,
        profile: departmentProfile,
        mismatch: departmentProfile !== null && departmentProfile !== normalizeProfile(profile),
    };
};

/* Интервалы недозвона автообзвона Тез: список минут, у каждого — вид словами, под
   списком — что это значит для попыток («2-я попытка — через 1 ч, 3-я — через 3 ч»). */
const RetryScheduleEditor = ({ values, onChange, maxAttempts, canEdit }) => {
    const list = Array.isArray(values) ? values : [];
    const schedule = scheduleFromInputs(list);
    const plan = retryPlanText(schedule, maxAttempts);
    const set = (idx, value) => onChange(list.map((v, i) => (i === idx ? value : v)));
    const remove = (idx) => onChange(list.filter((_, i) => i !== idx));
    // Новый интервал — копия последнего: так обычно и растёт лесенка «1 ч, 3 ч, 24 ч».
    const add = () => onChange([...list, list.length ? list[list.length - 1] : '60']);
    return (
        <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
                {list.map((value, idx) => (
                    // eslint-disable-next-line react/no-array-index-key
                    <div key={idx} className="flex items-center gap-1.5 rounded-xl bg-slate-50 py-1 pl-1 pr-1.5 ring-1 ring-slate-200/70">
                        <input
                            type="number"
                            min={1}
                            max={RETRY_MINUTES_MAX}
                            value={value}
                            onChange={(e) => set(idx, e.target.value)}
                            disabled={!canEdit}
                            aria-label={`Интервал ${idx + 1}, минут`}
                            className={`${iosInput} w-20 bg-white py-1.5 text-center font-mono`}
                        />
                        <span className="min-w-[3rem] text-[12px] tabular-nums text-slate-500">{formatMinutes(value) || '—'}</span>
                        {canEdit && (
                            <button type="button" onClick={() => remove(idx)} className={`${iosBtnGhost} px-1.5 py-1`} aria-label={`Убрать интервал ${idx + 1}`}>
                                <FaIcon className="fas fa-xmark" style={{ width: 10, height: 10 }} />
                            </button>
                        )}
                    </div>
                ))}
                {canEdit && list.length < RETRY_SCHEDULE_MAX && (
                    <button type="button" onClick={add} className={`${iosBtnGhost} py-1.5 text-[12.5px]`}>
                        <FaIcon className="fas fa-plus" style={{ width: 10, height: 10 }} /> Интервал
                    </button>
                )}
            </div>
            <div className="text-[12px] text-slate-500">
                {!schedule.length
                    ? 'Список пуст — повтор через «Повтор через, часов»'
                    : plan || 'Одна попытка: после недозвона водитель не возвращается'}
            </div>
        </div>
    );
};

/* Настройки автообзвона Тез (контракт 15.10.2026, §2) — вместо группы «Режим». Режима
   отдела целиком здесь нет намеренно (решение владельца): у Тез он остаётся выключенным,
   иначе «Настройки SIP» заперли бы карточки всех операторов, а телефоны всего отдела
   переключились бы в обзвон. Кому работать в режиме, руководитель включает лично —
   «Сотрудники» ниже; это и есть «операторы под распределение». Пояснения — под «i»,
   на экране числа и их вид словами. */
const TezAutodialRows = ({ form, setForm, canEdit, numberInput }) => {
    const schedule = scheduleFromInputs(form.retry_schedule);
    return (
        <>
            <section className="space-y-1.5">
                <div className="flex items-center gap-1.5">
                    <span className={iosGroupLabel}>Автообзвон</span>
                    <IosHint
                        label="Как включается режим"
                        text="Режим включают каждому оператору лично — «Сотрудники» ниже: им сервер и раздаёт пачки. Режим отдела целиком у Тез не включается. Телефон подхватывает эти настройки при следующем обновлении списка."
                    />
                </div>
                <div className={`${iosCard} divide-y divide-slate-100`}>
                    <Row
                        label="Автообзвон"
                        info="В статусе «Активный» телефон звонит сам, подряд: после итога и паузы — следующему водителю своей пачки, а кому пора перезвонить — первым. В статусе «Исход» автообзвон стоит: оператор сам звонит водителям из своих списков. В перерыве, тренинге, тех. перерыве и офлайне звонков нет. Выключено — телефон сам не звонит."
                    >
                        <IosToggle checked={!!form.auto_dial} disabled={!canEdit} onChange={(v) => setForm((f) => ({ ...f, auto_dial: v }))} />
                    </Row>
                    <Row
                        label="Пауза между звонками, с"
                        hint={formatSeconds(form.call_interval_sec)}
                        info="Сколько секунд телефон ждёт после итога, прежде чем звонить следующему водителю. Слишком короткая пауза упирается в лимит Binotel на число звонков."
                    >
                        {numberInput('call_interval_sec', ...LIMITS.call_interval_sec)}
                    </Row>
                    <Row
                        label="Постобработка, с"
                        hint={wrapupText(form.wrapup_sec)}
                        info="Сколько секунд после разговора у оператора на итог. Не успел — телефон сам ставит статус «Офлайн» (это время не оплачивается); чтобы продолжить, оператор заполняет итог и сам выбирает «Активный». 0 — без ограничения."
                    >
                        {numberInput('wrapup_sec', 0, LIMITS.wrapup_sec[1])}
                    </Row>
                    <Row
                        label="Строк за раз"
                        info="Сколько водителей оператор получает одной пачкой. Пачки выдаются по очереди, как в удалённом КЦ."
                    >
                        {numberInput('portion_size', ...LIMITS.portion_size)}
                    </Row>
                </div>
            </section>

            <section className="space-y-1.5">
                <div className={iosGroupLabel}>Недозвон</div>
                <div className={`${iosCard} divide-y divide-slate-100`}>
                    <Row
                        label="Попыток на номер"
                        info="Сколько раз звонить водителю, который не берёт трубку. После последнего недозвона он в обзвон не возвращается."
                    >
                        {numberInput('max_attempts', ...LIMITS.max_attempts)}
                    </Row>
                    <Row
                        label="Интервалы недозвона, мин"
                        hint={formatSchedule(schedule) || 'не заданы'}
                        info="Пауза перед следующей попыткой после недозвона, в минутах: первое число — после первого недозвона, второе — после второго, последнее повторяется. Например 60, 180, 1440 — через 1 ч, 3 ч и 24 ч. Недозвон берёт любой оператор."
                        wrap
                    >
                        <RetryScheduleEditor
                            values={form.retry_schedule}
                            onChange={(list) => setForm((f) => ({ ...f, retry_schedule: list }))}
                            maxAttempts={Number(form.max_attempts) || 1}
                            canEdit={canEdit}
                        />
                    </Row>
                    {schedule.length === 0 && (
                        <Row
                            label="Повтор через, часов"
                            info="Пока интервалы не заданы, недозвон возвращается в пачки через столько часов."
                        >
                            {numberInput('retry_after_hours', ...LIMITS.retry_after_hours)}
                        </Row>
                    )}
                </div>
            </section>

            <section className="space-y-1.5">
                <div className={iosGroupLabel}>Перезвоны и дожим</div>
                <div className={`${iosCard} divide-y divide-slate-100`}>
                    <Row
                        label="Перезвон ждёт своего оператора, мин"
                        hint={formatMinutes(form.callback_own_minutes) || 'сразу любой'}
                        info="Перезвон и дожим сначала ждут оператора, который их назначил. Не позвонил за столько минут после срока — водителя может взять любой оператор. 0 — сразу любой."
                    >
                        {numberInput('callback_own_minutes', ...LIMITS.callback_own_minutes)}
                    </Row>
                    <Row
                        label="Дожать через, мин"
                        hint={formatMinutes(form.followup_after_min) || 'сразу'}
                        info="Водитель обещал выйти на линию, а завершённого заказа в TEZ APP так и нет — через столько минут после обещанного времени он вернётся к своему оператору, чтобы дожать."
                    >
                        {numberInput('followup_after_min', ...LIMITS.followup_after_min)}
                    </Row>
                </div>
            </section>
        </>
    );
};

/** Панель настроек отдела: режим, порция, повторы, обзваниваемый месяц, номер для линии оператора.
    profile='tez' — вместо режима отдела автообзвон, постобработка, расписание недозвонов,
    перезвоны и дожим (TezAutodialRows выше). */
export const DialListSettingsPanel = ({ apiBaseUrl, authHeaders, departmentId, settings, periods = [], onSaved, canEdit = true, showToast, profile = PROFILE_REMOTE_CC }) => {
    const isTez = normalizeProfile(profile) === PROFILE_TEZ;
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
            ...(isTez ? tezSettingsForm(settings) : {}),
        });
    }, [settings, isTez]);

    const save = async () => {
        if (!form) return;
        // Тез: пределы проверяем до запроса (tezAutodial.js — те же, что у сервера), а
        // режим отдела не отправляем вовсе — он у Тез остаётся выключенным.
        const tezPayload = isTez ? tezSettingsPayload(form) : null;
        if (tezPayload) {
            const problem = validateTezSettings(tezPayload);
            if (problem) { toast(problem, 'error'); return; }
        }
        setSaving(true);
        try {
            const payload = tezPayload || {
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
            toast(isTez ? 'Настройки автообзвона сохранены. Телефоны подхватят их при следующем обновлении списка' : 'Настройки обзвона сохранены', 'success');
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
            {isTez ? (
                <TezAutodialRows form={form} setForm={setForm} canEdit={canEdit} numberInput={numberInput} />
            ) : (
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
            )}

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
/* Плитки базы автообзвона Тез (контракт §5, leads/summary): цель — выход на линию,
   поэтому «Подписали» здесь нет. Цвет — только со смыслом: зелёный — цель,
   янтарный — перезвоны и дожим, по которым пора звонить. Сервер, который новых
   ключей ещё не присылает, даёт прочерк, а не ноль: ноль соврал бы. */
const tezLeadTiles = (summary) => {
    const status = summary?.by_status || {};
    const has = (key) => summary && Object.prototype.hasOwnProperty.call(summary, key);
    const due = has('callbacks_due') || has('followups_due')
        ? (Number(summary.callbacks_due) || 0) + (Number(summary.followups_due) || 0) : '—';
    return [
        { label: 'Всего', value: summary?.total ?? 0, tone: 'text-slate-900',
            info: 'Водители базы выбранного месяца.' },
        { label: 'Уже на линии при загрузке', value: has('online_at_upload') ? summary.online_at_upload : '—', tone: 'text-slate-900',
            info: 'Работали ещё до загрузки базы: первый заказ месяца раньше, чем водителя загрузили. Обзвон им не нужен, успешек по ним нет.' },
        { label: 'Вышли на линию', value: has('online') ? summary.online : (summary?.signed ?? 0), tone: 'text-emerald-600',
            info: 'Всего водителей базы с завершённым заказом в TEZ APP в месяце базы. Такой водитель уходит из обзвона.' },
        { label: 'В обзвоне', value: (Number(status.new) || 0) + (Number(status.in_progress) || 0), tone: 'text-slate-900',
            info: 'Ещё не вышли на линию и не закрыты: новые и те, кому уже звонили.' },
        { label: 'Перезвоны и дожим к звонку', value: due, tone: Number(due) > 0 ? 'text-amber-600' : 'text-slate-900',
            info: 'Срок наступил: перезвон, который назначил оператор, или «дожать» водителя, обещавшего выйти на линию.' },
        { label: 'Обработано', value: (Number(status.done) || 0) + (Number(status.excluded) || 0), tone: 'text-slate-900',
            info: 'Обзвон закончен: дозвонились без перезвона, попытки кончились или руководитель исключил.' },
    ];
};

/* Проверка поездок в TEZ APP (leads/summary → trip_check): идёт / когда была / что нашла. */
const TripCheckCard = ({ check }) => {
    if (!check) return null;
    let state;
    if (check.configured === false) state = 'TEZ APP не настроен на сервере — поездки не проверяются';
    else if (check.running) state = 'идёт проверка…';
    else if (check.last_run) state = `проверено ${whenText(check.last_run)}`;
    else state = 'ещё не проверяли';
    const stats = check.configured === false ? [] : [
        { label: 'Проверено', value: check.checked },
        { label: 'Вышли на линию', value: check.online },
        { label: 'Ошибок', value: check.errors },
    ].filter((s) => s.value !== undefined && s.value !== null);
    return (
        <section className="space-y-1.5">
            <div className="flex items-center gap-1.5">
                <span className={iosGroupLabel}>Поездки в TEZ APP</span>
                <IosHint text="Раз в 30 минут и сразу после загрузки базы сервер спрашивает TEZ APP по номеру, был ли у водителя завершённый заказ в месяце базы. Вышел на линию — водитель уходит из обзвона, а успешка засчитывается оператору, который последним поговорил с ним не меньше 10 секунд до первого заказа. «Нет аккаунта» от «не ездил» TEZ APP не отличает." />
            </div>
            <div className={`${iosCard} flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3`}>
                <div className={`flex min-w-0 items-center gap-2 text-[13px] ${check.configured === false ? 'text-amber-700' : 'text-slate-700'}`}>
                    {check.running && <FaIcon className="fas fa-spinner fa-spin" />}
                    <span className="min-w-0">{state}</span>
                </div>
                {stats.map((s) => (
                    <div key={s.label} className="text-[12.5px] text-slate-500">
                        {s.label} <span className={`font-semibold tabular-nums ${s.label === 'Ошибок' && Number(s.value) > 0 ? 'text-rose-600' : 'text-slate-900'}`}>{s.value}</span>
                    </div>
                ))}
            </div>
        </section>
    );
};

export const DialListLeadsPanel = ({ apiBaseUrl, authHeaders, departmentId, summary, period = '', onPeriodChange, onChanged, canEdit = true, showToast, profile = PROFILE_REMOTE_CC }) => {
    const isTez = normalizeProfile(profile) === PROFILE_TEZ;
    const [uploading, setUploading] = useState(false);
    const [dragOver, setDragOver] = useState(false);
    // Тез: что сказал сервер о последней загрузке (зеркало в «Базу лидов ОП TEZ»,
    // проверка поездок) — живёт до смены отдела, тост пропадает за секунды.
    const [lastUpload, setLastUpload] = useState(null);
    // Тез: до какого момента опрашивать сводку после загрузки — проверка поездок
    // идёт фоном, и плитки «вышли на линию» догоняют её сами, без кнопки «Обновить».
    const [pollUntil, setPollUntil] = useState(0);
    const fileRef = useRef(null);

    const toast = useCallback((msg, kind = 'success') => {
        if (typeof showToast === 'function') showToast(msg, kind);
    }, [showToast]);
    // onChanged — перечитать отдел (настройки + сводку); в зависимости опроса не кладём.
    const reloadSummary = useStableCallback(() => onChanged?.());

    useEffect(() => { setLastUpload(null); setPollUntil(0); }, [departmentId]);

    const tripRunning = !!summary?.trip_check?.running;
    useEffect(() => {
        if (!isTez || (!tripRunning && Date.now() > pollUntil)) return undefined;
        const timer = setTimeout(reloadSummary, 5000);
        return () => clearTimeout(timer);
    }, [isTez, tripRunning, pollUntil, summary, reloadSummary]);

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
            // Не загрузилось ничего из-за ИИН — это ошибка файла, а не успех.
            const failedAll = !data.rows_new && !data.rows_duplicate && (data.rows_bad_iin || data.rows_invalid);
            if (isTez) {
                toast(tezUploadSummary(data, monthLabel(data.period || shownPeriod)), failedAll ? 'error' : 'success');
                // Сбой зеркала загрузку не валит (контракт §5) — но руководитель должен
                // знать, что во второй базе этого файла нет: отдельным тостом-ошибкой.
                const mirror = mirrorSummary(data.mirror);
                if (mirror && !mirror.ok) toast(mirror.text, 'error');
                setLastUpload({ fileName: file.name, at: new Date().toISOString(), data, mirror });
                setPollUntil(Date.now() + 90 * 1000);
            } else {
                toast(uploadSummary(data, shownPeriod), failedAll ? 'error' : 'success');
            }
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

    // Подписавшие документы — своей плиткой: их больше не обзванивают, и в «Не
    // звонили» / «В работе» они бы врали (сервер их туда и не считает).
    const tiles = isTez ? tezLeadTiles(summary) : [
        { label: 'Всего', value: summary?.total ?? 0, tone: 'text-slate-900' },
        { label: 'Не звонили', value: summary?.by_status?.new ?? 0, tone: 'text-slate-900' },
        { label: 'В работе', value: summary?.by_status?.in_progress ?? 0, tone: 'text-amber-600' },
        { label: 'Закрыто', value: summary?.by_status?.done ?? 0, tone: 'text-slate-900' },
        { label: 'Подписали', value: summary?.signed ?? 0, tone: 'text-emerald-600' },
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
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
                {tiles.map((t) => (
                    <div key={t.label} className={`${iosCard} px-3 py-3`}>
                        <div className={`text-[20px] font-semibold tabular-nums ${t.tone}`}>{t.value}</div>
                        {t.info ? (
                            <div className="flex items-start gap-1 text-[11.5px] text-slate-500">
                                <span className="min-w-0">{t.label}</span>
                                <IosHint text={t.info} label={`Что значит «${t.label}»`} align="right" />
                            </div>
                        ) : (
                            <div className="text-[11.5px] text-slate-500">{t.label}</div>
                        )}
                    </div>
                ))}
            </div>

            {isTez && <TripCheckCard check={summary?.trip_check} />}

            {isTez && lastUpload && (
                <section className="space-y-1.5">
                    <div className={iosGroupLabel}>Последняя загрузка</div>
                    <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                        <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
                            <div className="min-w-0">
                                <div className="truncate text-[13.5px] font-medium text-slate-800">{lastUpload.fileName}</div>
                                <div className="text-[11.5px] text-slate-500">{tezUploadSummary(lastUpload.data, monthLabel(lastUpload.data.period || shownPeriod))}</div>
                            </div>
                            {lastUpload.data.trip_check === 'queued' && <IosBadge tone="blue">проверка поездок поставлена</IosBadge>}
                        </div>
                        {lastUpload.mirror && (
                            <div className={`px-4 py-3 text-[12.5px] ${lastUpload.mirror.ok ? 'text-slate-600' : 'text-rose-600'}`}>
                                {lastUpload.mirror.text}
                            </div>
                        )}
                    </div>
                </section>
            )}

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
                    {isTez ? (
                        <div className="mt-1 text-[12.5px] text-slate-500">
                            Перетащите сюда CSV или Excel с колонками <b>fio</b> и <b>phone</b>, или выберите файл.{' '}
                            <span className="inline-block align-middle">
                                <IosHint
                                    align="right"
                                    text="ИИН не нужен: колонку iin, если она есть, сервер пропустит. Тот же файл сам попадает в «Базу лидов ОП TEZ» за этот месяц — успешки и зарплата ОП считаются там. Сразу после загрузки сервер проверяет поездки в TEZ APP: кто уже вышел на линию, из обзвона уходит. Внутри месяца повторные номера не дублируются; в базе другого месяца тот же водитель допускается."
                                />
                            </span>
                        </div>
                    ) : (
                        <div className="mt-1 text-[12.5px] text-slate-500">
                            Перетащите сюда CSV или Excel с колонками <b>fio</b>, <b>phone</b> и <b>iin</b>, или выберите файл.{' '}
                            <span className="inline-block align-middle">
                                <IosHint
                                align="right"
                                    text="ИИН обязателен: по нему раз в 3 часа проверяется, подписал ли водитель документы. Строки без ИИН или с ошибкой в нём не загружаются — после загрузки покажем их номера. В Excel задайте колонке iin текстовый формат: иначе длинный ИИН может превратиться в число вида 5,03E+11. Внутри месяца повторные номера и ИИН не дублируются; в базе другого месяца тот же водитель допускается."
                                />
                            </span>
                        </div>
                    )}
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
                                {b.rows_bad_iin > 0 && <IosBadge tone="red">без ИИН {b.rows_bad_iin}</IosBadge>}
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
/* Автообзвон Тез (profile='tez'): вместо «как у отдела / вкл / выкл» — переключатель
   вкл/выкл. Режим отдела у Тез всегда выключен, и «как у отдела» значило бы просто
   «выключен»; включённые лично и есть «операторы под распределение» (контракт §0.2).
   Выключение снимает личную настройку (null), а не пишет false: перейди человек
   потом в отдел, где режим включён целиком, он получит его «как у отдела». Рядом с
   линией — ставка (users.rate), если сервер её отдаёт. */
export const DialListOperatorsPanel = ({ apiBaseUrl, authHeaders, departmentId, departmentEnabled, canEdit = true, showToast, profile = PROFILE_REMOTE_CC }) => {
    const isTez = normalizeProfile(profile) === PROFILE_TEZ;
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

    const effectiveOf = (u) => (u.dial_list_enabled == null ? departmentEnabled : u.dial_list_enabled);
    const enabledCount = (users || []).filter(effectiveOf).length;

    return (
        <section className="space-y-1.5">
            <div className="flex items-center justify-between gap-3 px-1">
                {isTez ? (
                    <div className="flex items-center gap-1.5">
                        <span className={iosGroupLabel}>Сотрудники</span>
                        <IosHint text="Включённые здесь операторы и есть те, кому сервер раздаёт пачки водителей — по очереди, как в удалённом КЦ. У включённого телефон переходит в режим обзвона после перезапуска: в статусе «Активный» звонит сам, в «Исходе» оператор звонит сам из своих списков." />
                    </div>
                ) : (
                    <div className={iosGroupLabel}>Сотрудники</div>
                )}
                <span className="text-[11.5px] text-slate-500">
                    {isTez
                        ? (users ? `В режиме: ${enabledCount} из ${users.length}` : '')
                        : `«Как у отдела» сейчас значит ${departmentEnabled ? 'включён' : 'выключен'}`}
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
                    const effective = effectiveOf(u);
                    const rate = isTez ? rateLabel(u.rate) : '';
                    return (
                        <div key={u.id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
                            <div className="min-w-0">
                                <div className="flex items-center gap-2">
                                    <span className="truncate text-[13.5px] font-medium text-slate-800">{u.name || `#${u.id}`}</span>
                                    {u.login && <span className="text-[11.5px] text-slate-400">@{u.login}</span>}
                                    {/* Сотрудник другого отдела, посаженный на линию: чей он. */}
                                    {u.guest && u.department_name && (
                                        <span className="truncate text-[11.5px] text-slate-400">· {u.department_name}</span>
                                    )}
                                    <IosBadge tone={effective ? 'green' : 'slate'}>{effective ? 'обзвон' : 'обычный телефон'}</IosBadge>
                                </div>
                                <div className="text-[11.5px] text-slate-500">
                                    {u.sip_number ? `линия ${u.sip_number}` : 'линия не назначена — см. вкладку «Линии»'}
                                    {rate ? ` · ${rate}` : ''}
                                </div>
                            </div>
                            {isTez ? (
                                <IosToggle
                                    checked={!!effective}
                                    disabled={!canEdit || busyId === u.id}
                                    // Выключить: личную настройку снять (null), а если отдел вдруг
                                    // включён целиком — записать «выключен» явно.
                                    onChange={(on) => change(u, on ? 'on' : (departmentEnabled ? 'off' : 'inherit'))}
                                />
                            ) : (
                                <div className={canEdit && busyId !== u.id ? '' : 'pointer-events-none opacity-60'}>
                                    <IosSegmented
                                        value={modeFrom(u.dial_list_enabled)}
                                        options={OPERATOR_MODES}
                                        onChange={(mode) => change(u, mode)}
                                        ariaLabel={`Обзвон: ${u.name}`}
                                    />
                                </div>
                            )}
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
