import React from 'react';

import { Tap, TrainMark, Row } from './screenKit';
import { SaparLogo, SpGroupIcon, SpNavIcon, TpIcon, TpNavIcon } from './appIcons';
import { TRAINEE } from './scenarioTaxiPro';

/* Экраны приложения Такси.Про и кабинета Сапар, открытого поверх него.
 *
 * Нарисовано по настоящим кадрам за июль–август 2026. Отсюда и палитра
 * приложения: светло-серый фон #f3f4f6, белые карточки, ЯРКО-жёлтая кнопка
 * #fce000 с чёрной надписью и приглушённое золото #c8a417 у значков, ссылок и
 * активной вкладки. Раньше приложение было нарисовано «в целом жёлтым», и
 * узнать в нём Такси.Про было нельзя.
 *
 * Кабинет здесь именно ОКНО поверх приложения (со своей шапкой и крестиком) —
 * так его и видит водитель. На сайте Сапар те же данные выглядят иначе, и
 * второй тренажёр рисует их по-своему: это не дубль, а два разных пути, которые
 * человек должен различать.
 */

/** Период в сценарии хранится с предлогом («за Июль 2026») — так его читает
 *  барс в реплике. На экранах кабинета предлог свой, поэтому лишний срезаем;
 *  month() отдаёт только название месяца — кабинет пишет «за Июль подписаны»,
 *  без года. */
const bare = (label) => String(label || '').replace(/^за\s+/i, '');
const month = (label) => bare(label).split(' ')[0];

/* ── Нижняя навигация приложения ──────────────────────────────────────────
   Пять кнопок, из них правильная одна. Остальные — ловушки сценария, поэтому
   они настоящие кнопки, а не картинки: нажать не туда должно быть можно. */
const NAV = [
    ['nav_home', 'Главная', 'home'],
    ['nav_kaspi', 'Kaspi QR', 'kaspi'],
    ['nav_baiga', 'Байга', 'baiga'],
    ['nav_docs', 'Документы', 'docs'],
    ['nav_profile', 'Профиль', 'profile'],
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
                    {/* Красный счётчик неподписанных актов — в приложении он и
                        есть причина, по которой водитель заходит в раздел. */}
                    {id === 'nav_docs' && badge > 0 && (
                        <em className="wt-tp__badge">{badge}</em>
                    )}
                </i>
                {label}
            </Tap>
        ))}
    </nav>
);

/* ── Карточка акта ────────────────────────────────────────────────────────
   mode: 'sign' — нужна подпись, 'wait' — подписано в eGov, но список не обновлён,
   'signed' — статус пришёл. Три состояния, а не два: между подписью и статусом
   есть пауза, и именно она заставляет думать, что подписание не сработало. */
const TpAct = ({ period, number, mode, tap, target, targetable }) => (
    <article className={`wt-tp__act is-${mode}`}>
        <div className="wt-tp__act-head">
            <TpIcon name="doc" className="wt-tp__act-ico" />
            <b>Акт выполненных работ <span>{period.iso}</span></b>
            <span className="wt-tp__act-tools" aria-hidden="true">
                <TpIcon name="share" />
                <TpIcon name="download" />
            </span>
        </div>
        <small><b>Номер документа</b>: {number}</small>
        <small><b>Дата создания</b>: {period.human}</small>
        {mode === 'sign' ? (
            <Tap
                id="press_sign"
                target={targetable ? target : null}
                tap={tap}
                className="wt-tp__act-sign"
            >
                <TpIcon name="sign" />
                Подписать
            </Tap>
        ) : (
            <div className="wt-tp__act-status">
                <Tap id="open_act" target={target} tap={tap}>
                    <TpIcon name="eye" />
                    Просмотр
                </Tap>
                <span className={mode === 'signed' ? 'is-done' : 'is-wait'}>
                    {mode === 'signed' ? <TpIcon name="check2" /> : <TpIcon name="history" />}
                    {mode === 'signed' ? 'Подписан' : 'Ожидает подписи'}
                </span>
            </div>
        )}
    </article>
);

/* ── Главная ─────────────────────────────────────────────────────────────── */

