import React from 'react';

import { Tap, TrainMark, Row } from './screenKit';
import { AppIcon, BrIcon, SaparLogo, SpGroupIcon, SpNavIcon } from './appIcons';
import { TRAINEE } from './scenarioTaxiPro';
import { SAPAR_URL } from './scenarioSapar';

/* Экраны запасного способа: домашний экран телефона, браузер и кабинет Сапар.
 *
 * Всё нарисовано по выгрузке настоящих скриншотов подписания за июль–август
 * 2026 (5,3 тыс. кадров из диалогов с водителями). Оттуда взяты и раскладка, и
 * дословные надписи, и палитра: кабинет — светло-серо-синий фон #eef1f6 с
 * белыми карточками в 24 px радиуса, синяя кнопка «Подписать все документы»,
 * сиреневая «Подписать в eGov EgovMobile» и индиговая «Сохранить». Смысл в том,
 * что водитель должен УЗНАТЬ экран: тренажёр по абстрактному макету учит
 * абстрактному приложению.
 *
 * Рамка браузера нарисована на каждом экране кабинета намеренно. Она и есть
 * отличие этого пути от приложения: адрес видно всегда, обновление страницы —
 * кнопка в этой рамке, а не жест в списке, и именно из-за обновления «не в тот
 * момент» подпись теряется.
 */

/** Период в сценарии хранится с предлогом («за Июль 2026») — так его читает
 *  барс в реплике. На экранах кабинета предлог свой, поэтому лишний срезаем;
 *  month() отдаёт только название месяца — кабинет пишет «за Июль подписаны»,
 *  без года. */
const bare = (label) => String(label || '').replace(/^за\s+/i, '');
const month = (label) => bare(label).split(' ')[0];

/* ── Домашний экран ───────────────────────────────────────────────────────── */

const APPS = [
    ['open_taxipro', 'Такси.Про', 'taxipro'],
    ['open_egov', 'eGov mobile', 'egov'],
    ['open_whatsapp', 'Мессенджер', 'chat'],
    ['open_settings', 'Настройки', 'settings'],
];

export const PhoneHome = ({ tap, target }) => (
    <div className="wt-screen wt-home">
        <TrainMark />
        <div className="wt-home__grid">
            {APPS.map(([id, label, app]) => (
                <Tap key={id} id={id} target={target} tap={tap} className="wt-home__app">
                    <i className="wt-ico" aria-hidden="true"><AppIcon app={app} /></i>
                    {label}
                </Tap>
            ))}
        </div>
        {/* Chrome стоит в доке, как на телефоне: искать его в общей сетке —
            не то действие, которое отрабатывает шаг. */}
        <div className="wt-home__dock">
            <Tap id="open_chrome" target={target} tap={tap} className="wt-home__app">
                <i className="wt-ico" aria-hidden="true"><AppIcon app="chrome" /></i>
                Chrome
            </Tap>
        </div>
    </div>
);

/* ── Браузер ──────────────────────────────────────────────────────────────── */

/* Панель браузера НИЖНЯЯ и с обновлением внутри адресной строки — так она и
   выглядит на телефонах водителей (проверено по кадрам: адрес по центру, ↻
   справа от него, «···» с краю). Верхняя панель с замочком, которая стояла
   здесь раньше, не встречается ни на одном настоящем кадре. */
const BrowserBar = ({ url, tap, target }) => (
    <div className="wt-br__bar">
        <Tap id="browser_back" target={target} tap={tap} className="wt-br__btn"
            aria-label="Назад">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M15 5.5 8 12l7 6.5" />
            </svg>
        </Tap>
        <div className="wt-br__pill">
            <Tap id="browser_tabs" target={target} tap={tap} className="wt-br__pill-btn"
                aria-label="Вкладки">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
                    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <rect x="4" y="4" width="16" height="16" rx="3" />
                    <path d="M4 9h16" />
                </svg>
            </Tap>
            <Tap id="focus_address" target={target} tap={tap} className="wt-br__pill-url">
                {url}
            </Tap>
            <Tap id="refresh" target={target} tap={tap} className="wt-br__pill-btn"
                aria-label="Обновить страницу">
                <BrIcon name="refresh" />
            </Tap>
        </div>
        <Tap id="browser_menu" target={target} tap={tap} className="wt-br__btn"
            aria-label="Меню браузера">
            <BrIcon name="dots" />
        </Tap>
    </div>
);

