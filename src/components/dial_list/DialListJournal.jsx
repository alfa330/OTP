import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
    Ban, ChevronRight, Loader2, PhoneCall, PhoneMissed, PhoneOff, PhoneOutgoing, Play, Plus, RefreshCw,
    Search, SlidersHorizontal, StickyNote, TriangleAlert, Undo2, X,
} from 'lucide-react';
import {
    iosCard, iosInput, iosGroupLabel, iosBtnPrimary, iosBtnSecondary, iosBtnGhost, IosHint, IosModal,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { IosDateRangePicker, isoDate, rangeLabel } from '../ui/DateRangePicker';
import { OutcomeBadge } from './DialListOutcomesPanel';
import { buildPeriodOptions, monthLabel } from './dialListPeriods';

/*
 * Журнал водителей раздела «Обзвон из телефона» (запрос владельца 23.09.2026).
 *
 * Список всех, кого загрузили для обзвона за месяц: этап, ответственный оператор,
 * чем кончился последний звонок и какой итог поставил оператор. Карточка — вся
 * история: каждая попытка с исходом АТС, итогом оператора, длительностью и
 * записью разговора, ручные действия руководителя. Руководитель может вернуть
 * человека в список (попытки обнуляются) или исключить его из обзвона.
 *
 * Раскладка — канон фильтров сайта («Касания», «Посылки», «ИИ-оценка»):
 *   1) полоса: поиск, месяц базы, кнопка «Фильтры · N», обновить;
 *   2) чипы отобранного под полосой — видно, что отобрано, не раскрывая панель;
 *   3) панель редких фильтров (оператор, файл, дата звонка) — только по кнопке;
 *   4) полоса этапов — она же легенда цвета точек в строках, и чипы итогов.
 * Числа на полосе этапов и на чипах итогов сервер считает по той же выборке, что
 * и список (этапы — без фильтра этапа, итоги — без фильтра итога): число на чипе
 * равно числу строк, которые покажет нажатие.
 *
 * Цвет — только со смыслом: точка этапа и цвет итога из справочника. Аватар и
 * прочее — нейтральные; раньше аватар красился цветом этапа, и тот же смысл
 * стоял в строке дважды.
 *
 * Номер телефона здесь — только маска (последние 4 цифры), как и везде в
 * разделе: файл с номерами есть у того, кто его загрузил, а сервер номер наружу
 * не отдаёт вовсе.
 */

const PAGE = 50;
const MAX_PAGE = 200; // JOURNAL_MAX_LIMIT на сервере

/* Этапы в порядке жизни строки. dot — цвет точки: легенда в полосе и точка в
   строке берут его отсюда, разойтись им негде. */
const STAGES = [
    { value: 'queue', label: 'В очереди', dot: 'bg-slate-400' },
    { value: 'waiting', label: 'Ждут повтора', dot: 'bg-amber-400' },
    { value: 'issued', label: 'У операторов', dot: 'bg-blue-500' },
    { value: 'answered', label: 'Дозвонились', dot: 'bg-emerald-500' },
    { value: 'exhausted', label: 'Не дозвонились', dot: 'bg-rose-500' },
    { value: 'excluded', label: 'Исключены', dot: 'bg-slate-300' },
];
const STAGE_DOT = Object.fromEntries(STAGES.map((s) => [s.value, s.dot]));

const RESULT_LABEL = {
    answered: 'Дозвонились', busy: 'Занято', no_answer: 'Не ответил', other: 'Не состоялся', failed: 'Ошибка АТС',
    // Оператор сам завершил звонок до ответа водителя — попытка не засчитана.
    cancelled: 'Отменён до ответа',
};

const STATE_LABEL = {
    requested: 'Звонок запрошен у АТС',
    leg_ringing: 'АТС звонит оператору',
    leg_answered: 'Оператор на линии, набираем водителя',
    ended: 'Разговор завершён, ждём исход от АТС',
};

const SORT_OPTIONS = [
    { value: 'activity', label: 'Сначала свежие' },
    { value: 'name', label: 'По ФИО' },
    { value: 'created', label: 'По дате загрузки' },
    { value: 'attempts', label: 'Больше попыток' },
];

const TONE = {
    green: 'bg-emerald-50 text-emerald-600',
    amber: 'bg-amber-50 text-amber-600',
    red: 'bg-rose-50 text-rose-500',
    blue: 'bg-blue-50 text-blue-600',
    slate: 'bg-slate-100 text-slate-500',
};

// Как у «Посылок»: поле даты в панели фильтров — белый чип на всю ширину колонки.
const DATE_TRIGGER = 'flex w-full items-center gap-2 rounded-xl bg-white px-3 py-2 '
    + 'text-left text-[12.5px] font-medium text-slate-700 ring-1 ring-slate-200/70 '
    + 'shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition-all hover:bg-slate-50 '
    + 'active:scale-[0.99] focus:outline-none focus:ring-2 focus:ring-blue-500/60 '
    + '[&>span]:flex-1 [&>span]:text-left [&>span]:truncate';

// Разрушительное действие: своя строка, а не bg-rose поверх iosBtnPrimary —
// какой из двух фонов победит, решает порядок правил в CSS, а не в className.
const BTN_DANGER = 'inline-flex items-center justify-center gap-2 rounded-xl bg-rose-600 px-4 py-2.5 text-[13.5px] '
    + 'font-semibold text-white shadow-sm transition-all hover:bg-rose-700 active:scale-[0.98] '
    + 'disabled:cursor-not-allowed disabled:opacity-50';
const BTN_DANGER_GHOST = 'inline-flex items-center justify-center gap-1.5 rounded-xl px-3 py-2 text-[13px] font-medium '
    + 'text-rose-600 transition-all hover:bg-rose-50 active:scale-[0.98]';

const FIELD_HEAD = `flex h-5 items-center ${iosGroupLabel}`;

const shiftDays = (days) => {
    const d = new Date();
    d.setDate(d.getDate() - days);
    return isoDate(d);
};

const DATE_PRESETS = [
    { label: 'Сегодня', range: () => ({ from: isoDate(new Date()), to: isoDate(new Date()) }) },
    { label: 'Вчера', range: () => ({ from: shiftDays(1), to: shiftDays(1) }) },
    { label: '7 дней', range: () => ({ from: shiftDays(6), to: isoDate(new Date()) }) },
];

const readError = async (resp) => {
    const data = await resp.json().catch(() => ({}));
    return data?.error || `HTTP ${resp.status}`;
};

/* ─── форматирование ────────────────────────────────────────────────────── */

const pad2 = (n) => String(n).padStart(2, '0');
const MONTHS_GEN = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];

const toDate = (iso) => {
    if (!iso) return null;
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? null : d;
};

// Разница в календарных днях: 0 — сегодня, 1 — вчера, -1 — завтра.
const daysAgo = (d) => {
    const start = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    return Math.round((start(new Date()) - start(d)) / 86400000);
};

