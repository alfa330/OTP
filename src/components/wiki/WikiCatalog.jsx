import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
    Archive, FileText, Folder, Layers, Loader2, PenLine, Search,
} from 'lucide-react';
import { iosCard, iosGroupLabel, IosBadge, IosModal } from '../ui/ios';
import { typeBadge } from './articleTypes';

/* Вкладка «Статьи» — каталог: раздел плиткой, статьи раздела в модальном окне.
 *
 * Зачем она рядом с главной. Главная отвечает на вопрос «что почитать мне»:
 * избранное, недавнее, популярное. Каталог отвечает на другой — «что вообще
 * лежит в разделе N». Оглавление справа на главной это частично умеет, но оно
 * узкое, свёрнутое и рассчитано на переход к одной статье; чтобы окинуть
 * взглядом весь раздел, нужна сетка.
 *
 * Три корзины (статьи / черновики / архив) — один переключатель на весь
 * каталог, а не фильтр внутри каждого окна: человек приходит сюда с уже
 * заданным вопросом («где мои черновики»), и задавать его заново на каждой
 * плитке незачем. Состав корзин задан на сервере (schema.ARTICLE_BUCKETS) —
 * здесь только подписи.
 *
 * Плитки с нулём НЕ прячем: сетка обязана остаться на месте при переключении
 * корзины, иначе плитки скачут под курсором и человек открывает не тот раздел.
 * Нулевая плитка приглушена и честно открывается пустым окном.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

/* Ключ корзины совпадает с ключом ARTICLE_BUCKETS на сервере: он же уходит в
   ?bucket= и приходит в counts. Подписи и иконки — только здесь. */
const BUCKETS = [
    { key: 'published', label: 'Статьи', icon: FileText,
      empty: 'В этом разделе нет опубликованных статей.' },
    { key: 'draft', label: 'Черновики', icon: PenLine,
      empty: 'В этом разделе нет черновиков и статей на согласовании.' },
    { key: 'archived', label: 'Архив', icon: Archive,
      empty: 'В этом разделе нет архивных статей.' },
];

const BUCKET_BY_KEY = new Map(BUCKETS.map((b) => [b.key, b]));

// Синтетический раздел для статей, не привязанных ни к одной ветке. Тот же
// приём и та же подпись, что в оглавлении на главной (WikiIndexPanel).
const ORPHANS_ID = 'none';

const plural = (n, one, few, many) => {
    const mod100 = Math.abs(n) % 100;
    const mod10 = mod100 % 10;
    if (mod100 >= 11 && mod100 <= 14) return many;
    if (mod10 === 1) return one;
    if (mod10 >= 2 && mod10 <= 4) return few;
    return many;
};

const countLabel = (n) => `${n} ${plural(n, 'статья', 'статьи', 'статей')}`;

/* Переключатель корзин. Счётчик прямо на кнопке: он и есть ответ на вопрос
   «а есть ли там вообще что-нибудь», ради которого иначе пришлось бы
   переключиться и посмотреть. */
const BucketSwitch = ({ value, onChange, totals }) => (
    <div className="flex gap-1 overflow-x-auto rounded-2xl bg-slate-100 p-1">
        {BUCKETS.map(({ key, label, icon: Icon }) => {
            const active = value === key;
            return (
                <button
                    key={key}
                    type="button"
                    aria-pressed={active}
                    onClick={() => onChange(key)}
                    className={`flex shrink-0 items-center gap-1.5 rounded-xl px-3.5 py-2 text-[13px] font-medium transition ${
                        active ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'
                    }`}
                >
                    <Icon size={14} /> {label}
                    <span className={`tabular-nums ${active ? 'text-slate-500' : 'text-slate-400'}`}>
                        {totals?.[key] ?? 0}
                    </span>
                </button>
            );
        })}
    </div>
);

/* Плитка раздела.
 *
 * Порядок строк — название, потом путь: внутри пространства имена повторяются
 * намеренно («Супервайзер» есть и у СЗоВ, и у ОП), поэтому путь нужен, но
 * СТРОКОЙ НАД названием он сдвигал бы название вниз только у вложенных
 * разделов. В ряду из четырёх плиток названия вставали на разной высоте, и
 * сетка выглядела осыпавшейся. Снизу путь ничего не двигает: он растёт в
 * сторону счётчика, который и так прижат к низу.
 */
const SectionTile = ({ section, path, count, onOpen }) => {
    const empty = count === 0;
    return (
        <button
            type="button"
            onClick={onOpen}
            className={`${iosCard} flex min-h-[112px] w-full flex-col p-3.5 text-left transition hover:ring-2 hover:ring-indigo-500/20 active:scale-[0.99] ${
                empty ? 'opacity-60' : ''
            }`}
        >
            <span className={`mb-2 grid h-8 w-8 place-items-center rounded-xl ${
                empty ? 'bg-slate-100 text-slate-400' : 'bg-indigo-50 text-indigo-600'
            }`}>
                <Folder size={15} />
            </span>
            <span className="line-clamp-2 text-[13px] font-semibold leading-snug tracking-[-0.01em] text-slate-900">
                {section.name}
            </span>
            {path && (
                <span className="mt-0.5 truncate text-[10px] font-medium uppercase tracking-[0.06em] text-slate-400">
                    {path}
                </span>
            )}
            <span className="mt-auto pt-2 text-[11px] text-slate-500 tabular-nums">
                {countLabel(count)}
            </span>
        </button>
    );
};

