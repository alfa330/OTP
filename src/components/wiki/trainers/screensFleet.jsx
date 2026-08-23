import React from 'react';

import { TrainMark } from './screenKit';
import {
    BALANCE_HISTORY, CAR, CARD_TABS, CARS, COLUMNS, CONTRACTORS, DETAIL_BLOCKS, DRIVER,
    FILTERS, GPS_LOG, GPS_TILES, HOME, INCOME, LEGAL, LOYALTY, MENUS, NEWS, NOTIFICATIONS,
    ORDERS, PARK, PARKS, PHOTO_DAYS, SORTS, SUPPORT, TRANSACTIONS, TX_TOTALS,
} from './fleetData';

/* Учебная Диспетчерская — вторая вкладка окна оператора.
 *
 * ЗАЧЕМ. Оператор заводит обращение не вслепую: прежде чем выбрать категорию,
 * он смотрит в кабинет таксопарка. В разговоре про комиссию это решает всё — в
 * «Ведомости» видно, что сервис удержал своё двумя строками, а парк третьей.
 *
 * УРОКА ЗДЕСЬ НЕТ. Ни шагов, ни ловушек, ни «нажми не туда»: по кабинету ходят
 * свободно. Наказывать за то, что человек заглянул в справочник, значит
 * отучать в него заглядывать.
 *
 * ЧТО СКОПИРОВАНО. Раскладка, названия разделов, состав фильтров (15 осей),
 * колонок, сортировок, блоков карточки и пустых состояний — по описанию кадров
 * в приватном репозитории. НЕ скопированы логотип и фирменный знак: клон не
 * должен выдавать себя за чужой кабинет, поэтому слева нейтральный знак, а
 * сверху плашка «Учебная среда».
 *
 * Данных настоящих людей нет: водитель придуман и совпадает с водителем в CRM
 * и Oktell (см. fleetData.js).
 */

