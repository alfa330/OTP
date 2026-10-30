import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Plus, RefreshCw, Trash2 } from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import IosDatePicker from '../ui/DatePicker';
import { IosModal, IosSegmented, IosToggle, iosBtnPrimary, iosBtnSecondary, iosCard } from '../ui/ios';
import {
    GRANT_LABELS, INTEGRATION_LABELS, INTEGRATION_STATE_LABELS, REASON_KIND_LABELS, currentMonth, errorOf, fullDate,
    plural, shortTime,
} from './hiringMeta';
import { DATE_TRIGGER, Empty, FIELD, Group, Label, Loading, MonthSwitch, Pill, TOOLBAR_BTN, TOOLBAR_BTN_PRIMARY } from './hiringUi';

/*
 * Вкладка «Настройки» — руководитель рекрутинга (ТЗ 2: «настраивать справочники»):
 *   рекрутеры   кому раздел выдан и кем (штатный, удалённый, руководитель);
 *   источники   контактов и откликов (ТЗ 5);
 *   вакансии    куда идёт найм, есть ли собеседование в офисе (ТЗ 4.2);
 *   причины     отказа и отсева по инициаторам (ТЗ 6.2, 6.3), не трогают закрытые карточки;
 *   группы      на обучение: дата старта, тренер (ТЗ 1.2);
 *   план        месячные задачи на найм в FTE (ТЗ 1.2, 3);
 *   интеграции  HeadHunter, OLX, вебхук площадок — состояние и запуск вручную.
 */

const SECTIONS = [
    { value: 'people', label: 'Рекрутеры' },
    { value: 'sources', label: 'Источники' },
    { value: 'vacancies', label: 'Вакансии' },
    { value: 'reasons', label: 'Причины' },
    { value: 'groups', label: 'Группы' },
    { value: 'plans', label: 'План найма' },
    { value: 'integrations', label: 'Интеграции' },
];

const Row = ({ children, muted }) => (
    <li className={`flex items-center gap-3 px-4 py-2.5 ${muted ? 'opacity-50' : ''}`}>{children}</li>
);

/* Работающие сотрудники — один раз на вкладку: список в сотни строк, отбор по фамилии
   делает выпадающий список сам. */
const useUsers = (api) => {
    const [users, setUsers] = useState([]);
    useEffect(() => {
        let alive = true;
        api.get('/people/search').then(({ data }) => { if (alive) setUsers(data.items); }).catch(() => {});
        return () => { alive = false; };
    }, [api]);
    return users;
};

