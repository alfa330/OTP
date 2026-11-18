import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Minus, Plus, RefreshCw } from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import IosDatePicker from '../ui/DatePicker';
import { IosModal, IosSegmented, iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard } from '../ui/ios';
import {
    GROUP_CAPACITY_MAX, codeOf, errorOf, groupNameAfterStart, groupNameFor, groupRequestBody, groupSeats, groupWhen, stepCapacity,
} from './hiringMeta';
import { DATE_TRIGGER, Empty, FIELD, Label, Loading, TOOLBAR_BTN, TOOLBAR_BTN_PRIMARY } from './hiringUi';

/*
 * Вкладка «Группы» — группы на обучение (владелец 18.11.2026): тренер сам заводит группу, задаёт старт, вместимость
 * и вакансию; рекрутер, приглашая на обучение, видит в каждой группе свободные места, а в полную пригласить не может.
 * Тренер видит и правит свои группы, руководитель рекрутинга — все, он же назначает группе тренера и отдел. Место
 * занимает приглашённый и не отпавший кандидат: отказ до обучения и отсев место освобождают.
 *
 * Места меняются у всех сразу — рекрутеры приглашают, — поэтому список перечитывается при любом изменении карточек
 * в разделе (version), по кнопке «Обновить» и после отказа сервера «меньше занятых мест».
 */

const STATUS_FILTERS = { active: (group) => group.status === 'active', done: (group) => group.status !== 'active' };
/* Идущие — ближний старт первым, как их видит рекрутер при выборе; завершённые — недавние первыми. */
const byStart = (a, b) => String(a.start_date).localeCompare(String(b.start_date)) || a.id - b.id;
const SORTS = { active: byStart, done: (a, b) => byStart(b, a) };

/* Карточка группы: название и когда, крупно — сколько мест занято, полоса заполненности. Цвет — только у полной
   группы, обычная полоса нейтральная. */
const GroupCard = ({ group, showTrainer, onOpen }) => {
    const seats = groupSeats(group);
    const details = [groupWhen(group), showTrainer ? (group.trainer_name || 'без тренера') : null, group.vacancy_name]
        .filter(Boolean).join(' · ');
    return (
        <button type="button" onClick={() => onOpen(group)}
                className={`${iosCard} flex min-w-0 flex-col gap-3 p-4 text-left transition hover:ring-slate-300 active:scale-[0.99] ${group.status === 'active' ? '' : 'opacity-60'}`}>
            <div className="min-w-0">
                <div className="truncate text-[15px] font-semibold text-slate-900">{group.name}</div>
                <div className="truncate text-[12.5px] text-slate-500">{details}</div>
            </div>
            <div>
                <div className="flex items-baseline justify-between gap-2">
                    <span className="text-[22px] font-semibold leading-none tabular-nums text-slate-900">
                        {seats.taken}
                        {seats.capacity !== null && <span className="text-[15px] font-medium text-slate-400"> / {seats.capacity}</span>}
                    </span>
                    <span className={`truncate text-[12.5px] font-medium ${seats.full ? 'text-amber-700' : 'text-slate-500'}`}>
                        {seats.capacity === null ? 'вместимость не указана' : seats.full ? 'мест нет' : `свободно ${seats.free}`}
                    </span>
                </div>
                {seats.capacity !== null && (
                    <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100" aria-hidden="true">
                        <div className={`h-full rounded-full ${seats.full ? 'bg-amber-500' : 'bg-slate-400'}`}
                             style={{ width: `${Math.round(seats.share * 100)}%` }} />
                    </div>
                )}
            </div>
        </button>
    );
};

/* Вместимость — степпером, как в «Настройках» iOS: «−», число, «+». Набрать с клавиатуры тоже можно. Шаг — stepCapacity:
   из пустого поля «+» ставит число занятых мест, «−» не опускает ниже занятых. */
