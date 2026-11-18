import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Minus, Plus } from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import IosDatePicker from '../ui/DatePicker';
import { IosModal, IosSegmented, iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard } from '../ui/ios';
import { GROUP_CAPACITY_MAX, errorOf, groupNameFor, groupSeats, longTime } from './hiringMeta';
import { DATE_TRIGGER, Empty, FIELD, Label, Loading, TOOLBAR_BTN_PRIMARY } from './hiringUi';

/*
 * Вкладка «Группы» — группы на обучение (владелец 18.11.2026): тренер сам заводит группу, задаёт старт и
 * вместимость; рекрутер, приглашая на обучение, видит в каждой группе свободные места, а в полную пригласить
 * не может. Тренер видит и правит свои группы, руководитель рекрутинга — все, он же назначает группе тренера
 * и отдел. Место занимает приглашённый и не отпавший кандидат: отказ до обучения и отсев место освобождают.
 */

const STATUS_FILTERS = { active: (group) => group.status === 'active', done: (group) => group.status !== 'active' };
/* Идущие — ближний старт первым, как их видит рекрутер при выборе; завершённые — недавние первыми. */
const byStart = (a, b) => String(a.start_date).localeCompare(String(b.start_date)) || a.id - b.id;
const SORTS = { active: byStart, done: (a, b) => byStart(b, a) };

/* Карточка группы: название и старт, крупно — сколько мест занято, полоса заполненности. Цвет — только у
   полной группы. */
const GroupCard = ({ group, showTrainer, onOpen }) => {
    const seats = groupSeats(group);
    const details = [`старт ${longTime(group.start_date)}`, showTrainer ? (group.trainer_name || 'без тренера') : null, group.vacancy_name]
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
                        <div className={`h-full rounded-full ${seats.full ? 'bg-amber-500' : 'bg-blue-500'}`}
                             style={{ width: `${Math.round(seats.share * 100)}%` }} />
                    </div>
                )}
            </div>
        </button>
    );
};

/* Вместимость — степпером, как в «Настройках» iOS: «−», число, «+». Набрать с клавиатуры тоже можно. */
const CapacityStepper = ({ value, onChange, min }) => {
    const number = Number(value) || 0;
    const set = (next) => onChange(String(Math.min(GROUP_CAPACITY_MAX, Math.max(1, next))));
    return (
        <div className="flex items-center gap-3">
            <div className="inline-flex h-[42px] items-center rounded-xl bg-slate-100">
                <button type="button" aria-label="Меньше" onClick={() => set(number - 1)} disabled={number <= Math.max(1, min)}
                        className="grid h-full w-10 place-items-center rounded-l-xl text-slate-600 transition hover:bg-slate-200/70 active:scale-95 disabled:opacity-35">
                    <Minus size={15} />
                </button>
                <input value={value} inputMode="numeric" aria-label="Вместимость" placeholder="—"
                       onChange={(event) => onChange(event.target.value.replace(/\D/g, '').slice(0, 3))}
                       className="h-full w-14 bg-transparent text-center text-[16px] font-semibold tabular-nums text-slate-900 placeholder-slate-400 focus:outline-none" />
                {/* Из пустого поля «+» ставит число занятых мест (не меньше 1): меньше указать всё равно нельзя. */}
                <button type="button" aria-label="Больше" onClick={() => set(number ? number + 1 : Math.max(1, min))} disabled={number >= GROUP_CAPACITY_MAX}
                        className="grid h-full w-10 place-items-center rounded-r-xl text-slate-600 transition hover:bg-slate-200/70 active:scale-95 disabled:opacity-35">
                    <Plus size={15} />
                </button>
            </div>
            <span className="text-[13px] text-slate-500">человек</span>
        </div>
    );
};

const emptyForm = { name: '', start_date: '', capacity: '', vacancy_id: '', trainer_id: '', department_id: '' };

