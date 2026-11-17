import React from 'react';

import { Tap, TrainMark } from './screenKit';
import { SaparLogo, SpNavIcon, SpGroupIcon, TpIcon, TpNavIcon } from './appIcons';
import { TRAINEE } from './scenarioTaxiPro';

/* Экраны приложения Такси.Про и кабинета Сапар, открытого внутри него.
 *
 * Нарисовано по кадрам водителей из Chat2Desk за 17.10–17.11.2026 (iOS, светлая
 * тема). Что изменилось с лета и почему экраны переписаны целиком:
 *   • нижняя панель — плавающая карточка с подписями «Главная · Kaspi QR · Байга
 *     · Документы · Поддержка» («Профиль» из неё ушёл), справа над ней зелёная
 *     кнопка чата;
 *   • «Документы» — крупный заголовок и переключатель «Через ЭЦП · Через eGov ·
 *     Договоры»; акты с жёлтой «Подписать» живут на вкладке «Через eGov»;
 *   • кабинет Сапар открывается окном приложения «Подписание документов» с
 *     крестиком (а не вкладкой браузера), и сам кабинет новый: карточка с именем,
 *     разделы «Документы от Яндекса / от таксопарка», синяя «Подписать все
 *     документы» над панелью «Документы · Сервисы · Помощь · Профиль».
 *
 * Палитра снята пипеткой: фон #f2f1f6, белые карточки, жёлтый у кнопок #fbdf00
 * и у активной вкладки #f5d500, приглушённое золото #c3a526 у ссылок и значков,
 * зелёная кнопка чата #35c75a. У Сапара — прежние #2563ea, #8972f4, #4d5bf2.
 *
 * Тренажёр «через сайт Сапар» рисует кабинет в браузере своими классами
 * (.wt-sp__*), здесь — свои (.wt-sx__*): два пути водитель должен различать, и
 * правка одного не должна молча перекрашивать другой.
 */

/** Период в сценарии хранится с предлогом («за Октябрь 2026»), на экранах —
 *  без него: «за Октябрь 2026» стоит в кабинете под названием акта, «Октябрь
 *  2026» — над пакетом во вкладке «Через ЭЦП». */
const bare = (label) => String(label || '').replace(/^за\s+/i, '');

/* ── Нижняя панель и кнопка чата ──────────────────────────────────────────
   Пять кнопок, из них правильная одна. Остальные — ловушки сценария, поэтому
   они настоящие кнопки, а не картинки: нажать не туда должно быть можно. */
const NAV = [
    ['nav_home', 'Главная', 'home'],
    ['nav_kaspi', 'Kaspi QR', 'kaspi'],
    ['nav_baiga', 'Байга', 'baiga'],
    ['nav_docs', 'Документы', 'docs'],
    ['nav_support', 'Поддержка', 'support'],
];

const TpNav = ({ active, tap, target, badge = 0 }) => (
    <nav className="wt-tp__nav" aria-label="Навигация Такси.Про">
        {NAV.map(([id, label, icon]) => (
            <Tap
                key={id}
                id={id}
                target={target}
                tap={tap}
                className={active === id ? 'is-active' : ''}
            >
                <i aria-hidden="true">
                    <TpNavIcon name={icon} />
                    {/* Красная цифра неподписанных документов — в приложении она
                        и есть причина, по которой водитель заходит в раздел. */}
                    {id === 'nav_docs' && badge > 0 && (
                        <em className="wt-tp__badge">{badge}</em>
                    )}
                </i>
                {label}
            </Tap>
        ))}
    </nav>
);

/** Зелёная кнопка чата поддержки — висит над панелью на каждом экране. */
const TpFab = ({ tap, target }) => (
    <Tap id="support_chat" target={target} tap={tap} className="wt-tp__fab"
        aria-label="Чат с поддержкой таксопарка">
        <TpIcon name="chat" />
    </Tap>
);

/** Каркас экрана приложения: прокручиваемое содержимое, поверх — панель и чат.
 *  Панель плавает над списком, поэтому у содержимого запас снизу. */
const TpApp = ({ active, badge, tap, target, className = '', children }) => (
    <div className="wt-tp-app">
        <div className={`wt-screen wt-tp ${className}`.trim()}>
            <TrainMark />
            {children}
        </div>
        <TpFab tap={tap} target={target} />
        <TpNav active={active} tap={tap} target={target} badge={badge} />
    </div>
);

/* ── Главная ─────────────────────────────────────────────────────────────── */

