import React, { useLayoutEffect, useMemo, useRef, useState } from 'react';

import {
    YA_EMAIL, YA_FILES, YA_PARKS, YA_PARKS_CLOSED, YA_SAMPLE_APPEALS, YA_SEARCH_TYPES, YA_STATUSES, YA_TOPIC_TITLES,
    YA_UNREAD, emptyYaForm, topicAt, topicChildren, topicInputs, topicLeaves, yaMissing, yaStatus,
} from './crmYandex.js';
import { agoText, noNav, normYo } from './crmKit';

/* «Поддержка → Обращения в Яндекс»: список, новое обращение, обращение.
 *
 * Разметка — рабочей CRM 20.11.2026 (страницы Blade, не Vue): список с
 * фильтрами-селектами, форма со скриптом тем (парк → тема → подтема, поля
 * конечной темы, «Отправить» загорается, когда заполнены поля со звёздочкой)
 * и страница обращения с перепиской и ответом поддержке.
 *
 * Отправка учебная — «тестовый режим» из ТЗ: обращение и ответ появляются в
 * списке и переписке этой попытки, в Яндекс не уходит ничего.
 */

/** «Jana (Алматы)» → ['Jana', 'Алматы']. */
export const splitPark = (name) => {
    const match = /^(.*) \(([^()]+)\)$/.exec(String(name || ''));
    return match ? [match[1], match[2]] : [String(name || ''), ''];
};

/** Подсказка темы: строки и ссылки, переводы строк сохраняются. */
const Parts = ({ parts }) => (
    <>
        {(parts || []).map((part, index) => (Array.isArray(part)
            ? <a key={index} href={part[1]} target="_blank" rel="noopener noreferrer">{part[0]}</a>
            : <React.Fragment key={index}>{part}</React.Fragment>))}
    </>
);

/* «ID в Диспетчерской» — 24 знака, как у Яндекса; учебный, из номера. */
const fakeHex = (seed) => {
    let hash = 2166136261;
    for (const char of String(seed)) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619) >>> 0;
    return (hash.toString(16).padStart(8, '0').repeat(3)).slice(0, 24);
};

/** Все обращения списка: свои (новые сверху) и учебные. */
export const allAppeals = (world) => [...[...(world.yaAppeals || [])].reverse(), ...YA_SAMPLE_APPEALS];

const appealText = (appeal) => appeal.text || appeal.values?.message || '';

/* Вопрос в строке списка CRM обрезает до 160 знаков с «...». */
const cut = (text) => (text.length > 160 ? `${text.slice(0, 160)}...` : text);

const appealUpdated = (world, appeal) => appeal.updatedAt || appeal.createdAt || agoText(world.nowMs, appeal.updatedAgo);
const appealCreated = (world, appeal) => appeal.createdAt || agoText(world.nowMs, appeal.createdAgo);

/* Свежесть ответа: есть ответ поддержки, который стажёр ещё не открывал. */
const isUnread = (world, appeal) => appeal.unread && !(world.yaRead || []).includes(appeal.id);

/* Подписи времени «дд.мм.гггг чч:мм» → число для сортировки. */
const stamp = (text) => {
    const match = /^(\d{2})\.(\d{2})\.(\d{4}) (\d{2}):(\d{2})/.exec(String(text || ''));
    return match ? Number(`${match[3]}${match[2]}${match[1]}${match[4]}${match[5]}`) : 0;
};

const EMPTY_FILTER = {
    type: 'order_id', search: '', status: '', unread: '', perPage: '25', from: '', to: '',
    topic: '', sub: '', author: '', park: '',
};

/** Отбор списка — по правилам фильтра CRM. */
export const filterAppeals = (world, filter) => allAppeals(world).filter((appeal) => {
    if (filter.status && appeal.status !== filter.status) return false;
    if (filter.topic && appeal.topic[0] !== filter.topic) return false;
    if (filter.sub) {
        /* Подтема помнит раздел: одноимённые конечные темы есть в разных
           разделах («Другой вопрос», «Как подключить»). */
        const [topic, title] = filter.sub.split('\u0000');
        if (appeal.topic[appeal.topic.length - 1] !== title || appeal.topic[0] !== topic) return false;
    }
    if (filter.author && appeal.author !== filter.author) return false;
    if (filter.park && appeal.park !== filter.park) return false;
    if (filter.unread === '1' && !isUnread(world, appeal)) return false;
    if (filter.unread === 'open' && !(isUnread(world, appeal) && ['in_work', 'need_info'].includes(appeal.status))) return false;
    const needle = normYo(filter.search.trim());
    if (needle) {
        const haystack = filter.type === 'text' ? appealText(appeal) : (appeal.values || {})[filter.type];
        if (!normYo(haystack).replace(/\s+/g, '').includes(needle.replace(/\s+/g, ''))) return false;
    }
    return true;
});

