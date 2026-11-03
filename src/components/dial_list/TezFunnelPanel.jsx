import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, TriangleAlert } from 'lucide-react';
import { iosCard, iosGroupLabel, IosHint } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { IosDateRangePicker, isoDate } from '../ui/DateRangePicker';
import { Bar, Metric, PagedTable, Td, Th } from '../wiki/reportKit';
import {
    batchOptions, cityLabel, cityOptions, funnelConversions, funnelQuery, funnelSteps, reasonLabel,
    refusalSetupNotice,
} from './tezFunnel';

/*
 * «Воронка ОП» — первый разрез вкладки «Аналитика» раздела «Воронка ОП Tez» (задача #407 Алчинбаевой
 * Анель, 03.11.2026; владелец: «эту воронку можно положить в Аналитику»).
 *
 * Фильтры — база (месяц выбирается над разрезами, файл загрузки — здесь), период звонков и город;
 * город и период действуют и на воронку, и на отчёт о причинах отказа. Плитки — пять шагов и две
 * конверсии, под ними — по городам и причины отказа. Шаги с регистрацией ждут поле от TEZ APP:
 * пока его нет, плитка пишет «ждём данные TEZ APP», а в разбивке по городам этих колонок нет —
 * колонка прочерков была бы только шумом.
 *
 * Считает сервер (GET …/funnel, dial_list/analytics.tez_funnel); подписи и запрос — tezFunnel.js.
 * Ответ, пришедший после смены фильтров, не ложится на новые: у запроса свой счётчик.
 */

const FIELD_HEAD = `flex h-5 items-center ${iosGroupLabel}`;
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

