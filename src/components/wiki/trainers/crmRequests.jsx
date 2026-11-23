import React, { useEffect, useRef, useState } from 'react';

import {
    PARK_CITIES, PARKS, SOURCES, categoryChildren, chainIds, pathLabel, resolveChain, searchLeaves,
} from './crmCatalog.js';
import {
    canSave, crmFormFields, emptyForm, formLeaf, phoneHistory, sameCategoryTickets, withCategory,
} from './crmModel.js';
import { Multiselect, SearchSelect, agoIso, noNav, nowToIso } from './crmKit';

/* «Тикетная система → Обращения»: форма «Создать обращение» и список.
 *
 * Разметка — рабочей CRM 20.11.2026 (Vue-компонент list-request-form) строка в
 * строку: form-group row, has-success у принятых полей, vue-multiselect у
 * категорий, рамка конечной категории, large-checkbox. Порядок и поведение
 * тоже как в CRM: «Город» после парка, следующая категория после выбора,
 * поля категории, файлы, тикет и акция — после конечной; «Сохранить» — только
 * с конечной категорией; «Дублировать обращение» очищает форму под следующее
 * обращение того же звонка, без него CRM уходит на список.
 */

/* Акции парка: в CRM список приходит по выбранному парку. Учебные названия. */
const PROMOTIONS = [{ id: 7, title: 'Бонус за первые заказы' }, { id: 8, title: 'Возвращение в парк' }];

/* Поле ввода. Значение держится и локально (чтобы буквы появлялись сразу), и в
   мире: локальный стейт — против дёрганья курсора. Сменили значение в мире
   СНАРУЖИ («Дублировать обращение» очистило номер) — поле показывает новое. */
const useDraft = (value) => {
    const [draft, setDraft] = useState(value || '');
    const sent = useRef(value || '');
    useEffect(() => {
        if ((value || '') !== sent.current) {
            sent.current = value || '';
            setDraft(value || '');
        }
    }, [value]);
    return [draft, (next) => { sent.current = next; setDraft(next); }];
};

const Input = ({ id, value, placeholder, onChange, className = '' }) => {
    const [draft, setDraft] = useDraft(value);
    return (
        <input
            type="text"
            id={id}
            name={id}
            placeholder={placeholder}
            aria-required="true"
            aria-invalid="false"
            className={`form-control${draft ? ' form-control-success' : ''}${className}`}
            value={draft}
            onChange={(event) => { setDraft(event.target.value); onChange(event.target.value); }}
        />
    );
};

const Row = ({ label, htmlFor, ok = false, children }) => (
    <div className={`form-group row align-items-center${ok ? ' has-success' : ''}`}>
        <label className="col-form-label text-md-right col-md-2" htmlFor={htmlFor}>{label}</label>
        <div className="col-md-9 col-xl-8">{children}</div>
    </div>
);

/* Пустой <select> с v-model, которому нет пункта, Vue показывает пустым; у
   браузера без этого была бы видна первая строка. */
const Blank = () => <option value="" hidden aria-hidden="true" />;

/** Область «вставьте сюда изображения» — вставка из буфера, как в CRM. */
const PasteArea = ({ images, onAdd, onRemove }) => {
    const [active, setActive] = useState(false);
    const ref = useRef(null);
    useEffect(() => {
        if (!active) return undefined;
        const doc = ref.current.ownerDocument;
        const away = (event) => { if (ref.current && !ref.current.contains(event.target)) setActive(false); };
        doc.addEventListener('click', away);
        return () => doc.removeEventListener('click', away);
    }, [active]);
    return (
        <div
            ref={ref}
            className={`upload-area${active ? ' active' : ''}`}
            tabIndex={-1}
            onClick={() => setActive(true)}
            onPaste={(event) => {
                const files = [...(event.clipboardData?.items || [])]
                    .filter((item) => item.type.includes('image'))
                    .map((item) => item.getAsFile())
                    .filter(Boolean);
                /* Учебная среда: картинка никуда не загружается — остаётся
                   предпросмотром в этой вкладке. */
                files.forEach((file) => onAdd(URL.createObjectURL(file)));
            }}
        >
            <p>Нажмите и вставьте сюда изображения с весом не более 2МБ (Ctrl+V)</p>
            {images.length ? (
                <div className="image-container">
                    {images.map((url, index) => (
                        <div className="image-preview" key={url}>
                            <img src={url} alt="Загруженное изображение" />
                            <span className="remove-image" onClick={(event) => { event.stopPropagation(); onRemove(index); }}>✖</span>
                        </div>
                    ))}
                </div>
            ) : null}
        </div>
    );
};

