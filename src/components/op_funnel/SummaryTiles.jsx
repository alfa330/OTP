import React from 'react';
import { APPLE_FONT, iosCard } from '../ui/ios';
import FaIcon from '../common/FaIcon';
import {
    TONE_TEXT, formatDelta, formatHours, formatNumber, formatPercent, formatSeconds,
} from './funnelFormat';

/*
 * Плитки сводки раздела «Воронка ОП» (`/overview` → `summary` + `compare`).
 *
 * ЦВЕТ ЖИВЁТ ТОЛЬКО В ДЕЛЬТЕ. Само число не красится никогда: подкрашенное
 * число заставляет искать порог, которого у него нет, а восемь подкрашенных
 * плиток рядом — это светофор без правил. Требование приёмки владельца: шум =
 * брак. Поэтому у плитки ровно три слоя — подпись, число и (если есть, с чем
 * сравнивать) дельта к предыдущему периоду.
 *
 * У Верификатора воронки нет — там нагрузка и качество, и набор плиток другой
 * (`kind === 'load'`). Рисовать ему «Дозвон» и «Согласия» значило бы показывать
 * восемь нулей: в его суточных строках лидов не бывает вовсе.
 */

/*
 * Дельта к предыдущему периоду.
 *
 * ЛОВУШКА `direction`: в API (`metrics.compare`) это НЕ сторона стрелки, а
 * оценка — 'up' значит «стало лучше». У «Отказов» и «Нецелевых» рост попадает в
 * 'down', потому что они в `metrics.LOWER_IS_BETTER`. Если рисовать стрелку по
 * `direction`, выросшие отказы получат стрелку ВНИЗ и прочитаются как падение.
 * Поэтому: стрелка — по знаку дельты, цвет — по `direction`, и цвет считает
 * `formatDelta` (там же лежат пороги значимости, чтобы они не разошлись между
 * плитками, таблицей и графиком).
 *
 * Стрелки берутся иконками FaIcon, а не символами ↑ ↓ ↕: те выходят цветным
 * эмодзи-глифом и ломают набор строки — в проекте на этом уже горели.
 */
const Delta = ({ item, kind, wasFormat }) => {
    // Прошлого периода нет (оператор не работал, направление не выгружалось) —
    // строки нет вовсе: «—» под каждым числом это восемь прочерков ни о чём.
    if (!item || item.delta == null) return null;

    const { text, tone } = formatDelta(item.delta, kind, item.direction);
    if (!text) return null;

    const delta = Number(item.delta);
    const arrow = delta > 0 ? 'fa-chevron-up' : delta < 0 ? 'fa-chevron-down' : 'fa-minus';

    return (
        <span
            className={`inline-flex items-center gap-1 tabular-nums ${TONE_TEXT[tone] || TONE_TEXT['']}`}
            title={item.was == null ? undefined : `Предыдущий период: ${wasFormat(item.was)}`}
        >
            <FaIcon className={`fas ${arrow} text-[9px]`} aria-hidden="true" />
            {text}
        </span>
    );
};

const Tile = ({ label, value, sub = null, item = null, deltaKind = 'count', wasFormat = formatNumber }) => (
    <div className={`${iosCard} flex flex-col px-3.5 py-3`}>
        <div className="text-[11px] font-semibold uppercase leading-tight tracking-wider text-slate-400">
            {label}
        </div>
        <div className="mt-1 text-[21px] font-semibold leading-none tabular-nums text-slate-900">
            {value}
        </div>
        {(sub || item) && (
            <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11.5px]">
                {sub ? <span className="tabular-nums text-slate-500">{sub}</span> : null}
                <Delta item={item} kind={deltaKind} wasFormat={wasFormat} />
            </div>
        )}
    </div>
);

/* Знаменатель выписан в подписи («от дозвона»), потому что процент без него
 * ничего не значит: 37 % согласий — от базы или от дозвона? Знаменатели
 * зафиксированы решением владельца в `metrics.derive_rates`, и подпись обязана
 * повторять именно их. */
