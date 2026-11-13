import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Check, Download, FileText, Loader2, Paperclip, Pencil } from 'lucide-react';
import { IosModal, IosMenu, iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosGroupLabel } from '../ui/ios';
import IosTimePicker from '../ui/TimePicker';
import { saveBlobResponse, blobErrorText } from '../hr_office/hrOffice';
import { Loading } from '../hr_office/hrUi';
import {
    Choice, Field, FilePicker, FormDate, FormInput, FormSelect, FormText, IssueNav, MetaChip, QuantityInput, Row, TonePill,
    useIssueNav,
} from '../payments/paymentsUi';
import {
    ACTION_LABELS, COPY_KIND_LABELS, DELIVERY_TYPE_LABELS, DISCREPANCY_OPTIONS, DOC_TYPE_LABELS, EVENT_LABELS, FIELD_LABELS,
    FILE_KIND_LABELS, INSTANT_ACTIONS, LINE_STATUS_LABELS, NOTE_REQUIRED, URGENCY_LABELS, actionLayout, almatyNow, blankLines,
    dateText, dateTimeText, deadlineCell, errorText, fileSizeLabel, linesPayload, officeLine, plural,
    reconcileIssues, routeText, serverIssues, slaText, statusLabel, statusTone,
} from './mailMeta';

/*
 * Карточка отправления: что, откуда, куда, где сейчас, до какого срока, чем подтверждено и кто
 * что делал (ТЗ п. 3–8, 14). Шаг статуса открывается вторым уровнем того же окна (шеврон
 * «назад»), а не вторым окном поверх: два затемнения разом читаются как сбой.
 *
 * Какие шаги доступны — решает сервер (card.actions); главный из них — синей кнопкой, частые —
 * рядом, редкие — в меню «⋯».
 */
const VERDICTS = [{ value: 'ok', label: 'Совпадает' }, { value: 'bad', label: 'Расхождение' }];
const PROOF_KINDS = ['waybill', 'handover'];
const ATTACH_KINDS = Object.entries(FILE_KIND_LABELS).map(([value, label]) => ({ value, label }));
const anyDiscrepancy = (values) => (values.lines || []).some((line) => line.verdict === 'bad');

const addDays = (iso, days) => {
    const [year, month, day] = String(iso).split('-').map(Number);
    const date = new Date(year, month - 1, day + days);
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
};

function Section({ title, children, right = null }) {
    return (
        <section className="space-y-1.5">
            <div className="flex items-center justify-between gap-2">
                <span className={iosGroupLabel}>{title}</span>
                {right}
            </div>
            <div className="rounded-2xl bg-white px-4 py-2 ring-1 ring-slate-200/70">{children}</div>
        </section>
    );
}

function periodText(item) {
    if (!item.period_from && !item.period_to) return '';
    if (item.period_from && item.period_to) return `${dateText(item.period_from)} – ${dateText(item.period_to)}`;
    return dateText(item.period_from || item.period_to);
}

function ItemsTable({ items }) {
    const reconciled = items.some((item) => item.line_status);
    return (
        <div className="divide-y divide-slate-100">
            {items.map((item) => (
                <div key={item.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-2">
                    <div className="min-w-0 flex-1">
                        <div className="text-[13.5px] font-medium text-slate-900">
                            {item.doc_type === 'other' ? item.other_description : DOC_TYPE_LABELS[item.doc_type]}
                            {item.is_confidential && <span className="ml-2 text-[11.5px] font-normal text-slate-500">конфиденциально</span>}
                        </div>
                        <div className="text-[12px] text-slate-500">
                            {[item.doc_type === 'other' ? 'Иное' : '', periodText(item), COPY_KIND_LABELS[item.copy_kind],
                                item.doc_number ? `№ ${item.doc_number}` : ''].filter(Boolean).join(' · ')}
                        </div>
                        {item.line_note && <div className="text-[12px] text-amber-800">{item.line_note}</div>}
                    </div>
                    <div className="text-[13.5px] tabular-nums text-slate-900">{item.qty} шт.</div>
                    {reconciled && (
                        <div className="w-full sm:w-auto">
                            <TonePill tone={item.line_status === 'ok' ? 'done' : 'blocked'}>
                                {item.line_status === 'ok' ? 'совпадает' : `${LINE_STATUS_LABELS[item.line_status].toLowerCase()}: пришло ${item.received_qty}`}
                            </TonePill>
                        </div>
                    )}
                </div>
            ))}
        </div>
    );
}

/* Значение из «было → стало»: время и даты — по-русски, пустое — прочерком. */
const shownValue = (value) => {
    if (value === null || value === undefined || value === '') return '—';
    const text = String(value);
    if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(text)) return dateTimeText(text);
    if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return dateText(text);
    return text;
};

