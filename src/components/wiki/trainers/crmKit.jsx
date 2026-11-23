import React, { useLayoutEffect, useRef, useState } from 'react';

/* Детали учебной CRM с разметкой РАБОЧЕЙ CRM — один в один.
 *
 * Стили приходят из настоящего admin.css (см. crmFrame.jsx), поэтому здесь
 * важны не цвета, а классы и вложенность: они обязаны совпадать с тем, что
 * рисует CRM. Эталон снят с отрисованных страниц CRM 20.11.2026.
 *
 *   Multiselect — vue-multiselect 2.x: категории, «Поиск категории»;
 *   SearchSelect — список Craftable «search-select»: «Таксопарк»;
 *   Notifications — vue-notification: «Готово / Обращение создано»;
 *   VModal — vue-js-modal 1.x: «Автомобиль водителя», «Условия работы».
 *
 * Ссылки внутри CRM никуда не ведут: учебный документ живёт во фрейме, и
 * настоящий переход увёл бы фрейм с учебной CRM — поэтому у ссылок noNav.
 */

/** Ссылка CRM не уводит фрейм — переход делает тренажёр. */
export const noNav = (event) => { event.preventDefault(); };

/** Регистр не важен, «ё» = «е» — так сравнивают списки CRM. */
export const normYo = (text) => String(text == null ? '' : text).toLowerCase().replace(/ё/g, 'е');

const LABELS = { select: 'Press enter to select', selected: 'Selected', deselect: 'Press enter to remove' };

/**
 * vue-multiselect 2.x. options: [{ value, label }].
 *
 * Поиск по умолчанию — подстрока в подписи без учёта регистра (как у
 * vue-multiselect); свой поиск — filter(query) → options. Подписи «Press
 * enter to select» на строке под курсором — те же, что в CRM (showLabels).
 */
