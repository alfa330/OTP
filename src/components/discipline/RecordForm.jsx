import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { UserRound, X } from 'lucide-react';
import { IosHint, IosModal } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { apiCode, apiError } from './disciplineApi';
import {
    ACCEPT_EVIDENCE, AttachButton, dBtnPrimary, dBtnSecondary, dInput, Field, INVALID_RING, Plate, Spinner, T,
} from './disciplineUi';
import { OperatorPicker, useOperatorSearch } from './OperatorPicker';
import {
    DESCRIPTION_MIN, DRAFT_INTERVAL_MS, draftKey, fileSizeText, filesProblem, hasTimecode, isFullMoment,
    localInputValue, momentProblem, shiftProblem, splitMoment, timecodesProblem,
} from './disciplineRules';
import { MomentField, ScheduleShiftLink, ShiftField, TimecodeField } from './TimeFields';

/*
 * Форма «Зафиксировать нарушение» (ТЗ п. 6).
 *
 * Главное требование раздела (п. 6.2): СРАЗУ после выбора оператора, до
 * остальных полей, под полем появляется плашка — сколько у человека нарушений
 * за период и что будет этим (тренинг, объяснительная №N, акт), а если он на
 * особом контроле — красное «обязательно назначение меры». Плашка кликабельна
 * и раскрывает прошлые нарушения, не покидая форму.
 *
 * Смена подставляется из графика на момент нарушения (и заново — при смене
 * даты, пока её не правили руками) и обязательна (п. 6.1). Группа и линия —
 * из группы человека на эту дату. Описание — не короче 30 символов.
 * Доказательство обязательно, если этого требует тип.
 *
 * Дата, время, смена и тайм-код записи экрана — пикерами портала
 * (TimeFields.jsx), а не системными полями и не свободным текстом: в поле
 * смены и тайм-кода набирается только время.
 *
 * Черновик — на устройстве: каждые 30 секунд и при каждом изменении (п. 16:
 * «чтобы супервайзер не терял описание при обрыве связи»). Свой у каждого
 * пользователя и у каждого сотрудника. Форма из шапки открывается чистой, а
 * начатые черновики стоят строкой «Черновики: …» — выбрать; выбор человека в
 * поиске и форма из его карточки подхватывают его черновик сами. Черновик
 * стирают только отправка и «Начать заново». Файлы в черновик не попадают —
 * браузер их не сохраняет; об этом сказано у кнопки.
 *
 * Недостающее подсвечивается красным у самого поля, кнопка не серая: нажатие
 * прокручивает к первому незаполненному (правило портала).
 */

/* Черновики пользователя: { [id сотрудника]: { operator, form, savedAt } }. */
const readDrafts = (userId) => {
    try {
        const parsed = JSON.parse(window.localStorage.getItem(draftKey(userId)) || 'null');
        return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
    } catch {
        return {};
    }
};

const writeDrafts = (userId, drafts) => {
    try {
        if (Object.keys(drafts).length) window.localStorage.setItem(draftKey(userId), JSON.stringify(drafts));
        else window.localStorage.removeItem(draftKey(userId));
    } catch { /* черновик не сохранится */ }
};

const putDraft = (userId, draft) => writeDrafts(userId, { ...readDrafts(userId), [draft.operator.id]: draft });

const removeDraft = (userId, operatorId) => {
    if (!operatorId) return;
    const drafts = readDrafts(userId);
    delete drafts[operatorId];
    writeDrafts(userId, drafts);
};

const draftList = (drafts) => Object.values(drafts)
    .filter((draft) => draft?.operator?.id)
    .sort((a, b) => String(b.savedAt || '').localeCompare(String(a.savedAt || '')));

const numberValue = (value) => Number(String(value || '').replace(',', '.').replace(/\s/g, ''));

// Ответ сервера — к своему полю.
const FIELD_BY_CODE = {
    MEASURE_REQUIRED: 'measure_id', MEASURE_AMOUNT: 'measure_amount', MEASURE_PERCENT: 'measure_percent',
    SHIFT_REQUIRED: 'shift_label', SHIFT_FORMAT: 'shift_label', OCCURRED_AT: 'occurred_at', TYPE_ID: 'type_id',
    OTHER_TITLE: 'other_title', DESCRIPTION: 'description', EVIDENCE: 'evidence', EVIDENCE_NOTE: 'evidence',
};

