import React from 'react';

import { Tap } from './screenKit';

/* Корпус учебного КОМПЬЮТЕРА: окно браузера с вкладками.
 *
 * Зачем оно вообще нарисовано. Тренажёры про водителя показывают телефон,
 * потому что водитель работает с телефона. Оператор работает за компьютером, и
 * тот же приём («узнаю своё устройство — переношу действие на настоящее»)
 * требует не рамки-прямоугольника, а именно окна браузера: вкладки с названиями
 * систем, адрес в строке, кнопки окна справа.
 *
 * Вкладки — те, что открыты на смене. Сейчас это одна CRM: в ней и обращения,
 * и учётки водителей, и обращения в Яндекс (владелец, 23.11.2026: Диспетчерская
 * и Oktell в тренажёре не нужны). Корпус умеет и несколько вкладок: открытые
 * сразу, как на смене, а переключение между ними — свободное действие
 * (runner.browse), а не ход урока: наказывать за взгляд в справочник значит
 * отучать туда смотреть.
 *
 * Окно — в стиле macOS (владелец, 23.11.2026: «удобно и аккуратно, в стиле
 * iOS/macOS»): «светофор» слева, вкладка с мягкими углами, адресная строка-
 * капсула. Раньше окно было «как в Windows» — по системе операторов, — но
 * владелец выбрал единый спокойный вид всего портала.
 *
 * Кнопки окна и крестик вкладки, наоборот, НЕ декоративные: по ним промахиваются
 * в жизни, и тренажёр обязан на это ответить объяснением, а не молчанием.
 * Поэтому они — обычные цели движка со своими ловушками.
 *
 * Свернуть/развернуть/закрыть само окно тренажёр, разумеется, не делает: это
 * учебная картинка, и «закрыть» здесь означает разбор ошибки.
 */

/* Значок вкладки рисуем сами, а не тянем картинку: в тренажёре она была бы
   единственным внешним файлом ради шестнадцати пикселей. */
const TabIcon = ({ kind = 'crm' }) => (kind === 'fleet' ? (
    /* Диспетчерская — тот же нейтральный знак, что в шапке её кабинета
       (машинка), а не чужой логотип. */
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
        <rect width="16" height="16" rx="4" fill="#21201f" />
        <path d="M3.6 10.6h8.8M4.9 10.6V7.4l1-2.5h4.2l1 2.5v3.2M6.1 12.4v-1.8m3.8 1.8v-1.8"
            fill="none" stroke="#ffdc60" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
) : (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
        <rect width="16" height="16" rx="4" fill="#4273fa" />
        <text x="8" y="11.6" textAnchor="middle" fontSize="8.6" fontWeight="700"
            fontFamily="system-ui, sans-serif" fill="#fff">iT</text>
    </svg>
));

const NavIcon = ({ name }) => {
    const paths = {
        back: 'M15 5 8 12l7 7',
        forward: 'M9 5l7 7-7 7',
        reload: 'M20 12a8 8 0 1 1-2.5-5.8M20 4v4h-4',
    };
    return (
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor"
            strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d={paths[name]} />
        </svg>
    );
};

/* Замок в адресной строке. Мелочь, но именно по нему оператора учат отличать
   рабочую систему от подделки, и убирать его из учебного окна не стоит. */
const Lock = () => (
    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor"
        strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <rect x="4.5" y="10.5" width="15" height="10" rx="2.5" />
        <path d="M8 10.5V7.5a4 4 0 0 1 8 0v3" />
    </svg>
);

/* Кнопки окна macOS. Значки на кружках проявляются при наведении на группу
   (CSS), как в системе. */
const Lights = ({ tap, target }) => (
    <div className="wt-win__lights">
        <Tap id="win_close" target={target} tap={tap} className="wt-win__light is-close"
            aria-label="Закрыть окно">
            <svg viewBox="0 0 12 12" aria-hidden="true"><path d="M3.8 3.8l4.4 4.4M8.2 3.8 3.8 8.2" /></svg>
        </Tap>
        <Tap id="win_min" target={target} tap={tap} className="wt-win__light is-min"
            aria-label="Свернуть окно">
            <svg viewBox="0 0 12 12" aria-hidden="true"><path d="M3.2 6h5.6" /></svg>
        </Tap>
        <Tap id="win_max" target={target} tap={tap} className="wt-win__light is-max"
            aria-label="Развернуть окно">
            <svg viewBox="0 0 12 12" aria-hidden="true"><path d="M4 8V4.6M4 8h3.4M8 4v3.4M8 4H4.6" /></svg>
        </Tap>
    </div>
);

/* Кнопки окна Windows — справа: свернуть, восстановить (окно развёрнуто),
   закрыть. Те же цели движка, что у «светофора», — промах по ним объясняется. */
