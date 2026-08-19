import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    AlertTriangle, ArrowLeft, ArrowRight, CheckCircle2, ChevronRight, Loader2,
    Paperclip, Send, ShieldCheck, X,
} from 'lucide-react';
import {
    APPLE_FONT, iosCard, iosInput, iosGroupLabel,
    iosBtnPrimary, iosBtnSecondary, IosBadge, IosModal,
} from '../ui/ios';
import InfoHint from '../common/InfoHint';
import CustomSelect from '../ui/CustomSelect';
import {
    MISSING_ATTACHMENT, answerValue, carryOver, groupCatalog, groupIsComplete,
    groupsOf, localVerdict, missingGroup, referenceOptions, rowsOfGroup,
    stepIsComplete,
} from './wizardRules';

/* Мастер обращения по сценарию (ТЗ задачи #160).
 *
 * Вопросы задаются экранами, а не по одному. ТЗ требует последовательности — и
 * это про порядок и про ранний выход, а не про «один вопрос на экран»: поштучно
 * выходило до восемнадцати нажатий «Далее» во время разговора с водителем.
 * Порядок сохранён, правила срабатывают сразу на ответе, но спрашиваем блоками:
 * кто и за какой период → что происходит → устройство → что уже сделали →
 * вложение. Блоки одинаковы во всех тематиках, чтобы оператор не искал каждый
 * раз, с чего тут начинают.
 *
 * Исход, который не заканчивает сценарий (вернуть к проверке, перевести в другую
 * тематику), показывается ПОЛОСОЙ прямо под вопросами. Раньше он подменял собой
 * весь экран, и оператора будто выбрасывало из работы. Полноэкранным остался
 * только тот случай, когда сценарий действительно закончился.
 *
 * Пояснения живут под «i». Их много и они длинные — постоянно на экране это шум,
 * из-за которого не видно самих вопросов. */

const OUTCOME = {
    READY: 'ready',
    BLOCKED: 'blocked',
    CLOSE: 'close',
    SWITCH: 'switch',
    INCOMPLETE: 'incomplete',
};

const MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];

const errorText = (error, fallback) => (
    error?.response?.data?.error || error?.message || fallback
);

/* ─── Поле одного вопроса ─────────────────────────────────────────────────── */

