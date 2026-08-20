import React, { Suspense, lazy, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import DOMPurify from 'dompurify';
import {
    Archive, ArrowLeft, Clock, Eye, Link2, List, Loader2, Maximize2, Minimize2,
    Pencil, Star, User,
} from 'lucide-react';
import { iosCard, iosGroupLabel, iosBtnSecondary, IosBadge } from '../ui/ios';
import { scrollToElement } from './scrollContainer';
import { absolutizeFileUrls } from './fileUrls';
import { buildArticleLink, readArticleSlugFromHref } from './articleLink';
import { distinctiveTokens, foldKazakh, queryVariants } from './searchText';
import WikiAckPanel from './WikiAckPanel';

/* Классификатор авто — статья вики с ПУСТЫМ телом: вместо текста рисуется
   интерактивный калькулятор. Раньше он был отдельным разделом портала, но по
   смыслу это справочник, то есть статья (в исходной вике он тоже жил статьёй,
   по slug auto-list). Слаг фиксирован и совпадает с CLASSIFIER_SLUG из
   wiki/schema.py, где статья засевается.

   Компонент тянет за собой справочник на 106 КБ, поэтому грузится лениво —
   ровно как раньше, когда он был отдельным разделом. */
export const CLASSIFIER_SLUG = 'klassifikator-avto';
const ClassifierView = lazy(() => import('../classifier/ClassifierView'));

/* Страница статьи.
 *
 * Оглавление строится из готового DOM после вставки контента, а не парсингом
 * строки: заголовкам всё равно нужно проставить id, чтобы по ним можно было
 * прокрутить, и делать это дважды бессмысленно.
 *
 * Прокрутка — всегда через scrollContainer.js. В исходной вике все переходы
 * шли через window.scrollTo, который в нашем каркасе не делает ничего:
 * скроллится .main-content, а не окно.
 *
 * Санитизация на клиенте — второй рубеж. Первый (серверный) появится вместе с
 * редактором на этапе 4; сейчас содержимое создаётся только миграцией.
 */

const STATUS_LABELS = {
    draft: 'Черновик',
    on_approval: 'На согласовании',
    published: 'Опубликована',
    requires_verification: 'Требует проверки',
    archived: 'В архиве',
    expired: 'Устарела',
};

const STATUS_TONES = {
    draft: 'slate',
    on_approval: 'amber',
    published: 'green',
    requires_verification: 'amber',
    archived: 'slate',
    expired: 'red',
};

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

const fmtDate = (iso) => (iso
    ? new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: 'long', year: 'numeric' })
    : '—');

/* Разрешаем ровно те теги и атрибуты, что реально встречаются в контенте вики
   (посчитано по дампу прода: 26 тегов, 11 data-атрибутов). Без явного списка
   DOMPurify вырезал бы data-* и обесцветил 251 выделение, а 35 раскрывающихся
   блоков превратились бы в простые абзацы. */
const SANITIZE_OPTIONS = {
    ADD_TAGS: ['details', 'summary', 'mark', 'colgroup', 'col'],
    ADD_ATTR: [
        'data-color', 'data-title', 'data-default-open', 'data-allow-multiple',
        'data-required-for-ack', 'data-wiki-collapsible', 'data-wiki-collapsible-group',
        'data-id', 'data-icon', 'data-size', 'data-layout', 'open', 'colspan', 'rowspan',
    ],
};

/**
 * Каждая таблица заворачивается в прокручиваемую обёртку.
 *
 * Обёртка нужна снаружи таблицы, а не на ней самой: display:block на table
 * ломает вычисление ширин колонок, а без него прокрутке негде появиться. Тело
 * статьи вставляется через dangerouslySetInnerHTML, то есть места навесить
 * обёртку в разметке нет — делаем это здесь, ПОСЛЕ санитайзера.
 *
 * Порядок принципиален: обёртка добавляется к уже очищенному HTML, поэтому она
 * не может протащить ничего мимо DOMPurify.
 */
const wrapTables = (html) => {
    if (!html || html.indexOf('<table') === -1) return html;
    const parsed = new DOMParser().parseFromString(html, 'text/html');
    parsed.body.querySelectorAll('table').forEach((table) => {
        if (table.parentElement?.classList.contains('wiki-table-scroll')) return;
        const box = parsed.createElement('div');
        box.className = 'wiki-table-scroll';
        table.replaceWith(box);
        box.appendChild(table);
    });
    return parsed.body.innerHTML;
};

