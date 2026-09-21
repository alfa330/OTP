import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
    ArrowRightLeft, Check, ChevronRight, Folder, FolderOpen, Layers, Loader2, Search, X,
} from 'lucide-react';
import { iosBtnPrimary, iosBtnSecondary } from '../ui/ios';
import { sectionAncestors, sectionPathLabel, sectionTreeRows } from './sectionPicker';
import {
    SEARCH_FROM, currentSectionIds, keptNote, mayPutArticle, movePlan, moveSources,
    rightsAreKnown,
} from './articleMove';

/* Перенос статьи в другой раздел — панель прямо в строке каталога.
 *
 * ── Почему не модальное окно ───────────────────────────────────────────────
 *
 * Вопрос «куда переложить эту статью» решают, глядя на дерево разделов и на
 * список статей вокруг: «Прощание» переносят туда, где уже лежат «Приветствие»
 * и «Удержание». Модалка накрывает собой ровно это — и дерево слева, и соседние
 * строки, — то есть закрывает то, по чему принимают решение. Поэтому панель
 * раскрывается ПОД строкой, на своём месте в потоке страницы: статья, которую
 * переносят, остаётся видна над панелью и не даёт перепутать строку.
 *
 * ── Почему подтверждение внутри той же панели ──────────────────────────────
 *
 * Перенос — распоряжение содержимым ДВУХ разделов, и спросить о нём обязательно.
 * Но второй экран поверх первого («вы уверены?») отобрал бы дерево именно в тот
 * момент, когда человек хочет свериться: тот ли это «Оператор» — у СЗоВ и у ОП
 * ветки называются одинаково. Поэтому выбранный раздел подсвечивается в дереве,
 * а полоса подтверждения раскрывается под ним: видно и что выбрано, и где оно
 * лежит. Нажали на другую строку — полоса переписалась, а не закрылась.
 *
 * ── Права ──────────────────────────────────────────────────────────────────
 *
 * Бледные строки — разделы, куда этот человек класть статьи не вправе
 * (can_create в правиле раздела, его же спрашивает сервер). Показываем их, а не
 * прячем: дерево обязано повторять оргструктуру, и выпавшая середина ветки
 * сложила бы его в неправду — ровно тот дефект, который уже чинили в
 * «Структуре». Почему строка бледная, сказано словами под деревом: намёк
 * цветом читается как «сломалось».
 */

/* Высота дерева. Ограничена намеренно: под панелью лежат остальные строки
   списка, и дерево из сорока разделов уводило бы полосу подтверждения под
   сгиб — то есть прятало бы кнопку, ради которой панель открыли. */
const TREE_MAX = 'max-h-[42vh]';

/* Сколько длится раскрытие. То же число, что у панели в WikiCatalog
   (duration-300) и у UNFOLD_MS там же: по нему прокрутка ждёт, пока раскрытие
   кончится, — иначе в вид приезжает полоса нулевой высоты. */
const UNFOLD_MS = 300;

/* Показать элемент, ТОЛЬКО если он не виден целиком.
 *
 * Безусловный scrollIntoView дёргает страницу там, где дёргать нечего: на
 * мониторе панель почти всегда открывается на виду. Хуже того, прокрутка гасит
 * открытое меню строки (IosMenu закрывается от любого скролла — иначе оно
 * «приклеилось» бы над чужой строкой), и человек, успевший открыть «три точки»
 * соседней статьи, увидел бы, как меню тут же схлопнулось само. */
const reveal = (el) => {
    if (!el) return;
    const box = el.getBoundingClientRect();
    if (box.top >= 0 && box.bottom <= window.innerHeight) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
};

const toggled = (set, id) => {
    const next = new Set(set);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
};

/* Строка дерева. Три состояния сверх обычного, и все три — ответы на вопрос
   «почему я не могу это выбрать»: здесь статья лежит сейчас, здесь она уже
   лежит тоже, сюда её класть не вправе. */
