import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { Shuffle, Loader2, ClipboardList, AlertCircle, RefreshCw, PhoneIncoming, Search, CheckCircle2 } from 'lucide-react';
import { APPLE_FONT, iosCard, iosBtnPrimary, iosBtnSecondary, iosGroupLabel, scoreTone } from '../ui/ios';
import QueueList from './QueueList';
import { DigestLink } from './QueueDays';
import { isChat, subjectUnit, SUBJECT_C2D_SNAPSHOT, SUBJECT_IMPORTED_CALL } from './subjects';
import { filtersKey, filtersToParams, hasActiveFilters, pullParamsFromFilters } from './filters';
import { commonRowMeta, dayNeighbours, monthGroups, NO_DAY, plural } from './queueDayRules';
import {
    DAY_ROW_CLASS, DateLeaf, DayHeader, DayStat, MonthSection, RowChevron, Score, ShareBar, dayTitle,
} from './dayKit';

/* «Звонки» и «Чаты» — уже оценённое ИИ, по дням разговора.
 *
 * Формат — как у «Очереди ревью» (решение владельца 02.10.2026): дни по
 * месяцам, у месяца один список, по нажатию — экран дня с его разговорами.
 * Строка дня отвечает на вопросы про ВСЁ оценённое: сколько оценено и сколько
 * критических, кто работал, средние баллы ИИ и человека и сколько уже проверено.
 *
 * Состояние (дни, открытый день, строки) живёт в CallQaView (useDayList): пока
 * открыта карточка разговора, вкладка размонтирована, а вернуться человек должен
 * на тот же экран дня. Здесь — вид и кнопки подбора: случайный звонок,
 * подтяжка из АТС, точечный поиск.
 */

const DAY_COLUMNS = 'sm:grid sm:grid-cols-[minmax(0,1fr)_4rem_4.5rem_5.5rem_1rem] sm:items-center sm:gap-4';

/* Сколько и чего: «94 звонка», «30 заявок». Единица у переписки СЗоВ — заявка. */
const unitsText = (n, subject) => {
    const unit = subjectUnit(subject);
    if (unit === 'заявка') return plural(n, 'заявка', 'заявки', 'заявок');
    if (unit === 'чат') return plural(n, 'чат', 'чата', 'чатов');
    return plural(n, 'звонок', 'звонка', 'звонков');
};
const evaluatedWord = (n) => plural(n, 'оценён', 'оценено', 'оценено');
const criticalText = (n) => `${n} ${plural(n, 'критическое', 'критических', 'критических')}`;
const round = (value) => (value == null ? null : Math.round(value));

function DayRow({ day, subject, onOpen }) {
    const info = dayTitle(day.day);
    const ai = round(day.ai_avg);
    const human = round(day.human_avg);
    const directions = (day.directions || []).map((d) => d.name);
    const who = [
        day.operators ? `${day.operators} ${plural(day.operators, 'сотрудник', 'сотрудника', 'сотрудников')}` : null,
        directions.length ? directions.slice(0, 2).join(', ') + (directions.length > 2 ? ` +${directions.length - 2}` : '') : null,
    ].filter(Boolean).join(' · ');
    const label = [info.title, `${day.evaluated} ${unitsText(day.evaluated, subject)} ${evaluatedWord(day.evaluated)}`,
        day.critical ? criticalText(day.critical) : null].filter(Boolean).join(', ');
    return (
        <button type="button" onClick={() => onOpen(day.day)} data-qa-day-tile={day.day} aria-label={label}
                className={`${DAY_ROW_CLASS} ${DAY_COLUMNS}`}>
            <div className="flex min-w-0 flex-1 items-center gap-3.5">
                <DateLeaf day={day} info={info} />
                <div className="min-w-0 grow basis-0">
                    <div className="flex items-baseline gap-1.5">
                        <span className="text-[16px] font-semibold tabular-nums text-slate-900">{day.evaluated}</span>
                        <span className="text-[13.5px] text-slate-700">{unitsText(day.evaluated, subject)}</span>
                    </div>
                    <div className="mt-0.5 truncate text-[12.5px] text-slate-500">
                        {day.critical > 0 && (
                            <span className="font-medium text-rose-600">{criticalText(day.critical)}{who ? ' · ' : ''}</span>
                        )}
                        {who}
                    </div>
                    <div className="mt-1 flex items-baseline gap-3 text-[12px] text-slate-400 sm:hidden">
                        <span>ИИ <Score value={ai} /></span>
                        <span>Человек <Score value={human} /></span>
                        <span>проверено <span className="font-medium tabular-nums text-slate-600">{day.reviewed || 0} из {day.evaluated}</span></span>
                    </div>
                </div>
            </div>
            <Score value={ai} className="hidden text-right text-[15px] sm:block" />
            <Score value={human} className="hidden text-right text-[15px] sm:block" />
            <span className="hidden text-right text-[13px] tabular-nums text-slate-600 sm:block">
                {day.reviewed || 0} из {day.evaluated}
            </span>
            <RowChevron />
        </button>
    );
}