/* Подписи статусов совпадают с шапкой статьи (WikiArticle): один и тот же
   статус обязан называться одинаково в списке и на самой статье. */
const STATUS_LABELS = {
    draft: 'Черновик',
    on_approval: 'На согласовании',
    published: 'Опубликована',
    requires_verification: 'Требует проверки',
    archived: 'В архиве',
    expired: 'Устарела',
};

/* Строка статьи в окне раздела. Статус подписан только у черновиков и архива:
   в корзине «Статьи» он у всех один и был бы шумом на каждой строке. */
const ArticleRow = ({ article, showStatus, onOpen }) => {
    const type = typeBadge(article.article_type);
    return (
        <button
            type="button"
            onClick={onOpen}
            className="flex w-full items-start gap-3 rounded-xl px-3 py-2.5 text-left transition hover:bg-white"
        >
            <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-white text-slate-400 ring-1 ring-slate-200/70">
                <FileText size={13} />
            </span>
            <span className="min-w-0 flex-1">
                <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="text-[13.5px] font-semibold leading-snug text-slate-900">
                        {article.title}
                    </span>
                    {type && <IosBadge tone={type.tone}>{type.label}</IosBadge>}
                    {showStatus && article.status && (
                        <IosBadge tone="slate">{STATUS_LABELS[article.status] || article.status}</IosBadge>
                    )}
                </span>
                {article.summary && (
                    <span className="mt-0.5 line-clamp-2 text-[12px] leading-relaxed text-slate-500">
                        {article.summary}
                    </span>
                )}
            </span>
        </button>
    );
};

