import React, { useEffect, useMemo, useState } from 'react';
import { BookUp, FileText, Loader2, X } from 'lucide-react';
import {
    IosModal, iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';

/*
 * Каким отделам видна книга — одно окно на два случая:
 *
 *   publish — после выбора файла: какие книги уходят и в библиотеку каких
 *             отделов. Без отдела книгу не опубликовать: полки «для всех» нет,
 *             книга всегда выдана конкретным отделам (сервер проверяет то же).
 *   edit    — отделы уже загруженной книги (пункт «Отделы» в меню карточки).
 *
 * Выбор — общий селектор с поиском и «Выбрать все», отмеченные — чипами под
 * ним, как у отделов пространства вики: названия отделов длинные, и в кнопке
 * селектора после второго они уже не читаются.
 */

const formatSize = (bytes) => {
    const size = Number(bytes) || 0;
    if (size >= 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1).replace('.', ',')} МБ`;
    return `${Math.max(1, Math.round(size / 1024))} КБ`;
};

const LibraryDepartmentsModal = ({
    open, mode, files = [], book = null, departments = [], initialIds = [],
    busy = false, busyLabel = '', onSubmit, onClose, onPickFiles,
}) => {
    const publishing = mode === 'publish';
    const [ids, setIds] = useState([]);

    /* Набор собирается заново при каждом открытии: окно открывают для разных
       книг подряд, и второе открытие не должно показать отделы первой. */
    useEffect(() => {
        if (open) setIds([...new Set((initialIds || []).map(Number))]);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, book?.id]);

    /* CustomSelect отдаёт выбранное в renderValue строками ("70"), чипы —
       числами: сравнение по числу, иначе в кнопке стояло «Отдел № 70». */
    const nameOf = (id) => departments.find((item) => item.id === Number(id))?.name || `Отдел № ${id}`;

    /* Выключенный отдел выбрать нельзя, но если книга ему уже выдана, он
       остаётся в списке — иначе его не снять. */
    const options = useMemo(() => departments
        .filter((item) => item.active !== false || ids.includes(item.id))
        .map((item) => ({ value: item.id, label: item.name })), [departments, ids]);

    const renderValue = (values) => (
        <span className="truncate">{values.length === 1 ? nameOf(values[0]) : `Выбрано: ${values.length}`}</span>
    );

    const canSubmit = ids.length > 0 && !busy && (!publishing || files.length > 0);
    const many = publishing && files.length > 1;
    const title = publishing
        ? (files.length > 1 ? `Публикация книг: ${files.length}` : 'Публикация книги')
        : 'Отделы книги';

    return (
        <IosModal
            open={open}
            /* Во время загрузки окно не закрывается ничем, и жест «назад» на
               телефоне узнаёт об этом по false: запись окна возвращается в
               стек истории, иначе следующий жест его уже не закрыл бы. */
            onClose={() => {
                if (busy) return false;
                onClose?.();
                return undefined;
            }}
            title={title}
            subtitle={publishing ? `В библиотеке каких отделов ${many ? 'их' : 'её'} увидят` : book?.title}
            maxWidth="max-w-lg"
            footer={(
                <>
                    <button type="button" className={iosBtnSecondary} onClick={onClose} disabled={busy}>
                        Отмена
                    </button>
                    <button
                        type="button"
                        className={`${iosBtnPrimary} min-w-[150px] tabular-nums`}
                        onClick={() => onSubmit?.(ids)}
                        disabled={!canSubmit}
                    >
                        {busy ? <Loader2 size={15} className="animate-spin" /> : publishing && <BookUp size={15} />}
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
                                disabled={busy}
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
                            disabled={busy}
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
                                            disabled={busy}
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
            </div>
        </IosModal>
    );
};

export default LibraryDepartmentsModal;
