import React, { useMemo, useState } from 'react';
import { BookOpen, Plus, Users, CalendarCheck2, Archive, Pencil, Trash2, ListChecks } from 'lucide-react';
import { iosCard, iosBtnPrimary, IosBadge, IosMenu, IosHint } from '../ui/ios';
import { SearchField, ViewSwitcher, EmptyBlock, LoadingBlock, CoverageBar } from './pieces';
import {
    FAMILY_BASE, FAMILY_CORPORATE, FAMILY_LABELS, TOPIC_KIND_LABELS,
    VIEW_CARDS, VIEW_ROWS, TOPIC_VIEWS,
    remainingCount, coveragePercent,
    formatDuration, formatDayShort, pluralPeople, pluralSessions, tileTone, initials,
} from './constants';

/* Вкладка «По темам».
 *
 * Отвечает на вопрос «по каким темам в этом месяце проводили тренинги и как
 * идут дела» — поэтому единица показа здесь ТЕМА, а не занятие. Занятия видно,
 * когда тему раскрыли.
 *
 * Карточки против строк — это переключатель вида, а не замена одного другим:
 * карточка отвечает «что за тема и как идёт охват», строки — «сравни темы
 * между собой». У корпоративной темы в карточке есть действие, у базовой нет —
 * поэтому пустого места в базовой карточке быть не должно, и раскладка у них
 * разная по составу, а не по оформлению.
 */

// «Архив» — отдельный срез, а не строка в списке. Заархивированные темы не
// должны мешаться в рабочем списке, но и исчезать навсегда им нельзя: архив это
// единственный способ убрать тему с историей, и вернуть её надо откуда-то.
const SCOPE_ARCHIVE = 'archive';

const FAMILY_FILTERS = [
    { key: 'all', label: 'Все' },
    { key: FAMILY_BASE, label: FAMILY_LABELS[FAMILY_BASE] },
    { key: FAMILY_CORPORATE, label: FAMILY_LABELS[FAMILY_CORPORATE] },
    { key: SCOPE_ARCHIVE, label: 'Архив' },
];

