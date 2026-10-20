import React, { useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, Download, FileSignature, Lock, Search, ShieldAlert, X } from 'lucide-react';
import { IosMenu } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import IosDatePicker from '../ui/DatePicker';
import { IosTimePicker } from '../ui/TimePicker';
import { apiCode, apiError, saveBlob } from './disciplineApi';
import {
    ACCEPT_ACT, ACCEPT_DOCS, ACCEPT_EVIDENCE, AttachButton, Chip, dBtnDanger, dBtnPrimary, dBtnSecondary, dInput,
    EmptyState, Field, FileList, Group, INVALID_RING, LoadingRows, Spinner, StateBadge, StepChip, T,
} from './disciplineUi';
import {
    CARD_STATES_BESIDE_STEP, DESCRIPTION_MIN, filesProblem, fmtDate, fmtDateTime, fmtMoney, FILE_LIMITS,
    localInputValue, momentProblem, ordinalWord, shiftProblem, splitMoment, timecodesProblem, todayIso,
} from './disciplineRules';
import { MomentField, ScheduleShiftLink, ShiftField, TimecodeField } from './TimeFields';

/*
 * Карточка нарушения (ТЗ п. 6.3, 8, 9, 10.4). Всё, что делают с записью после
 * фиксации, — здесь, по порядку жизни записи: объяснительная → тренинг или акт
 * → мера → закрытие. Каждая секция показывается только тогда, когда она нужна
 * этой записи на её ступени лестницы, — пустые секции были бы шумом.
 *
 * Права приходят с сервера (permissions) — кнопки рисуются по ним, а граница
 * всё равно держится на сервере.
 *
 * Карточка — уровень окна-стопки раздела (DisciplineView): useRecordSheet
 * отдаёт шапку, подвал и тело, а окно одно на все уровни. Другая запись в
 * стопке — тело с чистого листа (key по записи): набранное на одной записи не
 * уйдёт в другую.
 */

const Line = ({ label, children }) => (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
        <span className={`shrink-0 text-[12.5px] text-slate-500 ${T.sub}`}>{label}</span>
        <span className={`min-w-0 text-right text-[13px] text-slate-800 ${T.sub}`}>{children}</span>
    </div>
);

/* Красная рамка вокруг пикера даты или времени: у них свой вид кнопки, и
   класс рамки заменил бы его целиком. */
const InvalidRing = ({ on, children }) => (
    <div className={on ? `w-fit rounded-xl ${INVALID_RING}` : undefined}>{children}</div>
);

/* Деньги меры одной строкой: сумма или процент. */
const measureValue = (amount, percent) => (amount ? fmtMoney(amount) : percent ? `${Number(percent)} %` : null);

/* Поле суммы или процента меры — по тому, чем мера выражена в справочнике. */
const MeasureValueField = ({ measure, amount, percent, onAmount, onPercent, errors }) => {
    if (measure?.value_kind === 'amount') {
        return (
            <Field label="Сумма, ₸" error={errors.amount} anchor="measure_amount">
                <input inputMode="decimal" className={`${dInput} ${errors.amount ? INVALID_RING : ''}`} value={amount}
                       onChange={(e) => onAmount(e.target.value)} />
            </Field>
        );
    }
    if (measure?.value_kind === 'percent') {
        return (
            <Field label="Процент" error={errors.percent} anchor="measure_percent">
                <input inputMode="decimal" className={`${dInput} ${errors.percent ? INVALID_RING : ''}`} value={percent}
                       onChange={(e) => onPercent(e.target.value)} />
            </Field>
        );
    }
    return null;
};

/* Сумма и процент — до отправки, теми же словами, что сервер. */
const measureValueErrors = (measure, amount, percent) => {
    const errors = {};
    if (measure?.value_kind === 'amount') {
        const value = Number(String(amount || '').replace(',', '.').replace(/\s/g, ''));
        if (!value || value <= 0) errors.amount = 'Укажите сумму меры';
    } else if (measure?.value_kind === 'percent') {
        const value = Number(String(percent || '').replace(',', '.'));
        if (!value || value <= 0 || value > 100) errors.percent = 'Укажите процент от 1 до 100';
    }
    return errors;
};

/* Ответ сервера — к своему полю: отказ по сумме встаёт под суммой, а не тостом. */
const serverFieldError = (err, map) => {
    const field = map[apiCode(err)];
    return field ? { [field]: apiError(err) } : null;
};

/* Признать файл недействительным (п. 13): файл не удаляется, остаётся в истории
   с пометкой и причиной. */
const InvalidateFile = ({ file, api, toast, reload, onDone, resets = false }) => {
    const [reason, setReason] = useState('');
    const [error, setError] = useState(null);
    const [busy, setBusy] = useState(false);
    const submit = async () => {
        if (reason.trim().length < 3) { setError('Укажите причину — хотя бы пару слов'); return; }
        setBusy(true);
        try {
            await api.invalidateFile(file.id, reason.trim());
            toast('Файл признан недействительным');
            onDone();
            reload();
        } catch (err) {
            setError(apiError(err, 'Не получилось'));
        } finally {
            setBusy(false);
        }
    };
    return (
        <div className="space-y-3 rounded-2xl bg-slate-50 p-3.5 motion-safe:animate-reveal">
            <Field label="Почему файл недействителен" error={error}
                   hint={resets
                       ? 'Файл не удаляется: он остаётся в истории зачёркнутым, с причиной. Если другой действующей версии нет, запись снова ждёт объяснительную.'
                       : 'Файл не удаляется: он остаётся в истории зачёркнутым, с причиной.'}>
                <input className={`${dInput} ${error ? INVALID_RING : ''}`} value={reason} autoFocus
                       placeholder={`«${file.name}»: не тот файл, нечитаемый скан…`}
                       onChange={(e) => { setReason(e.target.value); setError(null); }} />
            </Field>
            <div className="flex justify-end gap-2">
                <button type="button" className={dBtnSecondary} onClick={onDone}>Не менять</button>
                <button type="button" className={dBtnDanger} onClick={submit} disabled={busy}>Признать недействительным</button>
            </div>
        </div>
    );
};

