import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
    AlertTriangle, ArrowDownToLine, CheckCircle2, ChevronRight, Copy, FileText,
    Loader2, Trash2,
} from 'lucide-react';
import { iosCard, IosBadge, iosBtnGhost } from '../ui/ios';
import useStableCallback from './useStableCallback';

/* Половина вкладки «Статьи» — «Перенос»: очередь модерации приехавших статей.
 *
 * ── Зачем отдельный экран, а не корзина «Черновики» ────────────────────────
 *
 * Черновик — это «текст ещё пишут». Перенесённая статья — «текст готов, но его
 * не проверял никто из нас». Вопрос к человеку разный: у черновика «дописать
 * ли», у переноса «актуально ли это вообще и не лежит ли у нас то же самое».
 * Смешай их в одной корзине — и сорок приехавших статей похоронят три
 * настоящих черновика, которые кто-то не закончил.
 *
 * ── Почему экран пропадает ────────────────────────────────────────────────
 *
 * Пока очередь пуста, половины «Перенос» в переключателе нет вовсе (см.
 * WikiView: она появляется по totals.pending). Панель, которая всегда на месте
 * и всегда пишет «ничего нет», — это ровно тот визуальный шум, которого в
 * разделе быть не должно. Промодерированное при этом не теряется: ссылка
 * «показать разобранные» открывает и их.
 *
 * ── Почему две кнопки, а не три ───────────────────────────────────────────
 *
 * Опубликовать или убрать. «Посмотрю позже» — это не нажать ничего: строка
 * остаётся в очереди, и она же остаток работы. Подпись первой кнопки зависит от
 * того, где статья сейчас: черновик «Опубликовать», а уже живущую на витрине —
 * «Подтвердить». Одна подпись на два разных действия соврала бы в одном из них.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

/* Вердикт проверки на дубль. Ключи — из wiki/migration.py (VERDICT_OF_LABEL),
   подписи и цвет только здесь.
   «Уникальна» НЕ показываем: это норма, а красить норму значит утопить в ней
   три настоящих дубля. Правило владельца — цвет только там, где он несёт
   смысл. */
const VERDICTS = {
    duplicate: { tone: 'rose', label: 'дубль', icon: Copy },
    similar: { tone: 'amber', label: 'похоже на существующую', icon: Copy },
    nearby: { tone: 'slate', label: 'рядом с существующей', icon: Copy },
};

const STATUS_LABELS = {
    draft: 'Черновик', on_approval: 'На согласовании', published: 'Опубликована',
    requires_verification: 'Требует проверки', archived: 'В архиве', expired: 'Устарела',
};

const REVIEW_LABELS = {
    published: 'опубликована', kept: 'оставлена', discarded: 'убрана в архив',
};

const plural = (n, one, few, many) => {
    const mod100 = Math.abs(n) % 100;
    const mod10 = mod100 % 10;
    if (mod100 >= 11 && mod100 <= 14) return many;
    if (mod10 === 1) return one;
    if (mod10 >= 2 && mod10 <= 4) return few;
    return many;
};

const articleWord = (n) => plural(n, 'статья', 'статьи', 'статей');

/* Размер текста словами, а не байтами: «12 тыс. знаков» отвечает на вопрос
   «это заметка или регламент», а «12 226» требует деления в голове. */
const fmtSize = (size) => {
    const value = Number(size) || 0;
    if (value <= 0) return 'пустая';
    if (value < 1000) return `${value} знаков`;
    return `${Math.round(value / 1000)} тыс. знаков`;
};

