import React from 'react';
import { conditionGroups } from './waterMeta';
import { T, WaterGroup } from './waterUi';

/*
 * «Условия» — условия получения воды, только чтение (01.10.2026: «у операторов
 * из СЗоВ должен быть доступ к просмотру остатков и условиям получения»).
 *
 * Та же программа, что во вкладке «Настройки» у руководителя, но словами: что
 * сказать водителю, который звонит спросить, положена ли ему вода. Числа и
 * тарифы приходят с сервера (/ping) — текст меняется вместе с настройками.
 */
const WaterConditions = ({ settings }) => {
    if (!settings) {
        return <p className={`text-[13px] text-slate-500 ${T.body}`}>Условия программы ещё не настроены</p>;
    }
    return (
        /* На широком экране (с xl) обе группы стоят рядом, во всю ширину раздела. */
        <div className="max-w-2xl space-y-5 xl:grid xl:max-w-none xl:grid-cols-2 xl:items-start xl:gap-5 xl:space-y-0 2xl:gap-6">
            {conditionGroups(settings).map((group) => (
                <WaterGroup key={group.title} title={group.title}>
                    <dl className="-my-1 divide-y divide-slate-100">
                        {group.rows.map(([label, value]) => (
                            <div key={label} className={`flex justify-between gap-4 py-2 text-[13.5px] xl:py-2.5 2xl:gap-6 2xl:py-3.5 ${T.body}`}>
                                <dt className="shrink-0 text-slate-500">{label}</dt>
                                <dd className="text-right text-slate-900">{value}</dd>
                            </div>
                        ))}
                    </dl>
                </WaterGroup>
            ))}
        </div>
    );
};

export default WaterConditions;
