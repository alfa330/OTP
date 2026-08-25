/* Выбор статьи для внутренней ссылки.
 *
 * Зачем отдельное окно. До сих пор ссылка в редакторе ставилась через
 * window.prompt('Адрес ссылки') — то есть человек, желавший сослаться на
 * «Тарифы», должен был выйти из редактора, найти статью, скопировать адрес и
 * вернуться. Практически это значило, что внутренних ссылок никто не ставил.
 *
 * Откуда список. Из УЖЕ ЗАГРУЖЕННОГО оглавления витрины (WikiLibrary.index):
 * оно приезжает при открытии вики одним разбитым на страницы запросом и живёт,
 * пока открыт редактор, — редактор монтируется ранним возвратом внутри того же
 * компонента. Значит пикер стоит НОЛЬ запросов и ноль байт.
 *
 * Почему не /api/wiki/search и не /api/wiki/suggest:
 *   * /search пишет КАЖДЫЙ запрос в wiki_search_log, а из этого журнала
 *     собирается отчёт «что ищут» на вкладке «Аналитика». Пикер с подсказкой на
 *     каждое нажатие засыпал бы отчёт обрывками названий статей;
 *   * /suggest отдаёт жёстко 5 строк (limit в роуте не читается) — для выбора
 *     статьи из трёх сотен этого мало.
 * Оба, вдобавок, стоят пересчёта периметра на каждое нажатие.
 *
 * Границу пространства пикер получает даром: index грузится с space_id, то есть
 * это ровно тот периметр витрины, в котором человек и работает. Предложить
 * статью соседней вики он не может — а именно так выглядел дефект справочника
 * офисов, который уже чинили.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { FileText, Search } from 'lucide-react';

import { IosBadge, IosModal, iosInput } from '../ui/ios.jsx';
import { normalizeText } from './searchText.js';
import { typeBadge } from './articleTypes.js';

/* Статус рядом с названием — не украшение. В проде НИ ОДНА цель внутренней
   ссылки сегодня не опубликована (238 черновиков и 15 архивных на 253 пары),
   поэтому человек, выбирающий статью, обязан видеть, что ссылается на
   недописанное: у читателя без права видеть черновики такая ссылка даст 404. */
const STATUS_NOTE = {
    draft: { label: 'Черновик', tone: 'amber' },
    on_approval: { label: 'На согласовании', tone: 'amber' },
    requires_verification: { label: 'Требует проверки', tone: 'amber' },
    archived: { label: 'Архив', tone: 'slate' },
    expired: { label: 'Устарела', tone: 'slate' },
};

export default function ArticlePicker({ open, articles = [], currentId = null,
                                        onPick, onClose }) {
    const [query, setQuery] = useState('');
    const inputRef = useRef(null);

    // Открыли окно — курсор сразу в поиске: выбор статьи начинается с набора
    // названия, и лишний клик здесь ничем не оправдан.
    useEffect(() => {
        if (!open) return undefined;
        setQuery('');
        const timer = setTimeout(() => inputRef.current?.focus(), 60);
        return () => clearTimeout(timer);
    }, [open]);

    const rows = useMemo(() => {
        /* Саму себя статья в список не получает: ссылка статьи на себя не
           значит ничего ни в «Связанных материалах», ни в обратных ссылках, и
           сервер её всё равно отбросит (edit.link_content_articles). Показать
           её здесь значило бы предложить действие, которое молча ничего не даст. */
        const pool = articles.filter((row) => row?.slug && row.id !== currentId);
        const needle = normalizeText(query).trim();
        if (!needle) return pool.slice(0, 50);
        // Ищем по названию и по слагу — по слагу потому, что статьи из старой
        // вики люди помнят именно по нему.
        return pool
            .filter((row) => normalizeText(`${row.title} ${row.slug}`).includes(needle))
            .slice(0, 50);
    }, [articles, currentId, query]);

    return (
        <IosModal
            open={open}
            onClose={onClose}
            title="Ссылка на статью"
            subtitle="Выберите статью — в текст встанет ссылка на неё"
            maxWidth="max-w-xl"
        >
            <div className="space-y-3">
                <div className="relative">
                    <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                        ref={inputRef}
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder="Название или адрес статьи"
                        className={`${iosInput} pl-9`}
                    />
                </div>

                {rows.length === 0 ? (
                    <p className="px-1 py-6 text-center text-[13px] text-slate-500">
                        {articles.length === 0
                            ? 'Оглавление ещё загружается'
                            : 'Ничего не нашлось'}
                    </p>
                ) : (
                    <div className="overflow-hidden rounded-2xl ring-1 ring-slate-200/70">
                        {rows.map((row, i) => {
                            const note = STATUS_NOTE[row.status];
                            const type = typeBadge(row.article_type);
                            return (
                                <button
                                    key={row.id}
                                    type="button"
                                    onClick={() => onPick?.(row)}
                                    className={`flex w-full items-center gap-2.5 bg-white px-3.5 py-2.5 text-left
                                                transition hover:bg-slate-50 active:bg-slate-100
                                                ${i ? 'border-t border-slate-100' : ''}`}
                                >
                                    <FileText size={15} className="shrink-0 text-slate-400" />
                                    <span className="min-w-0 flex-1">
                                        <span className="block truncate text-[13.5px] font-medium text-slate-900">
                                            {row.title}
                                        </span>
                                        <span className="block truncate text-[11.5px] text-slate-500">
                                            {row.slug}
                                        </span>
                                    </span>
                                    {type?.label && (
                                        <IosBadge tone={type.tone || 'slate'}>{type.label}</IosBadge>
                                    )}
                                    {note && <IosBadge tone={note.tone}>{note.label}</IosBadge>}
                                </button>
                            );
                        })}
                    </div>
                )}

                {/* Оговорка стоит У СПИСКА, а не подвалом: обрез в 50 строк
                    иначе читается как «статей всего столько». */}
                {rows.length >= 50 && (
                    <p className="px-1 text-[11.5px] text-slate-500">
                        Показаны первые 50 — уточните запрос.
                    </p>
                )}
            </div>
        </IosModal>
    );
}