export default function TopicsTab({
    month,
    summaries: allSummaries,
    loading,
    canManage,
    view,
    onViewChange,
    onCreateTopic,
    onEditTopic,
    onArchiveTopic,
    onDeleteTopic,
    onRollout,
    onOpenTopic,
    onAddSession,
}) {
    const [family, setFamily] = useState('all');
    const [search, setSearch] = useState('');
    const [onlyRemaining, setOnlyRemaining] = useState(false);

    // Живые темы и архив разведены сразу: архивная тема не должна попадать ни в
    // «Все», ни в счётчики — иначе «Корпоративные 5» при трёх рабочих.
    const summaries = useMemo(
        () => (allSummaries || []).filter((item) => !item.isArchivedTopic),
        [allSummaries],
    );
    const archived = useMemo(
        () => (allSummaries || []).filter((item) => item.isArchivedTopic),
        [allSummaries],
    );

    const counts = useMemo(() => ({
        all: summaries.length,
        [FAMILY_BASE]: summaries.filter((item) => item.family === FAMILY_BASE).length,
        [FAMILY_CORPORATE]: summaries.filter((item) => item.family === FAMILY_CORPORATE).length,
        [SCOPE_ARCHIVE]: archived.length,
    }), [summaries, archived]);

    const visible = useMemo(() => {
        const needle = search.trim().toLowerCase();
        const source = family === SCOPE_ARCHIVE ? archived : summaries;
        return source.filter((item) => {
            if (family !== 'all' && family !== SCOPE_ARCHIVE && item.family !== family) return false;
            if (onlyRemaining && remainingCount(item) === 0) return false;
            if (needle && !String(item.title).toLowerCase().includes(needle)) return false;
            return true;
        });
    }, [summaries, archived, family, onlyRemaining, search]);

    const hasFilters = family !== 'all' || Boolean(search.trim()) || onlyRemaining;
    const remainingTotal = useMemo(
        () => summaries.reduce((acc, item) => acc + remainingCount(item), 0),
        [summaries],
    );

    const topicMenu = (summary) => {
        if (!canManage || summary.family !== FAMILY_CORPORATE || !summary.topic) return [];
        return [
            {
                key: 'rollout',
                label: 'Провести пачке',
                icon: ListChecks,
                onSelect: () => onRollout(summary.topic),
            },
            { key: 'edit', label: 'Изменить тему', icon: Pencil, onSelect: () => onEditTopic(summary.topic) },
            {
                key: 'archive',
                label: summary.topic.is_archived ? 'Вернуть из архива' : 'В архив',
                icon: Archive,
                separatorBefore: true,
                onSelect: () => onArchiveTopic(summary.topic, !summary.topic.is_archived),
            },
            // Удаление предлагаем только у темы без истории: у темы с
            // проведёнными занятиями удаление обнулило бы им привязку, и охват
            // прошлых месяцев перестал бы сходиться. Сервер это тоже запрещает.
            summary.totalSessions === 0 && {
                key: 'delete',
                label: 'Удалить тему',
                icon: Trash2,
                danger: true,
                onSelect: () => onDeleteTopic(summary.topic),
            },
        ].filter(Boolean);
    };

    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2 px-1">
                <div className="flex rounded-xl bg-slate-100 p-1">
                    {FAMILY_FILTERS.filter(
                        (item) => item.key !== SCOPE_ARCHIVE || counts[SCOPE_ARCHIVE] > 0,
                    ).map((item) => (
                        <button
                            key={item.key}
                            type="button"
                            onClick={() => setFamily(item.key)}
                            className={`flex items-center gap-1.5 whitespace-nowrap rounded-[9px] px-3 py-1.5 text-[12.5px] font-semibold transition-all ${
                                family === item.key
                                    ? 'bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.12)]'
                                    : 'text-slate-500 hover:text-slate-700'
                            }`}
                        >
                            {item.label}
                            <span className="tabular-nums text-slate-400">{counts[item.key] ?? 0}</span>
                        </button>
                    ))}
                </div>

                {/* Фильтр «есть незакрытый охват» — единственное, что на этом
                    экране требует действия, поэтому он отдельной кнопкой, а не
                    строкой в выпадающем списке. Показываем только когда есть
                    что фильтровать. */}
                {remainingTotal > 0 && (
                    <button
                        type="button"
                        onClick={() => setOnlyRemaining((prev) => !prev)}
                        aria-pressed={onlyRemaining}
                        className={`flex items-center gap-1.5 whitespace-nowrap rounded-full px-3 py-1.5 text-[12px] font-medium transition ${
                            onlyRemaining
                                ? 'bg-slate-900 text-white'
                                : 'text-slate-500 hover:bg-slate-100 hover:text-slate-700'
                        }`}
                    >
                        <Users size={12} />
                        Есть кому провести
                        <span className="tabular-nums font-semibold">{remainingTotal}</span>
                    </button>
                )}

                <SearchField value={search} onChange={setSearch} placeholder="Название темы" />
                <ViewSwitcher value={view} onChange={onViewChange} views={TOPIC_VIEWS} />

                {canManage && (
                    <button type="button" onClick={onCreateTopic} className={iosBtnPrimary}>
                        <Plus size={14} /> Корпоративная тема
                    </button>
                )}
            </div>

            {loading && <LoadingBlock />}

            {!loading && visible.length === 0 && (
                <EmptyBlock
                    icon={BookOpen}
                    title={family === SCOPE_ARCHIVE
                        ? 'В архиве пусто'
                        : (hasFilters ? 'Ничего не найдено' : 'В этом месяце тренингов не было')}
                    text={hasFilters
                        ? 'Попробуйте изменить условия поиска или снять фильтр.'
                        : 'Как только по какой-нибудь теме проведут занятие, она появится здесь. Корпоративные темы видны сразу — по ним можно начать раскатку.'}
                >
                    {!hasFilters && (
                        <button type="button" onClick={onAddSession} className={`${iosBtnPrimary} mt-1`}>
                            <Plus size={14} /> Провести занятие
                        </button>
                    )}
                </EmptyBlock>
            )}

            {!loading && visible.length > 0 && view === VIEW_CARDS && (
                <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
                    {visible.map((summary) => (
                        <TopicCard
                            key={summary.key}
                            summary={summary}
                            menu={topicMenu(summary)}
                            canManage={canManage}
                            onOpen={() => onOpenTopic(summary)}
                            onRollout={summary.topic ? () => onRollout(summary.topic) : null}
                        />
                    ))}
                </div>
            )}

            {!loading && visible.length > 0 && view === VIEW_ROWS && (
                <TopicRows
                    items={visible}
                    month={month}
                    onOpen={onOpenTopic}
                    onRollout={onRollout}
                    canManage={canManage}
                />
            )}
        </div>
    );
}

/* ── Карточка темы ──────────────────────────────────────────────────────── */

