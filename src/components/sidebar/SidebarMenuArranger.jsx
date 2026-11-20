/* Список разделов сайдбара в личной раскладке: свой порядок, папки и режим
 * правки. Стоит ВНУТРИ <ul> меню и получает его пункты детьми — ролевые ветки
 * App.jsx остаются как были.
 *
 * Строки отдаются ПЛОСКО, прямыми детьми <ul>: и пункты, и шапка папки, и её
 * разделы. Так на них работают все готовые стили строки (`> li > button`,
 * рельс, тёмная тема, шторка телефона) и проход paintSidebarMenu, который
 * красит карточки и держит поиск. Раздел папки — та же строка с отступом
 * (data-sb-depth), свёрнутая папка прячет свои строки (data-sb-folded — его
 * ставит этот компонент, а не разметка: им же управляет анимация).
 *
 * ПРАВКА (третья редакция владельца 20.11.2026): по «Изменить меню» меню
 * остаётся как было, страница уходит под стекло, у строк появляются ручки.
 * Разделы перетаскивают; подержать раздел над другим — папка из двух, как на
 * iPhone (сразу с полем имени); над папкой — в папку; отпустить заметно ниже
 * последнего раздела — новая карточка. «Обнулить» (на панели справа) уносит
 * все разделы на панель, и меню собирают заново, перетаскивая их обратно
 * (или щелчком — в конец). «Готово» — оставшееся на панели перелетает в конец
 * меню; «Отменить» — всё возвращается на прежние места.
 *
 * Движение — как в iOS/macOS: перетаскивают «парящую» строку (она ходит между
 * сайдбаром и панелью поверх всего), строки сайдбара расступаются, а на месте,
 * куда она ляжет, карточка держит пустую строку; любая перестановка доезжает
 * до нового места одним ходом — FLIP по КЛЮЧУ раздела, поэтому раздел
 * перелетает и между сайдбаром и панелью; массовый перелёт идёт каскадом с
 * лёгкой пружиной, исчезающая папка сворачивается. */
import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal, flushSync } from 'react-dom';
import { ChevronRight, Folder, FolderOpen, Menu as GripLines, RotateCcw } from 'lucide-react';
import { itemBadgeOf, itemIconOf, itemIsActive, readMenu } from './sidebarMenuElements.js';
import { arrangeRows, folderRef, layoutItemKeys, MAX_FOLDERS, mergeLayout, moveEntry, sameLayout } from './sidebarLayoutModel.js';
import {
    BLOCK_GAP_PX,
    compactBottom,
    compactRows,
    dropTarget,
    gapTop,
    keyboardGap,
    pointerZone,
    rowShifts,
    snapGapForFolder,
    unitHeight,
} from './sidebarDragMath.js';
import { sidebarLayoutStore as store, useSidebarLayout } from './sidebarLayoutStore.js';
import { foldMotion } from '../../utils/foldMotion.js';
import './sidebarLayout.css';

const ICON_STYLE = { width: '1em', height: '1em' };
const MOVE_MS = 280;
const MOVE_EASING = 'cubic-bezier(0.2, 0.8, 0.2, 1)';
/* Перелёт между сайдбаром и панелью — дольше и с лёгким «доводом», как у
   пружины iOS с затуханием около 0,85: строка чуть проскакивает и садится. */
const FLY_MS = 560;
const FLY_EASING = 'cubic-bezier(0.24, 1.12, 0.36, 1)';
// Каскад: каждая следующая строка стартует чуть позже, весь каскад — не длиннее 0,34 с.
const CASCADE_STEP_MS = 12;
const CASCADE_MAX_MS = 340;
const DRAG_START_PX = 4;
const EDGE_PX = 44;
const EDGE_STEP_PX = 14;
// Замер для FLIP старше этого не проигрывается: строки уже сто раз сдвинулись.
const FLIP_TTL_MS = 900;
/* Сколько подержать раздел над серединой другого, чтобы вышла папка: меньше —
   папки выходили бы сами, пока строку просто несут мимо; больше — ждать. */
const STACK_DWELL_MS = 380;
// Исчезающая строка (опустевшая папка) сворачивается по высоте.
const VANISH_MS = 240;

/* Цвета счётчика — те же классы, что у счётчиков самих пунктов меню. Сумма
   в папке берёт самый «срочный» цвет из её разделов. */
const BADGE_CLASS = {
    rose: 'bg-rose-500 text-white',
    orange: 'bg-orange-500 text-white',
    amber: 'bg-amber-500 text-white',
    white: 'bg-white/90 text-blue-700',
};
const BADGE_RANK = { rose: 0, orange: 1, amber: 2, white: 3 };

const reduceMotion = () => (
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
        ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
        : false
);

const isShown = (el) => el.getClientRects().length > 0;

/* Видимые строки списка с их местом в содержимом (offsetTop не видит
   transform — середины строк не дрожат, пока строки едут). Подсказка
   «перетащите сюда» и пустая строка на месте броска строками не считаются. */
const isRowNode = (el) => (el.tagName === 'LI' || el.tagName === 'HR') && el.dataset.sbKind !== 'hint';
const readGeometry = (list) => Array.from(list.children).filter((el) => isShown(el) && isRowNode(el)).map((el) => {
    const data = el.dataset;
    const kind = data.sbKind || (el.tagName === 'HR' ? 'hr' : 'item');
    return {
        el,
        kind,
        entry: kind === 'folder' ? folderRef(data.sbFolderId) : (data.sbKey || null),
        folder: data.sbFolder || null,
        id: data.sbFolderId || null,
        open: data.sbOpen === '1',
        block: Number(data.sbBlock || 0),
        fixed: data.sbFixed === '1',
        top: el.offsetTop,
        height: el.offsetHeight,
    };
});

// Папку поднимают вместе с её раскрытыми разделами — одним куском.
const unitAt = (geo, index) => {
    let end = index;
    const head = geo[index];
    if (head.kind === 'folder') {
        while (end + 1 < geo.length && geo[end + 1].folder === head.id) end += 1;
    }
    return { start: index, end };
};

// Без поднятого куска (раздел пришёл с панели): строки стоят где стоят.
const restingRows = (geo) => geo.map((row, index) => ({ ...row, index, ctop: row.top }));

const scrollParentOf = (el) => {
    for (let node = el; node && node !== document.body; node = node.parentElement) {
        const { overflowY } = window.getComputedStyle(node);
        if (/(auto|scroll)/.test(overflowY) && node.scrollHeight > node.clientHeight + 1) return node;
    }
    return null;
};

const contains = (rect, x, y, pad = 0) => (
    rect && x >= rect.left - pad && x <= rect.right + pad && y >= rect.top - pad && y <= rect.bottom + pad
);

const settle = (animation) => animation.finished.then(() => animation, () => animation);

/* Строки с ключом раздела — и в сайдбаре, и на панели. По ключу FLIP узнаёт
   раздел, перелетевший из одного места в другое: это разные узлы DOM. */
const keyedRows = (list, palette) => {
    const out = [];
    if (list) {
        for (const el of list.children) if (el.dataset.sbKey && isShown(el)) out.push([el, 'list']);
    }
    palette?.querySelectorAll('li[data-sb-key]').forEach((el) => {
        if (isShown(el)) out.push([el, 'palette']);
    });
    return out;
};

/* Замер «до»: место каждой строки, а у строк сайдбара ещё и отступ подписи
   (раздел уходит в папку и из неё — отступ доезжает вместе со строкой) и
   копия шапки папки: папка может исчезнуть (из неё унесли последний раздел),
   и сворачиваться тогда будет её копия. */
const captureKeyed = (list, palette) => {
    const rects = new Map();
    for (const [el, where] of keyedRows(list, palette)) {
        const rect = el.getBoundingClientRect();
        const record = { top: rect.top, left: rect.left, width: rect.width, height: rect.height, where };
        if (where === 'list') {
            const button = el.firstElementChild;
            if (button) record.pad = window.getComputedStyle(button).paddingLeft;
            // Папку, которую удаляют «минусом», уже свернули на месте — копия ей не нужна.
            if (el.dataset.sbKind === 'folder' && !el.dataset.sbGone) record.clone = el.cloneNode(true);
        }
        rects.set(el.dataset.sbKey, record);
    }
    return rects;
};