/* Файл в истории: у кого вложения закрыты, имени нет (сервер его убрал) — только вид. */
const fileText = (kind, filename) => (filename ? `${FILE_KIND_LABELS[kind] || 'файл'} «${filename}»`
    : (FILE_KIND_LABELS[kind] || 'файл').toLowerCase());

function eventDetails(event) {
    const payload = event.payload || {};
    const parts = [];
    if (payload.track_number) parts.push(`трек ${payload.track_number}`);
    if (payload.handed_at) parts.push(`передано ${dateTimeText(payload.handed_at)}`);
    if (payload.planned_pickup_on) parts.push(`забор ${dateText(payload.planned_pickup_on)}`);
    if (payload.delivered_on) parts.push(`вручено ${dateText(payload.delivered_on)}`);
    if (payload.due_on) parts.push(`срок до ${dateText(payload.due_on)}${payload.zone_manual ? ' (зона выбрана вручную)' : ''}`);
    if (payload.discrepancies) parts.push(`расхождений: ${payload.discrepancies}`);
    Object.entries(payload.changes || {}).forEach(([name, [before, after]]) => {
        parts.push(`${FIELD_LABELS[name] || name}: ${shownValue(before)} → ${shownValue(after)}`);
    });
    if (payload.items_changed) {
        parts.push(payload.items_before && payload.items_after
            ? `содержимое: было «${payload.items_before.join('; ') || '—'}» → стало «${payload.items_after.join('; ') || '—'}»`
            : 'изменено содержимое');
    }
    if (payload.file_kind) parts.push(fileText(payload.file_kind, payload.filename));
    (payload.files || []).forEach((file) => parts.push(fileText(file.kind, file.filename)));
    return parts.join(' · ');
}

function History({ events }) {
    return (
        <ol className="space-y-3 py-2">
            {[...events].reverse().map((event) => {
                const title = event.kind === 'status' || event.kind === 'created'
                    ? (event.kind === 'created' ? `Создано · ${statusLabel(event.to_status)}` : statusLabel(event.to_status))
                    : EVENT_LABELS[event.kind] || event.kind;
                const details = eventDetails(event);
                return (
                    <li key={event.id} className="flex gap-3">
                        <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-slate-300" />
                        <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-baseline gap-x-2">
                                <span className="text-[13.5px] font-medium text-slate-900">{title}</span>
                                <span className="text-[12px] tabular-nums text-slate-400">{dateTimeText(event.at)}</span>
                            </div>
                            <div className="text-[12.5px] text-slate-500">{event.actor_name || 'Система'}</div>
                            {details && <div className="text-[12.5px] text-slate-600">{details}</div>}
                            {event.note && <div className="whitespace-pre-line text-[13px] text-slate-800">«{event.note}»</div>}
                        </div>
                    </li>
                );
            })}
        </ol>
    );
}

/* ── Шаги статуса ──────────────────────────────────────────────────────────── */

function initialStep(step, card, today) {
    const now = almatyNow();
    const shipment = card.shipment;
    if (step === 'courier_request') return { planned_pickup_on: today, track_number: shipment.track_number || '', files: [] };
    if (step === 'pickup_failed') return { planned_pickup_on: addDays(today, 1), note: '' };
    if (step === 'hand_over') {
        return { date: now.date, time: now.time, track_number: shipment.track_number || '', zone_code: '', files: [] };
    }
    if (step === 'deliver') return { delivered_on: today, files: [] };
    if (step === 'reconcile') return { delivered_on: today, lines: blankLines(card.items), note: '', files: [] };
    if (step === 'attach') return { kind: 'other', files: [] };
    return { note: '' };
}

