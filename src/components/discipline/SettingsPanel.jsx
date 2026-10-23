import React, { useEffect, useRef, useState } from 'react';
import { ArrowRight, Check, CheckCircle2, FileText, Plus, Trash2 } from 'lucide-react';
import { IosHint, IosModal, IosSegmented, IosToggle } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { apiError } from './disciplineApi';
import useIsMobileShell from '../common/useIsMobileShell';
import {
    AttachButton, Chip, dBtnPrimary, dBtnSecondary, DepartmentPicker, dInput, EmptyState, Field, Group, INVALID_RING,
    LoadingRows, Spinner, T,
} from './disciplineUi';
import {
    addresseeProblem, filesProblem, fmtDateTime, ladderName, ladderProblems, ordinalWord, stepTitle,
} from './disciplineRules';

/*
 * «Настройки и справочники» (ТЗ п. 4.5, 5, 7, 12) — только руководителю.
 * Всё здесь меняется без разработчика: типы нарушений, меры, лестница, окно
 * подсчёта, сроки и каналы уведомлений, шаблон акта.
 *
 * Лестницы — у отдела свои, с названиями; действует одна. По умолчанию одна,
 * «Основная»: объяснительная, объяснительная, объяснительная + акт. Глава отдела
 * (и выше) называет лестницы сам, заводит новые и выбирает действующую
 * (владелец, 21.10.2026). За последней ступенью повторяется последняя.
 * Действующая лестница и окно действуют на нарушения, зафиксированные после
 * сохранения: прошлые записи считаются по правилам своего дня; название на
 * счёт не влияет.
 *
 * Несохранённые правки держатся у каждого отдела свои: правка справочника,
 * переход в другой отдел или на другую вкладку раздела их не стирает — до
 * «Сохранить» или «Отменить» (держатся в памяти страницы, по пользователю).
 */

const unsavedSettings = new Map();

const LIMIT_LABELS = {
    period_days: 'Длина окна',
    control_days: 'Срок контроля',
    explanation_days: 'Срок объяснительной',
};


const TemplateHint = 'Метки, которые подставит система: {{НОМЕР_АКТА}}, {{ДАТА}}, {{ФИО}}, {{ДОЛЖНОСТЬ}}, {{ОТДЕЛ}}, '
    + '{{ГРУППА}}, {{ДАТА_ПРИЁМА}}, {{НАРУШЕНИЯ}}, {{ОБЪЯСНИТЕЛЬНЫЕ}}, {{РЕШЕНИЕ}}, {{СОСТАВИЛ}}. Без шаблона '
    + 'выгружается стандартный акт по образцу организации.';

const AddresseeHint = 'Шапка бланка объяснительной — кому она адресована, как в шаблоне организации: «Заказчику: …». '
    + 'Пусто — в бланке линия, чтобы вписать от руки. Бланк скачивают в карточке нарушения и в «Моих записях».';

