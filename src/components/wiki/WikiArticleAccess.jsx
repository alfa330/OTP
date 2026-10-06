import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import axios from 'axios';
import {
    Building2, EyeOff, FileText, Folder, Layers, Loader2, Lock, RotateCw, Search,
} from 'lucide-react';
import {
    iosCard, iosGroupLabel, iosInput, iosBtnPrimary, iosBtnSecondary, IosBadge, IosHint,
    IosModal, IosPager,
} from '../ui/ios';
import {
    ACCESS_LOADING, buildPlaceTree, hiddenPlacesLabel, listHint, listTitle,
    loadArticleAccess, personRows, statusNotice,
} from './articleAccess';
import { paginate, searchPeople } from './peopleSearch';
import { spaceIcon } from './spaceIdentity';

/* «Расположение и доступ» — справка в самой статье.
 *
 * Решение владельца 06.10.2026: «находясь в статье, человек, у которого есть
 * доступ на редактирование, мог просматривать, кому доступен данный раздел… и
 * показывать в дереве, где находится данная статья». До этого оба ответа жили
 * во вкладке «Статьи → Структура»: раздел надо было найти в дереве по памяти, а
 * «Кому открыт раздел» открывалось только тому, кто вправе РАЗДАВАТЬ доступ.
 *
 * В тот же день владелец уточнил, каким должен быть ответ: не правила, а люди —
 * «кому открыта статья, просто выводить списком… с пагинацией, и поиск как в
 * вики, который учитывает ошибки… можно будет просматривать, кому и как открыт
 * раздел». Поэтому под деревом стоит один список: человек, его должность и
 * отдел, и одно слово о том, как ему открыто.
 *
 * И развёл их по людям: «кому открыт — у суперадмина, а где находится сама
 * статья — редакторам и выше». Редактор видит одно дерево, и окно у него
 * зовётся «Расположение»; супер-админу сервер присылает ещё и список людей
 * (people в ответе), и окно становится «Расположением и доступом».
 *
 * У супер-админа окно широкое, в две колонки: «слева список людей, кому открыт
 * доступ, с пагинацией, и справа дерево, где находится статья» (его же слова).
 * На узком экране колонки встают друг под другом, дерево — первым: оно в три
 * строки, а список в десять, и под списком до дерева пришлось бы листать.
 *
 * ── Только чтение ────────────────────────────────────────────────────────
 * Ни одного органа управления, и это не недоделка: выдача доступа живёт в
 * «Структуре» со своей лестницей (кто кому по чину, чей отдел, высота раздела).
 * Окно отвечает на вопрос «кто это прочитает» тому, кто пишет текст, — а писать
 * вправе и тренер, и автор статьи, которым раздавать доступ не положено.
 *
 * ── Кто в списке, считает сервер ─────────────────────────────────────────
 * Поимённо и тем же расчётом, что сам доступ (wiki/readers.py): правила
 * раздела и разделов выше, публичность, гости, правила самой статьи,
 * администраторы. Окно ничего не досчитывает — только ищет и листает.
 *
 * ── Одна статья в нескольких разделах ────────────────────────────────────
 * Места рисуются ОДНИМ деревом с общим стволом, а не списком путей: статья
 * одна, мест несколько, и список людей у неё тоже один. Раздел, которого
 * смотрящему не видно, сервер не называет — приходит только число.
 */

/* Людей на странице. Поиск и пейджер появляются, когда страниц больше одной. */
const PAGE_SIZE = 10;

/* Отступ вложенности — переменной, как в дереве «Структуры»: на телефоне шаг
   меньше, иначе шестой уровень лестницы оставлял названию треть экрана. */
const rowIndent = 'pl-[calc(12px+var(--depth)*14px)] sm:pl-[calc(14px+var(--depth)*18px)]';