/* Строка, которой больше нет (опустевшая папка), не пропадает рывком: её
   копия на прежнем месте сворачивается по высоте, пока строки под ней
   поднимаются. Копия — в такой же обёртке списка, чтобы стили строки
   достались ей как есть. */
const vanishRow = (clone, was) => {
    const shell = document.createElement('ul');
    shell.className = 'sidebar-menu-scroll sb-vanish';
    shell.setAttribute('aria-hidden', 'true');
    shell.style.top = `${was.top}px`;
    shell.style.left = `${was.left}px`;
    shell.style.width = `${was.width}px`;
    shell.style.height = `${was.height}px`;
    clone.removeAttribute('data-sb-key');
    shell.appendChild(clone);
    document.body.appendChild(shell);
    settle(shell.animate([
        { height: `${was.height}px`, opacity: 1, transform: 'none' },
        { height: '0px', opacity: 0, transform: 'scale(0.96)' },
    ], { duration: VANISH_MS, easing: MOVE_EASING, fill: 'forwards' })).then(() => shell.remove());
};

const cascadeDelay = (index) => Math.min(index * CASCADE_STEP_MS, CASCADE_MAX_MS);

// Строка, которой не было (новая папка, вернувшаяся ⌘Z): в сайдбаре растёт по высоте.
const growIn = (el, delay, height = el.offsetHeight) => {
    const motion = foldMotion(0, height, 0);
    if (!motion) return;
    el.style.overflow = 'hidden';
    settle(el.animate(motion.frames, { duration: motion.duration, easing: motion.easing, delay, fill: 'backwards' })).then(() => {
        el.style.overflow = '';
    });
};

// …а на панели проявляется, чуть вырастая.
const popIn = (el, delay) => {
    el.animate([
        { opacity: 0, transform: 'scale(0.92)' },
        { opacity: 1, transform: 'none' },
    ], { duration: 320, easing: FLY_EASING, delay, fill: 'backwards' });
};

/* СНАЧАЛА ВСЕ ЗАМЕРЫ, ПОТОМ ВСЕ ДВИЖЕНИЯ. Замер строки после того, как
   соседняя уже поехала, заставлял браузер пересчитывать страницу заново на
   каждой строке — на полусотне строк это кадр в треть секунды ровно в миг
   броска. */
const playKeyed = (list, palette, flip) => {
    if (!flip || Date.now() - flip.at > FLIP_TTL_MS || reduceMotion()) return;
    const plan = keyedRows(list, palette).map(([el, where]) => {
        const was = flip.rects.get(el.dataset.sbKey);
        if (!was) return { el, where, was: null, height: where === 'list' ? el.offsetHeight : 0 };
        // Ушёл в папку или из неё — отступ подписи доезжает тем же ходом.
        const button = where === 'list' && was.where === 'list' ? el.firstElementChild : null;
        return {
            el,
            where,
            was,
            now: el.getBoundingClientRect(),
            button,
            pad: button && was.pad ? window.getComputedStyle(button).paddingLeft : null,
        };
    });
    const seen = new Set(plan.map(({ el }) => el.dataset.sbKey));
    let cascade = 0;
    for (const { el, where, was, now, button, pad, height } of plan) {
        if (!was) {
            const delay = flip.cascade ? cascadeDelay(cascade++) : 0;
            if (where === 'list') growIn(el, delay, height);
            else popIn(el, delay);
            continue;
        }
        const dx = was.left - now.left;
        const dy = was.top - now.top;
        // Положенная из рук строка садится коротко — она уже над своим местом.
        const fromGhost = was.where === 'ghost';
        const across = was.where !== where;
        if (fromGhost && where === 'list') {
            el.classList.add('sb-landing');
            setTimeout(() => el.classList.remove('sb-landing'), 340);
        }
        if (pad && pad !== was.pad) {
            button.animate([{ paddingLeft: was.pad }, { paddingLeft: pad }], { duration: MOVE_MS, easing: MOVE_EASING });
        }
        if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) continue;
        if (across && !fromGhost) {
            flyAcross(el, was, now, flip.cascade ? cascadeDelay(cascade++) : 0);
            continue;
        }
        el.animate([
            { transform: `translate(${dx}px, ${dy}px)` },
            { transform: 'none' },
        ], {
            duration: fromGhost ? 320 : MOVE_MS,
            easing: fromGhost ? FLY_EASING : MOVE_EASING,
            fill: 'backwards',
        });
    }
    flip.rects.forEach((was, key) => {
        if (!seen.has(key) && was.clone) vanishRow(was.clone, was);
    });
};

/* Перелёт между сайдбаром и панелью рисует «летун» — копия строки поверх
   всего. Сама строка лететь через экран не может: и список сайдбара, и тело
   панели прокручиваются и обрезают всё, что за их краем, — строка была бы
   видна только у самой посадки. Настоящая строка ждёт невидимой и проявляется
   под летуном в миг посадки, летун тает. */
const flyAcross = (el, was, now, delay) => {
    const flyer = document.createElement('div');
    flyer.className = `sb-flyer${el.closest('.sb-palette') ? ' is-card' : ''}`;
    flyer.style.width = `${now.width}px`;
    flyer.style.height = `${now.height}px`;
    const tile = document.createElement('span');
    tile.className = 'sb-ghost-tile';
    const icon = el.querySelector(':scope > button > svg');
    if (icon) tile.appendChild(icon.cloneNode(true));
    const text = document.createElement('span');
    text.className = 'sb-ghost-label';
    text.textContent = el.dataset.sbKey || '';
    flyer.append(tile, text);
    document.body.appendChild(flyer);
    el.style.visibility = 'hidden';
    /* Строка меню и карточка панели одного размера (обычно так и есть) — летун
       только сдвигается: это видеокарта, без перерасчёта страницы на каждом
       кадре. Разного (маленькое окно) — меняет размер в полёте. */
    const resize = Math.abs((was.width || now.width) - now.width) > 0.5 || Math.abs((was.height || now.height) - now.height) > 0.5;
    const flight = flyer.animate(resize ? [
        {
            transform: `translate3d(${was.left}px, ${was.top}px, 0)`,
            width: `${was.width || now.width}px`,
            height: `${was.height || now.height}px`,
        },
        { transform: `translate3d(${now.left}px, ${now.top}px, 0)`, width: `${now.width}px`, height: `${now.height}px` },
    ] : [
        { transform: `translate3d(${was.left}px, ${was.top}px, 0)` },
        { transform: `translate3d(${now.left}px, ${now.top}px, 0)` },
    ], { duration: FLY_MS, easing: FLY_EASING, delay, fill: 'both' });
    settle(flight).then(() => {
        el.style.visibility = '';
        settle(flyer.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 140, easing: 'ease-out', fill: 'forwards' }))
            .then(() => flyer.remove());
    });
};

/* «Парящая» строка — своя, поверх всего (портал в body): строку сайдбара
   нельзя унести за край его списка (overflow), а раздел ходит и на панель.
   Вид — та же строка: плитка значка (копия того же svg) и подпись. */
const makeGhost = (sourceEl, label, rect) => {
    const ghost = document.createElement('div');
    ghost.className = 'sb-ghost';
    ghost.style.width = `${rect.width}px`;
    ghost.style.height = `${rect.height}px`;
    ghost.style.transform = `translate3d(${rect.left}px, ${rect.top}px, 0)`;
    const card = document.createElement('div');
    card.className = 'sb-ghost-card';
    const tile = document.createElement('span');
    tile.className = 'sb-ghost-tile';
    const icon = sourceEl.querySelector(':scope > button > svg');
    if (icon) tile.appendChild(icon.cloneNode(true));
    const text = document.createElement('span');
    text.className = 'sb-ghost-label';
    text.textContent = label;
    card.append(tile, text);
    ghost.append(card);
    document.body.appendChild(ghost);
    requestAnimationFrame(() => card.classList.add('is-lifted'));
    return ghost;
};

/* plus — у раздела стоит «99+», то есть число не меньше показанного. */
const badgeLabel = (total, plus, cap) => (total > cap || (plus && total >= cap) ? `${cap}+` : String(total));

/* Счётчики разделов свёрнутой папки — суммой на её шапке, как у папки на
   экране «Домой»: иначе новая задача внутри свёрнутой папки не видна вовсе. */