const Row = ({ item, busy, locked, onOpen, onApprove, onDiscard }) => {
    const verdict = VERDICTS[item.dedup_verdict];
    const published = item.status === 'published';
    const reviewed = Boolean(item.reviewed_at);
    const VerdictIcon = verdict?.icon;

    return (
        <div className="flex flex-col gap-2 px-3 py-3 transition hover:bg-slate-50 sm:flex-row sm:items-start">
            <button
                type="button"
                onClick={onOpen}
                className="flex min-w-0 flex-1 items-start gap-3 text-left"
            >
                <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-indigo-50 text-indigo-500">
                    <FileText size={14} />
                </span>
                <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span className="text-[13.5px] font-semibold leading-snug text-slate-900">
                            {item.title}
                        </span>
                        {/* Статус показываем только когда он НЕ черновик: в этой
                            очереди черновик — норма, и подпись «Черновик» у
                            каждой строки была бы одинаковой на всём списке. */}
                        {published && <IosBadge tone="emerald">Уже на витрине</IosBadge>}
                        {item.status !== 'draft' && !published && (
                            <IosBadge tone="slate">
                                {STATUS_LABELS[item.status] || item.status}
                            </IosBadge>
                        )}
                        {verdict && (
                            <IosBadge tone={verdict.tone}>
                                {VerdictIcon && <VerdictIcon size={9} className="mr-1 inline" />}
                                {verdict.label}
                                {item.dedup_score != null
                                    && ` ${Math.round(item.dedup_score * 100)}%`}
                            </IosBadge>
                        )}
                    </span>

                    {/* На что именно похожа — одной строкой и только когда есть
                        находка: «дубль» без ответа «чего именно» заставляет
                        искать вручную. */}
                    {verdict && item.dedup_note && (
                        <span className="mt-1 block text-[11.5px] leading-relaxed text-slate-500">
                            Похоже на «{item.dedup_note}»
                            {item.match_status && item.match_status !== 'published'
                                && ` (${STATUS_LABELS[item.match_status] || item.match_status})`}
                        </span>
                    )}

                    {item.summary && (
                        <span className="mt-0.5 block line-clamp-2 text-[12px] leading-relaxed text-slate-500">
                            {item.summary}
                        </span>
                    )}

                    <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10.5px] text-slate-400">
                        <span className="tabular-nums">
                            старая вика
                            {item.source_id != null ? ` #${item.source_id}` : ''}
                        </span>
                        <span className="tabular-nums">{fmtSize(item.size)}</span>
                        {item.sections && <span>{item.sections}</span>}
                        {/* Про неполноту проверки говорим прямо: «похожего не
                            нашли» и «не смогли посмотреть» — разные ответы. */}
                        {item.dedup_degraded && (
                            <span className="inline-flex items-center gap-1 text-amber-600">
                                <AlertTriangle size={10} /> смысловая проверка не сработала
                            </span>
                        )}
                        {reviewed && (
                            <span className="inline-flex items-center gap-1 text-emerald-600">
                                <CheckCircle2 size={10} />
                                {REVIEW_LABELS[item.review_action] || 'разобрана'}
                                {item.reviewed_by_name ? ` · ${item.reviewed_by_name}` : ''}
                            </span>
                        )}
                    </span>
                </span>
                <ChevronRight size={14} className="mt-2 hidden shrink-0 text-slate-300 sm:block" />
            </button>

            {/* Решения — кнопками, а не пунктами меню под «тремя точками».
                Меню годится там, где действий много и они редкие; здесь их два,
                и нажимают их подряд по всему списку: спрятать их за меню значит
                добавить лишнее нажатие к каждой строке. */}
            {!reviewed && (
                <span className="flex shrink-0 items-center gap-1.5 sm:mt-0.5">
                    {busy ? (
                        <span className="grid h-8 w-16 place-items-center">
                            <Loader2 size={14} className="animate-spin text-slate-400" />
                        </span>
                    ) : (
                        <>
                            <button
                                type="button"
                                disabled={locked}
                                onClick={onApprove}
                                className="rounded-xl bg-slate-900 px-3 py-1.5 text-[12px] font-medium text-white transition active:scale-[0.98] disabled:opacity-40"
                            >
                                {published ? 'Подтвердить' : 'Опубликовать'}
                            </button>
                            <button
                                type="button"
                                disabled={locked}
                                onClick={onDiscard}
                                aria-label={`Убрать «${item.title}»`}
                                title="Убрать в архив"
                                className="grid h-8 w-8 place-items-center rounded-xl text-slate-400 transition hover:bg-rose-50 hover:text-rose-600 active:scale-[0.98] disabled:opacity-40"
                            >
                                <Trash2 size={14} />
                            </button>
                        </>
                    )}
                </span>
            )}
        </div>
    );
};

