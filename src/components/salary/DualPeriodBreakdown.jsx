import React, { useState } from 'react';
import FaIcon from '../common/FaIcon';

// Подробная разбивка KPI при переводе оператор↔чат-менеджер посреди месяца.
// Показывает по каждому периоду (сегменту) полный набор метрик, как у обычных
// операторов/чат-менеджеров, раскрывающийся по клику на «расчёт» (без калькулятора).
// Стиль — macOS/iOS: мягкие карточки, скруглённые плитки, плавное раскрытие.

const num = (v, def = 0) => {
    const n = Number(v);
    return Number.isFinite(n) ? n : def;
};

const money = (v) =>
    num(v).toLocaleString('ru-RU', { minimumFractionDigits: 0, maximumFractionDigits: 0 }) + ' ₸';

// Время ответа: минуты (float) -> «м:сс» либо «X.X мин»
const fmtResponse = (minutes) => {
    const m = num(minutes);
    if (m <= 0) return '—';
    const totalSec = Math.round(m * 60);
    const mm = Math.floor(totalSec / 60);
    const ss = totalSec % 60;
    return `${mm}:${String(ss).padStart(2, '0')} мин`;
};

const scoreColor = (s) => (s >= 4.8 ? 'text-green-600' : s >= 4.5 ? 'text-amber-600' : s > 0 ? 'text-red-500' : 'text-gray-400');
const perHourColor = (v, isChat) => {
    const good = isChat ? 20 : 15;
    const ok = isChat ? 10 : 5;
    return v >= good ? 'text-green-600' : v >= ok ? 'text-amber-600' : v > 0 ? 'text-red-500' : 'text-gray-400';
};
const pctColor = (p) => (p >= 100 ? 'text-green-600' : p >= 85 ? 'text-blue-600' : p > 0 ? 'text-amber-600' : 'text-gray-400');

// Небольшая плитка метрики в стиле iOS
const Tile = ({ icon, label, value, valueClass = 'text-gray-900', hint }) => (
    <div className="rounded-xl bg-white/70 backdrop-blur ring-1 ring-gray-200/70 px-3 py-2.5">
        <div className="flex items-center gap-1.5 text-[11px] font-medium text-gray-500 mb-1">
            {icon ? <FaIcon className={`${icon} text-gray-400`} /> : null}
            <span className="truncate">{label}</span>
        </div>
        <div className={`text-[17px] font-semibold leading-tight ${valueClass}`}>{value}</div>
        {hint ? <div className="text-[10px] text-gray-400 mt-0.5 truncate">{hint}</div> : null}
    </div>
);

