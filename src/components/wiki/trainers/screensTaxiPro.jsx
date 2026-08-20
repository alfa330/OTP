import React from 'react';

import { Tap, TrainMark, Row } from './screenKit';
import { TRAINEE } from './scenarioTaxiPro';

/* Экраны приложения Такси.Про и кабинета Сапар, открытого поверх него.
 *
 * Кабинет здесь именно ОКНО поверх приложения (со своей шапкой и крестиком) —
 * так его и видит водитель. На сайте Сапар те же данные выглядят иначе, и
 * второй тренажёр рисует их по-своему: это не дубль, а два разных пути, которые
 * человек должен различать.
 */

/* ── Нижняя навигация приложения ──────────────────────────────────────────
   Пять кнопок, из них правильная одна. Остальные — ловушки сценария, поэтому
   они настоящие кнопки, а не картинки: нажать не туда должно быть можно. */
const NAV = [
    ['nav_home', 'Главная', 'M4 11 12 4l8 7v8a1 1 0 0 1-1 1h-4v-6h-6v6H5a1 1 0 0 1-1-1Z'],
    ['nav_kaspi', 'Kaspi QR', 'M4 4h6v6H4Zm10 0h6v6h-6ZM4 14h6v6H4Zm10 3h3v3h-3Z'],
    ['nav_baiga', 'Байга', 'M7 4h10v5a5 5 0 0 1-10 0Zm5 9v4m-4 3h8'],
    ['nav_docs', 'Документы', 'M7 3h7l4 4v14H7Zm7 0v4h4M10 12h6m-6 4h6'],
    ['nav_profile', 'Профиль', 'M12 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Zm-7 9a7 7 0 0 1 14 0'],
];

const TpNav = ({ active, tap, target }) => (
    <nav className="wt-tp__nav" aria-label="Навигация Такси.Про">
        {NAV.map(([id, label, path]) => (
            <Tap
                key={id}
                id={id}
                target={target}
                tap={tap}
                className={active === id ? 'is-active' : ''}
            >
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor"
                    strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d={path} />
                </svg>
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
        <div className="wt-tp__act-title">
            <b>Акт выполненных работ</b>
            <span>{period.iso}</span>
        </div>
        <small>Номер документа: {number}</small>
        <small>Дата создания: {period.human}</small>
        {mode === 'sign' ? (
            <Tap
                id="press_sign"
                target={targetable ? target : null}
                tap={tap}
                className="wt-tp__act-sign"
            >
                Подписать
            </Tap>
        ) : (
            <div className="wt-tp__act-status">
                <span className={mode === 'signed' ? 'is-done' : ''}>
                    {mode === 'signed' ? '✓✓ Подписан' : '◌ Ожидает подписи'}
                </span>
                <Tap id="open_act" target={target} tap={tap}>Открыть</Tap>
            </div>
        )}
    </article>
);

export const TpHome = ({ tap, target }) => (
    <div className="wt-screen wt-tp">
        <TrainMark />
        <header className="wt-tp__head">
            <div><span>Доброе утро,</span><b>{TRAINEE.first}!</b></div>
            <div className="wt-tp__avatar" aria-hidden="true" />
        </header>
        <Tap id="banner" target={target} tap={tap} className="wt-tp__banner">
            <em>учебный баннер</em>
            <b>Байга</b>
            <span>35 мест · призовой фонд 152 000 ₸</span>
        </Tap>
        <section className="wt-card">
            <div className="wt-card__head">
                <b>Выбранный таксопарк</b>
                <Tap id="change_park" target={target} tap={tap} className="wt-link">Изменить</Tap>
            </div>
            <ul className="wt-card__list">
                <li>{TRAINEE.park}</li>
                <li>{TRAINEE.car}</li>
                <li>{TRAINEE.phone}</li>
            </ul>
        </section>
        <section className="wt-card">
            <div className="wt-card__head">
                <b>Ваш баланс</b>
                <Tap id="history" target={target} tap={tap} className="wt-link">История</Tap>
            </div>
            <span className="wt-tp__balance">{TRAINEE.balance}</span>
            <Tap id="withdraw" target={target} tap={tap} className="wt-tp__withdraw">
                Вывод с баланса
            </Tap>
            {/* Эта подсказка есть и в приложении. Она и есть причина, по которой
                водитель вообще приходит подписывать документы. */}
            <p className="wt-note">Перед выводом средств убедитесь, что документы подписаны</p>
        </section>
        <div className="wt-tp__quick">
            <Tap id="promo" target={target} tap={tap}>Лимонад<br />для водителей</Tap>
            <Tap id="promo" target={target} tap={tap}>Байга<br />для лидеров</Tap>
        </div>
        <TpNav active="nav_home" tap={tap} target={target} />
    </div>
);

const TpDocsHead = ({ tap, target }) => (
    <div className="wt-tp__docs-head">
        <h3>Документы</h3>
        <div className="wt-tabs">
            <Tap id="tab_acts" target={target} tap={tap} className="is-active">Акты</Tap>
            <Tap id="tab_contracts" target={target} tap={tap}>Договоры</Tap>
        </div>
    </div>
);

export const TpDocuments = ({ world, tap, target }) => (
    <div className="wt-screen wt-tp">
        <TrainMark />
        <TpDocsHead tap={tap} target={target} />
        <TpAct period={world.period} number="334400000000000001" mode="sign"
            tap={tap} target={target} targetable />
        <TpAct period={world.period} number="334400000000000002" mode="sign"
            tap={tap} target={target} />
        <TpAct period={world.earlier} number="332000000000000003" mode="signed"
            tap={tap} target={target} />
        <TpNav active="nav_docs" tap={tap} target={target} />
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
                    ↻ Потяните вниз, чтобы обновить
                </Tap>
            )}
            <TpAct period={world.period} number="334400000000000001"
                mode={done ? 'signed' : 'wait'} tap={tap} target={target} />
            <TpAct period={world.period} number="334400000000000002"
                mode={done ? 'signed' : 'wait'} tap={tap} target={target} />
            <TpAct period={world.earlier} number="332000000000000003" mode="signed"
                tap={tap} target={target} />
            {done && (
                <div className="wt-tp__done">
                    <b>На всех актах статус «Подписан»</b>
                    <Tap id="finish" target={target} tap={tap} className="wt-primary">
                        Завершить урок
                    </Tap>
                </div>
            )}
            <TpNav active="nav_docs" tap={tap} target={target} />
        </div>
    );
};

