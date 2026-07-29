import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { MessageSquare, Loader2, AlertCircle, Users, Sparkles } from 'lucide-react';
import { iosCard, iosBtnPrimary, IosBadge } from '../ui/ios';
import EvaluationsList from './EvaluationsList';

/* Вкладка «Чаты» раздела ИИ-оценки: эпизоды переписки Верификаторов (Wazzup) —
 * сводка пригодности, подбор нового чата и уже оценённые чаты.
 *
 * Непроверенные карточки живут в общей «Очереди ревью» вместе со звонками:
 * очередь одна, дублировать её здесь незачем.
 *
 * Зачем чатам отдельная вкладка: у них есть ограничение, которого нет у звонков, —
 * в одном эпизоде могут отвечать несколько операторов, и тогда оценить работу
 * одного человека нельзя. Порог («не меньше N% ответов у одного оператора»)
 * отсекает такие эпизоды, поэтому сводка объясняет, почему пригодных чатов
 * меньше, чем всех диалогов. */

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
    const [randomBusy, setRandomBusy] = useState(false);

    useEffect(() => {
        if (!apiBaseUrl) return;
        axios.get(`${apiBaseUrl}/api/ai-qa/chat-overview`, { headers: headers() })
            .then((r) => setOverview(r.data || null))
            .catch(() => setOverview(null));
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

            <div className="flex justify-end">
                <button type="button" onClick={openRandom} disabled={randomBusy || !apiBaseUrl}
                        className={`${iosBtnPrimary} disabled:cursor-not-allowed disabled:opacity-50`}>
                    {randomBusy ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}
                    {randomBusy ? 'Выбираю…' : 'Оценить случайный чат'}
                </button>
            </div>

            {/* Только чаты: оценённые эпизоды. Непроверенные лежат в общей
                «Очереди ревью» вместе со звонками — второй очереди здесь не нужно. */}
            <EvaluationsList apiBaseUrl={apiBaseUrl} withAccessTokenHeader={withAccessTokenHeader}
                             onOpen={onOpen} showToast={showToast} subject="wz_episode" />
        </div>
    );
}
