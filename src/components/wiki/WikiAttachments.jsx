import React, { useCallback, useState } from 'react';
import axios from 'axios';
import {
    ChevronDown, ChevronUp, Download, Loader2, Paperclip, TextCursorInput, Trash2,
} from 'lucide-react';
import { iosCard, iosGroupLabel } from '../ui/ios';
import { absoluteFileUrl } from './fileUrls';
import { attachmentKind, attachmentMeta } from './attachments';

/* Приложения к статье — панель редактора.
 *
 * Отдельный компонент, а не ещё полторы сотни строк в WikiEditor: там уже 558
 * строк, и половина из них — тулбар TipTap, к файлам отношения не имеющий.
 *
 * Порядок работы намеренно двухшаговый и об этом сказано прямо в подсказке:
 * файл уезжает в хранилище СРАЗУ (иначе при сохранении статьи пришлось бы
 * ждать загрузку десятка мегабайт и гадать, что именно отвалилось), а
 * читателям он открывается только вместе с сохранением статьи. До сохранения
 * файл видит один загрузивший — это правило сервера, а не оформление
 * (wiki/routes_articles.py: wiki_file).
 *
 * Из-за этого «Убрать» здесь ничего не удаляет насовсем: строка исчезает из
 * списка, а файл открепится от статьи при сохранении и вернётся в то же
 * состояние, в каком был сразу после загрузки. Закрыть редактор без сохранения
 * — значит не потерять ничего.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

/* Кнопка-иконка строки файла. Отдельная, потому что их три на строку и
   различаются они только иконкой и подписью. */
const RowButton = ({ title, onClick, disabled, danger, children }) => (
    <button
        type="button"
        title={title}
        aria-label={title}
        onClick={onClick}
        disabled={disabled}
        className={`grid h-8 w-8 shrink-0 place-items-center rounded-lg transition disabled:opacity-30 ${
            danger ? 'text-slate-400 hover:bg-red-50 hover:text-red-600'
                : 'text-slate-400 hover:bg-slate-100 hover:text-slate-700'
        }`}
    >
        {children}
    </button>
);

