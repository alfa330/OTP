import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ImagePlus, Loader2, Plus, X } from 'lucide-react';

import { PHOTO_ACCEPT, PHOTO_MAX_COUNT, pluralPhotos } from './parcelPhoto';

/*
 * Весь вид фотографий раздела «Посылки»: плитка, зона прикрепления в форме и
 * лента в карточке.
 *
 * Отдельный файл, а не куски внутри ParcelForm и ParcelCard: плитка нужна обоим
 * (в форме — с крестиком, в карточке — без), и две её копии разъехались бы на
 * первой правке скругления.
 *
 * КЛАССЫ `tv-*` ИЗ «ЗАДАЧ» СЮДА НЕ ПЕРЕНОСЯТСЯ, хотя зона перетаскивания там
 * уже написана. Они физически есть в документе (TasksView импортируется
 * статически), но их цвета объявлены только внутри `.tv-root`: снаружи
 * `border: 1.5px dashed var(--border-strong)` становится невычислимым и
 * сбрасывает ВЕСЬ шорткат рамки, фон уходит в прозрачный, а `--accent`
 * подхватывает глобальный зелёный из styles.css. Переносится ПОВЕДЕНИЕ —
 * включая главную хитрость onDragLeave, — а вид берётся из палитры раздела.
 */

/* Плитка. Квадрат, потому что снимают и вертикально, и горизонтально, а сетка
   из разновысоких плиток читается как сломанная. */
export const PhotoTile = ({ src, alt = '', busy = false, onRemove, onOpen, onError }) => (
    <div className="group relative aspect-square overflow-hidden rounded-xl bg-slate-100 ring-1 ring-slate-200/70">
        {src ? (
            <img
                src={src}
                alt={alt}
                loading="lazy"
                onError={onError}
                onClick={onOpen}
                className={`h-full w-full object-cover transition ${onOpen ? 'cursor-zoom-in hover:brightness-95' : ''}`}
            />
        ) : (
            /* Подпись вместо сломанного <img>: не выдалась подпись адреса —
               человек должен видеть причину, а не серый крестик браузера. */
            <span className="flex h-full w-full items-center justify-center px-2 text-center text-[11.5px] leading-tight text-slate-400">
                Фото недоступно
            </span>
        )}
        {busy && (
            <span className="absolute inset-0 grid place-items-center bg-white/60">
                <Loader2 size={16} className="animate-spin text-slate-500" />
            </span>
        )}
        {onRemove && !busy && (
            /* Крестик виден ВСЕГДА, а не по наведению: на телефоне наведения
               нет, и спрятанная за hover кнопка там просто не существует. */
            <button
                type="button"
                onClick={onRemove}
                aria-label="Убрать фотографию"
                className="absolute right-1 top-1 grid h-[22px] w-[22px] place-items-center rounded-full bg-slate-900/55 text-white backdrop-blur transition hover:bg-slate-900/75 active:scale-95"
            >
                <X size={13} strokeWidth={2.5} />
            </button>
        )}
    </div>
);


/* Зона прикрепления в форме: перетаскивание, буфер, выбор файлом, камера. */
export const PhotoPicker = ({ tiles = [], disabled = false, onAdd, onRemove, active = true }) => {
    const inputRef = useRef(null);
    const [dragging, setDragging] = useState(false);
    const full = tiles.length >= PHOTO_MAX_COUNT;

    const openPicker = useCallback(() => {
        if (disabled || full) return;
        inputRef.current?.click?.();
    }, [disabled, full]);

    const handleDragOver = useCallback((event) => {
        event.preventDefault();
        if (event.dataTransfer) event.dataTransfer.dropEffect = disabled ? 'none' : 'copy';
        if (!disabled) setDragging(true);
    }, [disabled]);

    /* Хитрость, без которой рамка мигает: уход курсора НА ВЛОЖЕННУЮ плитку
       браузер тоже считает dragleave зоны. Приём взят у «Задач». */
    const handleDragLeave = useCallback((event) => {
        const next = event.relatedTarget;
        if (next && event.currentTarget.contains(next)) return;
        setDragging(false);
    }, []);

    const handleDrop = useCallback((event) => {
        event.preventDefault();
        setDragging(false);
        if (disabled) return;
        onAdd?.(event.dataTransfer?.files);
    }, [disabled, onAdd]);

    /* Вставка из буфера — на document, пока форма открыта: Ctrl+V жмут, не
       наведя курсор ни на что конкретное. */
    useEffect(() => {
        if (!active || disabled) return undefined;
        const onPaste = (event) => {
            const data = event.clipboardData;
            if (!data) return;
            // Если в буфере есть текст — это вставка текста, а не картинки.
            // Иначе Ctrl+V в поле «Описание» цеплял бы ещё и вложение.
            if (data.getData('text/plain')) return;
            const images = [...(data.files || [])].filter(
                (file) => String(file.type || '').startsWith('image/'));
            if (!images.length) return;
            event.preventDefault();
            onAdd?.(images);
        };
        document.addEventListener('paste', onPaste);
        return () => document.removeEventListener('paste', onPaste);
    }, [active, disabled, onAdd]);

    const input = (
        <input
            ref={inputRef}
            type="file"
            multiple
            accept={PHOTO_ACCEPT}
            className="sr-only"
            disabled={disabled}
            onChange={(event) => {
                onAdd?.(event.target.files);
                // Без сброса тот же файл второй раз не выберется: значение
                // не изменилось, и change не наступит.
                event.target.value = '';
            }}
        />
    );

    if (!tiles.length) {
        return (
            <div
                role="button"
                tabIndex={disabled ? -1 : 0}
                aria-disabled={disabled}
                onClick={openPicker}
                onKeyDown={(event) => {
                    if (event.key !== 'Enter' && event.key !== ' ') return;
                    event.preventDefault();
                    openPicker();
                }}
                onDragEnter={handleDragOver}
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
                className={`flex cursor-pointer flex-col items-center gap-1.5 rounded-2xl border border-dashed px-4 py-6 text-center transition ${
                    dragging ? 'border-blue-400 bg-blue-50/60' : 'border-slate-300 bg-slate-50 hover:bg-slate-100'
                } ${disabled ? 'pointer-events-none opacity-60' : ''}`}
            >
                {input}
                <ImagePlus size={20} className="text-slate-400" />
                <span className="text-[12.5px] text-slate-500">
                    Перетащите фото, вставьте из буфера или нажмите
                </span>
            </div>
        );
    }

    return (
        <div
            onDragEnter={handleDragOver}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            /* На широком экране четыре колонки, а не три: при трёх плитка в
               модалке выходит под 150 px, и десять снимков уезжают на четыре
               ряда под сгиб формы. На телефоне наоборот три: при четырёх
               плитка сжимается до 71 px, и крестик на ней становится мельче
               пальца — а промах по нему стоит человеку повторной попытки
               ровно там, где заводить посылку и так неудобно. */
            className={`grid grid-cols-3 gap-2 rounded-2xl border border-dashed p-2 transition sm:grid-cols-4 ${
                dragging ? 'border-blue-400 bg-blue-50/60' : 'border-transparent'
            }`}
        >
            {input}
            {tiles.map((tile) => (
                <PhotoTile
                    key={tile.key}
                    src={tile.src}
                    busy={tile.busy}
                    onError={tile.onError}
                    onRemove={disabled ? null : () => onRemove?.(tile)}
                />
            ))}
            {!full && (
                <button
                    type="button"
                    onClick={openPicker}
                    disabled={disabled}
                    aria-label="Добавить фотографию"
                    className="grid aspect-square place-items-center rounded-xl border border-dashed border-slate-300 text-slate-400 transition hover:border-slate-400 hover:bg-slate-50 hover:text-slate-500 disabled:opacity-50"
                >
                    <Plus size={18} />
                </button>
            )}
        </div>
    );
};


