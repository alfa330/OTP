import React from 'react';
import useScreenBackGesture from './useScreenBackGesture';

/* Лист действий в стиле телефона: группа крупных строк снизу экрана и
 * отдельной карточкой «Отмена».
 *
 * ЗАЧЕМ. В образце (настройки Telegram, видео владельца 11.09.2026) смена и
 * удаление фотографии живут ВМЕСТЕ: строка одна — «Изменить фотографию», а
 * выбор снимка и красное «Удалить фотографию» лежат в листе, который она
 * открывает. Отдельная строка удаления рядом со строкой смены — то, на что
 * владелец и пожаловался: «удалить фото находится отдельно от смены фото».
 *
 * ПОЧЕМУ СВОЙ ПРИМИТИВ, А НЕ ОКНО ПОРТАЛА. Окна на телефоне — это экраны,
 * въезжающие справа (см. mobile-shell.css): для двух строк выбора это слишком
 * большой ход. Лист поднимается снизу, гасит фон и закрывается нажатием мимо —
 * ровно как системный.
 */
export default function MobileActionSheet({ open, onClose, actions = [], cancelLabel = 'Отмена', title = '' }) {
    /* Системное «назад» закрывает лист, а не то, что под ним. */
    useScreenBackGesture(open, onClose);

    if (!open) return null;

    /* fixed inset-0 — на разметке, как у всех окон портала: слой
       .otp-modal-root в стилях оболочки задаёт только поведение, а место на
       экране приходит отсюда. Без этого лист оставался в потоке страницы. */
    return (
        <div className="otp-modal-root mobile-actions fixed inset-0 z-50" role="dialog" aria-modal="true">
            <div className="mobile-actions__dim" onClick={onClose} aria-hidden="true" />
            <div className="mobile-actions__sheet">
                <div className="mobile-actions__group">
                    {/* Подпись листа — как заголовок системного листа действий:
                        мелкая, серая, по центру. Нужна там, где строки не
                        называют действие, а предлагают ВЫБОР: без неё список
                        отделов читается как набор команд. */}
                    {title ? <div className="mobile-actions__title">{title}</div> : null}
                    {actions.map((action) => (
                        action.render
                            ? <React.Fragment key={action.key}>{action.render}</React.Fragment>
                            : (
                                <button
                                    key={action.key}
                                    type="button"
                                    /* Строка ВЫБОРА выравнивается по левому краю и держит справа
                                       место под галочку: так устроены списки выбора в телефоне, и
                                       по одной колонке глаз находит выбранное, не читая всё. Строка
                                       ДЕЙСТВИЯ остаётся по центру — это разные вещи. */
                                    className={`mobile-actions__row${action.danger ? ' mobile-actions__row--danger' : ''}${
                                        action.selected === undefined ? '' : ' mobile-actions__row--pick'
                                    }${action.selected ? ' is-selected' : ''}`}
                                    onClick={action.onClick}
                                    disabled={action.disabled}
                                    aria-checked={action.selected === undefined ? undefined : Boolean(action.selected)}
                                    role={action.selected === undefined ? undefined : 'menuitemradio'}
                                >
                                    <span className="mobile-actions__label">{action.label}</span>
                                    {action.selected ? (
                                        <svg className="mobile-actions__check" width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                                            <path d="M2.5 8.5l3.5 3.5 7.5-8" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" />
                                        </svg>
                                    ) : null}
                                </button>
                            )
                    ))}
                </div>
                <button type="button" className="mobile-actions__cancel" onClick={onClose}>
                    {cancelLabel}
                </button>
            </div>
        </div>
    );
}