const Avatar = () => (
    <span className="wt-tp__avatar" aria-hidden="true">
        <svg viewBox="0 0 40 40">
            <circle cx="20" cy="20" r="20" fill="#fbf1c6" />
            <path d="M12.4 15.6c0-5 3.4-7.8 7.6-7.8s7.6 2.8 7.6 7.8v1.6H12.4Z" fill="#2c2f36" />
            <circle cx="20" cy="17.4" r="6" fill="#f6d2b4" />
            <path d="M13.8 14.2c2.2.4 6.4-.4 8.6-3 1 2 2.6 3 3.8 3.2v-1.8c0-3.2-2.8-5-6.2-5s-6.2 1.8-6.2 5Z"
                fill="#2c2f36" />
            <path d="M8.6 36.4a11.4 11.4 0 0 1 22.8 0Z" fill="#2c2f36" />
            <path d="M17.4 25.8h5.2L20 31Z" fill="#ffffff" />
        </svg>
    </span>
);

export const TpHome = ({ tap, target }) => (
    <TpApp active="nav_home" badge={2} tap={tap} target={target}>
        <header className="wt-tp__hello">
            <b>Добрый день,<br />{TRAINEE.first}!</b>
            <Avatar />
        </header>
        {/* Лента баннеров с краями соседей — первое, что видно на главной, и
            первое, во что попадают мимо кнопок. */}
        <div className="wt-tp__banners">
            <Tap id="banner" target={target} tap={tap} className="wt-tp__banner is-peek"
                aria-label="Предыдущий баннер" />
            <Tap id="banner" target={target} tap={tap} className="wt-tp__banner">
                <em>Такси.Про</em>
                <b>Теперь смена авто и номера телефона прямо в Такси.Про</b>
                <span>Обновление</span>
            </Tap>
            <Tap id="banner" target={target} tap={tap} className="wt-tp__banner is-peek"
                aria-label="Следующий баннер" />
        </div>
        <section className="wt-tp__card">
            <div className="wt-tp__card-head">
                <span><TpIcon name="refresh" />Ваш баланс</span>
                <Tap id="history" target={target} tap={tap} className="wt-tp__link">
                    История
                    <TpIcon name="history" />
                </Tap>
            </div>
            <span className="wt-tp__balance">
                <TpIcon name="wallet" />
                {TRAINEE.balance}
            </span>
            {/* Кнопка вывода НЕ выглядит заблокированной — она такая же яркая, как
                всегда. Про блокировку водитель узнаёт из нижнего окна «Ошибка»
                после нажатия, предупреждающей строки под кнопкой в приложении нет. */}
            <Tap id="withdraw" target={target} tap={tap} className="wt-tp__yellow">
                Вывод с баланса
            </Tap>
        </section>
        <section className="wt-tp__card">
            <div className="wt-tp__card-head">
                <span>Выбранный таксопарк</span>
                <Tap id="change_park" target={target} tap={tap} className="wt-tp__link">
                    Изменить
                    <TpIcon name="edit" />
                </Tap>
            </div>
            <ul className="wt-tp__lines">
                <li><TpIcon name="park" />{TRAINEE.park}</li>
                <li><TpIcon name="car" />{TRAINEE.car}</li>
                <li><TpIcon name="phone" />{TRAINEE.phone}</li>
            </ul>
        </section>
        {/* Плитки и их порядок — с ноябрьских кадров: первыми «Байга» для
            водителей и курьеров, ниже розыгрыш парка и промокод. */}
        <div className="wt-tp__tiles">
            <Tap id="promo" target={target} tap={tap}>
                <i aria-hidden="true"><TpIcon name="trophy" /></i>
                Байга для водителей
            </Tap>
            <Tap id="promo" target={target} tap={tap}>
                <i aria-hidden="true"><TpIcon name="ticket" /></i>
                Байга для курьеров
            </Tap>
            <Tap id="promo" target={target} tap={tap}>
                <i aria-hidden="true"><TpIcon name="car" /></i>
                Розыгрыш Hyundai Elantra
            </Tap>
            <Tap id="promo" target={target} tap={tap}>
                <i aria-hidden="true"><TpIcon name="ticket" /></i>
                Промокод
            </Tap>
        </div>
    </TpApp>
);

/* ── Документы ───────────────────────────────────────────────────────────── */

const TABS = [
    ['tab_ecp', 'ecp', 'Через ЭЦП'],
    ['tab_egov', 'egov', 'Через eGov'],
    ['tab_contracts', 'contracts', 'Договоры'],
];