/** Страница в браузере: содержимое сверху, панель браузера снизу. */
const Browser = ({ url, tap, target, children }) => (
    <div className="wt-br">
        <div className="wt-br__page">{children}</div>
        <BrowserBar url={url} tap={tap} target={target} />
    </div>
);

export const ChromeBlank = ({ tap, target }) => (
    <div className="wt-screen wt-br-screen">
        <div className="wt-br">
            <div className="wt-br__page wt-br__start">
                <TrainMark>Учебный браузер</TrainMark>
                <div className="wt-br__logo" aria-hidden="true"><AppIcon app="chrome" /></div>
                <div className="wt-br__searchrow">
                    <Tap id="focus_address" target={target} tap={tap} className="wt-br__search">
                        <BrIcon name="search" />
                        <span>Поиск или адрес</span>
                    </Tap>
                    <Tap id="voice_search" target={target} tap={tap} className="wt-br__mic"
                        aria-label="Голосовой поиск">
                        <BrIcon name="mic" />
                    </Tap>
                </div>
                <div className="wt-br__tiles">
                    <Tap id="open_bookmark" target={target} tap={tap}>
                        <i aria-hidden="true">Н</i>Новости
                    </Tap>
                    <Tap id="open_bookmark" target={target} tap={tap}>
                        <i aria-hidden="true">П</i>Погода
                    </Tap>
                    <Tap id="open_bookmark" target={target} tap={tap}>
                        <i aria-hidden="true">К</i>Карты
                    </Tap>
                </div>
            </div>
        </div>
    </div>
);

export const ChromeAddress = ({ tap, target }) => (
    <div className="wt-screen wt-br-screen">
        <div className="wt-br">
            <div className="wt-br__omnibar">
                <span className="wt-br__input is-focused">сапар<i className="wt-caret" /></span>
                <Tap id="browser_menu" target={target} tap={tap} className="wt-br__cancel">
                    Отмена
                </Tap>
            </div>
            <div className="wt-br__suggest">
                {/* Правильная строка — не первая: в жизни первым стоит поиск,
                    и именно по нему нажимают, попадая куда угодно. */}
                <Tap id="google_search" target={target} tap={tap}>
                    <i aria-hidden="true"><BrIcon name="search" /></i>
                    <span><b>Найти в Google: сапар</b><small>поисковый запрос</small></span>
                </Tap>
                <Tap id="go_sapar" target={target} tap={tap}>
                    <i aria-hidden="true"><BrIcon name="link" /></i>
                    <span><b>{SAPAR_URL}</b><small>Кабинет водителя Sapar</small></span>
                </Tap>
                <Tap id="wrong_domain" target={target} tap={tap}>
                    <i aria-hidden="true"><BrIcon name="link" /></i>
                    <span><b>tps-driver.silt.com</b><small>похожий адрес</small></span>
                </Tap>
            </div>
            <div className="wt-br__keys" aria-hidden="true">
                {'йцукенгшщзхъфывапролджэячсмитьбю'.split('').map((letter, index) => (
                    <span key={`${letter}-${index}`}>{letter}</span>
                ))}
            </div>
        </div>
    </div>
);

/* ── Кабинет Сапар ────────────────────────────────────────────────────────
   Один каркас на все экраны кабинета: белая карточка-шапка со знаком sapar и
   переключателем языка, содержимое и плавающая нижняя панель из четырёх
   значков. Так кабинет и устроен — меняется только середина. */

const SP_NAV = [
    ['sp_profile', 'profile', 'Профиль'],
    ['open_documents', 'docs', 'Документы'],
    ['sp_help', 'help', 'Помощь'],
    ['sp_exit', 'exit', 'Выход'],
];

const SpNav = ({ tap, target, active = 'sp_profile' }) => (
    <nav className="wt-sp__nav" aria-label="Навигация кабинета">
        {SP_NAV.map(([id, icon, label]) => (
            <Tap
                key={id}
                id={id}
                target={target}
                tap={tap}
                className={active === id ? 'is-active' : ''}
            >
                <i aria-hidden="true"><SpNavIcon name={icon} /></i>
                {label}
            </Tap>
        ))}
    </nav>
);

