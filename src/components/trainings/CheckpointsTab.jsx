import React, { useMemo, useState } from 'react';
import { ShieldCheck, Lock, CalendarClock, ExternalLink, Check, XCircle, RotateCcw } from 'lucide-react';
import { iosCard, iosInput, iosBtnSecondary, iosBtnPrimary, IosBadge, IosModal, IosSegmented } from '../ui/ios';
import { EmptyBlock, LoadingBlock, ErrorBlock } from './pieces';
import { CHECKPOINT_KIND_LABELS, checkpointDaysLeft, formatDayLong, pluralDays } from './constants';

/* Вкладка «Контроль» раздела «Тренинги» (задача #86).
 *
 * Отвечает ровно на один вопрос: «кого я должен проверить и когда». Поэтому по
 * умолчанию показываются только открытые точки, а сортировка одна — по сроку:
 * просроченное само оказывается сверху, отдельного «сначала горящее» не нужно.
 *
 * Месяца у вкладки нет намеренно, и селектор месяца над ней спрятан: контроль
 * это не отчёт за период, а очередь дел. Точка, назначенная на октябрь, обязана
 * быть видна в августе — иначе о ней и вспомнят только в октябре.
 *
 * Цветом отмечено только то, что горит: просрочено — красным, сегодня —
 * янтарным, всё остальное нейтрально. Контроль сам по себе не авария.
 */

const SCOPE_OPEN = 'open';
const SCOPE_ALL = 'all';

/* Группы по сроку. Границы — не «красиво», а по тому, как человек планирует:
 * просроченное и сегодняшнее делают сейчас, неделя — это ближайший план,
 * остальное просто должно быть видно, чтобы не забыться. */
const buildGroups = (items) => {
    const overdue = [];
    const today = [];
    const week = [];
    const later = [];
    items.forEach((item) => {
        const left = checkpointDaysLeft(item.due_date);
        if (left === null) { later.push(item); return; }
        if (left < 0) overdue.push(item);
        else if (left === 0) today.push(item);
        else if (left <= 7) week.push(item);
        else later.push(item);
    });
    return [
        { key: 'overdue', title: 'Просрочено', tone: 'rose', items: overdue },
        { key: 'today', title: 'Проверить сегодня', tone: 'amber', items: today },
        { key: 'week', title: 'Ближайшая неделя', tone: 'slate', items: week },
        { key: 'later', title: 'Позже', tone: 'slate', items: later },
    ].filter((group) => group.items.length > 0);
};

const dueWording = (dueDate) => {
    const left = checkpointDaysLeft(dueDate);
    if (left === null) return '';
    if (left < 0) return `просрочена на ${Math.abs(left)} ${pluralDays(Math.abs(left))}`;
    if (left === 0) return 'сегодня';
    if (left === 1) return 'завтра';
    return `через ${left} ${pluralDays(left)}`;
};