/* Крупный заголовок и переключатель в серой дорожке: активная вкладка — жёлтая
   «таблетка». По этой полосе раздел и узнаётся. */
const TpDocsHead = ({ tab, tap, target }) => (
    <div className="wt-tp__docs-head">
        <h3>Документы</h3>
        <div className="wt-tp__seg" role="tablist" aria-label="Способ подписания">
            {TABS.map(([id, key, label]) => (
                <Tap key={id} id={id} target={target} tap={tap} role="tab"
                    aria-selected={tab === key} className={tab === key ? 'is-active' : ''}>
                    {label}
                </Tap>
            ))}
        </div>
    </div>
);

/* Вкладка «Через ЭЦП»: пакет документов месяца, который подписывают ключом ЭЦП,
   загруженным в приложение. В уроке это не путь, а ловушка — но рисуется она
   полностью: раздел открывается именно на ней, и человек должен узнать экран. */
const TpEcpPack = ({ world, tap, target }) => (
    <>
        <div className="wt-tp__pack-title">
            <small>Документы на подпись</small>
            <b>{bare(world.period.label)}</b>
            <span>Документов: 1 · подписываются одним пакетом</span>
        </div>
        <article className="wt-tp__card wt-tp__pack">
            <div className="wt-tp__pack-head">
                <i className="wt-tp__doc-tile" aria-hidden="true"><TpIcon name="doc" /></i>
                <span>
                    <b>АВР парка</b>
                    <small>{world.packDate}</small>
                </span>
                <span className="wt-tp__sum">
                    <b>10 000,00 ₸</b>
                    <small>в т.ч. НДС 1 071,43 ₸</small>
                </span>
            </div>
            <div className="wt-tp__pack-row">
                <span>Контрагент</span>
                <span>
                    <b>{TRAINEE.parkCompany}</b>
                    <small>БИН/ИИН {TRAINEE.parkBin}</small>
                </span>
            </div>
        </article>
        <p className="wt-tp__note">
            <TpIcon name="info" />
            Подписывается именно этот набор документов, целиком
        </p>
        <p className="wt-tp__note">
            <TpIcon name="info" />
            Документы подписываются вашим ключом ЭЦП, который вы загрузили в приложение
        </p>
        <Tap id="ecp_sign" target={target} tap={tap} className="wt-tp__yellow">
            <TpIcon name="sign" />
            Подписать
        </Tap>
        <Tap id="ecp_history" target={target} tap={tap} className="wt-tp__history">
            История подписаний
        </Tap>
    </>
);

/* ── Карточка акта ────────────────────────────────────────────────────────
   Состояний у акта в приложении два: «Подписать» или «Подписан · Открыть».
   Промежуточного «ждёт» нет — после подписи список просто остаётся старым,
   пока его не обновят, и именно это заставляет думать, что подпись не прошла. */
const TpAct = ({ period, number, signed, tap, target, highlight = true }) => (
    <article className="wt-tp__card wt-tp__act">
        <div className="wt-tp__act-head">
            <i className="wt-tp__doc-tile" aria-hidden="true"><TpIcon name="doc" /></i>
            <span>
                <b>Акт выполненных работ</b>
                <span>{period.human}</span>
                <small>Номер документа: {number}</small>
            </span>
        </div>
        {signed ? (
            <div className="wt-tp__act-status">
                <span><TpIcon name="verified" />Подписан</span>
                <Tap id="open_act" target={target} tap={tap}>Открыть</Tap>
            </div>
        ) : (
            <Tap id="press_sign" target={highlight ? target : null} tap={tap}
                className="wt-tp__yellow">
                <TpIcon name="sign" />
                Подписать
            </Tap>
        )}
    </article>
);

const ACTS = ['334429666432000000', '334429106061000000', '334408186500000000'];

/* Подсвечивается только «Подписать» верхнего акта — так говорит барс; нажатие
   у второго засчитывается тоже (id тот же), но искать глазами надо одну кнопку. */
const TpActs = ({ world, signed, tap, target }) => (
    <>
        <TpAct period={world.period} number={ACTS[0]} signed={signed} tap={tap} target={target} />
        <TpAct period={world.period} number={ACTS[1]} signed={signed} tap={tap} target={target}
            highlight={false} />
        <TpAct period={world.earlier} number={ACTS[2]} signed tap={tap} target={target} />
    </>
);

