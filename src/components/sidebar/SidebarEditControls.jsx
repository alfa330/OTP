/* Кнопки личной раскладки сайдбара: «Изменить меню» в подвале панели и то,
 * во что она превращается в режиме правки, — «Отменить», шаг назад и
 * «Готово». Кнопки «Новая папка» нет (владелец 20.11.2026): папка выходит,
 * как на iPhone, когда раздел кладут на раздел; «Обнулить» — на панели
 * справа, куда она и уносит разделы.
 *
 * ПОЧЕМУ В ПОДВАЛЕ, А НЕ В ШАПКЕ. В шапке справа от логотипа (он до 179 px)
 * свободно 29 px до поиска, а кнопки там по 30 px; колокол и поиск стоят на
 * местах, закреплённых тестами вида панели. Подвальная карточка видна всегда,
 * в том числе в свёрнутом рельсе, и её строки уже оформлены как строки
 * списка — новая выглядит родной. Управление правкой там же, где её начали.
 *
 * «ОТМЕНИТЬ» ОБЯЗАТЕЛЬНО: после «Обнулить» и десятка переносов вернуть меню
 * как было одним шагом можно только им. */
import React from 'react';
import { Check, Pencil, Undo2 } from 'lucide-react';
import { sidebarLayoutStore as store, useSidebarLayout } from './sidebarLayoutStore.js';
import './sidebarLayout.css';

const ICON_STYLE = { width: '1em', height: '1em' };

export function SidebarEditFooterRow({ onStart }) {
    const state = useSidebarLayout();
    if (!state.editing) {
        return (
            <li className="sb-edit-footer">
                <button
                    type="button"
                    onClick={() => {
                        onStart?.();
                        store.startEditing();
                    }}
                    title="Изменить меню: свой порядок разделов и папки"
                    className="w-full text-left py-3 px-4 rounded-lg hover:bg-blue-700 transition-all duration-200 flex items-center gap-3"
                >
                    <Pencil className="inline-block align-middle" style={ICON_STYLE} aria-hidden="true" />
                    <span className="sidebar-text">Изменить меню</span>
                </button>
            </li>
        );
    }
    const canUndo = state.history.length > 0;
    return (
        <li className="sb-edit-footer is-editing">
            <div className="sb-edit-toolbar" role="toolbar" aria-label="Правка меню">
                <button
                    type="button"
                    className="sb-tool"
                    onClick={() => store.cancelEditing()}
                    title="Отменить правку — меню останется как было"
                >
                    Отменить
                </button>
                <button
                    type="button"
                    className="sb-tool sb-tool--icon"
                    onClick={() => store.undo()}
                    disabled={!canUndo}
                    aria-label="Шаг назад"
                    title="Шаг назад — ⌘Z"
                >
                    <Undo2 style={ICON_STYLE} aria-hidden="true" />
                </button>
                <button
                    type="button"
                    className="sb-tool sb-tool--done"
                    onClick={() => store.finishEditing()}
                >
                    <Check style={ICON_STYLE} aria-hidden="true" />
                    <span>Готово</span>
                </button>
            </div>
        </li>
    );
}