// ── Строка дерева ───────────────────────────────────────────────────────────
const TreeRow = ({ row, articleTitle }) => {
    const style = { '--depth': row.depth };

    if (row.kind === 'space') {
        const icon = spaceIcon(row.space);
        return (
            <div style={style} className={`flex items-center gap-2 py-1.5 pr-3 ${rowIndent}`}>
                {icon
                    ? <span className="grid h-4 w-4 shrink-0 place-items-center text-[13px] leading-none">{icon}</span>
                    : <Layers size={15} className="shrink-0 text-indigo-500" />}
                <span className="min-w-0 truncate text-[13px] font-semibold text-slate-900">
                    {row.name || 'Пространство'}
                </span>
            </div>
        );
    }

    if (row.kind === 'section') {
        return (
            <div style={style} className={`flex items-center gap-2 py-1.5 pr-3 ${rowIndent}`}>
                {/* Ветка отдела и должность внутри неё — те же значки, что в
                    дереве «Структуры»: иначе одно дерево в двух окнах
                    выглядело бы двумя разными. */}
                {row.branch
                    ? <Building2 size={15} className="shrink-0 text-indigo-500" />
                    : <Folder size={15} className="shrink-0 text-amber-500" />}
                <span className={`min-w-0 truncate text-[13px] ${
                    row.home ? 'font-medium text-slate-900' : 'text-slate-600'}`}>
                    {row.name}
                </span>
                {row.archived && <IosBadge tone="slate" className="shrink-0">в архиве</IosBadge>}
            </div>
        );
    }

    /* Строка статьи — отметка «она лежит здесь», а не кнопка: список людей у
       статьи один на все её места, и выбирать между ними нечего. Плашка
       начинается левее значка (-ml-2 + pl-2), иначе значок упирался бы в её
       край. */
    return (
        <div style={style} className={`pr-2 ${rowIndent}`}>
            <div className="-ml-2 flex items-start gap-2 rounded-lg bg-indigo-50 py-1.5 pl-2 pr-2.5">
                <FileText size={15} className="mt-0.5 shrink-0 text-indigo-600" />
                {/* До двух строк, а не одна с многоточием: в колонке рядом со
                    списком длинное название обрывалось на полуслове. */}
                <span className="line-clamp-2 min-w-0 flex-1 break-words text-[13px] font-medium leading-snug text-indigo-700">
                    {articleTitle || 'Эта статья'}
                </span>
            </div>
        </div>
    );
};

// ── Строка списка людей ─────────────────────────────────────────────────────
/* Тот же вид, что у строки «Доступ к разделу» (WikiSectionAccess: AccessRow):
   слева кто, справа одно слово о том, как открыто. Шеврона нет — открывать
   нечего. */
const PersonRow = ({ title, meta, notes, value }) => (
    <div className="flex items-center gap-3 px-4 py-3">
        <div className="min-w-0 flex-1">
            <div className="break-words text-[14px] font-medium text-slate-900">{title}</div>
            {meta && (
                <div className="mt-0.5 break-words text-[11.5px] leading-snug text-slate-400">
                    {meta}
                </div>
            )}
            {notes && (
                <div className="mt-1 break-words text-[11.5px] leading-snug text-slate-400">
                    {notes}
                </div>
            )}
        </div>
        <span className="shrink-0 text-[13px] text-slate-700">{value}</span>
    </div>
);

const Notice = ({ icon: Icon, tone = 'slate', children }) => (
    <div className={`flex items-start gap-2 rounded-2xl px-4 py-3 text-[12.5px] leading-relaxed ${
        tone === 'amber' ? 'bg-amber-50 text-amber-800' : 'bg-slate-100 text-slate-600'}`}>
        <Icon size={15} className="mt-0.5 shrink-0" />
        <span>{children}</span>
    </div>
);

const EmptyCard = ({ children }) => (
    <div className={`${iosCard} px-4 py-6 text-center text-[12.5px] leading-relaxed text-slate-400`}>
        {children}
    </div>
);

/* Заголовок блока с подсказкой. «i» стоит у ПРАВОГО края строки и раскрывается
   влево. Сразу за заголовком, с пузырьком вправо, она на телефоне оказывалась у
   края экрана: из 256 px пузырька тело окна срезало две трети. От правого края
   пузырьку хватает ширины и на 320 px.

   Высота строки задана явно: в окне супер-админа два таких заголовка стоят
   рядом, над списком и над деревом, и без неё тот, что с «i», оказывался выше
   соседа — карточки под ними начинались вразнобой. */
const GroupHead = ({ children, hint = null, hintLabel = null }) => (
    <div className="flex min-h-6 items-center justify-between gap-2 pr-1">
        <span className={`${iosGroupLabel} min-w-0`}>{children}</span>
        {hint && (
            <span className="shrink-0">
                <IosHint align="right" label={hintLabel} text={hint} />
            </span>
        )}
    </div>
);

/* Список «кому открыта статья» — супер-админу. Отдельным компонентом: у
   редактора его нет вовсе, и заводить ему поиск со страницами незачем. */