/* ─── Рекрутеры ─────────────────────────────────────────────────────────── */
const PeopleSection = ({ api, toast, boot, reloadBoot }) => {
    const [items, setItems] = useState(null);
    const [userId, setUserId] = useState('');
    const [kind, setKind] = useState('staff');
    const users = useUsers(api);
    const load = useCallback(() => api.get('/people').then(({ data }) => setItems(data.items))
        .catch((error) => toast(errorOf(error, 'Не удалось загрузить'), 'error')), [api, toast]);
    useEffect(() => { load(); }, [load]);
    const add = async () => {
        if (!userId) return toast('Выберите сотрудника', 'error');
        try {
            const { data } = await api.post('/people', { user_id: Number(userId), kind });
            setItems(data.items);
            setUserId('');
            reloadBoot();
            toast('Доступ выдан', 'success');
        } catch (error) {
            toast(errorOf(error, 'Не удалось выдать'), 'error');
        }
        return undefined;
    };
    const remove = async (row) => {
        if (!window.confirm(`Отключить ${row.name}? Открытые карточки вернутся в пул.`)) return; // eslint-disable-line no-alert
        try {
            const { data } = await api.delete(`/people/${row.user_id}`);
            setItems(data.items);
            reloadBoot();
            toast(data.returned_to_pool ? `Отключено, в пул вернулось ${data.returned_to_pool}` : 'Отключено', 'success');
        } catch (error) {
            toast(errorOf(error, 'Не удалось отключить'), 'error');
        }
    };
    return (
        <div className="space-y-4">
            <div className={`${iosCard} p-4`}>
                <div className="grid gap-3 sm:grid-cols-[1fr_auto_auto] sm:items-end">
                    <div>
                        <Label>Сотрудник</Label>
                        <CustomSelect variant="ios" value={userId} onChange={setUserId} placeholder="Найти сотрудника" searchable
                                      options={users.map((u) => ({ value: String(u.id), label: u.name, meta: u.department_name || '' }))}
                                      searchPlaceholder="Фамилия" />
                    </div>
                    <div>
                        <Label hint="Штатный и удалённый берут карточки и ведут их; удалённый видит телефоны только в своих. Руководитель видит всё и ведёт настройки.">Вид доступа</Label>
                        <IosSegmented value={kind} onChange={setKind} ariaLabel="Вид доступа"
                                      options={(boot.meta.grant_kinds || []).map((g) => ({ value: g.code, label: g.label }))} />
                    </div>
                    <button type="button" className={`${TOOLBAR_BTN_PRIMARY} h-[41px]`} onClick={add}><Plus size={14} /> Выдать</button>
                </div>
            </div>
            <div className={`${iosCard} overflow-hidden`}>
                {!items ? <Loading text="Загружаем…" /> : items.length === 0 ? <Empty>Выдач ещё нет. Глава и сотрудники отдела HR входят как руководители без выдачи.</Empty> : (
                    <ul className="divide-y divide-slate-100">
                        {items.map((row) => (
                            <Row key={row.user_id} muted={!row.is_active}>
                                <div className="min-w-0 grow basis-0">
                                    <div className="truncate text-[13.5px] font-medium text-slate-900">{row.name}</div>
                                    <div className="truncate text-[12px] text-slate-500">{[row.department_name, `выдан ${shortTime(row.granted_at)}`].filter(Boolean).join(' · ')}</div>
                                </div>
                                <Pill tone={row.is_active ? 'blue' : 'slate'}>{row.is_active ? GRANT_LABELS[row.kind] : 'Отключён'}</Pill>
                                {row.is_active && <button type="button" className={TOOLBAR_BTN} onClick={() => remove(row)} aria-label="Отключить"><Trash2 size={14} /></button>}
                            </Row>
                        ))}
                    </ul>
                )}
            </div>
        </div>
    );
};

/* ─── Источники ─────────────────────────────────────────────────────────── */
const SourcesSection = ({ api, toast, boot, reloadBoot }) => {
    const [items, setItems] = useState(boot.dictionaries?.sources || []);
    const [name, setName] = useState('');
    const [kind, setKind] = useState('contacts');
    const add = async () => {
        if (!name.trim()) return toast('Название пустое', 'error');
        try {
            const { data } = await api.post('/sources', { name: name.trim(), kind });
            setItems(data.items);
            setName('');
            reloadBoot();
        } catch (error) {
            toast(errorOf(error, 'Не удалось добавить'), 'error');
        }
        return undefined;
    };
    const toggle = async (row) => {
        try {
            const { data } = await api.patch(`/sources/${row.id}`, { is_active: !row.is_active });
            setItems(data.items);
            reloadBoot();
        } catch (error) {
            toast(errorOf(error, 'Не удалось изменить'), 'error');
        }
    };
    return (
        <div className="space-y-4">
            <div className={`${iosCard} p-4`}>
                <div className="grid gap-3 sm:grid-cols-[1fr_auto_auto] sm:items-end">
                    <div><Label>Название</Label><input className={FIELD} value={name} onChange={(e) => setName(e.target.value)} placeholder="Например, База партнёров 2026" /></div>
                    <div>
                        <Label hint="Контакты — люди из загруженной базы, сами не откликались. Отклики — откликнулись сами (площадка, реклама, вручную).">Вид</Label>
                        <IosSegmented value={kind} onChange={setKind} ariaLabel="Вид источника" options={[{ value: 'contacts', label: 'Контакты' }, { value: 'responses', label: 'Отклики' }]} />
                    </div>
                    <button type="button" className={`${TOOLBAR_BTN_PRIMARY} h-[41px]`} onClick={add}><Plus size={14} /> Добавить</button>
                </div>
            </div>
            {['responses', 'contacts'].map((group) => (
                <Group key={group} title={group === 'contacts' ? 'Источники контактов' : 'Источники откликов'}>
                    <div className={`${iosCard} overflow-hidden`}>
                        <ul className="divide-y divide-slate-100">
                            {items.filter((s) => s.kind === group).map((row) => (
                                <Row key={row.id} muted={!row.is_active}>
                                    <div className="min-w-0 grow basis-0 text-[13.5px] text-slate-900">{row.name}</div>
                                    {row.platform && <Pill>{row.platform === 'webhook' ? 'вебхук' : row.platform === 'hh' ? 'API hh.ru' : 'робот OLX'}</Pill>}
                                    <IosToggle checked={row.is_active} onChange={() => toggle(row)} />
                                </Row>
                            ))}
                        </ul>
                    </div>
                </Group>
            ))}
        </div>
    );
};