function Overview({ days, subject, onOpenDay }) {
    const groups = monthGroups(days).map((group) => ({
        ...group,
        evaluated: group.days.reduce((sum, d) => sum + (d.evaluated || 0), 0),
        criticalTotal: group.days.reduce((sum, d) => sum + (d.critical || 0), 0),
    }));
    return (
        <div className="space-y-6">
            {groups.map((group) => (
                <MonthSection key={group.key} group={group} columns={DAY_COLUMNS}
                              aside={(
                                  <span className="text-[12.5px] text-slate-500">
                                      {group.evaluated} {unitsText(group.evaluated, subject)}
                                      {group.criticalTotal > 0 && <span className="text-rose-600"> · {criticalText(group.criticalTotal)}</span>}
                                  </span>
                              )}
                              head={(
                                  <>
                                      <span>День</span>
                                      <span className="text-right" title="Средний балл ИИ за день">ИИ</span>
                                      <span className="text-right" title="Средний балл человека за день">Человек</span>
                                      <span className="text-right" title="Проверено человеком из оценённых ИИ">Проверено</span>
                                      <span />
                                  </>
                              )}
                              renderDay={(day) => <DayRow day={day} subject={subject} onOpen={onOpenDay} />} />
            ))}
        </div>
    );
}

/* Итоги дня. Каждое число — один раз: «оценено» и «проверено» уже стоят в
   своих клетках, и подписи под средними их не повторяют. Число оценок под
   баллом человека — только когда оно расходится с «проверено» (проверку могли
   закрыть без своей оценки). */
function DaySummary({ day }) {
    const ai = round(day.ai_avg);
    const human = round(day.human_avg);
    const reviewed = day.reviewed || 0;
    const evaluated = day.evaluated || 0;
    const humanN = day.human_n || 0;
    return (
        <div className={`${iosCard} overflow-hidden`}>
            <dl className="grid grid-cols-2 gap-px bg-slate-100 sm:grid-cols-5">
                <DayStat wide label="Оценено ИИ" value={evaluated}
                         sub={day.critical ? criticalText(day.critical) : 'критических нет'}
                         subTone={day.critical ? 'font-medium text-rose-600' : 'text-slate-400'} />
                <DayStat label="Проверено" value={`${reviewed} из ${evaluated}`}>
                    <ShareBar share={evaluated ? reviewed / evaluated : 0} />
                </DayStat>
                <DayStat label="Поправили ИИ" value={reviewed ? day.corrected || 0 : null} />
                <DayStat label="Средний балл ИИ" value={ai} tone={ai != null ? scoreTone(ai) : null} />
                <DayStat label="Средний балл человека" value={human} tone={human != null ? scoreTone(human) : null}
                         sub={humanN && humanN !== reviewed
                             ? `${humanN} ${plural(humanN, 'оценка', 'оценки', 'оценок')}` : null} />
            </dl>
        </div>
    );
}

