import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    AlertTriangle, ArrowLeft, ArrowRight, CheckCircle2, ChevronRight, Loader2,
    Paperclip, Send, ShieldCheck, X, XCircle,
} from 'lucide-react';
import {
    APPLE_FONT, iosCard, iosInput, iosGroupLabel,
    iosBtnPrimary, iosBtnSecondary, iosBtnGhost, IosBadge, IosModal,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';

/* Мастер обращения по сценарию (ТЗ задачи #160).
 *
 * Почему вопросы задаются ПО ОДНОМУ, а не одной длинной формой. Это не про
 * красоту: в тематике «ошибка при подписании» шестнадцать вопросов, и половина
 * из них не понадобится, если на четвёртом выяснится, что документы вообще не
 * отображаются — тогда обращение уходит в другую тематику. Плоская форма
 * заставила бы оператора заполнить всё и только потом узнать, что заполнял зря.
 * Последовательный проход даёт ранний выход ровно там, где его задумало ТЗ.
 *
 * Текст обращения оператор не пишет и не правит: его собирает сервер и
 * показывает в предпросмотре. Это тоже требование ТЗ, а не наша строгость —
 * специалист в группе должен читать один и тот же формат от всех операторов. */

const OUTCOME = {
    READY: 'ready',
    BLOCKED: 'blocked',
    CLOSE: 'close',
    SWITCH: 'switch',
    INCOMPLETE: 'incomplete',
};

// Общие поля, которые переносим при переходе между тематиками: переспрашивать
// ИИН и период после «это другая тематика» — гарантированное раздражение.
const CARRY_OVER = ['iin', 'period', 'park', 'device', 'browser'];

const MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];

const errorText = (error, fallback) => (
    error?.response?.data?.error || error?.message || fallback
);

const answerValue = (answers, key) => {
    const raw = answers[key];
    return raw && typeof raw === 'object' ? raw.value : raw;
};

/* Локальная проверка правил — ровно та же таблица, что у сервера: он присылает
   её вместе со сценарием. Нужна, чтобы «переводим в другую тематику» появлялось
   сразу при ответе, а не после похода на сервер. Решение о самой отправке всё
   равно принимает сервер: правило, которое можно обойти, отключив JavaScript,
   защитой не является. */
const localVerdict = (scenario, answers) => {
    for (const rule of scenario.rules || []) {
        const [key, expected] = rule.when;
        if (answerValue(answers, key) === expected) return rule;
    }
    return null;
};

const isAnswered = (raw) => {
    if (raw === null || raw === undefined) return false;
    if (typeof raw === 'string') return raw.trim().length > 0;
    if (typeof raw === 'object') return Boolean(raw.value);
    return true;
};

const stepIsVisible = (step, answers) => {
    if (!step.depends_on) return true;
    return answerValue(answers, step.depends_on[0]) === step.depends_on[1];
};

/* ─── Поле одного шага ────────────────────────────────────────────────────── */

