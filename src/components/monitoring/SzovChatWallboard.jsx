import React, { useMemo } from 'react';
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
import { Grid, KeyTile, Section, StatTile } from './SzovWallboardTiles';
import {
    CHAT_STATUS_STYLE,
    formatDuration,
    formatInt,
    formatMinutes,
} from './szovWallboardShared';

/*
 * «Табло СЗоВ» — направление «Чат» (Chat2Desk). Второй экран того же раздела: переключатель
 * направления стоит в шапке, раскладка повторяет «Основу», чтобы взгляд не переучивался.
 *
 * Что показываем и почему именно это:
 *   - счётчики «сейчас» — сколько чатников на линии, сколько заняты и сколько на тренинге
 *     (запрос владельца); открытые чаты в этом ряду играют роль очереди с «Основы»;
 *   - график по часам — среднее время ответа ВНУТРИ чата (левая ось, минуты) против того,
 *     сколько чатников держало линию в этот час (правая ось, люди; занятые и на тренинге линию
 *     не держат, их минуты в сумму не идут).
 *
 * Оси две, и это осознанно: минуты и люди — разные величины, но смысл графика именно в их
 * паре («отвечали столько-то минут, а людей на линии было столько»), поэтому у каждой оси
 * подпись, у каждого ряда легенда, а в подсказке обе величины стоят рядом с единицами.
 */

// Ряды графика. Цвета проверены валидатором палитры на различимость при дальтонизме.
const CHART_COLORS = {
    online: '#3b82f6',
    inner: '#e11d48',
    target: '#059669',
};

/** Правая колонка: кто сейчас на смене, в каком статусе и сколько у него открытых чатов. */
const ChatPeopleColumn = ({ people, offline, scale = 1 }) => {
    const items = Array.isArray(people) ? people : [];
    const nameSize = `clamp(1rem, ${(1.25 * scale).toFixed(2)}vw, ${(1.375 * scale).toFixed(3)}rem)`;
    return (
        <div className={`${iosCard} flex flex-col p-5`}>
            <div className="mb-2 flex items-center gap-2.5 text-[15px] font-semibold text-slate-500">
                <FaIcon className="fas fa-comments"></FaIcon>
                <span>Чатники на смене</span>
            </div>
            {items.length === 0 ? (
                <div className="py-1.5 text-[15px] text-slate-400">Никого</div>
            ) : (
                <ul className="min-h-0 divide-y divide-slate-100 overflow-y-auto">
                    {items.map((item) => {
                        const style = CHAT_STATUS_STYLE[item.status_key];
                        return (
                            <li key={`${item.operator_id ?? item.name}`} className="py-3">
                                <div className="flex items-start gap-2">
                                    <span className="min-w-0 leading-snug text-slate-800" style={{ fontSize: nameSize }}>
                                        {item.name}
                                    </span>
                                    {/* Статус стоит у каждого, включая «Онлайн» (запрос владельца):
                                        со стены должно быть видно, кто держит линию, а кто нет,
                                        без вычитания из соседних плиток. */}
                                    <span className={`mt-0.5 shrink-0 rounded-md px-2 py-0.5 text-[12px] font-medium ${
                                        (style || CHAT_STATUS_STYLE.offline).chip}`}>
                                        {item.status}
                                    </span>
                                </div>
                                <div className="mt-0.5 flex items-center gap-2 text-[14px] font-medium tabular-nums text-slate-400">
                                    <span>{item.seconds === null || item.seconds === undefined
                                        ? 'с начала суток'
                                        : formatDuration(item.seconds)}</span>
                                    <span className="text-slate-300">·</span>
                                    <span>{formatInt(item.open_chats)} в работе</span>
                                </div>
                            </li>
                        );
                    })}
                </ul>
            )}
            {offline > 0 ? (
                <div className="mt-auto border-t border-slate-200/70 pt-3 text-[14px] text-slate-400">
                    Не в системе: {formatInt(offline)}
                </div>
            ) : null}
        </div>
    );
};

/*
 * Людей на линии считаем средней занятостью часа, поэтому число дробное — и таким остаётся:
 * округлять запрещено (решение владельца 19.08.2026), 3,8 честнее четвёрки. На стене «3,84» —
 * лишняя точность: показываем один знак и убираем его у целых. Ноль не подписываем: пустой час
 * и так пустой, цифра только шумит.
 */
const formatPeople = (value) => {
    const number = Number(value);
    if (!Number.isFinite(number) || number <= 0) return '';
    // Сначала округляем, потом убираем «,0»: иначе рядом стоят «1» и «1,0» — это одно и то же.
    const rounded = Math.round(number * 10) / 10;
    return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1).replace('.', ',');
};

/*
 * Час — промежуток, а не момент: в столбик идут чаты, начавшиеся с 12:00:00 по 12:59:59.
 * Подписываем его так же, как почасовой отчёт в Telegram, чтобы «12–13» на табло и «12:00–13:00»
 * в отчёте читались как одно и то же и никто не гадал, накопление это или интервал.
 */
