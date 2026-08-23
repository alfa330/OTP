import React from 'react';

import { TrainMark } from './screenKit';
import {
    BALANCE_HISTORY, CAR, CARD_TABS, CONTRACTORS, DRIVER, GPS_LOG, GPS_TILES,
    HOME, INCOME, PARK, PARKS, PHOTO_DAYS, TRANSACTIONS,
} from './fleetData';

/* Учебная Диспетчерская — вторая вкладка того же окна браузера.
 *
 * ЗАЧЕМ ОНА ЗДЕСЬ. Оператор заводит обращение не вслепую: прежде чем выбрать
 * категорию, он смотрит в кабинет таксопарка. В сценарии про комиссию это
 * решает всё — в «Ведомости» видно, что сервис удержал своё двумя строками, а
 * парк третьей, и только там вопрос водителя превращается в правильную ветку.
 *
 * УРОКА ЗДЕСЬ НЕТ. По кабинету ходят свободно: ни шагов, ни ловушек, ни
 * «нажми не туда». Это справочник, и наказывать за то, что человек в него
 * заглянул, значит отучать в него заглядывать.
 *
 * ЧТО СКОПИРОВАНО, А ЧТО НЕТ. Повторены раскладка, названия разделов, состав
 * колонок и пустые состояния — по ним кабинет и узнаётся. НЕ повторены логотип
 * и фирменный знак: клон не должен выдавать себя за чужой кабинет, поэтому
 * слева стоит наш нейтральный знак, а сверху — плашка «Учебная среда».
 *
 * Данные целиком вымышлены, см. fleetData.js.
 */

/* Цвета сняты пипеткой со скриншотов кабинета (23.08.2026): тёплый серый фон
   #f5f4f2, зелёный #029154, красный #fc5230, суммы #cc2d32, синие ссылки
   #4060e3. «Примерно серый» вместо тёплого сразу читается как чужой экран. */

const Icon = ({ name }) => {
    const d = {
        home: 'M4 10.5 12 4l8 6.5V20H4z',
        people: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM4 20a8 8 0 0 1 16 0',
        car: 'M5 16h14M6.5 16V11l1.7-4h7.6l1.7 4v5M8 19v-3m8 3v-3',
        wallet: 'M3 8h15a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Zm13 5h2',
        search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm10 2-4.5-4.5',
        info: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Zm0-14v.5m0 3.5v5',
        cap: 'M3 9.5 12 5l9 4.5-9 4.5Zm3 3V17c0 1 2.7 2.5 6 2.5s6-1.5 6-2.5v-4.5',
        bell: 'M18 8a6 6 0 1 0-12 0c0 6-2 7-2 7h16s-2-1-2-7M10.5 20a2 2 0 0 0 3 0',
        chevron: 'm9 6 6 6-6 6',
        up: 'm6 15 6-6 6 6',
    }[name];
    return (
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
            strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d={d} />
        </svg>
    );
};

/* Нейтральный знак вместо чужого логотипа — см. шапку файла. */
const Mark = () => (
    <span className="wt-fl__mark" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor"
            strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M5 16h14M7 16v-5l1.6-4h6.8L17 11v5M9 19v-3m6 3v-3" />
        </svg>
    </span>
);

const SIDE = [
    ['home', 'home', 'Главная'],
    ['contractors', 'people', 'Исполнители'],
    ['vehicles', 'car', 'Автомобили'],
    ['goals', 'wallet', 'Программа лояльности'],
];

/** Каркас кабинета: узкий сайдбар со значками, шапка и содержимое. */
const Shell = ({ world, go, title, crumb = null, children }) => (
    <div className="wt-fl">
        <TrainMark>Учебная среда</TrainMark>

        <aside className="wt-fl__side">
            <button type="button" className="wt-fl__logo" onClick={() => go({ fleetView: 'home' })}
                aria-label="На главную">
                <Mark />
            </button>
            {SIDE.map(([view, icon, label]) => (
                <button
                    key={view}
                    type="button"
                    title={label}
                    aria-label={label}
                    className={`wt-fl__nav${world.fleetView === view
                        || (view === 'contractors' && world.fleetView === 'card') ? ' is-on' : ''}`}
                    onClick={() => go({ fleetView: view })}
                >
                    <Icon name={icon} />
                </button>
            ))}
            <span className="wt-fl__side-foot" aria-hidden="true"><Icon name="info" /></span>
        </aside>

        <div className="wt-fl__body">
            <header className="wt-fl__head">
                <h1>
                    {title}
                    {crumb ? (<><span className="wt-fl__arrow">→</span><b>{crumb}</b></>) : null}
                </h1>
                <span className="wt-fl__head-right">
                    <i aria-hidden="true"><Icon name="search" /></i>
                    <span className="wt-fl__park">
                        <b>{PARK.initials}</b>
                        <span>{PARK.name}<small>{PARK.city}</small></span>
                    </span>
                </span>
            </header>

            <div className="wt-fl__content">{children}</div>

            {/* Два круга в правом нижнем углу — обучение и колокол. В кабинете
                они есть на каждом экране, и без них угол выглядит пустым. */}
            <div className="wt-fl__fabs" aria-hidden="true">
                <span className="wt-fl__fab"><Icon name="cap" /></span>
                <span className="wt-fl__fab wt-fl__fab--bell"><Icon name="bell" /><i>52</i></span>
            </div>
        </div>
    </div>
);

