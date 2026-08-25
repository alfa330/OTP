import React from 'react';

import { TrainMark } from './screenKit';

/* Учебный Oktell (клиент называется Okapp) — третья вкладка окна оператора.
 *
 * ЗАЧЕМ. Через Oktell к оператору приходит сам звонок: он встаёт на линию,
 * принимает вызов, ставит «Перезвон», переводит коллеге по внутреннему номеру и
 * потом ищет разговор в журнале. Без этого окна рабочее место неполное — CRM и
 * Диспетчерская отвечают на «что записать» и «что посмотреть», но не на
 * «как принять звонок».
 *
 * ГЛАВНОЕ, ЧТО ОБЯЗАН ПОКАЗАТЬ КЛОН: вход в клиент НЕ ставит в очередь. После
 * логина «Кабинет» показывает «В call-центре 0:00:00» и зелёную кнопку
 * «Войти в call-центр» — это отдельное осознанное действие. Новичок логинится,
 * уходит пить чай и не понимает, почему звонков нет.
 *
 * УРОКА ЗДЕСЬ НЕТ: ни шагов, ни ловушек, ходить можно куда угодно.
 *
 * ЧТО СКОПИРОВАНО. Раскладка, названия разделов, состав «Показателей», виджет
 * телефона, справочник по отделам, журнал с календарём. Данных коллег и номеров
 * нет — справочник придуман целиком.
 *
 * Оговорка по цветам: тёмный сайдбар #272d32, фон #ebedf0, блоки #dddfe0,
 * заголовки #666b70, тост #fef1ba — сняты пипеткой с кадров. Зелёная кнопка
 * «Войти в call-центр» на кадре ЗАМАЗАНА вместе с подписью, поэтому её оттенок
 * подобран по эпохе интерфейса, а не снят. Если появится незамазанный кадр —
 * поправить здесь.
 */

const NavIcon = ({ name }) => {
    const d = {
        phone: 'M6.5 3.5 9 8l-2 2a12 12 0 0 0 5 5l2-2 4.5 2.5-1 3a2 2 0 0 1-2 1.4C7.7 19.4 4.6 16.3 3.1 6.5A2 2 0 0 1 4.5 4.5Z',
        cabinet: 'M4 13a8 8 0 0 1 16 0v4a3 3 0 0 1-3 3h-1M4 13v3a2 2 0 0 0 2 2h1v-6H6a2 2 0 0 0-2 2Zm16 0v3a2 2 0 0 1-2 2h-1v-6h1a2 2 0 0 1 2 2Z',
        messages: 'M4 5h16v11H9l-5 4Z',
        journal: 'M7 4h11a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H7Zm0 0v16M4 8h3M4 12h3M4 16h3',
    }[name];
    return (
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
            strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d={d} />
        </svg>
    );
};

const SIDE = [
    ['phone', 'Телефон'],
    ['cabinet', 'Кабинет'],
    ['messages', 'Сообщения'],
    ['journal', 'Журнал'],
];

/* Справочник коллег. ВЫМЫШЛЕН целиком: на кадре он замазан по белому списку,
   и восстанавливать чужие имена с внутренними номерами нельзя и незачем. */
export const DIRECTORY = [
    ['СЗоВ', [
        ['Алиев Дамир', '1042', 'online'],
        ['Ким Ольга', '1043', 'busy'],
        ['Сатпаева Асель', '1044', 'online'],
        ['Ермеков Нурлан', '1051', 'offline'],
    ]],
    ['Старшие смены', [
        ['Оспанов Тимур · СВ', '1101', 'online'],
        ['Ибрагимова Динара · СВ', '1102', 'offline'],
    ]],
    ['Без отдела', [
        ['admin', '1000', 'online'],
        ['test_ivr', '1999', 'offline'],
    ]],
];

/* «Показатели» в кабинете. Подписи на кадре замазаны, кроме «В call-центре»;
   остальные названия взяты из описания в плане (в разговоре, ожидание, доля,
   счётчики) — с оговоркой, что это реконструкция, а не снимок. */
