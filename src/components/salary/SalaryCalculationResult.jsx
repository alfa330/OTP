import React, { useState } from 'react';

    const SalaryCalculationResult = ({ salaryResult }) => {
        const [copyMsg, setCopyMsg] = useState("");

        if (!salaryResult) return null;

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

        // --- Копирование итоговой суммы ---
        const handleCopyTotal = async () => {
            try {
            await navigator.clipboard.writeText(String(finalSalary));
            setCopyMsg("Скопировано!");
            setTimeout(() => setCopyMsg(""), 2000);
            } catch (e) {
            setCopyMsg("Не удалось скопировать");
            setTimeout(() => setCopyMsg(""), 2000);
            }
        };

        // Небольшой helper для тултипа (отображение валидной строки)
        const fmtNum = (v) =>
            new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(num(v));

        return (
            <div className="mt-6 p-6 bg-gray-50 rounded-lg shadow-sm border border-gray-200">
            {/* Header */}
            <div className="flex items-start justify-between gap-4">
                <div>
                <h3 className="text-xl font-semibold mb-1 text-gray-800">Результат расчёта</h3>
                <p className="text-sm text-gray-500">Сводка по KPI и выплатам</p>
                </div>

                <div className="flex items-center gap-3">
                <button
                    onClick={handleCopyTotal}
                    className="inline-flex items-center gap-2 px-3 py-1.5 bg-blue-600 text-white rounded hover:bg-blue-700 transition text-sm"
                    title="Скопировать итоговую сумму"
                >
                    <i className="fas fa-copy" />
                    Копировать итого
                </button>
                <div className="text-sm text-green-600 font-medium min-w-[90px] text-right">
                    {copyMsg ? copyMsg : ""}
                </div>
                </div>
            </div>

            {/* Separation */}
            <div className="mt-4 space-y-4">
                {/* Top summary */}
                <div className="bg-white p-4 rounded border border-gray-100">
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                    <div>
                    <div className="text-sm text-gray-600">Направление</div>
                    <div className="font-medium text-gray-800 mt-1">Оператор</div>
                    </div>
                    <div>
                    <div className="text-sm text-gray-600">Баллы KPI</div>
                    <div className="font-medium text-gray-800 mt-1">{simple(pointsRaw)}</div>
                    </div>
                    <div>
                    <div className="text-sm text-gray-600">Коэффициент премии</div>
                    <div className="flex items-center gap-2 mt-1">
                        <div className="font-medium text-gray-800">{displayPremiumCoefficient}</div>

                        {/* Tooltip i для коэффициента премии */}
                        <div className="relative group inline-block">
                        <span className="inline-flex items-center justify-center w-5 h-5 text-xs rounded-full bg-gray-200 text-gray-700">i</span>
                        <div className="pointer-events-none opacity-0 group-hover:opacity-100 group-focus:opacity-100 transition-all duration-150 absolute right-0 top-full mt-2 w-64 z-50">
                            <div className="bg-white border rounded shadow p-3 text-sm text-gray-700">
                            <div className="font-medium mb-1">Коэффициент премии</div>
                            <div className="text-xs text-gray-600">
                                Если выполнение нормы часов меньше чем 90% → коэффициент премии = <b>0.75</b>  
                            </div>
                            </div>
                        </div>
                        </div>
                    </div>
                    </div>
                </div>
                </div>

                {/* Components of payment */}
                <div className="bg-white p-4 rounded border border-gray-100">
                <h4 className="text-sm font-semibold text-gray-700 mb-3">Компоненты выплаты</h4>

                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                    {/* Оклад */}
                    <div className="p-3 rounded border border-gray-50 bg-gray-50 relative">
                    <div className="flex items-start justify-between">
                        <div>
                        <div className="text-xs text-gray-500">Оклад (базовая часть)</div>
                        <div className="mt-2 text-lg font-semibold text-gray-800">{money(baseSalaryProvided || baseSalaryCalc)}</div>
                        </div>

                        {/* i icon + tooltip */}
                        <div className="ml-2">
                        <div className="relative group inline-block">
                            <span className="inline-flex items-center justify-center w-5 h-5 text-xs rounded-full bg-gray-200 text-gray-700">i</span>
                            <div className="pointer-events-none opacity-0 group-hover:opacity-100 transition-all duration-150 absolute -right-1 top-full mt-2 w-72 z-50">
                            <div className="bg-white border rounded shadow p-3 text-sm text-gray-700">
                                <div className="font-medium mb-1">Формула</div>
                                <div className="text-xs text-gray-600 mb-2">Оклад = отработанные часы × 700</div>
                                <div className="text-sm">
                                {Number.isFinite(hoursWorked) ? (
                                    <>
                                    Подстановка: <b>{baseSalaryProvided/700} × 700</b> = <b>{fmtNum(baseSalaryProvided)}</b>
                                    </>
                                ) : (
                                    <span className="text-xs text-gray-500">Количество отработанных часов неизвестно.</span>
                                )}
                                </div>
                            </div>
                            </div>
                        </div>
                        </div>
                    </div>
                    </div>

                    {/* Премия */}
                    <div className="p-3 rounded border border-gray-50 bg-gray-50 relative">
                    <div className="flex items-start justify-between">
                        <div>
                        <div className="text-xs text-gray-500">Премиальная часть</div>
                        <div className="mt-2 text-lg font-semibold text-gray-800">{money(premiumPartProvided || premiumCalc)}</div>
                        </div>

                        {/* i icon + tooltip (формула + подстановка) */}
                        <div className="ml-2">
                        <div className="relative group inline-block">
                            <span className="inline-flex items-center justify-center w-5 h-5 text-xs rounded-full bg-gray-200 text-gray-700">i</span>
                            <div className="pointer-events-none opacity-0 group-hover:opacity-100 transition-all duration-150 absolute -right-1 top-full mt-2 w-80 z-50">
                            <div className="bg-white border rounded shadow p-3 text-sm text-gray-700">
                                <div className="font-medium mb-1">Формула</div>
                                <div className="text-xs text-gray-600 mb-2">Премия = Оклад × Баллы KPI × Коэффициент премии</div>

                                <div className="text-sm">
                                <div>Оклад для расчёта: <b>{money(baseForPremium)}</b></div>
                                <div>Баллы KPI: <b>{kpiFactor}</b> ({simple(pointsRaw)} {pointsRaw > 1 ? "%" : ""})</div>
                                <div>Коэффициент премии: <b>{displayPremiumCoefficient}</b></div>
                                <div className="mt-2 font-semibold">Подстановка: {fmtNum(baseForPremium)} × {kpiFactor} × {displayPremiumCoefficient} = <b>{fmtNum(premiumCalc)}</b></div>
                                </div>
                            </div>
                            </div>
                        </div>
                        </div>
                    </div>
                    </div>

                    {/* Бонусы (без тултипа) */}
                    <div className="p-3 rounded border border-gray-50 bg-gray-50">
                    <div className="text-xs text-gray-500">Бонусы</div>
                    <div className="mt-2 text-lg font-semibold text-gray-800">{shortMoney(bonuses)}</div>
                    </div>
                </div>
                </div>

                {/* Big clear total */}
                <div className="bg-white p-4 rounded border-l-4 border-l-green-500 flex items-center justify-between">
                <div>
                    <div className="text-sm text-gray-600">Итого к выплате</div>
                    <div className="text-2xl font-bold text-green-600 mt-1">{money(finalSalary)}</div>
                </div>

                <div className="text-sm text-gray-600 text-right">
                    <div>Норма часов: <span className="font-medium text-gray-800 ml-1">{isNaN(hoursNorm) ? "-" : hoursNorm.toFixed(2)}</span></div>
                    <div className="mt-1">Выполнение нормы: <span className="font-medium text-gray-800 ml-1">{hoursPercentage.toFixed(2)}%</span></div>
                </div>
                </div>

                {/* Детали расчёта — улучшенная читабельность и разделение */}
                <div className="bg-white p-4 rounded border border-gray-100">
                <h4 className="text-sm font-semibold text-gray-700 mb-3">Детали расчёта</h4>

                {/* Group: Часы и KPI */}
                <div className="mb-3">
                    <div className="text-xs text-gray-500 font-medium mb-2">Часы & KPI</div>
                    <div className="divide-y divide-gray-100 rounded-lg overflow-hidden border">
                    <div className="flex items-center justify-between px-4 py-2 bg-white">
                        <div className="text-sm text-gray-600">Норма часов</div>
                        <div className="font-medium text-gray-800">{isNaN(hoursNorm) ? "-" : hoursNorm.toFixed(2)}</div>
                    </div>
                    <div className="flex items-center justify-between px-4 py-2 bg-gray-50">
                        <div className="text-sm text-gray-600">Выполнение нормы</div>
                        <div className="font-medium text-gray-800">{hoursPercentage.toFixed(2)}%</div>
                    </div>
                    <div className="flex items-center justify-between px-4 py-2 bg-white">
                        <div className="text-sm text-gray-600">Баллы KPI</div>
                        <div className="font-medium text-gray-800">{simple(pointsRaw)}</div>
                    </div>
                    </div>
                </div>

                {/* Group: Компоненты выплат (подробно) */}
                <div className="mb-3">
                    <div className="text-xs text-gray-500 font-medium mb-2">Компоненты выплаты</div>
                    <div className="divide-y divide-gray-100 rounded-lg overflow-hidden border">
                    <div className="flex items-center justify-between px-4 py-2 bg-white">
                        <div className="text-sm text-gray-600">Базовый оклад</div>
                        <div className="font-medium text-gray-800">{money(baseForPremium)}</div>
                    </div>
                    <div className="flex items-center justify-between px-4 py-2 bg-gray-50">
                        <div className="text-sm text-gray-600">Премия</div>
                        <div className="font-medium text-gray-800">{money(premiumCalc)}</div>
                    </div>
                    <div className="flex items-center justify-between px-4 py-2 bg-white">
                        <div className="text-sm text-gray-600">Бонусы</div>
                        <div className="font-medium text-gray-800">{money(bonuses)}</div>
                    </div>
                    </div>
                </div>
                </div>
            </div>
            </div>
        );
        };

export default SalaryCalculationResult;