/* ─── Вакансии ──────────────────────────────────────────────────────────── */
const VacanciesSection = ({ api, toast, boot, reloadBoot }) => {
    const [items, setItems] = useState(boot.dictionaries?.vacancies || []);
    const [form, setForm] = useState({ name: '', department_id: '', office_interview: true });
    const departments = boot.dictionaries?.departments || [];
    const add = async () => {
        if (!form.name.trim()) return toast('Название пустое', 'error');
        try {
            const { data } = await api.post('/vacancies', { name: form.name.trim(), department_id: form.department_id ? Number(form.department_id) : null, office_interview: form.office_interview });
            setItems(data.items);
            setForm({ name: '', department_id: '', office_interview: true });
            reloadBoot();
        } catch (error) {
            toast(errorOf(error, 'Не удалось добавить'), 'error');
        }
        return undefined;
    };
    const patch = async (row, fields) => {
        try {
            const { data } = await api.patch(`/vacancies/${row.id}`, fields);
            setItems(data.items);
            reloadBoot();
        } catch (error) {
            toast(errorOf(error, 'Не удалось изменить'), 'error');
        }
    };
    return (
        <div className="space-y-4">
            <div className={`${iosCard} p-4`}>
                <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto_auto] sm:items-end">
                    <div><Label>Вакансия / должность</Label><input className={FIELD} value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} placeholder="Водитель такси" /></div>
                    <div>
                        <Label hint="Отдел, куда идёт найм: его руководитель отмечает смены и подтверждает «Отработал 7 дней».">Отдел</Label>
                        <CustomSelect variant="ios" value={form.department_id} onChange={(v) => setForm((f) => ({ ...f, department_id: v }))} placeholder="Не указан" searchable
                                      options={[{ value: '', label: 'Не указан' }, ...departments.map((d) => ({ value: String(d.id), label: d.name }))]} />
                    </div>
                    <div>
                        <Label hint="Есть — после «В работе» карточка идёт в «Приглашен в офис»; нет — сразу в «Приглашен на обучение».">Собеседование в офисе</Label>
                        <IosSegmented value={form.office_interview ? 'yes' : 'no'} onChange={(v) => setForm((f) => ({ ...f, office_interview: v === 'yes' }))} ariaLabel="Собеседование"
                                      options={[{ value: 'yes', label: 'Есть' }, { value: 'no', label: 'Нет' }]} />
                    </div>
                    <button type="button" className={`${TOOLBAR_BTN_PRIMARY} h-[41px]`} onClick={add}><Plus size={14} /> Добавить</button>
                </div>
            </div>
            <div className={`${iosCard} overflow-hidden`}>
                {items.length === 0 ? <Empty>Вакансий пока нет</Empty> : (
                    <ul className="divide-y divide-slate-100">
                        {items.map((row) => (
                            <Row key={row.id} muted={!row.is_active}>
                                <div className="min-w-0 grow basis-0">
                                    <div className="truncate text-[13.5px] text-slate-900">{row.name}</div>
                                    <div className="truncate text-[12px] text-slate-500">{[row.department_name || 'отдел не указан', row.office_interview ? 'собеседование в офисе' : 'без собеседования'].join(' · ')}</div>
                                </div>
                                <IosToggle checked={row.is_active} onChange={() => patch(row, { is_active: !row.is_active })} />
                            </Row>
                        ))}
                    </ul>
                )}
            </div>
        </div>
    );
};

