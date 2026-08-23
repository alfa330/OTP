import React, { useEffect, useRef, useState } from 'react';

import { Tap, TrainMark } from './screenKit';
import BrowserChrome from './BrowserChrome';
import FleetApp, { fleetUrl } from './screensFleet';
import {
    ANSWER, CATS, CITIES, PARKS, SOURCES, activeField, optionId,
} from './scenarioCrmTicket';

/* Экран CRM: тот же интерфейс, в котором оператор заводит обращение на смене.
 *
 * Три вещи скопированы с рабочей CRM намеренно, и менять их «чтобы красивее»
 * нельзя — на них держится узнавание:
 *   тёмно-синий сайдбар #485779 и синий акцент #4273fa (сняты пипеткой);
 *   порядок полей сверху вниз, включая «Город» СРАЗУ под таксопарком;
 *   поля, которых до времени нет: «Город» появляется после выбора парка,
 *   «Категория N+1» — после выбора N, «Комментарий» — после последней категории.
 *
 * Последнее — не украшение, а суть урока: новичок ищет город глазами и не
 * находит, потому что поля ещё нет. Показать его сразу значило бы стереть
 * ровно ту ошибку, ради которой тренажёр и сделан.
 */

const CRM_URL = 'backend.yataxi.kz/admin/list-requests/create';

/* Вкладки окна. Обе открыты с самого начала — так стоит браузер у оператора
   на смене, и «открой вторую систему» не должно быть отдельной задачей. */
const TABS = [
    { id: 'crm', title: 'Обращения - iTaxi', icon: 'crm' },
    { id: 'fleet', title: 'Диспетчерская', icon: 'fleet' },
];

/* Разделы сайдбара. Активен «Обращения» — мы в нём и находимся. Остальные
   кликабельны: уйти не туда посреди заполнения — живая ошибка. */
const NAV = [
    ['nav_drivers', 'Водители', false],
    ['nav_tickets_group', 'Тикетная система', false],
    ['nav_requests', 'Обращения', true],
    ['nav_tickets', 'Тикеты', false],
    ['nav_notifications', 'Уведомления', false],
    ['nav_edo', 'ЭДО', false],
    ['nav_news', 'Новостная лента', false],
];

const NavIcon = ({ name }) => {
    const d = {
        nav_drivers: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM4 20a8 8 0 0 1 16 0',
        nav_tickets_group: 'M4 6h16M4 12h16M4 18h16',
        nav_requests: 'M13 3 4 14h7l-1 7 9-11h-7l1-7Z',
        nav_tickets: 'M12 3c3 4 5 6.5 5 9a5 5 0 0 1-10 0c0-2.5 2-5 5-9Z',
        nav_notifications: 'M18 8a6 6 0 1 0-12 0c0 6-2 7-2 7h16s-2-1-2-7M10.5 20a2 2 0 0 0 3 0',
        nav_edo: 'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z',
        nav_news: 'M4 5h11v14H4zM15 9h5v10h-5z',
    }[name];
    return (
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor"
            strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d={d} />
        </svg>
    );
};

const Caret = () => (
    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor"
        strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="m6 9 6 6 6-6" />
    </svg>
);

/* Зелёная галочка «поле заполнено» — CRM ставит её справа в поле. Мелкая
   деталь, но именно по ней оператор на смене видит, что поле принято. */
const Ok = () => (
    <svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true" className="wt-crm__ok">
        <circle cx="12" cy="12" r="10" fill="#16dd6e" />
        <path d="m7.5 12.3 3 3 6-6.4" fill="none" stroke="#fff" strokeWidth="2.2"
            strokeLinecap="round" strokeLinejoin="round" />
    </svg>
);

/** Строка формы: подпись слева, поле справа — как в CRM. */
const Field = ({ label, children }) => (
    <div className="wt-crm__row">
        <span className="wt-crm__label">{label}</span>
        <div className="wt-crm__control">{children}</div>
    </div>
);

/* Выпадающий список. Своя реализация, а не <select>: раскрытый список
   системного селекта рисует ОС, и подсветить в нём нужный пункт невозможно —
   а подсветка цели есть на каждом экране тренажёра. В самой CRM «Категория»
   и так нарисована скриптом, а не системным списком. */
