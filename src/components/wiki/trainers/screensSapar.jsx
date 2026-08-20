import React from 'react';

import { Tap, TrainMark, Row } from './screenKit';
import { TRAINEE } from './scenarioTaxiPro';
import { SAPAR_URL } from './scenarioSapar';

/* Экраны запасного способа: домашний экран телефона, браузер и сайт Сапар.
 *
 * Браузерная рамка нарисована на каждом экране кабинета намеренно. Она и есть
 * отличие этого пути от приложения: адрес видно всегда, обновление страницы —
 * кнопка в этой рамке, а не жест в списке, и именно из-за обновления «не в тот
 * момент» подпись теряется.
 */

/* ── Домашний экран ───────────────────────────────────────────────────────── */

const APPS = [
    ['open_taxipro', 'Такси.Про', 'wt-ico--taxi', 'ТП'],
    ['open_egov', 'eGov Mobile', 'wt-ico--egov', 'eG'],
    ['open_whatsapp', 'Мессенджер', 'wt-ico--chat', '💬'],
    ['open_settings', 'Настройки', 'wt-ico--settings', '⚙'],
];

export const PhoneHome = ({ tap, target }) => (
    <div className="wt-screen wt-home">
        <TrainMark />
        <div className="wt-home__grid">
            {APPS.map(([id, label, cls, glyph]) => (
                <Tap key={id} id={id} target={target} tap={tap} className="wt-home__app">
                    <i className={`wt-ico ${cls}`} aria-hidden="true">{glyph}</i>
                    {label}
                </Tap>
            ))}
        </div>
        {/* Chrome стоит в доке, как на телефоне: искать его в общей сетке —
            не то действие, которое отрабатывает шаг. */}
        <div className="wt-home__dock">
            <Tap id="open_chrome" target={target} tap={tap} className="wt-home__app">
                <i className="wt-ico wt-ico--chrome" aria-hidden="true">◍</i>
                Chrome
            </Tap>
        </div>
    </div>
);

/* ── Браузер ──────────────────────────────────────────────────────────────── */

/** Рамка браузера: адрес, обновление, вкладки, меню. */
const Browser = ({ url, tap, target, children, secure = true }) => (
    <div className="wt-br">
        <div className="wt-br__bar">
            <span className={`wt-br__url${secure ? ' is-secure' : ''}`}>
                {secure && <i aria-hidden="true">🔒</i>}{url}
            </span>
            <Tap id="refresh" target={target} tap={tap} className="wt-br__btn"
                aria-label="Обновить страницу">↻</Tap>
            <Tap id="browser_tabs" target={target} tap={tap} className="wt-br__btn"
                aria-label="Вкладки">▣</Tap>
            <Tap id="browser_menu" target={target} tap={tap} className="wt-br__btn"
                aria-label="Меню браузера">⋮</Tap>
        </div>
        <div className="wt-br__page">{children}</div>
    </div>
);

export const ChromeBlank = ({ tap, target }) => (
    <div className="wt-screen wt-br-screen">
        <div className="wt-br">
            <div className="wt-br__bar">
                <Tap id="focus_address" target={target} tap={tap} className="wt-br__input">
                    Поиск или адрес
                </Tap>
                <Tap id="voice_search" target={target} tap={tap} className="wt-br__btn"
                    aria-label="Голосовой поиск">🎤</Tap>
                <Tap id="browser_menu" target={target} tap={tap} className="wt-br__btn"
                    aria-label="Меню браузера">⋮</Tap>
            </div>
            <div className="wt-br__page wt-br__start">
                <TrainMark>Учебный браузер</TrainMark>
                <div className="wt-br__tiles">
                    <Tap id="open_bookmark" target={target} tap={tap}>Новости</Tap>
                    <Tap id="open_bookmark" target={target} tap={tap}>Погода</Tap>
                    <Tap id="open_bookmark" target={target} tap={tap}>Карты</Tap>
                </div>
            </div>
        </div>
    </div>
);

export const ChromeAddress = ({ tap, target }) => (
    <div className="wt-screen wt-br-screen">
        <div className="wt-br">
            <div className="wt-br__bar">
                <span className="wt-br__input is-focused">сапар<i className="wt-caret" /></span>
                <Tap id="browser_menu" target={target} tap={tap} className="wt-br__btn"
                    aria-label="Меню браузера">⋮</Tap>
            </div>
            <div className="wt-br__suggest">
                {/* Правильная строка — не первая: в жизни первым стоит поиск,
                    и именно по нему нажимают, попадая куда угодно. */}
                <Tap id="google_search" target={target} tap={tap}>
                    <i aria-hidden="true">🔍</i>
                    <span><b>Найти в Google: сапар</b><small>поисковый запрос</small></span>
                </Tap>
                <Tap id="go_sapar" target={target} tap={tap}>
                    <i aria-hidden="true">🔗</i>
                    <span><b>{SAPAR_URL}</b><small>личный кабинет Сапар</small></span>
                </Tap>
                <Tap id="wrong_domain" target={target} tap={tap}>
                    <i aria-hidden="true">🔗</i>
                    <span><b>tps.silt.com</b><small>похожий адрес</small></span>
                </Tap>
            </div>
            <div className="wt-br__keys" aria-hidden="true">
                {'йцукенгшщзхфывапролджэячсмитьбю'.split('').map((letter, index) => (
                    <span key={`${letter}-${index}`}>{letter}</span>
                ))}
            </div>
        </div>
    </div>
);

