import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
    AlertTriangle, CheckCircle2, Copy, ExternalLink, FileText, Link2, Link2Off,
    Loader2, RefreshCw, Search, Sparkles, X,
} from 'lucide-react';
import {
    iosCard, iosInput, iosBtnPrimary, iosBtnSecondary, iosBtnGhost, iosGroupLabel,
    IosBadge, IosHint, IosMenu, IosModal, IosPager, IosSegmented, IosToggle,
} from '../ui/ios';
import SectionTreeSelect from './SectionTreeSelect';
import { selectableSections } from './sectionPicker';
/* Арифметика страницы берётся у каталога, а не пишется второй раз: «с какой по
   какую строку» ошибается на единицу молча, и у каталожной версии для этого уже
   есть тест (tests/wiki_catalog_all.test.mjs). Две копии разошлись бы на первой
   же правке. */
import { pageWindow } from './WikiCatalog';
import useStableCallback from './useStableCallback';

/* Импорт статей базы знаний Яндекс Про и живая связь с ними.
 *
 * ── Почему одна дверь, а не две ────────────────────────────────────────────
 *
 * «Перенести страницу» и «что у нас уже связано» — это один и тот же вопрос,
 * заданный до и после. Человек, открывший диалог, чаще всего сначала проверяет,
 * не перенесено ли это уже: у Яндекса статьи называются похоже («Тарифы»,
 * «Тариф „Межгород"»), и второй перенос той же страницы сервер отклонит, но
 * узнать об этом лучше здесь, а не по ответу.
 *
 * ── Почему внутри двери всё-таки две половины ──────────────────────────────
 *
 * Половины были и раньше, только стояли друг под другом: форма переноса, а под
 * ней список связей во всю длину. Список растёт (каждая перенесённая страница —
 * строка навсегда), и на втором десятке форма уезжала за верхний край: чтобы
 * перенести следующую страницу, приходилось прокручивать чужой список. При этом
 * ОДНОВРЕМЕННО две половины не нужны никогда — работают либо с одной, либо с
 * другой. Отсюда переключатель вверху и страницы по шесть строк в списке:
 * диалог перестал зависеть от того, сколько статей уже связано.
 *
 * ── Почему пояснения спрятаны под «i» ──────────────────────────────────────
 *
 * Каждое из них нужно ОДИН раз — в первый. Дальше это четыре абзаца серого
 * текста между тумблерами, через которые каждый раз приходится перепрыгивать
 * глазами до нужного переключателя. Под «i» они никуда не делись и открываются
 * наведением; в строке остаётся только то, что человек выбирает.
 *
 * ── Почему сначала «Проверить», а не сразу «Создать» ───────────────────────
 *
 * Разбор страницы — это чужой текст, чужие картинки и чужие таблицы. До
 * создания статьи человек обязан увидеть, ЧТО именно приедет: сколько
 * картинок, что не перенеслось (видео, врезки Яндекса, таблица на тысячи
 * строк) и не лежит ли у нас уже такая статья. Кнопка «Создать» без этого шага
 * означала бы «нажми и посмотри, что получилось».
 *
 * ── Почему предпросмотр показывается ТЕКСТОМ, а не вёрсткой ────────────────
 *
 * Тело статьи уже очищено сервером, но рисовать его здесь нечем: оформительские
 * блоки держатся на wiki-blocks.css в области .wiki-prose, а внутри модалки её
 * нет. Показать «как будет» наполовину хуже, чем показать состав: заголовки,
 * число картинок и таблиц. Как будет — видно в статье, до публикации она
 * черновик.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

/* Состояние сверки. Тона — ТОЛЬКО из BADGE_TONES (src/components/ui/ios.jsx:
   slate/green/red/blue/amber): незнакомое имя бейдж молча подменяет на slate, и
   «источник не прочитался» становится серой строчкой среди серых. */
const STATUS_VIEW = {
    ok: { tone: 'slate', label: 'совпадает с источником' },
    changed: { tone: 'green', label: 'обновлена из источника' },
    conflict: { tone: 'amber', label: 'источник изменился, статью правили' },
    error: { tone: 'red', label: 'источник не прочитался' },
};