export default function WikiCatalog({ base, headers, showToast, catalog, loading,
                                      bucket, onBucketChange, onOpenArticle }) {
    // Открытый раздел: {id, name, path} либо null. Держим весь объект, а не id —
    // шапка окна рисуется до того, как придёт список статей.
    const [open, setOpen] = useState(null);
    const [items, setItems] = useState(null);   // null = ещё не ответили
    const [busy, setBusy] = useState(false);
    const [filter, setFilter] = useState('');

    const spaces = catalog?.spaces || [];
    const sections = catalog?.sections || [];
    const totals = catalog?.totals;
    const orphans = catalog?.orphans;

    const active = BUCKET_BY_KEY.get(bucket) || BUCKETS[0];

    /* Путь до раздела: «Оператор» под «СЗоВ › Линия». Считаем один раз на весь
       каталог, а не в каждой плитке — иначе на каждый рендер сетки поднимался
       бы подъём по родителям для сотни разделов. */
    const pathById = useMemo(() => {
        const byId = new Map(sections.map((s) => [s.id, s]));
        const result = new Map();
        sections.forEach((section) => {
            const chain = [];
            let parent = section.parent_section_id ? byId.get(section.parent_section_id) : null;
            // Ограничитель как в sectionPicker: петель сервер не допускает, но
            // зациклиться здесь — подвесить вкладку намертво.
            let guard = 0;
            while (parent && guard < 50) {
                chain.unshift(parent.name);
                parent = parent.parent_section_id ? byId.get(parent.parent_section_id) : null;
                guard += 1;
            }
            result.set(section.id, chain.join(' › '));
        });
        return result;
    }, [sections]);

    /* Сетка сгруппирована пространствами: без заголовков сотня плиток — это
       стена, в которой не найти свой отдел. Порядок разделов внутри — тот, что
       пришёл с сервера (position), а не алфавитный: он задан в «Структуре». */
    const groups = useMemo(() => spaces
        .map((space) => ({
            space,
            rows: sections.filter((s) => s.space_id === space.id),
        }))
        .filter((group) => group.rows.length > 0), [spaces, sections]);

    const orphanCount = orphans?.[bucket] ?? 0;

    const loadArticles = useCallback((section) => {
        setBusy(true);
        setItems(null);
        axios.get(`${base}/articles`, {
            headers,
            params: { section_id: section.id, bucket, limit: 200 },
        })
            .then((r) => setItems(r.data?.items || []))
            .catch((e) => {
                setItems([]);
                showToast?.(errText(e, 'Не удалось загрузить статьи раздела'), 'error');
            })
            .finally(() => setBusy(false));
    }, [base, headers, bucket, showToast]);

    const openSection = (section, path) => {
        setFilter('');
        setOpen({ ...section, path });
        loadArticles(section);
    };

    /* Переключили корзину при открытом окне — перезапрашиваем его же раздел.
       Иначе в шапке стояло бы «Черновики», а в списке лежали опубликованные. */
    useEffect(() => {
        if (open) loadArticles(open);
        // loadArticles уже зависит от bucket; open в зависимостях привёл бы к
        // повторному запросу на каждое открытие поверх того, что делает
        // openSection.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [bucket]);

    // Esc закрывает окно: IosModal сам клавиши не слушает, а модалка без Esc
    // ловится мышью только по крестику и фону.
    useEffect(() => {
        if (!open) return undefined;
        const onKey = (e) => { if (e.key === 'Escape') setOpen(null); };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [open]);

    /* Фильтр внутри окна — по названию и описанию, на клиенте: список раздела
       уже целиком здесь, и ходить за ним на сервер ради подстроки незачем.
       Показываем поле только когда список длинный: над пятью строками оно
       занимает место, ничего не решая. */
    const shown = useMemo(() => {
        const term = filter.trim().toLowerCase();
        if (!term || !items) return items;
        return items.filter((a) => `${a.title} ${a.summary || ''}`.toLowerCase().includes(term));
    }, [items, filter]);

    if (loading && !catalog) {
        return (
            <div className={`${iosCard} h-[320px] overflow-hidden`}>
                <div className="sk-shimmer h-full w-full" />
            </div>
        );
    }

    return (
        <div className="space-y-3">
            <BucketSwitch value={bucket} onChange={onBucketChange} totals={totals} />

            {groups.length === 0 && orphanCount === 0 ? (
                <div className={`${iosCard} flex flex-col items-center gap-2 px-6 py-14 text-center`}>
                    <div className="grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                        <Layers size={22} />
                    </div>
                    <div className="text-[15px] font-semibold text-slate-900">Разделов пока нет</div>
                    <p className="max-w-sm text-[13px] leading-relaxed text-slate-500">
                        Вам не открыт ни один раздел вики. Доступ выдаёт руководитель
                        на вкладке «Структура».
                    </p>
                </div>
            ) : (
                <>
                    {groups.map(({ space, rows }) => (
                        <section key={space.id} className="space-y-1.5">
                            <div className={iosGroupLabel}>{space.name}</div>
                            <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 xl:grid-cols-4">
                                {rows.map((section) => (
                                    <SectionTile
                                        key={section.id}
                                        section={section}
                                        path={pathById.get(section.id)}
                                        count={section.counts?.[bucket] ?? 0}
                                        onOpen={() => openSection(section, pathById.get(section.id))}
                                    />
                                ))}
                            </div>
                        </section>
                    ))}

                    {/* Плитка появляется только когда бесхозные статьи есть: пустая
                        строка «Без раздела» — это вопрос без ответа. */}
                    {orphanCount > 0 && (
                        <section className="space-y-1.5">
                            <div className={iosGroupLabel}>Вне дерева</div>
                            <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 xl:grid-cols-4">
                                <SectionTile
                                    section={{ id: ORPHANS_ID, name: 'Без раздела' }}
                                    path=""
                                    count={orphanCount}
                                    onOpen={() => openSection({ id: ORPHANS_ID, name: 'Без раздела' }, '')}
                                />
                            </div>
                        </section>
                    )}
                </>
            )}

            <IosModal
                open={!!open}
                onClose={() => setOpen(null)}
                title={open?.name || ''}
                subtitle={[open?.path, active.label].filter(Boolean).join(' · ')}
                maxWidth="max-w-2xl"
            >
                {busy && (
                    <div className="flex items-center justify-center gap-2 py-12 text-slate-400">
                        <Loader2 size={16} className="animate-spin" />
                        <span className="text-[13px]">Загружаем…</span>
                    </div>
                )}

                {!busy && items && items.length > 5 && (
                    <div className="mb-2 flex items-center gap-2 rounded-xl bg-slate-100 px-3 py-2 transition focus-within:bg-white focus-within:ring-2 focus-within:ring-blue-500/70">
                        <Search size={15} className="shrink-0 text-slate-400" />
                        <input
                            value={filter}
                            onChange={(e) => setFilter(e.target.value)}
                            placeholder="Фильтр по названию"
                            className="wiki-focus-outside w-full min-w-0 bg-transparent text-[13px] text-slate-900 placeholder-slate-400 focus:outline-none"
                        />
                    </div>
                )}

                {!busy && items && items.length === 0 && (
                    <div className="flex flex-col items-center gap-2 px-6 py-12 text-center">
                        <div className="grid h-11 w-11 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                            <active.icon size={20} />
                        </div>
                        <div className="text-[14px] font-semibold text-slate-900">Пусто</div>
                        <p className="max-w-sm text-[12.5px] leading-relaxed text-slate-500">
                            {active.empty}
                        </p>
                    </div>
                )}

                {!busy && shown && shown.length === 0 && items.length > 0 && (
                    <div className="px-3 py-10 text-center text-[13px] text-slate-500">
                        Ничего не найдено по запросу «{filter.trim()}».
                    </div>
                )}

                {!busy && shown && shown.length > 0 && (
                    <div className="-mx-1 divide-y divide-slate-200/70">
                        {shown.map((article) => (
                            <ArticleRow
                                key={article.id}
                                article={article}
                                showStatus={bucket !== 'published'}
                                onOpen={() => { setOpen(null); onOpenArticle(article.slug); }}
                            />
                        ))}
                    </div>
                )}
            </IosModal>
        </div>
    );
}