export const TpHome = ({ tap, target }) => (
    <div className="wt-screen wt-tp">
        <TrainMark />
        <header className="wt-tp__head">
            <b>Добрый день,<br />{TRAINEE.first}!</b>
            <span className="wt-tp__avatar" aria-hidden="true">
                <svg viewBox="0 0 40 40" aria-hidden="true">
                    <circle cx="20" cy="20" r="20" fill="#e9eaee" />
                    <circle cx="20" cy="15.5" r="6.4" fill="#3f4756" />
                    <path d="M7.5 36a12.5 12.5 0 0 1 25 0Z" fill="#2f3542" />
                </svg>
            </span>
        </header>
        {/* Лента жёлтых баннеров с выглядывающими соседями — первое, что видно
            на главной, и первое, во что попадают мимо кнопок. */}
        <div className="wt-tp__promos">
            <Tap id="banner" target={target} tap={tap} className="wt-tp__promo is-peek"
                aria-label="Предыдущий баннер" />
            <Tap id="banner" target={target} tap={tap} className="wt-tp__promo">
                <em>учебный баннер</em>
                <b>Теперь смена авто и номера телефона прямо в Такси.Про</b>
                <span>Обновление</span>
            </Tap>
            <Tap id="banner" target={target} tap={tap} className="wt-tp__promo is-peek"
                aria-label="Следующий баннер" />
        </div>
        <section className="wt-tp__card">
            <div className="wt-tp__card-head">
                <b>Выбранный таксопарк</b>
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
        <section className="wt-tp__card">
            <div className="wt-tp__card-head">
                <b><TpIcon name="refresh" />Ваш баланс</b>
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
                всегда. Про блокировку водитель узнаёт только из окна с ошибкой
                после нажатия, и никакой предупреждающей строки под кнопкой в
                приложении нет: раньше она здесь была нарисована, и человек искал
                её глазами в настоящем Такси.Про. */}
            <Tap id="withdraw" target={target} tap={tap} className="wt-tp__withdraw">
                Вывод с баланса
            </Tap>
        </section>
        <div className="wt-tp__quick">
            <Tap id="promo" target={target} tap={tap} className="is-green">
                Участвовать в розыгрыше
            </Tap>
            <Tap id="promo" target={target} tap={tap} className="is-violet">
                Байга для водителей
            </Tap>
        </div>
        <TpNav active="nav_home" tap={tap} target={target} badge={2} />
    </div>
);

/* ── Документы ───────────────────────────────────────────────────────────── */

/* Вкладки раздела — НЕ пилюли в серой подложке, а две надписи с жёлтой чертой
   под активной. Это тот случай, когда форма важнее содержания: по черте раздел
   и опознаётся. */
const TpDocsHead = ({ tap, target }) => (
    <div className="wt-tp__docs-head">
        <h3>Документы</h3>
        <div className="wt-tp__tabs">
            <Tap id="tab_acts" target={target} tap={tap} className="is-active">Акты</Tap>
            <Tap id="tab_contracts" target={target} tap={tap}>Договоры</Tap>
        </div>
    </div>
);

export const TpDocuments = ({ world, tap, target }) => (
    <div className="wt-screen wt-tp">
        <TrainMark />
        <TpDocsHead tap={tap} target={target} />
        <TpAct period={world.period} number="334429666432000000" mode="sign"
            tap={tap} target={target} targetable />
        <TpAct period={world.period} number="334429106061000000" mode="sign"
            tap={tap} target={target} />
        <TpAct period={world.earlier} number="334408186500000000" mode="signed"
            tap={tap} target={target} />
        <TpNav active="nav_docs" tap={tap} target={target} badge={2} />
    </div>
);

export const TpCheck = ({ world, tap, target }) => {
    const done = !!world.refreshed;
    return (
        <div className="wt-screen wt-tp">
            <TrainMark />
            <TpDocsHead tap={tap} target={target} />
            {!done && (
                <Tap id="refresh" target={target} tap={tap} className="wt-tp__refresh">
                    <TpIcon name="refresh" />
                    Потяните вниз, чтобы обновить
                </Tap>
            )}
            <TpAct period={world.period} number="334429666432000000"
                mode={done ? 'signed' : 'wait'} tap={tap} target={target} />
            <TpAct period={world.period} number="334429106061000000"
                mode={done ? 'signed' : 'wait'} tap={tap} target={target} />
            <TpAct period={world.earlier} number="334408186500000000" mode="signed"
                tap={tap} target={target} />
            {done && (
                <div className="wt-tp__done">
                    <b>На всех актах статус «Подписан»</b>
                    <Tap id="finish" target={target} tap={tap} className="wt-tp__withdraw">
                        Завершить урок
                    </Tap>
                </div>
            )}
            <TpNav active="nav_docs" tap={tap} target={target} badge={done ? 0 : 2} />
        </div>
    );
};