/* Список файлов с действием «Недействителен» там, где сервер его разрешил. */
const RecordFiles = ({ files, api, toast, reload, onOpenFile, showKind = true, emptyText = null, recordOpen = false }) => {
    const [invalidating, setInvalidating] = useState(null);
    return (
        <div className="space-y-3">
            <FileList files={files} onOpen={onOpenFile} showKind={showKind} emptyText={emptyText}
                      onInvalidate={files.some((f) => f.can_invalidate) ? setInvalidating : null}
                      canInvalidate={(file) => Boolean(file.can_invalidate)} />
            {invalidating && (
                <InvalidateFile key={invalidating.id} file={invalidating} api={api} toast={toast} reload={reload}
                                onDone={() => setInvalidating(null)}
                                resets={recordOpen && ['explanation', 'refusal_act'].includes(invalidating.kind)} />
            )}
        </div>
    );
};

const ExplanationSection = ({ item, api, toast, reload, onOpenFile }) => {
    const [refusalOpen, setRefusalOpen] = useState(false);
    const [refusalFile, setRefusalFile] = useState(null);
    const [witnesses, setWitnesses] = useState([]);
    const [query, setQuery] = useState('');
    const [found, setFound] = useState([]);
    const [busy, setBusy] = useState(false);
    const [errors, setErrors] = useState({});
    const perms = item.permissions || {};
    const files = (item.files || []).filter((f) => f.kind === 'explanation' || f.kind === 'refusal_act');
    const done = item.explanation_received_at || item.refused_at;

    useEffect(() => {
        if (!refusalOpen || query.trim().length < 2) { setFound([]); return undefined; }
        let alive = true;
        const timer = setTimeout(() => {
            api.searchPeople(query).then((data) => { if (alive) setFound(data.items || []); }).catch(() => {});
        }, 200);
        return () => { alive = false; clearTimeout(timer); };
    }, [api, query, refusalOpen]);

    const upload = async (picked) => {
        const problem = filesProblem('explanation', picked.slice(0, 1));
        setErrors((e) => ({ ...e, upload: problem }));
        if (problem) return;
        setBusy(true);
        try {
            await api.uploadExplanation(item.id, picked[0]);
            toast('Объяснительная загружена');
            reload();
        } catch (err) {
            setErrors((e) => ({ ...e, upload: apiError(err, 'Файл не загрузился') }));
        } finally {
            setBusy(false);
        }
    };

    const saveRefusal = async () => {
        const next = {};
        if (!refusalFile) next.file = 'Приложите акт об отказе';
        if (witnesses.length < 2) next.witnesses = 'Нужно не меньше двух свидетелей';
        setErrors(next);
        if (Object.keys(next).length) return;
        setBusy(true);
        try {
            await api.refusal(item.id, refusalFile, witnesses.map((w) => w.id));
            toast('Отказ оформлен');
            setRefusalOpen(false);
            reload();
        } catch (err) {
            toast(apiError(err, 'Отказ не сохранился'), 'error');
        } finally {
            setBusy(false);
        }
    };

    let status;
    if (item.refused_at) status = <Chip tone="amber">Отказ от объяснения · {fmtDate(item.refused_at)}</Chip>;
    else if (item.explanation_received_at) status = <Chip tone="green">Получена {fmtDate(item.explanation_received_at)}</Chip>;
    else if (item.explanation_overdue) status = <Chip tone="red">Срок прошёл {fmtDateTime(item.explanation_due_at)}</Chip>;
    else status = <Chip tone="amber">Ждём до {fmtDateTime(item.explanation_due_at)}</Chip>;

    return (
        <Group title="Объяснительная" right={status}>
            <div className="space-y-3">
                {item.refusal_witnesses?.length ? (
                    <div className={`text-[12.5px] text-slate-600 ${T.sub}`}>Свидетели: {item.refusal_witnesses.join(', ')}</div>
                ) : null}
                <RecordFiles files={files} api={api} toast={toast} reload={reload} onOpenFile={onOpenFile}
                             emptyText={done ? null : 'Файла пока нет'}
                             recordOpen={!['closed', 'cancelled'].includes(item.state)} />
                {!item.refused_at && perms.can_upload_explanation && (
                    <div className="space-y-1">
                        <div className="flex flex-wrap items-center gap-3">
                            <AttachButton label={item.explanation_received_at ? 'Загрузить исправленную версию' : 'Загрузить объяснительную'}
                                          accept={ACCEPT_DOCS} onFiles={upload} disabled={busy} invalid={!!errors.upload} />
                            {!done && perms.can_work && !refusalOpen && (
                                <button type="button" className={`text-[12.5px] font-medium text-slate-500 hover:text-slate-800 ${T.sub}`}
                                        onClick={() => setRefusalOpen(true)}>
                                    Отказ от объяснения
                                </button>
                            )}
                            {busy && <Spinner />}
                        </div>
                        {errors.upload && <div className={`px-1 text-[12px] font-medium text-rose-600 ${T.note}`}>{errors.upload}</div>}
                    </div>
                )}
                {refusalOpen && (
                    <div className="space-y-3 rounded-2xl bg-slate-50 p-3.5">
                        <Field label="Акт об отказе" error={errors.file}
                               hint="Подписанный акт об отказе дать объяснение. Нарушение засчитывается в счётчик наравне с остальными, руководителю уходит уведомление.">
                            {refusalFile ? (
                                <div className="flex items-center gap-2 rounded-lg bg-white px-3 py-1.5 ring-1 ring-slate-200/70">
                                    <span className="min-w-0 flex-1 truncate text-[12.5px]">{refusalFile.name}</span>
                                    <button type="button" aria-label="Убрать" onClick={() => setRefusalFile(null)}><X size={14} /></button>
                                </div>
                            ) : (
                                <AttachButton label="Приложить акт" accept={ACCEPT_ACT} invalid={!!errors.file}
                                              onFiles={(picked) => {
                                                  const problem = filesProblem('refusal_act', picked.slice(0, 1));
                                                  if (!problem) setRefusalFile(picked[0]);
                                                  setErrors((e) => ({ ...e, file: problem }));
                                              }} />
                            )}
                        </Field>
                        <Field label="Свидетели" error={errors.witnesses}>
                            <div className="space-y-2">
                                {witnesses.length > 0 && (
                                    <div className="flex flex-wrap gap-1.5">
                                        {witnesses.map((w) => (
                                            <span key={w.id} className="inline-flex items-center gap-1 rounded-full bg-blue-50 px-2.5 py-1 text-[12px] text-blue-800 ring-1 ring-blue-100">
                                                {w.name}
                                                <button type="button" aria-label="Убрать" onClick={() => setWitnesses((prev) => prev.filter((x) => x.id !== w.id))}>
                                                    <X size={12} />
                                                </button>
                                            </span>
                                        ))}
                                    </div>
                                )}
                                <label className="relative block">
                                    <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                                    <input className={`${dInput} pl-8 ${errors.witnesses ? INVALID_RING : ''}`} placeholder="Найти сотрудника"
                                           value={query} onChange={(e) => setQuery(e.target.value)} />
                                </label>
                                {found.length > 0 && (
                                    <ul className="max-h-[180px] divide-y divide-slate-100 overflow-y-auto rounded-xl bg-white ring-1 ring-slate-200/70">
                                        {found.filter((p) => p.id !== item.operator_id && !witnesses.some((w) => w.id === p.id)).map((p) => (
                                            <li key={p.id}>
                                                <button type="button" className="w-full px-3 py-2 text-left text-[12.5px] hover:bg-slate-50"
                                                        onClick={() => { setWitnesses((prev) => [...prev, p]); setQuery(''); setErrors((e) => ({ ...e, witnesses: null })); }}>
                                                    <span className="font-medium text-slate-800">{p.name}</span>
                                                    {p.department_name ? <span className="text-slate-400"> · {p.department_name}</span> : null}
                                                </button>
                                            </li>
                                        ))}
                                    </ul>
                                )}
                            </div>
                        </Field>
                        <div className="flex justify-end gap-2">
                            <button type="button" className={dBtnSecondary} onClick={() => setRefusalOpen(false)}>Отмена</button>
                            <button type="button" className={dBtnPrimary} onClick={saveRefusal} disabled={busy}>Оформить отказ</button>
                        </div>
                    </div>
                )}
            </div>
        </Group>
    );
};