const hourLabel = (hour) => `${String(hour).padStart(2, '0')}–${String((hour + 1) % 24).padStart(2, '0')}`;

/** Подсказка графика: обе оси рядом, каждая со своей единицей — иначе их легко перепутать. */
const ChartTooltip = ({ active, payload, label }) => {
    if (!active || !payload?.length) return null;
    const row = payload[0]?.payload || {};
    return (
        <div className="rounded-xl border border-slate-200 bg-white/95 px-3 py-2 text-[12.5px] shadow-lg">
            <div className="mb-1 font-semibold text-slate-900">
                {label}
                {row.partial ? <span className="ml-1 font-normal text-slate-400">· час идёт</span> : null}
            </div>
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
                    <span className="font-medium tabular-nums text-slate-900">
                        {formatMinutes(row.firstSeconds)}
                    </span>
                </div>
                <div>
                    {/* Рядом с дробью — сколько всего минут смена простояла на линии в этом часу:
                        из них дробь и получается, и «3,8» перестаёт выглядеть выдумкой. */}
                    Было на линии:{' '}
                    <span className="font-medium tabular-nums" style={{ color: CHART_COLORS.online }}>
                        {row.online === null ? '—' : `${formatPeople(row.online) || '0'} чел.`}
                    </span>
                    {row.onlineSeconds === null ? null : (
                        <span className="tabular-nums text-slate-400">
                            {' · '}{formatMinutes(row.onlineSeconds, 0)} на линии
                        </span>
                    )}
                </div>
            </div>
        </div>
    );
};

/*
 * График по часам. Слева минуты — как быстро отвечали внутри чата, справа люди — сколько
 * чатников держало линию в этот час. Пунктир — цель по времени ответа.
 * Столбик «сколько людей нужно под цель» отсюда убран: пропорция от факта завышала в разы,
 * и на стене это читалось как план найма, которым оно не было (решение владельца 18.08.2026).
 */