/** Пометить вхождения в текстовых узлах. Возвращает первую пометку или null.
 *
 * Текст узла и искомое сворачиваются ОДИНАКОВО (казахские буквы к русским
 * двойникам): сервер находит статью по свёрнутому запросу, и без такой же
 * свёртки здесь получалось бы «статья открылась, слово в ней есть, а подсветки
 * нет». Свёртка посимвольная, один к одному, поэтому позиции вхождений не
 * съезжают и разрезать текст можно по ним же.
 */
const markNeedles = (nodes, rawNeedles, limit = 60) => {
    const needles = rawNeedles.map((needle) => foldKazakh(needle.toLowerCase()));
    let first = null;
    let marked = 0;
    for (const node of nodes) {
        if (marked >= limit) break;      // защита от вырожденного запроса
        const lower = foldKazakh((node.nodeValue || '').toLowerCase());
        let index = -1;
        let length = 0;
        for (const needle of needles) {
            const found = lower.indexOf(needle);
            if (found !== -1 && (index === -1 || found < index)) {
                index = found;
                length = needle.length;
            }
        }
        if (index === -1) continue;

        const range = document.createRange();
        range.setStart(node, index);
        range.setEnd(node, index + length);
        const mark = document.createElement('mark');
        mark.className = 'wiki-search-hit';
        try {
            range.surroundContents(mark);
        } catch {
            continue;    // узел уже разрезан предыдущей пометкой
        }
        marked += 1;
        if (!first) first = mark;
    }
    return first;
};

