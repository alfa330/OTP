import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Loader2, RefreshCw, TriangleAlert } from 'lucide-react';
import { iosBtnGhost, iosCard, iosGroupLabel, IosHint } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { IosDateRangePicker, isoDate } from '../ui/DateRangePicker';
import { Metric, PagedTable, Td, Th } from '../wiki/reportKit';
import {
    OUTCOMES_PENDING_TEXT, agreementSetupNotice, batchOptions, cityLabel, cityOptions, funnelConversions, funnelQuery,
    funnelSteps, monthOptions, outcomeRows, outcomeTiles, outcomesReady, outcomesTotal, periodHint, reasonLabel,
    filtersInline, refusalSetupNotice, reportLayout, reportShowsBars, splitInTwo, stepTileClass, uncheckedNotice,
} from './tezFunnel';

/*
 * «Воронка ОП» — первый разрез вкладки «Аналитика» раздела «Воронка ОП Tez» (задача #407 Алчинбаевой
 * Анель, 03.11.2026; владелец: «эту воронку можно положить в Аналитику»).
 *
 * Считается по базе успешек — той же, что во вкладке «Успешки» (владелец 06.11.2026). Поэтому месяц
 * базы выбирается здесь, из месяцев этой базы (по умолчанию — текущий, как в «Успешках»), а не над
 * разрезами: там месяцы базы автообзвона. Фильтры — месяц, файл загрузки, дни звонков и город; дни и
 * город действуют на всё ниже. Если TEZ APP ещё не ответил по части водителей, над плитками —
 * предупреждение: их регистрации и поездки пока не учтены.
 *
 * Раскладка (27.11.2026 — Анель: «структурируй отображение, а то заходишь и не сразу ориентируешься»,
 * «муравьиный шрифт не отправлять», «хочу просто скриншотить и кидать руководству»): сверху пять шагов
 * воронки, под ними главные показатели — согласились, отказались и две конверсии; ниже рядом «Итоги
 * звонков» (все итоги настроек — владелец: «всё, что в настройках итогов звонка добавлено, всё должно
 * быть выведено») и «Причины отказа» — все строки сразу, без листания, чтобы влезли в один снимок; «По
 * городам» — в самом низу. Крупно — только от ширины xl (size="lg" у кирпичей reportKit): телефон и
 * узкое окно как были.
 *
 * Считает сервер (GET …/funnel, dial_list/analytics.tez_funnel); подписи и запрос — tezFunnel.js.
 * Ответ, пришедший после смены фильтров, не ложится на новые: у запроса свой счётчик.
 */

// Кирпичи отчёта — крупного вида (на большом экране).
const SIZE = 'lg';
const GROUP_LABEL = `${iosGroupLabel} xl:text-[13px]`;

/* Блок отчёта: подпись с «i» и карточка во всю высоту ячейки сетки — у карточек одного ряда одна высота, а
   строка «Всего» (mt-auto) прижата к низу: «Всего с итогом» и «Всего отказов» стоят на одном уровне. Без числа
   строк в подписи: у итогов это число итогов в настройках, рядом с отчётом о водителях оно читалось бы как
   «водителей 7». */
const ReportBlock = ({ title, help, className = '', children }) => (
    <section className={`flex min-w-0 flex-col gap-1.5 ${className}`}>
        <div className="flex items-center gap-1.5 pr-1">
            <span className={GROUP_LABEL}>{title}</span>
            {help && <IosHint text={help} label={`Как считается «${title}»`} />}
        </div>
        <div className={`${iosCard} flex flex-1 flex-col overflow-hidden`}>{children}</div>
    </section>
);

/* Строка отчёта — имя | число | доля, одна раскладка на шапку, строки и «Всего». Flex, а не grid-cols-[…]: оболочка
   телефона (mobile-shell.css) схлопывает такие сетки в одну колонку, и имя, число и доля вставали друг под другом.
   Имя — min-w-0 grow basis-0 (класс flex-1 оболочка ловит своим :has), число и доля — фиксированной ширины:
   столбцы ровные, полосы стоят одним столбиком. Имя переносится, а не режется: «Не устраивает прило…» на снимке
   для руководства не прочесть. bars — есть ли полоса (в узкой колонке её нет, процент остаётся). */