const Picker = ({
    name, group, options, value, active, open, setOpen, tap, target, placeholder,
}) => {
    const isOpen = open === name;
    const listRef = useRef(null);

    /* Раскрытый список нужно ПОКАЗАТЬ целиком.
     *
     * Нижние поля формы стоят у самого низа вкладки, и список из тридцати одного
     * пункта уходит за край окна: человек видит первые пять строк, не находит
     * нужную и решает, что её нет. Прокручиваем вкладку к списку, а внутри
     * списка — к искомому пункту, иначе до него всё равно надо докручивать
     * вслепую. */
    useEffect(() => {
        if (!isOpen || !listRef.current) return;
        const list = listRef.current;
        list.scrollIntoView({ block: 'nearest' });
        const goal = list.querySelector('.is-target');
        if (goal) goal.scrollIntoView({ block: 'nearest' });
    }, [isOpen]);

    return (
        <div className={`wt-crm__picker${isOpen ? ' is-open' : ''}`}>
            <button
                type="button"
                className={`wt-crm__box${active && !isOpen ? ' is-target' : ''}${value ? ' has-value' : ''}`}
                /* Клик по ЧУЖОМУ полю — это ход в движке: человек трогает поле
                   не вовремя, и ловушка объясняет, зачем оно. Клик по своему —
                   просто раскрытие списка, за любопытство не наказываем. */
                onClick={() => (active ? setOpen(isOpen ? null : name) : tap(`field_${name}`))}
            >
                <span className={value ? '' : 'is-placeholder'}>{value || placeholder}</span>
                {value ? <Ok /> : null}
                <i className="wt-crm__caret" aria-hidden="true"><Caret /></i>
            </button>
            {isOpen && (
                <ul className="wt-crm__list" role="listbox" ref={listRef}>
                    {options.map((option, index) => (
                        <li key={option}>
                            <Tap
                                id={optionId(group, index)}
                                target={target}
                                tap={tap}
                                className="wt-crm__option"
                                role="option"
                            >
                                {option}
                            </Tap>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
};

/* Поле ввода. Пока оно не «своё» — это коробка, по которой можно промахнуться;
   как только своё — настоящий input, и значение уходит в движок по Enter.
   Enter, а не потеря фокуса: на blur ошибка вылетала бы от простого клика
   мимо, и человек получал бы разбор ошибки, которую не совершал. */
const TextField = ({
    name, action, active, value, placeholder, tap, multiline = false, hint,
}) => {
    const [draft, setDraft] = useState(value || '');

    if (!active) {
        return (
            <button type="button" className={`wt-crm__box${value ? ' has-value' : ''}`}
                onClick={() => tap(`field_${name}`)}>
                <span className={value ? '' : 'is-placeholder'}>{value || placeholder}</span>
                {value ? <Ok /> : null}
            </button>
        );
    }

    const send = () => tap(action, { value: draft });
    const onKey = (event) => {
        // Enter отправляет и в многострочном комментарии: перенос строки в нём
        // всё равно не нужен, а вторая кнопка «Готово» рядом с полем в CRM
        // отсутствует и сбивала бы с толку.
        if (event.key === 'Enter') { event.preventDefault(); send(); }
    };
    const Tag = multiline ? 'textarea' : 'input';
    return (
        <div className="wt-crm__input-wrap is-target">
            <Tag
                className={`wt-crm__input${multiline ? ' wt-crm__input--area' : ''}`}
                value={draft}
                placeholder={placeholder}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={onKey}
                rows={multiline ? 3 : undefined}
                autoFocus
                aria-label={placeholder}
            />
            <button type="button" className="wt-crm__enter" onClick={send}>
                Enter
            </button>
            {hint ? <small className="wt-crm__hint">{hint}</small> : null}
        </div>
    );
};

/* Карточка входящего звонка — окно софтфона поверх рабочего стола.
 *
 * В самой CRM её нет и быть не может: это отдельная программа, в которой
 * оператор видит, кто звонит. В тренажёре она обязательна — иначе «выбери
 * таксопарк {park}» превращается в угадайку, тогда как на смене эти данные
 * перед глазами. */
export const CallCard = ({ call }) => (
    <div className="wt-call">
        <div className="wt-call__head">
            <span className="wt-call__live" aria-hidden="true" />
            Входящий звонок
        </div>
        <dl className="wt-call__facts">
            <div><dt>Номер</dt><dd>{call.phone}</dd></div>
            <div><dt>Таксопарк</dt><dd>{call.park}</dd></div>
            <div><dt>Город</dt><dd>{call.city}</dd></div>
            <div><dt>Статус</dt><dd>{call.status}</dd></div>
        </dl>
        <p className="wt-call__said">
            <span>Водитель:</span> «{call.said}»
        </p>
    </div>
);

/** Страница CRM «Создать обращение» целиком. */
const CrmPage = ({ world, tap, target }) => {
    const { form } = world;
    const active = activeField(form);
    const [open, setOpen] = useState(null);

    /* Сколько уровней категорий показывать. Всегда на один больше, чем выбрано:
       следующий уровень появляется ровно тогда, когда предыдущий заполнен —
       так же, как это делает CRM. */
    const levels = Math.min(form.cats.length + 1, ANSWER.cats.length);
    const catOptions = [CATS.level1, CATS.level2, CATS.level3, CATS.level4];

    return (
        <div className="wt-crm">
            <TrainMark>Учебная CRM</TrainMark>

            <aside className="wt-crm__side">
                <div className="wt-crm__brand">iTaxi</div>
                <nav>
                    {NAV.map(([id, label, current]) => (
                        <Tap
                            key={id}
                            id={id}
                            target={target}
                            tap={tap}
                            className={`wt-crm__nav${current ? ' is-current' : ''}`}
                            aria-current={current ? 'page' : undefined}
                        >
                            <NavIcon name={id} />
                            {label}
                        </Tap>
                    ))}
                </nav>
            </aside>

            <main className="wt-crm__main">
                <div className="wt-crm__crumbs">
                    Главная <i>/</i> Обращения <i>/</i> <b>Создать обращение</b>
                </div>

                <section className="wt-crm__card">
                    <header className="wt-crm__card-head">+ Создать обращение</header>

                    <div className="wt-crm__form">
                        <Field label="Звонок/Чат">
                            <Picker
                                name="source" group="src" options={SOURCES}
                                value={form.source} active={active === 'source'}
                                open={open} setOpen={setOpen} tap={tap} target={target}
                                placeholder="Выберите источник"
                            />
                        </Field>

                        <Field label="Номер телефона">
                            <TextField
                                name="phone" action="phone_done" active={active === 'phone'}
                                value={form.phone} placeholder="Номер телефона" tap={tap}
                                hint="Одиннадцать цифр без плюса, затем Enter"
                            />
                        </Field>

                        <Field label="Номер В/У">
                            <button type="button" className="wt-crm__box"
                                onClick={() => tap('field_license')}>
                                <span className="is-placeholder">Номер В/У</span>
                            </button>
                        </Field>

                        <Field label="ID водителя">
                            <button type="button" className="wt-crm__box"
                                onClick={() => tap('field_account')}>
                                <span className="is-placeholder">ID водителя</span>
                            </button>
                        </Field>

                        <Field label="Дата обращения">
                            <button type="button" className="wt-crm__box has-value"
                                onClick={() => tap('field_date')}>
                                <span>{world.now}</span>
                            </button>
                        </Field>

                        <Field label="Таксопарк">
                            <Picker
                                name="park" group="park" options={PARKS}
                                value={form.park} active={active === 'park'}
                                open={open} setOpen={setOpen} tap={tap} target={target}
                                placeholder="Выберите таксопарк"
                            />
                        </Field>

                        {/* Города нет, пока не выбран парк — главный урок формы. */}
                        {form.park ? (
                            <Field label="Город">
                                <Picker
                                    name="city" group="city"
                                    options={CITIES[form.park] || []}
                                    value={form.city} active={active === 'city'}
                                    open={open} setOpen={setOpen} tap={tap} target={target}
                                    placeholder="Выберите город"
                                />
                            </Field>
                        ) : null}

                        {Array.from({ length: levels }, (_, level) => (
                            <Field key={`cat${level + 1}`} label={`Категория ${level + 1}`}>
                                <Picker
                                    name={`cat${level + 1}`} group={`c${level + 1}`}
                                    options={catOptions[level]}
                                    value={form.cats[level] || ''}
                                    active={active === `cat${level + 1}`}
                                    open={open} setOpen={setOpen} tap={tap} target={target}
                                    placeholder="Выберите категорию"
                                />
                            </Field>
                        ))}

                        {/* Комментарий приходит вместе с категорией: в CRM набор
                            доп. полей задаётся выбранной категорией, а не формой. */}
                        {form.cats.length >= ANSWER.cats.length ? (
                            <Field label="Комментарий">
                                <TextField
                                    name="comment" action="comment_done"
                                    active={active === 'comment'}
                                    value={form.comment} placeholder="Комментарий"
                                    tap={tap} multiline
                                    hint="Опиши суть обращения, затем Enter"
                                />
                            </Field>
                        ) : null}

                        <div className="wt-crm__dup">
                            <Tap id="dup_check" target={target} tap={tap}
                                className="wt-crm__check" aria-label="Дублировать обращение">
                                <i aria-hidden="true" />
                                Дублировать обращение
                            </Tap>
                        </div>

                        <div className="wt-crm__actions">
                            <Tap id="save" target={target} tap={tap} className="wt-crm__save">
                                <svg viewBox="0 0 24 24" width="15" height="15" fill="none"
                                    stroke="currentColor" strokeWidth="2" strokeLinecap="round"
                                    strokeLinejoin="round" aria-hidden="true">
                                    <path d="M12 3v12m0 0-4.5-4.5M12 15l4.5-4.5M4 20h16" />
                                </svg>
                                Сохранить
                            </Tap>
                        </div>
                    </div>
                </section>
            </main>
        </div>
    );
};

/** Экран шага: окно браузера с двумя вкладками — CRM и Диспетчерская.
 *
 * Урок живёт только в CRM. Диспетчерская — справочник без шагов и ловушек:
 * туда ходят смотреть, чью комиссию удержали, и переход туда не ход, а
 * свободное перемещение (browse), поэтому промахом он не считается. */
export const CrmForm = ({ world, tap, target, browse }) => {
    const onFleet = world.tab === 'fleet';
    return (
        <BrowserChrome
            tap={tap}
            target={target}
            tabs={TABS}
            active={world.tab || 'crm'}
            onSwitch={(id) => browse({ tab: id })}
            url={onFleet ? fleetUrl(world) : CRM_URL}
        >
            {onFleet
                ? <FleetApp world={world} go={browse} />
                : <CrmPage world={world} tap={tap} target={target} />}
        </BrowserChrome>
    );
};