const TrainingSection = ({ item, api, toast, reload }) => {
    const [form, setForm] = useState({ date: todayIso(), start: '', end: '' });
    const [errors, setErrors] = useState({});
    const [busy, setBusy] = useState(false);
    if (item.training) {
        return (
            <Group title="Тренинг" right={<Chip tone="green">Проведён</Chip>}>
                <div className={`text-[13px] text-slate-700 ${T.sub}`}>
                    {fmtDate(item.training.date)}, {item.training.start}–{item.training.end} · «Дисциплинарный тренинг» записан в «Тренинги»
                </div>
            </Group>
        );
    }
    // «Провести тренинг» уже сказано ступенью в шапке — у секции чип не повторяем.
    if (!item.permissions?.can_work) {
        return <Group title="Тренинг"><div className="text-[13px] text-slate-500">Тренинг ещё не отмечен.</div></Group>;
    }
    const change = (patch) => {
        setForm((f) => ({ ...f, ...patch }));
        setErrors((prev) => {
            const next = { ...prev };
            Object.keys(patch).forEach((key) => { delete next[key]; });
            return next;
        });
    };
    const save = async () => {
        const next = {};
        if (!form.date) next.date = 'Укажите дату';
        if (!form.start) next.start = 'Укажите начало';
        if (!form.end) next.end = 'Укажите конец';
        if (form.start && form.end && form.end <= form.start) next.end = 'Конец позже начала';
        setErrors(next);
        if (Object.keys(next).length) return;
        setBusy(true);
        try {
            await api.training(item.id, form);
            toast('Тренинг записан в «Тренинги»');
            reload();
        } catch (err) {
            toast(apiError(err, 'Тренинг не сохранился'), 'error');
        } finally {
            setBusy(false);
        }
    };
    return (
        <Group title="Тренинг"
               hint="Занятие ляжет в «Тренинги» с причиной «Дисциплинарный тренинг» и пойдёт в часы, как любой тренинг.">
            <div className="grid gap-3 sm:grid-cols-[1.4fr_1fr_1fr_auto] sm:items-start">
                <Field label="Дата" error={errors.date}>
                    <InvalidRing on={!!errors.date}>
                        <IosDatePicker value={form.date} max={todayIso()} onChange={(value) => change({ date: value })} />
                    </InvalidRing>
                </Field>
                <Field label="С" error={errors.start}>
                    <InvalidRing on={!!errors.start}>
                        <IosTimePicker value={form.start} onChange={(value) => change({ start: value })} />
                    </InvalidRing>
                </Field>
                <Field label="До" error={errors.end}>
                    <InvalidRing on={!!errors.end}>
                        <IosTimePicker value={form.end} onChange={(value) => change({ end: value })} />
                    </InvalidRing>
                </Field>
                <button type="button" className={`${dBtnPrimary} sm:mt-[22px]`} onClick={save} disabled={busy}>Тренинг проведён</button>
            </div>
        </Group>
    );
};

const ACT_FIELD_CODES = {
    ACT_COMPOSED_ON: 'composed_on', DECISION_MEASURE: 'decision', MEASURE_AMOUNT: 'amount', MEASURE_PERCENT: 'percent',
};

