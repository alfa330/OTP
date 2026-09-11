import React, { useMemo, useState } from 'react';
import {
    CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer,
    Tooltip, XAxis, YAxis,
} from 'recharts';

import { APPLE_FONT, iosCard, IosSegmented } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { formatNumber, formatPercent } from './funnelFormat';

/*
 * Динамика воронки по дням (ответ `/api/op_funnel/days`).
 *
 * ЛИНИЙ РОВНО ЧЕТЫРЕ, И БОЛЬШЕ НЕ БУДЕТ. Показателей в ответе полтора десятка,
 * и соблазн вывести их все заканчивается графиком, по которому не читается ни
 * один: четыре кривые на одной сетке — предел, дальше глаз перестаёт отделять
 * их друг от друга. Оставлены те, что отвечают на вопрос «где сломалось»:
 * выполнение плана дозвона (сколько набрали), согласия от дозвона (как
 * говорили), успешно от согласий (довели ли), нецелевые от дозвона (кому
 * звонили). Часы, лиды в час и отказы живут в таблице операторов — там их и
 * сравнивают построчно, а не глазами по кривой.
 *
 * ПРОЦЕНТЫ ПРИХОДЯТ ДОЛЯМИ (0.508). Ось и подсказка форматируют их сами: в API
 * одно соглашение, и второго на фронте заводить нельзя — иначе половина экрана
 * начнёт делить на сто, а половина нет.
 *
 * РЕЖИМ «ОПЕРАТОР» РИСУЕТ МЕНЬШЕ ЛИНИЙ, И ЭТО НЕ ОШИБКА. `/days` отдаёт по
 * оператору за сутки только `handled/reached/agreed/hours/plan_rate` (см.
 * `routes._cell`) — «успешно» и «нецелевые» в суточном разрезе есть лишь по
 * команде. Линия из сплошных пустот выглядела бы как провал показателя, поэтому
 * недоступные ряды не рисуются вовсе, а под графиком стоит строка, объясняющая
 * почему их нет.
 */

/* Ряды графика. Цвет здесь несёт смысл — он ОТЛИЧАЕТ показатель, другого
   способа опознать кривую нет. Янтарь достался нецелевым намеренно: это
   единственный ряд, рост которого плохо. */
const FUNNEL_SERIES = [
    { key: 'plan_reached_rate', title: '% плана дозвона', color: '#2563eb' },
    { key: 'agree_rate', title: '% согласий от дозвона', color: '#0d9488' },
    { key: 'success_rate', title: '% успешно от согласий', color: '#7c3aed' },
    { key: 'untargeted_rate', title: '% нецелевых от дозвона', color: '#d97706' },
];

/* У «Верификатора» воронки обзвона нет, и все четыре ряда выше у него пустые:
   вкладка «Динамика» показывала бы четыре плоские линии по нулю под честным
   заголовком. Поэтому у него свои ряды — нагрузка и качество ответа. */
const LOAD_SERIES = [
    { key: 'chats_plan_rate', title: '% от нормы чатов в час', color: '#2563eb' },
    { key: 'reply_gap', title: 'Запас по времени ответа', color: '#0d9488' },
];

/* Запасной расчёт конверсии, если в строке нет готового `rates`: суточный
   разрез по оператору приходит сырыми числами, а не долями. */
const RATE_FALLBACK = {
    plan_reached_rate: (row) => ratio(row.reached, row.plan_reached),
    agree_rate: (row) => ratio(row.agreed, row.reached),
    success_rate: (row) => ratio(row.succeeded, row.agreed),
    untargeted_rate: (row) => ratio(row.untargeted, row.reached),
    chats_plan_rate: (row, targets) => ratio(
        ratio((Number(row.chats) || 0) + (Number(row.tickets) || 0), row.work_hours),
        targets && targets.chats_per_hour),
    reply_gap: (row, targets) => {
        const share = ratio(targets && targets.reply_seconds, row.chat_reply_seconds);
        return share === null ? null : share - 1;
    },
};

function ratio(top, bottom) {
    const a = Number(top);
    const b = Number(bottom);
    if (!Number.isFinite(a) || !Number.isFinite(b) || b === 0) return null;
    return a / b;
}

function rateOf(row, key, targets) {
    if (!row) return null;
    const ready = row.rates ? row.rates[key] : undefined;
    if (ready !== undefined && ready !== null) return Number(ready);
    const fallback = RATE_FALLBACK[key];
    return fallback ? fallback(row, targets) : null;
}

/* Подпись дня оси X. Дату разбираем по частям, а не `new Date('2026-09-01')`:
   строка без времени читается как UTC-полночь, и в браузере западнее Гринвича
   день уезжал бы на предыдущий (на сдвиге суток в проекте уже горели —
   отчёт Chat2Desk уехал на пять часов). */
function dayParts(iso) {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
    if (!match) return null;
    return { y: Number(match[1]), m: Number(match[2]), d: Number(match[3]) };
}

function shortDay(iso) {
    const parts = dayParts(iso);
    return parts ? `${pad2(parts.d)}.${pad2(parts.m)}` : String(iso || '');
}

