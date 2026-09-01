import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
    AlertTriangle, CheckCircle2, Copy, ExternalLink, Link2, Link2Off, Loader2, RefreshCw,
    Search, Sparkles,
} from 'lucide-react';
import {
    iosCard, iosInput, iosBtnPrimary, iosBtnSecondary, iosBtnGhost, iosGroupLabel,
    IosBadge, IosHint, IosModal, IosToggle,
} from '../ui/ios';
import SectionTreeSelect from './SectionTreeSelect';
import { selectableSections } from './sectionPicker';
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

const HINT = 'Адрес статьи вида https://pro.yandex.com/kz-ru/almaty/knowledge-base/'
    + 'taxi/tariffs/intercity. Забираем текст целиком, включая свёрнутые блоки, '
    + 'которых в самой странице не видно, и переносим картинки к нам в WebP.';

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

const LinkedRow = ({ item, busy, onSync, onForce, onUnlink }) => {
    const view = STATUS_VIEW[item.last_status] || STATUS_VIEW.ok;
    return (
        <li className="flex flex-wrap items-start gap-2 rounded-xl bg-slate-50 px-3 py-2">
            <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] font-medium text-slate-900">
                    {item.title}
                </span>
                <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5
                                 text-[10.5px] text-slate-400">
                    <IosBadge tone={view.tone}>{view.label}</IosBadge>
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
            <span className="flex shrink-0 items-center gap-1.5">
                {busy ? (
                    <span className="grid h-8 w-16 place-items-center">
                        <Loader2 size={14} className="animate-spin text-slate-400" />
                    </span>
                ) : (
                    <>
                        <button type="button" className={`${iosBtnGhost} text-[11.5px]`}
                                onClick={onSync} title="Сверить со страницей источника">
                            <RefreshCw size={13} /> Сверить
                        </button>
                        {/* «Переписать» показываем ТОЛЬКО при конфликте: это
                            единственное действие в разделе, которое затирает
                            работу человека, и предлагать его без причины
                            нельзя. */}
                        {item.last_status === 'conflict' && (
                            <button type="button" className={`${iosBtnGhost} text-[11.5px] text-rose-600`}
                                    onClick={onForce}
                                    title="Взять текст источника поверх ручных правок">
                                Переписать
                            </button>
                        )}
                        {/* С подписью, а не одной иконкой. В первый же день
                            работы связь со статьёй «Межгород» сняли случайно:
                            иконка стояла рядом со «Сверить», и понять, что
                            вторая кнопка выключает слежение за источником,
                            было неоткуда. Действие обратимое, но узнаётся об
                            этом только после того, как источник изменится и
                            никто об этом не узнает. */}
                        <button type="button" className={`${iosBtnGhost} text-[11.5px]`}
                                onClick={onUnlink}
                                title="Больше не сверять эту статью с источником">
                            <Link2Off size={13} /> Отписать
                        </button>
                    </>
                )}
            </span>
        </li>
    );
};