/** «сегодня, 14:02», «вчера, 09:10», «завтра, 11:00», «22.09, 18:00», «22.09.2025». */
const fmtWhen = (iso) => {
    const d = toDate(iso);
    if (!d) return '';
    const clock = `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
    const ago = daysAgo(d);
    if (ago === 0) return `сегодня, ${clock}`;
    if (ago === 1) return `вчера, ${clock}`;
    if (ago === -1) return `завтра, ${clock}`;
    if (d.getFullYear() !== new Date().getFullYear()) return `${pad2(d.getDate())}.${pad2(d.getMonth() + 1)}.${d.getFullYear()}`;
    return `${pad2(d.getDate())}.${pad2(d.getMonth() + 1)}, ${clock}`;
};

const fmtClock = (iso) => {
    const d = toDate(iso);
    return d ? `${pad2(d.getHours())}:${pad2(d.getMinutes())}` : '';
};

const fmtDate = (iso) => {
    const d = toDate(iso);
    return d ? `${pad2(d.getDate())}.${pad2(d.getMonth() + 1)}.${d.getFullYear()}` : '';
};

/** Заголовок дня в истории: «Сегодня», «Вчера», «22 сентября». */
const fmtDay = (iso) => {
    const d = toDate(iso);
    if (!d) return 'Без даты';
    const ago = daysAgo(d);
    if (ago === 0) return 'Сегодня';
    if (ago === 1) return 'Вчера';
    const year = d.getFullYear() !== new Date().getFullYear() ? ` ${d.getFullYear()}` : '';
    return `${d.getDate()} ${MONTHS_GEN[d.getMonth()]}${year}`;
};

const dayKey = (iso) => {
    const d = toDate(iso);
    return d ? `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}` : '';
};

const fmtDuration = (sec) => {
    const s = Math.max(0, Number(sec) || 0);
    return s ? `${Math.floor(s / 60)}:${pad2(s % 60)}` : '';
};

const plural = (n, one, few, many) => {
    const a = Math.abs(n) % 100;
    const b = a % 10;
    if (a > 10 && a < 20) return many;
    if (b === 1) return one;
    if (b >= 2 && b <= 4) return few;
    return many;
};

const lower = (text) => (text ? text[0].toLowerCase() + text.slice(1) : '');

const initials = (name) => {
    const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
    return parts.length ? parts.slice(0, 2).map((p) => p[0].toUpperCase()).join('') : '•';
};

/** Что стоит за этапом строки — одна короткая фраза под ним. */
const stageDetail = (lead) => {
    const last = lead.last_call;
    const result = last?.result ? lower(RESULT_LABEL[last.result] || last.result) : '';
    // «Звонили ли» — по последнему звонку, а не по счётчику: итог «Перезвонить» и
    // возврат в список обнуляют attempts_total, а история звонков остаётся.
    const attempt = lead.attempts_total ? `попытка ${lead.attempts_total} из ${lead.max_attempts}` : '';
    switch (lead.stage) {
        case 'queue':
            return last ? [result, attempt].filter(Boolean).join(' · ') : 'ещё не звонили';
        case 'waiting':
            return [result, lead.next_retry_at ? `повтор ${fmtWhen(lead.next_retry_at)}` : attempt].filter(Boolean).join(' · ');
        case 'issued':
            return last ? [result, attempt].filter(Boolean).join(' · ') : 'в списке, ещё не звонили';
        case 'answered':
            return last?.billsec > 0 ? `разговор ${fmtDuration(last.billsec)}` : '';
        case 'exhausted':
            return lead.attempts_total
                ? `${lead.attempts_total} ${plural(lead.attempts_total, 'попытка', 'попытки', 'попыток')} без ответа`
                : result;
        default:
            return '';
    }
};

/* ─── мелкие детали ─────────────────────────────────────────────────────── */

const Dot = ({ stage, className = '' }) => (
    <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${STAGE_DOT[stage] || 'bg-slate-300'} ${className}`} />
);

const Avatar = ({ name, dim = false }) => (
    <div className={`grid h-9 w-9 shrink-0 place-items-center rounded-full bg-slate-100 text-[12px] font-semibold ${dim ? 'text-slate-400' : 'text-slate-500'}`}>
        {initials(name)}
    </div>
);

const Skeleton = ({ className = '' }) => <div className={`animate-pulse rounded-md bg-slate-200/70 ${className}`} />;

/* ─── строка списка ─────────────────────────────────────────────────────── */

/* Широкий экран — колонки (водитель · этап · итог · оператор), узкий — две-три
   строки как в «Недавних» iOS: имя и время звонка, этап, итог с комментарием.
   Одна и та же строка, разная подача: колонки на телефоне не помещаются, а
   стопка на широком экране не даёт пробежать глазами столбец этапов. */
const GRID = 'lg:grid lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1.1fr)_minmax(0,1.3fr)_minmax(0,1fr)_14px] lg:items-center lg:gap-4';

const LeadRow = ({ lead, onOpen, showMonth }) => {
    const excluded = lead.stage === 'excluded';
    const detail = stageDetail(lead);
    const operatorSub = lead.responsible_is_current ? 'в списке сейчас' : fmtWhen(lead.last_call?.at);
    const name = lead.full_name || 'Без имени';
    return (
        <button
            type="button"
            onClick={() => onOpen(lead)}
            className={`w-full px-4 py-3 text-left transition hover:bg-slate-50 active:bg-slate-100 ${GRID}`}
        >
            {/* Узкий экран */}
            <div className="flex items-start gap-3 lg:hidden">
                <Avatar name={lead.full_name} dim={excluded} />
                <div className="min-w-0 flex-1">
                    <div className="flex items-baseline gap-2">
                        <span className={`min-w-0 flex-1 truncate text-[14.5px] font-semibold ${excluded ? 'text-slate-400' : 'text-slate-900'}`}>{name}</span>
                        {lead.last_call && <span className="shrink-0 text-[12px] tabular-nums text-slate-400">{fmtWhen(lead.last_call.at)}</span>}
                    </div>
                    <div className="mt-0.5 flex min-w-0 items-center gap-1.5 text-[12.5px] text-slate-500">
                        <Dot stage={lead.stage} />
                        <span className="min-w-0 truncate">
                            <span className="text-slate-700">{lead.stage_label}</span>{detail ? ` · ${detail}` : ''}
                        </span>
                    </div>
                    {(lead.outcome || lead.comment) && (
                        <div className="mt-1.5 flex min-w-0 items-center gap-2">
                            {lead.outcome && <OutcomeBadge outcome={lead.outcome} small className="shrink-0" />}
                            {lead.comment && <span className="min-w-0 truncate text-[12.5px] text-slate-500">{lead.comment}</span>}
                        </div>
                    )}
                </div>
                <ChevronRight size={16} className="mt-2.5 shrink-0 text-slate-300" />
            </div>

            {/* Широкий экран: колонки */}
            <div className="hidden min-w-0 items-center gap-3 lg:flex">
                <Avatar name={lead.full_name} dim={excluded} />
                <div className="min-w-0">
                    <div className="flex items-center gap-1.5">
                        <span className={`truncate text-[14px] font-semibold ${excluded ? 'text-slate-400' : 'text-slate-900'}`}>{name}</span>
                        {lead.note && (
                            <span title={`Заметка: ${lead.note}`} className="shrink-0 text-slate-400">
                                <StickyNote size={13} />
                            </span>
                        )}
                    </div>
                    <div className="truncate text-[12px] tabular-nums text-slate-400">
                        {lead.phone_masked}{showMonth && lead.period_label ? ` · ${lead.period_label}` : ''}
                    </div>
                </div>
            </div>
            <div className="hidden min-w-0 lg:block">
                <div className="flex items-center gap-1.5 text-[13px] text-slate-800">
                    <Dot stage={lead.stage} />
                    <span className="truncate">{lead.stage_label}</span>
                </div>
                {detail && <div className="truncate pl-3.5 text-[12px] text-slate-500">{detail}</div>}
            </div>
            {/* Пустая клетка, а не «—»: столбец прочерков у каждой ещё не
                обзвоненной строки — тот же шум, только ровными рядами. */}
            <div className="hidden min-w-0 lg:block">
                {lead.outcome && <OutcomeBadge outcome={lead.outcome} small />}
                {lead.comment && <div className="mt-0.5 truncate text-[12px] text-slate-500" title={lead.comment}>{lead.comment}</div>}
            </div>
            <div className="hidden min-w-0 lg:block">
                {lead.responsible && <div className="truncate text-[13px] text-slate-800">{lead.responsible.name}</div>}
                {lead.responsible && operatorSub && <div className="truncate text-[12px] text-slate-500">{operatorSub}</div>}
            </div>
            <ChevronRight size={14} className="hidden text-slate-300 lg:block" />
        </button>
    );
};

