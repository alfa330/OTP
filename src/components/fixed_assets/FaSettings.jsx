import React, { useCallback, useEffect, useState } from 'react';
import { Eye, EyeOff, Loader2, Plus, X } from 'lucide-react';
import { IosMenu, IosSegmented, iosBtnPrimary, iosBtnSecondary } from '../ui/ios';
import { blankRequisites, errorFields, errorText, requisitesPayload } from './fixedAssetsMeta';
import {
    CardsSkeleton, ErrorNote, FIELD, FIELD_INVALID, Field, Footnote, FormDate, FormSelect, Group, GroupTitle, PersonSelect,
} from './fixedAssetsUi';

/*
 * «Настройки» — три части, по одной на экран: категории ОС (и кто проверяет ОС категории
 * перед списанием — ТЗ, процесс 3), роли раздела и реквизиты для актов. Роли берутся из
 * отделов портала; «Участники» — поимённые добавки к ним, их назначает суперадмин. Реквизиты
 * (организация, БИН, комиссия) печатаются в шапке актов и в форме ДА-3 акта списания.
 */
const PANES = [
    { value: 'categories', label: 'Категории' },
    { value: 'roles', label: 'Роли' },
    { value: 'requisites', label: 'Реквизиты актов' },
];
const CHECK_OPTIONS = [{ value: 'it', label: 'IT' }, { value: 'head', label: 'Руководитель отдела' }];
const ROLE_ORDER = ['accounting', 'hr', 'it', 'observer'];
const ROLE_DUTIES = {
    accounting: 'Принимает в учёт, получает оригиналы актов, видит всё',
    hr: 'Запускает передачу при увольнении, видит всю историю',
    it: 'Реестр и склад, проверка техники перед списанием, «Новое ОС»',
    observer: 'Видит всё, только чтение',
};

function Requisites({ saved, limit, busy, onSave }) {
    const [form, setForm] = useState(() => blankRequisites(saved));
    const [issues, setIssues] = useState({});
    useEffect(() => { setForm(blankRequisites(saved)); }, [saved]);
    const set = (patch) => { setForm((prev) => ({ ...prev, ...patch })); setIssues({}); };
    const setMember = (index, patch) => set({ members: form.members.map((member, at) => (at === index ? { ...member, ...patch } : member)) });
    const save = async () => { setIssues(await onSave(requisitesPayload(form)) || {}); };
    const text = (key, placeholder = '') => (
        <input className={`${FIELD} ${issues[key] ? FIELD_INVALID : ''}`} value={form[key]} placeholder={placeholder}
               onChange={(event) => set({ [key]: event.target.value })} />
    );
    return (
        <div className="max-w-2xl space-y-6">
            <section className="space-y-3">
                <GroupTitle className="!mb-0">Организация</GroupTitle>
                <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_200px]">
                    <Field label="Название">{text('org_name', 'ТОО «…»')}</Field>
                    <Field label="ИИН/БИН" error={issues.org_bin}>
                        <input className={`${FIELD} tabular-nums ${issues.org_bin ? FIELD_INVALID : ''}`} inputMode="numeric" maxLength={14}
                               value={form.org_bin} onChange={(event) => set({ org_bin: event.target.value.replace(/[^\d\s]/g, '') })} />
                    </Field>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="Руководитель — утверждает акт">{text('head_name', 'Фамилия Имя Отчество')}</Field>
                    <Field label="Главный бухгалтер">{text('chief_accountant_name', 'Фамилия Имя Отчество')}</Field>
                </div>
            </section>

            <section className="space-y-3">
                <GroupTitle className="!mb-0">Комиссия по списанию</GroupTitle>
                <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="Приказ №">{text('commission_order_number')}</Field>
                    <Field label="Дата приказа">
                        <FormDate value={form.commission_order_date} onChange={(value) => set({ commission_order_date: value || '' })}
                                  allowEmpty placeholder="Не указана" ariaLabel="Дата приказа о комиссии" />
                    </Field>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="Председатель — должность">{text('chairman_title', 'Генеральный директор')}</Field>
                    <Field label="Председатель — ФИО">{text('chairman_name', 'Фамилия Имя Отчество')}</Field>
                </div>
                {form.members.map((member, index) => (
                    // eslint-disable-next-line react/no-array-index-key
                    <div key={index} className="grid items-end gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
                        <Field label={`Член комиссии ${index + 1} — должность`}>
                            <input className={FIELD} value={member.title} onChange={(event) => setMember(index, { title: event.target.value })} />
                        </Field>
                        <Field label="ФИО">
                            <input className={FIELD} value={member.name} onChange={(event) => setMember(index, { name: event.target.value })} />
                        </Field>
                        <button type="button" aria-label={`Убрать члена комиссии ${index + 1}`}
                                onClick={() => set({ members: form.members.filter((_, at) => at !== index) })}
                                className="grid h-[41px] w-[41px] place-items-center rounded-xl bg-slate-100 text-slate-500 transition hover:bg-slate-200">
                            <X size={16} />
                        </button>
                    </div>
                ))}
                {form.members.length < limit && (
                    <button type="button" className={iosBtnSecondary} onClick={() => set({ members: [...form.members, { title: '', name: '' }] })}>
                        <Plus size={14} /> Член комиссии
                    </button>
                )}
                <Footnote>Проверивший ОС и руководитель отдела добавляются в акт сами.</Footnote>
            </section>

            <button type="button" className={iosBtnPrimary} onClick={save} disabled={busy}>
                {busy && <Loader2 size={14} className="animate-spin" />} Сохранить
            </button>
        </div>
    );
}

