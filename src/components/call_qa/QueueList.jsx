import React from 'react';
import {
    ShieldAlert, Clock, Server, Volume1, ImageOff, CheckCircle2, Sparkles,
    RotateCcw, MessageSquare, PhoneCall, Users,
} from 'lucide-react';
import { iosCard, IosBadge, scoreTone } from '../ui/ios';

/* Строка очереди ревью — общая для вкладок «Очередь ревью» и «Чаты»: там один и
 * тот же список /api/ai-qa/review-queue, отличается только фильтр по субъекту.
 * Метки отвечают на один вопрос — что открывать первым, поэтому на бейдже
 * короткая подпись, а полная формулировка уходит в подсказку. */

export const SUBJECT_CHAT = 'wz_episode';
export const isChat = (subject) => subject === SUBJECT_CHAT;
export const subjectTitle = (subject, id) => (isChat(subject) ? `Чат #${id}` : `Звонок #${id}`);

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

export default function QueueList({ items, onOpen }) {
    return (
        <div className="space-y-2.5">
            {items.map((c) => {
                const Icon = isChat(c.subject) ? MessageSquare : PhoneCall;
                return (
                    <button key={`${c.subject || 'call'}-${c.id}`} type="button" onClick={() => onOpen?.(c)}
                        className={`${iosCard} flex w-full flex-col items-stretch justify-between gap-2.5 p-3.5 text-left transition hover:ring-blue-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 active:scale-[0.995] sm:flex-row sm:items-center`}>
                        <div className="min-w-0">
                            <div className="flex items-center gap-2">
                                <Icon size={14} className="shrink-0 text-slate-400" aria-hidden="true" />
                                <span className="truncate text-[14px] font-semibold text-slate-900">
                                    {subjectTitle(c.subject, c.id)}
                                </span>
                                {c.stale && (
                                    <IosBadge tone="amber" title="Конфигурация ИИ (промпт, критерии или база знаний) изменилась после этой оценки. При открытии показывается прежняя оценка; пересчёт — только кнопкой «Переоценить» в карточке.">
                                        <RotateCcw size={11} aria-hidden="true" />устарела
                                    </IosBadge>
                                )}
                            </div>
                            {/* Направление ушло в подпись: в строке остаются только метки,
                                по которым выбирают, что смотреть. */}
                            <p className="mt-0.5 truncate text-[12px] text-slate-400">
                                {c.operator} · {c.direction} · {c.datetime}
                            </p>
                        </div>
                        <div className="flex flex-wrap items-center gap-1.5 sm:shrink-0 sm:justify-end">
                            <ScoreChip score={c.ai_score} unchecked={c.unchecked_weight || 0} />
                            {c.human_score != null && (
                                <IosBadge tone="green" title="Балл человека по этой же шкале"
                                          className="tabular-nums">
                                    <Users size={11} aria-hidden="true" />{c.human_score}
                                </IosBadge>
                            )}
                            <ReasonChips reasons={c.reasons} />
                        </div>
                    </button>
                );
            })}
        </div>
    );
}
