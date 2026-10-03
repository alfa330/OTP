import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { MessageSquare, Loader2, AlertCircle, Sparkles, Search } from 'lucide-react';
import { iosCard, iosBtnPrimary, iosBtnSecondary, IosHint } from '../ui/ios';
import EvaluationsList from './EvaluationsList';
import { chatSubjectOf, SUBJECT_C2D_SNAPSHOT, SOURCE_LABEL } from './subjects';
import { filtersToParams } from './filters';

/* Вкладка «Чаты» раздела ИИ-оценки: пригодность переписки, подбор новой и уже
 * оценённое по дням (тот же формат, что у «Очереди ревью» и «Звонков»).
 *
 * Источник зависит от отдела: ОП — эпизоды Wazzup (Верификаторы), СЗоВ — заявки
 * Chat2Desk, Тез КЦ — эпизоды ChatApp.
 *
 * Непроверенные карточки живут в общей «Очереди ревью» вместе со звонками:
 * очередь одна, дублировать её здесь незачем.
 *
 * Зачем чатам строка пригодности: у них есть ограничение, которого нет у
 * звонков, — переписку могут вести несколько сотрудников, и тогда оценить работу
 * одного человека нельзя. У эпизодов это порог «не меньше N% ответов у одного
 * оператора», у заявок Chat2Desk — признак ручной передачи чата (доли ответов у
 * них не бывает: заявка закреплена за одним оператором). Строка объясняет,
 * почему пригодных переписок меньше, чем всех. Раньше это были четыре плитки
 * над списком; теперь — одна тихая строка, а правило — под «i»: оно нужно один
 * раз, а место занимало всегда. */

const fmt = (n) => Number(n || 0).toLocaleString('ru-RU');

function Eligibility({ overview, isRequests }) {
    if (!overview) return null;
    if (overview.available === false) {
        return (
            <div className={`${iosCard} flex items-start gap-3 px-4 py-3.5`}>
                <AlertCircle size={18} className="mt-0.5 shrink-0 text-amber-500" />
                <div>
                    <p className="text-[13.5px] font-semibold text-slate-700">Чатовое направление не найдено</p>
                    <p className="mt-0.5 text-[12.5px] text-slate-500">
                        Переписка оценивается по чатовой шкале отдела. Проверьте, что в отделе есть направление
                        с чатовой моделью расчёта (у отдела продаж — со словом «Верификатор» в названии).
                    </p>
                </div>
            </div>
        );
    }
    const rule = overview.min_operator_share_pct != null
        ? `Оценить можно переписку, где не меньше ${overview.min_operator_share_pct}% ответов у одного оператора: работу одного человека в общей переписке не отделить.`
        : `Оценить можно заявку, где у оператора не меньше ${overview.min_operator_messages ?? 2} ответов и чат не передавали посреди заявки: автоназначение в начале передачей не считается.`;
    const hint = [rule,
        overview.unattributed ? `${fmt(overview.unattributed)} ${isRequests ? 'заявок' : 'диалогов'} без привязки автора к сотруднику — их не с кем сопоставить; привяжите авторов в разделе с перепиской этого отдела.` : null,
    ].filter(Boolean).join(' ');
    const directions = (overview.directions || []).map((d) => d.name).join(', ');
    return (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1 text-[12.5px] text-slate-500">
            <span className="inline-flex items-center gap-1.5 font-medium text-slate-600">
                <MessageSquare size={14} className="text-blue-500" aria-hidden="true" />
                {SOURCE_LABEL[overview.subject] || 'Переписка'}{directions ? ` · ${directions}` : ''}
            </span>
            <span className="tabular-nums">{isRequests ? 'Заявок' : 'Диалогов'} {fmt(overview.dialogs)}</span>
            <span className="tabular-nums">можно оценить <b className="font-semibold text-emerald-600">{fmt(overview.evaluable)}</b></span>
            {overview.multi_operator > 0 && (
                <span className="tabular-nums">
                    {isRequests ? 'с передачей' : 'несколько сотрудников'} <b className="font-semibold text-amber-600">{fmt(overview.multi_operator)}</b>
                </span>
            )}
            <IosHint text={hint} label="Какие переписки можно оценить" />
        </div>
    );
}