export const TpDocuments = ({ world, tap, target }) => {
    const tab = world.docsTab || 'ecp';
    return (
        <TpApp active="nav_docs" badge={2} tap={tap} target={target}>
            <TpDocsHead tab={tab} tap={tap} target={target} />
            {tab === 'ecp'
                ? <TpEcpPack world={world} tap={tap} target={target} />
                : <TpActs world={world} signed={false} tap={tap} target={target} />}
        </TpApp>
    );
};

export const TpCheck = ({ world, tap, target }) => {
    const done = !!world.refreshed;
    return (
        <TpApp active="nav_docs" badge={done ? 0 : 2} tap={tap} target={target}>
            <TpDocsHead tab="egov" tap={tap} target={target} />
            {!done && (
                <Tap id="refresh" target={target} tap={tap} className="wt-tp__refresh">
                    <TpIcon name="refresh" />
                    Потяните вниз, чтобы обновить
                </Tap>
            )}
            {/* Итог урока — над списком: внизу его закрывали бы панель и кнопка
                чата на невысоком окне. В приложении этой карточки нет. */}
            {done && (
                <div className="wt-tp__card wt-tp__done">
                    <b>На актах статус «Подписан»</b>
                    <Tap id="finish" target={target} tap={tap} className="wt-tp__yellow">
                        Завершить урок
                    </Tap>
                </div>
            )}
            <TpActs world={world} signed={done} tap={tap} target={target} />
        </TpApp>
    );
};

/* ── Окно «Подписание документов» ─────────────────────────────────────────
   Приложение открывает кабинет Сапар своим окном-«шторкой» iOS: сверху на
   чёрном фоне виден край приложения, ниже — окно с заголовком и серым кружком
   с крестиком. Крестик настоящий: на нём ловушка «не закрывай» до подписи и
   правильное действие после неё. */
const TpSheet = ({ tap, target, children, dock }) => (
    <div className="wt-tps">
        {/* Метка «учебный» — на чёрной полосе над окном: в шапке окна справа
            крестик, по центру заголовок, а у кабинета в правом верхнем углу
            переключатель языка — куда ни поставь, метка что-то закрывала бы. */}
        <div className="wt-tps__top">
            <TrainMark />
            <i className="wt-tps__behind" aria-hidden="true" />
        </div>
        <div className="wt-tps__sheet">
            <header className="wt-tps__head">
                <b>Подписание документов</b>
                <Tap id="close_sheet" target={target} tap={tap} className="wt-tps__x"
                    aria-label="Закрыть окно подписания">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4"
                        strokeLinecap="round" aria-hidden="true">
                        <path d="M7 7l10 10M17 7 7 17" />
                    </svg>
                </Tap>
            </header>
            <div className="wt-tps__body">
                <div className="wt-sx">{children}</div>
            </div>
            {dock}
        </div>
    </div>
);

/* Нижняя панель кабинета с ноября: «Документы» первой и активной, у «Сервисов»
   красная точка, «Выхода» больше нет. */
const SX_NAV = [
    ['sp_docs', 'docs', 'Документы'],
    ['sp_services', 'services', 'Сервисы'],
    ['sp_help', 'help', 'Помощь'],
    ['sp_profile', 'profile', 'Профиль'],
];

const SxNav = ({ tap, target }) => (
    <nav className="wt-sx__nav" aria-label="Навигация кабинета Сапар">
        {SX_NAV.map(([id, icon, label]) => (
            <Tap key={id} id={id} target={target} tap={tap}
                className={id === 'sp_docs' ? 'is-active' : ''}>
                <i aria-hidden="true">
                    <SpNavIcon name={icon} />
                    {id === 'sp_services' && <em className="wt-sx__dot" />}
                </i>
                {label}
            </Tap>
        ))}
    </nav>
);

/** Низ окна: синяя кнопка кабинета (если есть) над плавающей панелью. */
const SxDock = ({ tap, target, children }) => (
    <div className="wt-sx__dock">
        {children}
        <SxNav tap={tap} target={target} />
    </div>
);

const SxLang = ({ tap, target }) => (
    <Tap id="sp_lang" target={target} tap={tap} className="wt-sx__lang">
        RU
        <svg viewBox="0 0 12 12" width="9" height="9" aria-hidden="true">
            <path d="M3 4.6h6L6 8Z" fill="currentColor" />
        </svg>
    </Tap>
);