const DictionaryEditor = ({ kind, item, catalog, departmentId, api, toast, onClose, onSaved }) => {
    const isType = kind === 'type';
    const [form, setForm] = useState(item || (isType
        ? { title: '', severity: 'medium', weight: 1, needs_explanation: true, needs_evidence: false, is_other: false, is_archived: false }
        : { title: '', needs_approval: true, finance: 'none', value_kind: 'none', is_archived: false }));
    const [error, setError] = useState(null);
    const [busy, setBusy] = useState(false);

    const save = async () => {
        if (String(form.title || '').trim().length < 2) { setError('Укажите название'); return; }
        setBusy(true);
        try {
            const body = { ...form, department_id: departmentId };
            if (isType) await api.saveType(body); else await api.saveMeasure(body);
            toast('Сохранено');
            onSaved();
        } catch (err) {
            setError(apiError(err));
        } finally {
            setBusy(false);
        }
    };

    const toggleRow = (label, key, hint = null) => (
        <div className="flex items-center justify-between gap-3 rounded-xl bg-slate-50 px-3.5 py-2.5">
            <span className="flex items-center gap-1.5 text-[13px] text-slate-700">{label}{hint ? <IosHint text={hint} /> : null}</span>
            <IosToggle checked={Boolean(form[key])} onChange={(value) => setForm((f) => ({ ...f, [key]: value }))} />
        </div>
    );

    return (
        <IosModal open onClose={onClose} title={item ? (isType ? 'Тип нарушения' : 'Мера') : (isType ? 'Новый тип нарушения' : 'Новая мера')}
                  maxWidth="max-w-lg"
                  footer={(
                      <div className="flex justify-end gap-2">
                          <button type="button" className={dBtnSecondary} onClick={onClose}>Отмена</button>
                          <button type="button" className={dBtnPrimary} onClick={save} disabled={busy}>Сохранить</button>
                      </div>
                  )}>
            <div className="space-y-4">
                <Field label="Название" error={error}>
                    <input className={`${dInput} ${error ? INVALID_RING : ''}`} value={form.title}
                           onChange={(e) => { setForm((f) => ({ ...f, title: e.target.value })); setError(null); }} />
                </Field>
                {isType ? (
                    <>
                        <Field label="Тяжесть">
                            <IosSegmented value={form.severity} stretch slide onChange={(value) => setForm((f) => ({ ...f, severity: value }))}
                                          options={catalog.severities.filter((s) => s.value !== 'manual' || form.is_other)
                                              .map((s) => ({ value: s.value, label: s.label }))} />
                        </Field>
                        <Field label="Вес в счётчике" hint="Сколько ступеней лестницы засчитывает такое нарушение. Например, грубое — за два.">
                            <IosSegmented value={Number(form.weight)} stretch slide onChange={(value) => setForm((f) => ({ ...f, weight: value }))}
                                          options={[1, 2, 3, 4, 5].map((n) => ({ value: n, label: String(n) }))} />
                        </Field>
                        <div className="space-y-2">
                            {toggleRow('Нужна объяснительная', 'needs_explanation',
                                'Объяснительную запрашивает ступень лестницы; этот переключатель позволяет её не требовать для лёгких типов.')}
                            {toggleRow('Нужно доказательство файлом', 'needs_evidence')}
                            {toggleRow('«Иное» — название вводится вручную', 'is_other')}
                            {toggleRow('В архиве', 'is_archived', 'Архивный тип не предлагается в форме, старые записи с ним остаются как были.')}
                        </div>
                    </>
                ) : (
                    <>
                        <Field label="Финансовый эффект">
                            <IosSegmented value={form.finance} stretch slide
                                          onChange={(value) => setForm((f) => ({ ...f, finance: value, value_kind: value === 'none' ? 'none' : f.value_kind }))}
                                          options={catalog.finance.map((f) => ({ value: f.value, label: f.label }))} />
                        </Field>
                        {form.finance !== 'none' && (
                            <Field label="Чем выражена" hint="Сумма или процент вводятся при назначении меры и попадают в выгрузку на удержания.">
                                <IosSegmented value={form.value_kind} stretch slide onChange={(value) => setForm((f) => ({ ...f, value_kind: value }))}
                                              options={catalog.value_kinds.map((f) => ({ value: f.value, label: f.label }))} />
                            </Field>
                        )}
                        <div className="space-y-2">
                            {toggleRow('Требует утверждения руководителя', 'needs_approval',
                                'Мера от супервайзера ждёт руководителя. Мера с деньгами утверждается всегда, а запись на особом контроле закрывает руководитель при любой мере.')}
                            {toggleRow('В архиве', 'is_archived')}
                        </div>
                    </>
                )}
            </div>
        </IosModal>
    );
};