/* ── Сайт Сапар ───────────────────────────────────────────────────────────── */

const SpBrand = ({ title }) => (
    <div className="wt-sp__brand">
        <span className="wt-sp__logo">sapar</span>
        <b>{title}</b>
    </div>
);

const SiteNav = ({ tap, target, authorized }) => (
    <nav className="wt-sp__nav" aria-label="Навигация кабинета">
        {authorized ? (
            <>
                <Tap id="sp_profile" target={target} tap={tap} className="is-active">Профиль</Tap>
                <Tap id="open_documents" target={target} tap={tap}>Документы</Tap>
                <Tap id="sp_help" target={target} tap={tap}>Помощь</Tap>
                <Tap id="sp_exit" target={target} tap={tap}>Выход</Tap>
            </>
        ) : (
            <>
                <Tap id="sp_help" target={target} tap={tap}>Помощь</Tap>
                <Tap id="sp_lang" target={target} tap={tap}>Язык: RU</Tap>
            </>
        )}
    </nav>
);

export const SaparGuest = ({ tap, target }) => (
    <div className="wt-screen wt-sp wt-sp--site">
        <Browser url={SAPAR_URL} tap={tap} target={target}>
            <TrainMark />
            <SpBrand title="Вход в личный кабинет" />
            <p className="wt-sp__hint">
                Авторизация и подписание документов доступны только с мобильного устройства.
            </p>
            {/* Поля логина и пароля нарисованы и НЕ работают: их отсутствие —
                частый вопрос водителя, и увидеть его лучше здесь. */}
            <div className="wt-sp__fakefields" aria-hidden="true">
                <span>Логин</span>
                <span>Пароль</span>
            </div>
            <Tap id="login_password" target={target} tap={tap} className="wt-ghost">
                Войти по логину и паролю
            </Tap>
            <Tap id="login_egov" target={target} tap={tap} className="wt-primary">
                Войти через eGov Mobile
            </Tap>
            <SiteNav tap={tap} target={target} />
        </Browser>
    </div>
);

export const SaparProfile = ({ world, tap, target }) => (
    <div className="wt-screen wt-sp wt-sp--site">
        <Browser url={SAPAR_URL} tap={tap} target={target}>
            <TrainMark />
            <SpBrand title="Мой профиль" />
            <div className="wt-sp__welcome">
                <div><small>Добро пожаловать</small><b>{TRAINEE.short}</b></div>
                <em>{TRAINEE.status}</em>
            </div>
            <div className="wt-sp__section">
                <span>Информация о пользователе</span>
                <Row label="ФИО">{TRAINEE.short}</Row>
                <Row label="ИИН">{TRAINEE.iin}</Row>
            </div>
            <div className="wt-sp__section">
                <span>Документы от Яндекса</span>
                <b>Акт выполненных работ (АВР)</b>
                <small>{world.period.label}</small>
                <Tap id="open_doc" target={target} tap={tap} className="wt-link">Открыть файл</Tap>
            </div>
            <p className="wt-note">Подписание живёт в разделе «Документы».</p>
            <SiteNav tap={tap} target={target} authorized />
        </Browser>
    </div>
);

/** Список документов на подписание — здесь и стоит «Подписать все документы». */
export const SaparDocuments = ({ world, tap, target }) => (
    <div className="wt-screen wt-sp wt-sp--site">
        <Browser url={`${SAPAR_URL}/documents`} tap={tap} target={target}>
            <TrainMark />
            <SpBrand title="Документы" />
            <div className="wt-sp__doc">
                <div><b>АВР от Яндекса</b><small>{world.period.label}</small></div>
                <span className="wt-sp__badge">Не подписан</span>
                <Tap id="open_doc" target={target} tap={tap} className="wt-link">Открыть</Tap>
            </div>
            <div className="wt-sp__doc">
                <div><b>АВР между СМЗ и таксопарком</b><small>{world.period.label}</small></div>
                <span className="wt-sp__badge">Не подписан</span>
                <Tap id="open_doc" target={target} tap={tap} className="wt-link">Открыть</Tap>
            </div>
            <div className="wt-sp__doc is-done">
                <div><b>АВР от Яндекса</b><small>{world.earlier.label}</small></div>
                <span className="wt-sp__badge is-done">Подписан</span>
                <Tap id="open_doc" target={target} tap={tap} className="wt-link">Открыть</Tap>
            </div>
            <Tap id="sign_all" target={target} tap={tap} className="wt-primary">
                Подписать все документы
            </Tap>
            <SiteNav tap={tap} target={target} authorized />
        </Browser>
    </div>
);

