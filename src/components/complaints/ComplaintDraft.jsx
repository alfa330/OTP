import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Building2, Car, CarTaxiFront, Headset, Loader2, Search, Smartphone } from 'lucide-react';
import { iosBtnSecondary, iosGroupLabel, iosInput } from '../ui/ios';
import InfoHint from '../common/InfoHint';
import CustomSelect from '../ui/CustomSelect';
import IosDatePicker from '../ui/DatePicker';
import { IosTimePicker } from '../ui/TimePicker';
import { KAZAKHSTAN_CITY_OPTIONS } from '../../utils/kazakhstanCities';
import { DATE_TRIGGER, TIME_INPUT } from './styles';
// Разбор ссылки на аккаунт и вид телефона — те же, что у «Реестра посылок»:
// оператор вставляет ту же ссылку из Флита, что и там.
import { extractAccountId, fmtPhone } from '../parcels/parcelMeta';
import {
    DESCRIPTION_LIMIT, employeeDepartmentId, formPayload, formProblems, officeOptions,
    targetByCode, unitDepartments, willProcess,
} from './complaintRules';

/* Черновик жалобы (ТЗ задачи #297): состояние, проверка и отправка — отдельно,
 * поля — отдельно.
 *
 * Заводят жалобу в «Обращениях», в том же мастере, что и остальные обращения:
 * оператор выбирает направление «Жалоба → на кого» среди тематик (решение
 * владельца 29.09.2026). Поэтому здесь нет своего окна — мастер рисует поля в
 * своём и свои кнопки внизу, а состояние берёт у useComplaintDraft.
 *
 * Поля появляются по цели, а не выкладываются все сразу: подразделение и
 * сотрудник — только у КЦ, офис — у фронт-офиса, парк — у парка. Пояснения — под
 * «i». Всё на одной странице, без «Далее» на каждом шаге: во время разговора с
 * водителем лишние нажатия стоят секунд, а полей всего семь-десять. */

export const TARGET_ICONS = {
    call_center: Headset,
    car_rental: Car,
    front_office: Building2,
    taxi_park: CarTaxiFront,
    yandex: Smartphone,
};

/* driver_link — только помощник заполнения: в тело жалобы он не входит
 * (formPayload его не берёт), и в группу ссылка не уходит (владелец, 29.09.2026). */
const EMPTY = {
    reason: '', unit_id: '', employee_id: '', driver_link: '', driver_name: '', driver_phone: '',
    driver_ref: '', city: '', description: '', event_day: '', event_time: '',
};

const todayIso = () => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
};

const errorText = (error, fallback) => error?.response?.data?.error || error?.message || fallback;

/* Состояние черновика. targetCode задаёт мастер: цель выбирают на его первом
 * экране, среди тематик. Смена цели обнуляет то, что ей принадлежит (причину,
 * подразделение, сотрудника); данные водителя и описание остаются — водитель
 * тот же, и оператор мог просто промахнуться строкой. */
