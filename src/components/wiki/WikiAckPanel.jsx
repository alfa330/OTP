import React, { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { CheckCircle2, ChevronDown, Clock, Loader2, ShieldAlert } from 'lucide-react';
import { iosCard, iosBtnPrimary, IosBadge } from '../ui/ios';
import { getScrollContainer, isScrolledToEnd, observeScroll } from './scrollContainer';

/* Панель обязательного ознакомления.
 *
 * ГЛАВНОЕ ЗДЕСЬ — гейт «дочитал до конца», и он единственное место во всём
 * переносе, где прямое копирование оригинала дало бы работающий на вид, но
 * ложный результат.
 *
 * В исходной вике условие считалось так:
 *     window.innerHeight + window.scrollY >= documentElement.scrollHeight - 80
 * и проверялось сразу после подписки. В каркасе OTP окно не скроллится вовсе
 * (корень — flex h-screen overflow-hidden, прокручивается .main-content),
 * поэтому window.scrollY всегда 0, documentElement.scrollHeight равен высоте
 * вьюпорта, условие истинно с первого кадра — и отметка «ознакомлен»
 * проставлялась бы в момент ОТКРЫТИЯ статьи, без чтения.
 *
 * Здесь прокрутка берётся у контейнера (scrollContainer.js), а окончательное
 * решение всё равно принимает сервер: клиент сообщает лишь число раскрытых
 * обязательных блоков, а сверку с их общим количеством делает SQL.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

const fmtDate = (iso) => (iso
    ? new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: 'long', year: 'numeric' })
    : null);

export default function WikiAckPanel({ base, headers, articleId, bodyRef, showToast }) {
    const [assignment, setAssignment] = useState(null);
    const [busy, setBusy] = useState(false);
    const [scrolledToEnd, setScrolledToEnd] = useState(false);
    const [blocksOpened, setBlocksOpened] = useState(0);
    const reported = useRef(-1);

    const load = useCallback(() => {
        axios.get(`${base}/articles/${articleId}/ack`, { headers })
            .then((r) => setAssignment(r.data?.assignment || null))
            .catch(() => setAssignment(null));
    }, [base, headers, articleId]);

    useEffect(() => { load(); }, [load]);

    // Прокрутка — по контейнеру раздела, а не по окну.
    useEffect(() => {
        if (!assignment || assignment.acknowledged_at) return undefined;
        const container = getScrollContainer(bodyRef.current);
        if (!container) return undefined;

        const check = () => setScrolledToEnd(isScrolledToEnd(container));
        check();
        return observeScroll(container, check);
    }, [assignment, bodyRef]);

    // Раскрытые обязательные блоки считаем по реальному DOM: пользователь
    // должен именно развернуть их, а не пролистать мимо.
    useEffect(() => {
        if (!assignment || assignment.acknowledged_at || !bodyRef.current) return undefined;
        const nodes = bodyRef.current.querySelectorAll('[data-required-for-ack="true"], [data-required-for-ack="1"]');
        if (!nodes.length) { setBlocksOpened(0); return undefined; }

        const recount = () => {
            let opened = 0;
            nodes.forEach((node) => {
                if (node.tagName === 'DETAILS' ? node.open : node.getAttribute('data-open') === 'true') {
                    opened += 1;
                }
            });
            setBlocksOpened(opened);
        };
        recount();

        nodes.forEach((node) => node.addEventListener('toggle', recount));
        return () => nodes.forEach((node) => node.removeEventListener('toggle', recount));
    }, [assignment, bodyRef]);

    // Сообщаем серверу прогресс — только когда он реально изменился.
    useEffect(() => {
        if (!assignment || assignment.read_completed_at) return;
        if (!scrolledToEnd) return;
        if (reported.current === blocksOpened) return;
        reported.current = blocksOpened;

        axios.post(`${base}/articles/${articleId}/ack/read`,
            { blocks_opened: blocksOpened }, { headers })
            .then((r) => setAssignment((prev) => (prev ? { ...prev, ...r.data } : prev)))
            .catch(() => {});
    }, [scrolledToEnd, blocksOpened, assignment, base, headers, articleId]);

    const confirm = () => {
        setBusy(true);
        axios.post(`${base}/articles/${articleId}/ack/confirm`, {}, { headers })
            .then(() => { showToast?.('Ознакомление подтверждено', 'success'); load(); })
            .catch((e) => showToast?.(errText(e, 'Не удалось подтвердить'), 'error'))
            .finally(() => setBusy(false));
    };

    if (!assignment) return null;

    if (assignment.acknowledged_at) {
        return (
            <div className={`${iosCard} flex items-center gap-3 p-4`}>
                <CheckCircle2 size={20} className="shrink-0 text-emerald-500" />
                <div className="min-w-0">
                    <div className="text-[14px] font-semibold text-slate-900">
                        Вы ознакомились с документом
                    </div>
                    <div className="text-[12.5px] text-slate-500">
                        {fmtDate(assignment.acknowledged_at)} · редакция №{assignment.article_version}
                    </div>
                </div>
            </div>
        );
    }

    const total = assignment.blocks_total || 0;
    const blocksDone = total === 0 || blocksOpened >= total;
    const ready = !!assignment.read_completed_at || (scrolledToEnd && blocksDone);
    const overdue = assignment.due_at && new Date(assignment.due_at) < new Date();

    return (
        <div className={`${iosCard} space-y-3 border-l-[3px] border-l-amber-400 p-4`}>
            <div className="flex items-start gap-3">
                <ShieldAlert size={20} className="mt-0.5 shrink-0 text-amber-500" />
                <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[14px] font-semibold text-slate-900">
                            Требуется ознакомление
                        </span>
                        {assignment.due_at && (
                            <IosBadge tone={overdue ? 'red' : 'amber'}>
                                <Clock size={11} /> до {fmtDate(assignment.due_at)}
                            </IosBadge>
                        )}
                    </div>
                    <p className="mt-1 text-[12.5px] leading-relaxed text-slate-500">
                        Прочитайте документ до конца
                        {total > 0 && ' и раскройте все обязательные блоки'}
                        , после этого станет доступно подтверждение.
                    </p>
                </div>
            </div>

            <div className="space-y-1.5">
                <div className="flex items-center gap-2 text-[12.5px]">
                    {scrolledToEnd
                        ? <CheckCircle2 size={14} className="shrink-0 text-emerald-500" />
                        : <Loader2 size={14} className="shrink-0 animate-spin text-slate-300" />}
                    <span className={scrolledToEnd ? 'text-slate-700' : 'text-slate-400'}>
                        Документ прочитан до конца
                    </span>
                </div>
                {total > 0 && (
                    <div className="flex items-center gap-2 text-[12.5px]">
                        {blocksDone
                            ? <CheckCircle2 size={14} className="shrink-0 text-emerald-500" />
                            : <ChevronDown size={14} className="shrink-0 text-slate-300" />}
                        <span className={blocksDone ? 'text-slate-700' : 'text-slate-400'}>
                            Раскрыты обязательные блоки
                            <span className="ml-1 tabular-nums">{blocksOpened} из {total}</span>
                        </span>
                    </div>
                )}
            </div>

            <button
                type="button"
                className={`${iosBtnPrimary} w-full sm:w-auto`}
                disabled={!ready || busy}
                onClick={confirm}
            >
                {busy && <Loader2 size={14} className="animate-spin" />}
                Подтверждаю ознакомление
            </button>
        </div>
    );
}