const CapacityStepper = ({ value, onChange, min }) => {
    const number = Number(value) || 0;
    return (
        <div className="flex items-center gap-3">
            <div className="inline-flex h-[42px] items-center rounded-xl bg-slate-100">
                <button type="button" aria-label="Меньше" onClick={() => onChange(stepCapacity(value, -1, min))} disabled={number <= Math.max(1, min)}
                        className="grid h-full w-10 place-items-center rounded-l-xl text-slate-600 transition hover:bg-slate-200/70 active:scale-95 disabled:opacity-35">
                    <Minus size={15} />
                </button>
                <input value={value} inputMode="numeric" aria-label="Вместимость" placeholder="—"
                       onChange={(event) => onChange(event.target.value.replace(/\D/g, '').slice(0, 3))}
                       className="h-full w-14 bg-transparent text-center text-[16px] font-semibold tabular-nums text-slate-900 placeholder-slate-400 focus:outline-none" />
                <button type="button" aria-label="Больше" onClick={() => onChange(stepCapacity(value, 1, min))} disabled={number >= GROUP_CAPACITY_MAX}
                        className="grid h-full w-10 place-items-center rounded-r-xl text-slate-600 transition hover:bg-slate-200/70 active:scale-95 disabled:opacity-35">
                    <Plus size={15} />
                </button>
            </div>
            <span className="text-[13px] text-slate-500">человек</span>
        </div>
    );
};

const emptyForm = { name: '', start_date: '', capacity: '', vacancy_id: '', trainer_id: '', department_id: '' };

/* Окно группы: новая или правка. Тренер заводит группу на себя и обязан указать вакансию — по ней кандидат без своей
   вакансии попадёт в отдел найма; тренера и отдел назначает руководитель. onStale — места устарели (сервер отказал
   «меньше занятых»): вкладка перечитывает список и подставляет в окно свежую строку группы. */
