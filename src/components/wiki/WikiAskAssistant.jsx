import React from 'react';
import { ChevronRight, Sparkles } from 'lucide-react';
import { iosBtnPrimary } from '../ui/ios';

/* «Спросить Помощника» — выход из поиска в чат по базе знаний.
 *
 * Зачем отдельным файлом. Поисков в разделе ДВА — поле в шапке (WikiSearch) и
 * строка на витрине (WikiLibrary). Предложение спросить помощника обязано
 * выглядеть в них одинаково: это одно действие, а не две похожие кнопки.
 * Разложенное по двум файлам, оно разъедется на первой же правке.
 *
 * Две формы одного действия, и разница между ними смысловая:
 *   * строка (AskAssistantRow) — под выдачей, когда статьи всё-таки нашлись.
 *     Тихая, без цвета: человек нашёл нужное, и спотыкаться об неё не должен;
 *   * карточка (AskAssistantEmpty) — когда не нашлось ничего. Тогда помощник и
 *     есть главный ответ экрана, а «ничего не найдено» — только пояснение,
 *     почему он здесь. Тупика «ничего не найдено» у поиска больше нет.
 *
 * Запрос в подписи не для красоты: нажатие переносит его в строку ввода
 * помощника, и человек должен видеть, что именно туда поедет. Сам вопрос НЕ
 * отправляется — поисковый запрос редко совпадает с вопросом слово в слово,
 * и дописать его надо до отправки, а не после.
 */

/** Тихая строка под выдачей. */
export const AskAssistantRow = ({
    term, onAsk, selected = false, onHover, dataRow, className = '',
}) => (
    <button
        type="button"
        data-row={dataRow}
        onClick={onAsk}
        onMouseEnter={onHover}
        className={`flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-left transition ${
            selected ? 'bg-indigo-50' : 'hover:bg-slate-50'
        } ${className}`}
    >
        <Sparkles size={15} className={`shrink-0 ${selected ? 'text-indigo-500' : 'text-slate-300'}`} />
        <span className="min-w-0 flex-1 truncate text-[13px] text-slate-500">
            <span className="font-medium text-slate-800">Спросить Помощника</span>
            {term ? <>: «{term}»</> : null}
        </span>
        <ChevronRight size={14} className="shrink-0 text-slate-300" />
    </button>
);

/** Пустая выдача: помощник главным, «не найдено» — пояснением. */
export const AskAssistantEmpty = ({ term, onAsk, note = null, compact = false }) => (
    <div className={`flex flex-col items-center text-center ${compact ? 'px-3 py-7' : 'px-6 py-12'}`}>
        {/* Плитка нейтральная: цвет в этом кадре несёт кнопка — она и есть
            действие. Второй акцент рядом с ней был бы шумом. */}
        <div className={`grid place-items-center rounded-2xl bg-slate-100 text-slate-400 ${
            compact ? 'h-10 w-10' : 'h-12 w-12'
        }`}>
            <Sparkles size={compact ? 18 : 22} />
        </div>
        <div className={`mt-2 font-semibold text-slate-900 ${compact ? 'text-[13.5px]' : 'text-[15px]'}`}>
            Ничего не найдено{term ? <> по запросу «{term}»</> : null}
        </div>
        <p className={`mt-1 max-w-sm leading-relaxed text-slate-500 ${
            compact ? 'text-[12px]' : 'text-[13px]'
        }`}>
            Помощник ответит по статьям, которые вам доступны, и покажет источник.
        </p>
        <button type="button" onClick={onAsk} className={`${iosBtnPrimary} mt-3`}>
            <Sparkles size={15} /> Спросить Помощника
        </button>
        {note && (
            <p className={`mt-2.5 max-w-sm leading-relaxed text-slate-400 ${
                compact ? 'text-[11px]' : 'text-[12px]'
            }`}>
                {note}
            </p>
        )}
    </div>
);