function DayScreen({ days, day, state, subject, titleId, onOpen, onOpenDay, onClose, onLoadMore, onRetry,
                     onOpenDigest }) {
    const items = state?.items || [];
    const meta = commonRowMeta(items);
    const { earlier, later } = dayNeighbours(days, day.day);
    const remaining = state?.total != null ? Math.max(0, state.total - (state.end || 0)) : 0;
    const hasMore = Boolean(state) && !state.loading && !state.error && remaining > 0;
    const loading = !state || state.loading;
    return (
        <div className="space-y-4">
            <DayHeader day={day.day} earlier={earlier} later={later} onClose={onClose} onOpenDay={onOpenDay}
                       titleId={titleId}
                       right={onOpenDigest && day.day !== NO_DAY
                           && <DigestLink onClick={() => onOpenDigest(day.day)} />} />
            <DaySummary day={day} />
            <section aria-label="Разговоры дня" className="space-y-2">
                <div className="px-1">
                    <span className={iosGroupLabel.replace('px-1 ', '')}>
                        {isChat(subject) ? (subject === SUBJECT_C2D_SNAPSHOT ? 'Заявки дня' : 'Чаты дня') : 'Звонки дня'}
                    </span>
                </div>
                <div className={`${iosCard} overflow-hidden`}>
                    {items.length > 0 && <QueueList items={items} onOpen={onOpen} meta={meta} reasonsLabel="Отметки" />}
                    {loading && (
                        <div className={`flex items-center justify-center gap-2 py-8 text-[13px] text-slate-500 ${items.length ? 'border-t border-slate-100' : ''}`}>
                            <Loader2 size={15} className="animate-spin" aria-hidden="true" />Загружаю разговоры…
                        </div>
                    )}
                    {state?.error && (
                        <div className="flex items-center justify-center gap-2 border-t border-slate-100 px-4 py-4 text-[13px] text-rose-600 first:border-t-0">
                            Не удалось загрузить разговоры дня
                            <button type="button" onClick={onRetry}
                                    className="rounded-lg px-2 py-1.5 font-medium text-blue-600 hover:bg-blue-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60">
                                Повторить
                            </button>
                        </div>
                    )}
                    {!loading && !state?.error && !items.length && (
                        <div className="flex flex-col items-center gap-2 px-6 py-10 text-center text-[13px] text-slate-500">
                            <CheckCircle2 size={24} className="text-slate-300" aria-hidden="true" />
                            За этот день под отбор ничего не подошло
                        </div>
                    )}
                    {hasMore && (
                        <button type="button" onClick={onLoadMore}
                                className="w-full border-t border-slate-100 py-3 text-[13px] font-medium text-blue-600 transition hover:bg-slate-50 focus-visible:bg-blue-50/60 focus-visible:outline-none">
                            Показать ещё {remaining}
                        </button>
                    )}
                </div>
            </section>
        </div>
    );
}