export default function FaSettings({ api, toast, people, needPeople, onSaved }) {
    const [pane, setPane] = useState('categories');
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState('');
    const [fresh, setFresh] = useState({ name: '', check_by: 'it' });
    const [member, setMember] = useState({ user_id: null, role: 'accounting' });

    useEffect(() => { needPeople(); }, [needPeople]);
    const load = useCallback(() => api.get('/settings')
        .then((response) => { setData(response.data); setError(''); })
        .catch((requestError) => setError(errorText(requestError, 'Настройки не загрузились'))), [api]);
    useEffect(() => { load(); }, [load]);

    const run = async (name, work, done) => {
        setBusy(name);
        setError('');
        try {
            const response = await work();
            setData(response.data);
            if (done) toast(done, 'success');
            onSaved?.();
            return null;
        } catch (requestError) {
            setError(errorText(requestError, 'Не удалось сохранить'));
            return errorFields(requestError);
        } finally {
            setBusy('');
        }
    };

    const saveCategory = (category, patch) => run(`category-${category.id}`,
        () => api.put(`/settings/categories/${category.id}`, { name: category.name, check_by: category.check_by, is_active: category.is_active, ...patch }));
    const addCategory = async () => {
        if (!fresh.name.trim()) return;
        const failed = await run('category-new', () => api.post('/settings/categories', { name: fresh.name.trim(), check_by: fresh.check_by }), 'Категория добавлена');
        if (!failed) setFresh({ name: '', check_by: fresh.check_by });
    };
    const addMember = async () => {
        if (!member.user_id) return;
        const failed = await run('member-new', () => api.post('/settings/members', member), 'Участник добавлен');
        if (!failed) setMember({ user_id: null, role: member.role });
    };
    const removeMember = (item) => run(`member-${item.user_id}-${item.role}`,
        () => api.del('/settings/members', { user_id: item.user_id, role: item.role }), 'Участник убран');
    const saveRequisites = (body) => run('requisites', () => api.put('/settings/requisites', body), 'Реквизиты сохранены');

    if (!data && !error) return <CardsSkeleton count={3} height={64} />;
    if (!data) return <ErrorNote text={error} />;

    const roleLabels = data.role_labels || {};
    const roleOptions = ROLE_ORDER.map((role) => ({ value: role, label: roleLabels[role] || role }));
    return (
        <div className="space-y-5">
            <IosSegmented slide value={pane} onChange={setPane} options={PANES} ariaLabel="Часть настроек" />
            <ErrorNote text={error} />

            {pane === 'categories' && (
                <section className="max-w-2xl">
                    <GroupTitle>Кто проверяет ОС перед списанием</GroupTitle>
                    <Group>
                        {(data.categories || []).map((category) => (
                            <div key={category.id} className="flex flex-wrap items-center gap-3 px-4 py-2.5">
                                <div className="min-w-[140px] flex-1">
                                    <div className={`truncate text-[14.5px] font-medium ${category.is_active ? 'text-slate-900' : 'text-slate-400'}`}>{category.name}</div>
                                    <div className="text-[12.5px] tabular-nums text-slate-500">
                                        {[category.assets ? `ОС: ${category.assets}` : '', category.is_active ? '' : 'скрыта'].filter(Boolean).join(' · ') || 'ОС нет'}
                                    </div>
                                </div>
                                <IosSegmented size="xs" value={category.check_by} options={CHECK_OPTIONS} ariaLabel={`Кто проверяет перед списанием: ${category.name}`}
                                              onChange={(value) => value !== category.check_by && saveCategory(category, { check_by: value })} />
                                <IosMenu label={`Действия: ${category.name}`} items={[category.is_active
                                    ? { key: 'hide', label: 'Скрыть из выбора', icon: EyeOff, onSelect: () => saveCategory(category, { is_active: false }) }
                                    : { key: 'show', label: 'Вернуть в выбор', icon: Eye, onSelect: () => saveCategory(category, { is_active: true }) }]} />
                            </div>
                        ))}
                        <div className="flex flex-wrap items-center gap-2 px-4 py-3">
                            <input className={`${FIELD} min-w-[160px] flex-1`} placeholder="Новая категория" value={fresh.name} maxLength={120}
                                   onChange={(event) => setFresh((prev) => ({ ...prev, name: event.target.value }))}
                                   onKeyDown={(event) => { if (event.key === 'Enter') addCategory(); }} />
                            <IosSegmented size="xs" value={fresh.check_by} options={CHECK_OPTIONS} ariaLabel="Кто проверяет новую категорию перед списанием"
                                          onChange={(value) => setFresh((prev) => ({ ...prev, check_by: value }))} />
                            <button type="button" className={iosBtnSecondary} onClick={addCategory} disabled={!fresh.name.trim() || busy === 'category-new'}>
                                {busy === 'category-new' ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} Добавить
                            </button>
                        </div>
                    </Group>
                </section>
            )}

            {pane === 'roles' && (
                <section className="max-w-2xl space-y-3">
                    <Group>
                        {ROLE_ORDER.map((role) => {
                            const source = data.role_departments?.[role];
                            const named = (data.members || []).filter((item) => item.role === role);
                            return (
                                <div key={role} className="px-4 py-3">
                                    <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                                        <span className="text-[14.5px] font-semibold text-slate-900">{roleLabels[role]}</span>
                                        <span className="text-[13px] text-slate-500">
                                            {source
                                                ? (role === 'observer' ? (source.head || 'глава не назначен') : `${source.department} · ${source.people} чел.`)
                                                : 'только участники'}
                                        </span>
                                    </div>
                                    <div className="text-[12.5px] text-slate-500">{ROLE_DUTIES[role]}</div>
                                    {named.length > 0 && (
                                        <div className="mt-2 flex flex-wrap gap-1.5">
                                            {named.map((item) => (
                                                <span key={item.user_id} className="inline-flex items-center gap-1 rounded-full bg-slate-100 py-1 pl-3 pr-1 text-[13px] text-slate-700">
                                                    {item.name}
                                                    {data.can_manage_members && (
                                                        <button type="button" onClick={() => removeMember(item)} aria-label={`Убрать: ${item.name}`}
                                                                className="grid h-5 w-5 place-items-center rounded-full text-slate-400 transition hover:bg-slate-200 hover:text-slate-600">
                                                            <X size={12} />
                                                        </button>
                                                    )}
                                                </span>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </Group>
                    {data.can_manage_members && (
                        <div className="flex flex-wrap items-center gap-2">
                            <div className="min-w-[200px] flex-1">
                                <PersonSelect people={people || []} value={member.user_id} onChange={(value) => setMember((prev) => ({ ...prev, user_id: value }))}
                                              placeholder="Добавить сотрудника в роль" ariaLabel="Сотрудник" />
                            </div>
                            <div className="w-[200px]">
                                <FormSelect value={member.role} onChange={(value) => setMember((prev) => ({ ...prev, role: value || 'accounting' }))} options={roleOptions} ariaLabel="Роль" />
                            </div>
                            <button type="button" className={iosBtnPrimary} onClick={addMember} disabled={!member.user_id || busy === 'member-new'}>
                                {busy === 'member-new' ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} Добавить
                            </button>
                        </div>
                    )}
                    <Footnote>Руководитель отдела ведёт ОС своего отдела. Сотрудник видит свои ОС.</Footnote>
                </section>
            )}

            {pane === 'requisites' && (
                <Requisites saved={data.requisites} limit={data.limits?.commission_members || 8}
                            busy={busy === 'requisites'} onSave={saveRequisites} />
            )}
        </div>
    );
}
