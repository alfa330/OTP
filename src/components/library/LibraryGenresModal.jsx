import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, Pencil, Plus, Tags, Trash2 } from 'lucide-react';
import {
    IosMenu, IosModal, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel, iosInput,
} from '../ui/ios';
import {
    GENRE_NAME_MAX, findGenreByName, formatBookCount, normalizeGenreName, sortGenres,
} from './libraryMeta';

/*
 * «Жанры» — справочник библиотеки для тех, кто её ведёт, как список в
 * «Напоминаниях»: строка добавления сверху, ниже жанры по алфавиту, у каждого
 * число книг и меню «···» — переименовать или удалить.
 *
 * Переименование — прямо в строке (Enter или уход из поля сохраняют, Escape
 * отменяет). Удаление спрашивает подтверждение в той же строке, а не вторым
 * окном поверх первого: жанр снимается с книг, сами книги остаются.
 */

const GenreRow = ({ genre, count, onRename, onDelete }) => {
    const [mode, setMode] = useState('view'); // view | rename | confirm
    const [value, setValue] = useState(genre.name);
    const [busy, setBusy] = useState(false);
    const inputRef = useRef(null);
    /* Тот же приём, что у поля нового жанра: Enter и blur не должны
       отправить переименование дважды, Escape — не сохранять при уходе. */
    const busyRef = useRef(false);
    const editingRef = useRef(false);

    useEffect(() => {
        if (mode === 'rename') {
            inputRef.current?.focus();
            inputRef.current?.select();
        }
    }, [mode]);

    const startRename = () => { editingRef.current = true; setValue(genre.name); setMode('rename'); };
    const stopRename = () => { editingRef.current = false; setMode('view'); };

    /* Удалось или нет — строка выходит из правки. Причина отказа («такой
       жанр уже есть») уже в тосте, а поле, забирающее фокус обратно, слало
       бы тот же запрос и тот же тост на каждый щелчок мимо него. */
    const saveRename = async () => {
        if (busyRef.current || !editingRef.current) return;
        const name = normalizeGenreName(inputRef.current?.value ?? value);
        if (!name || name === genre.name) { stopRename(); return; }
        busyRef.current = true;
        setBusy(true);
        await onRename(genre, name);
        busyRef.current = false;
        setBusy(false);
        stopRename();
    };

    const confirmDelete = async () => {
        setBusy(true);
        const ok = await onDelete(genre);
        // Удалённая строка уходит из списка сама; осталась — значит отказ.
        if (!ok) { setBusy(false); setMode('view'); }
    };

    if (mode === 'confirm') {
        return (
            <div className="space-y-2.5 px-3.5 py-3">
                <div className="text-[13.5px] text-slate-800">
                    Удалить жанр «<span className="font-semibold">{genre.name}</span>»?
                </div>
                <p className="text-[12px] leading-relaxed text-slate-500">
                    {!count ? 'Книг в этом жанре нет.'
                        : count === 1 ? 'В жанре 1 книга — она останется в библиотеке, просто без этого жанра.'
                            : `В жанре ${formatBookCount(count)} — они останутся в библиотеке, просто без этого жанра.`}
                </p>
                <div className="flex justify-end gap-2">
                    <button type="button" className={`${iosBtnSecondary} !py-2`} onClick={() => setMode('view')} disabled={busy}>
                        Отмена
                    </button>
                    <button
                        type="button"
                        className={`${iosBtnPrimary} !bg-rose-600 !py-2 hover:!bg-rose-700`}
                        onClick={confirmDelete}
                        disabled={busy}
                    >
                        {busy && <Loader2 size={14} className="animate-spin" />}
                        Удалить
                    </button>
                </div>
            </div>
        );
    }

    return (
        <div className="flex min-h-[48px] items-center gap-2 py-1.5 pl-3.5 pr-2">
            {mode === 'rename' ? (
                <input
                    ref={inputRef}
                    value={value}
                    maxLength={GENRE_NAME_MAX}
                    onChange={(event) => setValue(event.target.value)}
                    onKeyDown={(event) => {
                        if (event.key === 'Enter') { event.preventDefault(); saveRename(); }
                        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); stopRename(); }
                    }}
                    onBlur={saveRename}
                    readOnly={busy}
                    aria-label={`Новое название жанра «${genre.name}»`}
                    className={`${iosInput} !py-1.5`}
                />
            ) : (
                <button
                    type="button"
                    onClick={startRename}
                    className="min-w-0 flex-1 truncate text-left text-[14px] text-slate-900"
                    title="Переименовать"
                >
                    {genre.name}
                </button>
            )}
            {busy ? (
                <Loader2 size={15} className="mx-2 shrink-0 animate-spin text-slate-400" />
            ) : (
                <>
                    {mode === 'view' && (
                        <span className="shrink-0 text-[12px] tabular-nums text-slate-400">
                            {count ? formatBookCount(count) : 'нет книг'}
                        </span>
                    )}
                    <IosMenu
                        label={`Действия с жанром «${genre.name}»`}
                        items={[
                            { key: 'rename', label: 'Переименовать', icon: Pencil, onSelect: startRename },
                            { key: 'delete', label: 'Удалить', icon: Trash2, danger: true, onSelect: () => setMode('confirm') },
                        ]}
                    />
                </>
            )}
        </div>
    );
};