function stepIssues(step, values, card) {
    const issues = [];
    if (NOTE_REQUIRED[step] && !String(values.note || '').trim()) issues.push({ key: 'note', text: NOTE_REQUIRED[step] });
    if (step === 'courier_request' && !values.planned_pickup_on) {
        issues.push({ key: 'planned_pickup_on', text: 'Укажите плановую дату забора' });
    }
    if (step === 'pickup_failed' && !values.planned_pickup_on) {
        issues.push({ key: 'planned_pickup_on', text: 'Укажите новую дату забора' });
    }
    if (step === 'hand_over') {
        if (!values.date || !values.time) issues.push({ key: 'handed_at', text: 'Укажите дату и время передачи' });
        if (!String(values.track_number || '').trim()) {
            issues.push({ key: 'track_number', text: 'Трек-номер или согласованный временный номер' });
        }
        if (!card.route_zone && !values.zone_code) issues.push({ key: 'zone_code', text: 'Выберите зону' });
        const hasProof = (card.files || []).some((file) => PROOF_KINDS.includes(file.kind));
        if (!hasProof && !(values.files || []).length) {
            issues.push({ key: 'files', text: 'Приложите накладную или подтверждение передачи' });
        }
    }
    if (step === 'deliver' && !values.delivered_on) issues.push({ key: 'delivered_on', text: 'Укажите дату вручения' });
    if (step === 'reconcile') {
        issues.push(...reconcileIssues(values.lines, card.items, {
            needDate: card.shipment.status !== 'delivered', deliveredOn: values.delivered_on, note: values.note,
        }));
    }
    if (step === 'attach' && !(values.files || []).length) issues.push({ key: 'files', text: 'Выберите файл' });
    return issues;
}

function stepPayload(step, values) {
    if (step === 'hand_over') {
        return {
            handed_at: `${values.date}T${values.time}`, track_number: values.track_number, zone_code: values.zone_code || null,
            confirm_duplicate: Boolean(values.confirm_duplicate),
        };
    }
    if (step === 'reconcile') {
        // Комментарий — только к расхождению: после «Всё совпадает» скрытый текст не уходит.
        const payload = { delivered_on: values.delivered_on, lines: linesPayload(values.lines) };
        if (anyDiscrepancy(values)) payload.note = values.note;
        return payload;
    }
    if (step === 'attach') return { kind: values.kind };
    const { files, ...rest } = values;
    return rest;
}