function TopicCard({ summary, menu, canManage, onOpen, onRollout }) {
    const corporate = summary.family === FAMILY_CORPORATE;
    const remaining = remainingCount(summary);
    const percent = coveragePercent(summary);
    /* «Прошли все» — только когда охват РЕАЛЬНО закрыт. Считать это по
     * `remaining === 0` нельзя: у архивной темы remainingCount намеренно
     * возвращает 0 (её не нужно догонять), и карточка архивной темы писала
     * зелёным «Прошли все» при 30 из 68. */
    const complete = summary.audienceCount > 0 && summary.coveredCount >= summary.audienceCount;

    return (
        <div className={`${iosCard} flex flex-col p-4`}>
            <div className="flex items-start gap-3">
                <span
                    className={`grid h-[38px] w-[38px] shrink-0 place-items-center rounded-[12px] text-[13px] font-semibold ring-1 ${tileTone(summary.title)}`}
                    aria-hidden="true"
                >
                    {initials(summary.title)}
                </span>

                <button
                    type="button"
                    onClick={onOpen}
                    className="min-w-0 flex-1 text-left"
                    title={`Показать занятия по теме «${summary.title}»`}
                >
                    <span className="block truncate text-[14px] font-semibold leading-snug text-slate-900">
                        {summary.title}
                    </span>
                    <span className="mt-1 flex flex-wrap items-center gap-1.5">
                        {corporate && <IosBadge tone="blue" className="!py-0 !text-[10px]">Корпоративная</IosBadge>}
                        {corporate && (
                            <IosBadge className="!py-0 !text-[10px]">
                                {TOPIC_KIND_LABELS[summary.kind] || 'Информационный'}
                            </IosBadge>
                        )}
                        {summary.isArchivedTopic && (
                            <IosBadge tone="amber" className="!py-0 !text-[10px]">В архиве</IosBadge>
                        )}
                        {summary.isArchivedReason && (
                            <IosBadge tone="amber" className="!py-0 !text-[10px]">Архивная</IosBadge>
                        )}
                        {summary.departmentName && (
                            <span className="truncate text-[11px] text-slate-400">{summary.departmentName}</span>
                        )}
                    </span>
                </button>

                {menu.length > 0 && (
                    <IosMenu items={menu} label={`Действия с темой «${summary.title}»`} />
                )}
            </div>

            {/* Охват — только у корпоративной темы: у базовой знаменателя нет и
                быть не должно, «обратную связь» не проводят всем поголовно. */}
            {corporate && summary.audienceCount > 0 && (
                <div className="mt-3.5 space-y-2">
                    <div className="flex items-baseline justify-between gap-2">
                        <span className="text-[13px] text-slate-500">Проведён</span>
                        <span className="text-[13px] tabular-nums text-slate-900">
                            <span className="text-[17px] font-semibold">{summary.coveredCount}</span>
                            <span className="text-slate-400"> / {summary.audienceCount}</span>
                        </span>
                    </div>
                    <CoverageBar covered={summary.coveredCount} audience={summary.audienceCount} />
                    <div className="flex items-center justify-between text-[11.5px]">
                        <span className={complete ? 'font-medium text-emerald-600' : 'text-slate-400'}>
                            {complete
                                ? 'Прошли все'
                                : (summary.isArchivedTopic
                                    ? 'Тема в архиве'
                                    : `Осталось ${summary.audienceCount - summary.coveredCount}`)}
                        </span>
                        {percent != null && (
                            <span className="tabular-nums text-slate-400">{percent}%</span>
                        )}
                    </div>
                </div>
            )}

            {corporate && summary.audienceCount === 0 && (
                <div className="mt-3.5 flex items-center gap-1.5 text-[12px] text-slate-400">
                    Аудитория пуста
                    <IosHint text="В отделе темы нет ни одного работающего сотрудника, которому можно провести тренинг. Проверьте, к какому отделу тема привязана." />
                </div>
            )}

            {/* Итоги месяца — единой строкой. Раньше эти же цифры дублировались
                и в карточке, и в модалке; здесь они живут только тут.
                Разделительная линия — только когда выше есть что отделять: у
                базовой темы над ней ничего нет, и линия с отступом рисовала бы
                пустую полосу в половину карточки. */}
            <div className={`flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] text-slate-500 ${
                corporate ? 'mt-3.5 border-t border-slate-100 pt-3' : 'mt-2.5'
            }`}>
                {summary.monthSessions > 0 ? (
                    <>
                        <span className="tabular-nums">
                            {summary.monthSessions} {pluralSessions(summary.monthSessions)}
                        </span>
                        <span className="text-slate-300">·</span>
                        <span className="tabular-nums">
                            {summary.monthOperators} {pluralPeople(summary.monthOperators)}
                        </span>
                        {summary.monthMinutes > 0 && !summary.noneCounted && (
                            <>
                                <span className="text-slate-300">·</span>
                                <span className="tabular-nums">{formatDuration(summary.monthMinutes)}</span>
                            </>
                        )}
                        {summary.monthLastDate && (
                            <>
                                <span className="text-slate-300">·</span>
                                <span className="tabular-nums">{formatDayShort(summary.monthLastDate)}</span>
                            </>
                        )}
                    </>
                ) : (
                    <span className="text-slate-400">В этом месяце занятий не было</span>
                )}
            </div>

            {canManage && corporate && onRollout && !summary.isArchivedTopic && remaining > 0 && (
                <button
                    type="button"
                    onClick={onRollout}
                    className="mt-3 flex w-full items-center justify-center gap-1.5 rounded-xl bg-slate-100 py-2 text-[12.5px] font-semibold text-slate-700 transition hover:bg-slate-200 active:scale-[0.98]"
                >
                    <CalendarCheck2 size={13} /> Провести пачке
                </button>
            )}
        </div>
    );
}