const GroupModal = ({ open, group, onClose, api, toast, boot, lead, users, onSaved, onStale }) => {
    const editing = Boolean(group?.id);
    const [form, setForm] = useState(emptyForm);
    // Пока название не трогали руками, оно идёт за датой старта: «Группа 24.11».
    const [nameTouched, setNameTouched] = useState(false);
    const [saving, setSaving] = useState('');
    const vacancies = useMemo(() => (boot.dictionaries?.vacancies || []).filter((v) => v.is_active !== false), [boot]);
    useEffect(() => {
        if (!open) return;
        if (group?.id) {
            setForm((prev) => (prev.__id === group.id ? prev : {
                __id: group.id, name: group.name || '', start_date: group.start_date || '',
                capacity: group.capacity ? String(group.capacity) : '', vacancy_id: group.vacancy_id ? String(group.vacancy_id) : '',
                trainer_id: group.trainer_id ? String(group.trainer_id) : '', department_id: group.department_id ? String(group.department_id) : '',
            }));
        } else {
            // Вакансия одна — тренеру не нужно выбирать то, из чего нечего выбрать.
            setForm({ ...emptyForm, vacancy_id: !lead && vacancies.length === 1 ? String(vacancies[0].id) : '' });
        }
        setNameTouched(Boolean(group?.id));
    }, [open, group, lead, vacancies]);
    useEffect(() => { if (!open) setForm(emptyForm); }, [open]);
    const taken = editing ? groupSeats(group).taken : 0;
    const set = (key) => (value) => setForm((prev) => ({ ...prev, [key]: value?.target ? value.target.value : value }));
    const setStart = (value) => setForm((prev) => ({ ...prev, start_date: value, name: groupNameAfterStart(prev.name, value, nameTouched) }));
    const vacancyOptions = (boot.dictionaries?.vacancies || []).filter((v) => v.is_active !== false || String(v.id) === form.vacancy_id);
    const departments = boot.dictionaries?.departments || [];
    // Тренеры — первыми: группу почти всегда ведёт человек с ролью «тренер», но назначить можно любого.
    const trainerOptions = useMemo(() => [
        { value: '', label: 'Без тренера' },
        ...users.filter((u) => u.role === 'trainer').map((u) => ({ value: String(u.id), label: u.name, meta: u.department_name || '', groupLabel: 'Тренеры' })),
        ...users.filter((u) => u.role !== 'trainer').map((u) => ({ value: String(u.id), label: u.name, meta: u.department_name || '', groupLabel: 'Остальные сотрудники' })),
    ], [users]);

    const request = async (kind, body) => {
        setSaving(kind);
        try {
            const { data } = editing ? await api.patch(`/groups/${group.id}`, body) : await api.post('/groups', body);
            onSaved(data.items, kind);
            onClose();
        } catch (error) {
            toast(errorOf(error, 'Не удалось сохранить группу'), 'error');
            if (codeOf(error) === 'HIRING_CAPACITY_BELOW_TAKEN') onStale(group?.id);
        } finally {
            setSaving('');
        }
    };
    const save = () => {
        const body = groupRequestBody(form, { lead });
        if (!body.start_date) return toast('Укажите дату старта', 'error');
        if (!body.name) return toast('Укажите название группы', 'error');
        if (!Number.isInteger(body.capacity) || body.capacity < 1 || body.capacity > GROUP_CAPACITY_MAX) return toast(`Вместимость — от 1 до ${GROUP_CAPACITY_MAX} человек`, 'error');
        if (body.capacity < taken) return toast(`В группе уже ${taken} — вместимость не может быть меньше`, 'error');
        if (!lead && !body.vacancy_id) return toast('Укажите вакансию группы', 'error');
        return request('save', body);
    };
    const toggleStatus = () => request('status', { status: group.status === 'active' ? 'done' : 'active' });

    return (
        <IosModal open={open} onClose={onClose} title={editing ? group.name : 'Новая группа'}
                  footer={(
                      <div className="flex flex-wrap items-center justify-between gap-2">
                          {editing ? (
                              <button type="button" className={iosBtnGhost} onClick={toggleStatus} disabled={Boolean(saving)}>
                                  {group.status === 'active' ? 'Завершить группу' : 'Вернуть в работу'}
                              </button>
                          ) : <span />}
                          <div className="flex gap-2">
                              <button type="button" className={iosBtnSecondary} onClick={onClose} disabled={Boolean(saving)}>Отмена</button>
                              <button type="button" className={iosBtnPrimary} onClick={save} disabled={Boolean(saving)}>{editing ? 'Сохранить' : 'Создать'}</button>
                          </div>
                      </div>
                  )}>
            <div className="space-y-3">
                <div className="grid gap-3 sm:grid-cols-[200px_1fr]">
                    <div>
                        <Label required hint="Первый день обучения: по нему отмечается выход на обучение.">Старт</Label>
                        <IosDatePicker value={form.start_date} onChange={setStart} allowEmpty triggerClassName={DATE_TRIGGER} ariaLabel="Старт группы" />
                    </div>
                    <div>
                        <Label required>Название</Label>
                        <input className={FIELD} value={form.name} maxLength={120} placeholder={groupNameFor(form.start_date) || 'Группа 24.11'}
                               onChange={(event) => { setNameTouched(true); set('name')(event); }} />
                    </div>
                </div>
                <div>
                    <Label required hint="Сколько кандидатов группа вмещает. Рекрутер видит свободные места, когда приглашает на обучение; в полную группу пригласить нельзя. Отказ до обучения и отсев место освобождают.">
                        Вместимость
                    </Label>
                    <CapacityStepper value={form.capacity} onChange={set('capacity')} min={taken} />
                    {taken > 0 && <div className="mt-1 text-[12px] text-slate-500">Сейчас в группе {taken} — меньше указать нельзя</div>}
                </div>
                <div>
                    <Label required={!lead} hint="Кандидату без вакансии подставится вакансия группы, а с ней — отдел, чей руководитель подтверждает «Отработал 7 дней».">
                        Вакансия
                    </Label>
                    <CustomSelect variant="ios" value={form.vacancy_id} onChange={set('vacancy_id')} placeholder={lead ? 'Не указана' : 'Выберите вакансию'}
                                  invalid={!lead && !form.vacancy_id}
                                  options={[...(lead ? [{ value: '', label: 'Не указана' }] : []), ...vacancyOptions.map((v) => ({ value: String(v.id), label: v.name }))]} />
                </div>
                {lead && (
                    <div className="grid gap-3 sm:grid-cols-2">
                        <div>
                            <Label hint="Тренер видит кандидатов группы, отмечает выход на обучение и отсев, правит группу.">Тренер</Label>
                            <CustomSelect variant="ios" value={form.trainer_id} onChange={set('trainer_id')} placeholder="Без тренера" searchable
                                          searchPlaceholder="Фамилия" options={trainerOptions} />
                        </div>
                        <div>
                            <Label>Отдел</Label>
                            <CustomSelect variant="ios" value={form.department_id} onChange={set('department_id')} placeholder="Не указан" searchable
                                          options={[{ value: '', label: 'Не указан' }, ...departments.map((d) => ({ value: String(d.id), label: d.name }))]} />
                        </div>
                    </div>
                )}
            </div>
        </IosModal>
    );
};

