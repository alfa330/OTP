import React from 'react';
import { Building2, Clock, Eye, FileText, FolderTree, PenLine, Star } from 'lucide-react';
import { iosCard } from '../ui/ios';

/* Центральная колонка витрины, когда человек ничего не ищет: счётчики, два
 * коротких списка «про меня» и популярные статьи.
 *
 * Здесь намеренно нет ссылок «Все» и «Настроить» из макета: за ними не стоит ни
 * одного экрана, а мёртвая кнопка в интерфейсе хуже её отсутствия. Всё, что
 * можно открыть, открывается — карточки кликабельны.
 */

const DAY = 24 * 60 * 60 * 1000;

/* «вчера» вместо «11.08.26»: в списке недавнего важна давность, а не дата. */
const fmtAgo = (iso) => {
    if (!iso) return '';
    const then = new Date(iso);
    if (Number.isNaN(then.getTime())) return '';
    const midnight = new Date();
    midnight.setHours(0, 0, 0, 0);
    const days = Math.floor((midnight.getTime() - then.getTime()) / DAY) + 1;
    if (days <= 0) return 'сегодня';
    if (days === 1) return 'вчера';
    if (days < 7) return `${days} дн. назад`;
    return then.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit' });
};

/* Счётчик — кнопка, а не подпись.
 *
 * Раньше это были четыре мёртвых числа: человек читал «9 черновиков» и шёл
 * искать их руками. Теперь число ведёт ровно туда, где лежит то, что оно
 * посчитало, — в каталог с нужной корзиной или в справочник парков.
 *
 * Иконка нужна как раз потому, что плитка стала кнопкой: без неё четыре
 * одинаковых прямоугольника с цифрами не читаются как действие.
 */
const StatTile = ({ icon: Icon, value, label, hint, onClick }) => (
    <button
        type="button"
        onClick={onClick}
        title={hint}
        className="group rounded-xl px-3 py-2.5 text-left ring-1 ring-slate-200/70 transition hover:bg-slate-50 hover:ring-slate-300 active:scale-[0.98]"
    >
        <div className="flex items-center gap-1.5">
            <Icon size={11} className="shrink-0 text-slate-400 transition group-hover:text-indigo-500" />
            <div className="text-[19px] font-bold leading-none tracking-[-0.02em] text-slate-900 tabular-nums">
                {value}
            </div>
        </div>
        <div className="mt-1 text-[10.5px] text-slate-500">{label}</div>
    </button>
);

const MiniCard = ({ title, subtitle, meta, onClick }) => (
    <button
        type="button"
        onClick={onClick}
        className="min-w-0 rounded-xl px-2.5 py-2 text-left ring-1 ring-slate-200/70 transition hover:bg-slate-50 active:scale-[0.99]"
    >
        <div className="truncate text-[12px] font-semibold tracking-[-0.01em] text-slate-900">{title}</div>
        <div className="mt-0.5 truncate text-[10.5px] text-slate-500">{subtitle}</div>
        {meta && <div className="mt-1.5 flex items-center gap-1.5 text-[9.5px] text-slate-400">{meta}</div>}
    </button>
);

const Panel = ({ icon: Icon, title, empty, items, children }) => (
    <div className={`${iosCard} flex min-w-0 flex-col`}>
        <div className="flex items-center gap-1.5 px-3 pb-1.5 pt-2.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-500">
            <Icon size={12} /> {title}
        </div>
        {items.length === 0
            ? <div className="px-3 pb-4 pt-2 text-[11.5px] leading-relaxed text-slate-400">{empty}</div>
            : <div className="grid grid-cols-2 gap-2 px-2.5 pb-2.5">{children}</div>}
    </div>
);

const POP_TONES = [
    'bg-indigo-50 text-indigo-600',
    'bg-emerald-50 text-emerald-600',
    'bg-orange-50 text-orange-600',
    'bg-pink-50 text-pink-600',
];

