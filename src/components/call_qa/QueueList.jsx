import React from 'react';
import DealBadge from './DealBadge';
import {
    ShieldAlert, CircleHelp, Server, Volume1, ImageOff, CheckCircle2, Sparkles,
    MessageSquare, PhoneCall, Users, ChevronRight, RotateCcw,
} from 'lucide-react';
import { IosBadge, scoreTone } from '../ui/ios';
import { itemKey, rowReasons, STALE_MARK, timeOf } from './queueDayRules';

/* Разговоры одного дня очереди ревью — список на экране дня (QueueDays).
 * Метки отвечают на один вопрос — что открывать первым, поэтому на бейдже
 * короткая подпись, а полная формулировка уходит в подсказку. */

// Знание о субъектах — в общем модуле: переписок теперь три вида (Wazzup у ОП,
// Chat2Desk у СЗоВ, ChatApp у Тез КЦ), и сравнение с одной строкой открывало бы
// чат как звонок. Реэкспорт оставлен: на эти имена ссылается CallQaView.
export { SUBJECT_WZ_EPISODE as SUBJECT_CHAT, isChat, subjectTitle } from './subjects';
import { isChat, subjectTitle, SUBJECT_IMPORTED_CALL, SOURCE_LABEL } from './subjects';

// Порядок ключей повторяет call_qa/review/queue.REASON_PRIORITY: бэкенд отдаёт
// причины по убыванию серьёзности, поэтому первая метка — главная.
// `loud` — причина, которая меняет, как смотреть разговор (нарушение, ненадёжный
// звук или вложение): она стоит цветным бейджем. Остальные — тихим словом с
// иконкой: «Спорное» у ОП стоит на восьми разговорах из десяти, и бейдж на каждой
// строке был бы шумом, а не сигналом.
export const REASON = {
    critical: { tone: 'red',   label: 'Критическое',  Icon: ShieldAlert, loud: true,
                hint: 'ИИ нашёл нарушение по критическому критерию — подтверждает человек' },
    lowconf:  { tone: 'amber', label: 'Спорное',      Icon: CircleHelp,
                hint: 'ИИ не уверен хотя бы в одном критерии' },
    pending:  { tone: 'blue',  label: 'Данные ПО',    Icon: Server,
                hint: 'Критерий проверяется по данным в ПО — ИИ его не оценивал' },
    asr:      { tone: 'amber', label: 'Слабый звук',  Icon: Volume1, loud: true,
                hint: 'Низкая уверенность распознавания речи' },
    media:    { tone: 'amber', label: 'Вложение',     Icon: ImageOff, loud: true,
                hint: 'Вложение не удалось прочитать — его содержание не оценивалось' },
    ok:       { tone: 'green', label: 'Без флагов',   Icon: CheckCircle2,
                hint: 'Поводов для проверки человеком ИИ не нашёл' },
    new:      { tone: 'slate', label: 'Новое',        Icon: Sparkles, hint: '' },
};
// «Устарела» — не причина очереди, а состояние оценки; стоит рядом с причинами.
export const STALE = {
    tone: 'amber', label: 'Устарела', Icon: RotateCcw,
    hint: 'Конфигурация ИИ (промпт, критерии или база знаний) изменилась после этой оценки. '
        + 'При открытии показывается прежняя оценка; пересчёт — только кнопкой «Переоценить» в карточке',
};
const VISIBLE_REASONS = 2;   // бейджей в строке; остальные — счётчиком, чтобы строка не рябила

const ICON_TONE = { red: 'text-rose-500', amber: 'text-amber-500', blue: 'text-blue-500', green: 'text-emerald-500' };
export const SCORE_TEXT = { green: 'text-emerald-600', amber: 'text-amber-600', red: 'text-rose-600', slate: 'text-slate-400' };

// Колонки списка на компьютере — одни на заголовок и строки, иначе они разъедутся.
// Когда у строк нечего сказать о причинах (всё общее названо над списком), колонки
// «Почему в очереди» нет вовсе: пустая колонка с заголовком выглядела бы поломкой.
const COLUMNS = 'sm:grid sm:grid-cols-[3rem_minmax(0,1fr)_minmax(0,15rem)_4.5rem_4.5rem_1rem] sm:items-center sm:gap-4';
const COLUMNS_NO_REASONS = 'sm:grid sm:grid-cols-[3rem_minmax(0,1fr)_4.5rem_4.5rem_1rem] sm:items-center sm:gap-4';