export function useComplaintDraft({ open, meta, targetCode, apiBaseUrl, headers, showToast }) {
    const [form, setForm] = useState(EMPTY);
    const [touched, setTouched] = useState(false);
    const [busy, setBusy] = useState(false);
    const [serverProblems, setServerProblems] = useState({});
    // Сотрудники по отделу — кэш на время открытого окна: переключая
    // подразделение туда-обратно, оператор не должен ждать тот же список дважды.
    const [employees, setEmployees] = useState({});
    // Поиск водителя по ссылке: идёт ли он и что не так. Номер запроса — чтобы
    // ответ на прежнюю ссылку не перезаписал данные новой.
    const [lookup, setLookup] = useState({ loading: false, error: '' });
    const lookupSeq = useRef(0);

    const target = targetByCode(meta, targetCode);
    const problems = useMemo(() => formProblems(target, form), [target, form]);
    const departmentId = employeeDepartmentId(target, form, meta);
    const groupReady = Boolean(meta?.group?.ready);
    const processing = willProcess(target);
    const accountId = useMemo(() => extractAccountId(form.driver_link), [form.driver_link]);
    /* Варианты выпадающих списков — стабильными ссылками. CustomSelect
       пересчитывает подсветку на каждую смену массива вариантов, и новый
       массив на каждую букву в соседнем поле давал лишний setState на каждое
       нажатие (при тяжёлой странице за окном React ругался «Maximum update
       depth exceeded»). */
    const employeeOptions = useMemo(() => [{ value: '', label: 'Не удалось определить' }].concat(
        (employees[departmentId] || []).map((person) => ({
            value: String(person.id),
            label: person.name,
            groupLabel: person.group_name || 'Без группы',
        })),
    ), [employees, departmentId]);
    const officeChoices = useMemo(() => [{ value: '', label: 'Не знаю' }]
        .concat(officeOptions(meta, form.city)), [meta, form.city]);
    const parkChoices = useMemo(() => [{ value: '', label: 'Не указан' }]
        .concat((meta?.parks || []).map((park) => ({ value: String(park.id), label: park.name }))),
    [meta]);

    useEffect(() => {
        if (open) return;
        setForm(EMPTY);
        setTouched(false);
        setServerProblems({});
        setLookup({ loading: false, error: '' });
        lookupSeq.current += 1;
    }, [open]);

    useEffect(() => {
        setForm((prev) => ({ ...prev, reason: '', unit_id: '', employee_id: '' }));
        setTouched(false);
        setServerProblems({});
    }, [targetCode]);

    /* ФИО, телефон и ВУ из CRM — как в «Реестре посылок». Из CRM берём то,
       что она знает; чего не знает — оставляем, как вписал оператор. */
    const lookupDriver = useCallback(async (link) => {
        if (!extractAccountId(link)) {
            setLookup({ loading: false, error: 'Вставьте ссылку на аккаунт водителя или его ID' });
            return;
        }
        const seq = ++lookupSeq.current;
        setLookup({ loading: true, error: '' });
        try {
            const response = await axios.post(`${apiBaseUrl}/api/complaints/driver-lookup`,
                { link }, { headers: headers() });
            if (seq !== lookupSeq.current) return;
            const found = response.data?.driver || {};
            setForm((prev) => ({
                ...prev,
                driver_name: found.name || prev.driver_name,
                driver_phone: fmtPhone(found.phone) || prev.driver_phone,
                driver_ref: found.license || prev.driver_ref,
            }));
            setServerProblems((prev) => ({ ...prev, driver_name: undefined, driver_phone: undefined }));
            setLookup({ loading: false, error: '' });
        } catch (error) {
            if (seq !== lookupSeq.current) return;
            setLookup({ loading: false,
                        error: errorText(error, 'Не удалось получить данные водителя') });
        }
    }, [apiBaseUrl, headers]);

    // Ищем сами, как только ссылка разобралась: её вставляют целиком, и
    // отдельное нажатие «Найти» было бы лишним шагом посреди разговора.
    useEffect(() => {
        if (!open || !accountId) return undefined;
        const timer = setTimeout(() => lookupDriver(form.driver_link), 250);
        return () => clearTimeout(timer);
        // form.driver_link — вне зависимостей: таймер держится за разобранный
        // id, а не за каждую букву вокруг него.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, accountId, lookupDriver]);

    useEffect(() => {
        if (!open || !departmentId || employees[departmentId]) return undefined;
        let cancelled = false;
        axios.get(`${apiBaseUrl}/api/complaints/employees?department_id=${departmentId}`,
            { headers: headers() })
            .then((response) => {
                if (!cancelled) setEmployees((prev) => ({ ...prev, [departmentId]: response.data.items || [] }));
            })
            .catch(() => {
                if (!cancelled) setEmployees((prev) => ({ ...prev, [departmentId]: [] }));
            });
        return () => { cancelled = true; };
    }, [open, departmentId, apiBaseUrl, headers, employees]);

    const set = useCallback((key, value) => {
        setForm((prev) => {
            const next = { ...prev, [key]: value };
            // Сменили подразделение — прежний сотрудник из другого отдела больше
            // не подходит, и молча отправить его значило бы получить отказ сервера.
            if (key === 'unit_id' && target?.unit === 'department') next.employee_id = '';
            return next;
        });
        setServerProblems((prev) => ({ ...prev, [key]: undefined }));
    }, [target]);

    const shown = (key) => (touched ? (serverProblems[key] || problems[key]) : serverProblems[key]);

    /* Отправка. Возвращает созданную жалобу или null — окно закрывает мастер. */
    const submit = async () => {
        setTouched(true);
        if (!target || Object.keys(problems).length) return null;
        setBusy(true);
        try {
            // День и время — двумя пикерами (как по всему сайту), а сервер ждёт
            // одно значение: время без дня смысла не имеет и не отправляется.
            const eventAt = form.event_day
                ? `${form.event_day}${form.event_time ? `T${form.event_time}` : ''}` : '';
            const response = await axios.post(`${apiBaseUrl}/api/complaints/complaints`,
                formPayload(target, { ...form, event_at: eventAt }), { headers: headers() });
            const { item, delivered, delivery_error: deliveryError } = response.data;
            if (item?.requires_processing && !delivered) {
                showToast?.(`Жалоба сохранена, но в группу не ушла: ${deliveryError || 'ошибка Telegram'}`, 'error');
            } else {
                showToast?.(item?.requires_processing ? 'Жалоба отправлена в группу'
                    : 'Жалоба зафиксирована', 'success');
            }
            return item || null;
        } catch (error) {
            setServerProblems(error?.response?.data?.fields || {});
            showToast?.(errorText(error, 'Не удалось сохранить жалобу'), 'error');
            return null;
        } finally {
            setBusy(false);
        }
    };

    return {
        target, form, set, shown, serverProblems, submit, busy, processing,
        // Отправить некуда: группа не выбрана. «Только зафиксировать» при этом
        // работает — в группу такая жалоба и не уходит.
        blocked: processing && !groupReady,
        employeeOptions,
        departmentId,
        lookup, lookupDriver, accountId, officeChoices, parkChoices,
    };
}

const Label = ({ children, hint, optional }) => (
    <div className="mb-1.5 flex items-center gap-1.5">
        <span className="text-[13px] font-medium leading-snug text-slate-800">{children}</span>
        {hint && <InfoHint side="left">{hint}</InfoHint>}
        {optional && <span className="text-[11px] text-slate-400">необязательно</span>}
    </div>
);

const Problem = ({ text }) => (text
    ? <div className="mt-1 text-[11.5px] text-rose-600">{text}</div> : null);

/* Поля черновика. Кнопки внизу рисует тот, кто показывает поля (мастер). */
export function ComplaintFields({ draft, meta }) {
    const {
        target, form, set, shown, serverProblems, blocked, employeeOptions, departmentId,
        lookup, lookupDriver, accountId, officeChoices, parkChoices,
    } = draft;
    if (!target) return null;

    return (
        <div className="space-y-4">
            {target.unit === 'department' && (
                <div>
                    <Label>Подразделение колл-центра</Label>
                    <div className="flex flex-wrap gap-1.5">
                        {unitDepartments(meta).map((item) => (
                            <button key={item.id} type="button"
                                    onClick={() => set('unit_id', String(item.id))}
                                    className={`rounded-xl px-3 py-1.5 text-[12.5px] font-medium transition-all active:scale-[0.98] ${
                                        String(form.unit_id) === String(item.id)
                                            ? 'bg-blue-600 text-white shadow-sm'
                                            : 'bg-slate-100 text-slate-700 hover:bg-slate-200'
                                    }`}>
                                {item.label}
                            </button>
                        ))}
                    </div>
                    <Problem text={shown('unit_id')} />
                </div>
            )}

            {target.employee && (
                <div>
                    <Label optional
                           hint="По данным звонка или чата либо со слов водителя. Не удалось определить — оставьте пустым: жалоба всё равно уйдёт, а сотрудника проставит супервайзер после проверки.">
                        Сотрудник, на которого жалоба
                    </Label>
                    <CustomSelect variant="ios" searchable
                                  value={form.employee_id || ''}
                                  disabled={!departmentId}
                                  onChange={(value) => set('employee_id', value)}
                                  options={employeeOptions}
                                  placeholder={departmentId ? 'Не удалось определить'
                                      : 'Сначала выберите подразделение'}
                                  searchPlaceholder="Поиск по ФИО"
                                  ariaLabel="Сотрудник, на которого жалоба" />
                    <Problem text={serverProblems.employee_id} />
                </div>
            )}

            {target.unit === 'office' && (
                <div>
                    <Label optional>Офис</Label>
                    <CustomSelect variant="ios" searchable value={form.unit_id || ''}
                                  onChange={(value) => set('unit_id', value)}
                                  options={officeChoices}
                                  placeholder="Не знаю" searchPlaceholder="Поиск офиса"
                                  ariaLabel="Офис" />
                </div>
            )}

            {target.unit === 'park' && (
                <div>
                    <Label optional>Таксопарк</Label>
                    <CustomSelect variant="ios" searchable value={form.unit_id || ''}
                                  onChange={(value) => set('unit_id', value)}
                                  options={parkChoices}
                                  placeholder="Не указан" ariaLabel="Таксопарк" />
                </div>
            )}

            <div>
                <Label>Причина жалобы</Label>
                <div className="flex flex-wrap gap-1.5">
                    {(target.reasons || []).map((item) => (
                        <button key={item.code} type="button" onClick={() => set('reason', item.code)}
                                className={`rounded-xl px-3 py-1.5 text-left text-[12.5px] font-medium transition-all active:scale-[0.98] ${
                                    form.reason === item.code
                                        ? 'bg-blue-600 text-white shadow-sm'
                                        : 'bg-slate-100 text-slate-700 hover:bg-slate-200'
                                }`}>
                            {item.title}
                        </button>
                    ))}
                </div>
                <Problem text={shown('reason')} />
            </div>

            <div className={iosGroupLabel}>Водитель</div>
            {/* Ссылка на аккаунт — как в «Реестре посылок»: ФИО, телефон и ВУ
                подтягиваются из CRM сами. Необязательна (водитель мог не дать
                ни ссылки, ни ID), и в группу не уходит. */}
            <div>
                <Label optional
                       hint="Вставьте адрес карточки водителя во Флите или его ID — ФИО, телефон и ВУ подтянутся сами. В группу ссылка не уходит.">
                    Ссылка на аккаунт или ID
                </Label>
                <div className="flex gap-2">
                    <input value={form.driver_link} onChange={(e) => set('driver_link', e.target.value)}
                           placeholder="https://fleet.yandex.kz/contractors/…" className={iosInput} />
                    <button type="button" onClick={() => lookupDriver(form.driver_link)}
                            disabled={!accountId || lookup.loading} aria-label="Найти водителя"
                            className={`${iosBtnSecondary} shrink-0`}>
                        {lookup.loading ? <Loader2 size={15} className="animate-spin" /> : <Search size={15} />}
                    </button>
                </div>
                <Problem text={lookup.error} />
            </div>
            {/* Поля — от верха ячейки: подписи в строке одной высоты, а строка
                ошибки под одним полем не должна сдвигать соседнее. */}
            <div className="grid grid-cols-1 items-start gap-4 sm:grid-cols-2">
                <div>
                    <Label>ФИО водителя</Label>
                    <input value={form.driver_name} onChange={(e) => set('driver_name', e.target.value)}
                           placeholder="Как в диспетчерской" className={iosInput} />
                    <Problem text={shown('driver_name')} />
                </div>
                <div>
                    <Label>Номер телефона</Label>
                    <input value={form.driver_phone} inputMode="tel"
                           onChange={(e) => set('driver_phone', e.target.value)}
                           placeholder="+7 701 123 45 67" className={`${iosInput} tabular-nums`} />
                    <Problem text={shown('driver_phone')} />
                </div>
                <div>
                    <Label optional>ID / ВУ водителя</Label>
                    <input value={form.driver_ref} onChange={(e) => set('driver_ref', e.target.value)}
                           placeholder="Если есть" className={iosInput} />
                </div>
                <div>
                    <Label>Город</Label>
                    <CustomSelect variant="ios" searchable value={form.city || ''}
                                  onChange={(value) => set('city', value)}
                                  options={KAZAKHSTAN_CITY_OPTIONS}
                                  placeholder="Выберите город" searchPlaceholder="Поиск города"
                                  ariaLabel="Город" />
                    <Problem text={shown('city')} />
                </div>
            </div>

            <div>
                <Label optional hint="Если это нужно для проверки: по времени находят звонок, чат или смену сотрудника.">
                    Когда произошло
                </Label>
                <div className="grid max-w-[340px] grid-cols-[1fr_112px] gap-2">
                    <IosDatePicker value={form.event_day || ''} max={todayIso()} allowEmpty
                                   placeholder="Дата" className="w-full" triggerClassName={DATE_TRIGGER}
                                   onChange={(value) => set('event_day', value || '')}
                                   ariaLabel="Дата события" />
                    <IosTimePicker value={form.event_time || ''} disabled={!form.event_day}
                                   onChange={(value) => set('event_time', value || '')}
                                   step={5} className="w-full" inputClassName={TIME_INPUT}
                                   placeholder="Время" ariaLabel="Время события" />
                </div>
                <Problem text={serverProblems.event_at} />
            </div>

            <div>
                <Label>Что произошло</Label>
                <textarea value={form.description} rows={4} maxLength={DESCRIPTION_LIMIT}
                          onChange={(e) => set('description', e.target.value)}
                          placeholder="Кратко: что случилось, что говорит водитель, чего он ждёт"
                          className={`${iosInput} resize-y`} />
                <Problem text={shown('description')} />
            </div>

            {target.processing === 'never' && (
                <div className="px-1 text-[12px] leading-snug text-slate-500">
                    Жалобы на Яндекс в группу не отправляются: сохраним для аналитики.
                </div>
            )}

            {blocked && (
                <div className="rounded-xl bg-amber-50 px-3.5 py-2.5 text-[12.5px] leading-snug text-amber-800 ring-1 ring-amber-100">
                    Telegram-группа для жалоб ещё не выбрана — отправить некуда. Её выбирает
                    руководство СЗоВ в настройках раздела «Жалобы».
                </div>
            )}
        </div>
    );
}