/** Каркас страницы кабинета: шапка, содержимое, нижняя панель. */
const SaparPage = ({ title, tap, target, active, nav = true, children }) => (
    <div className="wt-sp__page">
        <TrainMark />
        <header className="wt-sp__card wt-sp__brand">
            <SaparLogo />
            <Tap id="sp_lang" target={target} tap={tap} className="wt-sp__lang">
                RU
                <svg viewBox="0 0 12 12" width="9" height="9" aria-hidden="true">
                    <path d="M2 4.5 6 8.5l4-4" fill="none" stroke="currentColor" strokeWidth="1.4"
                        strokeLinecap="round" strokeLinejoin="round" />
                </svg>
            </Tap>
            <h2>{title}</h2>
        </header>
        {children}
        {nav && <SpNav tap={tap} target={target} active={active} />}
    </div>
);

/** Приветственная карточка: тёмно-синий градиент, ФИО заглавными и статус
 *  самозанятого пилюлей справа — самая узнаваемая деталь кабинета. */
const SpWelcome = () => (
    <div className="wt-sp__welcome">
        <div>
            <small>Добро пожаловать</small>
            <b>{TRAINEE.full}</b>
        </div>
        <em>{TRAINEE.status}</em>
    </div>
);

/** Секция кабинета: заголовок ЗАГЛАВНЫМИ и карточка состояния внутри. */
const SpSection = ({ title, children, className = '' }) => (
    <section className={`wt-sp__card wt-sp__section ${className}`.trim()}>
        <h3>{title}</h3>
        {children}
    </section>
);

/** Плашка состояния внутри секции: серая, синяя (ждём), зелёная (готово). */
const SpState = ({ tone = 'idle', children }) => (
    <p className={`wt-sp__state is-${tone}`}>{children}</p>
);

/** Рекламная полоса кабинета — она стоит прямо в странице, между карточками,
 *  и это НЕ картинка: нажатие уводит к партнёрам, а не к подписанию. */
const SpPartners = ({ tap, target }) => (
    <Tap id="partners" target={target} tap={tap} className="wt-sp__partners">
        <span>
            <b>Наши партнёры</b>
            <small>Скидки и бонусы для водителей</small>
        </span>
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
            strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="m9 5 7 7-7 7" />
        </svg>
    </Tap>
);

/* ── Вход ─────────────────────────────────────────────────────────────────
   Логина с паролем у кабинета нет и никогда не было: вход — это подпись в eGov
   Mobile, после которой в карточке появляется ЭЦП водителя и загорается
   «Войти». Раньше здесь были нарисованы поля «Логин» и «Пароль», которых на
   настоящем экране не существует, — самая заметная неправда прежних экранов. */

export const SaparGuest = ({ tap, target }) => (
    <div className="wt-screen wt-br-screen">
        <Browser url={SAPAR_URL} tap={tap} target={target}>
            <div className="wt-sp__page wt-sp__page--login">
                <TrainMark />
                <div className="wt-sp__hero">
                    <SaparLogo />
                </div>
                <h2 className="wt-sp__title">
                    Электронный документооборот<br />для СМЗ водителей
                </h2>
                <p className="wt-sp__lead">
                    Авторизация и подписание документации доступна только через мобильное
                    устройство
                </p>
                {/* Карточка входа. Пока подписи нет, в ней стоит объяснение, чем
                    подписывать: ЭЦП водителя (ФИО, срок действия, ИИН) появится
                    в ней только ПОСЛЕ возврата из eGov, и до этого «Войти»
                    ничего не открывает — на этом и стоит ловушка шага. */}
                <div className="wt-sp__well">
                    <p>
                        Для подписания необходимо наличие мобильного приложения{' '}
                        <em>eGov EgovMobile</em>
                    </p>
                    <small>
                        После нажатия на кнопку вы будете перенаправлены в eGov EgovMobile
                    </small>
                    <Tap id="login_egov" target={target} tap={tap} className="wt-violet">
                        Подписать в eGov EgovMobile
                    </Tap>
                </div>
                <Tap id="login_password" target={target} tap={tap} className="wt-indigo">
                    Войти
                </Tap>
                <p className="wt-sp__terms">
                    Нажимая «Войти», вы соглашаетесь с <b>пользовательским соглашением,
                    политикой конфиденциальности</b> и <b>публичной офертой</b>
                </p>
                <div className="wt-sp__or"><span>или</span></div>
                <Tap id="sp_park_login" target={target} tap={tap} className="wt-link is-center">
                    Войти как таксопарк
                </Tap>
                <Tap id="sp_video" target={target} tap={tap} className="wt-sp__accordion">
                    <span aria-hidden="true">›</span>ВИДЕОИНСТРУКЦИЯ
                </Tap>
            </div>
        </Browser>
    </div>
);