/* ─── Причины ───────────────────────────────────────────────────────────── */
const ReasonsSection = ({ api, toast, boot, reloadBoot }) => {
    const [items, setItems] = useState(boot.dictionaries?.reasons || []);
    const [kind, setKind] = useState('refusal_ours');
    const [name, setName] = useState('');
    const add = async () => {
        if (!name.trim()) return toast('Название пустое', 'error');
        try {
            const { data } = await api.post('/reasons', { kind, name: name.trim() });
            setItems(data.items);
            setName('');
            reloadBoot();
        } catch (error) {
            toast(errorOf(error, 'Не удалось добавить'), 'error');
        }
        return undefined;
    };
    const toggle = async (row) => {
        try {
            const { data } = await api.patch(`/reasons/${row.id}`, { is_active: !row.is_active });
            setItems(data.items);
            reloadBoot();
        } catch (error) {
            toast(errorOf(error, 'Не удалось изменить'), 'error');
        }
    };
    return (
        <div className="space-y-4">
            <div className={`${iosCard} p-4`}>
                <div className="grid gap-3 sm:grid-cols-[auto_1fr_auto] sm:items-end">
                    <div>
                        <Label hint="Отказ ставится до выхода на обучение, отсев — после. У каждого инициатора свой список.">Список</Label>
                        <CustomSelect variant="ios" value={kind} onChange={setKind} options={(boot.meta.reason_kinds || []).map((k) => ({ value: k.code, label: k.label }))} className="w-48" />
                    </div>
                    <div><Label>Причина</Label><input className={FIELD} value={name} onChange={(e) => setName(e.target.value)} /></div>
                    <button type="button" className={`${TOOLBAR_BTN_PRIMARY} h-[41px]`} onClick={add}><Plus size={14} /> Добавить</button>
                </div>
                <div className="mt-2 text-[12px] text-slate-500">Выключенная причина пропадает из выбора; закрытые карточки хранят причину как была.</div>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
                {Object.keys(REASON_KIND_LABELS).map((group) => (
                    <Group key={group} title={REASON_KIND_LABELS[group]}>
                        <div className={`${iosCard} overflow-hidden`}>
                            <ul className="divide-y divide-slate-100">
                                {items.filter((r) => r.kind === group).map((row) => (
                                    <Row key={row.id} muted={!row.is_active}>
                                        <div className="min-w-0 grow basis-0 text-[13px] text-slate-900">{row.name}{row.requires_comment ? <span className="ml-1.5 text-[11.5px] text-slate-400">с комментарием</span> : null}</div>
                                        <IosToggle checked={row.is_active} onChange={() => toggle(row)} />
                                    </Row>
                                ))}
                            </ul>
                        </div>
                    </Group>
                ))}
            </div>
        </div>
    );
};