const HourlyChart = ({ rows, targetSeconds, scale = 1 }) => {
    const data = useMemo(() => (rows || []).map((row) => ({
        hour: hourLabel(row.hour),
        chats: row.chats,
        innerMinutes: row.inner_reply_seconds === null || row.inner_reply_seconds === undefined
            ? null : Number((row.inner_reply_seconds / 60).toFixed(2)),
        innerSeconds: row.inner_reply_seconds ?? null,
        firstSeconds: row.first_reply_seconds ?? null,
        online: row.operators_online ?? null,
        onlineSeconds: row.online_seconds ?? null,
        partial: Boolean(row.partial),
    })), [rows]);

    if (data.length === 0) {
        return <div className="py-10 text-center text-[14px] text-slate-400">За сегодня чатов ещё не было</div>;
    }
    return (
        <div style={{ height: `${Math.round(300 * scale)}px` }}>
            <ResponsiveContainer width="100%" height="100%">
                {/* Верхний отступ держит подписи осей: без него «мин» и «чатники» обрезаются
                    краем области рисования. */}
                <ComposedChart data={data} margin={{ top: 26, right: 8, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" vertical={false} />
                    <XAxis dataKey="hour" tick={{ fontSize: 11 * scale, fill: '#94a3b8' }}
                           tickLine={false} axisLine={{ stroke: '#e2e8f0' }} interval="preserveStartEnd" />
                    {/* Подписи осей уводим выше делений: без отступа «мин» садится вплотную к
                        верхнему делению и читается как часть числа. */}
                    <YAxis yAxisId="left" tick={{ fontSize: 11 * scale, fill: '#94a3b8' }}
                           tickLine={false} axisLine={false} width={44 * scale}
                           label={{ value: 'мин', position: 'top', offset: 12,
                                    fontSize: 11 * scale, fill: '#94a3b8' }} />
                    <YAxis yAxisId="right" orientation="right" allowDecimals={false}
                           tick={{ fontSize: 11 * scale, fill: '#94a3b8' }} tickLine={false} axisLine={false}
                           width={44 * scale}
                           label={{ value: 'чатники', position: 'top', offset: 12,
                                    fontSize: 11 * scale, fill: '#94a3b8' }} />
                    <Tooltip content={<ChartTooltip />} cursor={{ fill: '#f8fafc' }} />
                    <Legend verticalAlign="bottom" height={28} iconType="circle"
                            wrapperStyle={{ fontSize: 12 * scale, color: '#475569' }} />
                    {/* Число над столбиком: людей на линии единицы, и на глаз 3 от 4 не отличить. */}
                    <Bar yAxisId="right" dataKey="online" name="Чатников на линии"
                         fill={CHART_COLORS.online} radius={[4, 4, 0, 0]} maxBarSize={26}>
                        <LabelList dataKey="online" position="top" formatter={formatPeople}
                                   style={{ fontSize: 10.5 * scale, fill: '#1d4ed8', fontWeight: 600 }} />
                    </Bar>
                    <ReferenceLine yAxisId="left" y={targetSeconds / 60} stroke={CHART_COLORS.target}
                                   strokeDasharray="5 4" strokeWidth={2}
                                   label={{ value: `цель ${formatMinutes(targetSeconds, 0)}`, position: 'right',
                                            fontSize: 11 * scale, fill: CHART_COLORS.target }} />
                    <Line yAxisId="left" type="monotone" dataKey="innerMinutes" name="Ответ внутри чата"
                          stroke={CHART_COLORS.inner} strokeWidth={2} dot={{ r: 3 }} connectNulls={false} />
                </ComposedChart>
            </ResponsiveContainer>
        </div>
    );
};

/** Тело табло по чатам. Отдельный компонент, чтобы встроенный и полноэкранный режим шли одной разметкой. */
export default function SzovChatWallboardBody({ snapshot, scale = 1 }) {
    const now = snapshot?.now || {};
    const today = snapshot?.today || {};
    const targetSeconds = Number(snapshot?.target_seconds) || 120;
    const inner = today.inner_reply_seconds;
    const innerTone = inner === null || inner === undefined
        ? 'neutral' : (Number(inner) <= targetSeconds ? 'good' : 'bad');

    // Часы с измеренным ответом внутри чата и доля тех, где уложились в цель.
    const measuredHours = (snapshot?.hourly || []).filter(
        (row) => row.inner_reply_seconds !== null && row.inner_reply_seconds !== undefined);
    const hoursMeasured = measuredHours.length;
    const hoursInTarget = measuredHours.filter((row) => row.inner_reply_seconds <= targetSeconds).length;

    // Отпуск и «не в системе» своих плиток не имеют — как тренинг и тех.причина на «Основе»,
    // показываем их приглушённой строкой и только когда есть кого показывать.
    const asideParts = [
        [Number(now.operators_on_break) || 0, 'на перерыве'],
        [Number(now.operators_on_holiday) || 0, 'в отпуске'],
        // «Прочее» — статус, которого мы ещё не видели: Chat2Desk завёл новый или переименовал.
        // Человек в нём не пропадает с табло, хотя своей плитки у такого статуса нет.
        [Number(now.operators_other) || 0, 'в прочих статусах'],
    ].filter(([count]) => count > 0).map(([count, label]) => `${formatInt(count)} ${label}`);

    return (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_19rem]" style={{ fontFamily: APPLE_FONT }}>
            <div className="space-y-4">
                <Section icon="fa-bolt" title="Чатники · сейчас">
                    <Grid>
                        <KeyTile label="Онлайн" value={formatInt(now.operators_online)} tone="info" scale={scale}
                                 hint="держат линию" />
                        <KeyTile label="Занят" value={formatInt(now.operators_busy)} tone="violet" scale={scale}
                                 hint="в системе, но не на линии" />
                        <KeyTile label="Тренинг" value={formatInt(now.operators_on_training)} tone="good" scale={scale} />
                        <KeyTile label="Открыто чатов" value={formatInt(now.open_chats)} tone="neutral" scale={scale}
                                 hint="в работе у чатников" />
                    </Grid>
                    {asideParts.length > 0 ? (
                        <div className="mt-3 px-1 text-[14px] text-slate-400">Ещё {asideParts.join(' · ')}</div>
                    ) : null}
                </Section>

                <Section icon="fa-chart-bar" title="Показатели за день">
                    <Grid>
                        <StatTile label="Чатов за сутки" value={formatInt(today.chats)} scale={scale} />
                        {/* Не «открыто сейчас»: это число почти совпадает с плиткой «Открыто чатов»
                            выше, и два одинаковых на вид счётчика рядом только сбивают. Полезнее
                            итог дня по цели — в скольких часах в неё уложились. */}
                        <StatTile label="Часов в цели" value={formatInt(hoursInTarget)}
                                  unit={`из ${formatInt(hoursMeasured)}`}
                                  tone={hoursInTarget === hoursMeasured ? 'good' : 'neutral'} scale={scale} />
                        <StatTile label="Ответ внутри чата" value={formatMinutes(inner, 1, false)} unit="мин"
                                  tone={innerTone} scale={scale} />
                        <StatTile label="Первый ответ" value={formatMinutes(today.first_reply_seconds, 1, false)}
                                  unit="мин" scale={scale} />
                    </Grid>
                </Section>

                <Section
                    icon="fa-clock"
                    title="По часам"
                    right={(
                        <span className="text-[12.5px] text-slate-400">
                            время ответа внутри чата и сколько чатников держало линию; цель {formatMinutes(targetSeconds, 0)}
                        </span>
                    )}
                >
                    <HourlyChart rows={snapshot?.hourly} targetSeconds={targetSeconds} scale={scale} />
                </Section>
            </div>

            <ChatPeopleColumn people={now.operators} offline={Number(now.operators_offline) || 0} scale={scale} />
        </div>
    );
}