export default function WikiHome({ isEditor, totals, sectionsTotal, parksCount,
                                   home, onOpen, onOpenCatalog, onOpenParks }) {
    const favorites = (home?.favorites || []).slice(0, 4);
    const recent = (home?.recent || []).slice(0, 4);
    const popular = (home?.popular || []).slice(0, 4);

    return (
        <>
            {/* Числа — из каталога, то есть из ПЕРИМЕТРА человека, а не из всей
                базы. Иначе счётчик обещал бы 29 статей, а список за ним отдавал
                двенадцать — те, к которым у человека есть доступ. */}
            {/* Плитка парков появляется, только если справочник в этом
                пространстве есть: у вики без парков она вела бы на вкладку,
                которой нет, и сообщала бы чужие 15 таксопарков. Сетка тогда
                на три колонки — пустая клетка читалась бы как «не догрузилось».
                Сравнение с null, а не с нулём: ноль парков — это «справочник
                есть и он пуст», и такую плитку прятать нельзя. */}
            {isEditor && totals && (
                <div className={`${iosCard} grid grid-cols-2 gap-2 p-2.5 ${
                    parksCount == null ? 'sm:grid-cols-3' : 'sm:grid-cols-4'}`}>
                    <StatTile
                        icon={FileText}
                        value={totals.published ?? 0}
                        label="Статей"
                        hint="Открыть каталог: опубликованные статьи"
                        onClick={() => onOpenCatalog?.('published')}
                    />
                    <StatTile
                        icon={PenLine}
                        value={totals.draft ?? 0}
                        label="Черновиков"
                        hint="Открыть каталог: черновики и статьи на согласовании"
                        onClick={() => onOpenCatalog?.('draft')}
                    />
                    {parksCount != null && (
                        <StatTile
                            icon={Building2}
                            value={parksCount}
                            label="Парков"
                            hint="Открыть справочник таксопарков"
                            onClick={() => onOpenParks?.()}
                        />
                    )}
                    <StatTile
                        icon={FolderTree}
                        value={sectionsTotal ?? 0}
                        label="Разделов"
                        hint="Открыть каталог разделов"
                        onClick={() => onOpenCatalog?.('published')}
                    />
                </div>
            )}

            {/* Две полки рядом — только на 2xl. Раньше: центр раздела на 1024 px
                отдаёт полке 309 px, и в двух колонках карточек от заголовка
                оставалось «Как заправить…». Ширина колонки здесь важнее того,
                что блок займёт две строки. */}
            {/* Полки «Черновики и модерация» здесь больше нет: черновики живут
                на вкладке «Статьи», и счётчик «Черновиков» выше открывает их
                там же — целиком и по разделам, а не первыми четырьмя. Держать
                на главной вторую, урезанную копию того же списка значило бы
                показывать одно и то же в двух местах и по-разному. */}
            <div className="grid grid-cols-1 gap-3 2xl:grid-cols-2">
                {/* Избранное показываем ВСЕГДА, а не «когда нет черновиков».
                    Раньше эти две полки делили одно место, и у редактора с
                    единственным черновиком избранное пропадало с экрана целиком
                    — вместе с единственным местом, где видно, что вообще
                    отмечено звездой. Полок стало больше на одну; сетка на две
                    колонки это выдерживает. */}
                <Panel
                    icon={Star}
                    title="Избранное"
                    items={favorites}
                    empty="Пусто. Звёздочка в шапке статьи оставляет её здесь."
                >
                    {favorites.map((article) => (
                        <MiniCard
                            key={article.id}
                            title={article.title}
                            subtitle={article.summary || 'Без описания'}
                            onClick={() => onOpen(article.slug)}
                        />
                    ))}
                </Panel>

                <Panel
                    icon={Clock}
                    title="Продолжить чтение"
                    items={recent}
                    empty="Здесь появятся статьи, которые вы открывали."
                >
                    {recent.map((article) => (
                        <MiniCard
                            key={article.id}
                            title={article.title}
                            subtitle={article.summary || 'Без описания'}
                            meta={fmtAgo(article.viewed_at)}
                            onClick={() => onOpen(article.slug)}
                        />
                    ))}
                </Panel>
            </div>

            {popular.length > 0 && (
                <div className={iosCard}>
                    <div className="px-3 pb-1.5 pt-2.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-500">
                        Популярные статьи
                    </div>
                    <div className="grid grid-cols-1 gap-2.5 p-2.5 pt-1 sm:grid-cols-2 2xl:grid-cols-4">
                        {popular.map((article, index) => (
                            <button
                                key={article.id}
                                type="button"
                                onClick={() => onOpen(article.slug)}
                                className="flex min-h-[112px] min-w-0 flex-col rounded-xl p-2.5 text-left ring-1 ring-slate-200/70 transition hover:bg-slate-50 active:scale-[0.99]"
                            >
                                <span className={`mb-2 grid h-7 w-7 place-items-center rounded-lg ${POP_TONES[index % POP_TONES.length]}`}>
                                    <FileText size={14} />
                                </span>
                                <span className="line-clamp-2 text-[12px] font-semibold leading-snug tracking-[-0.01em] text-slate-900">
                                    {article.title}
                                </span>
                                <span className="mt-1 line-clamp-2 text-[10.5px] leading-snug text-slate-500">
                                    {article.summary || 'Без описания'}
                                </span>
                                <span className="mt-auto flex items-center gap-1 pt-2 text-[9.5px] text-slate-400 tabular-nums">
                                    <Eye size={10} /> {article.views} просмотров
                                </span>
                            </button>
                        ))}
                    </div>
                </div>
            )}
        </>
    );
}
