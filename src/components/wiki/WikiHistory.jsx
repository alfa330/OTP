import React, { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { ArrowRight, Loader2, RotateCcw, Users } from 'lucide-react';
import {
    IosBadge, IosModal, IosSegmented, iosBtnPrimary, iosBtnSecondary,
} from '../ui/ios';
import { STATUS_TONES, statusLabel } from './articleTypes';
import { CHANGE_LABELS, comparePair, fmtStamp, plural, stateKey } from './historyView';

/* История версий статьи: кто менял, что именно изменилось и как вернуть.
 *
 * Экран строится по /articles/<id>/history — там строки таблицы версий уже
 * превращены в РЕДАКЦИИ (почему это не одно и то же, разобрано в шапке
 * wiki/history.py). Здесь важно следствие: список идёт от новых к старым, у
 * каждой редакции указан тот, кто её СОЗДАЛ, а сохранения, не изменившие
 * ничего, свёрнуты в приписку к редакции — не выброшены, но и не выданы за
 * правку текста.
 *
 * Номеров у редакций нет намеренно: «редакция №N» уже занята ознакомлениями
 * (WikiAckPanel), где номер значит другое. Два разных числа под одним словом на
 * соседних экранах хуже, чем отсутствие числа; редакция опознаётся автором и
 * датой.
 *
 * Главным в строке стоит ИМЯ, а не время. Экран открывают с вопросом «кто это
 * написал» — на него и должен отвечать первый взгляд; время отвечает на
 * «когда», и это второй вопрос, а не первый.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

/* Пословная разметка внутри строки. Убранное и дописанное подсвечиваются
   фоном, а не зачёркиванием: зачёркнутая кириллица читается заметно хуже
   латиницы, а в статьях вики строки длинные. */
const Parts = ({ parts, tone }) => (
    <>
        {(parts || []).map((part, index) => (
            <span
                key={index}
                className={part.op === 'same' ? '' : (tone === 'del'
                    ? 'rounded bg-rose-200/80 px-[1px]'
                    : 'rounded bg-emerald-200/80 px-[1px]')}
            >
                {part.text}
            </span>
        ))}
    </>
);

/* Строка сравнения. Знак в отдельной колонке слева, а не цветом одним: цвет
   различают не все, и при печати он пропадает вовсе. */
const Marked = ({ sign, tone, children }) => (
    <div className={`flex gap-2.5 px-3 py-1.5 ${
        tone === 'del' ? 'border-l-2 border-rose-300 bg-rose-50/70'
            : 'border-l-2 border-emerald-400 bg-emerald-50/70'}`}
    >
        <span
            aria-hidden
            className={`w-3 shrink-0 select-none text-center text-[13px] font-semibold leading-relaxed ${
                tone === 'del' ? 'text-rose-400' : 'text-emerald-500'}`}
        >
            {sign}
        </span>
        <span className={`min-w-0 flex-1 whitespace-pre-wrap break-words text-[13.5px] leading-relaxed ${
            tone === 'del' ? 'text-slate-700' : 'text-slate-800'}`}
        >
            {children}
        </span>
    </div>
);

/* Экспортируется ради теста: рендер модалки через react-dom/server эффектов не
   выполняет, то есть данные в неё не попадают, и строки сравнения иначе не
   проверить ничем. */
export const DiffLine = ({ row }) => {
    if (row.op === 'gap') {
        return (
            <div className="flex items-center gap-3 px-3 py-2">
                <span className="h-px flex-1 bg-slate-200" />
                <span className="whitespace-nowrap text-[11.5px] text-slate-400">
                    {row.skipped} {plural(row.skipped, 'строка', 'строки', 'строк')} без изменений
                </span>
                <span className="h-px flex-1 bg-slate-200" />
            </div>
        );
    }
    if (row.op === 'same') {
        return (
            <div className="flex gap-2.5 px-3 py-1.5">
                <span aria-hidden className="w-3 shrink-0" />
                <span className="min-w-0 flex-1 whitespace-pre-wrap break-words text-[13.5px] leading-relaxed text-slate-400">
                    {row.text}
                </span>
            </div>
        );
    }
    if (row.op === 'del') return <Marked sign="−" tone="del">{row.text}</Marked>;
    if (row.op === 'ins') return <Marked sign="+" tone="ins">{row.text}</Marked>;
    // change — та же строка до и после, с подсветкой изменённых слов.
    return (
        <div className="my-0.5 overflow-hidden rounded-lg">
            <Marked sign="−" tone="del"><Parts parts={row.before_parts} tone="del" /></Marked>
            <Marked sign="+" tone="ins"><Parts parts={row.after_parts} tone="ins" /></Marked>
        </div>
    );
};

/** Изменение отдельного поля: заголовок или аннотация. */
const FieldChange = ({ label, before, after }) => (
    <div className="rounded-2xl bg-white p-3 ring-1 ring-slate-200/70">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">{label}</div>
        <div className="mt-1.5 grid gap-1.5 sm:grid-cols-2">
            <div className="whitespace-pre-wrap break-words rounded-xl bg-rose-50/70 px-2.5 py-1.5 text-[13px] text-slate-600">
                {before || <span className="text-slate-400">пусто</span>}
            </div>
            <div className="whitespace-pre-wrap break-words rounded-xl bg-emerald-50/70 px-2.5 py-1.5 text-[13px] text-slate-800">
                {after || <span className="text-slate-400">пусто</span>}
            </div>
        </div>
    </div>
);

/** Точка на ленте: цветом отмечены события, а не порядок. */
const dotTone = (item) => {
    if (item.is_current) return 'bg-emerald-500';
    if (item.restored_from_version_id) return 'bg-amber-500';
    if (item.is_first) return 'bg-blue-500';
    return 'bg-slate-300';
};

/** Строка списка редакций. Экспортируется ради теста — см. DiffLine. */
export const HistoryRow = ({ item, active, onSelect, restoredFrom = null }) => {
    const extra = item.extra_saves || [];
    return (
        <li className="relative pl-6">
            <span
                aria-hidden
                className={`absolute left-[4px] top-[18px] h-2.5 w-2.5 rounded-full ring-[3px] ring-slate-50 ${dotTone(item)}`}
            />
            <button
                type="button"
                onClick={() => onSelect(item.key)}
                aria-current={active ? 'true' : undefined}
                className={`w-full rounded-2xl px-3.5 py-3 text-left transition ${
                    active
                        ? 'bg-white shadow-[0_1px_3px_rgba(15,23,42,0.08)] ring-2 ring-blue-500/70'
                        : 'bg-white/70 ring-1 ring-slate-200/70 hover:bg-white hover:ring-slate-300'
                }`}
            >
                <div className="flex items-start justify-between gap-2">
                    <span className="min-w-0 truncate text-[13.5px] font-semibold text-slate-900">
                        {item.editor_name || 'Неизвестно'}
                    </span>
                    {item.is_current && <IosBadge tone="green">Текущая</IosBadge>}
                </div>
                <div className="mt-0.5 text-[12px] tabular-nums text-slate-500">
                    {fmtStamp(item.created_at)}
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-1">
                    {item.is_first && <IosBadge tone="blue">Создание</IosBadge>}
                    {(item.changed || []).map((field) => (
                        <IosBadge key={field} tone="slate">{CHANGE_LABELS[field] || field}</IosBadge>
                    ))}
                    {item.restored_from_version_id && <IosBadge tone="amber">Откат</IosBadge>}
                </div>
                {/* Куда именно вернули — датой той редакции, а не номером версии:
                    номеров в этом списке нет вовсе, и «версия №5» отсылала бы к
                    счётчику, которого человек нигде не видит. */}
                {item.restored_from_version_id && (
                    <p className="mt-2 text-[12px] leading-snug text-amber-700">
                        Вернули редакцию{restoredFrom ? ` от ${restoredFrom}` : ''}
                    </p>
                )}
                {/* У отката комментарий свой, служебный («Восстановление прежней
                    редакции»), и рядом со строкой выше он повторял бы её же более
                    общими словами. Человек комментарий откату не пишет: поле
                    заполняет сама операция (wiki/edit.py: restore_version). */}
                {item.comment && !item.restored_from_version_id && (
                    <p className="mt-2 line-clamp-2 text-[12.5px] leading-snug text-slate-600">
                        {item.comment}
                    </p>
                )}
                {/* Сохранения, не тронувшие ни текст, ни заголовок, ни статус:
                    перенос статьи в другой раздел, смена тегов. Отдельными
                    строками они заполнили бы список наполовину, а совсем без них
                    пропало бы «кто ещё сюда заходил». */}
                {extra.length > 0 && (
                    <p
                        className="mt-1.5 text-[11.5px] leading-snug text-slate-400"
                        title={extra.map((save) => `${fmtStamp(save.created_at)} — ${save.editor_name || 'Неизвестно'}`).join('\n')}
                    >
                        + ещё {extra.length} {plural(extra.length, 'сохранение', 'сохранения', 'сохранений')} без
                        изменений в тексте
                    </p>
                )}
            </button>
        </li>
    );
};

/** Одна сторона сравнения: «что было» и «что стало». */
const CompareSide = ({ label, tone, entry }) => (
    <div className={`min-w-0 flex-1 rounded-2xl bg-white px-3.5 py-2.5 ring-1 ${
        tone === 'del' ? 'ring-rose-200' : 'ring-emerald-200'}`}
    >
        <div className="flex items-center justify-between gap-2">
            <span className={`text-[11px] font-semibold uppercase tracking-wide ${
                tone === 'del' ? 'text-rose-500' : 'text-emerald-600'}`}
            >
                {label}
            </span>
            {/* Какая из двух сторон — нынешний текст статьи. Без пометки «Было
                Иванов, стало Иванов» не отвечает на вопрос, куда смотреть: обе
                стороны выглядят одинаково прошлыми. */}
            {entry?.is_current && <IosBadge tone="green">Текущая</IosBadge>}
        </div>
        <div className="mt-0.5 truncate text-[13.5px] font-semibold text-slate-900">
            {entry?.editor_name || 'Неизвестно'}
        </div>
        <div className="text-[12px] tabular-nums text-slate-500">{fmtStamp(entry?.created_at)}</div>
    </div>
);

export default function WikiHistory({ base, headers, article, open, onClose,
                                      onRestored = null, showToast = null }) {
    const articleId = article?.id;
    const [state, setState] = useState({ loading: true });
    const [selected, setSelected] = useState(null);
    const [against, setAgainst] = useState('prev');
    const [diff, setDiff] = useState({});
    const [restoring, setRestoring] = useState(false);
    /* Счётчик перезагрузки: после отката история другая — в ней появилась и
       редакция-откат, и снимок того, что было до него. */
    const [reload, setReload] = useState(0);
    /* На телефоне список редакций стоит НАД сравнением, и после нажатия по
       строке правая колонка остаётся за нижним краем: экран выглядит так,
       будто нажатие ничего не сделало. На широком экране колонки рядом, и
       подкручивать нечего. */
    const diffRef = useRef(null);

    useEffect(() => {
        if (!open || !articleId) return undefined;
        let cancelled = false;
        setState({ loading: true });
        axios.get(`${base}/articles/${articleId}/history`, { headers })
            .then((r) => {
                if (cancelled) return;
                const items = r.data?.items || [];
                setState({ items, canRestore: !!r.data?.can_restore });
                // Выбор держим, пока он существует: после отката ключ текущей
                // редакции меняется, и слепое сохранение оставило бы пустой
                // правый столбец.
                setSelected((previous) => (
                    previous && items.some((item) => item.key === previous)
                        ? previous : (items[0]?.key || null)));
            })
            .catch((e) => {
                if (!cancelled) setState({ error: errText(e, 'Не удалось открыть историю версий') });
            });
        return () => { cancelled = true; };
    }, [open, base, headers, articleId, reload]);

    const items = state.items || [];
    const pair = useMemo(() => comparePair(items, selected, against),
                         [items, selected, against]);
    const { entry, mode, canPrev, canCurrent } = pair;

    /* Откат ссылается на СТРОКУ версий, а строк на редакцию бывает несколько
       (сохранения, не изменившие текста, слиты в одну запись — см. version_ids).
       Поэтому ищем по всем строкам редакции, а не по её ключу. */
    const restoredFrom = useMemo(() => {
        const byVersion = new Map();
        items.forEach((item) => (item.version_ids || []).forEach(
            (id) => byVersion.set(id, item)));
        return (item) => {
            const target = item.restored_from_version_id
                && byVersion.get(item.restored_from_version_id);
            return target ? fmtStamp(target.created_at) : null;
        };
    }, [items]);

    /* Сводка над списком отвечает на вопрос «что тут вообще происходило»
       раньше, чем человек начнёт читать строки. Людей считаем вместе с теми,
       чьи сохранения слиты в приписку: они статью тоже трогали. Времени
       последней правки здесь нет намеренно — оно стоит строкой ниже, первым
       пунктом ленты, и повторять его значило бы занять место ничем. */
    const summary = useMemo(() => {
        const people = new Set();
        items.forEach((item) => {
            if (item.editor_name) people.add(item.editor_name);
            (item.extra_saves || []).forEach((save) => {
                if (save.editor_name) people.add(save.editor_name);
            });
        });
        return { revisions: items.length, people: people.size };
    }, [items]);

    const fromKey = pair.from ? stateKey(pair.from) : null;
    const toKey = pair.to ? stateKey(pair.to) : null;

    useEffect(() => {
        if (!open || !fromKey || !toKey) { setDiff({}); return undefined; }
        let cancelled = false;
        setDiff({ loading: true });
        axios.get(`${base}/articles/${articleId}/history/diff`, {
            headers, params: { from: fromKey, to: toKey },
        })
            .then((r) => { if (!cancelled) setDiff({ data: r.data }); })
            .catch((e) => {
                if (!cancelled) setDiff({ error: errText(e, 'Не удалось сравнить редакции') });
            });
        return () => { cancelled = true; };
    }, [open, base, headers, articleId, fromKey, toKey]);

    useEffect(() => {
        if (!open || !selected || !diffRef.current) return;
        if (!window.matchMedia?.('(max-width: 767px)')?.matches) return;
        diffRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, [open, selected]);

    const restore = () => {
        if (!entry || entry.version_id == null || restoring) return;
        /* Подтверждение обязательно, но оговорка в нём важнее самого вопроса:
           «восстановить» звучит как «стереть то, что написано сейчас». На деле
           откат — это новая редакция поверх истории, и вернуть всё обратно
           можно тем же способом (wiki/edit.py: restore_version). */
        if (!window.confirm(`Вернуть статью к редакции от ${fmtStamp(entry.created_at)}`
            + ` (${entry.editor_name || 'автор неизвестен'})?\n\n`
            + 'Нынешний текст не пропадёт: он останется в истории отдельной редакцией, '
            + 'и откат можно будет отменить так же.')) return;
        setRestoring(true);
        axios.post(`${base}/articles/${articleId}/versions/${entry.version_id}/restore`, {},
                   { headers })
            .then(() => {
                showToast?.('Статья возвращена к выбранной редакции', 'success');
                setSelected(null);
                setReload((value) => value + 1);
                onRestored?.();
            })
            .catch((e) => showToast?.(errText(e, 'Не удалось восстановить редакцию'), 'error'))
            .finally(() => setRestoring(false));
    };

    const body = diff.data?.body;
    const hasFields = !!(diff.data && (diff.data.title || diff.data.summary || diff.data.status));

    return (
        <IosModal
            open={open}
            onClose={onClose}
            title="История версий"
            subtitle={article?.title}
            maxWidth="max-w-6xl"
            footer={(
                <>
                    <button type="button" className={iosBtnSecondary} onClick={onClose}>
                        Закрыть
                    </button>
                    {state.canRestore && entry && !entry.is_current && (
                        <button
                            type="button"
                            className={iosBtnPrimary}
                            disabled={restoring}
                            onClick={restore}
                        >
                            {restoring ? <Loader2 size={14} className="animate-spin" />
                                : <RotateCcw size={14} />}
                            Вернуть эту редакцию
                        </button>
                    )}
                </>
            )}
        >
            {state.loading && (
                <div className="flex items-center justify-center gap-2 py-16 text-slate-400">
                    <Loader2 size={18} className="animate-spin" />
                    <span className="text-[13px]">Собираем историю…</span>
                </div>
            )}

            {state.error && (
                <div className="rounded-2xl bg-rose-50 px-3.5 py-3 text-[13px] text-rose-700">
                    {state.error}
                </div>
            )}

            {!state.loading && !state.error && (
                <div className="grid gap-5 md:grid-cols-[minmax(0,340px)_minmax(0,1fr)]">
                    <div className="min-w-0">
                        <div className="mb-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 px-1 text-[12px] text-slate-500">
                            <span className="font-medium text-slate-700">
                                {summary.revisions} {plural(summary.revisions, 'редакция', 'редакции', 'редакций')}
                            </span>
                            <span className="flex items-center gap-1">
                                <Users size={12} /> {summary.people} {plural(summary.people, 'человек', 'человека', 'человек')}
                            </span>
                        </div>
                        <ol className="relative md:max-h-[58vh] md:overflow-y-auto md:pr-1.5">
                            {/* Лента слева: история читается как лента событий, а
                                не как таблица. Линия рисуется от первой точки до
                                последней, поэтому отступы сверху и снизу. */}
                            <span aria-hidden className="absolute left-[8px] top-5 bottom-5 w-px bg-slate-200" />
                            <div className="flex flex-col gap-1.5">
                                {items.map((item) => (
                                    <HistoryRow
                                        key={item.key}
                                        item={item}
                                        active={item.key === selected}
                                        onSelect={setSelected}
                                        restoredFrom={restoredFrom(item)}
                                    />
                                ))}
                            </div>
                        </ol>
                    </div>

                    <div className="min-w-0 scroll-mt-2" ref={diffRef}>
                        {items.length < 2 && (
                            <div className="rounded-2xl bg-white px-4 py-12 text-center ring-1 ring-slate-200/70">
                                <div className="text-[15px] font-semibold text-slate-900">
                                    Статью ещё не правили
                                </div>
                                <p className="mx-auto mt-1.5 max-w-xs text-[13px] leading-relaxed text-slate-500">
                                    В истории одна редакция — та, с которой статью создали.
                                    Сравнивать пока не с чем.
                                </p>
                            </div>
                        )}

                        {items.length >= 2 && entry && (
                            <div className="space-y-3">
                                {canPrev && canCurrent && (
                                    <div className="flex justify-end">
                                        <IosSegmented
                                            value={mode}
                                            onChange={setAgainst}
                                            ariaLabel="С чем сравнивать"
                                            options={[
                                                { value: 'prev', label: 'С предыдущей' },
                                                { value: 'current', label: 'С текущей' },
                                            ]}
                                        />
                                    </div>
                                )}

                                {pair.from && pair.to ? (
                                    <div className="flex items-center gap-2">
                                        <CompareSide label="Было" tone="del" entry={pair.from} />
                                        <ArrowRight size={16} className="shrink-0 text-slate-400" />
                                        <CompareSide label="Стало" tone="ins" entry={pair.to} />
                                    </div>
                                ) : (
                                    <div className="rounded-2xl bg-white px-3.5 py-3 text-[13px] text-slate-500 ring-1 ring-slate-200/70">
                                        Сравнивать не с чем: это единственная редакция.
                                    </div>
                                )}

                                {diff.loading && (
                                    <div className="flex items-center justify-center gap-2 py-12 text-slate-400">
                                        <Loader2 size={16} className="animate-spin" />
                                        <span className="text-[13px]">Считаем различия…</span>
                                    </div>
                                )}

                                {diff.error && (
                                    <div className="rounded-2xl bg-rose-50 px-3.5 py-3 text-[13px] text-rose-700">
                                        {diff.error}
                                    </div>
                                )}

                                {diff.data && (
                                    <>
                                        {hasFields && (
                                            <div className="space-y-2">
                                                {diff.data.title && (
                                                    <FieldChange label="Заголовок"
                                                                 before={diff.data.title.before}
                                                                 after={diff.data.title.after} />
                                                )}
                                                {diff.data.summary && (
                                                    <FieldChange label="Аннотация"
                                                                 before={diff.data.summary.before}
                                                                 after={diff.data.summary.after} />
                                                )}
                                                {diff.data.status && (
                                                    <div className="flex flex-wrap items-center gap-2 rounded-2xl bg-white px-3.5 py-2.5 ring-1 ring-slate-200/70">
                                                        <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                                                            Статус
                                                        </span>
                                                        <IosBadge tone={STATUS_TONES[diff.data.status.before] || 'slate'}>
                                                            {statusLabel(diff.data.status.before)}
                                                        </IosBadge>
                                                        <ArrowRight size={12} className="text-slate-400" />
                                                        <IosBadge tone={STATUS_TONES[diff.data.status.after] || 'slate'}>
                                                            {statusLabel(diff.data.status.after)}
                                                        </IosBadge>
                                                    </div>
                                                )}
                                            </div>
                                        )}

                                        {diff.data.identical && (
                                            <div className="rounded-2xl bg-slate-100/70 px-3.5 py-3 text-[13px] text-slate-500">
                                                Эти редакции полностью совпадают.
                                            </div>
                                        )}

                                        {/* Правка тронула разметку, а не слова. Без этой оговорки
                                            экран сказал бы «различий нет» там, где в списке слева
                                            стоит бейдж «Текст», — и выглядело бы это поломкой. */}
                                        {diff.data.markup_only && (
                                            <div className="rounded-2xl bg-amber-50 px-3.5 py-3 text-[13px] leading-relaxed text-amber-800">
                                                Слова не менялись — правка тронула только оформление:
                                                выделение, цитату, разбивку абзацев или таблицу.
                                            </div>
                                        )}

                                        {/* Изменились только поля — тело осталось прежним. Без
                                            этой строки правый столбец под «Статус: черновик →
                                            опубликована» просто пуст, и читается это как
                                            незагрузившееся сравнение, а не как «текст не трогали». */}
                                        {body && !body.added && !body.removed
                                            && !diff.data.identical && !diff.data.markup_only && (
                                            <div className="rounded-2xl bg-slate-100/70 px-3.5 py-3 text-[13px] text-slate-500">
                                                Текст статьи не менялся — различие только в полях выше.
                                            </div>
                                        )}

                                        {body && (body.added > 0 || body.removed > 0) && (
                                            <div className="overflow-hidden rounded-2xl bg-white ring-1 ring-slate-200/70">
                                                <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-3.5 py-2.5">
                                                    <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                                                        Текст статьи
                                                    </span>
                                                    <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[12px] font-medium text-emerald-700">
                                                        + {body.added} {plural(body.added, 'строка', 'строки', 'строк')}
                                                    </span>
                                                    <span className="rounded-full bg-rose-50 px-2 py-0.5 text-[12px] font-medium text-rose-600">
                                                        − {body.removed} {plural(body.removed, 'строка', 'строки', 'строк')}
                                                    </span>
                                                </div>
                                                <div className="py-1">
                                                    {body.rows.map((row, position) => (
                                                        <DiffLine key={position} row={row} />
                                                    ))}
                                                </div>
                                                {body.truncated && (
                                                    <p className="border-t border-slate-100 px-3.5 py-2.5 text-[12px] text-slate-500">
                                                        Показаны первые {body.rows.length} строк сравнения —
                                                        различий больше, чем помещается на экран.
                                                    </p>
                                                )}
                                            </div>
                                        )}
                                    </>
                                )}
                            </div>
                        )}
                    </div>
                </div>
            )}
        </IosModal>
    );
}