const ROW_LINE = 'flex items-center gap-3 px-4';
// Невысокое окно (до 920 px, ноутбук 1440×900): строки тоньше, шрифт тот же — отчёт влезает в один экран. Классы
// целиком, без склейки: Tailwind находит их в исходнике только так.
const ROW = `${ROW_LINE} py-1.5 text-[13px] xl:py-[5px] xl:text-[15px] xl:[@media(max-height:920px)]:py-[3px]`;
const HEAD = `${ROW_LINE} border-b border-slate-100 py-2 text-[11px] font-medium uppercase tracking-wide text-slate-500 `
    + 'xl:py-[7px] xl:text-[12px] xl:[@media(max-height:920px)]:py-[5px]';
const NAME = 'min-w-0 grow basis-0 break-words';
const COUNT = 'w-12 shrink-0 whitespace-nowrap text-right tabular-nums xl:w-14';
const shareWidth = (bars) => (bars ? 'w-[6.25rem] shrink-0 xl:w-[7.5rem]' : 'w-10 shrink-0 xl:w-12');

const ShareCell = ({ part, whole, bars }) => {
    const pct = whole ? Math.round((100 * part) / whole) : 0;
    return (
        <span className={`flex items-center justify-end gap-2 ${shareWidth(bars)}`}>
            {/* Полоса нейтральная: цвет здесь был бы шумом (у отказов — вся таблица про отказы). */}
            {bars && (
                <span className="h-1.5 w-12 shrink-0 overflow-hidden rounded-full bg-slate-100 xl:h-2 xl:w-16">
                    <span className="block h-full rounded-full bg-indigo-400" style={{ width: `${pct}%` }} />
                </span>
            )}
            <span className="w-10 shrink-0 text-right tabular-nums text-slate-700 xl:w-12">{pct}%</span>
        </span>
    );
};

const ReportHead = ({ name, count, bars }) => (
    <div className={HEAD}>
        <span className={NAME}>{name}</span>
        <span className="shrink-0 whitespace-nowrap text-right">{count}</span>
        <span className={`text-right ${shareWidth(bars)}`}>Доля</span>
    </div>
);

/* «Всего» — знаменатель долей: на снимке для руководства он нужен рядом с ними. */
const TotalRow = ({ label, count, bars }) => (
    <div className={`${ROW} font-semibold text-slate-900`}>
        <span className={NAME}>{label}</span>
        <span className={COUNT}>{num(count)}</span>
        <span className={shareWidth(bars)} />
    </div>
);

const ReasonRow = ({ reason, whole, bars }) => (
    <div className={ROW}>
        <span className={`${NAME} font-medium ${reason.subtype_id ? 'text-slate-800' : 'text-slate-500'}`}>{reasonLabel(reason)}</span>
        <span className={`${COUNT} font-semibold text-slate-900`}>{num(reason.count)}</span>
        <ShareCell part={reason.count} whole={whole} bars={bars} />
    </div>
);

// Крупный шрифт отчёта — от окна 1280 (xl): по нему порог ширины колонки для полосы доли.
const BIG_SCREEN_QUERY = '(min-width: 1280px)';
const useBigScreen = () => {
    const [big, setBig] = useState(() => typeof window !== 'undefined' && !!window.matchMedia?.(BIG_SCREEN_QUERY).matches);
    useEffect(() => {
        const mq = typeof window !== 'undefined' ? window.matchMedia?.(BIG_SCREEN_QUERY) : null;
        if (!mq) return undefined;
        const sync = () => setBig(mq.matches);
        sync();
        mq.addEventListener?.('change', sync);
        return () => mq.removeEventListener?.('change', sync);
    }, []);
    return big;
};

// Ширина блока — от неё раскладка (reportLayout): callback-ref, потому что блок появляется только с данными.
const useBlockWidth = () => {
    const [node, setNode] = useState(null);
    const [width, setWidth] = useState(0);
    useLayoutEffect(() => {
        if (!node) return undefined;
        const measure = () => setWidth(Math.round(node.getBoundingClientRect().width));
        measure();
        if (typeof ResizeObserver === 'undefined') return undefined;
        const observer = new ResizeObserver(measure);
        observer.observe(node);
        return () => observer.disconnect();
    }, [node]);
    return [setNode, width];
};