function pad2(value) {
    return String(value).padStart(2, '0');
}

function fullDay(iso) {
    const parts = dayParts(iso);
    return parts ? `${pad2(parts.d)}.${pad2(parts.m)}.${parts.y}` : String(iso || '');
}

/* Строка оператора за сутки. Поддержаны оба вида, в которых раздел носит
   операторов: сетка `/days` (`cells`) и уже разложенные по дням строки. */
function operatorRow(entry, day) {
    if (!entry) return null;
    const cells = entry.cells;
    if (cells && cells[day]) {
        const cell = cells[day];
        return {
            handled: cell.handled,
            reached: cell.reached,
            agreed: cell.agreed,
            work_hours: cell.hours,
            rates: { plan_reached_rate: cell.plan_rate },
        };
    }
    if (Array.isArray(entry.days)) {
        return entry.days.find((row) => String(row.day) === day) || null;
    }
    return null;
}

const DynamicsTooltip = ({ active, payload, label }) => {
    if (!active || !payload || !payload.length) return null;
    const row = payload[0].payload || {};
    return (
        <div className="rounded-xl bg-white/95 px-3 py-2 text-[12px] shadow-[0_10px_30px_rgba(15,23,42,0.14)] ring-1 ring-slate-200/80 backdrop-blur-xl"
             style={{ fontFamily: APPLE_FONT }}>
            <div className="mb-1 font-semibold text-slate-800">{fullDay(row.day) || label}</div>
            {payload.map((item) => (
                <div key={item.dataKey} className="flex items-center gap-2">
                    <span className="h-[3px] w-3.5 rounded-full" style={{ background: item.color }} />
                    <span className="text-slate-500">{item.name}</span>
                    <span className="ml-auto font-medium tabular-nums text-slate-900">
                        {formatPercent(item.value)}
                    </span>
                </div>
            ))}
            {/* Знаменатель обязателен: доля без него не говорит, от чего считалась,
                и «100 % согласий» при трёх дозвонах читается как успех. */}
            <div className="mt-1 border-t border-slate-100 pt-1 tabular-nums text-slate-500">
                обработано {formatNumber(row.handled)} · дозвон {formatNumber(row.reached)}
                {row.agreed !== null && row.agreed !== undefined ? ` · согласий ${formatNumber(row.agreed)}` : ''}
            </div>
        </div>
    );
};