/* ─── полосы этапов и итогов ────────────────────────────────────────────── */

/* Полоса этапов — фильтр и легенда разом (как статусы в «Посылках»): точка в
   ней того же цвета, что точка в строке. Переносится по строкам, а не уезжает
   вбок: на телефоне прокрутка вбок прятала половину этапов за край. */
const StageStrip = ({ value, onChange, counts }) => {
    const all = STAGES.reduce((sum, s) => sum + (Number(counts[s.value]) || 0), 0);
    const items = [{ value: '', label: 'Все', count: all }, ...STAGES.map((s) => ({ ...s, count: Number(counts[s.value]) || 0 }))];
    return (
        <div role="tablist" aria-label="Этап" className="flex flex-wrap items-center gap-1 rounded-xl bg-slate-100 p-1">
            {items.map((item) => {
                const active = value === item.value;
                return (
                    <button
                        key={item.value || 'all'}
                        type="button"
                        role="tab"
                        aria-selected={active}
                        onClick={() => onChange(item.value)}
                        className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg px-2.5 py-1.5 text-[12.5px] transition active:scale-[0.98] ${
                            active
                                ? 'bg-white font-semibold text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.10)]'
                                : `font-medium hover:bg-white/60 ${item.count ? 'text-slate-600' : 'text-slate-400'}`
                        }`}
                    >
                        {item.dot && <span className={`h-2 w-2 shrink-0 rounded-full ${item.dot} ${item.count || active ? '' : 'opacity-40'}`} />}
                        {item.label}
                        <span className={`tabular-nums ${active ? 'text-slate-500' : 'text-slate-400'}`}>{item.count}</span>
                    </button>
                );
            })}
        </div>
    );
};

/* Чипы итогов: белые, цвет — только точка. Итог с нулём не показываем — нажатие
   на него дало бы пустой список; выбранный остаётся, чтобы его можно было снять. */
const OutcomeStrip = ({ outcomes, value, onChange }) => {
    const visible = outcomes.filter((o) => o.count > 0 || o.id === value);
    if (!visible.length) return null;
    return (
        <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-0.5 text-[12px] text-slate-500">Итог оператора</span>
            {visible.map((o) => {
                const active = value === o.id;
                const color = /^#[0-9A-Fa-f]{6}$/.test(o.color || '') ? o.color : '#8E8E93';
                return (
                    <button
                        key={o.id}
                        type="button"
                        aria-pressed={active}
                        onClick={() => onChange(active ? '' : o.id)}
                        title={o.is_active === false ? 'Итог выключен, но встречается в истории' : undefined}
                        className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[12.5px] font-medium transition active:scale-[0.97] ${
                            active ? 'text-white shadow-sm' : 'bg-white text-slate-700 ring-1 ring-slate-200/80 hover:ring-slate-300'
                        }`}
                        style={active ? { backgroundColor: color } : undefined}
                    >
                        {!active && <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: color }} />}
                        <span className={o.is_active === false ? 'line-through decoration-slate-400' : ''}>{o.name}</span>
                        <span className={`tabular-nums ${active ? 'text-white/80' : 'text-slate-400'}`}>{o.count}</span>
                        {active && <X size={12} className="text-white/80" />}
                    </button>
                );
            })}
        </div>
    );
};

/* ─── карточка водителя ─────────────────────────────────────────────────── */

/* Сгруппированный список iOS: подпись слева, значение справа, разделитель с
   отступом слева — как в «Настройках». */
const InfoRow = ({ label, children, sub, subTitle }) => (
    <div className="flex items-start justify-between gap-4 py-2.5 pr-4">
        <div className="shrink-0 text-[13.5px] text-slate-500">{label}</div>
        <div className="min-w-0 text-right">
            <div className="text-[13.5px] text-slate-900">{children}</div>
            {sub && <div className="truncate text-[12px] text-slate-400" title={subTitle}>{sub}</div>}
        </div>
    </div>
);

const Bubble = ({ children }) => (
    <div className="mt-1.5 whitespace-pre-wrap break-words rounded-xl bg-slate-100/80 px-3 py-2 text-[13px] leading-snug text-slate-700">
        {children}
    </div>
);

const SOURCE_LABEL = { webhook: 'исход прислал вебхук Binotel', poll: 'исход получен опросом Binotel' };