const Icon = ({ name }) => {
    const d = {
        home: 'M4 10.5 12 4l8 6.5V20H4z',
        people: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM4 20a8 8 0 0 1 16 0',
        car: 'M5 16h14M6.5 16V11l1.7-4h7.6l1.7 4v5M8 19v-3m8 3v-3',
        wallet: 'M3 8h15a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Zm13 5h2',
        help: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Zm-2-13a2 2 0 1 1 3 2c-.7.5-1 1-1 2m0 3v.5',
        search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm10 2-4.5-4.5',
        info: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Zm0-14v.5m0 3.5v5',
        cap: 'M3 9.5 12 5l9 4.5-9 4.5Zm3 3V17c0 1 2.7 2.5 6 2.5s6-1.5 6-2.5v-4.5',
        bell: 'M18 8a6 6 0 1 0-12 0c0 6-2 7-2 7h16s-2-1-2-7M10.5 20a2 2 0 0 0 3 0',
        chevron: 'm9 6 6 6-6 6',
        close: 'M6 6l12 12M18 6 6 18',
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

/* Иконки левой панели — КНОПКИ, а не ссылки: они выдвигают подменю поверх
   страницы, адрес при этом не меняется. Так устроен кабинет. */
const SIDE = [
    ['park', 'home', 'О парке'],
    ['contractors', 'people', 'Исполнители'],
    ['fleet', 'car', 'Автопарк'],
    ['finance', 'wallet', 'Финансы'],
    ['help', 'help', 'Помощь'],
];

const Overlay = ({ title, onClose, children, wide = false }) => (
    <div className={`wt-fl__overlay${wide ? ' is-wide' : ''}`}>
        <header>
            {title}
            <button type="button" onClick={onClose} aria-label="Закрыть"><Icon name="close" /></button>
        </header>
        <div className="wt-fl__overlay-body">{children}</div>
    </div>
);

/** Каркас кабинета: узкий сайдбар со значками, шапка и содержимое. */
const Shell = ({ world, go, title, crumb = null, children }) => (
    <div className="wt-fl">
        <TrainMark>Учебная среда</TrainMark>

        <aside className="wt-fl__side">
            <button type="button" className="wt-fl__logo" onClick={() => go({ fleetView: 'home', fleetMenu: null })}
                aria-label="На главную"><Mark /></button>
            {SIDE.map(([key, icon, label]) => (
                <button
                    key={key}
                    type="button"
                    title={label}
                    aria-label={label}
                    className={`wt-fl__nav${(world.fleetView === key
                        || (key === 'contractors' && world.fleetView === 'card')
                        || world.fleetMenu === key) ? ' is-on' : ''}`}
                    onClick={() => (key === 'contractors'
                        ? go({ fleetView: 'contractors', fleetMenu: null })
                        : go({ fleetMenu: world.fleetMenu === key ? null : key }))}
                >
                    <Icon name={icon} />
                </button>
            ))}
            <span className="wt-fl__side-foot" aria-hidden="true"><Icon name="info" /></span>
        </aside>

        {/* Подменю кабинета выезжает поверх страницы. */}
        {world.fleetMenu ? (
            <nav className="wt-fl__submenu">
                <div className="wt-fl__submenu-head">
                    {SIDE.find(([k]) => k === world.fleetMenu)?.[2]}
                </div>
                {(MENUS[world.fleetMenu] || []).map((item) => (
                    <button
                        key={item}
                        type="button"
                        onClick={() => {
                            const to = {
                                Автомобили: 'vehicles',
                                'Карточка автомобиля': 'vehicles',
                                Техподдержка: 'support',
                                Новости: 'news',
                                'Правовые документы': 'legal',
                                'База знаний': 'legal',
                                Сводка: 'home',
                            }[item];
                            go(to ? { fleetView: to, fleetMenu: null } : { fleetView: 'notfound', fleetMenu: null });
                        }}
                    >
                        {item}
                    </button>
                ))}
            </nav>
        ) : null}

        <div className="wt-fl__body">
            <header className="wt-fl__head">
                <h1>
                    {title}
                    {crumb ? (<><span className="wt-fl__arrow">→</span><b>{crumb}</b></>) : null}
                </h1>
                <span className="wt-fl__head-right">
                    <button type="button" className="wt-fl__icon-btn" aria-label="Поиск"
                        onClick={() => go({ fleetPanel: 'search' })}><Icon name="search" /></button>
                    <button type="button" className="wt-fl__park"
                        onClick={() => go({ fleetView: 'parks' })}>
                        <b>{PARK.initials}</b>
                        <span>{PARK.name}<small>{PARK.city}</small></span>
                    </button>
                </span>
            </header>

            <div className="wt-fl__content">{children}</div>

            <div className="wt-fl__fabs">
                <button type="button" className="wt-fl__fab" aria-label="Обучение"
                    onClick={() => go({ fleetPanel: 'study' })}><Icon name="cap" /></button>
                <button type="button" className="wt-fl__fab wt-fl__fab--bell" aria-label="Уведомления"
                    onClick={() => go({ fleetPanel: 'bell' })}><Icon name="bell" /><i>52</i></button>
            </div>
        </div>

        {/* Панели поверх кабинета: поиск, уведомления, обучение. */}
        {world.fleetPanel === 'search' ? (
            <Overlay title="Поиск" onClose={() => go({ fleetPanel: null })}>
                <div className="wt-fl__search-big">Начните вводить имя, номер ВУ или номер машины</div>
                <p className="wt-fl__note">
                    Поиск срабатывает с трёх знаков. Именно сюда оператор вбивает номер звонящего.
                </p>
                <button type="button" className="wt-fl__found"
                    onClick={() => go({ fleetView: 'card', fleetTab: 'details', fleetPanel: null })}>
                    <i className="wt-fl__ava" aria-hidden="true" />
                    <span>{DRIVER.full}<small>{DRIVER.phonePretty} · {CAR.plate}</small></span>
                </button>
            </Overlay>
        ) : null}

        {world.fleetPanel === 'bell' ? (
            <Overlay title="Лента коммуникаций" onClose={() => go({ fleetPanel: null })}>
                {NOTIFICATIONS.map(([text, when]) => (
                    <div key={text} className="wt-fl__notice"><b>{text}</b><small>{when}</small></div>
                ))}
            </Overlay>
        ) : null}

        {world.fleetPanel === 'study' ? (
            <Overlay title="Обучение" onClose={() => go({ fleetPanel: null })}>
                <p className="wt-fl__note">
                    В кабинете здесь курс «Основы управления таксопарком». В учебную среду он
                    не перенесён — это чужой материал.
                </p>
            </Overlay>
        ) : null}
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
        {parts.map(([label, color]) => <li key={label}><i style={{ background: color }} />{label}</li>)}
    </ul>
);

const FleetHome = ({ world, go }) => (
    <Shell world={world} go={go} title="О парке">
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
                    {LOYALTY.slice(0, 2).map(([name, sub, rules, active]) => (
                        <div key={name} className={`wt-fl__grade${active ? ' is-bronze' : ''}`}>
                            <b>{name}</b><small>{sub}</small>
                            <ul>{rules.map((r) => <li key={r}>{r}</li>)}</ul>
                        </div>
                    ))}
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

const FILTER_ROWS = {
    on_order: (c) => c.online === 'На заказе',
    violation: () => true,
    moto: (c) => c.vehicle === 'Мотоцикл',
    courier: (c) => c.profession.includes('Курьер'),
};

const FleetContractors = ({ world, go }) => {
    const test = FILTER_ROWS[world.fleetFilter];
    const rows = test ? CONTRACTORS.filter(test) : CONTRACTORS;
    const openCard = () => go({ fleetView: 'card', fleetTab: 'details', fleetPanel: null });
    return (
        <Shell world={world} go={go} title="Исполнители">
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
                <button type="button" className="wt-fl__search"
                    onClick={() => go({ fleetPanel: 'search' })}>
                    <b>{rows.length}</b>
                    <span>Поиск по имени, ВУ или позывному</span>
                </button>
                {world.fleetFilter ? (
                    <button type="button" className="wt-fl__filter-chip"
                        onClick={() => go({ fleetFilter: null })}>
                        Фильтр применён ✕
                    </button>
                ) : null}
                <button type="button" className="wt-fl__filter-add"
                    onClick={() => go({ fleetPanel: 'filters' })}>+ Фильтры</button>
                <span className="wt-fl__tools">
                    <button type="button">Выбрать</button>
                    <button type="button" onClick={() => go({ fleetPanel: 'sort' })}>Сортировка ⇅</button>
                    <button type="button" onClick={() => go({ fleetPanel: 'columns' })}>Настроить колонки</button>
                </span>
            </div>

            <table className="wt-fl__table">
                <thead>
                    <tr><th>ФИО</th><th>Телефон</th><th className="is-right">Баланс и лимит</th></tr>
                </thead>
                <tbody>
                    {rows.map((c) => (
                        <tr key={c.name} className="is-open"
                            onClick={() => (c.me ? openCard() : go({ fleetPanel: 'panel' }))}>
                            <td>
                                <span className="wt-fl__who">
                                    <i className="wt-fl__ava" aria-hidden="true" />
                                    <span>{c.name}<small>{c.online} · {c.profession}</small></span>
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
                В кабинете здесь 11 597 исполнителей. В учебной среде список короткий, и полная
                карточка открывается у того водителя, который звонит.
            </p>

            {world.fleetPanel === 'filters' ? (
                <Overlay title="Фильтры" wide onClose={() => go({ fleetPanel: null })}>
                    <div className="wt-fl__filters-grid">
                        {FILTERS.map(([axis, values]) => (
                            <div key={axis}>
                                <b>{axis}</b>
                                <ul>{values.map((v) => <li key={v}>{v}</li>)}</ul>
                            </div>
                        ))}
                    </div>
                    <div className="wt-fl__overlay-foot">
                        <button type="button" className="wt-fl__yellow"
                            onClick={() => go({ fleetFilter: 'on_order', fleetPanel: null })}>
                            Показать «На заказе»
                        </button>
                    </div>
                </Overlay>
            ) : null}

            {world.fleetPanel === 'columns' ? (
                <Overlay title="Настроить колонки" onClose={() => go({ fleetPanel: null })}>
                    <ul className="wt-fl__checklist">
                        {COLUMNS.map((col, index) => (
                            <li key={col}><i className={index < 3 ? 'is-on' : ''} aria-hidden="true" />{col}</li>
                        ))}
                    </ul>
                </Overlay>
            ) : null}

            {world.fleetPanel === 'sort' ? (
                <Overlay title="Сортировка" onClose={() => go({ fleetPanel: null })}>
                    <ul className="wt-fl__checklist">
                        {SORTS.map((s, index) => (
                            <li key={s}><i className={index === 0 ? 'is-on' : ''} aria-hidden="true" />{s}</li>
                        ))}
                    </ul>
                </Overlay>
            ) : null}

            {/* Панель карточки поверх списка — отдельная раскладка кабинета,
                не та же страница. */}
            {world.fleetPanel === 'panel' ? (
                <Overlay title="Карточка исполнителя" onClose={() => go({ fleetPanel: null })}>
                    <p className="wt-fl__note">
                        В кабинете по строке открывается боковая панель с чипами, контактами и
                        показателями. В учебной среде подробности есть только у водителя,
                        который звонит, — остальные строки придуманы для вида.
                    </p>
                </Overlay>
            ) : null}
        </Shell>
    );
};

/* ── Карточка исполнителя ────────────────────────────────────────────────── */

const CardHead = ({ world, go }) => (
    <>
        <div className="wt-fl__sub">
            Парковый · {DRIVER.profession} · {CAR.plate} · {CAR.brand} {CAR.model}
        </div>
        <div className="wt-fl__badge">
            <i className="wt-fl__ava is-big" aria-hidden="true" />
            <b>{DRIVER.workStatus}</b>
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
                <button key={slug} type="button"
                    className={world.fleetTab === slug ? 'is-on' : ''}
                    onClick={() => go({ fleetTab: slug })}>
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
        {action ? <button type="button" className="wt-fl__yellow" onClick={onAction}>{action}</button> : null}
    </div>
);

const Field = ({ label, value }) => (
    <div className="wt-fl__field"><span>{label}</span><b>{value || '—'}</b></div>
);

const Filters = ({ items }) => (
    <div className="wt-fl__filters">{items.map((f) => <span key={f}>{f}</span>)}</div>
);

const TAB_BODY = {
    details: () => (
        <>
            <p className="wt-fl__hint">
                Некоторые поля недоступны для редактирования, для внесения изменений
                обратитесь в <u>поддержку</u>
            </p>
            {DETAIL_BLOCKS.map(([block, rows]) => (
                <section key={block} className="wt-fl__block">
                    <h3 className="wt-fl__h3">{block}</h3>
                    <div className="wt-fl__cols">
                        {rows.map(([label, value]) => <Field key={label} label={label} value={value} />)}
                    </div>
                </section>
            ))}
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
                <Field label="Статус" value={CAR.status} />
                <Field label="Госномер" value={CAR.plate} />
                <Field label="Марка" value={CAR.brand} />
                <Field label="VIN" value={CAR.vin} />
                <Field label="Модель" value={CAR.model} />
                <Field label="Номер кузова" value={CAR.body} />
                <Field label="Цвет" value={CAR.color} />
                <Field label="СТС" value={CAR.sts} />
                <Field label="Год" value={CAR.year} />
                <Field label="Категории" value={CAR.categories} />
                <Field label="Владелец автомобиля" value={CAR.owner} />
            </div>
            <h3 className="wt-fl__h3">Детские кресла</h3>
            <Field label="Парковые" value={CAR.childSeats} />
        </>
    ),
    income: () => (
        <>
            <Filters items={['27–28 июл.', 'Время начала: 00:00', 'Время окончания: 23:00']} />
            <div className="wt-fl__income">
                <section className="wt-fl__report">
                    <h3 className="wt-fl__h3">Отчёт</h3>
                    {INCOME.map(([label, value, tone]) => (
                        <div key={label} className="wt-fl__report-row">
                            <span>{label}</span><b className={`is-${tone}`}>{value}</b>
                        </div>
                    ))}
                </section>
                <section className="wt-fl__charts">
                    <h3 className="wt-fl__h3">Заказы</h3>
                    <div className="wt-fl__chart" aria-hidden="true">
                        <i style={{ height: '70%', background: '#fce000' }} />
                        <i style={{ height: '38%', background: '#8fa2f0' }} />
                    </div>
                    <p className="wt-fl__chart-foot">Всего заказов <b>2</b></p>
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
            <Filters items={['Заказ', 'Период: 20–27 июл.', '+ Фильтры']} />
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
                            <td>{t.date}</td><td>{t.event}</td><td>{t.category}</td>
                            <td className={`is-right${t.balance.startsWith('−') ? ' is-bad' : ' is-good'}`}>{t.balance}</td>
                            <td className={`is-right${t.sum.startsWith('−') ? ' is-bad' : ' is-good'}`}>{t.sum}</td>
                            <td>{t.comment}</td><td>{t.by}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
            <div className="wt-fl__totals">
                {TX_TOTALS.map(([label, value]) => (
                    <div key={label}><span>{label}</span><b>{value}</b></div>
                ))}
            </div>
            <p className="wt-fl__note">
                Строки с полосой слева — удержания ТАКСОПАРКА. Остальные комиссии в этой
                таблице берёт сервис.
            </p>
        </>
    ),
    orders: () => (
        <>
            <Filters items={['Дата подачи', 'Период: 20–27 июл.', '+ Фильтры']} />
            <table className="wt-fl__table">
                <thead>
                    <tr><th>Дата</th><th>Заказ</th><th>Тариф</th><th>Статус</th><th className="is-right">Сумма</th></tr>
                </thead>
                <tbody>
                    {ORDERS.map((row) => (
                        <tr key={row[1]}>
                            {row.map((cell, i) => (
                                <td key={cell} className={i === 4 ? 'is-right' : ''}>{cell}</td>
                            ))}
                        </tr>
                    ))}
                </tbody>
            </table>
        </>
    ),
    subvention: () => <Empty title="Нет данных" text="У водителя нет доступа к заказам" />,
    balances_history: () => (
        <>
            <Filters items={['17–23 авг.', 'Время начала: 00:00', 'Время окончания: 23:59']} />
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
        <Empty title="Раздел переехал" text="Теперь смены будут в разделе GPS"
            action="Перейти" onAction={() => go({ fleetTab: 'gps' })} />
    ),
    gps: () => (
        <>
            <Filters items={['22–23 авг.', 'Время начала: 00:00', 'Время окончания: 23:59', 'Статус']} />
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
                    ) : <p className="wt-fl__hint">Фотографии за эту дату не загружены</p>}
                </section>
            ))}
        </div>
    ),
    changes: () => (
        <div className="wt-fl__changes">
            <h3 className="wt-fl__h3">История изменений</h3>
            {[['Изменены условия работы', '21.08.2026, 14:02', 'Платформа'],
              ['Загружен фотоконтроль', '19.07.2026, 08:41', 'Исполнитель'],
              ['Изменён автомобиль', '02.03.2026, 11:15', 'Сотрудник парка']].map(([what, when, who]) => (
                <div key={when} className="wt-fl__change"><b>{what}</b><span>{when} · {who}</span></div>
            ))}
        </div>
    ),
    documents: () => (
        <div className="wt-fl__cols">
            <Field label="Водительское удостоверение" value="Загружено" />
            <Field label="Паспорт" value="Загружен" />
            <Field label="СТС" value="Не загружено" />
            <Field label="Договор" value="Подписан" />
        </div>
    ),
};

const FleetCard = ({ world, go }) => {
    const body = TAB_BODY[world.fleetTab] || TAB_BODY.details;
    return (
        <Shell world={world} go={go} title="Исполнители" crumb={DRIVER.full}>
            <CardHead world={world} go={go} />
            <div className="wt-fl__tab-body">{body(go)}</div>
            <div className="wt-fl__wa">Открыть в WhatsApp</div>
        </Shell>
    );
};

/* ── Остальные разделы ───────────────────────────────────────────────────── */

const FleetVehicles = ({ world, go }) => (
    <Shell world={world} go={go} title="Автомобили">
        <table className="wt-fl__table">
            <thead>
                <tr><th>Госномер</th><th>Марка и модель</th><th>Цвет</th><th>Год</th><th>Статус</th></tr>
            </thead>
            <tbody>
                {CARS.map((row) => (
                    <tr key={row[0]}>{row.map((cell) => <td key={cell}>{cell}</td>)}</tr>
                ))}
            </tbody>
        </table>
        <p className="wt-fl__note">В кабинете здесь 77 парковых автомобилей.</p>
    </Shell>
);

const FleetGoals = ({ world, go }) => (
    <Shell world={world} go={go} title="Программа лояльности">
        <div className="wt-fl__grades">
            {LOYALTY.map(([name, sub, rules, active]) => (
                <div key={name} className={`wt-fl__grade${active ? ' is-bronze' : ''}`}>
                    <b>{name}</b><small>{sub}</small>
                    <ul>{rules.map((r) => <li key={r}>{r}</li>)}</ul>
                </div>
            ))}
        </div>
    </Shell>
);

const FleetSupport = ({ world, go }) => (
    <Shell world={world} go={go} title="Мои обращения">
        <div className="wt-fl__toolbar">
            <button type="button" className="wt-fl__filter-add"
                onClick={() => go({ fleetPanel: 'sfilters' })}>+ Фильтры</button>
            <span className="wt-fl__tools">
                <button type="button" className="wt-fl__yellow"
                    onClick={() => go({ fleetView: 'support_new' })}>Новое обращение</button>
            </span>
        </div>
        <table className="wt-fl__table">
            <thead>
                <tr><th>Вопрос</th><th>Статус</th><th>Обновлено</th><th>Создано</th></tr>
            </thead>
            <tbody>
                {SUPPORT.map(([q, status, updated, created]) => (
                    <tr key={q}>
                        <td>{q}</td>
                        <td><span className={`wt-fl__status is-${status === 'Выполнен' ? 'ok'
                            : status === 'Закрыт' ? 'off' : 'wait'}`}>{status}</span></td>
                        <td>{updated}</td><td>{created}</td>
                    </tr>
                ))}
            </tbody>
        </table>
        <p className="wt-fl__note">
            Это обращения ПАРКА в поддержку сервиса — не то же самое, что обращение водителя
            в CRM на соседней вкладке.
        </p>
        {world.fleetPanel === 'sfilters' ? (
            <Overlay title="Фильтры обращений" onClose={() => go({ fleetPanel: null })}>
                <ul className="wt-fl__checklist">
                    {['Выполнен', 'Закрыт', 'Требуется информация', 'В работе'].map((s) => (
                        <li key={s}><i aria-hidden="true" />{s}</li>
                    ))}
                </ul>
            </Overlay>
        ) : null}
    </Shell>
);

const FleetSupportNew = ({ world, go }) => (
    <Shell world={world} go={go} title="Новое обращение">
        <div className="wt-fl__form">
            <Field label="Доступ" value="Мне и моей роли" />
            <Field label="Email" value="park@example.kz" />
            <div className="wt-fl__field"><span>Тема</span><b>Выберите тему обращения</b></div>
            <div className="wt-fl__field"><span>Сообщение</span><b>—</b></div>
            <div className="wt-fl__overlay-foot">
                <button type="button" className="wt-fl__yellow"
                    onClick={() => go({ fleetView: 'support' })}>Отправить</button>
                <span className="wt-fl__note">
                    Учебная форма: ничего не отправляется.
                </span>
            </div>
        </div>
    </Shell>
);

const FleetNews = ({ world, go }) => (
    <Shell world={world} go={go} title="Новости">
        {NEWS.map(([title, when]) => (
            <div key={title} className="wt-fl__notice"><b>{title}</b><small>{when}</small></div>
        ))}
    </Shell>
);

const FleetLegal = ({ world, go }) => (
    <Shell world={world} go={go} title="Правовые документы">
        <ul className="wt-fl__checklist">
            {LEGAL.map((doc) => <li key={doc}><i aria-hidden="true" />{doc}</li>)}
        </ul>
        <p className="wt-fl__note">Тексты документов в учебную среду не перенесены.</p>
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
        <Empty title="Ничего не найдено" text="Такой страницы не существует"
            action="Вернуться в начало" onAction={() => go({ fleetView: 'home' })} />
    </Shell>
);

const VIEWS = {
    home: FleetHome,
    contractors: FleetContractors,
    card: FleetCard,
    vehicles: FleetVehicles,
    goals: FleetGoals,
    support: FleetSupport,
    support_new: FleetSupportNew,
    news: FleetNews,
    legal: FleetLegal,
    parks: FleetParks,
    notfound: Fleet404,
};

/** Адрес, который показывает браузер для текущего экрана кабинета. */
export const fleetUrl = (world) => {
    const base = 'fleet.example-park.kz';
    const v = world.fleetView;
    if (v === 'card') return `${base}/contractors/${DRIVER.id.slice(0, 8)}…/${world.fleetTab}`;
    if (v === 'contractors') {
        return world.fleetFilter ? `${base}/contractors?filter=${world.fleetFilter}` : `${base}/contractors`;
    }
    if (v === 'support_new') return `${base}/support/new`;
    if (v === 'notfound') return `${base}/unknown`;
    return `${base}/${v || 'home'}`;
};

/** Кабинет целиком. go — свободный переход, не шаг урока (см. runner.browse). */
export default function FleetApp({ world, go }) {
    const View = VIEWS[world.fleetView] || FleetHome;
    return <View world={world} go={go} />;
}
