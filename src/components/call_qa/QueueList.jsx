import React from 'react';
import DealBadge from './DealBadge';
import {
    ShieldAlert, Clock, Server, Volume1, ImageOff, CheckCircle2, Sparkles,
    RotateCcw, MessageSquare, PhoneCall, Users, ChevronRight,
} from 'lucide-react';
import { IosBadge, scoreTone } from '../ui/ios';
import { itemKey, rowReasons, timeOf } from './queueDayRules';

/* Строка очереди ревью — общая для вкладок «Очередь ревью» и «Чаты»: там один и
 * тот же список /api/ai-qa/review-queue, отличается только фильтр по субъекту.
 * Метки отвечают на один вопрос — что открывать первым, поэтому на бейдже
 * короткая подпись, а полная формулировка уходит в подсказку. */

// Знание о субъектах — в общем модуле: переписок теперь три вида (Wazzup у ОП,
// Chat2Desk у СЗоВ, ChatApp у Тез КЦ), и сравнение с одной строкой открывало бы
// чат как звонок. Реэкспорт оставлен: на эти имена ссылается CallQaView.
export { SUBJECT_WZ_EPISODE as SUBJECT_CHAT, isChat, subjectTitle } from './subjects';
import { isChat, subjectTitle, SUBJECT_IMPORTED_CALL, SOURCE_LABEL } from './subjects';

// Порядок ключей повторяет call_qa/review/queue.REASON_PRIORITY: бэкенд отдаёт
// причины по убыванию серьёзности, поэтому первая метка — главная.
export const REASON = {
    critical: { tone: 'red',   label: 'Критическое',  Icon: ShieldAlert,
                hint: 'ИИ нашёл нарушение по критическому критерию — подтверждает человек' },
    lowconf:  { tone: 'amber', label: 'Спорное',      Icon: Clock,
                hint: 'ИИ не уверен хотя бы в одном критерии' },
    pending:  { tone: 'blue',  label: 'Данные ПО',    Icon: Server,
                hint: 'Критерий проверяется по данным в ПО — ИИ его не оценивал' },
    asr:      { tone: 'amber', label: 'Слабый звук',  Icon: Volume1,
                hint: 'Низкая уверенность распознавания речи' },
    media:    { tone: 'amber', label: 'Вложение',     Icon: ImageOff,
                hint: 'Вложение не удалось прочитать — его содержание не оценивалось' },
    ok:       { tone: 'green', label: 'Без флагов',   Icon: CheckCircle2,
                hint: 'Поводов для проверки человеком ИИ не нашёл' },
    new:      { tone: 'slate', label: 'Новое',        Icon: Sparkles, hint: '' },
};
const VISIBLE_REASONS = 2;   // остальные — счётчиком, чтобы строка не рябила

/** Балл ИИ: по нему решают, что открывать первым. */
function ScoreChip({ score, unchecked = 0 }) {
    if (score == null) return null;
    const hint = unchecked > 0
        ? `Балл ИИ ${score} из 100. Из них ${unchecked} зачтено без проверки: эти критерии проверяются по данным в ПО.`
        : `Балл ИИ ${score} из 100 — все критерии проверены по транскрипту.`;
    return (
        <IosBadge tone={scoreTone(score)} title={hint} className="tabular-nums">
            <Sparkles size={11} aria-hidden="true" />{score}
            {unchecked > 0 && <span className="font-normal opacity-70">/{100 - unchecked}</span>}
        </IosBadge>
    );
}

function ReasonChips({ reasons }) {
    const list = (reasons || []).map((key) => ({ key, ...(REASON[key] || REASON.new) }));
    const shown = list.slice(0, VISIBLE_REASONS);
    const hidden = list.slice(VISIBLE_REASONS);
    return (
        <>
            {shown.map((m) => (
                <IosBadge key={m.key} tone={m.tone} title={m.hint}>
                    <m.Icon size={11} aria-hidden="true" />{m.label}
                </IosBadge>
            ))}
            {hidden.length > 0 && (
                <IosBadge tone="slate" title={hidden.map((m) => m.hint || m.label).join('\n')}>
                    +{hidden.length}
                </IosBadge>
            )}
        </>
    );
}

/* Строки одного дня очереди — внутри карточки дня, списком с разделителями, как
 * таблица в iOS: карточка в карточке была бы лишней рамкой. День уже в заголовке,
 * поэтому у строки — только время. Причины, общие для всех ждущих разговоров дня
 * (`common`), названы на карточке дня и здесь не повторяются. */
export default function QueueList({ items, onOpen, common = [] }) {
    return (
        <ul className="divide-y divide-slate-100">
            {items.map((c) => {
                const Icon = isChat(c.subject) ? MessageSquare : PhoneCall;
                const time = timeOf(c.datetime);
                return (
                    <li key={itemKey(c)}>
                        <button type="button" onClick={() => onOpen?.(c)}
                            className="flex w-full flex-col items-stretch gap-2 px-4 py-3 text-left transition hover:bg-slate-50 focus-visible:bg-slate-50 focus-visible:outline-none active:bg-slate-100 sm:flex-row sm:items-center sm:justify-between">
                            <div className="flex min-w-0 items-start gap-3">
                                {/* Время — в своей колонке: по нему глаз идёт вниз по дню. */}
                                <span className="w-10 shrink-0 pt-px text-[12.5px] font-medium tabular-nums text-slate-400">
                                    {time || '—'}
                                </span>
                                <div className="min-w-0">
                                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                                        <Icon size={13} className="shrink-0 text-slate-400" aria-hidden="true" />
                                        <span className="truncate text-[13.5px] font-medium text-slate-900">{c.operator}</span>
                                        {/* id у calls и imported_calls — независимые последовательности,
                                            и у СЗоВ/Тез КЦ в одной очереди встречаются оба вида: без
                                            пометки две соседние строки читались бы как один звонок. */}
                                        {c.subject === SUBJECT_IMPORTED_CALL && (
                                            <IosBadge tone="blue" title={SOURCE_LABEL[c.subject]} className="!px-2 !py-0.5">из АТС</IosBadge>
                                        )}
                                        {c.stale && (
                                            <IosBadge tone="amber" className="!px-2 !py-0.5"
                                                      title="Конфигурация ИИ (промпт, критерии или база знаний) изменилась после этой оценки. При открытии показывается прежняя оценка; пересчёт — только кнопкой «Переоценить» в карточке.">
                                                <RotateCcw size={11} aria-hidden="true" />устарела
                                            </IosBadge>
                                        )}
                                        <DealBadge deal={c.deal} />
                                    </div>
                                    <p className="mt-0.5 truncate text-[12px] text-slate-400">
                                        {subjectTitle(c.subject, c.id)} · {c.direction}
                                    </p>
                                </div>
                            </div>
                            <div className="flex flex-wrap items-center gap-1.5 pl-[3.25rem] sm:shrink-0 sm:justify-end sm:pl-0">
                                <ScoreChip score={c.ai_score} unchecked={c.unchecked_weight || 0} />
                                {c.human_score != null && (
                                    <IosBadge tone="green" title="Балл человека по этой же шкале"
                                              className="tabular-nums">
                                        <Users size={11} aria-hidden="true" />{c.human_score}
                                    </IosBadge>
                                )}
                                <ReasonChips reasons={rowReasons(c.reasons, common)} />
                                <ChevronRight size={15} className="hidden text-slate-300 sm:block" aria-hidden="true" />
                            </div>
                        </button>
                    </li>
                );
            })}
        </ul>
    );
}
