import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
    AlertCircle, Calendar, ChevronDown, ChevronLeft, ChevronRight, Loader2, MessageSquare, PhoneCall, RotateCcw,
    ScrollText, Sparkles,
} from 'lucide-react';
import {
    APPLE_FONT, iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, IosHint, IosMenu, IosSegmented, scoreTone,
} from '../ui/ios';
import { IosDateRangeCalendar } from '../ui/DateRangePicker';
import AiMarkup from './AiMarkup';
import AiChatPanel from './AiChatPanel';
import QaChart from './QaChart';
import { DayStat, ShareBar } from './dayKit';
import { plural } from './queueDayRules';
import { useStickyHeight, useWide } from './chatLayout';
import { isoOf, monthRange, periodPresets, shiftMonth, spanDays, wholeMonthOf } from './usePeriod';

/* Экран «Период» вкладки «Сводка»: цифры за несколько дней (до месяца), графики
 * и чат с ИИ о периоде; у целого прошедшего месяца — ещё и сводка ИИ за месяц.
 *
 * Та же раскладка, что у экрана дня: слева цифры и текст, справа чат; на узком
 * экране — две вкладки. Период меняется в шапке: календарь (как у фильтров
 * раздела) с готовыми отрезками и стрелки по месяцам, когда открыт месяц.
 *
 * Цифры считает сервер по слепкам дней (call_qa/digest/facts), графики
 * рисуются из них на месте: средний балл по дням, критические по дням, доля
 * ошибок по критериям, балл по сотрудникам — один график за раз, по
 * переключателю, чтобы экран не превращался в приборную панель.
 */

const MONTHS_GEN = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября',
                    'октября', 'ноября', 'декабря'];
const MONTHS_TITLE = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь',
                      'Октябрь', 'Ноябрь', 'Декабрь'];

const SUGGESTIONS = [
    'Как менялся средний балл по дням? Покажи графиком',
    'Какие ошибки повторяются чаще всего и у кого?',
    'Кому из сотрудников нужна помощь в первую очередь?',
    'Что изменилось по сравнению с прошлым периодом?',
    'Сравни направления между собой',
];

const CHARTS = [
    { value: 'score', label: 'Балл по дням' },
    { value: 'critical', label: 'Критические' },
    { value: 'criteria', label: 'Критерии' },
    { value: 'people', label: 'Сотрудники' },
];

const round = (value) => (value == null ? null : Math.round(value));
const pct = (share) => (share == null ? null : Math.round(share * 100));
const talksText = (n) => `${n} ${plural(n, 'разговор', 'разговора', 'разговоров')}`;
const peopleText = (n) => `${n} ${plural(n, 'сотрудник', 'сотрудника', 'сотрудников')}`;
const daysText = (n) => `${n} ${plural(n, 'день', 'дня', 'дней')}`;
const shortDay = (iso) => { const p = String(iso).split('-'); return p.length === 3 ? `${p[2]}.${p[1]}` : iso; };
const capital = (text) => (text ? text[0].toUpperCase() + text.slice(1) : text);
const truncate = (text, max) => (text && text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text);
const SCORE_COLOR = { green: '#1baf7a', amber: '#eda100', red: '#e34948', slate: '#cbd5e1' };

const fmtStamp = (iso) => {
    if (!iso) return '';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return '';
    const time = date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    return date.toDateString() === new Date().toDateString() ? `сегодня в ${time}`
        : `${date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })} в ${time}`;
};

/* «Сентябрь 2026», «1–15 октября», «16 сентября — 16 октября» — как на сервере. */
export const periodTitle = (range, now = new Date()) => {
    if (!range?.from || !range?.to) return '';
    const [y1, m1, d1] = range.from.split('-').map(Number);
    const [y2, m2, d2] = range.to.split('-').map(Number);
    const year = (y) => (y === now.getFullYear() ? '' : ` ${y}`);
    if (wholeMonthOf(range)) return `${MONTHS_TITLE[m1 - 1]} ${y1}`;
    if (y1 === y2 && m1 === m2) {
        return d1 === d2 ? `${d1} ${MONTHS_GEN[m1 - 1]}${year(y1)}` : `${d1}–${d2} ${MONTHS_GEN[m1 - 1]}${year(y1)}`;
    }
    return `${d1} ${MONTHS_GEN[m1 - 1]}${year(y1)} — ${d2} ${MONTHS_GEN[m2 - 1]}${year(y2)}`;
};