const METRICS = [
    ['В разговоре', '0:00:00', 'Последний', '—'],
    ['Ожидание', '0:00:00', 'Доля', '0%'],
    ['Постобработка', '0:00:00', 'Доля', '0%'],
    ['Принято', '0', 'Пропущено', '0'],
];

/* Справочник перерывов подтверждён протоколом живого клиента:
   1 Тех.причина, 2 Перезвон, 3 Тренинг, 4 Перерыв. */
const BREAKS = ['Перезвон', 'Перерыв', 'Тренинг', 'Тех.причина'];

const JOURNAL = [
    ['Сегодня, ср', [
        ['10:54', 'in', '+7 701 555 01 42', '0:04:12', 'ok'],
        ['10:49', 'in', '+7 705 000 00 11', '0:01:38', 'ok'],
        ['10:49', 'out', '+7 702 000 00 22', '0:00:24', 'warn'],
        ['10:48', 'in', '+7 707 000 00 33', '—', 'miss'],
    ]],
    ['Вчера, вт', [
        ['17:44', 'in', '+7 708 000 00 44', '0:02:05', 'ok'],
        ['17:44', 'out', '+7 700 000 00 55', '0:00:48', 'ok'],
    ]],
    ['21 августа, пн', [
        ['07:07', 'out', '+7 701 555 01 42', '0:03:19', 'ok'],
        ['03:29', 'in', '+7 705 000 00 11', '—', 'miss'],
        ['03:19', 'in', '+7 702 000 00 22', '0:01:02', 'ok'],
    ]],
];

const Dot = ({ tone }) => <i className={`wt-ok__dot is-${tone}`} aria-hidden="true" />;

/** Каркас клиента: тёмная панель слева, содержимое справа, виджет телефона внизу. */
const Shell = ({ world, go, emit, children }) => (
    <div className="wt-ok">
        <TrainMark>Учебная среда</TrainMark>

        <aside className="wt-ok__side">
            <div className="wt-ok__logo">
                <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
                    <path d="M3 20 12 4l9 16Z" fill="none" stroke="currentColor" strokeWidth="1.6"
                        strokeLinejoin="round" />
                </svg>
                Okapp
            </div>
            {SIDE.map(([view, label]) => (
                <button
                    key={view}
                    type="button"
                    className={`wt-ok__nav${world.oktView === view ? ' is-on' : ''}`}
                    onClick={() => go({ oktView: view })}
                >
                    <NavIcon name={view} />
                    {label}
                </button>
            ))}

            {/* Виджет телефона внизу слева — как в клиенте. */}
            <div className="wt-ok__phonebar">
                <span className="wt-ok__ava" aria-hidden="true" />
                <button type="button" className="wt-ok__phonebtn"
                    onClick={() => go({ oktPhoneMenu: !world.oktPhoneMenu })}>
                    <NavIcon name="phone" />
                    <b>›</b>
                </button>
            </div>

            {world.oktPhoneMenu ? (
                <div className="wt-ok__phonemenu">
                    <div className="wt-ok__phonemenu-head">Выбор телефона</div>
                    <button type="button" className="is-on"><NavIcon name="phone" /> Офисная телефония</button>
                    <button type="button">Без телефона</button>
                    <button type="button" onClick={() => go({ oktLogged: false, oktView: 'login', oktPhoneMenu: false, oktIn: false, oktStatus: null })}>
                        Выйти
                    </button>
                    <label><i aria-hidden="true" /> Веб-телефон — звонки из браузера через гарнитуру</label>
                    <label><i className="is-checked" aria-hidden="true">✓</i> Автоответ — автоматически отвечать на звонки</label>
                </div>
            ) : null}
        </aside>

        <div className="wt-ok__body">{children}</div>

        {/* Тост стоит на всех экранах клиента — у учётки без телефона он висит
            постоянно, и именно так его видит стажёр. */}
        <div className="wt-ok__toast">
            <b>Веб-телефон отключен</b>
            <span>Not supported</span>
        </div>
    </div>
);

/* ── Экраны ──────────────────────────────────────────────────────────────── */

