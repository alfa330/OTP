import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Download, FileText, Loader2 } from 'lucide-react';
import InfoHint from '../common/InfoHint';
import CustomSelect from '../ui/CustomSelect';
import IosDatePicker from '../ui/DatePicker';
import { IosModal, IosSegmented, iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel } from '../ui/ios';
import EmployeeStatusBlock from './EmployeeStatusBlock';
import { DOC_TYPE_OPTIONS, EMPLOYMENT_OPTIONS, EMPLOYMENT_SHORT, SCHEDULE_OPTIONS, blobErrorText, dateText, errorText } from './hrOffice';
import { FIELD, FIELD_INVALID, Field, Loading } from './hrUi';

/*
 * Карточка оформления сотрудника (ТЗ п. 2–4): вид оформления и компания, договор,
 * личные данные для документов, проект и график для табеля, статус.
 *
 * Чего не хватает — красным у поля после «Сохранить» (ответ сервера по полям), а не
 * серой кнопкой. Пояснения — под «i» у подписи. Документы собираются из сохранённых
 * данных: несохранённые правки сначала сохраняются, потом качается файл.
 */
const EMPTY_DATA = {
    iin: '', doc_type: 'id_card', doc_number: '', doc_issuer: '', doc_issued_on: '', citizenship: '', birth_place: '',
    registration_address: '', actual_address: '', project: '', timesheet_group: '', schedule_pattern: '', day_hours: '',
};
const EMPTY_CONTRACT = { employment_type: '', company_id: null, number: '', signed_on: '', amount: '', job_title: '' };

function formFromCard(card) {
    const data = { ...EMPTY_DATA };
    Object.keys(EMPTY_DATA).forEach((key) => {
        const value = card.data?.[key];
        data[key] = value === null || value === undefined ? '' : String(value);
    });
    if (!data.doc_type) data.doc_type = 'id_card';
    const source = card.contract || {};
    const contract = {
        employment_type: source.employment_type || '',
        company_id: source.company_id ?? null,
        number: source.number || '',
        signed_on: source.signed_on || '',
        amount: source.amount === null || source.amount === undefined ? '' : String(source.amount),
        job_title: source.job_title || '',
    };
    return { data, contract };
}

/* ТЗ п. 3: умолчание для нового оформления (СЗоВ и ОП — ГПХ на Eazy Go Rent, Тез — GaBa).
   Подставляется в форму, но не в «исходное»: иначе форма считалась бы несохранённой без
   правок, «Сохранить» была бы серой и умолчание не сохранилось бы (разбор 10.11.2026). */
function withSuggestion(form, card) {
    const contract = { ...form.contract };
    if (!contract.employment_type && card.suggestion?.employment_type) {
        contract.employment_type = card.suggestion.employment_type;
        if (!contract.company_id && card.suggestion.company_id) contract.company_id = card.suggestion.company_id;
    }
    return { ...form, contract };
}

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

