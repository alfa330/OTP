import React, { useEffect, useRef, useState } from 'react';
import FaIcon from './FaIcon';

/* Экраны своей учётки на телефоне: смена логина, пароля и фотографии.
 *
 * ЗАЧЕМ ОТДЕЛЬНАЯ РАЗМЕТКА, А НЕ ПРАВКА ОБЩЕЙ. На компьютере это три окна,
 * свёрстанные под мышь: карточка посреди затемнённого экрана, подписи над
 * полями, значок внутри поля, кнопка «Обновить» по центру. На телефоне оболочка
 * растягивала ту же карточку на весь экран — и получался лист бумаги с полями в
 * рамках и синей таблеткой посреди пустоты. Владелец 11.09.2026 попросил сделать
 * эти три экрана «максимально удобными, аккуратными, в стиле ios/macos» и
 * ПРИМЕНИТЬ ТОЛЬКО К МОБИЛЬНОЙ ВЕРСИИ, поэтому настольная разметка осталась
 * слово в слово прежней, а здесь живёт вторая — телефонная.
 *
 * ОБРАЗЕЦ — СПИСОК НАСТРОЕК ТЕЛЕФОНА, тот же, по которому сделана шторка
 * разделов: полотно цвета --sheet-bg, группы строк со скруглением 16, заголовок
 * группы капителью над ней, пояснение серым под ней, действие — во всю ширину
 * под группой, где до него достаёт большой палец и куда не достаёт клавиатура.
 *
 * ШАПКА ЛИПКАЯ И СТЕКЛЯННАЯ — как верх разделов портала: с открытой клавиатурой
 * экран становится коротким, и уехавшая наверх кнопка «Назад» осталась бы
 * недосягаемой.
 */

/* Через сколько после появления экрана ставить курсор в поле. Экран въезжает
   справа 0.38 с (otp-screen-in в mobile-shell.css); клавиатура, поднятая
   посреди этого движения, дёргает его на середине — это и есть та «дёрганость»,
   про которую сказал владелец. Ждём, пока экран встанет на место. */
const SCREEN_SETTLE_MS = 420;

/** Каркас экрана: шапка со стрелкой «Назад» и полотно списка настроек. */
export const MobileAccountScreen = ({ title, onBack, children }) => (
    /* Слоя затемнения здесь нет: экран занимает экран целиком, гасить под ним
       нечего, а лишний элемент поверх страницы — это ещё одна мишень, куда
       может уйти нажатие. */
    <div
        className="otp-modal-root mobile-account fixed inset-0 z-50"
        role="dialog"
        aria-modal="true"
        aria-label={title}
    >
        <div className="otp-modal-card mobile-account__card">
            {/* Метки otp-modal-head здесь нет намеренно: отступ под вырез
                ставит своё правило, а два правила на один отступ — это две
                величины, которые разъедутся при первой же правке. */}
            <header className="mobile-account__head">
                <button
                    type="button"
                    onClick={onBack}
                    aria-label="Назад"
                    className="otp-modal-back mobile-account__back"
                >
                    <FaIcon className="fas fa-chevron-left" aria-hidden="true" />
                </button>
                <h2 className="mobile-account__title">{title}</h2>
                {/* Поле шириной со стрелку — заголовок встаёт на оптическую
                    середину, как в шапке любого приложения телефона. */}
                <span className="mobile-account__head-spacer" aria-hidden="true" />
            </header>
            <div className="mobile-account__body">{children}</div>
        </div>
    </div>
);

/** Группа строк: заголовок капителью сверху, пояснение серым снизу. */
export const MobileAccountGroup = ({ label = '', note = '', children }) => (
    <section className="mobile-account__section">
        {label ? <h3 className="mobile-account__label">{label}</h3> : null}
        <div className="mobile-account__group">{children}</div>
        {note ? <p className="mobile-account__note">{note}</p> : null}
    </section>
);

/**
 * Строка с полем ввода. Подпись — в самом поле (placeholder), как в настройках
 * телефона: отдельная подпись над каждым полем на узком экране удваивает высоту
 * группы, а сказать ею нечего сверх того, что уже написано в поле.
 *
 * settleFocus — поставить курсор, КОГДА ЭКРАН ПРИЕХАЛ (см. SCREEN_SETTLE_MS).
 */
export const MobileAccountField = ({
    id,
    type = 'text',
    value,
    onChange,
    placeholder = '',
    disabled = false,
    settleFocus = false,
    autoComplete = 'off',
    inputMode,
}) => {
    const ref = useRef(null);
    const [revealed, setRevealed] = useState(false);
    const isPassword = type === 'password';

    useEffect(() => {
        if (!settleFocus) return undefined;
        const timer = setTimeout(() => ref.current?.focus(), SCREEN_SETTLE_MS);
        return () => clearTimeout(timer);
    }, [settleFocus]);

    return (
        <div className="mobile-account__row">
            <input
                ref={ref}
                id={id}
                name={id}
                type={isPassword && revealed ? 'text' : type}
                className="mobile-account__input"
                value={value}
                onChange={onChange}
                placeholder={placeholder}
                disabled={disabled}
                autoComplete={autoComplete}
                inputMode={inputMode}
                /* minLength у поля НЕТ намеренно: встроенная проверка браузера
                   не даёт форме отправиться и показывает свой пузырь на чужом
                   языке, а наше сообщение в отведённой под него строке до
                   человека не доходит вовсе. Длину проверяет onSubmit. */
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
            />
            {/* Показать пароль. На телефоне это не украшение: попасть по всем
                символам вслепую, с автозаменой и узкой клавиатурой, — главный
                источник «пароли не совпадают». */}
            {isPassword && (
                <button
                    type="button"
                    className="mobile-account__reveal"
                    onClick={() => setRevealed((prev) => !prev)}
                    aria-label={revealed ? 'Скрыть пароль' : 'Показать пароль'}
                    tabIndex={-1}
                >
                    <FaIcon className={`fas ${revealed ? 'fa-eye-slash' : 'fa-eye'}`} aria-hidden="true" />
                </button>
            )}
        </div>
    );
};

/** Строка-действие внутри группы; danger — красная, как «Удалить» в настройках. */
export const MobileAccountRow = ({ icon = '', label, onClick, disabled = false, danger = false }) => (
    <button
        type="button"
        className={`mobile-account__row mobile-account__row--tap${danger ? ' mobile-account__row--danger' : ''}`}
        onClick={onClick}
        disabled={disabled}
    >
        {icon ? <FaIcon className={`fas ${icon} mobile-account__row-icon`} aria-hidden="true" /> : null}
        <span>{label}</span>
    </button>
);

/**
 * Место под ошибку. Высота занята всегда: без неё появление строки сдвигало бы
 * кнопку под пальцем ровно в момент нажатия.
 */
export const MobileAccountError = ({ children }) => (
    <p className="mobile-account__error" aria-live="polite">{children || ' '}</p>
);

/** Главное действие экрана — во всю ширину, под группой. */
export const MobileAccountAction = ({ label, loadingLabel = 'Сохраняем…', loading = false, disabled = false, onClick = null, type = 'submit' }) => (
    <button
        type={type}
        onClick={onClick || undefined}
        className="mobile-account__action"
        disabled={disabled || loading}
    >
        {loading ? loadingLabel : label}
    </button>
);

export default MobileAccountScreen;