const Login = ({ go, emit }) => (
    <div className="wt-ok wt-ok--login">
        <TrainMark>Учебная среда</TrainMark>
        <form
            className="wt-ok__login"
            onSubmit={(event) => {
                event.preventDefault();
                /* Пускаем с любыми данными: пароль здесь ничему не учит, а
                   заставлять угадывать учебный логин — терять время урока. */
                emit('okt.login');
                go({ oktLogged: true, oktView: 'cabinet' });
            }}
        >
            <div className="wt-ok__login-logo">
                <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
                    <path d="M3 20 12 4l9 16Z" fill="none" stroke="currentColor" strokeWidth="1.6"
                        strokeLinejoin="round" />
                </svg>
                oktell
            </div>
            <label>Логин<input type="text" defaultValue="стажёр" aria-label="Логин" /></label>
            <label>Пароль<input type="password" defaultValue="демо" aria-label="Пароль" /></label>
            <label className="wt-ok__login-save"><i aria-hidden="true">✓</i> Сохранить пароль</label>
            <button type="submit" className="wt-ok__login-btn">Войти</button>
        </form>
    </div>
);

const Cabinet = ({ world, go, emit }) => (
    <Shell world={world} go={go} emit={emit}>
        <div className="wt-ok__cols">
            <section className="wt-ok__main">
                <header className="wt-ok__head">
                    <h2>Показатели</h2>
                    <button type="button" className="wt-ok__refresh">⟳ Обновить</button>
                </header>

                <div className="wt-ok__block is-first">
                    <span>В call-центре</span>
                    <b>{world.oktIn ? '0:00:40' : '0:00:00'}</b>
                    {world.oktIn ? (
                        <button type="button" className="wt-ok__leave"
                            onClick={() => { emit('okt.callcenter_out'); go({ oktIn: false, oktStatus: null }); }}>
                            Выйти из call-центра
                        </button>
                    ) : (
                        <button type="button" className="wt-ok__enter"
                            onClick={() => { emit('okt.callcenter_in'); go({ oktIn: true }); }}>
                            Войти в call-центр
                        </button>
                    )}
                </div>

                {/* Остальные показатели появляются только внутри call-центра —
                    так же, как в клиенте. */}
                {world.oktIn ? METRICS.map(([l1, v1, l2, v2]) => (
                    <div key={l1} className="wt-ok__block">
                        <span>{l1}</span><b>{v1}</b>
                        <span className="is-right">{l2}</span><b className="is-right">{v2}</b>
                    </div>
                )) : null}

                {/* Блок статусов. В снятом кадре он скрыт (у той учётки нет
                    телефона), но у живого стажёра он есть — плановая оговорка. */}
                {world.oktIn ? (
                    <div className="wt-ok__statuses">
                        <span>Статус:</span>
                        {BREAKS.map((name) => (
                            <button
                                key={name}
                                type="button"
                                className={world.oktStatus === name ? 'is-on' : ''}
                                onClick={() => {
                                    const next = world.oktStatus === name ? null : name;
                                    emit('okt.status', { reason: next });
                                    go({ oktStatus: next });
                                }}
                            >
                                {name}
                            </button>
                        ))}
                        {world.oktStatus ? (
                            <em>сейчас «{world.oktStatus}» — звонки не приходят</em>
                        ) : (
                            <em>на линии, звонки приходят</em>
                        )}
                    </div>
                ) : null}
            </section>

            <section className="wt-ok__tasks">
                <h2>Мои задачи</h2>
                <a href="#none" onClick={(e) => e.preventDefault()}>Колл Системс / Таксопарк исходящая</a>
                <a href="#none" onClick={(e) => e.preventDefault()}>Колл Системс / Таксопарк</a>
            </section>
        </div>
    </Shell>
);