/* ── Строки ─────────────────────────────────────────────────────────────── */

function TopicRows({ items, onOpen, onRollout, canManage }) {
    return (
        <div className={`${iosCard} overflow-hidden`}>
            <div className="overflow-x-auto">
                <table className="w-full min-w-[760px] border-collapse text-left">
                    <thead>
                        <tr className="border-b border-slate-100 text-[11px] uppercase tracking-wider text-slate-400">
                            <th className="px-3.5 py-2.5 font-semibold">Тема</th>
                            <th className="px-3 py-2.5 font-semibold">Семья</th>
                            <th className="px-3 py-2.5 text-right font-semibold">Занятий</th>
                            <th className="px-3 py-2.5 text-right font-semibold">Сотрудников</th>
                            <th className="px-3 py-2.5 text-right font-semibold">Время</th>
                            <th className="px-3 py-2.5 font-semibold">Охват</th>
                            <th className="px-3 py-2.5 text-right font-semibold">Последнее</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                        {items.map((summary) => {
                            const corporate = summary.family === FAMILY_CORPORATE;
                            const remaining = remainingCount(summary);
                            const percent = coveragePercent(summary);
                            return (
                                <tr key={summary.key} className="transition-colors hover:bg-slate-50">
                                    <td className="px-3.5 py-2.5">
                                        <button
                                            type="button"
                                            onClick={() => onOpen(summary)}
                                            className="flex min-w-0 items-center gap-2 text-left"
                                        >
                                            <span className="truncate text-[13px] font-medium text-slate-800">
                                                {summary.title}
                                            </span>
                                            {summary.isArchivedTopic && (
                                                <IosBadge tone="amber" className="!py-0 !text-[10px]">архив</IosBadge>
                                            )}
                                            {summary.isArchivedReason && (
                                                <IosBadge tone="amber" className="!py-0 !text-[10px]">архив</IosBadge>
                                            )}
                                        </button>
                                    </td>
                                    <td className="px-3 py-2.5 text-[12px] text-slate-500">
                                        {FAMILY_LABELS[summary.family]}
                                    </td>
                                    <td className="px-3 py-2.5 text-right text-[12.5px] tabular-nums text-slate-700">
                                        {summary.monthSessions || '—'}
                                    </td>
                                    <td className="px-3 py-2.5 text-right text-[12.5px] tabular-nums text-slate-700">
                                        {summary.monthOperators || '—'}
                                    </td>
                                    <td className="px-3 py-2.5 text-right text-[12.5px] tabular-nums text-slate-700">
                                        {summary.monthMinutes > 0 && !summary.noneCounted
                                            ? formatDuration(summary.monthMinutes)
                                            : '—'}
                                    </td>
                                    <td className="px-3 py-2.5">
                                        {corporate && summary.audienceCount > 0 ? (
                                            <div className="flex items-center gap-2">
                                                <span className="w-[76px] shrink-0 text-[12px] tabular-nums text-slate-700">
                                                    {summary.coveredCount} / {summary.audienceCount}
                                                </span>
                                                <CoverageBar
                                                    covered={summary.coveredCount}
                                                    audience={summary.audienceCount}
                                                    className="w-[70px] shrink-0"
                                                />
                                                {percent != null && (
                                                    <span className="w-8 shrink-0 text-[11px] tabular-nums text-slate-400">
                                                        {percent}%
                                                    </span>
                                                )}
                                                {canManage && onRollout && summary.topic
                                                    && !summary.isArchivedTopic && remaining > 0 && (
                                                    <button
                                                        type="button"
                                                        onClick={() => onRollout(summary.topic)}
                                                        className="shrink-0 rounded-lg px-2 py-1 text-[11.5px] font-semibold text-slate-500 transition hover:bg-slate-100 hover:text-slate-800"
                                                    >
                                                        провести
                                                    </button>
                                                )}
                                            </div>
                                        ) : (
                                            <span className="text-[12px] text-slate-300">—</span>
                                        )}
                                    </td>
                                    <td className="px-3 py-2.5 text-right text-[12px] tabular-nums text-slate-500">
                                        {summary.monthLastDate ? formatDayShort(summary.monthLastDate) : '—'}
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