const FIELD_HEAD = `flex h-5 shrink-0 items-center ${iosGroupLabel}`;
const DATE_TRIGGER = 'flex w-full items-center gap-2 rounded-xl bg-white px-3 py-2 '
    + 'text-left text-[12.5px] font-medium text-slate-700 ring-1 ring-slate-200/70 '
    + 'shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition-all hover:bg-slate-50 '
    + 'active:scale-[0.99] focus:outline-none focus:ring-2 focus:ring-blue-500/60 '
    + '[&>span]:flex-1 [&>span]:text-left [&>span]:truncate';

const shiftDays = (n) => {
    const d = new Date();
    d.setDate(d.getDate() - n);
    return isoDate(d);
};

const DATE_PRESETS = [
    { label: 'Сегодня', range: () => ({ from: isoDate(new Date()), to: isoDate(new Date()) }) },
    { label: '7 дней', range: () => ({ from: shiftDays(6), to: isoDate(new Date()) }) },
    { label: 'Весь период', range: () => ({ from: '', to: '' }) },
];

const NF = new Intl.NumberFormat('ru-RU');
const num = (v) => NF.format(Number(v) || 0);
const pctText = (v) => `${Number(v || 0).toLocaleString('ru-RU', { maximumFractionDigits: 1 })}%`;

const Notice = ({ children }) => (
    <div className="flex items-start gap-2 rounded-xl bg-amber-50 px-3.5 py-2.5 text-[12.5px] text-amber-800 ring-1 ring-amber-100">
        <TriangleAlert size={14} className="mt-[1px] shrink-0" />
        <span>{children}</span>
    </div>
);