const folderBadge = (members) => {
    let total = 0;
    let plus = false;
    let tone = null;
    for (const element of members) {
        const badge = itemBadgeOf(element);
        if (!badge) continue;
        total += badge.count;
        plus = plus || badge.plus;
        if (!tone || BADGE_RANK[badge.tone] < BADGE_RANK[tone]) tone = badge.tone;
    }
    return total > 0 ? { total, plus, tone: tone || 'rose' } : null;
};

function FolderRow({ row, open, editing, renaming, confirming, members, onToggle, onRemove, handlers }) {
    const activeInside = !open && members.some(itemIsActive);
    const badge = open ? null : folderBadge(members);
    const badgeClass = badge ? BADGE_CLASS[badge.tone] || BADGE_CLASS.rose : '';
    const Icon = open ? FolderOpen : Folder;
    const cancelledRef = useRef(false);
    return (
        <li
            data-sb-key={row.entry}
            data-sb-kind="folder"
            data-sb-folder-id={row.id}
            data-sb-block={row.block}
            data-sb-open={open ? '1' : '0'}
            data-sb-search={row.name}
            data-sb-confirm={confirming ? '1' : undefined}
        >
            <button
                type="button"
                onClick={() => onToggle(row.id)}
                aria-expanded={open}
                title={row.name}
                className={`sb-folder-button relative w-full text-left py-3 px-4 rounded-lg hover:bg-blue-700 transition-all duration-200 flex items-center gap-3 ${activeInside ? 'sb-folder-has-active' : ''}`}
            >
                <Icon className="inline-block align-middle" style={ICON_STYLE} aria-hidden="true" />
                {badge && (
                    <span className={`sidebar-surveys-collapsed-badge inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 rounded-full ${badgeClass} text-[10px] font-semibold leading-none`}>
                        {badgeLabel(badge.total, badge.plus, 9)}
                    </span>
                )}
                <span className="sidebar-text inline-flex items-center gap-2">
                    <span className="sb-folder-name">{row.name}</span>
                    {badge && (
                        <span className={`inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 rounded-full ${badgeClass} text-[10px] font-semibold leading-none`}>
                            {badgeLabel(badge.total, badge.plus, 99)}
                        </span>
                    )}
                </span>
                <ChevronRight className="sb-folder-chevron inline-block align-middle ml-auto sidebar-text" style={ICON_STYLE} aria-hidden="true" />
            </button>
            {editing && (
                <span
                    className="sb-edit-overlay sb-edit-overlay--folder"
                    tabIndex={0}
                    aria-label={`Папка «${row.name}»: переместить — ⌥↑ ⌥↓, переименовать — щелчок`}
                    {...handlers}
                >
                    <button
                        type="button"
                        className={`sb-folder-delete ${confirming ? 'is-confirming' : ''}`}
                        data-sb-nodrag=""
                        aria-label={`Удалить папку «${row.name}»`}
                        onClick={(e) => {
                            e.stopPropagation();
                            store.askDelete(row.id);
                        }}
                    >
                        <span aria-hidden="true" />
                    </button>
                    {renaming && (
                        <input
                            className="sb-rename-input"
                            data-sb-nodrag=""
                            defaultValue={row.name}
                            maxLength={40}
                            aria-label="Название папки"
                            autoFocus
                            onFocus={(e) => {
                                /* Флаг Escape — только для этого поля: снятое по Escape
                                   поле уходит без blur, и флаг иначе дожил бы до
                                   следующего переименования и съел бы новое имя. */
                                cancelledRef.current = false;
                                e.target.select();
                            }}
                            onPointerDown={(e) => e.stopPropagation()}
                            onClick={(e) => e.stopPropagation()}
                            onKeyDown={(e) => {
                                e.stopPropagation();
                                if (e.key === 'Enter') {
                                    e.preventDefault();
                                    e.currentTarget.blur();
                                } else if (e.key === 'Escape') {
                                    e.preventDefault();
                                    cancelledRef.current = true;
                                    store.cancelRename();
                                }
                            }}
                            onBlur={(e) => {
                                if (cancelledRef.current) {
                                    cancelledRef.current = false;
                                    return;
                                }
                                store.finishRename(row.id, e.target.value);
                            }}
                        />
                    )}
                    {!confirming && (
                        <button
                            type="button"
                            className="sb-folder-toggle"
                            data-sb-nodrag=""
                            aria-label={open ? `Свернуть «${row.name}»` : `Развернуть «${row.name}»`}
                            aria-expanded={open}
                            onClick={(e) => {
                                e.stopPropagation();
                                onToggle(row.id);
                            }}
                        >
                            <ChevronRight style={ICON_STYLE} aria-hidden="true" />
                        </button>
                    )}
                    {confirming ? (
                        <button
                            type="button"
                            className="sb-folder-delete-confirm"
                            data-sb-nodrag=""
                            onClick={(e) => {
                                e.stopPropagation();
                                onRemove(row.id);
                            }}
                        >
                            Удалить
                        </button>
                    ) : (
                        <span className="sb-grip" aria-hidden="true"><GripLines style={ICON_STYLE} /></span>
                    )}
                </span>
            )}
        </li>
    );
}


/* Панель разделов справа от сайдбара — поверх размытой страницы, порталом в
   body (решение владельца 20.11: «задняя часть размывается, карточки по
   отдельности, всё помещается в экран без прокрутки»). Каждый раздел — своя
   карточка с промежутком, сетка подбирается под окно и число разделов. */
const PALETTE_GAP = 10;
const PALETTE_EDGE = 24;
// Заголовок с пояснением (две строки) над сеткой.
const PALETTE_HEAD = 88;
const CARD_MAX_W = 280;
const CARD_MIN_W = 168;
const ROW_H = 42;
const CARD_HEIGHTS = [ROW_H, 40, 38, 36];

/* Сетка, в которую помещаются все карточки. Карточка — ростом и шириной со
   строку меню, пока помещается: тогда раздел перелетает между меню и панелью,
   не меняя размера, — одним сдвигом, который браузер отдаёт видеокарте, и
   перелёт не дёргается даже на занятой машине. Не влезло по высоте — колонок
   больше и карточки ниже, но не уже 168 px; совсем не влезло (очень маленькое
   окно) — тогда уж прокрутка. Считается при отрисовке, а не эффектом:
   карточки, на которые летят разделы, замеряют своё место в том же кадре и
   должны уже стоять на своих местах. */
export const paletteGrid = (count, view) => {
    const { width: screenW, height: screenH, sidebarRight, rowWidth = CARD_MAX_W } = view;
    const cardW = Math.max(CARD_MIN_W, Math.min(CARD_MAX_W, Math.round(rowWidth)));
    const left = sidebarRight + PALETTE_EDGE;
    const width = Math.max(CARD_MIN_W, screenW - left - PALETTE_EDGE);
    const height = Math.max(CARD_HEIGHTS[0], screenH - PALETTE_EDGE * 2 - PALETTE_HEAD);
    const n = Math.max(1, count);
    const comfortCols = Math.max(1, Math.floor((width + PALETTE_GAP) / (cardW + PALETTE_GAP)));
    for (const h of CARD_HEIGHTS) {
        const perColumn = Math.max(1, Math.floor((height + PALETTE_GAP) / (h + PALETTE_GAP)));
        const cols = Math.min(n, Math.max(Math.ceil(n / perColumn), comfortCols));
        const w = Math.floor((width - (cols - 1) * PALETTE_GAP) / cols);
        if (w >= CARD_MIN_W) return { cols, w: Math.min(w, cardW), h, left, scroll: false };
    }
    const cols = Math.max(1, Math.floor((width + PALETTE_GAP) / (CARD_MIN_W + PALETTE_GAP)));
    return { cols, w: CARD_MIN_W, h: CARD_HEIGHTS[CARD_HEIGHTS.length - 1], left, scroll: true };
};

/* Ширина строки меню — по строке, а в пустом меню (после «Обнулить») — по
   самому списку без его полей. */
const menuRowWidth = () => {
    const list = document.querySelector('.sidebar-menu-scroll');
    if (!list) return CARD_MAX_W;
    const row = Array.from(list.querySelectorAll(':scope > li[data-sb-key]')).find(isShown);
    if (row) return row.getBoundingClientRect().width;
    const style = window.getComputedStyle(list);
    return list.clientWidth - parseFloat(style.paddingLeft || '0') - parseFloat(style.paddingRight || '0');
};