export function Multiselect({
    id, value, options, placeholder, onSelect, onOpen = null, onClose = null,
    showLabels = true, filter = null, small = false, disabled = false,
    autoOpen = false, target = false,
    noResult = 'No elements found. Consider changing the search query.', noOptions = 'List is empty.',
}) {
    const [open, setOpen] = useState(false);
    const [query, setQuery] = useState('');
    const [pointer, setPointer] = useState(0);
    const inputRef = useRef(null);
    const selected = options.find((option) => option.value === value) || null;
    const needle = normYo(query.trim());
    const shown = filter ? filter(query) : (needle
        ? options.filter((option) => normYo(option.label).includes(needle))
        : options);

    const activate = () => {
        if (disabled || open) return;
        setOpen(true);
        setQuery('');
        const at = selected ? options.indexOf(selected) : 0;
        setPointer(at < 0 ? 0 : at);
        if (onOpen) onOpen();
        setTimeout(() => inputRef.current && inputRef.current.focus(), 0);
    };
    const deactivate = () => {
        if (!open) return;
        setOpen(false);
        setQuery('');
        if (onClose) onClose();
    };
    const pick = (option) => {
        if (!option) return;
        onSelect(option.value, option);
        setOpen(false);
        setQuery('');
        if (inputRef.current) inputRef.current.blur();
        if (onClose) onClose();
    };

    /* Список Craftable открывает multiselect уже раскрытым — сразу при
       появлении, как activate() в $nextTick. */
    useLayoutEffect(() => { if (autoOpen) activate(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

    return (
        <div
            tabIndex={-1}
            role="combobox"
            aria-owns={`listbox-${id}`}
            className={`multiselect${small ? ' multiselect-sm' : ''}${open ? ' multiselect--active' : ''}`
                + `${disabled ? ' multiselect--disabled' : ''}${target ? ' is-target' : ''}`}
            onMouseDown={(event) => {
                if (open) return;
                event.preventDefault();
                activate();
            }}
        >
            <div
                className="multiselect__select"
                onMouseDown={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    if (open) deactivate(); else activate();
                }}
            />
            <div className="multiselect__tags">
                <div className="multiselect__tags-wrap" style={{ display: 'none' }} />
                <div className="multiselect__spinner" style={{ display: 'none' }} />
                <input
                    ref={inputRef}
                    name=""
                    id={id}
                    type="text"
                    autoComplete="off"
                    spellCheck="false"
                    placeholder={placeholder}
                    tabIndex={0}
                    aria-controls={`listbox-${id}`}
                    className="multiselect__input"
                    style={open ? { width: '100%' } : { width: 0, position: 'absolute', padding: 0 }}
                    value={query}
                    onFocus={activate}
                    onBlur={deactivate}
                    onChange={(event) => { setQuery(event.target.value); setPointer(0); }}
                    onKeyDown={(event) => {
                        if (event.key === 'Escape') { event.preventDefault(); deactivate(); event.currentTarget.blur(); }
                        if (event.key === 'ArrowDown') { event.preventDefault(); setPointer((p) => Math.min(p + 1, shown.length - 1)); }
                        if (event.key === 'ArrowUp') { event.preventDefault(); setPointer((p) => Math.max(p - 1, 0)); }
                        if (event.key === 'Enter') { event.preventDefault(); pick(shown[pointer]); }
                    }}
                />
                {!open && selected ? <span className="multiselect__single">{selected.label}</span> : null}
                {!open && !selected ? <span className="multiselect__placeholder"> {placeholder} </span> : null}
            </div>
            <div
                className="multiselect__content-wrapper"
                tabIndex={-1}
                style={open ? { maxHeight: 300 } : { maxHeight: 300, display: 'none' }}
            >
                <ul className="multiselect__content" role="listbox" id={`listbox-${id}`} style={{ display: 'inline-block' }}>
                    {open ? shown.map((option, index) => (
                        <li key={option.value} className="multiselect__element" id={`${id}-${index}`} role="option">
                            <span
                                className={`multiselect__option${index === pointer ? ' multiselect__option--highlight' : ''}`
                                    + `${option.value === value ? ' multiselect__option--selected' : ''}`}
                                data-select={showLabels ? LABELS.select : ''}
                                data-selected={showLabels ? LABELS.selected : ''}
                                data-deselect={showLabels ? LABELS.deselect : ''}
                                onMouseEnter={() => setPointer(index)}
                                onMouseDown={(event) => event.preventDefault()}
                                onClick={() => pick(option)}
                            >
                                <span>{option.label}</span>
                            </span>
                        </li>
                    )) : null}
                    <li style={open && needle && !shown.length ? undefined : { display: 'none' }}>
                        <span className="multiselect__option">{noResult}</span>
                    </li>
                    <li style={open && !options.length ? undefined : { display: 'none' }}>
                        <span className="multiselect__option">{noOptions}</span>
                    </li>
                </ul>
            </div>
        </div>
    );
}

/**
 * Список Craftable «search-select» — «Таксопарк» в форме обращения. Закрытый —
 * кнопка-«лицо»; по нажатию превращается в multiselect с поиском по словам
 * (регистр и «ё» не важны) и без подписей «Press enter».
 */
export function SearchSelect({
    id, value, options, placeholder = null, emptyLabel = null, onSelect, small = false,
    disabled = false, target = false,
}) {
    const [active, setActive] = useState(false);
    const items = emptyLabel === null ? options : [{ value: '', label: emptyLabel }, ...options];
    const selected = items.find((item) => String(item.value) === String(value == null ? '' : value)) || null;
    const placeholderText = placeholder || emptyLabel || 'Выберите';
    const filter = (query) => {
        const words = normYo(query).split(/\s+/).filter(Boolean);
        return words.length ? items.filter((item) => words.every((word) => normYo(item.label).includes(word))) : items;
    };

    return (
        <div className={`search-select${small ? ' search-select--sm' : ''}`}>
            {active ? (
                <Multiselect
                    id={id}
                    value={selected ? selected.value : undefined}
                    options={items}
                    placeholder={placeholderText}
                    showLabels={false}
                    filter={filter}
                    autoOpen
                    target={target}
                    noResult={<span>Ничего не найдено</span>}
                    noOptions={<span>Список пуст</span>}
                    onSelect={(next, option) => onSelect(next, option)}
                    onClose={() => setActive(false)}
                />
            ) : (
                <button
                    type="button"
                    className={`multiselect search-select__face${disabled ? ' multiselect--disabled' : ''}${target ? ' is-target' : ''}`}
                    id={id}
                    disabled={disabled}
                    onFocus={() => setActive(true)}
                    onMouseDown={(event) => { event.preventDefault(); setActive(true); }}
                >
                    <div className="multiselect__select" />
                    <div className="multiselect__tags">
                        {selected
                            ? <span className="multiselect__single">{selected.label}</span>
                            : <span className="multiselect__placeholder">{placeholderText}</span>}
                    </div>
                </button>
            )}
        </div>
    );
}

/** vue-notification — уведомление снизу справа, как «Готово · Обращение создано». */
export const Notifications = ({ note }) => (
    <div className="vue-notification-group" style={{ width: 300, bottom: 0, right: 0 }}>
        <span>
            {note ? (
                <div className="vue-notification-wrapper" data-id="0" style={{ transition: '300ms' }}>
                    <div className={`vue-notification-template vue-notification ${note.type || 'success'}`}>
                        {note.title ? <div className="notification-title">{note.title}</div> : null}
                        <div className="notification-content">{note.text}</div>
                    </div>
                </div>
            ) : null}
        </span>
    </div>
);

/**
 * vue-js-modal 1.x. Положение — как считает сам vue-js-modal: по центру по
 * горизонтали и на pivotY высоты окна по вертикали.
 */
export function VModal({ name, width, pivotY = 0.25, scrollable = false, onClose, children }) {
    const boxRef = useRef(null);
    const [place, setPlace] = useState({ top: 0, left: 0, width });
    useLayoutEffect(() => {
        const box = boxRef.current;
        if (!box) return;
        const view = box.ownerDocument.defaultView;
        const realWidth = Math.min(width, view.innerWidth);
        const top = Math.max(0, (view.innerHeight - box.offsetHeight) * pivotY);
        setPlace({ top, left: (view.innerWidth - realWidth) / 2, width: realWidth });
    }, [width, pivotY]);
    return (
        /* scrollable — как :scrollable="true" у окна CRM: окно выше экрана
           прокручивается вместе с подложкой, а не обрезается. */
        <div className={`v--modal-overlay${scrollable ? ' scrollable' : ''}`} aria-expanded="true" data-modal={name}>
            <div
                className="v--modal-background-click"
                onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}
            >
                <div className="v--modal-top-right" />
                <div
                    ref={boxRef}
                    className="v--modal-box v--modal modal--translation"
                    role="dialog"
                    aria-modal="true"
                    style={{ top: place.top, left: place.left, width: place.width, height: 'auto' }}
                >
                    {children}
                </div>
            </div>
        </div>
    );
}

const two = (value) => String(value).padStart(2, '0');

/** «N минут назад» от «сейчас» тренажёра — как пишет CRM: дд.мм.гггг чч:мм. */
export const agoText = (nowMs, minutes) => {
    const at = new Date(nowMs - minutes * 60000);
    return `${two(at.getDate())}.${two(at.getMonth() + 1)}.${at.getFullYear()} ${two(at.getHours())}:${two(at.getMinutes())}`;
};

/** То же время в виде списка «Обращения»: гггг-мм-дд чч:мм:сс. */
export const agoIso = (nowMs, minutes) => {
    const at = new Date(nowMs - minutes * 60000);
    return `${at.getFullYear()}-${two(at.getMonth() + 1)}-${two(at.getDate())} `
        + `${two(at.getHours())}:${two(at.getMinutes())}:${two(at.getSeconds())}`;
};

/** «20.11.2026 12:14:47» (как world.now) → «2026-11-20 12:14:47». */
export const nowToIso = (now) => {
    const match = /^(\d{2})\.(\d{2})\.(\d{4}) (\d{2}:\d{2}(?::\d{2})?)$/.exec(String(now || ''));
    return match ? `${match[3]}-${match[2]}-${match[1]} ${match[4]}` : String(now || '');
};