const Readers = ({ source, status, byListOnly, defaultQuery, defaultPage }) => {
    const people = useMemo(() => personRows(source), [source]);
    const [query, setQuery] = useState(defaultQuery);
    const [page, setPage] = useState(defaultPage);
    const found = useMemo(() => searchPeople(people, query), [people, query]);
    const shown = paginate(found, page, PAGE_SIZE);
    const notice = statusNotice(status);

    return (
        <section className="space-y-1.5">
            <GroupHead hint={listHint(byListOnly)} hintLabel="Кто попадает в список">
                {listTitle(status)}
                {people.length > 0 && (
                    <span className="ml-1.5 font-normal tabular-nums text-slate-400">
                        {people.length}
                    </span>
                )}
            </GroupHead>
            {/* Оговорка о невышедшей статье — про тех, кто её видит сейчас,
                поэтому стоит в списке, а не над окном. */}
            {notice && <Notice icon={EyeOff}>{notice}</Notice>}
            {/* «Не открывает», а не «не действует»: в строгом режиме
                правило раздела по-прежнему добавляет прав тому, кто в
                списке, — закрыто им только чтение. */}
            {byListOnly && (
                <Notice icon={Lock} tone="amber">
                    Статья открыта только по списку — правило раздела её не открывает.
                </Notice>
            )}
            {/* Поиск — только когда людей больше страницы: над пятью
                строками поле было бы лишним органом управления. */}
            {people.length > PAGE_SIZE && (
                <div className="relative">
                    <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                        value={query}
                        onChange={(event) => { setQuery(event.target.value); setPage(1); }}
                        placeholder="Имя, должность или отдел"
                        aria-label="Поиск по списку"
                        autoComplete="off"
                        className={`${iosInput} pl-9`}
                    />
                </div>
            )}
            {/* Пейджер НАД списком, как в каталоге вики и в импорте: под
                списком до него пришлось бы каждый раз прокручивать десять
                строк, а после перехода человек оказывался бы в конце
                новой страницы, а не в её начале. */}
            <IosPager
                page={shown.page}
                pageCount={shown.pageCount}
                total={shown.total}
                from={shown.from}
                to={shown.to}
                onPage={setPage}
                unit="сотрудники"
            />
            {shown.items.length > 0 ? (
                <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                    {shown.items.map(({ key, nameWords, words, ...row }) => (
                        <PersonRow key={key} {...row} />
                    ))}
                </div>
            ) : (
                <EmptyCard>
                    {people.length > 0 ? 'Никого не нашлось.' : 'Статья не открыта никому.'}
                </EmptyCard>
            )}
        </section>
    );
};

/* Экран с уже загруженными данными. Отдельно от окна, чтобы его можно было
   отрисовать без сети (tests/wiki_article_access.test.mjs): само окно ходит на
   сервер в эффекте, и серверный рендер дальше «Загружаем…» не уходит.
   defaultQuery и defaultPage — стартовые значения поиска и страницы, как у
   неуправляемого поля: окно их не передаёт, они нужны тому же рендеру. */
export function ArticleAccessView({ data, articleTitle, defaultQuery = '', defaultPage = 1 }) {
    const places = data?.places || [];
    const tree = useMemo(() => buildPlaceTree(places), [places]);
    /* Список людей сервер присылает не каждому: null — смотрящему он не
       положен, и окно состоит из одного дерева. */
    const withReaders = Array.isArray(data?.people);

    const hidden = Number(data?.hidden_places) || 0;

    const treeSection = (
        <section className="space-y-1.5">
            {/* Над единственным блоком подпись повторяла бы заголовок окна. */}
            {withReaders && <GroupHead>Где лежит</GroupHead>}
            {tree.length > 0 ? (
                <div className={`${iosCard} overflow-hidden py-1.5`}>
                    {tree.map((row) => (
                        <TreeRow key={row.key} row={row} articleTitle={articleTitle} />
                    ))}
                </div>
            ) : (
                <EmptyCard>
                    {hidden > 0
                        ? 'Разделы этой статьи вам не видны.'
                        : 'Статья не привязана ни к одному разделу.'}
                </EmptyCard>
            )}
            {tree.length > 0 && hidden > 0 && (
                <p className="px-1 text-[11.5px] leading-relaxed text-slate-400">
                    {hiddenPlacesLabel(hidden, withReaders)}
                </p>
            )}
        </section>
    );
    if (!withReaders) return treeSection;

    /* Две колонки с ширины окна, на которой списку остаётся его прежняя
       ширина. Дерево в разметке стоит первым (на узком экране оно сверху) и
       уезжает вправо порядком колонок; при длинном списке оно держится у
       верхнего края, чтобы место статьи оставалось на виду. */
    return (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,380px)] lg:items-start">
            <div className="lg:sticky lg:top-0 lg:order-2">{treeSection}</div>
            <div className="min-w-0 lg:order-1">
                <Readers
                    source={data.people}
                    status={data?.article?.status}
                    byListOnly={!!data?.article?.by_list_only}
                    defaultQuery={defaultQuery}
                    defaultPage={defaultPage}
                />
            </div>
        </div>
    );
}

