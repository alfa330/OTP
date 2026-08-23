import React, { useEffect, useRef, useState } from 'react';

import { TrainMark } from './screenKit';
import { SEARCH_MIN, findContractors, searchKind } from './caseData.js';
import {
    CARD_TABS, COLUMNS, FILTERS, HOME, LEGAL, LOYALTY, MENUS, NEWS, NOTIFICATIONS,
    PARKS, SORTS,
} from './fleetData';

/* Учебная Диспетчерская — вкладка кабинета таксопарка.
 *
 * ЗАЧЕМ. Правда о деле водителя разложена по системам, и водитель её сам не
 * расскажет: в «Ведомости» видно, чью комиссию удержали, в «Истории изменений» —
 * что ему меняли условия, в «Заказах» — что было с поездкой. Стажёр обязан
 * это найти.
 *
 * УРОКА ЗДЕСЬ НЕТ: ни шагов, ни ловушек. Ходить можно куда угодно, но каждое
 * действие уходит в ленту событий (emit) — по ней разбор потом скажет, куда
 * человек не посмотрел.
 *
 * ДАННЫЕ — ИЗ СЛЕПКА (world.case), не из модуля. Отсюда следует главное: чтобы
 * сменить водителя, правят JSON, а не этот файл. Из fleetData остаются только
 * справочники САМОГО кабинета (оси фильтров, колонки, сортировки, подменю) —
 * они от дела не зависят.
 *
 * ЧТО НЕ СКОПИРОВАНО: логотип и фирменный знак. Клон не должен выдавать себя за
 * чужой кабинет — свой нейтральный знак и плашка «Учебная среда».
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

/* 22 колонки «Заказов» — ровно те и в том порядке, что в кабинете. */
const ORDER_COLUMNS = [
    'Статус', 'Код заказа', 'Автомобиль', 'Дата подачи', 'Дата завершения',
    'Причина отмены', 'Адрес', 'Категория', 'Пробег', 'Стоимость в Про',
    'Наличные', 'Безналичная', 'Корпоративная', 'Чаевые', 'Компенсация промоакций',
    'Бонус', 'Прочие начисления', 'Комиссии сервиса', 'Прочие платежи',
    'Налоги и сборы', 'Платежи сервиса в счёт заказа', 'Комиссии партнёра',
];

/* Денежные колонки, которых у заказа может не быть: показываем ноль, как
   кабинет, а не пустоту. */