const plural = (n, one, few, many) => {
    const mod100 = Math.abs(n) % 100;
    const mod10 = mod100 % 10;
    if (mod100 >= 11 && mod100 <= 14) return many;
    if (mod10 === 1) return one;
    if (mod10 >= 2 && mod10 <= 4) return few;
    return many;
};

/* Вердикты, при которых находка — действительно та же статья. «рядом» сюда не
   входит: это находка от 0,82 по вектору (wiki/ai/similar.py), и на живой
   проверке она дала «Регламент обработки заявок в amoCRM» для статьи «Не
   приходят заказы». Красить и уговаривать по такому сигналу нельзя. */
const STRONG_VERDICTS = ['похоже', 'дубль'];

const HINT = 'Адрес статьи вида https://pro.yandex.com/kz-ru/almaty/knowledge-base/'
    + 'taxi/tariffs/intercity. Забираем текст целиком, включая свёрнутые блоки, '
    + 'которых в самой странице не видно, и переносим картинки к нам в WebP.';

const SECTION_HINT = 'Статья приедет ЧЕРНОВИКОМ и встанет в очередь «Перенос» — '
    + 'опубликует её человек. Раздел можно не выбирать: тогда статья ляжет вне '
    + 'дерева и найдётся в очереди.';

const AI_HINT = 'Помощник расставит вводку, плашки, шаги и карточки, не меняя ни '
    + 'слова текста и не трогая картинки. Тумблер останется у статьи: сверка '
    + 'будет оформлять её так же.';

const SYNC_HINT = 'Раз в сутки сверяем страницу. Изменилась — обновляем статью. '
    + 'Если статью правили руками, текст НЕ перезаписывается: она помечается '
    + '«источник изменился».';

const DUPLICATE_HINT = 'Свяжите с источником, если это ТА ЖЕ статья: текст при '
    + 'этом не перепишется, начнётся только сверка. Если находка про другое — '
    + 'просто создайте новую, кнопка внизу.';

/* Что показать про принесённое. Заголовки считаем по разметке, а не спрашиваем
   у сервера: он отдаёт готовое тело, и второй счётчик того же разошёлся бы. */
const compose = (html) => {
    const text = String(html || '');
    return {
        headings: (text.match(/<h2/g) || []).length,
        images: (text.match(/<img/g) || []).length,
        tables: (text.match(/<table/g) || []).length,
        chars: text.replace(/<[^>]+>/g, '').length,
    };
};

/* Страница списка связей. Шесть строк, а не десять как в каталоге: список живёт
   внутри модалки, у которой своя высота, и переключатель номеров обязан
   оставаться на виду вместе с последней строкой. */
const ROWS_PER_PAGE = 6;

/* Отборы списка. Ровно те три вопроса, с которыми в список приходят: «что
   сломалось», «что разошлось с источником» и «что перестали сверять». Четвёртого
   («что в порядке») нет намеренно: это и есть ответ по умолчанию. */
const FILTERS = {
    all: { label: 'Все', match: () => true },
    conflict: { label: 'Разошлись', match: (i) => i.last_status === 'conflict' },
    error: { label: 'Ошибки', match: (i) => i.last_status === 'error' },
    off: { label: 'Без сверки', match: (i) => !i.auto_sync },
};

