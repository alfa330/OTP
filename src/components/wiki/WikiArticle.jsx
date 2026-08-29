import React, { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import DOMPurify from 'dompurify';
import {
    Archive, ArrowLeft, ArrowUpRight, Clock, CornerDownLeft, Eye, History, Link2,
    List, Loader2, Maximize2, Minimize2, Pencil, Star, User,
} from 'lucide-react';
import { iosCard, iosGroupLabel, iosBtnSecondary, IosBadge } from '../ui/ios';
// fmtDeadline под своим именем: в файле уже есть свой fmtDate — «5 сентября
// 2026» для подписей статьи, а гостевому сроку нужен короткий цифровой формат
// с часом, и разбирать наивную дату через new Date() ему нельзя (см.
// guestAccess.js).
import { daysLeftLabel, fmtDeadline as fmtGuestDeadline } from './guestAccess';
import { STATUS_LABELS, STATUS_TONES, typeBadge } from './articleTypes';
import { findTrainer } from './trainers/registry';
import { getScrollContainer, scrollToElement } from './scrollContainer';
import { backLabel } from './articleTrail';
import { absolutizeFileUrls } from './fileUrls';
import { buildArticleLink, readArticleSlugFromHref } from './articleLink';
import { distinctiveTokens, foldKazakh, queryVariants } from './searchText';
import useCopyGuard from './useCopyGuard';
import useStableCallback from './useStableCallback';
import WikiAckPanel from './WikiAckPanel';
import WikiHistory from './WikiHistory';

/* Классификатор авто — статья вики с ПУСТЫМ телом: вместо текста рисуется
   интерактивный калькулятор. Раньше он был отдельным разделом портала, но по
   смыслу это справочник, то есть статья (в исходной вике он тоже жил статьёй,
   по slug auto-list). Слаг фиксирован и совпадает с CLASSIFIER_SLUG из
   wiki/schema.py, где статья засевается.

   Компонент тянет за собой справочник на 106 КБ, поэтому грузится лениво —
   ровно как раньше, когда он был отдельным разделом. */
export const CLASSIFIER_SLUG = 'klassifikator-avto';
const ClassifierView = lazy(() => import('../classifier/ClassifierView'));

/* Статусы, которые обязаны быть подписаны в списках связей.
 *
 * Опубликованная статья подписи не получает — это норма, и метка у каждой
 * строки превратилась бы в шум. А вот черновик и архив подписать НУЖНО: в проде
 * сегодня ни одна цель внутренней ссылки не опубликована (238 черновиков и 15
 * архивных на 253 пары), и строка без оговорки обещала бы готовый документ там,
 * где его нет. Читателю без права видеть черновики такие статьи не покажутся
 * вовсе — их отсекает периметр на сервере. */
const LINK_STATUS_NOTE = {
    draft: { label: 'Черновик', tone: 'amber' },
    on_approval: { label: 'На согласовании', tone: 'amber' },
    requires_verification: { label: 'Требует проверки', tone: 'amber' },
    archived: { label: 'Архив', tone: 'slate' },
    expired: { label: 'Устарела', tone: 'slate' },
};

/* Список связанных статей.
 *
 * Строка — настоящая <a href>, а не кнопка, и это не формальность. Ссылку в
 * ТЕКСТЕ статьи можно открыть в новой вкладке (Ctrl/Cmd-клик, средняя кнопка,
 * «Открыть в новой вкладке» из контекстного меню) — обработчик тела намеренно
 * пропускает такие клики браузеру. Сделай мы здесь <button>, соседний блок
 * повёл бы себя иначе, чем текст над ним, и молча: ничего не сломано, просто
 * привычное действие перестало работать.
 *
 * Индиго, а не синий: в разделе синим помечены ДЕЙСТВИЯ, а индиго
 * (--wiki-accent) — содержимое. Ссылка на статью — содержимое. */
const ArticleLinkList = ({ icon: Icon, title, hint, rows, onOpen }) => (
    <section>
        <div className={`${iosGroupLabel} mb-1 flex items-center gap-1.5`}>
            <Icon size={12} /> {title}
        </div>
        {/* Оговорка стоит У ЗАГОЛОВКА, а не подвалом: список сужен правами
            читателя, и у двух людей он честно разный. Без пояснения это
            читается как расхождение данных. */}
        <p className="mb-2 px-1 text-[11.5px] leading-relaxed text-slate-500">{hint}</p>
        <div className="flex flex-col gap-1">
            {rows.map((row) => {
                const note = LINK_STATUS_NOTE[row.status];
                return (
                    <a
                        key={row.id}
                        href={buildArticleLink(row.slug)}
                        onClick={(event) => {
                            // Модификаторы и средняя кнопка — браузеру: человек
                            // просит новую вкладку, а не переход внутри портала.
                            if (event.metaKey || event.ctrlKey || event.shiftKey
                                || event.altKey || event.button !== 0) return;
                            if (!onOpen) return;
                            event.preventDefault();
                            onOpen(row.slug);
                        }}
                        className="group flex items-center gap-2 rounded-xl px-2 py-1.5 text-[13px]
                                   text-indigo-700 transition hover:bg-indigo-50/70"
                    >
                        <span className="min-w-0 flex-1 truncate group-hover:underline">
                            {row.title}
                        </span>
                        {row.mutual && <IosBadge tone="slate">Взаимная</IosBadge>}
                        {note && <IosBadge tone={note.tone}>{note.label}</IosBadge>}
                    </a>
                );
            })}
        </div>
    </section>
);

/* Тренажёр — отдельный чанк: экраны двух приложений, барс и своя таблица стилей
   весят прилично, а открывают их только в статьях-тренажёрах. Грузим по нажатию
   на кнопку в тексте, а не при открытии статьи. */
const TrainerModal = lazy(() => import('./trainers/TrainerPlayer'));

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
        /* Кнопка тренажёра. Тот же список, что в серверном санитайзере
           (wiki/sanitize.py): разойдись они — статья сохранилась бы с кнопкой,
           а при чтении та превратилась бы в безымянный div, и тренажёр просто
           не открывался бы.

           data-width и data-align те же два атрибута носит и картинка, которой
           автор задал размер (WikiImageNode.jsx). Список у DOMPurify общий на
           все теги, поэтому добавлять сюда ничего не пришлось — но зависимость
           реальная: убери их отсюда ради тренажёра, и у картинок молча
           слетит размер. */
        'data-wiki-trainer', 'data-label', 'data-width', 'data-align',
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
                                      onOpenArticle = null,
                                      /* Предыдущая статья цепочки и позиция, на
                                         которой в ней оборвали чтение, — обе
                                         приходят из витрины (articleTrail.js).
                                         Пусто — в статью пришли из списка. */
                                      backTo = null, restoreScroll = 0 }) {
    const isClassifier = slug === CLASSIFIER_SLUG;
    const [article, setArticle] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [toc, setToc] = useState([]);
    const [activeId, setActiveId] = useState('');
    const bodyRef = useRef(null);
    /* «Тело статьи уже в DOM» — ОТДЕЛЬНОЕ состояние, а не производное от
       содержимого.
       Так пришлось сделать из-за порядка обновлений: setArticle зовётся в .then,
       а setLoading(false) — в .finally, и это два разных рендера. В первом из
       них содержимое уже посчитано, но компонент ещё возвращает «Открываем
       статью…», то есть див тела не смонтирован и bodyRef пуст. Эффекты,
       завязанные только на содержимое, срабатывали именно в этот момент и
       больше не повторялись: заголовки не получали id (оглавление статьи
       оставалось пустым), подсветка найденного слова не появлялась, а кнопка
       тренажёра не получала роль и попадание в Tab.
       Колбэк-ref будит эффекты тогда, когда узел реально появился, — независимо
       от того, какой именно ранний возврат задержал отрисовку. */
    const [bodyReady, setBodyReady] = useState(false);
    const attachBody = useCallback((node) => {
        bodyRef.current = node;
        setBodyReady(!!node);
    }, []);
    const [archiving, setArchiving] = useState(false);
    const [historyOpen, setHistoryOpen] = useState(false);
    /* Счётчик перезагрузки статьи. Нужен откату: после восстановления редакции
       в базе лежит другой текст, а на экране остался прежний — и человек видит,
       что «ничего не произошло». Отдельное состояние, а не перечитывание по
       slug: slug при откате не меняется, и эффект загрузки сам бы не сработал. */
    const [reloadKey, setReloadKey] = useState(0);
    /* Открытый тренажёр — сценарий, а не флаг: в одной статье кнопок может быть
       несколько (например, «через приложение» и «через сайт»), и открыться
       обязан именно тот, по которому нажали. */
    const [trainer, setTrainer] = useState(null);
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

    /* Уход по сети статей. Наверх идёт не только цель, но и ЗАГОЛОВОК текущей
       статьи: витрина держит цепочку по слагам и подписать кнопку возврата
       «Назад: «Тарифы»» без этого не может — заголовок знает только тот, кто
       статью загрузил. Отсутствие обработчика остаётся ОТСУТСТВИЕМ, а не
       пустой функцией: блок связей на нём отдаёт клик браузеру (строка там —
       настоящая <a href>), и заглушка отняла бы у ссылки эту способность. */
    const openLinked = onOpenArticle
        ? (target) => onOpenArticle(target, { title: article?.title || null })
        : null;

    /* Ссылка на другую статью внутри текста ведёт на тот же портал
       (?view=wiki&article=<slug>). Открываем её здесь же: полная перезагрузка
       приложения ради соседней статьи — это секунды ожидания и повторная
       авторизация. Внешние ссылки и клики с модификатором (открыть в новой
       вкладке) отдаём браузеру нетронутыми. */
    const onBodyClick = (event) => {
        if (event.defaultPrevented || event.button !== 0) return;
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

        /* Кнопка тренажёра. Обработчик делегированный, потому что тело статьи
           вставляется через dangerouslySetInnerHTML — навесить onClick в
           разметке негде, а склеивать HTML со строкой обработчика нельзя: это
           ровно та дыра, от которой стоит санитайзер. */
        const button = event.target?.closest?.('[data-wiki-trainer]');
        if (button) {
            event.preventDefault();
            const key = button.getAttribute('data-wiki-trainer');
            const scenario = findTrainer(key);
            if (scenario) setTrainer(scenario);
            // Тренажёр мог уехать из кода, а кнопка в статье остаться. Молчать
            // нельзя: для читателя это «нажал — ничего не произошло».
            else showToast?.('Этот тренажёр больше не доступен', 'error');
            return;
        }

        if (!openLinked) return;
        const anchor = event.target?.closest?.('a[href]');
        if (!anchor || anchor.target === '_blank') return;
        const target = readArticleSlugFromHref(anchor.getAttribute('href'));
        if (!target || target === slug) return;
        event.preventDefault();
        openLinked(target);
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
    }, [base, headers, slug, reloadKey]);

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

    /* Статья защищена от копирования (тумблер в редакторе). Признак приходит с
       сервера полем copy_protected и действует ОДИНАКОВО для всех, кто читает
       статью, — включая автора и администратора. Исключений тут нет намеренно:
       «у меня копируется, а у людей нет» — это состояние, в котором автор не
       может проверить собственный запрет, а поддержка не может воспроизвести
       жалобу. Кому текст нужен целиком, тот открывает «Править». */
    const copyProtected = !!article?.copy_protected;

    /* Исключений по типу статьи здесь НЕТ — и это решение, а не упущение.
       Соблазн вывести из-под запрета статью-справочник был: тела у неё нет,
       вместо текста рисуется калькулятор с полями ввода. Но тумблер в редакторе
       показывается у всякой статьи и сохраняется у всякой, и статья, у которой
       он включён, а на витрине не действует, — это ровно та ложь на экране,
       ради которой всё остальное здесь и написано: владелец видит «защита
       стоит», читатель копирует. Поля ввода при этом целы — им выделение
       возвращает отдельное правило в wiki-theme.css. */
    const protectText = copyProtected;
    const protectedRef = useRef(null);

    /* Тост — через стабильную обёртку: showToast приходит новой функцией на
       каждый рендер App, и в зависимостях эффекта ниже он переподписывал бы
       слушателей документа на любой чужой рендер (см. useStableCallback.js). */
    const notify = useStableCallback(showToast);

    /* Два блока связей — и ни одной статьи в обоих сразу.
     *
     * Взаимная пара (я ссылаюсь на неё, она на меня) попала бы и в «Связанные
     * материалы», и в «Сюда ссылаются» — две одинаковые строки в десяти
     * сантиметрах друг от друга читаются как ошибка данных. Поэтому статья
     * остаётся в ПЕРВОМ блоке (он ближе к тексту, из которого ссылка и растёт)
     * с пометкой «Взаимная», а из второго убирается.
     */
    const related = useMemo(() => {
        const back = new Set((article?.backlinks || []).map((row) => row.id));
        return (article?.related || []).map(
            (row) => (back.has(row.id) ? { ...row, mutual: true } : row));
    }, [article?.related, article?.backlinks]);

    const backlinks = useMemo(() => {
        const forward = new Set((article?.related || []).map((row) => row.id));
        return (article?.backlinks || []).filter((row) => !forward.has(row.id));
    }, [article?.related, article?.backlinks]);

    /* Кнопка тренажёра приходит из базы обычным div'ом: тега button там быть не
       может — санитайзер его не пропускает (и правильно: в тексте статьи кнопке
       с обработчиком не место). Значит, нажимаемой с клавиатуры её надо сделать
       здесь, после вставки контента: роль, попадание в Tab и подпись для
       скринридера. Правка идёт по готовому DOM, то есть ПОСЛЕ санитайзера, и
       ничего мимо него не проносит. */
    useEffect(() => {
        if (!safeHtml || !bodyRef.current) return;
        bodyRef.current.querySelectorAll('[data-wiki-trainer]').forEach((node) => {
            node.setAttribute('role', 'button');
            node.setAttribute('tabindex', '0');
            const label = node.getAttribute('data-label') || node.textContent?.trim();
            if (label) node.setAttribute('aria-label', `${label}. Откроется тренажёр`);
        });
    }, [safeHtml, bodyReady]);

    /* Пометка внутренних ссылок — ПРИ ЧТЕНИИ, а не в сохранённом тексте.
     *
     * Соблазн хранить класс прямо в теле статьи большой, но класс на <a>
     * переживает оба санитайзера, а значит ПОДДЕЛЫВАЕТСЯ: автор пишет
     * <a class="wiki-link-internal" href="//чужой-сайт/…">Тарифы</a> — и внешняя
     * ссылка получает знак доверия «это статья нашей вики», а открывается наружу.
     * Поэтому решение принимается здесь и только здесь, той же функцией, которой
     * решает переход по клику (readArticleSlugFromHref), а класс, притащенный из
     * тела, снимается принудительно.
     *
     * Работает по готовому DOM, после DOMPurify — мимо санитайзера ничего не
     * проносит. Зависимость от bodyReady обязательна: без неё эффект сработал бы
     * на рендере, где тела ещё нет в DOM, и больше не повторился.
     */
    useEffect(() => {
        if (!safeHtml || !bodyRef.current) return;
        bodyRef.current.querySelectorAll('a[href]').forEach((node) => {
            const target = readArticleSlugFromHref(node.getAttribute('href'));
            node.classList.remove('wiki-link-internal');
            if (target && node.target !== '_blank') {
                node.classList.add('wiki-link-internal');
            }
        });
    }, [safeHtml, bodyReady]);

    /* ЗАЩИТА ОТ КОПИРОВАНИЯ (тумблер в редакторе, wiki_articles.copy_protected).
       Выделение гасит CSS (.wiki-no-copy), буфер обмена — этот хук; почему
       нужны оба рубежа, написано в useCopyGuard.js.

       Тост обязателен: Ctrl+C, который молча ничего не делает, читается как
       поломка портала, а не как запрет. */
    const onCopyBlocked = useCallback(
        () => notify?.('Копирование из этой статьи запрещено', 'info'), [notify]);
    useCopyGuard(protectText, protectedRef, onCopyBlocked);

    /* Пробел и Enter на кнопке тренажёра. У настоящей button это работает само,
       у div с role=button — нет, и без этого кнопка остаётся недоступной тем,
       кто не пользуется мышью. */
    const onBodyKeyDown = (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        const button = event.target?.closest?.('[data-wiki-trainer]');
        if (!button) return;
        event.preventDefault();
        const scenario = findTrainer(button.getAttribute('data-wiki-trainer'));
        if (scenario) setTrainer(scenario);
        else showToast?.('Этот тренажёр больше не доступен', 'error');
    };

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
    }, [safeHtml, bodyReady]);

    /* Прокрутка при смене статьи.
     *
     * Вперёд по сети статей — всегда в начало: открывают соседнюю статью из
     * блока связей, а он стоит в самом подвале, то есть в момент нажатия
     * человек прокручен вниз. Назад — туда, где чтение оборвали: возврат в
     * «Тарифы» на первый экран означал бы искать абзац со ссылкой заново.
     *
     * Ставим ПОСЛЕ того, как тело оказалось в DOM (bodyReady): пока на экране
     * карточка «Открываем статью…», страница ростом с вьюпорт, и любая позиция
     * тут же обрезалась бы в ноль. Подсветка найденного слова прокручивает
     * своим таймером позже и намеренно перебивает эту позицию: пришли из
     * поиска — значит нужно совпадение, а не начало текста.
     */
    useEffect(() => {
        if (!bodyReady) return;
        const container = getScrollContainer(bodyRef.current);
        if (container) container.scrollTo({ top: Math.max(0, restoreScroll || 0), behavior: 'auto' });
    }, [bodyReady, slug, restoreScroll]);

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
    }, [safeHtml, highlightTerm, bodyReady]);

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
                <button type="button" className={`${iosBtnSecondary} mt-4`} onClick={onBack}
                        title={backTo?.title ? `Вернуться к статье «${backTo.title}»` : undefined}>
                    <ArrowLeft size={14} /> {backLabel(backTo)}
                </button>
            </div>
        );
    }

    // Подпись типа документа: null у обычной статьи — бейджа тогда нет вовсе.
    const typeMeta = typeBadge(article?.article_type);

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
                {/* Куда ведёт возврат, зависит от того, как человек сюда попал:
                    из списка — в список, по ссылке из другой статьи — в неё.
                    Подпись обязана это называть, иначе кнопка обещает одно, а
                    делает другое (см. articleTrail.js). */}
                <button type="button" className={iosBtnSecondary} onClick={onBack}
                        title={backTo?.title ? `Вернуться к статье «${backTo.title}»` : undefined}>
                    <ArrowLeft size={14} /> {backLabel(backTo)}
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
                    {/* История — рядом с «Править» и ЛЕВЕЕ её: вопрос «кто это
                        написал» возникает у читателя, а не только у того, кто
                        правит, поэтому кнопка не гейтится правом на правку.
                        Гостю её не показываем: гостевой доступ открывает статью,
                        а не всё, что из неё когда-то убрали (сервер такой запрос
                        тоже отклоняет — wiki/routes_edit.py: _history_denied). */}
                    {!article.guest_access && (
                        <button
                            type="button"
                            className={iosBtnSecondary}
                            title="Кто и когда менял статью, сравнение и откат"
                            onClick={() => setHistoryOpen(true)}
                        >
                            <History size={14} /> История
                        </button>
                    )}
                    {onEdit && article.permissions?.can_edit && (
                        <button type="button" className={iosBtnSecondary}
                                onClick={() => onEdit(article)}>
                            <Pencil size={14} /> Править
                        </button>
                    )}
                </div>
            </div>

            <WikiHistory
                base={base}
                headers={headers}
                article={article}
                open={historyOpen}
                onClose={() => setHistoryOpen(false)}
                onRestored={() => setReloadKey((value) => value + 1)}
                showToast={showToast}
            />

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
            {/* Запрет копирования накрывает КАРТОЧКУ, а не только тело.
                Тело — это лишь текст; заголовок, аннотация и оглавление лежат
                в шапке рядом, и запрет на одном теле обходился бы Ctrl+A:
                название статьи и полный список её заголовков выделились бы и
                скопировались как ни в чём не бывало. */}
            <article
                ref={protectedRef}
                className={`${iosCard} overflow-clip${protectText ? ' wiki-no-copy' : ''}`}
                /* Перетаскивание — тот же вынос наружу, только мышью: кусок
                   текста и картинку роняют в соседнее окно, и запрета на буфер
                   обмена это не касается. */
                onDragStart={protectText ? (event) => event.preventDefault() : undefined}
            >
                <header className="border-b border-slate-100 px-5 py-4 sm:px-7 sm:py-6">
                    <div className="mb-2 flex flex-wrap items-center gap-1.5">
                        <IosBadge tone={STATUS_TONES[article.status] || 'slate'}>
                            {STATUS_LABELS[article.status] || article.status}
                        </IosBadge>
                        {/* Тип документа — сразу за статусом: у должностной
                            инструкции и регламента другой вес, чем у заметки,
                            и знать об этом надо до чтения, а не после. */}
                        {typeMeta && (
                            <IosBadge tone={typeMeta.tone}>{typeMeta.label}</IosBadge>
                        )}
                        {/* У классификатора visibility_mode='restricted' по
                            устройству — у него собственный периметр, но правило
                            в нём одно: читать могут все роли. Бейдж «только по
                            списку» на статье, открытой всем, вводил бы в
                            заблуждение каждого, кто её откроет. */}
                        {article.visibility_mode === 'restricted' && !isClassifier && (
                            <IosBadge tone="amber">Только по списку</IosBadge>
                        )}
                        {article.strict_mode && <IosBadge tone="red">Строгий режим</IosBadge>}
                        {/* Пометка «сведения не действуют» нужна ЧИТАТЕЛЮ не
                            меньше, чем помощнику. Оператор открывает статью с
                            середины, по ссылке из ответа, и заголовка «Архивные
                            акции» над таблицей может не увидеть вовсе —
                            27.08.2026 ровно так и вышло. Бейдж стоит в шапке,
                            которую видно с любого места. */}
                        {article.historical && (
                            <IosBadge tone="red">Сведения не действуют</IosBadge>
                        )}
                        {/* Читателю надо объяснить, почему текст не выделяется.
                            Без бейджа неработающее выделение — это «портал
                            сломался», и человек идёт в поддержку вместо того,
                            чтобы понять запрет. */}
                        {protectText && (
                            <IosBadge tone="slate">Копирование запрещено</IosBadge>
                        )}
                        {/* Статья открыта ТОЛЬКО гостевым доступом — значит у
                            неё есть дата, после которой она пропадёт. Сервер
                            присылает поле лишь в этом случае: тому, кому статья
                            открыта ещё и правилом, подпись «до 5 сентября» была
                            бы неправдой — пятого он увидит её как обычно.
                            Без бейджа человек узнаёт об окончании доступа
                            единственным способом: открыв статью, которая вчера
                            открывалась, а сегодня отвечает «не найдена». */}
                        {article.guest_access && (
                            <IosBadge tone="amber">
                                Гостевой доступ до {fmtGuestDeadline(article.guest_access.expires_at)}
                            </IosBadge>
                        )}
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
                        <div ref={attachBody} className="min-w-0 flex-1">
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
                            ref={attachBody}
                            className="wiki-prose min-w-0 flex-1"
                            onClick={onBodyClick}
                            onKeyDown={onBodyKeyDown}
                            dangerouslySetInnerHTML={{ __html: safeHtml }}
                        />
                    )}
                </div>

                {(related.length > 0 || backlinks.length > 0) && (
                    <footer className="space-y-4 border-t border-slate-100 px-5 py-4 sm:px-7">
                        {related.length > 0 && (
                            <ArticleLinkList
                                icon={ArrowUpRight}
                                title="Связанные материалы"
                                hint="Статьи, на которые ссылается этот текст."
                                rows={related}
                                onOpen={openLinked}
                            />
                        )}
                        {backlinks.length > 0 && (
                            <ArticleLinkList
                                icon={CornerDownLeft}
                                title="Сюда ссылаются"
                                hint="Статьи, в тексте которых есть ссылка на эту."
                                rows={backlinks}
                                onOpen={openLinked}
                            />
                        )}
                    </footer>
                )}
            </article>

            {/* Что увидит нажавший Ctrl+P. На экране блока нет вовсе, на бумаге
                нет самой статьи — и стоять он обязан СНАРУЖИ карточки, иначе
                при печати скрылся бы вместе с ней. Пустой лист человек прочитал
                бы как сбой печати, а не как запрет. */}
            {protectText && (
                <p className="wiki-print-only px-1 text-[13px] text-slate-500">
                    Статья защищена от копирования и не печатается.
                </p>
            )}

            {article.why && (
                <p className="px-1 text-[11.5px] text-slate-400">
                    Доступ: {article.why}
                    {article.guest_access && (
                        <>{' — '}{daysLeftLabel(article.guest_access.days_left,
                                                article.guest_access.expires_at)}</>
                    )}
                </p>
            )}

            {/* Тренажёр открывается ПОВЕРХ статьи и на весь экран: учебный
                телефон с барсом рядом в колонку текста не влезает, а сжимать его
                до ширины абзаца — значит сделать экраны нечитаемыми.
                Ленивый чанк ждать не заставляет: пока грузится, показываем
                строку вместо пустого экрана. */}
            {trainer && (
                <Suspense fallback={(
                    <div className="fixed inset-0 z-[95] flex items-center justify-center gap-2
                                    bg-slate-900/40 text-white backdrop-blur-md">
                        <Loader2 size={18} className="animate-spin" />
                        <span className="text-[13px]">Готовим тренажёр…</span>
                    </div>
                )}>
                    <TrainerModal
                        scenario={trainer}
                        onClose={() => setTrainer(null)}
                        /* Учёт попытки. Статью передаём id'шником: тренажёр
                           один, а статей с ним несколько, и «в какой статье
                           сколько раз» без этого не посчитать. */
                        record={{
                            base, headers, articleId: article?.id, source: 'article',
                        }}
                    />
                </Suspense>
            )}
        </div>
    );
}