/* ── Кабинет Сапар поверх приложения ─────────────────────────────────────── */

const SpNav = ({ tap, target }) => (
    <nav className="wt-sp__nav" aria-label="Навигация кабинета Сапар">
        <Tap id="sp_profile" target={target} tap={tap} className="is-active">Профиль</Tap>
        <Tap id="sp_docs" target={target} tap={tap}>Документы</Tap>
        <Tap id="sp_help" target={target} tap={tap}>Помощь</Tap>
        <Tap id="sp_exit" target={target} tap={tap}>Выход</Tap>
    </nav>
);

const SpHead = ({ tap, target }) => (
    <header className="wt-sp__head">
        <b>Подписание документов</b>
        <Tap id="close_webview" target={target} tap={tap} className="wt-sp__close"
            aria-label="Закрыть окно подписания">×</Tap>
    </header>
);

const SpBrand = ({ title }) => (
    <div className="wt-sp__brand">
        <span className="wt-sp__logo">sapar</span>
        <b>{title}</b>
    </div>
);

/** Профиль кабинета с рекламным окном поверх — третий шаг инструкции. */
export const SpAd = ({ world, tap, target }) => (
    <div className="wt-screen wt-sp">
        <SpHead tap={tap} target={target} />
        <div className="wt-sp__blurred" aria-hidden="true">
            <SpBrand title="Мой профиль" />
            <div className="wt-sp__welcome">
                <div><small>Добро пожаловать</small><b>{TRAINEE.short}</b></div>
                <em>{TRAINEE.status}</em>
            </div>
            <div className="wt-sp__section">
                <span>Документы от Яндекса</span>
                <b>Акт выполненных работ (АВР)</b>
                <small>{world.period.label}</small>
            </div>
        </div>
        {/* Реклама перекрывает кабинет целиком: нажатие «Подписать все документы»
            уходит в баннер, и человек решает, что кнопка не работает. */}
        <div className="wt-sp__ad" role="dialog" aria-label="Рекламное окно партнёра">
            <div className="wt-sp__ad-cover">
                <span>Учебный партнёр</span>
                <Tap id="close_ad" target={target} tap={tap} className="wt-sp__ad-x"
                    aria-label="Закрыть рекламное окно">×</Tap>
            </div>
            <div className="wt-sp__ad-body">
                <span>Наши партнёры</span>
                <b>Автосервис «Пример»</b>
                <p>Учебный баннер вместо настоящей рекламы: техобслуживание и ремонт…</p>
                <Tap id="ad_more" target={target} tap={tap} className="wt-link">Подробнее</Tap>
            </div>
        </div>
        <Tap id="sign_all" target={target} tap={tap} className="wt-primary wt-sp__behind">
            Подписать все документы
        </Tap>
        <SpNav tap={tap} target={target} />
    </div>
);

export const SpProfile = ({ world, tap, target }) => (
    <div className="wt-screen wt-sp">
        <SpHead tap={tap} target={target} />
        <SpBrand title="Мой профиль" />
        <div className="wt-sp__welcome">
            <div><small>Добро пожаловать</small><b>{TRAINEE.short}</b></div>
            <em>{TRAINEE.status}</em>
        </div>
        <div className="wt-sp__section">
            <span>Документы от Яндекса</span>
            <b>Акт выполненных работ (АВР)</b>
            <small>{world.period.label}</small>
        </div>
        <div className="wt-sp__section">
            <span>Документы от таксопарка</span>
            <b>АВР между самозанятым водителем и таксопарком</b>
            <small>{world.period.label}</small>
        </div>
        <div className="wt-sp__section">
            <span>Информация о пользователе</span>
            <Row label="ФИО">{TRAINEE.short}</Row>
            <Row label="ИИН">{TRAINEE.iin}</Row>
        </div>
        <Tap id="sign_all" target={target} tap={tap} className="wt-primary">
            Подписать все документы
        </Tap>
        <SpNav tap={tap} target={target} />
    </div>
);

export const SpSignAll = ({ world, tap, toggle, target }) => {
    const open = !!world.docsExpanded;
    return (
        <div className="wt-screen wt-sp">
            <SpHead tap={tap} target={target} />
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
                    <div><b>АВР от Яндекса</b><small>{world.period.label} · учебный документ</small></div>
                    <div><b>АВР между СМЗ и таксопарком</b><small>{world.period.label} · учебный документ</small></div>
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
            <SpNav tap={tap} target={target} />
        </div>
    );
};