const TezFunnelPanel = ({ apiBaseUrl, authHeaders, departmentId }) => {
    // Месяц базы: '' — какой выберет сервер (текущий).
    const [month, setMonth] = useState('');
    // Файлы и города у каждого месяца базы свои: выбранный в прошлом месяце здесь ничего бы не нашёл.
    // Выбор помнит свой месяц — другой месяц сразу начинается с «все», без запроса со старым выбором.
    const scope = `${departmentId}|${month}`;
    const [picked, setPicked] = useState({ scope: '', batchId: '', city: '' });
    const batchId = picked.scope === scope ? picked.batchId : '';
    const city = picked.scope === scope ? picked.city : '';
    const pick = (patch) => setPicked({ scope, batchId, city, ...patch });
    const [range, setRange] = useState({ from: '', to: '' });
    const [reload, setReload] = useState(0);
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const seq = useRef(0);

    const load = useCallback(async () => {
        const my = ++seq.current;
        setLoading(true);
        setError('');
        try {
            const qs = funnelQuery({ period: month, batchId, dateFrom: range.from, dateTo: range.to, city });
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/funnel${qs}`, {
                credentials: 'include', headers: authHeaders(),
            });
            const body = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(body?.error || `HTTP ${resp.status}`);
            if (my === seq.current) setData(body);
        } catch (e) {
            if (my === seq.current) setError(e.message || 'Не удалось посчитать воронку');
        } finally {
            if (my === seq.current) setLoading(false);
        }
    }, [apiBaseUrl, authHeaders, departmentId, month, batchId, range.from, range.to, city]);

    useEffect(() => { load(); }, [load, reload]);

    const months = useMemo(() => monthOptions(data), [data]);
    const steps = useMemo(() => funnelSteps(data), [data]);
    const cities = useMemo(() => cityOptions(data), [data]);
    const batches = useMemo(() => batchOptions(data), [data]);
    const results = useMemo(() => [...outcomeTiles(data), ...funnelConversions(data)], [data]);
    // Ширина — корня раздела: он есть с первого кадра, и раскладка не перескакивает, когда приходят данные.
    const [panelRef, panelWidth] = useBlockWidth();
    const bigScreen = useBigScreen();
    const layout = reportLayout(panelWidth);
    const bars = reportShowsBars(panelWidth, bigScreen);
    const inline = filtersInline(panelWidth, bigScreen);
    const field = inline ? 'flex items-center gap-2.5' : 'space-y-1.5';
    const grow = inline ? 'min-w-0 flex-1' : 'min-w-0';
    const outcomes = useMemo(() => outcomeRows(data), [data]);
    const outcomesWhole = outcomesTotal(data);
    const byCity = Array.isArray(data?.by_city) ? data.by_city : [];
    const reasons = Array.isArray(data?.refusals?.reasons) ? data.refusals.reasons : [];
    const refusalsTotal = Number(data?.refusals?.total) || 0;
    // Причины — двумя колонками, кроме телефона; одна причина — одной колонкой (вторая была бы пустой шапкой).
    const reasonColumns = layout !== 'single' && reasons.length > 1 ? splitInTwo(reasons) : [reasons];
    const setupNotice = refusalSetupNotice(data);
    const agreementNotice = agreementSetupNotice(data);
    const tezNotice = uncheckedNotice(data);

    return (
        <div ref={panelRef} className="space-y-4">
            {/* Фильтры воронки: месяц и файл базы, дни звонков, город. Подпись каждого поля — блок одной высоты.
                Широкий раздел на крупном шрифте — одной строкой, подпись слева от поля (filtersInline): так отчёт
                влезает в один экран; уже — подпись над полем, иначе значения обрезались бы. */}
            <div className={`${iosCard} grid gap-3 sm:grid-cols-2 lg:grid-cols-4 ${inline ? 'gap-4 p-2.5' : 'p-3.5'}`}>
                {/* div, а не label: рядом с выбором — кнопка «Обновить», label отдавал бы щелчок ей. */}
                <div className={field}>
                    <span className={FIELD_HEAD}>Месяц базы</span>
                    <div className={`flex min-w-0 items-center gap-2 ${inline ? 'flex-1' : ''}`}>
                        <CustomSelect
                            value={month || data?.period || ''}
                            onChange={(v) => setMonth(String(v))}
                            options={months}
                            variant="ios"
                            className="min-w-0 flex-1"
                            ariaLabel="Месяц базы"
                        />
                        <button
                            type="button"
                            onClick={() => setReload((n) => n + 1)}
                            disabled={loading}
                            className={`${iosBtnGhost} shrink-0`}
                            aria-label="Обновить"
                            title="Обновить"
                        >
                            <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
                        </button>
                    </div>
                </div>
                <label className={`block ${field}`}>
                    <span className={FIELD_HEAD}>Файл базы</span>
                    <CustomSelect value={batchId} onChange={(v) => pick({ batchId: String(v) })} options={batches} variant="ios" className={grow} ariaLabel="Файл базы" />
                </label>
                {/* div, а не label: календарь раскрывается внутри, и label отдавал бы щелчок кнопке. */}
                <div className={field}>
                    {/* «i» — рядом с подписью, а не внутри: внутри пузырёк наследовал её прописные буквы. */}
                    <div className="flex h-5 shrink-0 items-center gap-1.5">
                        <span className={iosGroupLabel}>Период звонков</span>
                        <IosHint text={periodHint(data)} label="Что значит «Период звонков»" />
                    </div>
                    <div className={grow}>
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
                <label className={`block ${field}`}>
                    <span className={FIELD_HEAD}>Город</span>
                    <CustomSelect value={city} onChange={(v) => pick({ city: String(v) })} options={cities} variant="ios" searchable={cities.length > 9} className={grow} ariaLabel="Город" />
                </label>
            </div>

            {error && !data && <div className="rounded-xl bg-rose-50 px-4 py-3 text-[13px] text-rose-700">{error}</div>}
            {error && data && <div className="rounded-xl bg-rose-50 px-3.5 py-2 text-[12.5px] text-rose-700">Не удалось обновить: {error}</div>}

            {!data && loading && (
                <div className={`${iosCard} flex items-center justify-center gap-2 py-12 text-[13px] text-slate-500`}>
                    <Loader2 size={15} className="animate-spin" /> Считаем воронку…
                </div>
            )}

            {data && (
                <div className={`space-y-4 transition-opacity xl:space-y-3.5 xl:[@media(max-height:920px)]:space-y-2.5 ${loading ? 'opacity-60' : ''}`}>
                    <section className="space-y-1.5">
                        <div className="flex items-center gap-1.5">
                            <span className={GROUP_LABEL}>Воронка</span>
                            <IosHint text="Каждый водитель учитывается один раз в каждом шаге. Регистрации и поездки до контакта с оператором в результат после контакта не идут. Даты регистрации и поездок — из TEZ APP. Согласились и отказались — по последнему итогу водителя в выбранные дни." />
                        </div>
                        {tezNotice && <Notice>{tezNotice}</Notice>}
                        <div className={`${iosCard} space-y-2 p-2 xl:space-y-2.5`}>
                            {/* Пять шагов: края рядов совпадают на любой ширине — телефон 2+2+1, планшет 3+2,
                                компьютер в ряд (stepTileClass). Плитка — во всю высоту ячейки: ряд ровный. */}
                            <div className="grid grid-cols-2 gap-2 sm:grid-cols-6 lg:grid-cols-5 xl:gap-2.5">
                                {steps.map((s, i) => (
                                    <div key={s.key} className={`${stepTileClass(i, steps.length)} [&>*]:h-full`}>
                                        <Metric label={s.label} value={s.value} hint={s.hint} help={s.help} size={SIZE}
                                            tone={s.tone || null} helpAlign={i >= steps.length - 2 ? 'right' : 'left'} />
                                    </div>
                                ))}
                            </div>
                            {/* Главные показатели: согласия и отказы (Анель: «как основные показатели отдельно») и
                                конверсии — один ряд на всю ширину карточки, края совпадают с рядом шагов. */}
                            <div className="grid grid-cols-2 gap-2 lg:grid-cols-4 xl:gap-2.5">
                                {results.map((c, i) => (
                                    <Metric key={c.key} label={c.label} value={c.value} hint={c.hint} help={c.help} size={SIZE}
                                        helpAlign={i >= results.length - 2 ? 'right' : 'left'} />
                                ))}
                            </div>
                        </div>
                    </section>

                    {/* Предупреждения — над обоими отчётами, а не над одним: иначе карточки ряда съехали бы. */}
                    {(agreementNotice || setupNotice) && (
                        <div className="space-y-2">
                            {agreementNotice && <Notice>{agreementNotice}</Notice>}
                            {setupNotice && <Notice>{setupNotice}</Notice>}
                        </div>
                    )}

                    {/* Раскладка — по ширине самого блока (reportLayout): три равные колонки, друг под другом или
                        телефон. Карточки ряда одной высоты, «Всего» прижато к низу — на одном уровне. */}
                    <div className={layout === 'three' ? 'grid grid-cols-3 gap-4' : 'grid grid-cols-1 gap-4'}>
                        <ReportBlock
                            title="Итоги звонков"
                            className={layout === 'three' ? 'col-span-1' : ''}
                            help="Последний итог водителя в выбранные дни — тот, что оператор ставит в карточке водителя в iCORE Phone при обзвоне через раздел. Звонки без итога (недозвоны) и звонки Binotel мимо обзвона его не меняют. Каждый водитель — один раз. Показаны все итоги из «Настройки» → «Итоги», и с нулём, — по убыванию числа водителей (при равенстве — в порядке настроек); выключенный итог — только если по нему есть водители. Доля — от всех водителей с итогом."
                        >
                            {!outcomesReady(data) ? (
                                <p className="px-4 py-3 text-[12.5px] text-slate-500 xl:text-[15px]">{OUTCOMES_PENDING_TEXT}</p>
                            ) : (
                                <>
                                    <ReportHead name="Итог" count="Водителей" bars={bars} />
                                    <div className="divide-y divide-slate-50">
                                        {outcomes.map((r) => (
                                            <React.Fragment key={r.key}>
                                                <div className={ROW}>
                                                    <span className={`${NAME} flex items-center gap-2.5`}>
                                                        <span className="h-2.5 w-2.5 shrink-0 rounded-[3px] xl:h-3 xl:w-3" style={{ backgroundColor: r.color }} />
                                                        <span className={`${NAME} font-medium ${r.inactive ? 'text-slate-500' : 'text-slate-800'}`}>{r.name}</span>
                                                        {r.inactive && <span className="shrink-0 text-[11px] text-slate-400 xl:text-[12.5px]">выключен</span>}
                                                    </span>
                                                    <span className={`${COUNT} ${r.count ? 'font-semibold text-slate-900' : 'text-slate-400'}`}>{num(r.count)}</span>
                                                    <ShareCell part={r.count} whole={outcomesWhole} bars={bars} />
                                                </div>
                                                {r.subtypes.map((s) => (
                                                    <div key={`${r.key}:${s.key}`} className={`${ROW} text-slate-500`}>
                                                        <span className={`${NAME} pl-5 xl:pl-[22px]`}>{s.name}</span>
                                                        <span className={COUNT}>{num(s.count)}</span>
                                                        <ShareCell part={s.count} whole={outcomesWhole} bars={bars} />
                                                    </div>
                                                ))}
                                            </React.Fragment>
                                        ))}
                                    </div>
                                    <div className="mt-auto border-t border-slate-100 bg-slate-50/70">
                                        <TotalRow label="Всего с итогом" count={outcomesWhole} bars={bars} />
                                    </div>
                                </>
                            )}
                        </ReportBlock>

                        <ReportBlock
                            title="Причины отказа"
                            className={layout === 'three' ? 'col-span-2' : ''}
                            help="Отказ — последний итог водителя в выбранные дни, если это «Отказ» (итог ставит оператор в карточке водителя в iCORE Phone при обзвоне через раздел); причина — его тип, при необходимости с комментарием. Каждый водитель — один раз. Сумма причин равна «Отказались» и строке «Отказ» в итогах звонков. Доля — от всех отказов."
                        >
                            {reasons.length === 0 ? (
                                <p className="px-4 py-3 text-[12.5px] text-slate-500 xl:text-[15px]">Отказов за выбранный период нет.</p>
                            ) : (
                                <>
                                    {/* Две колонки по порядку (сверху вниз, потом во второй) — по высоте вровень с итогами. */}
                                    <div className={`grid ${reasonColumns.length === 2 ? 'grid-cols-2 divide-x divide-slate-100' : 'grid-cols-1'}`}>
                                        {reasonColumns.map((column, c) => (
                                            <div key={c} className="min-w-0">
                                                <ReportHead name="Причина" count="Отказов" bars={bars} />
                                                <div className="divide-y divide-slate-50">
                                                    {column.map((r) => <ReasonRow key={r.subtype_id || 'none'} reason={r} whole={refusalsTotal} bars={bars} />)}
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                    <div className={`mt-auto grid border-t border-slate-100 bg-slate-50/70 ${reasonColumns.length === 2 ? 'grid-cols-2' : 'grid-cols-1'}`}>
                                        <TotalRow label="Всего отказов" count={refusalsTotal} bars={bars} />
                                    </div>
                                </>
                            )}
                        </ReportBlock>
                    </div>

                    <PagedTable
                        title="По городам"
                        help="Те же шаги и конверсии по городам. Город — из колонки «город» в файле базы (загрузка во вкладке «База водителей»); у баз, загруженных без этой колонки, — строка «Город не указан»."
                        rows={byCity}
                        perPage={10}
                        size={SIZE}
                        empty="В выбранной базе водителей нет."
                        head={(
                            <tr>
                                <Th size={SIZE}>Город</Th>
                                <Th size={SIZE} right>База</Th>
                                <Th size={SIZE} right>Уже зарегистрированы</Th>
                                <Th size={SIZE} right>Диалог</Th>
                                <Th size={SIZE} right>Регистрация после</Th>
                                <Th size={SIZE} right>Вышли к заказам</Th>
                                <Th size={SIZE} right>Диалог → регистрация</Th>
                                <Th size={SIZE} right>Диалог → заказы</Th>
                            </tr>
                        )}
                        renderRow={(r) => (
                            <tr key={r.key}>
                                <Td size={SIZE}><span className={`whitespace-nowrap font-medium ${r.city ? 'text-slate-800' : 'text-slate-500'}`}>{cityLabel(r.city)}</span></Td>
                                <Td size={SIZE} right>{num(r.base)}</Td>
                                <Td size={SIZE} right>{num(r.already_registered)}</Td>
                                <Td size={SIZE} right>{num(r.dialog)}</Td>
                                <Td size={SIZE} right>{num(r.registered_after)}</Td>
                                <Td size={SIZE} right><span className="font-semibold text-slate-900">{num(r.orders_after)}</span></Td>
                                <Td size={SIZE} right>{pctText(r.dialog_to_registration)}</Td>
                                <Td size={SIZE} right>{pctText(r.dialog_to_orders)}</Td>
                            </tr>
                        )}
                    />
                </div>
            )}
        </div>
    );
};

export default TezFunnelPanel;