function StepForm({ step, card, values, setValues, issueOf, context, duplicate }) {
    const set = (changes) => setValues((prev) => ({ ...prev, ...changes }));
    const zones = context?.zones || [];
    const hasProof = (card.files || []).some((file) => PROOF_KINDS.includes(file.kind));
    const needDate = card.shipment.status !== 'delivered';

    if (step === 'courier_request') {
        return (
            <div className="space-y-4">
                <Field label="Плановая дата забора" required as="div" anchor="planned_pickup_on" issue={issueOf('planned_pickup_on')}>
                    <FormDate value={values.planned_pickup_on} onChange={(value) => set({ planned_pickup_on: value || '' })} />
                </Field>
                <Field label="Трек-номер" hint="Если курьерская служба уже выдала номер. Можно внести и при передаче курьеру." optionalMark>
                    <FormInput value={values.track_number} maxLength={64} onChange={(event) => set({ track_number: event.target.value })} />
                </Field>
                <Field label="Накладная" as="div" hint="Скан или фото накладной MBE KZ — она же станет доказательством передачи.">
                    <FilePicker files={values.files} onChange={(files) => set({ files })} label="Приложить накладную" />
                </Field>
            </div>
        );
    }
    if (step === 'hand_over') {
        // Эконом — только зоны, где задан его срок: иначе сервер не посчитает срок.
        const economy = card.shipment.delivery_type === 'economy';
        const zoneOptions = zones
            .filter((zone) => !economy || (zone.eco_max_days !== null && zone.eco_max_days !== undefined))
            .map((zone) => ({ value: zone.code, label: `${zone.label} · ${economy ? zone.eco_max_days : zone.max_days} раб. дн.` }));
        return (
            <div className="space-y-4">
                <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_9rem]">
                    <Field label="Дата передачи" required as="div" anchor="handed_at" issue={issueOf('handed_at')}>
                        <FormDate value={values.date} onChange={(value) => set({ date: value || '' })} />
                    </Field>
                    <Field label="Время" required as="div">
                        <IosTimePicker value={values.time} onChange={(value) => set({ time: value || '' })} step={5} allowEmpty={false}
                                       ariaLabel="Время передачи" />
                    </Field>
                </div>
                <Field label="Трек-номер" required anchor="track_number" issue={issueOf('track_number')}
                       hint="Номер отправления у курьерской службы. Нет номера — согласованный временный.">
                    <FormInput value={values.track_number} maxLength={64}
                               onChange={(event) => set({ track_number: event.target.value, confirm_duplicate: false })} />
                </Field>
                {duplicate && (
                    <div className="rounded-xl bg-amber-50 px-3.5 py-2.5 text-[13px] text-amber-900 ring-1 ring-amber-200/70">
                        Этот трек уже есть у {duplicate.join(', ')}. Если это не ошибка — нажмите «Передано курьеру» ещё раз.
                    </div>
                )}
                {card.route_zone ? (
                    <div className="rounded-xl bg-slate-50 px-3.5 py-2.5 text-[13px] text-slate-700 ring-1 ring-slate-200/70">
                        {card.route_zone.label}
                        {card.route_zone.window ? ` · ${card.route_zone.window[0] === card.route_zone.window[1]
                            ? card.route_zone.window[1] : `${card.route_zone.window[0]}–${card.route_zone.window[1]}`} раб. дн.` : ''}
                        {' — срок посчитается от даты передачи, праздники РК не считаются.'}
                    </div>
                ) : (
                    <Field label="Зона маршрута" required as="div" anchor="zone_code" issue={issueOf('zone_code')}
                           hint={`Маршрута ${routeText(card.shipment)} нет в матрице «Справочников» — выберите зону договора вручную.`}>
                        <FormSelect value={values.zone_code || null} options={zoneOptions} placeholder="Зона договора"
                                    onChange={(value) => set({ zone_code: value })} ariaLabel="Зона маршрута" />
                    </Field>
                )}
                <Field label={hasProof ? 'Подтверждение передачи' : 'Накладная или подтверждение передачи'} required={!hasProof}
                       optionalMark={hasProof} as="div" anchor="files" issue={issueOf('files')}
                       hint={hasProof ? 'Накладная уже приложена — второй файл по желанию.' : 'Без доказательства передачи статус не ставится (ТЗ п. 14).'}>
                    <FilePicker files={values.files} onChange={(files) => set({ files })} label="Приложить файл" />
                </Field>
            </div>
        );
    }
    if (step === 'deliver') {
        return (
            <div className="space-y-4">
                <Field label="Дата вручения" required as="div" anchor="delivered_on" issue={issueOf('delivered_on')}
                       hint="Когда посылку физически вручили. Это отметка курьера — сверка содержимого отдельно.">
                    <FormDate value={values.delivered_on} onChange={(value) => set({ delivered_on: value || '' })} />
                </Field>
                <Field label="Подтверждение доставки" as="div">
                    <FilePicker files={values.files} onChange={(files) => set({ files })} label="Приложить файл" />
                </Field>
            </div>
        );
    }
    if (step === 'reconcile') {
        const setLine = (index, changes) => set({ lines: values.lines.map((line, at) => (at === index ? { ...line, ...changes } : line)) });
        const anyBad = values.lines.some((line) => line.verdict === 'bad');
        return (
            <div className="space-y-4">
                {needDate && (
                    <Field label="Дата вручения" required as="div" anchor="delivered_on" issue={issueOf('delivered_on')}>
                        <FormDate value={values.delivered_on} onChange={(value) => set({ delivered_on: value || '' })} />
                    </Field>
                )}
                <div className="flex items-center justify-between">
                    <span className={iosGroupLabel}>Сверка по строкам</span>
                    <button type="button" className={`${iosBtnGhost} !py-1.5 text-[12.5px]`}
                            onClick={() => set({ lines: values.lines.map((line) => ({ ...line, verdict: 'ok' })) })}>
                        <Check size={14} /> Всё совпадает
                    </button>
                </div>
                {card.items.map((item, index) => {
                    const line = values.lines[index];
                    return (
                        <div key={item.id} className="space-y-2 rounded-2xl bg-slate-50 p-3 ring-1 ring-slate-200/60">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                                <div className="min-w-0">
                                    <div className="text-[13.5px] font-medium text-slate-900">
                                        {item.doc_type === 'other' ? item.other_description : DOC_TYPE_LABELS[item.doc_type]}
                                    </div>
                                    <div className="text-[12px] text-slate-500">
                                        {[periodText(item), COPY_KIND_LABELS[item.copy_kind], `заявлено ${item.qty}`].filter(Boolean).join(' · ')}
                                    </div>
                                </div>
                                <Field as="div" anchor={`lines.${index}.line_status`} issue={issueOf(`lines.${index}.line_status`)}>
                                    <Choice value={line.verdict} options={VERDICTS} ariaLabel="Итог строки"
                                            onChange={(value) => setLine(index, { verdict: value })} />
                                </Field>
                            </div>
                            {line.verdict === 'bad' && (
                                <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_8rem]">
                                    <Field label="Что не так" required as="div">
                                        <FormSelect value={line.line_status} options={DISCREPANCY_OPTIONS} ariaLabel="Вид расхождения"
                                                    onChange={(value) => setLine(index, { line_status: value })} />
                                    </Field>
                                    <Field label="Пришло, шт." required anchor={`lines.${index}.received_qty`}
                                           issue={issueOf(`lines.${index}.received_qty`)}>
                                        <QuantityInput value={line.received_qty}
                                                       onChange={(value) => setLine(index, { received_qty: value.replace(/[^\d]/g, '') })} />
                                    </Field>
                                </div>
                            )}
                        </div>
                    );
                })}
                {anyBad && (
                    <>
                        <Field label="Комментарий к расхождению" required anchor="note" issue={issueOf('note')}
                               hint="Отправитель и координатор увидят его в карточке и в истории.">
                            <FormText rows={2} value={values.note} maxLength={2000} onChange={(event) => set({ note: event.target.value })} />
                        </Field>
                        <Field label="Фото или акт" as="div" hint="Повреждение упаковки, вскрытый конверт, акт расхождения.">
                            <FilePicker files={values.files} onChange={(files) => set({ files })} label="Приложить файл" />
                        </Field>
                    </>
                )}
            </div>
        );
    }
    if (step === 'attach') {
        return (
            <div className="space-y-4">
                <Field label="Что это" required as="div">
                    <FormSelect value={values.kind} options={ATTACH_KINDS} onChange={(value) => set({ kind: value })} ariaLabel="Вид файла" />
                </Field>
                <Field label="Файлы" required as="div" anchor="files" issue={issueOf('files')}>
                    <FilePicker files={values.files} onChange={(files) => set({ files })} label="Выбрать файл" />
                </Field>
            </div>
        );
    }
    // Шаги с одной причиной: «Забор не состоялся», «Не удалось вручить», «Проблема решена»,
    // «Начать возврат», «Возвращено», «Отменить».
    return (
        <div className="space-y-4">
            {step === 'pickup_failed' && (
                <Field label="Новая дата забора" required as="div" anchor="planned_pickup_on" issue={issueOf('planned_pickup_on')}>
                    <FormDate value={values.planned_pickup_on} onChange={(value) => set({ planned_pickup_on: value || '' })} />
                </Field>
            )}
            <Field label={NOTE_REQUIRED[step] ? NOTE_REQUIRED[step].replace(/^Укажите /, '').replace(/^Опишите, /, '')
                .replace(/^./, (letter) => letter.toUpperCase()) : 'Комментарий'}
                   required={Boolean(NOTE_REQUIRED[step])} optionalMark={!NOTE_REQUIRED[step]} anchor="note" issue={issueOf('note')}>
                <FormText rows={3} value={values.note} maxLength={2000} onChange={(event) => set({ note: event.target.value })} />
            </Field>
        </div>
    );
}