/** Балл ИИ: по нему решают, что открывать первым. «/90» — сколько из 100 ИИ проверил сам. */
function AiScore({ score, unchecked = 0, className = '' }) {
    if (score == null) return <span className={`text-slate-300 ${className}`} aria-label="Балла ИИ нет">—</span>;
    const hint = unchecked > 0
        ? `Балл ИИ ${score} из 100. Из них ${unchecked} зачтено без проверки: эти критерии проверяются по данным в ПО.`
        : `Балл ИИ ${score} из 100 — все критерии проверены по транскрипту.`;
    return (
        <span title={hint} className={`tabular-nums ${className}`}>
            <span className="sr-only">Балл ИИ </span>
            <span className={`font-semibold ${SCORE_TEXT[scoreTone(score)]}`}>{score}</span>
            {unchecked > 0 && <span className="text-[11px] font-normal text-slate-400">/{100 - unchecked}</span>}
        </span>
    );
}

/** Балл человека по той же шкале — рядом с баллом ИИ: расхождение видно по цвету. */
function HumanScore({ score, className = '' }) {
    if (score == null) return <span className={`text-slate-300 ${className}`} aria-label="Балла человека нет">—</span>;
    const value = Math.round(Number(score));
    return (
        <span title={`Балл человека ${value} из 100 — журнал оценок или своя оценка в карточке`}
              className={`tabular-nums ${className}`}>
            <span className="sr-only">, балл человека </span>
            <span className={`font-semibold ${SCORE_TEXT[scoreTone(value)]}`}>{value}</span>
        </span>
    );
}

function LoudChips({ reasons }) {
    const list = reasons.map((key) => ({ key, ...REASON[key] }));
    const shown = list.slice(0, VISIBLE_REASONS);
    const hidden = list.slice(VISIBLE_REASONS);
    return (
        <>
            {shown.map((m) => (
                <IosBadge key={m.key} tone={m.tone} title={m.hint} className="!px-2 !py-0.5">
                    <m.Icon size={11} aria-hidden="true" />{m.label}
                </IosBadge>
            ))}
            {hidden.length > 0 && (
                <IosBadge tone="slate" title={hidden.map((m) => m.hint || m.label).join('\n')} className="!px-2 !py-0.5">
                    +{hidden.length}
                </IosBadge>
            )}
        </>
    );
}

/** Тихая метка: иконка в цвет причины и серое слово, полная формулировка — в подсказке. */
export function QuietMark({ meta }) {
    return (
        <span className="inline-flex items-center gap-1 whitespace-nowrap" title={meta.hint}>
            <meta.Icon size={12} className={ICON_TONE[meta.tone] || 'text-slate-400'} aria-hidden="true" />
            {meta.label}
        </span>
    );
}

function Reasons({ loud, quiet }) {
    if (!loud.length && !quiet.length) return null;
    return (
        <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1 text-[12px] text-slate-500">
            {loud.length > 0 && <LoudChips reasons={loud} />}
            {quiet.map(({ key, meta }) => <QuietMark key={key} meta={meta} />)}
        </div>
    );
}

/* Список — как таблица в «Почте» или «Finder» на Mac: на компьютере у строки
 * колонки с заголовком (время, сотрудник, почему в очереди, баллы ИИ и человека),
 * баллы — числами в своей колонке, а не пилюлями. На телефоне колонки
 * складываются: баллы справа столбиком, причины под именем.
 *
 * Причины, общие для всех разговоров дня (`common`, туда же попадает «устарела»,
 * если она у всех), названы над списком и здесь не повторяются; одинаковые у всех
 * строк источник и направление (`meta`) — тоже. */
const rowMarks = (c, common) => {
    const reasons = rowReasons(c.reasons, common).filter((key) => REASON[key]);
    const loud = reasons.filter((key) => REASON[key].loud);
    const quiet = reasons.filter((key) => !REASON[key].loud).map((key) => ({ key, meta: REASON[key] }));
    if (c.stale && !common.includes(STALE_MARK)) quiet.push({ key: STALE_MARK, meta: STALE });
    return { loud, quiet };
};

/* reasonsLabel — подпись колонки меток: в очереди это «почему в очереди», а на
 * экране дня «Звонков» и «Чатов» те же метки (критическое, плохой звук,
 * вложение) — просто отметки уже оценённого разговора. */