const AttemptItem = ({ attempt, recording, onRecording }) => {
    const result = attempt.result;
    const tone = result === 'answered' ? 'green' : result === 'failed' ? 'red'
        : result === 'cancelled' ? 'slate' : result ? 'amber' : 'blue';
    const Icon = result === 'answered' ? PhoneCall : result === 'failed' ? TriangleAlert
        : result === 'other' || result === 'cancelled' ? PhoneOff : result ? PhoneMissed : PhoneOutgoing;
    const title = result ? (RESULT_LABEL[result] || result) : (STATE_LABEL[attempt.state] || 'Звонок');
    // Отмена — оператор положил трубку, пока АТС набирала водителя: такая попытка
    // не идёт в счёт ни строке, ни водителю, поэтому подпись объясняет это явно.
    const cancelledHint = result === 'cancelled'
        ? ' · оператор завершил звонок до ответа водителя, попытка не засчитана' : '';
    const rec = recording || {};
    return (
        <li className="flex gap-3 py-3 pr-4">
            <div className={`mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full ${TONE[tone]}`}>
                <Icon size={14} />
            </div>
            <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2">
                    <span className="text-[14px] font-semibold text-slate-900">{title}</span>
                    {/* Ответ АТС — служебный текст на английском; нужен, только когда
                        разбираются, почему звонок не ушёл, поэтому он под «i». */}
                    {attempt.api_error && <span className="self-center"><IosHint text={attempt.api_error} label="Ответ АТС" /></span>}
                    {attempt.billsec > 0 && <span className="text-[13px] tabular-nums text-slate-500">{fmtDuration(attempt.billsec)}</span>}
                    <span className="ml-auto shrink-0 text-[12px] tabular-nums text-slate-400" title={SOURCE_LABEL[attempt.final_source]}>
                        {fmtClock(attempt.requested_at)}
                    </span>
                </div>
                <div className="text-[12.5px] text-slate-500">
                    {attempt.operator?.name || 'Оператор'}
                    {attempt.internal_number && <> · линия {attempt.internal_number}</>}
                    {cancelledHint}
                </div>
                {attempt.outcome && <OutcomeBadge outcome={attempt.outcome} className="mt-2" />}
                {attempt.comment && <Bubble>{attempt.comment}</Bubble>}
                {attempt.recording_available && (
                    <div className="mt-2">
                        {rec.url ? (
                            // autoPlay: запись запросили нажатием, второе нажатие «▶» было бы лишним.
                            <audio controls autoPlay preload="auto" src={rec.url} className="h-9 w-full max-w-md" aria-label="Запись разговора" />
                        ) : (
                            <button
                                type="button"
                                onClick={() => onRecording(attempt.id)}
                                disabled={rec.loading}
                                className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-3 py-1.5 text-[12.5px] font-medium text-slate-700 transition hover:bg-slate-200 active:scale-[0.98] disabled:opacity-60"
                            >
                                {rec.loading ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} className="fill-current" />}
                                {rec.loading ? 'Запрашиваем у АТС…' : 'Прослушать запись'}
                            </button>
                        )}
                        {rec.error && <div className="mt-1 text-[12px] text-rose-600">{rec.error}</div>}
                    </div>
                )}
            </div>
        </li>
    );
};

const EVENT_META = {
    requeue: { label: 'Вернули в список', Icon: Undo2 },
    restore: { label: 'Вернули в обзвон', Icon: Undo2 },
    exclude: { label: 'Исключили из обзвона', Icon: Ban },
    note: { label: 'Заметка', Icon: StickyNote },
};

const EventItem = ({ event }) => {
    const meta = EVENT_META[event.kind] || { label: event.kind, Icon: StickyNote };
    const label = event.kind === 'note' && !event.note ? 'Заметку убрали' : meta.label;
    return (
        <li className="flex gap-3 py-3 pr-4">
            <div className={`mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full ${TONE.slate}`}>
                <meta.Icon size={14} />
            </div>
            <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2">
                    <span className="text-[14px] font-semibold text-slate-900">{label}</span>
                    <span className="ml-auto shrink-0 text-[12px] tabular-nums text-slate-400">{fmtClock(event.at)}</span>
                </div>
                <div className="text-[12.5px] text-slate-500">{event.actor?.name || 'Руководитель'}</div>
                {event.note && <Bubble>{event.note}</Bubble>}
            </div>
        </li>
    );
};

/* История по дням: заголовок дня, под ним события без повторения даты у каждого. */
const History = ({ lead, recordings, onRecording }) => {
    const groups = useMemo(() => {
        const timeline = [
            ...(lead.attempts || []).map((a) => ({ kind: 'attempt', at: a.requested_at, item: a })),
            ...(lead.events || []).map((e) => ({ kind: 'event', at: e.at, item: e })),
        ].sort((x, y) => String(y.at || '').localeCompare(String(x.at || '')));
        const out = [];
        timeline.forEach((t) => {
            const key = dayKey(t.at);
            if (!out.length || out[out.length - 1].key !== key) out.push({ key, at: t.at, items: [] });
            out[out.length - 1].items.push(t);
        });
        return out;
    }, [lead.attempts, lead.events]);

    if (!groups.length) {
        return <div className="px-4 py-6 text-center text-[13px] text-slate-500">Звонков по этому водителю ещё не было.</div>;
    }
    return groups.map((g) => (
        <div key={g.key || 'none'}>
            <div className="px-4 pb-0.5 pt-3 text-[12px] font-semibold text-slate-500">{fmtDay(g.at)}</div>
            <ul className="ml-4 divide-y divide-slate-100">
                {g.items.map((t) => (t.kind === 'attempt'
                    ? <AttemptItem key={`a-${t.item.id}`} attempt={t.item} recording={recordings[t.item.id]} onRecording={onRecording} />
                    : <EventItem key={`e-${t.item.id}`} event={t.item} />))}
            </ul>
        </div>
    ));
};

const NoteSection = ({ lead, canEdit, busy, draft, setDraft, onSave }) => {
    if (!canEdit && !lead.note) return null;
    const editing = draft !== null;
    const changed = editing && draft.trim() !== (lead.note || '').trim();
    return (
        <section className="space-y-1.5">
            <div className="flex items-center gap-1.5">
                <span className={iosGroupLabel}>Заметка</span>
                <IosHint text="Заметку видят только руководители в журнале. Оператору в телефоне она не показывается." />
            </div>
            <div className={`${iosCard} overflow-hidden`}>
                {editing ? (
                    <div className="p-3">
                        <textarea
                            value={draft}
                            onChange={(e) => setDraft(e.target.value)}
                            onKeyDown={(e) => {
                                if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && changed && !busy) onSave();
                            }}
                            rows={3}
                            maxLength={500}
                            autoFocus
                            placeholder="Например: просил звонить после 18:00"
                            className={`${iosInput} resize-none`}
                        />
                        <div className="mt-2 flex items-center justify-end gap-2">
                            <span className="mr-auto text-[11.5px] tabular-nums text-slate-400">{draft.length}/500</span>
                            <button type="button" onClick={() => setDraft(null)} className={iosBtnGhost}>Отмена</button>
                            <button type="button" onClick={onSave} disabled={busy || !changed} className={iosBtnPrimary}>
                                {busy && <Loader2 size={13} className="animate-spin" />}
                                Сохранить
                            </button>
                        </div>
                    </div>
                ) : lead.note ? (
                    <div className="flex items-start gap-3 px-4 py-3">
                        <div className="min-w-0 flex-1 whitespace-pre-wrap break-words text-[13.5px] leading-snug text-slate-800">{lead.note}</div>
                        {canEdit && (
                            <button type="button" onClick={() => setDraft(lead.note || '')} className="shrink-0 text-[13px] font-medium text-blue-600 hover:text-blue-700">
                                Изменить
                            </button>
                        )}
                    </div>
                ) : (
                    <button
                        type="button"
                        onClick={() => setDraft('')}
                        className="flex w-full items-center gap-2 px-4 py-3 text-left text-[13.5px] font-medium text-blue-600 transition hover:bg-slate-50"
                    >
                        <Plus size={15} /> Добавить заметку
                    </button>
                )}
            </div>
        </section>
    );
};

