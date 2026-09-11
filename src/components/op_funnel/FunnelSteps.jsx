import React from 'react';
import { APPLE_FONT, iosCard } from '../ui/ios';
import FaIcon from '../common/FaIcon';
import { formatNumber, formatPercent } from './funnelFormat';

/*
 * Воронка «Обработано → Дозвон → Согласия → Успешно» (`/overview` → `funnel`).
 *
 * Это разметка, а не график: библиотеке здесь нечего считать — четыре числа и
 * три перехода. `recharts` дал бы холст, ось и тултип там, где нужны полоса и
 * подпись, и на телефоне сжал бы всё это в нечитаемое.
 *
 * Полосы одного нейтрального тона. Раскрашивать шаги в градиент означало бы
 * заявить смысл, которого у цвета шага нет: «Успешно» не хуже «Дозвона», это
 * просто следующая ступень. Цвет в разделе оставлен под то, что действительно
 * что-то значит — дельты и аномалии.
 *
 * Ширина считается от САМОГО БОЛЬШОГО шага, а не от первого: у направлений без
 * обзвона первый шаг может оказаться не максимумом, и полосы уехали бы за край.
 */

/* Полоса ненулевого шага не должна исчезать. При 17 успешных против 5 787
 * обработанных честная ширина — 0,3 %, то есть меньше пикселя: на экране это
 * пустая дорожка, и «Успешно» читается как ноль. Минимум в 1,5 % врёт про
 * масштаб на доли процента, но не врёт про факт «они есть» — а число рядом
 * точное. */
const MIN_VISIBLE_WIDTH = 1.5;

export default function FunnelSteps({ steps }) {
    const list = Array.isArray(steps) ? steps : [];
    // У Верификатора воронки нет вовсе — API отдаёт пустой массив. Пустая
    // карточка с заголовком «Воронка» это чистый шум, поэтому не рисуем ничего.
    if (!list.length) return null;

    const max = list.reduce((acc, step) => Math.max(acc, Number(step && step.value) || 0), 0);

    return (
        <section style={{ fontFamily: APPLE_FONT }} className={`${iosCard} p-4`}>
            <div className="flex items-baseline justify-between gap-3">
                <h3 className="text-[13px] font-semibold text-slate-700">Путь лида</h3>
                <span className="text-[11.5px] text-slate-400">процент — переход с предыдущего шага</span>
            </div>

            <div className="mt-3 space-y-1">
                {list.map((step, index) => {
                    const value = Number(step && step.value) || 0;
                    const width = max > 0
                        ? Math.max((value / max) * 100, value > 0 ? MIN_VISIBLE_WIDTH : 0)
                        : 0;
                    const rate = step && step.step_rate;

                    return (
                        <div key={(step && step.key) || index}>
                            {rate != null && (
                                <div className="flex items-center gap-1 py-1.5 text-[11.5px] text-slate-400">
                                    {/* Стрелка перехода — иконкой, а не символом ↓:
                                        текстовые стрелки рисуются цветным эмодзи-глифом. */}
                                    <FaIcon className="fas fa-arrow-down text-[10px]" aria-hidden="true" />
                                    <span className="tabular-nums">{formatPercent(rate)}</span>
                                </div>
                            )}

                            <div className="flex items-baseline justify-between gap-3">
                                <span className="min-w-0 truncate text-[12.5px] font-medium text-slate-600">
                                    {(step && step.title) || (step && step.key) || ''}
                                </span>
                                <span className="shrink-0 text-[14px] font-semibold tabular-nums text-slate-900">
                                    {formatNumber(step && step.value)}
                                </span>
                            </div>

                            <div
                                className="mt-1 h-2 overflow-hidden rounded-full bg-slate-100"
                                role="img"
                                aria-label={`${(step && step.title) || ''}: ${formatNumber(step && step.value)}`}
                            >
                                <div
                                    className="h-full rounded-full bg-slate-400 transition-[width] duration-300"
                                    style={{ width: `${width}%` }}
                                />
                            </div>
                        </div>
                    );
                })}
            </div>
        </section>
    );
}