const Phone = ({ world, go, emit }) => (
    <Shell world={world} go={go} emit={emit}>
        <div className="wt-ok__cols">
            <section className="wt-ok__dir">
                <div className="wt-ok__search">введите имя или номер</div>
                {DIRECTORY.map(([dept, people]) => (
                    <div key={dept} className="wt-ok__dept">
                        <div className="wt-ok__dept-name">{dept}</div>
                        {people.map(([name, ext, state]) => (
                            <div key={ext} className="wt-ok__person">
                                <Dot tone={state} />
                                <span>{name}</span>
                                <b>{ext}</b>
                            </div>
                        ))}
                    </div>
                ))}
                <p className="wt-ok__note">
                    Отсюда звонок переводят коллеге или старшему смены — по внутреннему номеру.
                    Справочник учебный, имена и номера вымышлены.
                </p>
            </section>
            <section className="wt-ok__tasks">
                <h2>Мои задачи</h2>
                <a href="#none" onClick={(e) => e.preventDefault()}>Колл Системс / Таксопарк исходящая</a>
                <a href="#none" onClick={(e) => e.preventDefault()}>Колл Системс / Таксопарк</a>
            </section>
        </div>
    </Shell>
);

const Messages = ({ world, go, emit }) => (
    <Shell world={world} go={go} emit={emit}>
        <div className="wt-ok__main">
            <header className="wt-ok__head"><h2>Сообщения</h2></header>
            <div className="wt-ok__empty">Новых сообщений нет</div>
        </div>
    </Shell>
);

const CAL = [
    [27, 28, 29, 30, 31, 1, 2],
    [3, 4, 5, 6, 7, 8, 9],
    [10, 11, 12, 13, 14, 15, 16],
    [17, 18, 19, 20, 21, 22, 23],
    [24, 25, 26, 27, 28, 29, 30],
];

const Journal = ({ world, go, emit }) => (
    <Shell world={world} go={go} emit={emit}>
        <div className="wt-ok__journal">
            <aside className="wt-ok__jside">
                <h2>Журнал</h2>
                <button type="button" className="is-on">Мои звонки</button>
                <button type="button">Пропущенные</button>
                <button type="button">Отложенные</button>
                <button type="button">Звонки отдела</button>
                <div className="wt-ok__cal">
                    <div className="wt-ok__cal-head">◀ Август 2026 ▶</div>
                    <div className="wt-ok__cal-week">
                        {['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'].map((d) => <span key={d}>{d}</span>)}
                    </div>
                    {CAL.map((week, wi) => (
                        <div key={`w${wi}`} className="wt-ok__cal-week">
                            {week.map((day, di) => (
                                <span
                                    key={`d${wi}-${di}`}
                                    className={`${(wi === 0 && day > 20) || (wi === 4 && day < 5) ? 'is-dim' : ''}${day === 23 && wi === 3 ? ' is-on' : ''}`}
                                >
                                    {day}
                                </span>
                            ))}
                        </div>
                    ))}
                </div>
            </aside>

            <section className="wt-ok__jmain">
                <h2>Мои звонки</h2>
                <div className="wt-ok__jfilters">
                    <span>Номер, имя или отдел</span>
                    <span>Комментарий или тег</span>
                </div>
                {JOURNAL.map(([day, rows]) => (
                    <div key={day}>
                        <div className="wt-ok__jday">{day}</div>
                        {rows.map(([time, dir, num, dur, tone], index) => (
                            <div key={`${day}-${index}`} className="wt-ok__jrow">
                                <span className={tone === 'miss' ? 'is-miss' : ''}>{time}</span>
                                <i className="wt-ok__arrow" aria-hidden="true">{dir === 'in' ? '←' : '→'}</i>
                                <b>{num}</b>
                                <Dot tone={tone} />
                                <u>{dur}</u>
                            </div>
                        ))}
                    </div>
                ))}
            </section>
        </div>
    </Shell>
);

const VIEWS = { cabinet: Cabinet, phone: Phone, messages: Messages, journal: Journal };

/** Адрес вкладки для строки браузера. Имя внутреннее, как в сети офиса. */
export const oktellUrl = (world) => (world.oktLogged
    ? `oktell_srv/#/${world.oktView || 'cabinet'}`
    : 'oktell_srv/#/login');

/** Клиент целиком. go — свободное перемещение, не шаг урока. */
export default function OktellApp({ world, go, emit }) {
    if (!world.oktLogged) return <Login go={go} emit={emit} />;
    const View = VIEWS[world.oktView] || Cabinet;
    return <View world={world} go={go} emit={emit} />;
}
