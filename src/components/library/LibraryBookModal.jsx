import React, { useEffect, useMemo, useRef, useState } from 'react';
import { BookUp, Check, FileText, Loader2, Plus, X } from 'lucide-react';
import {
    IosModal, iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { GENRE_NAME_MAX, findGenreByName, normalizeGenreName, sortGenres } from './libraryMeta';

/*
 * Отделы и жанры книги — одно окно на два случая:
 *
 *   publish — после выбора файла: какие книги уходят, в библиотеку каких
 *             отделов и в какие жанры. Без отдела книгу не опубликовать: полки
 *             «для всех» нет, книга всегда выдана конкретным отделам (сервер
 *             проверяет то же). Жанр — по желанию.
 *   edit    — отделы и жанры уже загруженной книги (меню карточки).
 *
 * Отделы — общий селектор с поиском и «Выбрать все», отмеченные — чипами под
 * ним, как у отделов пространства вики: названия отделов длинные, и в кнопке
 * селектора после второго они уже не читаются.
 *
 * Жанры — все сразу «пилюлями», нажатие отмечает: жанров немного, и выбор
 * виден целиком без выпадающего списка. Нужного нет — «Новый жанр» прямо
 * здесь: он сохраняется в общий справочник и сразу отмечается у книги.
 */

const formatSize = (bytes) => {
    const size = Number(bytes) || 0;
    if (size >= 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1).replace('.', ',')} МБ`;
    return `${Math.max(1, Math.round(size / 1024))} КБ`;
};

const pill = 'inline-flex h-[30px] max-w-full items-center gap-1 rounded-full px-3 text-[12.5px] font-medium transition active:scale-[0.97] disabled:opacity-50';

/* Поле нового жанра — на месте кнопки «Новый жанр». Жанр создают Enter,
   галочка в поле или кнопка окна «Сохранить»/«Опубликовать» (окно само
   дописывает набранное). Escape убирает поле.

   Уход фокуса жанр НЕ создаёт: щелчок по «Отмене» сначала уводит фокус из
   поля, и при сохранении «по blur» отменённое окно оставляло бы в общем
   справочнике жанр, от которого человек отказался (опечатку — навсегда). */
const NewGenreField = ({ value, creating, onChange, onCommit, onCancel }) => {
    const inputRef = useRef(null);
    useEffect(() => { inputRef.current?.focus(); }, []);
    return (
        <span className={`${pill} w-[200px] bg-white !px-0 ring-2 ring-blue-500/70`}>
            <input
                ref={inputRef}
                value={value}
                maxLength={GENRE_NAME_MAX}
                onChange={(event) => onChange(event.target.value)}
                onKeyDown={(event) => {
                    if (event.key === 'Enter') { event.preventDefault(); onCommit(); }
                    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onCancel(); }
                }}
                readOnly={creating}
                placeholder="Название жанра"
                aria-label="Название нового жанра"
                className="h-full min-w-0 flex-1 bg-transparent pl-3 text-[12.5px] text-slate-900 placeholder-slate-400 focus:outline-none"
            />
            <button
                type="button"
                // Фокус остаётся в поле: галочка — та же клавиша Enter, а не уход из него.
                onMouseDown={(event) => event.preventDefault()}
                onClick={onCommit}
                disabled={creating}
                aria-label="Создать жанр"
                className="grid h-full w-8 shrink-0 place-items-center rounded-r-full text-slate-400 transition hover:text-blue-600"
            >
                {creating ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
            </button>
        </span>
    );
};

const LibraryBookModal = ({
    open, mode, files = [], book = null, departments = [], initialIds = [], lockedDepartment = null,
    genres = [], initialGenreIds = [], onCreateGenre,
    busy = false, busyLabel = '', onSubmit, onClose, onPickFiles,
}) => {
    const publishing = mode === 'publish';
    const [ids, setIds] = useState([]);
    const [genreIds, setGenreIds] = useState([]);
    /* null — поля нового жанра нет; строка — набранное в нём. */
    const [draft, setDraft] = useState(null);
    const [creating, setCreating] = useState(false);
    /* Отмеченные жанры — ещё и в ref: «Сохранить», дождавшись создания
       нового жанра, берёт набор отсюда — состояние в его замыкании уже
       устарело, а перерисовка могла ещё не случиться. */
    const genreIdsRef = useRef([]);
    const creatingRef = useRef(false);
    const submittingRef = useRef(false);
    const applyGenreIds = (next) => {
        genreIdsRef.current = next;
        setGenreIds(next);
    };

    /* Пока жанр создаётся, окно заперто: «Отмена», крестик и жест «назад»
       не закрывают его, второй «Сохранить» не нажать, отделы и жанры не
       меняются. Иначе ответ, пришедший после «Отмены», сохранил бы книгу,
       от сохранения которой отказались. Запрос — один и короткий. */
    const locked = busy || creating;

    /* Набор собирается заново при каждом открытии: окно открывают для разных
       книг подряд, и второе открытие не должно показать отделы первой. */
    useEffect(() => {
        if (!open) return;
        /* СВ выдаёт книгу только своему отделу — выбирать нечего, и поля
           отделов в окне нет. */
        setIds(lockedDepartment ? [lockedDepartment.id] : [...new Set((initialIds || []).map(Number))]);
        applyGenreIds([...new Set((initialGenreIds || []).map(Number))]);
        setDraft(null);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, book?.id]);

    /* Жанр удалили в окне «Жанры», пока это окно держало его отмеченным, —
       отметка уходит вместе с ним, иначе сервер отказал бы «жанра нет». */
    useEffect(() => {
        const next = genreIdsRef.current.filter((id) => genres.some((genre) => genre.id === id));
        if (next.length !== genreIdsRef.current.length) applyGenreIds(next);
    }, [genres]);

    /* CustomSelect отдаёт выбранное в renderValue строками ("70"), чипы —
       числами: сравнение по числу, иначе в кнопке стояло «Отдел № 70». */
    const nameOf = (id) => departments.find((item) => item.id === Number(id))?.name || `Отдел № ${id}`;

    /* Выдать книгу можно любому действующему отделу; выключенный, которому
       она уже выдана, остаётся в списке — иначе его не снять. */
    const options = useMemo(() => departments
        .filter((item) => item.active !== false || ids.includes(item.id))
        .map((item) => ({ value: item.id, label: item.name })), [departments, ids]);

    const sortedGenres = useMemo(() => sortGenres(genres), [genres]);

    const toggleGenre = (id) => {
        const prev = genreIdsRef.current;
        applyGenreIds(prev.includes(id) ? prev.filter((value) => value !== id) : [...prev, id]);
    };
    const pickGenre = (id) => {
        if (!genreIdsRef.current.includes(id)) applyGenreIds([...genreIdsRef.current, id]);
    };

    /* Набранный жанр — в книгу: уже есть (без учёта регистра) — отмечается
       он, нет — создаётся в справочнике. -> false, если не создался: тост с
       причиной уже на экране, имя остаётся в поле, и повторный Enter его
       повторит. Пустое поле просто убирается. */
    const commitDraft = async () => {
        if (draft === null) return true;
        const name = normalizeGenreName(draft);
        if (!name) {
            setDraft(null);
            return true;
        }
        const existing = findGenreByName(genres, name);
        if (existing) {
            pickGenre(existing.id);
            setDraft(null);
            return true;
        }
        if (creatingRef.current) return false;
        creatingRef.current = true;
        setCreating(true);
        const created = await onCreateGenre?.(name);
        creatingRef.current = false;
        setCreating(false);
        if (!created) return false;
        pickGenre(created.id);
        setDraft(null);
        return true;
    };

    /* «Сохранить» с набранным, но ещё не созданным жанром — сначала жанр,
       потом книга с ним. Не создался — книга не сохраняется. */
    const submit = async () => {
        if (submittingRef.current || locked) return;
        submittingRef.current = true;
        try {
            if (!(await commitDraft())) return;
            onSubmit?.({ departmentIds: ids, genreIds: genreIdsRef.current });
        } finally {
            submittingRef.current = false;
        }
    };

    const renderValue = (values) => (
        <span className="truncate">{values.length === 1 ? nameOf(values[0]) : `Выбрано: ${values.length}`}</span>
    );

    const canSubmit = ids.length > 0 && !locked && (!publishing || files.length > 0);
    const many = publishing && files.length > 1;
    const title = publishing
        ? (files.length > 1 ? `Публикация книг: ${files.length}` : 'Публикация книги')
        : (lockedDepartment ? 'Жанры книги' : 'Отделы и жанры');
    const publishSubtitle = lockedDepartment
        ? `${many ? 'Их' : 'Её'} увидят сотрудники вашего отдела`
        : `В библиотеке каких отделов ${many ? 'их' : 'её'} увидят`;

    return (
        <IosModal
            open={open}
            /* Во время загрузки и создания жанра окно не закрывается ничем, и
               жест «назад» на телефоне узнаёт об этом по false: запись окна
               возвращается в стек истории, иначе следующий жест его уже не
               закрыл бы. */
            onClose={() => {
                if (locked) return false;
                onClose?.();
                return undefined;
            }}
            title={title}
            subtitle={publishing ? publishSubtitle : book?.title}
            maxWidth="max-w-lg"
            footer={(
                <>
                    <button type="button" className={iosBtnSecondary} onClick={onClose} disabled={locked}>
                        Отмена
                    </button>
                    <button
                        type="button"
                        className={`${iosBtnPrimary} min-w-[150px] tabular-nums`}
                        onClick={submit}
                        disabled={!canSubmit}
                    >
                        {locked ? <Loader2 size={15} className="animate-spin" /> : publishing && <BookUp size={15} />}
                        {busy && busyLabel ? busyLabel : (publishing ? 'Опубликовать' : 'Сохранить')}
                    </button>
                </>
            )}
        >
            <div className="space-y-5">
                {publishing && (
                    <section className="space-y-1.5">
                        <div className="flex items-end justify-between gap-2">
                            <div className={iosGroupLabel}>{files.length > 1 ? 'Файлы' : 'Файл'}</div>
                            <button
                                type="button"
                                className={`${iosBtnGhost} !px-2 !py-1 text-[12px]`}
                                onClick={onPickFiles}
                                disabled={locked}
                            >
                                Выбрать другие
                            </button>
                        </div>
                        <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                            {files.map((file) => (
                                <div key={`${file.name}-${file.size}`} className="flex items-center gap-2.5 px-3.5 py-2.5">
                                    <FileText size={16} className="shrink-0 text-slate-400" />
                                    <span className="min-w-0 flex-1 truncate text-[13.5px] text-slate-800">{file.name}</span>
                                    <span className="shrink-0 text-[12px] tabular-nums text-slate-400">{formatSize(file.size)}</span>
                                </div>
                            ))}
                        </div>
                    </section>
                )}

                {!lockedDepartment && (
                    <section className="space-y-1.5">
                        <div className={iosGroupLabel}>Каким отделам видно</div>
                        <div className={`${iosCard} space-y-2 p-3.5`}>
                            <CustomSelect
                                variant="ios"
                                multiple
                                searchable
                                bulkActions
                                searchPlaceholder="Поиск по названию отдела…"
                                ariaLabel="Отделы, которым видна книга"
                                placeholder="Выберите отделы"
                                value={ids}
                                options={options}
                                onChange={(next) => setIds((Array.isArray(next) ? next : []).map(Number))}
                                renderValue={renderValue}
                                disabled={locked}
                            />
                            {ids.length > 0 && (
                                <div className="flex flex-wrap gap-1.5">
                                    {ids.map((id) => (
                                        <span
                                            key={id}
                                            className="inline-flex max-w-full items-center gap-1 rounded-full bg-slate-100 py-1 pl-2.5 pr-1 text-[12px] text-slate-700"
                                        >
                                            <span className="truncate">{nameOf(id)}</span>
                                            <button
                                                type="button"
                                                onClick={() => setIds((prev) => prev.filter((value) => value !== id))}
                                                disabled={locked}
                                                aria-label={`Убрать отдел ${nameOf(id)}`}
                                                className="grid h-4 w-4 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-200 hover:text-slate-600"
                                            >
                                                <X size={11} />
                                            </button>
                                        </span>
                                    ))}
                                </div>
                            )}
                        </div>
                        <p className="px-1 text-[11.5px] leading-relaxed text-slate-400">
                            {ids.length
                                ? `${many ? 'Книги' : 'Книгу'} увидят сотрудники только выбранных отделов.`
                                : `Выберите хотя бы один отдел — без него ${many ? 'книги' : 'книгу'} не увидит никто.`}
                        </p>
                    </section>
                )}

                <section className="space-y-1.5">
                    <div className={iosGroupLabel}>Жанры</div>
                    <div className={`${iosCard} p-3.5`}>
                        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Жанры книги">
                            {sortedGenres.map((genre) => {
                                const on = genreIds.includes(genre.id);
                                return (
                                    <button
                                        key={genre.id}
                                        type="button"
                                        aria-pressed={on}
                                        onClick={() => toggleGenre(genre.id)}
                                        disabled={locked}
                                        className={`${pill} ${on
                                            ? 'bg-blue-50 text-blue-700 ring-1 ring-blue-200'
                                            : 'bg-slate-100 text-slate-600 hover:bg-slate-200 hover:text-slate-800'}`}
                                    >
                                        {on && <Check size={13} className="shrink-0" />}
                                        <span className="truncate">{genre.name}</span>
                                    </button>
                                );
                            })}
                            {draft === null ? (
                                <button
                                    type="button"
                                    onClick={() => setDraft('')}
                                    disabled={locked}
                                    className={`${pill} border border-dashed border-slate-300 text-slate-500 hover:bg-slate-50 hover:text-slate-700`}
                                >
                                    <Plus size={13} className="shrink-0" />
                                    Новый жанр
                                </button>
                            ) : (
                                <NewGenreField
                                    value={draft}
                                    creating={creating}
                                    onChange={setDraft}
                                    onCommit={() => { if (!locked) commitDraft(); }}
                                    onCancel={() => { if (!creating) setDraft(null); }}
                                />
                            )}
                        </div>
                    </div>
                </section>
            </div>
        </IosModal>
    );
};

export default LibraryBookModal;