/** Лист подписания: кто подписывает, что подписывается и чем. */
export const SaparSignSheet = ({ world, tap, toggle, target }) => {
    const open = !!world.docsExpanded;
    return (
        <div className="wt-screen wt-sp wt-sp--site">
            <Browser url={`${SAPAR_URL}/documents`} tap={tap} target={target}>
                <TrainMark />
                <SpBrand title="Подписать все документы" />
                <p className="wt-sp__hint">Водитель: {TRAINEE.full}<br />({TRAINEE.iin})</p>
                <button
                    type="button"
                    className={`wt-sp__collapse${open ? ' is-open' : ''}`}
                    onClick={() => toggle('docsExpanded')}
                    aria-expanded={open}
                >
                    Документы на подписание (2)
                    <span aria-hidden="true">{open ? '⌃' : '⌄'}</span>
                </button>
                {open && (
                    <div className="wt-sp__collapse-body">
                        <div><b>АВР от Яндекса</b><small>{world.period.label}</small></div>
                        <div><b>АВР между СМЗ и таксопарком</b><small>{world.period.label}</small></div>
                    </div>
                )}
                <p className="wt-sp__hint">
                    Для подписания нужно приложение <b>eGov Mobile</b>. После нажатия вы будете
                    перенаправлены в него.
                </p>
                <Tap id="open_egov" target={target} tap={tap} className="wt-secondary">
                    Подписать в eGov Mobile
                </Tap>
                <Tap id="save" target={target} tap={tap} className="wt-primary">Сохранить</Tap>
                <SiteNav tap={tap} target={target} authorized />
            </Browser>
        </div>
    );
};

/** Подпись получена, но НЕ сохранена — самый пропускаемый экран. */
export const SaparSave = ({ world, tap, target }) => (
    <div className="wt-screen wt-sp wt-sp--site">
        <Browser url={`${SAPAR_URL}/documents`} tap={tap} target={target}>
            <TrainMark />
            <SpBrand title="Подписать все документы" />
            <div className="wt-sp__signed">
                <b>Подпись получена</b>
                <small>Статус: подписано, но не сохранено</small>
            </div>
            <div className="wt-sp__doc">
                <div><b>АВР от Яндекса</b><small>{world.period.label}</small></div>
                <span className="wt-sp__badge">Не сохранён</span>
            </div>
            <div className="wt-sp__doc">
                <div><b>АВР между СМЗ и таксопарком</b><small>{world.period.label}</small></div>
                <span className="wt-sp__badge">Не сохранён</span>
            </div>
            <Tap id="open_egov" target={target} tap={tap} className="wt-secondary">
                Подписать в eGov Mobile
            </Tap>
            <Tap id="save" target={target} tap={tap} className="wt-primary wt-pulse">Сохранить</Tap>
            <p className="wt-note">Без этой кнопки документы не получат статус «Подписан».</p>
            <SiteNav tap={tap} target={target} authorized />
        </Browser>
    </div>
);

export const SaparStatus = ({ world, tap, target }) => {
    const done = !!world.refreshed;
    return (
        <div className="wt-screen wt-sp wt-sp--site">
            <Browser url={`${SAPAR_URL}/documents`} tap={tap} target={target}>
                <TrainMark />
                <SpBrand title="Документы" />
                {!done && <p className="wt-note">Сохранено. Статус обновится после перезагрузки страницы.</p>}
                <div className={`wt-sp__doc${done ? ' is-done' : ''}`}>
                    <div><b>АВР от Яндекса</b><small>{world.period.label}</small></div>
                    <span className={`wt-sp__badge${done ? ' is-done' : ''}`}>
                        {done ? 'Подписан' : 'Обработка'}
                    </span>
                </div>
                <div className={`wt-sp__doc${done ? ' is-done' : ''}`}>
                    <div><b>АВР между СМЗ и таксопарком</b><small>{world.period.label}</small></div>
                    <span className={`wt-sp__badge${done ? ' is-done' : ''}`}>
                        {done ? 'Подписан' : 'Обработка'}
                    </span>
                </div>
                {done && (
                    <div className="wt-sp__signed is-final">
                        <b>У обоих документов статус «Подписан»</b>
                        <Tap id="finish" target={target} tap={tap} className="wt-primary">
                            Завершить урок
                        </Tap>
                    </div>
                )}
                <SiteNav tap={tap} target={target} authorized />
            </Browser>
        </div>
    );
};