const CONFIRM_TEXT = {
    exclude: 'Исключить водителя из обзвона? Операторы больше его не получат.',
    requeue: 'Вернуть водителя в список? Попытки обнулятся. Если его строка ещё на экране у оператора — '
        + 'она снова станет доступной для звонка, иначе водитель попадёт в ближайшую порцию.',
    restore: 'Вернуть водителя в обзвон? Попытки обнулятся, он попадёт в ближайшую порцию.',
};

const LeadSheet = ({
    open, lead, loading, error, canEdit, busy, recordings,
    onClose, onRetry, onRequeue, onExclude, onSaveNote, onRecording,
}) => {
    const [confirm, setConfirm] = useState(null); // 'requeue' | 'restore' | 'exclude' | null
    const [reason, setReason] = useState('');
    const [noteDraft, setNoteDraft] = useState(null); // null — заметку не редактируем
    const leadId = lead?.id;

    useEffect(() => { setConfirm(null); setReason(''); setNoteDraft(null); }, [leadId, open]);

    /* Набранная, но не сохранённая заметка не должна пропадать от промаха мимо
       окна, крестика или системного «назад»: спрашиваем. false — окно остаётся
       (так его понимают и IosModal, и жест «назад» на телефоне). */
    const noteDirty = noteDraft !== null && noteDraft.trim() !== (lead?.note || '').trim();
    const requestClose = () => {
        if (noteDirty && !window.confirm('Заметка не сохранена. Закрыть карточку без неё?')) return false;
        onClose();
        return true;
    };

    /* Escape снимает сперва то, что открыто внутри окна (подтверждение, правку
       заметки), и только потом закрывает само окно. Нажатие, пришедшее из
       раскрытого элемента (подсказка «i» и т. п.), — его собственное: оно
       закрывает подсказку, а не карточку.
       Слушаем в фазе ЗАХВАТА: на всплытии React уже успевает перерисовать
       подсказку закрытой, и aria-expanded="true" на ней было бы не застать. */
    const escRef = useRef(null);
    escRef.current = (e) => {
        if (e.target?.closest?.('[aria-expanded="true"]')) return;
        if (confirm) { setConfirm(null); setReason(''); return; }
        if (noteDraft !== null) { setNoteDraft(null); return; }
        requestClose();
    };
    useEffect(() => {
        if (!open) return undefined;
        const onKey = (e) => { if (e.key === 'Escape') escRef.current?.(e); };
        window.addEventListener('keydown', onKey, true);
        return () => window.removeEventListener('keydown', onKey, true);
    }, [open]);

    const historyReady = Array.isArray(lead?.attempts);
    const canRequeue = canEdit && historyReady && ['answered', 'exhausted', 'waiting', 'excluded'].includes(lead.stage);
    const canExclude = canEdit && historyReady && lead.stage !== 'excluded';
    const requeueKind = lead?.stage === 'excluded' ? 'restore' : 'requeue';

    const submit = async () => {
        const ok = await (confirm === 'exclude' ? onExclude(reason) : onRequeue(reason));
        if (ok) { setConfirm(null); setReason(''); }
    };
    const saveNote = async () => {
        if (await onSaveNote(noteDraft)) setNoteDraft(null);
    };

    let footer = null;
    if (lead && (canRequeue || canExclude)) {
        footer = confirm ? (
            <div className="w-full space-y-2.5">
                <div className="text-[13px] leading-snug text-slate-700">
                    {CONFIRM_TEXT[confirm]}
                    {confirm === 'exclude' && lead.stage === 'issued' ? ' Строка уйдёт из списка оператора.' : ''}
                </div>
                <input
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter' && !busy) submit(); }}
                    maxLength={500}
                    autoFocus
                    placeholder="Причина — необязательно"
                    className={iosInput}
                />
                <div className="flex justify-end gap-2">
                    <button type="button" onClick={() => { setConfirm(null); setReason(''); }} className={iosBtnSecondary}>Отмена</button>
                    <button type="button" disabled={busy} onClick={submit} className={confirm === 'exclude' ? BTN_DANGER : iosBtnPrimary}>
                        {busy && <Loader2 size={14} className="animate-spin" />}
                        {confirm === 'exclude' ? 'Исключить' : requeueKind === 'restore' ? 'Вернуть в обзвон' : 'Вернуть в список'}
                    </button>
                </div>
            </div>
        ) : (
            <>
                {canExclude && (
                    <button type="button" onClick={() => setConfirm('exclude')} className={`${BTN_DANGER_GHOST} mr-auto`}>
                        <Ban size={14} /> Исключить из обзвона
                    </button>
                )}
                {/* Без «i»: подвал окна режет всплывающую подсказку своим краем. */}
                {lead.stage === 'issued' && (
                    <span className="text-[12.5px] text-slate-500">Сейчас у оператора — вернуть можно после звонка</span>
                )}
                {canRequeue && (
                    <button type="button" onClick={() => setConfirm(requeueKind)} className={iosBtnPrimary}>
                        <Undo2 size={15} /> {requeueKind === 'restore' ? 'Вернуть в обзвон' : 'Вернуть в список'}
                    </button>
                )}
            </>
        );
    }

    const firstBatch = lead?.batches?.[0];
    const batchLine = [firstBatch?.file_name, fmtDate(firstBatch?.uploaded_at || lead?.created_at), firstBatch?.uploaded_by]
        .filter(Boolean).join(' · ');

    return (
        <IosModal
            open={open}
            onClose={requestClose}
            title={lead?.full_name || (loading ? 'Загрузка…' : 'Водитель')}
            subtitle={lead ? [lead.phone_masked, monthLabel(lead.period)].filter(Boolean).join(' · ') : undefined}
            maxWidth="max-w-xl"
            footer={footer}
        >
            {!lead && error ? (
                <div className="py-8 text-center">
                    <div className="text-[13.5px] text-rose-600">{error}</div>
                    <button type="button" onClick={onRetry} className={`${iosBtnSecondary} mt-3`}>Повторить</button>
                </div>
            ) : !lead ? (
                <div className="space-y-3 py-2">
                    <Skeleton className="h-4 w-1/2" /><Skeleton className="h-4 w-2/3" /><Skeleton className="h-4 w-1/3" />
                </div>
            ) : (
                <div className="space-y-5">
                    <div className={`${iosCard} overflow-hidden`}>
                        <div className="ml-4 divide-y divide-slate-100">
                            <InfoRow
                                label="Этап"
                                sub={lead.stage === 'waiting' && lead.next_retry_at ? `повтор ${fmtWhen(lead.next_retry_at)}`
                                    : lead.stage === 'answered' && lead.answered_at ? fmtWhen(lead.answered_at) : ''}
                            >
                                <span className="inline-flex items-center gap-1.5"><Dot stage={lead.stage} />{lead.stage_label}</span>
                            </InfoRow>
                            <InfoRow label="Попытки">
                                <span className="tabular-nums">{lead.attempts_total} из {lead.max_attempts}</span>
                            </InfoRow>
                            <InfoRow
                                label="Ответственный"
                                sub={lead.responsible ? (lead.responsible_is_current ? 'в списке сейчас' : 'по последнему звонку') : ''}
                            >
                                {lead.responsible?.name || <span className="text-slate-400">ещё не выдавался</span>}
                            </InfoRow>
                            <InfoRow
                                label="База"
                                sub={`${batchLine}${lead.upload_count > 1
                                    ? ` · в файлах ${lead.upload_count} ${plural(lead.upload_count, 'раз', 'раза', 'раз')}` : ''}`}
                                subTitle={batchLine}
                            >
                                {monthLabel(lead.period) || '—'}
                            </InfoRow>
                        </div>
                    </div>

                    <NoteSection lead={lead} canEdit={canEdit && historyReady} busy={busy} draft={noteDraft} setDraft={setNoteDraft} onSave={saveNote} />

                    <section className="space-y-1.5">
                        <div className="flex items-center justify-between gap-3">
                            <span className={iosGroupLabel}>История</span>
                            {historyReady && lead.attempts.length > 0 && (
                                <span className="px-1 text-[12px] tabular-nums text-slate-400">
                                    {lead.attempts.length} {plural(lead.attempts.length, 'звонок', 'звонка', 'звонков')}
                                </span>
                            )}
                        </div>
                        <div className={`${iosCard} overflow-hidden pb-1`}>
                            {historyReady ? (
                                <History lead={lead} recordings={recordings} onRecording={onRecording} />
                            ) : error ? (
                                <div className="px-4 py-5 text-center text-[13px] text-rose-600">
                                    {error}
                                    <button type="button" onClick={onRetry} className="ml-2 font-medium text-blue-600">Повторить</button>
                                </div>
                            ) : (
                                <div className="space-y-3 p-4">
                                    <Skeleton className="h-4 w-2/5" /><Skeleton className="h-4 w-3/5" /><Skeleton className="h-4 w-1/3" />
                                </div>
                            )}
                        </div>
                    </section>
                </div>
            )}
        </IosModal>
    );
};