const ActSection = ({ item, api, toast, reload, onOpenFile, onOpenRecord, measures }) => {
    const act = item.act_full;
    const [busy, setBusy] = useState(false);
    const blank = () => ({
        composed_on: act?.composed_on || '', decision_measure_id: act?.decision_measure_id || null,
        decision_amount: act?.decision_amount ? String(Number(act.decision_amount)) : '',
        decision_percent: act?.decision_percent ? String(Number(act.decision_percent)) : '',
    });
    const [form, setForm] = useState(blank);
    const [errors, setErrors] = useState({});
    useEffect(() => { setForm(blank()); setErrors({}); }, [act?.composed_on, act?.decision_measure_id, act?.decision_amount, act?.decision_percent]); // eslint-disable-line react-hooks/exhaustive-deps
    if (!act) return null;
    const manage = item.permissions?.can_manage;
    const signed = (act.files || []).some((f) => f.kind === 'act' && !f.superseded && !f.invalid);
    const decision = measures.find((m) => m.id === form.decision_measure_id) || null;

    const change = (patch, field) => {
        setForm((f) => ({ ...f, ...patch }));
        if (field) setErrors((e) => ({ ...e, [field]: null }));
    };
    const draft = async () => {
        setBusy(true);
        try {
            saveBlob(await api.actDraft(act.id), `Акт_${act.number}.docx`);
        } catch (err) {
            toast(apiError(err, 'Черновик не собрался'), 'error');
        } finally {
            setBusy(false);
        }
    };
    const upload = async (picked) => {
        const problem = filesProblem('act', picked.slice(0, 1));
        setErrors((e) => ({ ...e, file: problem }));
        if (problem) return;
        setBusy(true);
        try {
            await api.uploadAct(act.id, picked[0]);
            toast('Акт приложен');
            reload();
        } catch (err) {
            setErrors((e) => ({ ...e, file: apiError(err, 'Файл не загрузился') }));
        } finally {
            setBusy(false);
        }
    };
    const save = async (approve) => {
        // Утвердить можно с датой составления и решением (п. 9); сумма или процент
        // нужны, если мера ими выражена, — и при простом сохранении тоже.
        const next = { ...measureValueErrors(decision, form.decision_amount, form.decision_percent) };
        if (approve && !form.composed_on) next.composed_on = 'Укажите дату составления';
        if (approve && !form.decision_measure_id) next.decision = 'Выберите решение по акту';
        setErrors(next);
        if (Object.values(next).some(Boolean)) return;
        setBusy(true);
        try {
            await api.updateAct(act.id, { ...form, ...(approve ? { approve: true } : {}) });
            toast(approve ? 'Акт утверждён' : 'Сохранено');
            reload();
        } catch (err) {
            const field = serverFieldError(err, ACT_FIELD_CODES);
            if (field) setErrors((e) => ({ ...e, ...field }));
            else toast(apiError(err), 'error');
        } finally {
            setBusy(false);
        }
    };

    const approvedValue = measureValue(act.decision_amount, act.decision_percent);
    return (
        <Group title={`Акт ${act.number}`}
               right={<Chip tone={signed ? 'blue' : 'red'}>{act.approved_at ? 'Утверждён' : signed ? 'Подписан' : 'Черновик'}</Chip>}>
            <div className="space-y-3">
                {act.basis_check && (
                    <div className="flex items-start gap-2 rounded-xl bg-amber-50 px-3 py-2.5 text-[12.5px] text-amber-900 ring-1 ring-amber-200/70">
                        <AlertTriangle size={15} className="mt-0.5 shrink-0" />
                        <span>Проверьте основание акта: после отмены или правки записей нарушений за период стало меньше порога.</span>
                    </div>
                )}
                <div className={`text-[12.5px] text-slate-500 ${T.sub}`}>Записи периода, на которых основан акт:</div>
                <ul className="flex flex-wrap gap-1.5">
                    {(act.records || []).map((record) => (
                        <li key={record.id}>
                            {record.hidden ? (
                                <span className="inline-flex items-center gap-1 rounded-full bg-slate-50 px-2.5 py-1 text-[12px] text-slate-400"
                                      title="Запись вне вашего доступа">
                                    <Lock size={11} />
                                    {record.number} · {fmtDate(record.occurred_at)}
                                </span>
                            ) : (
                                <button type="button" onClick={() => onOpenRecord(record.id)}
                                        className="rounded-full bg-slate-100 px-2.5 py-1 text-[12px] text-slate-700 hover:bg-slate-200">
                                    {record.number} · {fmtDate(record.occurred_at)}
                                </button>
                            )}
                        </li>
                    ))}
                </ul>
                <RecordFiles files={act.files || []} api={api} toast={toast} reload={reload} onOpenFile={onOpenFile} showKind={false} />
                <div className="space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                        <button type="button" className={dBtnSecondary} onClick={draft} disabled={busy}>
                            <Download size={15} />
                            Черновик DOCX
                        </button>
                        {manage && (
                            <AttachButton label={signed ? 'Заменить подписанный акт' : 'Приложить подписанный акт'} accept={ACCEPT_ACT}
                                          onFiles={upload} disabled={busy} invalid={!!errors.file} />
                        )}
                        {busy && <Spinner />}
                    </div>
                    {errors.file && <div className={`px-1 text-[12px] font-medium text-rose-600 ${T.note}`}>{errors.file}</div>}
                </div>
                {act.approved_at && act.decision_title ? (
                    <div className={`text-[13px] text-slate-700 ${T.sub}`}>
                        Решение: <span className="font-semibold text-slate-900">{act.decision_title}</span>
                        {approvedValue ? <span className="text-slate-600"> · {approvedValue}</span> : null}
                    </div>
                ) : null}
                {act.approved_at ? (
                    <div className={`text-[12.5px] text-slate-500 ${T.sub}`}>
                        Утвердил {act.approved_by_name}, {fmtDate(act.approved_at)} · утверждённый акт не меняется
                    </div>
                ) : null}
                {manage && !act.approved_at && (
                    <div className="grid gap-3 rounded-2xl bg-slate-50 p-3.5 sm:grid-cols-2">
                        <Field label="Дата составления" error={errors.composed_on}>
                            <InvalidRing on={!!errors.composed_on}>
                                <IosDatePicker value={form.composed_on || ''} allowEmpty
                                               onChange={(value) => change({ composed_on: value }, 'composed_on')} />
                            </InvalidRing>
                        </Field>
                        <Field label="Решение (мера)" error={errors.decision}>
                            <CustomSelect variant="ios" value={form.decision_measure_id} placeholder="Выберите меру" invalid={!!errors.decision}
                                          options={measures.map((m) => ({ value: m.id, label: m.title }))}
                                          onChange={(value) => {
                                              change({ decision_measure_id: value || null, decision_amount: '', decision_percent: '' }, 'decision');
                                              setErrors((e) => ({ ...e, amount: null, percent: null }));
                                          }} />
                        </Field>
                        <MeasureValueField measure={decision} amount={form.decision_amount} percent={form.decision_percent}
                                           errors={errors}
                                           onAmount={(value) => change({ decision_amount: value }, 'amount')}
                                           onPercent={(value) => change({ decision_percent: value }, 'percent')} />
                        <div className="flex flex-wrap items-center justify-end gap-2 sm:col-span-2">
                            <button type="button" className={dBtnSecondary} disabled={busy} onClick={() => save(false)}>
                                Сохранить
                            </button>
                            <button type="button" className={dBtnPrimary} disabled={busy} onClick={() => save(true)}>
                                <FileSignature size={15} />
                                Утвердить
                            </button>
                        </div>
                    </div>
                )}
            </div>
        </Group>
    );
};