export default function EvaluationsList(props) {
    const { list, apiBaseUrl, withAccessTokenHeader, onOpen, showToast, subject = 'call',
            department, canPull = false, filters = null, onResetFilters, onFind = null,
            onOpenDigest = null, titleId = 'qa-evals-day-title', lead = null } = props;
    const chats = isChat(subject);
    const filtersActive = hasActiveFilters(filters);
    const headers = () => (withAccessTokenHeader ? withAccessTokenHeader() : {});
    const [busy, setBusy] = useState(false);
    const [pullBusy, setPullBusy] = useState(false);
    const randomRequest = useRef({ id: 0, controller: null });
    const pullRequest = useRef({ id: 0, department: null });
    /* Отдел, открытый СЕЙЧАС. Сравнивать ответ с `department` из замыкания
     * бесполезно: там лежит значение того рендера, в котором запрос ушёл, —
     * ровно оно же записано и в pullRequest. Ref переживает перерисовку и
     * показывает, на что человек смотрит в момент ответа. */
    const departmentRef = useRef(department);
    departmentRef.current = department;
    const filtersSignature = filtersKey(filters);

    /* Подбор случайного звонка — это несколько запросов ORDER BY random() и
       секунды ожидания. Сменили отдел или отбор, ушли со вкладки — ответ уже
       ни к чему: карточка открылась бы поверх другого экрана и про чужой отдел. */
    useEffect(() => () => randomRequest.current.controller?.abort(),
        [apiBaseUrl, subject, department, filtersSignature]);

    const days = list?.days;
    const openDay = list?.openDay ? (days || []).find((d) => d.day === list.openDay) : null;

    const randomCall = () => {
        if (!apiBaseUrl) { showToast?.('Бэкенд недоступен', 'error'); return; }
        randomRequest.current.controller?.abort();
        const controller = new AbortController();
        const requestId = randomRequest.current.id + 1;
        randomRequest.current = { id: requestId, controller };
        const requestDepartment = department;
        setBusy(true);
        // Подбор идёт по ТОМУ ЖЕ отбору, что и список: выбрав сотрудника и
        // период, человек ждёт звонок именно оттуда, а не из всего отдела.
        axios.get(`${apiBaseUrl}/api/ai-qa/random-call`,
            { params: { ...(department ? { department } : {}), ...filtersToParams(filters) },
              headers: headers(), signal: controller.signal })
            .then((r) => {
                if (requestId === randomRequest.current.id && departmentRef.current === requestDepartment) {
                    onOpen?.(r.data.call);
                }
            })
            .catch((e) => {
                if (!axios.isCancel(e) && requestId === randomRequest.current.id) {
                    showToast?.(e?.response?.data?.error || 'Не удалось получить случайный звонок', 'error');
                }
            })
            .finally(() => {
                if (requestId === randomRequest.current.id) setBusy(false);
            });
    };

    /* «Подтянуть из АТС» — сверх пула портала. Пул (звонки, подтянутые в журнал
     * кнопкой «Случайный звонок», но так и не оценённые) конечен; эта кнопка
     * идёт за новым звонком: СЗоВ — в Oktell, Тез КЦ — в Binotel, отдел продаж —
     * в свои касания CDR (запись приносит мост по заказу). Запись уходит в наше
     * хранилище, строка ложится как «не оценён», и оценки в журнале от этого не
     * появляется. */
    const pullCall = () => {
        if (!apiBaseUrl) { showToast?.('Бэкенд недоступен', 'error'); return; }
        // Запрос идёт в АТС и качает запись — это секунды, за которые человек
        // успевает сменить отдел или уйти со вкладки. Ответ применяем только
        // если он всё ещё про ТОТ отдел: иначе открылась бы карточка чужого.
        // Запрос при этом НЕ отменяем: звонок уже подтянут и лежит в пуле, и
        // обрыв соединения его оттуда не убрал бы — просто не открываем карточку.
        const requestId = pullRequest.current.id + 1;
        pullRequest.current = { id: requestId, department };
        setPullBusy(true);
        // Сотрудник и период берутся из отбора: без них подтяжка идёт «по всему
        // отделу за неделю», и выставленный рядом фильтр не значил бы ничего.
        // Направление и группа в АТС не уходят — они сужают ЛЮДЕЙ, а не звонки.
        axios.post(`${apiBaseUrl}/api/ai-qa/pull-call`,
            { ...(department ? { department } : {}), count: 1, ...pullParamsFromFilters(filters) },
            { headers: headers() })
            .then((r) => {
                if (requestId !== pullRequest.current.id
                    || departmentRef.current !== pullRequest.current.department) return;
                const call = (r.data?.calls || [])[0] || r.data?.call;
                if (!call) { showToast?.('АТС не вернула подходящий звонок', 'error'); return; }
                // Запись Binotel докачивается не мгновенно. Карточку открываем
                // сразу — сервер добирает запись сам, — но говорим об ожидании,
                // иначе долгая загрузка читается как зависание.
                showToast?.(call.audio_pending
                    ? 'Звонок подтянут — качаю запись и оцениваю'
                    : 'Звонок подтянут — оцениваю', 'success');
                onOpen?.({ id: call.id, subject: SUBJECT_IMPORTED_CALL,
                           operator: call.operator_name, datetime: call.datetime });
            })
            .catch((e) => {
                if (requestId !== pullRequest.current.id) return;
                showToast?.(e?.response?.data?.error
                    || 'Не удалось подтянуть звонок из АТС', 'error');
            })
            .finally(() => { if (requestId === pullRequest.current.id) setPullBusy(false); });
    };

    const total = (days || []).reduce((sum, d) => sum + (d.evaluated || 0), 0);
    const critical = (days || []).reduce((sum, d) => sum + (d.critical || 0), 0);

    if (openDay) {
        return (
            <div style={{ fontFamily: APPLE_FONT }}>
                <DayScreen days={days} day={openDay} state={list.dayItems[openDay.day]} subject={subject}
                           titleId={titleId} onOpen={onOpen}
                           onOpenDay={(day) => { list.open(day); }}
                           onClose={list.close}
                           onLoadMore={() => list.loadDay(openDay.day)}
                           onRetry={() => list.loadDay(openDay.day)}
                           onOpenDigest={onOpenDigest} />
            </div>
        );
    }

    return (
        <div style={{ fontFamily: APPLE_FONT }} className="space-y-4">
            {lead}
            <div className="flex flex-col items-stretch justify-between gap-3 sm:flex-row sm:items-center">
                <p className="px-1 text-[13px] text-slate-500">
                    {days && total > 0 ? (
                        <>
                            <b className="font-semibold text-slate-900">{total}</b>
                            {' '}{unitsText(total, subject)} {evaluatedWord(total)} ИИ
                            {critical > 0 && <span className="font-medium text-rose-600"> · {criticalText(critical)}</span>}
                        </>
                    ) : (chats ? 'Переписки, оценённые ИИ' : 'Звонки, оценённые ИИ')}
                </p>
                <div className="flex shrink-0 flex-wrap items-center gap-2">
                    <button type="button" onClick={() => list?.reload()} disabled={days === null}
                            className={`${iosBtnSecondary} !px-3`} title="Обновить список" aria-label="Обновить список">
                        <RefreshCw size={14} className={days === null ? 'animate-spin' : ''} />
                    </button>
                    {/* Точечный подбор — рядом со случайным: конкретный звонок по
                        номеру телефона, сотруднику и периоду. У чатов свой подбор —
                        в шапке вкладки «Чаты», здесь его не дублируем. */}
                    {!chats && onFind && (
                        <button type="button" onClick={onFind} disabled={busy || pullBusy}
                                className={`${iosBtnSecondary} disabled:cursor-not-allowed disabled:opacity-50`}
                                title="Найти конкретный звонок по номеру телефона, сотруднику и периоду">
                            <Search size={14} />Найти звонок
                        </button>
                    )}
                    {!chats && canPull && (
                        <button type="button" onClick={pullCall} disabled={pullBusy || busy}
                                className={`${iosBtnSecondary} disabled:cursor-not-allowed disabled:opacity-50`}
                                title="Взять новый звонок прямо из АТС (Oktell / Binotel / касания CDR) и оценить его">
                            {pullBusy ? <Loader2 size={14} className="animate-spin" /> : <PhoneIncoming size={14} />}
                            {pullBusy ? 'Тяну из АТС…' : 'Из АТС'}
                        </button>
                    )}
                    {!chats && (
                        <button type="button" onClick={randomCall} disabled={busy || pullBusy} className={iosBtnPrimary}
                                title={canPull
                                    ? 'Случайный звонок из подтянутых, но не оценённых в журнале'
                                    : 'Случайный звонок из оценённых человеком — проверить, как его оценит ИИ'}>
                            {busy ? <Loader2 size={15} className="animate-spin" /> : <Shuffle size={15} />}
                            {busy ? 'Подбираю звонок…' : 'Оценить случайный звонок'}
                        </button>
                    )}
                    {props.actions}
                </div>
            </div>

            {days === null ? (
                <div className={`${iosCard} flex items-center justify-center gap-2 px-6 py-12 text-slate-500`} role="status">
                    <Loader2 size={20} className="animate-spin" aria-hidden="true" />Загрузка оценок…
                </div>
            ) : list.error ? (
                <div className={`${iosCard} flex flex-col items-center gap-3 px-6 py-12 text-center`} role="alert">
                    <AlertCircle size={25} className="text-rose-500" />
                    <p className="text-[13px] font-medium text-slate-700">Не удалось загрузить оценки</p>
                    <button type="button" onClick={() => list.reload()} className={iosBtnSecondary}><RefreshCw size={14} />Повторить</button>
                </div>
            ) : days.length === 0 ? (
                /* «Пока ничего не оценено» при активном отборе — неправда:
                   оценки есть, просто не под этот срез. Человеку нужно снять
                   фильтр, а не подбирать первый звонок заново. */
                <div className={`${iosCard} flex flex-col items-center gap-2 px-6 py-14 text-center`}>
                    <ClipboardList size={26} className="text-slate-300" />
                    <p className="text-[13px] text-slate-500">
                        {filtersActive
                            ? 'Под выбранные фильтры ничего не нашлось.'
                            : (chats ? 'Пока ни одна переписка не оценена ИИ.'
                                     : 'Пока ни один звонок не оценён ИИ.')}
                    </p>
                    <p className="text-[12px] text-slate-400">
                        {filtersActive
                            ? 'Снимите часть фильтров или расширьте период.'
                            : (chats
                                ? 'Нажмите кнопку подбора выше, чтобы получить первую оценку.'
                                : 'Нажмите «Оценить случайный звонок», чтобы протестировать оценку.')}
                    </p>
                    {filtersActive && onResetFilters && (
                        <button type="button" onClick={onResetFilters} className={iosBtnSecondary}>
                            Сбросить фильтры
                        </button>
                    )}
                </div>
            ) : (
                <Overview days={days} subject={subject} onOpenDay={(day) => list.open(day, { fromList: true })} />
            )}
        </div>
    );
}