const Field = ({ step, value, onChange, autoFocus, problem, options = null }) => {
    const raw = value;
    const current = raw && typeof raw === 'object' ? raw.value : raw;
    const detail = raw && typeof raw === 'object' ? raw.detail : '';
    const inputRef = useRef(null);

    useEffect(() => { if (autoFocus) inputRef.current?.focus(); }, [autoFocus, step.key]);

    const control = (() => {
        // Вопрос из справочника: тот же селектор с поиском, что в остальном
        // портале. Пятнадцать парков и полторы сотни городов руками не листают.
        if (options) {
            return (
                <CustomSelect variant="ios" value={current || ''} options={options}
                              onChange={onChange} searchable
                              placeholder={step.placeholder || 'Выберите'}
                              searchPlaceholder={`Поиск: ${step.label.toLowerCase()}`}
                              ariaLabel={step.label} />
            );
        }
        if (step.kind === 'yesno' || step.kind === 'yesno_date') {
            return (
                <div className="space-y-2">
                    <div className="flex flex-wrap gap-2">
                        {/* Третий вариант — только там, где вопрос его допускает
                            (step.allow_unknown): оператор не всегда может
                            проверить ответ сам, но выдумывать «нет» он не должен. */}
                        {[['yes', 'Да'], ['no', 'Нет'],
                            ...(step.allow_unknown ? [['unknown', 'Неизвестно']] : [])
                        ].map(([code, label]) => (
                            <button key={code} type="button"
                                    onClick={() => onChange(step.kind === 'yesno_date'
                                        ? { value: code, detail: code === 'yes' ? detail || '' : '' }
                                        : code)}
                                    className={`min-w-[84px] rounded-xl px-4 py-2 text-[13.5px] font-semibold transition-all active:scale-[0.98] ${
                                        current === code
                                            ? 'bg-blue-600 text-white shadow-sm'
                                            : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                                    }`}>
                                {label}
                            </button>
                        ))}
                    </div>
                    {step.kind === 'yesno_date' && current === 'yes' && (
                        <input ref={inputRef}
                               type={step.date_kind === 'text' ? 'text' : 'date'}
                               value={detail || ''}
                               placeholder={step.date_label || 'Уточните'}
                               onChange={(e) => onChange({ value: 'yes', detail: e.target.value })}
                               className={iosInput} />
                    )}
                </div>
            );
        }
        if (step.kind === 'choice') {
            return (
                <div className="flex flex-wrap gap-1.5">
                    {(step.options || []).map((option) => (
                        <button key={option} type="button" onClick={() => onChange(option)}
                                className={`rounded-xl px-3 py-1.5 text-[12.5px] font-medium transition-all active:scale-[0.98] ${
                                    current === option
                                        ? 'bg-blue-600 text-white shadow-sm'
                                        : 'bg-slate-100 text-slate-700 hover:bg-slate-200'
                                }`}>
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
                    <CustomSelect className="flex-1" variant="ios" value={month || ''}
                                  onChange={(next) => onChange(`${year || now.getFullYear()}-${next}`)}
                                  options={MONTHS.map((label, index) => ({
                                      value: String(index + 1).padStart(2, '0'), label }))}
                                  placeholder="Месяц" ariaLabel="Месяц отчётного периода" />
                    <CustomSelect className="w-28" variant="ios" value={year || ''}
                                  onChange={(next) => onChange(`${next}-${month || '01'}`)}
                                  options={years.map((y) => ({ value: String(y), label: String(y) }))}
                                  placeholder="Год" ariaLabel="Год отчётного периода" />
                </div>
            );
        }
        if (step.kind === 'longtext') {
            return (
                <textarea ref={inputRef} value={current || ''} rows={3}
                          placeholder={step.placeholder || undefined}
                          onChange={(e) => onChange(e.target.value)}
                          className={`${iosInput} resize-y`} />
            );
        }
        if (step.kind === 'datetime') {
            return (
                <input ref={inputRef} type={step.date_only ? 'date' : 'datetime-local'}
                       value={current || ''} onChange={(e) => onChange(e.target.value)}
                       className={iosInput} />
            );
        }
        return (
            <input ref={inputRef} type="text" value={current || ''}
                   /* Пример показываем подсказкой в самом поле: он виден, пока
                      поле пустое, и исчезает при вводе — постоянного места на
                      экране не занимает. */
                   placeholder={step.placeholder || undefined}
                   inputMode={step.kind === 'iin' ? 'numeric' : undefined}
                   maxLength={step.kind === 'iin' ? 12 : undefined}
                   onChange={(e) => onChange(step.kind === 'iin'
                       ? e.target.value.replace(/\D/g, '') : e.target.value)}
                   className={`${iosInput} ${step.kind === 'iin' ? 'tabular-nums tracking-wider' : ''}`} />
        );
    })();

    /* Колонка на всю высоту ячейки, поле прижато книзу: в строке из двух вопросов
       подписи бывают разной длины, и без этого одно поле оказывалось на строку
       ниже другого. В строке из одного вопроса ни то, ни другое ничего не меняет. */
    return (
        <div className="flex h-full flex-col">
            <div className="mb-1.5 flex items-center gap-1.5">
                <span className="text-[13px] font-medium leading-snug text-slate-800">
                    {step.label}
                </span>
                {/* Подсказка под «i»: она нужна раз в жизни, а место занимала бы всегда. */}
                {step.hint && <InfoHint side="left">{step.hint}</InfoHint>}
                {step.optional && (
                    <span className="text-[11px] text-slate-400">необязательно</span>
                )}
            </div>
            <div className="mt-auto">{control}</div>
            {problem && (
                <div className="mt-1 text-[11.5px] text-rose-600">{problem}</div>
            )}
        </div>
    );
};

/* ─── Полоса исхода: не заканчивает сценарий, значит не занимает экран ────── */

const OutcomeBar = ({ verdict, onDismiss, onSwitch }) => {
    const switching = verdict.outcome === OUTCOME.SWITCH;
    return (
        <div className={`rounded-2xl px-3.5 py-3 ring-1 ${
            switching ? 'bg-blue-50/70 ring-blue-100' : 'bg-amber-50/70 ring-amber-100'
        }`}>
            <div className="flex items-start gap-2.5">
                <span className={`mt-[1px] shrink-0 ${switching ? 'text-blue-600' : 'text-amber-600'}`}>
                    {switching ? <ChevronRight size={16} /> : <AlertTriangle size={16} />}
                </span>
                <div className="min-w-0 flex-1">
                    <div className="text-[13px] leading-relaxed text-slate-700">
                        {verdict.message}
                    </div>
                    <div className="mt-2 flex flex-wrap gap-2">
                        {switching ? (
                            <button type="button" onClick={() => onSwitch(verdict.switch_to)}
                                    className={iosBtnPrimary}>
                                Перейти: {verdict.switch_title}
                            </button>
                        ) : (
                            <button type="button" onClick={onDismiss} className={iosBtnPrimary}>
                                <ShieldCheck size={14} /> Проверил — продолжить
                            </button>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
};

/* ─── Сценарий закончился: единственный случай, когда занимаем весь экран ── */

const ClosedScreen = ({ verdict, onClose }) => (
    <div className="flex flex-col items-center gap-3 py-8 text-center">
        <span className="grid h-12 w-12 place-items-center rounded-full bg-emerald-50 text-emerald-600">
            <CheckCircle2 size={22} />
        </span>
        <div className="text-[15px] font-semibold text-slate-900">
            Обращение в группу не отправляем
        </div>
        <p className="max-w-[420px] text-[13px] leading-relaxed text-slate-600">
            {verdict.message}
        </p>
        <button type="button" onClick={onClose} className={`mt-1 ${iosBtnPrimary}`}>
            Понятно
        </button>
    </div>
);

/* ─── Мастер ──────────────────────────────────────────────────────────────── */

export default function TicketWizard({
    open, onClose, catalog, taxiParks = [], apiBaseUrl, headers, showToast, onCreated,
}) {
    const [scenarioKey, setScenarioKey] = useState('');
    const [phase, setPhase] = useState('pick');   // pick | checks | form | preview | closed
    const [groupIndex, setGroupIndex] = useState(0);
    const [answers, setAnswers] = useState({});
    const [checksConfirmed, setChecksConfirmed] = useState(false);
    const [attachment, setAttachment] = useState(null);
    const [verdict, setVerdict] = useState(null);
    const [dismissed, setDismissed] = useState(null);
    const [missing, setMissing] = useState({});
    const [preview, setPreview] = useState(null);
    const [busy, setBusy] = useState(false);
    const fileRef = useRef(null);

    const scenario = useMemo(
        () => (catalog || []).find((item) => item.key === scenarioKey) || null,
        [catalog, scenarioKey],
    );

    const catalogGroups = useMemo(() => groupCatalog(catalog), [catalog]);
    const groups = useMemo(() => groupsOf(scenario, answers), [scenario, answers]);
    const group = groups[groupIndex] || null;
    const groupRows = useMemo(
        () => rowsOfGroup(scenario, group, answers),
        [scenario, group, answers],
    );

    const reset = useCallback(() => {
        setScenarioKey(''); setPhase('pick'); setGroupIndex(0); setAnswers({});
        setChecksConfirmed(false); setAttachment(null); setVerdict(null);
        setDismissed(null); setMissing({}); setPreview(null);
        if (fileRef.current) fileRef.current.value = '';
    }, []);

    useEffect(() => { if (open) reset(); }, [open, reset]);

    /* Правила проверяются на КАЖДОМ ответе, а не по кнопке «Далее»: смысл
       раннего выхода в том, чтобы оператор узнал «этого делать не нужно» сразу,
       а не пройдя ещё десяток вопросов. Отклонённую полосу не показываем
       повторно, пока ответ не изменится. */
    useEffect(() => {
        if (!scenario || phase !== 'form') return;
        const hit = localVerdict(scenario, answers);
        if (!hit) { setVerdict(null); return; }
        const signature = `${hit.when[0]}:${hit.when[1]}`;
        if (hit.outcome === OUTCOME.CLOSE) {
            setVerdict({ ...hit, signature });
            setPhase('closed');
            return;
        }
        setVerdict(dismissed === signature ? null : {
            ...hit,
            signature,
            switch_title: (catalog || []).find((s) => s.key === hit.switch_to)?.title,
        });
    }, [answers, scenario, phase, dismissed, catalog]);

    const startScenario = (key) => {
        const next = (catalog || []).find((item) => item.key === key);
        setScenarioKey(key);
        setGroupIndex(0);
        setVerdict(null);
        setDismissed(null);
        setMissing({});
        setPhase(next?.checks?.length ? 'checks' : 'form');
    };

    const switchScenario = (key) => {
        setAnswers(carryOver(answers));
        setChecksConfirmed(false);
        setAttachment(null);
        if (fileRef.current) fileRef.current.value = '';
        startScenario(key);
    };

    const setAnswer = (key, value) => {
        setMissing((prev) => (prev[key] ? { ...prev, [key]: undefined } : prev));
        setAnswers((prev) => ({ ...prev, [key]: value }));
    };

    const canLeaveGroup = scenario && group
        && groupIsComplete(scenario, group, { answers, attachment });

    const goNext = () => {
        if (groupIndex + 1 < groups.length) { setGroupIndex(groupIndex + 1); return; }
        askServer();
    };

    const goBack = () => {
        setVerdict(null);
        if (phase === 'preview') { setPhase('form'); setGroupIndex(groups.length - 1); return; }
        if (groupIndex > 0) { setGroupIndex(groupIndex - 1); return; }
        if (scenario?.checks?.length) { setPhase('checks'); return; }
        reset();
    };

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
                setMissing({});
                setPhase('preview');
            } else if (data.outcome === OUTCOME.INCOMPLETE) {
                const target = missingGroup(scenario, answers, data.missing || {});
                setMissing(data.missing || {});
                if (target.phase === 'checks') {
                    setChecksConfirmed(false);
                    setPhase('checks');
                } else if (target.group) {
                    setGroupIndex(Math.max(0, groups.indexOf(target.group)));
                }
                showToast?.(target.message || data.message || 'Заполнены не все данные', 'error');
            } else if (data.outcome === OUTCOME.CLOSE) {
                setVerdict(data);
                setPhase('closed');
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
            showToast?.(created.delivered
                ? `Обращение №${created.item.id} отправлено в «${created.item.queue_title}»`
                : `Обращение №${created.item.id} сохранено, но не ушло в Telegram: ${created.delivery_error}`,
            created.delivered ? 'success' : 'error');
            onCreated?.(created.item.id);
            onClose();
        } catch (error) {
            showToast?.(errorText(error, 'Не удалось отправить обращение'), 'error');
        } finally {
            setBusy(false);
        }
    };

    const footer = (() => {
        if (phase === 'pick' || phase === 'closed') {
            return <button type="button" onClick={onClose} className={iosBtnSecondary}>Закрыть</button>;
        }
        if (phase === 'checks') {
            return (
                <>
                    <button type="button" onClick={reset} className={iosBtnSecondary}>Назад</button>
                    <button type="button" disabled={!checksConfirmed}
                            onClick={() => { setPhase('form'); setGroupIndex(0); }}
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
                        <ArrowLeft size={14} /> Изменить
                    </button>
                    <button type="button" onClick={submit} disabled={busy} className={iosBtnPrimary}>
                        {busy ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
                        Подтвердить и отправить
                    </button>
                </>
            );
        }
        const last = groupIndex + 1 >= groups.length;
        return (
            <>
                <button type="button" onClick={goBack} className={iosBtnSecondary}>
                    <ArrowLeft size={14} /> Назад
                </button>
                <button type="button" onClick={goNext} disabled={busy || !canLeaveGroup}
                        className={iosBtnPrimary}>
                    {busy ? <Loader2 size={14} className="animate-spin" />
                        : last ? <CheckCircle2 size={14} /> : <ArrowRight size={14} />}
                    {last ? 'Проверить и отправить' : 'Далее'}
                </button>
            </>
        );
    })();

    const subtitle = phase === 'pick' ? 'Выберите тематику — дальше система задаст вопросы'
        : phase === 'checks' ? 'Проверьте это до обращения'
            : phase === 'preview' ? 'Так обращение увидят в группе'
                : phase === 'closed' ? null
                    : `${group} · шаг ${groupIndex + 1} из ${groups.length}`;

    return (
        <IosModal open={open} onClose={onClose}
                  title={scenario ? scenario.title : 'Новое обращение'}
                  subtitle={subtitle} maxWidth="max-w-xl" footer={footer}>
            <div style={{ fontFamily: APPLE_FONT }}>
                {phase === 'form' && groups.length > 1 && (
                    <div className="mb-4 h-1 w-full overflow-hidden rounded-full bg-slate-100">
                        <div className="h-full rounded-full bg-blue-500 transition-all"
                             style={{ width: `${((groupIndex + 1) / groups.length) * 100}%` }} />
                    </div>
                )}

                {phase === 'pick' && (
                    <div className="space-y-4">
                        {/* Тематики сгруппированы по рабочей группе-получателю:
                            оператор сразу видит, кого побеспокоит обращение, а
                            заголовок один на группу — вместо повторения её
                            названия в каждой строке. */}
                        {catalogGroups.map((group) => (
                            <div key={group.code}>
                                {catalogGroups.length > 1 && group.title && (
                                    <div className={`${iosGroupLabel} mb-1.5`}>{group.title}</div>
                                )}
                                <div className="space-y-1.5">
                                    {group.items.map((item) => (
                                        <button key={item.key} type="button" disabled={!item.is_ready}
                                                onClick={() => startScenario(item.key)}
                                                className={`flex w-full items-center gap-2 rounded-2xl px-4 py-3 text-left transition-all ${
                                                    item.is_ready
                                                        ? 'bg-slate-50 hover:bg-slate-100 active:scale-[0.99]'
                                                        : 'cursor-not-allowed bg-slate-50/60 opacity-60'
                                                }`}>
                                            <span className="min-w-0 flex-1 text-[14px] font-medium text-slate-900">
                                                {item.title}
                                            </span>
                                            {/* «Когда используется» — под «i»: в списке
                                                тематик столько же абзацев мешают выбирать. */}
                                            <InfoHint title={item.title}>{item.when_to_use}</InfoHint>
                                            {item.is_ready
                                                ? <ChevronRight size={15} className="shrink-0 text-slate-400" />
                                                : <IosBadge tone="amber">Нет группы</IosBadge>}
                                        </button>
                                    ))}
                                </div>
                            </div>
                        ))}
                        {!catalogGroups.length && (
                            <div className="py-10 text-center text-[13px] text-slate-400">
                                Тематики не настроены
                            </div>
                        )}
                    </div>
                )}

                {phase === 'checks' && scenario && (
                    <div className="space-y-3">
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

                        {scenario.status_glossary?.length > 0 && (
                            <div className={`${iosCard} p-4`}>
                                <div className="flex items-center gap-1.5">
                                    <span className={iosGroupLabel}>Что означают статусы</span>
                                    <InfoHint side="left">
                                        Пригодится, когда специалист ответит: чтобы всем водителям
                                        объясняли одинаково.
                                    </InfoHint>
                                </div>
                                <div className="mt-2 space-y-1.5">
                                    {scenario.status_glossary.map((row) => (
                                        <div key={row.status} className="text-[12.5px] leading-relaxed text-slate-600">
                                            <b className="text-slate-800">{row.status}</b> — {row.meaning}
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}

                        <label className="flex cursor-pointer items-start gap-2.5 rounded-xl bg-blue-50/60 px-3.5 py-3">
                            <input type="checkbox" checked={checksConfirmed}
                                   onChange={(e) => setChecksConfirmed(e.target.checked)}
                                   className="mt-0.5 h-4 w-4 rounded accent-blue-600" />
                            <span className="text-[13px] leading-snug text-slate-700">
                                Подтверждаю, что выполнил проверки
                            </span>
                            <InfoHint side="left">
                                Если вопрос решился по ходу проверок — обращение отправлять не нужно,
                                просто закройте это окно.
                            </InfoHint>
                        </label>
                    </div>
                )}

                {phase === 'closed' && verdict && (
                    <ClosedScreen verdict={verdict} onClose={onClose} />
                )}

                {phase === 'form' && scenario && (
                    <div className="space-y-4">
                        {verdict && (
                            <OutcomeBar verdict={verdict}
                                        onDismiss={() => { setDismissed(verdict.signature); setVerdict(null); }}
                                        onSwitch={switchScenario} />
                        )}

                        {groupRows.map((row, rowIndex) => (
                            /* Пара половинных вопросов — одной строкой; на узком
                               экране всё равно друг под другом. */
                            <div key={row.map((s) => s.key).join('+')}
                                 className={row.length > 1
                                     ? 'grid grid-cols-1 gap-4 sm:grid-cols-2' : undefined}>
                                {row.map((step) => (
                            step.kind === 'attachment' ? (
                                <div key={step.key}>
                                    <div className="mb-1.5 flex items-center gap-1.5">
                                        <span className="text-[13px] font-medium text-slate-800">
                                            {step.label}
                                        </span>
                                        {scenario.attachment_hint && (
                                            <InfoHint side="left">{scenario.attachment_hint}</InfoHint>
                                        )}
                                    </div>
                                    <button type="button" onClick={() => fileRef.current?.click()}
                                            className={`${iosCard} flex w-full items-center gap-3 px-4 py-3.5 text-left transition hover:bg-slate-50`}>
                                        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-slate-100 text-slate-500">
                                            <Paperclip size={16} />
                                        </span>
                                        <span className="min-w-0 flex-1 truncate text-[13.5px] text-slate-800">
                                            {attachment ? attachment.name : 'Выбрать файл'}
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
                                        <div className="mt-1 text-[11.5px] text-amber-600">
                                            {missing[MISSING_ATTACHMENT] || 'Без вложения обращение отправить нельзя'}
                                        </div>
                                    )}
                                </div>
                            ) : (
                                <Field key={step.key} step={step} value={answers[step.key]}
                                       autoFocus={rowIndex === 0 && step === row[0]}
                                       problem={missing[step.key]}
                                       options={referenceOptions(step, { taxiParks })}
                                       onChange={(value) => setAnswer(step.key, value)} />
                            )
                                ))}
                            </div>
                        ))}
                    </div>
                )}

                {phase === 'preview' && preview && (
                    <div className="space-y-3">
                        <div>
                            <div className="flex items-center gap-1.5">
                                <span className={iosGroupLabel}>Тема</span>
                                <InfoHint side="left">
                                    Текст собирает система по вашим ответам и вручную он не
                                    редактируется — так специалист получает одинаковую выжимку
                                    от всех операторов.
                                </InfoHint>
                            </div>
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