const LinkedRow = ({ item, busy, onSync, onForce, onToggleSync, onOpen }) => {
    const view = STATUS_VIEW[item.last_status] || STATUS_VIEW.ok;
    /* Плашку рисуем, ТОЛЬКО когда есть что сказать. «Совпадает с источником» —
       это состояние почти каждой строки, и повторённое шесть раз подряд оно
       перестаёт читаться вовсе, зато топит в себе единственную жёлтую. То, что
       статью сверяли и всё в порядке, уже сказано датой ниже. */
    const flagged = item.last_status && item.last_status !== 'ok';
    return (
        <li className="flex items-start gap-2 px-3 py-2.5">
            <span className="min-w-0 flex-1">
                <span className="flex min-w-0 items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-slate-900">
                        {item.title}
                    </span>
                    {flagged && (
                        <IosBadge tone={view.tone} className="shrink-0">{view.label}</IosBadge>
                    )}
                </span>
                <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5
                                 text-[10.5px] text-slate-400">
                    {item.last_checked_at && <span>сверено {item.last_checked_at}</span>}
                    {!item.auto_sync && <span>автосверка выключена</span>}
                    {item.ai_format && <span>оформляет помощник</span>}
                    <a href={item.url} target="_blank" rel="noopener noreferrer"
                       className="inline-flex items-center gap-1 text-indigo-600">
                        <ExternalLink size={10} /> источник
                    </a>
                </span>
                {item.last_error && (
                    <span className="mt-1 block text-[11.5px] leading-relaxed text-amber-600">
                        {item.last_error}
                    </span>
                )}
            </span>
            {/* Действия за «тремя точками» — общий приём раздела (каталог,
                новости, структура). Ряд из трёх подписанных кнопок в каждой
                строке читался как украшение и занимал больше места, чем сама
                строка, а на телефоне переносился на второй ряд. */}
            {busy ? (
                <span className="grid h-8 w-8 shrink-0 place-items-center">
                    <Loader2 size={14} className="animate-spin text-slate-400" />
                </span>
            ) : (
                <IosMenu
                    label={`Действия со статьёй «${item.title}»`}
                    items={[
                        onOpen && {
                            key: 'open', label: 'Открыть статью', icon: FileText,
                            onSelect: onOpen,
                        },
                        {
                            key: 'sync', label: 'Сверить с источником', icon: RefreshCw,
                            onSelect: onSync,
                        },
                        {
                            key: 'toggle',
                            label: item.auto_sync ? 'Не сверять' : 'Снова сверять',
                            icon: item.auto_sync ? Link2Off : Link2,
                            /* ТУМБЛЕР, а не отвязка. Раньше кнопка удаляла связь
                               совсем: статья пропадала из этого списка, и вернуть
                               её было неоткуда — а повторный импорт той же
                               страницы заводил вторую копию. Теперь выключается
                               только сверка, строка остаётся на месте, и включить
                               обратно можно тем же нажатием. Забыть источник
                               совсем умеет API (DELETE), но в интерфейсе такого
                               действия нет: терять статью одним нажатием нельзя. */
                            onSelect: onToggleSync,
                        },
                        /* «Переписать» показываем ТОЛЬКО при конфликте: это
                           единственное действие в разделе, которое затирает
                           работу человека, и предлагать его без причины нельзя. */
                        item.last_status === 'conflict' && {
                            key: 'force', label: 'Переписать из источника',
                            icon: AlertTriangle, danger: true, separatorBefore: true,
                            onSelect: onForce,
                        },
                    ]}
                />
            )}
        </li>
    );
};

