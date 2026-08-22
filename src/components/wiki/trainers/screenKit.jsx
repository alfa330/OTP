import React from 'react';

/* Общие детали учебных экранов.
 *
 * Каждый экран получает одни и те же три вещи: world (что уже сделано), tap
 * (нажатие) и target (какая кнопка сейчас правильная). Всё остальное — разметка
 * конкретного приложения.
 *
 * Подсветка цели живёт ЗДЕСЬ, а не в каждом экране: подсвечивать «ту самую
 * кнопку» надо на всех экранах одинаково, иначе человек ищет её по-новому на
 * каждом шаге. Класс is-target добавляется автоматически по совпадению id.
 */

/** Кнопка учебного телефона: сама решает, подсвечена ли она. */
export const Tap = ({
    id, target, tap, className = '', children, payload, ...rest
}) => (
    <button
        type="button"
        onClick={() => tap(id, payload)}
        className={`${className}${id === target ? ' is-target' : ''}`}
        {...rest}
    >
        {children}
    </button>
);

/** Плашка «это учебная среда». Стоит на экранах, повторяющих реальные приложения:
 *  скриншот тренажёра неотличим от скриншота приложения, и без метки его
 *  пересылают как настоящий. */
export const TrainMark = ({ children = 'Учебный экран' }) => (
    <div className="wt-mark">{children}</div>
);

/* ── Вступление и финал: одинаковые для всех сценариев ─────────────────────
   Их разметка не зависит от приложения — меняются только название сценария и
   список шагов, а он и так лежит в сценарии. */

export const IntroScreen = ({ scenario, tap, target }) => (
    <div className="wt-screen wt-intro">
        <span className="wt-intro__chip">{scenario.app} · учебный режим</span>
        <div className="wt-intro__icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor"
                strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
            </svg>
        </div>
        <h2>{scenario.title}</h2>
        <p>{scenario.description}</p>
        <div className="wt-intro__flow">
            {(scenario.checklist || []).slice(0, 4).map((item, index) => (
                <span key={`${index}-${item}`}>{index + 1}. {item}</span>
            ))}
        </div>
        {/* Оговорка про учебные данные у сценариев разная: в подписании это ФИО,
            ИИН и подпись, в смене провайдера — ни того, ни другого на экранах
            нет, и обещать «подпись не создаётся» там незачем. */}
        <p className="wt-intro__note">
            {scenario.dataNote
                || 'Данные учебные: ФИО, ИИН, коды и номера документов вымышлены, '
                + 'настоящая подпись не создаётся.'}
        </p>
        <Tap id="begin" target={target} tap={tap} className="wt-primary">Начать урок</Tap>
    </div>
);

export const ResultScreen = ({ scenario, onRestart }) => (
    <div className="wt-screen wt-result">
        <div className="wt-result__check" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="30" height="30" fill="none" stroke="currentColor"
                strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                <path d="m4 12.5 5 5L20 6.5" />
            </svg>
        </div>
        <h2>Готово</h2>
        <p>Документы подписаны и статус проверен. Коротко повторим путь:</p>
        <ol className="wt-result__recap">
            {/* Ключ по индексу: в чек-листе есть повторяющиеся пункты —
                «Нажать „Подписать“» встречается дважды (в приложении и в eGov). */}
            {(scenario.checklist || []).map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}
        </ol>
        <button type="button" className="wt-primary" onClick={onRestart}>Пройти ещё раз</button>
    </div>
);

/** Строка «поле — значение» в карточках кабинета и eGov. */
export const Row = ({ label, children }) => (
    <div className="wt-row"><span>{label}</span><b>{children}</b></div>
);
