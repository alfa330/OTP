import React, { useMemo, useState } from 'react';
import {
    Bar,
    CartesianGrid,
    ComposedChart,
    LabelList,
    Legend,
    Line,
    ReferenceLine,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from 'recharts';
import FaIcon from '../common/FaIcon';
import { APPLE_FONT, iosCard } from '../ui/ios';
import { Grid, KeyTile, Section, SegmentedSwitch, StatTile } from './SzovWallboardTiles';
import { WALLBOARD_TONE_TEXT, chatReplyTone, formatInt, formatMinutes, pluralRu } from './szovWallboardShared';
import { OP_CHAT_METRIC_MAP, opMetricHint, readOpMetric } from './opWallboardShared';

/*
 * «Табло ОП» · «Чат» — чаты верификаторов в Wazzup (задача #367). Раскладка та же, что у «Чата»
 * табло СЗоВ, чтобы взгляд не переучивался: «сейчас» и итоги дня слева, график по часам, люди
 * справа. Отличие одно, и оно из источника: статусов у Wazzup нет, и на табло их нет вовсе
 * (решение владельца 25.09.2026) — «сейчас» здесь про чаты, а не про людей.
 *
 * Цвет — только у времени ответа и только относительно нормы из снимка (первый ответ — минута,
 * внутри чата — четыре, решение владельца). Остальные плитки нейтральные.
 */

const CHART_COLORS = {
    people: '#3b82f6',
    inner: '#e11d48',
    target: '#059669',
};

const hourLabel = (hour) => `${String(hour).padStart(2, '0')}–${String((hour + 1) % 24).padStart(2, '0')}`;

const chatsWord = (count) => pluralRu(count, 'чат', 'чата', 'чатов');

const NowTile = ({ metricKey, snapshot, scale }) => {
    const metric = OP_CHAT_METRIC_MAP[metricKey];
    const { value, tone } = readOpMetric(metric, snapshot);
    return <KeyTile label={metric.label} value={value} hint={opMetricHint(metric, snapshot)} tone={tone} scale={scale} />;
};

/*
 * Правая колонка: кто из верификаторов сегодня писал клиентам, сколько чатов у него сейчас в работе
 * и за сутки и как он отвечает. Чат, где писали двое, засчитан обоим — поэтому сумма по людям
 * больше «Чатов за сутки», и это не ошибка (так же считают «Чаты ОП» и «Воронка ОП»).
 */
const VerifiersColumn = ({ people, firstTarget, innerTarget, scale = 1 }) => {
    const items = Array.isArray(people) ? people : [];
    const nameSize = `clamp(1rem, ${(1.25 * scale).toFixed(2)}vw, ${(1.375 * scale).toFixed(3)}rem)`;
    return (
        <div className={`${iosCard} flex flex-col p-5`}>
            <div className="mb-2 flex items-center gap-2.5 text-[15px] font-semibold text-slate-500">
                <FaIcon className="fas fa-user-check"></FaIcon>
                <span>Верификаторы сегодня</span>
            </div>
            {items.length === 0 ? (
                <div className="py-1.5 text-[15px] text-slate-400">Сегодня ещё никто не писал</div>
            ) : (
                <ul className="min-h-0 divide-y divide-slate-100 overflow-y-auto">
                    {items.map((item) => (
                        <li key={item.user_id} className="py-3">
                            <div className="leading-snug text-slate-800" style={{ fontSize: nameSize }}>{item.name}</div>
                            {/* flex-wrap + whitespace-nowrap в паре: строка переносится целиком,
                                а не сжимает каждый кусок с переносом внутри слова. */}
                            <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[14px] font-medium tabular-nums text-slate-400">
                                {item.in_work ? (
                                    <>
                                        <span className="whitespace-nowrap text-slate-600">
                                            {formatInt(item.in_work)} {chatsWord(item.in_work)} в работе
                                        </span>
                                        <span className="text-slate-300">·</span>
                                    </>
                                ) : null}
                                <span className="whitespace-nowrap">
                                    {formatInt(item.chats)} {item.in_work ? '' : `${chatsWord(item.chats)} `}за сутки
                                </span>
                            </div>
                            {/* Время ответа — только у того, кому сегодня было на что отвечать:
                                у остальных строка была бы парой прочерков, то есть шумом. */}
                            {item.first_reply_seconds !== null || item.inner_reply_seconds !== null ? (
                                <div className="mt-1 flex flex-wrap items-baseline gap-x-2 text-[13px] tabular-nums text-slate-400">
                                    <span className="whitespace-nowrap">
                                        первый{' '}
                                        <span className={`font-medium ${WALLBOARD_TONE_TEXT[
                                            chatReplyTone(item.first_reply_seconds, firstTarget)]}`}>
                                            {formatMinutes(item.first_reply_seconds, 1, false)}
                                        </span>
                                    </span>
                                    <span className="text-slate-300">·</span>
                                    <span className="whitespace-nowrap">
                                        внутри{' '}
                                        <span className={`font-medium ${WALLBOARD_TONE_TEXT[
                                            chatReplyTone(item.inner_reply_seconds, innerTarget)]}`}>
                                            {formatMinutes(item.inner_reply_seconds, 1, false)}
                                        </span>
                                    </span>
                                    <span>мин</span>
                                </div>
                            ) : null}
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
};

/*
 * Режимы подсказки графика, как у «Чата» СЗоВ: цифры часа или кто в этом часу писал клиентам и
 * сколько чатов было у каждого — это ответ на вопрос владельца «сколько чатов у человека».
 */
export const OP_CHAT_TOOLTIP_MODES = [
    { key: 'metrics', label: 'Показатели', hint: 'Чаты, время ответа и сколько верификаторов писали' },
    { key: 'people', label: 'Кто писал', hint: 'ФИО и сколько чатов у каждого в этом часу' },
];

const TOOLTIP_NAMES_LIMIT = 12;

const TooltipHeader = ({ label, partial }) => (
    <div className="mb-1 font-semibold text-slate-900">
        {label}
        {partial ? <span className="ml-1 font-normal text-slate-400">· час идёт</span> : null}
    </div>
);

const PeopleTooltip = ({ active, payload, label }) => {
    if (!active || !payload?.length) return null;
    const row = payload[0]?.payload || {};
    const people = Array.isArray(row.people) ? row.people : [];
    const shown = people.slice(0, TOOLTIP_NAMES_LIMIT);
    return (
        <div className="max-w-[24rem] rounded-xl border border-slate-200 bg-white/95 px-3 py-2 text-[12.5px] shadow-lg">
            <TooltipHeader label={label} partial={row.partial} />
            {people.length === 0 ? (
                <div className="text-slate-400">Никто не писал</div>
            ) : (
                <>
                    <ul className="space-y-0.5 text-slate-700">
                        {shown.map((person) => (
                            <li key={person.user_id} className="flex items-baseline justify-between gap-3">
                                <span className="min-w-0">{person.name}</span>
                                <span className="shrink-0 tabular-nums text-slate-400">
                                    {formatInt(person.chats)} {chatsWord(person.chats)}
                                </span>
                            </li>
                        ))}
                    </ul>
                    {people.length > shown.length ? (
                        <div className="mt-0.5 text-slate-400">и ещё {formatInt(people.length - shown.length)}</div>
                    ) : null}
                </>
            )}
        </div>
    );
};

const ChartTooltip = ({ active, payload, label }) => {
    if (!active || !payload?.length) return null;
    const row = payload[0]?.payload || {};
    return (
        <div className="rounded-xl border border-slate-200 bg-white/95 px-3 py-2 text-[12.5px] shadow-lg">
            <TooltipHeader label={label} partial={row.partial} />
            <div className="space-y-0.5 text-slate-600">
                <div>Чатов начато: <span className="font-medium tabular-nums text-slate-900">{formatInt(row.chats)}</span></div>
                <div>
                    Ответ внутри чата:{' '}
                    <span className="font-medium tabular-nums" style={{ color: CHART_COLORS.inner }}>
                        {formatMinutes(row.innerSeconds)}
                    </span>
                </div>
                <div>
                    Первый ответ:{' '}
                    <span className="font-medium tabular-nums text-slate-900">{formatMinutes(row.firstSeconds)}</span>
                </div>
                <div>
                    Верификаторов писали:{' '}
                    <span className="font-medium tabular-nums" style={{ color: CHART_COLORS.people }}>
                        {formatInt(row.verifiers)}
                    </span>
                </div>
            </div>
        </div>
    );
};

const formatPeople = (value) => {
    const number = Number(value);
    return Number.isFinite(number) && number > 0 ? String(Math.round(number)) : '';
};

/*
 * График по часам: слева минуты — как быстро отвечали внутри чата, справа люди — сколько
 * верификаторов писали клиентам в этот час. Пунктир — норма ответа внутри чата. Час — промежуток:
 * в столбик идут диалоги, начавшиеся с 12:00:00 по 12:59:59.
 */
const HourlyChart = ({ rows, targetSeconds, scale = 1, tooltipMode = 'metrics' }) => {
    const data = useMemo(() => (rows || []).map((row) => ({
        hour: hourLabel(row.hour),
        chats: row.chats,
        innerMinutes: row.inner_reply_seconds === null || row.inner_reply_seconds === undefined
            ? null : Number((row.inner_reply_seconds / 60).toFixed(2)),
        innerSeconds: row.inner_reply_seconds ?? null,
        firstSeconds: row.first_reply_seconds ?? null,
        verifiers: row.verifiers ?? 0,
        people: Array.isArray(row.operators) ? row.operators : [],
        partial: Boolean(row.partial),
    })), [rows]);

    // Верх левой оси — удвоенная норма, а если весь день плохой, то полторы медианы часов. Иначе
    // ночной час с одним медленным чатом (замер 25.09.2026: 30 мин в 04–05 при одном человеке)
    // растягивал шкалу до получаса, и дневные минуты прилипали к нулю. Выброс уходит за край,
    // точная цифра — в подсказке.
    const leftAxisMax = useMemo(() => {
        const values = data.map((row) => row.innerMinutes).filter((value) => value !== null)
            .sort((a, b) => a - b);
        const median = values.length ? values[Math.floor(values.length / 2)] : 0;
        return Math.ceil(Math.max((targetSeconds / 60) * 2, median * 1.5));
    }, [data, targetSeconds]);

    if (!data.some((row) => row.chats || row.verifiers)) {
        return <div className="py-10 text-center text-[14px] text-slate-400">За сегодня чатов ещё не было</div>;
    }
    return (
        <div style={{ height: `${Math.round(300 * scale)}px` }}>
            <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={data} margin={{ top: 26, right: 8, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" vertical={false} />
                    <XAxis dataKey="hour" tick={{ fontSize: 11 * scale, fill: '#94a3b8' }}
                           tickLine={false} axisLine={{ stroke: '#e2e8f0' }} interval="preserveStartEnd" />
                    <YAxis yAxisId="left" domain={[0, leftAxisMax]} allowDataOverflow
                           tick={{ fontSize: 11 * scale, fill: '#94a3b8' }}
                           tickLine={false} axisLine={false} width={44 * scale}
                           label={{ value: 'мин', position: 'top', offset: 12, fontSize: 11 * scale, fill: '#94a3b8' }} />
                    <YAxis yAxisId="right" orientation="right" allowDecimals={false}
                           tick={{ fontSize: 11 * scale, fill: '#94a3b8' }} tickLine={false} axisLine={false}
                           width={44 * scale}
                           label={{ value: 'люди', position: 'top', offset: 12, fontSize: 11 * scale, fill: '#94a3b8' }} />
                    <Tooltip content={tooltipMode === 'people' ? <PeopleTooltip /> : <ChartTooltip />}
                             cursor={{ fill: '#f8fafc' }} />
                    <Legend verticalAlign="bottom" height={28} iconType="circle"
                            wrapperStyle={{ fontSize: 12 * scale, color: '#475569' }} />
                    <Bar yAxisId="right" dataKey="verifiers" name="Верификаторов писали"
                         fill={CHART_COLORS.people} radius={[4, 4, 0, 0]} maxBarSize={26}>
                        <LabelList dataKey="verifiers" position="top" formatter={formatPeople}
                                   style={{ fontSize: 10.5 * scale, fill: '#1d4ed8', fontWeight: 600 }} />
                    </Bar>
                    <ReferenceLine yAxisId="left" y={targetSeconds / 60} stroke={CHART_COLORS.target}
                                   strokeDasharray="5 4" strokeWidth={2}
                                   label={{ value: `норма ${formatMinutes(targetSeconds, 0)}`, position: 'right',
                                            fontSize: 11 * scale, fill: CHART_COLORS.target }} />
                    <Line yAxisId="left" type="monotone" dataKey="innerMinutes" name="Ответ внутри чата"
                          stroke={CHART_COLORS.inner} strokeWidth={2} dot={{ r: 3 }} connectNulls={false} />
                </ComposedChart>
            </ResponsiveContainer>
        </div>
    );
};

/** Тело табло «Чат». Одна разметка для страницы и для стены — различается только масштабом. */
export default function OpChatWallboardBody({ snapshot, scale = 1 }) {
    const [tooltipMode, setTooltipMode] = useState(OP_CHAT_TOOLTIP_MODES[0].key);
    const today = snapshot?.today || {};
    const firstTarget = Number(snapshot?.first_target_seconds) || 60;
    const innerTarget = Number(snapshot?.inner_target_seconds) || 240;

    return (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_19rem]" style={{ fontFamily: APPLE_FONT }}>
            <div className="space-y-4">
                <Section icon="fa-bolt" title="Чаты · сейчас">
                    <Grid>
                        <NowTile metricKey="op_chat_in_work" snapshot={snapshot} scale={scale} />
                        <NowTile metricKey="op_chat_waiting" snapshot={snapshot} scale={scale} />
                        <NowTile metricKey="op_chat_longest_wait" snapshot={snapshot} scale={scale} />
                        <NowTile metricKey="op_chat_verifiers" snapshot={snapshot} scale={scale} />
                    </Grid>
                </Section>

                <Section icon="fa-chart-bar" title="Показатели за день">
                    <Grid cols={3}>
                        <StatTile label="Чатов за сутки" value={formatInt(today.chats)} scale={scale} />
                        <StatTile label="Первый ответ" value={formatMinutes(today.first_reply_seconds, 1, false)}
                                  unit="мин" tone={chatReplyTone(today.first_reply_seconds, firstTarget)} scale={scale} />
                        <StatTile label="Ответ внутри чата" value={formatMinutes(today.inner_reply_seconds, 1, false)}
                                  unit="мин" tone={chatReplyTone(today.inner_reply_seconds, innerTarget)} scale={scale} />
                    </Grid>
                </Section>

                <Section
                    icon="fa-clock"
                    title="По часам"
                    right={(
                        <div className="flex items-center gap-3">
                            <span className="hidden text-[12.5px] text-slate-400 xl:inline">
                                {tooltipMode === 'people'
                                    ? 'наведите на час — покажем, кто писал и сколько чатов'
                                    : `ответ внутри чата и сколько верификаторов писали; норма ${formatMinutes(innerTarget, 0)}`}
                            </span>
                            <SegmentedSwitch compact value={tooltipMode} options={OP_CHAT_TOOLTIP_MODES}
                                             onChange={setTooltipMode} />
                        </div>
                    )}
                >
                    <HourlyChart rows={snapshot?.hourly} targetSeconds={innerTarget} scale={scale}
                                 tooltipMode={tooltipMode} />
                </Section>
            </div>

            <VerifiersColumn people={snapshot?.now?.operators} firstTarget={firstTarget}
                             innerTarget={innerTarget} scale={scale} />
        </div>
    );
}
