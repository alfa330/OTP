import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import {
    AlertTriangle, Check, FileUp, HelpCircle, Loader2, RefreshCw, Search,
    Sparkles, Wand2,
} from 'lucide-react';
import {
    iosCard, iosGroupLabel, iosInput, iosBtnSecondary, IosBadge, IosHint, IosToggle,
} from '../ui/ios';

/* Панель помощника в редакторе статьи.
 *
 * Три действия, и все они делают одно и то же по сути — приносят в редактор
 * новый текст, который человек потом смотрит и сохраняет сам:
 *   * собрать статью из документа (новая статья);
 *   * обновить существующую статью новым документом;
 *   * поправить текст по указанию словами.
 * Ничего не пишется в базу отсюда. Кнопку «Сохранить» нажимает человек.
 *
 * Отдельный компонент, а не ещё двести строк в WikiEditor: там уже 425 строк и
 * восемнадцать пакетов TipTap.
 *
 * Флажок «Поддержка ИИ» — рубильник, а не украшение. Пока он выключен, ни текст
 * статьи, ни документы во внешний API не уходят: кнопки недоступны, а проверка
 * дублей идёт только по нашей базе. Сервер это тоже проверяет — эндпоинты
 * отказывают без явного признака.
 *
 * Похожие статьи показываются С ОТРЫВКОМ и НЕ открываются по клику: открытие
 * пишет просмотр, а у статей со строгим режимом — ещё и запись в журнал чтения.
 * Проверка «нет ли дубля» следов чтения оставлять не должна.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

const ACCEPT_WITH_AI = '.docx,.doc,.pdf,.xlsx,.xlsm,.csv,.txt,.md,.html,.htm,.png,.jpg,.jpeg,.webp';

const VERDICT_TONE = { дубль: 'red', похоже: 'amber', рядом: 'slate' };

const VERDICT_HINT = {
    дубль: 'Скорее всего, эта статья уже есть — проверьте, не создаёте ли вторую',
    похоже: 'Есть очень близкая статья — возможно, стоит дописать её, а не создавать новую',
    рядом: 'Есть статьи по той же теме',
};

const POWER_HINT = 'Помощник соберёт статью из загруженного документа, обновит её '
    + 'новой версией документа, поправит текст по вашему указанию и подскажет, если '
    + 'такая статья уже есть. Пока флажок выключен, текст статьи и документы во '
    + 'внешний сервис не отправляются, а сама статья не попадает в ответы помощника.';

const FORMATS_HINT = 'Word, Excel, CSV, PDF, текст, фото или скан. Таблицы из Word и '
    + 'Excel переносятся программой без участия модели, а PDF и снимок модель читает '
    + 'сама — постранично, вместе с сеткой таблиц.';

const UPDATE_HINT = 'Загрузите новую версию документа — помощник сверит её со статьёй '
    + 'построчно: изменившееся заменит, новое добавит, а про исчезнувшее спросит, а не '
    + 'удалит молча. Список изменений покажет отдельно.';

const EDIT_HINT = 'Напишите словами, что поправить: «сократи вдвое», «добавь раздел '
    + 'про доставку», «оформи условия таблицей». Помощник меняет только то, о чём '
    + 'сказано, остальной текст переносит дословно.';

const percent = (score) => `${Math.round((Number(score) || 0) * 100)}%`;

const SimilarRow = ({ item, onUpdate }) => (
    <li className="rounded-xl bg-slate-50 px-3 py-2">
        <div className="flex flex-wrap items-center gap-2">
            <IosBadge tone={VERDICT_TONE[item.verdict] || 'slate'}>
                {item.verdict} · {percent(item.score)}
            </IosBadge>
            <span className="text-[13px] font-medium text-slate-900">{item.title}</span>
            {item.status && item.status !== 'published' && (
                <IosBadge tone="slate">{item.status === 'draft' ? 'черновик' : item.status}</IosBadge>
            )}
            {item.section && (
                <span className="text-[11px] text-slate-500">в разделе «{item.section}»</span>
            )}
            <span className="text-[11px] text-slate-400">нашлось по: {item.found_by}</span>
            {/* Главный смысл находки: чаще всего документ не новая статья, а новая
                версия существующей. Кнопка ведёт прямо туда, унося с собой файл. */}
            {onUpdate && (
                <button
                    type="button"
                    onClick={() => onUpdate(item)}
                    className="ml-auto inline-flex items-center gap-1 rounded-lg bg-white px-2 py-1 text-[11.5px] font-medium text-indigo-600 ring-1 ring-indigo-100 transition hover:bg-indigo-50"
                >
                    <RefreshCw size={12} /> Обновить её этим документом
                </button>
            )}
        </div>
        {item.excerpt && (
            <div className="mt-1 text-[12px] leading-snug text-slate-500">{item.excerpt}</div>
        )}
    </li>
);