/* ─── Группы на обучение ────────────────────────────────────────────────── */
const GroupsSection = ({ api, toast, boot, reloadBoot }) => {
    const [items, setItems] = useState(null);
    const [form, setForm] = useState({ name: '', start_date: '', trainer_id: '', department_id: '', vacancy_id: '' });
    const trainers = useUsers(api);
    const departments = boot.dictionaries?.departments || [];
    const vacancies = boot.dictionaries?.vacancies || [];
    const load = useCallback(() => api.get('/groups').then(({ data }) => setItems(data.items))
        .catch((error) => toast(errorOf(error, 'Не удалось загрузить'), 'error')), [api, toast]);
    useEffect(() => { load(); }, [load]);
    const add = async () => {
        if (!form.name.trim() || !form.start_date) return toast('Нужны название и дата старта', 'error');
        try {
            const { data } = await api.post('/groups', { name: form.name.trim(), start_date: form.start_date,
                trainer_id: form.trainer_id ? Number(form.trainer_id) : null, department_id: form.department_id ? Number(form.department_id) : null,
                vacancy_id: form.vacancy_id ? Number(form.vacancy_id) : null });
            setItems(data.items);
            setForm({ name: '', start_date: '', trainer_id: '', department_id: '', vacancy_id: '' });
            reloadBoot();
        } catch (error) {
            toast(errorOf(error, 'Не удалось добавить'), 'error');
        }
        return undefined;
    };
    const finish = async (row) => {
        try {
            const { data } = await api.patch(`/groups/${row.id}`, { status: row.status === 'active' ? 'done' : 'active' });
            setItems(data.items);
            reloadBoot();
        } catch (error) {
            toast(errorOf(error, 'Не удалось изменить'), 'error');
        }
    };
    return (
        <div className="space-y-4">
            <div className={`${iosCard} p-4`}>
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-[1fr_160px_1fr_1fr_1fr_auto] lg:items-end">
                    <div><Label>Название</Label><input className={FIELD} value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} placeholder="Группа 12.11" /></div>
                    <div><Label hint="Первый день обучения: по нему отмечается выход на обучение.">Старт</Label><IosDatePicker value={form.start_date} onChange={(v) => setForm((f) => ({ ...f, start_date: v }))} allowEmpty triggerClassName={DATE_TRIGGER} ariaLabel="Старт группы" /></div>
                    <div>
                        <Label hint="Тренер видит кандидатов группы и отмечает выход на обучение и отсев на обучении.">Тренер</Label>
                        <CustomSelect variant="ios" value={form.trainer_id} onChange={(v) => setForm((f) => ({ ...f, trainer_id: v }))} placeholder="Не указан" searchable searchPlaceholder="Фамилия"
                                      options={[{ value: '', label: 'Не указан' }, ...trainers.map((u) => ({ value: String(u.id), label: u.name, meta: u.department_name || '' }))]} />
                    </div>
                    <div>
                        <Label>Отдел</Label>
                        <CustomSelect variant="ios" value={form.department_id} onChange={(v) => setForm((f) => ({ ...f, department_id: v }))} placeholder="Не указан" searchable
                                      options={[{ value: '', label: 'Не указан' }, ...departments.map((d) => ({ value: String(d.id), label: d.name }))]} />
                    </div>
                    <div>
                        <Label>Вакансия</Label>
                        <CustomSelect variant="ios" value={form.vacancy_id} onChange={(v) => setForm((f) => ({ ...f, vacancy_id: v }))} placeholder="Не указана"
                                      options={[{ value: '', label: 'Не указана' }, ...vacancies.map((v) => ({ value: String(v.id), label: v.name }))]} />
                    </div>
                    <button type="button" className={`${TOOLBAR_BTN_PRIMARY} h-[41px]`} onClick={add}><Plus size={14} /> Добавить</button>
                </div>
            </div>
            <div className={`${iosCard} overflow-hidden`}>
                {!items ? <Loading text="Загружаем…" /> : items.length === 0 ? <Empty>Групп пока нет</Empty> : (
                    <ul className="divide-y divide-slate-100">
                        {items.map((row) => (
                            <Row key={row.id} muted={row.status !== 'active'}>
                                <div className="min-w-0 grow basis-0">
                                    <div className="truncate text-[13.5px] text-slate-900">{row.name} <span className="text-slate-400">· с {fullDate(row.start_date)}</span></div>
                                    <div className="truncate text-[12px] text-slate-500">{[row.trainer_name || 'тренер не указан', row.department_name, row.vacancy_name, `${row.cards_total} ${plural(row.cards_total, 'кандидат', 'кандидата', 'кандидатов')}`].filter(Boolean).join(' · ')}</div>
                                </div>
                                <button type="button" className={TOOLBAR_BTN} onClick={() => finish(row)}>{row.status === 'active' ? 'Завершить' : 'Вернуть'}</button>
                            </Row>
                        ))}
                    </ul>
                )}
            </div>
        </div>
    );
};