export default function WikiMigration({ base, headers, showToast, onOpenArticle,
                                        onReviewed, space = null }) {
    /* Колбэки родителя приходят новыми на КАЖДЫЙ его рендер. Попади они в
       зависимости load — и очередь перезапрашивалась бы по кругу: ответ →
       setState → рендер → новый load → снова запрос. Ровно это и выглядело как
       «раздел лагает». Обёртка стабильна, актуальная функция живёт в ref. */
    const toast = useStableCallback(showToast);
    const reviewed = useStableCallback(onReviewed);
    const openArticle = useStableCallback(onOpenArticle);

    const [state, setState] = useState(null);      // {totals, items}
    const [loading, setLoading] = useState(true);
    const [showAll, setShowAll] = useState(false);
    const [acting, setActing] = useState(null);    // id статьи, над которой работаем

    const load = useCallback((withAll) => {
        setLoading(true);
        /* Пространство просим ТО ЖЕ, что показано в шапке: по счётчику из
           каталога эта половина и появилась, а он сужен пространством. Не
           передай его — список окажется шире счётчика. */
        return axios.get(`${base}/migration`, {
            headers,
            params: { space_id: space?.id || null, ...(withAll ? { all: 1 } : {}) },
        })
            .then((r) => setState(r.data))
            .catch((e) => toast(errText(e, 'Не удалось получить очередь переноса'), 'error'))
            .finally(() => setLoading(false));
        /* headers родитель мемоизирует (WikiView: useMemo по
           withAccessTokenHeader), поэтому в зависимостях он безопасен — как и
           у остальных загрузчиков раздела. Круг давали только колбэки. */
    }, [base, headers, space?.id, toast]);

    useEffect(() => { load(showAll); }, [load, showAll]);

    const items = state?.items || [];
    const totals = state?.totals || { imported: 0, pending: 0, duplicates: 0 };

    const decide = (item, publish) => {
        setActing(item.article_id);
        const path = publish ? 'publish' : 'discard';
        axios.post(`${base}/migration/${item.article_id}/${path}`, {}, { headers })
            .then((r) => {
                if (r.data?.status === 'already_reviewed') {
                    toast('Эту статью уже разобрали', 'info');
                } else if (publish) {
                    toast(item.status === 'published'
                        ? 'Статья подтверждена и снята из очереди'
                        : 'Статья опубликована', 'success');
                } else {
                    toast('Статья убрана в архив', 'success');
                }
                // Каталог и счётчики главной меняются вместе с решением: статья
                // сменила корзину. Обновляет их владелец экрана, а не мы —
                // второй перезапрос того же списка был бы платой ни за что.
                reviewed();
                return load(showAll);
            })
            .catch((e) => toast(errText(e, 'Не удалось применить решение'), 'error'))
            .finally(() => setActing(null));
    };

    /* Одна фраза о состоянии работы вместо ряда плиток со числами: плитки
       «Перенесено / Разобрано / Осталось» — это одно и то же число тремя
       способами. */
    const headline = useMemo(() => {
        if (!totals.imported) return 'Из старой вики пока ничего не переносили.';
        if (!totals.pending) {
            return `Все ${totals.imported} ${articleWord(totals.imported)} разобраны.`;
        }
        const parts = [`Ждут проверки ${totals.pending} из ${totals.imported}`];
        if (totals.duplicates) {
            parts.push(`из них похожи на дубли — ${totals.duplicates}`);
        }
        return `${parts.join(', ')}.`;
    }, [totals]);

    return (
        <div className={`${iosCard} overflow-hidden`}>
            <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5 border-b border-slate-200/70 px-3 py-2.5">
                <div className="min-w-0">
                    <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-500">
                        <ArrowDownToLine size={12} /> Перенос из старой вики
                    </div>
                    <p className="mt-0.5 text-[12px] leading-relaxed text-slate-500">
                        {headline}
                    </p>
                </div>
                {/* Ссылка, а не тумблер: это разовое «а что уже разобрали»,
                    и постоянному управлению здесь взяться неоткуда. */}
                {totals.imported > totals.pending && (
                    <button
                        type="button"
                        onClick={() => setShowAll((prev) => !prev)}
                        className={`${iosBtnGhost} shrink-0 text-[12px]`}
                    >
                        {showAll ? 'Только неразобранные' : 'Показать разобранные'}
                    </button>
                )}
            </div>

            {loading && !state && (
                <div className="grid place-items-center py-14">
                    <Loader2 size={18} className="animate-spin text-slate-400" />
                </div>
            )}

            {!loading && items.length === 0 && (
                <div className="flex flex-col items-center gap-2 px-6 py-14 text-center">
                    <div className="grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                        <CheckCircle2 size={22} />
                    </div>
                    <div className="text-[15px] font-semibold text-slate-900">
                        Очередь пуста
                    </div>
                    <p className="max-w-sm text-[13px] leading-relaxed text-slate-500">
                        Все перенесённые статьи разобраны: каждая либо опубликована,
                        либо убрана в архив.
                    </p>
                </div>
            )}

            {/* Обрезанный список обязан сказать, что он обрезан: очередь — это
                список ДЕЛ, и незаметно выпавшее из него дело не будет сделано
                никогда. Появляется только когда обрезание реально случилось. */}
            {items.length > 0 && !showAll && totals.pending > items.length && (
                <p className="border-b border-slate-200/70 bg-amber-50/60 px-3 py-2 text-[11.5px] text-amber-700">
                    Показаны первые {items.length} из {totals.pending}. Разберите
                    их — остальные подтянутся.
                </p>
            )}

            {items.length > 0 && (
                <div className="divide-y divide-slate-100">
                    {items.map((item) => (
                        <Row
                            key={item.article_id}
                            item={item}
                            busy={acting === item.article_id}
                            locked={acting != null && acting !== item.article_id}
                            onOpen={() => openArticle(item.slug)}
                            onApprove={() => decide(item, true)}
                            onDiscard={() => decide(item, false)}
                        />
                    ))}
                </div>
            )}
        </div>
    );
}
