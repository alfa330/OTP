import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { MessageSquare, Loader2, AlertCircle, Users, Sparkles } from 'lucide-react';
import { iosCard, iosBtnPrimary, IosBadge } from '../ui/ios';
import EvaluationsList from './EvaluationsList';
import { chatSubjectOf, SUBJECT_C2D_SNAPSHOT, SOURCE_LABEL } from './subjects';

/* Вкладка «Чаты» раздела ИИ-оценки: сводка пригодности, подбор новой переписки
 * и уже оценённые.
 *
 * Источник зависит от отдела: ОП — эпизоды Wazzup (Верификаторы), СЗоВ — заявки
 * Chat2Desk, Тез КЦ — эпизоды ChatApp.
 *
 * Непроверенные карточки живут в общей «Очереди ревью» вместе со звонками:
 * очередь одна, дублировать её здесь незачем.
 *
 * Зачем чатам отдельная вкладка: у них есть ограничение, которого нет у звонков, —
 * переписку могут вести несколько сотрудников, и тогда оценить работу одного
 * человека нельзя. У эпизодов это порог «не меньше N% ответов у одного
 * оператора», у заявок Chat2Desk — признак ручной передачи чата (доли ответов у
 * них не бывает: заявка закреплена за одним оператором). Сводка объясняет,
 * почему пригодных переписок меньше, чем всех. */

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

export default function ChatQueue({ apiBaseUrl, withAccessTokenHeader, showToast, onOpen, department }) {
    const headers = () => (withAccessTokenHeader ? withAccessTokenHeader() : {});
    const [overview, setOverview] = useState(null);
    const [randomBusy, setRandomBusy] = useState(false);
    const subject = chatSubjectOf(department);
    const isRequests = subject === SUBJECT_C2D_SNAPSHOT;
    // Единица оценки у Chat2Desk — ЗАЯВКА, а не эпизод переписки. Слово видно во
    // всех подписях: заказчик работает этими терминами, и «эпизод» в СЗоВ
    // означал бы не то, что показано.
    const unit = isRequests ? 'заявок' : 'диалогов';

    useEffect(() => {
        if (!apiBaseUrl) return;
        let alive = true;
        setOverview(null);
        axios.get(`${apiBaseUrl}/api/ai-qa/chat-overview`,
                  { params: { ...(department ? { department } : {}) }, headers: headers() })
            .then((r) => { if (alive) setOverview(r.data || null); })
            .catch(() => { if (alive) setOverview(null); });
        return () => { alive = false; };
        // eslint-disable-next-line
    }, [apiBaseUrl, department]);

    const openRandom = async () => {
        if (!apiBaseUrl || randomBusy) return;
        setRandomBusy(true);
        try {
            const r = await axios.get(`${apiBaseUrl}/api/ai-qa/random-chat`,
                { params: { ...(department ? { department } : {}) }, headers: headers() });
            if (r.data?.call) onOpen?.(r.data.call);
            else showToast?.('Подходящая переписка не найдена', 'error');
        } catch (error) {
            showToast?.(error?.response?.data?.error || 'Не удалось выбрать переписку', 'error');
        } finally {
            setRandomBusy(false);
        }
    };

    return (
        <div className="space-y-3">
            {overview && overview.available === false ? (
                <div className={`${iosCard} flex flex-col items-center gap-2 px-6 py-10 text-center`}>
                    <AlertCircle size={24} className="text-amber-500" />
                    <p className="text-[14px] font-semibold text-slate-700">Чатовое направление не найдено</p>
                    <p className="text-[12.5px] text-slate-500">
                        Переписка оценивается по чатовой шкале отдела. Проверьте, что в отделе
                        есть направление с чатовой моделью расчёта (у отдела продаж — со словом
                        «Верификатор» в названии).
                    </p>
                </div>
            ) : overview ? (
                <div className={`${iosCard} p-3.5`}>
                    <div className="mb-2.5 flex flex-wrap items-center gap-2">
                        <MessageSquare size={16} className="text-blue-500" />
                        <p className="text-[13.5px] font-semibold text-slate-800">
                            {isRequests ? 'Заявки в переписке' : 'Эпизоды переписки'}
                        </p>
                        <IosBadge tone="blue">{SOURCE_LABEL[subject]}</IosBadge>
                        {(overview.directions || []).map((d) => (
                            <IosBadge key={d.id} tone="slate">{d.name}</IosBadge>
                        ))}
                    </div>
                    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                        <OverviewTile label={isRequests ? 'Заявок всего' : 'Диалогов всего'}
                                      value={overview.dialogs ?? 0} />
                        <OverviewTile label="Можно оценить" value={overview.evaluable ?? 0} tone="green"
                                      hint={overview.min_operator_share_pct != null
                                          ? `≥ ${overview.min_operator_share_pct}% ответов у одного оператора`
                                          : `≥ ${overview.min_operator_messages ?? 2} ответов оператора, чат не передавали`} />
                        <OverviewTile label="Несколько сотрудников" value={overview.multi_operator ?? 0} tone="amber"
                                      hint={isRequests ? 'чат передавали посреди заявки'
                                                       : 'оценить одного человека нельзя'} />
                        <OverviewTile label="Уже оценено ИИ" value={overview.evaluated ?? 0} />
                    </div>
                    {overview.unattributed ? (
                        <p className="mt-2 flex items-start gap-1.5 px-0.5 text-[11.5px] text-slate-500">
                            <Users size={13} className="mt-0.5 shrink-0 text-slate-400" />
                            {overview.unattributed} {unit} без привязки автора к сотруднику — их не с кем сопоставить.
                            Привяжите авторов в разделе с перепиской этого отдела.
                        </p>
                    ) : null}
                </div>
            ) : null}

            <div className="flex justify-end">
                <button type="button" onClick={openRandom} disabled={randomBusy || !apiBaseUrl}
                        className={`${iosBtnPrimary} disabled:cursor-not-allowed disabled:opacity-50`}>
                    {randomBusy ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}
                    {randomBusy ? 'Выбираю…' : (isRequests ? 'Оценить случайную заявку' : 'Оценить случайный чат')}
                </button>
            </div>

            {/* Только чаты: оценённые эпизоды. Непроверенные лежат в общей
                «Очереди ревью» вместе со звонками — второй очереди здесь не нужно. */}
            <EvaluationsList apiBaseUrl={apiBaseUrl} withAccessTokenHeader={withAccessTokenHeader}
                             onOpen={onOpen} showToast={showToast} subject={subject}
                             department={department} />
        </div>
    );
}
