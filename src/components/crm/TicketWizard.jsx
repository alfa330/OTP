import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    AlertTriangle, ArrowLeft, ArrowRight, CheckCircle2, ChevronRight, Copy,
    CornerDownRight, Loader2, Paperclip, Send, ShieldCheck, UserCheck, X,
} from 'lucide-react';
import {
    APPLE_FONT, iosCard, iosInput, iosGroupLabel,
    iosBtnPrimary, iosBtnSecondary, IosBadge, IosModal,
} from '../ui/ios';
import InfoHint from '../common/InfoHint';
import CustomSelect from '../ui/CustomSelect';
import {
    CHECKS_AFTER_GROUP, MISSING_ATTACHMENT, afterCategory, afterChecks, answerValue,
    blockedLabel, carryOver, checksAreComplete, checksPayload, describeSnapshot,
    entryCategories, entryIsComplete, groupCatalog, groupIsComplete, groupsOf, hasChecks,
    localVerdict, lookupKey, missingGroup, needsLookup, needsSaparCheck, nextStop,
    officeOptions, openStop, pairRows, periodLabel, periodOptions, previousStop,
    referenceOptions, routeNote, rowsOfGroup, saparKey, stepIsComplete, toggleCheck,
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
    PASS: 'pass',
    INCOMPLETE: 'incomplete',
    // Разобраться может только супервайзер: обращение никуда не уходит, оператор
    // передаёт ему готовый набор данных (инструкция #230, §4).
    ESCALATE: 'escalate',
};

// Справочники компании, по которым проверяются тематики регионов (ТЗ #201).
const LOOKUP = { PARCELS: 'parcels', OFFICES: 'offices' };

// Исходы проверки по ИИН — до выбора категории.
const ENTRY = {
    DOCUMENTS: 'documents',
    NO_DOCUMENTS: 'no_documents',
    CLOSE: 'close',
    UNKNOWN: 'unknown',
};

/* Статус офиса → тон бейджа. Значения те же, что на вкладке «Офисы» в вики:
   зелёный «Открыт», красный «Закрыт», серый — «нет офиса» и «нет графика», про
   которые сказать нечего. */
const OFFICE_TONE = { open: 'green', closed: 'red', absent: 'slate', none: 'slate' };

const errorText = (error, fallback) => (
    error?.response?.data?.error || error?.message || fallback
);

/* '2026-08-31' → '31.08'. Год не пишем: закрытие офиса «до» — это дни, а не
   годы, и четыре лишних знака в строке ничего не уточняют. */
const formatDayShort = (iso) => {
    const found = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || '').trim());
    return found ? `${found[3]}.${found[2]}` : '';
};

/* ─── Поле одного вопроса ─────────────────────────────────────────────────── */