/* ── Кабинет Сапар поверх приложения ─────────────────────────────────────
   Приложение открывает кабинет своим окном: сверху полоса с названием и
   крестиком, дальше та же страница, что и в браузере. Поэтому шапка, нижняя
   панель и карточки здесь ровно такие же, как в тренажёре сайта, — водитель
   должен видеть, что это одно и то же место, открытое двумя способами. */

const SP_NAV = [
    ['sp_profile', 'profile', 'Профиль'],
    ['sp_docs', 'docs', 'Документы'],
    ['sp_help', 'help', 'Помощь'],
    ['sp_exit', 'exit', 'Выход'],
];

const SpNav = ({ tap, target }) => (
    <nav className="wt-sp__nav" aria-label="Навигация кабинета Сапар">
        {SP_NAV.map(([id, icon, label]) => (
            <Tap key={id} id={id} target={target} tap={tap}
                className={id === 'sp_profile' ? 'is-active' : ''}>
                <i aria-hidden="true"><SpNavIcon name={icon} /></i>
                {label}
            </Tap>
        ))}
    </nav>
);

/* Шапка Chrome Custom Tab — того самого «браузера внутри приложения», которым
   Такси.Про открывает кабинет. Своего окна с адресом и крестиком у приложения
   НЕТ: на кадрах это всегда браузер, и отличается он ровно этой полосой —
   крестик и шеврон слева, заголовок страницы с доменом под ним по центру,
   «поделиться», закладка и «⋮» справа. Крестик здесь настоящий (на нём висит
   ловушка close_webview): в Custom Tab он и закрывает страницу. */
const SpHead = ({ title, tap, target }) => (
    <header className="wt-sp__tab">
        <Tap id="close_webview" target={target} tap={tap} className="wt-sp__tab-btn"
            aria-label="Закрыть страницу подписания">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1"
                strokeLinecap="round" aria-hidden="true">
                <path d="M6 6l12 12M18 6 6 18" />
            </svg>
        </Tap>
        <Tap id="browser_menu" target={target} tap={tap} className="wt-sp__tab-btn"
            aria-label="Свернуть страницу">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1"
                strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="m6 9.5 6 6 6-6" />
            </svg>
        </Tap>
        <span className="wt-sp__tab-title">
            <b>{title}</b>
            <small>tps-driver.silt.kz</small>
        </span>
        <Tap id="browser_share" target={target} tap={tap} className="wt-sp__tab-btn"
            aria-label="Поделиться">
            <TpIcon name="share" />
        </Tap>
        <Tap id="browser_bookmark" target={target} tap={tap} className="wt-sp__tab-btn"
            aria-label="В закладки">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
                strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M7 4h10v16l-5-4-5 4Z" />
            </svg>
        </Tap>
        <Tap id="browser_menu" target={target} tap={tap} className="wt-sp__tab-btn"
            aria-label="Меню браузера">
            <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                <circle cx="12" cy="5" r="1.6" />
                <circle cx="12" cy="12" r="1.6" />
                <circle cx="12" cy="19" r="1.6" />
            </svg>
        </Tap>
    </header>
);

const SpBrandCard = ({ title, tap, target }) => (
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
);

const SpWelcome = () => (
    <div className="wt-sp__welcome">
        <div>
            <small>Добро пожаловать</small>
            <b>{TRAINEE.full}</b>
        </div>
        <em>{TRAINEE.status}</em>
    </div>
);

const SpSection = ({ title, children, className = '' }) => (
    <section className={`wt-sp__card wt-sp__section ${className}`.trim()}>
        <h3>{title}</h3>
        {children}
    </section>
);

