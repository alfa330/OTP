import React, { useEffect, useState } from 'react';
import axios from 'axios';
import {
    MessageSquare, Loader2, AlertCircle, CheckCircle2, Users, Sparkles, ImageOff,
} from 'lucide-react';
import { iosCard, iosBtnPrimary, iosBtnSecondary, IosBadge } from '../ui/ios';

/* Вкладка «Чаты» раздела ИИ-оценки: эпизоды переписки Верификаторов (Wazzup).
 *
 * Зачем отдельная вкладка, а не просто фильтр очереди: у чатов есть своё
 * ограничение, которого нет у звонков, — в одном эпизоде могут отвечать
 * несколько операторов, и тогда оценить работу одного человека нельзя. Порог
 * («не меньше N% ответов у одного оператора») отсекает такие эпизоды, поэтому
 * сводка сверху объясняет, почему пригодных чатов меньше, чем всех диалогов. */

const OverviewTile = ({ label, value, tone = 'slate', hint }) => (
    <div className="rounded-2xl bg-slate-50 px-3.5 py-3">
        <p className="text-[11.5px] font-medium text-slate-500">{label}</p>
        <p className={`mt-0.5 text-[19px] font-semibold ${
            tone === 'green' ? 'text-emerald-600' : tone === 'amber' ? 'text-amber-600' : 'text-slate-900'}`}>
            {value}
        </p>
        {hint && <p className="mt-0.5 text-[11px] text-slate-400">{hint}</p>}
    </div>
);

