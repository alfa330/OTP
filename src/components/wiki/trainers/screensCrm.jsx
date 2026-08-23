import React, { useEffect, useRef, useState } from 'react';

import { TrainMark } from './screenKit';
import BrowserChrome from './BrowserChrome';
import FleetApp, { fleetUrl } from './screensFleet';
import OktellApp, { DIRECTORY, oktellUrl } from './screensOktell';
import CallPanel from './CallPanel';
import { PARKS, PARK_CITIES, SOURCES, childrenAt } from './crmCatalog.js';

/* Экран CRM: тот же интерфейс, в котором оператор заводит обращение на смене.
 *
 * Три вещи скопированы с рабочей CRM намеренно, и менять их «чтобы красивее»
 * нельзя — на них держится узнавание:
 *   тёмно-синий сайдбар #485779 и синий акцент #4273fa (сняты пипеткой);
 *   порядок полей сверху вниз, включая «Город» СРАЗУ под таксопарком;
 *   поля, которых до времени нет: «Город» появляется после выбора парка,
 *   «Категория N+1» — после выбора N.
 *
 * Форма СВОБОДНАЯ: подсветки правильного варианта нет, ошибиться нельзя, любую
 * ветку из 387 категорий можно пройти до конца. Дерево настоящее (crmCatalog),
 * потому что учить искать в укороченном списке — учить не тому.
 */

const CRM_URL = 'backend.yataxi.kz/admin/list-requests/create';

/* Вкладки окна. Все три открыты сразу, как стоит браузер у оператора на смене:
   открывать их по очереди — не задача урока, а лишний шаг. */
const TABS = [
    { id: 'crm', title: 'Обращения - iTaxi', icon: 'crm' },
    { id: 'fleet', title: 'Диспетчерская', icon: 'fleet' },
    { id: 'oktell', title: 'Okapp — Oktell', icon: 'oktell' },
];