export default function QueueList({ items, onOpen, common = [], meta = {}, reasonsLabel = 'Почему в очереди' }) {
    const marks = new Map(items.map((c) => [itemKey(c), rowMarks(c, common)]));
    const withReasons = [...marks.values()].some((m) => m.loud.length || m.quiet.length);
    const columns = withReasons ? COLUMNS : COLUMNS_NO_REASONS;
    return (
        <div role="list">
            <div className={`hidden border-b border-slate-100 px-5 py-2 text-[11px] font-medium uppercase tracking-wide text-slate-400 ${columns}`}
                 aria-hidden="true">
                <span>Время</span>
                <span>Сотрудник</span>
                {withReasons && <span>{reasonsLabel}</span>}
                <span className="text-right">ИИ</span>
                <span className="text-right">Человек</span>
                <span />
            </div>
            {items.map((c) => {
                const Icon = isChat(c.subject) ? MessageSquare : PhoneCall;
                const time = timeOf(c.datetime);
                const { loud, quiet } = marks.get(itemKey(c));
                // id у calls и imported_calls — независимые последовательности, и в одной
                // очереди встречаются оба вида: без пометки две соседние строки читались бы
                // как один звонок. Когда у всех строк один вид — пометка не нужна.
                const imported = c.subject === SUBJECT_IMPORTED_CALL && !meta.subject;
                const direction = meta.direction ? '' : c.direction;
                return (
                    <div role="listitem" key={itemKey(c)} className="border-b border-slate-100 last:border-b-0">
                        <button type="button" onClick={() => onOpen?.(c)} data-qa-row={itemKey(c)}
                            className={`flex w-full items-start gap-3 px-4 py-3 text-left transition hover:bg-slate-50 focus-visible:bg-blue-50/60 focus-visible:outline-none active:bg-slate-100 sm:px-5 ${columns}`}>
                            <span className="w-10 shrink-0 pt-px text-[13px] tabular-nums text-slate-500 sm:w-auto sm:pt-0">
                                {time || '—'}
                            </span>
                            <div className="min-w-0 flex-1">
                                <div className="flex min-w-0 items-center gap-1.5">
                                    <span className="truncate text-[14px] font-medium text-slate-900">{c.operator}</span>
                                    {c.deal && <span className="hidden sm:contents"><DealBadge deal={c.deal} /></span>}
                                </div>
                                <div className="mt-0.5 flex min-w-0 items-center gap-1 text-[12px] text-slate-500">
                                    <Icon size={12} className="shrink-0 text-slate-400" aria-hidden="true" />
                                    <span className="truncate">
                                        {subjectTitle(c.subject, c.id)}
                                        {imported && <span title={SOURCE_LABEL[c.subject]}> из АТС</span>}
                                        {direction ? ` · ${direction}` : ''}
                                    </span>
                                </div>
                                {/* На телефоне причины — под именем, сделка — рядом с ними. */}
                                {(loud.length > 0 || quiet.length > 0 || c.deal) && (
                                    <div className="mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 sm:hidden">
                                        <Reasons loud={loud} quiet={quiet} />
                                        {c.deal && <DealBadge deal={c.deal} />}
                                    </div>
                                )}
                            </div>
                            {withReasons && <div className="hidden min-w-0 sm:block"><Reasons loud={loud} quiet={quiet} /></div>}
                            {/* Телефон: баллы столбиком справа — ИИ крупнее, человек под ним. */}
                            <div className="flex shrink-0 flex-col items-end gap-0.5 text-right sm:hidden">
                                <AiScore score={c.ai_score} unchecked={c.unchecked_weight || 0} className="text-[15px]" />
                                <span className="inline-flex items-center gap-1 text-[12px]">
                                    <Users size={11} className="text-slate-400" aria-hidden="true" />
                                    <HumanScore score={c.human_score} />
                                </span>
                            </div>
                            <AiScore score={c.ai_score} unchecked={c.unchecked_weight || 0}
                                     className="hidden text-right text-[15px] sm:block" />
                            <HumanScore score={c.human_score} className="hidden text-right text-[15px] sm:block" />
                            <ChevronRight size={16} className="shrink-0 self-center text-slate-300" aria-hidden="true" />
                        </button>
                    </div>
                );
            })}
        </div>
    );
}