// Время черновика — по часам устройства (savedAt хранится в UTC).
const draftTime = (iso) => {
    const date = iso ? new Date(iso) : null;
    if (!date || Number.isNaN(date.getTime())) return '';
    return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
};

const EMPTY = {
    operator: null, occurred_at: '', shift_label: '', shift_touched: false, type_id: null, other_title: '',
    description: '', evidence_note: '', measure_id: null, measure_amount: '', measure_percent: '', client_token: '',
};

// Метка заполнения формы: если ответ на «Зафиксировать» потерялся в сети и
// человек нажал ещё раз, сервер узнаёт ту же отправку и второго нарушения не
// создаёт. Хранится в черновике — переживает и закрытие формы.
const newClientToken = () => {
    try {
        if (window.crypto?.randomUUID) return window.crypto.randomUUID();
    } catch { /* ниже — запасной вариант */ }
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
};

const RecordForm = ({ open, api, toast, userId = null, initialOperator = null, onClose, onCreated }) => {
    const [form, setForm] = useState(EMPTY);
    const [files, setFiles] = useState([]);
    const [query, setQuery] = useState('');
    const [context, setContext] = useState(null);
    const [contextLoading, setContextLoading] = useState(false);
    const [plateOpen, setPlateOpen] = useState(false);
    const [errors, setErrors] = useState({});
    const [saving, setSaving] = useState(false);
    const [draftNote, setDraftNote] = useState(null);
    const [savedDrafts, setSavedDrafts] = useState([]);
    // Сервер ответил «на особом контроле — нужна мера», хотя при выборе человека
    // контроля не было (поставили, пока писали): поле меры появляется само.
    const [forceMeasure, setForceMeasure] = useState(false);
    const [contextNonce, setContextNonce] = useState(0);
    const bodyRef = useRef(null);
    const dirty = useRef(false);

    const freshForm = (operator = null) => ({
        ...EMPTY, operator, occurred_at: localInputValue(), client_token: newClientToken(),
    });

    // Черновик человека — в форму (с отметкой «Восстановлен черновик»).
    const applyDraft = (draft) => {
        setForm({ ...freshForm(), ...draft.form, operator: draft.operator });
        setDraftNote(draft.savedAt || null);
        setErrors({});
        setForceMeasure(false);
        dirty.current = false;
    };

    // Открытие: из карточки сотрудника — его черновик, если есть; из шапки —
    // чистая форма, а начатые черновики — строкой ниже, на выбор.
    useEffect(() => {
        if (!open) return;
        const drafts = readDrafts(userId);
        const own = initialOperator ? drafts[initialOperator.id] : null;
        if (own?.operator) {
            applyDraft(own);
        } else {
            setForm(freshForm(initialOperator ? { id: initialOperator.id, name: initialOperator.name } : null));
            setDraftNote(null);
        }
        setSavedDrafts(initialOperator ? [] : draftList(drafts));
        setFiles([]);
        setErrors({});
        setQuery('');
        setContext(null);
        setPlateOpen(false);
        setForceMeasure(false);
        dirty.current = false;
    }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

    // Выбрали человека в поиске — его черновик, если он есть.
    const pickOperator = (person) => {
        const draft = readDrafts(userId)[person.id];
        if (draft?.operator) {
            applyDraft(draft);
            return;
        }
        setDraftNote(null);
        update({ operator: { id: person.id, name: person.name }, shift_label: '', shift_touched: false });
    };

    const update = (patch) => {
        dirty.current = true;
        if ('operator' in patch) setForceMeasure(false);
        setForm((prev) => ({ ...prev, ...patch }));
        // Другой сотрудник — другая запись: прежние подсказки о недостающем снимаются.
        if ('operator' in patch) { setErrors({}); return; }
        setErrors((prev) => {
            const next = { ...prev };
            Object.keys(patch).forEach((key) => { delete next[key]; });
            return next;
        });
    };

    // Черновик: каждые 30 секунд и вскоре после правки — когда есть что терять
    // (одно выбранное ФИО черновиком не становится и чужой черновик не трогает).
    const saveDraft = useCallback(() => {
        if (!dirty.current || !form.operator) return;
        const written = form.description.trim() || form.type_id || form.evidence_note.trim() || form.other_title.trim()
            || form.measure_id;
        if (!written) return;
        const { operator, ...rest } = form;
        putDraft(userId, { operator, form: rest, savedAt: new Date().toISOString() });
    }, [form, userId]);
    useEffect(() => {
        if (!open) return undefined;
        const timer = setInterval(saveDraft, DRAFT_INTERVAL_MS);
        return () => clearInterval(timer);
    }, [open, saveDraft]);
    useEffect(() => {
        if (!open) return undefined;
        const timer = setTimeout(saveDraft, 1500);
        return () => clearTimeout(timer);
    }, [open, form, saveDraft]);

    // Поиск сотрудника.
    const search = useOperatorSearch(api, open, !form.operator, query);

    // Плашка и подстановки — по человеку, моменту и весу типа. Момент — только
    // полный: дата без времени серверу ничего не скажет.
    const operatorId = form.operator?.id;
    const momentAt = isFullMoment(form.occurred_at) ? form.occurred_at : '';
    useEffect(() => {
        if (!open || !operatorId) { setContext(null); return undefined; }
        let alive = true;
        setContextLoading(true);
        const timer = setTimeout(() => {
            api.operatorContext({ operator_id: operatorId, at: momentAt || undefined,
                type_id: form.type_id || undefined })
                .then((data) => {
                    if (!alive) return;
                    setContext(data);
                    // Смена — из графика на новый момент, пока её не правили руками.
                    setForm((prev) => (prev.shift_touched ? prev : { ...prev, shift_label: data.shift_label || '' }));
                })
                .catch((err) => {
                    if (!alive) return;
                    setContext(null);
                    toast(apiError(err, 'Не удалось загрузить историю сотрудника'), 'error');
                })
                .finally(() => { if (alive) setContextLoading(false); });
        }, 150);
        return () => { alive = false; clearTimeout(timer); };
    }, [api, open, operatorId, momentAt, form.type_id, contextNonce]); // eslint-disable-line react-hooks/exhaustive-deps

    const types = context?.types || [];
    const measures = context?.measures || [];
    const selectedType = types.find((t) => t.id === form.type_id) || null;
    const selectedMeasure = measures.find((m) => m.id === form.measure_id) || null;
    const measureRequired = Boolean(context?.measure_required) || forceMeasure;

    const typeOptions = useMemo(() => types.map((t) => ({ value: t.id, label: t.title })), [types]);
    const measureOptions = useMemo(() => measures.map((m) => ({ value: m.id, label: m.title })), [measures]);

    const validate = () => {
        const next = {};
        if (!form.operator) next.operator = 'Выберите сотрудника';
        const moment = momentProblem(form.occurred_at);
        if (moment) next.occurred_at = moment;
        if (!form.shift_label.trim()) next.shift_label = 'Укажите смену — в графике на это время её нет';
        else if (shiftProblem(form.shift_label)) next.shift_label = shiftProblem(form.shift_label);
        if (!form.type_id) next.type_id = 'Выберите тип нарушения';
        if (selectedType?.is_other && form.other_title.trim().length < 3) next.other_title = 'Опишите нарушение';
        if (form.description.trim().length < DESCRIPTION_MIN) {
            next.description = `Минимум ${DESCRIPTION_MIN} символов — сейчас ${form.description.trim().length}`;
        }
        const timecodes = timecodesProblem(form.evidence_note);
        if (timecodes) next.evidence = timecodes;
        else if (selectedType?.needs_evidence && !files.length && !hasTimecode(form.evidence_note)) {
            next.evidence = 'Для этого типа нужно доказательство: файл или тайм-код';
        }
        if (measureRequired) {
            if (!selectedMeasure) next.measure_id = 'Оператор на особом контроле — выберите меру';
            else if (selectedMeasure.value_kind === 'amount' && !(numberValue(form.measure_amount) > 0)) next.measure_amount = 'Укажите сумму меры';
            else if (selectedMeasure.value_kind === 'percent'
                && !(numberValue(form.measure_percent) > 0 && numberValue(form.measure_percent) <= 100)) {
                next.measure_percent = 'Укажите процент от 1 до 100';
            }
        }
        return next;
    };

    const scrollToFirst = (fieldErrors) => {
        const order = ['operator', 'occurred_at', 'shift_label', 'type_id', 'other_title', 'description', 'evidence',
            'measure_id', 'measure_amount', 'measure_percent'];
        const first = order.find((key) => fieldErrors[key]);
        const node = first && bodyRef.current?.querySelector(`[data-field="${first}"]`);
        node?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    };

    const submit = async () => {
        const fieldErrors = validate();
        setErrors(fieldErrors);
        if (Object.keys(fieldErrors).length) {
            scrollToFirst(fieldErrors);
            return;
        }
        setSaving(true);
        try {
            const result = await api.createRecord({
                operator_id: form.operator.id,
                occurred_at: form.occurred_at,
                shift_label: form.shift_label,
                type_id: form.type_id,
                other_title: selectedType?.is_other ? form.other_title : '',
                description: form.description,
                evidence_note: form.evidence_note,
                measure_id: measureRequired ? form.measure_id : '',
                measure_amount: measureRequired ? form.measure_amount : '',
                measure_percent: measureRequired ? form.measure_percent : '',
                client_token: form.client_token,
            }, files.map((file) => ['files', file]));
            removeDraft(userId, form.operator.id);
            dirty.current = false;
            toast(`Нарушение ${result.number} зафиксировано`);
            onCreated?.(result.id);
        } catch (err) {
            // Сервер ответил — записи нет, и следующая отправка — новая (метка
            // нужна только на случай, когда ответ потерялся в сети).
            if (err?.response) setForm((prev) => ({ ...prev, client_token: newClientToken() }));
            const field = FIELD_BY_CODE[apiCode(err)];
            if (field === 'measure_id' && !measureRequired) {
                setForceMeasure(true);
                setContextNonce((n) => n + 1);
            }
            if (field) {
                setErrors({ [field]: apiError(err) });
                // Поле могло ещё не появиться — тогда ошибка видна и тостом.
                requestAnimationFrame(() => {
                    if (bodyRef.current?.querySelector(`[data-field="${field}"]`)) scrollToFirst({ [field]: true });
                    else toast(apiError(err), 'error');
                });
            } else {
                toast(apiError(err, 'Запись не сохранилась'), 'error');
            }
        } finally {
            setSaving(false);
        }
    };

    const close = () => {
        saveDraft();
        onClose?.();
    };

    // «Начать заново»: черновик этого человека стёрт, человек остаётся выбранным.
    const clearDraft = () => {
        removeDraft(userId, form.operator?.id);
        setDraftNote(null);
        setForm(freshForm(form.operator));
        setErrors({});
        dirty.current = false;
    };

    const footer = (
        <div className="flex items-center justify-end gap-2">
            <button type="button" className={dBtnSecondary} onClick={close}>Отмена</button>
            <button type="button" className={dBtnPrimary} onClick={submit} disabled={saving}>
                {saving ? <Spinner className="!text-white" /> : null}
                Зафиксировать
            </button>
        </div>
    );

    return (
        <IosModal open={open} onClose={close} title="Зафиксировать нарушение" maxWidth="max-w-2xl" footer={footer}
                  sizeKey={form.operator ? (context ? 'ready' : 'loading') : 'pick'}>
            <div ref={bodyRef} className="space-y-4 xl:space-y-5">
                {draftNote && (
                    <div className={`flex items-center justify-between gap-3 rounded-xl bg-blue-50 px-3.5 py-2.5 text-[12.5px] text-blue-800 ${T.sub}`}>
                        <span>Восстановлен черновик{draftTime(draftNote) ? ` от ${draftTime(draftNote)}` : ''}</span>
                        <button type="button" onClick={clearDraft} className="font-semibold hover:underline">Начать заново</button>
                    </div>
                )}
                {!form.operator && savedDrafts.length > 0 && (
                    <div className={`flex flex-wrap items-center gap-1.5 text-[12.5px] ${T.sub}`}>
                        <span className="text-slate-500">Черновики:</span>
                        {savedDrafts.map((draft) => (
                            <button key={draft.operator.id} type="button" onClick={() => applyDraft(draft)}
                                    className="max-w-[240px] truncate rounded-full bg-blue-50 px-2.5 py-1 font-medium text-blue-700 ring-1 ring-blue-100 transition hover:bg-blue-100">
                                {draft.operator.name}{draftTime(draft.savedAt) ? ` · ${draftTime(draft.savedAt)}` : ''}
                            </button>
                        ))}
                    </div>
                )}

                <Field label="Оператор" error={errors.operator} anchor="operator">
                    {form.operator ? (
                        <div className="space-y-2.5">
                            <div className="flex items-center gap-3 rounded-xl bg-slate-100 px-3.5 py-2.5">
                                <UserRound size={18} className="shrink-0 text-slate-400" />
                                <div className="min-w-0 flex-1">
                                    <div className={`truncate text-[14px] font-semibold text-slate-900 ${T.body}`}>{form.operator.name}</div>
                                    <div className={`truncate text-[12px] text-slate-500 ${T.note}`}>
                                        {[context?.operator?.group_name, context?.operator?.direction_name,
                                            context?.operator?.department_name].filter(Boolean).join(' · ') || ' '}
                                    </div>
                                </div>
                                <button type="button" aria-label="Выбрать другого" onClick={() => {
                                    saveDraft();
                                    setDraftNote(null);
                                    setForm(freshForm());
                                    setErrors({});
                                    setSavedDrafts(draftList(readDrafts(userId)));
                                    dirty.current = false;
                                }}
                                        className="grid h-7 w-7 place-items-center rounded-full text-slate-400 hover:bg-slate-200 hover:text-slate-600">
                                    <X size={15} />
                                </button>
                            </div>
                            {contextLoading && !context ? <div className="h-[60px] animate-pulse rounded-2xl bg-slate-200/70" /> : (
                                <Plate plate={context?.plate} previous={context?.previous || []} expanded={plateOpen}
                                       onToggle={() => setPlateOpen((v) => !v)} />
                            )}
                        </div>
                    ) : (
                        <OperatorPicker query={query} onQuery={setQuery} {...search} onPick={pickOperator}
                                        invalid={!!errors.operator} autoFocus />
                    )}
                </Field>

                {form.operator && (
                    <>
                        <div className="grid gap-4 sm:grid-cols-2">
                            <Field label="Дата и время" error={errors.occurred_at} anchor="occurred_at"
                                   hint="По умолчанию — сейчас. Время набирается цифрами: «1430» — это 14:30.">
                                <MomentField value={form.occurred_at} invalid={!!errors.occurred_at}
                                             onChange={(value) => update({ occurred_at: value })} />
                            </Field>
                            <div className="space-y-1.5">
                                <Field label="Смена" error={errors.shift_label} anchor="shift_label"
                                       hint="Подставляется из графика на момент нарушения и меняется вместе с датой. Если в графике смены нет или она другая — укажите начало и конец. Конец раньше начала — ночная смена.">
                                    <ShiftField value={form.shift_label} invalid={!!errors.shift_label}
                                                onChange={(value) => update({ shift_label: value, shift_touched: true })} />
                                </Field>
                                {form.shift_touched && context?.shift_label && context.shift_label !== form.shift_label.trim() ? (
                                    <ScheduleShiftLink label={context.shift_label}
                                                       onPick={() => update({ shift_label: context.shift_label, shift_touched: false })} />
                                ) : null}
                            </div>
                        </div>

                        <Field label="Тип нарушения" error={errors.type_id} anchor="type_id">
                            <CustomSelect variant="ios" roomy value={form.type_id} options={typeOptions} searchable invalid={!!errors.type_id}
                                          placeholder="Выберите из справочника" ariaLabel="Тип нарушения"
                                          onChange={(value) => update({ type_id: value || null })} />
                        </Field>
                        {selectedType?.is_other && (
                            <Field label="Название нарушения" error={errors.other_title} anchor="other_title">
                                <input className={`${dInput} ${errors.other_title ? INVALID_RING : ''}`} value={form.other_title}
                                       placeholder="Что именно нарушено" onChange={(e) => update({ other_title: e.target.value })} />
                            </Field>
                        )}

                        <Field label="Описание обстоятельств" error={errors.description} anchor="description">
                            <textarea rows={4} className={`${dInput} resize-y ${errors.description ? INVALID_RING : ''}`}
                                      value={form.description} placeholder="Что произошло, когда и как это установлено"
                                      onChange={(e) => update({ description: e.target.value })} />
                            <div className={`px-1 text-right text-[11.5px] tabular-nums ${form.description.trim().length >= DESCRIPTION_MIN ? 'text-slate-400' : 'text-slate-500'} ${T.note}`}>
                                {form.description.trim().length} / {DESCRIPTION_MIN}
                            </div>
                        </Field>

                        <Field label="Доказательства" error={errors.evidence} anchor="evidence"
                               hint="Скриншот, фото или запись экрана — до 5 файлов (видео MP4 до 50 МБ). Файлы в черновик не сохраняются — приложите их перед отправкой.">
                            <div className="space-y-2">
                                {files.length > 0 && (
                                    <ul className="space-y-1">
                                        {files.map((file, index) => (
                                            <li key={`${file.name}-${index}`} className="flex items-center gap-2 rounded-lg bg-slate-50 px-3 py-1.5">
                                                <span className={`min-w-0 flex-1 truncate text-[12.5px] text-slate-700 ${T.sub}`}>{file.name}</span>
                                                <span className="text-[11.5px] tabular-nums text-slate-400">{fileSizeText(file.size)}</span>
                                                <button type="button" aria-label="Убрать файл" onClick={() => setFiles((prev) => prev.filter((_, i) => i !== index))}
                                                        className="grid h-6 w-6 place-items-center rounded-full text-slate-400 hover:bg-slate-200">
                                                    <X size={13} />
                                                </button>
                                            </li>
                                        ))}
                                    </ul>
                                )}
                                {files.length < 5 && (
                                    <AttachButton label="Приложить" multiple accept={ACCEPT_EVIDENCE} invalid={!!errors.evidence}
                                                  onFiles={(picked) => {
                                                      // Предел числа и размера — до отправки, а не после загрузки по мобильной сети.
                                                      const problem = filesProblem('evidence', picked, files.length);
                                                      if (!problem) setFiles((prev) => [...prev, ...picked]);
                                                      setErrors((prev) => ({ ...prev, evidence: problem || undefined }));
                                                  }} />
                                )}
                                <div className="space-y-1.5 pt-1">
                                    <div className="flex items-center gap-1.5 px-1">
                                        <span className={`text-[12.5px] font-medium text-slate-500 ${T.sub}`}>Тайм-код записи экрана</span>
                                        <IosHint text="Время на записи экрана в день нарушения: с какого момента смотреть и до какого. Конец можно не указывать; фрагментов — до пяти." />
                                    </div>
                                    <TimecodeField key={form.client_token || 'new'} value={form.evidence_note}
                                                   invalid={!!errors.evidence} defaultTime={splitMoment(form.occurred_at).time || '09:00'}
                                                   onChange={(value) => update({ evidence_note: value, evidence: undefined })} />
                                </div>
                            </div>
                        </Field>

                        {measureRequired && (
                            <div className="space-y-3 rounded-2xl bg-rose-50/70 p-3.5 ring-1 ring-rose-200/70">
                                <Field label="Мера" error={errors.measure_id} anchor="measure_id"
                                       hint="Оператор на особом контроле: мера обязательна. Мера ждёт утверждения руководителя, если так помечена в справочнике (мера с деньгами — всегда). Закрывает запись руководитель.">
                                    <CustomSelect variant="ios" roomy value={form.measure_id} options={measureOptions} invalid={!!errors.measure_id}
                                                  placeholder="Выберите меру" ariaLabel="Мера"
                                                  onChange={(value) => update({ measure_id: value || null, measure_amount: '', measure_percent: '' })} />
                                </Field>
                                {selectedMeasure?.value_kind === 'amount' && (
                                    <Field label="Сумма, ₸" error={errors.measure_amount} anchor="measure_amount">
                                        <input inputMode="decimal" className={`${dInput} ${errors.measure_amount ? INVALID_RING : ''}`}
                                               value={form.measure_amount} onChange={(e) => update({ measure_amount: e.target.value })} />
                                    </Field>
                                )}
                                {selectedMeasure?.value_kind === 'percent' && (
                                    <Field label="Процент переменной части" error={errors.measure_percent} anchor="measure_percent">
                                        <input inputMode="decimal" className={`${dInput} ${errors.measure_percent ? INVALID_RING : ''}`}
                                               value={form.measure_percent} onChange={(e) => update({ measure_percent: e.target.value })} />
                                    </Field>
                                )}
                            </div>
                        )}
                    </>
                )}
            </div>
        </IosModal>
    );
};

export default RecordForm;