const Bullets = ({ icon: Icon, title, tone, items }) => (
    <div className={`rounded-xl p-3 ring-1 ${tone}`}>
        <div className="flex items-center gap-1.5 text-[12.5px] font-medium">
            <Icon size={14} /> {title}
        </div>
        <ul className="mt-1 list-disc space-y-0.5 pl-5 text-[12px] leading-snug">
            {items.map((line, index) => <li key={index}>{line}</li>)}
        </ul>
    </div>
);

export default function WikiAiDraft({
    base, headers, showToast, enabled, onEnabledChange, onDraft, onContent,
    getSnapshot, excludeId = null, pendingUpdateFile = null, onPendingUsed = null,
    onUpdateExisting = null,
    /* Пространство работы — ТОЛЬКО для журнала. Статьи ещё нет (черновик из
       документа) или она не сохранена (правка по указанию), и вывести её
       пространство сервер не может ни из чего: запись оказывается «ничьей»,
       а такие видны в журнале обоих пространств сразу. */
    spaceId = null,
}) {
    const [busy, setBusy] = useState(null);
    const [result, setResult] = useState(null);
    const [duplicates, setDuplicates] = useState(null);
    const [instruction, setInstruction] = useState('');
    // Файл держим у себя: если документ окажется новой версией существующей
    // статьи, его надо унести в неё, а не заставлять человека выбирать заново.
    const lastFile = useRef(null);
    const isExisting = !!excludeId;

    const updateFromDocument = (file) => {
        if (!file) return;
        const snapshot = getSnapshot?.() || {};
        const form = new FormData();
        form.append('file', file);
        form.append('ai_support', '1');
        form.append('content', snapshot.content || '');
        form.append('title', snapshot.title || '');
        if (excludeId) form.append('article_id', String(excludeId));
        setBusy('update');
        setResult(null);
        axios.post(`${base}/articles/ai/update`, form,
                   { headers, params: { space_id: spaceId || undefined } })
            .then((r) => {
                const data = r.data || {};
                onContent?.(data.content);
                setResult(data);
                showToast?.(data.changes?.length
                    ? `Статья обновлена, изменений: ${data.changes.length}`
                    : 'Документ обработан, изменений не найдено', 'success');
            })
            .catch((e) => showToast?.(errText(e, 'Не удалось обновить статью'), 'error'))
            .finally(() => setBusy(null));
    };

    /* Документ, принесённый из проверки дублей: статья уже открыта, обновляем
       сразу. Одноразово — иначе повторный рендер запускал бы модель заново. */
    useEffect(() => {
        if (!pendingUpdateFile || !enabled || busy) return;
        updateFromDocument(pendingUpdateFile);
        onPendingUsed?.();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [pendingUpdateFile, enabled]);

    const buildFromDocument = (file) => {
        if (!file) return;
        lastFile.current = file;
        const form = new FormData();
        form.append('file', file);
        form.append('ai_support', '1');
        setBusy('draft');
        setResult(null);
        setDuplicates(null);
        axios.post(`${base}/import/ai`, form,
                   { headers, params: { space_id: spaceId || undefined } })
            .then((r) => {
                const data = r.data || {};
                onDraft?.(data);
                setResult(data);
                setDuplicates(data.duplicates || null);
                showToast?.(`Статья собрана из документа (${data.kind})`, 'success');
            })
            .catch((e) => showToast?.(errText(e, 'Не удалось собрать статью'), 'error'))
            .finally(() => setBusy(null));
    };

    const applyInstruction = () => {
        const snapshot = getSnapshot?.() || {};
        setBusy('edit');
        setResult(null);
        axios.post(`${base}/articles/ai/edit`, {
            content: snapshot.content || '', title: snapshot.title || '',
            instruction, article_id: excludeId, ai_support: enabled,
            space_id: spaceId || undefined,
        }, { headers })
            .then((r) => {
                const data = r.data || {};
                onContent?.(data.content);
                setResult(data);
                setInstruction('');
                showToast?.('Правка применена', 'success');
            })
            .catch((e) => showToast?.(errText(e, 'Не удалось применить правку'), 'error'))
            .finally(() => setBusy(null));
    };

    const checkDuplicates = () => {
        const snapshot = getSnapshot?.() || {};
        setBusy('similar');
        axios.post(`${base}/articles/similar`, {
            title: snapshot.title || '', content: snapshot.content || '',
            exclude_id: excludeId,
            // При выключенном флажке сервер ищет только по своей базе: смысловой
            // поиск считает внешний сервис, а панель обещает, что наружу ничего
            // не уходит.
            ai_support: enabled,
        }, { headers })
            .then((r) => {
                setDuplicates(r.data || null);
                if (!(r.data?.items || []).length) {
                    showToast?.('Похожих статей не нашлось', 'success');
                }
            })
            .catch((e) => showToast?.(errText(e, 'Не удалось проверить'), 'error'))
            .finally(() => setBusy(null));
    };

    const items = duplicates?.items || [];
    const locked = !enabled || busy !== null;
    const fileButton = `${iosBtnSecondary} ${enabled ? 'cursor-pointer' : 'pointer-events-none opacity-40'}`;

    return (
        <section className="space-y-1.5">
            <div className={iosGroupLabel}>Помощник</div>
            <div className={`${iosCard} divide-y divide-slate-100`}>
                <div className="flex items-center justify-between gap-3 p-4">
                    <div className="flex items-center gap-1.5 text-[14px] font-medium text-slate-900">
                        <Sparkles size={15} className="text-indigo-500" />
                        Поддержка ИИ
                        <IosHint text={POWER_HINT} label="Что делает поддержка ИИ" />
                    </div>
                    <IosToggle checked={enabled} onChange={onEnabledChange} />
                </div>

                <div className="space-y-3 p-4">
                    <div className="flex flex-wrap items-center gap-2">
                        {/* У существующей статьи первая кнопка — ОБНОВИТЬ, а не
                            собрать заново: пересборка стёрла бы правки, которые
                            люди вносили руками после импорта. */}
                        {isExisting ? (
                            <label className={fileButton} title="Новая версия документа">
                                {busy === 'update'
                                    ? <Loader2 size={14} className="animate-spin" />
                                    : <RefreshCw size={14} />}
                                Обновить из документа
                                <input
                                    type="file"
                                    className="hidden"
                                    disabled={locked}
                                    accept={ACCEPT_WITH_AI}
                                    onChange={(e) => { updateFromDocument(e.target.files?.[0]); e.target.value = ''; }}
                                />
                            </label>
                        ) : (
                            <label className={fileButton} title="Документ, из которого собрать статью">
                                {busy === 'draft'
                                    ? <Loader2 size={14} className="animate-spin" />
                                    : <FileUp size={14} />}
                                Собрать из документа
                                <input
                                    type="file"
                                    className="hidden"
                                    disabled={locked}
                                    accept={ACCEPT_WITH_AI}
                                    onChange={(e) => { buildFromDocument(e.target.files?.[0]); e.target.value = ''; }}
                                />
                            </label>
                        )}
                        <IosHint text={isExisting ? UPDATE_HINT : FORMATS_HINT}
                                 label="Какие файлы понимает" />
                        <button
                            type="button"
                            className={iosBtnSecondary}
                            disabled={busy !== null}
                            onClick={checkDuplicates}
                        >
                            {busy === 'similar'
                                ? <Loader2 size={14} className="animate-spin" />
                                : <Search size={14} />}
                            Такая статья уже есть?
                        </button>
                    </div>

                    {/* Правка словами. Enter отправляет: поле однострочное, и
                        тянуться мышью к кнопке ради каждой правки утомительно. */}
                    <div className="flex flex-wrap items-center gap-2">
                        <input
                            className={`${iosInput} min-w-[220px] flex-1 text-[13px]`}
                            value={instruction}
                            disabled={locked}
                            placeholder="Что поправить? Например: сократи вдвое и оформи условия таблицей"
                            onChange={(e) => setInstruction(e.target.value)}
                            onKeyDown={(e) => {
                                if (e.key === 'Enter' && instruction.trim().length >= 3) {
                                    e.preventDefault();
                                    applyInstruction();
                                }
                            }}
                        />
                        <IosHint text={EDIT_HINT} label="Как формулировать правку" align="right" />
                        <button
                            type="button"
                            className={iosBtnSecondary}
                            disabled={locked || instruction.trim().length < 3}
                            onClick={applyInstruction}
                        >
                            {busy === 'edit'
                                ? <Loader2 size={14} className="animate-spin" />
                                : <Wand2 size={14} />}
                            Применить
                        </button>
                    </div>

                    {result && (
                        <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
                            {result.model && <span>{result.model}</span>}
                            {result.elapsed ? <span>· {result.elapsed} с</span> : null}
                            {result.tables ? <span>· таблиц: {result.tables}</span> : null}
                            {result.images?.length ? <span>· картинок: {result.images.length}</span> : null}
                            {!result.warnings?.length && (
                                <span className="inline-flex items-center gap-1 text-emerald-600">
                                    <Check size={12} /> замечаний нет
                                </span>
                            )}
                        </div>
                    )}

                    {!!result?.changes?.length && (
                        <Bullets
                            icon={Check}
                            title="Что изменилось"
                            tone="bg-emerald-50 text-emerald-900 ring-emerald-200/70"
                            items={result.changes}
                        />
                    )}

                    {/* Вопросы отдельно от замечаний: замечание — «проверь», а
                        вопрос — «без тебя не решить». Смешивать их значит
                        приучать пролистывать и то, и другое. */}
                    {!!result?.questions?.length && (
                        <Bullets
                            icon={HelpCircle}
                            title="Помощник спрашивает"
                            tone="bg-indigo-50 text-indigo-900 ring-indigo-200/70"
                            items={result.questions}
                        />
                    )}

                    {!!result?.warnings?.length && (
                        <Bullets
                            icon={AlertTriangle}
                            title="Проверьте перед публикацией"
                            tone="bg-amber-50 text-amber-900 ring-amber-200/70"
                            items={result.warnings}
                        />
                    )}

                    {duplicates && (
                        <div className="space-y-2">
                            <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
                                {items.length ? (
                                    <span className="font-medium text-slate-700">
                                        {VERDICT_HINT[duplicates.verdict] || 'Похожие статьи'}
                                    </span>
                                ) : (
                                    <span className="inline-flex items-center gap-1 text-emerald-600">
                                        <Check size={13} /> Похожих статей не нашлось
                                    </span>
                                )}
                            </div>
                            {!!items.length && (
                                <ul className="space-y-1.5">
                                    {items.map((item) => (
                                        <SimilarRow
                                            key={item.article_id}
                                            item={item}
                                            onUpdate={(!isExisting && lastFile.current && onUpdateExisting)
                                                ? (row) => onUpdateExisting(row, lastFile.current)
                                                : null}
                                        />
                                    ))}
                                </ul>
                            )}
                            {/* Честно говорим про границы проверки. «Ничего не нашлось» при
                                неполном покрытии значит меньше, чем кажется, и молчать об
                                этом хуже, чем признать. Причины неполноты разные, и
                                называть их одинаково нельзя: выключенный флажок это
                                выбор редактора, а неполный индекс — свойство вики. */}
                            {!duplicates.vector_covered && (
                                <p className="text-[11px] leading-snug text-slate-400">
                                    {duplicates.ai_support === false
                                        ? 'Поддержка ИИ выключена, поэтому искали только по названию и словам текста — они не покидают нашу базу.'
                                        : 'Проверка шла по названию и словам текста: смысловой поиск охватывает только статьи, попавшие в индекс помощника.'}
                                </p>
                            )}
                        </div>
                    )}
                </div>
            </div>
        </section>
    );
}
