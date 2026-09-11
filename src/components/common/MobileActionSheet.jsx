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
export default function MobileActionSheet({ open, onClose, actions = [], cancelLabel = 'Отмена' }) {
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
                    {actions.map((action) => (
                        action.render
                            ? <React.Fragment key={action.key}>{action.render}</React.Fragment>
                            : (
                                <button
                                    key={action.key}
                                    type="button"
                                    className={`mobile-actions__row${action.danger ? ' mobile-actions__row--danger' : ''}`}
                                    onClick={action.onClick}
                                    disabled={action.disabled}
                                >
                                    {action.label}
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