export default function WikiYandexImport({
    open, base, headers, structure, showToast, onClose, onDone, onOpenArticle,
}) {
    const [url, setUrl] = useState('');
    const [preview, setPreview] = useState(null);
    const [sectionId, setSectionId] = useState(null);
    const [aiFormat, setAiFormat] = useState(false);
    const [autoSync, setAutoSync] = useState(true);
    const [busy, setBusy] = useState(null);
    const [linked, setLinked] = useState([]);
    const [rowBusy, setRowBusy] = useState(null);

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
        setUrl('');
        setPreview(null);
        setBusy(null);
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

    const unlinkRow = (item) => {
        setRowBusy(item.article_id);
        axios.delete(`${base}/yandex/${item.article_id}`, { headers })
            .then(() => {
                toast(`«${item.title}» больше не сверяется с источником`, 'success');
                loadLinked();
            })
            .catch((e) => toast(errText(e, 'Не удалось отписать'), 'error'))
            .finally(() => setRowBusy(null));
    };

    const stats = preview ? compose(preview.content) : null;
    const duplicates = preview?.duplicates?.items || [];

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
                        вторую копию. Гасим её и оставляем «Связать снова». */}
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
                </div>
            )}
        >
            <div className="space-y-4">
                <div>
                    <label className={iosGroupLabel}>Ссылка на статью</label>
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
                    <IosHint text={HINT} label="Какие ссылки понимает" />
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

                        {/* Найденный дубль — чаще всего не повод отказаться от
                            переноса, а повод СВЯЗАТЬ уже написанную статью с
                            источником: так поставлена задача про «Межгород» —
                            статья в вике есть, следить надо за Яндексом. Без
                            этой кнопки единственным выходом была бы вторая
                            статья с тем же текстом. */}
                        {duplicates.length > 0 && (
                            <div className="rounded-xl bg-rose-50 px-3 py-2">
                                <span className="text-[11.5px] font-medium text-rose-700">
                                    Похожее у нас уже есть — можно связать с источником
                                    вместо переноса
                                </span>
                                <ul className="mt-1 space-y-1">
                                    {duplicates.slice(0, 3).map((d) => (
                                        <li key={d.article_id}
                                            className="flex flex-wrap items-center gap-1.5 text-[11.5px] text-rose-700">
                                            <Copy size={11} className="shrink-0" />
                                            <button
                                                type="button"
                                                className="truncate text-left underline decoration-rose-300"
                                                onClick={() => onOpenArticle?.(d.slug)}
                                            >
                                                {d.title}
                                            </button>
                                            <span className="shrink-0 text-rose-400">
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
                    <label className={iosGroupLabel}>Раздел</label>
                    <SectionTreeSelect
                        sections={sections}
                        spaces={spaces}
                        value={sectionId}
                        onChange={setSectionId}
                    />
                    <p className="mt-1 px-1 text-[11.5px] leading-relaxed text-slate-400">
                        Статья приедет ЧЕРНОВИКОМ и встанет в очередь «Перенос» —
                        опубликует её человек.
                    </p>
                </div>

                <label className="flex items-start gap-3 rounded-xl bg-slate-50 px-3 py-2">
                    <IosToggle checked={aiFormat} onChange={setAiFormat} />
                    <span className="min-w-0">
                        <span className="flex items-center gap-1.5 text-[13px] font-medium text-slate-900">
                            <Sparkles size={13} /> Оформить блоками помощником
                        </span>
                        <span className="mt-0.5 block text-[11.5px] leading-relaxed text-slate-500">
                            Помощник расставит вводку, плашки, шаги и карточки, не
                            меняя ни слова текста и не трогая картинки. Тумблер
                            останется у статьи: сверка будет оформлять её так же.
                        </span>
                    </span>
                </label>

                <label className="flex items-start gap-3 rounded-xl bg-slate-50 px-3 py-2">
                    <IosToggle checked={autoSync} onChange={setAutoSync} />
                    <span className="min-w-0">
                        <span className="flex items-center gap-1.5 text-[13px] font-medium text-slate-900">
                            <RefreshCw size={13} /> Обновлять из источника
                        </span>
                        <span className="mt-0.5 block text-[11.5px] leading-relaxed text-slate-500">
                            Раз в сутки сверяем страницу. Изменилась — обновляем
                            статью. Если статью правили руками, текст НЕ
                            перезаписывается: она помечается «источник изменился».
                        </span>
                    </span>
                </label>

                {/* Уже связанные статьи. Здесь же, а не отдельным экраном:
                    «перенести» и «что уже перенесено» — один вопрос, заданный
                    до и после. */}
                {linked.length > 0 && (
                    <div>
                        <label className={iosGroupLabel}>
                            Уже связаны с базой знаний ({linked.length})
                        </label>
                        <ul className="space-y-1.5">
                            {linked.map((item) => (
                                <LinkedRow
                                    key={item.article_id}
                                    item={item}
                                    busy={rowBusy === item.article_id}
                                    onSync={() => syncRow(item, false)}
                                    onForce={() => syncRow(item, true)}
                                    onUnlink={() => unlinkRow(item)}
                                />
                            ))}
                        </ul>
                    </div>
                )}
            </div>
        </IosModal>
    );
}