const FieldArea = ({ name, value, onChange, className = '' }) => {
    const [draft, setDraft] = useDraft(value);
    return (
        <textarea aria-invalid="false" aria-required="true" className={`form-control${className}`} name={name} value={draft}
            onChange={(event) => { setDraft(event.target.value); onChange(event.target.value); }} />
    );
};

const FieldInput = ({ type, id, name, placeholder, value, onChange }) => {
    const [draft, setDraft] = useDraft(value);
    return (
        <input className="form-control" type={type} id={id} name={name} placeholder={placeholder} value={draft}
            onChange={(event) => { setDraft(event.target.value); onChange(event.target.value); }} />
    );
};

/** Страница «Создать обращение». */
export const RequestCreate = ({ ui }) => {
    const { world } = ui;
    const form = world.form;
    const set = (patch) => ui.set({ form: { ...form, ...patch } });
    const leaf = formLeaf(form);
    const history = phoneHistory(world, form.phone);
    const same = sameCategoryTickets(world, form);
    const chain = resolveChain(form.cats);
    const cities = form.park ? PARK_CITIES[form.park] || [] : [];

    /* Уровни категорий: всегда на один больше выбранного, пока выбранное не
       конечное, — так ведёт себя CRM. Выбор — НОМЕРА узлов. */
    const levels = [];
    for (let i = 0; i <= chain.length; i += 1) {
        const parent = i === 0 ? null : chain[i - 1];
        if (parent && parent.leaf) break;
        const options = categoryChildren(parent ? parent.id : 0);
        if (!options.length) break;
        levels.push(options);
    }

    /* Конечная категория — выбор с проверкой урока (ui.pick), промежуточная —
       просто шаг по дереву. */
    const pickCats = (cats) => {
        const next = withCategory(form, cats);
        if (formLeaf(next)) ui.pick('lr_leaf', { form: next });
        else ui.set({ form: next });
    };

    const save = () => {
        if (!canSave(form)) return;
        const count = (world.crmRequests || []).length;
        const saved = {
            ...form,
            id: 1829800 + count + 1,
            at: world.now,
            ticketNo: leaf.ticket && !form.linked && form.ticket !== false ? 41300 + count + 1 : (form.linked || null),
        };
        const requests = [...(world.crmRequests || []), saved];
        const patch = form.duplicate
            ? {
                /* «Дублировать»: CRM оставляет источник, парк и город и
                   очищает остальное — следующее обращение того же звонка. */
                crmRequests: requests,
                form: { ...form, phone: '', license: '', account: '', cats: [], info: {}, files: [], images: [], linked: '', promotion: '' },
                crmNote: { title: 'Готово', text: 'Обращение создано', type: 'success' },
            }
            : {
                /* Без «Дублировать» CRM уходит на список, а «Создать
                   обращение» там — новая чистая страница. */
                crmRequests: requests,
                form: emptyForm(world.case?.crm?.prefill),
                crmView: 'requests',
                crmGroups: [...new Set([...(world.crmGroups || []), 'tickets'])],
            };
        ui.save('lr_save', patch);
    };

    return (
        <div className="container-xl">
            <div className="card">
                <form className="form-horizontal form-create" method="post" noValidate
                    onSubmit={(event) => { event.preventDefault(); save(); }}>
                    <nav aria-label="breadcrumb">
                        <ol className="breadcrumb">
                            <li className="breadcrumb-item"><a href="#" onClick={(e) => { noNav(e); ui.crumb('home'); }}>Главная</a></li>
                            <li className="breadcrumb-item"><a href="#" className={ui.t('crumb_requests').trim() || undefined}
                                onClick={(e) => { noNav(e); ui.go('requests', {}, 'crumb_requests'); }}>Обращения</a></li>
                            <li className="breadcrumb-item active" aria-current="page">Создать обращение</li>
                        </ol>
                    </nav>
                    <div className="card-header d-flex justify-content-between align-items-center">
                        <div><i className="fa fa-plus" /> Создать обращение</div>
                    </div>
                    <div className="card-body">
                        <Row label="Звонок/Чат" htmlFor="request_from" ok={Boolean(form.source)}>
                            <select
                                name="request_from"
                                id="request_from"
                                aria-required="true"
                                aria-invalid="false"
                                className={`form-control${form.source ? ' form-control-success' : ''}${ui.t('lr_source')}`}
                                value={form.source}
                                onChange={(event) => ui.pick('lr_source', { form: { ...form, source: event.target.value } })}
                            >
                                {form.source ? null : <Blank />}
                                {SOURCES.map((source) => <option key={source} value={source}>{source}</option>)}
                            </select>
                        </Row>

                        <Row label="Номер телефона" htmlFor="phone" ok={Boolean(form.phone)}>
                            <Input id="phone" value={form.phone} placeholder="Номер телефона" className={ui.t('lr_phone')}
                                onChange={(value) => ui.touch('lr_phone', { form: { ...form, phone: value, linked: '' } })} />
                            {history.requests.length || history.open_tickets.length ? (
                                <div className="small mt-2 p-2 bg-light border rounded">
                                    {history.open_tickets.length ? (
                                        <div>
                                            <strong>Открытые тикеты по этому номеру:</strong>
                                            {history.open_tickets.map((ticket) => (
                                                <div key={`t${ticket.id}`}>
                                                    <a href="#" target="_blank" className={ui.t('lr_history_ticket').trim() || undefined}
                                                        onClick={(e) => { noNav(e); ui.act('lr_history_ticket'); }}>№{ticket.id}</a>
                                                    {' '}— {ticket.category}, {ticket.status}, {ticket.when}
                                                </div>
                                            ))}
                                        </div>
                                    ) : null}
                                    {history.requests.length ? (
                                        <div className="mt-1">
                                            <strong>Последние обращения:</strong>
                                            {history.requests.map((item) => (
                                                <div key={`r${item.id}`}>
                                                    <a href="#" target="_blank" onClick={noNav}>№{item.id}</a>
                                                    {' '}— {item.category}, {item.when}
                                                </div>
                                            ))}
                                        </div>
                                    ) : null}
                                </div>
                            ) : null}
                        </Row>

                        <Row label="Номер В/У" htmlFor="driver_license" ok={Boolean(form.license)}>
                            <Input id="driver_license" value={form.license} placeholder="Номер В/У" className={ui.t('lr_license')}
                                onChange={(value) => ui.touch('lr_license', { form: { ...form, license: value } })} />
                        </Row>

                        <Row label="ID водителя" htmlFor="account_id" ok={Boolean(form.account)}>
                            <Input id="account_id" value={form.account} placeholder="ID водителя"
                                onChange={(value) => ui.touch('lr_account', { form: { ...form, account: value } })} />
                        </Row>

                        <Row label="Дата обращения" htmlFor="request_date">
                            <div className="input-group input-group--custom">
                                <div className="input-group-addon"><i className="fa fa-calendar" /></div>
                                <input className="form-control input flatpickr flatpickr-input" type="hidden" id="request_date"
                                    name="request_date" placeholder="Выберите дату и время" data-input="" value={nowToIso(world.now)} readOnly />
                                <input className="form-control input flatpickr flatpickr-input form-control input"
                                    placeholder="Выберите дату и время" type="text" readOnly value={world.now} />
                            </div>
                        </Row>

                        <Row label="Таксопарк" htmlFor="park_id" ok={Boolean(form.park)}>
                            <SearchSelect
                                id="park_id"
                                value={form.park}
                                placeholder="Таксопарк"
                                target={Boolean(ui.t('lr_park', 'lr_park_city'))}
                                options={PARKS.map((park) => ({ value: park, label: park }))}
                                onSelect={(park) => ui.pick('lr_park_city', { form: { ...form, park, city: '', promotion: '' } })}
                            />
                        </Row>

                        {/* «Города» нет, пока у парка нет городов, — так устроена CRM. */}
                        {cities.length ? (
                            <Row label="Город" htmlFor="city_id" ok={Boolean(form.city)}>
                                <select
                                    name="city_id"
                                    id="city_id"
                                    aria-required="true"
                                    aria-invalid="false"
                                    className={`form-control${form.city ? ' form-control-success' : ''}${ui.t('lr_city', 'lr_park_city')}`}
                                    value={form.city}
                                    onChange={(event) => ui.pick('lr_park_city', { form: { ...form, city: event.target.value } })}
                                >
                                    {form.city ? null : <Blank />}
                                    {cities.map((city) => <option key={city} value={city}>{city}</option>)}
                                </select>
                            </Row>
                        ) : null}

                        <div className="form-group row align-items-center">
                            <label className="col-form-label text-md-right col-md-2" htmlFor="category-search">Поиск категории</label>
                            <div className="col-md-9 col-xl-8">
                                <Multiselect
                                    id="category-search"
                                    value={null}
                                    options={[]}
                                    placeholder="Начните вводить название категории"
                                    target={Boolean(ui.t('lr_cat_search', 'lr_leaf'))}
                                    filter={(query) => searchLeaves(query).map((node) => ({ value: node.id, label: pathLabel(node) }))}
                                    onSelect={(id) => {
                                        const node = searchLeaves('').find((item) => item.id === id);
                                        if (node) pickCats(chainIds(node));
                                    }}
                                />
                            </div>
                        </div>

                        {levels.map((options, level) => (
                            <div className="form-group row align-items-center" key={`cat${level}`}>
                                <label className="col-form-label text-md-right col-md-2" htmlFor={`category-select-${level}`}>
                                    Категория {level + 1}
                                </label>
                                <div className="col-md-9 col-xl-8">
                                    <Multiselect
                                        id={`category-select-${level}`}
                                        value={chain[level] ? chain[level].id : null}
                                        placeholder="Выберите категорию"
                                        options={options.map((node) => ({ value: node.id, label: node.title }))}
                                        onSelect={(id) => pickCats([...chain.slice(0, level).map((node) => node.id), id])}
                                    />
                                </div>
                            </div>
                        ))}

                        <div className="form-group row">
                            <div className="ml-md-auto col-md-10">
                                {leaf ? (
                                    <div className="p-2 border rounded bg-light">
                                        <div><strong>{pathLabel(leaf)}</strong></div>
                                        <div>
                                            {leaf.ticket ? (
                                                <span>
                                                    {' '}Тикет уйдёт в отдел:{' '}
                                                    {leaf.executors.length ? <span>{leaf.executors.join(', ')}</span>
                                                        : (!leaf.directUsers ? <span className="text-danger">отдел не назначен</span> : null)}
                                                </span>
                                            ) : <span className="text-muted">Без тикета — обращение только для учёта</span>}
                                        </div>
                                        {Object.entries(leaf.schedules || {}).map(([department, schedule]) => (
                                            <div className="small text-muted" key={department}>{department}: {schedule}</div>
                                        ))}
                                        {leaf.ticket && (leaf.slaTake || leaf.slaProcess) ? (
                                            <div className="small text-muted">
                                                SLA:
                                                {leaf.slaTake ? <span> взять за {leaf.slaTake} ч</span> : null}
                                                {leaf.slaTake && leaf.slaProcess ? <span>,</span> : null}
                                                {leaf.slaProcess ? <span> обработать за {leaf.slaProcess} ч</span> : null}
                                            </div>
                                        ) : null}
                                        <a className={`small${ui.t('lr_instruction')}`} href="#" target="_blank"
                                            onClick={(e) => { noNav(e); ui.act('lr_instruction'); }}>
                                            Инструкция по созданию обращения
                                        </a>
                                    </div>
                                ) : (
                                    <div className="small text-muted">Выберите конечную категорию — без неё обращение не сохранится</div>
                                )}
                            </div>
                        </div>

                        {leaf && leaf.ticket && same.length ? (
                            <div className="form-group row">
                                <div className="ml-md-auto col-md-10">
                                    <div className={`p-2 border border-warning rounded${ui.t('lr_link')}`}>
                                        <strong>По этому номеру уже есть открытый тикет в этой категории</strong>
                                        <div className="form-check">
                                            <input className="form-check-input" type="radio" id="linked-none" value=""
                                                checked={!form.linked}
                                                onChange={() => ui.pick('lr_link', { form: { ...form, linked: '' } })} />
                                            <label className="form-check-label" htmlFor="linked-none">Создать новый тикет</label>
                                        </div>
                                        {same.map((ticket) => (
                                            <div className="form-check" key={`l${ticket.id}`}>
                                                <input type="radio" className="form-check-input" id={`linked-${ticket.id}`} value={ticket.id}
                                                    checked={form.linked === ticket.id}
                                                    onChange={() => ui.pick('lr_link', { form: { ...form, linked: ticket.id } })} />
                                                <label className="form-check-label" htmlFor={`linked-${ticket.id}`}>
                                                    Присоединить к тикету №{ticket.id} ({ticket.status}) — новый тикет не создастся, в
                                                    существующий придёт заметка
                                                </label>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            </div>
                        ) : null}

                        {leaf && leaf.promotion && form.park ? (
                            <Row label="Акция" htmlFor="promotion_id" ok={Boolean(form.promotion)}>
                                <select name="promotion_id" id="promotion_id" aria-required="true" aria-invalid="false"
                                    className={`form-control${form.promotion ? ' form-control-success' : ''}`}
                                    value={form.promotion}
                                    onChange={(event) => set({ promotion: event.target.value })}>
                                    {form.promotion ? null : <Blank />}
                                    {PROMOTIONS.map((item) => <option key={item.id} value={item.title}>{item.title}</option>)}
                                </select>
                            </Row>
                        ) : null}

                        {(leaf ? leaf.fields : []).map((field, index) => (
                            <div className="form-group row align-items-center" key={`${leaf.id}-${field.title}`}>
                                <label className="col-form-label text-md-right col-md-2" htmlFor={`${field.type}-${index}`}>
                                    {' '}{field.title}
                                    {field.required === false ? <span className="text-muted"> (необязательно)</span> : null}
                                </label>
                                <div className="col-md-9 col-xl-8">
                                    {field.type === 'textarea' ? (
                                        <FieldArea name={`add_info[${index}].value`} value={(form.info || {})[field.title] || ''}
                                            className={ui.t('lr_info')}
                                            onChange={(value) => ui.touch('lr_info', { form: { ...form, info: { ...(form.info || {}), [field.title]: value } } })} />
                                    ) : (
                                        <FieldInput type={field.type} id={`${field.type}-${index}`} name={`add_info[${index}].value`}
                                            placeholder={field.title} value={(form.info || {})[field.title] || ''}
                                            onChange={(value) => ui.touch('lr_info', { form: { ...form, info: { ...(form.info || {}), [field.title]: value } } })} />
                                    )}
                                </div>
                            </div>
                        ))}

                        {leaf && leaf.file ? (
                            <>
                                <div className="form-group row align-items-center">
                                    <label className="col-form-label text-md-right col-md-2" htmlFor="files"> Загрузить файлы </label>
                                    <div className="col-md-9 col-xl-8">
                                        <input aria-invalid="false" aria-required="true" className={`form-control${ui.t('lr_files')}`}
                                            id="files" multiple name="files" placeholder="Загрузить файлы" type="file"
                                            onChange={(event) => {
                                                /* Учебная среда: файл не загружается — остаётся только имя. */
                                                const names = [...(event.target.files || [])].map((file) => file.name).slice(0, 8);
                                                ui.touch('lr_files', { form: { ...form, files: names } });
                                            }} />
                                    </div>
                                </div>
                                <div className="form-group row align-items-center">
                                    <label className="col-form-label text-md-right col-md-2" htmlFor="image" />
                                    <div className="col-md-9 col-xl-8">
                                        <PasteArea images={form.images || []}
                                            onAdd={(url) => set({ images: [...(form.images || []), url] })}
                                            onRemove={(index) => set({ images: (form.images || []).filter((_, n) => n !== index) })} />
                                    </div>
                                </div>
                            </>
                        ) : null}

                        {leaf && leaf.ticket && !form.linked ? (
                            <div className="form-group row large-checkbox has-success">
                                <div className="ml-md-auto col-md-10">
                                    <input aria-invalid="false" aria-required="false" className="form-check-input" data-vv-name="ticket_available"
                                        id="ticket_available" name="ticket_available_fake_element" type="checkbox"
                                        checked={form.ticket !== false}
                                        onChange={() => ui.touch('lr_ticket', { form: { ...form, ticket: form.ticket === false } })} />
                                    <label className="form-check-label" htmlFor="ticket_available"> Формировать тикет </label>
                                    <input name="ticket_available" type="hidden" value={form.ticket !== false ? 1 : 0} />
                                </div>
                            </div>
                        ) : null}

                        <div className="form-group row large-checkbox">
                            <div className="ml-md-auto col-md-10">
                                <input aria-invalid="false" aria-required="false" className="form-check-input" data-vv-name="duplicate_request"
                                    id="duplicate_request" name="duplicate_request_fake_element" type="checkbox"
                                    checked={Boolean(form.duplicate)}
                                    onChange={() => ui.touch('lr_duplicate', { form: { ...form, duplicate: !form.duplicate } })} />
                                <label className={`form-check-label${ui.t('lr_duplicate')}`} htmlFor="duplicate_request"> Дублировать обращение </label>
                                <input name="duplicate_request" type="hidden" value={form.duplicate ? 1 : 0} />
                            </div>
                        </div>
                    </div>
                    <div className="card-footer">
                        <button type="submit" className={`btn btn-primary${ui.t('lr_save')}`} disabled={!canSave(form)}>
                            <i className="fa fa-download" /> Сохранить
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
};

/* Учебные строки списка: номера, операторы и телефоны выдуманы. */
/* Обращения, к которым водители учебной CRM отношения не имеют: посторонние
   и пассажирские звонки, чаты — чтобы список выглядел как список смены. */
const SAMPLE_REQUESTS = [
    ['Учебный оператор 1', 'Звонок', '77000000001', '', 'iTaxi', 'Алматы', 'Консультация', 'Консультация по Тарифам', 14, null],
    ['Учебный оператор 2', 'Whatsapp', '77000000003', 'УЧ000003', 'Регионы', 'Алматы', 'Консультация', 'Консультация по Акциям таксопарка', 19, null],
    ['Учебный оператор 2', 'Телеграм', '77000000005', 'УЧ000005', 'Аманат', 'Караганда', 'Консультация', 'Консультация по Фотоконтролю', 44, null],
];

/* Строка списка из истории водителя учебной CRM: кто из операторов и когда
   заводил ему обращение. */
const historyRows = (world) => (world.crmCases || []).flatMap((item) => (item.crm?.history?.requests || []).map((request) => {
    const path = request.category.split(' › ');
    return {
        id: request.id,
        at: request.at.replace('T', ' ').slice(0, 19),
        operator: request.operator || 'Оператор',
        source: request.source || 'Звонок',
        phone: item.contractor.phone,
        license: item.contractor.license,
        park: item.park.name,
        city: item.park.city,
        category: `${path[path.length - 2]} - ${path[path.length - 1]}`,
        promotion: '',
        ticket: request.ticket || null,
        fields: [['Звонок/Чат', request.source || 'Звонок'], ['Номер телефона', item.contractor.phone],
            ['Номер В/У', item.contractor.license], ['Таксопарк', item.park.name], ['Город', item.park.city],
            ['Категория', request.category]],
    };
}));

const EMPTY_FILTER = { source: '', ticket: '', search: '', park: null, city: null };

/** Страница «Обращения» — список. */
export const RequestList = ({ ui }) => {
    const { world } = ui;
    const [filtersOpen, setFiltersOpen] = useState(true);
    const [draft, setDraft] = useState(EMPTY_FILTER);
    const [applied, setApplied] = useState(EMPTY_FILTER);

    const own = [...(world.crmRequests || [])].reverse().map((request) => {
        const path = resolveChain(request.cats || []);
        const leaf = path[path.length - 1] || null;
        const parent = path.length > 1 ? path[path.length - 2] : null;
        return {
            id: request.id, at: nowToIso(request.at), operator: 'Стажёр', source: request.source,
            phone: request.phone, license: request.license, park: request.park, city: request.city,
            category: `${parent ? `${parent.title} - ` : ''}${leaf ? leaf.title : ''}`,
            promotion: request.promotion, ticket: request.ticketNo, fields: crmFormFields(request),
        };
    });
    const samples = SAMPLE_REQUESTS.map(([operator, source, phone, license, park, city, parent, title, ago, ticket], index) => ({
        id: 1829790 - index, at: agoIso(world.nowMs, ago), operator, source, phone, license, park, city,
        category: `${parent} - ${title}`, promotion: '', ticket,
        fields: [['Звонок/Чат', source], ['Номер телефона', phone], ['Номер В/У', license], ['Таксопарк', park],
            ['Город', city], ['Категория', `${parent} - ${title}`]].filter(([, value]) => value),
    }));
    const all = [...own, ...[...historyRows(world), ...samples].sort((a, b) => (a.at < b.at ? 1 : -1))];
    const rows = all.filter((row) => {
        if (applied.source && row.source !== applied.source) return false;
        if (applied.ticket === '1' && !row.ticket) return false;
        if (applied.ticket === '0' && row.ticket) return false;
        if (applied.park && row.park !== applied.park) return false;
        if (applied.city && row.city !== applied.city) return false;
        const needle = applied.search.replace(/\s+/g, '').toLowerCase();
        if (needle && ![row.id, row.phone, row.license]
            .some((value) => String(value || '').replace(/\s+/g, '').toLowerCase().includes(needle))) {
            return false;
        }
        return true;
    });
    const parks = [...new Set(all.map((row) => row.park).filter(Boolean))];
    const cities = [...new Set(all.map((row) => row.city).filter(Boolean))];
    const changed = Object.values(applied).filter(Boolean).length;

    return (
        <div className="row">
            <div className="col">
                <div className="card">
                    <nav aria-label="breadcrumb">
                        <ol className="breadcrumb">
                            <li className="breadcrumb-item"><a href="#" onClick={(e) => { noNav(e); ui.crumb('home'); }}>Главная</a></li>
                            <li className="breadcrumb-item active" aria-current="page">Обращения</li>
                        </ol>
                    </nav>
                    <div className="card-header">
                        <i className="fa fa-align-justify" /> Обращения
                        <a className={`btn btn-primary btn-spinner btn-sm pull-right m-b-0${ui.t('lr_create_btn')}`} href="#" role="button"
                            onClick={(e) => { noNav(e); ui.go('request_new', {}, 'lr_create_btn'); }}>
                            <i className="fa fa-plus" /> Создать обращение
                        </a>
                    </div>
                    <div className="card-body">
                        <div className="card-block">
                            <form onSubmit={(event) => {
                                event.preventDefault();
                                setApplied(draft);
                                ui.pick('lr_filter_apply', null, { filter: draft });
                            }}>
                                <div className="d-flex align-items-center mb-2">
                                    <a className="text-primary" href="#" onClick={(e) => { noNav(e); setFiltersOpen(!filtersOpen); }}>
                                        <i className={`fa ${filtersOpen ? 'fa-chevron-down' : 'fa-chevron-right'}`} /> Фильтры
                                    </a>
                                    {changed ? <span className="badge badge-primary ml-2">{changed}</span> : null}
                                    {changed ? (
                                        <a className="text-muted small ml-3" href="#"
                                            onClick={(e) => { noNav(e); setDraft(EMPTY_FILTER); setApplied(EMPTY_FILTER); }}>Сбросить всё</a>
                                    ) : null}
                                </div>
                                <div style={filtersOpen ? undefined : { display: 'none' }}>
                                    <div className="row">
                                        <div className="col-md-2 form-group"><label className="small">Создано с</label> <input className="form-control form-control-sm" type="date" /></div>
                                        <div className="col-md-2 form-group"><label className="small">Создано по</label> <input className="form-control form-control-sm" type="date" /></div>
                                        <div className="col-md-4 form-group">
                                            <label className="small">Категория</label>
                                            <Multiselect id="filter-type" small value={null} placeholder="Все" onSelect={() => {}}
                                                options={categoryChildren(0).map((node) => ({ value: node.id, label: node.title }))} />
                                        </div>
                                        <div className="col-md-4 form-group">
                                            <label className="small">ФИО оператора</label>
                                            <Multiselect id="filter-creator" small value={null} placeholder="Все" onSelect={() => {}}
                                                options={['Стажёр', 'Учебный оператор 1', 'Учебный оператор 2', 'Учебный оператор 3']
                                                    .map((name) => ({ value: name, label: name }))} />
                                        </div>
                                    </div>
                                    <div className="row">
                                        <div className="col-md-3 form-group">
                                            <label className="small">Таксопарк</label>
                                            <Multiselect id="filter-park" small value={draft.park} placeholder="Все"
                                                options={parks.map((park) => ({ value: park, label: park }))}
                                                onSelect={(park) => setDraft({ ...draft, park })} />
                                        </div>
                                        <div className="col-md-3 form-group">
                                            <label className="small">Город</label>
                                            <Multiselect id="filter-city" small value={draft.city} placeholder="Все"
                                                options={cities.map((city) => ({ value: city, label: city }))}
                                                onSelect={(city) => setDraft({ ...draft, city })} />
                                        </div>
                                        <div className="col-md-3 form-group">
                                            <label className="small">Акция</label>
                                            <Multiselect id="filter-promotion" small value={null} placeholder="Все" onSelect={() => {}}
                                                options={PROMOTIONS.map((item) => ({ value: item.id, label: item.title }))} />
                                        </div>
                                        <div className="col-md-3 form-group">
                                            <label className="small">Звонок/Чат</label>
                                            <select className="form-control form-control-sm" value={draft.source}
                                                onChange={(event) => setDraft({ ...draft, source: event.target.value })}>
                                                <option value="">Все</option>
                                                {SOURCES.map((source) => <option key={source} value={source}>{source}</option>)}
                                            </select>
                                        </div>
                                    </div>
                                    <div className="row">
                                        <div className="col-md-3 form-group">
                                            <label className="small">Тикет создан</label>
                                            <select className="form-control form-control-sm" value={draft.ticket}
                                                onChange={(event) => setDraft({ ...draft, ticket: event.target.value })}>
                                                <option value="">Все</option>
                                                <option value="1">Да</option>
                                                <option value="0">Нет</option>
                                            </select>
                                        </div>
                                        <div className="col-md-4 form-group">
                                            <label className="small">Поиск</label>
                                            <div className="input-group input-group-sm">
                                                <input className={`form-control${ui.t('lr_filter_search')}`} placeholder="ID, телефон, В/У" value={draft.search}
                                                    onChange={(event) => {
                                                        setDraft({ ...draft, search: event.target.value });
                                                        ui.touch('lr_filter_search', null, { search: event.target.value });
                                                    }} />
                                                <span className="input-group-append">
                                                    <button className={`btn btn-primary${ui.t('lr_filter_apply')}`} type="submit"><i className="fa fa-search" /></button>
                                                </span>
                                            </div>
                                        </div>
                                        <div className="col-md-2 form-group">
                                            <label className="small">На стр.</label>
                                            <select className="form-control form-control-sm" defaultValue="10">
                                                <option value="10">10</option>
                                                <option value="25">25</option>
                                                <option value="50">50</option>
                                                <option value="100">100</option>
                                            </select>
                                        </div>
                                        <div className="col-md-3 form-group d-flex align-items-end">
                                            <button className="btn btn-sm btn-primary" type="submit"><i className="fa fa-filter" /> Применить</button>
                                            <button className="btn btn-sm btn-outline-secondary ml-2" type="button" disabled={!changed}
                                                onClick={() => { setDraft(EMPTY_FILTER); setApplied(EMPTY_FILTER); }}>Сбросить</button>
                                        </div>
                                    </div>
                                </div>
                            </form>
                            {/* Шире окна тренажёра — прокрутка вбок (wt-scroll-x, crmFrame.jsx). */}
                            <div className="wt-scroll-x wt-scroll-x--actions">
                                <table className="table table-hover table-listing">
                                    <thead>
                                        <tr>
                                            <th><a><span className="fa fa-sort-amount-desc" /> ID</a></th>
                                            <th><a><span className="fa" /> Время создания</a></th>
                                            <th><a><span className="fa" /> ФИО оператора</a></th>
                                            <th>Звонок/Чат</th>
                                            <th>Номер телефона</th>
                                            <th>Номер В/У</th>
                                            <th><a><span className="fa" /> Таксопарк</a></th>
                                            <th><a><span className="fa" /> Город</a></th>
                                            <th><a><span className="fa" /> Категория</a></th>
                                            <th><a><span className="fa" /> Акция</a></th>
                                            <th>Номер тикета</th>
                                            <th />
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {rows.map((row) => (
                                            <tr key={row.id} className="">
                                                <td>{row.id}</td>
                                                <td>{row.at}</td>
                                                <td>{row.operator}</td>
                                                <td>{row.source}</td>
                                                <td>{row.phone}</td>
                                                <td>{row.license}</td>
                                                <td>{row.park}</td>
                                                <td>{row.city}</td>
                                                <td>{row.category}</td>
                                                <td>{row.promotion}</td>
                                                <td>{row.ticket ? <a href="#" onClick={noNav}>{row.ticket}</a> : null}</td>
                                                <td>
                                                    <div className="row no-gutters">
                                                        <div className="col-auto">
                                                            <a className={`btn btn-sm btn-spinner btn-info${ui.t('lr_row_show')}`} href="#" role="button" title="Показать"
                                                                onClick={(e) => { noNav(e); ui.go('request_show', { crmShown: row }, 'lr_row_show', { id: row.id, own: row.operator === 'Стажёр' }); }}>
                                                                <i className="fa fa-eye" />
                                                            </a>
                                                        </div>
                                                        <div className="col-auto">
                                                            <a className="btn btn-sm btn-spinner btn-info" href="#" role="button" title="Правка"
                                                                onClick={(e) => { noNav(e); ui.act('lr_row_edit', { crmView: 'stub', crmStub: 'request_edit' }); }}>
                                                                <i className="fa fa-edit" />
                                                            </a>
                                                        </div>
                                                    </div>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                            <div className="row">
                                <div className="col-sm">
                                    <span className="pagination-caption"> 1 to {rows.length} of total {rows.length} items.</span>
                                </div>
                                <div className="col-sm-auto">
                                    <nav>
                                        <ul className="pagination sizeClass">
                                            <li className="disabled page-item"><span className="page-link"><span aria-hidden="true">«</span></span></li>
                                            <li className="active page-item"><a className="page-link" href="#" onClick={noNav}>1</a></li>
                                            <li className="disabled page-item"><span className="page-link"><span aria-hidden="true">»</span></span></li>
                                        </ul>
                                    </nav>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
};

/* Просмотр обращения. Страница просмотра в CRM при съёмке не снималась —
   здесь та же карточка CRM (breadcrumb, card-header, таблица table-sm) с
   полями обращения. */
export const RequestShow = ({ ui }) => {
    const row = ui.world.crmShown;
    if (!row) return null;
    return (
        <div className="container-xl">
            <div className="card">
                <nav aria-label="breadcrumb">
                    <ol className="breadcrumb">
                        <li className="breadcrumb-item"><a href="#" onClick={(e) => { noNav(e); ui.crumb('home'); }}>Главная</a></li>
                        <li className="breadcrumb-item"><a href="#" onClick={(e) => { noNav(e); ui.go('requests', {}, 'crumb_requests'); }}>Обращения</a></li>
                        <li className="breadcrumb-item active" aria-current="page">Обращение №{row.id}</li>
                    </ol>
                </nav>
                <div className="card-header"><i className="fa fa-inbox" /> Обращение №{row.id}</div>
                <div className="card-body">
                    <table className="table table-sm mb-0">
                        <tbody>
                            <tr><th style={{ width: '40%' }}>Время создания</th><td>{row.at}</td></tr>
                            <tr><th>ФИО оператора</th><td>{row.operator}</td></tr>
                            {row.fields.map(([title, value]) => <tr key={title}><th>{title}</th><td>{value}</td></tr>)}
                            {row.ticket ? <tr><th>Номер тикета</th><td>{row.ticket}</td></tr> : null}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    );
};