/* ─── План найма ────────────────────────────────────────────────────────── */
const PlansSection = ({ api, toast, boot }) => {
    const [month, setMonth] = useState(currentMonth);
    const [items, setItems] = useState(null);
    const [form, setForm] = useState({ vacancy_id: '', department_id: '', recruiter_id: '', plan_fte: '', planned_date: '', comment: '' });
    const departments = boot.dictionaries?.departments || [];
    const vacancies = boot.dictionaries?.vacancies || [];
    const recruiters = boot.dictionaries?.recruiters || [];
    const load = useCallback(() => api.get('/plans', { month }).then(({ data }) => setItems(data.items))
        .catch((error) => toast(errorOf(error, 'Не удалось загрузить'), 'error')), [api, month, toast]);
    useEffect(() => { load(); }, [load]);
    const add = async () => {
        if (!form.plan_fte || Number(form.plan_fte) <= 0) return toast('Укажите план в FTE', 'error');
        try {
            const { data } = await api.post('/plans', { month, vacancy_id: form.vacancy_id ? Number(form.vacancy_id) : null,
                department_id: form.department_id ? Number(form.department_id) : null, recruiter_id: form.recruiter_id ? Number(form.recruiter_id) : null,
                plan_fte: Number(String(form.plan_fte).replace(',', '.')), planned_date: form.planned_date || null, comment: form.comment || null });
            setItems(data.items);
            setForm({ vacancy_id: '', department_id: '', recruiter_id: '', plan_fte: '', planned_date: '', comment: '' });
        } catch (error) {
            toast(errorOf(error, 'Не удалось добавить'), 'error');
        }
        return undefined;
    };
    const remove = async (row) => {
        try {
            await api.delete(`/plans/${row.id}`);
            load();
        } catch (error) {
            toast(errorOf(error, 'Не удалось удалить'), 'error');
        }
    };
    return (
        <div className="space-y-4">
            <MonthSwitch value={month} onChange={setMonth} />
            <div className={`${iosCard} p-4`}>
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-[1fr_1fr_1fr_120px_160px_auto] lg:items-end">
                    <div>
                        <Label>Вакансия</Label>
                        <CustomSelect variant="ios" value={form.vacancy_id} onChange={(v) => setForm((f) => ({ ...f, vacancy_id: v }))} placeholder="Любая"
                                      options={[{ value: '', label: 'Любая' }, ...vacancies.map((v) => ({ value: String(v.id), label: v.name }))]} />
                    </div>
                    <div>
                        <Label>Отдел</Label>
                        <CustomSelect variant="ios" value={form.department_id} onChange={(v) => setForm((f) => ({ ...f, department_id: v }))} placeholder="Любой" searchable
                                      options={[{ value: '', label: 'Любой' }, ...departments.map((d) => ({ value: String(d.id), label: d.name }))]} />
                    </div>
                    <div>
                        <Label hint="Без рекрутера — общий план, его видят все на рабочем столе.">Рекрутер</Label>
                        <CustomSelect variant="ios" value={form.recruiter_id} onChange={(v) => setForm((f) => ({ ...f, recruiter_id: v }))} placeholder="Общий" searchable
                                      options={[{ value: '', label: 'Общий' }, ...recruiters.map((r) => ({ value: String(r.id), label: r.name }))]} />
                    </div>
                    <div><Label hint="План в FTE: каждый «Отработал 7 дней» закрывает 1 FTE.">План, FTE</Label><input className={FIELD} inputMode="decimal" value={form.plan_fte} onChange={(e) => setForm((f) => ({ ...f, plan_fte: e.target.value }))} placeholder="10" /></div>
                    <div><Label hint="Плановая дата закрытия — после неё остаток считается просрочкой SLA.">Срок</Label><IosDatePicker value={form.planned_date} onChange={(v) => setForm((f) => ({ ...f, planned_date: v }))} allowEmpty triggerClassName={DATE_TRIGGER} ariaLabel="Срок" /></div>
                    <button type="button" className={`${TOOLBAR_BTN_PRIMARY} h-[41px]`} onClick={add}><Plus size={14} /> Добавить</button>
                </div>
            </div>
            <div className={`${iosCard} overflow-hidden`}>
                {!items ? <Loading text="Загружаем…" /> : items.length === 0 ? <Empty>На этот месяц задач нет</Empty> : (
                    <ul className="divide-y divide-slate-100">
                        {items.map((row) => (
                            <Row key={row.id}>
                                <div className="min-w-0 grow basis-0">
                                    <div className="truncate text-[13.5px] text-slate-900">{row.vacancy_name || 'Любая вакансия'}{row.department_name ? ` · ${row.department_name}` : ''}</div>
                                    <div className="truncate text-[12px] text-slate-500">{[row.recruiter_name || 'общий план', row.planned_date ? `до ${fullDate(row.planned_date)}` : null, row.comment].filter(Boolean).join(' · ')}</div>
                                </div>
                                <span className="text-[14px] font-semibold tabular-nums text-slate-900">{Number(row.plan_fte)} FTE</span>
                                <button type="button" className={TOOLBAR_BTN} onClick={() => remove(row)} aria-label="Удалить"><Trash2 size={14} /></button>
                            </Row>
                        ))}
                    </ul>
                )}
            </div>
        </div>
    );
};

