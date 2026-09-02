/* Переключатель пространств — всплывающее меню в стиле macOS.
 *
 * Что здесь решено и почему.
 *
 * 1. ОДНА дверь вместо трёх. Раньше в шапке стояли пилюля со списком и две
 *    круглые кнопки рядом — «+» и «карандаш». Действия редкие (пространство
 *    заводят раз в квартал), а место занимали всегда, и на телефоне ряд
 *    переносился. Теперь всё внутри меню: выбор сверху, действия внизу за
 *    разделителем — ровно так устроены всплывающие меню macOS.
 *
 * 2. НАСТРОИТЬ можно ЛЮБОЕ пространство, а не только открытое. Прежний
 *    «карандаш» правил текущее: чтобы поправить соседнее, надо было сначала
 *    в него переключиться, дождаться перезагрузки раздела и только потом
 *    открыть окно. Шестерёнка стоит на строке — она и говорит, какое
 *    пространство настраивается.
 *
 * 3. АРХИВ — дверь в обе стороны. Кнопка «В архив» в конструкторе была
 *    односторонней: архивное пространство пропадает из /ping и /structure
 *    (queries.spaces_for_user берёт только status='active'), и вернуть его из
 *    интерфейса было нечем — при том, что сервер это умеет (PATCH status).
 *    Архивные показываем отдельной группой и только тому, кто их и убирал.
 *
 * 4. Меню живёт в ПОРТАЛЕ. Шапка раздела прокручивается вместе со страницей и
 *    лежит внутри контейнеров с overflow; абсолютно спозиционированный список
 *    обрезался бы первым же таким предком. Тот же приём, что в IosMenu.
 */
import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import axios from 'axios';
import { Archive, Check, ChevronsUpDown, Loader2, Plus, RotateCcw, Search, Settings2 } from 'lucide-react';

import { APPLE_FONT } from '../ui/ios';
import {
    SPACE_SEARCH_THRESHOLD, filterSpaces, spaceIcon, spaceMonogram,
} from './spaceIdentity';

const MENU_WIDTH = 304;

/* Значок пространства: emoji, если выбран, иначе монограмма имени.
 * Плитка намеренно нейтральная (slate), без цвета по идентификатору: цвет,
 * выведенный из id, ничего не значит и в списке из пяти пространств читается
 * как разметка, которой нет. */
const SpaceGlyph = ({ space, size = 'md', dimmed = false }) => {
    const icon = spaceIcon(space);
    const box = size === 'sm' ? 'h-6 w-6 rounded-[7px] text-[12px]' : 'h-7 w-7 rounded-lg text-[12.5px]';
    return (
        <span
            aria-hidden="true"
            className={`grid shrink-0 place-items-center font-semibold ${box} ${
                dimmed ? 'bg-slate-100 text-slate-400' : 'bg-slate-200/70 text-slate-600'
            }`}
        >
            {icon || spaceMonogram(space)}
        </span>
    );
};

