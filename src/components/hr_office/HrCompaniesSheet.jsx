import React, { useCallback, useEffect, useState } from 'react';
import { ChevronRight, Loader2, Plus } from 'lucide-react';
import { IosModal, IosSegmented, IosToggle, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel } from '../ui/ios';
import { errorText } from './hrOffice';
import { Empty, FIELD, FIELD_INVALID, Field, Loading } from './hrUi';

/*
 * Справочник компаний-заказчиков (ТЗ п. 2, 12): реквизиты для договора и АВР.
 * «В лице …» хранится готовой фразой — с падежом и родом подписанта, как в
 * преамбуле договора; подписант — в именительном, как под подписью.
 */
const KIND_OPTIONS = [
    { value: 'ip', label: 'ИП' },
    { value: 'too', label: 'ТОО' },
];
const EMPTY = {
    name: '', kind: 'too', bin: '', legal_address: '', contacts: '', bank_name: '', iik: '', bik: '', kbe: '',
    in_person: '', signer_name: '', signer_position: 'Директор', city: 'Алматы', is_active: true,
};

function CompanyForm({ api, toast, company, onDone }) {
    const [form, setForm] = useState(() => ({ ...EMPTY, ...Object.fromEntries(
        Object.entries(company || {}).filter(([key, value]) => key in EMPTY && value !== null && value !== undefined)) }));
    const [errors, setErrors] = useState({});
    const [saving, setSaving] = useState(false);
    const set = (key) => (event) => setForm((prev) => ({ ...prev, [key]: event?.target ? event.target.value : event }));
    const cls = (key) => `${FIELD} ${errors[key] ? FIELD_INVALID : ''}`;

    const save = async () => {
        setSaving(true);
        setErrors({});
        try {
            if (company?.id) await api.put(`/companies/${company.id}`, form);
            else await api.post('/companies', form);
            toast?.('Компания сохранена', 'success');
            onDone(true);
        } catch (requestError) {
            setErrors(requestError?.response?.data?.fields || {});
            toast?.(errorText(requestError, 'Не сохранилось'), 'error');
        } finally {
            setSaving(false);
        }
    };

    const idLabel = form.kind === 'ip' ? 'ИИН' : 'БИН';
    return (
        <div className="space-y-4">
            <div className={`${iosCard} grid gap-3 p-4 sm:grid-cols-2`}>
                <Field label="Название" error={errors.name} hint="Без «ИП» и «ТОО» — форма выбирается рядом.">
                    <input className={cls('name')} value={form.name} onChange={set('name')} placeholder="Eazy Go Rent" />
                </Field>
                <Field label="Форма">
                    <IosSegmented value={form.kind} onChange={set('kind')} options={KIND_OPTIONS} stretch ariaLabel="Форма" />
                </Field>
                <Field label={idLabel} error={errors.bin}>
                    <input className={cls('bin')} value={form.bin} onChange={set('bin')} inputMode="numeric" maxLength={14} />
                </Field>
                <Field label="Город договора" error={errors.city}>
                    <input className={cls('city')} value={form.city} onChange={set('city')} />
                </Field>
                <Field label="Юридический адрес" error={errors.legal_address} className="sm:col-span-2">
                    <input className={cls('legal_address')} value={form.legal_address} onChange={set('legal_address')} />
                </Field>
                <Field label="Банк" error={errors.bank_name}>
                    <input className={cls('bank_name')} value={form.bank_name} onChange={set('bank_name')} placeholder='АО "Kaspi Bank"' />
                </Field>
                <Field label="Счёт (ИИК)" error={errors.iik}>
                    <input className={cls('iik')} value={form.iik} onChange={set('iik')} placeholder="KZ…" />
                </Field>
                <Field label="БИК" error={errors.bik}>
                    <input className={cls('bik')} value={form.bik} onChange={set('bik')} />
                </Field>
                <Field label="КБе" error={errors.kbe}>
                    <input className={cls('kbe')} value={form.kbe} onChange={set('kbe')} inputMode="numeric" maxLength={2} />
                </Field>
                <Field label="«В лице …» для преамбулы" error={errors.in_person} className="sm:col-span-2"
                       hint="Фраза целиком, как в договоре: «в лице директора Ивановой А.А., действующей на основании Устава». Для ИП — на основании талона или уведомления.">
                    <input className={cls('in_person')} value={form.in_person} onChange={set('in_person')}
                           placeholder="в лице директора …, действующей на основании …" />
                </Field>
                <Field label="Подписант" error={errors.signer_name} hint="Как под подписью: «Иванова А.А.».">
                    <input className={cls('signer_name')} value={form.signer_name} onChange={set('signer_name')} />
                </Field>
                <Field label="Должность подписанта" error={errors.signer_position}>
                    <input className={cls('signer_position')} value={form.signer_position} onChange={set('signer_position')} />
                </Field>
                <Field label="Контакты для АВР" error={errors.contacts} className="sm:col-span-2"
                       hint="«Данные о средствах связи» в шапке АВР: телефон, почта.">
                    <input className={cls('contacts')} value={form.contacts} onChange={set('contacts')} />
                </Field>
                {company?.id && (
                    <div className="flex items-center justify-between gap-3 sm:col-span-2">
                        <span className="text-[13.5px] text-slate-700">Действует — показывать в выборе</span>
                        <IosToggle checked={form.is_active !== false} onChange={(value) => setForm((prev) => ({ ...prev, is_active: value }))} />
                    </div>
                )}
            </div>
            <div className="flex justify-end gap-2">
                <button type="button" className={iosBtnSecondary} onClick={() => onDone(false)} disabled={saving}>Отмена</button>
                <button type="button" className={iosBtnPrimary} onClick={save} disabled={saving}>
                    {saving && <Loader2 size={15} className="animate-spin" />} Сохранить
                </button>
            </div>
        </div>
    );
}