const MEASURE_FIELD_CODES = { MEASURE_AMOUNT: 'amount', MEASURE_PERCENT: 'percent' };

const MeasureSection = ({ item, api, toast, reload, measures }) => {
    const perms = item.permissions || {};
    const [form, setForm] = useState({ measure_id: null, measure_amount: '', measure_percent: '' });
    const [busy, setBusy] = useState(false);
    const [errors, setErrors] = useState({});
    // Мера на записи без контроля не обязательна — поле прячется за кнопку.
    const [open, setOpen] = useState(false);
    const selected = measures.find((m) => m.id === form.measure_id) || null;
    const show = item.under_control || item.measure_title || perms.can_manage;
    if (!show) return null;
    if (!item.under_control && !item.measure_title && !open) {
        if (!perms.can_propose_measure) return null;
        return (
            <div className="flex justify-start">
                <button type="button" className={`${dBtnSecondary} !py-2`} onClick={() => setOpen(true)}>Назначить меру</button>
            </div>
        );
    }

    const save = async () => {
        const next = selected ? measureValueErrors(selected, form.measure_amount, form.measure_percent)
            : { measure: 'Выберите меру' };
        setErrors(next);
        if (Object.values(next).some(Boolean)) return;
        setBusy(true);
        try {
            await api.measure(item.id, form);
            toast(perms.can_manage ? 'Мера назначена' : 'Мера предложена руководителю');
            setForm({ measure_id: null, measure_amount: '', measure_percent: '' });
            reload();
        } catch (err) {
            setErrors(serverFieldError(err, MEASURE_FIELD_CODES) || { measure: apiError(err) });
        } finally {
            setBusy(false);
        }
    };
    const approve = async () => {
        setBusy(true);
        try {
            await api.approveMeasure(item.id);
            toast('Мера утверждена');
            reload();
        } catch (err) {
            toast(apiError(err), 'error');
        } finally {
            setBusy(false);
        }
    };

    const value = measureValue(item.measure_amount, item.measure_percent);
    // Кто и что может поменять, решает сервер: утверждённое удержание — никто,
    // утверждённую руководителем меру — только он, применённую без утверждения — и СВ.
    const canChange = Boolean(perms.can_propose_measure);
    return (
        <Group title="Мера" right={item.measure_state === 'approved' ? <Chip tone="blue">{item.measure_approved_by_name ? 'Утверждена' : 'Применена'}</Chip>
            : item.measure_state === 'proposed' ? <Chip tone="amber">На утверждении</Chip>
                : item.under_control ? <Chip tone="red">Обязательна</Chip> : null}>
            <div className="space-y-3">
                {item.measure_title ? (
                    <div className="space-y-0.5">
                        <div className={`text-[14px] font-semibold text-slate-900 ${T.body}`}>
                            {item.measure_title}{value ? <span className="font-normal text-slate-600"> · {value}</span> : null}
                        </div>
                        <div className={`text-[12px] text-slate-500 ${T.note}`}>
                            {item.measure_state === 'approved'
                                ? (item.measure_approved_by_name
                                    ? `Утвердил ${item.measure_approved_by_name}, ${fmtDate(item.measure_approved_at)}`
                                    : `Применена без утверждения, ${fmtDate(item.measure_approved_at)}`)
                                : `Предложил ${item.measure_proposed_by_name || ''}`}
                        </div>
                    </div>
                ) : null}
                {item.measure_state === 'proposed' && perms.can_approve_measure && (
                    <button type="button" className={dBtnPrimary} onClick={approve} disabled={busy}>Утвердить меру</button>
                )}
                {canChange && (
                    <div className="grid gap-3 sm:grid-cols-[1fr_180px_auto] sm:items-start">
                        <Field label={item.measure_title ? 'Другая мера' : (perms.can_manage ? 'Назначить меру' : 'Предложить меру')} error={errors.measure}>
                            <CustomSelect variant="ios" value={form.measure_id} placeholder="Выберите меру" invalid={!!errors.measure}
                                          options={measures.map((m) => ({ value: m.id, label: m.title }))}
                                          onChange={(v) => { setForm({ measure_id: v || null, measure_amount: '', measure_percent: '' }); setErrors({}); }} />
                        </Field>
                        <MeasureValueField measure={selected} amount={form.measure_amount} percent={form.measure_percent} errors={errors}
                                           onAmount={(v) => { setForm((f) => ({ ...f, measure_amount: v })); setErrors((e) => ({ ...e, amount: null })); }}
                                           onPercent={(v) => { setForm((f) => ({ ...f, measure_percent: v })); setErrors((e) => ({ ...e, percent: null })); }} />
                        {!selected?.value_kind || selected.value_kind === 'none' ? <span className="hidden sm:block" /> : null}
                        <button type="button" className={`${dBtnSecondary} sm:mt-[22px]`} onClick={save} disabled={busy}>
                            {perms.can_manage ? 'Назначить' : 'Предложить'}
                        </button>
                    </div>
                )}
            </div>
        </Group>
    );
};

const EDIT_FIELD_CODES = {
    OCCURRED_AT: 'occurred_at', TYPE_ID: 'type_id', OTHER_TITLE: 'other_title', DESCRIPTION: 'description',
    SHIFT_REQUIRED: 'shift_label', SHIFT_FORMAT: 'shift_label', EVIDENCE_NOTE: 'evidence_note',
};