const GroupsTab = ({ api, toast, boot, caps, reloadBoot, version }) => {
    const lead = Boolean(caps.lead);
    const [items, setItems] = useState(null);
    const [loadError, setLoadError] = useState('');
    const [loading, setLoading] = useState(false);
    const [filter, setFilter] = useState('active');
    // null — окно закрыто; {} — новая группа; строка списка — её правка.
    const [editing, setEditing] = useState(null);
    const [users, setUsers] = useState([]);

    const load = useCallback(() => {
        setLoading(true);
        return api.get('/groups')
            .then(({ data }) => { setItems(data.items); setLoadError(''); return data.items; })
            .catch((error) => { setLoadError(errorOf(error, 'Группы не загрузились')); return null; })
            .finally(() => setLoading(false));
    }, [api]);
    // Места меняют и рекрутеры: перечитываем при любом изменении карточек в разделе (version растёт).
    useEffect(() => { load(); }, [load, version]);
    // Список сотрудников — только руководителю: тренера группе назначает он.
    useEffect(() => {
        if (!lead) return undefined;
        let alive = true;
        api.get('/people/search').then(({ data }) => { if (alive) setUsers(data.items); }).catch(() => {});
        return () => { alive = false; };
    }, [api, lead]);

    const done = (items || []).filter(STATUS_FILTERS.done).length;
    const view = done ? filter : 'active';
    const shown = (items || []).filter(STATUS_FILTERS[view]).sort(SORTS[view]);
    const onSaved = (next, kind) => {
        setItems(next);
        reloadBoot();
        if (kind === 'status') toast(editing?.status === 'active' ? 'Группа завершена — рекрутеры её больше не выбирают' : 'Группа снова принимает кандидатов', 'success');
        else toast(editing?.id ? 'Группа сохранена' : 'Группа создана', 'success');
    };
    // Места устарели, пока окно было открыто: свежий список и свежая строка группы в окне (минимум степпера, «Сейчас в группе»).
    const onStale = async (groupId) => {
        const fresh = await load();
        const row = (fresh || []).find((group) => group.id === groupId);
        if (row) setEditing(row);
    };

    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
                {done > 0 && (
                    <IosSegmented value={filter} onChange={setFilter} ariaLabel="Какие группы"
                                  options={[{ value: 'active', label: 'Идут' }, { value: 'done', label: `Завершены · ${done}` }]} />
                )}
                <div className="ml-auto flex items-center gap-2">
                    <button type="button" className={TOOLBAR_BTN} onClick={load} disabled={loading} aria-label="Обновить" title="Обновить">
                        <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
                    </button>
                    {caps.create_groups && (
                        <button type="button" className={TOOLBAR_BTN_PRIMARY} onClick={() => setEditing({})}>
                            <Plus size={14} /> Новая группа
                        </button>
                    )}
                </div>
            </div>
            {!items ? (loadError ? (
                <div className={`${iosCard} flex flex-col items-center gap-3 py-10 text-[13px] text-slate-600`}>
                    {loadError}
                    <button type="button" className={TOOLBAR_BTN} onClick={load} disabled={loading}>Повторить</button>
                </div>
            ) : <Loading text="Загружаем группы…" />) : shown.length === 0 ? (
                <div className={iosCard}>
                    <Empty>{items.length === 0 ? 'Групп пока нет' : 'Действующих групп нет'}</Empty>
                </div>
            ) : (
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
                    {shown.map((group) => (
                        <GroupCard key={group.id} group={group} showTrainer={lead} onOpen={setEditing} />
                    ))}
                </div>
            )}
            <GroupModal open={Boolean(editing)} group={editing} onClose={() => setEditing(null)} api={api} toast={toast} boot={boot}
                        lead={lead} users={users} onSaved={onSaved} onStale={onStale} />
        </div>
    );
};

export default GroupsTab;