const TargetRow = ({ section, depth, hasChildren, open, chosen, here, already, allowed,
                     onToggle, onChoose }) => (
    <div
        className={`flex items-center rounded-lg transition ${
            chosen ? 'bg-indigo-50 ring-1 ring-indigo-200' : allowed ? 'hover:bg-slate-100' : ''
        }`}
        style={{ paddingLeft: `${4 + depth * 12}px` }}
    >
        {hasChildren ? (
            <button
                type="button"
                aria-expanded={open}
                aria-label={open ? 'Свернуть подразделы' : 'Развернуть подразделы'}
                onClick={onToggle}
                className="grid h-7 w-7 shrink-0 place-items-center rounded text-slate-400 transition hover:bg-slate-200/70 hover:text-slate-600"
            >
                <ChevronRight size={12} className={`transition-transform ${open ? 'rotate-90' : ''}`} />
            </button>
        ) : (
            <span className="h-7 w-7 shrink-0" />
        )}

        <button
            type="button"
            disabled={!allowed}
            onClick={onChoose}
            title={allowed ? undefined : 'Класть статьи в этот раздел вам нельзя'}
            className={`wiki-move-row flex min-w-0 flex-1 items-center gap-1.5 py-1.5 pr-2 text-left ${
                allowed ? '' : 'cursor-default'
            }`}
        >
            {open && hasChildren
                ? <FolderOpen size={13} className={`shrink-0 ${allowed ? 'text-amber-500' : 'text-slate-300'}`} />
                : <Folder size={13} className={`shrink-0 ${allowed ? 'text-amber-500' : 'text-slate-300'}`} />}
            {/* Метка стоит ВПЛОТНУЮ к названию, а не у правого края: панель
                во всю ширину колонки, и «здесь сейчас» у края отвечало бы на
                вопрос о строке, до которой полсотни пустых пикселей. Подпись
                есть только у исключения: она и есть причина, по которой строка
                не выбирается, — у остальных причины нет. */}
            <span className="flex min-w-0 flex-1 items-center gap-1.5">
                <span className={`truncate text-[12.5px] ${
                    chosen ? 'font-bold text-indigo-900'
                           : allowed ? 'font-semibold text-slate-800' : 'text-slate-400'
                }`}>
                    {section.name}
                </span>
                {here && (
                    <span className="shrink-0 text-[10.5px] font-medium text-indigo-500">здесь сейчас</span>
                )}
                {already && !here && (
                    <span className="shrink-0 text-[10.5px] text-slate-400">уже там</span>
                )}
            </span>
            {chosen && <Check size={13} className="shrink-0 text-indigo-600" />}
        </button>
    </div>
);

