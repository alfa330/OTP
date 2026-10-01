import React from 'react';
import { IosSection } from '../ui/ios';
import { conditionGroups } from './waterMeta';

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
        return <p className="text-[13px] text-slate-500">Условия программы ещё не настроены</p>;
    }
    return (
        <div className="max-w-2xl space-y-5">
            {conditionGroups(settings).map((group) => (
                <IosSection key={group.title} title={group.title}>
                    <dl className="-my-1 divide-y divide-slate-100">
                        {group.rows.map(([label, value]) => (
                            <div key={label} className="flex justify-between gap-4 py-2 text-[13.5px]">
                                <dt className="shrink-0 text-slate-500">{label}</dt>
                                <dd className="text-right text-slate-900">{value}</dd>
                            </div>
                        ))}
                    </dl>
                </IosSection>
            ))}
        </div>
    );
};

export default WaterConditions;