export default function DynamicsChart({ days = [], targets = null, operators = [],
                                       kind = 'funnel' }) {
    const [mode, setMode] = useState('team');
    const [userId, setUserId] = useState(null);

    /* Набор рядов зависит от направления. У «Верификатора» воронки обзвона нет, и
       все четыре ряда выше у него пустые: вкладка показывала бы честный
       заголовок над четырьмя плоскими нулями. */
    const SERIES = kind === 'load' ? LOAD_SERIES : FUNNEL_SERIES;

    /* Операторы: несопоставленный (user_id = 0) уходит вниз списка — это не
       человек, а сигнал о пробеле в сопоставлении. */
    const people = useMemo(() => {
        const list = Array.isArray(operators) ? operators.filter(Boolean) : [];
        return [...list].sort((a, b) => {
            const aUnmapped = !a.user_id;
            const bUnmapped = !b.user_id;
            if (aUnmapped !== bUnmapped) return aUnmapped ? 1 : -1;
            return String(a.name || '').localeCompare(String(b.name || ''), 'ru');
        });
    }, [operators]);

    /* Выбранный оператор проверяется по текущему списку, а не хранится слепо:
       смена периода или направления оставила бы в состоянии человека, которого
       в выборке уже нет, и график молча показал бы пустоту. */
    const active = useMemo(() => {
        if (!people.length) return null;
        const found = people.find((item) => item.user_id === userId);
        return found || people[0];
    }, [people, userId]);

    const perOperator = mode === 'operator' && !!active;

    const rows = useMemo(() => {
        const list = Array.isArray(days) ? days : [];
        return list.map((item) => {
            const day = String(item.day || '');
            const source = perOperator ? operatorRow(active, day) : item;
            const point = {
                day,
                label: shortDay(day),
                handled: source ? source.handled ?? null : null,
                reached: source ? source.reached ?? null : null,
                agreed: source ? source.agreed ?? null : null,
            };
            SERIES.forEach((series) => { point[series.key] = rateOf(source, series.key, targets); });
            return point;
        });
    }, [days, perOperator, active]);

    /* Ряд, в котором нет ни одного значения, не рисуется: пустая линия в
       легенде — обещание показателя, которого нет. */
    const visible = useMemo(
        () => SERIES.filter((series) => rows.some((row) => row[series.key] !== null
                                                        && row[series.key] !== undefined)),
        [rows],
    );

    /* Порог — норма направления («с какого % плана красим зелёным», приходит
       числом 100), а не зашитая единица: нормы правит руководитель, и линия
       обязана ехать вместе с ними. */
    const threshold = useMemo(() => {
        const green = Number(targets && targets.green_from);
        return Number.isFinite(green) && green > 0 ? green / 100 : 1;
    }, [targets]);

    const hasDays = rows.length > 0;
    const hasLines = visible.length > 0;
    const hidden = SERIES.length - visible.length;

    return (
        <section className={`${iosCard} p-4`} style={{ fontFamily: APPLE_FONT }}>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div>
                    <div className="text-[13px] font-semibold text-slate-800">Динамика по дням</div>
                    <div className="text-[11.5px] text-slate-500">
                        {perOperator ? active.name || 'Не сопоставлен' : 'Вся команда направления'}
                    </div>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    <IosSegmented
                        value={mode}
                        ariaLabel="Чью динамику показать"
                        options={[
                            { value: 'team', label: 'Команда' },
                            { value: 'operator', label: 'Оператор' },
                        ]}
                        onChange={(value) => setMode(people.length ? value : 'team')}
                    />
                    {mode === 'operator' && people.length > 0 && (
                        <CustomSelect
                            variant="ios"
                            className="w-56"
                            ariaLabel="Оператор"
                            searchable={people.length > 8}
                            searchPlaceholder="Поиск оператора…"
                            value={active ? active.user_id : null}
                            options={people.map((item) => ({
                                value: item.user_id,
                                label: item.name || 'Не сопоставлен',
                            }))}
                            onChange={(value) => setUserId(value)}
                        />
                    )}
                </div>
            </div>

            {!hasDays && (
                <p className="py-10 text-center text-[13px] text-slate-500">
                    За выбранный период суточных данных нет. Они появятся после обновления данных.
                </p>
            )}

            {hasDays && !hasLines && (
                <p className="py-10 text-center text-[13px] text-slate-500">
                    {perOperator
                        ? 'По этому оператору суточной разбивки за период нет.'
                        : 'За период нет ни одного посчитанного показателя.'}
                </p>
            )}

            {hasDays && hasLines && (
                <>
                    <div className="h-64 sm:h-72"
                         role="img"
                         aria-label={`Динамика по дням, ${rows.length} суток; значения перечислены после графика`}>
                        <ResponsiveContainer width="100%" height="100%">
                            <LineChart data={rows} margin={{ top: 8, right: 12, left: -14, bottom: 0 }}>
                                <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" vertical={false} />
                                <XAxis
                                    dataKey="label"
                                    tick={{ fontSize: 10, fill: '#94a3b8' }}
                                    interval="preserveStartEnd"
                                    tickLine={false}
                                    axisLine={{ stroke: '#e2e8f0' }}
                                />
                                <YAxis
                                    tick={{ fontSize: 10, fill: '#94a3b8' }}
                                    tickLine={false}
                                    axisLine={false}
                                    width={52}
                                    /* Потолок не жёсткий: перевыполнение плана на 140 %
                                       случается, и обрезанная по 100 % ось спрятала бы
                                       именно тот день, ради которого сюда смотрят. */
                                    domain={[0, (max) => Math.max(threshold * 1.1, max * 1.1, 0.1)]}
                                    tickFormatter={(value) => `${Math.round(value * 100)} %`}
                                />
                                <Tooltip content={<DynamicsTooltip />}
                                         cursor={{ stroke: '#cbd5e1', strokeDasharray: '3 3' }} />
                                <Legend
                                    verticalAlign="bottom"
                                    height={28}
                                    iconType="plainline"
                                    wrapperStyle={{ fontSize: 11.5, color: '#64748b', paddingTop: 4 }}
                                />
                                <ReferenceLine
                                    y={threshold}
                                    stroke="#94a3b8"
                                    strokeDasharray="4 4"
                                    label={{
                                        value: `план ${formatPercent(threshold, 0)}`,
                                        position: 'insideTopRight',
                                        fill: '#64748b',
                                        fontSize: 10.5,
                                    }}
                                />
                                {visible.map((series) => (
                                    <Line
                                        key={series.key}
                                        type="monotone"
                                        dataKey={series.key}
                                        name={series.title}
                                        stroke={series.color}
                                        strokeWidth={2}
                                        dot={false}
                                        activeDot={{ r: 3.5 }}
                                        /* Разрыв показывается разрывом: день без
                                           знаменателя (ноль дозвонов) — это «нет
                                           данных», а соединённая через него линия
                                           врала бы о непрерывной работе. */
                                        connectNulls={false}
                                        isAnimationActive={false}
                                    />
                                ))}
                            </LineChart>
                        </ResponsiveContainer>
                    </div>

                    {perOperator && hidden > 0 && (
                        <p className="mt-2 text-[11.5px] leading-snug text-slate-500">
                            По оператору за сутки считаются выполнение плана и согласия;
                            «успешно» и «нецелевые» в суточном разрезе есть только по команде.
                        </p>
                    )}

                    {/* Те же числа построчно — для скринридера: график для него картинка. */}
                    <ul className="sr-only">
                        {rows.map((row) => (
                            <li key={row.day}>
                                {fullDay(row.day)}:{' '}
                                {visible.map((series) => `${series.title} ${formatPercent(row[series.key])}`).join(', ')}
                            </li>
                        ))}
                    </ul>
                </>
            )}
        </section>
    );
}