export default function WikiSpaceSwitch({
    spaces = [], value, onChange, cards = [], canManage = false,
    onCreate, onEdit, onRestored, base, headers, showToast,
}) {
    const [open, setOpen] = useState(false);
    const [coords, setCoords] = useState(null);
    const [query, setQuery] = useState('');
    const [cursor, setCursor] = useState(-1);
    const [archived, setArchived] = useState(null);   // null — ещё не спрашивали
    const [restoring, setRestoring] = useState(0);
    const btnRef = useRef(null);
    const popRef = useRef(null);
    const searchRef = useRef(null);

    const current = spaces.find((sp) => sp.id === value) || spaces[0] || null;
    const searchable = spaces.length >= SPACE_SEARCH_THRESHOLD;
    const shown = useMemo(
        () => (searchable ? filterSpaces(spaces, query) : spaces),
        [spaces, query, searchable],
    );

    /* Полная карточка для конструктора: в /ping лежит только то, что нужно
       шапке (имя, значок, тумблеры), без списка отделов. Карточки приходят из
       /structure, а архивные — из /spaces; берём первую попавшуюся, а на
       крайний случай — саму строку списка, чтобы окно всё равно открылось. */
    const cardOf = useCallback(
        (space) => cards.find((c) => c.id === space.id)
            || (archived || []).find((c) => c.id === space.id)
            || space,
        [cards, archived],
    );

    const place = useCallback(() => {
        const el = btnRef.current;
        if (!el) return;
        const rect = el.getBoundingClientRect();
        const below = window.innerHeight - rect.bottom;
        // Высоту меню знать заранее нельзя (списки разной длины), поэтому
        // разворачиваем вверх по грубому признаку «снизу меньше 260» и там же
        // ограничиваем максимум — сам список прокручивается внутри.
        const up = below < 260 && rect.top > below;
        setCoords({
            left: Math.max(8, Math.min(window.innerWidth - MENU_WIDTH - 8, Math.round(rect.left))),
            top: up ? undefined : Math.round(rect.bottom + 6),
            bottom: up ? Math.round(window.innerHeight - rect.top + 6) : undefined,
            maxHeight: Math.max(200, Math.round((up ? rect.top : below) - 16)),
        });
    }, []);

    useLayoutEffect(() => { if (open) place(); }, [open, place]);

    /* Слушатели вешаем ТОЛЬКО пока меню раскрыто: постоянный слушатель на
       документе висел бы на каждом экране раздела ради окна, которое
       открывают раз в месяц. */
    useEffect(() => {
        if (!open) return undefined;
        const away = (event) => {
            if (btnRef.current?.contains(event.target)) return;
            if (popRef.current?.contains(event.target)) return;
            setOpen(false);
        };
        const onKey = (event) => {
            if (event.key !== 'Escape') return;
            setOpen(false);
            requestAnimationFrame(() => btnRef.current?.focus());
        };
        // Прокрутка закрывает, а не тащит меню за собой: шапка уезжает вверх,
        // а «приклеенное» меню осталось бы висеть над чужим содержимым.
        const onScroll = () => setOpen(false);
        document.addEventListener('mousedown', away);
        document.addEventListener('keydown', onKey);
        window.addEventListener('scroll', onScroll, true);
        window.addEventListener('resize', place);
        return () => {
            document.removeEventListener('mousedown', away);
            document.removeEventListener('keydown', onKey);
            window.removeEventListener('scroll', onScroll, true);
            window.removeEventListener('resize', place);
        };
    }, [open, place]);

    /* Архивные спрашиваем ОДИН раз и только у того, кто вправе их вернуть:
       остальным сервер ответит 403 и добавит красную строку в консоль на
       каждом открытии меню. */
    useEffect(() => {
        if (!open || !canManage || archived !== null || !base) return;
        axios.get(`${base}/spaces`, { headers })
            .then((r) => setArchived((r.data?.items || []).filter((sp) => sp.status === 'archived')))
            .catch(() => setArchived([]));
    }, [open, canManage, archived, base, headers]);

    /* Список архивных устаревает вместе со списком живых. Пространство, только
       что убранное в архив из конструктора, обязано найтись в группе «В архиве»
       при следующем же открытии меню — иначе человек, промахнувшийся кнопкой,
       увидит дверь назад только после перезахода в раздел. Раздел перечитывает
       /ping после каждой правки, и новый список — это и есть признак «спроси
       заново». */
    useEffect(() => { setArchived(null); }, [spaces]);

    useEffect(() => {
        if (open) return;
        setQuery('');
        setCursor(-1);
    }, [open]);

    /* Фокус уезжает В МЕНЮ, а не остаётся на кнопке. С полем поиска — в поле
       (меню открывают, чтобы выбрать, и первые набранные буквы не должны
       уходить в пустоту), без поля — на сам список: стрелки слушает он, а
       пока фокус на кнопке снаружи портала, до него не доходит ни одно
       нажатие, и клавиатура молча не работает. */
    useEffect(() => {
        if (!open) return;
        requestAnimationFrame(() => {
            if (searchable) searchRef.current?.focus();
            else popRef.current?.focus();
        });
    }, [open, searchable]);

    const pick = (id) => { setOpen(false); if (id !== value) onChange?.(id); };

    const edit = (space) => { setOpen(false); onEdit?.(cardOf(space)); };

    const restore = (space) => {
        setRestoring(space.id);
        axios.patch(`${base}/spaces/${space.id}`, { status: 'active' }, { headers })
            .then(() => {
                showToast?.(`Пространство «${space.name}» возвращено из архива`, 'success');
                setArchived((list) => (list || []).filter((sp) => sp.id !== space.id));
                setOpen(false);
                /* Возвращённое пространство сразу становится текущим — его и
                   доставали, чтобы в нём работать. Список приходит из /ping,
                   поэтому перечитать его обязан раздел: без этого строка не
                   появится в меню до следующего захода. */
                onRestored?.(space.id);
            })
            .catch((error) => showToast?.(
                error?.response?.data?.error || 'Не удалось вернуть пространство', 'error'))
            .finally(() => setRestoring(0));
    };

    /* Клавиатура. Стрелки ходят по строкам ВЫБОРА — по тому, ради чего меню и
       открыли; шестерёнка и «Новое пространство» достаются табом, как и любая
       кнопка. Роль combobox здесь была бы враньём: это меню, а не поле ввода. */
    const onListKey = (event) => {
        if (!shown.length) return;
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            setCursor((prev) => {
                const step = event.key === 'ArrowDown' ? 1 : -1;
                const next = prev < 0 ? (step > 0 ? 0 : shown.length - 1) : prev + step;
                return (next + shown.length) % shown.length;
            });
            return;
        }
        if (event.key === 'Home' || event.key === 'End') {
            event.preventDefault();
            setCursor(event.key === 'Home' ? 0 : shown.length - 1);
            return;
        }
        if (event.key === 'Enter' && cursor >= 0 && shown[cursor]) {
            event.preventDefault();
            pick(shown[cursor].id);
        }
    };

    if (!current) return null;

    // Выбирать не из чего и настраивать нечем — тогда это подпись, а не
    // управление: кнопка, которая ничего не делает, врёт о своих намерениях.
    const inert = spaces.length < 2 && !canManage;

    const trigger = (
        <button
            ref={btnRef}
            type="button"
            disabled={inert}
            onClick={() => setOpen((x) => !x)}
            aria-haspopup="menu"
            aria-expanded={open}
            aria-label={`Пространство: ${current.name}`}
            className={`flex max-w-[260px] items-center gap-2 rounded-xl bg-white py-1.5 pl-1.5 pr-2.5 text-[13px] font-semibold text-slate-800 ring-1 ring-slate-200/80 transition ${
                inert
                    ? 'cursor-default'
                    : 'shadow-[0_1px_2px_rgba(15,23,42,0.05)] hover:bg-slate-50 active:scale-[0.98]'
            } ${open ? 'bg-slate-50 ring-slate-300' : ''}`}
        >
            <SpaceGlyph space={current} size="sm" />
            <span className="min-w-0 truncate">{current.name}</span>
            {!inert && <ChevronsUpDown size={13} className="shrink-0 text-slate-400" />}
        </button>
    );

    return (
        <>
            {trigger}

            {open && coords && createPortal(
                <div
                    ref={popRef}
                    role="menu"
                    tabIndex={-1}
                    aria-label="Пространства вики"
                    onKeyDown={onListKey}
                    style={{
                        position: 'fixed',
                        left: coords.left,
                        top: coords.top,
                        bottom: coords.bottom,
                        width: MENU_WIDTH,
                        maxHeight: coords.maxHeight,
                        zIndex: 99999,
                        fontFamily: APPLE_FONT,
                    }}
                    className="flex flex-col overflow-hidden rounded-2xl bg-white/95 shadow-[0_18px_48px_rgba(15,23,42,0.20)] outline-none ring-1 ring-slate-200/80 backdrop-blur-xl animate-[fadeIn_.12s_ease]"
                >
                    {searchable && (
                        <div className="flex items-center gap-2 border-b border-slate-200/70 px-3 py-2">
                            <Search size={14} className="shrink-0 text-slate-400" />
                            <input
                                ref={searchRef}
                                value={query}
                                onChange={(e) => { setQuery(e.target.value); setCursor(-1); }}
                                placeholder="Поиск пространства…"
                                aria-label="Поиск пространства"
                                className="w-full bg-transparent text-[13px] text-slate-800 placeholder-slate-400 outline-none"
                            />
                        </div>
                    )}

                    <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-1.5">
                        {!shown.length && (
                            <p className="px-2.5 py-4 text-center text-[12.5px] text-slate-400">
                                Ничего не нашлось
                            </p>
                        )}

                        {shown.map((space, index) => {
                            const active = space.id === current.id;
                            return (
                                <div
                                    key={space.id}
                                    className={`group flex items-center gap-1 rounded-xl pr-1 transition ${
                                        index === cursor ? 'bg-slate-100' : 'hover:bg-slate-100/70'
                                    }`}
                                >
                                    <button
                                        type="button"
                                        role="menuitemradio"
                                        aria-checked={active}
                                        onMouseEnter={() => setCursor(index)}
                                        onClick={() => pick(space.id)}
                                        className="flex min-w-0 flex-1 items-center gap-2.5 rounded-xl px-2 py-1.5 text-left"
                                    >
                                        <SpaceGlyph space={space} />
                                        <span className="min-w-0 flex-1">
                                            <span className={`block truncate text-[13.5px] ${
                                                active ? 'font-semibold text-slate-900' : 'text-slate-800'
                                            }`}>
                                                {space.name}
                                            </span>
                                            {/* Вторая строка — только когда ей есть что
                                                сказать: пустая строка ради ровных высот
                                                делает список выше, а читать в нём нечего. */}
                                            {(space.guest_only || space.description) && (
                                                <span className="block truncate text-[11.5px] text-slate-400">
                                                    {space.guest_only ? 'Вы здесь в гостях' : space.description}
                                                </span>
                                            )}
                                        </span>
                                        <Check
                                            size={15}
                                            className={`shrink-0 text-blue-600 ${active ? '' : 'invisible'}`}
                                        />
                                    </button>

                                    {/* Шестерёнка правит ИМЕННО эту строку — не ту,
                                        что сейчас открыта.

                                        Видна ВСЕГДА, пусть и бледной. Спрятанная до
                                        наведения, она заменяла собой прежний
                                        «карандаш» в шапке — то есть отбирала
                                        единственную заметную дверь к настройкам и
                                        ничего не давала взамен: о том, что строку
                                        можно настроить, узнавал бы только тот, кто
                                        случайно поводил по ней мышью. */}
                                    {canManage && onEdit && (
                                        <button
                                            type="button"
                                            onClick={() => edit(space)}
                                            aria-label={`Настроить пространство «${space.name}»`}
                                            title="Настроить"
                                            className="grid h-7 w-7 shrink-0 place-items-center rounded-lg text-slate-300 transition hover:bg-slate-200/70 hover:text-slate-700 group-hover:text-slate-500"
                                        >
                                            <Settings2 size={14} />
                                        </button>
                                    )}
                                </div>
                            );
                        })}

                        {/* ── Архив ───────────────────────────────────────────
                            Показываем только когда он непуст: заголовок группы
                            без строк — обещание, за которым ничего нет. */}
                        {canManage && !!archived?.length && !query && (
                            <>
                                <div className="mx-2 my-1.5 h-px bg-slate-200/70" />
                                <div className="flex items-center gap-1.5 px-2.5 pb-1 pt-0.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                                    <Archive size={11} /> В архиве
                                </div>
                                {archived.map((space) => (
                                    <div
                                        key={space.id}
                                        className="group flex items-center gap-2.5 rounded-xl px-2 py-1.5 transition hover:bg-slate-100/70"
                                    >
                                        <SpaceGlyph space={space} dimmed />
                                        <span className="min-w-0 flex-1 truncate text-[13px] text-slate-400">
                                            {space.name}
                                        </span>
                                        <button
                                            type="button"
                                            onClick={() => restore(space)}
                                            disabled={restoring === space.id}
                                            className="inline-flex shrink-0 items-center gap-1 rounded-lg px-2 py-1 text-[12px] font-medium text-blue-600 transition hover:bg-blue-50 disabled:opacity-50"
                                        >
                                            {restoring === space.id
                                                ? <Loader2 size={12} className="animate-spin" />
                                                : <RotateCcw size={12} />}
                                            Вернуть
                                        </button>
                                    </div>
                                ))}
                            </>
                        )}
                    </div>

                    {canManage && onCreate && (
                        <div className="border-t border-slate-200/70 p-1.5">
                            <button
                                type="button"
                                role="menuitem"
                                onClick={() => { setOpen(false); onCreate(); }}
                                className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-[13.5px] text-slate-800 transition hover:bg-slate-100"
                            >
                                <Plus size={15} className="text-slate-400" />
                                Новое пространство
                            </button>
                        </div>
                    )}
                </div>,
                document.body,
            )}
        </>
    );
}