const CheckpointCard = ({ item, onResolveClick, onReopen, onOpenJournal }) => {
    const [showDetails, setShowDetails] = useState(false);
    const left = checkpointDaysLeft(item.due_date);
    const isOpen = item.status === 'open';
    const overdue = isOpen && left !== null && left < 0;
    const dueToday = isOpen && left === 0;
    const hasDetails = Boolean(item.reason || item.internal_comment);

    const dueTone = overdue
        ? 'text-rose-600'
        : (dueToday ? 'text-amber-600' : 'text-slate-500');

    return (
        <div className={`${iosCard} p-4`}>
            <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1.5">
                <div className="min-w-0">
                    <div className="truncate text-[14px] font-semibold text-slate-900">
                        {item.operator_name || `Сотрудник #${item.operator_id}`}
                    </div>
                    <div className={`mt-0.5 flex items-center gap-1.5 text-[12.5px] tabular-nums ${dueTone}`}>
                        <CalendarClock size={13} className="shrink-0" />
                        {formatDayLong(item.due_date)}
                        {isOpen && dueWording(item.due_date) && <span>· {dueWording(item.due_date)}</span>}
                    </div>
                </div>
                <div className="flex flex-wrap items-center gap-1.5">
                    <IosBadge tone={overdue ? 'red' : (dueToday ? 'amber' : 'slate')}>
                        {item.kind_label || CHECKPOINT_KIND_LABELS[item.kind] || 'Контроль качества'}
                    </IosBadge>
                    {item.status === 'done' && <IosBadge tone="green">Проверено</IosBadge>}
                    {item.status === 'cancelled' && <IosBadge>Контроль снят</IosBadge>}
                </div>
            </div>

            <p className="mt-2.5 text-[13px] leading-relaxed text-slate-700">
                <span className="text-slate-400">Проверить: </span>{item.focus || '—'}
            </p>

            {showDetails && (
                <div className="mt-2.5 space-y-2 rounded-xl bg-slate-50 p-3">
                    {item.reason && (
                        <p className="text-[12.5px] leading-relaxed text-slate-600">
                            <span className="text-slate-400">Причина контроля: </span>{item.reason}
                        </p>
                    )}
                    {item.internal_comment && (
                        <p className="flex items-start gap-1.5 text-[12.5px] leading-relaxed text-slate-500">
                            <Lock size={12} className="mt-0.5 shrink-0" />
                            <span>{item.internal_comment}</span>
                        </p>
                    )}
                </div>
            )}

            {item.status !== 'open' && item.resolution_comment && (
                <p className="mt-2.5 text-[12.5px] leading-relaxed text-slate-500">
                    <span className="text-slate-400">Итог: </span>{item.resolution_comment}
                </p>
            )}

            <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
                {isOpen ? (
                    <>
                        <button type="button" className={iosBtnPrimary} onClick={() => onResolveClick(item, 'done')}>
                            <Check size={14} /> Проверено
                        </button>
                        <button type="button" className={iosBtnSecondary} onClick={() => onResolveClick(item, 'cancelled')}>
                            <XCircle size={14} /> Снять контроль
                        </button>
                    </>
                ) : (
                    <button type="button" className={iosBtnSecondary} onClick={() => onReopen(item)}>
                        <RotateCcw size={14} /> Вернуть в работу
                    </button>
                )}

                {hasDetails && (
                    <button
                        type="button"
                        onClick={() => setShowDetails((value) => !value)}
                        className="text-[12.5px] font-medium text-slate-500 transition hover:text-slate-800"
                    >
                        {showDetails ? 'Скрыть подробности' : 'Подробности'}
                    </button>
                )}

                {onOpenJournal && (
                    <button
                        type="button"
                        onClick={() => onOpenJournal(item)}
                        className="ml-auto flex items-center gap-1.5 text-[12.5px] font-medium text-blue-600 transition hover:text-blue-700"
                    >
                        Открыть в журнале <ExternalLink size={13} />
                    </button>
                )}
            </div>

            {item.status !== 'open' && (item.resolved_at || item.resolved_by_name) && (
                <div className="mt-2 text-[11.5px] text-slate-400">
                    {item.resolved_by_name || 'Закрыто'}{item.resolved_at ? ` · ${item.resolved_at}` : ''}
                </div>
            )}
        </div>
    );
};

