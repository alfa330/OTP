import React, { useEffect, useMemo, useState } from 'react';
import { Plus, Search, ShieldAlert, ShieldCheck, X } from 'lucide-react';
import { IosModal, IosSegmented } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import IosDatePicker from '../ui/DatePicker';
import { apiCode, apiError } from './disciplineApi';
import {
    Chip, dBtnPrimary, dBtnSecondary, dInput, EmptyState, Field, Group, INVALID_RING, LoadingRows, T,
} from './disciplineUi';
import { controlReason, daysLeftText, fmtDate, fmtDateTime, pluralViolations, shiftIsoDays, todayIso } from './disciplineRules';

/*
 * «Особый контроль» (ТЗ п. 4.4 и 10): полный список с историей — кто, когда,
 * по какой причине и с каким результатом, включая завершённые периоды.
 *
 * Три источника постановки выглядят в списке одинаково, отличается только
 * основание (п. 10.3): автоматически после акта, вручную решением руководителя,
 * испытательный срок нового сотрудника. Автоматического снятия нет (п. 10.5):
 * по окончании руководитель выбирает — снять, продлить на N дней, применить
 * меру или инициировать расторжение; пока решения нет, строка жёлтая.
 */

const ROW_TONES = { red: 'bg-rose-50', yellow: 'bg-amber-50', normal: '' };

const DECISIONS = [
    { value: 'removed', label: 'Снять' },
    { value: 'extended', label: 'Продлить' },
    { value: 'measure', label: 'Мера' },
    { value: 'termination', label: 'Расторжение' },
];

const DecisionPanel = ({ row, api, toast, measures, onDone }) => {
    const [decision, setDecision] = useState('removed');
    const [days, setDays] = useState('14');
    const [measureId, setMeasureId] = useState(null);
    const [amount, setAmount] = useState('');
    const [percent, setPercent] = useState('');
    const [comment, setComment] = useState('');
    const [error, setError] = useState(null);
    const [valueError, setValueError] = useState(null);
    const [busy, setBusy] = useState(false);
    const options = measures.filter((m) => m.department_id === row.department_id || !row.department_id);
    const measure = options.find((m) => m.id === measureId) || null;
    const save = async () => {
        if (decision === 'extended' && !(Number(days) >= 1 && Number(days) <= 365)) { setError('Продлить можно на 1–365 дней'); return; }
        if (decision === 'measure') {
            if (!measureId) { setError('Выберите меру'); return; }
            // Мера решением по контролю идёт в выгрузку на удержания — с суммой или процентом.
            if (measure?.value_kind === 'amount' && !(Number(String(amount).replace(',', '.').replace(/\s/g, '')) > 0)) {
                setValueError('Укажите сумму меры'); return;
            }
            if (measure?.value_kind === 'percent') {
                const value = Number(String(percent).replace(',', '.'));
                if (!(value > 0 && value <= 100)) { setValueError('Укажите процент от 1 до 100'); return; }
            }
        }
        setBusy(true);
        try {
            await api.decideControl(row.id, { decision, days: Number(days), measure_id: measureId, comment,
                measure_amount: amount, measure_percent: percent });
            toast(decision === 'extended' ? 'Контроль продлён' : 'Решение принято');
            onDone();
        } catch (err) {
            if (['MEASURE_AMOUNT', 'MEASURE_PERCENT'].includes(apiCode(err))) setValueError(apiError(err));
            else setError(apiError(err));
        } finally {
            setBusy(false);
        }
    };
    return (
        <div className="space-y-3 rounded-2xl bg-white p-3.5 ring-1 ring-slate-200/70">
            <IosSegmented value={decision} options={DECISIONS} onChange={(v) => { setDecision(v); setError(null); }} stretch slide
                          ariaLabel="Решение по контролю" />
            {decision === 'extended' && (
                <Field label="На сколько дней" error={error}>
                    <input inputMode="numeric" className={`${dInput} ${error ? INVALID_RING : ''}`} value={days}
                           onChange={(e) => { setDays(e.target.value.replace(/\D/g, '')); setError(null); }} />
                </Field>
            )}
            {decision === 'measure' && (
                <div className="grid gap-3 sm:grid-cols-[1fr_180px]">
                    <Field label="Мера" error={error}>
                        <CustomSelect variant="ios" value={measureId} placeholder="Выберите меру" invalid={!!error}
                                      options={options.map((m) => ({ value: m.id, label: m.title }))}
                                      onChange={(v) => { setMeasureId(v || null); setAmount(''); setPercent(''); setError(null); setValueError(null); }} />
                    </Field>
                    {measure?.value_kind === 'amount' && (
                        <Field label="Сумма, ₸" error={valueError}>
                            <input inputMode="decimal" className={`${dInput} ${valueError ? INVALID_RING : ''}`} value={amount}
                                   onChange={(e) => { setAmount(e.target.value); setValueError(null); }} />
                        </Field>
                    )}
                    {measure?.value_kind === 'percent' && (
                        <Field label="Процент" error={valueError}>
                            <input inputMode="decimal" className={`${dInput} ${valueError ? INVALID_RING : ''}`} value={percent}
                                   onChange={(e) => { setPercent(e.target.value); setValueError(null); }} />
                        </Field>
                    )}
                </div>
            )}
            <Field label="Комментарий">
                <input className={dInput} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Необязательно" />
            </Field>
            {error && decision !== 'extended' && decision !== 'measure' ? <div className="text-[12px] text-rose-600">{error}</div> : null}
            <div className="flex justify-end gap-2">
                <button type="button" className={dBtnSecondary} onClick={() => onDone(false)}>Отмена</button>
                <button type="button" className={dBtnPrimary} onClick={save} disabled={busy}>Сохранить решение</button>
            </div>
        </div>
    );
};

