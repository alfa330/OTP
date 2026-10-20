import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowRight, FileText, Plus, Trash2 } from 'lucide-react';
import { IosHint, IosModal, IosSegmented, IosToggle } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { apiError } from './disciplineApi';
import {
    AttachButton, Chip, dBtnPrimary, dBtnSecondary, dInput, EmptyState, Field, Group, INVALID_RING,
    LoadingRows, Spinner, T,
} from './disciplineUi';
import { filesProblem, fmtDateTime, ordinalWord, stepTitle } from './disciplineRules';

/*
 * «Настройки и справочники» (ТЗ п. 4.5, 5, 7, 12) — только руководителю.
 * Всё здесь меняется без разработчика: типы нарушений, меры, лестница, окно
 * подсчёта, сроки и каналы уведомлений, шаблон акта.
 *
 * Лестница — список ступеней. Два готовых варианта: «Как в ТЗ» (объяснительные,
 * акт на третьем — по умолчанию, по нему написаны критерии приёмки) и «Как на
 * образце» (так нарисовал заказчик: тренинг, объяснительные №1–3, акт на
 * четвёртом). За последней ступенью повторяется последняя. Лестница и окно
 * действуют на нарушения, зафиксированные после сохранения: прошлые записи
 * считаются по правилам своего дня.
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

const LADDER_EQ = (a, b) => a.length === b.length && a.every((step, i) => step === b[i]);

const TemplateHint = 'Метки, которые подставит система: {{НОМЕР_АКТА}}, {{ДАТА}}, {{ФИО}}, {{ДОЛЖНОСТЬ}}, {{ОТДЕЛ}}, '
    + '{{ГРУППА}}, {{ДАТА_ПРИЁМА}}, {{НАРУШЕНИЯ}}, {{ОБЪЯСНИТЕЛЬНЫЕ}}, {{РЕШЕНИЕ}}, {{СОСТАВИЛ}}. Без шаблона '
    + 'выгружается стандартный акт.';

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

const SettingsPanel = ({ api, toast, departments, version, userId = null }) => {
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

    const presets = data?.catalog?.presets || [];
    const presetValue = useMemo(() => {
        if (!draft) return 'custom';
        const found = presets.find((p) => LADDER_EQ(p.ladder, draft.ladder));
        return found ? found.value : 'custom';
    }, [draft, presets]);
    const dirty = data && draft && JSON.stringify(draft) !== JSON.stringify(data.settings);

    const setLadder = (ladder) => setDraft((d) => ({ ...d, ladder }));
    const limits = data?.catalog?.limits || {};
    const validate = () => {
        const next = {};
        Object.entries(limits).forEach(([key, range]) => {
            if (key === 'period_days' && draft.period_kind !== 'rolling') return;
            const [low, high] = range;
            const value = Number(draft[key]);
            if (!Number.isInteger(value) || value < low || value > high) {
                next[key] = `${LIMIT_LABELS[key] || 'Значение'} — от ${low} до ${high} дней`;
            }
        });
        return next;
    };
    const save = async () => {
        const next = validate();
        setErrors(next);
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

    return (
        <div className="space-y-5 xl:space-y-6">
            {departments.length > 1 && (
                <IosSegmented value={departmentId} onChange={setDepartmentId} slide ariaLabel="Отдел"
                              options={departments.map((d) => ({ value: d.id, label: d.name }))} />
            )}
            {error ? <Group><EmptyState title={error} /></Group> : !data || !draft ? <LoadingRows rows={8} /> : (
                <>
                    <div className="grid gap-5 xl:grid-cols-2 xl:gap-6">
                        <Group title="Лестница эскалации"
                               right={<IosHint align="right" text="Номер нарушения считается в окне подсчёта; за последней ступенью повторяется последняя. Новая лестница действует на нарушения, зафиксированные после сохранения: у прежних записей номер, ступень и акт остаются как были." />}>
                            <div className="space-y-4">
                                <IosSegmented value={presetValue} stretch slide ariaLabel="Готовые варианты"
                                              onChange={(value) => {
                                                  const preset = presets.find((p) => p.value === value);
                                                  if (preset) setLadder([...preset.ladder]);
                                              }}
                                              options={[...presets.map((p) => ({ value: p.value, label: p.label })),
                                                  { value: 'custom', label: 'Своя' }]} />
                                <ol className="space-y-2">
                                    {draft.ladder.map((step, index) => (
                                        <li key={index} className="flex items-center gap-2">
                                            <span className={`w-[118px] shrink-0 text-[13px] text-slate-600 ${T.sub}`}>
                                                {ordinalWord(index + 1).replace(/^./, (c) => c.toUpperCase())}
                                            </span>
                                            <ArrowRight size={14} className="shrink-0 text-slate-300" />
                                            <div className="min-w-0 flex-1">
                                                <CustomSelect variant="ios" value={step} ariaLabel={`Ступень ${index + 1}`}
                                                              options={data.catalog.steps}
                                                              onChange={(value) => setLadder(draft.ladder.map((s, i) => (i === index ? value : s)))} />
                                            </div>
                                            <button type="button" aria-label="Убрать ступень" disabled={draft.ladder.length <= 1}
                                                    onClick={() => setLadder(draft.ladder.filter((_, i) => i !== index))}
                                                    className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-slate-400 hover:bg-slate-100 hover:text-rose-600 disabled:opacity-30">
                                                <Trash2 size={15} />
                                            </button>
                                        </li>
                                    ))}
                                </ol>
                                {draft.ladder.length < 10 && (
                                    <button type="button" className={`${dBtnSecondary} !py-2`}
                                            onClick={() => setLadder([...draft.ladder, draft.ladder[draft.ladder.length - 1] || 'explanation'])}>
                                        <Plus size={15} />
                                        Ступень
                                    </button>
                                )}
                                <div className={`rounded-xl bg-slate-50 px-3.5 py-2.5 text-[12.5px] text-slate-600 ${T.sub}`}>
                                    {draft.ladder.map((step, i) => `${i + 1}-е — ${stepTitle(step).toLowerCase()}`).join(' · ')}
                                </div>
                            </div>
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
                                    <Field label="Длина окна, дней" error={errors.period_days}>
                                        <input inputMode="numeric" className={`${dInput} ${errors.period_days ? INVALID_RING : ''}`}
                                               value={draft.period_days} onChange={editNumber('period_days')} />
                                    </Field>
                                )}
                                <div className="grid gap-4 sm:grid-cols-2">
                                    <Field label="Особый контроль после акта, дней" error={errors.control_days}>
                                        <input inputMode="numeric" className={`${dInput} ${errors.control_days ? INVALID_RING : ''}`}
                                               value={draft.control_days} onChange={editNumber('control_days')} />
                                    </Field>
                                    <Field label="Срок объяснительной, рабочих дней" error={errors.explanation_days}
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
                                <span className={`text-[13px] text-slate-500 ${T.sub}`}>Шаблона нет — выгружается стандартный акт.</span>
                            )}
                            <span className="flex items-center gap-2">
                                {templateBusy && <Spinner />}
                                <AttachButton label={data.template ? 'Заменить шаблон' : 'Загрузить DOCX'} accept=".docx"
                                              onFiles={uploadTemplate} disabled={templateBusy} invalid={!!templateError} />
                            </span>
                        </div>
                        {templateError && <div className={`mt-2 text-[12px] font-medium text-rose-600 ${T.note}`}>{templateError}</div>}
                    </Group>
                </>
            )}

            {editor && data && (
                <DictionaryEditor kind={editor.kind} item={editor.item} catalog={data.catalog} departmentId={departmentId}
                                  api={api} toast={toast} onClose={() => setEditor(null)}
                                  onSaved={() => { setEditor(null); refreshDictionaries(); }} />
            )}
        </div>
    );
};

export default SettingsPanel;