const funnelTiles = (summary, rates) => ([
    {
        label: 'Часы', value: formatHours(summary.work_hours),
        cmp: 'work_hours', deltaKind: 'hours', wasFormat: formatHours,
    },
    {
        label: 'Обработано', value: formatNumber(summary.handled),
        cmp: 'handled', deltaKind: 'count', wasFormat: formatNumber,
    },
    {
        label: 'Дозвон', value: formatNumber(summary.reached),
        sub: `${formatPercent(rates.reach_rate)} от обработанных`,
        cmp: 'reach_rate', deltaKind: 'percent', wasFormat: formatPercent,
    },
    {
        label: 'Согласия', value: formatNumber(summary.agreed),
        sub: `${formatPercent(rates.agree_rate)} от дозвона`,
        cmp: 'agree_rate', deltaKind: 'percent', wasFormat: formatPercent,
    },
    {
        label: 'Успешно', value: formatNumber(summary.succeeded),
        sub: `${formatPercent(rates.success_rate)} от согласий`,
        cmp: 'success_rate', deltaKind: 'percent', wasFormat: formatPercent,
    },
    {
        label: 'Отказы', value: formatNumber(summary.rejected),
        cmp: 'rejected', deltaKind: 'count', wasFormat: formatNumber,
    },
    {
        label: 'Нецелевые', value: formatNumber(summary.untargeted),
        cmp: 'untargeted', deltaKind: 'count', wasFormat: formatNumber,
    },
    {
        // Факт здесь — сам процент. Число дозвонов рядом не ставим: оно уже стоит
        // в плитке «Дозвон», а одно и то же число в двух местах экрана читатель
        // начинает сверять вместо того, чтобы читать.
        label: 'План/Факт', value: formatPercent(rates.plan_reached_rate),
        sub: summary.plan_reached == null
            ? null : `план ${formatNumber(summary.plan_reached)} дозвонов`,
        cmp: 'plan_reached_rate', deltaKind: 'percent', wasFormat: formatPercent,
    },
]);

/*
 * Верификатор: нагрузка и качество.
 *
 * ЛОВУШКА: `summary.handled` у этого направления НОЛЬ — его считает
 * `metrics.handled_for_source` по лидам, а лидов у верификатора не бывает.
 * «Обработано» для него — это чаты плюс тикеты, ровно как в
 * `metrics.verificator_rates`. Взять готовое поле значило бы показать ноль при
 * полной смене работы.
 *
 * Дельт у этих плиток нет намеренно: `metrics.COMPARABLE_METRICS` не сравнивает
 * ни чаты, ни тикеты, ни время ответа, а подставить сюда дельту чужого поля
 * `handled` (которое всегда ноль) значило бы рисовать «0» как измеренный факт.
 */
const loadTiles = (summary, rates) => {
    const handled = (Number(summary.chats) || 0) + (Number(summary.tickets) || 0);
    return [
        {
            label: 'Часы', value: formatHours(summary.work_hours),
            cmp: 'work_hours', deltaKind: 'hours', wasFormat: formatHours,
        },
        { label: 'Чаты', value: formatNumber(summary.chats) },
        { label: 'Тикеты', value: formatNumber(summary.tickets) },
        { label: 'Обработано', value: formatNumber(handled), sub: 'чаты и тикеты вместе' },
        // Один знак после запятой: «4» и «4,6» чата в час — разная смена.
        { label: 'Чаты/час', value: formatNumber(rates.chats_per_hour, 1) },
        {
            label: 'Ср. время ответа', value: formatSeconds(summary.chat_reply_seconds),
            sub: 'первый ответ человека',
        },
        {
            label: 'Ср. время обработки тикета',
            value: formatSeconds(summary.ticket_handle_seconds),
        },
    ];
};

export default function SummaryTiles({ summary, compare, kind = 'funnel' }) {
    // Сводки нет — не рисуем ничего: восемь плиток с прочерками занимают экран
    // и не сообщают ровно ничего, чего не сказала бы пустая область.
    if (!summary) return null;

    const rates = summary.rates || {};
    const comparison = compare || {};
    const tiles = kind === 'load' ? loadTiles(summary, rates) : funnelTiles(summary, rates);

    return (
        <div
            style={{ fontFamily: APPLE_FONT }}
            className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-4"
        >
            {tiles.map((tile) => (
                <Tile
                    key={tile.label}
                    label={tile.label}
                    value={tile.value}
                    sub={tile.sub}
                    item={tile.cmp ? comparison[tile.cmp] : null}
                    deltaKind={tile.deltaKind}
                    wasFormat={tile.wasFormat}
                />
            ))}
        </div>
    );
}