const ORDER_MONEY = [
    'cash', 'cashless', 'corporate', 'tips', 'promo_compensation', 'bonus',
    'other_income', 'service_commission', 'other_payments', 'taxes',
    'service_payments', 'partner_commission',
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

/* Поиск. Фильтрует сразу, а СОБЫТИЕ шлёт по паузе в 800 мс: иначе лента
   превратилась бы в посимвольный лог и перестала читаться человеком. */
const SearchBox = ({ value, total, onQuery, emit, className, placeholder }) => {
    const [draft, setDraft] = useState(value || '');
    const timer = useRef(null);

    useEffect(() => () => clearTimeout(timer.current), []);

    const change = (text) => {
        setDraft(text);
        onQuery(text);
        clearTimeout(timer.current);
        if (text.trim().length < SEARCH_MIN) return;
        timer.current = setTimeout(() => {
            emit('ui.search', { query: text.trim(), kind: searchKind(text), results: total(text) });
        }, 800);
    };

    return (
        <span className={className}>
            <b>{total(draft)}</b>
            <input
                type="text"
                value={draft}
                placeholder={placeholder}
                aria-label={placeholder}
                onChange={(event) => change(event.target.value)}
            />
        </span>
    );
};

/** Каркас кабинета. */
const Shell = ({ world, go, emit, title, crumb = null, children }) => {
    const c = world.case;
    return (
        <div className="wt-fl">
            <TrainMark>Учебная среда</TrainMark>

            <aside className="wt-fl__side">
                <button type="button" className="wt-fl__logo"
                    onClick={() => go({ fleetView: 'home', fleetMenu: null })}
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

            {world.fleetMenu ? (
                <nav className="wt-fl__submenu">
                    <div className="wt-fl__submenu-head">
                        {SIDE.find(([k]) => k === world.fleetMenu)?.[2]}
                    </div>
                    {(MENUS[world.fleetMenu] || []).map((item) => (
                        <button key={item} type="button" onClick={() => {
                            const to = {
                                Автомобили: 'vehicles', 'Карточка автомобиля': 'vehicles',
                                Техподдержка: 'support', Новости: 'news',
                                'Правовые документы': 'legal', 'База знаний': 'legal',
                                Сводка: 'home',
                            }[item];
                            go(to ? { fleetView: to, fleetMenu: null }
                                : { fleetView: 'notfound', fleetMenu: null });
                        }}>
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
                            <b>{c.park.initials}</b>
                            <span>{c.park.name}<small>{c.park.city}</small></span>
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

            {world.fleetPanel === 'search' ? (
                <Overlay title="Поиск" onClose={() => go({ fleetPanel: null })}>
                    <SearchBox
                        className="wt-fl__search-big"
                        placeholder="Начните вводить имя, номер ВУ или номер машины"
                        value={world.fleetQuery}
                        total={(q) => findContractors(c.contractors, q).items.length}
                        onQuery={(q) => go({ fleetQuery: q })}
                        emit={emit}
                    />
                    <p className="wt-fl__note">Поиск срабатывает с трёх знаков.</p>
                    <GlobalHits world={world} go={go} emit={emit} />
                </Overlay>
            ) : null}

            {world.fleetPanel === 'bell' ? (
                <Overlay title="Лента коммуникаций" onClose={() => go({ fleetPanel: null })}>
                    {NOTIFICATIONS.map(([text, when]) => (
                        <div key={text} className="wt-fl__notice"><b>{text}</b><small>{when.slice(0, 10)}</small></div>
                    ))}
                </Overlay>
            ) : null}

            {world.fleetPanel === 'diagnostics' ? (
            <Overlay title="Диагностика" onClose={() => go({ fleetPanel: null })}>
                <p className="wt-fl__diag">{c.contractor.diagnostics || 'Нет данных'}</p>
                {(c.contractor.diagnostics_details || []).map((line) => (
                    <div key={line} className="wt-fl__notice"><b>{line}</b></div>
                ))}
                <p className="wt-fl__note">
                    Допуск к линии кабинет объясняет здесь, а не во вкладке «Документы» —
                    она пустая.
                </p>
            </Overlay>
        ) : null}

        {world.fleetPanel === 'priority' ? (
            <Overlay title="Приоритет" onClose={() => go({ fleetPanel: null })}>
                <p className="wt-fl__diag">{c.contractor.priority || 'Нет данных'}</p>
                <p className="wt-fl__note">
                    Баллы приоритета начисляются за выполненные заказы и сгорают за отмены.
                    Их видно и в карточке заказа строкой «Баллы приоритета».
                </p>
            </Overlay>
        ) : null}

        {/* «История изменений» — модальное окно: своего адреса у неё нет. */}
        {world.fleetPanel === 'changes' ? (
            <Overlay title="История изменений" wide onClose={() => go({ fleetPanel: null })}>
                {c.changes.length ? c.changes.map((ch, index) => (
                    <div key={`${ch.at}-${index}`} className="wt-fl__change">
                        <b>{ch.field}: {ch.from} → {ch.to}</b>
                        <span>{ch.when} · {ch.author}</span>
                    </div>
                )) : <p className="wt-fl__note">Изменений не было</p>}
            </Overlay>
        ) : null}

        {world.fleetPanel === 'study' ? (
                <Overlay title="Обучение" onClose={() => go({ fleetPanel: null })}>
                    <p className="wt-fl__note">
                        В кабинете здесь курс «Основы управления таксопарком». В учебную среду
                        он не перенесён — это чужой материал.
                    </p>
                </Overlay>
            ) : null}
        </div>
    );
};

/** Открыть карточку исполнителя. panel_only — страница 404, панель работает. */
const openContractor = (world, go, emit, person, via) => {
    emit('ui.open_contractor', { id: person.id, via });
    const hero = world.case.contractor;
    const isHero = person.id === hero.id;
    if (isHero && hero.panel_only) {
        // Ловушка настоящего кабинета: прямой адрес карточки отвечает 404,
        // и открыть человека можно только панелью поверх списка.
        go({ fleetView: 'notfound', fleet404: true, fleetOpenId: person.id, fleetPanel: null });
        return;
    }
    go({ fleetView: 'card', fleetTab: 'details', fleetOpenId: person.id, fleetPanel: null });
};

const GlobalHits = ({ world, go, emit }) => {
    const found = findContractors(world.case.contractors, world.fleetQuery);
    if (!found.ready) return null;
    if (!found.items.length) return <p className="wt-fl__note">Ничего не найдено</p>;
    return (
        <div className="wt-fl__hits">
            {found.items.slice(0, 8).map((person) => (
                <button key={person.id} type="button" className="wt-fl__found"
                    onClick={() => openContractor(world, go, emit, person, 'search')}>
                    <i className="wt-fl__ava" aria-hidden="true" />
                    <span>{person.name}<small>{person.phone_pretty} · {person.license}</small></span>
                </button>
            ))}
        </div>
    );
};

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

const FleetHome = (props) => (
    <Shell {...props} title="О парке">
        <button type="button" className="wt-fl__chip wt-fl__chip--period">17–23 авг.</button>
        <div className="wt-fl__tiles">
            <section className="wt-fl__tile">
                <button type="button" className="wt-fl__tile-head"
                    onClick={() => props.go({ fleetView: 'contractors' })}>
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
                    onClick={() => props.go({ fleetView: 'vehicles' })}>
                    <Icon name="car" /> Автомобили <Icon name="chevron" />
                </button>
                <div className="wt-fl__big">{HOME.cars}<span>парковых автомобилей</span></div>
                <Legend parts={HOME.carParts} />
                <Bars parts={[['a', '#029154', 67], ['b', '#fce000', 7], ['c', '#8f97a8', 1], ['d', '#fc5230', 2]]} />
            </section>

            <section className="wt-fl__tile">
                <button type="button" className="wt-fl__tile-head"
                    onClick={() => props.go({ fleetView: 'goals' })}>
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
                <button type="button" className="wt-fl__row-link" onClick={() => {
                    props.emit('ui.filter', { axis: 'Проблемы', value: 'С нарушениями' });
                    props.go({ fleetView: 'contractors', fleetFilters: ['С нарушениями'] });
                }}>
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

const BY_FILTER = {
    'На заказе': (p) => p.online === 'На заказе',
    Офлайн: (p) => p.online === 'Офлайн',
    Мотоцикл: (p) => p.vehicle === 'Мотоцикл',
    'Курьер на автомобиле': (p) => p.profession === 'Курьер на автомобиле',
    'С нарушениями': () => true,
};

const FleetContractors = (props) => {
    const { world, go, emit } = props;
    const c = world.case;
    const filters = world.fleetFilters || [];

    const filtered = c.contractors.filter(
        (person) => filters.every((f) => (BY_FILTER[f] ? BY_FILTER[f](person) : true)),
    );
    const found = findContractors(filtered, world.fleetQuery);
    const rows = found.items;

    return (
        <Shell {...props} title="Исполнители">
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
                <SearchBox
                    className="wt-fl__search"
                    placeholder="Поиск по имени, ВУ или позывному"
                    value={world.fleetQuery}
                    total={(q) => findContractors(filtered, q).items.length}
                    onQuery={(q) => go({ fleetQuery: q })}
                    emit={emit}
                />
                {/* Фильтры НАКАПЛИВАЮТСЯ: каждый снимается своим крестиком.
                    Так ведёт себя кабинет, и делать вид, что каждый переход
                    чистый, значит готовить стажёра к неожиданности. */}
                {filters.map((f) => (
                    <button key={f} type="button" className="wt-fl__filter-chip"
                        onClick={() => go({ fleetFilters: filters.filter((x) => x !== f) })}>
                        {f} ✕
                    </button>
                ))}
                <button type="button" className="wt-fl__filter-add"
                    onClick={() => go({ fleetPanel: 'filters' })}>+ Фильтры</button>
                <span className="wt-fl__tools">
                    <button type="button">Выбрать</button>
                    <button type="button" onClick={() => go({ fleetPanel: 'sort' })}>Сортировка ⇅</button>
                    <button type="button" onClick={() => go({ fleetPanel: 'columns' })}>Настроить колонки</button>
                </span>
            </div>

            {rows.length ? (
                <table className="wt-fl__table">
                    <thead>
                        <tr><th>ФИО</th><th>Телефон</th><th className="is-right">Баланс и лимит</th></tr>
                    </thead>
                    <tbody>
                        {rows.map((person) => (
                            <tr key={person.id} className="is-open"
                                onClick={() => openContractor(world, go, emit, person, 'row')}>
                                <td>
                                    <span className="wt-fl__who">
                                        <i className="wt-fl__ava" aria-hidden="true" />
                                        <span>{person.name}<small>{person.online} · {person.profession}</small></span>
                                    </span>
                                </td>
                                <td>{person.phone_pretty}</td>
                                <td className="is-right">
                                    <u className={person.balance.startsWith('−') ? 'is-bad' : ''}>{person.balance}</u>
                                    <span className="wt-fl__limit">{person.limit}</span>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            ) : (
                <Empty title="Ничего не найдено" text="Попробуйте изменить запрос или снять фильтры" />
            )}

            {world.fleetPanel === 'filters' ? (
                <Overlay title="Фильтры" wide onClose={() => go({ fleetPanel: null })}>
                    <div className="wt-fl__filters-grid">
                        {FILTERS.map(([axis, values]) => (
                            <div key={axis}>
                                <b>{axis}</b>
                                <ul>
                                    {values.map((v) => (
                                        <li key={v}>
                                            {BY_FILTER[v] ? (
                                                <button type="button" onClick={() => {
                                                    emit('ui.filter', { axis, value: v });
                                                    go({
                                                        fleetFilters: filters.includes(v) ? filters : [...filters, v],
                                                        fleetPanel: null,
                                                    });
                                                }}>{v}</button>
                                            ) : v}
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        ))}
                    </div>
                </Overlay>
            ) : null}

            {world.fleetPanel === 'columns' ? (
                <Overlay title="Настроить колонки" onClose={() => go({ fleetPanel: null })}>
                    <ul className="wt-fl__checklist">
                        {COLUMNS.map((col, index) => (
                            <li key={col}>
                                <button type="button" onClick={() => emit('ui.columns', { column: col })}>
                                    <i className={index < 3 ? 'is-on' : ''} aria-hidden="true" />{col}
                                </button>
                            </li>
                        ))}
                    </ul>
                </Overlay>
            ) : null}

            {world.fleetPanel === 'sort' ? (
                <Overlay title="Сортировка" onClose={() => go({ fleetPanel: null })}>
                    <ul className="wt-fl__checklist">
                        {SORTS.map((s, index) => (
                            <li key={s}>
                                <button type="button" onClick={() => {
                                    emit('ui.sort', { by: s });
                                    go({ fleetPanel: null });
                                }}>
                                    <i className={index === 0 ? 'is-on' : ''} aria-hidden="true" />{s}
                                </button>
                            </li>
                        ))}
                    </ul>
                </Overlay>
            ) : null}
        </Shell>
    );
};

/* ── Карточка исполнителя ────────────────────────────────────────────────── */

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

/** Кого сейчас открыли: героя или обычную строку списка. */
const openedPerson = (world) => (world.case.contractors || [])
    .find((p) => p.id === world.fleetOpenId) || world.case.contractors[0] || {};

const CardHead = ({ world, go, emit }) => {
    const c = world.case;
    const hero = c.contractor;
    const person = openedPerson(world);
    const isHero = person.id === hero.id;
    return (
        <>
            <div className="wt-fl__sub">
                {isHero
                    ? `Парковый · ${hero.profession} · ${c.car.plate} · ${c.car.brand} ${c.car.model}`
                    : `Парковый · ${person.profession || '—'}`}
            </div>
            <div className="wt-fl__badge">
                <i className="wt-fl__ava is-big" aria-hidden="true" />
                <b>{isHero ? hero.work_status : 'Работает'}</b>
                <span><small>Статус</small>{person.online || '—'}</span>
                <span className="wt-fl__acct">
                    <small>Состояние счёта</small>
                    <em>−</em><u>{person.balance || '—'}</u><em className="is-plus">+</em>
                </span>
                <span><small>Рейтинг</small>{isHero ? hero.rating : '—'}</span>
                {/* «Диагностика ›» и «Приоритет ›» — кнопки со стрелкой: они
                    открывают панель справа. Именно диагностика объясняет допуск
                    к линии, а не вкладка «Документы» — та в кабинете пуста. */}
                <button type="button" className="wt-fl__hdr-btn"
                    onClick={() => go({ fleetPanel: 'diagnostics' })}>
                    <small>Диагностика ›</small>{isHero ? (hero.diagnostics || 'Нет данных') : 'Нет данных'}
                </button>
                <button type="button" className="wt-fl__hdr-btn"
                    onClick={() => go({ fleetPanel: 'priority' })}>
                    <small>Приоритет ›</small>{isHero ? (hero.priority || 'Нет данных') : 'Нет данных'}
                </button>
                <span><small>Термокороб</small>{isHero ? hero.thermobox : '—'}</span>
            </div>
            {isHero && (hero.warnings || []).map((w) => (
                <div key={w} className="wt-fl__warn">● {w}</div>
            ))}
            <nav className="wt-fl__tabs">
                {CARD_TABS.map(([slug, label]) => (
                    <button key={slug} type="button"
                        className={world.fleetTab === slug ? 'is-on' : ''}
                        onClick={() => {
                            emit('ui.open_tab', { tab: slug });
                            /* У «Истории изменений» нет своего адреса: это
                               модальное окно, а не вкладка со слугом. */
                            if (slug === 'changes') { go({ fleetPanel: 'changes' }); return; }
                            go({ fleetTab: slug });
                        }}>
                        {label}
                    </button>
                ))}
            </nav>
        </>
    );
};

/* Тело вкладки. Герой — с данными, остальные — с пустыми состояниями
   настоящего кабинета: сорок полных карточек не нужны никому, а «Работает»
   ≠ «есть данные» и у живого водителя тоже. */
const tabBody = (slug, world, go, emit, isHero) => {
    const c = world.case;
    if (!isHero) {
        return <Empty title="Нет данных" text="За выбранный период записей нет" />;
    }
    switch (slug) {
    case 'details':
        return (
            <>
                <p className="wt-fl__hint">
                    Некоторые поля недоступны для редактирования, для внесения изменений
                    обратитесь в <u>поддержку</u>
                </p>
                {c.detail_blocks.map(([block, rows]) => (
                    <section key={block} className="wt-fl__block">
                        <h3 className="wt-fl__h3">{block}</h3>
                        <div className="wt-fl__cols">
                            {rows.map(([label, value]) => <Field key={label} label={label} value={value} />)}
                        </div>
                    </section>
                ))}
            </>
        );
    case 'car':
        return (
            <>
                <h3 className="wt-fl__h3 is-caps">Выбор автомобиля</h3>
                <div className="wt-fl__seg-row">
                    <span className="is-on">Существующий</span><span>Новый</span>
                    <u>Полная карточка автомобиля</u>
                </div>
                <h3 className="wt-fl__h3">Детали</h3>
                <div className="wt-fl__cols">
                    <Field label="Статус" value={c.car.status} />
                    <Field label="Госномер" value={c.car.plate} />
                    <Field label="Марка" value={c.car.brand} />
                    <Field label="VIN" value={c.car.vin} />
                    <Field label="Модель" value={c.car.model} />
                    <Field label="Номер кузова" value={c.car.body} />
                    <Field label="Цвет" value={c.car.color} />
                    <Field label="СТС" value={c.car.sts} />
                    <Field label="Год" value={c.car.year} />
                    <Field label="Категории" value={c.car.categories} />
                    <Field label="Владелец автомобиля" value={c.car.owner} />
                </div>
                <h3 className="wt-fl__h3">Детские кресла</h3>
                <Field label="Парковые" value={c.car.child_seats} />
            </>
        );
    case 'income':
        return (
            <>
                <Filters items={['Период', 'Время начала: 00:00', 'Время окончания: 23:00']} />
                <div className="wt-fl__income">
                    <section className="wt-fl__report">
                        <h3 className="wt-fl__h3">Отчёт</h3>
                        {c.income.map(([label, value, tone]) => (
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
                        <p className="wt-fl__chart-foot">Всего заказов <b>{c.orders.length}</b></p>
                        <h3 className="wt-fl__h3">Часы</h3>
                        <div className="wt-fl__chart" aria-hidden="true">
                            <i style={{ height: '52%', background: '#fce000' }} />
                        </div>
                    </section>
                </div>
            </>
        );
    case 'transactions':
        return c.transactions.length ? (
            <>
                <Filters items={['Заказ', 'Период', '+ Фильтры']} />
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
                        {c.transactions.map((t, index) => (
                            <tr key={`${t.event}-${t.category}-${index}`} className={t.park ? 'is-park' : ''}>
                                <td>{t.when}</td><td>{t.event}</td><td>{t.category}</td>
                                <td className={`is-right${t.balance.startsWith('−') ? ' is-bad' : ' is-good'}`}>{t.balance}</td>
                                <td className={`is-right${t.sum.startsWith('−') ? ' is-bad' : ' is-good'}`}>{t.sum}</td>
                                <td>{t.comment}</td><td>{t.by}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
                <div className="wt-fl__totals">
                    {c.tx_totals.map(([label, value]) => (
                        <div key={label}><span>{label}</span><b>{value}</b></div>
                    ))}
                </div>
                <p className="wt-fl__note">
                    Строки с полосой слева — удержания ТАКСОПАРКА. Остальные комиссии в этой
                    таблице берёт сервис.
                </p>
            </>
        ) : <Empty title="Ничего не найдено" text="За выбранный период операций нет" />;
    case 'orders':
        return c.orders.length ? (
            <>
                <Filters items={['Дата подачи', 'Период', '+ Фильтры']} />
                {/* 22 колонки — столько их в кабинете. Короткий набор
                    «дата · номер · сумма» научил бы читать не тот экран. */}
                <div className="wt-fl__scroll-x">
                    <table className="wt-fl__table wt-fl__table--wide">
                        <thead>
                            <tr>{ORDER_COLUMNS.map((col) => <th key={col}>{col}</th>)}</tr>
                        </thead>
                        <tbody>
                            {c.orders.map((o) => (
                                <tr key={o.id}>
                                    <td>{o.status}</td>
                                    <td>
                                        {/* Карточка открывается ССЫЛКОЙ в этой
                                            ячейке, а не кликом по строке. */}
                                        <button type="button" className="wt-fl__link"
                                            onClick={() => {
                                                emit('ui.open_order', { id: o.id });
                                                go({ fleetView: 'order', fleetOrderId: o.id });
                                            }}>
                                            {o.id}
                                        </button>
                                    </td>
                                    <td>{c.car.plate}</td>
                                    <td>{o.when}</td>
                                    <td>{o.finished_when || '—'}</td>
                                    <td>{o.cancel_reason || '—'}</td>
                                    <td>{o.address}</td>
                                    <td>{o.category}</td>
                                    <td className="is-right">{o.distance_km}</td>
                                    <td className="is-right">{o.price}</td>
                                    {ORDER_MONEY.map((field) => (
                                        <td key={field} className="is-right">{o[field] || '0,00'}</td>
                                    ))}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </>
        ) : <Empty title="Ничего не найдено" text="За выбранный период заказов нет" />;
    case 'subvention':
        return <Empty title="Нет данных" text="У водителя нет доступа к заказам" />;
    case 'balances_history':
        return (
            <>
                <Filters items={['Период', 'Время начала: 00:00', 'Время окончания: 23:59']} />
                <table className="wt-fl__table">
                    <thead>
                        <tr><th>Дата</th><th className="is-right">Баланс, ₸</th><th className="is-right">Изменение, ₸</th></tr>
                    </thead>
                    <tbody>
                        {c.balance_history.map((row) => (
                            <tr key={row.at}>
                                <td>{row.when}</td>
                                <td className="is-right is-bad">{row.balance}</td>
                                <td className="is-right">{row.change}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </>
        );
    case 'shifts':
        return (
            <Empty title="Раздел переехал" text="Теперь смены будут в разделе GPS"
                action="Перейти" onAction={() => go({ fleetTab: 'gps' })} />
        );
    case 'gps':
        return (
            <>
                <Filters items={['Период', 'Время начала: 00:00', 'Время окончания: 23:59', 'Статус']} />
                <div className="wt-fl__gps">
                    {c.gps_tiles.map(([label, value]) => (
                        <div key={label} className="wt-fl__gps-tile"><span>{label}</span><b>{value}</b></div>
                    ))}
                </div>
                <table className="wt-fl__table">
                    <thead>
                        <tr><th>Статус</th><th>Дата и время</th><th>Скорость</th><th>Время</th><th>Пробег</th><th>Детали</th></tr>
                    </thead>
                    <tbody>
                        {c.gps_log.map((row, index) => (
                            <tr key={`${row.at}-${index}`}>
                                <td>● {row.status}</td><td>{row.when}</td><td /><td /><td /><td>{row.details}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </>
        );
    case 'photo-control':
        return (
            <div className="wt-fl__photo">
                {c.photo_days.map((day) => (
                    <section key={day.date}>
                        <h3 className="wt-fl__h3">
                            {day.ok ? null : <i className="wt-fl__dot is-red" aria-hidden="true" />}
                            {day.title}
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
        );
    case 'changes':
        return c.changes.length ? (
            <div className="wt-fl__changes">
                <h3 className="wt-fl__h3">История изменений</h3>
                {c.changes.map((ch, index) => (
                    <div key={`${ch.at}-${index}`} className="wt-fl__change">
                        <b>{ch.field}: {ch.from} → {ch.to}</b>
                        <span>{ch.when} · {ch.author}</span>
                    </div>
                ))}
            </div>
        ) : <Empty title="Нет данных" text="Изменений не было" />;
    case 'documents':
        /* В кабинете вкладка ПУСТАЯ: только плитка загрузки. Ни видов
           документов, ни сроков, ни статусов — сроки живут в «Деталях» у ВУ,
           а допуск к линии объясняет «Диагностика». */
        return (
            <div className="wt-fl__docs">
                <button type="button" className="wt-fl__doc-add"
                    onClick={() => emit('ui.action', { what: 'upload_document', args: {} })}
                    aria-label="Загрузить документ">+</button>
                {c.documents.map(([label, value]) => <Field key={label} label={label} value={value} />)}
            </div>
        );
    default:
        return <Empty title="Нет данных" text="Раздел пуст" />;
    }
};

const FleetCard = (props) => {
    const { world, go, emit } = props;
    const person = openedPerson(world);
    const isHero = person.id === world.case.contractor.id;
    return (
        <Shell {...props} title="Исполнители" crumb={person.name}>
            <CardHead world={world} go={go} emit={emit} />
            <div className="wt-fl__tab-body">{tabBody(world.fleetTab, world, go, emit, isHero)}</div>
            <div className="wt-fl__card-foot">
                <button type="button" className="wt-fl__wa"
                    onClick={() => emit('ui.action', { what: 'whatsapp', args: { id: person.id } })}>
                    Открыть в WhatsApp
                </button>
                {isHero ? (
                    <>
                        <button type="button" className="wt-fl__act"
                            onClick={() => props.act('balance_add', { id: person.id })}>
                            Начислить 1 000 ₸
                        </button>
                        <button type="button" className="wt-fl__act"
                            onClick={() => props.act('balance_sub', { id: person.id })}>
                            Списать 1 000 ₸
                        </button>
                        <button type="button" className="wt-fl__act"
                            onClick={() => props.act('limit', { id: person.id })}>
                            Понизить лимит
                        </button>
                    </>
                ) : null}
            </div>
        </Shell>
    );
};

/* ── Остальные разделы ───────────────────────────────────────────────────── */

const FleetVehicles = (props) => (
    <Shell {...props} title="Автомобили">
        <table className="wt-fl__table">
            <thead>
                <tr><th>Госномер</th><th>Марка и модель</th><th>Цвет</th><th>Год</th><th>Статус</th></tr>
            </thead>
            <tbody>
                {props.world.case.cars.map((row) => (
                    <tr key={row[0]}>{row.map((cell) => <td key={cell}>{cell}</td>)}</tr>
                ))}
            </tbody>
        </table>
    </Shell>
);

const FleetGoals = (props) => (
    <Shell {...props} title="Программа лояльности">
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

const FleetSupport = (props) => (
    <Shell {...props} title="Мои обращения">
        <div className="wt-fl__toolbar">
            <button type="button" className="wt-fl__filter-add"
                onClick={() => props.go({ fleetPanel: 'sfilters' })}>+ Фильтры</button>
            <span className="wt-fl__tools">
                <button type="button" className="wt-fl__yellow"
                    onClick={() => props.go({ fleetView: 'support_new' })}>Новое обращение</button>
            </span>
        </div>
        <table className="wt-fl__table">
            <thead>
                <tr><th>Вопрос</th><th>Статус</th><th>Обновлено</th><th>Создано</th></tr>
            </thead>
            <tbody>
                {props.world.case.support.map(([q, status, updated, created]) => (
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
        {props.world.fleetPanel === 'sfilters' ? (
            <Overlay title="Фильтры обращений" onClose={() => props.go({ fleetPanel: null })}>
                <ul className="wt-fl__checklist">
                    {['Выполнен', 'Закрыт', 'Требуется информация', 'В работе'].map((s) => (
                        <li key={s}><i aria-hidden="true" />{s}</li>
                    ))}
                </ul>
            </Overlay>
        ) : null}
    </Shell>
);

const FleetSupportNew = (props) => (
    <Shell {...props} title="Новое обращение">
        <div className="wt-fl__form">
            <Field label="Доступ" value="Мне и моей роли" />
            <Field label="Email" value="park@example.kz" />
            <div className="wt-fl__field"><span>Тема</span><b>Выберите тему обращения</b></div>
            <div className="wt-fl__field"><span>Сообщение</span><b>—</b></div>
            <div className="wt-fl__overlay-foot">
                <button type="button" className="wt-fl__yellow"
                    onClick={() => props.act('support_new', {})}>Отправить</button>
                <span className="wt-fl__note">Учебная форма: ничего не отправляется.</span>
            </div>
        </div>
    </Shell>
);

const FleetNews = (props) => (
    <Shell {...props} title="Новости">
        {NEWS.map(([title, when]) => (
            <div key={title} className="wt-fl__notice"><b>{title}</b><small>{when}</small></div>
        ))}
    </Shell>
);

const FleetLegal = (props) => (
    <Shell {...props} title="Правовые документы">
        <ul className="wt-fl__checklist">
            {LEGAL.map((doc) => <li key={doc}><i aria-hidden="true" />{doc}</li>)}
        </ul>
    </Shell>
);

const FleetParks = ({ world, go, act }) => (
    <div className="wt-fl wt-fl--picker">
        <TrainMark>Учебная среда</TrainMark>
        <div className="wt-fl__picker">
            <h2>Выберите парк</h2>
            <div className="wt-fl__picker-list">
                <span className="wt-fl__picker-search">Поиск</span>
                {PARKS.map((park) => (
                    <button key={`${park.name}-${park.city}`} type="button"
                        className={park.name === world.case.park.name ? 'is-on' : ''}
                        onClick={() => {
                            act('switch_park', { to: park.name });
                            go({ fleetView: 'home' });
                        }}>
                        <b>{park.initials}</b>
                        <span>{park.name}<small>{park.city}</small></span>
                    </button>
                ))}
            </div>
        </div>
    </div>
);

const Fleet404 = (props) => (
    <Shell {...props} title="Исполнители">
        <Empty
            title="Ничего не найдено"
            text={props.world.fleet404
                ? 'Страница карточки этого исполнителя недоступна — открывается только панель поверх списка'
                : 'Такой страницы не существует'}
            action="Вернуться к списку"
            onAction={() => props.go({ fleetView: 'contractors', fleet404: false })} />
    </Shell>
);


/* ── Карточка заказа ─────────────────────────────────────────────────────────
 *
 * Отдельная СТРАНИЦА `/orders/<32hex>`, а не строка таблицы: открывается
 * ссылкой в ячейке «Код заказа». Здесь же единственное место в кабинете, где
 * видно ожидание клиента — блок «Выполнение» с посекундными этапами.
 * Ожидание = «В пути» минус «На месте»; у отменённого — от «На месте» до отмены.
 */

const Rows = ({ items }) => (
    <>
        {items.map(([label, value]) => (
            <div key={label} className="wt-fl__ord-row"><span>{label}</span><b>{value}</b></div>
        ))}
    </>
);

const Stages = ({ stages, inline = false }) => (
    <div className={`wt-fl__stages${inline ? ' is-inline' : ''}`}>
        {stages.map((stage, index) => (
            <div key={`${stage.name}-${index}`} className="wt-fl__stage">
                <span>
                    {stage.name}
                    {stage.delta ? <i>{stage.delta}</i> : null}
                </span>
                <b>{stage.when}</b>
            </div>
        ))}
    </div>
);

const FleetOrder = (props) => {
    const { world, go, emit } = props;
    const c = world.case;
    const order = c.orders.find((o) => o.id === world.fleetOrderId) || c.orders[0];

    if (!order) {
        return (
            <Shell {...props} title="Заказ">
                <Empty title="Ничего не найдено" text="Такого заказа нет"
                    action="К списку заказов"
                    onAction={() => go({ fleetView: 'card', fleetTab: 'orders' })} />
            </Shell>
        );
    }

    const card = order.card;
    const done = /Заверш|Выполн/i.test(order.status);

    return (
        <Shell {...props} title={`Заказ ${order.id}`}>
            <div className="wt-fl__order">
                <aside className={`wt-fl__ord-head${done ? ' is-done' : ' is-cancelled'}`}>
                    <b>{order.status}</b>
                    <div className="wt-fl__ord-who">
                        <i className="wt-fl__ava" aria-hidden="true" />
                        <span>{c.contractor.last} {c.contractor.first}</span>
                    </div>
                    <Rows items={[
                        ['ВУ', c.contractor.license],
                        ['Телефон', c.contractor.phone_pretty],
                        ['Номер заказа в парке', order.id],
                        ['Номер заказа', order.order_uuid || '—'],
                        ['Дата подачи заказа', order.when],
                        ['Тип заказа', card?.type || '—'],
                    ]} />
                    <div className="wt-fl__ord-map" aria-hidden="true" />
                    <Rows items={[
                        ['Откуда', order.address || '—'],
                        ['Куда', order.address_to || '—'],
                        ['Расстояние', order.distance_km ? `${order.distance_km} км` : '—'],
                    ]} />
                </aside>

                <section className="wt-fl__ord-main">
                    <header className="wt-fl__ord-block-head">
                        Детализация
                        <span className="wt-fl__chip">Транзакции</span>
                    </header>
                    {card?.totals?.length
                        ? <Rows items={card.totals} />
                        : <p className="wt-fl__note">Детализация по этому заказу не заполнена</p>}

                    <header className="wt-fl__ord-block-head">Описание</header>
                    <Rows items={[
                        ['Баллы приоритета', card?.priority_points || '—'],
                        ['Статус', order.status],
                        ['Тариф', order.category || '—'],
                        ['Номер заказа', order.id],
                    ]} />
                    {/* «Длительность поездки» раскрывается теми же этапами —
                        так это и сделано в кабинете. */}
                    {card?.stages?.length ? (
                        <>
                            <div className="wt-fl__ord-row is-open"><span>Длительность поездки</span>
                                <b>{card.stages.find((x) => x.delta && /Выполн|Отмен/i.test(x.name))?.delta
                                    || '—'}</b>
                            </div>
                            <Stages stages={card.stages} inline />
                        </>
                    ) : null}
                    <Rows items={[
                        ['Пробег', order.distance_km ? `${order.distance_km} км` : '—'],
                        ['Оплата', card?.payment || '—'],
                        ['Чей заказ', card?.owner || '—'],
                    ]} />

                    <header className="wt-fl__ord-block-head">Выполнение</header>
                    {card?.stages?.length
                        ? <Stages stages={card.stages} />
                        : <p className="wt-fl__note">Хронология по этому заказу не заполнена</p>}

                    <header className="wt-fl__ord-block-head">Комментарий</header>
                    <div className="wt-fl__ord-comment">Введите текст</div>
                    <div className="wt-fl__ord-foot">
                        <button type="button" className="wt-fl__act"
                            onClick={() => go({ fleetView: 'card', fleetTab: 'orders' })}>Отмена</button>
                        <button type="button" className="wt-fl__yellow"
                            onClick={() => emit('ui.action', { what: 'order_comment', args: { id: order.id } })}>
                            Сохранить
                        </button>
                    </div>
                </section>

                <aside className="wt-fl__ord-side">
                    <button type="button" className="wt-fl__act"
                        onClick={() => emit('ui.action', { what: 'order_support', args: { id: order.id } })}>
                        Поддержка
                    </button>
                </aside>
            </div>
        </Shell>
    );
};

const VIEWS = {
    home: FleetHome,
    contractors: FleetContractors,
    card: FleetCard,
    order: FleetOrder,
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
    if (v === 'order') {
        const order = (world.case.orders || []).find((o) => o.id === world.fleetOrderId);
        return `${base}/orders/${String(order?.order_uuid || '').slice(0, 12)}…`;
    }
    if (v === 'card') return `${base}/contractors/${String(world.fleetOpenId || '').slice(0, 8)}…/${world.fleetTab}`;
    if (v === 'contractors') {
        const filters = world.fleetFilters || [];
        return filters.length ? `${base}/contractors?filters=${filters.length}` : `${base}/contractors`;
    }
    if (v === 'support_new') return `${base}/support/new`;
    if (v === 'notfound') return `${base}/404`;
    return `${base}/${v || 'home'}`;
};

/** Кабинет целиком. go — свободный переход, emit — лента, act — правка данных. */
export default function FleetApp({ world, go, emit, act }) {
    const View = VIEWS[world.fleetView] || FleetHome;
    return <View world={world} go={go} emit={emit} act={act} />;
}