/* ─── Интеграции ────────────────────────────────────────────────────────── */
const IntegrationsSection = ({ api, toast, boot }) => {
    const [items, setItems] = useState(boot.integrations || []);
    const [running, setRunning] = useState('');
    // Кабинеты OLX «для найма» (владелец 30.10.2026: кабинеты лидов — таксопарки — к рекрутерам не
    // попадают, для найма подключаются отдельные аккаунты). Пусто — чаты робота в воронку не идут.
    const olxRow = items.find((row) => row.code === 'olx');
    const savedCabinets = olxRow?.settings?.cabinets || [];
    const [cabinets, setCabinets] = useState(savedCabinets);
    const [savingCabinets, setSavingCabinets] = useState(false);
    useEffect(() => {
        let alive = true;
        api.get('/integrations').then(({ data }) => {
            if (!alive) return;
            setItems(data.items);
            setCabinets((data.items.find((row) => row.code === 'olx')?.settings?.cabinets) || []);
        }).catch(() => {});
        return () => { alive = false; };
    }, [api]);
    const cabinetsChanged = JSON.stringify([...cabinets].sort()) !== JSON.stringify([...savedCabinets].sort());
    const saveCabinets = async () => {
        setSavingCabinets(true);
        try {
            const { data } = await api.put('/integrations/olx/settings', { cabinets });
            setItems(data.items);
            toast(cabinets.length ? 'Кабинеты для найма сохранены' : 'Чаты робота OLX в воронку не идут', 'success');
        } catch (error) {
            toast(errorOf(error, 'Не удалось сохранить'), 'error');
        } finally {
            setSavingCabinets(false);
        }
    };
    const run = async (code) => {
        setRunning(code);
        try {
            const { data } = await api.post(`/integrations/${code}/run`);
            setItems(data.items);
            toast(data.received === null ? 'Опрос не удался — смотрите ошибку' : `Получено откликов: ${data.received}`, data.received === null ? 'error' : 'success');
        } catch (error) {
            toast(errorOf(error, 'Опрос не запустился'), 'error');
        } finally {
            setRunning('');
        }
    };
    const hints = {
        hh: 'Нужны переменные окружения HH_ACCESS_TOKEN и HH_USER_AGENT (токен приложения работодателя hh.ru). Опрос раз в 15 минут: активные вакансии → отклики → контакты резюме.',
        olx: 'Раз в 5 минут из журнала робота OLX берутся обращения с номером телефона — только из кабинетов, выбранных здесь как «для найма». Кабинеты лидов (таксопарки) к рекрутерам не попадают.',
        webhook: 'Площадки без API (Kaspi, таргет, лид-формы) присылают отклики на POST /api/hiring/external/responses по ключу партнёра с областью «Отклики в воронку найма» («Сессии → Ключи»).',
    };
    return (
        <div className={`${iosCard} overflow-hidden`}>
            <ul className="divide-y divide-slate-100">
                {items.map((row) => (
                    <Row key={row.code}>
                        <div className="min-w-0 grow basis-0">
                            <div className="flex items-center gap-2 text-[13.5px] font-medium text-slate-900">
                                {INTEGRATION_LABELS[row.code] || row.code}
                                <Pill tone={row.state === 'ok' ? 'green' : row.state === 'error' ? 'red' : 'slate'}>{INTEGRATION_STATE_LABELS[row.state] || row.state}</Pill>
                            </div>
                            <div className="text-[12px] text-slate-500">{hints[row.code]}</div>
                            {row.code === 'olx' && (
                                <div className="mt-2 flex flex-wrap items-center gap-2">
                                    <div className="w-72">
                                        <CustomSelect variant="ios" multiple value={cabinets} onChange={setCabinets} ariaLabel="Кабинеты OLX для найма"
                                                      placeholder="Кабинеты для найма не выбраны"
                                                      options={(row.cabinets_available || []).map((c) => ({ value: c.code, label: c.title, meta: c.code }))}
                                                      renderValue={(vals) => (vals.length ? `Кабинеты для найма: ${vals.length}` : 'Кабинеты для найма не выбраны')} />
                                    </div>
                                    {cabinetsChanged && (
                                        <button type="button" className={TOOLBAR_BTN_PRIMARY} onClick={saveCabinets} disabled={savingCabinets}>Сохранить</button>
                                    )}
                                </div>
                            )}
                            <div className="mt-0.5 text-[12px] tabular-nums text-slate-500">
                                {[row.last_run_at ? `последний опрос ${shortTime(row.last_run_at)}` : 'ещё не опрашивалась',
                                    row.last_ok_at ? `успешно ${shortTime(row.last_ok_at)}` : null,
                                    `получено всего ${row.received_total}`].filter(Boolean).join(' · ')}
                            </div>
                            {row.last_error && <div className="mt-0.5 text-[12px] text-rose-600">{row.last_error}</div>}
                        </div>
                        {row.code !== 'webhook' && (
                            <button type="button" className={TOOLBAR_BTN} onClick={() => run(row.code)} disabled={running === row.code}>
                                <RefreshCw size={14} className={running === row.code ? 'animate-spin' : ''} /> Опросить
                            </button>
                        )}
                    </Row>
                ))}
            </ul>
        </div>
    );
};

const SettingsTab = ({ api, toast, boot, reloadBoot }) => {
    const [section, setSection] = useState('people');
    const shared = { api, toast, boot, reloadBoot };
    return (
        <div className="space-y-4">
            <div className="-mx-1 max-w-full overflow-x-auto px-1 [scrollbar-width:none]">
                <IosSegmented value={section} onChange={setSection} options={SECTIONS} ariaLabel="Раздел настроек" />
            </div>
            {section === 'people' && <PeopleSection {...shared} />}
            {section === 'sources' && <SourcesSection {...shared} />}
            {section === 'vacancies' && <VacanciesSection {...shared} />}
            {section === 'reasons' && <ReasonsSection {...shared} />}
            {section === 'groups' && <GroupsSection {...shared} />}
            {section === 'plans' && <PlansSection {...shared} />}
            {section === 'integrations' && <IntegrationsSection {...shared} />}
        </div>
    );
};

export default SettingsTab;
