import React, { useState } from 'react';
import FaIcon from '../common/FaIcon';
import {
    TEZ_NORM_HOURS,
    TEZ_LINE_OKLAD,
    TEZ_OP_OKLAD,
    OSNOVA_HOURLY_RATE,
    POTOK_HOURLY_RATE,
    VERIFICATOR_HOURLY_RATE,
    YANDEX_REG_HOURLY_RATE,
} from '../../utils/salaryFormula';

const num = (v) => {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
};
const money = (v) =>
    new Intl.NumberFormat("ru-RU", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    }).format(num(v)) + " ТГ";
const shortMoney = (v) =>
    new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(Math.round(num(v))) + " ТГ";
const simple = (v) => {
    const n = num(v);
    return Number.isInteger(n) ? n.toString() : n.toFixed(2);
};
const fmtNum = (v) =>
    new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(num(v));
const pct = (v) => `${num(v).toFixed(2)}%`;

// Кнопка «Копировать итого» — общая для всех моделей.
const CopyTotalButton = ({ value }) => {
    const [copyMsg, setCopyMsg] = useState("");
    const handleCopyTotal = async () => {
        try {
            await navigator.clipboard.writeText(String(value));
            setCopyMsg("Скопировано!");
            setTimeout(() => setCopyMsg(""), 2000);
        } catch (e) {
            setCopyMsg("Не удалось скопировать");
            setTimeout(() => setCopyMsg(""), 2000);
        }
    };
    return (
        <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3 w-full sm:w-auto">
            <button
                onClick={handleCopyTotal}
                className="w-full sm:w-auto inline-flex items-center justify-center gap-2 px-3 py-2 sm:py-1.5 bg-blue-600 text-white rounded hover:bg-blue-700 transition text-sm"
                title="Скопировать итоговую сумму"
            >
                <FaIcon className="fas fa-copy" />
                Копировать итого
            </button>
            <div className="text-sm text-green-600 font-medium min-w-[90px] text-left sm:text-right">
                {copyMsg || ""}
            </div>
        </div>
    );
};

// Плитка сводки (верхний блок «Направление / …»).
const SummaryTile = ({ title, value, tooltip }) => (
    <div>
        <div className="text-sm text-gray-600">{title}</div>
        <div className="flex items-center gap-2 mt-1">
            <div className="font-medium text-gray-800">{value}</div>
            {tooltip && (
                <div className="relative group inline-block">
                    <span className="inline-flex items-center justify-center w-5 h-5 text-xs rounded-full bg-gray-200 text-gray-700">i</span>
                    <div className="pointer-events-none opacity-0 group-hover:opacity-100 group-focus:opacity-100 transition-all duration-150 absolute right-0 top-full mt-2 w-64 max-w-[calc(100vw-3rem)] z-50">
                        <div className="bg-white border rounded shadow p-3 text-sm text-gray-700">{tooltip}</div>
                    </div>
                </div>
            )}
        </div>
    </div>
);