const STEP_SUBMIT = { attach: 'Приложить', reconcile: 'Сохранить сверку' };

export default function MailShipmentCard({ open, shipmentId, api, context, toast, onClose, onChanged, onEdit }) {
    const [card, setCard] = useState(null);
    const [loadError, setLoadError] = useState('');
    const [step, setStep] = useState(null);
    const [values, setValues] = useState({});
    const [tried, setTried] = useState(false);
    const [fromServer, setFromServer] = useState([]);
    const [duplicate, setDuplicate] = useState(null);
    const [busy, setBusy] = useState('');
    const stepRef = useRef(null);
    const today = context?.today || almatyNow().date;

    const load = useCallback(() => api.get(`/shipments/${shipmentId}`)
        .then(({ data }) => { setCard(data); setLoadError(''); })
        .catch((requestError) => setLoadError(errorText(requestError, 'Карточка не открылась'))), [api, shipmentId]);
    useEffect(() => { load(); }, [load]);

    const issues = useMemo(() => {
        if (!step || !tried || !card) return [];
        const local = stepIssues(step, values, card);
        const keys = new Set(local.map((item) => item.key));
        return local.concat(fromServer.filter((item) => !keys.has(item.key)));
    }, [step, tried, values, card, fromServer]);
    const issueOf = (key) => issues.find((item) => item.key === key)?.text || '';
    /* «Не хватает: N ⌃ ⌄» и шаги — как в форме: в сверке на восемь строк неотмеченная строка
       бывает ниже края окна, и молчащая кнопка выглядела бы сломанной. */
    const issueKeys = useMemo(() => [...new Set(issues.map((item) => item.key))], [issues]);
    const nav = useIssueNav(stepRef, issueKeys);

    const openStep = (code) => {
        setStep(code);
        setValues(initialStep(code, card, today));
        setTried(false);
        setFromServer([]);
        setDuplicate(null);
    };

    const applied = (data, message) => {
        setCard(data);
        setStep(null);
        onChanged?.();
        if (message) toast?.(message, 'success');
    };

    const runInstant = async (code) => {
        setBusy(code);
        try {
            const { data } = await api.post(`/shipments/${shipmentId}/actions/${code}`, {});
            applied(data, `Статус: ${statusLabel(data.shipment.status)}`);
        } catch (requestError) {
            // Чего не хватает до «Готово к отправке», карточка уже говорит строкой сверху.
            toast?.(errorText(requestError, 'Не получилось'), 'error');
        } finally {
            setBusy('');
        }
    };

    const submitStep = async () => {
        setTried(true);
        setFromServer([]);
        if (stepIssues(step, values, card).length) {
            nav.first();
            return;
        }
        setBusy(step);
        try {
            // Фото и акт расхождения не уходят, если в итоге всё совпало.
            const files = step === 'reconcile' && !anyDiscrepancy(values) ? []
                : (values.files || []).map((entry) => entry.file);
            const path = step === 'attach' ? `/shipments/${shipmentId}/files` : `/shipments/${shipmentId}/actions/${step}`;
            const { data } = await api.post(path, stepPayload(step, values), files.length ? files : null);
            applied(data, step === 'attach' ? 'Файл приложен' : `Статус: ${statusLabel(data.shipment.status)}`);
        } catch (requestError) {
            const body = requestError?.response?.data;
            if (body?.code === 'MAIL_TRACK_DUPLICATE') {
                setDuplicate(body.duplicates || []);
                setValues((prev) => ({ ...prev, confirm_duplicate: true }));
            } else {
                const fields = serverIssues(requestError);
                setFromServer(fields);
                if (fields.length) nav.first();
                toast?.(errorText(requestError, 'Не получилось'), 'error');
            }
        } finally {
            setBusy('');
        }
    };

    const download = async (file) => {
        try {
            const response = await api.blob(`/files/${file.id}/download`);
            saveBlobResponse(response, file.filename);
        } catch (requestError) {
            toast?.(await blobErrorText(requestError, 'Файл не скачался'), 'error');
        }
    };

    const shipment = card?.shipment;
    const actions = card?.actions || [];
    const layout = actionLayout(actions);
    const { primary, secondary } = layout;
    const menu = layout.menu.map((code) => ({
        key: code, label: ACTION_LABELS[code], danger: code === 'cancel', onSelect: () => openStep(code),
    }));
    const startAction = (code) => (INSTANT_ACTIONS.includes(code) ? runInstant(code) : openStep(code));

    const viewFooter = card && (
        <div className="flex flex-wrap items-center gap-2">
            {card.edit?.allowed && (
                <button type="button" className={iosBtnGhost} onClick={() => onEdit?.(card)}>
                    <Pencil size={14} /> Изменить
                </button>
            )}
            <div className="ml-auto flex flex-wrap items-center gap-2">
                {menu.length > 0 && <IosMenu items={menu} label="Ещё шаги" />}
                {secondary.map((code) => (
                    <button key={code} type="button" className={iosBtnSecondary} onClick={() => startAction(code)} disabled={Boolean(busy)}>
                        {busy === code && <Loader2 size={15} className="animate-spin" />}
                        {ACTION_LABELS[code]}
                    </button>
                ))}
                {primary && (
                    <button type="button" className={iosBtnPrimary} onClick={() => startAction(primary)} disabled={Boolean(busy)}>
                        {busy === primary && <Loader2 size={15} className="animate-spin" />}
                        {ACTION_LABELS[primary]}
                    </button>
                )}
            </div>
        </div>
    );
    const stepFooter = step && (
        <div className="flex flex-wrap items-center justify-end gap-2">
            <IssueNav count={issueKeys.length} onStep={(where) => nav.step(where)} />
            <button type="button" className={iosBtnGhost} onClick={() => setStep(null)}>Назад</button>
            <button type="button" className={step === 'cancel' ? `${iosBtnPrimary} !bg-rose-600 hover:!bg-rose-700` : iosBtnPrimary}
                    onClick={submitStep} disabled={Boolean(busy)}>
                {busy === step && <Loader2 size={15} className="animate-spin" />}
                {STEP_SUBMIT[step] || ACTION_LABELS[step]}
            </button>
        </div>
    );

    const missing = Object.values(card?.missing_for_ready || {});
    const deadline = deadlineCell(shipment);
    const title = step ? (step === 'attach' ? 'Приложить файл' : ACTION_LABELS[step]) : (shipment?.number || 'Отправление');
    const subtitle = shipment ? `${shipment.number && step ? `${shipment.number} · ` : ''}${routeText(shipment)}` : '';

    return (
        <IosModal open={open} onClose={onClose} onBack={step ? () => setStep(null) : null} title={title} subtitle={subtitle}
                  maxWidth="max-w-3xl" footer={step ? stepFooter : viewFooter}
                  sizeKey={card ? (step ? `step-${step}` : 'view') : (loadError ? 'error' : 'loading')}>
            {loadError && <div className="p-4 text-[13.5px] text-rose-600">{loadError}</div>}
            {!card && !loadError && (
                <div className="flex items-center justify-center sm:min-h-[calc(92vh-6rem)]">
                    <Loading text="Открываем отправление…" />
                </div>
            )}
            {card && step && (
                <div ref={stepRef} className="motion-safe:animate-fade-in-soft" data-mail-step={step}>
                    <StepForm step={step} card={card} values={values} setValues={setValues} issueOf={issueOf} context={context}
                              duplicate={duplicate} />
                </div>
            )}
            {card && !step && (
                <div className="space-y-5 motion-safe:animate-fade-in-soft" data-mail-card={shipment.number}>
                    <div className="flex flex-wrap items-center gap-2">
                        <TonePill tone={statusTone(shipment)}>{statusLabel(shipment.status)}</TonePill>
                        {deadline && <MetaChip tone={deadline.tone}>{deadline.text}</MetaChip>}
                        {shipment.urgency === 'urgent' && <MetaChip tone="soon">срочное</MetaChip>}
                        <span className="text-[12.5px] text-slate-500">
                            создал(а) {shipment.created_by_name || '—'} · {dateTimeText(shipment.created_at)}
                        </span>
                    </div>
                    {missing.length > 0 && actions.includes('ready') && (
                        <div className="rounded-xl bg-amber-50 px-3.5 py-2.5 text-[13px] text-amber-900 ring-1 ring-amber-200/70">
                            До «Готово к отправке» не хватает: {missing.map((text) => text.replace(/^(Укажите|Выберите|Добавьте) /, '')).join(', ')}.
                        </div>
                    )}

                    <Section title={`Содержимое · ${shipment.qty_total} ${plural(shipment.qty_total, ['документ', 'документа', 'документов'])}`}>
                        <ItemsTable items={card.items} />
                    </Section>

                    <Section title="Откуда и куда">
                        <Row label="Откуда" wide>{officeLine(shipment.sender_office_name, shipment.sender_city)}</Row>
                        <Row label="Отправитель" wide>{[shipment.sender_name, shipment.sender_phone].filter(Boolean).join(' · ')}</Row>
                        <Row label="Куда" wide>{officeLine(shipment.recipient_office_name, shipment.recipient_city)}</Row>
                        <Row label="Получатель" wide>{[shipment.recipient_name, shipment.recipient_phone].filter(Boolean).join(' · ')}</Row>
                        <Row label="Адрес" wide>{[shipment.recipient_address, shipment.recipient_postcode].filter(Boolean).join(', ')}</Row>
                    </Section>

                    {(shipment.track_number || shipment.planned_pickup_on || shipment.handed_at) && (
                        <Section title="Курьер и срок">
                            <Row label="Трек-номер" wide>{shipment.track_number}</Row>
                            <Row label="Плановый забор" wide>{shipment.planned_pickup_on ? dateText(shipment.planned_pickup_on) : ''}</Row>
                            <Row label="Передано курьеру" wide>{dateTimeText(shipment.handed_at)}</Row>
                            <Row label="Зона" wide>
                                {shipment.zone_code
                                    ? `${(context?.zones || []).find((zone) => zone.code === shipment.zone_code)?.label || shipment.zone_code}`
                                      + `${shipment.zone_manual ? ' (выбрана вручную)' : ''} · ${slaText(shipment)}`
                                    : ''}
                            </Row>
                            <Row label="Крайний срок" wide>
                                {shipment.due_on ? `${dateText(shipment.due_on)}${shipment.earliest_on && shipment.earliest_on !== shipment.due_on
                                    ? ` (ожидаем с ${dateText(shipment.earliest_on)})` : ''}` : ''}
                            </Row>
                            <Row label="Вручено" wide>{shipment.delivered_on ? dateText(shipment.delivered_on) : ''}</Row>
                            <Row label="Сверено" wide>
                                {shipment.reconciled_at ? `${shipment.reconciled_by_name || ''} · ${dateTimeText(shipment.reconciled_at)}` : ''}
                            </Row>
                        </Section>
                    )}

                    <Section title="Отправление">
                        <Row label="Юрлицо-плательщик" wide>{shipment.payer_name}</Row>
                        <Row label="Доставка" wide>
                            {[DELIVERY_TYPE_LABELS[shipment.delivery_type], URGENCY_LABELS[shipment.urgency]].filter(Boolean).join(' · ')}
                        </Row>
                        <Row label="Мест и вес" wide>
                            {[`${shipment.places} ${plural(shipment.places, ['место', 'места', 'мест'])}`,
                                shipment.weight_kg ? `${String(shipment.weight_kg).replace('.', ',')} кг` : '',
                                shipment.volume_weight_kg ? `объёмный ${String(shipment.volume_weight_kg).replace('.', ',')} кг` : '']
                                .filter(Boolean).join(' · ')}
                        </Row>
                        <Row label="Объявленная ценность" wide>
                            {shipment.declared_value ? `${Number(shipment.declared_value).toLocaleString('ru-RU')} ₸` : ''}
                        </Row>
                        <Row label="Комментарий" wide>{shipment.comment}</Row>
                    </Section>

                    <Section title="Файлы" right={card.can_attach ? (
                        <button type="button" className={`${iosBtnGhost} !py-1 text-[12.5px]`} onClick={() => openStep('attach')}>
                            <Paperclip size={13} /> Приложить
                        </button>
                    ) : null}>
                        {card.files_hidden && <div className="py-2 text-[13px] text-slate-500">Вложения конфиденциального отправления вам закрыты</div>}
                        {!card.files_hidden && card.files.length === 0 && <div className="py-2 text-[13px] text-slate-400">Файлов пока нет</div>}
                        <div className="divide-y divide-slate-100">
                            {card.files.map((file) => (
                                <button key={file.id} type="button" onClick={() => download(file)}
                                        className="flex w-full items-center gap-3 py-2 text-left transition hover:bg-slate-50/80">
                                    <FileText size={15} className="shrink-0 text-slate-400" />
                                    <span className="min-w-0 flex-1">
                                        <span className="block truncate text-[13.5px] text-slate-900">{file.filename}</span>
                                        <span className="block text-[12px] text-slate-500">
                                            {[FILE_KIND_LABELS[file.kind], fileSizeLabel(file.size), file.uploaded_by_name,
                                                dateTimeText(file.uploaded_at)].filter(Boolean).join(' · ')}
                                        </span>
                                    </span>
                                    <Download size={15} className="shrink-0 text-slate-400" />
                                </button>
                            ))}
                        </div>
                    </Section>

                    <Section title="История">
                        <History events={card.events} />
                    </Section>
                </div>
            )}
        </IosModal>
    );
}