/* ── Главная ─────────────────────────────────────────────────────────────── */

const Bars = ({ parts }) => {
    const total = parts.reduce((sum, p) => sum + p[2], 0) || 1;
    return (
        <div className="wt-fl__bars" aria-hidden="true">
            {parts.map(([label, color, value]) => (
                <i key={label} style={{ height: `${Math.max(6, (value / total) * 100)}%`, background: color }} />
            ))}
        </div>
    );
};

const Legend = ({ parts }) => (
    <ul className="wt-fl__legend">
        {parts.map(([label, color]) => (
            <li key={label}><i style={{ background: color }} />{label}</li>
        ))}
    </ul>
);

const FleetHome = ({ world, go }) => (
    <Shell world={world} go={go} title="Главная">
        <button type="button" className="wt-fl__chip wt-fl__chip--period">17–23 авг.</button>

        <div className="wt-fl__tiles">
            <section className="wt-fl__tile">
                <button type="button" className="wt-fl__tile-head"
                    onClick={() => go({ fleetView: 'contractors' })}>
                    <Icon name="people" /> Исполнители <Icon name="chevron" />
                </button>
                <div className="wt-fl__big">{HOME.online}<span>на линии</span></div>
                <Legend parts={HOME.onlineParts} />
                <Bars parts={[['a', '#029154', 2], ['b', '#fc9000', 5], ['c', '#fc5230', 51]]} />
                <div className="wt-fl__mini">
                    <div><span>Рейтинг парка</span><b>{HOME.rating}</b></div>
                    <div><span>Ср. время на линии</span><b>{HOME.avgOnline}</b></div>
                    <div><span>Новые</span><b>{HOME.fresh[0]} <em>{HOME.fresh[1]}</em></b></div>
                    <div><span>Отток</span><b>{HOME.churn[0]} <em>{HOME.churn[1]}</em></b></div>
                </div>
            </section>

            <section className="wt-fl__tile">
                <button type="button" className="wt-fl__tile-head"
                    onClick={() => go({ fleetView: 'vehicles' })}>
                    <Icon name="car" /> Автомобили <Icon name="chevron" />
                </button>
                <div className="wt-fl__big">{HOME.cars}<span>парковых автомобилей</span></div>
                <Legend parts={HOME.carParts} />
                <Bars parts={[['a', '#029154', 67], ['b', '#fce000', 7], ['c', '#8f97a8', 1], ['d', '#fc5230', 2]]} />
            </section>

            <section className="wt-fl__tile">
                <button type="button" className="wt-fl__tile-head"
                    onClick={() => go({ fleetView: 'goals' })}>
                    <Icon name="wallet" /> Программа лояльности <Icon name="chevron" />
                </button>
                <div className="wt-fl__loyalty">
                    <div className="wt-fl__grade is-bronze">
                        <b>Бронзовый</b>
                        <small>Скидка на Диспетчерскую</small>
                        <ul>
                            <li>2000 поездок в месяц</li>
                            <li>Заполнен профиль партнёра</li>
                            <li>Водители подтверждают занятость</li>
                            <li>Рейтинг парка не менее 4,3</li>
                        </ul>
                    </div>
                    <div className="wt-fl__grade">
                        <b>Серебряный</b>
                        <small>Скидка на Диспетчерскую</small>
                        <ul>
                            <li>30 новых водителей с 50 заказами</li>
                            <li>100 часов на линии с подтверждённым авто</li>
                        </ul>
                    </div>
                </div>
            </section>

            <section className="wt-fl__tile wt-fl__tile--narrow">
                <div className="wt-fl__tile-head is-plain">
                    <i className="wt-fl__dot is-red" aria-hidden="true" /> Проблемы {HOME.problems}
                </div>
                <button type="button" className="wt-fl__row-link"
                    onClick={() => go({ fleetView: 'contractors', fleetFilter: 'violation' })}>
                    <em>52</em> С нарушениями <Icon name="chevron" />
                </button>
                <div className="wt-fl__tile-head is-plain">
                    <i className="wt-fl__dot is-blue" aria-hidden="true" /> Возможности {HOME.chances}
                </div>
                <span className="wt-fl__row-link is-off"><em>15</em> Откликов на автомобиль</span>
                <span className="wt-fl__row-link is-off">
                    <em>0</em> Пройти обучающий курс «Основы управления таксопарком»
                </span>
            </section>
        </div>
    </Shell>
);