/* id новой лестницы — латиница и цифры, как ждёт сервер (catalog._LADDER_ID). */
const newLadderId = () => `l${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;

/* Лестницы отдела: плашки с названиями (выбранная — мягкой голубой заливкой с
   галочкой, у действующей — «действует»), под ними название и ступени
   выбранной. Новая лестница — копия выбранной: её называют и правят. Удалить
   можно любую, кроме действующей; всё — в черновике до «Сохранить настройки». */
const LadderLibrary = ({ draft, setDraft, catalog, pickedId, onPick, problems, onNameChange }) => {
    const ladders = draft.ladders || [];
    const activeId = draft.active_ladder;
    const picked = ladders.find((l) => l.id === pickedId) || ladders.find((l) => l.id === activeId) || ladders[0];
    const nameRef = useRef(null);
    const [fresh, setFresh] = useState(null);
    useEffect(() => {
        if (fresh && picked?.id === fresh) nameRef.current?.focus();
    }, [fresh, picked?.id]);
    if (!picked) return null;
    const limits = catalog.ladders || {};
    const maxSteps = limits.max_steps || 10;
    const setLadders = (update) => setDraft((d) => ({ ...d, ladders: update(d.ladders || []) }));
    const patch = (change) => setLadders((list) => list.map((l) => (l.id === picked.id ? { ...l, ...change } : l)));
    const setSteps = (steps) => patch({ steps });
    const add = () => {
        const id = newLadderId();
        setLadders((list) => [...list, { id, name: '', steps: [...picked.steps] }]);
        onPick(id);
        setFresh(id);
    };
    const remove = () => {
        setLadders((list) => list.filter((l) => l.id !== picked.id));
        onPick(activeId);
    };
    const isActive = picked.id === activeId;
    const error = problems?.[picked.id] || null;
    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2" role="tablist" aria-label="Лестницы отдела">
                {ladders.map((ladder) => {
                    const on = ladder.id === picked.id;
                    return (
                        <button key={ladder.id} type="button" role="tab" aria-selected={on} onClick={() => onPick(ladder.id)}
                                className={`inline-flex max-w-full items-center gap-1.5 rounded-full px-3 py-1.5 text-[13px] font-medium transition active:scale-[0.97] ${T.sub} ${
                                    on ? 'bg-blue-50 text-blue-700 ring-1 ring-blue-200' : 'bg-slate-100 text-slate-700 hover:bg-slate-200/60'} ${
                                    problems?.[ladder.id] ? INVALID_RING : ''}`}>
                            {on ? <Check size={14} className="shrink-0 text-blue-600" /> : null}
                            <span className="truncate">{ladderName(ladder.name) || 'Без названия'}</span>
                            {ladder.id === activeId ? (
                                <span className="shrink-0 rounded-full bg-emerald-50 px-1.5 py-px text-[11px] font-semibold text-emerald-700">действует</span>
                            ) : null}
                        </button>
                    );
                })}
                {ladders.length < (limits.max || 10) ? (
                    <button type="button" onClick={add}
                            className={`inline-flex items-center gap-1 rounded-full px-3 py-1.5 text-[13px] font-medium text-blue-600 transition hover:bg-blue-50 active:scale-[0.97] ${T.sub}`}>
                        <Plus size={14} />
                        Новая лестница
                    </button>
                ) : null}
            </div>
            <Field label="Название" error={error} anchor="ladder_name">
                <input ref={nameRef} className={`${dInput} ${error ? INVALID_RING : ''}`} value={picked.name}
                       maxLength={limits.name_max || 60} placeholder="Например, Основная"
                       onChange={(e) => { patch({ name: e.target.value }); onNameChange?.(picked.id); }} />
            </Field>
            <ol className="space-y-2">
                {picked.steps.map((step, index) => (
                    <li key={index} className="flex items-center gap-2">
                        {/* На телефоне подпись уже и без стрелки: иначе «Объяснительная + акт» обрезается. */}
                        <span className={`w-[76px] shrink-0 text-[13px] text-slate-600 sm:w-[118px] ${T.sub}`}>
                            {ordinalWord(index + 1).replace(/^./, (c) => c.toUpperCase())}
                        </span>
                        <ArrowRight size={14} className="hidden shrink-0 text-slate-300 sm:block" />
                        <div className="min-w-0 flex-1">
                            <CustomSelect variant="ios" value={step} ariaLabel={`Ступень ${index + 1}`}
                                          options={catalog.steps}
                                          onChange={(value) => setSteps(picked.steps.map((s, i) => (i === index ? value : s)))} />
                        </div>
                        <button type="button" aria-label="Убрать ступень" disabled={picked.steps.length <= 1}
                                onClick={() => setSteps(picked.steps.filter((_, i) => i !== index))}
                                className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-slate-400 hover:bg-slate-100 hover:text-rose-600 disabled:opacity-30">
                            <Trash2 size={15} />
                        </button>
                    </li>
                ))}
            </ol>
            {picked.steps.length < maxSteps && (
                <button type="button" className={`${dBtnSecondary} !py-2`}
                        onClick={() => setSteps([...picked.steps, picked.steps[picked.steps.length - 1] || 'explanation'])}>
                    <Plus size={15} />
                    Ступень
                </button>
            )}
            <div className={`rounded-xl bg-slate-50 px-3.5 py-2.5 text-[12.5px] text-slate-600 ${T.sub}`}>
                {picked.steps.map((step, i) => `${i + 1}-е — ${stepTitle(step).toLowerCase()}`).join(' · ')}
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 pt-3">
                {isActive ? (
                    <span className={`inline-flex items-center gap-1.5 text-[12.5px] text-emerald-700 ${T.sub}`}>
                        <CheckCircle2 size={15} className="shrink-0" />
                        По этой лестнице считаются нарушения
                    </span>
                ) : (
                    <>
                        <button type="button" className={`${dBtnSecondary} !py-2`}
                                onClick={() => setDraft((d) => ({ ...d, active_ladder: picked.id }))}>
                            Сделать действующей
                        </button>
                        <button type="button" onClick={remove}
                                className={`inline-flex items-center gap-1.5 rounded-xl px-3 py-2 text-[13px] font-medium text-rose-600 transition hover:bg-rose-50 ${T.sub}`}>
                            <Trash2 size={15} />
                            Удалить лестницу
                        </button>
                    </>
                )}
            </div>
        </div>
    );
};

const SettingsPanel = ({ api, toast, departments, version, userId = null }) => {
    const mobile = useIsMobileShell();
    const [departmentId, setDepartmentId] = useState(departments[0]?.id || null);
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    // Несохранённое — у каждого отдела своё и переживает уход с вкладки.
    const [drafts, setDrafts] = useState(() => unsavedSettings.get(userId) || {});
    useEffect(() => { unsavedSettings.set(userId, drafts); }, [userId, drafts]);
    const [errors, setErrors] = useState({});
    const [saving, setSaving] = useState(false);
    const [templateBusy, setTemplateBusy] = useState(false);
    const [templateError, setTemplateError] = useState(null);
    const [editor, setEditor] = useState(null);
    // Какую лестницу отдела открыли для правки (по умолчанию — действующую).
    const [ladderPick, setLadderPick] = useState({});
    const pickLadder = (id) => setLadderPick((prev) => ({ ...prev, [departmentId]: id }));
    const departmentNow = useRef(departmentId);
    departmentNow.current = departmentId;

    useEffect(() => {
        if (!departmentId) return undefined;
        let alive = true;
        setData(null);
        setErrors({});
        api.settings(departmentId)
            .then((payload) => { if (alive) { setData(payload); setError(null); } })
            .catch((err) => { if (alive) setError(apiError(err, 'Настройки не загрузились')); });
        return () => { alive = false; };
    }, [api, departmentId, version]);

    // Справочники и шаблон — без перезагрузки экрана и без потери правок лестницы.
    // Ответ — только в тот отдел, для которого его просили (отдел могли сменить).
    const refreshDictionaries = () => {
        const requested = departmentId;
        api.settings(requested)
            .then((payload) => {
                if (departmentNow.current !== requested) return;
                setData((prev) => (prev ? { ...prev, types: payload.types, measures: payload.measures,
                    template: payload.template } : payload));
            })
            .catch((err) => toast(apiError(err, 'Справочник не обновился'), 'error'));
    };

    const draft = data ? (drafts[departmentId] || data.settings) : null;
    const setDraft = (update) => setDrafts((prev) => {
        const current = prev[departmentId] || data.settings;
        return { ...prev, [departmentId]: typeof update === 'function' ? update(current) : update };
    });
    const dropDraft = () => setDrafts((prev) => {
        const next = { ...prev };
        delete next[departmentId];
        return next;
    });
    const editNumber = (key) => (e) => {
        const value = e.target.value.replace(/\D/g, '');
        setDraft((d) => ({ ...d, [key]: value }));
        setErrors((prev) => ({ ...prev, [key]: null }));
    };

    const dirty = data && draft && JSON.stringify(draft) !== JSON.stringify(data.settings);

    const limits = data?.catalog?.limits || {};
    const validate = () => {
        const next = {};
        const ladderIssues = ladderProblems(draft.ladders, data?.catalog?.ladders?.name_max);
        if (Object.keys(ladderIssues).length) next.ladders = ladderIssues;
        Object.entries(limits).forEach(([key, range]) => {
            if (key === 'period_days' && draft.period_kind !== 'rolling') return;
            const [low, high] = range;
            const value = Number(draft[key]);
            if (!Number.isInteger(value) || value < low || value > high) {
                next[key] = `${LIMIT_LABELS[key] || 'Значение'} — от ${low} до ${high} дней`;
            }
        });
        const addressee = addresseeProblem(draft.explanation_addressee, data?.catalog?.addressee);
        if (addressee) next.explanation_addressee = addressee;
        return next;
    };
    const save = async () => {
        const next = validate();
        setErrors(next);
        if (next.ladders) {
            // Подвести к первой лестнице без названия (или с занятым): открыть её и показать поле.
            pickLadder((draft.ladders || []).find((l) => next.ladders[l.id])?.id);
            requestAnimationFrame(() => document.querySelector('[data-field="ladder_name"]')
                ?.scrollIntoView({ behavior: 'smooth', block: 'center' }));
        } else if (Object.keys(next).length) {
            // Сроки и «Кому»: к первому по экрану полю с ошибкой — липкая «Сохранить»
            // нажимается и тогда, когда само поле за краем экрана.
            requestAnimationFrame(() => Object.keys(next)
                .map((key) => document.querySelector(`[data-field="${key}"]`)).filter(Boolean)
                .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top)[0]
                ?.scrollIntoView({ behavior: 'smooth', block: 'center' }));
        }
        if (Object.keys(next).length) return;
        setSaving(true);
        try {
            // Длина окна при «Календарном месяце» скрыта — её и не отправляем.
            const { period_days: periodDays, ...rest } = draft;
            const result = await api.saveSettings(departmentId, draft.period_kind === 'rolling'
                ? { ...rest, period_days: periodDays } : rest);
            setData((prev) => ({ ...prev, settings: result.settings }));
            dropDraft();
            toast('Настройки сохранены');
        } catch (err) {
            toast(apiError(err, 'Настройки не сохранились'), 'error');
        } finally {
            setSaving(false);
        }
    };
    const uploadTemplate = async (files) => {
        const problem = filesProblem('act_template', files.slice(0, 1));
        setTemplateError(problem);
        if (problem) return;
        setTemplateBusy(true);
        try {
            await api.uploadTemplate(departmentId, files[0]);
            toast('Шаблон акта загружен');
            refreshDictionaries();
        } catch (err) {
            setTemplateError(apiError(err, 'Шаблон не загрузился'));
        } finally {
            setTemplateBusy(false);
        }
    };

    if (!departments.length) return <Group><EmptyState title="Нет отделов под вашим руководством" /></Group>;

    /* Окно — рядом со столбиком, а не в нём: ребёнку space-y достаётся margin-top,
       и затемнение начиналось на 24 px ниже края экрана — светлая полоса над окном. */
    return (
        <>
            <div className="space-y-5 xl:space-y-6">
                {departments.length > 1 && (
                    <DepartmentPicker mobile={mobile} value={departmentId} onChange={setDepartmentId}
                                      options={departments.map((d) => ({ value: d.id, label: d.name }))} />
                )}
                {error ? <Group><EmptyState title={error} /></Group> : !data || !draft ? <LoadingRows rows={8} /> : (
                    <>
                        <div className="grid gap-5 xl:grid-cols-2 xl:gap-6">
                            <Group title="Лестница эскалации"
                                   right={<IosHint align="right" text="По действующей лестнице считаются номер и ступень новых нарушений; за последней ступенью повторяется последняя. Новая действующая лестница или новые ступени действуют на нарушения, зафиксированные после сохранения: у прежних записей номер, ступень и акт остаются как были. Название на счёт не влияет." />}>
                                <LadderLibrary draft={draft} setDraft={setDraft} catalog={data.catalog}
                                               pickedId={ladderPick[departmentId]} onPick={pickLadder}
                                               problems={errors.ladders}
                                               onNameChange={(id) => setErrors((prev) => {
                                                   if (!prev.ladders?.[id]) return prev;
                                                   const ladders = { ...prev.ladders };
                                                   delete ladders[id];
                                                   return { ...prev, ladders: Object.keys(ladders).length ? ladders : undefined };
                                               })} />
                            </Group>

                            <Group title="Период и сроки">
                                <div className="space-y-4">
                                    <Field label="Окно подсчёта"
                                           hint="Скользящее окно — N дней до нарушения: счётчик не обнуляется 1-го числа. Календарный месяц — с первого числа. Новое окно действует на нарушения, зафиксированные после сохранения.">
                                        <IosSegmented value={draft.period_kind} stretch slide
                                                      onChange={(value) => setDraft((d) => ({ ...d, period_kind: value }))}
                                                      options={data.catalog.period_kinds} />
                                    </Field>
                                    {draft.period_kind === 'rolling' && (
                                        <Field label="Длина окна, дней" error={errors.period_days} anchor="period_days">
                                            <input inputMode="numeric" className={`${dInput} ${errors.period_days ? INVALID_RING : ''}`}
                                                   value={draft.period_days} onChange={editNumber('period_days')} />
                                        </Field>
                                    )}
                                    <div className="grid gap-4 sm:grid-cols-2">
                                        <Field label="Особый контроль после акта, дней" error={errors.control_days} anchor="control_days">
                                            <input inputMode="numeric" className={`${dInput} ${errors.control_days ? INVALID_RING : ''}`}
                                                   value={draft.control_days} onChange={editNumber('control_days')} />
                                        </Field>
                                        <Field label="Срок объяснительной, рабочих дней" error={errors.explanation_days} anchor="explanation_days"
                                               hint="Рабочие дни — понедельник–пятница от момента фиксации.">
                                            <input inputMode="numeric" className={`${dInput} ${errors.explanation_days ? INVALID_RING : ''}`}
                                                   value={draft.explanation_days} onChange={editNumber('explanation_days')} />
                                        </Field>
                                    </div>
                                </div>
                            </Group>
                        </div>

                        <Group title="Уведомления" hint="Колокол iCore приходит всегда. Отправки почты в портале нет — вместо неё Telegram тем, у кого он привязан.">
                            <ul className="divide-y divide-slate-100">
                                {data.catalog.events.map((event) => (
                                    <li key={event.value} className="flex items-center justify-between gap-3 py-2.5 first:pt-0 last:pb-0">
                                        <span className="min-w-0">
                                            <span className={`block text-[13.5px] text-slate-800 ${T.body}`}>{event.label}</span>
                                            <span className={`block text-[12px] text-slate-500 ${T.note}`}>{event.who}</span>
                                        </span>
                                        <span className="flex items-center gap-2">
                                            <span className={`text-[12px] text-slate-500 ${T.note}`}>Telegram</span>
                                            <IosToggle checked={Boolean(draft.telegram?.[event.value])}
                                                       onChange={(value) => setDraft((d) => ({ ...d, telegram: { ...d.telegram, [event.value]: value } }))} />
                                        </span>
                                    </li>
                                ))}
                            </ul>
                        </Group>

                        <Group title="Бланк объяснительной" hint={AddresseeHint}>
                            <Field label="Кому" error={errors.explanation_addressee} anchor="explanation_addressee">
                                <textarea rows={2} className={`${dInput} resize-y ${errors.explanation_addressee ? INVALID_RING : ''}`}
                                          value={draft.explanation_addressee || ''}
                                          placeholder="Заказчику: Индивидуальному предпринимателю «…»"
                                          onChange={(e) => {
                                              const { value } = e.target;
                                              setDraft((d) => ({ ...d, explanation_addressee: value }));
                                              setErrors((prev) => ({ ...prev, explanation_addressee: null }));
                                          }} />
                            </Field>
                        </Group>

                        {dirty && (
                            <div className="sticky bottom-4 z-10 flex justify-end">
                                <div className="flex items-center gap-2 rounded-2xl bg-white/90 p-2 shadow-lg ring-1 ring-slate-200/70 backdrop-blur">
                                    <button type="button" className={dBtnSecondary} onClick={() => { dropDraft(); setErrors({}); }}>Отменить</button>
                                    <button type="button" className={dBtnPrimary} onClick={save} disabled={saving}>Сохранить настройки</button>
                                </div>
                            </div>
                        )}

                        <div className="grid gap-5 xl:grid-cols-2 xl:gap-6">
                            <Group title="Типы нарушений" right={(
                                <button type="button" className={`text-[12.5px] font-semibold text-blue-600 ${T.sub}`} onClick={() => setEditor({ kind: 'type' })}>
                                    Добавить
                                </button>
                            )} cardClassName="p-0 overflow-hidden">
                                <ul className="divide-y divide-slate-100">
                                    {data.types.map((type) => (
                                        <li key={type.id}>
                                            <button type="button" onClick={() => setEditor({ kind: 'type', item: type })}
                                                    className={`flex w-full items-center gap-2 px-4 py-2.5 text-left hover:bg-slate-50 ${type.is_archived ? 'opacity-50' : ''}`}>
                                                <span className={`min-w-0 flex-1 truncate text-[13px] text-slate-800 ${T.sub}`}>{type.title}</span>
                                                {type.weight > 1 ? <Chip tone="amber">×{type.weight}</Chip> : null}
                                                <Chip tone={type.severity === 'gross' ? 'red' : 'slate'}>
                                                    {(data.catalog.severities.find((s) => s.value === type.severity) || {}).label}
                                                </Chip>
                                                {type.is_archived ? <Chip>Архив</Chip> : null}
                                            </button>
                                        </li>
                                    ))}
                                </ul>
                            </Group>
                            <Group title="Меры воздействия" right={(
                                <button type="button" className={`text-[12.5px] font-semibold text-blue-600 ${T.sub}`} onClick={() => setEditor({ kind: 'measure' })}>
                                    Добавить
                                </button>
                            )} cardClassName="p-0 overflow-hidden">
                                <ul className="divide-y divide-slate-100">
                                    {data.measures.map((measure) => (
                                        <li key={measure.id}>
                                            <button type="button" onClick={() => setEditor({ kind: 'measure', item: measure })}
                                                    className={`flex w-full items-center gap-2 px-4 py-2.5 text-left hover:bg-slate-50 ${measure.is_archived ? 'opacity-50' : ''}`}>
                                                <span className={`min-w-0 flex-1 truncate text-[13px] text-slate-800 ${T.sub}`}>{measure.title}</span>
                                                {measure.finance !== 'none' ? <Chip tone="blue">₸ {(data.catalog.finance.find((f) => f.value === measure.finance) || {}).label?.toLowerCase()}</Chip> : null}
                                                {!measure.needs_approval ? <Chip>без утверждения</Chip> : null}
                                                {measure.is_archived ? <Chip>Архив</Chip> : null}
                                            </button>
                                        </li>
                                    ))}
                                </ul>
                            </Group>
                        </div>

                        <Group title="Шаблон акта" right={<IosHint text={TemplateHint} align="right" />}>
                            <div className="flex flex-wrap items-center justify-between gap-3">
                                {data.template ? (
                                    <span className="flex min-w-0 items-center gap-2 text-[13px] text-slate-700">
                                        <FileText size={16} className="shrink-0 text-blue-600" />
                                        <span className="truncate">{data.template.name}</span>
                                        <span className="text-[12px] text-slate-400">· {fmtDateTime(data.template.uploaded_at)}</span>
                                    </span>
                                ) : (
                                    <span className={`text-[13px] text-slate-500 ${T.sub}`}>Шаблона нет — выгружается стандартный акт по образцу организации.</span>
                                )}
                                <span className="flex items-center gap-2">
                                    {templateBusy && <Spinner />}
                                    <AttachButton label={data.template ? 'Заменить шаблон' : 'Загрузить DOCX'} accept=".docx"
                                                  onFiles={uploadTemplate} disabled={templateBusy} invalid={!!templateError}
                                                  edge={false} />
                                </span>
                            </div>
                            {templateError && <div className={`mt-2 text-[12px] font-medium text-rose-600 ${T.note}`}>{templateError}</div>}
                        </Group>
                    </>
                )}
            </div>
                {editor && data && (
                    <DictionaryEditor kind={editor.kind} item={editor.item} catalog={data.catalog} departmentId={departmentId}
                                      api={api} toast={toast} onClose={() => setEditor(null)}
                                      onSaved={() => { setEditor(null); refreshDictionaries(); }} />
                )}
        </>
    );
};

export default SettingsPanel;