export default function ChatQueue({ apiBaseUrl, withAccessTokenHeader, showToast, onOpen, department,
                                    filters = null, onResetFilters = null, onFind = null, list = null,
                                    onOpenDigest = null }) {
    const headers = () => (withAccessTokenHeader ? withAccessTokenHeader() : {});
    const [overview, setOverview] = useState(null);
    const [randomBusy, setRandomBusy] = useState(false);
    const subject = chatSubjectOf(department);
    const isRequests = subject === SUBJECT_C2D_SNAPSHOT;

    useEffect(() => {
        if (!apiBaseUrl) return;
        let alive = true;
        setOverview(null);
        axios.get(`${apiBaseUrl}/api/ai-qa/chat-overview`,
                  { params: { ...(department ? { department } : {}) }, headers: headers() })
            .then((r) => { if (alive) setOverview(r.data ? { ...r.data, subject } : null); })
            .catch(() => { if (alive) setOverview(null); });
        return () => { alive = false; };
        // eslint-disable-next-line
    }, [apiBaseUrl, department]);

    /* Подбор переписки — не мгновенная операция: сервер перебирает кандидатов
     * отдела и проверяет пригодность. За эти секунды человек успевает
     * переключить отдел, а компонент при этом НЕ размонтируется (меняется лишь
     * проп), поэтому старый промис доживает до .then и без сверки открыл бы
     * карточку чужого отдела. Тот же приём, что в EvaluationsList. */
    const randomRequest = useRef({ id: 0, controller: null });
    const departmentRef = useRef(department);
    departmentRef.current = department;

    useEffect(() => () => randomRequest.current.controller?.abort(), []);

    const openRandom = async () => {
        if (!apiBaseUrl || randomBusy) return;
        randomRequest.current.controller?.abort();
        const controller = new AbortController();
        const requestId = randomRequest.current.id + 1;
        const requestedDepartment = department;
        randomRequest.current = { id: requestId, controller };
        setRandomBusy(true);
        try {
            // Подбор идёт по тому же отбору, что и список под ним: выбрав
            // сотрудника и период, человек ждёт переписку именно оттуда.
            const r = await axios.get(`${apiBaseUrl}/api/ai-qa/random-chat`,
                { params: { ...(department ? { department } : {}), ...filtersToParams(filters) },
                  headers: headers(), signal: controller.signal });
            if (requestId !== randomRequest.current.id
                || departmentRef.current !== requestedDepartment) return;
            if (r.data?.call) onOpen?.(r.data.call);
            else showToast?.('Подходящая переписка не найдена', 'error');
        } catch (error) {
            if (axios.isCancel(error) || requestId !== randomRequest.current.id
                || departmentRef.current !== requestedDepartment) return;
            showToast?.(error?.response?.data?.error || 'Не удалось выбрать переписку', 'error');
        } finally {
            if (requestId === randomRequest.current.id) setRandomBusy(false);
        }
    };

    const actions = (
        <>
            {/* Точечный подбор: конкретная переписка по номеру клиента, сотруднику и периоду. */}
            {onFind && (
                <button type="button" onClick={onFind} disabled={randomBusy || !apiBaseUrl}
                        className={`${iosBtnSecondary} disabled:cursor-not-allowed disabled:opacity-50`}
                        title="Найти конкретную переписку по номеру телефона, сотруднику и периоду">
                    <Search size={14} />{isRequests ? 'Найти заявку' : 'Найти чат'}
                </button>
            )}
            <button type="button" onClick={openRandom} disabled={randomBusy || !apiBaseUrl}
                    className={`${iosBtnPrimary} disabled:cursor-not-allowed disabled:opacity-50`}>
                {randomBusy ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}
                {randomBusy ? 'Выбираю…' : (isRequests ? 'Оценить случайную заявку' : 'Оценить случайный чат')}
            </button>
        </>
    );

    return (
        <EvaluationsList list={list} apiBaseUrl={apiBaseUrl} withAccessTokenHeader={withAccessTokenHeader}
                         onOpen={onOpen} showToast={showToast} subject={subject}
                         department={department} titleId="qa-chats-day-title"
                         filters={filters} onResetFilters={onResetFilters}
                         onOpenDigest={onOpenDigest}
                         lead={<Eligibility overview={overview} isRequests={isRequests} />}
                         actions={actions} />
    );
}