export default function ArticleMovePanel({
    article, sections = [], spaces = [], names = null,
    fromId = null, toId = null, busy = false,
    onFrom = () => {}, onTo = () => {}, onConfirm = () => {}, onClose = () => {},
}) {
    const [query, setQuery] = useState('');
    /* Ветка, в которой статья лежит сейчас, раскрыта с первого кадра: переносят
       почти всегда по соседству, и начинать с закрытого дерева значило бы
       заставить человека заново искать то место, откуда он пришёл. */
    const [expanded, setExpanded] = useState(
        () => new Set(sectionAncestors(sections, fromId).map((s) => s.id)));
    const rootRef = useRef(null);
    const confirmRef = useRef(null);

    /* Панель раскрывается ПОД строкой, а строка бывает последней на экране: на
       телефоне так почти всегда. Показываем её сами — но только когда она уже
       раскрылась: прокрути раньше, и в вид приедет полоса нулевой высоты.
       Срок тот же, что у самого раскрытия (duration-300 в WikiCatalog). */
    useEffect(() => {
        const id = window.setTimeout(() => reveal(rootRef.current), UNFOLD_MS + 40);
        return () => window.clearTimeout(id);
    }, []);

    const known = rightsAreKnown(sections);
    const current = currentSectionIds(article);
    const sources = useMemo(
        () => moveSources(article, sections, names), [article, sections, names]);
    const plan = movePlan(sections, article, fromId, toId);

    /* Текст полосы подтверждения пишется по ПОСЛЕДНЕМУ выбранному разделу, а
       решение о кнопке — по текущему (plan). Разница нужна ровно на время
       складывания: полоса остаётся в разметке, чтобы сложиться плавно, и, читай
       она текущий выбор, на этих трёх десятых доли секунды в ней было бы
       «Переместить в «undefined»?» — сложившийся пустой прямоугольник вместо
       уезжающего вопроса. */
    const [lastTo, setLastTo] = useState(toId);
    useEffect(() => { if (toId) setLastTo(toId); }, [toId]);
    const shown = plan.ready ? plan : movePlan(sections, article, fromId, lastTo);
    const note = keptNote(shown);

    /* Показываем ли строки, в которые нельзя. Считаем по дереву, а не «есть ли
       вообще такие разделы»: пояснение под деревом обязано появляться только
       когда бледные строки в нём действительно есть. */
    const dimmed = useMemo(
        () => sections.some((s) => !mayPutArticle(s, known) && s.status !== 'archived'),
        [sections, known]);

    const hits = useMemo(() => {
        const q = query.trim().toLowerCase();
        if (!q) return null;
        return sections
            .filter((s) => s.status !== 'archived'
                && sectionPathLabel(sections, s.id).toLowerCase().includes(q))
            .slice(0, 40);
    }, [sections, query]);

    const choose = (section) => {
        onTo(section.id);
        /* Полоса подтверждения раскрывается ниже дерева, и на невысоком экране
           она оказывается за краем — нажатие выглядело бы как «ничего не
           произошло». Показываем её сами, ближайшим движением. */
        window.setTimeout(() => reveal(confirmRef.current), UNFOLD_MS + 40);
    };

    const rowProps = (section, depth, hasChildren) => {
        const here = Number(section.id) === Number(fromId);
        const already = current.includes(Number(section.id));
        return {
            section, depth, hasChildren,
            open: expanded.has(section.id),
            chosen: Number(section.id) === Number(toId),
            here,
            already,
            allowed: !here && !already && mayPutArticle(section, known),
            onToggle: () => setExpanded((prev) => toggled(prev, section.id)),
            onChoose: () => choose(section),
        };
    };

    const treeSpaces = spaces.filter((sp) => sp.status !== 'archived');

    return (
        <div ref={rootRef}
             className="rounded-2xl bg-white p-2.5 shadow-sm ring-1 ring-slate-200/70">
            <div className="flex items-start gap-2">
                <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-indigo-50 text-indigo-500">
                    <ArrowRightLeft size={13} />
                </span>
                <div className="min-w-0 flex-1">
                    <div className="text-[12.5px] font-bold tracking-[-0.01em] text-slate-900">
                        Куда переместить статью
                    </div>
                    {/* Откуда переносим. У статьи в одном разделе это просто
                        строка; у статьи в нескольких — выбор, и без него
                        перенос молча забрал бы её из того раздела, который
                        оказался первым в ответе API. */}
                    {sources.length === 0 && (
                        <div className="mt-0.5 text-[11.5px] text-slate-500">
                            Статья не привязана ни к одному разделу — выберите, куда её положить.
                        </div>
                    )}
                    {sources.length === 1 && (
                        <div className="mt-0.5 truncate text-[11.5px] text-slate-500">
                            Сейчас в разделе «{sources[0].name}»
                        </div>
                    )}
                    {sources.length > 1 && (
                        <div className="mt-1">
                            <div className="text-[11.5px] text-slate-500">
                                Статья лежит в {sources.length} разделах — из какого переносим:
                            </div>
                            <div className="mt-1 flex flex-wrap gap-1">
                                {sources.map((source) => {
                                    const on = Number(source.id) === Number(fromId);
                                    return (
                                        <button
                                            key={source.id}
                                            type="button"
                                            aria-pressed={on}
                                            disabled={!source.allowed}
                                            onClick={() => onFrom(source.id)}
                                            title={source.allowed
                                                ? undefined
                                                : 'Забирать статьи из этого раздела вам нельзя'}
                                            className={`wiki-move-row max-w-full truncate rounded-full px-2.5 py-1 text-[11.5px] font-medium transition ${
                                                !source.allowed
                                                    ? 'cursor-default bg-slate-100/70 text-slate-400'
                                                    : on
                                                        ? 'bg-indigo-50 text-indigo-700 ring-1 ring-indigo-200'
                                                        : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                                            }`}
                                        >
                                            {source.name}
                                        </button>
                                    );
                                })}
                            </div>
                        </div>
                    )}
                </div>
                <button
                    type="button"
                    onClick={onClose}
                    aria-label="Закрыть перенос"
                    className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
                >
                    <X size={13} />
                </button>
            </div>

            {/* Поиск появляется только у большого дерева: над десятком строк
                поле занимает место, ничего не решая. */}
            {sections.length >= SEARCH_FROM && (
                <div className="mt-2 flex items-center gap-2 rounded-lg bg-slate-100 px-2.5 py-1.5 transition focus-within:bg-white focus-within:ring-2 focus-within:ring-blue-500/70">
                    <Search size={13} className="shrink-0 text-slate-400" />
                    <input
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder="Найти раздел"
                        className="wiki-focus-outside w-full min-w-0 bg-transparent text-[12px] text-slate-900 placeholder-slate-400 focus:outline-none"
                    />
                    {query && (
                        <button
                            type="button"
                            onClick={() => setQuery('')}
                            aria-label="Очистить"
                            className="grid h-4 w-4 shrink-0 place-items-center rounded-full bg-slate-200 text-slate-500 transition hover:bg-slate-300"
                        >
                            <X size={9} />
                        </button>
                    )}
                </div>
            )}

            <div className={`mt-2 ${TREE_MAX} overflow-y-auto overscroll-contain rounded-xl bg-slate-50/70 p-1`}>
                {hits ? (
                    hits.length === 0 ? (
                        <p className="px-2 py-6 text-center text-[12px] text-slate-400">
                            Ни одного раздела с «{query.trim()}» в названии.
                        </p>
                    ) : hits.map((section) => {
                        const props = rowProps(section, 0, false);
                        return (
                            <TargetRow
                                key={section.id}
                                {...props}
                                hasChildren={false}
                                open={false}
                                /* В находках — весь путь: у СЗоВ и у ОП ветки
                                   называются одинаково, и плоский список из
                                   одних имён снова дал бы три неразличимых
                                   «Супервайзера». */
                                section={{ ...section, name: sectionPathLabel(sections, section.id) }}
                            />
                        );
                    })
                ) : treeSpaces.map((space) => {
                    const rows = sectionTreeRows(sections, space.id, expanded);
                    if (!rows.length) return null;
                    return (
                        <div key={space.id}>
                            {treeSpaces.length > 1 && (
                                <div className="flex items-center gap-1.5 px-2 pb-0.5 pt-1.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-400">
                                    <Layers size={11} /> {space.name}
                                </div>
                            )}
                            {rows.map(({ section, depth, hasChildren }) => (
                                <TargetRow key={section.id} {...rowProps(section, depth, hasChildren)} />
                            ))}
                        </div>
                    );
                })}
            </div>

            {dimmed && (
                /* Одной фразой. Куда идти за правом, здесь не рассказываем:
                   строк объяснений под каждой панелью набирается больше, чем
                   самой панели, а этот экран принадлежит тому, кто и так знает
                   про вкладку «Структура». */
                <p className="mt-1.5 px-1 text-[11px] leading-relaxed text-slate-400">
                    Бледные разделы выбрать нельзя: класть в них статьи вам не разрешено.
                </p>
            )}

            {/* ── Подтверждение ──────────────────────────────────────────
                Раскрывается сеткой 0fr → 1fr, а не появляется рывком: высота
                считается по содержимому, поэтому полоса с одной строкой и с
                тремя раскрываются одинаково плавно. Тот же приём, что у
                пояснений в «Новостях». */}
            <div
                ref={confirmRef}
                className={`grid transition-all duration-300 ease-out ${
                    plan.ready ? 'mt-2 grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0'
                }`}
                aria-hidden={!plan.ready}
            >
                <div className="overflow-hidden">
                    <div className="rounded-xl bg-amber-50/80 px-3 py-2.5 ring-1 ring-amber-200/70">
                        <div className="text-[12.5px] font-semibold leading-snug text-amber-900">
                            {shown.to ? `Переместить в «${shown.to.name}»?` : 'Переместить?'}
                        </div>
                        <p className="mt-0.5 text-[11.5px] leading-relaxed text-amber-900/80">
                            {shown.from && shown.to
                                ? `Статья «${article?.title}» пропадёт из «${shown.from.name}» и появится в «${shown.to.name}».`
                                : shown.to
                                    ? `Статья «${article?.title}» появится в «${shown.to.name}».`
                                    : ''}
                            {' '}
                            {/* Про доступ говорим прямо: раздел решает, кто
                                статью видит, и перенос меняет круг читателей —
                                это главное последствие, а не подробность. */}
                            Кому она видна, решают правила нового раздела.
                            {note ? ` ${note}` : ''}
                        </p>
                        <div className="mt-2 flex items-center justify-end gap-2">
                            <button
                                type="button"
                                /* Погашена вместе со всей полосой: свёрнутая
                                   полоса остаётся в разметке ради плавного
                                   раскрытия, и живая кнопка внутри неё ловила
                                   бы Tab у скрытого от глаз блока. */
                                disabled={busy || !plan.ready}
                                onClick={() => onTo(null)}
                                className={iosBtnSecondary}
                            >
                                Отмена
                            </button>
                            <button
                                type="button"
                                disabled={busy || !plan.ready}
                                onClick={onConfirm}
                                className={iosBtnPrimary}
                            >
                                {busy
                                    ? <><Loader2 size={13} className="animate-spin" /> Переносим…</>
                                    : <><ArrowRightLeft size={13} /> Переместить</>}
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