/* ─── сам журнал ────────────────────────────────────────────────────────── */

const DialListJournal = ({
    apiBaseUrl, authHeaders, departmentId, batches = [], periods = [], activePeriod = '',
    period = '', onPeriodChange, canEdit = true, showToast, onChanged,
}) => {
    const [q, setQ] = useState('');
    const [qDebounced, setQDebounced] = useState('');
    const [stage, setStage] = useState('');
    const [operatorId, setOperatorId] = useState('');
    const [batchId, setBatchId] = useState('');
    const [outcomeId, setOutcomeId] = useState('');
    const [range, setRange] = useState({ from: '', to: '' });
    const [sort, setSort] = useState('activity');
    const [filtersOpen, setFiltersOpen] = useState(false);

    const [items, setItems] = useState([]);
    const [total, setTotal] = useState(0);
    const [byStage, setByStage] = useState({});
    const [byOutcome, setByOutcome] = useState([]);
    const [meta, setMeta] = useState({});
    const [loading, setLoading] = useState(true);
    const [loadingMore, setLoadingMore] = useState(false);
    const [error, setError] = useState('');
    const [users, setUsers] = useState([]);

    const [openId, setOpenId] = useState(null);
    const [card, setCard] = useState(null);
    const [cardLoading, setCardLoading] = useState(false);
    const [cardError, setCardError] = useState('');
    const [busy, setBusy] = useState(false);
    const [recordings, setRecordings] = useState({});
    const requestSeq = useRef(0);
    const summarySeq = useRef(0);
    const cardSeq = useRef(0);
    const openIdRef = useRef(null);
    const itemsCount = useRef(0);
    itemsCount.current = items.length;

    const toast = useCallback((msg, kind = 'success') => {
        if (typeof showToast === 'function') showToast(msg, kind);
    }, [showToast]);

    useEffect(() => {
        const t = setTimeout(() => setQDebounced(q.trim()), 300);
        return () => clearTimeout(t);
    }, [q]);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/users`, { credentials: 'include', headers: authHeaders() });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok) throw new Error(data?.error || `HTTP ${resp.status}`);
                if (!cancelled) setUsers(Array.isArray(data.users) ? data.users : []);
            } catch {
                if (!cancelled) setUsers([]);
            }
        })();
        return () => { cancelled = true; };
    }, [apiBaseUrl, authHeaders, departmentId]);

    // Месяц: '' — обзваниваемый (сервер знает какой), 'all' — все, иначе ISO первого дня.
    const shownPeriod = period || activePeriod || '';

    const buildQuery = useCallback((offset, limit) => {
        const qs = new URLSearchParams({ limit: String(limit), offset: String(offset), sort });
        if (qDebounced) qs.set('q', qDebounced);
        if (stage) qs.set('stage', stage);
        if (operatorId) qs.set('operator_id', operatorId);
        if (batchId) qs.set('batch_id', batchId);
        if (outcomeId) qs.set('outcome_id', outcomeId);
        if (period) qs.set('period', period);
        if (range.from) qs.set('date_from', range.from);
        if (range.to) qs.set('date_to', range.to);
        return qs;
    }, [qDebounced, stage, operatorId, batchId, outcomeId, period, range.from, range.to, sort]);

    const applySummary = (data) => {
        setTotal(Number(data.total) || 0);
        setByStage(data.by_stage || {});
        setByOutcome(Array.isArray(data.by_outcome) ? data.by_outcome : []);
        setMeta({ period: data.period, period_label: data.period_label, active_period: data.active_period });
    };

    /* keepSize — перечитать столько строк, сколько уже показано: после действия в
       карточке список не должен схлопываться обратно до первой страницы. Сервер
       отдаёт за раз не больше MAX_PAGE строк; если показано больше, перечитываем
       только числа (total и сводку), а саму строку уже поправили на месте. */
    const load = useCallback(async (offset = 0, { keepSize = false } = {}) => {
        if (keepSize && itemsCount.current > MAX_PAGE) {
            const seq = ++summarySeq.current;
            try {
                const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/leads?${buildQuery(0, 1).toString()}`, { credentials: 'include', headers: authHeaders() });
                if (resp.ok && seq === summarySeq.current) applySummary(await resp.json());
            } catch { /* числа догонят на следующей загрузке */ }
            return;
        }
        const seq = ++requestSeq.current;
        const limit = keepSize ? Math.max(PAGE, itemsCount.current) : PAGE;
        if (offset === 0) setLoading(true); else setLoadingMore(true);
        setError('');
        try {
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/leads?${buildQuery(offset, limit).toString()}`, { credentials: 'include', headers: authHeaders() });
            if (!resp.ok) throw new Error(await readError(resp));
            const data = await resp.json();
            if (seq !== requestSeq.current) return;
            const list = Array.isArray(data.items) ? data.items : [];
            // При «Сначала свежие» водитель, по которому только что позвонили, между
            // страницами переезжает вверх и пришёл бы второй раз — с тем же key.
            setItems((cur) => {
                if (offset === 0) return list;
                const seen = new Set(cur.map((it) => it.id));
                return [...cur, ...list.filter((it) => !seen.has(it.id))];
            });
            applySummary(data);
        } catch (e) {
            if (seq !== requestSeq.current) return;
            setError(e.message || 'Не удалось загрузить журнал');
        } finally {
            if (seq === requestSeq.current) { setLoading(false); setLoadingMore(false); }
        }
    }, [apiBaseUrl, authHeaders, departmentId, buildQuery]);

    useEffect(() => { load(0); }, [load]);

    const loadCard = useCallback(async (leadId) => {
        const seq = ++cardSeq.current;
        setCardLoading(true);
        setCardError('');
        try {
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/leads/${leadId}`, { credentials: 'include', headers: authHeaders() });
            if (!resp.ok) throw new Error(await readError(resp));
            const data = await resp.json();
            if (seq === cardSeq.current && data.lead) setCard(data.lead);
        } catch (e) {
            if (seq === cardSeq.current) setCardError(e.message || 'Не удалось открыть карточку');
        } finally {
            if (seq === cardSeq.current) setCardLoading(false);
        }
    }, [apiBaseUrl, authHeaders]);

    // Строку списка показываем сразу, историю догружаем: окно открывается без пустой паузы.
    const openLead = (lead) => {
        openIdRef.current = lead.id;
        setOpenId(lead.id);
        setCard({ ...lead, attempts: undefined, events: undefined });
        setRecordings({});
        loadCard(lead.id);
    };

    /* Карточку при закрытии не обнуляем: на телефоне экран ещё уезжает вправо, и
       на нём должна остаться та же карточка, а не скелетон «Загрузка…». Следующее
       открытие всё равно ставит свою. */
    const closeCard = useCallback(() => {
        cardSeq.current += 1;
        openIdRef.current = null;
        setOpenId(null);
        setCardLoading(false);
        setCardError('');
    }, []);

    const act = async (path, method, body, okMessage) => {
        const leadId = openId;
        if (!leadId) return false;
        setBusy(true);
        try {
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/leads/${leadId}/${path}`, {
                method, credentials: 'include',
                headers: authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify(body || {}),
            });
            if (!resp.ok) throw new Error(await readError(resp));
            const data = await resp.json();
            if (data.lead) {
                // Пока шёл запрос, карточку могли закрыть и открыть другую: ответ по
                // прежнему водителю в неё не кладём, иначе кнопки действовали бы на
                // одного, а на экране был бы другой.
                if (openIdRef.current === leadId) setCard(data.lead);
                setItems((cur) => cur.map((it) => (it.id === data.lead.id ? { ...it, ...data.lead, attempts: undefined, events: undefined } : it)));
                load(0, { keepSize: true });
                onChanged?.();
            }
            if (okMessage) toast(okMessage, 'success');
            return true;
        } catch (e) {
            toast(e.message || 'Не получилось', 'error');
            return false;
        } finally {
            setBusy(false);
        }
    };

    const fetchRecording = async (attemptId) => {
        setRecordings((cur) => ({ ...cur, [attemptId]: { loading: true } }));
        try {
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/attempts/${attemptId}/recording`, { credentials: 'include', headers: authHeaders() });
            if (!resp.ok) throw new Error(await readError(resp));
            const data = await resp.json();
            setRecordings((cur) => ({ ...cur, [attemptId]: { url: data.url } }));
        } catch (e) {
            setRecordings((cur) => ({ ...cur, [attemptId]: { error: e.message || 'Запись недоступна' } }));
        }
    };

    const operatorOptions = useMemo(() => [
        { value: '', label: 'Все операторы' },
        ...users.map((u) => ({ value: String(u.id), label: u.name || u.login || `#${u.id}` })),
    ], [users]);

    const batchOptions = useMemo(() => [
        { value: '', label: 'Все файлы' },
        ...batches.map((b) => ({ value: String(b.id), label: `${b.file_name || 'файл'} · ${fmtDate(b.created_at)}` })),
    ], [batches]);

    const periodOptions = useMemo(
        () => buildPeriodOptions(periods, { includeAll: true, activePeriod, grouped: true }),
        [periods, activePeriod],
    );

    /* Отобранное в панели — чипами под полосой. Этап и итог сюда не входят: у них
       свои полосы, и выбранное там и так видно. */
    const chips = [
        operatorId && {
            key: 'operator', name: 'Оператор',
            label: operatorOptions.find((o) => o.value === operatorId)?.label || `#${operatorId}`,
            clear: () => setOperatorId(''),
        },
        batchId && {
            key: 'batch', name: 'Файл',
            label: batchOptions.find((o) => o.value === batchId)?.label || 'файл',
            clear: () => setBatchId(''),
        },
        (range.from || range.to) && {
            key: 'range', name: 'Звонили', label: rangeLabel(range.from, range.to),
            clear: () => setRange({ from: '', to: '' }),
        },
    ].filter(Boolean);

    const hasFilters = Boolean(qDebounced || stage || outcomeId || chips.length);
    const resetFilters = () => {
        setQ(''); setStage(''); setOperatorId(''); setBatchId(''); setOutcomeId(''); setRange({ from: '', to: '' });
    };
    const viewingOtherMonth = shownPeriod && shownPeriod !== 'all' && activePeriod && shownPeriod !== activePeriod;

    return (
        <section className="space-y-3">
            {/* Полоса: поиск · месяц · фильтры · обновить */}
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <div className="relative sm:flex-1">
                    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                        type="search"
                        value={q}
                        onChange={(e) => setQ(e.target.value)}
                        placeholder="ФИО или последние цифры номера"
                        className={`${iosInput} pl-9`}
                        aria-label="Поиск по журналу"
                    />
                </div>
                <div className="flex items-center gap-2">
                    <CustomSelect
                        value={shownPeriod}
                        onChange={(v) => onPeriodChange?.(v)}
                        options={periodOptions}
                        variant="ios"
                        className="min-w-0 flex-1 sm:w-64 sm:flex-none"
                        ariaLabel="Месяц базы"
                    />
                    <button
                        type="button"
                        onClick={() => setFiltersOpen((v) => !v)}
                        aria-expanded={filtersOpen}
                        className={`${chips.length ? iosBtnPrimary : iosBtnSecondary} shrink-0`}
                    >
                        <SlidersHorizontal size={15} />
                        Фильтры{chips.length ? <span className="tabular-nums">· {chips.length}</span> : null}
                    </button>
                    <button
                        type="button"
                        onClick={() => load(0, { keepSize: items.length <= MAX_PAGE })}
                        disabled={loading}
                        className={`${iosBtnGhost} shrink-0`}
                        aria-label="Обновить"
                        title="Обновить"
                    >
                        <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
                    </button>
                </div>
            </div>

            {chips.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5">
                    {chips.map((chip) => (
                        <button
                            key={chip.key}
                            type="button"
                            onClick={chip.clear}
                            title={`Убрать: ${chip.label}`}
                            className="group inline-flex max-w-full items-center gap-1.5 rounded-full bg-white px-2.5 py-1 text-[12.5px] text-slate-700 ring-1 ring-slate-200/80 transition hover:ring-slate-300 active:scale-[0.98]"
                        >
                            <span className="text-slate-400">{chip.name}</span>
                            <span className="truncate font-medium">{chip.label}</span>
                            <X size={12} className="shrink-0 text-slate-400 group-hover:text-slate-600" />
                        </button>
                    ))}
                    <button
                        type="button"
                        onClick={() => { setOperatorId(''); setBatchId(''); setRange({ from: '', to: '' }); }}
                        className="px-1.5 text-[12.5px] text-slate-500 underline decoration-slate-300 underline-offset-2 transition hover:text-slate-700"
                    >
                        сбросить всё
                    </button>
                </div>
            )}

            {filtersOpen && (
                /* Подпись каждого поля — блок одной высоты (FIELD_HEAD). Строчная
                   подпись внутри <label> занимала строку высотой со шрифт самого
                   label (~24 px), а подпись-блок — свою (18 px), и поле дат
                   стояло на 6 px выше соседей. */
                <div className={`${iosCard} grid gap-3 p-3.5 sm:grid-cols-3`}>
                    <label className="block space-y-1.5">
                        <span className={FIELD_HEAD}>Оператор</span>
                        <CustomSelect value={operatorId} onChange={setOperatorId} options={operatorOptions} variant="ios" searchable={users.length > 8} ariaLabel="Оператор" />
                    </label>
                    <label className="block space-y-1.5">
                        <span className={FIELD_HEAD}>Файл загрузки</span>
                        <CustomSelect value={batchId} onChange={setBatchId} options={batchOptions} variant="ios" ariaLabel="Файл загрузки" />
                    </label>
                    {/* div, а не label: календарь раскрывается внутри, и label
                        отдавал бы щелчок по пустому месту календаря кнопке-чипу —
                        календарь закрывался бы на полуслове. */}
                    <div className="space-y-1.5">
                        <span className={FIELD_HEAD}>Дата звонка</span>
                        <IosDateRangePicker
                            from={range.from}
                            to={range.to}
                            max={isoDate(new Date())}
                            presets={DATE_PRESETS}
                            triggerClassName={DATE_TRIGGER}
                            onChange={(next) => setRange({ from: next?.from || '', to: next?.to || '' })}
                        />
                    </div>
                </div>
            )}

            <StageStrip value={stage} onChange={setStage} counts={byStage} />
            <OutcomeStrip outcomes={byOutcome} value={outcomeId} onChange={setOutcomeId} />

            {viewingOtherMonth && (
                <div className="rounded-xl bg-amber-50 px-3.5 py-2.5 text-[12.5px] text-amber-800 ring-1 ring-amber-100">
                    {monthLabel(shownPeriod)} сейчас не обзванивается: операторы получают порции из базы за {monthLabel(activePeriod)}.
                </div>
            )}

            <div className="space-y-1.5 pt-1">
                <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2 px-1 text-[13px] text-slate-500">
                        <span>
                            <span className="font-semibold tabular-nums text-slate-900">{total}</span>{' '}
                            {plural(total, 'водитель', 'водителя', 'водителей')}
                        </span>
                        {loading && items.length > 0 && <Loader2 size={13} className="animate-spin text-slate-400" />}
                    </div>
                    <CustomSelect value={sort} onChange={setSort} options={SORT_OPTIONS} variant="ios" className="w-44" ariaLabel="Сортировка" />
                </div>

                <div className={`${iosCard} overflow-hidden`}>
                    <div className={`hidden border-b border-slate-100 bg-slate-50/70 px-4 py-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400 ${GRID}`}>
                        <div className="pl-12">Водитель</div>
                        <div>Этап</div>
                        <div>Итог оператора</div>
                        <div>Оператор</div>
                        <div />
                    </div>
                    {error ? (
                        <div className="px-4 py-6 text-center text-[13px] text-rose-600">
                            {error}
                            <button type="button" onClick={() => load(0)} className="ml-2 font-medium text-blue-600">Повторить</button>
                        </div>
                    ) : loading && items.length === 0 ? (
                        <div className="divide-y divide-slate-100">
                            {[0, 1, 2, 3, 4].map((i) => (
                                <div key={i} className="flex items-center gap-3 px-4 py-3.5">
                                    <Skeleton className="h-9 w-9 rounded-full" />
                                    <div className="flex-1 space-y-2"><Skeleton className="h-3.5 w-1/3" /><Skeleton className="h-3 w-1/2" /></div>
                                </div>
                            ))}
                        </div>
                    ) : items.length === 0 ? (
                        <div className="px-4 py-12 text-center">
                            <div className="mx-auto grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                                <Search size={18} />
                            </div>
                            <div className="mt-3 text-[14px] font-semibold text-slate-800">
                                {hasFilters ? 'Никого не нашли' : `В базе за ${meta.period_label || monthLabel(shownPeriod) || 'этот месяц'} пока никого`}
                            </div>
                            <div className="mt-1 text-[12.5px] text-slate-500">
                                {hasFilters ? 'Под выбранные условия никто не подходит.' : 'Загрузите список водителей во вкладке «База водителей» — они появятся здесь.'}
                            </div>
                            {hasFilters && (
                                <button type="button" onClick={resetFilters} className={`${iosBtnSecondary} mt-4`}>Сбросить фильтры</button>
                            )}
                        </div>
                    ) : (
                        <>
                            <div className={`divide-y divide-slate-100 transition-opacity ${loading ? 'opacity-60' : ''}`}>
                                {items.map((lead) => (
                                    <LeadRow key={lead.id} lead={lead} onOpen={openLead} showMonth={shownPeriod === 'all'} />
                                ))}
                            </div>
                            {items.length < total && (
                                <div className="flex items-center justify-between gap-3 border-t border-slate-100 px-4 py-2.5">
                                    <span className="text-[12px] tabular-nums text-slate-500">Показано {items.length} из {total}</span>
                                    <button type="button" onClick={() => load(items.length)} disabled={loadingMore || loading} className={iosBtnSecondary}>
                                        {loadingMore && <Loader2 size={13} className="animate-spin" />}
                                        Показать ещё
                                    </button>
                                </div>
                            )}
                        </>
                    )}
                </div>
            </div>

            <LeadSheet
                open={Boolean(openId)}
                lead={card}
                loading={cardLoading}
                error={cardError}
                canEdit={canEdit}
                busy={busy}
                recordings={recordings}
                onClose={closeCard}
                onRetry={() => openId && loadCard(openId)}
                onRequeue={(note) => act('requeue', 'POST', { note }, card?.stage === 'excluded' ? 'Водитель возвращён в обзвон' : 'Водитель возвращён в список')}
                onExclude={(note) => act('exclude', 'POST', { note }, 'Водитель исключён из обзвона')}
                onSaveNote={(note) => act('note', 'PUT', { note }, note.trim() ? 'Заметка сохранена' : 'Заметка удалена')}
                onRecording={fetchRecording}
            />
        </section>
    );
};

export default DialListJournal;
