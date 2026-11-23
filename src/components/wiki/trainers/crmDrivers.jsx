import React, { useEffect, useState } from 'react';

import { caseByAccount, crmAccount, findCrmAccounts } from './crmModel.js';
import {
    beyondWindow, defaultPeriod, fmtDuration, fmtMoney, fmtNumber, fmtShiftDuration, gpsBadge, gpsRows,
    gpsSummary, incomeSummary, isoDay, orderCard, orderRows, orderStatus, orderTotals, otherChanges, statementCategories,
    statementRows, statementSummary, statusChanges, workRuleLog,
} from './crmCard.js';
import { emptyYaForm, yaParkFor } from './crmYandex.js';
import { Multiselect, VModal, noNav } from './crmKit';

/* «Водители → Учетные записи водителей» и «Карточка водителя» — разметкой
 * рабочей CRM (driver-account-listing в режиме оператора и карточка
 * driver-card), снятой 20.11.2026.
 *
 * У оператора общего списка нет: только поиск по ТОЧНОМУ ID в Яндексе,
 * телефону, ИИН, ВУ или госномеру, и CRM помнит «Недавние поиски». Водителей
 * в учебной CRM несколько, и у каждого своя ситуация — всё, что нужно для
 * ответа, видно в его карточке (Диспетчерской в тренажёре нет).
 *
 * Кнопки, которые в CRM МЕНЯЮТ данные водителя (СМЗ, Физлицо, Лимит,
 * Автомобиль, Код), здесь есть — по ним учатся, где что лежит, — но ничего не
 * меняют: «Код» спрашивает подтверждение тем же окном браузера, что и CRM, и
 * ничего не отправляет; остальные открывают страницу «не входит в учебную
 * среду».
 */

const two = (value) => String(value).padStart(2, '0');
const hhmm = () => {
    const at = new Date();
    return `${two(at.getHours())}:${two(at.getMinutes())}`;
};

/* Текст подтверждения «Код» — дословно из CRM (onsubmit формы resend-otp). */
export const CODE_CONFIRM = 'Отправить водителю НОВЫЙ код подтверждения по SMS?\n\n'
    + 'Текущий пароль водителя перестанет действовать — войти он сможет только по новому коду.';

/* «В Диспетчерской» в рабочей CRM открывает кабинет Яндекса в новой вкладке.
   В тренажёре его нет — всё нужное в карточке CRM, о чём и говорим. */
const FLEET_NOTE = {
    title: 'Учебная среда',
    text: 'Диспетчерская в тренажёре не открывается: всё, что нужно, — в карточке водителя CRM.',
    type: 'warn',
};

/** Итог поиска: что показать и что записать в «Недавние поиски». */
export const accountSearchPatch = (world, text) => {
    const value = String(text || '').trim();
    const found = findCrmAccounts(world, value);
    const history = world.drvHistory || [];
    const item = {
        search: value,
        label: found.length ? found[0].name : 'Не найдено',
        found: found.length > 0,
        at: hhmm(),
    };
    return {
        drvQuery: value,
        drvSearched: value,
        drvHistory: value ? [item, ...history.filter((h) => h.search !== value)].slice(0, 10) : history,
    };
};

/* «Недавние поиски» — компонент driver-lookup-history: раскрыт, пока
   результатов нет, и сворачивается, когда водитель нашёлся. */
const LookupHistory = ({ ui, items, expanded, onPick }) => {
    const [open, setOpen] = useState(expanded);
    useEffect(() => setOpen(expanded), [expanded]);
    if (!items.length) return null;
    return (
        <div className="mb-3">
            <a href="#" className={`small${ui.t('drv_history_toggle')}`} onClick={(e) => { noNav(e); setOpen(!open); }}>
                <i className="fa fa-history" /> Недавние поиски ({items.length})
                {' '}<i className={`fa ${open ? 'fa-angle-up' : 'fa-angle-down'}`} />
            </a>
            <div className="list-group mt-1" style={open ? { maxHeight: '22rem', overflowY: 'auto' } : { maxHeight: '22rem', overflowY: 'auto', display: 'none' }}>
                {items.map((item) => (
                    <a key={item.search} href="#" title="Найти снова"
                        className={`list-group-item list-group-item-action py-1 px-2 d-flex align-items-center${ui.t('drv_history')}`}
                        onClick={(e) => { noNav(e); onPick(item.search); }}>
                        <span className="text-monospace text-nowrap mr-3">{item.search}</span>
                        <span className={`text-truncate flex-grow-1${item.found ? '' : ' text-muted'}`}>{item.label}</span>
                        <span className="small text-muted text-nowrap ml-3">{item.at}</span>
                    </a>
                ))}
            </div>
        </div>
    );
};

/* Переход в карточку: вкладка «Общее», окна закрыты, время открытия — для
   «Истории» карточки (открытие пишется туда, как в рабочей CRM). */
const openCard = (ui, id, row) => ui.go('account', {
    drvOpenId: row.accountId,
    drvOpenNumber: row.id,
    drvTab: 'general',
    drvModal: null,
    drvOrder: null,
    drvOpenedAt: ui.world.now.slice(0, 16),
}, id, { accountId: row.accountId });

/* Страницы, которых в учебной среде нет: перевод в СМЗ, лимит, редактор
   автомобиля… В CRM это отдельные страницы, и все они МЕНЯЮТ данные. */
const stub = (ui, id, key, payload = {}) => ui.act(id, { crmView: 'stub', crmStub: key, drvModal: null }, payload);

/* Подпись «Работает» в списке учёток — как workStatusLabel CRM. */
const WORK_LABEL = { Работает: ['Да', 'badge-success'], Уволен: ['Уволен', 'badge-danger'] };