const LibraryGenresModal = ({ open, genres = [], counts, onClose, onCreate, onRename, onDelete }) => {
    const [draft, setDraft] = useState('');
    const [adding, setAdding] = useState(false);
    const [notice, setNotice] = useState('');
    const sorted = useMemo(() => sortGenres(genres), [genres]);

    useEffect(() => {
        if (open) { setDraft(''); setNotice(''); }
    }, [open]);

    const add = async () => {
        const name = normalizeGenreName(draft);
        if (!name || adding) return;
        const existing = findGenreByName(genres, name);
        if (existing) {
            setNotice(`Жанр «${existing.name}» уже есть`);
            return;
        }
        setAdding(true);
        const created = await onCreate(name);
        setAdding(false);
        if (created) { setDraft(''); setNotice(''); }
    };

    return (
        <IosModal
            open={open}
            onClose={onClose}
            title="Жанры"
            subtitle="Общие для всей библиотеки"
            maxWidth="max-w-md"
        >
            <div className="space-y-5">
                <section className="space-y-1.5">
                    <form
                        className={`${iosCard} flex items-center gap-2 p-2`}
                        onSubmit={(event) => { event.preventDefault(); add(); }}
                    >
                        <input
                            value={draft}
                            maxLength={GENRE_NAME_MAX}
                            onChange={(event) => { setDraft(event.target.value); setNotice(''); }}
                            placeholder="Новый жанр"
                            aria-label="Название нового жанра"
                            className={`${iosInput} !py-2`}
                        />
                        <button
                            type="submit"
                            className={`${iosBtnPrimary} shrink-0 !py-2`}
                            disabled={!normalizeGenreName(draft) || adding}
                        >
                            {adding ? <Loader2 size={15} className="animate-spin" /> : <Plus size={15} />}
                            Добавить
                        </button>
                    </form>
                    {notice && <p className="px-1 text-[11.5px] text-slate-500">{notice}</p>}
                </section>

                {sorted.length ? (
                    <section className="space-y-1.5">
                        <div className={iosGroupLabel}>Все жанры · {sorted.length}</div>
                        <div className={`${iosCard} divide-y divide-slate-100`}>
                            {sorted.map((genre) => (
                                <GenreRow
                                    key={genre.id}
                                    genre={genre}
                                    count={counts?.get(genre.id) || 0}
                                    onRename={onRename}
                                    onDelete={onDelete}
                                />
                            ))}
                        </div>
                    </section>
                ) : (
                    <div className="px-4 py-8 text-center">
                        <Tags size={26} className="mx-auto text-slate-300" />
                        <p className="mt-2.5 text-[13.5px] font-medium text-slate-700">Жанров пока нет</p>
                        <p className="mt-1 text-[12.5px] text-slate-500">
                            Добавьте первый — и отмечайте его у книг в меню карточки
                        </p>
                    </div>
                )}
            </div>
        </IosModal>
    );
};

export default LibraryGenresModal;
