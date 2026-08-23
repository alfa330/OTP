import React from 'react';

import { Tap } from './screenKit';

/* Корпус учебного КОМПЬЮТЕРА: окно браузера в Windows с НЕСКОЛЬКИМИ вкладками.
 *
 * Зачем оно вообще нарисовано. Тренажёры про водителя показывают телефон,
 * потому что водитель работает с телефона. Оператор работает за компьютером, и
 * тот же приём («узнаю своё устройство — переношу действие на настоящее»)
 * требует не рамки-прямоугольника, а именно окна браузера: вкладки с названиями
 * систем, адрес в строке, кнопки окна справа.
 *
 * Вкладок ДВЕ и они открыты сразу, как на смене: CRM, где заводят обращение, и
 * Диспетчерская, куда идут смотреть. Оператор не открывает их по очереди —
 * они уже открыты, и переключение между ними это одно движение, а не задача.
 * Поэтому переключение вкладок — свободное действие (runner.browse), а не ход
 * урока: наказывать за взгляд в справочник значит отучать туда смотреть.
 *
 * Кнопки окна и крестик вкладки, наоборот, НЕ декоративные: по ним промахиваются
 * в жизни, и тренажёр обязан на это ответить объяснением, а не молчанием.
 * Поэтому они — обычные цели движка со своими ловушками.
 *
 * Свернуть/развернуть/закрыть само окно тренажёр, разумеется, не делает: это
 * учебная картинка, и «закрыть» здесь означает разбор ошибки.
 */

/* Значки вкладок рисуем сами, а не тянем картинки: в тренажёре они были бы
   единственными внешними файлами ради шестнадцати пикселей. Знак Диспетчерской
   намеренно НЕ повторяет чужой логотип — см. шапку screensFleet.jsx. */
const TabIcon = ({ kind }) => (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
        <rect width="16" height="16" rx="4" fill={kind === 'fleet' ? '#1f2733' : '#4273fa'} />
        {kind === 'fleet' ? (
            <path d="M3.4 10.6h9.2M4.6 10.6V7.3l1-2.4h4.8l1 2.4v3.3M5.5 12.2v-1.6m5 1.6v-1.6"
                fill="none" stroke="#fff" strokeWidth="1.1" strokeLinecap="round" />
        ) : (
            <text x="8" y="11.6" textAnchor="middle" fontSize="8.6" fontWeight="700"
                fontFamily="system-ui, sans-serif" fill="#fff">iT</text>
        )}
    </svg>
);

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

/** Полоса вкладок с кнопками окна Windows (прямоугольные, не «светофор» macOS). */
const TabStrip = ({ tap, target, tabs, active, onSwitch }) => (
    <div className="wt-win__tabs">
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

        {/* Кнопки окна. У Windows они в правом верхнем углу и подписаны
            чёрточкой, квадратом и крестиком — по ним и узнаётся система. */}
        <div className="wt-win__controls">
            <Tap id="win_min" target={target} tap={tap} aria-label="Свернуть окно">
                <svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true">
                    <path d="M2 6h8" stroke="currentColor" strokeWidth="1.1" />
                </svg>
            </Tap>
            <Tap id="win_max" target={target} tap={tap} aria-label="Развернуть окно">
                <svg viewBox="0 0 12 12" width="11" height="11" fill="none" aria-hidden="true">
                    <rect x="2.4" y="2.4" width="7.2" height="7.2" stroke="currentColor"
                        strokeWidth="1.1" />
                </svg>
            </Tap>
            <Tap id="win_close" target={target} tap={tap} className="is-close"
                aria-label="Закрыть окно">
                <svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true">
                    <path d="M2.6 2.6l6.8 6.8M9.4 2.6 2.6 9.4" stroke="currentColor"
                        strokeWidth="1.1" />
                </svg>
            </Tap>
        </div>
    </div>
);

/** Панель навигации с адресной строкой. */
const Toolbar = ({ tap, target, url }) => (
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

        {/* Аватар профиля браузера — только картинка: промахнуться по нему
            мимо формы можно, но объяснять там нечего. */}
        <span className="wt-win__avatar" aria-hidden="true">ШХ</span>
    </div>
);

/** Окно браузера целиком: полоса вкладок, панель навигации и содержимое. */
export default function BrowserChrome({
    tap, target, tabs, active, onSwitch, url, children,
}) {
    return (
        <div className="wt-win">
            <TabStrip tap={tap} target={target} tabs={tabs} active={active} onSwitch={onSwitch} />
            <Toolbar tap={tap} target={target} url={url} />
            <div className="wt-win__page">{children}</div>
        </div>
    );
}