const EditForm = ({ item, api, toast, onDone }) => {
    const originalMoment = localInputValue(new Date(String(item.occurred_at).replace(' ', 'T')));
    const [form, setForm] = useState({
        occurred_at: originalMoment, type_id: item.type_id, other_title: '', description: item.description || '',
        shift_label: item.shift_label || '', shift_touched: false, evidence_note: item.evidence_note || '',
    });
    // Типы — отдела самой записи (приходят с записью).
    const types = item.types || [];
    const [scheduleShift, setScheduleShift] = useState(null);
    const [busy, setBusy] = useState(false);
    const [errors, setErrors] = useState({});
    // Новая дата — смена из графика на неё: подставляется, пока смену не правили руками.
    useEffect(() => {
        if (!form.occurred_at || form.occurred_at === originalMoment) { setScheduleShift(null); return undefined; }
        let alive = true;
        const timer = setTimeout(() => {
            api.operatorContext({ operator_id: item.operator_id, at: form.occurred_at })
                .then((data) => {
                    if (!alive) return;
                    setScheduleShift(data.shift_label || null);
                    if (data.shift_label) {
                        setForm((f) => (f.shift_touched ? f : { ...f, shift_label: data.shift_label }));
                    }
                })
                .catch(() => { if (alive) setScheduleShift(null); });
        }, 250);
        return () => { alive = false; clearTimeout(timer); };
    }, [api, item.operator_id, form.occurred_at]); // eslint-disable-line react-hooks/exhaustive-deps
    const selected = types.find((t) => t.id === form.type_id);
    const change = (patch) => {
        setForm((f) => ({ ...f, ...patch }));
        setErrors((prev) => {
            const next = { ...prev };
            Object.keys(patch).forEach((key) => { delete next[key]; });
            return next;
        });
    };
    const save = async () => {
        const next = {};
        const moment = momentProblem(form.occurred_at);
        if (moment) next.occurred_at = moment;
        // Смену и тайм-код проверяем, только если их правили: прежняя запись
        // свободным текстом сохраняется как была.
        if (!form.shift_label.trim()) next.shift_label = 'Укажите смену';
        else if (form.shift_label !== (item.shift_label || '') && shiftProblem(form.shift_label)) {
            next.shift_label = shiftProblem(form.shift_label);
        }
        if (form.evidence_note !== (item.evidence_note || '') && timecodesProblem(form.evidence_note)) {
            next.evidence_note = timecodesProblem(form.evidence_note);
        }
        if (form.description.trim().length < DESCRIPTION_MIN) next.description = `Описание — минимум ${DESCRIPTION_MIN} символов`;
        setErrors(next);
        if (Object.keys(next).length) return;
        setBusy(true);
        try {
            const body = { occurred_at: form.occurred_at, description: form.description, shift_label: form.shift_label,
                evidence_note: form.evidence_note };
            if (form.type_id !== item.type_id || selected?.is_other) {
                body.type_id = form.type_id;
                if (selected?.is_other) body.other_title = form.other_title || item.type_title;
            }
            await api.editRecord(item.id, body);
            toast('Запись изменена');
            onDone(true);
        } catch (err) {
            const field = serverFieldError(err, EDIT_FIELD_CODES);
            if (field) setErrors(field);
            else toast(apiError(err), 'error');
        } finally {
            setBusy(false);
        }
    };
    return (
        <Group title="Изменить запись">
            <div className="space-y-3">
                <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="Дата и время" error={errors.occurred_at}>
                        <MomentField value={form.occurred_at} invalid={!!errors.occurred_at}
                                     onChange={(value) => change({ occurred_at: value })} />
                    </Field>
                    <div className="space-y-1.5">
                        <Field label="Смена" error={errors.shift_label}>
                            <ShiftField value={form.shift_label} invalid={!!errors.shift_label}
                                        onChange={(value) => change({ shift_label: value, shift_touched: true })} />
                        </Field>
                        {form.shift_touched && scheduleShift && scheduleShift !== form.shift_label.trim() ? (
                            <ScheduleShiftLink label={scheduleShift}
                                               onPick={() => change({ shift_label: scheduleShift, shift_touched: false })} />
                        ) : null}
                    </div>
                </div>
                <Field label="Тип нарушения" error={errors.type_id}>
                    <CustomSelect variant="ios" value={form.type_id} options={types.map((t) => ({ value: t.id, label: t.title }))}
                                  invalid={!!errors.type_id} onChange={(v) => change({ type_id: v })} searchable />
                </Field>
                {selected?.is_other && (
                    <Field label="Название нарушения" error={errors.other_title}>
                        <input className={`${dInput} ${errors.other_title ? INVALID_RING : ''}`} value={form.other_title || item.type_title}
                               onChange={(e) => change({ other_title: e.target.value })} />
                    </Field>
                )}
                <Field label="Описание обстоятельств" error={errors.description}>
                    <textarea rows={4} className={`${dInput} resize-y ${errors.description ? INVALID_RING : ''}`} value={form.description}
                              onChange={(e) => change({ description: e.target.value })} />
                </Field>
                <Field label="Тайм-код записи экрана" error={errors.evidence_note}
                       hint="Время на записи экрана в день нарушения: с какого момента смотреть и до какого. Конец можно не указывать; фрагментов — до пяти.">
                    <TimecodeField value={form.evidence_note} invalid={!!errors.evidence_note}
                                   defaultTime={splitMoment(form.occurred_at).time || '09:00'}
                                   onChange={(value) => change({ evidence_note: value })} />
                </Field>
                <div className="flex justify-end gap-2">
                    <button type="button" className={dBtnSecondary} onClick={() => onDone(false)}>Отмена</button>
                    <button type="button" className={dBtnPrimary} onClick={save} disabled={busy}>Сохранить</button>
                </div>
            </div>
        </Group>
    );
};