const Field = ({ step, value, onChange, autoFocus, problem, options = null,
                offices = null }) => {
    const raw = value;
    const current = raw && typeof raw === 'object' ? raw.value : raw;
    const detail = raw && typeof raw === 'object' ? raw.detail : '';
    const inputRef = useRef(null);

    useEffect(() => { if (autoFocus) inputRef.current?.focus(); }, [autoFocus, step.key]);

    const control = (() => {
        /* Офисы города — списком, а не выпадашкой, и это не украшение: статус
           работы офиса и есть та самая обязательная проверка (§3.2 ТЗ). В
           закрытом селекте оператор увидел бы его только после выбора, то есть
           уже сделав выбор вслепую. Офисов в городе от одного до четырёх —
           список умещается целиком. */
        if (offices) {
            if (!offices.length) {
                return (
                    <div className="rounded-xl bg-slate-50 px-3.5 py-3 text-[13px] text-slate-500">
                        Выберите город — покажем офисы и их статус на сегодня.
                    </div>
                );
            }
            return (
                <div className="space-y-1.5">
                    {offices.map((office) => {
                        const picked = String(current || '') === String(office.id);
                        return (
                            <button key={office.id} type="button"
                                    aria-pressed={picked}
                                    onClick={() => onChange(String(office.id))}
                                    className={`w-full rounded-xl px-3.5 py-2.5 text-left transition-all active:scale-[0.99] ${
                                        picked
                                            ? 'bg-blue-50 ring-1 ring-blue-200'
                                            : 'bg-slate-50 hover:bg-slate-100'
                                    }`}>
                                <span className="flex items-start justify-between gap-2">
                                    <span className="min-w-0">
                                        <span className="block text-[13.5px] font-medium text-slate-900">
                                            {office.name}
                                        </span>
                                        {office.address && (
                                            <span className="mt-0.5 block text-[12.5px] leading-snug text-slate-500">
                                                {office.address}
                                            </span>
                                        )}
                                    </span>
                                    <IosBadge tone={OFFICE_TONE[office.state] || 'slate'}>
                                        {office.label}
                                    </IosBadge>
                                </span>
                                {/* Причина закрытия — то, что оператор и передаст
                                    водителю вместо обращения в группу. */}
                                {(office.note || office.closed_until) && (
                                    <span className="mt-1 block text-[12px] leading-snug text-slate-500">
                                        {[office.note, office.closed_until
                                            ? `до ${formatDayShort(office.closed_until)}` : null]
                                            .filter(Boolean).join(' · ')}
                                    </span>
                                )}
                            </button>
                        );
                    })}
                </div>
            );
        }
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
            /* Один список вместо «месяц + год», и начинается он с ПРОШЛОГО
               месяца: отчётный период — это месяц, ЗА который документы, а
               документы за июль подписывают в августе. Раньше список шёл с
               января, и до нужного месяца приходилось листать.

               Периода, которого нет в списке (старое обращение), не теряем:
               добавляем его отдельной строкой, иначе открытие такой карточки
               молча стирало бы ответ. */
            const options = periodOptions();
            const known = current && options.some((item) => item.value === current);
            return (
                <CustomSelect variant="ios" value={current || ''}
                              onChange={(next) => onChange(next)}
                              options={known || !current ? options
                                  : [{ value: current, label: periodLabel(current) || current },
                                      ...options]}
                              searchable={true}
                              placeholder="Выберите месяц"
                              ariaLabel="Отчётный период" />
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

/* Telegram-группа уведённой темы — строкой под её названием.
 *
 * Оператору это не настройка, а предупреждение: обращение по этой теме уйдёт
 * не тем людям, что остальные из этой тематики. Молчать нельзя — он выбирает
 * тему, глядя на заголовок раздела, — но и в бейдж выносить незачем: адрес
 * читают один раз, а бейджи тянут взгляд постоянно.
 */
const TopicRoute = ({ item }) => {
    const target = routeNote(item);
    if (!target) return null;
    return (
        <span className="mt-0.5 flex items-center gap-1 text-[11.5px] text-slate-500">
            <CornerDownRight size={11} className="shrink-0 text-slate-400" />
            <span className="truncate">Уйдёт в группу «{target}»</span>
        </span>
    );
};

/* Полоса исхода умеет показать и НАЙДЕННОЕ — записи реестра невостребованных
   посылок. Без них «в реестре есть запись» это утверждение, которое оператору
   нечем проверить: та ли это посылка, решает он, а решать не по чему. */
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
                    {(verdict.items || []).length > 0 && (
                        <ul className="mt-2 space-y-1">
                            {verdict.items.map((item) => (
                                <li key={item}
                                    className="rounded-xl bg-white/70 px-3 py-2 text-[12.5px] leading-snug text-slate-700">
                                    {item}
                                </li>
                            ))}
                        </ul>
                    )}
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

/* Что Sapar ответил про водителя. Показывается на всех следующих экранах:
 * оператор отвечает на вопросы, глядя на эти данные, и прятать их за кнопку
 * значило бы заставлять его помнить их наизусть.
 *
 * Тон несёт смысл: «документы есть» и «документов нет» — противоположные
 * ответы, и одинаково серыми их показывать нельзя. Отдельный тон у молчания
 * сервиса: это не ответ, а его отсутствие. */
const SAPAR_TONE = {
    green: { box: 'bg-emerald-50/70 ring-emerald-100', icon: 'text-emerald-600' },
    amber: { box: 'bg-amber-50/70 ring-amber-100', icon: 'text-amber-600' },
    muted: { box: 'bg-slate-50 ring-slate-200/70', icon: 'text-slate-400' },
};

const SaparNote = ({ snapshot }) => {
    const note = describeSnapshot(snapshot);
    const tone = SAPAR_TONE[note.tone] || SAPAR_TONE.muted;
    return (
        <div className={`flex items-start gap-2.5 rounded-2xl px-3.5 py-3 ring-1 ${tone.box}`}>
            <span className={`mt-[1px] shrink-0 ${tone.icon}`}>
                {note.tone === 'green' ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
            </span>
            <div className="min-w-0 space-y-0.5">
                <div className="text-[13px] font-semibold text-slate-800">
                    Sapar: {note.title}
                </div>
                {note.lines.map((line) => (
                    <div key={line} className="text-[12.5px] leading-snug text-slate-600">{line}</div>
                ))}
            </div>
        </div>
    );
};


const ClosedScreen = ({ verdict, onClose }) => {
    /* Консультация — не украшение к сообщению об исходе, а то, ради чего этот
       экран и открылся: оператор проговаривает водителю именно эти пункты
       (инструкция #230, §3.2). Поэтому список, а не абзац: по нему идут сверху
       вниз во время разговора. */
    const script = verdict.script || [];
    return (
        <div className="flex flex-col items-center gap-3 py-6 text-center">
            <span className="grid h-12 w-12 place-items-center rounded-full bg-emerald-50 text-emerald-600">
                <CheckCircle2 size={22} />
            </span>
            <div className="text-[15px] font-semibold text-slate-900">
                Обращение в группу не отправляем
            </div>
            <p className="max-w-[440px] text-[13px] leading-relaxed text-slate-600">
                {verdict.message}
            </p>
            {/* То, на основании чего исход и получился: офисы города со
                статусами. Строками, а не фразой через точки — оператор читает
                это водителю. */}
            {(verdict.items || []).length > 0 && (
                <div className={`${iosCard} w-full divide-y divide-slate-100 text-left`}>
                    {verdict.items.map((item) => (
                        <div key={item} className="px-4 py-2.5 text-[13px] leading-relaxed text-slate-700">
                            {item}
                        </div>
                    ))}
                </div>
            )}
            {script.length > 0 && (
                <div className={`${iosCard} mt-1 w-full divide-y divide-slate-100 text-left`}>
                    <div className="px-4 py-2.5">
                        <span className={iosGroupLabel}>Что сказать водителю</span>
                    </div>
                    {script.map((line, index) => (
                        <div key={line} className="flex gap-2.5 px-4 py-2.5">
                            <span className="mt-[2px] grid h-5 w-5 shrink-0 place-items-center rounded-full bg-slate-100 text-[11px] font-semibold tabular-nums text-slate-500">
                                {index + 1}
                            </span>
                            <span className="text-[13px] leading-relaxed text-slate-700">{line}</span>
                        </div>
                    ))}
                </div>
            )}
            <button type="button" onClick={onClose} className={`mt-1 ${iosBtnPrimary}`}>
                Понятно
            </button>
        </div>
    );
};

/* ─── Передача супервайзеру (инструкция #230, §4) ─────────────────────────── */

/* Водитель настаивает, что Sapar был выбран вовремя и не менялся. Фактическую
 * дату выбора провайдера может запросить только супервайзер — обращение никуда
 * не уходит, оператор передаёт ему данные.
 *
 * Перечень собран системой из уже введённых ответов, а не набирается заново:
 * шесть значений, переписанные в мессенджер по памяти, теряются по одному.
 * Кнопка копирует ровно то, что показано, — расходиться экрану с буфером
 * обмена нельзя. */
const HandoffScreen = ({ handoff, onCopy, copied }) => (
    <div className="space-y-3 py-2">
        <div className="flex flex-col items-center gap-2 text-center">
            <span className="grid h-12 w-12 place-items-center rounded-full bg-blue-50 text-blue-600">
                <UserCheck size={22} />
            </span>
            <div className="text-[15px] font-semibold text-slate-900">{handoff.title}</div>
            <p className="max-w-[440px] text-[13px] leading-relaxed text-slate-600">
                {handoff.message}
            </p>
        </div>

        <div className={`${iosCard} divide-y divide-slate-100`}>
            {handoff.rows.map((row) => (
                <div key={row.label} className="flex gap-3 px-4 py-2.5">
                    <span className="w-[46%] shrink-0 text-[12.5px] text-slate-500">{row.label}</span>
                    <span className="min-w-0 flex-1 break-words text-[13px] font-medium tabular-nums text-slate-800">
                        {row.value}
                    </span>
                </div>
            ))}
        </div>

        <div className="flex items-start gap-2.5 rounded-2xl bg-amber-50/70 px-3.5 py-3 ring-1 ring-amber-100">
            <span className="mt-[1px] shrink-0 text-amber-600"><AlertTriangle size={16} /></span>
            <div className="text-[13px] leading-relaxed text-slate-700">{handoff.note}</div>
        </div>

        {/* Кнопка одна: закрыть окно можно из подвала, и вторая кнопка «Понятно»
            рядом только отвлекала бы от того единственного, что тут делают. */}
        <div className="flex justify-center">
            <button type="button" onClick={onCopy} className={iosBtnPrimary}>
                {copied ? <CheckCircle2 size={14} /> : <Copy size={14} />}
                {copied ? 'Скопировано' : 'Скопировать'}
            </button>
        </div>
    </div>
);

/* ─── Мастер ──────────────────────────────────────────────────────────────── */

export default function TicketWizard({
    open, onClose, catalog, entries = [], taxiParks = [], apiBaseUrl, headers,
    showToast, onCreated,
}) {
    const [scenarioKey, setScenarioKey] = useState('');
    // pick | entry | category | checks | form | preview | closed | handoff
    const [phase, setPhase] = useState('pick');
    const [groupIndex, setGroupIndex] = useState(0);
    const [answers, setAnswers] = useState({});
    const [checksConfirmed, setChecksConfirmed] = useState(false);
    // Номера отмеченных пунктов — для тематик, где по ТЗ отмечают каждый пункт.
    const [checkedItems, setCheckedItems] = useState([]);
    const [attachment, setAttachment] = useState(null);
    const [verdict, setVerdict] = useState(null);
    const [dismissed, setDismissed] = useState(null);
    const [missing, setMissing] = useState({});
    const [preview, setPreview] = useState(null);
    // Что ответил Sapar по ИИН и периоду, и по какой паре мы его спрашивали.
    const [saparSnapshot, setSaparSnapshot] = useState(null);
    const [saparChecked, setSaparChecked] = useState('');
    // Что ответил справочник компании (реестр посылок или статусы офисов) и по
    // каким ответам мы его спрашивали.
    const [lookup, setLookup] = useState(null);
    const [lookupChecked, setLookupChecked] = useState('');
    // Вход в тематику: сначала проверка по ИИН, категория — после неё.
    const [entry, setEntry] = useState(null);
    const [entryVerdict, setEntryVerdict] = useState(null);
    // Вердикты категорий по тому же снимку: второй раз Sapar не спрашиваем.
    const [categoryVerdicts, setCategoryVerdicts] = useState({});
    // С какого экрана начался сценарий: часть ответов дана ещё на входе, и
    // «Назад» с первого же экрана должно вести к категориям, а не в пустоту.
    const [startGroup, setStartGroup] = useState(0);
    // Категорию выбрал оператор или её открыла сама проверка. Решает, куда
    // ведёт «Назад»: к списку категорий или обратно к данным водителя. В
    // «документы не поступили» списка не было — возвращать в него нельзя.
    const [categoryPicked, setCategoryPicked] = useState(false);
    const [handoff, setHandoff] = useState(null);
    const [copied, setCopied] = useState(false);
    const [busy, setBusy] = useState(false);
    const fileRef = useRef(null);

    const scenario = useMemo(
        () => (catalog || []).find((item) => item.key === scenarioKey) || null,
        [catalog, scenarioKey],
    );

    const catalogGroups = useMemo(() => groupCatalog(catalog, entries), [catalog, entries]);

    const checksReady = useMemo(
        () => checksAreComplete(scenario, {
            confirmedAll: checksConfirmed, confirmedItems: checkedItems,
        }),
        [scenario, checksConfirmed, checkedItems],
    );
    const entryRows = useMemo(() => pairRows((entry && entry.steps) || []), [entry]);
    /* Категории входа. Пока Sapar не ответил, «Документы не поступили» остаётся
       в списке: решить за оператора нечем, а работать надо. */
    const categories = useMemo(
        () => entryCategories(entry, catalog, {
            withNoDocuments: entryVerdict?.outcome === ENTRY.UNKNOWN,
        }),
        [entry, catalog, entryVerdict],
    );
    const groups = useMemo(() => groupsOf(scenario, answers), [scenario, answers]);
    const group = groups[groupIndex] || null;
    const groupRows = useMemo(
        () => rowsOfGroup(scenario, group, answers),
        [scenario, group, answers],
    );

    const reset = useCallback(() => {
        setScenarioKey(''); setPhase('pick'); setGroupIndex(0); setAnswers({});
        setChecksConfirmed(false); setCheckedItems([]); setAttachment(null); setVerdict(null);
        setDismissed(null); setMissing({}); setPreview(null);
        setSaparSnapshot(null); setSaparChecked('');
        setLookup(null); setLookupChecked('');
        setEntry(null); setEntryVerdict(null); setCategoryVerdicts({});
        setStartGroup(0); setCategoryPicked(false); setHandoff(null); setCopied(false);
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

    /* Статусы офисов спрашиваем сразу по городу, не дожидаясь «Далее»: их ответ
       рисует следующий вопрос — список офисов со статусом на сегодня. У реестра
       посылок ответ решает только «идти ли дальше», и он спрашивается по кнопке
       (см. goNext), чтобы не дёргать базу на каждую букву в ФИО. */
    useEffect(() => {
        if (phase !== 'form' || !scenario?.lookup_on_answer) return;
        if (!needsLookup(scenario, answers, lookupChecked)) return;
        askLookup(answers);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [scenario, answers, phase, lookupChecked]);

    const startScenario = (key, { carried = null } = {}) => {
        const next = (catalog || []).find((item) => item.key === key) || null;
        const state = { answers: carried || answers, attachment: null, checksReady: false };
        setScenarioKey(key);
        setVerdict(null);
        setDismissed(null);
        setMissing({});
        setHandoff(null);
        setLookup(null);
        setLookupChecked('');
        if (!entry) {
            // Без входа порядок прежний: первым экран «кто и за какой период»,
            // с него же запускается предпроверка Sapar.
            setGroupIndex(0);
            setStartGroup(0);
            setPhase('form');
            return;
        }
        /* Со входом ИИН, период, парк и город уже введены, а проверка пройдена.
           Показывать тот же экран второй раз незачем — ведём по инструкции:
           чек-лист, потом первый экран, где действительно есть что заполнять. */
        const groupList = groupsOf(next, state.answers);
        const open = openStop(next, groupList, state);
        const index = open.phase === 'form' ? open.groupIndex : Math.max(0, groupList.length - 1);
        setGroupIndex(index);
        setStartGroup(index);
        const stop = afterCategory(next, groupList, state);
        if (stop.phase === 'checks') { setPhase('checks'); return; }
        if (stop.phase === 'submit') { setPhase('form'); return; }
        setPhase('form');
    };

    const switchScenario = (key) => {
        const carried = carryOver(answers);
        setAnswers(carried);
        setChecksConfirmed(false);
        setCheckedItems([]);
        setAttachment(null);
        if (fileRef.current) fileRef.current.value = '';
        startScenario(key, { carried });
    };

    /* ─── Вход в тематику: проверка по ИИН до выбора категории ────────────── */

    const startEntry = (item) => {
        setEntry(item);
        setEntryVerdict(null);
        setCategoryVerdicts({});
        setScenarioKey('');
        setAnswers({});
        setSaparSnapshot(null);
        setSaparChecked('');
        setVerdict(null);
        setPhase('entry');
    };

    /* Спрашивает Sapar по введённым ИИН и периоду. Дальше три дороги, и все три
       из инструкции: месяц ещё не закрыт — обращение не нужно; документы есть —
       открываем категории; документов нет — это §3, и категорию тут выбирать
       нечего, тематика одна.

       Sapar промолчал — показываем все категории, включая «Документы не
       поступили»: считать молчание сервиса за «документов нет» нельзя. */
    const runEntryCheck = async () => {
        setBusy(true);
        try {
            const response = await axios.post(
                `${apiBaseUrl}/api/crm/entry/${entry.queue_code}/sapar`,
                { iin: answerValue(answers, 'iin'), period: answerValue(answers, 'period') },
                { headers: headers() },
            );
            const data = response.data || {};
            setSaparSnapshot(data.snapshot || null);
            setSaparChecked(saparKey(answers));
            setCategoryVerdicts(data.categories || {});
            const hit = data.verdict || { outcome: ENTRY.UNKNOWN };
            setEntryVerdict(hit);
            if (hit.outcome === ENTRY.CLOSE) {
                setVerdict({ outcome: OUTCOME.CLOSE, message: hit.message });
                setPhase('closed');
                return;
            }
            if (hit.outcome === ENTRY.NO_DOCUMENTS && hit.scenario) {
                setCategoryPicked(false);
                startScenario(hit.scenario);
                return;
            }
            setPhase('category');
        } catch (error) {
            // Отказ сети не повод держать оператора: категории он выберет сам.
            setSaparSnapshot(null);
            setSaparChecked(saparKey(answers));
            setCategoryVerdicts({});
            setEntryVerdict({ outcome: ENTRY.UNKNOWN, message: null });
            setPhase('category');
        } finally {
            setBusy(false);
        }
    };

    /* Категория выбрана. Вердикт по ней уже посчитан на входе тем же снимком —
       второй раз Sapar не спрашиваем. */
    const pickCategory = (key) => {
        setCategoryPicked(true);
        // Чек-лист у каждой категории свой: вернулся к списку и выбрал другую —
        // подтверждать проверки надо заново.
        setChecksConfirmed(false);
        setCheckedItems([]);
        setAttachment(null);
        if (fileRef.current) fileRef.current.value = '';
        const hit = categoryVerdicts[key];
        if (hit && hit.outcome === OUTCOME.CLOSE) {
            setVerdict(hit);
            setScenarioKey(key);
            setPhase('closed');
            return;
        }
        if (hit && hit.outcome === OUTCOME.SWITCH && hit.switch_to) {
            startScenario(hit.switch_to);
            return;
        }
        startScenario(key);
    };

    const copyHandoff = async () => {
        try {
            await navigator.clipboard.writeText(handoff.text);
            setCopied(true);
        } catch (error) {
            showToast?.('Не удалось скопировать — выделите текст вручную', 'error');
        }
    };

    const setAnswer = (key, value) => {
        setMissing((prev) => (prev[key] ? { ...prev, [key]: undefined } : prev));
        setAnswers((prev) => ({ ...prev, [key]: value }));
    };

    const canLeaveGroup = scenario && group
        && groupIsComplete(scenario, group, { answers, attachment });

    const goNext = async () => {
        // Пока оператор не ушёл с экрана с ИИН — спрашиваем Sapar. Половина
        // вопросов дальше существует только потому, что раньше эти данные
        // узнавали у водителя; часть обращений на этом и заканчивается.
        //
        // Со входом этот вопрос уже задан ДО выбора категории, и повторять его
        // нельзя: тот же снимок стоил бы второго запроса в Sapar.
        if (!entry && needsSaparCheck(scenario, group, answers, saparChecked)) {
            const passed = await askSapar();
            if (!passed) return;
        }
        /* Проверка по справочнику — там, где ТЗ ставит её: после данных, по
           которым её вообще можно сделать, и до всего остального. */
        if (scenario?.lookup && !scenario.lookup_on_answer
            && needsLookup(scenario, answers, lookupChecked)) {
            const passed = await askLookup();
            if (!passed) return;
        }
        if (entry) {
            // Чек-лист со входом идёт перед вопросами, а не между ними.
            goTo(openStopFromHere());
            return;
        }
        goTo(nextStop(scenario, groups, groupIndex, { checksReady }));
    };

    /* Следующий экран, где ещё есть что заполнять. Считается от текущего, а не
       от начала: пройденный экран заполнен, и вернуться на него «вперёд» нельзя. */
    const openStopFromHere = () => {
        const rest = groups.slice(groupIndex + 1);
        const index = rest.findIndex(
            (name) => !groupIsComplete(scenario, name, { answers, attachment }));
        return index >= 0
            ? { phase: 'form', groupIndex: groupIndex + 1 + index }
            : { phase: 'submit' };
    };

    /* Один переход — одно место. Раньше «куда дальше» решалось в трёх кнопках
       по-разному, и порядок экранов расходился между ними. */
    const goTo = (stop) => {
        if (stop.phase === 'submit') { askServer(); return; }
        if (stop.phase === 'pick') { reset(); return; }
        if (stop.phase === 'checks') { setPhase('checks'); return; }
        setPhase('form');
        setGroupIndex(stop.groupIndex);
    };

    /* Спрашивает Sapar. true — идём дальше, false — мастер остался на месте
       (обращение закрыто, переводится в другую тематику или мы просто ждём).

       Отказ Sapar НЕ останавливает оператора: считать молчание сервиса за
       «документов нет» нельзя, а работать надо в любом случае. */
    const askSapar = async () => {
        setBusy(true);
        try {
            const response = await axios.post(
                `${apiBaseUrl}/api/crm/scenarios/${scenarioKey}/sapar`,
                { iin: answerValue(answers, 'iin'), period: answerValue(answers, 'period') },
                { headers: headers() },
            );
            const { snapshot, verdict: hit } = response.data || {};
            setSaparChecked(saparKey(answers));
            setSaparSnapshot(snapshot || null);
            if (!hit || hit.outcome === OUTCOME.PASS) return true;
            if (hit.outcome === OUTCOME.CLOSE) {
                setVerdict(hit);
                setPhase('closed');
                return false;
            }
            setVerdict({
                ...hit,
                signature: `sapar:${saparKey(answers)}`,
                switch_title: (catalog || []).find((s) => s.key === hit.switch_to)?.title,
            });
            return false;
        } catch (error) {
            // Сеть или отказ — не повод держать оператора: идём по вопросам.
            setSaparChecked(saparKey(answers));
            setSaparSnapshot(null);
            return true;
        } finally {
            setBusy(false);
        }
    };

    /* Спрашивает справочник компании — обязательную проверку тематики (ТЗ #201).
       true — оператор идёт дальше, false — мастер остался на месте.

       Отказ справочника оператора НЕ останавливает, как и молчание Sapar: по
       «мы не смогли посмотреть» нельзя ни закрыть обращение, ни отправить его
       мимо проверки, а работать надо в любом случае. Дальше решает сервер: он
       перечитывает статус офиса сам и на отправке, и в предпросмотре. */
    const askLookup = async (source = answers) => {
        const key = lookupKey(scenario, source);
        setBusy(true);
        try {
            const response = await axios.post(
                `${apiBaseUrl}/api/crm/scenarios/${scenarioKey}/lookup`,
                { answers: source },
                { headers: headers() },
            );
            const { snapshot, verdict: hit } = response.data || {};
            setLookup(snapshot || null);
            setLookupChecked(key);
            /* Выбранный офис мог остаться от прошлого города — снимаем сами.
               Иначе в обращение уехал бы адрес, которого оператор в этом городе
               не выбирал, а на экране стоял бы пустой выбор. */
            const list = (snapshot && snapshot.offices) || [];
            const picked = answerValue(source, 'office');
            if (picked && !list.some((item) => String(item.id) === String(picked))) {
                setAnswers((prev) => ({ ...prev, office: '' }));
            }
            if (!hit || hit.outcome === OUTCOME.PASS) return true;
            if (hit.outcome === OUTCOME.CLOSE) {
                setVerdict(hit);
                setPhase('closed');
                return false;
            }
            setVerdict({ ...hit, signature: `lookup:${key}` });
            return false;
        } catch (error) {
            setLookup(null);
            setLookupChecked(key);
            return true;
        } finally {
            setBusy(false);
        }
    };

    const goBack = () => {
        setVerdict(null);
        if (phase === 'category') { setPhase('entry'); return; }
        if (phase === 'preview') { setPhase('form'); setGroupIndex(groups.length - 1); return; }
        if (phase === 'checks') {
            // Со входом чек-лист стоит сразу за выбором категории — туда и
            // возвращаем. Без входа — на первый экран вопросов, как было.
            if (entry) { setPhase(categoryPicked ? 'category' : 'entry'); return; }
            goTo({ phase: 'form', groupIndex: CHECKS_AFTER_GROUP });
            return;
        }
        if (entry) {
            /* Экраны до startGroup заполнены ещё на входе — «Назад» с первого
               рабочего экрана ведёт к чек-листу или к категориям, а не в них. */
            if (groupIndex <= startGroup) {
                setPhase(hasChecks(scenario) ? 'checks'
                    : categoryPicked ? 'category' : 'entry');
                return;
            }
            setPhase('form');
            setGroupIndex(groupIndex - 1);
            return;
        }
        goTo(previousStop(scenario, groups, groupIndex));
    };

    const askServer = async () => {
        setBusy(true);
        try {
            const response = await axios.post(
                `${apiBaseUrl}/api/crm/scenarios/${scenarioKey}/evaluate`,
                {
                    answers,
                    has_attachment: Boolean(attachment),
                    ...checksPayload(scenario, {
                        confirmedAll: checksConfirmed, confirmedItems: checkedItems,
                    }),
                },
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
            } else if (data.outcome === OUTCOME.ESCALATE) {
                setHandoff(data.handoff || null);
                setCopied(false);
                setPhase(data.handoff ? 'handoff' : 'closed');
                if (!data.handoff) setVerdict(data);
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
            const checks = checksPayload(scenario, {
                confirmedAll: checksConfirmed, confirmedItems: checkedItems,
            });
            form.append('checks_confirmed', checks.checks_confirmed ? '1' : '0');
            form.append('checks_done', checks.checks_done.join(','));
            if (attachment) form.append('attachment', attachment);
            const response = await axios.post(`${apiBaseUrl}/api/crm/tickets`, form,
                { headers: headers() });
            const created = response.data;
            showToast?.(created.delivered
                ? `Обращение №${created.item.id} отправлено в «${
                    created.item.tg_chat_title || created.item.queue_title}»`
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
        if (phase === 'pick' || phase === 'closed' || phase === 'handoff') {
            return <button type="button" onClick={onClose} className={iosBtnSecondary}>Закрыть</button>;
        }
        if (phase === 'entry') {
            return (
                <>
                    <button type="button" onClick={reset} className={iosBtnSecondary}>
                        <ArrowLeft size={14} /> Назад
                    </button>
                    <button type="button" onClick={runEntryCheck}
                            disabled={busy || !entryIsComplete(entry, answers)}
                            className={iosBtnPrimary}>
                        {busy ? <Loader2 size={14} className="animate-spin" />
                            : <ShieldCheck size={14} />}
                        Проверить документы
                    </button>
                </>
            );
        }
        if (phase === 'category') {
            return (
                <button type="button" onClick={goBack} className={iosBtnSecondary}>
                    <ArrowLeft size={14} /> Назад
                </button>
            );
        }
        if (phase === 'checks') {
            return (
                <>
                    <button type="button" onClick={goBack} className={iosBtnSecondary}>Назад</button>
                    <button type="button" disabled={!checksReady}
                            onClick={() => goTo(afterChecks(groups, entry
                                ? openStop(scenario, groups,
                                           { answers, attachment, checksReady: true })
                                : null))}
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
        // Тематика, из которой в группу ничего не уходит, не должна обещать
        // отправку: «Документы не поступили» кончается передачей супервайзеру.
        const finish = scenario?.final_outcome === OUTCOME.ESCALATE
            ? 'Передать супервайзеру' : 'Проверить и отправить';
        return (
            <>
                <button type="button" onClick={goBack} className={iosBtnSecondary}>
                    <ArrowLeft size={14} /> Назад
                </button>
                <button type="button" onClick={goNext} disabled={busy || !canLeaveGroup}
                        className={iosBtnPrimary}>
                    {busy ? <Loader2 size={14} className="animate-spin" />
                        : last ? <CheckCircle2 size={14} /> : <ArrowRight size={14} />}
                    {last ? finish : 'Далее'}
                </button>
            </>
        );
    })();

    const subtitle = phase === 'pick' ? 'Выберите тематику — дальше система задаст вопросы'
        : phase === 'entry' ? 'Данные водителя — проверим документы за период'
            : phase === 'category' ? 'Выберите категорию обращения'
                : phase === 'checks' ? 'Проверьте это до обращения'
                    : phase === 'preview' ? 'Так обращение увидят в группе'
                        : (phase === 'closed' || phase === 'handoff') ? null
                            // Экраны, заполненные на входе, оператор не видел —
                            // и в счётчике их быть не должно: «шаг 2 из 2»
                            // читается как «первый я пропустил».
                            : `${group} · шаг ${groupIndex - startGroup + 1} из ${
                                groups.length - startGroup}`;

    return (
        <IosModal open={open} onClose={onClose}
                  title={(phase === 'entry' || phase === 'category')
                      ? (entry ? entry.title : 'Новое обращение')
                      : scenario ? scenario.title
                          : entry ? entry.title : 'Новое обращение'}
                  subtitle={subtitle} maxWidth="max-w-xl" footer={footer}>
            <div style={{ fontFamily: APPLE_FONT }}>
                {phase === 'form' && groups.length - startGroup > 1 && (
                    <div className="mb-4 h-1 w-full overflow-hidden rounded-full bg-slate-100">
                        <div className="h-full rounded-full bg-blue-500 transition-all"
                             style={{ width: `${((groupIndex - startGroup + 1)
                                 / (groups.length - startGroup)) * 100}%` }} />
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
                                {catalogGroups.length > 1 && group.title && !group.entry && (
                                    <div className={`${iosGroupLabel} mb-1.5`}>{group.title}</div>
                                )}
                                <div className="space-y-1.5">
                                    {group.items.map((item) => (
                                        <button key={item.key} type="button" disabled={!item.is_ready}
                                                onClick={() => (item.entry
                                                    ? startEntry(item.entry)
                                                    : startScenario(item.key))}
                                                className={`flex w-full items-center gap-2 rounded-2xl px-4 py-3 text-left transition-all ${
                                                    item.is_ready
                                                        ? 'bg-slate-50 hover:bg-slate-100 active:scale-[0.99]'
                                                        : 'cursor-not-allowed bg-slate-50/60 opacity-60'
                                                }`}>
                                            <span className="min-w-0 flex-1">
                                                <span className="block truncate text-[14px] font-medium text-slate-900">
                                                    {item.title}
                                                </span>
                                                {/* Адрес пишем ТОЛЬКО у уведённой темы:
                                                    у остальных он и есть заголовок раздела,
                                                    а повторённый в каждой строке заголовок
                                                    перестают читать. */}
                                                <TopicRoute item={item} />
                                            </span>
                                            {/* «Когда используется» — под «i»: в списке
                                                тематик столько же абзацев мешают выбирать. */}
                                            <InfoHint title={item.title}>{item.when_to_use}</InfoHint>
                                            {item.is_ready
                                                ? <ChevronRight size={15} className="shrink-0 text-slate-400" />
                                                : <IosBadge tone="amber">{blockedLabel(item)}</IosBadge>}
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

                {/* Вход в тематику: сначала данные водителя, потом проверка.
                    По инструкции #230 категорию выбирают уже зная, есть ли
                    документы за период, — иначе выбор делается вслепую. */}
                {phase === 'entry' && entry && (
                    <div className="space-y-4">
                        {entryRows.map((row, rowIndex) => (
                            <div key={row.map((step) => step.key).join('+')}
                                 className={row.length > 1
                                     ? 'grid grid-cols-1 gap-4 sm:grid-cols-2' : undefined}>
                                {row.map((step) => (
                                    <Field key={step.key} step={step} value={answers[step.key]}
                                           autoFocus={rowIndex === 0 && step === row[0]}
                                           problem={missing[step.key]}
                                           options={referenceOptions(step, { taxiParks })}
                                           onChange={(value) => setAnswer(step.key, value)} />
                                ))}
                            </div>
                        ))}
                        <div className="flex items-start gap-2.5 px-1 text-[12.5px] leading-relaxed text-slate-500">
                            <ShieldCheck size={14} className="mt-[2px] shrink-0 text-slate-400" />
                            <span>
                                По ИИН и периоду система сама проверит, поступили ли документы.
                                От ответа зависит, какие категории обращения будут доступны.
                            </span>
                        </div>
                    </div>
                )}

                {/* Категории открываются только после проверки (§2 инструкции). */}
                {phase === 'category' && entry && (
                    <div className="space-y-3">
                        {saparSnapshot && <SaparNote snapshot={saparSnapshot} />}
                        {entryVerdict?.outcome === ENTRY.UNKNOWN && !saparSnapshot && (
                            <div className="flex items-start gap-2.5 rounded-2xl bg-slate-50 px-3.5 py-3 ring-1 ring-slate-200/70">
                                <span className="mt-[1px] shrink-0 text-slate-400">
                                    <AlertTriangle size={16} />
                                </span>
                                <div className="text-[13px] leading-relaxed text-slate-600">
                                    Sapar не ответил — проверьте документы сами и выберите категорию.
                                </div>
                            </div>
                        )}
                        <div className="space-y-1.5">
                            {categories.map((item) => (
                                /* Категория с неготовым адресом не нажимается по той же
                                   причине, что и тематика в картотеке: пройти интервью и
                                   упереться в «отправлять некуда» хуже, чем не начать. */
                                <button key={item.key} type="button" disabled={item.is_ready === false}
                                        onClick={() => pickCategory(item.key)}
                                        className={`flex w-full items-center gap-2 rounded-2xl px-4 py-3 text-left transition-all ${
                                            item.is_ready === false
                                                ? 'cursor-not-allowed bg-slate-50/60 opacity-60'
                                                : 'bg-slate-50 hover:bg-slate-100 active:scale-[0.99]'
                                        }`}>
                                    <span className="min-w-0 flex-1">
                                        <span className="block truncate text-[14px] font-medium text-slate-900">
                                            {item.title}
                                        </span>
                                        <TopicRoute item={item} />
                                    </span>
                                    <InfoHint title={item.title}>{item.when_to_use}</InfoHint>
                                    {item.is_ready === false
                                        ? <IosBadge tone="amber">{blockedLabel(item)}</IosBadge>
                                        : <ChevronRight size={15} className="shrink-0 text-slate-400" />}
                                </button>
                            ))}
                        </div>
                    </div>
                )}

                {phase === 'checks' && scenario && (
                    <div className="space-y-3">
                        {/* Что ответил реестр невостребованных посылок. Пустой
                            ответ показываем спокойной строкой, а не бейджем: он
                            ничего не доказывает — раздел «Посылки» открылся
                            недавно, и записи в нём может просто ещё не быть.
                            Поэтому пункт проверки оператор всё равно отмечает
                            сам, а строка лишь говорит, что искать вручную то же
                            самое второй раз не нужно. */}
                        {scenario.lookup === LOOKUP.PARCELS && lookup
                            && !(lookup.items || []).length && (
                            <div className="flex items-start gap-2.5 rounded-2xl bg-slate-50 px-3.5 py-3 ring-1 ring-slate-200/70">
                                <span className="mt-[1px] shrink-0 text-slate-400">
                                    <ShieldCheck size={16} />
                                </span>
                                <div className="text-[13px] leading-relaxed text-slate-600">
                                    В реестре невостребованных посылок совпадений по этому
                                    водителю не нашлось — ни по телефону, ни по ВУ, ни по ФИО.
                                </div>
                            </div>
                        )}
                        {/* Два вида чек-листа. Обычный — нумерованный перечень и одна
                            галочка под ним. Тематика с checks_each отмечает каждый пункт
                            отдельно: тогда номер уступает место флажку, а строка сама
                            становится нажимаемой. Списка получается один, а не два. */}
                        <div className={`${iosCard} divide-y divide-slate-100`}>
                            {scenario.checks.map((check, index) => {
                                const done = checkedItems.includes(index);
                                const body = (
                                    <>
                                        {scenario.checks_each ? (
                                            <input type="checkbox" checked={done} readOnly
                                                   tabIndex={-1}
                                                   className="mt-[3px] h-4 w-4 shrink-0 rounded accent-blue-600" />
                                        ) : (
                                            <span className="mt-[2px] grid h-5 w-5 shrink-0 place-items-center rounded-full bg-slate-100 text-[11px] font-semibold tabular-nums text-slate-500">
                                                {index + 1}
                                            </span>
                                        )}
                                        <span className={`text-[13px] leading-relaxed ${
                                            scenario.checks_each && done ? 'text-slate-500' : 'text-slate-700'
                                        }`}>
                                            {check}
                                        </span>
                                    </>
                                );
                                return scenario.checks_each ? (
                                    <button key={check} type="button"
                                            aria-pressed={done}
                                            onClick={() => setCheckedItems(toggleCheck(checkedItems, index))}
                                            className="flex w-full cursor-pointer gap-2.5 px-4 py-2.5 text-left transition hover:bg-slate-50">
                                        {body}
                                    </button>
                                ) : (
                                    <div key={check} className="flex gap-2.5 px-4 py-2.5">{body}</div>
                                );
                            })}
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

                        {scenario.checks_each ? (
                            <div className="flex items-center gap-1.5 px-1 text-[12.5px] text-slate-500">
                                <span className="tabular-nums">
                                    Отмечено {checkedItems.length} из {scenario.checks.length}
                                </span>
                                <InfoHint side="left">
                                    Отметьте каждый пункт, который проверили. Если по условиям
                                    термокороб водителю не положен — обращение отправлять не нужно,
                                    просто закройте это окно.
                                </InfoHint>
                            </div>
                        ) : (
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
                        )}
                    </div>
                )}

                {phase === 'closed' && verdict && (
                    <ClosedScreen verdict={verdict} onClose={onClose} />
                )}

                {phase === 'handoff' && handoff && (
                    <HandoffScreen handoff={handoff} onCopy={copyHandoff} copied={copied} />
                )}

                {phase === 'form' && scenario && (
                    <div className="space-y-4">
                        {verdict && (
                            <OutcomeBar verdict={verdict}
                                        onDismiss={() => { setDismissed(verdict.signature); setVerdict(null); }}
                                        onSwitch={switchScenario} />
                        )}

                        {saparSnapshot && <SaparNote snapshot={saparSnapshot} />}

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
                                       offices={officeOptions(step, lookup)}
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
                            <div className="mt-1.5 rounded-xl bg-slate-100 px-3.5 py-3">
                                {/* Первая строка карточки в группе — просьба тематики
                                    (group_title). Раньше её в предпросмотре не было, и
                                    оператор видел не то, что увидят коллеги: у «Статуса
                                    работы офиса» именно она несёт сам вопрос из ТЗ
                                    («Уточнение — работает офис или нет?»). */}
                                {scenario?.group_title && (
                                    <div className="mb-2 text-[13.5px] font-semibold leading-snug text-slate-900">
                                        {scenario.group_title}
                                    </div>
                                )}
                                <pre className="whitespace-pre-wrap break-words font-sans text-[13px] leading-relaxed text-slate-800">
{preview.body}
                                </pre>
                            </div>
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