export default function CheckpointsTab({
    checkpoints = [],
    counts = null,
    loading = false,
    loadError = '',
    scope = SCOPE_OPEN,
    onScopeChange,
    onReload,
    onResolve,
    onOpenJournal = null,
}) {
    const [dialog, setDialog] = useState(null);     // {item, action}
    const [comment, setComment] = useState('');
    const [saving, setSaving] = useState(false);

    const openItems = useMemo(
        () => checkpoints.filter((item) => item.status === 'open'), [checkpoints]);
    const closedItems = useMemo(
        () => checkpoints.filter((item) => item.status !== 'open'), [checkpoints]);
    const groups = useMemo(() => buildGroups(openItems), [openItems]);

    const openDialog = (item, action) => { setComment(''); setDialog({ item, action }); };

    const submitDialog = async () => {
        if (!dialog) return;
        setSaving(true);
        try {
            await onResolve(dialog.item, dialog.action, comment);
            setDialog(null);
        } finally {
            setSaving(false);
        }
    };

    const reopen = async (item) => { await onResolve(item, 'reopen', ''); };

    if (loadError && !loading) {
        return <ErrorBlock text={loadError} onRetry={onReload} />;
    }
    if (loading) return <LoadingBlock label="Загружаем контроль…" />;

    const overdueCount = Number(counts?.overdue || 0);
    const todayCount = Number(counts?.today || 0);

    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2 px-1">
                <div className="text-[12.5px] text-slate-500">
                    {openItems.length === 0
                        ? 'Открытых проверок нет'
                        : (
                            <span className="tabular-nums">
                                {openItems.length} на контроле
                                {overdueCount > 0 && <span className="text-rose-600"> · {overdueCount} просрочено</span>}
                                {todayCount > 0 && <span className="text-amber-600"> · {todayCount} сегодня</span>}
                            </span>
                        )}
                </div>
                <IosSegmented
                    value={scope}
                    onChange={onScopeChange}
                    ariaLabel="Что показывать"
                    options={[
                        { value: SCOPE_OPEN, label: 'На контроле' },
                        { value: SCOPE_ALL, label: 'История' },
                    ]}
                />
            </div>

            {openItems.length === 0 && closedItems.length === 0 ? (
                <EmptyBlock
                    icon={ShieldCheck}
                    title={scope === SCOPE_ALL ? 'Контрольных точек не было' : 'Никого не нужно проверять'}
                    text={'Контрольная точка ставится из «Журнала оценок»: откройте оценку, нажмите «ОС» '
                        + 'и включите «Поставить на контроль».'}
                />
            ) : (
                <>
                    {groups.map((group) => (
                        <section key={group.key} className="space-y-2">
                            <div className="flex items-center gap-2 px-1">
                                <span className={`text-[11px] font-semibold uppercase tracking-wider ${
                                    group.tone === 'rose'
                                        ? 'text-rose-600'
                                        : (group.tone === 'amber' ? 'text-amber-600' : 'text-slate-400')
                                }`}>
                                    {group.title}
                                </span>
                                <span className="text-[11px] tabular-nums text-slate-400">{group.items.length}</span>
                            </div>
                            <div className="grid gap-2 lg:grid-cols-2">
                                {group.items.map((item) => (
                                    <CheckpointCard
                                        key={item.id}
                                        item={item}
                                        onResolveClick={openDialog}
                                        onReopen={reopen}
                                        onOpenJournal={onOpenJournal}
                                    />
                                ))}
                            </div>
                        </section>
                    ))}

                    {closedItems.length > 0 && (
                        <section className="space-y-2">
                            <div className="px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                                Закрытые
                            </div>
                            <div className="grid gap-2 lg:grid-cols-2">
                                {closedItems.map((item) => (
                                    <CheckpointCard
                                        key={item.id}
                                        item={item}
                                        onResolveClick={openDialog}
                                        onReopen={reopen}
                                        onOpenJournal={onOpenJournal}
                                    />
                                ))}
                            </div>
                        </section>
                    )}
                </>
            )}

            <IosModal
                open={Boolean(dialog)}
                onClose={() => setDialog(null)}
                title={dialog?.action === 'cancelled' ? 'Снять контроль' : 'Проверка проведена'}
                subtitle={dialog?.item?.operator_name || undefined}
                maxWidth="max-w-md"
                footer={(
                    <>
                        <button type="button" className={iosBtnSecondary} onClick={() => setDialog(null)}>
                            Отмена
                        </button>
                        <button type="button" className={iosBtnPrimary} onClick={submitDialog} disabled={saving}>
                            {saving ? 'Сохраняем…' : (dialog?.action === 'cancelled' ? 'Снять контроль' : 'Готово')}
                        </button>
                    </>
                )}
            >
                <div className="space-y-2">
                    <p className="text-[12.5px] leading-relaxed text-slate-500">
                        {dialog?.action === 'cancelled'
                            ? 'Проверка не понадобилась. Точка уйдёт в историю, сотруднику ничего не покажем.'
                            : 'Точка уйдёт из списка. Итог виден только руководителям — сотрудник его не увидит.'}
                    </p>
                    <textarea
                        rows={3}
                        value={comment}
                        onChange={(event) => setComment(event.target.value)}
                        maxLength={2000}
                        placeholder={dialog?.action === 'cancelled'
                            ? 'Почему сняли контроль (необязательно)'
                            : 'Что показала проверка (необязательно)'}
                        className={iosInput}
                    />
                </div>
            </IosModal>
        </div>
    );
}