/* ── Исполнители: список ─────────────────────────────────────────────────── */

const FleetContractors = ({ world, go }) => {
    const onOrder = world.fleetFilter === 'on_order';
    const rows = onOrder ? CONTRACTORS.filter((c) => c.online === 'На заказе') : CONTRACTORS;
    return (
        <Shell world={world} go={go} title="Исполнители">
            {/* Зелёный баннер кабинета — он висит над списком всегда. */}
            <div className="wt-fl__banner">
                Гибкие комиссии парка теперь видны водителям при регистрации в Про.
                Подключите эту опцию, чтобы выделиться среди других парков
                <span className="wt-fl__banner-btn">Настроить</span>
            </div>

            <div className="wt-fl__chips">
                <span className="wt-fl__pill is-red">↓12 Ограничения</span>
                <span className="wt-fl__pill is-orange">97 Предупреждения</span>
                <span className="wt-fl__pill is-blue">↑60 Возможности</span>
                <i className="wt-fl__chips-sep" aria-hidden="true" />
                <span className="wt-fl__seg">Новые <em>17</em></span>
                <span className="wt-fl__seg">Активные <em>118</em></span>
                <span className="wt-fl__seg">Отток <em>11 462</em></span>
                <span className="wt-fl__seg">Архив</span>
            </div>

            <div className="wt-fl__toolbar">
                <span className="wt-fl__search">
                    <b>{rows.length}</b>
                    <span>Поиск по имени, ВУ или позывному</span>
                </span>
                {onOrder ? (
                    <button type="button" className="wt-fl__filter-chip"
                        onClick={() => go({ fleetFilter: null })}>
                        Статус на линии: На заказе ✕
                    </button>
                ) : (
                    <button type="button" className="wt-fl__filter-add"
                        onClick={() => go({ fleetFilter: 'on_order' })}>
                        + Фильтры
                    </button>
                )}
                <span className="wt-fl__tools">
                    <span>Выбрать</span><span>Сортировка ⇅</span><span>Настроить колонки</span>
                </span>
            </div>

            <table className="wt-fl__table">
                <thead>
                    <tr><th>ФИО</th><th>Телефон</th><th className="is-right">Баланс и лимит</th></tr>
                </thead>
                <tbody>
                    {rows.map((c) => (
                        <tr key={c.name} onClick={() => (c.me ? go({ fleetView: 'card', fleetTab: 'details' }) : null)}
                            className={c.me ? 'is-open' : ''}>
                            <td>
                                <span className="wt-fl__who">
                                    <i className="wt-fl__ava" aria-hidden="true" />
                                    <span>{c.name}<small>{c.online}</small></span>
                                </span>
                            </td>
                            <td>{c.phone}</td>
                            <td className="is-right">
                                <u className={c.balance.startsWith('−') ? 'is-bad' : ''}>{c.balance}</u>
                                <span className="wt-fl__limit">{c.limit}</span>
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
            <p className="wt-fl__note">
                В кабинете здесь 11 597 исполнителей. В учебной среде список короткий,
                и открывается только карточка водителя, который звонит.
            </p>
        </Shell>
    );
};

/* ── Карточка исполнителя ────────────────────────────────────────────────── */

const CardHead = ({ world, go }) => (
    <>
        <div className="wt-fl__sub">Парковый · Водитель · {CAR.plate} · {CAR.brand} {CAR.model}</div>
        <div className="wt-fl__badge">
            <i className="wt-fl__ava is-big" aria-hidden="true" />
            <b>{DRIVER.status}</b>
            <span><small>Статус</small>{DRIVER.online}</span>
            <span className="wt-fl__acct">
                <small>Состояние счёта</small>
                <em>−</em><u>{DRIVER.balance}</u><em className="is-plus">+</em>
            </span>
            <span><small>Рейтинг</small>{DRIVER.rating}</span>
            <span><small>Диагностика ›</small>Нет данных</span>
            <span><small>Приоритет ›</small>Нет данных</span>
            <span><small>Термокороб</small>{DRIVER.thermobox}</span>
        </div>
        <div className="wt-fl__warn">● {DRIVER.warning}</div>
        <nav className="wt-fl__tabs">
            {CARD_TABS.map(([slug, label]) => (
                <button
                    key={slug}
                    type="button"
                    className={world.fleetTab === slug ? 'is-on' : ''}
                    onClick={() => go({ fleetTab: slug })}
                >
                    {label}
                </button>
            ))}
        </nav>
    </>
);

const Empty = ({ title, text, action = null, onAction = null }) => (
    <div className="wt-fl__empty">
        <b>{title}</b>
        <span>{text}</span>
        {action ? (
            <button type="button" className="wt-fl__yellow" onClick={onAction}>{action}</button>
        ) : null}
    </div>
);

const Field = ({ label, value, wide = false }) => (
    <div className={`wt-fl__field${wide ? ' is-wide' : ''}`}>
        <span>{label}</span>
        <b>{value || '—'}</b>
    </div>
);

const TAB_BODY = {
    details: () => (
        <>
            <h3 className="wt-fl__h3">Детали</h3>
            <p className="wt-fl__hint">
                Некоторые поля недоступны для редактирования, для внесения изменений
                обратитесь в <u>поддержку</u>
            </p>
            <div className="wt-fl__cols">
                <div>
                    <Field label="Фамилия" value={DRIVER.last} />
                    <Field label="Имя" value={DRIVER.first} />
                    <Field label="Отчество" value={DRIVER.middle} />
                    <Field label="Телефон" value={DRIVER.phone} />
                    <Field label="Адрес" value="Укажите адрес" />
                    <Field label="Источник" value={DRIVER.source} />
                    <Field label="Статус" value={DRIVER.status} />
                </div>
                <div>
                    <Field label="Водительский стаж с" value="дд.мм.гггг" />
                    <Field label="Серия и номер ВУ" value={DRIVER.license} />
                    <Field label="Страна выдачи ВУ" value={DRIVER.country} />
                    <Field label="Дата выдачи ВУ" value={DRIVER.licenseFrom} />
                    <Field label="Действует до" value={DRIVER.licenseTo} />
                    <Field label="Слабослышащий водитель" value="Нет" />
                </div>
            </div>
        </>
    ),
    car: () => (
        <>
            <h3 className="wt-fl__h3 is-caps">Выбор автомобиля</h3>
            <div className="wt-fl__seg-row">
                <span className="is-on">Существующий</span><span>Новый</span>
                <u>Полная карточка автомобиля</u>
            </div>
            <h3 className="wt-fl__h3">Детали</h3>
            <div className="wt-fl__cols">
                <div>
                    <Field label="Статус" value={CAR.status} />
                    <Field label="Марка" value={CAR.brand} />
                    <Field label="Модель" value={CAR.model} />
                    <Field label="Цвет" value={CAR.color} />
                    <Field label="Год" value={CAR.year} />
                    <Field label="Владелец автомобиля" value={CAR.owner} />
                </div>
                <div>
                    <Field label="Госномер" value={CAR.plate} />
                    <Field label="VIN" value={CAR.vin} />
                    <Field label="Номер кузова" value={CAR.body} />
                    <Field label="СТС" value={CAR.sts} />
                </div>
            </div>
            <h3 className="wt-fl__h3">Детские кресла</h3>
            <Field label="Парковые" value={CAR.childSeats} />
        </>
    ),
    income: () => (
        <>
            <div className="wt-fl__filters">
                <span>27–28 июл.</span><span>Время начала: 00:00</span><span>Время окончания: 23:00</span>
            </div>
            <div className="wt-fl__income">
                <section className="wt-fl__report">
                    <h3 className="wt-fl__h3">Отчёт</h3>
                    {INCOME.map(([label, value, tone]) => (
                        <div key={label} className="wt-fl__report-row">
                            <span>{label}</span>
                            <b className={`is-${tone}`}>{value}</b>
                        </div>
                    ))}
                </section>
                <section className="wt-fl__charts">
                    <h3 className="wt-fl__h3">Заказы</h3>
                    <div className="wt-fl__chart" aria-hidden="true">
                        <i style={{ height: '70%', background: '#fce000' }} />
                        <i style={{ height: '38%', background: '#8fa2f0' }} />
                    </div>
                    <p className="wt-fl__chart-foot">Всего заказов <b>4</b></p>
                    <h3 className="wt-fl__h3">Часы</h3>
                    <div className="wt-fl__chart" aria-hidden="true">
                        <i style={{ height: '52%', background: '#fce000' }} />
                    </div>
                </section>
            </div>
        </>
    ),
    transactions: () => (
        <>
            <div className="wt-fl__filters">
                <span>Заказ</span><span>Период: 20–27 июл.</span><span>+ Фильтры</span>
            </div>
            <label className="wt-fl__check">
                <i aria-hidden="true">✓</i> Показать все, кроме наличных и в ожидании
            </label>
            <table className="wt-fl__table wt-fl__table--tx">
                <thead>
                    <tr>
                        <th>Дата</th><th>Событие</th><th>Категория</th>
                        <th className="is-right">Баланс</th><th className="is-right">Сумма</th>
                        <th>Комментарий</th><th>Инициатор</th>
                    </tr>
                </thead>
                <tbody>
                    {TRANSACTIONS.map((t, index) => (
                        <tr key={`${t.event}-${t.category}-${index}`} className={t.park ? 'is-park' : ''}>
                            <td>{t.date}</td>
                            <td>{t.event}</td>
                            <td>{t.category}</td>
                            <td className={`is-right${t.balance.startsWith('−') ? ' is-bad' : ' is-good'}`}>{t.balance}</td>
                            <td className={`is-right${t.sum.startsWith('−') ? ' is-bad' : ' is-good'}`}>{t.sum}</td>
                            <td>{t.comment}</td>
                            <td>{t.by}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
            <p className="wt-fl__note">
                Строки, выделенные слева полосой, — удержания ТАКСОПАРКА. Остальные
                комиссии в этой таблице берёт сервис.
            </p>
        </>
    ),
    orders: () => (
        <>
            <div className="wt-fl__filters">
                <span>Дата подачи</span><span>Период: 17–23 авг.</span><span>+ Фильтры</span>
            </div>
            <Empty title="Ничего не найдено" text="За выбранный период заказов нет" />
        </>
    ),
    subvention: () => (
        <Empty title="Нет данных" text="У водителя нет доступа к заказам" />
    ),
    balances_history: () => (
        <>
            <div className="wt-fl__filters">
                <span>17–23 авг.</span><span>Время начала: 00:00</span><span>Время окончания: 23:59</span>
            </div>
            <table className="wt-fl__table">
                <thead>
                    <tr><th>Дата</th><th className="is-right">Баланс, ₸</th><th className="is-right">Изменение, ₸</th></tr>
                </thead>
                <tbody>
                    {BALANCE_HISTORY.map((row) => (
                        <tr key={row.date}>
                            <td>{row.date}</td>
                            <td className="is-right is-bad">{row.balance}</td>
                            <td className="is-right">{row.change}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </>
    ),
    shifts: (go) => (
        <Empty
            title="Раздел переехал"
            text="Теперь смены будут в разделе GPS"
            action="Перейти"
            onAction={() => go({ fleetTab: 'gps' })}
        />
    ),
    gps: () => (
        <>
            <div className="wt-fl__filters">
                <span>22–23 авг.</span><span>Время начала: 00:00</span>
                <span>Время окончания: 23:59</span><span>Статус</span>
            </div>
            <div className="wt-fl__gps">
                {GPS_TILES.map(([label, value]) => (
                    <div key={label} className="wt-fl__gps-tile"><span>{label}</span><b>{value}</b></div>
                ))}
            </div>
            <table className="wt-fl__table">
                <thead>
                    <tr><th>Статус</th><th>Дата и время</th><th>Скорость</th><th>Время</th><th>Пробег</th><th>Детали</th></tr>
                </thead>
                <tbody>
                    {GPS_LOG.map(([status, when, details], index) => (
                        <tr key={`${when}-${index}`}>
                            <td>● {status}</td><td>{when}</td><td /><td /><td /><td>{details}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </>
    ),
    'photo-control': () => (
        <div className="wt-fl__photo">
            {PHOTO_DAYS.map((day) => (
                <section key={day.date}>
                    <h3 className="wt-fl__h3">
                        {day.ok ? null : <i className="wt-fl__dot is-red" aria-hidden="true" />}
                        {day.date}
                    </h3>
                    {day.shots.length ? (
                        <div className="wt-fl__shots">
                            {day.shots.map((shot) => (
                                <figure key={shot}>
                                    <figcaption>{shot}</figcaption>
                                    <div className="wt-fl__shot" aria-hidden="true" />
                                </figure>
                            ))}
                        </div>
                    ) : (
                        <p className="wt-fl__hint">Фотографии за эту дату не загружены</p>
                    )}
                </section>
            ))}
        </div>
    ),
    changes: () => (
        <Empty
            title="Раздела нет в учебной среде"
            text="«История изменений» в кабинете есть, но в тренажёр она не перенесена — выдумывать её содержимое нельзя"
        />
    ),
    documents: () => (
        <Empty
            title="Раздела нет в учебной среде"
            text="«Документы» в кабинете есть, но в тренажёр они не перенесены — выдумывать их содержимое нельзя"
        />
    ),
};

const FleetCard = ({ world, go }) => {
    const body = TAB_BODY[world.fleetTab] || TAB_BODY.details;
    return (
        <Shell world={world} go={go} title="Исполнители"
            crumb={`${DRIVER.last} ${DRIVER.first} ${DRIVER.middle}`}>
            <CardHead world={world} go={go} />
            <div className="wt-fl__tab-body">{body(go)}</div>
            <div className="wt-fl__wa" aria-hidden="true">Открыть в WhatsApp</div>
        </Shell>
    );
};

/* ── Автомобили, программа лояльности, выбор парка, 404 ──────────────────── */

const FleetVehicles = ({ world, go }) => (
    <Shell world={world} go={go} title="Автомобили">
        <Empty
            title="Ничего не найдено"
            text="В учебной среде список автомобилей не заполнен: тренажёр про обращение водителя"
            action="Вернуться к исполнителям"
            onAction={() => go({ fleetView: 'contractors' })}
        />
    </Shell>
);

const FleetGoals = ({ world, go }) => (
    <Shell world={world} go={go} title="Программа лояльности">
        <Empty
            title="Ничего не найдено"
            text="Разделы программы лояльности в учебную среду не перенесены"
            action="Вернуться на главную"
            onAction={() => go({ fleetView: 'home' })}
        />
    </Shell>
);

const FleetParks = ({ world, go }) => (
    <div className="wt-fl wt-fl--picker">
        <TrainMark>Учебная среда</TrainMark>
        <div className="wt-fl__picker">
            <h2>Выберите парк</h2>
            <div className="wt-fl__picker-list">
                <span className="wt-fl__picker-search">Поиск</span>
                {PARKS.map((park) => (
                    <button key={`${park.name}-${park.city}`} type="button"
                        className={park.name === PARK.name ? 'is-on' : ''}
                        onClick={() => go({ fleetView: 'home' })}>
                        <b>{park.initials}</b>
                        <span>{park.name}<small>{park.city}</small></span>
                    </button>
                ))}
            </div>
        </div>
    </div>
);

const Fleet404 = ({ world, go }) => (
    <Shell world={world} go={go} title="Исполнители">
        <Empty
            title="Ничего не найдено"
            text="Такой страницы не существует"
            action="Вернуться в начало"
            onAction={() => go({ fleetView: 'home' })}
        />
    </Shell>
);

const VIEWS = {
    home: FleetHome,
    contractors: FleetContractors,
    card: FleetCard,
    vehicles: FleetVehicles,
    goals: FleetGoals,
    parks: FleetParks,
    notfound: Fleet404,
};

/** Адрес, который показывает браузер для текущего экрана кабинета. */
export const fleetUrl = (world) => {
    const base = 'fleet.example-park.kz';
    if (world.fleetView === 'card') return `${base}/contractors/${DRIVER.id.slice(0, 8)}…/${world.fleetTab}`;
    if (world.fleetView === 'contractors') {
        return world.fleetFilter === 'on_order'
            ? `${base}/contractors?statuses=on_order`
            : `${base}/contractors`;
    }
    if (world.fleetView === 'parks') return `${base}/parks`;
    if (world.fleetView === 'notfound') return `${base}/unknown`;
    return `${base}/${world.fleetView || 'home'}`;
};

/** Кабинет целиком. go — свободный переход, не шаг урока (см. runner.browse). */
export default function FleetApp({ world, go }) {
    const View = VIEWS[world.fleetView] || FleetHome;
    return <View world={world} go={go} />;
}