export default function HrPersonSheet({ open = true, api, boot, toast, userId, onClose, onSaved, onOpenCompanies }) {
    const [card, setCard] = useState(null);
    const [form, setForm] = useState(null);
    const [initial, setInitial] = useState(null);
    const [errors, setErrors] = useState({});
    const [warnings, setWarnings] = useState([]);
    // Карточка «Учета сотрудников» обновилась не полностью — «Сохранить» остаётся доступной для повтора.
    const [retry, setRetry] = useState(false);
    const [saving, setSaving] = useState(false);
    const [downloading, setDownloading] = useState('');
    const [loadError, setLoadError] = useState('');

    const apply = useCallback((data) => {
        const saved = formFromCard(data);
        setCard(data);
        setForm(withSuggestion(saved, data));
        setInitial(saved);
    }, []);

    useEffect(() => {
        let alive = true;
        api.get(`/people/${userId}`)
            .then(({ data }) => { if (alive) apply(data); })
            .catch((requestError) => { if (alive) setLoadError(errorText(requestError, 'Карточка не открылась')); });
        return () => { alive = false; };
    }, [api, userId, apply]);

    const companies = useMemo(() => (boot.companies || []).filter((item) => item.is_active || item.id === form?.contract.company_id),
        [boot.companies, form?.contract.company_id]);
    const companyOptions = companies.map((item) => ({
        value: item.id, label: item.display_name || item.name,
        meta: item.missing?.length ? 'нет реквизитов' : '',
    }));
    const company = companies.find((item) => item.id === form?.contract.company_id) || null;
    const dirty = form && initial && !same(form, initial);

    const setData = (key) => (value) => setForm((prev) => ({ ...prev, data: { ...prev.data, [key]: value } }));
    const setContract = (key) => (value) => setForm((prev) => ({ ...prev, contract: { ...prev.contract, [key]: value } }));
    const onInput = (setter) => (event) => setter(event.target.value);
    const fieldClass = (key) => `${FIELD} ${errors[key] ? FIELD_INVALID : ''}`;

    const save = async () => {
        setSaving(true);
        setErrors({});
        try {
            const { data } = await api.put(`/people/${userId}`, {
                data: form.data,
                contract: { ...form.contract, auto_number: !form.contract.number },
            });
            apply(data);
            const list = data.warnings || [];
            setWarnings(list);
            const partial = list.some((text) => text.includes('не полностью'));
            setRetry(partial);
            onSaved?.();
            toast?.(partial ? 'Сохранено не полностью — нажмите «Сохранить» ещё раз' : 'Сохранено', partial ? 'warning' : 'success');
            return true;
        } catch (requestError) {
            const fields = requestError?.response?.data?.fields;
            if (fields) {
                setErrors(fields);
                // Прокрутка к первому полю с ошибкой: в длинной форме иначе не видно, что не так.
                window.requestAnimationFrame(() => {
                    document.querySelector('[data-hr-invalid="true"]')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
                });
            }
            toast?.(errorText(requestError, 'Не сохранилось'), 'error');
            return false;
        } finally {
            setSaving(false);
        }
    };

    const download = async (kinds = '') => {
        if (dirty && !(await save())) return;
        setDownloading(kinds || 'all');
        try {
            await api.download('get', `/people/${userId}/documents`, {
                params: kinds ? { kinds } : undefined, fallback: 'документы.zip',
            });
        } catch (requestError) {
            toast?.(await blobErrorText(requestError, 'Документы не собрались'), 'error');
        } finally {
            setDownloading('');
        }
    };

    const invalid = (key) => (errors[key] ? { 'data-hr-invalid': 'true' } : {});
    const projectOptions = [{ value: '', label: card?.data?.project_default ? `По отделу: ${card.data.project_default}` : 'По отделу' }]
        .concat((boot.projects || []).map((item) => ({ value: item, label: item })));

    // Меню документов — по виду оформления в форме: сменили вид — меняется и набор.
    const docTitles = Object.fromEntries((boot.doc_kinds || []).map((item) => [item.kind, item.title]));
    const docKinds = card ? ((boot.doc_kinds_by_type || {})[form?.contract.employment_type || ''] || ['questionnaire']) : [];
    const docItems = docKinds.map((kind) => ({ kind, title: docTitles[kind] || kind }));
    // Подвал стоит и пока карточка грузится (кнопки неактивны): окно сразу в своём росте.
    const footer = (
        <div className="flex w-full flex-wrap items-center justify-end gap-2">
            <button type="button" className={iosBtnSecondary} onClick={onClose} disabled={saving}>Закрыть</button>
            <button type="button" className={iosBtnPrimary} onClick={save} disabled={!card || saving || (!dirty && !retry)}>
                {saving && <Loader2 size={15} className="animate-spin" />} Сохранить
            </button>
        </div>
    );
    const suggestion = card?.suggestion;
    const usesDefault = Boolean(card && !card.contract?.id && suggestion?.employment_type
        && form?.contract.employment_type === suggestion.employment_type
        && (!suggestion.company_id || form?.contract.company_id === suggestion.company_id));
    const chosenType = form?.contract.employment_type || '';
    /* Чего не хватает документам — по тому, что сейчас в форме: подставленное умолчание (вид и
       компания) сохранится перед скачиванием, и «не хватает: договор, компания» было бы неправдой. */
    const filled = (value) => String(value ?? '').trim() !== '';
    const docsMissing = form ? [
        !filled(form.data.iin) && 'ИИН',
        !filled(form.data.doc_number) && 'удостоверение',
        !filled(form.data.registration_address) && 'адрес',
        !(form.contract.employment_type && form.contract.company_id) && 'вид оформления и компания',
        !filled(form.contract.amount) && 'сумма договора',
        company?.missing?.length > 0 && 'реквизиты компании',
    ].filter(Boolean) : [];

    return (
        <IosModal open={open} onClose={onClose} maxWidth="max-w-3xl" title={card?.name || 'Сотрудник'}
                  subtitle={card ? [card.department, card.project].filter(Boolean).join(' · ') : ''} footer={footer}
                  sizeKey={card ? 'ready' : (loadError ? 'error' : 'loading')}>
            {loadError && <div className="p-4 text-[13.5px] text-rose-600">{loadError}</div>}
            {/* Карточка всегда выше экрана — окно сразу встаёт в свой предельный рост (92vh минус шапка
                и подвал), а не открывается коротким и вырастает на ответе сервера (замер 10.11.2026:
                284 → 880 px, с долгим кадром посередине). */}
            {!card && !loadError && (
                <div className="flex items-center justify-center sm:min-h-[calc(92vh-6rem)]">
                    <Loading text="Открываем карточку…" />
                </div>
            )}
            {card && form && (
                <div className="space-y-5 motion-safe:animate-fade-in-soft">
                    {/* Предупреждения сервера — все, а не только про ИИН (тот виден у своего поля). */}
                    {warnings.filter((text) => !text.includes('ИИН')).length > 0 && (
                        <div className="space-y-1 rounded-2xl bg-amber-50 px-4 py-3 text-[13px] leading-relaxed text-amber-900 ring-1 ring-amber-200/70">
                            {warnings.filter((text) => !text.includes('ИИН')).map((text) => <div key={text}>{text}</div>)}
                        </div>
                    )}
                    {/* ТЗ п. 2 и 4: «система автоматически создаёт заполненные документы, которые можно
                        загрузить и распечатать» — на виду, первым блоком, каждый документ своей кнопкой. */}
                    <section className="space-y-1.5" data-hr-documents>
                        <div className="flex items-center gap-1.5">
                            <span className={iosGroupLabel}>Документы для приема</span>
                            <InfoHint text="Собираются сами из данных карточки по шаблонам HR («Шаблоны» в «Приеме»): Word-файлы — скачать и распечатать. Несохранённые правки сохранятся перед скачиванием." />
                        </div>
                        <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                            {!chosenType && (
                                <div className="px-4 py-3 text-[13px] text-slate-500">
                                    Выберите вид оформления и компанию ниже — к анкете добавятся договор и остальные документы.
                                </div>
                            )}
                            {docItems.map((item) => (
                                <div key={item.kind} className="flex items-center gap-3 px-4 py-2.5">
                                    <FileText size={16} className="shrink-0 text-slate-400" />
                                    <span className="min-w-0 flex-1 truncate text-[13.5px] text-slate-800">{item.title}</span>
                                    <button type="button" className={iosBtnGhost} onClick={() => download(item.kind)}
                                            disabled={!!downloading || saving}>
                                        {downloading === item.kind ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />} Скачать
                                    </button>
                                </div>
                            ))}
                            <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
                                {docsMissing.length
                                    ? <span className="text-[12.5px] text-amber-700">Не хватает: {docsMissing.join(', ')} — в документах будут пропуски</span>
                                    : <span className="text-[12.5px] text-emerald-700">
                                        {dirty ? 'Всё заполнено — перед скачиванием карточка сохранится' : 'Всё заполнено — документы готовы'}
                                    </span>}
                                <button type="button" className={iosBtnPrimary} onClick={() => download('')} disabled={!!downloading || saving}>
                                    {downloading === 'all' ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />}
                                    {docItems.length > 1 ? 'Скачать все' : 'Скачать'}
                                </button>
                            </div>
                        </div>
                    </section>

                    <section className="space-y-1.5">
                        <div className={iosGroupLabel}>Оформление</div>
                        {usesDefault && (
                            <div className="px-1 text-[12.5px] text-blue-700" data-hr-default-hiring>
                                По умолчанию для {suggestion.for || 'этого отдела'}: {EMPLOYMENT_SHORT[suggestion.employment_type]}, {suggestion.company_name}
                                {suggestion.company_id ? ' — подставлено, можно поменять.' : ' — такой компании нет в справочнике, заведите её в «Справочнике».'}
                            </div>
                        )}
                        <div className={`${iosCard} grid gap-3 p-4 sm:grid-cols-2`}>
                            <Field label="Вид оформления" error={errors.employment_type}>
                                <div {...invalid('employment_type')}>
                                    <IosSegmented value={form.contract.employment_type} onChange={setContract('employment_type')}
                                                  options={EMPLOYMENT_OPTIONS} stretch ariaLabel="Вид оформления" />
                                </div>
                            </Field>
                            <Field label="Компания" error={errors.company_id}
                                   note={company?.missing?.length ? `В справочнике не хватает: ${company.missing.join(', ')}` : ''}
                                   hint="Компания-заказчик из справочника «Компании»: её реквизиты попадают в договор и АВР.">
                                <div {...invalid('company_id')} className="flex gap-2">
                                    <CustomSelect variant="ios" value={form.contract.company_id} onChange={setContract('company_id')}
                                                  options={companyOptions} placeholder="Выберите компанию" className="min-w-0 flex-1"
                                                  ariaLabel="Компания" invalid={!!errors.company_id} searchable
                                                  textClassName="text-[14px] text-slate-900" />
                                    <button type="button" className={iosBtnSecondary} onClick={onOpenCompanies}>Справочник</button>
                                </div>
                            </Field>
                            <Field label="Должность" error={errors.job_title} className="sm:col-span-2"
                                   hint="Идёт в договор и в АВР: «Услуги <должность>».">
                                <input {...invalid('job_title')} className={fieldClass('job_title')} value={form.contract.job_title}
                                       onChange={onInput(setContract('job_title'))} placeholder="Например, HR менеджер" />
                            </Field>
                            <Field label="Вознаграждение в месяц, ₸" error={errors.amount}
                                   hint="Сумма из договора: в договоре — цифрами и прописью, в АВР — стоимостью.">
                                <input {...invalid('amount')} className={fieldClass('amount')} inputMode="decimal"
                                       value={form.contract.amount} onChange={onInput(setContract('amount'))} placeholder="250000" />
                            </Field>
                            <Field label="Номер договора" error={errors.number}
                                   hint="Пусто — номер присвоится сам при сохранении, по порядку в компании. Можно вписать номер бумажного договора.">
                                <input {...invalid('number')} className={fieldClass('number')} value={form.contract.number}
                                       onChange={onInput(setContract('number'))}
                                       placeholder={card.contract.next_number ? `присвоится: ${card.contract.next_number}` : 'присвоится сам'} />
                            </Field>
                            <Field label="Дата договора" error={errors.signed_on}>
                                <IosDatePicker value={form.contract.signed_on} onChange={setContract('signed_on')} allowEmpty
                                               placeholder="Сегодня при сохранении" ariaLabel="Дата договора" />
                            </Field>
                        </div>
                    </section>

                    <section className="space-y-1.5">
                        <div className="flex items-center gap-1.5">
                            <span className={iosGroupLabel}>Документ</span>
                            <InfoHint text="ИИН, документ и адреса видит только отдел кадров. В истории карточки остаётся только факт правки, без значений." />
                        </div>
                        {(card.intake || card.data_updated) && (
                            <div className="px-1 text-[12px] text-slate-500" data-hr-intake-by>
                                {card.intake
                                    ? `Внесено при создании учётки: ${card.intake.by || '—'}, ${dateText(card.intake.at)}`
                                    : `Последняя правка: ${card.data_updated.by || '—'}, ${dateText(card.data_updated.at)}`}
                            </div>
                        )}
                        <div className={`${iosCard} grid gap-3 p-4 sm:grid-cols-2`}>
                            <Field label="ИИН" error={errors.iin} note={warnings.find((text) => text.includes('ИИН')) || ''}>
                                <input {...invalid('iin')} className={fieldClass('iin')} inputMode="numeric" maxLength={14}
                                       value={form.data.iin} onChange={onInput(setData('iin'))} placeholder="12 цифр" />
                            </Field>
                            <Field label="Документ">
                                <IosSegmented value={form.data.doc_type} onChange={setData('doc_type')} options={DOC_TYPE_OPTIONS}
                                              stretch ariaLabel="Вид документа" />
                            </Field>
                            <Field label="Номер документа" error={errors.doc_number}>
                                <input {...invalid('doc_number')} className={fieldClass('doc_number')} value={form.data.doc_number}
                                       onChange={onInput(setData('doc_number'))} />
                            </Field>
                            <Field label="Кем выдан" error={errors.doc_issuer}>
                                <input className={fieldClass('doc_issuer')} value={form.data.doc_issuer}
                                       onChange={onInput(setData('doc_issuer'))} placeholder="МВД РК" />
                            </Field>
                            <Field label="Дата выдачи" error={errors.doc_issued_on}>
                                <div {...invalid('doc_issued_on')}>
                                    <IosDatePicker value={form.data.doc_issued_on} onChange={setData('doc_issued_on')} allowEmpty
                                                   ariaLabel="Дата выдачи" />
                                </div>
                            </Field>
                            <Field label="Гражданство" error={errors.citizenship}>
                                <input className={fieldClass('citizenship')} value={form.data.citizenship}
                                       onChange={onInput(setData('citizenship'))} placeholder="Казахстан" />
                            </Field>
                            <Field label="Адрес регистрации" error={errors.registration_address} className="sm:col-span-2">
                                <input className={fieldClass('registration_address')} value={form.data.registration_address}
                                       onChange={onInput(setData('registration_address'))} placeholder="Город, улица, дом, квартира" />
                            </Field>
                            <Field label="Фактический адрес" error={errors.actual_address} className="sm:col-span-2"
                                   hint="Пусто — в документах будет адрес регистрации.">
                                <input className={fieldClass('actual_address')} value={form.data.actual_address}
                                       onChange={onInput(setData('actual_address'))} />
                            </Field>
                            <Field label="Место рождения" error={errors.birth_place} className="sm:col-span-2">
                                <input className={fieldClass('birth_place')} value={form.data.birth_place}
                                       onChange={onInput(setData('birth_place'))} />
                            </Field>
                        </div>
                    </section>

                    <section className="space-y-1.5">
                        <div className={iosGroupLabel}>Табель</div>
                        <div className={`${iosCard} grid gap-3 p-4 sm:grid-cols-2`}>
                            <Field label="Проект" error={errors.project}
                                   hint="Проект табеля и отбора. Пока не выбран — по отделу.">
                                <CustomSelect variant="ios" value={form.data.project} onChange={setData('project')}
                                              options={projectOptions} ariaLabel="Проект" textClassName="text-[14px] text-slate-900" />
                            </Field>
                            <Field label="Отдел в табеле" error={errors.timesheet_group}
                                   hint="Строка «Отдел» табеля HR («Коммерческий отдел»). Пусто — отдел из iCORE.">
                                <input className={fieldClass('timesheet_group')} value={form.data.timesheet_group}
                                       onChange={onInput(setData('timesheet_group'))} />
                            </Field>
                            <Field label="График" error={errors.schedule_pattern}
                                   hint="Если смен в «Графиках работы» нет и в «Отметках» нет плана: 5/2 и 6/1 — по календарю с праздниками РК.">
                                <IosSegmented value={form.data.schedule_pattern || '5/2'} onChange={setData('schedule_pattern')}
                                              options={SCHEDULE_OPTIONS} stretch ariaLabel="График" />
                            </Field>
                            <Field label="Часов в рабочий день" error={errors.day_hours}
                                   hint="Пусто — по графику: 8 часов у 5/2 и 6/1, 10 у 2/2, или по плану смены.">
                                <input {...invalid('day_hours')} className={fieldClass('day_hours')} inputMode="decimal"
                                       value={form.data.day_hours} onChange={onInput(setData('day_hours'))} placeholder="8" />
                            </Field>
                        </div>
                    </section>

                    <EmployeeStatusBlock api={api} userId={userId} />
                </div>
            )}
        </IosModal>
    );
}
