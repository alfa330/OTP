import React, { useCallback, useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import IosDatePicker from '../ui/DatePicker';
import { IosSegmented, iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel } from '../ui/ios';
import { PERIOD_STATUSES } from '../../utils/employmentStatus';
import { dateText, errorText, todayIso } from './hrOffice';

/*
 * Статус сотрудника (ТЗ HR «iCore 4», п. 5): «Работает», отпуск, без содержания /
 * приостановление договора, больничный — с подписями по виду оформления.
 *
 * Меняется своей ручкой /api/hr_office/people/<id>/status одной транзакцией (разбор
 * 10.11.2026): ручка графика при наложении режет прежний период, а не заменяет его, и
 * укоротить отпуск или вернуть «Работает» через неё было нельзя без потерь. «Увольнение»
 * здесь только показывается — у него причина и ЧС, его меняют в «Учете сотрудников».
 *
 * Два режима: по user_id (карточка «Приема») и по строке «Отметок» (ФИО и карточка
 * Workpace/Clockster): найденный человек всегда подписан ФИО и отделом — подменить его
 * тёзкой незаметно нельзя. Нет прав — блока нет.
 */
const PERIOD_TONE = {
    annual_leave: 'bg-emerald-50 text-emerald-700 ring-emerald-100',
    bs: 'bg-amber-50 text-amber-700 ring-amber-100',
    sick_leave: 'bg-sky-50 text-sky-700 ring-sky-100',
    dismissal: 'bg-slate-100 text-slate-600 ring-slate-200',
};
const DATE_INVALID = '!ring-2 !ring-rose-400/80';

function periodText(period) {
    if (!period) return '';
    if (period.end && period.end !== period.start) return `${dateText(period.start)} – ${dateText(period.end)}`;
    if (period.end) return dateText(period.start);
    return `с ${dateText(period.start)}`;
}

export default function EmployeeStatusBlock({ api, userId = null, name = '', employeeId = '', onChanged = null, title = 'Статус' }) {
    const [people, setPeople] = useState(null);
    const [chosen, setChosen] = useState(userId);
    const [hidden, setHidden] = useState(false);
    const [editing, setEditing] = useState(false);
    const [form, setForm] = useState({ status: 'working', start: todayIso(), end: todayIso() });
    const [errors, setErrors] = useState({});
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    const load = useCallback(async () => {
        try {
            if (userId) {
                const { data } = await api.get(`/people/${userId}/status`);
                setPeople([data]);
                setChosen(data.user_id);
            } else if (name) {
                const { data } = await api.get('/status/lookup', { name, employee_id: employeeId || undefined });
                const found = data.people || [];
                setPeople(found);
                setChosen((current) => (found.some((item) => item.user_id === current) ? current : found[0]?.user_id ?? null));
            }
        } catch (requestError) {
            if (requestError?.response?.status === 403) setHidden(true);
            else setError(errorText(requestError, 'Статус не загрузился'));
        }
    }, [api, userId, name, employeeId]);

    useEffect(() => {
        setPeople(null);
        setEditing(false);
        setError('');
        load();
    }, [load]);

    if (hidden) return null;
    const person = (people || []).find((item) => item.user_id === chosen) || null;
    const current = person?.period || null;
    const editable = !current || current.editable !== false;

    const startEdit = () => {
        setForm({
            status: current && PERIOD_STATUSES.includes(current.code) ? current.code : 'working',
            start: current?.start || todayIso(),
            end: current?.end || current?.start || todayIso(),
        });
        setErrors({});
        setError('');
        setEditing(true);
    };

    const save = async () => {
        if (!person) return;
        const periodStatus = PERIOD_STATUSES.includes(form.status);
        if (periodStatus && (!form.start || !form.end || form.end < form.start)) {
            setErrors({ start: !form.start, end: !form.end || form.end < form.start });
            return;
        }
        setSaving(true);
        setErrors({});
        setError('');
        try {
            const { data } = await api.post(`/people/${person.user_id}/status`, {
                status: form.status,
                start: periodStatus ? form.start : undefined,
                end: periodStatus ? form.end : undefined,
                period_id: current?.id ?? null,
            });
            setPeople((prev) => (prev || []).map((item) => (item.user_id === data.user_id ? { ...item, ...data } : item)));
            setEditing(false);
            onChanged?.();
        } catch (requestError) {
            const fields = requestError?.response?.data?.fields;
            if (fields) setErrors({ start: !!fields.start, end: !!fields.end });
            setError(errorText(requestError, 'Статус не сохранился'));
        } finally {
            setSaving(false);
        }
    };

    const options = person?.options || [];
    const nothingToSave = form.status === 'working' && !current;
    return (
        <section className="space-y-1.5">
            <div className={iosGroupLabel}>{title}</div>
            <div className={`${iosCard} space-y-3 p-4`}>
                {people === null && !error && (
                    <div className="flex items-center gap-2 text-[13px] text-slate-500">
                        <Loader2 size={14} className="animate-spin" /> Загружаем статус…
                    </div>
                )}
                {people && people.length === 0 && (
                    <div className="text-[13px] text-slate-500">Учётки iCORE с таким ФИО не нашлось — статус ставится в её карточке</div>
                )}
                {people && people.length > 1 && (
                    <CustomSelect variant="ios" value={chosen} onChange={(value) => { setChosen(value); setEditing(false); }}
                                  ariaLabel="Сотрудник"
                                  options={people.map((item) => ({ value: item.user_id, label: item.name, meta: item.department }))} />
                )}
                {person && !userId && people.length === 1 && (
                    <div className="text-[12.5px] text-slate-500">{[person.name, person.department].filter(Boolean).join(' · ')}</div>
                )}
                {person && !editing && (
                    <div className="flex flex-wrap items-center justify-between gap-3">
                        <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                                {current ? (
                                    <span className={`rounded-lg px-2 py-0.5 text-[13px] font-semibold ring-1 ${PERIOD_TONE[current.code] || PERIOD_TONE.dismissal}`}>
                                        {current.label}
                                    </span>
                                ) : (
                                    <span className="text-[14px] font-medium text-slate-900">{person.label}</span>
                                )}
                                {current && <span className="text-[13px] tabular-nums text-slate-500">{periodText(current)}</span>}
                            </div>
                            {person.upcoming && (
                                <div className="mt-1 text-[12.5px] text-slate-500">
                                    Дальше: {person.upcoming.label} · {periodText(person.upcoming)}
                                </div>
                            )}
                            {!editable && <div className="mt-1 text-[12px] text-slate-400">Увольнение меняется в «Учете сотрудников»</div>}
                        </div>
                        {editable && <button type="button" className={iosBtnSecondary} onClick={startEdit}>Изменить</button>}
                    </div>
                )}
                {person && editing && (
                    <div className="space-y-3">
                        {options.length <= 3 ? (
                            <IosSegmented value={form.status} onChange={(value) => setForm((prev) => ({ ...prev, status: value }))}
                                          options={options} stretch ariaLabel="Статус" />
                        ) : (
                            <CustomSelect variant="ios" value={form.status} ariaLabel="Статус"
                                          onChange={(value) => setForm((prev) => ({ ...prev, status: value }))}
                                          options={options} textClassName="text-[14px] text-slate-900" />
                        )}
                        {PERIOD_STATUSES.includes(form.status) && (
                            <div className="grid grid-cols-2 gap-2">
                                <IosDatePicker value={form.start} ariaLabel="С какого дня" triggerClassName={errors.start ? DATE_INVALID : ''}
                                               onChange={(value) => setForm((prev) => ({ ...prev, start: value, end: prev.end < value ? value : prev.end }))} />
                                <IosDatePicker value={form.end} min={form.start} ariaLabel="По какой день" triggerClassName={errors.end ? DATE_INVALID : ''}
                                               onChange={(value) => setForm((prev) => ({ ...prev, end: value }))} />
                            </div>
                        )}
                        {form.status === 'working' && current && (
                            <div className="text-[12.5px] text-slate-500">
                                {current.start < todayIso()
                                    ? `«${current.label}» закончится вчерашним днём`
                                    : `«${current.label}» будет снят`}
                            </div>
                        )}
                        <div className="flex justify-end gap-2">
                            <button type="button" className={iosBtnGhost} onClick={() => setEditing(false)} disabled={saving}>Отмена</button>
                            <button type="button" className={iosBtnPrimary} onClick={save} disabled={saving || nothingToSave}>
                                {saving && <Loader2 size={14} className="animate-spin" />} Сохранить
                            </button>
                        </div>
                    </div>
                )}
                {error && <div className="text-[12.5px] text-rose-600">{error}</div>}
            </div>
        </section>
    );
}
