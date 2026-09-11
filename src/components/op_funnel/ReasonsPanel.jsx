import React, { useMemo } from 'react';
import FaIcon from '../common/FaIcon';
import { IosHint } from '../ui/ios';
import {
    BUCKET_ORDER,
    formatNumber,
    formatPercent,
    reasonBucketLabel,
} from './funnelFormat';

/**
 * Разбивка причин закрытия по корзинам: недозвон, отказы, нецелевые и отдельно
 * «увели в другой процесс».
 *
 * Зачем «увели» отдельной группой, а не в отказах. В amoCRM семь причин
 * закрытия из двадцати семи — это названия этапов и воронок («Диалоги»,
 * «YaPROREG», «Дожим приглашенные», «Звонки (не закрытые)»): ими помечают лид,
 * ушедший в другой процесс. Сводные листы супервайзера их в отказы не считают,
 * и если сложить вместе, отказы удваиваются — на 1 сентября у одного оператора
 * выходило 14 вместо 7. Поэтому группа своя, и в интерфейсе подписано, что это
 * не отказ: иначе супервайзер решит, что цифра отказов занижена.
 *
 * Клик по причине обязан приводить к списку конкретных людей — это критерий
 * приёмки ТЗ, а не удобство: «двадцать отказов по комиссии» без имён нельзя ни
 * проверить, ни отработать.
 */

/* Корзины отличаются смыслом, поэтому подписаны, а не только названы.
   Нейтральный тон у всех: цветом здесь мерить нечего — сорок отказов это плохо
   или нормально, зависит от объёма базы, и красить их «в красное» было бы
   ложным сигналом. */
const BUCKET_HINTS = {
    nedozvon: 'Разговора не было: не дозвонились, сбросили, неверный номер.',
    otkaz: 'Поговорили, но человек отказался.',
    netsel: 'Лид изначально не наш: нет авто, не тот возраст, третье лицо.',
    moved: 'Лид не отказывал — его увели в другой процесс (другая воронка, офис, дожим). '
        + 'В отказы такие лиды не считаются.',
};

const BucketBlock = ({ bucket, onOpenReason }) => {
    const reasons = bucket.reasons || [];
    const top = reasons[0]?.leads || 0;
    return (
        <section className="rounded-2xl bg-white ring-1 ring-slate-200/70 shadow-sm p-4 sm:p-5">
            <header className="flex items-start justify-between gap-3 mb-3">
                <div className="min-w-0">
                    <div className="flex items-center gap-2">
                        <h3 className="text-[15px] font-semibold text-slate-900">
                            {reasonBucketLabel(bucket.bucket)}
                        </h3>
                        <IosHint text={BUCKET_HINTS[bucket.bucket] || ''} />
                    </div>
                    <p className="text-xs text-slate-500 mt-0.5">
                        {formatNumber(reasons.length)} причин
                    </p>
                </div>
                <div className="text-right shrink-0">
                    <div className="text-xl font-semibold text-slate-900 tabular-nums leading-none">
                        {formatNumber(bucket.leads)}
                    </div>
                    <div className="text-[11px] text-slate-500 mt-1">лидов</div>
                </div>
            </header>

            <ul className="space-y-1.5">
                {reasons.map((reason) => {
                    /* Полоса нормируется на САМУЮ ЧАСТУЮ причину корзины, а не на
                       сумму: при двадцати причинах доли по 5 % дали бы двадцать
                       одинаковых огрызков, и сравнивать было бы нечего. */
                    const width = top > 0 ? Math.max(2, Math.round((reason.leads / top) * 100)) : 0;
                    const clickable = typeof onOpenReason === 'function';
                    return (
                        <li key={reason.code}>
                            <button
                                type="button"
                                disabled={!clickable}
                                onClick={() => clickable && onOpenReason({
                                    bucket: bucket.bucket,
                                    code: reason.code,
                                    title: reason.title,
                                })}
                                className={`w-full text-left rounded-xl px-3 py-2 transition
                                    ${clickable
                                        ? 'hover:bg-slate-50 active:scale-[0.98] cursor-pointer'
                                        : 'cursor-default'}`}
                            >
                                <div className="flex items-baseline justify-between gap-3">
                                    <span className="text-[13px] text-slate-700 truncate">
                                        {reason.title}
                                    </span>
                                    <span className="flex items-baseline gap-2 shrink-0">
                                        <span className="text-[13px] font-medium text-slate-900 tabular-nums">
                                            {formatNumber(reason.leads)}
                                        </span>
                                        <span className="text-[11px] text-slate-400 tabular-nums w-11 text-right">
                                            {formatPercent(reason.share, 0)}
                                        </span>
                                        {clickable && (
                                            <FaIcon className="fas fa-chevron-right text-[10px] text-slate-300" />
                                        )}
                                    </span>
                                </div>
                                <div className="mt-1 h-1 rounded-full bg-slate-100 overflow-hidden">
                                    <div
                                        className="h-full rounded-full bg-slate-400"
                                        style={{ width: `${width}%` }}
                                    />
                                </div>
                            </button>
                        </li>
                    );
                })}
            </ul>
        </section>
    );
};

export default function ReasonsPanel({ buckets = [], onOpenReason = null }) {
    /* Порядок корзин фиксированный (BUCKET_ORDER), а не по объёму: он повторяет
       путь лида — не дозвонились, отказался, не наш, увели. Плавающий порядок
       заставлял бы искать корзину глазами при каждом открытии. */
    const ordered = useMemo(() => {
        const byKey = new Map((buckets || []).map((item) => [item.bucket, item]));
        const known = BUCKET_ORDER
            .map((key) => byKey.get(key))
            .filter((item) => item && (item.leads || (item.reasons || []).length));
        const extra = (buckets || []).filter((item) => !BUCKET_ORDER.includes(item.bucket));
        return [...known, ...extra];
    }, [buckets]);

    if (!ordered.length) {
        return (
            <div className="rounded-2xl bg-white ring-1 ring-slate-200/70 p-8 text-center">
                <FaIcon className="fas fa-filter text-2xl text-slate-300" />
                <p className="mt-3 text-sm text-slate-500">
                    За выбранный период причин закрытия нет.
                </p>
                <p className="mt-1 text-xs text-slate-400">
                    Либо лиды ещё не закрывали, либо данные за период не выгружены.
                </p>
            </div>
        );
    }

    return (
        <div className="grid gap-3 sm:gap-4 md:grid-cols-2">
            {ordered.map((bucket) => (
                <BucketBlock key={bucket.bucket} bucket={bucket} onOpenReason={onOpenReason} />
            ))}
        </div>
    );
}