const TezFunnelPanel = ({ apiBaseUrl, authHeaders, departmentId, period = '', reloadKey = 0 }) => {
    // Файлы и города у каждого месяца базы свои: выбранный в прошлом месяце здесь ничего бы не нашёл.
    // Выбор помнит свой месяц — другой месяц сразу начинается с «все», без запроса со старым выбором.
    const scope = `${departmentId}|${period}`;
    const [picked, setPicked] = useState({ scope: '', batchId: '', city: '' });
    const batchId = picked.scope === scope ? picked.batchId : '';
    const city = picked.scope === scope ? picked.city : '';
    const pick = (patch) => setPicked({ scope, batchId, city, ...patch });
    const [range, setRange] = useState({ from: '', to: '' });
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const seq = useRef(0);

    const load = useCallback(async () => {
        const my = ++seq.current;
        setLoading(true);
        setError('');
        try {
            const qs = funnelQuery({ period, batchId, dateFrom: range.from, dateTo: range.to, city });
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
    }, [apiBaseUrl, authHeaders, departmentId, period, batchId, range.from, range.to, city]);

    useEffect(() => { load(); }, [load, reloadKey]);

    const steps = useMemo(() => funnelSteps(data), [data]);
    const conversions = useMemo(() => funnelConversions(data), [data]);
    const cities = useMemo(() => cityOptions(data), [data]);
    const batches = useMemo(() => batchOptions(data), [data]);
    const byCity = Array.isArray(data?.by_city) ? data.by_city : [];
    const reasons = Array.isArray(data?.refusals?.reasons) ? data.refusals.reasons : [];
    const refusalsTotal = Number(data?.refusals?.total) || 0;
    const setupNotice = refusalSetupNotice(data);

    return (
        <div className="space-y-4">
            {/* Фильтры воронки: файл базы, дни звонков, город. Подпись каждого поля — блок одной высоты. */}
            <div className={`${iosCard} grid gap-3 p-3.5 sm:grid-cols-3`}>
                <label className="block space-y-1.5">
                    <span className={FIELD_HEAD}>Файл базы</span>
                    <CustomSelect value={batchId} onChange={(v) => pick({ batchId: String(v) })} options={batches} variant="ios" ariaLabel="Файл базы" />
                </label>
                {/* div, а не label: календарь раскрывается внутри, и label отдавал бы щелчок кнопке. */}
                <div className="space-y-1.5">
                    <span className={`${FIELD_HEAD} gap-1.5`}>
                        Период звонков
                        <IosHint text="Дни, когда состоялся диалог (по Алматы). Вышли к заказам — после первого диалога водителя в эти дни. Причины отказа — по отказам этих дней." label="Что значит «Период звонков»" />
                    </span>
                    <IosDateRangePicker
                        from={range.from}
                        to={range.to}
                        max={isoDate(new Date())}
                        presets={DATE_PRESETS}
                        triggerClassName={DATE_TRIGGER}
                        onChange={(next) => setRange({ from: next?.from || '', to: next?.to || '' })}
                    />
                </div>
                <label className="block space-y-1.5">
                    <span className={FIELD_HEAD}>Город</span>
                    <CustomSelect value={city} onChange={(v) => pick({ city: String(v) })} options={cities} variant="ios" searchable={cities.length > 9} ariaLabel="Город" />
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
                <div className={`space-y-4 transition-opacity ${loading ? 'opacity-60' : ''}`}>
                    <section className="space-y-1.5">
                        <div className="flex items-center gap-1.5">
                            <span className={iosGroupLabel}>Воронка</span>
                            <IosHint text="Каждый водитель учитывается один раз в каждом шаге. Регистрации и поездки до контакта с оператором в результат после контакта не идут." />
                        </div>
                        <div className={`${iosCard} space-y-2 p-2`}>
                            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
                                {steps.map((s, i) => (
                                    <Metric key={s.key} label={s.label} value={s.value} hint={s.hint} help={s.help}
                                        tone={s.tone || null} helpAlign={i >= steps.length - 2 ? 'right' : 'left'} />
                                ))}
                            </div>
                            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                                {conversions.map((c, i) => (
                                    <Metric key={c.key} label={c.label} value={c.value} hint={c.hint} help={c.help}
                                        helpAlign={i === conversions.length - 1 ? 'right' : 'left'} />
                                ))}
                            </div>
                        </div>
                    </section>

                    <PagedTable
                        title="По городам"
                        help="Город — из колонки «город» в файле базы (вкладка «База водителей»); водители без города — строкой «Город не указан». Колонки регистрации появятся, когда TEZ APP начнёт отдавать дату регистрации."
                        rows={byCity}
                        perPage={10}
                        empty="В выбранной базе водителей нет."
                        head={(
                            <tr>
                                <Th>Город</Th>
                                <Th right>База</Th>
                                <Th right>Диалог</Th>
                                <Th right>Вышли к заказам</Th>
                                <Th right>Диалог → заказы</Th>
                            </tr>
                        )}
                        renderRow={(r) => (
                            <tr key={r.key}>
                                <Td><span className={`whitespace-nowrap font-medium ${r.city ? 'text-slate-800' : 'text-slate-500'}`}>{cityLabel(r.city)}</span></Td>
                                <Td right>{num(r.base)}</Td>
                                <Td right>{num(r.dialog)}</Td>
                                <Td right><span className="font-semibold text-slate-900">{num(r.orders_after)}</span></Td>
                                <Td right>{pctText(r.dialog_to_orders)}</Td>
                            </tr>
                        )}
                    />

                    <div className="space-y-2">
                        {setupNotice && <Notice>{setupNotice}</Notice>}
                        <PagedTable
                            title="Причины отказа"
                            help="Отказ — итог «Отказ», который оператор ставит в карточке водителя в iCORE Phone; причина — его тип, при необходимости с комментарием. Каждый водитель считается один раз — с последней причиной отказа в выбранный период. Доля — от всех отказов."
                            rows={reasons}
                            perPage={10}
                            empty="Отказов за выбранный период нет."
                            head={(
                                <tr>
                                    <Th>Причина</Th>
                                    <Th right>Отказов</Th>
                                    <Th right>Доля</Th>
                                </tr>
                            )}
                            renderRow={(r) => (
                                <tr key={r.subtype_id || 'none'}>
                                    <Td><span className={`font-medium ${r.subtype_id ? 'text-slate-800' : 'text-slate-500'}`}>{reasonLabel(r)}</span></Td>
                                    <Td right><span className="font-semibold text-slate-900">{num(r.count)}</span></Td>
                                    {/* Полоса нейтральная: вся таблица про отказы, красный здесь был бы шумом. */}
                                    <Td right><Bar done={r.count} total={refusalsTotal} /></Td>
                                </tr>
                            )}
                        />
                    </div>
                </div>
            )}
        </div>
    );
};

export default TezFunnelPanel;
