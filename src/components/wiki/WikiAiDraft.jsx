import React, { useState } from 'react';
import axios from 'axios';
import {
    AlertTriangle, Check, FileUp, Loader2, Search, Sparkles,
} from 'lucide-react';
import {
    iosCard, iosGroupLabel, iosBtnSecondary, IosBadge, IosHint, IosToggle,
} from '../ui/ios';

/* Панель помощника в редакторе статьи: флажок, сборка из документа, проверка дублей.
 *
 * Отдельный компонент, а не ещё сто строк в WikiEditor: там уже 425 строк и
 * восемнадцать пакетов TipTap, и панель с тремя состояниями сделала бы файл
 * нечитаемым.
 *
 * Флажок «Поддержка ИИ» — не украшение, а рубильник. Пока он выключен, документ
 * во внешний API не уходит вообще: кнопка сборки недоступна, а обычный импорт
 * (разбор формата на нашем сервере) работает как раньше. Сервер это тоже
 * проверяет — /import/ai отказывает без явного признака.
 *
 * Похожие статьи показываются С ОТРЫВКОМ и НЕ открываются по клику. Причина не в
 * лени: открытие статьи пишет просмотр, а у статей со строгим режимом — ещё и
 * запись в журнал чтения. Проверка «нет ли дубля» не должна оставлять следов
 * чтения, поэтому доказательство приходит прямо в ответе.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

const ACCEPT_WITH_AI = '.docx,.doc,.pdf,.xlsx,.xlsm,.csv,.txt,.md,.png,.jpg,.jpeg,.webp';

const VERDICT_TONE = { дубль: 'red', похоже: 'amber', рядом: 'slate' };

const VERDICT_HINT = {
    дубль: 'Скорее всего, эта статья уже есть — проверьте, не создаёте ли вторую',
    похоже: 'Есть очень близкая статья — возможно, стоит дописать её, а не создавать новую',
    рядом: 'Есть статьи по той же теме',
};

const POWER_HINT = 'Помощник соберёт статью из загруженного документа и подскажет, '
    + 'если такая статья уже есть. Пока флажок выключен, текст статьи и документы во '
    + 'внешний сервис не отправляются, а сама статья не попадает в ответы помощника.';

const FORMATS_HINT = 'Word, Excel, CSV, PDF, текст, фото или скан. Таблицы из Word и '
    + 'Excel переносятся программой без участия модели, а PDF и снимок модель читает '
    + 'сама — постранично, вместе с сеткой таблиц.';

const percent = (score) => `${Math.round((Number(score) || 0) * 100)}%`;

const SimilarRow = ({ item }) => (
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
        </div>
        {item.excerpt && (
            <div className="mt-1 text-[12px] leading-snug text-slate-500">{item.excerpt}</div>
        )}
    </li>
);

export default function WikiAiDraft({
    base, headers, showToast, enabled, onEnabledChange, onDraft, getSnapshot,
    excludeId = null,
}) {
    const [busy, setBusy] = useState(null);
    const [result, setResult] = useState(null);
    const [duplicates, setDuplicates] = useState(null);

    const buildFromDocument = (file) => {
        if (!file) return;
        const form = new FormData();
        form.append('file', file);
        form.append('ai_support', '1');
        setBusy('draft');
        setResult(null);
        setDuplicates(null);
        axios.post(`${base}/import/ai`, form, { headers })
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
                        <label
                            className={`${iosBtnSecondary} ${enabled ? 'cursor-pointer' : 'pointer-events-none opacity-40'}`}
                            title={enabled ? 'Документ, из которого собрать статью'
                                : 'Включите поддержку ИИ'}
                        >
                            {busy === 'draft'
                                ? <Loader2 size={14} className="animate-spin" />
                                : <FileUp size={14} />}
                            Собрать из документа
                            <input
                                type="file"
                                className="hidden"
                                disabled={!enabled || busy !== null}
                                accept={ACCEPT_WITH_AI}
                                onChange={(e) => { buildFromDocument(e.target.files?.[0]); e.target.value = ''; }}
                            />
                        </label>
                        <IosHint text={FORMATS_HINT} label="Какие файлы понимает" />
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

                    {!!result?.warnings?.length && (
                        <div className="rounded-xl bg-amber-50 p-3 ring-1 ring-amber-200/70">
                            <div className="flex items-center gap-1.5 text-[12.5px] font-medium text-amber-900">
                                <AlertTriangle size={14} />
                                Проверьте перед публикацией
                            </div>
                            <ul className="mt-1 list-disc space-y-0.5 pl-5 text-[12px] leading-snug text-amber-900">
                                {result.warnings.map((warning, index) => (
                                    <li key={index}>{warning}</li>
                                ))}
                            </ul>
                        </div>
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
                                        <SimilarRow key={item.article_id} item={item} />
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