export default function WikiArticle({ base, headers, slug, onBack, showToast,
                                      highlightTerm = null, classifierPrefill = null,
                                      onEdit = null, onArchived = null,
                                      onOpenArticle = null }) {
    const isClassifier = slug === CLASSIFIER_SLUG;
    const [article, setArticle] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [toc, setToc] = useState([]);
    const [activeId, setActiveId] = useState('');
    const bodyRef = useRef(null);
    const [archiving, setArchiving] = useState(false);
    /* Чтение во весь экран. Нужно для широких статей: у «Все акции» одиннадцать
       колонок, и таблице требуется около 2500px — в колонку раздела она не
       влезает ни при какой вёрстке, поэтому единственный честный ответ это
       отдать ей всю ширину окна. Оглавление при этом остаётся: без него длинная
       статья на весь экран превращается в простыню. */
    const [immersive, setImmersive] = useState(false);

    /* Esc выходит из режима, а прокрутка страницы под ним замирает: иначе на
       широкой статье получаются две полосы прокрутки, и человек тянет не ту. */
    useEffect(() => {
        if (!immersive) return undefined;
        const onKey = (event) => { if (event.key === 'Escape') setImmersive(false); };
        const previous = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        window.addEventListener('keydown', onKey);
        return () => {
            document.body.style.overflow = previous;
            window.removeEventListener('keydown', onKey);
        };
    }, [immersive]);

    /* Ссылка на статью. Кладём в буфер: адресную строку человек не открывает,
       а ссылку надо отправить в переписке. Запасной путь через execCommand
       нужен не для красоты — clipboard.writeText есть только в защищённом
       контексте, и в старом вебвью кнопка иначе молча ничего не делала бы. */
    const copyLink = () => {
        const link = buildArticleLink(article?.slug || slug);
        if (!link) { showToast?.('Не удалось собрать ссылку на статью', 'error'); return; }
        const ok = () => showToast?.('Ссылка на статью скопирована', 'success');
        const fallback = () => {
            const field = document.createElement('textarea');
            field.value = link;
            field.setAttribute('readonly', '');
            field.style.position = 'fixed';
            field.style.opacity = '0';
            document.body.appendChild(field);
            field.select();
            let copied = false;
            try { copied = document.execCommand('copy'); } catch (error) { copied = false; }
            document.body.removeChild(field);
            if (copied) ok();
            else showToast?.('Не удалось скопировать — адрес статьи есть в адресной строке', 'error');
        };
        if (!navigator.clipboard?.writeText) { fallback(); return; }
        navigator.clipboard.writeText(link).then(ok).catch(fallback);
    };

    /* Ссылка на другую статью внутри текста ведёт на тот же портал
       (?view=wiki&article=<slug>). Открываем её здесь же: полная перезагрузка
       приложения ради соседней статьи — это секунды ожидания и повторная
       авторизация. Внешние ссылки и клики с модификатором (открыть в новой
       вкладке) отдаём браузеру нетронутыми. */
    const onBodyClick = (event) => {
        if (!onOpenArticle) return;
        if (event.defaultPrevented || event.button !== 0) return;
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        const anchor = event.target?.closest?.('a[href]');
        if (!anchor || anchor.target === '_blank') return;
        const target = readArticleSlugFromHref(anchor.getAttribute('href'));
        if (!target || target === slug) return;
        event.preventDefault();
        onOpenArticle(target);
    };

    const archive = () => {
        // Подтверждение обязательно: кнопка стоит рядом с «Править», а промах по
        // соседней кнопке не должен уносить статью из витрины.
        if (!window.confirm(`Убрать статью «${article?.title}» в архив?

`
            + 'Она пропадёт из списков и из ответов помощника. '
            + 'Восстановить сможет администратор.')) return;
        setArchiving(true);
        axios.delete(`${base}/articles/${article.id}`, { headers })
            .then(() => {
                showToast?.('Статья убрана в архив', 'success');
                onArchived?.(article);
            })
            .catch((e) => showToast?.(errText(e, 'Не удалось убрать в архив'), 'error'))
            .finally(() => setArchiving(false));
    };

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        setError('');
        axios.get(`${base}/articles/${encodeURIComponent(slug)}`, { headers })
            .then((r) => { if (!cancelled) setArticle(r.data); })
            .catch((e) => { if (!cancelled) setError(errText(e, 'Статья не найдена')); })
            .finally(() => { if (!cancelled) setLoading(false); });
        return () => { cancelled = true; };
    }, [base, headers, slug]);

    /* Адреса файлов раскрываем до абсолютных ПОСЛЕ санитайзера: фронт и API на
       разных доменах, и относительный `/api/wiki/file/<id>` браузер искал бы на
       домене страницы (см. fileUrls.js). Порядок важен — подстановка идёт по
       уже очищенному HTML и мимо DOMPurify ничего не проносит. */
    const safeHtml = useMemo(
        () => (article?.content
            ? absolutizeFileUrls(
                wrapTables(DOMPurify.sanitize(article.content, SANITIZE_OPTIONS)), base)
            : ''),
        [article?.content, base],
    );

    // Оглавление собираем после того, как контент оказался в DOM.
    useEffect(() => {
        if (!safeHtml || !bodyRef.current) { setToc([]); return; }
        const nodes = bodyRef.current.querySelectorAll('h1, h2, h3');
        const entries = [];
        nodes.forEach((node, index) => {
            const text = (node.textContent || '').trim();
            if (!text) return;
            if (!node.id) node.id = `wiki-h-${index}`;
            entries.push({ id: node.id, text, level: Number(node.tagName.slice(1)) });
        });
        setToc(entries);
        setActiveId(entries[0]?.id || '');
    }, [safeHtml]);

    // Подсветка активного пункта оглавления. IntersectionObserver вместо
    // обработчика прокрутки: он не будит React на каждый кадр.
    useEffect(() => {
        if (!toc.length || !bodyRef.current) return undefined;
        const observer = new IntersectionObserver(
            (records) => {
                const visible = records
                    .filter((r) => r.isIntersecting)
                    .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
                if (visible[0]?.target?.id) setActiveId(visible[0].target.id);
            },
            { rootMargin: '-88px 0px -70% 0px', threshold: 0 },
        );
        toc.forEach(({ id }) => {
            const node = document.getElementById(id);
            if (node) observer.observe(node);
        });
        return () => observer.disconnect();
    }, [toc]);

    /* Подсветка слова, по которому статью нашли в поиске. Работает по
       текстовым узлам готового DOM: разметку менять строкой нельзя — контент
       уже прошёл DOMPurify, и любое склеивание HTML открыло бы дыру заново.
       Совпадение ищется по вариантам написания (транслит, раскладка) — тем же,
       которыми искал сервер. */
    useEffect(() => {
        const container = bodyRef.current;
        const term = String(highlightTerm || '').trim();
        if (!container || !safeHtml || term.length < 2) return undefined;

        const needles = queryVariants(term)
            .map((v) => v.toLowerCase())
            .filter((v) => v.length >= 2)
            .slice(0, 8);

        const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
        const textNodes = [];
        while (walker.nextNode()) textNodes.push(walker.currentNode);

        let firstHit = markNeedles(textNodes, needles);

        /* Второй проход — по СЛОВАМ, а не по фразе. Нужен для источников
           помощника: у него цитата длинная, а у табличных кусков она вообще
           служебная сборка «Акция: Лимонопад; Условия: …», которой в тексте
           статьи дословно нет никогда. Поиску первый проход подходит (там
           короткий запрос), помощнику — нет, поэтому проходы именно два, а не
           один универсальный. */
        if (!firstHit && term.length >= 24) {
            const tokens = distinctiveTokens(term);
            if (tokens.length) {
                let best = null;
                let bestScore = 0;
                for (const node of textNodes) {
                    const lower = foldKazakh((node.nodeValue || '').toLowerCase());
                    if (lower.trim().length < 3) continue;
                    let score = 0;
                    for (const token of tokens) if (lower.includes(foldKazakh(token))) score += 1;
                    if (score > bestScore) {
                        bestScore = score;
                        best = node;
                    }
                }
                // Одно случайное слово — не цитата. Двух совпадений достаточно:
                // в служебной сборке таблицы это уже пара «поле + значение».
                if (best && bestScore >= 2) firstHit = markNeedles([best], tokens);
            }
        }

        if (firstHit) {
            // Совпадение внутри свёрнутой раскрывашки без этого не видно.
            let details = firstHit.closest('details');
            while (details) {
                details.open = true;
                details = details.parentElement?.closest('details');
            }
            const timer = setTimeout(() => scrollToElement(firstHit), 80);
            return () => {
                clearTimeout(timer);
                container.querySelectorAll('mark.wiki-search-hit').forEach((m) => {
                    const parent = m.parentNode;
                    if (!parent) return;
                    while (m.firstChild) parent.insertBefore(m.firstChild, m);
                    parent.removeChild(m);
                    parent.normalize();
                });
            };
        }
        return undefined;
    }, [safeHtml, highlightTerm]);

    /* Звезда работает в обе стороны.
     *
     * Раньше кнопка ВСЕГДА слала POST, а вставка в базе идёт с ON CONFLICT DO
     * NOTHING: второе нажатие не делало ничего, но показывало «Добавлено в
     * избранное». Убрать статью из избранного было нельзя ниоткуда — при том,
     * что DELETE на сервере есть и работает.
     *
     * Состояние ведём локально и меняем СРАЗУ, до ответа сервера: звезда должна
     * откликаться на нажатие мгновенно, а сеть тут не при чём. Если запрос не
     * прошёл — возвращаем как было, чтобы картинка не врала. */
    const [favorite, setFavorite] = useState(false);
    const [favoriteBusy, setFavoriteBusy] = useState(false);

    useEffect(() => { setFavorite(!!article?.is_favorite); }, [article?.id, article?.is_favorite]);

    const toggleFavorite = () => {
        if (!article || favoriteBusy) return;
        const next = !favorite;
        setFavorite(next);
        setFavoriteBusy(true);
        axios({
            method: next ? 'post' : 'delete',
            url: `${base}/articles/${article.id}/favorite`,
            headers,
        })
            .then((r) => {
                const applied = r.data?.is_favorite ?? next;
                setFavorite(applied);
                showToast?.(applied ? 'Добавлено в избранное' : 'Убрано из избранного',
                            'success');
            })
            .catch((e) => {
                setFavorite(!next);
                showToast?.(errText(e, 'Не удалось изменить избранное'), 'error');
            })
            .finally(() => setFavoriteBusy(false));
    };

    if (loading) {
        return (
            <div className={`${iosCard} flex items-center justify-center gap-2 py-16 text-slate-400`}>
                <Loader2 size={18} className="animate-spin" />
                <span className="text-[13px]">Открываем статью…</span>
            </div>
        );
    }

    if (error) {
        return (
            <div className={`${iosCard} px-6 py-14 text-center`}>
                <div className="text-[15px] font-semibold text-slate-900">{error}</div>
                <p className="mx-auto mt-1 max-w-sm text-[13px] leading-relaxed text-slate-500">
                    Возможно, статья удалена или у вас нет к ней доступа.
                </p>
                <button type="button" className={`${iosBtnSecondary} mt-4`} onClick={onBack}>
                    <ArrowLeft size={14} /> К списку
                </button>
            </div>
        );
    }

    return (
        <div
            className={immersive
                ? 'wiki-immersive fixed inset-y-0 right-0 z-40 space-y-4 overflow-y-auto bg-slate-100 p-4 sm:p-6'
                : 'space-y-4'}
            /* Слева окно начинается ПОСЛЕ сайдбара, а не от нуля: сайдбар
               рисуется выше по слою и накрывал бы левый край статьи. Тот же
               приём, что у полноэкранных окон доски задач — переменная
               --app-sidebar-offset из :root (src/styles.css), она же
               обнуляется на мобильном, где сайдбар скрыт. */
            style={immersive ? { left: 'var(--app-sidebar-offset, 0px)' } : undefined}
        >
            <div className="flex flex-wrap items-center justify-between gap-2">
                <button type="button" className={iosBtnSecondary} onClick={onBack}>
                    <ArrowLeft size={14} /> К списку
                </button>
                {/* Правка открывается ОТСЮДА, и до сих пор её здесь не было:
                    единственным входом в редактор была кнопка «Новая статья»,
                    то есть существующую статью нельзя было открыть на правку
                    вообще, даже администратору. Право берём из ответа сервера
                    (permissions.can_edit), а не из роли: у статьи есть свои
                    правила доступа, и роль их не описывает. */}
                {/* Перенос обязателен: кнопок в строке четыре, и на телефоне они
                    иначе уезжают за правый край экрана — «Править» не достать. */}
                <div className="flex flex-wrap items-center justify-end gap-2">
                    {/* Удаление МЯГКОЕ: статья уходит в архив, потому что жёсткое
                        снесло бы каскадом версии, просмотры, назначения на
                        ознакомление и избранное. Кнопка так и называется — «В
                        архив», чтобы не обещать того, чего не происходит. */}
                    {onArchived && article.permissions?.can_delete
                        && article.status !== 'archived' && (
                        <button
                            type="button"
                            className={iosBtnSecondary}
                            disabled={archiving}
                            onClick={archive}
                        >
                            {archiving ? <Loader2 size={14} className="animate-spin" />
                                : <Archive size={14} />}
                            В архив
                        </button>
                    )}
                    <button
                        type="button"
                        className={iosBtnSecondary}
                        title="Скопировать ссылку на статью"
                        onClick={copyLink}
                    >
                        <Link2 size={14} /> Ссылка
                    </button>
                    <button
                        type="button"
                        className={iosBtnSecondary}
                        title={immersive ? 'Выйти из полноэкранного режима (Esc)'
                            : 'Читать во весь экран: широкой таблице нужна вся ширина'}
                        onClick={() => setImmersive((value) => !value)}
                    >
                        {immersive ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
                        {immersive ? 'Свернуть' : 'Во весь экран'}
                    </button>
                    {onEdit && article.permissions?.can_edit && (
                        <button type="button" className={iosBtnSecondary}
                                onClick={() => onEdit(article)}>
                            <Pencil size={14} /> Править
                        </button>
                    )}
                </div>
            </div>

            {/* Панель ознакомления идёт ПЕРЕД статьёй: требование надо видеть
                до чтения, а не найти под текстом. */}
            <WikiAckPanel
                base={base}
                headers={headers}
                articleId={article.id}
                bodyRef={bodyRef}
                showToast={showToast}
            />

            {/* overflow-CLIP, а не hidden. Обрезка углов нужна обоим, но hidden
                делает карточку контейнером прокрутки, и position:sticky внутри
                неё перестаёт работать: оглавление уезжало вместе со страницей.
                clip обрезает, не создавая контейнера прокрутки. */}
            <article className={`${iosCard} overflow-clip`}>
                <header className="border-b border-slate-100 px-5 py-4 sm:px-7 sm:py-6">
                    <div className="mb-2 flex flex-wrap items-center gap-1.5">
                        <IosBadge tone={STATUS_TONES[article.status] || 'slate'}>
                            {STATUS_LABELS[article.status] || article.status}
                        </IosBadge>
                        {/* У классификатора visibility_mode='restricted' по
                            устройству — у него собственный периметр, но правило
                            в нём одно: читать могут все роли. Бейдж «только по
                            списку» на статье, открытой всем, вводил бы в
                            заблуждение каждого, кто её откроет. */}
                        {article.visibility_mode === 'restricted' && !isClassifier && (
                            <IosBadge tone="amber">Только по списку</IosBadge>
                        )}
                        {article.strict_mode && <IosBadge tone="red">Строгий режим</IosBadge>}
                        {article.tags?.map((tag) => (
                            <IosBadge key={tag} tone="slate">{tag}</IosBadge>
                        ))}
                    </div>

                    <h1 className="text-[24px] font-semibold leading-tight tracking-[-0.015em] text-slate-900 sm:text-[28px]">
                        {article.title}
                    </h1>
                    {article.summary && (
                        <p className="mt-2 max-w-2xl text-[14px] leading-relaxed text-slate-500">
                            {article.summary}
                        </p>
                    )}

                    <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-slate-400">
                        {article.author_name && (
                            <span className="flex items-center gap-1"><User size={12} /> {article.author_name}</span>
                        )}
                        <span className="flex items-center gap-1">
                            <Clock size={12} /> {fmtDate(article.updated_at)}
                        </span>
                        <span className="flex items-center gap-1 tabular-nums">
                            <Eye size={12} /> {article.views}
                        </span>
                        {/* Одна кнопка на оба действия: и надпись, и заливка
                            звезды говорят, что произойдёт по нажатию. */}
                        <button
                            type="button"
                            onClick={toggleFavorite}
                            disabled={favoriteBusy}
                            aria-pressed={favorite}
                            title={favorite
                                ? 'Убрать статью из избранного'
                                : 'Статья появится в блоке «Избранное» на главной вики'}
                            className={`flex items-center gap-1 rounded-md px-1 transition disabled:opacity-50 ${
                                favorite ? 'text-amber-500 hover:text-slate-500' : 'hover:text-amber-600'
                            }`}
                        >
                            <Star size={12} fill={favorite ? 'currentColor' : 'none'} />
                            {favorite ? 'В избранном' : 'В избранное'}
                        </button>
                    </div>
                </header>

                <div className="flex flex-col gap-6 px-5 py-5 sm:px-7 sm:py-7 lg:flex-row-reverse">
                    {toc.length > 1 && (
                        <nav className="lg:w-56 lg:shrink-0">
                            {/* Длинное оглавление прокручивается само, а не
                                вылезает за экран: закреплённый блок обязан
                                помещаться в окно целиком. */}
                            <div className="lg:sticky lg:top-4 lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto lg:pr-1">
                                <div className={`${iosGroupLabel} mb-1.5 flex items-center gap-1.5`}>
                                    <List size={12} /> Содержание
                                </div>
                                <ul className="space-y-0.5 border-l border-slate-200 pl-3">
                                    {toc.map((item) => (
                                        <li key={item.id}>
                                            <button
                                                type="button"
                                                onClick={() => scrollToElement(document.getElementById(item.id))}
                                                className={`block w-full text-left text-[12.5px] leading-snug transition ${
                                                    activeId === item.id
                                                        ? 'font-medium text-indigo-600'
                                                        : 'text-slate-500 hover:text-slate-800'
                                                }`}
                                                style={{ paddingLeft: `${(item.level - 1) * 10}px` }}
                                            >
                                                {item.text}
                                            </button>
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        </nav>
                    )}

                    {isClassifier ? (
                        <div ref={bodyRef} className="min-w-0 flex-1">
                            <Suspense fallback={(
                                <div className="flex items-center justify-center gap-2 py-14 text-slate-400">
                                    <Loader2 size={18} className="animate-spin" />
                                    <span className="text-[13px]">Загружаем справочник…</span>
                                </div>
                            )}>
                                <ClassifierView prefill={classifierPrefill} embedded />
                            </Suspense>
                        </div>
                    ) : (
                        <div
                            ref={bodyRef}
                            className="wiki-prose min-w-0 flex-1"
                            onClick={onBodyClick}
                            dangerouslySetInnerHTML={{ __html: safeHtml }}
                        />
                    )}
                </div>

                {article.backlinks?.length > 0 && (
                    <footer className="border-t border-slate-100 px-5 py-4 sm:px-7">
                        <div className={`${iosGroupLabel} mb-2 flex items-center gap-1.5`}>
                            <Link2 size={12} /> Сюда ссылаются
                        </div>
                        <div className="flex flex-wrap gap-1.5">
                            {article.backlinks.map((link) => (
                                <IosBadge key={link.id} tone="blue">{link.title}</IosBadge>
                            ))}
                        </div>
                    </footer>
                )}
            </article>

            {article.why && (
                <p className="px-1 text-[11.5px] text-slate-400">
                    Доступ: {article.why}
                </p>
            )}
        </div>
    );
}