const StepField = ({ step, value, onChange, onSubmit }) => {
    const raw = value;
    const current = raw && typeof raw === 'object' ? raw.value : raw;
    const detail = raw && typeof raw === 'object' ? raw.detail : '';
    const inputRef = useRef(null);

    // Фокус на поле при каждом шаге: оператор говорит с водителем и печатает,
    // лишний клик мышью здесь — прямая потеря секунд.
    useEffect(() => { inputRef.current?.focus(); }, [step.key]);

    const enterSubmits = (event) => {
        if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); onSubmit(); }
    };

    if (step.kind === 'yesno' || step.kind === 'yesno_date') {
        return (
            <div className="space-y-3">
                <div className="flex gap-2">
                    {[['yes', 'Да'], ['no', 'Нет']].map(([code, label]) => (
                        <button key={code} type="button"
                                onClick={() => onChange(step.kind === 'yesno_date'
                                    ? { value: code, detail: code === 'yes' ? detail || '' : '' }
                                    : code)}
                                className={`flex-1 rounded-xl px-4 py-3 text-[14px] font-semibold transition-all active:scale-[0.98] ${
                                    current === code
                                        ? 'bg-blue-600 text-white shadow-sm'
                                        : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                                }`}>
                            {label}
                        </button>
                    ))}
                </div>
                {step.kind === 'yesno_date' && current === 'yes' && (
                    <div>
                        <div className={iosGroupLabel}>{step.date_label || 'Уточните'}</div>
                        <input ref={inputRef}
                               type={step.date_kind === 'text' ? 'text' : 'date'}
                               value={detail || ''}
                               onChange={(e) => onChange({ value: 'yes', detail: e.target.value })}
                               onKeyDown={enterSubmits}
                               className={`mt-1.5 ${iosInput}`} />
                    </div>
                )}
            </div>
        );
    }

    if (step.kind === 'choice') {
        return (
            <div className="space-y-1.5">
                {(step.options || []).map((option) => (
                    <button key={option} type="button" onClick={() => onChange(option)}
                            className={`flex w-full items-center gap-2.5 rounded-xl px-3.5 py-2.5 text-left text-[13.5px] transition-all active:scale-[0.99] ${
                                current === option
                                    ? 'bg-blue-600 text-white shadow-sm'
                                    : 'bg-slate-100 text-slate-700 hover:bg-slate-200'
                            }`}>
                        <span className={`grid h-4 w-4 shrink-0 place-items-center rounded-full border-2 ${
                            current === option ? 'border-white' : 'border-slate-400'
                        }`}>
                            {current === option && <span className="h-1.5 w-1.5 rounded-full bg-white" />}
                        </span>
                        {option}
                    </button>
                ))}
            </div>
        );
    }

    if (step.kind === 'period') {
        const [year, month] = String(current || '').split('-');
        const now = new Date();
        const years = [now.getFullYear(), now.getFullYear() - 1, now.getFullYear() - 2];
        return (
            <div className="flex gap-2">
                <CustomSelect className="flex-1" variant="ios"
                              value={month || ''}
                              onChange={(next) => onChange(`${year || now.getFullYear()}-${next}`)}
                              options={MONTHS.map((label, index) => ({
                                  value: String(index + 1).padStart(2, '0'), label }))}
                              placeholder="Месяц" ariaLabel="Месяц отчётного периода" />
                <CustomSelect className="w-32" variant="ios"
                              value={year || ''}
                              onChange={(next) => onChange(`${next}-${month || '01'}`)}
                              options={years.map((y) => ({ value: String(y), label: String(y) }))}
                              placeholder="Год" ariaLabel="Год отчётного периода" />
            </div>
        );
    }

    if (step.kind === 'longtext') {
        return (
            <textarea ref={inputRef} value={current || ''} rows={4}
                      onChange={(e) => onChange(e.target.value)}
                      className={`${iosInput} resize-y`} />
        );
    }

    if (step.kind === 'datetime') {
        return (
            <input ref={inputRef} type={step.date_only ? 'date' : 'datetime-local'}
                   value={current || ''} onChange={(e) => onChange(e.target.value)}
                   onKeyDown={enterSubmits} className={iosInput} />
        );
    }

    // iin и text
    return (
        <input ref={inputRef} type="text" value={current || ''}
               inputMode={step.kind === 'iin' ? 'numeric' : undefined}
               maxLength={step.kind === 'iin' ? 12 : undefined}
               onChange={(e) => onChange(step.kind === 'iin'
                   ? e.target.value.replace(/\D/g, '') : e.target.value)}
               onKeyDown={enterSubmits}
               className={`${iosInput} ${step.kind === 'iin' ? 'tabular-nums tracking-wider' : ''}`} />
    );
};

/* ─── Экран исхода: закрыли, перевели или вернули к проверке ──────────────── */