const WinControls = ({ tap, target }) => (
    <div className="wt-win__controls">
        <Tap id="win_min" target={target} tap={tap} aria-label="Свернуть окно">
            <svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true">
                <path d="M1.5 6h9" stroke="currentColor" strokeWidth="1" />
            </svg>
        </Tap>
        <Tap id="win_max" target={target} tap={tap} aria-label="Свернуть в окно">
            <svg viewBox="0 0 12 12" width="11" height="11" fill="none" aria-hidden="true">
                <rect x="1.5" y="3.5" width="7" height="7" rx="1" stroke="currentColor" strokeWidth="1" />
                <path d="M3.5 3.5V2.5a1 1 0 0 1 1-1h5a1 1 0 0 1 1 1v5a1 1 0 0 1-1 1h-1" stroke="currentColor" strokeWidth="1" />
            </svg>
        </Tap>
        <Tap id="win_close" target={target} tap={tap} className="is-close" aria-label="Закрыть окно">
            <svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true">
                <path d="M2 2l8 8M10 2 2 10" stroke="currentColor" strokeWidth="1" />
            </svg>
        </Tap>
    </div>
);

/** Полоса вкладок: «светофор» macOS слева или кнопки Windows справа. */
const TabStrip = ({ tap, target, tabs, active, onSwitch, windows = false }) => (
    <div className="wt-win__tabs">
        {windows ? null : <Lights tap={tap} target={target} />}
        {/* Вкладка — НЕ одна большая кнопка: внутри неё живёт крестик, который
            сам является целью движка. Кнопка в кнопке — невалидная разметка, и
            клик по крестику заодно переключал бы вкладку. Поэтому переключение
            висит на «лице» вкладки, а крестик стоит рядом с ним. */}
        {tabs.map((tab) => (
            <div
                key={tab.id}
                className={`wt-win__tab${tab.id === active ? ' is-active' : ''}`}
            >
                <button
                    type="button"
                    className="wt-win__tab-face"
                    onClick={() => (tab.id === active ? null : onSwitch(tab.id))}
                    aria-current={tab.id === active ? 'page' : undefined}
                >
                    <TabIcon kind={tab.icon} />
                    <span className="wt-win__tab-name">{tab.title}</span>
                </button>
                {tab.id === active ? (
                    <Tap id="tab_close" target={target} tap={tap} className="wt-win__tab-x"
                        aria-label="Закрыть вкладку">
                        <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor"
                            strokeWidth="2.4" strokeLinecap="round" aria-hidden="true">
                            <path d="M6 6l12 12M18 6 6 18" />
                        </svg>
                    </Tap>
                ) : <i className="wt-win__tab-x is-ghost" aria-hidden="true" />}
            </div>
        ))}
        <Tap id="tab_new" target={target} tap={tap} className="wt-win__tab-add"
            aria-label="Новая вкладка">
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor"
                strokeWidth="2.2" strokeLinecap="round" aria-hidden="true">
                <path d="M12 5v14M5 12h14" />
            </svg>
        </Tap>
        {windows ? <WinControls tap={tap} target={target} /> : null}
    </div>
);

/** Панель навигации с адресной строкой. */
const Toolbar = ({ tap, target, url, windows = false }) => (
    <div className="wt-win__bar">
        <Tap id="browser_back" target={target} tap={tap} className="wt-win__nav"
            aria-label="Назад"><NavIcon name="back" /></Tap>
        <span className="wt-win__nav is-off" aria-hidden="true"><NavIcon name="forward" /></span>
        <Tap id="browser_reload" target={target} tap={tap} className="wt-win__nav"
            aria-label="Обновить"><NavIcon name="reload" /></Tap>

        <Tap id="address_bar" target={target} tap={tap} className="wt-win__omni">
            <i className="wt-win__lock" aria-hidden="true"><Lock /></i>
            <span className="wt-win__url">{url}</span>
        </Tap>

        {/* Профиль браузера — только картинка: промахнуться по нему мимо
            формы можно, но объяснять там нечего. Безымянный: учётка у каждого
            стажёра своя, чужие инициалы здесь путали бы. */}
        <span className="wt-win__avatar" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor">
                <circle cx="12" cy="8.6" r="3.7" />
                <path d="M4.6 19.4c1.3-3.5 4.1-5.3 7.4-5.3s6.1 1.8 7.4 5.3z" />
            </svg>
        </span>
        {/* «⋮» — меню браузера в Windows: только картинка. */}
        {windows ? (
            <span className="wt-win__kebab" aria-hidden="true">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
                    <circle cx="12" cy="5.5" r="1.6" /><circle cx="12" cy="12" r="1.6" /><circle cx="12" cy="18.5" r="1.6" />
                </svg>
            </span>
        ) : null}
    </div>
);

/** Окно браузера целиком: полоса вкладок, панель навигации и содержимое.
 *  variant="windows" — окно Windows, развёрнутое на весь экран (среда
 *  «Звонок ИИ-водителя»); по умолчанию — окно macOS (курс). */
export default function BrowserChrome({
    tap, target, tabs, active, onSwitch, url, children, variant = 'mac',
}) {
    const windows = variant === 'windows';
    return (
        <div className={`wt-win${windows ? ' wt-win--windows' : ''}`}>
            <TabStrip tap={tap} target={target} tabs={tabs} active={active} onSwitch={onSwitch} windows={windows} />
            <Toolbar tap={tap} target={target} url={url} windows={windows} />
            <div className="wt-win__page">{children}</div>
        </div>
    );
}