/** Страница «Учетные записи водителей» (оператор: только поиск). */
export const AccountSearch = ({ ui }) => {
    const { world } = ui;
    const [query, setQuery] = useState(world.drvQuery || '');
    useEffect(() => setQuery(world.drvQuery || ''), [world.drvQuery]);
    const searched = world.drvSearched;
    const rows = searched ? findCrmAccounts(world, searched) : [];

    const run = (text, id = 'drv_search') => {
        const value = String(text || '').trim();
        const patch = accountSearchPatch(world, value);
        setQuery(value);
        /* Поиск — выбор, а не кнопка-ход: результат («Найдено: 0», строка в
           «Недавних поисках») виден всегда, а неверный запрос там, где урок
           ждёт поиска, помощник объясняет. */
        ui.pick(id, patch, { query: value });
        if (ui.emit) ui.emit('crm.driver_search', { query: value, found: findCrmAccounts(world, value).length });
    };

    return (
        <div className="row">
            <div className="col">
                <div className="card">
                    <nav aria-label="breadcrumb">
                        <ol className="breadcrumb">
                            <li className="breadcrumb-item"><a href="#" onClick={(e) => { noNav(e); ui.crumb('home'); }}>Главная</a></li>
                            <li className="breadcrumb-item active" aria-current="page">Учетные записи водителей</li>
                        </ol>
                    </nav>
                    <div className="card-header"><i className="fa fa-align-justify" /> Учетные записи водителей </div>
                    <div className="card-body">
                        <div className="card-block" style={{ overflowX: 'scroll' }}>
                            <form onSubmit={(event) => { event.preventDefault(); run(query); }}>
                                <div className="row">
                                    <div className="form-group col-md-6">
                                        <label className="small">Поиск</label>
                                        <div className="input-group input-group-sm">
                                            <input
                                                className={`form-control${ui.t('drv_query')}`}
                                                placeholder="ID в Яндексе, телефон, ИИН, ВУ или госномер"
                                                value={query}
                                                onChange={(event) => {
                                                    setQuery(event.target.value);
                                                    ui.touch('drv_query', null, { query: event.target.value });
                                                }}
                                            />
                                            <span className="input-group-append">
                                                <button className={`btn btn-primary${ui.t('drv_search')}`} type="button" onClick={() => run(query)}>
                                                    <i className="fa fa-search" />
                                                </button>
                                            </span>
                                        </div>
                                    </div>
                                </div>
                                <div className="row">
                                    <div className="col form-group px-2">
                                        <button className={`btn btn-sm btn-primary ml-2${ui.t('drv_apply')}`} type="submit">
                                            <i className="fa fa-filter" /> Применить
                                        </button>
                                        {' '}
                                        <button className="btn btn-sm btn-outline-secondary ml-2" disabled={!searched} type="button"
                                            onClick={() => { setQuery(''); ui.touch('drv_reset', { drvQuery: '', drvSearched: null }); }}>
                                            {' '}Сбросить{' '}
                                        </button>
                                        {' '}
                                        <span className="text-muted small ml-2"> Найдено: {rows.length} </span>
                                    </div>
                                </div>
                            </form>

                            <LookupHistory ui={ui} items={world.drvHistory || []} expanded={!rows.length}
                                onPick={(text) => run(text, 'drv_history')} />

                            <table className="table table-hover table-listing">
                                <thead>
                                    <tr>
                                        <th>ID</th>
                                        <th>Аккаунт</th>
                                        <th>Водитель</th>
                                        <th>Название парка</th>
                                        <th>Работает</th>
                                        <th>Статус</th>
                                        <th>Тип сотрудничества</th>
                                        <th>Обновлено</th>
                                        <th />
                                    </tr>
                                </thead>
                                <tbody>
                                    {rows.map((row) => {
                                        const [workLabel, workTone] = WORK_LABEL[row.workStatus] || ['Нет', 'badge-warning'];
                                        return (
                                            <tr key={row.id}>
                                                <td>{row.id}</td>
                                                <td>
                                                    <a href="#" target="_blank" className={ui.t('drv_fleet_link').trim() || undefined}
                                                        onClick={(e) => { noNav(e); ui.act('drv_fleet_link', { crmNote: FLEET_NOTE }); }}>
                                                        {' '}{row.accountId}{' '}
                                                    </a>
                                                </td>
                                                <td>
                                                    <a href="#" target="_blank" onClick={(e) => { noNav(e); stub(ui, 'drv_driver_link', 'drivers'); }}>
                                                        {' '}{row.driverId}{' '}
                                                    </a>
                                                </td>
                                                <td>{row.park}</td>
                                                <td><span className={`badge ${workTone}`}> {workLabel} </span></td>
                                                <td>
                                                    <span className={row.online === 'Офлайн' ? 'text-muted' : 'text-success'}>
                                                        <i className="fa fa-circle" style={{ fontSize: 8 }} /> {row.online}
                                                    </span>
                                                </td>
                                                <td>{row.employmentShort}</td>
                                                <td><small> {world.now.slice(0, 16)} </small></td>
                                                <td>
                                                    <div className="row no-gutters">
                                                        <div className="col-auto mr-1">
                                                            <a className={`btn btn-sm btn-primary${ui.t('drv_open')}`} href="#" title="Подробнее" role="button"
                                                                onClick={(e) => { noNav(e); openCard(ui, 'drv_open', row); }}>
                                                                <i className="fa fa-eye" /> Подробнее
                                                            </a>
                                                        </div>
                                                        {row.employmentKind === 'park_employee' ? (
                                                            <div className="col-auto ml-1">
                                                                <a className={`btn btn-sm btn-spinner btn-warning${ui.t('drv_smz')}`} href="#" title="Перевести в СМЗ" role="button"
                                                                    onClick={(e) => { noNav(e); stub(ui, 'drv_smz', 'smz', { accountId: row.accountId }); }}> СМЗ </a>
                                                            </div>
                                                        ) : null}
                                                        {['individual_entrepreneur', 'selfemployed'].includes(row.employmentKind) ? (
                                                            <div className="col-auto ml-1">
                                                                <a className={`btn btn-sm btn-spinner text-white${ui.t('drv_individual')}`} href="#" role="button"
                                                                    style={{ backgroundColor: '#20a8d8' }} title="Перерегистрировать в физическое лицо"
                                                                    onClick={(e) => { noNav(e); stub(ui, 'drv_individual', 'individual', { accountId: row.accountId }); }}> Физлицо </a>
                                                            </div>
                                                        ) : null}
                                                        <div className="col-auto ml-1">
                                                            <a className={`btn btn-sm text-white${ui.t('drv_limit')}`} href="#" style={{ backgroundColor: '#63c2de' }} title="Управление лимитом"
                                                                onClick={(e) => { noNav(e); stub(ui, 'drv_limit', 'limit', { accountId: row.accountId }); }}>
                                                                <i className="fa fa-money" /> Лимит
                                                            </a>
                                                        </div>
                                                        <div className="col-auto ml-1">
                                                            <a className={`btn btn-sm btn-spinner text-white${ui.t('drv_car')}`} href="#" role="button"
                                                                style={{ backgroundColor: '#6f42c1' }} title="Редаĸтор автомобиля"
                                                                onClick={(e) => { noNav(e); stub(ui, 'drv_car', 'car_editor', { accountId: row.accountId }); }}> Автомобиль </a>
                                                        </div>
                                                        <div className="col-auto ml-1">
                                                            <form onSubmit={(event) => event.preventDefault()}>
                                                                <button className={`btn btn-sm btn-spinner text-white${ui.t('drv_code')}`} style={{ backgroundColor: '#f86c6b' }}
                                                                    type="button" title="Отправить водителю новый код подтверждения"
                                                                    onClick={(event) => {
                                                                        /* Не та кнопка в уроке — сразу объяснение, без
                                                                           окна: подтверждение перед ошибкой сбивает. */
                                                                        if (ui.misses('drv_code')) { ui.act('drv_code', null, { accountId: row.accountId }); return; }
                                                                        const view = event.currentTarget.ownerDocument.defaultView;
                                                                        if (!view.confirm(CODE_CONFIRM)) {
                                                                            ui.touch('drv_code_cancel');
                                                                            return;
                                                                        }
                                                                        ui.act('drv_code', {
                                                                            drvCodeSent: [...(world.drvCodeSent || []), { accountId: row.accountId, at: world.now }],
                                                                            crmNote: { title: 'Готово', text: 'Учебная среда: SMS не отправлено', type: 'success' },
                                                                        }, { accountId: row.accountId });
                                                                    }}>
                                                                    <i className="fa fa-key" /> Код
                                                                </button>
                                                            </form>
                                                        </div>
                                                    </div>
                                                </td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                            {!rows.length ? (
                                <div className="alert alert-secondary py-3 mb-0">
                                    {' '}Общий список вам недоступен: здесь показываются только аккаунты найденного водителя. Введите ID в
                                    Яндексе, телефон, ИИН, ВУ или госномер — нужно точное совпадение, обрывок номера или ФИО не подойдут.{' '}
                                </div>
                            ) : null}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
};

const TABS = [
    ['general', 'Общее'], ['income', 'Заработок'], ['statement', 'Ведомость'], ['orders', 'Заказы'],
    ['gps', 'GPS'], ['bonuses', 'Бонусы'], ['statuses', 'Изменения статусов'], ['history', 'История'],
    ['promotions', 'Акции и розыгрыши'],
];

/** Название вкладки карточки — для реплик помощника. */
export const accountTabTitle = (key) => (TABS.find(([id]) => id === key) || [key, key])[1];

const Detail = ({ label, value, text = false }) => (
    <div className={`detail-row${text ? ' detail-row--text' : ''}`}>
        <span className="detail-row__label">{label}</span> <span className="detail-row__value">{value || '—'}</span>
    </div>
);

const Tile = ({ icon, label, value, muted = false, tone = '', extra = null, extraTitle = '' }) => (
    <div className={`dispatcher-tile${muted ? ' dispatcher-tile--muted' : ''}`}>
        <div className="dispatcher-tile__label"><i className={`fa ${icon}`} /> {label} </div>
        <div className={`dispatcher-tile__value${tone ? ` ${tone}` : ''}`}> {value} </div>
        {extra ? <div className="dispatcher-tile__extra" title={extraTitle}> {extra} </div> : null}
    </div>
);

/* Поля периода вкладок: CRM грузит данные за выбранный промежуток, по
   умолчанию — вчера и сегодня. Здесь фильтр настоящий: сдвинул дату —
   строки за другие дни появились или ушли. */
const PeriodFields = ({ draft, setDraft }) => (
    <>
        <div className="income-filters__field">
            <label className="small text-muted mb-0">С даты</label>
            <input className="form-control form-control-sm" type="date" max={draft.to} value={draft.from}
                onChange={(event) => setDraft({ ...draft, from: event.target.value })} />
        </div>
        <div className="income-filters__field">
            <label className="small text-muted mb-0">По дату</label>
            <input className="form-control form-control-sm" type="date" min={draft.from} value={draft.to}
                onChange={(event) => setDraft({ ...draft, to: event.target.value })} />
        </div>
        <div className="income-filters__field">
            <label className="small text-muted mb-0">Время начала</label>
            <input className="form-control form-control-sm" type="time" value={draft.timeFrom}
                onChange={(event) => setDraft({ ...draft, timeFrom: event.target.value })} />
        </div>
        <div className="income-filters__field">
            <label className="small text-muted mb-0">Время окончания</label>
            <input className="form-control form-control-sm" type="time" value={draft.timeTo}
                onChange={(event) => setDraft({ ...draft, timeTo: event.target.value })} />
        </div>
    </>
);

const ShowButton = ({ onClick, children = null, target = '' }) => (
    <div className="income-filters__field income-filters__field--action">
        <button className={`btn btn-sm btn-primary${target}`} type="button" onClick={onClick}><i className="fa fa-refresh" /> Показать </button>
        {children}
    </div>
);

/* Период вкладки: черновик полей и применённый «Показать». */
const usePeriod = (nowMs) => {
    const [draft, setDraft] = useState(() => defaultPeriod(nowMs));
    const [period, setPeriod] = useState(draft);
    return { draft, setDraft, period, apply: () => setPeriod(draft) };
};

/* multiselect фильтра — как в CRM; пустой справочник — «Справочник не получен». */
const FilterSelect = ({ label, placeholder, options = [], value = null, onSelect = () => {} }) => (
    <div className="income-filters__field statement-filters__categories">
        <label className="small text-muted mb-0">{label}</label>
        <Multiselect id={null} value={value} placeholder={placeholder} showLabels={false}
            options={options.map((title) => ({ value: title, label: title }))} onSelect={onSelect}
            noOptions={<span>Справочник не получен</span>} />
    </div>
);

const OrderLink = ({ ui, number, children }) => (
    <a href="#" title="Открыть заказ" className={ui.t('drv_order').trim() || undefined}
        onClick={(e) => { noNav(e); ui.touch('drv_order', { drvModal: 'order-card', drvOrder: number }, { order: number }); }}>
        {children}
    </a>
);

/* Столбики «Заказы» за дни периода: завершённые, отменённые, несостоявшиеся —
   цвета графика CRM. */
const OrdersChart = ({ days }) => {
    const max = Math.max(1, ...days.map((day) => day.completed + day.cancelled + day.failed));
    return (
        <div className="income-chart" style={{ display: 'flex', alignItems: 'flex-end', gap: 12, padding: '8px 4px 22px' }}>
            {days.map((day) => (
                <div key={day.day} style={{ flex: '0 0 36px', textAlign: 'center', fontSize: 11, color: '#73818f' }}>
                    <div style={{ height: 140, display: 'flex', flexDirection: 'column-reverse' }}>
                        <div style={{ height: `${(day.completed / max) * 100}%`, background: '#4dbd74' }} />
                        <div style={{ height: `${(day.cancelled / max) * 100}%`, background: '#f8cb00' }} />
                        <div style={{ height: `${(day.failed / max) * 100}%`, background: '#f86c6b' }} />
                    </div>
                    {day.day}
                </div>
            ))}
        </div>
    );
};

/* Вкладки, кроме «Общее» — разметкой вкладок рабочей CRM. */
const IncomePane = ({ world, item }) => {
    const { draft, setDraft, period, apply } = usePeriod(world.nowMs);
    const summary = incomeSummary(item, period);
    return (
        <div>
            <div className="income-filters mb-3"><PeriodFields draft={draft} setDraft={setDraft} /><ShowButton onClick={apply} /></div>
            <div className="row">
                <div className="col-md-6">
                    <div className="income-card">
                        <h6 className="income-card__title">Отчёт</h6>
                        <div className="income-row"><span>Завершённые поездки</span> <span>{summary.completed}</span></div>
                        <div className="income-row"><span>Сумма с таксометра</span> <span>{fmtMoney(summary.price)}</span></div>
                        <div className="income-row"><span>Пробег, км</span> <span>{fmtNumber(summary.mileage_km, 1)}</span></div>
                        {summary.balances.map((row) => (
                            <div className="income-row" key={row.key}>
                                <span>{row.label}</span> <span className={row.value < 0 ? 'text-danger' : ''}>{fmtMoney(row.value)}</span>
                            </div>
                        ))}
                        <div className="income-row income-row--total"><span>Итого</span> <span>{fmtMoney(summary.total)}</span></div>
                        <div className="income-row"><span>Часы работы</span> <span>{fmtDuration(summary.work_seconds)}</span></div>
                        <div className="income-row"><span>Среднечасовой заработок</span> <span>{fmtMoney(summary.per_hour)} в час</span></div>
                    </div>
                </div>
                <div className="col-md-6">
                    <div className="income-card mb-3">
                        <h6 className="income-card__title">Заказы</h6>
                        {summary.days.length ? (
                            <>
                                <OrdersChart days={summary.days} />
                                <div className="income-row income-row--total">
                                    <span>Всего заказов</span>
                                    <span>{summary.days.reduce((all, day) => all + day.completed + day.cancelled + day.failed, 0)}</span>
                                </div>
                            </>
                        ) : <p className="text-muted small mb-0">Данные по заказам не получены.</p>}
                    </div>
                    <div className="income-card">
                        <h6 className="income-card__title">Часы</h6>
                        {summary.work_seconds ? (
                            <div className="income-row income-row--total"><span>Всего часов</span> <span>{fmtDuration(summary.work_seconds, false)}</span></div>
                        ) : <p className="text-muted small mb-0">Данные по часам не получены.</p>}
                    </div>
                </div>
            </div>
            <div className="mt-3">
                <button className="btn btn-sm btn-outline-primary" type="button"><i className="fa fa-table" /> Отчёт по транзакциям по дням </button>
            </div>
        </div>
    );
};

const StatementPane = ({ ui, world, item }) => {
    const { draft, setDraft, period, apply } = usePeriod(world.nowMs);
    const [withoutCash, setWithoutCash] = useState(false);
    const [category, setCategory] = useState(null);
    const rows = statementRows(item, period, { withoutCash, category: category || '' });
    const summary = statementSummary(rows);
    return (
        <div>
            <div className="income-filters mb-3">
                <PeriodFields draft={draft} setDraft={setDraft} />
                <FilterSelect label="Категории" placeholder="Все категории" options={statementCategories(item)}
                    value={category} onSelect={(next) => setCategory(next === category ? null : next)} />
                <div className="income-filters__field form-check statement-filters__cash">
                    <input className="form-check-input" id="statement-without-cash" type="checkbox" checked={withoutCash}
                        onChange={() => setWithoutCash(!withoutCash)} />
                    {' '}
                    <label className="form-check-label" htmlFor="statement-without-cash"> Показать все, кроме наличных и в ожидании </label>
                </div>
                <ShowButton onClick={apply} target={ui.t('drv_period')} />
            </div>
            {beyondWindow(period, world.nowMs) ? (
                <div className="alert alert-info py-2 small">
                    {' '}Диспетчерская хранит операции только примерно за последние двое суток. За более ранние дни ведомость будет
                    пустой или неполной, хотя в «Заработке» деньги за них есть.{' '}
                </div>
            ) : null}
            <div className="">
                <div className="table-responsive">
                    <table className="table table-sm table-hover statement-table mb-2">
                        <thead>
                            <tr>
                                <th>Дата</th><th>Событие</th><th>Категория</th><th className="text-right">Баланс</th>
                                <th className="text-right">Сумма</th><th>Комментарий</th><th>Инициатор</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows.length ? rows.map((row) => (
                                <tr key={row.id}>
                                    <td className="text-nowrap">{row.when || '—'}</td>
                                    <td className="text-nowrap">
                                        {row.order ? <OrderLink ui={ui} number={row.order}>{row.event || 'Заказ'}</OrderLink> : <span>{row.event || '—'}</span>}
                                    </td>
                                    <td>{row.category || '—'}</td>
                                    <td className="text-right text-nowrap">{fmtMoney(row.balance)}</td>
                                    <td className={`text-right text-nowrap${row.amount < 0 ? ' text-danger' : ''}`}> {fmtMoney(row.amount)} </td>
                                    <td className="statement-table__comment">{row.comment || '—'}</td>
                                    <td>{row.by || '—'}</td>
                                </tr>
                            )) : (
                                <tr><td className="text-center text-muted py-3" colSpan={7}> Операций за период нет. </td></tr>
                            )}
                        </tbody>
                    </table>
                </div>
                <div className="income-card statement-summary">
                    <div className="income-row"><span>Загружено операций</span> <span>{summary.count}</span></div>
                    <div className="income-row">
                        <span>Сумма загруженных операций</span> <span className={summary.sum < 0 ? 'text-danger' : ''}>{fmtMoney(summary.sum)}</span>
                    </div>
                    <div className="income-row">
                        <span>Баланс на начало периода</span> <span>{summary.before === null ? '—' : fmtMoney(summary.before)}</span>
                    </div>
                    <div className="income-row income-row--total">
                        <span>Баланс на конец периода</span>
                        <span>
                            {summary.after === null ? '—' : fmtMoney(summary.after)}
                            {summary.change !== null ? (
                                <span className="text-muted font-weight-normal"> ({summary.change > 0 ? '+' : ''}{fmtMoney(summary.change)})</span>
                            ) : null}
                        </span>
                    </div>
                </div>
            </div>
        </div>
    );
};

const OrdersPane = ({ ui, world, item }) => {
    const { draft, setDraft, period, apply } = usePeriod(world.nowMs);
    const rows = orderRows(item, period);
    const totals = orderTotals(rows);
    return (
        <div>
            <div className="income-filters mb-2">
                <PeriodFields draft={draft} setDraft={setDraft} />
                <div className="income-filters__field">
                    <label className="small text-muted mb-0">Период по</label>
                    <select className="form-control form-control-sm" defaultValue="booked_at">
                        <option value="booked_at">дате подачи</option>
                        <option value="ended_at">дате завершения</option>
                    </select>
                </div>
                <div className="income-filters__field">
                    <label className="small text-muted mb-0">Код заказа</label>
                    <input className="form-control form-control-sm" inputMode="numeric" placeholder="42897480" type="text" />
                </div>
            </div>
            <div className="income-filters mb-2">
                <FilterSelect label="Статус" placeholder="Все статусы" options={['Выполнен', 'Отменён', 'Не состоялся']} />
                <FilterSelect label="Способ оплаты" placeholder="Все способы" options={['Наличные', 'Безналичная']} />
                <FilterSelect label="Категория" placeholder="Все категории" options={[...new Set((item.orders || []).map((o) => o.category))]} />
                <ShowButton onClick={apply} />
            </div>
            <div className="form-check mb-3">
                <input className="form-check-input" id="orders-show-empty" type="checkbox" />
                {' '}
                <label className="form-check-label small" htmlFor="orders-show-empty"> Показывать денежные колонки, где у всех заказов ноль </label>
            </div>
            <div className="">
                <div className="table-responsive daily-report">
                    <table className="table table-sm table-hover daily-report__table orders-table mb-2">
                        <thead>
                            <tr>
                                <th className="daily-report__name orders-table__status">Статус</th><th>Код заказа</th><th>Автомобиль</th>
                                <th>Дата подачи</th><th>Дата завершения</th><th>Причина отмены</th><th>Адрес</th><th>Категория</th>
                                <th className="text-right">Пробег, км</th><th className="text-right">Стоимость, ₸</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows.length ? rows.map((row) => {
                                const status = orderStatus(row.status);
                                return (
                                    <tr key={row.id}>
                                        <td className="daily-report__name orders-table__status"><span className={`badge ${status.tone}`}>{status.label}</span></td>
                                        <td><OrderLink ui={ui} number={row.number}>{row.number}</OrderLink></td>
                                        <td>{row.car || '—'}</td>
                                        <td>{row.booked_at || '—'}</td>
                                        <td>{row.ended_at || '—'}</td>
                                        <td className="orders-table__wrap">{row.cancel_reason || ''}</td>
                                        <td className="orders-table__wrap orders-table__address">
                                            {row.from ? <div>{row.from} <span className="text-muted">· откуда</span></div> : null}
                                            {row.to ? <div>{row.to} <span className="text-muted">· куда</span></div> : null}
                                        </td>
                                        <td>{row.category || '—'}</td>
                                        <td className="text-right">{row.km === null || row.km === undefined ? '' : fmtMoney(row.km)}</td>
                                        <td className="text-right">{row.price === null || row.price === undefined ? '' : fmtMoney(row.price)}</td>
                                    </tr>
                                );
                            }) : (
                                <tr><td className="text-center text-muted py-3" colSpan={10}> Заказов за период нет. </td></tr>
                            )}
                        </tbody>
                        {rows.length ? (
                            <tfoot>
                                <tr className="daily-report__total">
                                    <td className="daily-report__name orders-table__status">Кол-во: {totals.count}</td>
                                    <td colSpan={7} />
                                    <td className="text-right">{fmtMoney(totals.km)}</td>
                                    <td className="text-right">{fmtMoney(totals.price)}</td>
                                </tr>
                            </tfoot>
                        ) : null}
                    </table>
                </div>
            </div>
        </div>
    );
};

const GpsPane = ({ ui, world, item }) => {
    const { draft, setDraft, period, apply } = usePeriod(world.nowMs);
    const [onlyLogins, setOnlyLogins] = useState(false);
    const rows = gpsRows(item, period, onlyLogins);
    return (
        <div>
            <div className="income-filters mb-2">
                <PeriodFields draft={draft} setDraft={setDraft} />
                <FilterSelect label="События" placeholder="Все события"
                    options={['Вход в Яндекс Про', 'Выход из Яндекс Про', 'Смена тарифов', 'Смена режима дохода',
                        'Смена режима перемещения', 'Свободен', 'Занят', 'На заказе', 'Офлайн']} />
                <ShowButton onClick={() => { setOnlyLogins(false); apply(); }}>
                    {' '}<button className="btn btn-sm btn-link" type="button" onClick={() => setOnlyLogins(true)}> Только вход и выход </button>
                </ShowButton>
            </div>
            <p className="small text-muted mb-3"> Диспетчерская отдаёт события не больше чем за 48 часов. </p>
            <div className="">
                <div className="dispatcher-tiles mb-3">
                    {gpsSummary(rows).map(([label, value]) => (
                        <div className="dispatcher-tile" key={label}>
                            <div className="dispatcher-tile__label">{label}</div>
                            <div className="dispatcher-tile__value"> {fmtNumber(value, 1)} км </div>
                        </div>
                    ))}
                </div>
                <div aria-label="Вид" className="btn-group btn-group-sm mb-3" role="group">
                    <button className="btn btn-primary" type="button"><i className="fa fa-list" /> Список </button>
                    <button className="btn btn-outline-primary" type="button"><i className="fa fa-map-o" /> Карта </button>
                </div>
                <div>
                    <div className="table-responsive">
                        <table className="table table-sm table-hover statement-table mb-2">
                            <thead>
                                <tr>
                                    <th>Статус</th><th>Дата и время</th><th className="text-right">Скорость</th>
                                    <th className="text-right">Время</th><th className="text-right">Пробег</th><th>Детали</th>
                                </tr>
                            </thead>
                            <tbody>
                                {rows.length ? rows.map((row, index) => (
                                    <tr key={index}>
                                        <td className="text-nowrap"><span className={`badge ${gpsBadge(row.type)}`}>{row.label}</span></td>
                                        <td className="text-nowrap">{row.when || '—'}</td>
                                        <td className="text-right text-nowrap" />
                                        <td className="text-right text-nowrap">{fmtShiftDuration(row.sec)}</td>
                                        <td className="text-right text-nowrap">{row.km === null || row.km === undefined ? '' : `${fmtNumber(row.km, 1)} км`}</td>
                                        <td className="statement-table__comment">
                                            {row.order ? (
                                                <div>
                                                    <OrderLink ui={ui} number={row.order.number}>Заказ {row.order.number}</OrderLink>
                                                    {row.order.address ? <span className="text-muted"> · {row.order.address}</span> : null}
                                                </div>
                                            ) : null}
                                            {row.details ? <div>{row.details}</div> : null}
                                        </td>
                                    </tr>
                                )) : (
                                    <tr><td className="text-center text-muted py-3" colSpan={6}> Событий за период нет. </td></tr>
                                )}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    );
};

const BonusesPane = ({ item }) => {
    const sections = [
        ['active', 'Активные цели', item.bonuses?.active || []],
        ['previous', 'Прошедшие за неделю', item.bonuses?.previous || []],
    ].filter(([, , goals]) => goals.length);
    return (
        <div>
            <div className="d-flex align-items-center mb-3">
                <span className="small text-muted"> Цели Яндекса из Диспетчерской: на сегодня и за прошедшую неделю. </span>
                {' '}
                <button className="btn btn-sm btn-outline-primary ml-auto" type="button"><i className="fa fa-refresh" /> Обновить </button>
            </div>
            {!sections.length ? <p className="text-muted">Целей на сегодня и за прошедшую неделю нет.</p> : null}
            {sections.map(([key, title, goals]) => (
                <div className="mb-3" key={key}>
                    <h6 className="mb-2">{title}</h6>
                    {goals.map((goal) => (
                        <div className="income-card bonus-goal mb-2" key={goal.id}>
                            <div className="d-flex flex-wrap align-items-baseline">
                                <div><strong>{goal.title}</strong> <span className="text-muted small ml-1">{goal.window}</span></div>
                                <div className="ml-auto text-right">
                                    {goal.earned !== null
                                        ? <span className="text-success font-weight-bold">+{Number(goal.earned).toLocaleString('ru-RU')} {goal.currency}</span>
                                        : <span className="text-muted"> до {Number(goal.max_amount).toLocaleString('ru-RU')} {goal.currency} </span>}
                                </div>
                            </div>
                            <div className="bonus-goal__steps">
                                {goal.steps.map((step) => (
                                    <span key={step.rides} className={`badge ${step.completed ? 'badge-success' : 'badge-light'}`}>
                                        +{Number(step.amount).toLocaleString('ru-RU')} {goal.currency} за {step.rides}
                                    </span>
                                ))}
                            </div>
                            <div className="small">
                                {' '}{goal.rides} из {goal.max_rides} заказов
                                <div className="progress bonus-goal__progress">
                                    <div className="progress-bar bg-success" style={{ width: `${goal.max_rides ? Math.min(100, Math.round((goal.rides / goal.max_rides) * 100)) : 0}%` }} />
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            ))}
        </div>
    );
};

const StatusesPane = ({ item, opened }) => {
    const changes = statusChanges(item);
    const rules = workRuleLog(item);
    const others = otherChanges(item);
    return (
        <div>
            <table className="table table-sm table-hover">
                <thead><tr><th>Дата и время</th><th>Что изменилось</th><th>Было</th><th>Стало</th><th>Источник</th></tr></thead>
                <tbody>
                    {changes.length ? changes.map((row, index) => (
                        <tr key={index}>
                            <td>{row.when}</td><td>{row.what}</td><td className="text-muted">{row.from}</td>
                            <td><strong>{row.to}</strong></td><td><span className="badge badge-light">Диспетчерская</span></td>
                        </tr>
                    )) : (
                        <tr>
                            <td>{opened}</td><td>Статус (онлайн/офлайн)</td><td className="text-muted">—</td>
                            <td><strong>offline</strong></td><td><span className="badge badge-light">Диспетчерская</span></td>
                        </tr>
                    )}
                    {others.map((row, index) => (
                        <tr key={`c${index}`}>
                            <td>{row.when}</td><td>{row.field}</td><td className="text-muted">{row.from}</td>
                            <td><strong>{row.to}</strong></td><td><span className="badge badge-light">{row.author}</span></td>
                        </tr>
                    ))}
                </tbody>
            </table>
            <h6 className="mt-4 mb-2">Журнал условий работы</h6>
            {rules.length ? (
                <table className="table table-sm table-hover">
                    <thead><tr><th>Дата и время</th><th>Было</th><th>Стало</th><th>Кто сменил</th></tr></thead>
                    <tbody>
                        {rules.map((row, index) => (
                            <tr key={index}><td>{row.when}</td><td className="text-muted">{row.from}</td><td><strong>{row.to}</strong></td><td>{row.by}</td></tr>
                        ))}
                    </tbody>
                </table>
            ) : <p className="text-muted mb-0">Смен условия работы пока не записано: журнал ведётся с момента его включения.</p>}
        </div>
    );
};

/* «История»: кто и когда открывал карточку. Открытие пишется — правда
   рабочей CRM; у водителя с обращениями видно и прошлых операторов. */
const HistoryPane = ({ item, opened }) => {
    const past = (item.crm?.history?.requests || []).map((request) => ({
        when: request.when, who: request.operator || 'Оператор', what: 'Просмотр карточки', details: 'Открыта карточка водителя',
    }));
    const rows = [{ when: opened, who: 'Стажёр', what: 'Просмотр карточки', details: 'Открыта карточка водителя' }, ...past];
    return (
        <div>
            <table className="table table-sm table-hover">
                <thead><tr><th>Дата и время</th><th>Оператор</th><th>Действие</th><th>Детали</th></tr></thead>
                <tbody>
                    {rows.map((row, index) => (
                        <tr key={index}><td>{row.when}</td><td>{row.who}</td><td>{row.what}</td><td className="text-muted small">{row.details}</td></tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
};

const CarField = ({ label, value, wide = false }) => (
    <div className={`car-field${wide ? ' car-field--wide' : ''}`}>
        <span className="car-field__label">{label}</span>
        <span className="car-field__value">{value || '—'}</span>
    </div>
);

/* Карта заказа. В рабочей CRM здесь настоящая карта с точками адресов. В
   тренажёре адреса учебные, а чужих карт нет (ни запросов наружу, ни тайлов),
   поэтому — схема: кварталы, проспекты, парк и река, а точки А и Б — теми же
   значками .order-map-pin, что ставит CRM. Где стоят точки, решает номер
   заказа: у разных заказов они разные, у одного и того же — всегда одни. */
const seeded = (text) => {
    let h = 2166136261;
    for (const ch of String(text)) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
    if (!h) h = 1;
    return () => {
        h ^= h << 13;
        h ^= h >>> 17;
        h ^= h << 5;
        return ((h >>> 0) % 1000) / 1000;
    };
};

const MAP_W = 600;
const MAP_H = 360;
const STREETS_X = [48, 112, 178, 246, 318, 386, 452, 522];
const STREETS_Y = [40, 104, 170, 238, 300];

const OrderMap = ({ number, from, to }) => {
    const rnd = seeded(number);
    const avenueX = STREETS_X[2 + Math.floor(rnd() * 4)];
    const avenueY = STREETS_Y[1 + Math.floor(rnd() * 3)];
    /* Парк — целый квартал между улицами, улицы его не режут. */
    const parkI = Math.floor(rnd() * (STREETS_X.length - 1));
    const parkJ = Math.floor(rnd() * (STREETS_Y.length - 1));
    const river = 300 + rnd() * 50;
    const pick = (xs, ys) => [xs[Math.floor(rnd() * xs.length)], ys[Math.floor(rnd() * ys.length)]];
    const a = pick(STREETS_X.slice(0, 3), STREETS_Y.slice(0, 4));
    const b = pick(STREETS_X.slice(4), STREETS_Y.slice(1));
    const pin = ([x, y], kind, letter, title) => (
        <span className={`order-map-pin order-map-pin--${kind}`} title={title}
            style={{ position: 'absolute', left: `${(x / MAP_W) * 100}%`, top: `${(y / MAP_H) * 100}%`, transform: 'translate(-50%, -50%)', boxShadow: '0 1px 3px rgba(0,0,0,.35)' }}>
            {letter}
        </span>
    );
    const road = { vectorEffect: 'non-scaling-stroke', fill: 'none' };
    return (
        <div className="order-card__map" style={{ position: 'relative', overflow: 'hidden' }}>
            <svg viewBox={`0 0 ${MAP_W} ${MAP_H}`} preserveAspectRatio="none" width="100%" height="100%" aria-hidden="true" style={{ display: 'block' }}>
                <rect width={MAP_W} height={MAP_H} fill="#f2efe9" />
                <rect x={STREETS_X[parkI] + 6} y={STREETS_Y[parkJ] + 6} rx={4} fill="#d6e9cf"
                    width={STREETS_X[parkI + 1] - STREETS_X[parkI] - 12} height={STREETS_Y[parkJ + 1] - STREETS_Y[parkJ] - 12} />
                <path d={`M -10 ${river} C 150 ${river - 40}, 260 ${river + 30}, 380 ${river - 10} S 560 ${river - 60}, 620 ${river - 30}`}
                    stroke="#c3dbef" strokeWidth={16} style={road} />
                {STREETS_X.map((x) => <line key={`cx${x}`} x1={x} y1={0} x2={x} y2={MAP_H} stroke="#e0dad0" strokeWidth={8} style={road} />)}
                {STREETS_Y.map((y) => <line key={`cy${y}`} x1={0} y1={y} x2={MAP_W} y2={y} stroke="#e0dad0" strokeWidth={8} style={road} />)}
                {STREETS_X.map((x) => <line key={`x${x}`} x1={x} y1={0} x2={x} y2={MAP_H} stroke="#fff" strokeWidth={6} style={road} />)}
                {STREETS_Y.map((y) => <line key={`y${y}`} x1={0} y1={y} x2={MAP_W} y2={y} stroke="#fff" strokeWidth={6} style={road} />)}
                <line x1={avenueX} y1={0} x2={avenueX} y2={MAP_H} stroke="#e6c88c" strokeWidth={11} style={road} />
                <line x1={0} y1={avenueY} x2={MAP_W} y2={avenueY} stroke="#e6c88c" strokeWidth={11} style={road} />
                <line x1={avenueX} y1={0} x2={avenueX} y2={MAP_H} stroke="#fbe3a6" strokeWidth={9} style={road} />
                <line x1={0} y1={avenueY} x2={MAP_W} y2={avenueY} stroke="#fbe3a6" strokeWidth={9} style={road} />
            </svg>
            {from ? pin(a, 'from', 'А', from) : null}
            {to ? pin(b, 'to', 'Б', to) : null}
            <span className="small text-muted" style={{ position: 'absolute', right: 8, bottom: 4, background: 'rgba(255,255,255,.75)', padding: '0 4px', borderRadius: 3 }}>
                {' '}Учебная схема{' '}
            </span>
        </div>
    );
};

/** Окно «Заказ N» — карточка заказа CRM. */
const OrderCardModal = ({ ui, item, number }) => {
    const data = orderCard(item, number);
    const close = () => ui.touch('drv_order_close', { drvModal: null, drvOrder: null });
    if (!data) return null;
    const { order, money } = data;
    const status = orderStatus(order.status);
    return (
        <VModal name="order-card" width={1040} pivotY={0.05} scrollable onClose={close}>
            <div className="p-4 order-card">
                <div className="d-flex align-items-start mb-2">
                    <h5 className="modal-title mb-0">
                        {' '}Заказ {order.number}{' '}
                        <span className={`badge ml-1 ${status.tone}`}>{status.label}</span>
                    </h5>
                    <button aria-label="Закрыть" className={`close ml-auto${ui.t('drv_order_close')}`} type="button" onClick={close}>×</button>
                </div>
                <p className="small text-muted mb-3">
                    {' '}{[order.driver, order.car, order.work_rule].filter(Boolean).join(' · ')}
                    {order.booked_at ? <span> · подача {order.booked_at}</span> : null}
                </p>
                <div className="row">
                    <div className="col-md-7 mb-3">
                        <OrderMap number={order.number}
                            from={order.segments[0]?.from.address} to={order.segments[0]?.to[0]?.address} />
                        <div className="order-card__legend small text-muted mt-1">
                            <span>Трека у заказа нет — на карте только адреса.</span>
                        </div>
                    </div>
                    <div className="col-md-5 mb-3">
                        <div className="detail-row"><span className="detail-row__label">ID заказа</span> <span className="detail-row__value order-card__id">{order.id}</span></div>
                        {order.cancelled_by ? <Detail label="Кто отменил" value={order.cancelled_by} /> : null}
                        {order.cancel_reason ? <Detail label="Причина отмены" value={order.cancel_reason} text /> : null}
                        {money.driver_income ? <Detail label="Доход водителя" value={money.driver_income} /> : null}
                        <Detail label="Стоимость" value={order.price === null ? '—' : `${fmtMoney(order.price)} ₸`} />
                        {money.payment_method ? <Detail label="Оплата" value={money.payment_method} /> : null}
                        <Detail label="Тариф" value={money.tariff || order.category || '—'} />
                        <Detail label="Расстояние" value={order.mileage_km === null ? '—' : `${fmtMoney(order.mileage_km)} км`} />
                        <Detail label="Длительность поездки" value={order.trip_duration || '—'} />
                        <h6 className="mt-3 mb-1">Выполнение</h6>
                        {order.stages.map((stage) => (
                            <div className="detail-row" key={stage.label}>
                                <span className="detail-row__label">
                                    {' '}{stage.label}{' '}
                                    {stage.after ? <span className="text-muted">+{stage.after}</span> : null}
                                </span>
                                {' '}
                                <span className="detail-row__value">{stage.at}</span>
                            </div>
                        ))}
                        {!order.stages.length ? <p className="small text-muted mb-0">Этапов нет.</p> : null}
                    </div>
                </div>
                <h6 className="mb-1">Маршрут</h6>
                {order.segments.map((segment, index) => (
                    <div className="order-card__segment" key={index}>
                        {segment.from.address ? <div><span className="order-card__pin order-card__pin--from">А</span> {segment.from.address}</div> : null}
                        {segment.to.map((point, pointIndex) => (point.address ? (
                            <div key={pointIndex}><span className="order-card__pin order-card__pin--to">Б</span> {point.address}</div>
                        ) : null))}
                    </div>
                ))}
                {money.blocks.length ? <h6 className="mt-3 mb-1">Оплата и комиссии</h6> : null}
                {money.blocks.map((block, blockIndex) => (
                    <div className="order-card__money" key={`b${blockIndex}`}>
                        <div className="detail-row font-weight-bold"><span>{block.name}</span> <span>{block.value}</span></div>
                        {block.groups.map((group, groupIndex) => (
                            <div className="pl-3" key={`g${groupIndex}`}>
                                {group.name && group.name !== block.name ? (
                                    <div className="detail-row"><span className="detail-row__label">{group.name}</span> <span className="detail-row__value">{group.value}</span></div>
                                ) : null}
                                {group.items.map((entry, entryIndex) => (
                                    <div className="detail-row pl-3" key={`i${entryIndex}`}>
                                        <span className="detail-row__label">
                                            {' '}{entry.name}{' '}
                                            {entry.description ? <span className="d-block small">{entry.description}</span> : null}
                                        </span>
                                        {' '}
                                        <span className="detail-row__value font-weight-normal">{entry.value}</span>
                                    </div>
                                ))}
                            </div>
                        ))}
                    </div>
                ))}
                {money.transactions.length ? (
                    <h6 className="mt-3 mb-1">
                        {' '}Операции по заказу{' '}
                        {money.balance_change !== null ? (
                            <small className="text-muted"> — баланс {money.balance_change > 0 ? '+' : ''}{fmtMoney(money.balance_change)} ₸ </small>
                        ) : null}
                    </h6>
                ) : null}
                {money.transactions.map((group, groupIndex) => (
                    <div className="mb-2" key={`t${groupIndex}`}>
                        <div className="small font-weight-bold">{group.name}</div>
                        {group.items.map((entry, entryIndex) => (
                            <div className="detail-row" key={entryIndex}>
                                <span className="detail-row__label">{entry.description} <span className="text-muted">{entry.at}</span></span>
                                {' '}
                                <span className={`detail-row__value${entry.amount < 0 ? ' text-danger' : ''}`}> {fmtMoney(entry.amount)} </span>
                            </div>
                        ))}
                    </div>
                ))}
            </div>
        </VModal>
    );
};

/** Карточка водителя. */
export const AccountCard = ({ ui }) => {
    const { world } = ui;
    const item = caseByAccount(world, world.drvOpenId);
    if (!item) return null;
    const index = Math.max(0, (world.crmCases || []).indexOf(item));
    const account = crmAccount(item, index);
    const tab = world.drvTab || 'general';
    const refreshedAt = (world.drvRefreshed || {})[account.accountId] || null;
    const refreshed = Boolean(refreshedAt);
    const opened = world.drvOpenedAt || world.now.slice(0, 16);
    const modal = world.drvModal;
    const photo = (item.photo_days || [])[0] || null;
    const orders = item.orders || [];
    const yaPark = yaParkFor(account.parkName, account.parkCity);
    const need = (value) => (refreshed ? value : 'Нет данных');
    const close = (id) => ui.touch(id, { drvModal: null });

    return (
        <div className="container-fluid driver-card">
            <nav aria-label="breadcrumb">
                <ol className="breadcrumb">
                    <li className="breadcrumb-item"><a href="#" onClick={(e) => { noNav(e); ui.crumb('home'); }}>Главная</a></li>
                    <li className="breadcrumb-item">
                        <a href="#" className={ui.t('crumb_accounts').trim() || undefined}
                            onClick={(e) => { noNav(e); ui.go('accounts', { drvModal: null }, 'crumb_accounts'); }}>Учетные записи водителей</a>
                    </li>
                    <li className="breadcrumb-item active" aria-current="page">{account.name}</li>
                </ol>
            </nav>
            <div className="row">
                <div className="col-lg-8">
                    <div className="card">
                        <div className="card-header d-flex align-items-center flex-wrap">
                            <div className="driver-photo mr-2"><span className="driver-photo__initials"> {account.initials} </span> </div>
                            {' '}
                            <div><strong>{account.name}</strong> <span className="text-muted small ml-2">{account.accountId}</span></div>
                            {' '}
                            <div className="ml-auto">
                                <a className={`btn btn-sm btn-outline-secondary${ui.t('drv_to_fleet')}`} href="#" rel="noopener" target="_blank"
                                    onClick={(e) => { noNav(e); ui.act('drv_to_fleet', { crmNote: FLEET_NOTE }); }}>
                                    <i className="fa fa-external-link" /> В Диспетчерской{' '}
                                </a>
                                {' '}
                                <a className={`btn btn-sm btn-outline-warning${ui.t('drv_to_yandex')}`} href="#" rel="noopener" target="_blank"
                                    title={`Обращение заводится по парку ${account.park}`}
                                    onClick={(e) => { noNav(e); ui.go('ya_new', { yaForm: emptyYaForm(yaPark), drvModal: null }, 'drv_to_yandex', { accountId: account.accountId }); }}>
                                    <i className="fa fa-life-ring" /> Написать в поддержку Яндекса{' '}
                                </a>
                            </div>
                        </div>
                        <div className="card-body">
                            <div className="row mb-3">
                                <div className="col-md-4">
                                    <Detail label="Телефон" value={account.phoneCrm} />
                                    {' '}
                                    <Detail label="Парк" value={account.park} />
                                </div>
                                {' '}
                                <div className="col-md-4">
                                    <Detail label="Профессия" value={account.profession} />
                                    {' '}
                                    <Detail label="Тип сотрудничества" value={account.employment} />
                                    {' '}
                                    <div className="detail-row">
                                        <span className="detail-row__label">Условия работы</span>{' '}
                                        <span className="detail-row__value">
                                            <span>{account.termsCrm}</span>{' '}
                                            <button className={`btn btn-sm btn-outline-secondary py-0 px-1 ml-1${ui.t('drv_workrule')}`} title="Условия работы" type="button"
                                                onClick={() => ui.touch('drv_workrule', { drvModal: 'work-rule' })}>
                                                <i className="fa fa-cog" />
                                            </button>
                                        </span>
                                    </div>
                                </div>
                                {' '}
                                <div className="col-md-4">
                                    <div className="detail-row">
                                        <span className="detail-row__label">Статус в CRM</span>{' '}
                                        <span className="detail-row__value">
                                            <span className={`badge ${account.workBadge}`}>{account.workStatus}</span>
                                        </span>
                                    </div>
                                    {' '}
                                    <Detail label="Дата создания" value={account.created} />
                                </div>
                            </div>

                            <ul className="nav nav-tabs mb-3">
                                {TABS.map(([key, title]) => (
                                    <li className="nav-item" key={key}>
                                        <a className={`nav-link${tab === key ? ' active' : ''}${ui.t(`drv_tab_${key}`)}`} href="#"
                                            onClick={(e) => {
                                                noNav(e);
                                                ui.pick(`drv_tab_${key}`, { drvTab: key }, { tab: key, accountId: account.accountId });
                                                if (ui.emit) ui.emit('crm.driver_tab', { tab: key });
                                            }}>
                                            {key === 'statuses' || key === 'history'
                                                ? <> {title} <span className="badge badge-pill badge-secondary">
                                                    {key === 'history' ? 1 + (item.crm?.history?.requests || []).length
                                                        : Math.max(1, statusChanges(item).length + otherChanges(item).length)}
                                                </span></>
                                                : title}
                                        </a>
                                    </li>
                                ))}
                            </ul>

                            {tab === 'general' ? (
                                <div>
                                    <div className="d-flex align-items-center flex-wrap mb-2">
                                        <h6 className="mb-0 mr-3">Данные из Диспетчерской</h6>
                                        {' '}
                                        <div className="ml-auto d-flex align-items-center">
                                            <small className="text-muted mr-2 text-right">
                                                {' '}Обновлено из Диспетчерской: {refreshedAt || opened}
                                                {refreshed ? null : (
                                                    <>
                                                        {' '}<br />{' '}
                                                        <span className="text-warning">
                                                            Рейтинг, диагностика, приоритет, термокороб, фотоконтроль, устройство, личные,
                                                            паспортные и банковские данные — нажмите «Обновить»
                                                        </span>
                                                    </>
                                                )}
                                            </small>
                                            {' '}
                                            <button className={`btn btn-sm btn-outline-primary${ui.t('drv_refresh')}`} type="button"
                                                onClick={() => ui.act('drv_refresh', {
                                                    drvRefreshed: { ...(world.drvRefreshed || {}), [account.accountId]: world.now.slice(0, 16) },
                                                }, { accountId: account.accountId })}>
                                                <i className="fa fa-refresh" /> Обновить{' '}
                                            </button>
                                        </div>
                                    </div>
                                    {' '}
                                    <div className="dispatcher-tiles mb-4">
                                        <Tile icon="fa-user" label="Работает" value={account.working ? 'Да' : 'Нет'} tone={account.working ? 'text-success' : ''} />
                                        <Tile icon="fa-wifi" label="Статус" value={account.online} />
                                        <Tile icon="fa-credit-card" label="Состояние счёта" value={account.balanceCrm}
                                            tone={account.balanceValue !== null && account.limitValue !== null && account.balanceValue < account.limitValue ? 'text-danger' : ''}
                                            extra={`К выводу в приложении: ${account.withdrawable}`}
                                            extraTitle={'Баланс минус то, что приложение удерживает при выводе:\nнеснижаемый остаток — 100 ₸\n'
                                                + `Комиссия, минимальная сумма, суточный лимит и запреты вывода не учтены.\nДанные Яндекса на ${opened}`} />
                                        <Tile icon="fa-star" label="Рейтинг" value={need(account.rating)} muted={!refreshed} />
                                        <Tile icon="fa-stethoscope" label="Диагностика" value={need(account.diagnostics)} muted={!refreshed}
                                            tone={refreshed && account.diagnostics !== 'Всё в порядке' ? 'text-danger' : ''} />
                                        <Tile icon="fa-flag" label="Приоритет" value={need(account.priority)} muted={!refreshed} />
                                        <Tile icon="fa-cube" label="Термокороб" value={need(account.thermobox)} muted={!refreshed} />
                                        <Tile icon="fa-camera" label="Фотоконтроль" muted={!refreshed}
                                            value={need(photo ? (photo.ok ? 'Пройден' : 'Не пройден') : 'Нет данных')}
                                            tone={refreshed && photo ? (photo.ok ? 'text-success' : 'text-danger') : ''} />
                                    </div>
                                    {' '}
                                    <div className="row">
                                        <div className="col-md-4">
                                            <h6><i className="fa fa-car" /> Автомобиль</h6>
                                            <Detail label="Марка / модель" value={account.car.title} />
                                            <Detail label="Гос. номер" value={account.car.plate} />
                                            <Detail label="Год выпуска" value={account.car.year} />
                                            <Detail label="Цвет" value={account.car.color} />
                                            <Detail label="Тарифы" value={account.car.tariffs} />
                                            {' '}
                                            <button className={`btn btn-sm btn-link px-0${ui.t('drv_car_more')}`} type="button"
                                                onClick={() => ui.touch('drv_car_more', { drvModal: 'car-details' })}>Подробнее</button>
                                            {' '}
                                            <button className={`btn btn-sm btn-link px-0 ml-2${ui.t('drv_car_bind')}`} type="button"
                                                onClick={() => stub(ui, 'drv_car_bind', 'car_binding')}>
                                                <i className="fa fa-link" /> Сменить или отвязать{' '}
                                            </button>
                                        </div>
                                        {' '}
                                        <div className="col-md-4">
                                            <h6><i className="fa fa-address-book" /> Контакты</h6>
                                            <Detail label="Номер телефона" value={account.phoneCrm} />
                                            <Detail label="Номер В/У" value={account.license} />
                                            <Detail label="ИИН" value={account.iin} />
                                            <div className="detail-row detail-row--action">
                                                <a className={`small${ui.t('drv_check')}`} href="#"
                                                    onClick={(e) => { noNav(e); stub(ui, 'drv_check', 'check_driver'); }}>
                                                    <i className="fa fa-search" /> Проверить водителя{' '}
                                                </a>
                                            </div>
                                            <Detail label="Позывной" value={account.callsign} />
                                            <Detail label="Email" value="—" />
                                        </div>
                                        {' '}
                                        <div className="col-md-4">
                                            <h6><i className="fa fa-info-circle" /> Дополнительно</h6>
                                            <Detail label="Лимит по счёту" value={account.limit} />
                                            <Detail label="Провайдер ЭДО" value={account.edo} />
                                            <Detail label="Дата приёма" value={account.hired} />
                                            <Detail label="Дата увольнения" value={account.fired} />
                                            <Detail label="Последняя транзакция" value={account.lastTransaction} />
                                            <Detail label="Последнее изменение статуса" value={statusChanges(item)[0]?.when || '—'} />
                                            <Detail label="Модель устройства" value="—" />
                                            <Detail label="Версия Яндекс Про" value="—" />
                                        </div>
                                    </div>
                                    {' '}
                                    <h6 className="mt-3"><i className="fa fa-id-card-o" /> Личные данные</h6>
                                    <div className="row">
                                        <div className="col-md-4"><Detail label="Дата рождения" /><Detail label="Доверенный контакт" /></div>
                                        <div className="col-md-4"><Detail label="Email в Диспетчерской" text /><Detail label="ID для платежа" /></div>
                                        <div className="col-md-4"><Detail label="Адрес" text /><Detail label="Отзыв о водителе" text /></div>
                                    </div>
                                    {' '}
                                    <h6 className="mt-3"><i className="fa fa-address-card-o" /> Паспортные данные</h6>
                                    <div className="row">
                                        <div className="col-md-4">
                                            <Detail label="Статус проверки паспорта" /><Detail label="Вид паспорта" />
                                            <Detail label="Страна" /><Detail label="Номер и серия" />
                                        </div>
                                        <div className="col-md-4"><Detail label="Кем выдан" text /><Detail label="Дата выдачи" /><Detail label="Действует до" /></div>
                                        <div className="col-md-4"><Detail label="Адрес регистрации" text /><Detail label="Почтовый индекс" /><Detail label="ОГРН" /></div>
                                    </div>
                                    {' '}
                                    <h6 className="mt-3"><i className="fa fa-university" /> Банковские реквизиты</h6>
                                    <div className="row">
                                        <div className="col-md-4"><Detail label="БИК" /></div>
                                        <div className="col-md-4"><Detail label="Расчётный счёт" /></div>
                                        <div className="col-md-4"><Detail label="Корреспондентский счёт" /></div>
                                    </div>
                                    {' '}
                                    <hr />
                                    {' '}
                                    <h6><i className="fa fa-list" /> Заказы</h6>
                                    <div className="row text-center">
                                        <div className="col"><div className="h4 mb-0">{orders.filter((o) => o.day === isoDay(world.nowMs)).length}</div> <small className="text-muted">за сегодня</small></div>
                                        <div className="col"><div className="h4 mb-0">{orders.length}</div> <small className="text-muted">за 7 дней</small></div>
                                        <div className="col"><div className="h4 mb-0">{orders.length}</div> <small className="text-muted">за 30 дней</small></div>
                                        <div className="col"><div className="h4 mb-0">{orders.length}</div> <small className="text-muted">всего</small></div>
                                        <div className="col"><div className="h6 mb-0">{orders[0]?.when || '—'}</div> <small className="text-muted">последний заказ</small></div>
                                    </div>
                                </div>
                            ) : null}
                            {tab === 'income' ? <IncomePane world={world} item={item} /> : null}
                            {tab === 'statement' ? <StatementPane ui={ui} world={world} item={item} /> : null}
                            {tab === 'orders' ? <OrdersPane ui={ui} world={world} item={item} /> : null}
                            {tab === 'gps' ? <GpsPane ui={ui} world={world} item={item} /> : null}
                            {tab === 'bonuses' ? <BonusesPane item={item} /> : null}
                            {tab === 'statuses' ? <StatusesPane item={item} opened={opened} /> : null}
                            {tab === 'history' ? <HistoryPane item={item} opened={opened} /> : null}
                            {tab === 'promotions' ? (
                                <div>
                                    <h6 className="text-muted text-uppercase small font-weight-bold mb-2">Байга</h6>
                                    <div style={{ maxWidth: 460 }}><p className="text-muted small">Водитель не участвует в розыгрышах этой недели.</p></div>
                                </div>
                            ) : null}
                        </div>
                    </div>
                </div>
                <div className="col-lg-4">
                    <div className="card">
                        <div className="card-header"><i className="fa fa-camera" /> Фотоконтроль </div>
                        <div className="card-body">
                            <div className="detail-row">
                                <span className="detail-row__label">Статус</span>{' '}
                                <span className="detail-row__value">
                                    {refreshed && photo
                                        ? <span className={photo.ok ? 'text-success' : 'text-danger'}>{photo.ok ? 'Пройден' : 'Не пройден'}</span>
                                        : <span className="text-muted">Нет данных</span>}
                                </span>
                            </div>
                            <Detail label="Дата последнего фотоконтроля" value={refreshed && photo ? photo.when : '—'} />
                            {refreshed && photo && !photo.ok && photo.reason ? <Detail label="Причина" value={photo.reason} text /> : null}
                            <h6 className="mt-3">Фото автомобиля</h6>
                            {refreshed && photo ? (
                                <div className="row no-gutters">
                                    {photo.shots.map((shot) => (
                                        <div className="col-6 p-1" key={shot}>
                                            <div className="photo-control-empty">{shot}</div>
                                        </div>
                                    ))}
                                </div>
                            ) : (
                                <div className="photo-control-empty">
                                    {refreshed ? ' Фотоконтроль у водителя не проводился. ' : ' Фото ещё не получены. Нажмите «Обновить» в блоке данных Диспетчерской. '}
                                </div>
                            )}
                            {refreshed && (item.photo_days || []).length > 1 ? (
                                <>
                                    <h6 className="mt-3">История</h6>
                                    {(item.photo_days || []).map((day) => (
                                        <div className="photo-history-item d-flex" key={day.when}>
                                            <span>{day.when}</span>
                                            <span className={`ml-auto ${day.ok ? 'text-success' : 'text-danger'}`}>{day.ok ? 'Пройден' : 'Не пройден'}</span>
                                        </div>
                                    ))}
                                </>
                            ) : null}
                        </div>
                    </div>
                </div>
            </div>

            {modal === 'order-card' && world.drvOrder ? <OrderCardModal ui={ui} item={item} number={world.drvOrder} /> : null}

            {modal === 'car-details' ? (
                <VModal name="car-details" width={860} pivotY={0.1} onClose={() => close('drv_car_back')}>
                    <div className="p-4">
                        <div className="d-flex align-items-start mb-3">
                            <h5 className="modal-title mb-0">Автомобиль водителя: {account.name}</h5>
                            {' '}
                            <button aria-label="Закрыть" className="close ml-auto" type="button" onClick={() => close('drv_car_back')}>×</button>
                        </div>
                        {account.hasCar ? (
                            <>
                                <h6 className="font-weight-bold">Детали автомобиля</h6>
                                <div className="row">
                                    <div className="col-md-6">
                                        <CarField label="Статус" value={account.car.status} />
                                        <CarField label="Марка" value={account.car.brand} />
                                        <CarField label="Модель" value={account.car.model} />
                                        <CarField label="Цвет" value={account.car.color} />
                                        <CarField label="Год" value={account.car.year} />
                                        <CarField label="Владелец" value={account.car.owner} />
                                    </div>
                                    <div className="col-md-6">
                                        <CarField label="Гос. номер" value={account.car.plate} />
                                        <CarField label="VIN" value={account.car.vin} />
                                        <CarField label="Номер кузова" value={account.car.body} />
                                        <CarField label="СТС" value={account.car.sts} />
                                        <CarField label="Позывной" value={account.callsign} />
                                    </div>
                                </div>
                                <hr />
                                <div className="row">
                                    <div className="col-md-6">
                                        <h6 className="font-weight-bold">Комплектация и брендинг</h6>
                                        <CarField label="КПП" value={account.car.transmission} />
                                        <CarField label="Оклейка" value="Нет" />
                                        <CarField label="Lightbox" value="Нет" />
                                    </div>
                                    <div className="col-md-6">
                                        <h6 className="font-weight-bold">Параметры и тарифы</h6>
                                        <CarField label="Вид топлива" value={account.car.fuel} />
                                        <CarField label="Тариф / Класс" value={account.car.tariffs} wide />
                                    </div>
                                </div>
                            </>
                        ) : <div className="alert alert-secondary">У водителя нет привязанного автомобиля.</div>}
                        <div className="d-flex align-items-center flex-wrap mt-3">
                            <button className={`btn btn-outline-primary${ui.t('drv_car_back')}`} type="button"
                                onClick={() => close('drv_car_back')}>
                                <i className="fa fa-arrow-left" /> Вернуться в карточку водителя{' '}
                            </button>
                            {' '}
                            <a className="btn btn-link ml-2" href="#"
                                onClick={(e) => { noNav(e); stub(ui, 'drv_car', 'car_editor'); }}>
                                {' '}Открыть полный редактор{' '}
                            </a>
                        </div>
                    </div>
                </VModal>
            ) : null}

            {modal === 'work-rule' ? (
                <VModal name="work-rule" width={520} pivotY={0.2} onClose={() => close('drv_workrule_close')}>
                    <div className="p-4">
                        <h5 className="modal-title mb-3">Условия работы</h5>
                        <CarField label="Название" value={account.terms} />
                        <CarField label="Комиссия парка по умолчанию" value={account.parkCommission} />
                        <div className="d-flex justify-content-end mt-3">
                            <button className={`btn btn-link text-muted${ui.t('drv_workrule_close')}`} type="button"
                                onClick={() => close('drv_workrule_close')}>Закрыть</button>
                        </div>
                    </div>
                </VModal>
            ) : null}
        </div>
    );
};