const viewport = () => ({
    width: window.innerWidth,
    height: window.innerHeight,
    sidebarRight: document.querySelector('.sidebar')?.getBoundingClientRect().right || 300,
    rowWidth: menuRowWidth(),
});

/* Пояснение под заголовком панели — два жеста, которых не видно, пока о них
   не скажешь: папка — раздел на раздел, новая карточка — ниже последнего. */
const GESTURES_NOTE = 'Раздел на раздел — папка, между блоками или ниже последнего — новый блок.';

function SidebarPalette({ mode, keys, iconFor, handlers, paletteRef, onReset, canReset }) {
    const closing = mode === 'closing';
    const [, setResized] = useState(0);
    useEffect(() => {
        const onResize = () => setResized((n) => n + 1);
        window.addEventListener('resize', onResize);
        return () => window.removeEventListener('resize', onResize);
    }, []);
    /* Сетку считаем по числу разделов на момент открытия: пока человек уносит
       карточки в меню, сетка не перестраивается (не прыгает), а редеет. */
    const sizeRef = useRef(null);
    if (!sizeRef.current || keys.length > sizeRef.current.count) sizeRef.current = { count: keys.length };
    const grid = paletteGrid(sizeRef.current.count, viewport());
    return (
        <>
            <div className={`sb-scrim${closing ? ' is-closing' : ''}`} aria-hidden="true" />
            <section
                ref={paletteRef}
                className={`sb-palette${closing ? ' is-closing' : ''}${grid.scroll ? ' is-scroll' : ''}`}
                style={{ left: grid.left }}
                aria-label="Разделы меню"
            >
                <header className="sb-palette-head">
                    <div className="sb-palette-heading">
                        <h2 className="sb-palette-title">Разделы</h2>
                        <p className="sb-palette-note">
                            {keys.length
                                ? `Перетащите раздел в меню слева или нажмите на него. ${GESTURES_NOTE} Что останется здесь, после «Готово» встанет в конец меню.`
                                : `Перетаскивайте разделы в меню слева. ${GESTURES_NOTE}`}
                        </p>
                    </div>
                    {/* «Обнулить» — все разделы сюда, меню собирают заново; ⌘Z вернёт. */}
                    <button
                        type="button"
                        className="sb-palette-reset"
                        onClick={onReset}
                        disabled={closing || !canReset}
                        title="Убрать все разделы из меню сюда и собрать меню заново (⌘Z — вернуть)"
                    >
                        <RotateCcw style={ICON_STYLE} aria-hidden="true" />
                        <span>Обнулить</span>
                    </button>
                </header>
                {keys.length ? (
                    <ul
                        className="sb-palette-grid"
                        style={{ gridTemplateColumns: `repeat(${grid.cols}, ${grid.w}px)`, gridAutoRows: `${grid.h}px` }}
                    >
                        {keys.map((key) => (
                            <li key={key} className="sb-palette-row" data-sb-key={key}>
                                <button
                                    type="button"
                                    className="sb-palette-btn"
                                    title={key}
                                    aria-label={`${key}: в меню`}
                                    {...handlers}
                                >
                                    {iconFor(key)}
                                    <span className="sb-palette-label">{key}</span>
                                </button>
                            </li>
                        ))}
                    </ul>
                ) : (
                    !closing && (
                        <div className="sb-palette-empty">
                            <p>Все разделы в меню</p>
                            <p className="sb-palette-empty-note">«Обнулить» уберёт их сюда — и меню можно собрать заново</p>
                        </div>
                    )
                )}
            </section>
        </>
    );
}