/* ── Профиль ─────────────────────────────────────────────────────────────── */

export const SaparProfile = ({ world, tap, target }) => (
    <div className="wt-screen wt-br-screen">
        <Browser url={`${SAPAR_URL}/ru/my`} tap={tap} target={target}>
            <SaparPage title="Мой профиль" tap={tap} target={target} active="sp_profile">
                <SpWelcome />
                <SpSection title="Документы от Яндекса">
                    <SpState tone="wait">
                        Документы от Яндекса за {month(world.period.label)} ожидают подписания.
                    </SpState>
                </SpSection>
                <SpSection title="Документы от таксопарка">
                    <SpState>Нет документов на подписание</SpState>
                </SpSection>
                <SpPartners tap={tap} target={target} />
                <SpSection title="Информация о пользователе" className="wt-sp__user">
                    <Row label="ФИО">{TRAINEE.full}</Row>
                    <Row label="ИИН">{TRAINEE.iin}</Row>
                </SpSection>
            </SaparPage>
        </Browser>
    </div>
);

/* ── Мои документы ───────────────────────────────────────────────────────── */

/** Карточка документа в списке: номер с периодом, источник и плашка статуса.
 *  Именно так подписанное и просроченное различаются в кабинете. */
const SpDoc = ({ number, period, source, status, tone }) => (
    <article className="wt-sp__card wt-sp__doc">
        <b>Документ №{number} — {bare(period)}</b>
        <small>{source}</small>
        <span className={`wt-sp__badge is-${tone}`}>АВР: {status}</span>
    </article>
);

export const SaparDocuments = ({ world, tap, target }) => (
    <div className="wt-screen wt-br-screen">
        <Browser url={`${SAPAR_URL}/ru/documents`} tap={tap} target={target}>
            <SaparPage title="Мои документы" tap={tap} target={target} active="open_documents">
                <div className="wt-sp__tabs">
                    <Tap id="tab_yandex" target={target} tap={tap} className="is-active">
                        Документы от Яндекс
                    </Tap>
                    <Tap id="tab_park" target={target} tap={tap}>Документы от таксопарка</Tap>
                </div>
                <SpDoc number="334409096919000000" period={world.period.label}
                    source="Яндекс Такси / Доставка" status="Не подписан" tone="todo" />
                <SpDoc number="334409106061000000" period={world.period.label}
                    source="Яндекс Такси / Доставка" status="Не подписан" tone="todo" />
                <SpDoc number="332019485070000000" period={world.earlier.label}
                    source="Яндекс Такси / Доставка" status="Подписан" tone="done" />
                <Tap id="sign_all" target={target} tap={tap} className="wt-blue">
                    Подписать все документы
                </Tap>
            </SaparPage>
        </Browser>
    </div>
);

/* ── Лист подписания ─────────────────────────────────────────────────────── */

/** Группа документов в листе: кружок-ярлык, название и строки со «Скачать». */
const SpGroup = ({ kind, title, rows }) => (
    <div className="wt-sp__group">
        <div className="wt-sp__group-head">
            <SpGroupIcon kind={kind} />
            <b>{title}</b>
        </div>
        <ul>
            {rows.map((row) => (
                <li key={row}>
                    <span>{row}</span>
                    <i aria-hidden="true">Скачать</i>
                </li>
            ))}
        </ul>
    </div>
);

/** Карточка «нужен eGov» — общая для листа подписания и экрана сохранения. */
const SpEgovWell = ({ tap, target, pulse = false }) => (
    <div className="wt-sp__well">
        <p>
            Для подписания необходимо наличие мобильного приложения{' '}
            <em>eGov EgovMobile</em>
        </p>
        <small>После нажатия на кнопку вы будете перенаправлены в eGov EgovMobile</small>
        <Tap id="open_egov" target={target} tap={tap}
            className={`wt-violet${pulse ? ' wt-pulse' : ''}`}>
            Подписать в eGov EgovMobile
        </Tap>
    </div>
);

