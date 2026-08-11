import React, { useMemo, useState } from 'react';
import { FileText, Folder, Layers, Loader2, Search } from 'lucide-react';
import { iosCard } from '../ui/ios';

/* Правая колонка витрины — оглавление раздела: разделы сверху, статьи снизу.
 *
 * Это навигатор, а не второй список результатов. Поиск здесь — фильтр по
 * оглавлению (по названиям, на клиенте), в отличие от полнотекстового поиска в
 * центре: тот ищет ПО ТЕКСТУ статей и отвечает карточками со сниппетами.
 * Два поля рядом оправданы только пока они делают разное, поэтому у местного
 * и подпись другая.
 */

const rowBase = 'flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[12.5px] transition';

const GroupLabel = ({ children }) => (
    <div className="px-3 pb-1 pt-2.5 text-[10px] font-semibold uppercase tracking-[0.07em] text-slate-400">
        {children}
    </div>
);

export default function WikiIndexPanel({
    tree, sectionId, onSection, articles, onOpen, loading, scopeAll,
}) {
    const [filter, setFilter] = useState('');

    const needle = filter.trim().toLowerCase();
    const match = (text) => !needle || String(text || '').toLowerCase().includes(needle);

    const shownTree = useMemo(() => (
        tree
            .map(({ space, rows }) => ({ space, rows: rows.filter(({ section }) => match(section.name)) }))
            .filter(({ rows }) => rows.length > 0)
    ), [tree, needle]);

    const shownArticles = useMemo(
        () => articles.filter((a) => match(a.title) || match(a.summary)),
        [articles, needle],
    );

    const draftCount = shownArticles.filter((a) => a.status !== 'published').length;

    return (
        <aside className="lg:w-[272px] lg:shrink-0 2xl:w-[306px]">
            <div className={`${iosCard} flex flex-col overflow-hidden lg:sticky lg:top-4 lg:max-h-[calc(100vh-2.5rem)]`}>
                <div className="flex items-center justify-between gap-2 px-3 pb-1 pt-2.5">
                    <span className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-500">
                        <Layers size={11} /> Разделы и статьи
                    </span>
                    <span className="flex shrink-0 items-center gap-1">
                        {draftCount > 0 && (
                            <span className="rounded-full bg-amber-50 px-1.5 py-0.5 text-[9.5px] font-bold text-amber-700 tabular-nums">
                                {draftCount} черн.
                            </span>
                        )}
                        <span className="rounded-full bg-indigo-50 px-1.5 py-0.5 text-[9.5px] font-bold text-indigo-600 tabular-nums">
                            {shownArticles.length}
                        </span>
                    </span>
                </div>

                <div className="mx-2.5 mb-1.5 flex items-center gap-1.5 rounded-lg bg-slate-100 px-2.5 py-1.5">
                    <Search size={12} className="shrink-0 text-slate-400" />
                    <input
                        value={filter}
                        onChange={(e) => setFilter(e.target.value)}
                        placeholder="Поиск по оглавлению"
                        className="w-full min-w-0 bg-transparent text-[12px] text-slate-800 placeholder-slate-400 focus:outline-none"
                    />
                </div>

                <div className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-2">
                    <button
                        type="button"
                        onClick={() => onSection(null)}
                        className={`${rowBase} ${
                            sectionId
                                ? 'text-slate-700 hover:bg-slate-50'
                                : 'bg-indigo-50 font-semibold text-indigo-600'
                        }`}
                    >
                        <Folder size={13} className={sectionId ? 'text-slate-400' : 'text-indigo-500'} />
                        <span className="truncate">{scopeAll ? 'Всё содержимое' : 'Все статьи'}</span>
                    </button>

                    {shownTree.map(({ space, rows }) => (
                        <div key={space.id}>
                            <GroupLabel>{space.name}</GroupLabel>
                            {rows.map(({ section, depth }) => {
                                const active = sectionId === section.id;
                                const count = scopeAll ? section.articles_count : section.readable_count;
                                return (
                                    <button
                                        key={section.id}
                                        type="button"
                                        onClick={() => onSection(active ? null : section.id)}
                                        style={{ paddingLeft: `${10 + depth * 12}px` }}
                                        className={`${rowBase} ${
                                            active
                                                ? 'bg-indigo-50 font-semibold text-indigo-600'
                                                : 'text-slate-700 hover:bg-slate-50'
                                        }`}
                                    >
                                        <Folder size={13} className={active ? 'text-indigo-500' : 'text-slate-400'} />
                                        <span className="min-w-0 flex-1 truncate">{section.name}</span>
                                        {/* Счётчик — по видимым статьям: цифра рядом с названием
                                            обязана совпасть с тем, что откроется по клику. */}
                                        {count > 0 && (
                                            <span className="shrink-0 text-[10.5px] tabular-nums text-slate-400">{count}</span>
                                        )}
                                    </button>
                                );
                            })}
                        </div>
                    ))}

                    <GroupLabel>Статьи</GroupLabel>

                    {loading && (
                        <div className="flex items-center justify-center gap-2 py-6 text-slate-400">
                            <Loader2 size={14} className="animate-spin" />
                            <span className="text-[12px]">Загружаем…</span>
                        </div>
                    )}

                    {!loading && shownArticles.length === 0 && (
                        <div className="px-3 py-5 text-center text-[12px] text-slate-400">
                            {needle ? 'В оглавлении ничего не нашлось' : 'Статей здесь пока нет'}
                        </div>
                    )}

                    {!loading && shownArticles.map((article) => (
                        <button
                            key={article.id}
                            type="button"
                            onClick={() => onOpen(article.slug)}
                            className={`${rowBase} text-slate-700 hover:bg-slate-50`}
                        >
                            <FileText size={13} className="shrink-0 text-slate-400" />
                            <span className="min-w-0 flex-1 truncate">{article.title}</span>
                            {article.status === 'draft' && (
                                <span className="shrink-0 rounded bg-amber-50 px-1.5 py-0.5 text-[9px] font-bold text-amber-700">
                                    Черновик
                                </span>
                            )}
                        </button>
                    ))}
                </div>
            </div>
        </aside>
    );
}