/* Период открыт «по месяцу»: с первого числа и до конца месяца (или до сегодня). */
export const monthViewOf = (range) => {
    if (!range?.from?.endsWith('-01')) return null;
    const month = range.from.slice(0, 7);
    return monthRange(month).to === range.to ? month : null;
};

/* ── выбор периода ───────────────────────────────────────────────────────── */

function PeriodPicker({ range, onChange }) {
    const [open, setOpen] = useState(false);
    const ref = useRef(null);
    useEffect(() => {
        if (!open) return undefined;
        const onDown = (e) => { if (!ref.current?.contains(e.target)) setOpen(false); };
        const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
        document.addEventListener('mousedown', onDown);
        document.addEventListener('keydown', onKey);
        return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
    }, [open]);
    const today = isoOf(new Date());
    return (
        <div ref={ref} className="relative">
            <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} aria-haspopup="dialog"
                    className={`${iosBtnGhost} ${open ? 'bg-slate-100 text-slate-900' : 'text-slate-700'}`}>
                <Calendar size={15} className="text-slate-400" aria-hidden="true" />
                <span>{periodTitle(range)}</span>
                <ChevronDown size={14} className={`text-slate-400 transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden="true" />
            </button>
            {open && (
                <div role="dialog" aria-label="Выбор периода" className="absolute right-0 top-full z-50 mt-2 motion-safe:animate-popover-in">
                    {/* Длиннее 31 дня календарь не запрещает — лишний период обрежет
                        usePeriod.open и скажет об этом; подсказка под сеткой
                        уже занята «Выберите начало периода». */}
                    <IosDateRangeCalendar from={range.from} to={range.to} max={today} presets={periodPresets()}
                                          onChange={(next) => { setOpen(false); onChange(next); }} />
                </div>
            )}
        </div>
    );
}

const ARROW = 'grid h-8 w-8 place-items-center rounded-lg text-slate-600 transition hover:bg-white hover:shadow-sm disabled:cursor-default disabled:text-slate-300 disabled:hover:bg-transparent disabled:hover:shadow-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60';

function MonthArrows({ month, onOpenMonth }) {
    const next = shiftMonth(month, 1);
    const nextAllowed = `${next}-01` <= isoOf(new Date());
    const label = (m) => { const [y, mm] = m.split('-').map(Number); return `${MONTHS_TITLE[mm - 1]} ${y}`; };
    return (
        <div className="flex items-center gap-0.5 rounded-xl bg-slate-100 p-0.5">
            <button type="button" className={ARROW} onClick={() => onOpenMonth(shiftMonth(month, -1))}
                    aria-label={`Предыдущий месяц: ${label(shiftMonth(month, -1))}`} title={label(shiftMonth(month, -1))}>
                <ChevronLeft size={16} aria-hidden="true" />
            </button>
            <button type="button" className={ARROW} disabled={!nextAllowed} onClick={() => onOpenMonth(next)}
                    aria-label={nextAllowed ? `Следующий месяц: ${label(next)}` : 'Следующего месяца ещё нет'}
                    title={nextAllowed ? label(next) : undefined}>
                <ChevronRight size={16} aria-hidden="true" />
            </button>
        </div>
    );
}

/* ── цифры и графики ─────────────────────────────────────────────────────── */

function PeriodStats({ summary, previous }) {
    const totals = summary?.totals || {};
    const prev = previous?.totals || null;
    const ai = round(totals.ai_avg);
    const prevAi = round(prev?.ai_avg);
    const human = round(totals.human_avg);
    const evaluated = totals.evaluated || 0;
    const reviewed = totals.reviewed || 0;
    const share = pct(totals.critical_share);
    const prevShare = pct(prev?.critical_share);
    return (
        <dl className="grid grid-cols-2 gap-px bg-slate-100 sm:grid-cols-4">
            <DayStat label="Оценено" value={evaluated}
                     sub={totals.operators ? `${peopleText(totals.operators)} · ${daysText(totals.days || 0)}` : null}
                     subTone="text-slate-400" />
            <DayStat label="Средний балл ИИ" value={ai} tone={ai != null ? scoreTone(ai) : null}
                     sub={prevAi != null ? `прошлый период ${prevAi}` : null}
                     subTone={prevAi != null && ai != null && ai < prevAi - 4 ? 'font-medium text-amber-600' : 'text-slate-400'} />
            <DayStat label="Критических" value={totals.critical ?? 0} tone={totals.critical ? 'red' : null}
                     sub={share != null ? (prevShare != null ? `${share}% · было ${prevShare}%` : `${share}% разговоров`) : null}
                     subTone="text-slate-400" />
            <DayStat label="Проверено людьми" value={`${reviewed} из ${evaluated}`}
                     sub={human != null ? `балл человека ${human}` : null} subTone="text-slate-400">
                <ShareBar share={evaluated ? reviewed / evaluated : 0} />
            </DayStat>
        </dl>
    );
}

/* Спецификации графиков из цифр периода — чистая функция, её проверяет node-тест. */
export const chartSpec = (kind, summary) => {
    if (!summary) return null;
    const days = (summary.days || []).filter((d) => d.evaluated > 0);
    if (kind === 'score') {
        if (days.length < 2) return null;
        const series = [{ name: 'Балл ИИ', values: days.map((d) => d.ai_avg) }];
        if (days.some((d) => d.human_avg != null)) series.push({ name: 'Балл человека', values: days.map((d) => d.human_avg) });
        return { kind: 'line', title: '', categories: days.map((d) => shortDay(d.day)), series };
    }
    if (kind === 'critical') {
        if (days.length < 2) return null;
        return { kind: 'bar', title: '', categories: days.map((d) => shortDay(d.day)),
                 series: [{ name: 'Критических', values: days.map((d) => d.critical || 0) }] };
    }
    if (kind === 'criteria') {
        const rows = (summary.criteria || []).filter((c) => c.bad > 0 && c.applicable >= 3).slice(0, 10);
        if (!rows.length) return null;
        // Направление — только у одноимённых критериев разных шкал: иначе подпись
        // удваивается без нужды.
        const names = rows.reduce((acc, c) => acc.set(c.name, (acc.get(c.name) || 0) + 1), new Map());
        return { kind: 'hbar', title: '',
                 categories: rows.map((c) => (names.get(c.name) > 1 ? `${c.name} · ${c.direction}` : c.name)),
                 series: [{ name: 'Доля ошибок, %', values: rows.map((c) => pct(c.bad_share)) }] };
    }
    if (kind === 'people') {
        const rows = (summary.people || []).filter((p) => p.ai_avg != null)
            .sort((a, b) => a.ai_avg - b.ai_avg).slice(0, 20);
        if (rows.length < 2) return null;
        return { kind: 'hbar', title: '', categories: rows.map((p) => truncate(p.name, 30)),
                 series: [{ name: 'Средний балл ИИ', values: rows.map((p) => round(p.ai_avg)) }] };
    }
    return null;
};

const EMPTY_CHART = {
    score: 'Для ряда по дням нужно хотя бы два дня с оценками',
    critical: 'Для ряда по дням нужно хотя бы два дня с оценками',
    criteria: 'Ошибок по критериям в этом периоде не было',
    people: 'Для сравнения нужно хотя бы два сотрудника с оценками',
};

const CHART_HINT = 'Балл по дням — средний балл ИИ (и человека, где он есть) за каждый день периода. '
    + 'Критические — сколько разговоров с нарушением по критическому критерию в день. Критерии — доля ошибок '
    + '(«Неверно» и «Недочёт») из применимых разговоров, десять самых частых. Сотрудники — средний балл ИИ, от '
    + 'самого низкого, до двадцати человек. Значок таблицы рядом с графиком показывает те же числа списком.';

function PeriodCharts({ summary, chart, setChart }) {
    const spec = useMemo(() => chartSpec(chart, summary), [chart, summary]);
    const colorOf = chart === 'people' ? (value) => SCORE_COLOR[scoreTone(value)]
        : chart === 'critical' ? () => SCORE_COLOR.red : null;
    return (
        <div className="border-t border-slate-100 px-4 pb-4 pt-3 sm:px-5">
            <div className="flex items-center gap-2">
                <div className="-mx-1 min-w-0 overflow-x-auto px-1">
                    <IosSegmented value={chart} options={CHARTS} onChange={setChart} size="xs"
                                  ariaLabel="Какой график показать" className="min-w-max" />
                </div>
                <IosHint label="О графиках" text={CHART_HINT} align="right" />
            </div>
            <div className="mt-3">
                {spec ? (
                    <QaChart spec={spec} colorOf={colorOf} />
                ) : (
                    <p className="py-6 text-center text-[12.5px] text-slate-400">{EMPTY_CHART[chart]}</p>
                )}
            </div>
        </div>
    );
}

function Placeholder({ icon: Icon = ScrollText, tone = 'text-slate-300', title, text = null, children = null }) {
    return (
        <div className="flex flex-col items-center gap-2 px-6 py-12 text-center">
            <Icon size={26} className={tone} aria-hidden="true" />
            <p className="text-[14px] font-semibold text-slate-700">{title}</p>
            {text && <p className="max-w-md text-[12.5px] leading-relaxed text-slate-500">{text}</p>}
            {children && <div className="mt-2 flex flex-wrap justify-center gap-2">{children}</div>}
        </div>
    );
}

/* ── сводка за месяц ─────────────────────────────────────────────────────── */

function SectionStats({ stats }) {
    if (!stats) return null;
    const ai = round(stats.ai_avg);
    const prev = round(stats.previous?.ai_avg);
    const human = round(stats.human_avg);
    const evaluated = stats.evaluated || 0;
    const reviewed = stats.reviewed || 0;
    return (
        <dl className="grid grid-cols-2 gap-px border-b border-slate-100 bg-slate-100 sm:grid-cols-4">
            <DayStat label="Оценено" value={evaluated} sub={stats.operators ? peopleText(stats.operators) : null} subTone="text-slate-400" />
            <DayStat label="Средний балл ИИ" value={ai} tone={ai != null ? scoreTone(ai) : null}
                     sub={prev != null ? `прошлый месяц ${prev}` : null}
                     subTone={prev != null && ai != null && ai < prev - 4 ? 'font-medium text-amber-600' : 'text-slate-400'} />
            <DayStat label="Критических" value={stats.critical ?? 0} tone={stats.critical ? 'red' : null}
                     sub={stats.below_60 ? `ниже 60 баллов — ${stats.below_60}` : 'ниже 60 баллов нет'} subTone="text-slate-400" />
            <DayStat label="Проверено людьми" value={`${reviewed} из ${evaluated}`}
                     sub={human != null ? `балл человека ${human}` : null} subTone="text-slate-400">
                <ShareBar share={evaluated ? reviewed / evaluated : 0} />
            </DayStat>
        </dl>
    );
}

function MonthSummary({ period, data, title, onOpenRef }) {
    const month = data.month;
    if (!month) return null;
    const sections = month.sections || [];
    const hasText = Boolean(month.overview_html) || sections.some((s) => s.html);
    const running = Boolean(month.running);
    const canGenerate = month.can_generate !== false;
    const options = [
        ...(month.overview_html ? [{ value: 'overview', label: 'Главное' }] : []),
        ...sections.map((s) => ({
            value: s.key, label: s.direction,
            icon: s.family === 'chats'
                ? <MessageSquare size={13} className="text-slate-400" aria-hidden="true" />
                : <PhoneCall size={13} className="text-slate-400" aria-hidden="true" />,
        })),
    ];
    const selected = options.some((o) => o.value === period.section) ? period.section : options[0]?.value;
    const section = sections.find((s) => s.key === selected);
    const heading = (
        <div className="flex items-center gap-2 px-1">
            <h3 className="text-[18px] font-semibold text-slate-900">Сводка за месяц</h3>
            <IosHint label="О сводке за месяц"
                     text="ИИ пишет её, когда месяц прошёл: по цифрам месяца и прошлого месяца, критическим случаям, замечаниям оценщика и сводкам дней — раздел на каждое направление и «Главное» по отделу. Цифры над текстом считает сервер. ИИ может ошибаться — сверяйтесь с разговорами по ссылкам." />
        </div>
    );
    if (!hasText) {
        return (
            <section className="space-y-2" aria-label="Сводка за месяц">
                {heading}
                <div className={`${iosCard} overflow-hidden`}>
                    {running ? (
                        <Placeholder icon={Loader2} tone="animate-spin text-blue-500" title="ИИ пишет сводку за месяц…"
                                     text="Читает цифры месяца, критические случаи и сводки дней. Обычно это несколько минут — страница обновится сама." />
                    ) : month.status === 'ready' && data.evaluated > 0 ? (
                        <Placeholder title="Сводка этого месяца — в общих разделах"
                                     text="Разговоры ваших направлений вошли в разделы вместе с другими направлениями. Такие разделы видит глава отдела." />
                    ) : month.status === 'failed' && month.error ? (
                        <Placeholder icon={AlertCircle} tone="text-rose-500" title="Сводку составить не удалось" text={month.error}>
                            {canGenerate && (
                                <button type="button" onClick={() => period.generateMonth(false)} disabled={period.generating}
                                        className={iosBtnPrimary}><RotateCcw size={15} />Повторить</button>
                            )}
                        </Placeholder>
                    ) : (
                        <Placeholder icon={Sparkles} tone="text-blue-500" title={`Сводки за ${title.toLowerCase()} ещё нет`}
                                     text={canGenerate
                                         ? 'ИИ пишет её в первые дни следующего месяца. Составить сейчас — несколько минут: что было с качеством, что изменилось, что повторяется, кому нужна помощь и что делать дальше.'
                                         : 'ИИ пишет её в первые дни следующего месяца.'}>
                            {canGenerate && (
                                <button type="button" onClick={() => period.generateMonth(false)} disabled={period.generating}
                                        className={iosBtnPrimary}>
                                    {period.generating ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}
                                    Составить сводку
                                </button>
                            )}
                        </Placeholder>
                    )}
                </div>
            </section>
        );
    }
    return (
        <section className="space-y-2" aria-label="Сводка за месяц">
            {heading}
            {options.length > 1 && (
                <div className="-mx-1 overflow-x-auto px-1 pb-0.5">
                    <IosSegmented value={selected} options={options} onChange={period.setSection}
                                  ariaLabel="Раздел сводки за месяц" className="min-w-max" />
                </div>
            )}
            <article className={`${iosCard} overflow-hidden`} aria-label={section ? `Сводка: ${section.direction}` : 'Сводка: главное'}>
                {section && <SectionStats stats={section.stats} />}
                <div className="px-5 py-5 sm:px-6">
                    {selected === 'overview' ? (
                        <AiMarkup html={month.overview_html} onOpenRef={onOpenRef} />
                    ) : section?.html ? (
                        <AiMarkup html={section.html} onOpenRef={onOpenRef} />
                    ) : (
                        <Placeholder icon={AlertCircle} tone="text-amber-500" title="ИИ не смог написать этот раздел"
                                     text={section?.error || 'Раздел допишется при следующем обновлении сводки.'}>
                            {canGenerate && (
                                <button type="button" onClick={() => period.generateMonth(false)} disabled={period.generating || running}
                                        className={iosBtnSecondary}><RotateCcw size={14} />Дописать раздел</button>
                            )}
                        </Placeholder>
                    )}
                </div>
                {month.generated_at && (
                    <div className="flex items-center gap-1.5 border-t border-slate-100 px-5 py-2.5 text-[11.5px] text-slate-400 sm:px-6">
                        <Sparkles size={12} aria-hidden="true" />
                        <span>Сводка ИИ · {fmtStamp(month.generated_at)}</span>
                        <IosHint label="Откуда сводка" align="left"
                                 text="Текст пишет модель по цифрам месяца, вердиктам оценщика, исправлениям людей и сводкам дней. Она может ошибаться: прежде чем делать выводы о сотруднике, откройте разговор по ссылке." />
                    </div>
                )}
            </article>
        </section>
    );
}

/* Состояние сводки месяца справа от заголовка: пишется / устарела / переписать. */
function MonthStatus({ period, data, trouble }) {
    const month = data?.month;
    if (!month) return null;
    const hasText = Boolean(month.overview_html) || (month.sections || []).some((s) => s.html);
    if (hasText && trouble) {
        return (
            <button type="button" onClick={period.reloadView}
                    className="inline-flex items-center gap-1.5 rounded-xl px-2.5 py-1.5 text-[13px] font-medium text-amber-700 transition hover:bg-amber-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500/60">
                <AlertCircle size={14} aria-hidden="true" />Нет связи — проверить
            </button>
        );
    }
    if (month.running) {
        return hasText ? (
            <span className="inline-flex items-center gap-1.5 text-[13px] font-medium text-blue-600" role="status">
                <Loader2 size={14} className="animate-spin" aria-hidden="true" />ИИ обновляет сводку…
            </span>
        ) : null;
    }
    const menu = month.can_force && hasText ? (
        <IosMenu label="Ещё" items={[{ key: 'force', label: 'Переписать заново', icon: RotateCcw,
                                         onSelect: () => period.generateMonth(true) }]} />
    ) : null;
    if (hasText && (month.stale || month.incomplete) && month.can_generate !== false) {
        return (
            <span className="inline-flex items-center gap-1">
                <button type="button" onClick={() => period.generateMonth(false)} disabled={period.generating}
                        className="inline-flex items-center gap-1.5 rounded-xl px-2.5 py-1.5 text-[13px] font-medium text-amber-700 transition hover:bg-amber-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500/60 disabled:opacity-60">
                    {period.generating ? <Loader2 size={14} className="animate-spin" /> : <RotateCcw size={14} />}
                    {month.stale ? 'Оценки изменились — обновить' : 'Сводка дописана не полностью — дописать'}
                </button>
                {menu}
            </span>
        );
    }
    return menu;
}

/* ── экран ───────────────────────────────────────────────────────────────── */

export default function PeriodView({ period, scoped = false, chatRestore = null, onOpenRef }) {
    const { range } = period;
    const data = period.view?.key === period.key ? period.view.data : null;
    const loading = period.view?.key === period.key && period.view.loading && !data;
    const error = period.view?.key === period.key ? period.view.error : '';
    const trouble = period.view?.key === period.key && Boolean(data) && (period.view.stalled || Boolean(error));
    const month = monthViewOf(range);
    const title = capital(periodTitle(range));
    const { pane, setPane } = period;
    const [chatRef, chatHeight] = useStickyHeight(true);
    const wide = useWide();
    const chatCount = (period.chat.messages || []).filter((m) => m.role === 'assistant').length;
    const pending = data?.pending?.length || 0;
    const empty = data && !data.evaluated && !pending;
    const kicker = month ? (data?.month ? 'Сводка за месяц' : 'Месяц') : `Период · ${daysText(spanDays(range))}`;

    return (
        <div style={{ fontFamily: APPLE_FONT }} className="space-y-4">
            <div className="flex items-center justify-between gap-2">
                <button type="button" onClick={period.close} className={`${iosBtnGhost} -ml-2`}>
                    <ChevronLeft size={16} aria-hidden="true" />Все дни
                </button>
                <div className="flex items-center gap-2">
                    {month && <MonthArrows month={month} onOpenMonth={period.openMonth} />}
                    <PeriodPicker range={range} onChange={period.open} />
                </div>
            </div>
            <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2 px-1">
                <div className="min-w-0">
                    <div className="text-[13px] font-medium text-slate-500">{kicker}</div>
                    <h2 id="qa-period-title" tabIndex={-1}
                        className="rounded-md text-[24px] font-semibold leading-tight text-slate-900 outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60">
                        {title}
                    </h2>
                </div>
                <MonthStatus period={period} data={data} trouble={trouble} />
            </div>

            <div className="xl:hidden">
                <IosSegmented value={pane} onChange={setPane} stretch ariaLabel="Цифры или чат"
                              options={[
                                  { value: 'summary', label: 'Период', icon: <ScrollText size={14} aria-hidden="true" /> },
                                  { value: 'chat', label: 'Спросить ИИ', icon: <Sparkles size={14} aria-hidden="true" />, count: chatCount },
                              ]} />
            </div>

            <div className="xl:grid xl:grid-cols-[minmax(0,1fr)_380px] xl:items-start xl:gap-4">
                <div data-qa-pane="summary" className={`space-y-4 ${pane === 'chat' ? 'hidden xl:block' : ''}`}>
                    {loading ? (
                        <div className={`${iosCard} flex items-center justify-center gap-2 px-6 py-16 text-[13px] text-slate-500`} role="status">
                            <Loader2 size={18} className="animate-spin" aria-hidden="true" />Собираю период…
                        </div>
                    ) : error && !data ? (
                        <div className={iosCard}>
                            <Placeholder icon={AlertCircle} tone="text-rose-500" title={error}>
                                <button type="button" onClick={period.reloadView} className={iosBtnSecondary}>
                                    <RotateCcw size={14} />Повторить
                                </button>
                            </Placeholder>
                        </div>
                    ) : data ? (
                        <>
                            {pending > 0 && (
                                <div className="flex items-center gap-2 px-1 text-[13px] text-blue-600" role="status">
                                    <Loader2 size={14} className="animate-spin" aria-hidden="true" />
                                    <span>ИИ готовит данные за период: осталось {daysText(pending)}</span>
                                    <IosHint label="Почему не сразу"
                                             text="Цифры периода собираются из слепков дней. Дни, по которым слепка ещё нет (старые или с новыми оценками), сервер досчитывает в фоне — цифры ниже пока неполные, страница обновится сама." />
                                </div>
                            )}
                            {empty ? (
                                <div className={iosCard}>
                                    <Placeholder title="За этот период нет оценённых разговоров"
                                                 text="Цифры собираются по оценкам ИИ — оценённых разговоров с датами этого периода нет." />
                                </div>
                            ) : (
                                <section className={`${iosCard} overflow-hidden`} aria-label="Цифры периода">
                                    <PeriodStats summary={data.summary} previous={data.previous} />
                                    <PeriodCharts summary={data.summary} chart={period.chart} setChart={period.setChart} />
                                </section>
                            )}
                            {!empty && <MonthSummary period={period} data={data} title={title} onOpenRef={onOpenRef} />}
                        </>
                    ) : null}
                </div>
                <div ref={chatRef} data-qa-pane="chat" className={`${pane === 'summary' ? 'hidden xl:block' : ''} xl:sticky xl:top-4`}>
                    <AiChatPanel
                        title="Спросить ИИ" subtitle={`о периоде · ${title.toLowerCase()}${scoped ? ' · ваши направления' : ''}`}
                        intro="ИИ видит цифры периода по дням, направлениям, критериям и сотрудникам, критические случаи с цитатами, замечания оценщика и сводки дней. Разговоры открываются по ссылкам в ответе; попросите график — он его построит."
                        suggestions={SUGGESTIONS} thread={period.chat} asking={period.asking}
                        draft={period.draft} setDraft={period.setDraft} draftFocus={period.draftFocus}
                        onAsk={period.ask} onClear={period.clearChat} onSuggest={period.suggest} onOpenRef={onOpenRef}
                        visible={wide || pane === 'chat'} restore={chatRestore}
                        placeholder="Спросите о периоде…" busyText="Собираю данные за период…"
                        className="h-[min(72vh,720px)] xl:h-auto"
                        style={chatHeight ? { height: chatHeight } : undefined} />
                </div>
            </div>
        </div>
    );
}