const EvidenceBlock = ({ item, api, toast, reload, onOpenFile }) => {
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);
    const evidence = (item.files || []).filter((f) => f.kind === 'evidence');
    const live = evidence.filter((f) => !f.invalid && !f.superseded).length;
    const limit = FILE_LIMITS.evidence.maxFiles;
    const add = async (picked) => {
        const problem = filesProblem('evidence', picked, live);
        setError(problem);
        if (problem) return;
        setBusy(true);
        try {
            await api.addEvidence(item.id, picked);
            toast(picked.length > 1 ? 'Доказательства добавлены' : 'Доказательство добавлено');
            reload();
        } catch (err) {
            setError(apiError(err, 'Файл не загрузился'));
        } finally {
            setBusy(false);
        }
    };
    return (
        <div className="space-y-3">
            <RecordFiles files={evidence} api={api} toast={toast} reload={reload} onOpenFile={onOpenFile} showKind={false} />
            {item.permissions?.can_work && (live < limit ? (
                <div className="space-y-1">
                    <div className="flex items-center gap-3">
                        <AttachButton label="Добавить доказательство" multiple accept={ACCEPT_EVIDENCE} onFiles={add}
                                      disabled={busy} invalid={!!error} />
                        {busy && <Spinner />}
                    </div>
                    {error && <div className={`px-1 text-[12px] font-medium text-rose-600 ${T.note}`}>{error}</div>}
                </div>
            ) : (
                <div className={`text-[12px] text-slate-400 ${T.note}`}>Доказательств {limit} из {limit} — больше не добавить</div>
            ))}
        </div>
    );
};