/* Тело окна по состоянию запроса. Три состояния, а не «данные или пусто»:
   сорвавшийся запрос обязан выглядеть отказом, а не уверенным «раздел никому не
   открыт» — по такому экрану пошли бы раздавать доступ, который уже выдан.
   Отдельным компонентом ради того же серверного рендера: в самом окне состояние
   меняет эффект, и дальше «Загружаем…» тест бы не ушёл. */
export function ArticleAccessBody({ state, articleTitle, onRetry }) {
    if (state.status === 'failed') {
        return (
            <div className="flex flex-col items-center gap-2 px-4 py-12 text-center">
                <p className="text-[12.5px] leading-relaxed text-slate-500">{state.error}</p>
                <button type="button" className={iosBtnSecondary} onClick={onRetry}>
                    <RotateCw size={14} /> Повторить
                </button>
            </div>
        );
    }
    if (state.status === 'ready') {
        return <ArticleAccessView data={state.data} articleTitle={articleTitle} />;
    }
    return (
        <div className="flex items-center justify-center gap-2 py-14 text-slate-400">
            <Loader2 size={16} className="animate-spin" />
            <span className="text-[13px]">Загружаем…</span>
        </div>
    );
}

export default function WikiArticleAccess({ base, headers, article, open, onClose }) {
    const [state, setState] = useState(ACCESS_LOADING);
    const articleId = article?.id;

    /* Окно открыли заново — прежний ответ показывать нельзя ни на кадр: за ним
       может стоять другая статья. Сбрасываем ПРИ РЕНДЕРЕ, а не в эффекте:
       эффект отработал бы после первой отрисовки, и вчерашний список успел бы
       мигнуть. При закрытии состояние не трогаем — на телефоне окно уезжает
       анимацией, и содержимое в нём должно остаться до конца. */
    const [wasOpen, setWasOpen] = useState(open);
    if (open !== wasOpen) {
        setWasOpen(open);
        if (open) setState(ACCESS_LOADING);
    }

    /* Сам запрос и разбор ответа — в loadArticleAccess (articleAccess.js): там
       отказ превращается в состояние, а не в исключение. Ответ, пришедший
       после закрытия окна или смены статьи, выбрасываем: за ним уже другой
       экран. */
    const load = useCallback(() => {
        if (!articleId) return undefined;
        let cancelled = false;
        setState(ACCESS_LOADING);
        loadArticleAccess(axios.get, { base, articleId, headers })
            .then((next) => { if (!cancelled) setState(next); });
        return () => { cancelled = true; };
    }, [base, headers, articleId]);

    /* Грузим при КАЖДОМ открытии, а не один раз на статью: окно открывают,
       чтобы узнать, как есть сейчас, а правила за это время могли поменять в
       соседней вкладке. */
    useEffect(() => {
        if (!open) return undefined;
        return load();
    }, [open, load]);

    const modal = (
        <IosModal
            open={open}
            onClose={onClose}
            title={article?.can_view_readers ? 'Расположение и доступ' : 'Расположение'}
            subtitle={article?.title}
            maxWidth={article?.can_view_readers ? 'max-w-5xl' : 'max-w-xl'}
            footer={(
                <button type="button" className={iosBtnPrimary} onClick={onClose}>Готово</button>
            )}
        >
            <ArticleAccessBody state={state} articleTitle={article?.title} onRetry={load} />
        </IosModal>
    );

    /* Окно рисуется в body, а не на месте вызова, и причин две — обе видны
       только в браузере. Страница статьи раскладывает детей через space-y, и
       отступ между ними доставался бы и самому окну: сверху оставалась полоса
       в 16 px, не закрытая затемнением (на телефоне экран съезжал на те же
       16 px). А в режиме «Во весь экран» статья сама становится слоем z-40, и
       окно внутри неё оказывалось ПОД сайдбаром портала — затемнение обрывалось
       у его края. Вне браузера (серверный рендер в тестах) портала нет.

       Обёртка wiki-scope обязательна, хотя своего места она не занимает
       (display: contents): на телефоне кегль раздела поднимает слой
       wiki-mobile.css, и отбирает он строки по предку .wiki-scope. Без неё
       окно, уехавшее в body, осталось бы с настольными 13 px посреди раздела,
       где всё остальное набрано по лестнице iOS. Тот же приём — у
       полноэкранного поиска (WikiSearch). */
    if (typeof document === 'undefined') return modal;
    return createPortal(<div className="wiki-scope contents">{modal}</div>, document.body);
}