/** Страница списка. */
export const YaList = ({ ui }) => {
    const { world } = ui;
    const [draft, setDraft] = useState(world.yaFilter || EMPTY_FILTER);
    const applied = world.yaFilter || EMPTY_FILTER;
    const [sort, setSort] = useState({ by: 'updated', dir: 'desc' });
    const set = (patch) => setDraft({ ...draft, ...patch });

    const topics = draft.topic ? [draft.topic] : YA_TOPIC_TITLES;
    const authors = ['Оператор Учебный', 'Стажёр'];

    const rows = useMemo(() => {
        const found = filterAppeals(world, applied);
        const key = sort.by === 'updated' ? (a) => stamp(appealUpdated(world, a)) : (a) => stamp(appealCreated(world, a));
        return [...found].sort((a, b) => (sort.dir === 'desc' ? key(b) - key(a) : key(a) - key(b)) || b.id - a.id);
    }, [world, applied, sort]);

    const apply = (id = 'ya_apply') => ui.touch(id, { yaFilter: draft }, { filter: draft });
    const sortBy = (by) => setSort(sort.by === by
        ? { by, dir: sort.dir === 'desc' ? 'asc' : 'desc' }
        : { by, dir: 'desc' });
    const arrow = (by) => (sort.by === by ? (sort.dir === 'desc' ? ' ↓' : ' ↑') : '');

    return (
        <div className="row">
            <div className="col">
                <div className="card">
                    <div className="card-header">
                        <a className={`btn btn-sm btn-primary float-right${ui.t('ya_create_btn')}`} href="#"
                            onClick={(e) => { noNav(e); ui.go('ya_new', { yaForm: emptyYaForm() }, 'ya_create_btn'); }}>+ Новое обращение</a>
                        {' '}<i className="icon-earphones-alt" /> Обращения в поддержку Яндекса{' '}
                        <small className="text-muted ml-2"> сверено с Диспетчерской {agoText(world.nowMs, 1)} </small>
                    </div>
                    <div className="card-body">
                        <form onSubmit={(event) => { event.preventDefault(); apply(); }}>
                            <div className="row">
                                <div className="col-lg-2 form-group">
                                    <select className={`form-control${ui.t('ya_search_type')}`} name="search_type" value={draft.type}
                                        onChange={(event) => set({ type: event.target.value })}>
                                        {YA_SEARCH_TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                                    </select>
                                </div>
                                {' '}
                                <div className="col-lg-4 form-group">
                                    <div className="input-group">
                                        <input className={`form-control${ui.t('ya_search')}`} maxLength={100} name="search" placeholder="Поиск"
                                            value={draft.search} onChange={(event) => set({ search: event.target.value })} />
                                        <span className="input-group-append">
                                            <button className="btn btn-primary" type="submit"><i className="fa fa-search" /></button>
                                        </span>
                                    </div>
                                </div>
                                {' '}
                                <div className="col-lg-2 form-group">
                                    <select className={`form-control${ui.t('ya_status')}`} name="status" value={draft.status}
                                        onChange={(event) => set({ status: event.target.value })}>
                                        <option value="">Любой статус</option>
                                        {YA_STATUSES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                                    </select>
                                </div>
                                {' '}
                                <div className="col-lg-2 form-group">
                                    <select className={`form-control${ui.t('ya_unread')}`} name="unread" value={draft.unread}
                                        onChange={(event) => set({ unread: event.target.value })}>
                                        {YA_UNREAD.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                                    </select>
                                </div>
                                {' '}
                                <div className="col-lg-2 form-group">
                                    <select className="form-control" name="per_page" value={draft.perPage}
                                        onChange={(event) => set({ perPage: event.target.value })}>
                                        <option value="25">По 25</option> <option value="50">По 50</option> <option value="100">По 100</option>
                                    </select>
                                </div>
                            </div>
                            {' '}
                            <div className="row">
                                <div className="col-lg-2 form-group">
                                    <input className="form-control" name="date_from" title="Создано с" type="date" value={draft.from}
                                        onChange={(event) => set({ from: event.target.value })} />
                                </div>
                                {' '}
                                <div className="col-lg-2 form-group">
                                    <input className="form-control" name="date_to" title="Создано по" type="date" value={draft.to}
                                        onChange={(event) => set({ to: event.target.value })} />
                                </div>
                                {' '}
                                <div className="col-lg-2 form-group">
                                    <select className={`form-control${ui.t('ya_filter_topic')}`} name="topic_id" value={draft.topic}
                                        onChange={(event) => set({ topic: event.target.value, sub: '' })}>
                                        <option value="">Любая тема</option>
                                        {YA_TOPIC_TITLES.map((title) => <option key={title} value={title}>{title}</option>)}
                                    </select>
                                </div>
                                {' '}
                                <div className="col-lg-2 form-group">
                                    <select className={`form-control${ui.t('ya_filter_sub')}`} name="subtopic_id" value={draft.sub}
                                        onChange={(event) => set({ sub: event.target.value })}>
                                        <option value="">Любая подтема</option>
                                        {topics.map((topic) => (
                                            <optgroup key={topic} label={topic}>
                                                {topicLeaves(topic).map((title) => (
                                                    <option key={title} value={`${topic}\u0000${title}`}>{title}</option>
                                                ))}
                                            </optgroup>
                                        ))}
                                    </select>
                                </div>
                                {' '}
                                <div className="col-lg-2 form-group">
                                    <select className="form-control" name="operator_id" value={draft.author}
                                        onChange={(event) => set({ author: event.target.value })}>
                                        <option value="">Любой оператор</option>
                                        {authors.map((name) => <option key={name} value={name}>{name}</option>)}
                                    </select>
                                </div>
                                {' '}
                                <div className="col-lg-2 form-group">
                                    <select className="form-control" name="park_id" value={draft.park}
                                        onChange={(event) => set({ park: event.target.value })}>
                                        <option value="">Любой парк</option>
                                        {YA_PARKS.map((park) => <option key={park} value={park}>{park}</option>)}
                                    </select>
                                </div>
                            </div>
                            {' '}
                            <div className="mb-3">
                                <button className={`btn btn-sm btn-primary${ui.t('ya_apply')}`} type="submit">Применить</button>
                                {' '}
                                <a className="btn btn-sm btn-link" href="#"
                                    onClick={(e) => { noNav(e); setDraft(EMPTY_FILTER); ui.touch('ya_reset', { yaFilter: EMPTY_FILTER }); }}>Сбросить</a>
                            </div>
                        </form>
                        {' '}
                        <table className="table table-hover table-listing">
                            <thead>
                                <tr>
                                    <th style={{ width: 16 }} /><th>Вопрос</th><th>Статус</th><th>Автор</th><th>Таксопарк</th>
                                    <th className="text-nowrap"><a href="#" onClick={(e) => { noNav(e); sortBy('updated'); }}>Обновлено{arrow('updated')}</a></th>
                                    <th className="text-nowrap"><a href="#" onClick={(e) => { noNav(e); sortBy('created'); }}>Создано{arrow('created')}</a></th>
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map((appeal) => {
                                    const [park, city] = splitPark(appeal.park);
                                    const status = yaStatus(appeal.status);
                                    return (
                                        <tr key={appeal.id}>
                                            <td>
                                                {isUnread(world, appeal) ? (
                                                    <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: '#e55353' }}
                                                        title="Новый ответ поддержки — вы ещё не открывали" />
                                                ) : null}
                                            </td>
                                            <td>
                                                <a className={`text-body${ui.t(`ya_row_${appeal.id}`, 'ya_row')}`} href="#"
                                                    onClick={(e) => {
                                                        noNav(e);
                                                        ui.go('ya_show', {
                                                            yaOpen: appeal.id,
                                                            yaRead: [...new Set([...(world.yaRead || []), appeal.id])],
                                                        }, `ya_row_${appeal.id}`, { id: appeal.id });
                                                    }}>
                                                    {' '}{cut(appealText(appeal))}{' '}
                                                </a>
                                                {' '}
                                                <small className="text-muted d-block"> {appeal.topic.join(' • ')} </small>
                                            </td>
                                            <td><span className={`badge badge-${status.tone}`}>{status.label}</span></td>
                                            <td> {appeal.author} <small className="text-muted d-block">{YA_EMAIL}</small></td>
                                            <td> {park} <small className="text-muted d-block">{city}</small></td>
                                            <td className="text-nowrap">{appealUpdated(world, appeal)}</td>
                                            <td className="text-nowrap">{appealCreated(world, appeal)}</td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                        {' '}
                        {!rows.length ? <p className="text-muted">Обращений не найдено.</p> : null}
                        <div className="row">
                            <div className="col-sm"><span className="text-muted">Всего: {rows.length}</span></div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
};

/** Поле конечной темы — как его рисует скрипт формы CRM. */
const TopicInput = ({ ui, input, form }) => {
    if (input.kind === 'label') {
        return <div className="alert alert-light small" style={{ whiteSpace: 'pre-line' }}><Parts parts={input.parts} /></div>;
    }
    const values = form.values || {};
    const value = values[input.name];
    const id = `ya_field_${input.name}`;
    const setValue = (next) => ui.touch(id, { yaForm: { ...form, values: { ...values, [input.name]: next } } },
        { name: input.name, value: next });
    const name = `values[${input.name}]`;
    let control;
    if (input.kind === 'textarea') {
        control = (
            <textarea className={`form-control${ui.t(id)}`} data-kind="textarea" data-required={input.required ? '1' : ''}
                maxLength={10000} name={name} rows={4} value={value || ''} onChange={(e) => setValue(e.target.value)} />
        );
    } else if (input.kind === 'date') {
        control = (
            <input className={`form-control${ui.t(id)}`} data-kind="date" data-required={input.required ? '1' : ''}
                name={name} type="date" value={value || ''} onChange={(e) => setValue(e.target.value)} />
        );
    } else if (input.kind === 'date_period') {
        const pair = Array.isArray(value) ? value : ['', ''];
        control = (
            <div className={`form-row${ui.t(id)}`} data-kind="date_period" data-required={input.required ? '1' : ''}>
                {['с', 'по'].map((label, index) => (
                    <div className="col" key={label}>
                        <small className="text-muted">{label}</small>
                        <input className="form-control" type="date" name={`${name}[]`} title={label} value={pair[index] || ''}
                            onChange={(e) => {
                                const next = [...pair];
                                next[index] = e.target.value;
                                setValue(next);
                            }} />
                    </div>
                ))}
            </div>
        );
    } else if (input.kind === 'file') {
        control = (
            <input className={`form-control-file${ui.t('ya_files')}`} data-kind="file" data-required={input.required ? '1' : ''}
                multiple name="files[]" type="file"
                onChange={(event) => {
                    const files = Array.from(event.target.files || []).map((file) => file.name);
                    ui.touch('ya_files', { yaForm: { ...form, files } }, { files });
                }} />
        );
    } else {
        control = (
            <input className={`form-control${ui.t(id)}`} data-kind="text" data-required={input.required ? '1' : ''}
                maxLength={10000} name={name} type="text" value={value || ''} onChange={(e) => setValue(e.target.value)} />
        );
    }
    return (
        <div className="form-group">
            <label>{input.title}{input.required ? ' *' : ''}</label>
            {control}
            {input.kind === 'file' ? (
                <small className="text-muted d-block">
                    До {YA_FILES.max_count} файлов, каждый до {YA_FILES.max_size_mb} МБ, все вместе — до 30 МБ.
                </small>
            ) : null}
            {input.hint ? <small className="text-muted d-block"><Parts parts={input.hint} /></small> : null}
        </div>
    );
};

/* Поиск по списку парков — скрипт select-search CRM: поле над списком,
   найденное — вторым списком под ним (до 8 строк), Enter берёт первое. */
const ParkPicker = ({ ui, value, onPick }) => {
    const [query, setQuery] = useState('');
    const selectRef = useRef(null);
    const [place, setPlace] = useState(null);
    const words = normYo(query).split(/\s+/).filter(Boolean);
    const found = words.length ? YA_PARKS.filter((park) => words.every((word) => normYo(park).includes(word))) : [];
    const open = words.length > 0;

    useLayoutEffect(() => {
        const select = selectRef.current;
        if (!open || !select) return;
        setPlace({ left: select.offsetLeft, top: select.offsetTop + select.offsetHeight, minWidth: select.offsetWidth });
    }, [open]);

    const pick = (park) => {
        if (!park || YA_PARKS_CLOSED.includes(park)) return;
        setQuery('');
        onPick(park);
    };

    return (
        <div className="form-group" style={{ position: 'relative' }}>
            <label htmlFor="appeal-park">Таксопарк *</label>
            {' '}
            <input aria-label="Поиск парка" autoComplete="off" className={`form-control form-control-sm mb-1${ui.t('ya_park_search')}`}
                placeholder="Поиск парка" type="search" value={query}
                onChange={(event) => {
                    setQuery(event.target.value);
                    ui.touch('ya_park_search', null, { query: event.target.value });
                }}
                onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                        event.preventDefault();
                        pick(found.find((park) => !YA_PARKS_CLOSED.includes(park)));
                    }
                    if (event.key === 'Escape' && open) { event.preventDefault(); setQuery(''); }
                }}
            />
            <select className={`form-control${ui.t('ya_park')}`} id="appeal-park" name="park_id" ref={selectRef}
                value={value} onChange={(event) => pick(event.target.value)}>
                {YA_PARKS.map((park) => (
                    <option key={park} value={park} disabled={YA_PARKS_CLOSED.includes(park)}>{park}</option>
                ))}
            </select>
            <select className="select-search-results form-control" tabIndex={-1}
                size={Math.min(Math.max(found.length || 1, 2), 8)}
                style={open ? { left: place?.left, top: place?.top, minWidth: place?.minWidth } : { display: 'none' }}
                value="" onChange={(event) => pick(event.target.value)}>
                {found.length ? found.map((park) => (
                    <option key={park} value={park} disabled={YA_PARKS_CLOSED.includes(park)}
                        onMouseUp={() => pick(park)}>{park}</option>
                )) : <option value="" disabled>— ничего не найдено —</option>}
            </select>
            {' '}
            <small className="text-muted">Обращение уйдёт от кабинета этого парка.</small>
        </div>
    );
};

/** Новое обращение из формы — то, что уйдёт в список попытки. */
export const yaAppealFrom = (world, form) => {
    const id = 14830 + (world.yaAppeals || []).length + 1;
    return {
        id,
        topic: [form.topics[0], form.topics[form.topics.length - 1]],
        path: form.topics,
        text: (form.values || {}).message || '',
        values: form.values || {},
        files: form.files || [],
        restricted: form.restricted,
        status: 'in_work',
        author: 'Стажёр',
        park: form.park,
        createdAt: world.now.slice(0, 16),
        unread: false,
    };
};

/** Страница «Новое обращение в поддержку Яндекса». */
export const YaCreate = ({ ui }) => {
    const { world } = ui;
    const form = world.yaForm || emptyYaForm();
    const [check, setCheck] = useState('');

    /* Уровни тем: первый всегда, следующий — пока выбранная тема раскрывается. */
    const levels = [];
    for (let i = 0; i <= form.topics.length; i += 1) {
        const options = topicChildren(form.topics.slice(0, i));
        if (!options.length || (i > 0 && topicAt(form.topics.slice(0, i))?.type !== 'nested')) break;
        levels.push(options);
    }
    const topic = topicAt(form.topics);
    const missing = yaMissing(form);

    const pickTopic = (level, index) => {
        const options = levels[level] || [];
        const node = options[index];
        const topics = node ? [...form.topics.slice(0, level), node.title] : form.topics.slice(0, level);
        ui.pick(`ya_topic_${level}`, { yaForm: { ...form, topics, values: {}, files: [] } }, { level, topics });
    };

    const send = () => {
        if (missing.length) {
            setCheck('Заполните поля со звёздочкой.');
            return;
        }
        setCheck('');
        const appeal = yaAppealFrom(world, form);
        ui.act('ya_send', {
            yaAppeals: [...(world.yaAppeals || []), appeal],
            yaForm: emptyYaForm(),
            yaOpen: appeal.id,
            crmView: 'ya_show',
            crmNote: { title: 'Готово', text: 'Учебное обращение создано: в Яндекс ничего не ушло', type: 'success' },
        }, { form });
        if (ui.emit) ui.emit('crm.ya_send', { topic: form.topics.join(' › ') });
    };

    return (
        <div>
            <div className="mb-3">
                <a className={`btn btn-sm btn-link pl-0${ui.t('ya_back')}`} href="#" onClick={(e) => { noNav(e); ui.go('ya_list', {}, 'ya_back'); }}>← Все обращения</a>
            </div>
            {' '}
            <div className="row">
                <div className="col-lg-8">
                    <div className="card">
                        <div className="card-header"><i className="icon-earphones-alt" /> Новое обращение в поддержку Яндекса</div>
                        <div className="card-body">
                            <form id="appeal-create-form" method="post" encType="multipart/form-data" noValidate
                                onSubmit={(event) => { event.preventDefault(); send(); }}>
                                <ParkPicker ui={ui} value={form.park}
                                    onPick={(park) => ui.pick('ya_park', { yaForm: { ...form, park, topics: [], values: {}, files: [] } }, { park })} />
                                {' '}
                                <div className="form-group">
                                    <label className="d-block">Тип</label>
                                    {' '}
                                    <div>Текстовое обращение <span className="text-muted">— ответ примерно за 5 минут</span></div>
                                    {' '}
                                    <small className="text-muted">
                                        {' '}Обратный звонок пока недоступен: у учётки CRM в Диспетчерской нет прав на номера для звонка.{' '}
                                    </small>
                                </div>
                                {' '}
                                <div className="form-group">
                                    <input id="appeal-restricted" name="restricted" type="checkbox" value="1" checked={form.restricted}
                                        className={ui.t('ya_restricted').trim() || undefined}
                                        onChange={() => ui.touch('ya_restricted', { yaForm: { ...form, restricted: !form.restricted } })} />
                                    {' '}
                                    <label htmlFor="appeal-restricted">Доступ: мне и моей роли</label>
                                    {' '}
                                    <small className="text-muted d-block">
                                        {' '}Как в Диспетчерской: с отметкой обращение там видит только учётка CRM, без неё — все учётки
                                        парка. В CRM обращение видите вы, коллеги по роли и руководители — в любом случае.{' '}
                                    </small>
                                </div>
                                {' '}
                                <div className="form-group">
                                    <label className="d-block">Email</label>
                                    {' '}
                                    <span className="text-muted" id="appeal-email">{YA_EMAIL}</span>
                                    {' '}
                                    <small className="text-muted d-block">Подставляется сам: почта учётки CRM в Диспетчерской.</small>
                                </div>
                                {' '}
                                <div id="appeal-topics">
                                    {levels.map((options, level) => {
                                        const chosen = form.topics[level];
                                        const index = options.findIndex((node) => node.title === chosen);
                                        return (
                                            <div className="form-group" key={`topic${level}`}>
                                                <label>{level === 0 ? 'Тема *' : 'Подтема *'}</label>
                                                <select className={`form-control${ui.t(`ya_topic_${level}`)}`} data-topic-level={String(level)}
                                                    value={index < 0 ? '' : String(index)}
                                                    onChange={(event) => pickTopic(level, event.target.value === '' ? -1 : Number(event.target.value))}>
                                                    <option value="">{level === 0 ? 'Выберите тему' : 'Выберите подтему'}</option>
                                                    {options.map((node, i) => <option key={node.title} value={String(i)}>{node.title}</option>)}
                                                </select>
                                            </div>
                                        );
                                    })}
                                </div>
                                {' '}
                                <div className={`alert alert-info${topic && topic.type === 'link' ? '' : ' d-none'}`} id="appeal-link"
                                    style={{ whiteSpace: 'pre-line' }}>
                                    {topic && topic.type === 'link'
                                        ? (topic.link?.length ? <Parts parts={topic.link} />
                                            : 'По этой теме обращение не создают — ответ в базе знаний Диспетчерской.')
                                        : null}
                                </div>
                                {' '}
                                <div id="appeal-fields">
                                    {topicInputs(topic).map((input, index) => (
                                        <TopicInput key={`${form.topics.join('/')}-${index}`} ui={ui} input={input} form={form} />
                                    ))}
                                </div>
                                {' '}
                                <div className={`alert alert-danger${check ? '' : ' d-none'}`} id="appeal-create-check">{check}</div>
                                {' '}
                                <button className={`btn btn-primary${ui.t('ya_send')}`} disabled={missing.length > 0} id="appeal-create-submit" type="submit">Отправить</button>
                                {' '}
                                <small className="text-muted ml-2">Кнопка станет активной, когда заполнены все поля со звёздочкой.</small>
                            </form>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
};

/** Обращение: поля слева, переписка и ответ поддержке справа. */
export const YaShow = ({ ui }) => {
    const { world } = ui;
    const [reply, setReply] = useState('');
    const [files, setFiles] = useState([]);
    const [check, setCheck] = useState('');
    const [fileKey, setFileKey] = useState(0);
    const appeal = allAppeals(world).find((item) => item.id === world.yaOpen);

    const back = (
        <a className={`btn btn-sm btn-link pl-0${ui.t('ya_back')}`} href="#" onClick={(e) => { noNav(e); ui.go('ya_list', {}, 'ya_back'); }}>← Все обращения</a>
    );
    if (!appeal) {
        return <div><div className="mb-3">{back}</div><p className="text-muted">Обращение не найдено.</p></div>;
    }

    const status = yaStatus(appeal.status);
    const [park, city] = splitPark(appeal.park);
    const created = appealCreated(world, appeal);
    const updated = appealUpdated(world, appeal);
    const leafTopic = topicAt(appeal.path || appeal.topic);
    const extraFields = topicInputs(leafTopic)
        .filter((input) => !['label', 'textarea', 'file'].includes(input.kind) && (appeal.values || {})[input.name])
        .map((input) => [input.title, Array.isArray(appeal.values[input.name])
            ? appeal.values[input.name].join(' — ') : appeal.values[input.name]]);
    const thread = [
        { from: 'crm', who: appeal.author, at: created, text: appealText(appeal), files: appeal.files || [] },
        ...(appeal.reply ? [{ from: 'support', who: 'Поддержка Яндекса', at: updated, text: appeal.reply, files: [] }] : []),
        ...((world.yaThreads || {})[appeal.id] || []),
    ];

    const send = () => {
        const text = reply.trim();
        if (!text && !files.length) {
            setCheck('Напишите текст ответа или приложите файлы.');
            return;
        }
        setCheck('');
        const message = { from: 'crm', who: 'Стажёр', at: world.now.slice(0, 16), text, files };
        ui.act('ya_reply_send', {
            yaThreads: { ...(world.yaThreads || {}), [appeal.id]: [...((world.yaThreads || {})[appeal.id] || []), message] },
            crmNote: { title: 'Готово', text: 'Учебный ответ: в Яндекс ничего не ушло', type: 'success' },
        }, { id: appeal.id, text, files });
        setReply('');
        setFiles([]);
        setFileKey((n) => n + 1);
        if (ui.emit) ui.emit('crm.ya_reply', { id: appeal.id });
    };

    return (
        <div>
            <div className="mb-3">
                {back}
                {' '}
                <a className={`btn btn-sm btn-link float-right${ui.t('ya_fleet')}`} href="#" rel="noopener noreferrer" target="_blank"
                    onClick={(e) => {
                        noNav(e);
                        ui.act('ya_fleet', {
                            crmNote: {
                                title: 'Учебная среда',
                                text: 'Диспетчерская в тренажёре не открывается: переписка обращения — здесь, справа.',
                                type: 'warn',
                            },
                        });
                    }}>
                    {' '}Открыть в Диспетчерской{' '}
                </a>
            </div>
            {' '}
            <div className="row">
                <div className="col-lg-5">
                    <div className="card">
                        <div className="card-header">
                            <i className="icon-earphones-alt" /> Обращение{' '}
                            <span className={`badge badge-${status.tone} float-right`}>{status.label}</span>
                        </div>
                        <div className="card-body">
                            <table className="table table-sm mb-0">
                                <tbody>
                                    <tr><th style={{ width: '40%' }}>Тип</th><td>Текстовое обращение</td></tr>
                                    <tr><th>Тема</th><td>{appeal.topic[0]}</td></tr>
                                    <tr><th>Подтема</th><td>{appeal.topic[appeal.topic.length - 1]}</td></tr>
                                    <tr><th>Email</th><td style={{ wordBreak: 'break-word' }}>{YA_EMAIL}</td></tr>
                                    {extraFields.map(([title, value]) => (
                                        <tr key={title}><th>{title}</th><td style={{ wordBreak: 'break-word' }}>{value}</td></tr>
                                    ))}
                                    <tr><th>Автор</th><td> {appeal.author} <small className="text-muted d-block">{YA_EMAIL}</small></td></tr>
                                    <tr><th>Таксопарк</th><td> {park} <small className="text-muted">({city})</small></td></tr>
                                    <tr><th>Создано</th><td>{created}</td></tr>
                                    <tr><th>Обновлено</th><td>{updated}</td></tr>
                                    <tr><th>Доступ в Диспетчерской</th><td>{appeal.restricted ? 'Только учётке CRM' : 'Всем учёткам парка'}</td></tr>
                                    <tr><th>ID в Диспетчерской</th><td><small>{fakeHex(appeal.id)}</small></td></tr>
                                </tbody>
                            </table>
                        </div>
                    </div>
                    {' '}
                    <div className="card">
                        <div className="card-header"><i className="fa fa-history" /> История статусов</div>
                        <div className="card-body">
                            <p className="text-muted mb-0">Смен статуса пока не было.</p>
                            {' '}
                            <small className="text-muted d-block mt-2">
                                {' '}Яндекс историю статусов не отдаёт: здесь смены, которые CRM заметила, начиная с {created}.
                                Время — последнего изменения обращения в Яндексе.{' '}
                            </small>
                        </div>
                    </div>
                </div>
                {' '}
                <div className="col-lg-7">
                    <div className="card">
                        <div className="card-header"><i className="icon-bubbles" /> Переписка</div>
                        <div className="card-body">
                            {thread.map((message, index) => (
                                <div key={index} className={`d-flex mb-3${message.from === 'crm' ? ' justify-content-end' : ''}`}>
                                    <div className="p-2 rounded"
                                        style={{ maxWidth: '85%', background: message.from === 'crm' ? '#e3f2fd' : '#f0f3f5' }}>
                                        <div className="small text-muted mb-1">
                                            <strong>{message.who}</strong>{message.from === 'crm' ? ' · из CRM' : ''} · {message.at}{' '}
                                        </div>
                                        {' '}
                                        {message.text ? <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{message.text}</div> : null}
                                        {message.files && message.files.length ? (
                                            <div className="small mt-1">
                                                {message.files.map((name) => (
                                                    <div key={name}><i className="fa fa-paperclip" /> {name}</div>
                                                ))}
                                            </div>
                                        ) : null}
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                    {' '}
                    <div className="card" id="reply">
                        <div className="card-header"><i className="fa fa-reply" /> Ответ поддержке</div>
                        <div className="card-body">
                            <form id="appeal-reply-form" method="post" encType="multipart/form-data" noValidate
                                onSubmit={(event) => { event.preventDefault(); send(); }}>
                                <div className="form-group">
                                    <textarea className={`form-control${ui.t('ya_reply_text')}`} maxLength={10000} name="text"
                                        placeholder="Текст ответа поддержке Яндекса" rows={4} value={reply}
                                        onChange={(event) => {
                                            setReply(event.target.value);
                                            ui.touch('ya_reply_text', null, { text: event.target.value });
                                        }} />
                                </div>
                                {' '}
                                <div className="form-group">
                                    <input key={fileKey} className={`form-control-file${ui.t('ya_reply_files')}`} multiple name="files[]" type="file"
                                        onChange={(event) => {
                                            const names = Array.from(event.target.files || []).map((file) => file.name);
                                            setFiles(names);
                                            ui.touch('ya_reply_files', null, { files: names });
                                        }} />
                                    {' '}
                                    <small className="text-muted d-block mt-1">
                                        {' '}До 8 файлов, каждый до 20 МБ, все вместе — до 30 МБ. Файлы уходят отдельным сообщением, как в Диспетчерской.{' '}
                                    </small>
                                </div>
                                {' '}
                                <div className={`alert alert-danger${check ? '' : ' d-none'}`} id="appeal-reply-check">{check}</div>
                                {' '}
                                <button className={`btn btn-primary${ui.t('ya_reply_send')}`} type="submit">Отправить</button>
                                {' '}
                                <small className="text-muted ml-2">
                                    {' '}В Яндекс ответ уйдёт от учётки CRM в Диспетчерской; в CRM будет видно, что отвечали вы.{' '}
                                </small>
                            </form>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
};