const NAV = [
    ['nav_drivers', 'Водители'],
    ['nav_tickets_group', 'Тикетная система'],
    ['nav_requests', 'Обращения'],
    ['nav_tickets', 'Тикеты'],
    ['nav_notifications', 'Уведомления'],
    ['nav_edo', 'ЭДО'],
    ['nav_news', 'Новостная лента'],
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

const Field = ({ label, children }) => (
    <div className="wt-crm__row">
        <span className="wt-crm__label">{label}</span>
        <div className="wt-crm__control">{children}</div>
    </div>
);

/* Выпадающий список. Своя реализация, а не <select>: раскрытый список
   системного селекта рисует ОС, и внутри учебного окна он выглядел бы чужим.
   В самой CRM «Категория» тоже нарисована скриптом. */
const Picker = ({ name, options, value, open, setOpen, onPick, placeholder }) => {
    const isOpen = open === name;
    const listRef = useRef(null);

    /* Раскрытый список нужно показать целиком: нижние поля формы стоят у края
       вкладки, и список из тридцати пунктов уходит за него — человек видит пять
       строк и решает, что остального нет. */
    useEffect(() => {
        if (isOpen && listRef.current) listRef.current.scrollIntoView({ block: 'nearest' });
    }, [isOpen]);

    return (
        <div className={`wt-crm__picker${isOpen ? ' is-open' : ''}`}>
            <button
                type="button"
                className={`wt-crm__box${value ? ' has-value' : ''}`}
                onClick={() => setOpen(isOpen ? null : name)}
            >
                <span className={value ? '' : 'is-placeholder'}>{value || placeholder}</span>
                {value ? <Ok /> : null}
                <i className="wt-crm__caret" aria-hidden="true"><Caret /></i>
            </button>
            {isOpen && (
                <ul className="wt-crm__list" role="listbox" ref={listRef}>
                    {options.map((option) => (
                        <li key={option}>
                            <button
                                type="button"
                                className={`wt-crm__option${option === value ? ' is-picked' : ''}`}
                                role="option"
                                aria-selected={option === value}
                                onClick={() => { onPick(option); setOpen(null); }}
                            >
                                {option}
                            </button>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
};

/* Поле ввода. Значение держится и локально (чтобы буквы появлялись сразу), и в
   мире (чтобы дожить до «Сохранить»): мир обновляется на каждый ввод, локальный
   стейт нужен только против дёрганья курсора. */
const TextField = ({ value, placeholder, onChange, multiline = false }) => {
    const [draft, setDraft] = useState(value || '');
    const Tag = multiline ? 'textarea' : 'input';
    return (
        <div className="wt-crm__input-wrap">
            <Tag
                className={`wt-crm__input${multiline ? ' wt-crm__input--area' : ''}`}
                value={draft}
                placeholder={placeholder}
                rows={multiline ? 3 : undefined}
                aria-label={placeholder}
                onChange={(event) => { setDraft(event.target.value); onChange(event.target.value); }}
            />
            {draft ? <Ok /> : null}
        </div>
    );
};

/** Страница CRM «Создать обращение». */
const CrmPage = ({ world, browse, onSave }) => {
    const form = world.form;
    const [open, setOpen] = useState(null);

    const set = (patch) => browse({ form: { ...form, ...patch } });

    /* Выбор категории уровня level обрезает всё, что было выбрано глубже:
       иначе под новой веткой остались бы пункты из старой. */
    const pickCat = (level, value) => {
        const cats = form.cats.slice(0, level);
        cats[level] = value;
        set({ cats });
    };

    // Сколько уровней категорий показывать: всегда на один больше выбранного,
    // пока у выбранного есть дети — ровно так ведёт себя CRM.
    const levels = [];
    for (let i = 0; i <= form.cats.length; i += 1) {
        const options = childrenAt(form.cats.slice(0, i));
        if (!options.length) break;
        levels.push(options);
    }

    return (
        <div className="wt-crm">
            <TrainMark>Учебная CRM</TrainMark>

            <aside className="wt-crm__side">
                <div className="wt-crm__brand">iTaxi</div>
                <nav>
                    {NAV.map(([id, label]) => (
                        <button key={id} type="button"
                            className={`wt-crm__nav${id === 'nav_requests' ? ' is-current' : ''}`}
                            aria-current={id === 'nav_requests' ? 'page' : undefined}>
                            <NavIcon name={id} />
                            {label}
                        </button>
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
                            <Picker name="source" options={SOURCES} value={form.source}
                                open={open} setOpen={setOpen}
                                onPick={(v) => set({ source: v })}
                                placeholder="Выберите источник" />
                        </Field>

                        <Field label="Номер телефона">
                            <TextField value={form.phone} placeholder="Номер телефона"
                                onChange={(v) => set({ phone: v })} />
                        </Field>

                        <Field label="Номер В/У">
                            <TextField value={form.license} placeholder="Номер В/У"
                                onChange={(v) => set({ license: v })} />
                        </Field>

                        <Field label="ID водителя">
                            <TextField value={form.account} placeholder="ID водителя"
                                onChange={(v) => set({ account: v })} />
                        </Field>

                        <Field label="Дата обращения">
                            <div className="wt-crm__box has-value"><span>{world.now}</span></div>
                        </Field>

                        <Field label="Таксопарк">
                            <Picker name="park" options={PARKS} value={form.park}
                                open={open} setOpen={setOpen}
                                onPick={(v) => set({ park: v, city: '' })}
                                placeholder="Выберите таксопарк" />
                        </Field>

                        {/* Города нет, пока не выбран парк — так устроена CRM. */}
                        {form.park ? (
                            <Field label="Город">
                                <Picker name="city" options={PARK_CITIES[form.park] || []}
                                    value={form.city} open={open} setOpen={setOpen}
                                    onPick={(v) => set({ city: v })}
                                    placeholder="Выберите город" />
                            </Field>
                        ) : null}

                        {levels.map((options, level) => (
                            <Field key={`cat${level}`} label={`Категория ${level + 1}`}>
                                <Picker name={`cat${level}`} options={options}
                                    value={form.cats[level] || ''} open={open} setOpen={setOpen}
                                    onPick={(v) => pickCat(level, v)}
                                    placeholder="Выберите категорию" />
                            </Field>
                        ))}

                        <Field label="Комментарий">
                            <TextField value={form.comment} placeholder="Комментарий" multiline
                                onChange={(v) => set({ comment: v })} />
                        </Field>

                        <div className="wt-crm__dup">
                            <button type="button" className="wt-crm__check"
                                onClick={() => set({ duplicate: !form.duplicate })}
                                aria-pressed={form.duplicate}>
                                <i className={form.duplicate ? 'is-on' : ''} aria-hidden="true">
                                    {form.duplicate ? '✓' : ''}
                                </i>
                                Дублировать обращение
                            </button>
                        </div>

                        {/* Единственное действие движка на всю среду: оно
                            заканчивает попытку и отдаёт итог в статистику. */}
                        <div className="wt-crm__actions">
                            {/* Сохранение — не ход движка: в режиме смены после
                                него идёт постобработка, и попытку закрывает
                                стажёр. Подробности в TrainerPlayer.doSave. */}
                            <button type="button" className="wt-crm__save" onClick={onSave}>
                                <svg viewBox="0 0 24 24" width="15" height="15" fill="none"
                                    stroke="currentColor" strokeWidth="2" strokeLinecap="round"
                                    strokeLinejoin="round" aria-hidden="true">
                                    <path d="M12 3v12m0 0-4.5-4.5M12 15l4.5-4.5M4 20h16" />
                                </svg>
                                Сохранить
                            </button>
                        </div>
                    </div>
                </section>
            </main>
        </div>
    );
};

/** Экран рабочего места: окно браузера с тремя вкладками и плашкой звонка.
 *
 * Плашка стоит ПОВЕРХ окна, а не внутри вкладки: во время разговора оператор
 * сидит в Диспетчерской, и кнопки «Удержание» и «Перевод» обязаны ехать за ним.
 */
export const DeskScreen = ({
    world, tap, target, browse, emit, act, onSave, onCall, voice, aiSpeaking,
    micLevel, micError, onRing, devMode,
}) => {
    const tab = world.tab || 'crm';
    const url = tab === 'fleet' ? fleetUrl(world)
        : tab === 'oktell' ? oktellUrl(world)
            : CRM_URL;
    return (
        <div className="wt-desk__inner">
            <BrowserChrome
                tap={tap}
                target={target}
                tabs={TABS}
                active={tab}
                onSwitch={(id) => { emit('ui.tab', { tab: id }); browse({ tab: id }); }}
                url={url}
            >
                {tab === 'fleet'
                    ? <FleetApp world={world} go={browse} emit={emit} act={act} /> : null}
                {tab === 'oktell'
                    ? <OktellApp world={world} go={browse} emit={emit} /> : null}
                {tab === 'crm'
                    ? <CrmPage world={world} browse={browse} onSave={onSave} /> : null}
            </BrowserChrome>

            {onCall ? (
                <CallPanel
                    call={world.call}
                    onCall={onCall}
                    directory={DIRECTORY}
                    voice={voice}
                    aiSpeaking={aiSpeaking}
                    micLevel={micLevel}
                    micError={micError}
                    onRing={onRing}
                    devMode={devMode}
                />
            ) : null}
        </div>
    );
};