const OutcomeScreen = ({ verdict, scenario, onBack, onSwitch, onClose }) => {
    const closing = verdict.outcome === OUTCOME.CLOSE;
    const switching = verdict.outcome === OUTCOME.SWITCH;
    const Icon = closing ? CheckCircle2 : switching ? ChevronRight : AlertTriangle;
    const tone = closing ? 'text-emerald-600 bg-emerald-50'
        : switching ? 'text-blue-600 bg-blue-50' : 'text-amber-600 bg-amber-50';

    return (
        <div className="flex flex-col items-center gap-3 py-6 text-center">
            <span className={`grid h-12 w-12 place-items-center rounded-full ${tone}`}>
                <Icon size={22} />
            </span>
            <div className="text-[15px] font-semibold text-slate-900">
                {closing ? 'Обращение в группу не отправляем'
                    : switching ? 'Это другая тематика'
                        : 'Сначала выполните проверку'}
            </div>
            <p className="max-w-[420px] text-[13px] leading-relaxed text-slate-600">
                {verdict.message}
            </p>
            <div className="mt-2 flex flex-wrap items-center justify-center gap-2">
                {closing && (
                    <button type="button" onClick={onClose} className={iosBtnPrimary}>
                        Понятно, закрыть
                    </button>
                )}
                {switching && (
                    <button type="button" onClick={() => onSwitch(verdict.switch_to)}
                            className={iosBtnPrimary}>
                        Перейти: {verdict.switch_title}
                    </button>
                )}
                {verdict.outcome === OUTCOME.BLOCKED && (
                    <button type="button" onClick={onBack} className={iosBtnPrimary}>
                        <ShieldCheck size={14} /> Проверил — продолжить
                    </button>
                )}
                <button type="button" onClick={onBack} className={iosBtnSecondary}>
                    <ArrowLeft size={14} /> Вернуться к вопросам
                </button>
            </div>
            {closing && scenario?.status_glossary?.length > 0 && (
                <div className="mt-3 w-full max-w-[460px] rounded-xl bg-slate-50 p-3 text-left">
                    {scenario.status_glossary.map((row) => (
                        <div key={row.status} className="text-[12px] leading-relaxed text-slate-600">
                            <b>{row.status}</b> — {row.meaning}
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
};

/* ─── Мастер целиком ──────────────────────────────────────────────────────── */

export default function TicketWizard({
    open, onClose, catalog, apiBaseUrl, headers, showToast, onCreated,
}) {
    const [scenarioKey, setScenarioKey] = useState('');
    const [phase, setPhase] = useState('pick');   // pick | checks | steps | preview
    const [stepIndex, setStepIndex] = useState(0);
    const [answers, setAnswers] = useState({});
    const [checksConfirmed, setChecksConfirmed] = useState(false);
    const [attachment, setAttachment] = useState(null);
    const [verdict, setVerdict] = useState(null);
    const [preview, setPreview] = useState(null);
    const [busy, setBusy] = useState(false);
    const fileRef = useRef(null);

    const scenario = useMemo(
        () => (catalog || []).find((item) => item.key === scenarioKey) || null,
        [catalog, scenarioKey],
    );

    const steps = useMemo(() => {
        if (!scenario) return [];
        return scenario.steps.filter((step) => stepIsVisible(step, answers));
    }, [scenario, answers]);

    const reset = useCallback(() => {
        setScenarioKey(''); setPhase('pick'); setStepIndex(0); setAnswers({});
        setChecksConfirmed(false); setAttachment(null); setVerdict(null); setPreview(null);
        if (fileRef.current) fileRef.current.value = '';
    }, []);

    useEffect(() => { if (open) reset(); }, [open, reset]);

    const startScenario = (key) => {
        setScenarioKey(key);
        setStepIndex(0);
        setVerdict(null);
        const next = (catalog || []).find((item) => item.key === key);
        setPhase(next?.checks?.length ? 'checks' : 'steps');
    };

    // Переход в другую тематику: общие ответы переносим, остальное спрашиваем заново.
    const switchScenario = (key) => {
        const carried = {};
        for (const field of CARRY_OVER) {
            if (answers[field] !== undefined) carried[field] = answers[field];
        }
        setAnswers(carried);
        setChecksConfirmed(false);
        setAttachment(null);
        if (fileRef.current) fileRef.current.value = '';
        startScenario(key);
    };

    const setAnswer = (key, value) => {
        setAnswers((prev) => ({ ...prev, [key]: value }));
    };

    const currentStep = steps[stepIndex] || null;

    const goNext = () => {
        if (!currentStep) return;
        // Правило могло сработать именно на этом ответе — показываем исход сразу.
        const hit = localVerdict(scenario, answers);
        if (hit) {
            setVerdict({
                outcome: hit.outcome, message: hit.message,
                switch_to: hit.switch_to,
                switch_title: (catalog || []).find((s) => s.key === hit.switch_to)?.title,
            });
            return;
        }
        if (stepIndex + 1 < steps.length) { setStepIndex(stepIndex + 1); return; }
        askServer();
    };

    const goBack = () => {
        setVerdict(null);
        if (phase === 'preview') { setPhase('steps'); setStepIndex(Math.max(0, steps.length - 1)); return; }
        if (stepIndex > 0) { setStepIndex(stepIndex - 1); return; }
        if (scenario?.checks?.length) { setPhase('checks'); return; }
        reset();
    };

    /* Последнее слово — за сервером: он пересчитывает те же правила и собирает
       текст обращения. Клиентская подсветка до этого момента — только подсказка. */
    const askServer = async () => {
        setBusy(true);
        try {
            const response = await axios.post(
                `${apiBaseUrl}/api/crm/scenarios/${scenarioKey}/evaluate`,
                { answers, has_attachment: Boolean(attachment), checks_confirmed: checksConfirmed },
                { headers: headers() },
            );
            const data = response.data;
            if (data.outcome === OUTCOME.READY) {
                setPreview(data.preview);
                setVerdict(null);
                setPhase('preview');
            } else if (data.outcome === OUTCOME.INCOMPLETE) {
                // Возвращаем к первому незаполненному шагу — это точнее, чем
                // общая надпись «заполните всё».
                const missing = data.missing || {};
                const index = steps.findIndex((s) => missing[s.key]);
                if (index >= 0) setStepIndex(index);
                setVerdict({ outcome: OUTCOME.INCOMPLETE, missing, message: data.message });
            } else {
                setVerdict(data);
            }
        } catch (error) {
            showToast?.(errorText(error, 'Не удалось проверить ответы'), 'error');
        } finally {
            setBusy(false);
        }
    };

    const submit = async () => {
        setBusy(true);
        try {
            const form = new FormData();
            form.append('scenario_key', scenarioKey);
            form.append('answers', JSON.stringify(answers));
            form.append('checks_confirmed', checksConfirmed ? '1' : '0');
            if (attachment) form.append('attachment', attachment);
            const response = await axios.post(`${apiBaseUrl}/api/crm/tickets`, form,
                { headers: headers() });
            const created = response.data;
            if (created.delivered) {
                showToast?.(`Обращение №${created.item.id} отправлено в «${created.item.queue_title}»`,
                    'success');
            } else {
                showToast?.(`Обращение №${created.item.id} сохранено, но не ушло в Telegram: ${created.delivery_error}`,
                    'error');
            }
            onCreated?.(created.item.id);
            onClose();
        } catch (error) {
            showToast?.(errorText(error, 'Не удалось отправить обращение'), 'error');
        } finally {
            setBusy(false);
        }
    };

    const needsFile = scenario && scenario.attachment !== 'none';
    const missingNow = verdict?.outcome === OUTCOME.INCOMPLETE ? (verdict.missing || {}) : {};

    /* ── Шапка и футер модалки зависят от фазы ──────────────────────────── */
    const footer = (() => {
        if (phase === 'pick') {
            return <button type="button" onClick={onClose} className={iosBtnSecondary}>Отмена</button>;
        }
        if (phase === 'checks') {
            return (
                <>
                    <button type="button" onClick={reset} className={iosBtnSecondary}>Назад</button>
                    <button type="button" disabled={!checksConfirmed}
                            onClick={() => { setPhase('steps'); setStepIndex(0); }}
                            className={iosBtnPrimary}>
                        <ShieldCheck size={14} /> Проверил — продолжить
                    </button>
                </>
            );
        }
        if (phase === 'preview') {
            return (
                <>
                    <button type="button" onClick={goBack} className={iosBtnSecondary}>
                        <ArrowLeft size={14} /> Изменить ответы
                    </button>
                    <button type="button" onClick={submit} disabled={busy} className={iosBtnPrimary}>
                        {busy ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
                        Подтвердить и отправить
                    </button>
                </>
            );
        }
        if (verdict && verdict.outcome !== OUTCOME.INCOMPLETE) return null;
        const last = stepIndex + 1 >= steps.length;
        const answered = currentStep && (isAnswered(answers[currentStep.key]) || currentStep.optional);
        return (
            <>
                <button type="button" onClick={goBack} className={iosBtnSecondary}>
                    <ArrowLeft size={14} /> Назад
                </button>
                <button type="button" onClick={goNext} disabled={busy || !answered}
                        className={iosBtnPrimary}>
                    {busy ? <Loader2 size={14} className="animate-spin" />
                        : last ? <CheckCircle2 size={14} /> : <ArrowRight size={14} />}
                    {last ? 'К предпросмотру' : 'Далее'}
                </button>
            </>
        );
    })();

    return (
        <IosModal
            open={open}
            onClose={onClose}
            title={scenario ? scenario.title : 'Новое обращение'}
            subtitle={phase === 'pick' ? 'Выберите тематику — дальше система задаст вопросы'
                : phase === 'checks' ? 'Обязательные проверки перед обращением'
                    : phase === 'preview' ? 'Так обращение увидят в группе'
                        : `Вопрос ${Math.min(stepIndex + 1, steps.length)} из ${steps.length}`}
            maxWidth="max-w-xl"
            footer={footer}
        >
            <div style={{ fontFamily: APPLE_FONT }}>
                {/* Полоса прогресса: без неё шестнадцать вопросов ощущаются бесконечными. */}
                {phase === 'steps' && !verdict && steps.length > 1 && (
                    <div className="mb-4 h-1 w-full overflow-hidden rounded-full bg-slate-100">
                        <div className="h-full rounded-full bg-blue-500 transition-all"
                             style={{ width: `${((stepIndex + 1) / steps.length) * 100}%` }} />
                    </div>
                )}

                {phase === 'pick' && (
                    <div className="space-y-2">
                        {(catalog || []).map((item) => (
                            <button key={item.key} type="button"
                                    disabled={!item.is_ready}
                                    onClick={() => startScenario(item.key)}
                                    title={item.is_ready ? undefined
                                        : 'Для этой тематики администратор ещё не привязал Telegram-группу'}
                                    className={`w-full rounded-2xl px-4 py-3 text-left transition-all ${
                                        item.is_ready
                                            ? 'bg-slate-50 hover:bg-slate-100 active:scale-[0.99]'
                                            : 'cursor-not-allowed bg-slate-50/60 opacity-60'
                                    }`}>
                                <div className="flex items-center justify-between gap-2">
                                    <span className="text-[14px] font-semibold text-slate-900">
                                        {item.title}
                                    </span>
                                    {item.is_ready
                                        ? <ChevronRight size={16} className="shrink-0 text-slate-400" />
                                        : <IosBadge tone="amber">Не настроена</IosBadge>}
                                </div>
                                <div className="mt-1 text-[12px] leading-snug text-slate-500">
                                    {item.when_to_use}
                                </div>
                            </button>
                        ))}
                        {!(catalog || []).length && (
                            <div className="py-10 text-center text-[13px] text-slate-400">
                                Тематики не настроены
                            </div>
                        )}
                    </div>
                )}

                {phase === 'checks' && scenario && (
                    <div className="space-y-3">
                        <p className="text-[13px] leading-relaxed text-slate-600">
                            Прежде чем обращаться в группу, выполните проверки. Если вопрос
                            решится по ходу — обращение отправлять не нужно.
                        </p>
                        <div className={`${iosCard} divide-y divide-slate-100`}>
                            {scenario.checks.map((check, index) => (
                                <div key={check} className="flex gap-2.5 px-4 py-2.5">
                                    <span className="mt-[2px] grid h-5 w-5 shrink-0 place-items-center rounded-full bg-slate-100 text-[11px] font-semibold tabular-nums text-slate-500">
                                        {index + 1}
                                    </span>
                                    <span className="text-[13px] leading-relaxed text-slate-700">{check}</span>
                                </div>
                            ))}
                        </div>
                        <label className="flex cursor-pointer items-start gap-2.5 rounded-xl bg-blue-50/60 px-3.5 py-3">
                            <input type="checkbox" checked={checksConfirmed}
                                   onChange={(e) => setChecksConfirmed(e.target.checked)}
                                   className="mt-0.5 h-4 w-4 rounded accent-blue-600" />
                            <span className="text-[13px] leading-snug text-slate-700">
                                Подтверждаю, что выполнил все проверки выше
                            </span>
                        </label>
                    </div>
                )}

                {phase === 'steps' && verdict && verdict.outcome !== OUTCOME.INCOMPLETE && (
                    <OutcomeScreen verdict={verdict} scenario={scenario}
                                   onBack={() => setVerdict(null)}
                                   onSwitch={switchScenario}
                                   onClose={onClose} />
                )}

                {phase === 'steps' && (!verdict || verdict.outcome === OUTCOME.INCOMPLETE)
                    && currentStep && (
                    <div className="space-y-3">
                        <div>
                            <div className="text-[15px] font-semibold leading-snug text-slate-900">
                                {currentStep.label}
                                {currentStep.optional && (
                                    <span className="ml-2 text-[11.5px] font-normal text-slate-400">
                                        необязательно
                                    </span>
                                )}
                            </div>
                            {currentStep.hint && (
                                <div className="mt-1 text-[12px] leading-snug text-slate-500">
                                    {currentStep.hint}
                                </div>
                            )}
                        </div>

                        {currentStep.kind === 'attachment' ? (
                            <div className="space-y-2">
                                <button type="button" onClick={() => fileRef.current?.click()}
                                        className={`${iosCard} flex w-full items-center gap-3 px-4 py-4 text-left transition hover:bg-slate-50`}>
                                    <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-slate-100 text-slate-500">
                                        <Paperclip size={16} />
                                    </span>
                                    <span className="min-w-0 flex-1">
                                        <span className="block truncate text-[13.5px] font-medium text-slate-800">
                                            {attachment ? attachment.name : 'Выбрать файл'}
                                        </span>
                                        <span className="block text-[11.5px] text-slate-500">
                                            {scenario.attachment_hint}
                                        </span>
                                    </span>
                                    {attachment && (
                                        <X size={15} className="shrink-0 text-slate-400"
                                           onClick={(e) => {
                                               e.stopPropagation();
                                               setAttachment(null);
                                               if (fileRef.current) fileRef.current.value = '';
                                           }} />
                                    )}
                                </button>
                                <input ref={fileRef} type="file" className="hidden"
                                       accept={scenario.attachment === 'image'
                                           ? 'image/*' : 'image/*,video/*'}
                                       onChange={(e) => setAttachment(e.target.files?.[0] || null)} />
                                {!attachment && (
                                    <div className="flex items-start gap-1.5 px-1 text-[11.5px] text-amber-600">
                                        <AlertTriangle size={12} className="mt-[2px] shrink-0" />
                                        Без вложения обращение отправить нельзя
                                    </div>
                                )}
                            </div>
                        ) : (
                            <StepField step={currentStep}
                                       value={answers[currentStep.key]}
                                       onChange={(value) => setAnswer(currentStep.key, value)}
                                       onSubmit={goNext} />
                        )}

                        {missingNow[currentStep.key] && (
                            <div className="flex items-center gap-1.5 px-1 text-[12px] text-rose-600">
                                <XCircle size={13} /> {missingNow[currentStep.key]}
                            </div>
                        )}

                        {/* Уже отвеченное — свёрнутой лентой: оператор видит, что
                            накопилось, и может вернуться, не теряя место. */}
                        {stepIndex > 0 && (
                            <div className="mt-4 border-t border-slate-100 pt-3">
                                <div className={iosGroupLabel}>Уже ответили</div>
                                <div className="mt-1.5 space-y-1">
                                    {steps.slice(0, stepIndex).map((item, index) => (
                                        <button key={item.key} type="button"
                                                onClick={() => { setVerdict(null); setStepIndex(index); }}
                                                className="flex w-full items-baseline gap-2 rounded-lg px-1 py-0.5 text-left text-[11.5px] transition hover:bg-slate-50">
                                            <span className="truncate text-slate-500">{item.label}</span>
                                            <span className="ml-auto shrink-0 font-medium text-slate-700">
                                                {item.kind === 'yesno' || item.kind === 'yesno_date'
                                                    ? (answerValue(answers, item.key) === 'yes' ? 'да' : 'нет')
                                                    : String(answerValue(answers, item.key) || '—').slice(0, 28)}
                                            </span>
                                        </button>
                                    ))}
                                </div>
                            </div>
                        )}
                    </div>
                )}

                {phase === 'preview' && preview && (
                    <div className="space-y-3">
                        <div className="flex items-start gap-2 rounded-xl bg-blue-50/70 px-3.5 py-2.5 text-[12px] leading-snug text-slate-600">
                            <ShieldCheck size={14} className="mt-[1px] shrink-0 text-blue-600" />
                            Текст собран системой по вашим ответам и вручную не редактируется —
                            так специалист получает одинаковую выжимку от всех операторов.
                        </div>
                        <div>
                            <div className={iosGroupLabel}>Тема</div>
                            <div className="mt-1.5 rounded-xl bg-slate-100 px-3.5 py-2.5 text-[13.5px] font-medium text-slate-800">
                                {preview.subject}
                            </div>
                        </div>
                        <div>
                            <div className={iosGroupLabel}>Сообщение в группу</div>
                            <pre className="mt-1.5 whitespace-pre-wrap break-words rounded-xl bg-slate-100 px-3.5 py-3 font-sans text-[13px] leading-relaxed text-slate-800">
{preview.body}
                            </pre>
                        </div>
                        {attachment && (
                            <div className="flex items-center gap-1.5 px-1 text-[12px] text-slate-500">
                                <Paperclip size={12} /> {attachment.name}
                            </div>
                        )}
                    </div>
                )}
            </div>
        </IosModal>
    );
}