export default function WikiYandexImport({
    open, base, headers, structure, showToast, onClose, onDone, onOpenArticle,
}) {
    const [tab, setTab] = useState('import');
    const [url, setUrl] = useState('');
    const [preview, setPreview] = useState(null);
    const [sectionId, setSectionId] = useState(null);
    const [aiFormat, setAiFormat] = useState(false);
    const [autoSync, setAutoSync] = useState(true);
    const [busy, setBusy] = useState(null);
    const [linked, setLinked] = useState([]);
    const [rowBusy, setRowBusy] = useState(null);
    const [query, setQuery] = useState('');
    const [filter, setFilter] = useState('all');
    const [page, setPage] = useState(1);

    const toast = useStableCallback(showToast || (() => {}));
    const done = useStableCallback(onDone || (() => {}));

    const sections = useMemo(
        () => selectableSections(structure?.sections || []), [structure]);
    const spaces = useMemo(() => structure?.spaces || [], [structure]);

    const loadLinked = useStableCallback(() => {
        axios.get(`${base}/yandex`, { headers })
            .then((r) => setLinked(r.data?.items || []))
            .catch(() => setLinked([]));
    });

    useEffect(() => {
        if (!open) return;
        setTab('import');
        setUrl('');
        setPreview(null);
        setBusy(null);
        setQuery('');
        setFilter('all');
        setPage(1);
        loadLinked();
    }, [open, loadLinked]);

    const check = () => {
        if (!url.trim()) return;
        setBusy('preview');
        setPreview(null);
        axios.post(`${base}/yandex/preview`, { url: url.trim(), ai_format: aiFormat },
                   { headers })
            .then((r) => setPreview(r.data))
            .catch((e) => toast(errText(e, 'Страница не разобралась'), 'error'))
            .finally(() => setBusy(null));
    };

    const create = () => {
        if (!url.trim()) return;
        setBusy('import');
        axios.post(`${base}/yandex/import`, {
            url: url.trim(),
            section_ids: sectionId ? [sectionId] : [],
            ai_format: aiFormat,
            auto_sync: autoSync,
        }, { headers })
            .then((r) => {
                const body = r.data || {};
                if (body.created) {
                    toast(`Статья создана черновиком${body.images
                        ? `, картинок: ${body.images}` : ''}`, 'success');
                } else {
                    toast('Эта страница уже перенесена — статья обновляется сверкой',
                          'info');
                }
                (body.warnings || []).slice(0, 3).forEach((w) => toast(w, 'info'));
                loadLinked();
                setPreview(null);
                setUrl('');
                done(body);
            })
            .catch((e) => toast(errText(e, 'Не удалось создать статью'), 'error'))
            .finally(() => setBusy(null));
    };

    /* Связать уже написанную статью с источником. Текст при этом НЕ
       переписывается: связка только начинает сверку, и первое расхождение
       придёт конфликтом — иначе кнопка «Связать» уничтожала бы статью, которую
       кто-то писал руками. */
    const linkExisting = (item) => {
        if (!url.trim()) return;
        setBusy(`link-${item.article_id}`);
        axios.post(`${base}/yandex/${item.article_id}/link`, {
            url: url.trim(), auto_sync: autoSync, ai_format: aiFormat,
        }, { headers })
            .then(() => {
                toast(`«${item.title}» связана с источником — текст не изменён`,
                      'success');
                loadLinked();
                setPreview(null);
                setUrl('');
            })
            .catch((e) => toast(errText(e, 'Не удалось связать статью'), 'error'))
            .finally(() => setBusy(null));
    };

    const syncRow = (item, force) => {
        setRowBusy(item.article_id);
        axios.post(`${base}/yandex/${item.article_id}/sync`, { force: !!force },
                   { headers })
            .then((r) => {
                const status = r.data?.status;
                const view = STATUS_VIEW[status] || STATUS_VIEW.ok;
                toast(`«${item.title}»: ${view.label}`,
                      status === 'error' ? 'error' : 'success');
                (r.data?.warnings || []).slice(0, 2).forEach((w) => toast(w, 'info'));
                loadLinked();
                if (status === 'changed') done(r.data);
            })
            .catch((e) => toast(errText(e, 'Сверка не удалась'), 'error'))
            .finally(() => setRowBusy(null));
    };

    const toggleSyncRow = (item) => {
        const on = !item.auto_sync;
        setRowBusy(item.article_id);
        axios.patch(`${base}/yandex/${item.article_id}`, { auto_sync: on }, { headers })
            .then(() => {
                toast(on
                    ? `«${item.title}» снова сверяется с источником`
                    : `«${item.title}» больше не сверяется — связь сохранена`, 'success');
                loadLinked();
            })
            .catch((e) => toast(errText(e, 'Не удалось переключить сверку'), 'error'))
            .finally(() => setRowBusy(null));
    };

    const stats = preview ? compose(preview.content) : null;
    /* Сильные первыми: «рядом» — это находка от 0,82 по вектору, то есть чаще
       всего просто соседняя тема. Ставить её первой значит показывать пальцем
       не туда. */
    const duplicates = [...(preview?.duplicates?.items || [])].sort(
        (a, b) => STRONG_VERDICTS.indexOf(b.verdict) - STRONG_VERDICTS.indexOf(a.verdict));
    const strongDuplicate = duplicates.some((d) => STRONG_VERDICTS.includes(d.verdict));

    /* Отбор без строк не показываем: кнопка «Ошибки», которая всегда открывает
       пустоту, — это вопрос «а что, бывают ошибки?» на ровном месте. */
    const filterOptions = useMemo(() => ([
        { value: 'all', label: FILTERS.all.label, count: linked.length },
        ...['conflict', 'error', 'off']
            .map((key) => ({
                value: key,
                label: FILTERS[key].label,
                count: linked.filter(FILTERS[key].match).length,
            }))
            .filter((option) => option.count > 0),
    ]), [linked]);

    /* Выбранный отбор берётся из ЖИВОГО перечня, а не из состояния напрямую:
       починили последний конфликт — кнопка «Разошлись» исчезла, и человек
       остался бы смотреть на пустой список с работающим пейджером. */
    const activeFilter = filterOptions.some((o) => o.value === filter) ? filter : 'all';

    const matched = useMemo(() => {
        const needle = query.trim().toLowerCase();
        return linked
            .filter(FILTERS[activeFilter].match)
            .filter((item) => !needle || `${item.title} ${item.source_title || ''} ${item.url}`
                .toLowerCase().includes(needle));
    }, [linked, activeFilter, query]);

    const pageView = pageWindow(matched, page, ROWS_PER_PAGE);

    const tabs = [
        { value: 'import', label: 'Перенести' },
        { value: 'linked', label: 'Связанные', count: linked.length },
    ];

    return (
        <IosModal
            open={open}
            onClose={onClose}
            title="Импорт из Яндекс Про"
            subtitle="Статья базы знаний pro.yandex.com"
            maxWidth="max-w-2xl"
            footer={(
                <div className="flex flex-wrap items-center justify-end gap-2">
                    <button type="button" className={iosBtnSecondary} onClick={onClose}>
                        Закрыть
                    </button>
                    {/* Создавать нечего, если страница уже связана или уже
                        переносилась: кнопка в этом состоянии либо вернёт ту же
                        статью, либо (до появления проверки провенанса) заводила
                        вторую копию. Гасим её и оставляем «Связать снова».
                        На половине «Связанные» её нет вовсе: там переносить
                        нечего, и мёртвая главная кнопка только сбивает. */}
                    {tab === 'import' && (
                        <button
                            type="button"
                            className={iosBtnPrimary}
                            disabled={!preview || busy !== null
                                || !!preview.linked_article_id || !!preview.imported}
                            onClick={create}
                        >
                            {busy === 'import'
                                ? <Loader2 size={15} className="animate-spin" />
                                : <CheckCircle2 size={15} />}
                            Создать статью
                        </button>
                    )}
                </div>
            )}
        >
            <div className="space-y-4">
                {/* Переключатель половин показываем, только когда вторая не
                    пуста: пока не связано ничего, «Связанные (0)» — это вкладка
                    с надписью «здесь ничего нет». */}
                {linked.length > 0 && (
                    <IosSegmented
                        value={tab}
                        options={tabs}
                        onChange={setTab}
                        stretch
                        ariaLabel="Половины диалога"
                    />
                )}

                {tab === 'import' ? (
                    <div className="space-y-4">
                        <div>
                            <div className="flex items-center gap-1.5 pb-1">
                                <span className={iosGroupLabel}>Ссылка на статью</span>
                                <IosHint text={HINT} label="Какие ссылки понимает" />
                            </div>
                            <div className="flex gap-2">
                                <input
                                    className={`${iosInput} flex-1`}
                                    value={url}
                                    onChange={(e) => { setUrl(e.target.value); setPreview(null); }}
                                    onKeyDown={(e) => { if (e.key === 'Enter') check(); }}
                                    placeholder="https://pro.yandex.com/kz-ru/almaty/knowledge-base/…"
                                    spellCheck={false}
                                />
                                <button
                                    type="button"
                                    className={`${iosBtnSecondary} shrink-0`}
                                    disabled={!url.trim() || busy !== null}
                                    onClick={check}
                                >
                                    {busy === 'preview'
                                        ? <Loader2 size={14} className="animate-spin" />
                                        : <Search size={14} />}
                                    Проверить
                                </button>
                            </div>
                        </div>

                        {/* Что приедет. Показывается только после разбора: до него
                            говорить не о чем, а пустая рамка «здесь будет предпросмотр»
                            — это шум. */}
                        {preview && (
                            <div className={`${iosCard} space-y-2 p-3`}>
                                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                                    <span className="text-[14px] font-semibold text-slate-900">
                                        {preview.source?.title}
                                    </span>
                                    {preview.source?.last_update && (
                                        <span className="text-[11px] text-slate-400">
                                            в источнике изменена {preview.source.last_update}
                                        </span>
                                    )}
                                </div>
                                {preview.summary && (
                                    <p className="text-[12px] leading-relaxed text-slate-500">
                                        {preview.summary}
                                    </p>
                                )}
                                <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-500">
                                    <span>{stats.headings} {plural(stats.headings,
                                        'раздел', 'раздела', 'разделов')}</span>
                                    <span>{stats.images} {plural(stats.images,
                                        'картинка', 'картинки', 'картинок')}</span>
                                    {stats.tables > 0 && (
                                        <span>{stats.tables} {plural(stats.tables,
                                            'таблица', 'таблицы', 'таблиц')}</span>
                                    )}
                                    <span className="tabular-nums">
                                        {stats.chars.toLocaleString('ru-RU')} знаков
                                    </span>
                                </div>

                                {/* Чего НЕ перенеслось — не мелким шрифтом в углу.
                                    Видео и врезки Яндекса в вике не живут, и человек
                                    должен узнать об этом от импортёра, а не от
                                    читателя статьи. */}
                                {(preview.warnings || []).length > 0 && (
                                    <ul className="space-y-0.5">
                                        {preview.warnings.map((w, i) => (
                                            <li key={i} className="flex items-start gap-1.5
                                                                   text-[11.5px] leading-relaxed text-amber-600">
                                                <AlertTriangle size={12} className="mt-0.5 shrink-0" />
                                                {w}
                                            </li>
                                        ))}
                                    </ul>
                                )}

                                {/* Похожие статьи — СПРАВКА, а не требование.
                                    Первая же живая проверка показала, чем опасен
                                    уговаривающий тон: на статью «Не приходят заказы»
                                    поиск дал «Регламент обработки заявок в amoCRM» с
                                    самым слабым вердиктом «рядом», а подпись звала
                                    связать её «вместо переноса». Человек читает это как
                                    «создавать нельзя» — хотя создать новую статью тут и
                                    есть правильное действие.

                                    Поэтому: красным — только настоящий дубль, всё
                                    остальное серым; связать можно, но главная кнопка
                                    внизу остаётся «Создать статью». Что делать с
                                    находкой — под «i»: это объяснение, а не сигнал. */}
                                {duplicates.length > 0 && (
                                    <div className={`rounded-xl px-3 py-2 ${
                                        strongDuplicate ? 'bg-rose-50' : 'bg-slate-50'}`}>
                                        <span className="flex items-center gap-1.5">
                                            <span className={`text-[11.5px] font-medium ${
                                                strongDuplicate ? 'text-rose-700' : 'text-slate-600'}`}>
                                                {strongDuplicate
                                                    ? 'Такая статья у нас уже есть'
                                                    : 'Похожее у нас есть — но, возможно, это про другое'}
                                            </span>
                                            <IosHint text={DUPLICATE_HINT}
                                                     label="Связать или создать новую" />
                                        </span>
                                        <ul className="mt-1 space-y-1">
                                            {duplicates.slice(0, 3).map((d) => (
                                                <li key={d.article_id}
                                                    className={`flex flex-wrap items-center gap-1.5 text-[11.5px] ${
                                                        STRONG_VERDICTS.includes(d.verdict)
                                                            ? 'text-rose-700' : 'text-slate-600'}`}>
                                                    <Copy size={11} className="shrink-0" />
                                                    <button
                                                        type="button"
                                                        className="truncate text-left underline decoration-slate-300"
                                                        onClick={() => onOpenArticle?.(d.slug)}
                                                    >
                                                        {d.title}
                                                    </button>
                                                    <span className="shrink-0 text-slate-400">
                                                        {d.verdict}
                                                    </span>
                                                    <button
                                                        type="button"
                                                        className={`${iosBtnGhost} ml-auto shrink-0 text-[11px]`}
                                                        disabled={busy !== null}
                                                        onClick={() => linkExisting(d)}
                                                    >
                                                        {busy === `link-${d.article_id}`
                                                            ? <Loader2 size={12} className="animate-spin" />
                                                            : <Link2 size={12} />}
                                                        Связать
                                                    </button>
                                                </li>
                                            ))}
                                        </ul>
                                    </div>
                                )}

                                {preview.linked_article_id && (
                                    <p className="text-[11.5px] leading-relaxed text-slate-500">
                                        Эта страница уже перенесена — статья обновляется сверкой,
                                        вторая не создастся.
                                    </p>
                                )}

                                {/* Страницу перенесли, а потом отписали. Раньше выйти из
                                    этого состояния было НЕОТКУДА: единственная кнопка
                                    звалась «Создать статью» и завела бы вторую копию.
                                    Поэтому предложение связать стоит здесь — рядом с
                                    названием, до всех замечаний. */}
                                {!preview.linked_article_id && preview.imported && (
                                    <div className="flex flex-wrap items-center gap-2 rounded-xl
                                                    bg-amber-50 px-3 py-2">
                                        <span className="min-w-0 flex-1 text-[11.5px]
                                                         leading-relaxed text-amber-800">
                                            Эта страница уже переносилась в статью
                                            «{preview.imported.title}», но сейчас с источником
                                            не связана — обновления не приходят.
                                        </span>
                                        <button
                                            type="button"
                                            className={`${iosBtnSecondary} shrink-0 text-[12px]`}
                                            disabled={busy !== null}
                                            onClick={() => linkExisting(preview.imported)}
                                        >
                                            {busy === `link-${preview.imported.article_id}`
                                                ? <Loader2 size={13} className="animate-spin" />
                                                : <Link2 size={13} />}
                                            Связать снова
                                        </button>
                                    </div>
                                )}
                            </div>
                        )}

                        <div>
                            <div className="flex items-center gap-1.5 pb-1">
                                <span className={iosGroupLabel}>Раздел</span>
                                <IosHint text={SECTION_HINT} label="Куда попадёт статья" />
                            </div>
                            <SectionTreeSelect
                                sections={sections}
                                spaces={spaces}
                                value={sectionId}
                                onChange={setSectionId}
                            />
                        </div>

                        {/* Два тумблера одной группой, как в настройках iOS: строка
                            = название + «i» + переключатель. Раньше под каждым
                            стоял абзац на три строки, и оба тумблера вместе
                            занимали пол-экрана — при том что читают их один раз. */}
                        <div className={`${iosCard} divide-y divide-slate-100`}>
                            {/* Строки НЕ <label>, хотя тумблер в них один. Внутри
                                строки теперь стоит «i» — тоже <button>, и он
                                оказывается первым labelable-потомком: браузер
                                считал бы подсказку тем самым полем, которое
                                подписывает ярлык, и нажатие по названию открывало
                                бы пояснение вместо переключения. */}
                            <div className="flex items-center gap-3 px-3 py-2.5">
                                <span className="flex min-w-0 flex-1 items-center gap-1.5">
                                    <Sparkles size={13} className="shrink-0 text-slate-400" />
                                    <span className="truncate text-[13px] font-medium text-slate-900">
                                        Оформить блоками помощником
                                    </span>
                                    <IosHint text={AI_HINT} label="Что сделает помощник" />
                                </span>
                                <IosToggle checked={aiFormat} onChange={setAiFormat} />
                            </div>
                            <div className="flex items-center gap-3 px-3 py-2.5">
                                <span className="flex min-w-0 flex-1 items-center gap-1.5">
                                    <RefreshCw size={13} className="shrink-0 text-slate-400" />
                                    <span className="truncate text-[13px] font-medium text-slate-900">
                                        Обновлять из источника
                                    </span>
                                    <IosHint text={SYNC_HINT} label="Как работает сверка" />
                                </span>
                                <IosToggle checked={autoSync} onChange={setAutoSync} />
                            </div>
                        </div>
                    </div>
                ) : (
                    <div className="space-y-2">
                        {/* Поле поиска — только когда список длиннее страницы: над
                            шестью строками оно ничего не решает, а место занимает.
                            Тот же порог и та же причина, что в каталоге. */}
                        {linked.length > ROWS_PER_PAGE && (
                            <div className="flex items-center gap-2 rounded-lg bg-slate-100 px-2.5 py-1.5
                                            transition focus-within:bg-white focus-within:ring-2
                                            focus-within:ring-blue-500/70">
                                <Search size={13} className="shrink-0 text-slate-400" />
                                <input
                                    value={query}
                                    onChange={(e) => { setQuery(e.target.value); setPage(1); }}
                                    placeholder="Найти по названию статьи или адресу"
                                    className="w-full min-w-0 bg-transparent text-[12px] text-slate-900
                                               placeholder-slate-400 focus:outline-none"
                                    spellCheck={false}
                                />
                                {query && (
                                    <button
                                        type="button"
                                        onClick={() => { setQuery(''); setPage(1); }}
                                        aria-label="Очистить поиск"
                                        className="grid h-4 w-4 shrink-0 place-items-center rounded-full
                                                   bg-slate-200 text-slate-500 transition hover:bg-slate-300"
                                    >
                                        <X size={10} />
                                    </button>
                                )}
                            </div>
                        )}

                        {filterOptions.length > 1 && (
                            <IosSegmented
                                value={activeFilter}
                                options={filterOptions}
                                onChange={(value) => { setFilter(value); setPage(1); }}
                                ariaLabel="Что показать в списке"
                            />
                        )}

                        {/* Пейджер НАД списком: под ним до него пришлось бы
                            прокручивать всю страницу — ровно та работа, ради
                            избавления от которой страницы и заводят. Тот же
                            приём и по той же причине — в каталоге вики. */}
                        {matched.length > ROWS_PER_PAGE && (
                            <IosPager
                                page={pageView.safePage}
                                pageCount={pageView.pageCount}
                                total={matched.length}
                                from={pageView.from + 1}
                                to={pageView.from + pageView.rows.length}
                                onPage={setPage}
                                unit="статьи"
                            />
                        )}

                        {pageView.rows.length > 0 ? (
                            /* Белая карточка с разделителями, а не строки-пилюли:
                               подложка модалки сама slate-50, и на ней «серая
                               плашка на сером» переставала быть строкой списка. */
                            <ul className={`${iosCard} divide-y divide-slate-100`}>
                                {pageView.rows.map((item) => (
                                    <LinkedRow
                                        key={item.article_id}
                                        item={item}
                                        busy={rowBusy === item.article_id}
                                        onSync={() => syncRow(item, false)}
                                        onForce={() => syncRow(item, true)}
                                        onToggleSync={() => toggleSyncRow(item)}
                                        onOpen={onOpenArticle && item.slug
                                            ? () => onOpenArticle(item.slug) : null}
                                    />
                                ))}
                            </ul>
                        ) : (
                            <p className="rounded-xl bg-slate-50 px-3 py-6 text-center
                                          text-[12px] text-slate-400">
                                Ничего не нашлось
                            </p>
                        )}
                    </div>
                )}
            </div>
        </IosModal>
    );
}
