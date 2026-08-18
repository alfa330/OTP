import React, { useMemo } from 'react';
import {
    Bar,
    CartesianGrid,
    ComposedChart,
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
 *     сколько чатников надо было держать в этот час ради цели в 2 минуты (правая ось, люди).
 *
 * Оси две, и это осознанно: минуты и люди — разные величины, но смысл графика именно в их
 * паре («медленно отвечаем — потому что людей столько, а надо столько»), поэтому у каждой оси
 * подпись, у каждого ряда легенда, а в подсказке обе величины стоят рядом с единицами.
 */

// Ряды графика. Цвета проверены валидатором палитры на различимость при дальтонизме:
// синий (факт) / янтарный (норма) / малиновый (время ответа) дают ΔE 19+ на худшей паре.
const CHART_COLORS = {
    online: '#3b82f6',
    required: '#f59e0b',
    inner: '#e11d48',
    target: '#059669',
};

const KEY_PALETTE = {
    info: { bg: 'bg-blue-100/70', text: 'text-blue-700', hint: 'text-blue-600/80' },
    violet: { bg: 'bg-violet-100/70', text: 'text-violet-700', hint: 'text-violet-600/80' },
    good: { bg: 'bg-emerald-100/70', text: 'text-emerald-700', hint: 'text-emerald-600/80' },
    warn: { bg: 'bg-amber-100/70', text: 'text-amber-700', hint: 'text-amber-600/80' },
    bad: { bg: 'bg-rose-100/70', text: 'text-rose-700', hint: 'text-rose-600/80' },
    neutral: { bg: 'bg-slate-100', text: 'text-slate-700', hint: 'text-slate-500' },
};

// Те же два размера цифр, что на «Основе»: ключевые читаются через зал, дневные — рядом.
const VALUE_SIZE = { key: [3, 4.7, 5], stat: [2.375, 3.5, 3.75] };

const valueFontSize = (size, scale) => {
    const [min, mid, max] = VALUE_SIZE[size] || VALUE_SIZE.stat;
    return `clamp(${(min * scale).toFixed(3)}rem, ${(mid * scale).toFixed(2)}vw, ${(max * scale).toFixed(3)}rem)`;
};

const Section = ({ icon, title, children, right = null }) => (
    <div className={`${iosCard} p-5`}>
        <div className="mb-4 flex items-center justify-between gap-3 px-0.5">
            <div className="flex items-center gap-2.5 text-[15px] font-semibold text-slate-500">
                <FaIcon className={`fas ${icon}`}></FaIcon>
                <span>{title}</span>
            </div>
            {right}
        </div>
        {children}
    </div>
);

const Grid = ({ children }) => (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">{children}</div>
);

const KeyTile = ({ label, value, hint, tone = 'neutral', scale = 1 }) => {
    const palette = KEY_PALETTE[tone] || KEY_PALETTE.neutral;
    return (
        <div className={`flex flex-col items-center gap-2 rounded-2xl px-4 py-6 text-center ${palette.bg}`}>
            <div className={`text-[15px] font-semibold ${palette.text}`}>{label}</div>
            <div
                className={`font-semibold tabular-nums leading-none ${palette.text}`}
                style={{ fontSize: valueFontSize('key', scale) }}
            >
                {value}
            </div>
            {hint ? <div className={`text-[13px] leading-tight ${palette.hint}`}>{hint}</div> : null}
        </div>
    );
};

const StatTile = ({ label, value, tone = 'neutral', scale = 1 }) => (
    <div className="flex flex-col items-center gap-2.5 rounded-2xl border border-slate-200/80 px-4 py-5 text-center">
        <div className="text-[14px] font-medium text-slate-500">{label}</div>
        <div
            className={`font-semibold tabular-nums leading-none ${
                (KEY_PALETTE[tone] || KEY_PALETTE.neutral).text === 'text-slate-700'
                    ? 'text-slate-900'
                    : (KEY_PALETTE[tone] || KEY_PALETTE.neutral).text}`}
            style={{ fontSize: valueFontSize('stat', scale) }}
        >
            {value}
        </div>
    </div>
);

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
                                    {/* Чип только у тех, кто не на линии: иначе «Онлайн» стоял бы
                                        у большинства строк и перестал бы что-либо сообщать. */}
                                    {style && item.status_key !== 'online' ? (
                                        <span className={`mt-0.5 shrink-0 rounded-md px-2 py-0.5 text-[12px] font-medium ${style.chip}`}>
                                            {item.status}
                                        </span>
                                    ) : null}
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

const hourLabel = (hour) => `${String(hour).padStart(2, '0')}:00`;

/** Подсказка графика: обе оси рядом, каждая со своей единицей — иначе их легко перепутать. */
const ChartTooltip = ({ active, payload, label, targetSeconds }) => {
    if (!active || !payload?.length) return null;
    const row = payload[0]?.payload || {};
    const target = formatMinutes(targetSeconds, 0);
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
                        {row.innerSeconds === null ? '—' : formatMinutes(row.innerSeconds)}
                    </span>
                </div>
                <div>
                    Было на линии:{' '}
                    <span className="font-medium tabular-nums" style={{ color: CHART_COLORS.online }}>
                        {row.online === null ? '—' : `${String(row.online).replace('.', ',')} чел.`}
                    </span>
                </div>
                <div>
                    Нужно под {target}:{' '}
                    <span className="font-medium tabular-nums" style={{ color: CHART_COLORS.required }}>
                        {row.required === null ? '—' : `${formatInt(row.required)} чел.`}
                    </span>
                </div>
            </div>
        </div>
    );
};

