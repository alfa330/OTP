import React, { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Ban, ChevronLeft, Loader2, RotateCcw, MessageSquare, PhoneCall, CheckCircle2 } from 'lucide-react';
import { iosBtnGhost, iosCard } from '../ui/ios';
import { isChat, subjectTitle, SUBJECT_C2D_SNAPSHOT } from './subjects';
import { filtersKey, filtersToParams, hasActiveFilters } from './filters';
import { dayTitle, itemKey, plural, timeOf } from './queueDayRules';
import { exclusionAuthor } from './exclusionRules';

/* Экран «Исключённые» вкладок «Звонки» и «Чаты»: что исключили, почему и кто —
 * и вернуть, если исключили по ошибке.
 *
 * Отбор тот же, что у списка над ним (отдел, вид субъекта, фильтры панели),
 * только наоборот: здесь ровно то, что из списка пропало. Над строками —
 * переключатели категорий с числами: по ним видно, на что уходят исключения,
 * и их же ИИ собирает из причин, написанных своими словами.
 *
 * Строка открывает карточку (там тоже есть «Вернуть»), кнопка «Вернуть» в строке
 * возвращает сразу — без захода в карточку. */

const PAGE = 50;

const unitText = (n, subject) => {
    if (subject === SUBJECT_C2D_SNAPSHOT) return plural(n, 'заявка', 'заявки', 'заявок');
    if (isChat(subject)) return plural(n, 'чат', 'чата', 'чатов');
    return plural(n, 'звонок', 'звонка', 'звонков');
};

const FILTER_CHIP = 'inline-flex min-h-7 items-center gap-1.5 rounded-full px-3 py-1 text-[12.5px] font-medium '
    + 'transition-all active:scale-[0.97] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60';