const PeriodCard = ({ part, open, onToggle }) => {
    const isChat = part.model === 'chat';
    const pct = num(part.hoursPercentage);
    const perHour = num(part.perHour);

    return (
        <div className={`rounded-2xl border transition-all duration-200 overflow-hidden ${open ? 'border-blue-200 bg-blue-50/40 shadow-sm' : 'border-gray-200 bg-white hover:bg-gray-50'}`}>
            {/* Заголовок — кликабельный */}
            <button
                type="button"
                onClick={onToggle}
                aria-expanded={open}
                className="w-full flex items-center gap-3 px-3.5 py-3 text-left"
            >
                <span className={`w-9 h-9 rounded-full flex items-center justify-center shrink-0 ${isChat ? 'bg-indigo-100 text-indigo-600' : 'bg-sky-100 text-sky-600'}`}>
                    <FaIcon className={isChat ? 'fas fa-comments' : 'fas fa-headset'} />
                </span>
                <div className="min-w-0 flex-1">
                    <div className="text-sm font-semibold text-gray-800 truncate">
                        {part.directionName || (isChat ? 'Чат-менеджер' : 'Оператор')}
                    </div>
                    <div className="text-[11px] text-gray-500 truncate">
                        {isChat ? 'Чат-модель' : 'Операторская модель'} · дни {part.startDay}–{part.endDay}
                    </div>
                </div>
                <div className="text-right shrink-0">
                    <div className="text-[10px] uppercase tracking-wide text-gray-400">Примерная ЗП</div>
                    <div className="text-base font-bold text-green-600 whitespace-nowrap">{money(part.finalSalary)}</div>
                </div>
                <FaIcon className={`fas fa-chevron-down text-gray-400 text-xs transition-transform duration-200 shrink-0 ${open ? 'rotate-180' : ''}`} />
            </button>

            {/* Раскрытие с полными метриками */}
            <div className={`grid transition-all duration-300 ease-out ${open ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0'}`}>
                <div className="overflow-hidden">
                    <div className="px-3.5 pb-3.5 pt-1 space-y-3">
                        {/* KPI-метрики модели */}
                        <div>
                            <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-400 mb-2">Показатели KPI</div>
                            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                                <Tile
                                    icon={isChat ? 'fas fa-comments' : 'fas fa-phone'}
                                    label={isChat ? 'Чаты в час' : 'Звонки в час'}
                                    value={perHour.toFixed(2)}
                                    valueClass={perHourColor(perHour, isChat)}
                                />
                                <Tile
                                    icon={isChat ? 'fas fa-comment-dots' : 'fas fa-phone-volume'}
                                    label={isChat ? 'Всего чатов' : 'Всего звонков'}
                                    value={num(part.interactions).toFixed(0)}
                                />
                                {isChat && (
                                    <Tile
                                        icon="fas fa-star"
                                        label="Ср. оценка"
                                        value={num(part.avgScore) > 0 ? num(part.avgScore).toFixed(2) : '—'}
                                        valueClass={scoreColor(num(part.avgScore))}
                                        hint="из 5"
                                    />
                                )}
                                {isChat && (
                                    <Tile
                                        icon="fas fa-clock"
                                        label="Ср. время ответа"
                                        value={fmtResponse(part.respMinutes)}
                                    />
                                )}
                            </div>
                        </div>

                        {/* Часы и выполнение нормы */}
                        <div>
                            <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-400 mb-2">Часы за период</div>
                            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                                <Tile icon="fas fa-hourglass-half" label="Отработано" value={`${num(part.hoursWorked).toFixed(2)} ч`} />
                                <Tile icon="fas fa-bullseye" label="Норма периода" value={`${num(part.hoursNorm).toFixed(2)} ч`} />
                                <Tile
                                    icon="fas fa-chart-line"
                                    label="Выполнение"
                                    value={`${pct.toFixed(0)}%`}
                                    valueClass={pctColor(pct)}
                                    hint={`${num(part.workedDays)} раб. дн.`}
                                />
                            </div>
                        </div>

                        {/* Компоненты выплаты */}
                        <div>
                            <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-400 mb-2">Из чего складывается ЗП</div>
                            <div className="rounded-xl bg-white/70 ring-1 ring-gray-200/70 divide-y divide-gray-100">
                                <div className="flex items-center justify-between px-3.5 py-2 text-sm">
                                    <span className="text-gray-500">Баллы KPI</span>
                                    <span className="font-semibold text-gray-900">{num(part.points).toFixed(0)}</span>
                                </div>
                                <div className="flex items-center justify-between px-3.5 py-2 text-sm">
                                    <span className="text-gray-500">Оклад (часы × 700)</span>
                                    <span className="font-semibold text-gray-900">{money(part.baseSalary)}</span>
                                </div>
                                <div className="flex items-center justify-between px-3.5 py-2 text-sm">
                                    <span className="text-gray-500">
                                        Премия
                                        <span className="text-gray-400"> · коэфф. {num(part.premiumCoefficient)}</span>
                                    </span>
                                    <span className="font-semibold text-gray-900">{money(part.premiumPart)}</span>
                                </div>
                                <div className="flex items-center justify-between px-3.5 py-2 text-sm">
                                    <span className="text-gray-500">Бонусы</span>
                                    <span className="font-semibold text-gray-900">{money(part.bonuses)}</span>
                                </div>
                                <div className="flex items-center justify-between px-3.5 py-2 text-sm bg-green-50/60">
                                    <span className="font-medium text-gray-700">Итого за период</span>
                                    <span className="font-bold text-green-600">{money(part.finalSalary)}</span>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
};

const DualPeriodBreakdown = ({ parts, onOpenCalculator }) => {
    // По умолчанию раскрыт первый период — чтобы данные были видны сразу.
    const [openIdx, setOpenIdx] = useState(0);

    if (!Array.isArray(parts) || parts.length < 2) return null;

    const total = parts.reduce((sum, p) => sum + num(p.finalSalary), 0);

    return (
        <div className="space-y-3">
            <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-[12px] text-amber-800">
                <FaIcon className="fas fa-circle-info mt-0.5" />
                <span>
                    В этом месяце вы работали по двум моделям. Метрики и ЗП считаются отдельно по каждому периоду.
                    Нажмите на период, чтобы увидеть все показатели.
                </span>
            </div>

            {parts.map((part, i) => (
                <PeriodCard
                    key={i}
                    part={part}
                    open={openIdx === i}
                    onToggle={() => setOpenIdx((cur) => (cur === i ? null : i))}
                />
            ))}

            {/* Общий итог по моделям */}
            <div className="rounded-2xl border border-green-200 bg-green-50/60 px-4 py-3 flex items-center justify-between">
                <div className="flex items-center gap-2">
                    <span className="w-8 h-8 rounded-full bg-green-100 text-green-600 flex items-center justify-center">
                        <FaIcon className="fas fa-coins" />
                    </span>
                    <span className="text-sm font-semibold text-gray-700">Итого по всем моделям</span>
                </div>
                <span className="text-lg font-bold text-green-600">{money(total)}</span>
            </div>

            {typeof onOpenCalculator === 'function' && (
                <button
                    type="button"
                    onClick={onOpenCalculator}
                    className="w-full inline-flex items-center justify-center gap-2 rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-gray-600 hover:bg-gray-50 transition"
                >
                    <FaIcon className="fas fa-calculator" />
                    Открыть в общем калькуляторе
                </button>
            )}
        </div>
    );
};

export default DualPeriodBreakdown;