/** Профиль кабинета с рекламным окном поверх — третий шаг инструкции. */
export const SpAd = ({ world, tap, target }) => (
    <div className="wt-screen wt-sp">
        <SpHead title="Мой профиль" tap={tap} target={target} />
        <div className="wt-sp__page">
            <div className="wt-sp__blurred" aria-hidden="true">
                <SpBrandCard title="Мой профиль" />
                <SpWelcome />
                <SpSection title="Документы от Яндекса">
                    <p className="wt-sp__state is-wait">
                        Документы от Яндекса за {month(world.period.label)} ожидают подписания.
                    </p>
                </SpSection>
                <SpSection title="Документы от таксопарка">
                    <b className="wt-sp__doc-name">Агентский договор</b>
                    <small>от таксопарка {TRAINEE.park}</small>
                </SpSection>
                <SpSection title="Информация о пользователе" className="wt-sp__user">
                    <Row label="ФИО">{TRAINEE.full}</Row>
                    <Row label="ИИН">{TRAINEE.iin}</Row>
                </SpSection>
            </div>
            {/* Реклама перекрывает кабинет целиком: нажатие «Подписать все
                документы» уходит в баннер, и человек решает, что кнопка не
                работает. */}
            <div className="wt-sp__ad" role="dialog" aria-label="Рекламное окно партнёра">
                <div className="wt-sp__ad-cover">
                    <span>Учебный партнёр</span>
                    <Tap id="close_ad" target={target} tap={tap} className="wt-sp__ad-x"
                        aria-label="Закрыть рекламное окно">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
                            strokeWidth="2.4" strokeLinecap="round" aria-hidden="true">
                            <path d="M6 6l12 12M18 6 6 18" />
                        </svg>
                    </Tap>
                </div>
                <div className="wt-sp__ad-body">
                    <span>Наши партнёры</span>
                    <b>Автосервис «Пример»</b>
                    <p>Учебный баннер вместо настоящей рекламы: техобслуживание и ремонт…</p>
                    <Tap id="ad_more" target={target} tap={tap} className="wt-link">Подробнее</Tap>
                </div>
            </div>
            <Tap id="sign_all" target={target} tap={tap} className="wt-blue wt-sp__behind">
                Подписать все документы
            </Tap>
            <SpNav tap={tap} target={target} />
        </div>
    </div>
);

export const SpProfile = ({ world, tap, target }) => (
    <div className="wt-screen wt-sp">
        <SpHead title="Мой профиль" tap={tap} target={target} />
        <div className="wt-sp__page">
            <SpBrandCard title="Мой профиль" tap={tap} target={target} />
            <SpWelcome />
            <SpSection title="Документы от Яндекса">
                <p className="wt-sp__state is-wait">
                    Документы от Яндекса за {month(world.period.label)} ожидают подписания.
                </p>
            </SpSection>
            <SpSection title="Документы от таксопарка">
                <b className="wt-sp__doc-name">Агентский договор</b>
                <small>от таксопарка {TRAINEE.park}</small>
                <Tap id="sign_all" target={target} tap={tap} className="wt-blue">
                    Подписать все документы
                </Tap>
            </SpSection>
            <SpSection title="Информация о пользователе" className="wt-sp__user">
                <Row label="ФИО">{TRAINEE.full}</Row>
                <Row label="ИИН">{TRAINEE.iin}</Row>
            </SpSection>
            <SpNav tap={tap} target={target} />
        </div>
    </div>
);

export const SpSignAll = ({ world, tap, toggle, target }) => {
    const open = !!world.docsExpanded;
    return (
        <div className="wt-screen wt-sp">
            <SpHead title="Подписать все документы" tap={tap} target={target} />
            <div className="wt-sp__page">
                <SpBrandCard title="Подписать все документы" tap={tap} target={target} />
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
                            <div className="wt-sp__group">
                                <div className="wt-sp__group-head">
                                    <SpGroupIcon kind="yandex" />
                                    <b>Документы от Яндекса</b>
                                </div>
                                <ul>
                                    <li>
                                        <span>АВР №66909180/26: {bare(world.period.label)}</span>
                                        <i aria-hidden="true">Скачать</i>
                                    </li>
                                </ul>
                            </div>
                            <div className="wt-sp__group">
                                <div className="wt-sp__group-head">
                                    <SpGroupIcon kind="park" />
                                    <b>Документы от Таксопарка</b>
                                </div>
                                <ul>
                                    <li>
                                        <span>Агентский договор: {bare(world.period.label)}</span>
                                        <i aria-hidden="true">Скачать</i>
                                    </li>
                                </ul>
                            </div>
                        </>
                    )}
                </section>
                <div className="wt-sp__well">
                    <p>
                        Для подписания необходимо наличие мобильного приложения{' '}
                        <em>eGov EgovMobile</em>
                    </p>
                    <small>После нажатия на кнопку вы будете перенаправлены в eGov EgovMobile</small>
                    <Tap id="open_egov" target={target} tap={tap} className="wt-violet">
                        Подписать в eGov EgovMobile
                    </Tap>
                </div>
                <Tap id="save" target={target} tap={tap} className="wt-indigo">Сохранить</Tap>
                <SpNav tap={tap} target={target} />
            </div>
        </div>
    );
};
