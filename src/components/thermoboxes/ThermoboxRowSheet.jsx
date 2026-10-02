import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { Loader2, Minus, Plus } from 'lucide-react';
import {
    IosModal, IosSegmented, IosToggle, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel, iosInput,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import {
    COUNT_FIELDS, CONDITION_FIELDS, FIELD_LABELS, SPECIAL_CONDITION_MAX, TARIFF_SHORT,
    changedFields, depositLabel, displayValue, draftErrors, draftOf, eventLabel, fmtStamp,
    ordersLabel, parseWhole, periodLabel, plural, tariffLabel,
} from './thermoboxMeta';

/*
 * Карточка офиса: остатки и условия выдачи, история правок, видимость в таблице.
 *
 * Форма — список в духе «Настроек» iOS: подпись слева, значение справа. Кто
 * только смотрит (КЦ), видит те же строки без полей — один и тот же порядок
 * глазами и руками, без второй вёрстки.
 */

const errorOf = (requestError, fallback) => requestError?.response?.data?.error || fallback;

const ROW = 'flex min-h-[48px] items-center justify-between gap-3 px-3.5 py-2';
const ROW_LABEL = 'text-[14px] text-slate-800';

/* Степпер как UIStepper: «−» и «+» по краям, число посередине можно набрать. */
const Stepper = ({ value, onChange, invalid, label }) => {
    const number = parseWhole(value);
    const step = (delta) => {
        const base = Number.isFinite(number) ? number : 0;
        onChange(String(Math.max(0, base + delta)));
    };
    const button = 'grid h-8 w-9 place-items-center text-slate-600 transition hover:bg-slate-200/70 active:scale-95 disabled:opacity-30';
    return (
        <div className={`flex items-center overflow-hidden rounded-[10px] bg-slate-100 ${invalid ? 'ring-1 ring-rose-300' : ''}`}>
            <button type="button" className={button} onClick={() => step(-1)}
                    disabled={!Number.isFinite(number) || number <= 0} aria-label={`${label}: меньше`}>
                <Minus size={15} strokeWidth={2.4} />
            </button>
            <input
                className={`h-8 w-14 border-0 bg-transparent text-center text-[15px] font-semibold tabular-nums focus:outline-none focus:ring-0 ${invalid ? 'text-rose-600' : 'text-slate-900'}`}
                inputMode="numeric"
                value={value}
                onChange={(event) => onChange(event.target.value)}
                onFocus={(event) => event.target.select()}
                aria-label={label}
            />
            <button type="button" className={button} onClick={() => step(1)} aria-label={`${label}: больше`}>
                <Plus size={15} strokeWidth={2.4} />
            </button>
        </div>
    );
};

const NumberField = ({ value, onChange, invalid, suffix, label, width = 'w-20' }) => (
    <label className="flex items-center gap-2">
        <input
            className={`${width} rounded-[10px] border-0 bg-slate-100 px-2.5 py-1.5 text-right text-[14px] tabular-nums text-slate-900 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/70 ${invalid ? 'ring-1 ring-rose-300 text-rose-600' : ''}`}
            inputMode="numeric"
            value={value}
            onChange={(event) => onChange(event.target.value)}
            onFocus={(event) => event.target.select()}
            aria-label={label}
        />
        {suffix && <span className="text-[13px] text-slate-500">{suffix}</span>}
    </label>
);

/* Форма строки. draft — значения полей (числа строками), editable — рисовать
   поля или только значения. Ошибки показываются под группой одной строкой:
   красная рамка уже говорит, где, а текст — что не так. */
export const RowForm = ({ draft, row, editable, onChange }) => {
    const errors = useMemo(() => (editable ? draftErrors(draft) : {}), [draft, editable]);
    const set = (field) => (value) => onChange({ ...draft, [field]: value });
    const deposit = parseWhole(draft.deposit_tenge);
    const depositOn = editable ? draft.deposit_tenge !== '0' : Boolean(row?.deposit_tenge);
    const countErrors = COUNT_FIELDS.map((field) => errors[field] && `${FIELD_LABELS[field]}: ${errors[field].toLowerCase()}`).filter(Boolean);
    const conditionErrors = CONDITION_FIELDS.map((field) => errors[field] && `${FIELD_LABELS[field]}: ${errors[field].toLowerCase()}`).filter(Boolean);

    return (
        <div className="space-y-4">
            <section className="space-y-1.5">
                <div className={iosGroupLabel}>В наличии</div>
                <div className={`${iosCard} divide-y divide-slate-100`}>
                    {COUNT_FIELDS.map((field) => (
                        <div key={field} className={ROW}>
                            <span className={ROW_LABEL}>{FIELD_LABELS[field]}</span>
                            {editable ? (
                                <Stepper value={draft[field]} onChange={set(field)} invalid={Boolean(errors[field])}
                                         label={FIELD_LABELS[field]} />
                            ) : (
                                <span className={`text-[17px] font-semibold tabular-nums ${row?.[field] ? 'text-slate-900' : 'text-slate-300'}`}>
                                    {row?.[field] ?? 0}
                                </span>
                            )}
                        </div>
                    ))}
                </div>
                {countErrors.length > 0 && <p className="px-1 text-[12px] text-rose-600">{countErrors[0]}</p>}
            </section>

            <section className="space-y-1.5">
                <div className={iosGroupLabel}>Условия выдачи</div>
                <div className={`${iosCard} divide-y divide-slate-100`}>
                    <div className={ROW}>
                        <span className={ROW_LABEL}>Тариф</span>
                        {editable ? (
                            <IosSegmented
                                size="xs"
                                value={draft.tariff}
                                onChange={set('tariff')}
                                ariaLabel="Тариф"
                                options={Object.entries(TARIFF_SHORT).map(([value, label]) => ({ value, label }))}
                            />
                        ) : (
                            <span className="text-right text-[14px] text-slate-600">{tariffLabel(row?.tariff)}</span>
                        )}
                    </div>
                    <div className={ROW}>
                        <span className={ROW_LABEL}>Заказы</span>
                        {editable ? (
                            <NumberField value={draft.min_orders} onChange={set('min_orders')} invalid={Boolean(errors.min_orders)}
                                         suffix="+ заказов" label="Заказы" width="w-16" />
                        ) : (
                            <span className="text-[14px] text-slate-600">{ordersLabel(row?.min_orders || 0)}</span>
                        )}
                    </div>
                    <div className={ROW}>
                        <span className={ROW_LABEL}>Срок</span>
                        {editable ? (
                            <NumberField value={draft.period_days} onChange={set('period_days')} invalid={Boolean(errors.period_days)}
                                         suffix={plural(parseWhole(draft.period_days) || 0, 'день', 'дня', 'дней')} label="Срок" width="w-16" />
                        ) : (
                            <span className="text-[14px] text-slate-600">{periodLabel(row?.period_days || 7)}</span>
                        )}
                    </div>
                    <div className={ROW}>
                        <span className={ROW_LABEL}>Депозит</span>
                        {editable ? (
                            <div className="flex items-center gap-3">
                                {depositOn && (
                                    <NumberField value={draft.deposit_tenge} onChange={set('deposit_tenge')}
                                                 invalid={Boolean(errors.deposit_tenge)} suffix="₸" label="Сумма депозита" width="w-24" />
                                )}
                                <IosToggle
                                    checked={depositOn}
                                    onChange={(next) => set('deposit_tenge')(next ? String(Number.isFinite(deposit) && deposit > 0 ? deposit : 5000) : '0')}
                                />
                            </div>
                        ) : (
                            <span className="text-[14px] text-slate-600">{depositLabel(row?.deposit_tenge || 0)}</span>
                        )}
                    </div>
                    <div className={editable ? 'space-y-1.5 px-3.5 py-2.5' : ROW}>
                        <span className={ROW_LABEL}>Отдельное условие</span>
                        {editable ? (
                            <input
                                className={iosInput}
                                value={draft.special_condition}
                                maxLength={SPECIAL_CONDITION_MAX}
                                onChange={(event) => set('special_condition')(event.target.value)}
                                placeholder="Например: НЕ новички"
                            />
                        ) : (
                            <span className="text-right text-[14px] text-slate-600">{row?.special_condition || '—'}</span>
                        )}
                    </div>
                </div>
                {conditionErrors.length > 0 && <p className="px-1 text-[12px] text-rose-600">{conditionErrors[0]}</p>}
            </section>
        </div>
    );
};

const History = ({ items, loading }) => {
    if (loading) {
        return (
            <div className="flex items-center justify-center gap-2 py-10 text-[13px] text-slate-500">
                <Loader2 size={15} className="animate-spin" /> Загружаем историю…
            </div>
        );
    }
    if (!items.length) {
        return <p className="py-10 text-center text-[13.5px] text-slate-500">Изменений пока не было</p>;
    }
    return (
        <ol className={`${iosCard} divide-y divide-slate-100`}>
            {items.map((item) => (
                <li key={item.id} className="px-3.5 py-3">
                    <div className="flex items-baseline justify-between gap-3 text-[12px] text-slate-500">
                        <span className="truncate font-medium text-slate-700">{item.actor_name || 'Сотрудник'}</span>
                        <span className="shrink-0 tabular-nums">{fmtStamp(item.created_at)}</span>
                    </div>
                    {item.kind === 'edited' && item.changes?.length ? (
                        <ul className="mt-1 space-y-0.5 text-[13.5px]">
                            {item.changes.map((change) => (
                                <li key={change.field} className="flex flex-wrap items-baseline gap-x-1.5">
                                    <span className="text-slate-500">{FIELD_LABELS[change.field] || change.field}:</span>
                                    <span className="text-slate-400 line-through decoration-slate-300">{displayValue(change.field, change.from)}</span>
                                    <span className="text-slate-400">→</span>
                                    <span className="font-medium text-slate-900">{displayValue(change.field, change.to)}</span>
                                </li>
                            ))}
                        </ul>
                    ) : (
                        <div className="mt-1 text-[13.5px] text-slate-800">{eventLabel(item.kind)}</div>
                    )}
                </li>
            ))}
        </ol>
    );
};

/* ── Карточка офиса ─────────────────────────────────────────────────────── */

export const ThermoboxRowSheet = ({ open, row, onClose, capabilities, apiBaseUrl, headers, onSaved, onConflict, showToast }) => {
    const canEdit = Boolean(capabilities?.can_edit) && Boolean(row?.is_active);
    const canManage = Boolean(capabilities?.can_manage);
    const [tab, setTab] = useState('data');
    const [draft, setDraft] = useState(() => draftOf(row));
    const [events, setEvents] = useState([]);
    const [eventsLoading, setEventsLoading] = useState(false);
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        if (!open || !row) return;
        setTab('data');
        setDraft(draftOf(row));
        setEvents([]);
        // Карточку заново открывают другим офисом — от него и зависим, а не
        // от каждой свежей копии строки после сохранения.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, row?.id]);

    const loadEvents = useCallback(() => {
        if (!row) return;
        setEventsLoading(true);
        axios.get(`${apiBaseUrl}/api/thermoboxes/rows/${row.id}/events`, { headers: headers() })
            .then((response) => setEvents(response.data?.items || []))
            .catch(() => setEvents([]))
            .finally(() => setEventsLoading(false));
    }, [apiBaseUrl, headers, row]);

    useEffect(() => { if (open && tab === 'history') loadEvents(); }, [open, tab, loadEvents]);

    if (!row) return null;

    const changes = changedFields(row, draft);
    const errors = draftErrors(draft);
    const dirty = Object.keys(changes).length > 0;
    const valid = Object.keys(errors).length === 0;

    const save = async () => {
        if (!dirty || !valid || saving) return;
        setSaving(true);
        try {
            const response = await axios.put(`${apiBaseUrl}/api/thermoboxes/rows`,
                { items: [{ id: row.id, version: row.version, ...changes }] }, { headers: headers() });
            const fresh = (response.data?.rows || [])[0];
            if (fresh) {
                onSaved?.([fresh]);
                setDraft(draftOf(fresh));
            }
            showToast?.('Сохранено', 'success');
            onClose();
        } catch (requestError) {
            const data = requestError?.response?.data;
            if (data?.code === 'THERMO_CONFLICT' && Array.isArray(data.rows)) {
                onConflict?.(data.rows);
                const fresh = data.rows.find((item) => item.id === row.id);
                if (fresh) setDraft(draftOf(fresh));
            }
            showToast?.(errorOf(requestError, 'Не удалось сохранить'), 'error');
        } finally {
            setSaving(false);
        }
    };

    const toggleVisible = async (next) => {
        setSaving(true);
        try {
            const response = await axios.patch(`${apiBaseUrl}/api/thermoboxes/rows/${row.id}/visibility`,
                { is_active: next }, { headers: headers() });
            if (response.data?.row) onSaved?.([response.data.row]);
            showToast?.(next ? 'Офис снова в таблице' : 'Офис убран из таблицы', 'success');
        } catch (requestError) {
            showToast?.(errorOf(requestError, 'Не удалось сохранить'), 'error');
        } finally {
            setSaving(false);
        }
    };

    const footer = canEdit && tab === 'data' ? (
        <>
            <button type="button" className={iosBtnSecondary} onClick={() => setDraft(draftOf(row))} disabled={!dirty || saving}>
                Сбросить
            </button>
            <button type="button" className={iosBtnPrimary} onClick={save} disabled={!dirty || !valid || saving}>
                {saving && <Loader2 size={15} className="animate-spin" />}
                Сохранить
            </button>
        </>
    ) : null;

    return (
        <IosModal open={open} onClose={onClose} title={row.city} subtitle={row.address || row.name} footer={footer}>
            <div className="space-y-4">
                <IosSegmented
                    value={tab}
                    onChange={setTab}
                    stretch
                    ariaLabel="Карточка офиса"
                    options={[
                        { value: 'data', label: 'Данные' },
                        { value: 'history', label: 'История' },
                    ]}
                />

                {tab === 'data' ? (
                    <>
                        <RowForm draft={draft} row={row} editable={canEdit} onChange={setDraft} />
                        {row.updated_at && (
                            <p className="px-1 text-[12px] text-slate-500">
                                Обновлено {fmtStamp(row.updated_at)}{row.updated_by_name ? ` · ${row.updated_by_name}` : ''}
                            </p>
                        )}
                        {canManage && (
                            <div className={`${iosCard} flex items-center justify-between gap-3 px-3.5 py-3`}>
                                <div>
                                    <div className="text-[14px] text-slate-800">Показывать в таблице</div>
                                    <div className="text-[12px] text-slate-500">Убранный офис скрыт от всех, история сохраняется</div>
                                </div>
                                <IosToggle checked={row.is_active} onChange={toggleVisible} disabled={saving} />
                            </div>
                        )}
                    </>
                ) : (
                    <History items={events} loading={eventsLoading} />
                )}
            </div>
        </IosModal>
    );
};

/* ── Добавить офис в таблицу ────────────────────────────────────────────── */

const NEW_DRAFT = {
    free_boxes: '0', thermo_bags: '0', used_boxes: '0',
    tariff: 'auto_couriers', min_orders: '15', period_days: '7', deposit_tenge: '5000', special_condition: '',
};

export const AddOfficeSheet = ({ open, onClose, directory, apiBaseUrl, headers, onAdded, showToast }) => {
    const [officeId, setOfficeId] = useState(null);
    const [draft, setDraft] = useState(NEW_DRAFT);
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        if (open) { setOfficeId(null); setDraft(NEW_DRAFT); }
    }, [open]);

    const errors = draftErrors(draft);
    const valid = Boolean(officeId) && Object.keys(errors).length === 0;
    const options = useMemo(() => (directory || []).map((office) => ({
        value: office.id, label: `${office.city} · ${office.address || office.name}`,
    })), [directory]);

    const save = async () => {
        if (!valid || saving) return;
        setSaving(true);
        try {
            const body = { office_id: officeId, ...changedFields({}, draft) };
            const response = await axios.post(`${apiBaseUrl}/api/thermoboxes/rows`, body, { headers: headers() });
            if (response.data?.row) onAdded?.([response.data.row]);
            showToast?.('Офис добавлен в таблицу', 'success');
            onClose();
        } catch (requestError) {
            showToast?.(errorOf(requestError, 'Не удалось добавить офис'), 'error');
        } finally {
            setSaving(false);
        }
    };

    return (
        <IosModal
            open={open}
            onClose={onClose}
            title="Добавить офис"
            footer={(
                <button type="button" className={`${iosBtnPrimary} w-full sm:w-auto`} disabled={!valid || saving} onClick={save}>
                    {saving && <Loader2 size={15} className="animate-spin" />}
                    Добавить
                </button>
            )}
        >
            <div className="space-y-4">
                <section className="space-y-1.5">
                    <div className={iosGroupLabel}>Офис из справочника</div>
                    <CustomSelect
                        value={officeId}
                        onChange={setOfficeId}
                        options={options}
                        placeholder={options.length ? 'Выберите офис' : 'Все офисы уже в таблице'}
                        disabled={!options.length}
                        variant="ios"
                        searchable
                        ariaLabel="Офис"
                    />
                </section>
                <RowForm draft={draft} editable onChange={setDraft} />
            </div>
        </IosModal>
    );
};