/** Лист подписания: кто подписывает, что подписывается и чем. */
export const SaparSignSheet = ({ world, tap, toggle, target }) => {
    const open = !!world.docsExpanded;
    return (
        <div className="wt-screen wt-br-screen">
            <Browser url={`${SAPAR_URL}/ru/sign`} tap={tap} target={target}>
                <SaparPage title="Подписать все документы" tap={tap} target={target}
                    active="open_documents">
                    <p className="wt-sp__driver">
                        Водитель: {TRAINEE.full}<br />({TRAINEE.iin})
                    </p>
                    <section className="wt-sp__card wt-sp__sheet">
                        <button
                            type="button"
                            className={`wt-sp__collapse${open ? ' is-open' : ''}`}
                            onClick={() => toggle('docsExpanded')}
                            aria-expanded={open}
                        >
                            <span aria-hidden="true">›</span>ДОКУМЕНТЫ НА ПОДПИСАНИЕ
                        </button>
                        {open && (
                            <>
                                <SpGroup
                                    kind="yandex"
                                    title="Документы от Яндекса"
                                    rows={[`АВР №66909180/26: ${bare(world.period.label)}`]}
                                />
                                <SpGroup
                                    kind="park"
                                    title="Документы от Таксопарка"
                                    rows={[`АВР №1: ${bare(world.period.label)}`]}
                                />
                            </>
                        )}
                    </section>
                    <SpEgovWell tap={tap} target={target} />
                    <Tap id="save" target={target} tap={tap} className="wt-indigo">Сохранить</Tap>
                </SaparPage>
            </Browser>
        </div>
    );
};

/** Подпись получена, но НЕ сохранена — самый пропускаемый экран.
 *  В кабинете он выглядит ровно как лист подписания: ничего не меняется, и
 *  именно поэтому «Сохранить» пропускают. Разницу даёт одна плашка сверху. */
export const SaparSave = ({ world, tap, target }) => (
    <div className="wt-screen wt-br-screen">
        <Browser url={`${SAPAR_URL}/ru/sign`} tap={tap} target={target}>
            <SaparPage title="Подписать все документы" tap={tap} target={target}
                active="open_documents">
                <p className="wt-sp__driver">
                    Водитель: {TRAINEE.full}<br />({TRAINEE.iin})
                </p>
                <SpSection title="Подпись получена">
                    <SpState tone="wait">
                        Подпись из eGov EgovMobile получена. Документы будут отправлены
                        после сохранения.
                    </SpState>
                </SpSection>
                <SpEgovWell tap={tap} target={target} />
                <Tap id="save" target={target} tap={tap} className="wt-indigo wt-pulse">
                    Сохранить
                </Tap>
                <p className="wt-note">
                    Без этой кнопки документы останутся со статусом «Не подписан».
                </p>
            </SaparPage>
        </Browser>
    </div>
);

/* ── Статус после сохранения ─────────────────────────────────────────────── */

export const SaparStatus = ({ world, tap, target }) => {
    const done = !!world.refreshed;
    return (
        <div className="wt-screen wt-br-screen">
            <Browser url={`${SAPAR_URL}/ru/my`} tap={tap} target={target}>
                <SaparPage title="Мой профиль" tap={tap} target={target} active="sp_profile">
                    {done ? (
                        <p className="wt-sp__banner">Все документы успешно подписаны</p>
                    ) : (
                        <p className="wt-note">
                            Сохранено. Статус обновится после перезагрузки страницы.
                        </p>
                    )}
                    <SpWelcome />
                    <SpSection title="Документы от Яндекса">
                        <SpState tone={done ? 'done' : 'wait'}>
                            {done
                                ? `Документы от Яндекса за ${month(world.period.label)} подписаны.`
                                : `Документы от Яндекса за ${month(world.period.label)} ожидают подписания.`}
                        </SpState>
                    </SpSection>
                    <SpSection title="Документы от таксопарка">
                        <SpState tone={done ? 'done' : 'idle'}>
                            {done ? 'Все документы подписаны.' : 'Нет документов на подписание'}
                        </SpState>
                    </SpSection>
                    {done && (
                        <Tap id="finish" target={target} tap={tap} className="wt-blue">
                            Завершить урок
                        </Tap>
                    )}
                    <SpPartners tap={tap} target={target} />
                </SaparPage>
            </Browser>
        </div>
    );
};