export default function ChatQueue({ apiBaseUrl, withAccessTokenHeader, showToast, onOpen }) {
    const headers = () => (withAccessTokenHeader ? withAccessTokenHeader() : {});
    const [overview, setOverview] = useState(null);
    const [items, setItems] = useState(null);
    const [total, setTotal] = useState(0);
    const [err, setErr] = useState(false);
    const [moreBusy, setMoreBusy] = useState(false);
    const [randomBusy, setRandomBusy] = useState(false);

    const PAGE = 30;
    const load = (offset = 0, append = false) => {
        if (append) setMoreBusy(true); else { setItems(null); setErr(false); }
        if (!apiBaseUrl) { setErr(true); setItems([]); setMoreBusy(false); return; }
        axios.get(`${apiBaseUrl}/api/ai-qa/review-queue`,
            { params: { limit: PAGE, offset, subject: 'wz_episode' }, headers: headers() })
            .then((r) => {
                const page = r.data.items || [];
                setTotal(typeof r.data.total === 'number' ? r.data.total : page.length);
                setItems((prev) => (append && Array.isArray(prev) ? [...prev, ...page] : page));
            })
            .catch(() => {
                if (append) showToast?.('Не удалось подгрузить ещё', 'error');
                else { setItems([]); setErr(true); }
            })
            .finally(() => setMoreBusy(false));
    };

    useEffect(() => {
        if (!apiBaseUrl) return;
        axios.get(`${apiBaseUrl}/api/ai-qa/chat-overview`, { headers: headers() })
            .then((r) => setOverview(r.data || null))
            .catch(() => setOverview(null));
        load();
        // eslint-disable-next-line
    }, [apiBaseUrl]);

    const openRandom = async () => {
        if (!apiBaseUrl || randomBusy) return;
        setRandomBusy(true);
        try {
            const r = await axios.get(`${apiBaseUrl}/api/ai-qa/random-chat`, { headers: headers() });
            if (r.data?.call) onOpen?.(r.data.call);
            else showToast?.('Подходящий чат не найден', 'error');
        } catch (error) {
            showToast?.(error?.response?.data?.error || 'Не удалось выбрать чат', 'error');
        } finally {
            setRandomBusy(false);
        }
    };

    return (
        <div className="space-y-3">
            {overview && overview.available === false ? (
                <div className={`${iosCard} flex flex-col items-center gap-2 px-6 py-10 text-center`}>
                    <AlertCircle size={24} className="text-amber-500" />
                    <p className="text-[14px] font-semibold text-slate-700">Направление Верификаторов не найдено</p>
                    <p className="text-[12.5px] text-slate-500">
                        Чаты оцениваются по шкале Верификаторов. Проверьте, что у направления
                        отдела продаж в названии есть слово «Верификатор».
                    </p>
                </div>
            ) : overview ? (
                <div className={`${iosCard} p-3.5`}>
                    <div className="mb-2.5 flex flex-wrap items-center gap-2">
                        <MessageSquare size={16} className="text-blue-500" />
                        <p className="text-[13.5px] font-semibold text-slate-800">Эпизоды переписки Верификаторов</p>
                        {(overview.directions || []).map((d) => (
                            <IosBadge key={d.id} tone="slate">{d.name}</IosBadge>
                        ))}
                    </div>
                    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                        <OverviewTile label="Диалогов всего" value={overview.dialogs ?? 0} />
                        <OverviewTile label="Можно оценить" value={overview.evaluable ?? 0} tone="green"
                                      hint={`≥ ${overview.min_operator_share_pct}% ответов у одного оператора`} />
                        <OverviewTile label="Несколько операторов" value={overview.multi_operator ?? 0} tone="amber"
                                      hint="оценить одного человека нельзя" />
                        <OverviewTile label="Уже оценено ИИ" value={overview.evaluated ?? 0} />
                    </div>
                    {overview.unattributed ? (
                        <p className="mt-2 flex items-start gap-1.5 px-0.5 text-[11.5px] text-slate-500">
                            <Users size={13} className="mt-0.5 shrink-0 text-slate-400" />
                            {overview.unattributed} эпизодов без привязки автора к сотруднику — их не с кем сопоставить.
                            Привяжите авторов в разделе «Чаты верификаторов» → «Привязка».
                        </p>
                    ) : null}
                </div>
            ) : null}

            <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-[12.5px] text-slate-500">
                    Очередь ревью по чатам{items ? `: ${items.length} из ${total}` : ''}
                </p>
                <button type="button" onClick={openRandom} disabled={randomBusy || !apiBaseUrl}
                        className={`${iosBtnPrimary} disabled:cursor-not-allowed disabled:opacity-50`}>
                    {randomBusy ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}
                    {randomBusy ? 'Выбираю…' : 'Оценить случайный чат'}
                </button>
            </div>

            {items === null ? (
                <div className={`${iosCard} flex flex-col items-center justify-center gap-3 px-6 py-16 text-center`} role="status">
                    <Loader2 size={26} className="animate-spin text-blue-500" />
                    <p className="text-[13px] text-slate-500">Загружаю очередь чатов…</p>
                </div>
            ) : err ? (
                <div className={`${iosCard} flex flex-col items-center gap-3 px-6 py-14 text-center`} role="alert">
                    <AlertCircle size={26} className="text-rose-500" />
                    <p className="text-[13.5px] font-medium text-slate-700">Не удалось загрузить очередь чатов</p>
                    <button type="button" onClick={() => load()} className={iosBtnSecondary}>Повторить</button>
                </div>
            ) : items.length === 0 ? (
                <div className={`${iosCard} flex flex-col items-center gap-3 px-6 py-14 text-center`}>
                    <CheckCircle2 size={26} className="text-emerald-500" />
                    <div>
                        <p className="text-[14px] font-semibold text-slate-700">Непроверенных чатов нет</p>
                        <p className="mt-1 text-[12.5px] text-slate-500">
                            Нажмите «Оценить случайный чат», чтобы получить новый эпизод на проверку.
                        </p>
                    </div>
                    <button type="button" onClick={() => load()} className={iosBtnSecondary}>Обновить</button>
                </div>
            ) : (
                <div className="space-y-2.5">
                    {items.map((c) => (
                        <button key={c.id} type="button" onClick={() => onOpen?.(c)}
                            className={`${iosCard} flex w-full flex-col items-stretch justify-between gap-2.5 p-3.5 text-left transition hover:ring-blue-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 active:scale-[0.995] sm:flex-row sm:items-center`}>
                            <div className="min-w-0">
                                <div className="flex items-center gap-2">
                                    <MessageSquare size={14} className="shrink-0 text-blue-500" />
                                    <span className="text-[14px] font-semibold text-slate-900">Чат #{c.id}</span>
                                    <IosBadge tone="slate">{c.direction}</IosBadge>
                                </div>
                                <p className="mt-0.5 text-[12px] text-slate-400">{c.operator} · {c.datetime}</p>
                            </div>
                            <div className="flex flex-wrap items-center gap-1.5 sm:shrink-0 sm:justify-end">
                                {(c.reasons || []).includes('media') && (
                                    <IosBadge tone="amber" title="Часть вложений (фото/голосовых) не удалось прочитать — проверьте их вручную.">
                                        <ImageOff size={11} />Вложение не прочитано
                                    </IosBadge>
                                )}
                                {c.stale && <IosBadge tone="amber">оценка устарела</IosBadge>}
                            </div>
                        </button>
                    ))}
                    {items.length < total && (
                        <div className="flex justify-center pt-1">
                            <button type="button" onClick={() => load(items.length, true)}
                                    disabled={moreBusy} className={iosBtnSecondary}>
                                {moreBusy ? 'Загрузка…' : `Показать ещё (осталось ${total - items.length})`}
                            </button>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