// Плитка компонента выплаты с тултипом-формулой.
const ComponentTile = ({ title, value, tooltip }) => (
    <div className="p-3 rounded border border-gray-50 bg-gray-50 relative">
        <div className="flex items-start justify-between">
            <div>
                <div className="text-xs text-gray-500">{title}</div>
                <div className="mt-2 text-lg font-semibold text-gray-800">{value}</div>
            </div>
            {tooltip && (
                <div className="ml-2">
                    <div className="relative group inline-block">
                        <span className="inline-flex items-center justify-center w-5 h-5 text-xs rounded-full bg-gray-200 text-gray-700">i</span>
                        <div className="pointer-events-none opacity-0 group-hover:opacity-100 transition-all duration-150 absolute -right-1 top-full mt-2 w-72 max-w-[calc(100vw-3rem)] z-50">
                            <div className="bg-white border rounded shadow p-3 text-sm text-gray-700">{tooltip}</div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    </div>
);

// Строка в группе «Детали расчёта».
const DetailRow = ({ label, value, alt, strong }) => (
    <div className={`flex items-center justify-between gap-3 px-3 sm:px-4 py-2 ${alt ? 'bg-gray-50' : 'bg-white'}`}>
        <div className={`text-sm ${strong ? 'font-semibold text-gray-800' : 'text-gray-600'}`}>{label}</div>
        <div className={`text-right ${strong ? 'font-semibold text-gray-900' : 'font-medium text-gray-800'}`}>{value}</div>
    </div>
);

const DetailGroup = ({ title, children }) => (
    <div className="mb-3">
        <div className="text-xs text-gray-500 font-medium mb-2">{title}</div>
        <div className="divide-y divide-gray-100 rounded-lg overflow-hidden border">{children}</div>
    </div>
);

// Большой итог — одинаковый у всех моделей.
const TotalBlock = ({ finalSalary, hoursNorm, hoursPercentage, extra }) => (
    <div className="bg-white p-4 rounded border-l-4 border-l-green-500 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
            <div className="text-sm text-gray-600">Итого к выплате</div>
            <div className="text-xl sm:text-2xl font-bold text-green-600 mt-1 break-words">{money(finalSalary)}</div>
        </div>
        <div className="text-sm text-gray-600 text-left sm:text-right">
            <div>Норма часов: <span className="font-medium text-gray-800 sm:ml-1">{Number.isNaN(hoursNorm) ? "-" : num(hoursNorm).toFixed(2)}</span></div>
            <div className="mt-1">Выполнение нормы: <span className="font-medium text-gray-800 sm:ml-1">{num(hoursPercentage).toFixed(2)}%</span></div>
            {extra}
        </div>
    </div>
);

const CardShell = ({ subtitle, finalSalary, children }) => (
    <div className="mt-6 p-4 sm:p-6 bg-gray-50 rounded-lg shadow-sm border border-gray-200 overflow-hidden">
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
            <div>
                <h3 className="text-lg sm:text-xl font-semibold mb-1 text-gray-800">Результат расчёта</h3>
                <p className="text-sm text-gray-500">{subtitle}</p>
            </div>
            <CopyTotalButton value={finalSalary} />
        </div>
        <div className="mt-4 space-y-4">{children}</div>
    </div>
);

/**
 * Карточка результата для моделей TEZ (Линия / ОП) — та же структура, что у СЗоВ:
 * шапка с копированием, сводка, плитки компонентов с формулами, крупный итог,
 * детали расчёта. Отличаются только составляющие выплаты.
 */
const TezCalculationResult = ({ salaryResult, label }) => {
    const isOp = salaryResult.model === 'tez_op';
    const hourlyRate = (isOp ? TEZ_OP_OKLAD : TEZ_LINE_OKLAD) / TEZ_NORM_HOURS;

    const hoursWorked = num(salaryResult.hoursWorked);
    const hoursNorm = num(salaryResult.hoursNorm);
    const hoursPercentage = num(salaryResult.hoursPercentage);
    const oklad = num(salaryResult.oklad);
    const bonuses = num(salaryResult.bonuses);
    const fines = num(salaryResult.fines);
    const withholding = num(salaryResult.withholding);
    const finalSalary = num(salaryResult.finalSalary);

    const dealPercent = num(salaryResult.dealPercent) * 100;
    const bonusDeals = num(salaryResult.bonusDeals);
    const planTarget = num(salaryResult.planTarget);
    const planFact = num(salaryResult.planFact);

    const qualityPercent = num(salaryResult.qualityPercent) * 100;
    const bonusQuality = num(salaryResult.bonusQuality);
    const seniorityPercent = num(salaryResult.seniorityPercent) * 100;
    const bonusSeniority = num(salaryResult.bonusSeniority);

    const modelLabel = label || (isOp ? 'Оператор ОП TEZ' : 'Оператор Линия TEZ');
    const componentsGridClass = isOp
        ? 'grid grid-cols-1 sm:grid-cols-3 gap-3'
        : 'grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3';

    return (
        <CardShell
            subtitle={isOp ? 'Сводка по успешкам и выплатам' : 'Сводка по качеству, стажу и выплатам'}
            finalSalary={finalSalary}
        >
            {/* Сводка */}
            <div className="bg-white p-4 rounded border border-gray-100">
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                    <SummaryTile title="Направление" value={modelLabel} />
                    {isOp ? (
                        <>
                            <SummaryTile
                                title="План успешек"
                                value={planTarget > 0 ? simple(planTarget) : '—'}
                                tooltip={(
                                    <>
                                        <div className="font-medium mb-1">Индивидуальный план</div>
                                        <div className="text-xs text-gray-600">
                                            Считается от плана отдела на 1 FTE с учётом ставки, переработки и признака новичка.
                                        </div>
                                    </>
                                )}
                            />
                            <SummaryTile title="Факт успешек" value={simple(planFact)} />
                        </>
                    ) : (
                        <>
                            <SummaryTile
                                title="Бонус за качество"
                                value={pct(qualityPercent)}
                                tooltip={(
                                    <>
                                        <div className="font-medium mb-1">Доля к окладу</div>
                                        <div className="text-xs text-gray-600">
                                            96–100 → 100%, 86–95 → 80%, 76–85 → 60%, 70–75 → 40%, 0–69 → 20%
                                        </div>
                                    </>
                                )}
                            />
                            <SummaryTile
                                title="Надбавка за стаж"
                                value={pct(seniorityPercent)}
                                tooltip={(
                                    <>
                                        <div className="font-medium mb-1">Доля к окладу с бонусом</div>
                                        <div className="text-xs text-gray-600">
                                            18+ мес → 30%, 13–17 → 25%, 10–12 → 20%, 6–9 → 15%, 3–5 → 10%, 0–2 → 0%
                                        </div>
                                    </>
                                )}
                            />
                        </>
                    )}
                </div>
            </div>

            {/* Компоненты выплаты */}
            <div className="bg-white p-4 rounded border border-gray-100">
                <h4 className="text-sm font-semibold text-gray-700 mb-3">Компоненты выплаты</h4>
                <div className={componentsGridClass}>
                    <ComponentTile
                        title="Оклад (базовая часть)"
                        value={money(oklad)}
                        tooltip={(
                            <>
                                <div className="font-medium mb-1">Формула</div>
                                <div className="text-xs text-gray-600 mb-2">
                                    Оклад = {isOp ? TEZ_OP_OKLAD.toLocaleString('ru-RU') : TEZ_LINE_OKLAD.toLocaleString('ru-RU')} ÷ {TEZ_NORM_HOURS} × отработанные часы
                                </div>
                                <div className="text-sm font-semibold">
                                    Подстановка: {fmtNum(hourlyRate)} × {fmtNum(hoursWorked)} = <b>{fmtNum(oklad)}</b>
                                </div>
                            </>
                        )}
                    />
                    {isOp ? (
                        <ComponentTile
                            title="Бонус за успешки"
                            value={money(bonusDeals)}
                            tooltip={(
                                <>
                                    <div className="font-medium mb-1">Формула</div>
                                    <div className="text-xs text-gray-600 mb-2">Бонус = Оклад × % сделок, где % сделок = факт ÷ план</div>
                                    <div className="text-sm">
                                        <div>Факт успешек: <b>{simple(planFact)}</b></div>
                                        <div>План успешек: <b>{planTarget > 0 ? simple(planTarget) : '—'}</b></div>
                                        <div>% сделок: <b>{pct(dealPercent)}</b></div>
                                        <div className="mt-2 font-semibold">
                                            Подстановка: {fmtNum(oklad)} × {(num(salaryResult.dealPercent)).toFixed(4)} = <b>{fmtNum(bonusDeals)}</b>
                                        </div>
                                    </div>
                                </>
                            )}
                        />
                    ) : (
                        <>
                            <ComponentTile
                                title="Бонус за качество"
                                value={money(bonusQuality)}
                                tooltip={(
                                    <>
                                        <div className="font-medium mb-1">Формула</div>
                                        <div className="text-xs text-gray-600 mb-2">Бонус = Оклад × доля за качество</div>
                                        <div className="text-sm font-semibold">
                                            Подстановка: {fmtNum(oklad)} × {(num(salaryResult.qualityPercent)).toFixed(2)} = <b>{fmtNum(bonusQuality)}</b>
                                        </div>
                                    </>
                                )}
                            />
                            <ComponentTile
                                title="Надбавка за стаж"
                                value={money(bonusSeniority)}
                                tooltip={(
                                    <>
                                        <div className="font-medium mb-1">Формула</div>
                                        <div className="text-xs text-gray-600 mb-2">Надбавка = (Оклад + бонус за качество) × доля за стаж</div>
                                        <div className="text-sm font-semibold">
                                            Подстановка: ({fmtNum(oklad)} + {fmtNum(bonusQuality)}) × {(num(salaryResult.seniorityPercent)).toFixed(2)} = <b>{fmtNum(bonusSeniority)}</b>
                                        </div>
                                    </>
                                )}
                            />
                        </>
                    )}
                    <ComponentTile title="Бонусы" value={shortMoney(bonuses)} />
                </div>
            </div>

            <TotalBlock
                finalSalary={finalSalary}
                hoursNorm={hoursNorm}
                hoursPercentage={hoursPercentage}
                extra={isOp ? (
                    <div className="mt-1">% сделок: <span className="font-medium text-gray-800 sm:ml-1">{pct(dealPercent)}</span></div>
                ) : null}
            />

            {/* Детали расчёта */}
            <div className="bg-white p-4 rounded border border-gray-100">
                <h4 className="text-sm font-semibold text-gray-700 mb-3">Детали расчёта</h4>

                <DetailGroup title={isOp ? 'Часы & план' : 'Часы & качество'}>
                    <DetailRow label="Норма часов" value={hoursNorm.toFixed(2)} />
                    <DetailRow alt label="Отработанные часы" value={hoursWorked.toFixed(2)} />
                    <DetailRow label="Выполнение нормы" value={pct(hoursPercentage)} />
                    {isOp ? (
                        <>
                            <DetailRow alt label="План успешек" value={planTarget > 0 ? simple(planTarget) : '—'} />
                            <DetailRow label="Факт успешек" value={simple(planFact)} />
                            <DetailRow alt label="% сделок" value={pct(dealPercent)} />
                        </>
                    ) : (
                        <>
                            <DetailRow alt label="Бонус за качество" value={pct(qualityPercent)} />
                            <DetailRow label="Надбавка за стаж" value={pct(seniorityPercent)} />
                        </>
                    )}
                </DetailGroup>

                <DetailGroup title="Компоненты выплаты">
                    <DetailRow label="Оклад" value={money(oklad)} />
                    {isOp ? (
                        <DetailRow alt label="Бонус за успешки" value={money(bonusDeals)} />
                    ) : (
                        <>
                            <DetailRow alt label="Бонус за качество" value={money(bonusQuality)} />
                            <DetailRow label="Надбавка за стаж" value={money(bonusSeniority)} />
                        </>
                    )}
                    <DetailRow alt={isOp} label="Бонусы" value={`+ ${money(bonuses)}`} />
                    <DetailRow alt={!isOp} label="Штрафы" value={`− ${money(fines)}`} />
                    <DetailRow alt={isOp} label="Удержано 50%" value={`− ${money(withholding)}`} />
                    <DetailRow strong label="Итого к выплате" value={money(finalSalary)} />
                </DetailGroup>
            </div>
        </CardShell>
    );
};

/**
 * Карточка результата для модели ОП «Основа»: постоянная часть за часы плюс
 * переменная за сделки, из которой удерживают процент по качеству звонков.
 * Формулы — calculateOsnovaSalary (таблица владельца «Основа_калькулятор зарплаты»).
 */
const OsnovaCalculationResult = ({ salaryResult, label }) => {
    const hoursWorked = num(salaryResult.hoursWorked);
    const hoursNorm = num(salaryResult.hoursNorm);
    const hoursPercentage = num(salaryResult.hoursPercentage);
    const oklad = num(salaryResult.oklad);
    const deals = num(salaryResult.deals);
    const planTarget = num(salaryResult.planTarget);
    const planPercent = num(salaryResult.planPercent) * 100;
    const dealPrice = num(salaryResult.dealPrice);
    const bonusDeals = num(salaryResult.bonusDeals);
    const quality = num(salaryResult.quality);
    const withholdRate = num(salaryResult.qualityWithholdRate) * 100;
    const qualityWithheld = num(salaryResult.qualityWithheld);
    const fines = num(salaryResult.fines);
    const bonuses = num(salaryResult.bonuses);
    const finalSalary = num(salaryResult.finalSalary);
    const hourlyRate = num(salaryResult.hourlyRate) || OSNOVA_HOURLY_RATE;

    return (
        <CardShell subtitle="Сводка по часам, сделкам и качеству звонков" finalSalary={finalSalary}>
            {/* Сводка */}
            <div className="bg-white p-4 rounded border border-gray-100">
                <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
                    <SummaryTile title="Направление" value={label || 'Оператор ОП Основа'} />
                    <SummaryTile
                        title="План сделок"
                        value={planTarget > 0 ? simple(Math.round(planTarget * 10) / 10) : '—'}
                        tooltip={(
                            <>
                                <div className="font-medium mb-1">Индивидуальный план</div>
                                <div className="text-xs text-gray-600">
                                    План на 1 FTE ÷ норму 1 FTE × отработанные часы.
                                    {salaryResult.nightShift ? ' Ночная смена — план вдвое меньше.' : ''}
                                    {salaryResult.isNewbie ? ' Новичок — ×0,8.' : ''}
                                </div>
                            </>
                        )}
                    />
                    <SummaryTile title="Факт сделок" value={simple(deals)} />
                    <SummaryTile
                        title="Цена сделки"
                        value={shortMoney(dealPrice)}
                        tooltip={(
                            <>
                                <div className="font-medium mb-1">Ступени по % плана</div>
                                <div className="text-xs text-gray-600">
                                    &lt;50% → 100 ₸, 50–80% → 200 ₸, 80–100% → 400 ₸, 100–120% → 450 ₸,
                                    120–140% → 500 ₸, от 140% → 600 ₸
                                </div>
                            </>
                        )}
                    />
                </div>
            </div>

            {/* Компоненты выплаты */}
            <div className="bg-white p-4 rounded border border-gray-100">
                <h4 className="text-sm font-semibold text-gray-700 mb-3">Компоненты выплаты</h4>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                    <ComponentTile
                        title="Постоянная часть (часы)"
                        value={money(oklad)}
                        tooltip={(
                            <>
                                <div className="font-medium mb-1">Формула</div>
                                <div className="text-xs text-gray-600 mb-2">
                                    Постоянная часть = отработанные часы × {hourlyRate} ₸/ч
                                </div>
                                <div className="text-sm font-semibold">
                                    Подстановка: {fmtNum(hoursWorked)} × {hourlyRate} = <b>{fmtNum(oklad)}</b>
                                </div>
                            </>
                        )}
                    />
                    <ComponentTile
                        title="Бонус за сделки"
                        value={money(bonusDeals)}
                        tooltip={(
                            <>
                                <div className="font-medium mb-1">Формула</div>
                                <div className="text-xs text-gray-600 mb-2">Бонус = сделки × цена сделки по % плана</div>
                                <div className="text-sm">
                                    <div>Факт сделок: <b>{simple(deals)}</b></div>
                                    <div>План сделок: <b>{planTarget > 0 ? simple(Math.round(planTarget * 10) / 10) : '—'}</b></div>
                                    <div>% плана: <b>{pct(planPercent)}</b></div>
                                    <div className="mt-2 font-semibold">
                                        Подстановка: {simple(deals)} × {fmtNum(dealPrice)} = <b>{fmtNum(bonusDeals)}</b>
                                    </div>
                                </div>
                            </>
                        )}
                    />
                    <ComponentTile
                        title="Удержано за качество"
                        value={`− ${money(qualityWithheld)}`}
                        tooltip={(
                            <>
                                <div className="font-medium mb-1">Удержание с бонуса</div>
                                <div className="text-xs text-gray-600 mb-2">
                                    96–100% → 0%, 91–95% → 10%, 86–90% → 20%, 80–85% → 30%, 74–79% → 40%, ниже 74% → 50%
                                </div>
                                <div className="text-sm">
                                    <div>Качество звонков: <b>{pct(quality)}</b></div>
                                    <div className="mt-2 font-semibold">
                                        Подстановка: {fmtNum(bonusDeals)} × {(num(salaryResult.qualityWithholdRate)).toFixed(2)} = <b>{fmtNum(qualityWithheld)}</b>
                                    </div>
                                </div>
                            </>
                        )}
                    />
                    <ComponentTile title="Премии" value={shortMoney(bonuses)} />
                </div>
            </div>

            <TotalBlock
                finalSalary={finalSalary}
                hoursNorm={hoursNorm}
                hoursPercentage={hoursPercentage}
                extra={(
                    <div className="mt-1">% плана: <span className="font-medium text-gray-800 sm:ml-1">{pct(planPercent)}</span></div>
                )}
            />

            {/* Детали расчёта */}
            <div className="bg-white p-4 rounded border border-gray-100">
                <h4 className="text-sm font-semibold text-gray-700 mb-3">Детали расчёта</h4>

                <DetailGroup title="Часы & план">
                    <DetailRow label="Норма часов" value={hoursNorm.toFixed(2)} />
                    <DetailRow alt label="Отработанные часы" value={hoursWorked.toFixed(2)} />
                    <DetailRow label="Выполнение нормы" value={pct(hoursPercentage)} />
                    <DetailRow
                        alt
                        label={`План на 1 FTE${salaryResult.nightShift ? ' (ночь)' : ''}`}
                        value={num(salaryResult.planPerFte) > 0 ? simple(Math.round(num(salaryResult.planPerFte) * 10) / 10) : '—'}
                    />
                    <DetailRow label="Норма часов на 1 FTE" value={simple(num(salaryResult.normHoursFte))} />
                    <DetailRow alt label={`План сделок${salaryResult.isNewbie ? ' (новичок ×0,8)' : ''}`} value={planTarget > 0 ? simple(Math.round(planTarget * 10) / 10) : '—'} />
                    <DetailRow label="Факт сделок" value={simple(deals)} />
                    <DetailRow alt label="% плана" value={pct(planPercent)} />
                </DetailGroup>

                <DetailGroup title="Качество звонков">
                    <DetailRow label="Качество" value={pct(quality)} />
                    <DetailRow alt label="Удержание с бонуса" value={pct(withholdRate)} />
                </DetailGroup>

                <DetailGroup title="Компоненты выплаты">
                    <DetailRow label="Постоянная часть (часы)" value={money(oklad)} />
                    <DetailRow alt label={`Бонус за сделки (${fmtNum(dealPrice)} × ${simple(deals)})`} value={money(bonusDeals)} />
                    <DetailRow label="Удержано за качество" value={`− ${money(qualityWithheld)}`} />
                    <DetailRow alt label="Штрафы" value={`− ${money(fines)}`} />
                    <DetailRow label="Премии" value={`+ ${money(bonuses)}`} />
                    <DetailRow strong label="Итого к выплате" value={money(finalSalary)} />
                </DetailGroup>
            </div>
        </CardShell>
    );
};

/**
 * Карточка результата для модели ОП «Поток»: часы по ставке плюс два потока
 * продаж со своими ступенями цены сделки. Качество на выплату не влияет.
 * Формулы — calculatePotokSalary (таблица владельца «Поток_калькулятор_зарплаты»).
 */
const PotokCalculationResult = ({ salaryResult, label }) => {
    const hoursWorked = num(salaryResult.hoursWorked);
    const hoursNorm = num(salaryResult.hoursNorm);
    const hoursPercentage = num(salaryResult.hoursPercentage);
    const hourlyRate = num(salaryResult.hourlyRate) || POTOK_HOURLY_RATE;
    const oklad = num(salaryResult.oklad);
    const churnSales = num(salaryResult.churnSales);
    const focusSales = num(salaryResult.focusSales);
    const totalSales = num(salaryResult.totalSales);
    const planTarget = num(salaryResult.planTarget);
    const planPercent = num(salaryResult.planPercent) * 100;
    const churnPrice = num(salaryResult.churnPrice);
    const focusPrice = num(salaryResult.focusPrice);
    const bonusChurn = num(salaryResult.bonusChurn);
    const bonusFocus = num(salaryResult.bonusFocus);
    const fines = num(salaryResult.fines);
    const withholding = num(salaryResult.withholding);
    const finalSalary = num(salaryResult.finalSalary);
    const planValue = planTarget > 0 ? simple(Math.round(planTarget * 10) / 10) : '—';

    return (
        <CardShell subtitle="Сводка по часам и двум потокам продаж" finalSalary={finalSalary}>
            {/* Сводка */}
            <div className="bg-white p-4 rounded border border-gray-100">
                <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
                    <SummaryTile title="Направление" value={label || 'Оператор ОП Поток'} />
                    <SummaryTile
                        title="План продаж"
                        value={planValue}
                        tooltip={(
                            <>
                                <div className="font-medium mb-1">Индивидуальный план</div>
                                <div className="text-xs text-gray-600">
                                    План на 1 FTE ÷ норму 1 FTE × отработанные часы.
                                    {salaryResult.isNewbie ? ' Новичок — ×0,8.' : ''}
                                </div>
                            </>
                        )}
                    />
                    <SummaryTile
                        title="Факт продаж"
                        value={simple(totalSales)}
                        tooltip={(
                            <>
                                <div className="font-medium mb-1">Оба потока вместе</div>
                                <div className="text-xs text-gray-600">
                                    Отток {simple(churnSales)} + Фокус {simple(focusSales)}. Процент плана считается
                                    по сумме, а цена сделки — по своей ступени для каждого потока.
                                </div>
                            </>
                        )}
                    />
                    <SummaryTile title="% плана" value={pct(planPercent)} />
                </div>
            </div>

            {/* Компоненты выплаты */}
            <div className="bg-white p-4 rounded border border-gray-100">
                <h4 className="text-sm font-semibold text-gray-700 mb-3">Компоненты выплаты</h4>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                    <ComponentTile
                        title="Сумма за часы"
                        value={money(oklad)}
                        tooltip={(
                            <>
                                <div className="font-medium mb-1">Формула</div>
                                <div className="text-xs text-gray-600 mb-2">Часы × ставка {fmtNum(hourlyRate)} ₸/ч</div>
                                <div className="text-sm font-semibold">
                                    Подстановка: {fmtNum(hoursWorked)} × {fmtNum(hourlyRate)} = <b>{fmtNum(oklad)}</b>
                                </div>
                            </>
                        )}
                    />
                    <ComponentTile
                        title={`Бонус «Отток» (${fmtNum(churnPrice)} ₸)`}
                        value={money(bonusChurn)}
                        tooltip={(
                            <>
                                <div className="font-medium mb-1">Ступени по % плана</div>
                                <div className="text-xs text-gray-600 mb-2">
                                    &lt;70% → 200 ₸, 70–80% → 300 ₸, 80–90% → 400 ₸, 90–100% → 450 ₸,
                                    100–120% → 500 ₸, 120–150% → 550 ₸, 150–200% → 600 ₸, от 200% → 800 ₸
                                </div>
                                <div className="text-sm font-semibold">
                                    Подстановка: {simple(churnSales)} × {fmtNum(churnPrice)} = <b>{fmtNum(bonusChurn)}</b>
                                </div>
                            </>
                        )}
                    />
                    <ComponentTile
                        title={`Бонус «Фокус» (${fmtNum(focusPrice)} ₸)`}
                        value={money(bonusFocus)}
                        tooltip={(
                            <>
                                <div className="font-medium mb-1">Ступени по % плана</div>
                                <div className="text-xs text-gray-600 mb-2">
                                    &lt;70% → 700 ₸, 70% → 750 ₸, 80% → 800 ₸, 90% → 850 ₸, 100% → 900 ₸,
                                    120% → 950 ₸, 140% → 1000 ₸, 160% → 1100 ₸, 180% → 1200 ₸,
                                    200% → 1400 ₸, 220% → 1600 ₸
                                </div>
                                <div className="text-sm font-semibold">
                                    Подстановка: {simple(focusSales)} × {fmtNum(focusPrice)} = <b>{fmtNum(bonusFocus)}</b>
                                </div>
                            </>
                        )}
                    />
                </div>
            </div>

            <TotalBlock
                finalSalary={finalSalary}
                hoursNorm={hoursNorm}
                hoursPercentage={hoursPercentage}
                extra={(
                    <div className="mt-1">% плана: <span className="font-medium text-gray-800 sm:ml-1">{pct(planPercent)}</span></div>
                )}
            />

            {/* Детали расчёта */}
            <div className="bg-white p-4 rounded border border-gray-100">
                <h4 className="text-sm font-semibold text-gray-700 mb-3">Детали расчёта</h4>

                <DetailGroup title="Часы & план">
                    <DetailRow label="Норма часов" value={hoursNorm.toFixed(2)} />
                    <DetailRow alt label="Отработанные часы" value={hoursWorked.toFixed(2)} />
                    <DetailRow label="Выполнение нормы" value={pct(hoursPercentage)} />
                    <DetailRow alt label="Ставка, ₸/час" value={money(hourlyRate)} />
                    <DetailRow label="План на 1 FTE" value={num(salaryResult.planPerFte) > 0 ? simple(num(salaryResult.planPerFte)) : '—'} />
                    <DetailRow alt label="Норма часов на 1 FTE" value={simple(num(salaryResult.normHoursFte))} />
                    <DetailRow label={`План продаж${salaryResult.isNewbie ? ' (новичок ×0,8)' : ''}`} value={planValue} />
                    <DetailRow alt label="% плана" value={pct(planPercent)} />
                </DetailGroup>

                <DetailGroup title="Продажи по потокам">
                    <DetailRow label="Отток, шт" value={simple(churnSales)} />
                    <DetailRow alt label="Цена за продажу «Отток»" value={money(churnPrice)} />
                    <DetailRow label="Фокус, шт" value={simple(focusSales)} />
                    <DetailRow alt label="Цена за продажу «Фокус»" value={money(focusPrice)} />
                    <DetailRow label="Итог продаж, шт" value={simple(totalSales)} />
                </DetailGroup>

                <DetailGroup title="Компоненты выплаты">
                    <DetailRow label="Сумма за часы" value={money(oklad)} />
                    <DetailRow alt label="Бонус «Отток»" value={money(bonusChurn)} />
                    <DetailRow label="Бонус «Фокус»" value={money(bonusFocus)} />
                    <DetailRow alt label="Штрафы" value={`− ${money(fines)}`} />
                    <DetailRow label="Удержано 50%" value={`− ${money(withholding)}`} />
                    <DetailRow strong label="Итого к выплате" value={money(finalSalary)} />
                </DetailGroup>
            </div>
        </CardShell>
    );
};

/**
 * Карточка результата для модели ОП «Верификатор»: оклад по ставке плюс два
 * бонуса, оба считаются процентом ОТ ОКЛАДА — за качество (прямой процент) и за
 * выполнение плана продаж (ступень 0/5/10/20/30%).
 * Формулы — calculateVerificatorSalary (лист «Верик» таблицы владельца,
 * сверено с презентацией «Мотивационная схема верификатора»).
 */
const VerificatorCalculationResult = ({ salaryResult, label }) => {
    const hoursWorked = num(salaryResult.hoursWorked);
    const hoursNorm = num(salaryResult.hoursNorm);
    const hoursPercentage = num(salaryResult.hoursPercentage);
    const hourlyRate = num(salaryResult.hourlyRate) || VERIFICATOR_HOURLY_RATE;
    const oklad = num(salaryResult.oklad);
    const sales = num(salaryResult.sales);
    const planTarget = num(salaryResult.planTarget);
    const planPercent = num(salaryResult.planPercent) * 100;
    const planBonusPercent = num(salaryResult.planBonusPercent);
    const totalBonusPercent = num(salaryResult.totalBonusPercent);
    const quality = num(salaryResult.quality);
    const bonusQuality = num(salaryResult.bonusQuality);
    const bonusPlan = num(salaryResult.bonusPlan);
    const bonusTotal = num(salaryResult.bonusTotal);
    const promoFines = num(salaryResult.promoFines);
    const fines = num(salaryResult.fines);
    const finalSalary = num(salaryResult.finalSalary);
    const planValue = planTarget > 0 ? simple(Math.round(planTarget * 10) / 10) : '—';

    return (
        <CardShell subtitle="Сводка по часам, качеству и плану продаж" finalSalary={finalSalary}>
            {/* Сводка */}
            <div className="bg-white p-4 rounded border border-gray-100">
                <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
                    <SummaryTile title="Направление" value={label || 'Оператор ОП Верификатор'} />
                    <SummaryTile
                        title="План продаж"
                        value={planValue}
                        tooltip={(
                            <>
                                <div className="font-medium mb-1">Индивидуальный план</div>
                                <div className="text-xs text-gray-600">
                                    План на 1 FTE ÷ норму 1 FTE × отработанные часы.
                                    {salaryResult.nightShift ? ' Ночная смена — план вдвое меньше.' : ''}
                                    {salaryResult.isNewbie ? ' Новичок — ×0,8.' : ''}
                                </div>
                            </>
                        )}
                    />
                    <SummaryTile title="Факт продаж" value={simple(sales)} />
                    <SummaryTile
                        title="Итого баллов"
                        value={`${simple(Math.round(totalBonusPercent * 100) / 100)}%`}
                        tooltip={(
                            <>
                                <div className="font-medium mb-1">Баллы = качество + премия за план</div>
                                <div className="text-xs text-gray-600">
                                    Качество {pct(quality)} + план {simple(planBonusPercent)}% — бонус берётся
                                    от оклада по этой сумме.
                                </div>
                            </>
                        )}
                    />
                </div>
            </div>

            {/* Компоненты выплаты */}
            <div className="bg-white p-4 rounded border border-gray-100">
                <h4 className="text-sm font-semibold text-gray-700 mb-3">Компоненты выплаты</h4>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                    <ComponentTile
                        title="Оклад (часы)"
                        value={money(oklad)}
                        tooltip={(
                            <>
                                <div className="font-medium mb-1">Формула</div>
                                <div className="text-xs text-gray-600 mb-2">
                                    Оклад = отработанные часы × {hourlyRate} ₸/ч
                                </div>
                                <div className="text-sm font-semibold">
                                    Подстановка: {fmtNum(hoursWorked)} × {hourlyRate} = <b>{fmtNum(oklad)}</b>
                                </div>
                            </>
                        )}
                    />
                    <ComponentTile
                        title="Бонус за качество"
                        value={money(bonusQuality)}
                        tooltip={(
                            <>
                                <div className="font-medium mb-1">Формула</div>
                                <div className="text-xs text-gray-600 mb-2">
                                    Бонус за качество = оклад × % качества. Ступеней нет: сколько процентов
                                    качества, столько процентов оклада.
                                </div>
                                <div className="text-sm font-semibold">
                                    Подстановка: {fmtNum(oklad)} × {pct(quality)} = <b>{fmtNum(bonusQuality)}</b>
                                </div>
                            </>
                        )}
                    />
                    <ComponentTile
                        title="Бонус за план"
                        value={money(bonusPlan)}
                        tooltip={(
                            <>
                                <div className="font-medium mb-1">Ступени по % плана</div>
                                <div className="text-xs text-gray-600 mb-2">
                                    0–79,9% → 0%, 80–89,9% → 5%, 90–99,9% → 10%, 100–109,9% → 20%, от 110% → 30%
                                </div>
                                <div className="text-sm">
                                    <div>План продаж: <b>{planValue}</b></div>
                                    <div>Факт продаж: <b>{simple(sales)}</b></div>
                                    <div>% плана: <b>{pct(planPercent)}</b></div>
                                    <div className="mt-2 font-semibold">
                                        Подстановка: {fmtNum(oklad)} × {simple(planBonusPercent)}% = <b>{fmtNum(bonusPlan)}</b>
                                    </div>
                                </div>
                            </>
                        )}
                    />
                    <ComponentTile
                        title="Штрафы"
                        value={`− ${shortMoney(promoFines + fines)}`}
                        tooltip={(
                            <>
                                <div className="font-medium mb-1">Две отдельные суммы</div>
                                <div className="text-sm">
                                    <div>Штраф за акции: <b>{fmtNum(promoFines)}</b></div>
                                    <div>Штрафы: <b>{fmtNum(fines)}</b></div>
                                </div>
                            </>
                        )}
                    />
                </div>
            </div>

            <TotalBlock
                finalSalary={finalSalary}
                hoursNorm={hoursNorm}
                hoursPercentage={hoursPercentage}
                extra={(
                    <div className="mt-1">% плана: <span className="font-medium text-gray-800 sm:ml-1">{pct(planPercent)}</span></div>
                )}
            />

            {/* Детали расчёта */}
            <div className="bg-white p-4 rounded border border-gray-100">
                <h4 className="text-sm font-semibold text-gray-700 mb-3">Детали расчёта</h4>

                <DetailGroup title="Часы & план">
                    <DetailRow label="Норма часов" value={hoursNorm.toFixed(2)} />
                    <DetailRow alt label="Отработанные часы" value={hoursWorked.toFixed(2)} />
                    <DetailRow label="Выполнение нормы" value={pct(hoursPercentage)} />
                    <DetailRow alt label="Ставка, ₸/час" value={money(hourlyRate)} />
                    <DetailRow
                        label={`План на 1 FTE${salaryResult.nightShift ? ' (ночь)' : ''}`}
                        value={num(salaryResult.planPerFte) > 0 ? simple(Math.round(num(salaryResult.planPerFte) * 10) / 10) : '—'}
                    />
                    <DetailRow alt label="Норма часов на 1 FTE" value={simple(num(salaryResult.normHoursFte))} />
                    <DetailRow label={`План продаж${salaryResult.isNewbie ? ' (новичок ×0,8)' : ''}`} value={planValue} />
                    <DetailRow alt label="Факт продаж" value={simple(sales)} />
                    <DetailRow label="% плана" value={pct(planPercent)} />
                </DetailGroup>

                <DetailGroup title="Баллы бонуса">
                    <DetailRow label="Качество" value={pct(quality)} />
                    <DetailRow alt label="Премия за план" value={`${simple(planBonusPercent)}%`} />
                    <DetailRow strong label="Итого баллов" value={`${simple(Math.round(totalBonusPercent * 100) / 100)}%`} />
                </DetailGroup>

                <DetailGroup title="Компоненты выплаты">
                    <DetailRow label="Оклад (часы)" value={money(oklad)} />
                    <DetailRow alt label="Бонус за качество" value={money(bonusQuality)} />
                    <DetailRow label="Бонус за план" value={money(bonusPlan)} />
                    <DetailRow alt label="Сумма бонусов" value={money(bonusTotal)} />
                    <DetailRow label="Штраф за акции" value={`− ${money(promoFines)}`} />
                    <DetailRow alt label="Штрафы" value={`− ${money(fines)}`} />
                    <DetailRow strong label="Итого к выплате" value={money(finalSalary)} />
                </DetailGroup>
            </div>
        </CardShell>
    );
};

/**
 * Карточка результата для модели ОП «Яндекс Регистрация»: оклад по ставке плюс
 * бонус за личные успешные заявки. Цена успешки — ступень по выполнению плана
 * конверсии ГРУППОЙ, из бонуса удерживают процент по ЛИЧНОМУ качеству звонков.
 * Формулы — calculateYandexRegSalary (KPI.xlsx, лист «ЯР»).
 */
const YandexRegCalculationResult = ({ salaryResult, label }) => {
    const hoursWorked = num(salaryResult.hoursWorked);
    const hoursNorm = num(salaryResult.hoursNorm);
    const hoursPercentage = num(salaryResult.hoursPercentage);
    const hourlyRate = num(salaryResult.hourlyRate) || YANDEX_REG_HOURLY_RATE;
    const oklad = num(salaryResult.oklad);
    const groupRequests = num(salaryResult.groupRequests);
    const groupSuccesses = num(salaryResult.groupSuccesses);
    const factConversion = num(salaryResult.factConversion) * 100;
    const targetConversion = num(salaryResult.targetConversion) * 100;
    const planPercent = num(salaryResult.planPercent) * 100;
    const deals = num(salaryResult.deals);
    const dealPrice = num(salaryResult.dealPrice);
    const bonusDeals = num(salaryResult.bonusDeals);
    const quality = num(salaryResult.quality);
    const withholdRate = num(salaryResult.qualityWithholdRate) * 100;
    const qualityWithheld = num(salaryResult.qualityWithheld);
    const fines = num(salaryResult.fines);
    const bonuses = num(salaryResult.bonuses);
    const finalSalary = num(salaryResult.finalSalary);

    return (
        <CardShell subtitle="Сводка по часам, конверсии группы и качеству звонков" finalSalary={finalSalary}>
            {/* Сводка */}
            <div className="bg-white p-4 rounded border border-gray-100">
                <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
                    <SummaryTile title="Направление" value={label || 'Оператор ОП Яндекс Регистрация'} />
                    <SummaryTile
                        title="Конверсия группы"
                        value={groupRequests > 0 ? pct(factConversion) : '—'}
                        tooltip={(
                            <>
                                <div className="font-medium mb-1">Факт. конверсия</div>
                                <div className="text-xs text-gray-600">
                                    Успешные заявки группы ÷ поступившие заявки группы. Показатель общий —
                                    он задаёт цену успешки всем операторам ЯР.
                                </div>
                            </>
                        )}
                    />
                    <SummaryTile title="Мои успешки" value={simple(deals)} />
                    <SummaryTile
                        title="Цена успешки"
                        value={shortMoney(dealPrice)}
                        tooltip={(
                            <>
                                <div className="font-medium mb-1">Ступени по % плана конверсии</div>
                                <div className="text-xs text-gray-600">
                                    до 80% → 0 ₸, 80–90% → 200 ₸, 90–100% → 240 ₸, 100–110% → 280 ₸,
                                    110–120% → 320 ₸, от 120% → 360 ₸
                                </div>
                            </>
                        )}
                    />
                </div>
            </div>

            {/* Компоненты выплаты */}
            <div className="bg-white p-4 rounded border border-gray-100">
                <h4 className="text-sm font-semibold text-gray-700 mb-3">Компоненты выплаты</h4>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                    <ComponentTile
                        title="Оклад (часы)"
                        value={money(oklad)}
                        tooltip={(
                            <>
                                <div className="font-medium mb-1">Формула</div>
                                <div className="text-xs text-gray-600 mb-2">
                                    Оклад = отработанные часы × {hourlyRate} ₸/ч
                                </div>
                                <div className="text-sm font-semibold">
                                    Подстановка: {fmtNum(hoursWorked)} × {hourlyRate} = <b>{fmtNum(oklad)}</b>
                                </div>
                            </>
                        )}
                    />
                    <ComponentTile
                        title="Бонус за успешки"
                        value={money(bonusDeals)}
                        tooltip={(
                            <>
                                <div className="font-medium mb-1">Формула</div>
                                <div className="text-xs text-gray-600 mb-2">
                                    Бонус = мои успешные заявки × цену успешки по % плана конверсии группы
                                </div>
                                <div className="text-sm">
                                    <div>Конверсия группы: <b>{groupRequests > 0 ? pct(factConversion) : '—'}</b></div>
                                    <div>Целевая конверсия: <b>{pct(targetConversion)}</b></div>
                                    <div>% плана: <b>{pct(planPercent)}</b></div>
                                    <div className="mt-2 font-semibold">
                                        Подстановка: {simple(deals)} × {fmtNum(dealPrice)} = <b>{fmtNum(bonusDeals)}</b>
                                    </div>
                                </div>
                            </>
                        )}
                    />
                    <ComponentTile
                        title="Удержано за качество"
                        value={`− ${money(qualityWithheld)}`}
                        tooltip={(
                            <>
                                <div className="font-medium mb-1">Удержание с бонуса</div>
                                <div className="text-xs text-gray-600 mb-2">
                                    96–100% → 0%, 91–95% → 10%, 86–90% → 20%, 80–85% → 30%, 75–79% → 40%,
                                    74% и ниже → 50%
                                </div>
                                <div className="text-sm">
                                    <div>Качество звонков: <b>{pct(quality)}</b> — показатель личный</div>
                                    <div className="mt-2 font-semibold">
                                        Подстановка: {fmtNum(bonusDeals)} × {num(salaryResult.qualityWithholdRate).toFixed(2)} = <b>{fmtNum(qualityWithheld)}</b>
                                    </div>
                                </div>
                            </>
                        )}
                    />
                    <ComponentTile title="Премии" value={shortMoney(bonuses)} />
                </div>
            </div>

            <TotalBlock
                finalSalary={finalSalary}
                hoursNorm={hoursNorm}
                hoursPercentage={hoursPercentage}
                extra={(
                    <div className="mt-1">% плана конверсии: <span className="font-medium text-gray-800 sm:ml-1">{pct(planPercent)}</span></div>
                )}
            />

            {/* Детали расчёта */}
            <div className="bg-white p-4 rounded border border-gray-100">
                <h4 className="text-sm font-semibold text-gray-700 mb-3">Детали расчёта</h4>

                <DetailGroup title="Часы">
                    <DetailRow label="Норма часов" value={hoursNorm.toFixed(2)} />
                    <DetailRow alt label="Отработанные часы" value={hoursWorked.toFixed(2)} />
                    <DetailRow label="Выполнение нормы" value={pct(hoursPercentage)} />
                    <DetailRow alt label="Ставка, ₸/час" value={money(hourlyRate)} />
                </DetailGroup>

                <DetailGroup title="Конверсия группы">
                    <DetailRow label="Поступило заявок" value={simple(groupRequests)} />
                    <DetailRow alt label="Успешно закрыто" value={simple(groupSuccesses)} />
                    <DetailRow label="Факт. конверсия" value={groupRequests > 0 ? pct(factConversion) : '—'} />
                    <DetailRow alt label="Целевая конверсия" value={pct(targetConversion)} />
                    <DetailRow label="% выполнения плана" value={pct(planPercent)} />
                </DetailGroup>

                <DetailGroup title="Качество звонков">
                    <DetailRow label="Качество" value={pct(quality)} />
                    <DetailRow alt label="Удержание с бонуса" value={pct(withholdRate)} />
                </DetailGroup>

                <DetailGroup title="Компоненты выплаты">
                    <DetailRow label="Оклад (часы)" value={money(oklad)} />
                    <DetailRow alt label={`Бонус за успешки (${fmtNum(dealPrice)} × ${simple(deals)})`} value={money(bonusDeals)} />
                    <DetailRow label="Удержано за качество" value={`− ${money(qualityWithheld)}`} />
                    <DetailRow alt label="Штрафы" value={`− ${money(fines)}`} />
                    <DetailRow label="Премии" value={`+ ${money(bonuses)}`} />
                    <DetailRow strong label="Итого к выплате" value={money(finalSalary)} />
                </DetailGroup>
            </div>
        </CardShell>
    );
};

const SalaryCalculationResult = ({ salaryResult, label }) => {
        if (!salaryResult) return null;

        // Модели TEZ считаются иначе (оклад от ставки, вычеты), поэтому у них своя
        // раскладка внутри той же карточки, что и у СЗоВ.
        if (salaryResult.model === 'tez_op' || salaryResult.model === 'tez_line') {
            return <TezCalculationResult salaryResult={salaryResult} label={label} />;
        }

        // ОП «Основа»: часы × 600 + сделки × цена сделки − удержание по качеству.
        if (salaryResult.model === 'op_osnova') {
            return <OsnovaCalculationResult salaryResult={salaryResult} label={label} />;
        }

        // ОП «Поток»: часы × ставку + два потока продаж со своими ступенями.
        if (salaryResult.model === 'op_potok') {
            return <PotokCalculationResult salaryResult={salaryResult} label={label} />;
        }

        // ОП «Верификатор»: оклад × (качество% + премия за план%), без ступеней качества.
        if (salaryResult.model === 'op_verificator') {
            return <VerificatorCalculationResult salaryResult={salaryResult} label={label} />;
        }

        // ОП «Яндекс Регистрация»: цена успешки от конверсии ГРУППЫ, удержание по личному качеству.
        if (salaryResult.model === 'op_yandex_reg') {
            return <YandexRegCalculationResult salaryResult={salaryResult} label={label} />;
        }

        // --- Входные данные (с устойчивыми нэйминг-фоллбэками) ---
        const pointsRaw = salaryResult.points ?? salaryResult.kpi_points ?? 0; // может быть 85 или 0.85
        const premiumCoefficientRaw = salaryResult.premiumCoefficient ?? salaryResult.premium_coefficient ?? 1;
        const hoursNorm = num(salaryResult.hoursNorm ?? salaryResult.hours_norm ?? 0);
        const hoursPercentage = num(salaryResult.hoursPercentage ?? salaryResult.hours_percentage ?? 0);
        const hoursWorked = num(salaryResult.hoursWorked ?? salaryResult.hours_worked ?? salaryResult.hours ?? hoursNorm);
        const baseSalaryProvided = num(salaryResult.baseSalary ?? salaryResult.base_salary ?? 0);
        const premiumPartProvided = num(salaryResult.premiumPart ?? salaryResult.premium_part ?? 0);
        const bonuses = num(salaryResult.bonuses ?? salaryResult.bonuses_amount ?? salaryResult.bonus ?? 0);
        const finalSalary = num(salaryResult.finalSalary ?? salaryResult.final_salary ?? 0);

        // --- Логика KPI: если pointsRaw > 1 и <=100, считаем как проценты (85 -> 0.85) ---
        const kpiFactor = (() => {
            const p = Number(pointsRaw);
            if (!Number.isFinite(p)) return 0;
            return p / 100;
        })();

        // --- Коэффициент премии: если выполнение нормы < 90% -> 0.75 (как просили) ---
        const displayPremiumCoefficient = hoursPercentage < 90 ? 0.75 : num(premiumCoefficientRaw || 1);

        // --- Рассчитанный оклад по формуле: hoursWorked * 700 ---
        const baseSalaryCalc = (() => {
            const h = hoursWorked;
            // умножаем цифры точно (js Number), показываем 2 знака
            return Number.isFinite(h) ? h * 700 : 0;
        })();

        // --- Рассчитанная премия по формуле: (используем baseSalaryProvided если он задан, иначе baseSalaryCalc) ---
        const baseForPremium = baseSalaryProvided || baseSalaryCalc || 0;
        const premiumCalc = baseForPremium * kpiFactor * displayPremiumCoefficient;

        return (
            <CardShell subtitle="Сводка по KPI и выплатам" finalSalary={finalSalary}>
                {/* Top summary */}
                <div className="bg-white p-4 rounded border border-gray-100">
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                    <SummaryTile
                        title="Направление"
                        value={label || (salaryResult.model === 'chat' ? 'Чат' : 'Оператор')}
                    />
                    <SummaryTile title="Баллы KPI" value={simple(pointsRaw)} />
                    <SummaryTile
                        title="Коэффициент премии"
                        value={displayPremiumCoefficient}
                        tooltip={(
                            <>
                            <div className="font-medium mb-1">Коэффициент премии</div>
                            <div className="text-xs text-gray-600">
                                Если выполнение нормы часов меньше чем 90% → коэффициент премии = <b>0.75</b>
                            </div>
                            </>
                        )}
                    />
                </div>
                </div>

                {/* Components of payment */}
                <div className="bg-white p-4 rounded border border-gray-100">
                <h4 className="text-sm font-semibold text-gray-700 mb-3">Компоненты выплаты</h4>

                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                    <ComponentTile
                        title="Оклад (базовая часть)"
                        value={money(baseSalaryProvided || baseSalaryCalc)}
                        tooltip={(
                            <>
                                <div className="font-medium mb-1">Формула</div>
                                <div className="text-xs text-gray-600 mb-2">Оклад = отработанные часы × 700</div>
                                <div className="text-sm">
                                {Number.isFinite(hoursWorked) ? (
                                    <>
                                    Подстановка: <b>{fmtNum(hoursWorked)} × 700</b> = <b>{fmtNum(baseSalaryProvided || baseSalaryCalc)}</b>
                                    </>
                                ) : (
                                    <span className="text-xs text-gray-500">Количество отработанных часов неизвестно.</span>
                                )}
                                </div>
                            </>
                        )}
                    />

                    <ComponentTile
                        title="Премиальная часть"
                        value={money(premiumPartProvided || premiumCalc)}
                        tooltip={(
                            <>
                                <div className="font-medium mb-1">Формула</div>
                                <div className="text-xs text-gray-600 mb-2">Премия = Оклад × Баллы KPI × Коэффициент премии</div>
                                <div className="text-sm">
                                <div>Оклад для расчёта: <b>{money(baseForPremium)}</b></div>
                                <div>Баллы KPI: <b>{kpiFactor}</b> ({simple(pointsRaw)} {pointsRaw > 1 ? "%" : ""})</div>
                                <div>Коэффициент премии: <b>{displayPremiumCoefficient}</b></div>
                                <div className="mt-2 font-semibold">Подстановка: {fmtNum(baseForPremium)} × {kpiFactor} × {displayPremiumCoefficient} = <b>{fmtNum(premiumCalc)}</b></div>
                                </div>
                            </>
                        )}
                    />

                    <ComponentTile title="Бонусы" value={shortMoney(bonuses)} />
                </div>
                </div>

                <TotalBlock finalSalary={finalSalary} hoursNorm={hoursNorm} hoursPercentage={hoursPercentage} />

                {/* Детали расчёта — улучшенная читабельность и разделение */}
                <div className="bg-white p-4 rounded border border-gray-100">
                <h4 className="text-sm font-semibold text-gray-700 mb-3">Детали расчёта</h4>

                <DetailGroup title="Часы & KPI">
                    <DetailRow label="Норма часов" value={Number.isNaN(hoursNorm) ? "-" : hoursNorm.toFixed(2)} />
                    <DetailRow alt label="Выполнение нормы" value={`${hoursPercentage.toFixed(2)}%`} />
                    <DetailRow label="Баллы KPI" value={simple(pointsRaw)} />
                </DetailGroup>

                <DetailGroup title="Компоненты выплаты">
                    <DetailRow label="Базовый оклад" value={money(baseForPremium)} />
                    <DetailRow alt label="Премия" value={money(premiumCalc)} />
                    <DetailRow label="Бонусы" value={money(bonuses)} />
                </DetailGroup>
                </div>
            </CardShell>
        );
        };

export default SalaryCalculationResult;