export default function WikiAttachments({
    base, headers, showToast, items = [], onChange, onInsert = null,
}) {
    // Имена файлов, которые сейчас едут в хранилище. Именно имена, а не
    // счётчик: когда прикладывают пять файлов разом, человек должен видеть,
    // какой именно ещё грузится, — а не «загружается 3».
    const [uploading, setUploading] = useState([]);
    const [dragging, setDragging] = useState(false);

    const upload = useCallback((fileList) => {
        const files = Array.from(fileList || []);
        if (!files.length) return;

        setUploading((names) => [...names, ...files.map((f) => f.name)]);
        files.forEach((file) => {
            const form = new FormData();
            form.append('file', file);
            axios.post(`${base}/attachments`, form, { headers })
                .then((r) => {
                    // Список обновляем ФУНКЦИЕЙ: файлы грузятся параллельно, и
                    // ответы приходят вперемешку — сложение с «тем списком, что
                    // был на момент отправки» теряло бы соседние приложения.
                    onChange?.((prev) => [...prev, r.data]);
                })
                .catch((e) => showToast?.(
                    `${file.name}: ${errText(e, 'не удалось приложить')}`, 'error'))
                .finally(() => setUploading(
                    (names) => {
                        // Убираем ОДНО вхождение имени: два файла с одинаковым
                        // именем — обычное дело, и второй не должен пропасть из
                        // индикатора вместе с первым.
                        const index = names.indexOf(file.name);
                        return index === -1 ? names
                            : [...names.slice(0, index), ...names.slice(index + 1)];
                    }));
        });
    }, [base, headers, onChange, showToast]);

    const move = (index, delta) => onChange?.((prev) => {
        const next = [...prev];
        const target = index + delta;
        if (target < 0 || target >= next.length) return prev;
        [next[index], next[target]] = [next[target], next[index]];
        return next;
    });

    const remove = (id) => onChange?.((prev) => prev.filter((a) => a.id !== id));

    return (
        <section className="space-y-1.5">
            <div className={iosGroupLabel}>Файлы к статье</div>
            <div className={`${iosCard} space-y-3 p-4`}>
                {/* Зона приёма — она же кнопка. Перетаскивание работает и
                    выглядит как перетаскивание, но остаётся вторым способом:
                    на телефоне его нет вовсе, поэтому клик по этой же области
                    открывает обычный выбор файлов. */}
                <label
                    onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
                    onDragLeave={() => setDragging(false)}
                    onDrop={(e) => {
                        e.preventDefault();
                        setDragging(false);
                        upload(e.dataTransfer?.files);
                    }}
                    className={`flex cursor-pointer flex-col items-center justify-center gap-1 rounded-xl border-2 border-dashed px-4 py-5 text-center transition ${
                        dragging
                            ? 'border-blue-400 bg-blue-50/60'
                            : 'border-slate-200 bg-slate-50/60 hover:border-slate-300 hover:bg-slate-100/60'
                    }`}
                >
                    <Paperclip size={18} className="text-slate-400" />
                    <span className="text-[13.5px] font-medium text-slate-700">
                        Перетащите файлы сюда или выберите на компьютере
                    </span>
                    <span className="text-[12px] text-slate-400">
                        Договор, бланк, презентация — до 25 МБ каждый
                    </span>
                    <input
                        type="file"
                        multiple
                        className="hidden"
                        onChange={(e) => { upload(e.target.files); e.target.value = ''; }}
                    />
                </label>

                {(items.length > 0 || uploading.length > 0) && (
                    <ul className="space-y-1.5">
                        {items.map((attachment, index) => {
                            const kind = attachmentKind(attachment.name);
                            const Icon = kind.icon;
                            return (
                                <li
                                    key={attachment.id}
                                    className="flex items-center gap-3 rounded-xl bg-slate-50 px-3 py-2"
                                >
                                    <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-lg ${kind.tone}`}>
                                        <Icon size={16} />
                                    </span>
                                    <span className="min-w-0 flex-1">
                                        <span className="block truncate text-[13.5px] font-medium text-slate-800">
                                            {attachment.name}
                                        </span>
                                        <span className="block text-[11.5px] text-slate-400">
                                            {attachmentMeta(attachment)}
                                        </span>
                                    </span>
                                    {/* Порядок — стрелками, а не перетаскиванием
                                        строк: приложений у статьи единицы, а
                                        перетаскивание пришлось бы дублировать
                                        для клавиатуры и телефона. */}
                                    {items.length > 1 && (
                                        <>
                                            <RowButton
                                                title="Выше"
                                                disabled={index === 0}
                                                onClick={() => move(index, -1)}
                                            >
                                                <ChevronUp size={15} />
                                            </RowButton>
                                            <RowButton
                                                title="Ниже"
                                                disabled={index === items.length - 1}
                                                onClick={() => move(index, 1)}
                                            >
                                                <ChevronDown size={15} />
                                            </RowButton>
                                        </>
                                    )}
                                    {/* Тот же файл можно назвать и в тексте —
                                        на том шаге инструкции, где он нужен.
                                        Кнопка есть только когда есть куда
                                        вставлять: панель переиспользуема. */}
                                    {onInsert && (
                                        <RowButton
                                            title="Вставить ссылку в текст статьи"
                                            onClick={() => onInsert(attachment)}
                                        >
                                            <TextCursorInput size={15} />
                                        </RowButton>
                                    )}
                                    <a
                                        href={absoluteFileUrl(attachment.download_url
                                            || `${attachment.url}?download=1`, base)}
                                        target="_blank"
                                        rel="noreferrer"
                                        title="Скачать — проверить, что приложился нужный файл"
                                        aria-label={`Скачать ${attachment.name}`}
                                        className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
                                    >
                                        <Download size={15} />
                                    </a>
                                    <RowButton
                                        title="Убрать из статьи"
                                        danger
                                        onClick={() => remove(attachment.id)}
                                    >
                                        <Trash2 size={15} />
                                    </RowButton>
                                </li>
                            );
                        })}

                        {uploading.map((name, index) => (
                            <li
                                key={`uploading-${index}-${name}`}
                                className="flex items-center gap-3 rounded-xl bg-slate-50 px-3 py-2 text-slate-400"
                            >
                                <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-slate-100">
                                    <Loader2 size={16} className="animate-spin" />
                                </span>
                                <span className="min-w-0 flex-1 truncate text-[13.5px]">{name}</span>
                                <span className="shrink-0 text-[11.5px]">Загружаем…</span>
                            </li>
                        ))}
                    </ul>
                )}

                <p className="px-0.5 text-[11.5px] leading-relaxed text-slate-400">
                    Файлы встанут под текстом статьи — читатель откроет или скачает их
                    оттуда. У читателей они появятся после сохранения статьи, а «Убрать»
                    отвяжет файл тем же сохранением.
                    {onInsert && ' Кнопкой «Вставить ссылку в текст» тот же файл можно'
                        + ' назвать в нужном месте статьи, а скрепка в панели редактора'
                        + ' кладёт в текст новый файл.'}
                </p>
            </div>
        </section>
    );
}