export default function SidebarMenuArranger({
    children,
    listRef,
    onArranged,
    transparent,
    userId,
    apiBaseUrl,
    getHeaders,
}) {
    store.ensureUser(userId);
    const state = useSidebarLayout();
    const menu = useMemo(() => readMenu(children, { transparent }), [children, transparent]);
    const present = useMemo(() => new Set(menu.items.keys()), [menu]);
    store.setContext({ defaultBlocks: menu.defaultBlocks, present });

    const { editing } = state;
    // В правке — ровно то, что человек уже перенёс; вне правки — сохранённое, слитое с меню.
    const layout = useMemo(
        () => (editing ? state.working : mergeLayout(state.saved, menu.defaultBlocks)),
        [editing, state.working, state.saved, menu.defaultBlocks],
    );
    const rows = useMemo(() => arrangeRows(layout, present, { editing }), [layout, present, editing]);
    const groups = useMemo(() => {
        if (!editing) return [];
        const placed = layoutItemKeys(state.working);
        return menu.defaultBlocks
            .map((block, index) => ({ index, keys: block.filter((key) => present.has(key) && !placed.has(key)) }))
            .filter((group) => group.keys.length);
    }, [editing, state.working, menu.defaultBlocks, present]);
    // На панели — по одной карточке на раздел, в обычном порядке меню.
    const paletteKeys = useMemo(() => groups.flatMap((group) => group.keys), [groups]);

    /* Панель живёт, пока идёт правка, и ещё миг после — пока уезжает. Состояние
       меняется прямо в отрисовке: панель должна остаться в том же кадре, где
       правка кончилась, иначе ей было бы нечем уехать. */
    const [paletteMode, setPaletteMode] = useState(editing ? 'open' : 'off');
    if (editing && paletteMode !== 'open') setPaletteMode('open');
    if (!editing && paletteMode === 'open') setPaletteMode('closing');
    useEffect(() => {
        if (paletteMode !== 'closing') return undefined;
        const timer = setTimeout(() => setPaletteMode('off'), 420);
        return () => clearTimeout(timer);
    }, [paletteMode]);
    // Портал — только в браузере (серверная отрисовка в тестах его не умеет).
    const [mounted, setMounted] = useState(false);
    useEffect(() => {
        setMounted(true);
    }, []);

    const onArrangedRef = useRef(onArranged);
    onArrangedRef.current = onArranged;
    const repaint = useCallback(() => {
        onArrangedRef.current?.();
    }, []);

    const paletteRef = useRef(null);
    const flipRef = useRef(null);
    const folding = useRef(new Map());
    const drag = useRef(null);
    const busy = useRef(false);
    const press = useRef(null);
    const suppressClick = useRef(false);
    const refocusKey = useRef(null);
    const revealKey = useRef(null);

    useEffect(() => {
        store.connect({ userId, apiBaseUrl, getHeaders });
    }, [userId, apiBaseUrl, getHeaders]);

    /* Замер «до» для FLIP — по ключам разделов в сайдбаре и на панели. Не
       посреди перетаскивания: строки там стоят со сдвигами (так бывает, когда
       перетаскивание начали, не закрыв поле имени папки, — имя сохраняется). */
    useEffect(() => store.onBeforeChange(({ cascade } = {}) => {
        if (flipRef.current || drag.current) return;
        flipRef.current = { rects: captureKeyed(listRef.current, paletteRef.current), at: Date.now(), cascade };
    }), [listRef]);

    /* Свёрнутость папок — атрибутом на строках, после каждой отрисовки: React
       его не задаёт, и пока папка едет, атрибут держит анимация. */
    useLayoutEffect(() => {
        const list = listRef.current;
        if (!list) return;
        for (const el of list.children) {
            const id = el.dataset.sbFolder;
            if (!id) {
                if (el.dataset.sbFolded) delete el.dataset.sbFolded;
                continue;
            }
            if (folding.current.has(id)) continue;
            const folded = state.closed[id] ? '1' : '0';
            if (el.dataset.sbFolded !== folded) el.dataset.sbFolded = folded;
        }
        repaint();
        if (revealKey.current) {
            const key = revealKey.current;
            revealKey.current = null;
            Array.from(list.children).find((el) => el.dataset.sbKey === key)?.scrollIntoView({ block: 'nearest' });
        }
        const flip = flipRef.current;
        flipRef.current = null;
        if (flip) playKeyed(list, paletteRef.current, flip);
        if (refocusKey.current) {
            const key = refocusKey.current;
            refocusKey.current = null;
            const inList = Array.from(list.children).find((el) => el.dataset.sbKey === key);
            const target = inList?.querySelector('.sb-edit-overlay')
                || paletteRef.current?.querySelector(`li[data-sb-key="${CSS.escape(key)}"] > button`);
            target?.focus({ preventScroll: false });
        }
    });

    useEffect(() => {
        document.body.classList.toggle('sidebar-editing', editing);
        return () => document.body.classList.remove('sidebar-editing');
    }, [editing]);

    /* ⌘Z / Ctrl+Z — шаг назад, ⇧⌘Z / Ctrl+Y — вперёд. По коду клавиши, а не
       по букве: на русской раскладке это «я». В поле имени папки — его
       собственная отмена ввода. */
    useEffect(() => {
        if (!editing) return undefined;
        const onKey = (e) => {
            const target = e.target;
            const typing = target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable);
            const isZ = e.code === 'KeyZ' || /^[zя]$/i.test(e.key || '');
            const isY = e.code === 'KeyY';
            if ((e.metaKey || e.ctrlKey) && !e.altKey && !typing && (isZ || (isY && e.ctrlKey))) {
                e.preventDefault();
                e.stopPropagation();
                if (drag.current || busy.current) return;
                if ((isZ && e.shiftKey) || isY) store.redo();
                else store.undo();
                return;
            }
            if (e.key === 'Escape' && !typing) {
                if (drag.current) {
                    e.preventDefault();
                    endDrag(false);
                } else if (store.getState().confirmDeleteId) {
                    e.preventDefault();
                    store.cancelDelete();
                }
            }
        };
        window.addEventListener('keydown', onKey, true);
        return () => window.removeEventListener('keydown', onKey, true);
    // endDrag читает только ref'ы — пересоздавать подписку незачем.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [editing]);

    // Щелчок мимо красной «Удалить» снимает её, как в списках iOS.
    useEffect(() => {
        if (!state.confirmDeleteId) return undefined;
        const onDown = (e) => {
            if (e.target.closest?.('.sb-folder-delete, .sb-folder-delete-confirm')) return;
            store.cancelDelete();
        };
        window.addEventListener('pointerdown', onDown, true);
        return () => window.removeEventListener('pointerdown', onDown, true);
    }, [state.confirmDeleteId]);

    // Правку закрыли посреди броска (свернули панель) — строку на место.
    useEffect(() => {
        if (!editing && drag.current) endDrag(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [editing]);

    useEffect(() => () => {
        const session = drag.current;
        if (session) {
            cancelAnimationFrame(session.raf);
            session.ghost.remove();
        }
        document.body.classList.remove('sb-drag-active');
        // Список ушёл со страницы — это выход из учётки: правку не оставляем висеть.
        store.disconnect();
    }, []);

    /* ── Папка: раскрыть или свернуть ─────────────────────────────────── */
    const toggleFolder = useCallback((id) => {
        const list = listRef.current;
        if (!list) return;
        const opening = store.isClosed(id);
        const members = Array.from(list.children).filter((el) => el.dataset.sbFolder === id);
        const running = folding.current.get(id);
        // Повторный щелчок посреди движения: новый ход — с той высоты, где строки сейчас.
        const from = members.map((el) => (running ? el.getBoundingClientRect().height : null));
        const fromOpacity = members.map((el) => (running ? Number(window.getComputedStyle(el).opacity) : null));
        running?.anims.forEach((animation) => animation.cancel());
        /* Папка «в движении» — ДО обновления хранилища: отрисовка, которую оно
           вызовет, иначе успела бы спрятать строки (эффект свёрнутости), и
           сворачиваться было бы уже нечему. */
        const record = { anims: [], opening };
        folding.current.set(id, record);
        flushSync(() => store.setFolderOpen(id, opening));
        if (opening) {
            members.forEach((el) => {
                el.dataset.sbFolded = '0';
            });
            repaint();
        }
        const heights = members.map((el) => el.scrollHeight || el.offsetHeight);
        const total = heights.reduce((sum, h) => sum + h, 0);
        const motion = reduceMotion() || !members.length
            ? null
            : foldMotion(opening ? 0 : total, opening ? total : 0, opening ? 0 : 1);
        const finish = () => {
            if (folding.current.get(id) !== record) return;
            folding.current.delete(id);
            members.forEach((el) => {
                el.style.overflow = '';
                el.dataset.sbFolded = opening ? '0' : '1';
            });
            repaint();
        };
        if (!motion) {
            finish();
            return;
        }
        const anims = record.anims;
        members.forEach((el, i) => {
            const h = heights[i];
            const start = from[i] ?? (opening ? 0 : h);
            const end = opening ? h : 0;
            const startOpacity = fromOpacity[i] ?? (opening ? 0 : 1);
            el.style.overflow = 'hidden';
            anims.push(el.animate([
                { height: `${start}px`, opacity: startOpacity, offset: 0 },
                { opacity: opening ? 1 : 0, offset: opening ? 0.7 : 0.9 },
                { height: `${end}px`, opacity: opening ? 1 : 0, offset: 1 },
            ], { duration: motion.duration, easing: motion.easing, fill: 'forwards' }));
            const button = el.firstElementChild;
            if (button) {
                anims.push(button.animate([
                    { transform: `translateY(${start - h}px)` },
                    { transform: `translateY(${end - h}px)` },
                ], { duration: motion.duration, easing: motion.easing, fill: 'forwards' }));
            }
        });
        Promise.all(anims.map(settle)).then(() => {
            if (folding.current.get(id) !== record) return;
            // Сначала свёрнутость (строки уходят из раскладки), потом снять
            // удержание последнего кадра — иначе строка мелькнула бы во весь рост.
            finish();
            anims.forEach((animation) => animation.cancel());
        });
    }, [listRef, repaint]);

    const removeFolder = useCallback((id) => {
        const list = listRef.current;
        const head = list && Array.from(list.children).find((el) => el.dataset.sbKind === 'folder' && el.dataset.sbFolderId === id);
        if (!head || reduceMotion()) {
            store.deleteFolder(id);
            return;
        }
        head.style.overflow = 'hidden';
        head.dataset.sbGone = '1';
        const animation = head.animate([
            { height: `${head.offsetHeight}px`, opacity: 1 },
            { height: '0px', opacity: 0 },
        ], { duration: 200, easing: 'cubic-bezier(0.4, 0, 0.2, 1)', fill: 'forwards' });
        settle(animation).then(() => {
            flushSync(() => store.deleteFolder(id));
            animation.cancel();
        });
    }, [listRef]);

    /* ── Перетаскивание: и в сайдбаре, и с панели, и обратно ───────────── */
    const clearDrag = (session) => {
        const list = listRef.current;
        // Сначала снять переходы, потом сдвиги: строки встают на места без хода.
        list?.classList.remove('sb-dragging', 'sb-settling');
        document.body.classList.remove('sb-drag-active');
        session.geo.forEach(({ el }) => {
            el.style.transform = '';
        });
        session.sourceEls.forEach((el) => el.classList.remove('sb-drag-source'));
        session.intoEl?.classList.remove('sb-drop-into');
        session.stackEl?.classList.remove('sb-stack-target');
        session.slot?.remove();
        paletteRef.current?.classList.remove('is-drop-target');
        list?.querySelector(':scope > li.sb-drop-hint')?.classList.remove('is-target');
    };

    /* Скругления карточек, пока строку держат: её карточка уже без неё (иначе
       на месте поднятой первой строки карточка висела бы с прямым верхом), а
       пустая строка на месте броска — часть карточки, в которую строка ляжет:
       соседи по обе стороны от неё не скругляются, карточка не рвётся. */
    const roundAround = (session, slotAt) => {
        const seq = session.compact.slice();
        if (slotAt !== null && slotAt !== undefined) seq.splice(slotAt, 0, null);
        let run = [];
        const flush = () => {
            run.forEach((row, i) => {
                if (!row) return;
                row.el.classList.toggle('sidebar-run-first', i === 0);
                row.el.classList.toggle('sidebar-run-last', i === run.length - 1);
            });
            run = [];
        };
        seq.forEach((row) => {
            if (row && row.kind === 'hr') flush();
            else run.push(row);
        });
        flush();
    };

    /* Пустая строка там, куда ляжет поднятое: карточка держит для него место
       своим цветом (как список iOS), а ниже последнего раздела — пунктирная
       карточка «новый блок». Появляется на месте, дальше переезжает плавно. */
    const placeSlot = (session) => {
        const slot = session.slot;
        if (!slot) return;
        const { compact } = session;
        let top = null;
        let slotAt = null;
        let roundTop = true;
        let roundBottom = true;
        const split = session.split !== null ? compact[session.split] : null;
        if (session.overList && split) {
            // Между карточками: сразу под чертой, карточки ниже отъехали на её место.
            top = split.ctop + split.height;
        } else if (session.overList && compact.length && session.newBlock) {
            top = compactBottom(compact, session.geo) + BLOCK_GAP_PX;
        } else if (session.overList && compact.length && session.gap !== null) {
            slotAt = session.gap;
            top = gapTop(compact, slotAt, session.geo);
            const prev = compact[slotAt - 1];
            const next = compact[slotAt];
            roundTop = !prev || prev.kind === 'hr';
            roundBottom = !next || next.kind === 'hr';
        }
        roundAround(session, slotAt);
        if (top === null) {
            slot.classList.remove('is-shown');
            return;
        }
        const transform = `translate3d(0, ${top}px, 0)`;
        if (!slot.classList.contains('is-shown')) {
            /* Пустая строка возникает на своём месте, а не едет туда с верха
               списка. С пометкой important: переходы ей задаёт правило списка
               в перетаскивании — тоже important, и простой стиль его не бьёт. */
            slot.style.setProperty('transition', 'none', 'important');
            slot.style.transform = transform;
            slot.style.height = `${session.unitH}px`;
            void slot.offsetHeight;
            slot.style.removeProperty('transition');
        }
        slot.style.transform = transform;
        slot.style.height = `${session.unitH}px`;
        slot.classList.add('is-shown');
        slot.classList.toggle('is-new', Boolean(session.newBlock || split));
        slot.classList.toggle('is-muted', Boolean(session.into || session.stack));
        slot.classList.toggle('round-top', roundTop);
        slot.classList.toggle('round-bottom', roundBottom);
    };

    const updateDrag = () => {
        const session = drag.current;
        const list = listRef.current;
        if (!session || !list) return;
        const x = session.lastX;
        const y = session.lastY;
        session.ghost.style.transform = `translate3d(${x - session.grabX}px, ${y - session.grabY}px, 0)`;
        const listRect = list.getBoundingClientRect();
        const sideRect = list.closest('.sidebar')?.getBoundingClientRect() || listRect;
        // Над сайдбаром — всё левее его правой кромки (с запасом) в высоту списка.
        const overList = x <= sideRect.right + 40 && y >= listRect.top - 40 && y <= listRect.bottom + 40;
        const overPalette = !overList && session.source === 'list' && session.kind === 'item'
            && contains(paletteRef.current?.getBoundingClientRect(), x, y);
        let zone = { gap: null, into: null, center: null, newBlock: false };
        if (overList) {
            // Всё — по КУРСОРУ в координатах содержимого списка (см. sidebarDragMath.js).
            zone = pointerZone(session.compact, {
                pointerY: y - listRect.top + list.scrollTop,
                shownGap: session.gap,
                shownSplit: session.split,
                height: session.unitH,
                dragged: session.dragged,
                wasNewBlock: session.newBlock,
            });
            if (session.kind === 'folder' && zone.gap !== null) zone.gap = snapGapForFolder(session.compact, zone.gap);
        }
        /* Папка из двух — как на iPhone: курсор подержали над серединой
           раздела. Выдержку отсчитываем с той минуты, как курсор пришёл на этот
           раздел; дождаться её помогает tick — курсор при этом стоит. */
        const now = performance.now();
        // Папок уже предел — раздел на раздел просто встаёт в щель.
        if (zone.center !== null && Object.keys(store.workingLayout()?.folders || {}).length >= MAX_FOLDERS) zone.center = null;
        if (zone.center === null) {
            session.centerIndex = null;
        } else if (session.centerIndex !== zone.center) {
            session.centerIndex = zone.center;
            session.centerAt = now;
        }
        const stackRow = zone.center !== null && now - session.centerAt >= STACK_DWELL_MS
            ? session.compact[zone.center]
            : null;
        const { gap, into, newBlock } = zone;
        const split = overList ? (zone.split ?? null) : null;
        let target = null;
        if (overList) {
            const intoRow = into ? session.compact.find((row) => row.kind === 'folder' && row.id === into) : null;
            // Черта несёт номер карточки под ней: новая встаёт перед ней.
            if (split !== null) target = { newBlock: true, at: session.compact[split].block };
            else if (newBlock) target = { newBlock: true };
            else if (intoRow) target = { block: intoRow.block, folder: into, before: null };
            else target = dropTarget(session.compact, gap ?? session.compact.length, session.dragged, x - session.startX);
        }
        session.target = target;
        session.overList = overList;
        session.overPalette = overPalette;
        session.newBlock = Boolean(overList && newBlock);
        session.stack = stackRow ? stackRow.entry : null;
        if (gap !== session.gap || split !== session.split) {
            // Нет щели: строка ушла из сайдбара (дыра на её месте закрывается)
            // или раздел с панели ещё не над ним (строки стоят). Раздвинутая
            // черта — карточки под ней отъезжают на место новой.
            rowShifts(session.geo, session.compact, gap, session.unitH, split).forEach((delta, index) => {
                session.geo[index].el.style.transform = Math.abs(delta) > 0.5 ? `translate3d(0, ${delta}px, 0)` : '';
            });
            session.gap = gap;
            session.split = split;
        }
        if (session.into !== into) {
            session.intoEl?.classList.remove('sb-drop-into');
            session.intoEl = into ? session.compact.find((row) => row.kind === 'folder' && row.id === into)?.el || null : null;
            session.intoEl?.classList.add('sb-drop-into');
            session.into = into;
        }
        const stackEl = stackRow?.el || null;
        if (session.stackEl !== stackEl) {
            session.stackEl?.classList.remove('sb-stack-target');
            session.stackEl = stackEl;
            stackEl?.classList.add('sb-stack-target');
        }
        placeSlot(session);
        const card = session.ghost.firstChild;
        card.classList.toggle('is-into', Boolean(into || stackRow));
        card.classList.toggle('is-nested', Boolean(target?.folder && !into));
        card.classList.toggle('is-leaving', overPalette);
        paletteRef.current?.classList.toggle('is-drop-target', overPalette);
        list.querySelector(':scope > li.sb-drop-hint')?.classList.toggle('is-target', overList);
        /* Над сайдбаром парящий раздел — строка меню, над панелью — карточка:
           размер плавно подстраивается под место, куда он ляжет, и посадка
           выходит без скачка размера. */
        const size = overList ? session.rowSize : session.cardSize;
        if (size && (size.width !== session.shownW || size.height !== session.shownH)) {
            session.ghost.style.width = `${size.width}px`;
            session.ghost.style.height = `${size.height}px`;
            card.classList.toggle('is-card', !overList);
            session.shownW = size.width;
            session.shownH = size.height;
        }
    };

    const tick = () => {
        const session = drag.current;
        if (!session) return;
        // Курсор стоит над серединой раздела — выдержка истекла: папка.
        if (session.centerIndex !== null && !session.stack && performance.now() - session.centerAt >= STACK_DWELL_MS) {
            updateDrag();
        }
        const scroller = session.overList ? session.scroller : null;
        if (scroller) {
            const box = scroller.getBoundingClientRect();
            let step = 0;
            if (session.lastY < box.top + EDGE_PX) step = -Math.min(EDGE_STEP_PX, Math.ceil(((box.top + EDGE_PX - session.lastY) / EDGE_PX) * EDGE_STEP_PX));
            else if (session.lastY > box.bottom - EDGE_PX) step = Math.min(EDGE_STEP_PX, Math.ceil(((session.lastY - (box.bottom - EDGE_PX)) / EDGE_PX) * EDGE_STEP_PX));
            if (step) {
                const before = scroller.scrollTop;
                scroller.scrollTop = before + step;
                if (scroller.scrollTop !== before) updateDrag();
            }
        }
        session.raf = requestAnimationFrame(tick);
    };

    const beginDrag = (start) => {
        const list = listRef.current;
        if (!list || !store.getState().editing || busy.current) return false;
        const fromPalette = start.source === 'palette';
        const geo = readGeometry(list);
        const rect = start.rowEl.getBoundingClientRect();
        // Размеры строки сайдбара и карточки панели — под них подстраивается парящий раздел.
        const listRow = geo.find((row) => row.kind !== 'hr');
        const rowSize = listRow
            ? { width: listRow.el.offsetWidth, height: listRow.height }
            : { width: Math.max(0, list.clientWidth - 20), height: 42 };
        let session;
        if (fromPalette) {
            const key = start.rowEl.dataset.sbKey;
            if (!key) return false;
            session = {
                entry: key,
                label: key,
                kind: 'item',
                folder: null,
                block: 0,
                unit: null,
                compact: restingRows(geo),
                // Щель в меню — ростом со строку меню, а не с карточку панели.
                unitH: rowSize.height,
                gap: null,
                sourceEls: [start.rowEl],
            };
        } else {
            const index = geo.findIndex((row) => row.el === start.rowEl);
            if (index === -1 || geo[index].fixed || geo[index].kind === 'hr') return false;
            const unit = unitAt(geo, index);
            const head = geo[index];
            const kind = head.kind === 'folder' ? 'folder' : 'item';
            session = {
                entry: head.entry,
                label: kind === 'folder' ? (start.rowEl.dataset.sbSearch || '') : head.entry,
                kind,
                folder: head.folder,
                block: head.block,
                unit,
                compact: compactRows(geo, unit),
                unitH: unitHeight(geo, unit),
                gap: unit.start,
                sourceEls: geo.slice(unit.start, unit.end + 1).map((row) => row.el),
            };
        }
        store.cancelDelete();
        if (store.getState().renamingId) document.activeElement?.blur?.();
        const paletteCard = paletteRef.current?.querySelector('li[data-sb-key]')?.getBoundingClientRect();
        // Пустая строка на месте броска — первым узлом списка: строки рисуются поверх неё.
        const slot = document.createElement('div');
        slot.className = 'sb-slot';
        slot.setAttribute('aria-hidden', 'true');
        list.prepend(slot);
        drag.current = {
            ...session,
            rowSize,
            slot,
            centerIndex: null,
            centerAt: 0,
            stack: null,
            stackEl: null,
            newBlock: false,
            split: null,
            cardSize: paletteCard ? { width: paletteCard.width, height: paletteCard.height } : null,
            shownW: rect.width,
            shownH: rect.height,
            source: fromPalette ? 'palette' : 'list',
            dragged: { kind: session.kind, folder: session.folder, block: session.block },
            geo,
            ghost: makeGhost(start.rowEl, session.label, rect),
            sourceRect: rect,
            grabX: start.x - rect.left,
            grabY: start.y - rect.top,
            startX: start.x,
            lastX: start.x,
            lastY: start.y,
            into: null,
            intoEl: null,
            target: null,
            overList: !fromPalette,
            overPalette: false,
            pointerId: start.pointerId,
            scroller: scrollParentOf(list),
            raf: 0,
        };
        list.classList.add('sb-dragging');
        document.body.classList.add('sb-drag-active');
        drag.current.sourceEls.forEach((el) => el.classList.add('sb-drag-source'));
        suppressClick.current = true;
        updateDrag();
        drag.current.raf = requestAnimationFrame(tick);
        return true;
    };

    // Никуда не положили — парящая строка возвращается туда, откуда её взяли.
    const cancelDrag = (session) => {
        const list = listRef.current;
        busy.current = true;
        list?.classList.add('sb-settling');
        session.geo.forEach(({ el }) => {
            el.style.transform = '';
        });
        session.intoEl?.classList.remove('sb-drop-into');
        session.stackEl?.classList.remove('sb-stack-target');
        session.slot?.classList.remove('is-shown');
        paletteRef.current?.classList.remove('is-drop-target');
        const source = session.sourceEls[0];
        const back = source?.isConnected && isShown(source) ? source.getBoundingClientRect() : session.sourceRect;
        session.ghost.firstChild.classList.remove('is-lifted', 'is-into', 'is-nested', 'is-leaving');
        const animation = session.ghost.animate([
            { transform: session.ghost.style.transform },
            { transform: `translate3d(${back.left}px, ${back.top}px, 0)` },
        ], { duration: reduceMotion() ? 0 : MOVE_MS, easing: MOVE_EASING, fill: 'forwards' });
        settle(animation).then(() => {
            session.ghost.remove();
            clearDrag(session);
            busy.current = false;
            repaint();
        });
    };

    // В свёрнутую папку строка «влетает» в её шапку и гаснет.
    const flyIntoFolder = (session) => {
        const head = session.intoEl?.getBoundingClientRect();
        if (!head || reduceMotion()) return Promise.resolve();
        const toY = head.top + (head.height - session.unitH) / 2;
        return settle(session.ghost.animate([
            { transform: session.ghost.style.transform, opacity: 1 },
            { transform: `translate3d(${head.left}px, ${toY}px, 0) scale(0.6)`, opacity: 0 },
        ], { duration: 200, easing: 'cubic-bezier(0.4, 0, 1, 1)', fill: 'forwards' }));
    };

    function endDrag(commit) {
        const session = drag.current;
        if (!session) return;
        drag.current = null;
        cancelAnimationFrame(session.raf);
        const list = listRef.current;
        let action = null;
        if (commit && session.overList && session.stack) {
            action = 'stack';
        } else if (commit && session.overList && session.target) {
            if (session.source === 'palette') {
                action = 'place';
            } else {
                /* «Бросили туда же» — по тому, что видно: щель на старом месте и
                   та же папка (или та же верхняя ступень). */
                const samePlace = !session.into
                    && !session.newBlock
                    && session.split === null
                    && session.gap === session.unit.start
                    && (session.target.folder ?? null) === (session.folder ?? null);
                const working = store.workingLayout();
                if (!samePlace && working && !sameLayout(moveEntry(working, session.entry, session.target), working)) action = 'move';
            }
        } else if (commit && session.overPalette) {
            action = 'unplace';
        }
        if (!action || !list) {
            cancelDrag(session);
            return;
        }
        const run = () => {
            if (action === 'stack') store.stack(session.stack, session.entry);
            else if (action === 'place') store.place(session.entry, session.target);
            else if (action === 'move') store.move(session.entry, session.target);
            else store.unplace(session.entry);
        };
        if (action !== 'unplace' && session.into && store.isClosed(session.into)) {
            busy.current = true;
            flyIntoFolder(session).then(() => {
                session.ghost.remove();
                clearDrag(session);
                flushSync(run);
                busy.current = false;
            });
            return;
        }
        /* Положили: строка доезжает с того места, где парила, — замер сейчас,
           место раздела в замере — место парящей строки. */
        const ghostRect = session.ghost.getBoundingClientRect();
        flipRef.current = { rects: captureKeyed(list, paletteRef.current), at: Date.now(), cascade: false };
        flipRef.current.rects.set(session.entry, { top: ghostRect.top, left: ghostRect.left, where: 'ghost' });
        session.ghost.remove();
        clearDrag(session);
        flushSync(run);
    }

    const onPointerDown = (e) => {
        if (e.button !== 0 || drag.current || busy.current || !store.getState().editing) return;
        if (e.target.closest('[data-sb-nodrag]')) return;
        const inPalette = Boolean(e.currentTarget.closest('.sb-palette'));
        const rowEl = e.currentTarget.closest('li');
        if (!rowEl || rowEl.dataset.sbFixed === '1') return;
        const touch = e.pointerType === 'touch' || e.pointerType === 'pen';
        // В сайдбаре пальцем — только за «ручку»: прокрутка листа остаётся родной.
        if (touch && !inPalette && !e.target.closest('.sb-grip')) return;
        suppressClick.current = false;
        const start = { pointerId: e.pointerId, x: e.clientX, y: e.clientY, rowEl, source: inPalette ? 'palette' : 'list' };
        try {
            e.currentTarget.setPointerCapture(e.pointerId);
        } catch (err) {
            /* без захвата строка всё равно поедет, пока курсор над ней */
        }
        if (touch) {
            e.preventDefault();
            beginDrag(start);
        } else {
            press.current = start;
        }
    };

    const onPointerMove = (e) => {
        const session = drag.current;
        if (session && e.pointerId === session.pointerId) {
            session.lastX = e.clientX;
            session.lastY = e.clientY;
            updateDrag();
            return;
        }
        const start = press.current;
        if (!start || start.pointerId !== e.pointerId) return;
        if (Math.hypot(e.clientX - start.x, e.clientY - start.y) < DRAG_START_PX) return;
        press.current = null;
        if (beginDrag(start)) {
            drag.current.lastX = e.clientX;
            drag.current.lastY = e.clientY;
            updateDrag();
        }
    };

    const onPointerUp = (e) => {
        press.current = null;
        if (drag.current && e.pointerId === drag.current.pointerId) endDrag(true);
    };

    const onPointerCancel = (e) => {
        press.current = null;
        if (drag.current && e.pointerId === drag.current.pointerId) endDrag(false);
    };

    const onOverlayClick = (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (suppressClick.current) {
            suppressClick.current = false;
            return;
        }
        const rowEl = e.currentTarget.closest('li');
        // Щелчок по папке в режиме правки — её имя, как «Изменить ящик» в Почте iOS.
        if (rowEl?.dataset.sbKind === 'folder') store.startRename(rowEl.dataset.sbFolderId);
    };

    /* «Обнулить»: все разделы каскадом улетают на панель. Посреди броска или
       перелёта — нет: строки в этот миг стоят не на своих местах. */
    const onReset = () => {
        if (busy.current || drag.current) return;
        store.reset();
    };

    /* С панели щелчком — в конец меню. Список прокручивается к новой строке
       до замера, чтобы раздел долетел туда, где его видно. */
    const placeAtEnd = (key) => {
        if (!key || busy.current || drag.current) return;
        revealKey.current = key;
        store.place(key);
    };

    const onPaletteClick = (e) => {
        e.preventDefault();
        if (suppressClick.current) {
            suppressClick.current = false;
            return;
        }
        placeAtEnd(e.currentTarget.closest('li')?.dataset.sbKey);
    };

    const onPaletteKeyDown = (e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        e.preventDefault();
        const row = e.currentTarget.closest('li');
        const rows = Array.from(paletteRef.current?.querySelectorAll('li[data-sb-key]') || []);
        const next = rows[rows.indexOf(row) + 1] || rows[rows.indexOf(row) - 1];
        refocusKey.current = next ? next.dataset.sbKey : row?.dataset.sbKey;
        placeAtEnd(row?.dataset.sbKey);
    };

    /* Клавиатура в сайдбаре: ⌥↑/⌥↓ — переложить строку на шаг, ↑/↓ — к
       соседней строке, Delete — раздел обратно на панель (папку — удалить, с
       подтверждением). */
    const onOverlayKeyDown = (e) => {
        const list = listRef.current;
        const rowEl = e.currentTarget.closest('li');
        if (!list || !rowEl || e.target !== e.currentTarget) return;
        if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
            e.preventDefault();
            const direction = e.key === 'ArrowUp' ? -1 : 1;
            if (!e.altKey) {
                const overlays = Array.from(list.querySelectorAll(':scope > li .sb-edit-overlay')).filter((el) => isShown(el) && el.tabIndex >= 0);
                overlays[overlays.indexOf(e.currentTarget) + direction]?.focus();
                return;
            }
            const geo = readGeometry(list);
            const index = geo.findIndex((row) => row.el === rowEl);
            if (index === -1 || geo[index].fixed) return;
            const unit = unitAt(geo, index);
            const compact = compactRows(geo, unit);
            const head = geo[index];
            const dragged = { kind: head.kind === 'folder' ? 'folder' : 'item', entry: head.entry, folder: head.folder, block: head.block };
            const working = store.workingLayout();
            if (!working) return;
            /* Ближайшее место, которое что-то меняет: папку не пускают внутрь
               раскрытой папки — её щель отскакивает к краю, и шаг растёт, пока
               не перешагнёт эту папку целиком. */
            for (let step = 1; step <= compact.length; step += 1) {
                let gap = keyboardGap(compact, unit, direction * step);
                if (dragged.kind === 'folder') gap = snapGapForFolder(compact, gap);
                const target = dropTarget(compact, gap, dragged, 0);
                if (!sameLayout(moveEntry(working, dragged.entry, target), working)) {
                    refocusKey.current = rowEl.dataset.sbKey;
                    store.move(dragged.entry, target);
                    return;
                }
            }
            return;
        }
        if (rowEl.dataset.sbKind === 'folder') {
            if (e.key === 'Enter') {
                e.preventDefault();
                store.startRename(rowEl.dataset.sbFolderId);
            } else if (e.key === ' ') {
                e.preventDefault();
                toggleFolder(rowEl.dataset.sbFolderId);
            } else if (e.key === 'Delete' || e.key === 'Backspace') {
                e.preventDefault();
                store.askDelete(rowEl.dataset.sbFolderId);
            }
        } else if ((e.key === 'Delete' || e.key === 'Backspace') && rowEl.dataset.sbKey) {
            e.preventDefault();
            const overlays = Array.from(list.querySelectorAll(':scope > li .sb-edit-overlay')).filter((el) => isShown(el));
            const at = overlays.indexOf(e.currentTarget);
            const next = (overlays[at + 1] || overlays[at - 1])?.closest('li');
            refocusKey.current = next ? next.dataset.sbKey : null;
            store.unplace(rowEl.dataset.sbKey);
        }
    };

    const handlers = {
        onPointerDown,
        onPointerMove,
        onPointerUp,
        onPointerCancel,
        onClick: onOverlayClick,
        onKeyDown: onOverlayKeyDown,
    };

    const paletteHandlers = {
        onPointerDown,
        onPointerMove,
        onPointerUp,
        onPointerCancel,
        onClick: onPaletteClick,
        onKeyDown: onPaletteKeyDown,
    };

    const iconFor = (key) => {
        const icon = itemIconOf(menu.items.get(key)?.element);
        return icon ? React.cloneElement(icon, { key: 'icon' }) : <span className="sb-palette-dot" aria-hidden="true" />;
    };

    /* ── Строки ───────────────────────────────────────────────────────── */
    const out = [];
    const membersOf = (id) => rows.filter((row) => row.type === 'item' && row.folder === id)
        .map((row) => menu.items.get(row.key)?.element)
        .filter(Boolean);
    rows.forEach((row, index) => {
        if (row.type === 'hr') {
            const divider = menu.divider;
            const props = { key: `hr:${row.block}:${index}`, 'data-sb-kind': 'hr', 'data-sb-block': row.block };
            out.push(divider
                ? React.cloneElement(divider, props)
                : <hr {...props} className="border-t border-white-700 my-2 opacity-40" />);
            return;
        }
        if (row.type === 'folder') {
            out.push(
                <FolderRow
                    key={row.entry}
                    row={row}
                    open={!state.closed[row.id]}
                    editing={editing}
                    renaming={state.renamingId === row.id}
                    confirming={state.confirmDeleteId === row.id}
                    members={membersOf(row.id)}
                    onToggle={toggleFolder}
                    onRemove={removeFolder}
                    handlers={handlers}
                />,
            );
            return;
        }
        const item = menu.items.get(row.key);
        if (!item) return;
        const element = item.element;
        out.push(React.cloneElement(
            element,
            {
                key: `item:${row.key}`,
                'data-sb-key': row.key,
                'data-sb-kind': 'item',
                'data-sb-block': row.block,
                'data-sb-folder': row.folder || undefined,
                'data-sb-depth': row.folder ? '1' : undefined,
                'data-sb-fixed': item.fixed ? '1' : undefined,
            },
            <React.Fragment key="row">{element.props.children}</React.Fragment>,
            editing ? (
                <span
                    key="edit"
                    className="sb-edit-overlay"
                    role={item.fixed ? undefined : 'button'}
                    tabIndex={item.fixed ? -1 : 0}
                    aria-label={item.fixed ? undefined : `${row.key}: переместить — ⌥↑ ⌥↓, убрать на панель — Delete`}
                    {...handlers}
                >
                    {!item.fixed && <span className="sb-grip" aria-hidden="true"><GripLines style={ICON_STYLE} /></span>}
                </span>
            ) : null,
        ));
    });
    // Пока в меню не перенесено ни одного раздела — место, куда их бросать.
    if (editing && !rows.some((row) => row.type === 'item')) {
        out.push(
            <li key="sb-drop-hint" className="sb-drop-hint" data-sb-kind="hint" aria-hidden="true">
                <span>Перетащите сюда разделы с панели справа</span>
            </li>,
        );
    }
    if (mounted && paletteMode !== 'off' && typeof document !== 'undefined') {
        out.push(createPortal(
            <SidebarPalette
                mode={paletteMode}
                keys={paletteMode === 'open' ? paletteKeys : []}
                iconFor={iconFor}
                handlers={paletteHandlers}
                paletteRef={paletteRef}
                onReset={onReset}
                canReset={Boolean(editing && state.working?.blocks.length)}
            />,
            document.body,
            'sb-palette',
        ));
    }
    return out;
}