const NewControlForm = ({ open, api, toast, onClose, onCreated }) => {
    const [query, setQuery] = useState('');
    const [results, setResults] = useState([]);
    const [operator, setOperator] = useState(null);
    const [source, setSource] = useState('manual');
    const [reason, setReason] = useState('');
    const [start, setStart] = useState(todayIso());
    const [end, setEnd] = useState(shiftIsoDays(todayIso(), 30));
    const [errors, setErrors] = useState({});
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        if (!open) return;
        setQuery(''); setOperator(null); setSource('manual'); setReason(''); setStart(todayIso());
        setEnd(shiftIsoDays(todayIso(), 30)); setErrors({});
    }, [open]);

    useEffect(() => {
        if (!open || operator) return undefined;
        let alive = true;
        const timer = setTimeout(() => {
            api.searchOperators(query).then((data) => { if (alive) setResults(data.items || []); }).catch(() => {});
        }, 200);
        return () => { alive = false; clearTimeout(timer); };
    }, [api, open, query, operator]);

    const save = async () => {
        const next = {};
        if (!operator) next.operator = 'Выберите сотрудника';
        if (source === 'manual' && reason.trim().length < 3) next.reason = 'Укажите причину';
        if (!end) next.end = 'Укажите дату окончания';
        else if (end < start) next.end = 'Окончание раньше начала';
        setErrors(next);
        if (Object.keys(next).length) return;
        setBusy(true);
        try {
            await api.createControl({ operator_id: operator.id, source, reason, start_date: start, end_date: end });
            toast('Сотрудник поставлен на особый контроль');
            onCreated();
        } catch (err) {
            setErrors({ form: apiError(err) });
        } finally {
            setBusy(false);
        }
    };

    return (
        <IosModal open={open} onClose={onClose} title="Поставить на особый контроль" maxWidth="max-w-lg"
                  footer={(
                      <div className="flex justify-end gap-2">
                          <button type="button" className={dBtnSecondary} onClick={onClose}>Отмена</button>
                          <button type="button" className={dBtnPrimary} onClick={save} disabled={busy}>Поставить</button>
                      </div>
                  )}>
            <div className="space-y-4">
                <Field label="Сотрудник" error={errors.operator}>
                    {operator ? (
                        <div className="flex items-center justify-between gap-2 rounded-xl bg-slate-100 px-3.5 py-2.5">
                            <span className="truncate text-[14px] font-semibold text-slate-900">{operator.name}</span>
                            <button type="button" aria-label="Другой" onClick={() => setOperator(null)}><X size={15} /></button>
                        </div>
                    ) : (
                        <div className="space-y-2">
                            <label className="relative block">
                                <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                                <input className={`${dInput} pl-9 ${errors.operator ? INVALID_RING : ''}`} placeholder="ФИО"
                                       value={query} onChange={(e) => setQuery(e.target.value)} />
                            </label>
                            {results.length > 0 && (
                                <ul className="max-h-[220px] divide-y divide-slate-100 overflow-y-auto rounded-xl ring-1 ring-slate-200/70">
                                    {results.map((person) => (
                                        <li key={person.id}>
                                            <button type="button" className="w-full px-3.5 py-2.5 text-left hover:bg-slate-50"
                                                    onClick={() => { setOperator(person); setErrors((e) => ({ ...e, operator: null })); }}>
                                                <span className="block text-[13.5px] font-medium text-slate-900">{person.name}</span>
                                                <span className="block text-[12px] text-slate-500">{[person.group_name, person.department_name].filter(Boolean).join(' · ')}</span>
                                            </button>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </div>
                    )}
                </Field>
                <Field label="Основание"
                       hint="Испытательный срок — срок по договору нового сотрудника. Это не то же самое, что контроль после акта: основания разные, список общий.">
                    <IosSegmented value={source} onChange={setSource} stretch slide ariaLabel="Основание"
                                  options={[{ value: 'manual', label: 'Решение руководителя' }, { value: 'probation', label: 'Испытательный срок' }]} />
                </Field>
                <Field label="Причина" error={errors.reason}>
                    <input className={`${dInput} ${errors.reason ? INVALID_RING : ''}`} value={reason}
                           placeholder={source === 'probation' ? 'Испытательный срок нового сотрудника' : 'За что'}
                           onChange={(e) => { setReason(e.target.value); setErrors((x) => ({ ...x, reason: null })); }} />
                </Field>
                <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="Начало"><IosDatePicker value={start} onChange={setStart} /></Field>
                    <Field label="Окончание" error={errors.end}><IosDatePicker value={end} min={start} onChange={setEnd} /></Field>
                </div>
                {errors.form && <div className="rounded-xl bg-rose-50 px-3.5 py-2.5 text-[12.5px] text-rose-700">{errors.form}</div>}
            </div>
        </IosModal>
    );
};

const ControlsPanel = ({ api, toast, departmentId, version, reload, openOperator, caps }) => {
    const [state, setState] = useState('active');
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    const [deciding, setDeciding] = useState(null);
    const [createOpen, setCreateOpen] = useState(false);

    useEffect(() => {
        let alive = true;
        api.controls(state)
            .then((payload) => { if (alive) { setData(payload); setError(null); } })
            .catch((err) => { if (alive) setError(apiError(err, 'Список не загрузился')); });
        return () => { alive = false; };
    }, [api, state, version]);

    const rows = useMemo(() => (data?.items || []).filter((row) => !departmentId || row.department_id === departmentId
        || row.department_id == null), [data, departmentId]);

    return (
        <div className="space-y-3 xl:space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <IosSegmented value={state} onChange={setState} slide ariaLabel="Какие показать"
                              options={[{ value: 'active', label: 'Действующие' }, { value: 'closed', label: 'Завершённые' }]} />
                {caps.can_controls && (
                    <button type="button" className={dBtnSecondary} onClick={() => setCreateOpen(true)}>
                        <Plus size={15} />
                        Поставить на контроль
                    </button>
                )}
            </div>

            {error ? <Group><EmptyState title={error} /></Group> : !data ? <LoadingRows rows={5} /> : !rows.length ? (
                <Group>
                    <EmptyState icon={state === 'active' ? ShieldCheck : ShieldAlert}
                                title={state === 'active' ? 'На особом контроле никого нет' : 'Завершённых периодов нет'} />
                </Group>
            ) : (
                <div className="overflow-hidden rounded-2xl bg-white ring-1 ring-slate-200/70">
                    <ul className="divide-y divide-slate-100">
                        {rows.map((row) => (
                            <li key={row.id} className={ROW_TONES[row.tone] || ''}>
                                <div className="grid gap-2 px-4 py-3 sm:grid-cols-[minmax(0,1.2fr)_minmax(0,1.6fr)_auto] sm:items-center sm:gap-4 xl:px-5 2xl:py-4">
                                    <button type="button" className="min-w-0 text-left" onClick={() => openOperator(row.operator_id)}>
                                        <span className={`block truncate text-[13.5px] font-semibold text-slate-900 hover:text-blue-700 ${T.body}`}>{row.operator_name}</span>
                                        <span className={`block truncate text-[12px] text-slate-500 ${T.note}`}>
                                            {[row.group_name, row.direction_name].filter(Boolean).join(' · ') || 'Без группы'}
                                        </span>
                                    </button>
                                    <span className="min-w-0">
                                        <span className={`block text-[12.5px] text-slate-700 ${T.sub}`}>
                                            {controlReason(row)}
                                        </span>
                                        <span className={`block text-[12px] tabular-nums text-slate-500 ${T.note}`}>
                                            {fmtDate(row.start_date)} – {fmtDate(row.end_date)}
                                            {row.responsible_name ? ` · ответственный: ${row.responsible_name}` : ''}
                                            {row.decided_at ? ` · ${row.decision_label} ${fmtDateTime(row.decided_at)}${row.decision_detail ? ` — ${row.decision_detail}` : ''}` : ''}
                                        </span>
                                    </span>
                                    <span className="flex flex-wrap items-center gap-1.5 sm:justify-end">
                                        {row.violations > 0 && <Chip tone="red">{row.violations} {pluralViolations(row.violations)}</Chip>}
                                        {!row.decided_at && <Chip tone={row.tone === 'normal' ? 'slate' : 'amber'}>{daysLeftText(row.days_left)}</Chip>}
                                        {row.can_decide && deciding !== row.id && (
                                            <button type="button" className={`${dBtnSecondary} !px-3 !py-1.5`} onClick={() => setDeciding(row.id)}>
                                                Решение
                                            </button>
                                        )}
                                    </span>
                                </div>
                                {deciding === row.id && (
                                    <div className="px-4 pb-3 xl:px-5">
                                        <DecisionPanel row={row} api={api} toast={toast} measures={data.measures || []}
                                                       onDone={(changed = true) => { setDeciding(null); if (changed) reload(); }} />
                                    </div>
                                )}
                            </li>
                        ))}
                    </ul>
                </div>
            )}

            <NewControlForm open={createOpen} api={api} toast={toast} onClose={() => setCreateOpen(false)}
                            onCreated={() => { setCreateOpen(false); reload(); }} />
        </div>
    );
};

export default ControlsPanel;