export default function HrCompaniesSheet({ open = true, api, toast, onClose, onChanged }) {
    const [companies, setCompanies] = useState(null);
    const [editing, setEditingState] = useState(null);
    const [error, setError] = useState('');
    // Второй уровень окна (карточка компании) въезжает справа, назад — обратно, как в «Настройках».
    const [motion, setMotion] = useState('');
    const setEditing = (next) => {
        setMotion(next ? 'motion-safe:animate-push-in' : 'motion-safe:animate-pop-in');
        setEditingState(next);
    };

    const load = useCallback(() => api.get('/companies')
        .then(({ data }) => setCompanies(data.companies || []))
        .catch((requestError) => setError(errorText(requestError, 'Справочник не загрузился'))), [api]);
    useEffect(() => { load(); }, [load]);

    const done = (changed) => {
        setEditing(null);
        if (changed) {
            load();
            onChanged?.();
        }
    };

    return (
        <IosModal open={open} onClose={onClose} onBack={editing ? () => setEditing(null) : null} maxWidth="max-w-2xl"
                  title={editing ? (editing.id ? editing.display_name || editing.name : 'Новая компания') : 'Компании'}
                  subtitle={editing ? '' : 'Заказчики в договорах и АВР'}
                  sizeKey={editing ? `edit-${editing.id || 'new'}` : (companies ? 'list' : 'loading')}>
            {editing ? (
                <div key={`edit-${editing.id || 'new'}`} className={motion}>
                    <CompanyForm api={api} toast={toast} company={editing.id ? editing : null} onDone={done} />
                </div>
            ) : (
                <div key="list" className={`space-y-3 ${motion}`}>
                    {error && <div className="text-[13.5px] text-rose-600">{error}</div>}
                    {!companies && !error && <Loading />}
                    {companies && (
                        <div className="space-y-1.5 motion-safe:animate-fade-in-soft">
                            <div className="flex items-end justify-between">
                                <div className={iosGroupLabel}>{companies.length ? `Всего ${companies.length}` : ''}</div>
                                <button type="button" className={iosBtnSecondary} onClick={() => setEditing({})}>
                                    <Plus size={15} /> Компания
                                </button>
                            </div>
                            <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                                {companies.length === 0 && <Empty>Компаний пока нет — добавьте или загрузите таблицу HR</Empty>}
                                {companies.map((item) => (
                                    <button key={item.id} type="button" onClick={() => setEditing(item)}
                                            className={`flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-slate-50 ${item.is_active ? '' : 'opacity-60'}`}>
                                        <span className="min-w-0 flex-1">
                                            <span className="block truncate text-[14px] font-medium text-slate-900">{item.display_name || item.name}</span>
                                            <span className="block truncate text-[12.5px] text-slate-500">
                                                {item.bin ? `${item.id_label} ${item.bin}` : ''}{item.signer_name ? ` · ${item.signer_name}` : ''}
                                                {!item.is_active ? ' · не действует' : ''}
                                            </span>
                                        </span>
                                        {item.missing?.length > 0 && (
                                            <span className="hidden max-w-[45%] truncate text-[12px] text-amber-700 sm:block">
                                                {item.missing.length >= 4 ? 'нет реквизитов' : `нет: ${item.missing.join(', ')}`}
                                            </span>
                                        )}
                                        <ChevronRight size={16} className="shrink-0 text-slate-300" />
                                    </button>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            )}
        </IosModal>
    );
}