/*
 * График по часам. Слева минуты (как быстро отвечали внутри чата), справа люди (сколько их
 * было и сколько нужно было под цель). Пунктир — сама цель: пока малиновая линия под ним,
 * людей хватает, и янтарный столбик не выше синего.
 */
const HourlyChart = ({ rows, targetSeconds, scale = 1 }) => {
    const data = useMemo(() => (rows || []).map((row) => ({
        hour: hourLabel(row.hour),
        chats: row.chats,
        innerMinutes: row.inner_reply_seconds === null || row.inner_reply_seconds === undefined
            ? null : Number((row.inner_reply_seconds / 60).toFixed(2)),
        innerSeconds: row.inner_reply_seconds ?? null,
        online: row.operators_online ?? null,
        required: row.operators_required ?? null,
        partial: Boolean(row.partial),
    })), [rows]);

    if (data.length === 0) {
        return <div className="py-10 text-center text-[14px] text-slate-400">За сегодня чатов ещё не было</div>;
    }
    return (
        <div style={{ height: `${Math.round(300 * scale)}px` }}>
            <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" vertical={false} />
                    <XAxis dataKey="hour" tick={{ fontSize: 11 * scale, fill: '#94a3b8' }}
                           tickLine={false} axisLine={{ stroke: '#e2e8f0' }} interval="preserveStartEnd" />
                    <YAxis yAxisId="left" tick={{ fontSize: 11 * scale, fill: '#94a3b8' }}
                           tickLine={false} axisLine={false} width={44 * scale}
                           label={{ value: 'мин', position: 'insideTopLeft', fontSize: 11 * scale, fill: '#94a3b8' }} />
                    <YAxis yAxisId="right" orientation="right" allowDecimals={false}
                           tick={{ fontSize: 11 * scale, fill: '#94a3b8' }} tickLine={false} axisLine={false}
                           width={44 * scale}
                           label={{ value: 'чатники', position: 'insideTopRight', fontSize: 11 * scale, fill: '#94a3b8' }} />
                    <Tooltip content={<ChartTooltip targetSeconds={targetSeconds} />} cursor={{ fill: '#f8fafc' }} />
                    <Legend verticalAlign="bottom" height={28} iconType="circle"
                            wrapperStyle={{ fontSize: 12 * scale, color: '#475569' }} />
                    <Bar yAxisId="right" dataKey="online" name="Было на линии"
                         fill={CHART_COLORS.online} radius={[4, 4, 0, 0]} maxBarSize={26} />
                    <Bar yAxisId="right" dataKey="required" name="Нужно под цель"
                         fill={CHART_COLORS.required} radius={[4, 4, 0, 0]} maxBarSize={26} />
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

    // Отпуск и «не в системе» своих плиток не имеют — как тренинг и тех.причина на «Основе»,
    // показываем их приглушённой строкой и только когда есть кого показывать.
    const asideParts = [
        [Number(now.operators_on_break) || 0, 'на перерыве'],
        [Number(now.operators_on_holiday) || 0, 'в отпуске'],
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
                        <StatTile label="Открыто сейчас" value={formatInt(today.chats_open)} scale={scale} />
                        <StatTile label="Ответ внутри чата" value={formatMinutes(inner)} tone={innerTone} scale={scale} />
                        <StatTile label="Первый ответ" value={formatMinutes(today.first_reply_seconds)} scale={scale} />
                    </Grid>
                </Section>

                <Section
                    icon="fa-clock"
                    title="По часам"
                    right={(
                        <span className="text-[12.5px] text-slate-400">
                            сколько чатников нужно, чтобы отвечать внутри чата за {formatMinutes(targetSeconds, 0)}
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