/** Шапка кабинета: знак, язык, имя с инициалами, ИИН, «?» и статус. */
const SxProfileCard = ({ tap, target }) => (
    <header className="wt-sx__card wt-sx__head">
        <div className="wt-sx__head-top">
            <SaparLogo />
            <SxLang tap={tap} target={target} />
        </div>
        <div className="wt-sx__who">
            <span>
                <b>{TRAINEE.cabinet}</b>
                <small>ИИН: {TRAINEE.iin}</small>
            </span>
            <Tap id="sp_status_help" target={target} tap={tap} className="wt-sx__q"
                aria-label="Почему подписание платное или бесплатное">?</Tap>
            <em>{TRAINEE.statusShort}</em>
        </div>
    </header>
);

/** Шапка страницы подписания: знак, язык и заголовок вместо имени. */
const SxTitleCard = ({ title, tap, target }) => (
    <header className="wt-sx__card wt-sx__head">
        <div className="wt-sx__head-top">
            <SaparLogo />
            <SxLang tap={tap} target={target} />
        </div>
        <h2>{title}</h2>
    </header>
);

const SxSection = ({ title, children }) => (
    <section className="wt-sx__card wt-sx__section">
        <h3>{title}</h3>
        {children}
    </section>
);

const SxDoc = ({ name, sub }) => (
    <div className="wt-sx__doc">
        <b>{name}</b>
        <small>{sub}</small>
    </div>
);

/** Объявление кабинета с крестиком справа — оба нажатия ловушки. */
const SxNotice = ({ tap, target }) => (
    <div className="wt-sx__notice">
        <Tap id="sp_notice" target={target} tap={tap}>
            Обновили «Сервисы»: посмотрите предложения для водителей. <u>Посмотреть</u>
        </Tap>
        <Tap id="sp_notice" target={target} tap={tap} className="wt-sx__notice-x"
            aria-label="Скрыть объявление">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
                strokeLinecap="round" aria-hidden="true">
                <path d="M7 7l10 10M17 7 7 17" />
            </svg>
        </Tap>
    </div>
);

/* Раздел «Документы» внизу кабинета — строки с периодом и цветным ярлыком
   статуса. На кадрах он почти всегда уходит под плавающую панель. */
const SxHistory = ({ world, signed }) => (
    <SxSection title="Документы">
        {['Документ от водителя', 'Документ от Яндекс'].map((name) => (
            <div key={name} className="wt-sx__hist">
                <span>{name} ({world.period.label})</span>
                <span className="wt-sx__hist-status">
                    АВР:
                    <em className={signed ? 'is-done' : 'is-wait'}>
                        {signed ? 'На Подписании у Яндекса' : 'На Подписании у Водителя'}
                    </em>
                </span>
            </div>
        ))}
    </SxSection>
);

/** Кабинет: документы месяца от Яндекса и от таксопарка. */
const SxCabinet = ({ world, tap, target }) => (
    <>
        <SxProfileCard tap={tap} target={target} />
        <SxSection title="Документы от Яндекса">
            <SxDoc name="Акт выполненных работ (АВР)" sub={world.period.label} />
        </SxSection>
        {/* Объявление стоит между разделами — так на кадрах ноября. */}
        <SxNotice tap={tap} target={target} />
        <SxSection title="Документы от таксопарка">
            <SxDoc
                name="Акт выполненных работ (АВР) между СМЗ (Самозанятый) Водителем и таксопарком"
                sub={world.period.label}
            />
            <SxDoc name="Агентский договор" sub={`от таксопарка ${TRAINEE.parkCompany}`} />
        </SxSection>
        <SxHistory world={world} signed={false} />
    </>
);

/** Окно подписания с рекламой поверх кабинета — третий шаг инструкции. */
export const TpSheetAd = ({ world, tap, target }) => (
    <TpSheet
        tap={tap}
        target={target}
        dock={(
            <SxDock tap={tap} target={target}>
                <Tap id="sign_all" target={target} tap={tap} className="wt-sx__blue is-behind">
                    Подписать все документы
                </Tap>
            </SxDock>
        )}
    >
        {/* inert, а не только aria-hidden: без него Tab уводил фокус на кнопки
            под рекламой, невидимые для экранного диктора. */}
        <div className="wt-sx__blurred" aria-hidden="true" inert="">
            <SxCabinet world={world} tap={() => {}} target={null} />
        </div>
        {/* Реклама перекрывает кабинет целиком: нажатие «Подписать все
            документы» уходит в баннер, и человек решает, что кнопка не работает. */}
        <div className="wt-sx__ad" role="dialog" aria-label="Рекламное окно партнёра">
            <div className="wt-sx__ad-cover">
                <span>Учебный партнёр</span>
                <Tap id="close_ad" target={target} tap={tap} className="wt-sx__ad-x"
                    aria-label="Закрыть рекламное окно">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
                        strokeWidth="2.4" strokeLinecap="round" aria-hidden="true">
                        <path d="M6 6l12 12M18 6 6 18" />
                    </svg>
                </Tap>
            </div>
            <div className="wt-sx__ad-body">
                <span>Сервисы для водителей</span>
                <b>Автосервис «Пример»</b>
                <p>Учебный баннер вместо настоящей рекламы: техобслуживание и ремонт со скидкой…</p>
                <Tap id="ad_more" target={target} tap={tap} className="wt-sx__ad-more">
                    Подробнее
                </Tap>
            </div>
        </div>
    </TpSheet>
);