/* Сколько плиток показываем в карточке, не открывая просмотр. */
export const STRIP_LIMIT = 3;

/* Лента в карточке.
 *
 * Потолок в три плитки — не экономия места ради экономии: высота секции тогда
 * ПОСТОЯННА при любом числе снимков (≈96 px), и блок «Что с посылкой» не
 * уезжает за сгиб на телефоне, когда к посылке приложили девять кадров.
 *
 * Единственная фотография рисуется крупно и из полного адреса, а не из
 * миниатюры: 480 пикселей, растянутые на 360 CSS px при плотности 3×, — это
 * заметное мыло, и именно один снимок будет самым частым случаем.
 */
export const PhotoStrip = ({ photos = [], onOpen, onStale }) => {
    if (!photos.length) return null;

    if (photos.length === 1) {
        const only = photos[0];
        return (
            <button
                type="button"
                onClick={() => onOpen?.(only.url, 0)}
                disabled={!only.url}
                className="block w-full overflow-hidden rounded-2xl bg-slate-100 ring-1 ring-slate-200/70 disabled:cursor-default"
            >
                {only.url ? (
                    <img
                        src={only.url}
                        alt="Фотография посылки"
                        onError={onStale}
                        className="aspect-[4/3] w-full cursor-zoom-in object-cover transition hover:brightness-95"
                    />
                ) : (
                    <span className="flex aspect-[4/3] w-full items-center justify-center text-[12px] text-slate-400">
                        Фото недоступно
                    </span>
                )}
            </button>
        );
    }

    const shown = photos.slice(0, STRIP_LIMIT);
    const rest = photos.length - shown.length;

    return (
        <div className="flex flex-wrap gap-2">
            {shown.map((photo, index) => (
                <button
                    key={photo.id}
                    type="button"
                    onClick={() => onOpen?.(photo.url, index)}
                    disabled={!photo.url}
                    className="h-[72px] w-[72px] shrink-0 overflow-hidden rounded-xl bg-slate-100 ring-1 ring-slate-200/70 disabled:cursor-default"
                >
                    {photo.thumb_url ? (
                        <img
                            src={photo.thumb_url}
                            alt=""
                            loading="lazy"
                            onError={onStale}
                            className="h-full w-full cursor-zoom-in object-cover transition hover:brightness-95"
                        />
                    ) : (
                        <span className="flex h-full w-full items-center justify-center px-1 text-center text-[10.5px] leading-tight text-slate-400">
                            Нет
                        </span>
                    )}
                </button>
            ))}
            {rest > 0 && (
                <button
                    type="button"
                    onClick={() => onOpen?.(photos[STRIP_LIMIT]?.url, STRIP_LIMIT)}
                    className="grid h-[72px] w-[72px] shrink-0 place-items-center rounded-xl bg-slate-100 text-[13px] font-semibold text-slate-600 ring-1 ring-slate-200/70 transition hover:bg-slate-200/70"
                    title={`Ещё ${rest} ${pluralPhotos(rest)}`}
                >
                    +{rest}
                </button>
            )}
        </div>
    );
};