function CategoryFilter({ value, onChange, total, categories, uncategorized }) {
    const options = [
        { value: 'all', label: 'Все', count: total },
        ...categories.map((c) => ({ value: String(c.id), label: c.title, count: c.count })),
        ...(uncategorized ? [{ value: 'none', label: 'Без категории', count: uncategorized }] : []),
    ];
    if (options.length < 3) return null;   // одна категория — переключать нечего
    return (
        <div className="flex flex-wrap gap-1.5 px-1" role="group" aria-label="Причина исключения">
            {options.map((option) => {
                const on = option.value === value;
                return (
                    <button key={option.value} type="button" aria-pressed={on} onClick={() => onChange(option.value)}
                            className={`${FILTER_CHIP} ${on ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>
                        <span>{option.label}</span>
                        <span className={`tabular-nums ${on ? 'text-white/60' : 'text-slate-400'}`}>{option.count}</span>
                    </button>
                );
            })}
        </div>
    );
}

function Row({ item, onOpen, onRestore, restoring, canRestore }) {
    const ex = item.exclusion || {};
    const Icon = isChat(item.subject) ? MessageSquare : PhoneCall;
    // «13.10 09:13» → дата и время двумя строками, как в остальных списках раздела;
    // у заявки Chat2Desk времени нет — только дата.
    const time = timeOf(item.datetime);
    const date = String(item.datetime || '').replace(time || '', '').trim() || dayTitle(item.day).date;
    const reason = String(ex.reason || '').trim();
    return (
        <div role="listitem" className="flex items-center gap-2 border-b border-slate-100 pr-3 last:border-b-0 sm:pr-4">
            <button type="button" onClick={() => onOpen(item)} data-qa-row={itemKey(item)}
                    className="flex min-w-0 flex-1 items-start gap-3 px-4 py-3 text-left transition hover:bg-slate-50 focus-visible:bg-blue-50/60 focus-visible:outline-none active:bg-slate-100 sm:px-5">
                <span className="w-11 shrink-0 pt-px text-[13px] leading-snug tabular-nums text-slate-500">
                    {date || '—'}
                    {time && <span className="block text-[12px] text-slate-400">{time}</span>}
                </span>
                <span className="min-w-0 flex-1">
                    <span className="block truncate text-[14px] font-medium text-slate-900">{item.operator}</span>
                    <span className="mt-0.5 flex min-w-0 items-center gap-1 text-[12px] text-slate-500">
                        <Icon size={12} className="shrink-0 text-slate-400" aria-hidden="true" />
                        <span className="truncate">
                            {subjectTitle(item.subject, item.id)}{item.direction ? ` · ${item.direction}` : ''}
                        </span>
                    </span>
                    <span className="mt-1 flex min-w-0 items-start gap-1.5 text-[12.5px] text-slate-700">
                        <Ban size={12} className="mt-[3px] shrink-0 text-rose-500" aria-hidden="true" />
                        <span className="min-w-0">
                            {ex.category
                                ? <span className="font-medium">{ex.category}</span>
                                : ex.category_pending && <span className="text-slate-400">ИИ подбирает категорию</span>}
                            {reason && <span className="text-slate-500">{ex.category || ex.category_pending ? ' · ' : ''}{reason}</span>}
                        </span>
                    </span>
                    {exclusionAuthor(ex) && (
                        <span className="mt-0.5 block truncate text-[11.5px] text-slate-400">{exclusionAuthor(ex)}</span>
                    )}
                </span>
            </button>
            {canRestore && (
                <button type="button" onClick={() => onRestore(item)} disabled={restoring}
                        aria-label={`Вернуть оценку: ${subjectTitle(item.subject, item.id)}`}
                        className="inline-flex shrink-0 items-center gap-1 rounded-lg px-2.5 py-1.5 text-[12.5px] font-medium text-blue-600 transition hover:bg-blue-50 active:scale-[0.97] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 disabled:opacity-50">
                    {restoring ? <Loader2 size={13} className="animate-spin" aria-hidden="true" /> : <RotateCcw size={13} aria-hidden="true" />}
                    Вернуть
                </button>
            )}
        </div>
    );
}

export default function ExcludedList({ apiBaseUrl, headers, department, subject, filters, onOpen, onClose,
                                       onRestored, showToast }) {
    const [category, setCategory] = useState('all');
    const [state, setState] = useState({ items: [], total: 0, categories: [], uncategorized: 0,
                                         loading: true, error: false, canRestore: false });
    const [restoring, setRestoring] = useState(null);
    const headersRef = useRef(headers);
    headersRef.current = headers;
    const request = useRef(0);
    const signature = filtersKey(filters);

    const load = (offset = 0) => {
        if (!apiBaseUrl) { setState((s) => ({ ...s, loading: false, error: true })); return; }
        const id = ++request.current;
        setState((s) => ({ ...s, loading: true, error: false, ...(offset ? {} : { items: [] }) }));
        axios.get(`${apiBaseUrl}/api/ai-qa/exclusions`, {
            params: { subject, limit: PAGE, offset, ...(department ? { department } : {}),
                      ...(category !== 'all' ? { category } : {}), ...filtersToParams(filters) },
            headers: headersRef.current?.() || {},
        })
            .then((r) => {
                if (id !== request.current) return;
                const data = r.data || {};
                setState((s) => {
                    const base = offset ? s.items : [];
                    const known = new Set(base.map(itemKey));
                    return {
                        items: [...base, ...(data.items || []).filter((item) => !known.has(itemKey(item)))],
                        total: Number(data.total) || 0,
                        categories: data.categories || [],
                        uncategorized: Number(data.uncategorized) || 0,
                        loading: false, error: false, canRestore: data.can_restore !== false,
                    };
                });
            })
            .catch(() => {
                if (id !== request.current) return;
                setState((s) => ({ ...s, loading: false, error: true }));
            });
    };

    // eslint-disable-next-line react-hooks/exhaustive-deps
    useEffect(() => { load(0); }, [apiBaseUrl, department, subject, category, signature]);

    /* Счётчики категорий приходят по всему отбору, без фильтра категории, — «Все»
       считается из них же: число на «Все» не должно зависеть от того, какой
       переключатель сейчас выбран. */
    const allCount = useMemo(
        () => state.categories.reduce((sum, c) => sum + c.count, 0) + state.uncategorized,
        [state.categories, state.uncategorized]);

    const restore = async (item) => {
        if (!apiBaseUrl || restoring) return;
        setRestoring(itemKey(item));
        try {
            await axios.delete(`${apiBaseUrl}/api/ai-qa/exclusion`, {
                params: { subject_kind: item.subject, call_id: item.id },
                headers: headersRef.current?.() || {},
            });
            const ex = item.exclusion || {};
            setState((s) => ({
                ...s,
                items: s.items.filter((row) => itemKey(row) !== itemKey(item)),
                total: Math.max(0, s.total - 1),
                categories: s.categories.map((c) => (c.id === ex.category_id ? { ...c, count: Math.max(0, c.count - 1) } : c))
                    .filter((c) => c.count > 0),
                uncategorized: ex.category_id == null ? Math.max(0, s.uncategorized - 1) : s.uncategorized,
            }));
            showToast?.(`Оценка возвращена: ${subjectTitle(item.subject, item.id)}`, 'success');
            // Вернули последнюю оценку выбранной категории — её переключатель пропал,
            // и пустой список под ним читался бы как «ничего нет вовсе».
            if (category !== 'all') {
                const left = category === 'none' ? state.uncategorized - 1
                    : (state.categories.find((c) => String(c.id) === category)?.count || 0) - 1;
                if (left <= 0) setCategory('all');
            }
            onRestored?.(item);
        } catch (e) {
            showToast?.(e?.response?.data?.error || 'Не удалось вернуть оценку', 'error');
        } finally {
            setRestoring(null);
        }
    };

    const filtered = hasActiveFilters(filters);
    const remaining = Math.max(0, state.total - state.items.length);

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between gap-2">
                <button type="button" onClick={onClose} className={`${iosBtnGhost} -ml-2`}>
                    <ChevronLeft size={16} aria-hidden="true" />Все дни
                </button>
            </div>
            <div className="px-1">
                <h2 id="qa-excluded-title" tabIndex={-1}
                    className="flex items-baseline gap-2 rounded-md text-[24px] font-semibold leading-tight text-slate-900 outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60">
                    Исключённые
                    {allCount > 0 && (
                        <span className="text-[17px] font-medium tabular-nums text-slate-400"
                              aria-label={`${allCount} ${unitText(allCount, subject)}`}>{allCount}</span>
                    )}
                </h2>
                <p className="mt-0.5 text-[13px] text-slate-500">
                    Не учитываются в очереди, списках, «Обзоре» и «Сводке»
                </p>
            </div>

            <CategoryFilter value={category} onChange={setCategory} total={allCount}
                            categories={state.categories} uncategorized={state.uncategorized} />

            <div className={`${iosCard} overflow-hidden`}>
                {state.items.length > 0 && (
                    <div role="list" aria-label="Исключённые оценки">
                        {state.items.map((item) => (
                            <Row key={itemKey(item)} item={item} onOpen={onOpen} onRestore={restore}
                                 restoring={restoring === itemKey(item)} canRestore={state.canRestore} />
                        ))}
                    </div>
                )}
                {state.loading && (
                    <div className={`flex items-center justify-center gap-2 py-8 text-[13px] text-slate-500 ${state.items.length ? 'border-t border-slate-100' : ''}`}
                         role="status">
                        <Loader2 size={15} className="animate-spin" aria-hidden="true" />Загружаю исключённые…
                    </div>
                )}
                {state.error && !state.loading && (
                    <div className="flex items-center justify-center gap-2 px-4 py-6 text-[13px] text-rose-600" role="alert">
                        Не удалось загрузить исключённые
                        <button type="button" onClick={() => load(state.items.length)}
                                className="rounded-lg px-2 py-1.5 font-medium text-blue-600 hover:bg-blue-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60">
                            Повторить
                        </button>
                    </div>
                )}
                {!state.loading && !state.error && !state.items.length && (
                    <div className="flex flex-col items-center gap-2 px-6 py-10 text-center text-[13px] text-slate-500">
                        <CheckCircle2 size={24} className="text-slate-300" aria-hidden="true" />
                        {filtered || category !== 'all' ? 'Под выбранный отбор исключённых нет' : 'Исключённых оценок нет'}
                    </div>
                )}
                {!state.loading && !state.error && remaining > 0 && (
                    <button type="button" onClick={() => load(state.items.length)}
                            className="w-full border-t border-slate-100 py-3 text-[13px] font-medium text-blue-600 transition hover:bg-slate-50 focus-visible:bg-blue-50/60 focus-visible:outline-none">
                        Показать ещё {remaining}
                    </button>
                )}
            </div>
        </div>
    );
}