export const TpSheetCabinet = ({ world, tap, target }) => (
    <TpSheet
        tap={tap}
        target={target}
        dock={(
            <SxDock tap={tap} target={target}>
                <Tap id="sign_all" target={target} tap={tap} className="wt-sx__blue">
                    Подписать все документы
                </Tap>
            </SxDock>
        )}
    >
        <SxCabinet world={world} tap={tap} target={target} />
    </TpSheet>
);

export const TpSheetSignAll = ({ world, tap, toggle, target }) => {
    const open = !!world.docsExpanded;
    return (
        <TpSheet tap={tap} target={target} dock={<SxDock tap={tap} target={target} />}>
            <SxTitleCard title="Подписать все документы" tap={tap} target={target} />
            <p className="wt-sx__driver">Водитель: {TRAINEE.full} ({TRAINEE.iin})</p>
            <section className="wt-sx__card wt-sx__fold">
                <button
                    type="button"
                    className={`wt-sx__fold-btn${open ? ' is-open' : ''}`}
                    onClick={() => toggle('docsExpanded')}
                    aria-expanded={open}
                >
                    <span aria-hidden="true">›</span>Документы на подписание
                </button>
                {open && (
                    <>
                        <div className="wt-sx__group">
                            <div className="wt-sx__group-head">
                                <SpGroupIcon kind="yandex" />
                                <b>Документы от Яндекса</b>
                            </div>
                            <p>Акт выполненных работ (АВР): {bare(world.period.label)}</p>
                        </div>
                        <div className="wt-sx__group">
                            <div className="wt-sx__group-head">
                                <SpGroupIcon kind="park" />
                                <b>Документы от таксопарка</b>
                            </div>
                            <p>АВР между СМЗ и таксопарком: {bare(world.period.label)}</p>
                            <p>Агентский договор</p>
                        </div>
                    </>
                )}
            </section>
            <div className="wt-sx__well">
                <p>
                    Для подписания необходимо наличие мобильного приложения{' '}
                    <em>eGov EgovMobile</em>
                </p>
                <small>После нажатия на кнопку вы будете перенаправлены в eGov EgovMobile</small>
                <Tap id="open_egov" target={target} tap={tap} className="wt-sx__violet">
                    Подписать в eGov EgovMobile
                </Tap>
            </div>
            <Tap id="save" target={target} tap={tap} className="wt-sx__indigo">Сохранить</Tap>
        </TpSheet>
    );
};

/** После eGov: главная страница кабинета в состоянии «подписано» — так её
 *  видят водители в окне Такси.Про (кадры 6–10.11.2026): плашка «Все документы
 *  успешно подписаны», зелёные «Вы успешно подписали АВР и закрыли ЭДО» и «Нет
 *  документов на подписание», синяя «Участвовать в розыгрыше». Дальше — закрыть
 *  окно крестиком. */
export const TpSheetSigned = ({ world, tap, target }) => (
    <TpSheet tap={tap} target={target} dock={<SxDock tap={tap} target={target} />}>
        <SxProfileCard tap={tap} target={target} />
        <p className="wt-sx__lav">Все документы успешно подписаны</p>
        <SxSection title="Документы от Яндекса">
            <p className="wt-sx__ok">✅ Вы успешно подписали АВР и закрыли ЭДО</p>
        </SxSection>
        <Tap id="sp_raffle" target={target} tap={tap} className="wt-sx__blue">
            Участвовать в розыгрыше
        </Tap>
        <SxSection title="Документы от таксопарка">
            <p className="wt-sx__ok">Нет документов на подписание</p>
        </SxSection>
        <SxHistory world={world} signed />
    </TpSheet>
);