/* Окно группы: новая или правка. Тренер заводит группу на себя; тренера и отдел назначает руководитель. */
const GroupModal = ({ open, group, onClose, api, toast, boot, lead, users, onSaved }) => {
    const editing = Boolean(group?.id);
    const [form, setForm] = useState(emptyForm);
    // Пока название не трогали руками, оно идёт за датой старта: «Группа 24.11».
    const [nameTouched, setNameTouched] = useState(false);
    const [saving, setSaving] = useState('');
    useEffect(() => {
        if (!open) return;
        setForm(group?.id ? {
            name: group.name || '', start_date: group.start_date || '', capacity: group.capacity ? String(group.capacity) : '',
            vacancy_id: group.vacancy_id ? String(group.vacancy_id) : '', trainer_id: group.trainer_id ? String(group.trainer_id) : '',
            department_id: group.department_id ? String(group.department_id) : '',
        } : emptyForm);
        setNameTouched(Boolean(group?.id));
    }, [open, group]);
    const taken = editing ? groupSeats(group).taken : 0;
    const set = (key) => (value) => setForm((prev) => ({ ...prev, [key]: value?.target ? value.target.value : value }));
    const setStart = (value) => setForm((prev) => ({ ...prev, start_date: value, name: nameTouched ? prev.name : groupNameFor(value) }));
    const vacancies = (boot.dictionaries?.vacancies || []).filter((v) => v.is_active !== false || String(v.id) === form.vacancy_id);
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
        } finally {
            setSaving('');
        }
    };
    const save = () => {
        const name = form.name.trim() || groupNameFor(form.start_date);
        const capacity = Number(form.capacity);
        if (!form.start_date) return toast('Укажите дату старта', 'error');
        if (!name) return toast('Укажите название группы', 'error');
        if (!Number.isInteger(capacity) || capacity < 1 || capacity > GROUP_CAPACITY_MAX) return toast(`Вместимость — от 1 до ${GROUP_CAPACITY_MAX} человек`, 'error');
        if (capacity < taken) return toast(`В группе уже ${taken} — вместимость не может быть меньше`, 'error');
        const body = { name, start_date: form.start_date, capacity, vacancy_id: form.vacancy_id ? Number(form.vacancy_id) : null };
        if (lead) {
            body.trainer_id = form.trainer_id ? Number(form.trainer_id) : null;
            body.department_id = form.department_id ? Number(form.department_id) : null;
        }
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
                    <Label hint="Кандидату без вакансии подставится вакансия группы.">Вакансия</Label>
                    <CustomSelect variant="ios" value={form.vacancy_id} onChange={set('vacancy_id')} placeholder="Не указана"
                                  options={[{ value: '', label: 'Не указана' }, ...vacancies.map((v) => ({ value: String(v.id), label: v.name }))]} />
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

const GroupsTab = ({ api, toast, boot, caps, reloadBoot }) => {
    const lead = Boolean(caps.lead);
    const [items, setItems] = useState(null);
    const [filter, setFilter] = useState('active');
    // null — окно закрыто; {} — новая группа; строка списка — её правка.
    const [editing, setEditing] = useState(null);
    const [users, setUsers] = useState([]);

    const load = useCallback(() => api.get('/groups')
        .then(({ data }) => setItems(data.items))
        .catch((error) => { setItems([]); toast(errorOf(error, 'Группы не загрузились'), 'error'); }), [api, toast]);
    useEffect(() => { load(); }, [load]);
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

    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
                {done > 0 && (
                    <IosSegmented value={filter} onChange={setFilter} ariaLabel="Какие группы"
                                  options={[{ value: 'active', label: 'Идут' }, { value: 'done', label: `Завершены · ${done}` }]} />
                )}
                <button type="button" className={`${TOOLBAR_BTN_PRIMARY} ml-auto`} onClick={() => setEditing({})}>
                    <Plus size={14} /> Новая группа
                </button>
            </div>
            {!items ? <Loading text="Загружаем группы…" /> : shown.length === 0 ? (
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
                        lead={lead} users={users} onSaved={onSaved} />
        </div>
    );
};

export default GroupsTab;