export const useRecordSheet = ({ recordId, open = true, api, toast, onChanged, onOpenOperator, onOpenFile, onOpenRecord }) => {
    const [item, setItem] = useState(null);
    const [error, setError] = useState(null);
    const [version, setVersion] = useState(0);
    const [mode, setMode] = useState('view');
    const [cancelReason, setCancelReason] = useState('');
    const [cancelError, setCancelError] = useState(null);
    const [blockersShown, setBlockersShown] = useState(false);
    const [busy, setBusy] = useState(false);
    const [comment, setComment] = useState('');
    const [commentOpen, setCommentOpen] = useState(false);
    const [commentBusy, setCommentBusy] = useState(false);

    // Другая запись — с чистого листа: прежняя не мелькает с рабочими кнопками.
    // Уход на уровень сотрудника запись не сбрасывает: «назад» вернёт её сразу,
    // свежая подгрузится поверх.
    const lastRecord = useRef(null);
    // Окно закрыли совсем — следующее открытие с чистого листа (не в режиме правки).
    useEffect(() => { if (!open) lastRecord.current = null; }, [open]);
    useEffect(() => {
        if (!recordId || lastRecord.current === recordId) return;
        lastRecord.current = recordId;
        setItem((prev) => (prev && prev.id === recordId ? prev : null));
        setError(null);
        setMode('view');
        setBlockersShown(false);
        setCancelReason('');
        setCancelError(null);
        setCommentOpen(false);
    }, [recordId]);

    useEffect(() => {
        if (!recordId) return undefined;
        let alive = true;
        api.record(recordId)
            .then((data) => {
                if (!alive) return;
                setItem(data);
                setError(null);
                setComment(data.manager_comment || '');
            })
            .catch((err) => { if (alive) setError(apiError(err, 'Запись не открылась')); });
        return () => { alive = false; };
    }, [api, recordId, version]);

    // Пока ответ по новой записи не пришёл, старую не показываем.
    const shown = item && item.id === recordId ? item : null;

    // Меры — отдела самой записи (приходят с записью): для «Решения» акта и меры на контроле.
    const measures = shown?.measures || [];

    const reload = () => { setVersion((v) => v + 1); onChanged?.(); };
    const perms = shown?.permissions || {};
    const internal = Boolean(shown && 'description' in shown);

    const close = async () => {
        if (shown.close_blockers?.length) { setBlockersShown(true); return; }
        setBusy(true);
        try {
            await api.closeRecord(shown.id);
            toast('Запись закрыта');
            reload();
        } catch (err) {
            toast(apiError(err), 'error');
        } finally {
            setBusy(false);
        }
    };
    const cancel = async () => {
        if (cancelReason.trim().length < 5) { setCancelError('Причина — минимум 5 символов'); return; }
        setBusy(true);
        try {
            await api.cancelRecord(shown.id, cancelReason);
            toast('Запись отменена');
            setMode('view');
            reload();
        } catch (err) {
            setCancelError(apiError(err));
        } finally {
            setBusy(false);
        }
    };
    const saveComment = async () => {
        setCommentBusy(true);
        try {
            await api.comment(shown.id, comment);
            toast('Комментарий сохранён');
            reload();
        } catch (err) {
            toast(apiError(err), 'error');
        } finally {
            setCommentBusy(false);
        }
    };

    const menuItems = useMemo(() => [
        perms.can_edit ? { key: 'edit', label: 'Изменить запись', onSelect: () => setMode('edit') } : null,
        perms.can_cancel ? { key: 'cancel', label: 'Отменить запись', danger: true, onSelect: () => setMode('cancel') } : null,
    ].filter(Boolean), [perms.can_edit, perms.can_cancel]);

    const finished = shown && (shown.state === 'closed' || shown.state === 'cancelled');
    const footer = shown && !finished && (perms.can_close || menuItems.length) ? (
        <div className="w-full space-y-2">
            {blockersShown && shown.close_blockers?.length > 0 && (
                <ul className="space-y-1 rounded-xl bg-rose-50 px-3.5 py-2.5 text-[12.5px] text-rose-700 ring-1 ring-rose-200/70">
                    {shown.close_blockers.map((b) => <li key={b.code}>• {b.label}</li>)}
                </ul>
            )}
            <div className="flex items-center justify-between gap-2">
                {menuItems.length ? <IosMenu items={menuItems} label="Действия с записью" align="left" /> : <span />}
                {perms.can_close && (
                    <button type="button" className={dBtnPrimary} onClick={close} disabled={busy}>Закрыть запись</button>
                )}
            </div>
        </div>
    ) : null;

    const body = error ? <EmptyState title={error} /> : !shown ? <LoadingRows rows={5} /> : (
        <div key={shown.id} className="space-y-4 xl:space-y-5">
            <div className="space-y-3">
                <div className="flex flex-wrap items-center gap-1.5">
                    <StepChip item={shown} />
                    {CARD_STATES_BESIDE_STEP.includes(shown.state) ? <StateBadge state={shown.state} /> : null}
                    {shown.ordinal ? <Chip tone="slate">{ordinalWord(shown.ordinal)} за период</Chip> : null}
                </div>
                {shown.under_control && shown.control && (
                    <div className="flex items-start gap-2 rounded-xl bg-rose-50 px-3.5 py-2.5 text-[12.5px] text-rose-900 ring-1 ring-rose-200/70">
                        <ShieldAlert size={15} className="mt-0.5 shrink-0" />
                        <span>Зафиксировано на особом контроле (до {fmtDate(shown.control.end_date)}): {shown.control.reason}. Закрывает запись руководитель.</span>
                    </div>
                )}
                <Group cardClassName="px-4 py-2 xl:px-5">
                    <Line label="Сотрудник">
                        {internal ? (
                            <button type="button" className="font-semibold text-blue-600 hover:underline" onClick={() => onOpenOperator(shown.operator_id)}>
                                {shown.operator_name}
                            </button>
                        ) : shown.operator_name}
                    </Line>
                    <Line label="Когда"><span className="tabular-nums">{fmtDateTime(shown.occurred_at)}</span></Line>
                    {internal && <Line label="Группа · линия">{[shown.group_name, shown.direction_name].filter(Boolean).join(' · ') || '—'}</Line>}
                    {internal && shown.shift_label ? <Line label="Смена">{shown.shift_label}</Line> : null}
                    {internal && <Line label="Зафиксировал">{shown.created_by_name}</Line>}
                    {shown.state === 'cancelled' && internal && (
                        <Line label="Отменена">{fmtDate(shown.cancelled_at)} · {shown.cancelled_by_name}: {shown.cancel_reason}</Line>
                    )}
                    {shown.state === 'closed' && internal && <Line label="Закрыта">{fmtDate(shown.closed_at)} · {shown.closed_by_name}</Line>}
                </Group>
            </div>

            {mode === 'edit' && <EditForm item={shown} api={api} toast={toast} onDone={(changed) => { setMode('view'); if (changed) reload(); }} />}

            {mode === 'cancel' && (
                <Group title="Отменить запись" hint="Отмена пересчитает номера остальных нарушений человека. Запись останется в журнале как отменённая.">
                    <div className="space-y-3">
                        <Field error={cancelError}>
                            <textarea rows={3} className={`${dInput} resize-y ${cancelError ? INVALID_RING : ''}`} placeholder="Почему запись ошибочна"
                                      value={cancelReason} autoFocus
                                      onChange={(e) => { setCancelReason(e.target.value); setCancelError(null); }} />
                        </Field>
                        <div className="flex justify-end gap-2">
                            <button type="button" className={dBtnSecondary} onClick={() => setMode('view')}>Не отменять</button>
                            <button type="button" className={dBtnDanger} onClick={cancel} disabled={busy}>Отменить запись</button>
                        </div>
                    </div>
                </Group>
            )}

            {(shown.needs_explanation_now || shown.explanation_received_at || shown.refused_at) && (
                <ExplanationSection item={shown} api={api} toast={toast} reload={reload} onOpenFile={onOpenFile} />
            )}

            {internal && shown.action === 'training' && <TrainingSection item={shown} api={api} toast={toast} reload={reload} />}

            {internal && <ActSection item={shown} api={api} toast={toast} reload={reload} onOpenFile={onOpenFile}
                                     onOpenRecord={onOpenRecord} measures={measures} />}

            {internal ? (
                <MeasureSection item={shown} api={api} toast={toast} reload={reload} measures={measures} />
            ) : shown.measure_title ? (
                <Group title="Мера">
                    <div className="text-[13.5px] font-medium text-slate-800">
                        {shown.measure_title}
                        {measureValue(shown.measure_amount, shown.measure_percent)
                            ? <span className="font-normal text-slate-600"> · {measureValue(shown.measure_amount, shown.measure_percent)}</span> : null}
                    </div>
                </Group>
            ) : null}

            {internal && (
                <Group title="Обстоятельства">
                    <div className="space-y-3">
                        <p className={`whitespace-pre-wrap text-[13.5px] leading-relaxed text-slate-800 ${T.body}`}>{shown.description}</p>
                        {shown.evidence_note ? <div className={`text-[12.5px] text-slate-600 ${T.sub}`}>Тайм-код: {shown.evidence_note}</div> : null}
                        <EvidenceBlock item={shown} api={api} toast={toast} reload={reload} onOpenFile={onOpenFile} />
                    </div>
                </Group>
            )}

            {internal && (shown.manager_comment || commentOpen) && (
                <Group title="Комментарий руководителя">
                    {perms.can_manage && !finished ? (
                        <div className="space-y-2">
                            <textarea rows={2} autoFocus={commentOpen && !shown.manager_comment} className={`${dInput} resize-y`}
                                      value={comment} onChange={(e) => setComment(e.target.value)} />
                            {comment !== (shown.manager_comment || '') && (
                                <div className="flex items-center justify-end gap-3">
                                    {commentBusy && <Spinner />}
                                    <button type="button" className={dBtnSecondary} onClick={saveComment} disabled={commentBusy}>Сохранить</button>
                                </div>
                            )}
                        </div>
                    ) : <p className="whitespace-pre-wrap text-[13px] text-slate-700">{shown.manager_comment}</p>}
                </Group>
            )}
            {internal && perms.can_manage && !finished && !shown.manager_comment && !commentOpen && (
                <div className="flex justify-start">
                    <button type="button" className={`${dBtnSecondary} !py-2`} onClick={() => setCommentOpen(true)}>Комментарий руководителя</button>
                </div>
            )}

            {internal && (shown.history || []).length > 0 && (
                <Group title="История">
                    <ol className="space-y-2">
                        {shown.history.map((row) => (
                            <li key={row.id} className="grid grid-cols-[92px_1fr] gap-3 text-[12.5px]">
                                <span className="tabular-nums text-slate-400">{fmtDateTime(row.at)}</span>
                                <span className="min-w-0 text-slate-700">
                                    <span className="font-medium">{row.action_label}</span>
                                    {row.actor_name ? <span className="text-slate-500"> · {row.actor_name}</span> : null}
                                    {row.changes ? <span className="block truncate text-slate-400">{row.changes}</span> : null}
                                </span>
                            </li>
                        ))}
                    </ol>
                </Group>
            )}
        </div>
    );

    return {
        title: shown ? shown.number : 'Запись',
        subtitle: shown ? shown.type_title : '',
        footer,
        body,
        sizeKey: shown ? `ready-${mode}` : (error ? 'error' : 'loading'),
    };
};
