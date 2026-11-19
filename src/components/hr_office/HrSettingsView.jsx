import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { BookOpen, ChevronLeft, ChevronRight, History, Loader2, Plus, Trash2 } from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import InfoHint from '../common/InfoHint';
import useIsMobileShell from '../common/useIsMobileShell';
import { IosSegmented, iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel } from '../ui/ios';
import HrJournalSheet from './HrJournalSheet';
import { errorText, plural } from './hrOffice';
import { Empty, FIELD, FIELD_INVALID, Field, Loading, TOOLBAR_BTN, TOOLBAR_BTN_PRIMARY, useSheetState } from './hrUi';

/*
 * «Настройки» «Кадров» — вид внутри раздела: шапка «Кадры» с вкладками «Прием / Табель / АВР» и сайдбар портала
 * остаются на месте, шестерёнка в углу шапки меняет содержимое под ней (владелец 19.11.2026, по шагам: «отдельной
 * кнопкой в углу», «не модальным окном», «не на весь экран — в рамках раздела, сайдбар и прием/табель/авр на месте»).
 * Первая просьба дня — «раздел настройки, чтобы можно было задавать правило: для этих по умолчанию этот ИП и т. д.».
 *
 * Правило: отделы + кому в отделе → вид оформления, компания, должность (hr_office/defaults.py). Вид и компания
 * подставляются новым сотрудникам без договора в «Приеме», подсказкой — в окне создания учётки; должность — в
 * договор, табель и АВР тем, у кого своей нет.
 *
 * Компьютер — как список и содержимое в приложениях macOS: слева правила, справа выбранное (первое выбрано сразу).
 * Телефон — список, по нажатию правило со стрелкой «назад». Журнал — окном, как у остальных вкладок «Кадров».
 * Несохранённую правку молча не теряем: уход с правила (другое правило, вкладка, шестерёнка) спрашивает внизу
 * «Изменения не сохранены» — как диалог macOS. guardRef — через него HrOfficeView спрашивает перед сменой вкладки.
 */
const HOW_IT_WORKS = 'Вид и компания подставляются сотрудникам, у которых ещё нет договора: в «Приеме» — в строке и '
    + 'в карточке, в окне создания учётки — подсказкой. Должность — в договор, табель и АВР всем, у кого своей нет ни '
    + 'в карточке, ни в договоре. Правило для операторов или супервайзеров сильнее правила «Всем» того же отдела. '
    + 'Вид и компанию в готовых договорах правила не меняют.';
const SCOPE_HINT = 'Операторам — вместе со стажёрами и тренерами. Правило для роли сильнее правила «Всем» '
    + 'того же отдела: так можно задать компанию всему отделу, а должность — только операторам.';
const EMPTY_FORM = { department_ids: [], role_scope: 'all', employment_type: '', company_id: '', job_title: '' };
const NEW_RULE = {};

function formOf(rule) {
    if (!rule?.id) return { ...EMPTY_FORM };
    return {
        department_ids: (rule.department_ids || []).map(String),
        role_scope: rule.role_scope || 'all',
        employment_type: rule.employment_type || '',
        company_id: rule.company_id ? String(rule.company_id) : '',
        job_title: rule.job_title || '',
    };
}

// Правка «та же», если совпадает по смыслу: порядок отделов и пробелы вокруг должности не в счёт.
const comparable = (form) => JSON.stringify({
    ...form, department_ids: [...form.department_ids].map(String).sort(), job_title: form.job_title.trim(),
});

/* «отдела «Тез КЦ»», «отделов «СЗоВ» и «Отдел продаж»»: названия отделов не склоняются, поэтому — в кавычках после
   «отдела / отделов» (как defaults.departments_of на сервере). */
function departmentsOf(names) {
    if (!names.length) return '';
    const quoted = names.map((name) => `«${name}»`);
    const list = quoted.length > 1 ? `${quoted.slice(0, -1).join(', ')} и ${quoted[quoted.length - 1]}` : quoted[0];
    return `${names.length > 1 ? 'отделов' : 'отдела'} ${list}`;
}

function outcome(rule) {
    return [rule.employment_label, rule.company].filter(Boolean).join(' · ');
}

const waitingText = (count) => `сейчас подставляется ${count} ${plural(count, ['сотруднику', 'сотрудникам', 'сотрудникам'])}`;

function RoleChip({ children, active = false }) {
    return (
        <span className={`rounded-md px-1.5 py-0.5 text-[11.5px] font-medium ${active ? 'bg-white/80 text-blue-700' : 'bg-slate-100 text-slate-600'}`}>
            {children}
        </span>
    );
}

/* Строка списка. На компьютере выбранная — мягкой голубой заливкой (как список в приложениях macOS), на телефоне —
   строка «Настроек» iOS с шевроном. */
function RuleItem({ rule, active, narrow, onOpen }) {
    const what = outcome(rule);
    return (
        <button type="button" onClick={() => onOpen(rule.id)} data-hr-rule={rule.id} aria-current={active ? 'true' : undefined}
                className={`flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left transition focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 ${
                    active ? 'bg-blue-50 ring-1 ring-blue-200/80' : 'hover:bg-slate-50 active:bg-slate-50'}`}>
            <span className="min-w-0 grow basis-0">
                <span className={`block truncate text-[14px] font-medium ${active ? 'text-blue-800' : 'text-slate-900'}`}>
                    {rule.departments.join(', ')}
                </span>
                <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px]">
                    <RoleChip active={active}>{rule.role_title}</RoleChip>
                    {what && <span className="text-slate-700">{what}</span>}
                </span>
                {rule.job_title && <span className="mt-0.5 block truncate text-[12.5px] text-slate-500">должность: {rule.job_title}</span>}
                {rule.waiting > 0 && <span className="mt-0.5 block text-[12px] text-slate-400">{waitingText(rule.waiting)}</span>}
                {rule.company_inactive && (
                    <span className="mt-0.5 block text-[12px] text-amber-700">компания помечена недействующей — не подставляется</span>
                )}
            </span>
            {narrow && <ChevronRight size={17} className="shrink-0 text-slate-300" />}
        </button>
    );
}

function RuleForm({ data, rule, form, errors, onField }) {
    const departmentOptions = useMemo(() => (data?.departments || [])
        .map((row) => ({ value: String(row.id), label: row.full_name || row.name })), [data]);
    // Отдел из правила, который уже не активен, — остаётся в выборе (сервер его не выкинет).
    const knownIds = new Set(departmentOptions.map((option) => option.value));
    const extraDepartments = (rule?.department_ids || []).map(String).filter((id) => !knownIds.has(id))
        .map((id) => ({ value: id, label: rule.departments?.[rule.department_ids.map(String).indexOf(id)] || `Отдел №${id}` }));
    const companyOptions = [{ value: '', label: 'Не задавать' }]
        .concat((data?.companies || []).map((company) => ({ value: String(company.id), label: company.name })));
    if (rule?.company_id && !companyOptions.some((option) => option.value === String(rule.company_id))) {
        companyOptions.push({ value: String(rule.company_id), label: rule.company || 'Недействующая компания' });
    }
    const typeOptions = [{ value: '', label: 'Не задавать' }].concat(data?.employment_types || []);
    const scopeOptions = data?.role_scopes || [];

    const names = (ids) => ids.map((id) => {
        const option = departmentOptions.find((item) => item.value === id) || extraDepartments.find((item) => item.value === id);
        return option ? option.label.split(' — ')[0] : '';
    }).filter(Boolean);
    const picked = names(form.department_ids);
    const scopeLabel = scopeOptions.find((option) => option.value === form.role_scope)?.label || '';
    const preview = [
        typeOptions.find((option) => option.value === form.employment_type && option.value)?.label,
        companyOptions.find((option) => option.value === form.company_id && option.value)?.label,
        form.job_title.trim() && `должность «${form.job_title.trim()}»`,
    ].filter(Boolean).join(', ');

    return (
        <div className="flex flex-col gap-4" data-hr-rule-form>
            <section className="flex flex-col gap-1.5">
                <div className={iosGroupLabel}>Кому</div>
                <div className={`${iosCard} grid gap-3 p-4`}>
                    <Field label="Отделы" error={errors.department_ids}
                           hint="Правило действует для сотрудников этих отделов. Можно выбрать несколько.">
                        <CustomSelect variant="ios" multiple searchable value={form.department_ids}
                                      onChange={onField('department_ids')} options={departmentOptions.concat(extraDepartments)}
                                      placeholder="Выберите отделы" ariaLabel="Отделы" invalid={!!errors.department_ids}
                                      renderValue={(values) => names(values).join(', ') || 'Выберите отделы'}
                                      textClassName="text-[14px] text-slate-900" />
                    </Field>
                    <Field label="Кому в отделе" error={errors.role_scope} hint={SCOPE_HINT}>
                        <IosSegmented value={form.role_scope} onChange={onField('role_scope')} options={scopeOptions}
                                      stretch ariaLabel="Кому в отделе" />
                    </Field>
                </div>
            </section>
            <section className="flex flex-col gap-1.5">
                <div className={iosGroupLabel}>Что подставлять</div>
                <div className={`${iosCard} grid gap-3 p-4 sm:grid-cols-2`}>
                    <Field label="Вид оформления" error={errors.employment_type} className="sm:col-span-2">
                        <div className={errors.employment_type ? `rounded-xl ${FIELD_INVALID}` : ''}>
                            <IosSegmented value={form.employment_type} onChange={onField('employment_type')}
                                          options={typeOptions} stretch ariaLabel="Вид оформления" />
                        </div>
                    </Field>
                    <Field label="Компания" error={errors.company_id}
                           hint="Компания-заказчик из справочника «Компании» во вкладке «Прием».">
                        <CustomSelect variant="ios" searchable value={form.company_id} onChange={onField('company_id')}
                                      options={companyOptions} placeholder="Не задавать" ariaLabel="Компания"
                                      invalid={!!errors.company_id} textClassName="text-[14px] text-slate-900" />
                    </Field>
                    <Field label="Должность" error={errors.job_title}
                           hint="Попадёт в договор, табель и АВР, если у сотрудника своя должность не указана.">
                        <input className={`${FIELD} ${errors.job_title ? FIELD_INVALID : ''}`} value={form.job_title}
                               onChange={onField('job_title')} maxLength={200} placeholder="Не задавать" />
                    </Field>
                </div>
            </section>
            <div className="px-1 text-[12.5px] leading-relaxed text-slate-500" data-hr-rule-preview>
                {picked.length && preview
                    ? <>Новым сотрудникам {departmentsOf(picked)} ({scopeLabel.toLowerCase()}) подставится: <span className="text-slate-700">{preview}</span>.</>
                    : 'Выберите отделы и что подставлять — здесь будет видно, как сработает правило.'}
            </div>
        </div>
    );
}

/* onChanged — правила поменялись: вкладки «Кадров» перечитывают строки (умолчания «Приема», должность в табеле и
   АВР). guardRef — HrOfficeView кладёт сюда смену вкладки, а вид сначала спрашивает про несохранённую правку. */
export default function HrSettingsView({ api, boot, toast, onChanged, guardRef }) {
    const narrow = useIsMobileShell();
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [selectedId, setSelectedId] = useState(null);   // id правила, 'new' — новое, null — ничего
    const [form, setForm] = useState(EMPTY_FORM);
    const [errors, setErrors] = useState({});
    const [saving, setSaving] = useState('');
    // Двойное нажатие «Сохранить» в одном кадре (до перерисовки с disabled) не шлёт второй запрос.
    const busy = useRef(false);
    const [confirmDelete, setConfirmDelete] = useState(false);
    // Куда человек уходит с несохранённой правкой — ждёт ответа «Сохранить / Не сохранять».
    const [pending, setPending] = useState(null);
    const [journal, keptJournal, openJournal, closeJournal, , journalToken] = useSheetState(false);
    // Свежий список правил для действий после ответа сервера (выбрать сохранённое, первое после удаления).
    const rulesRef = useRef([]);
    const applyData = (next) => {
        rulesRef.current = next?.rules || [];
        setData(next);
    };

    useEffect(() => {
        api.get('/settings/rules')
            .then(({ data: next }) => { applyData(next); setError(''); })
            .catch((requestError) => setError(errorText(requestError, 'Настройки не загрузились')));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [api]);

    const rules = data?.rules || [];
    const rule = selectedId === 'new' ? NEW_RULE : (rules.find((item) => item.id === selectedId) || null);
    const dirty = Boolean(rule) && comparable(form) !== comparable(formOf(rule));

    const select = (id) => {
        const target = id === 'new' ? NEW_RULE : (rulesRef.current.find((item) => item.id === id) || null);
        setSelectedId(target ? (id === 'new' ? 'new' : target.id) : null);
        setForm(formOf(target));
        setErrors({});
        setConfirmDelete(false);
        setPending(null);
    };
    // Компьютер: сразу выбрано первое правило — справа не пусто. Телефон начинает со списка.
    const autoPicked = useRef(false);
    useEffect(() => {
        if (!data || narrow || autoPicked.current) return;
        autoPicked.current = true;
        if (data.rules.length) select(data.rules[0].id);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [data, narrow]);

    // Уйти с правила: правка не сохранена — сначала вопрос внизу правила, иначе сразу.
    const attempt = (action) => {
        if (saving) return;
        if (dirty) {
            setPending({ run: action });
            return;
        }
        action();
    };
    // Смена вкладки и шестерёнка в шапке «Кадров» тоже спрашивают (HrOfficeView).
    useLayoutEffect(() => {
        if (guardRef) guardRef.current = attempt;
    });
    useEffect(() => () => { if (guardRef) guardRef.current = null; }, [guardRef]);

    // Ошибки, что зависят от всей формы, снимает правка любого её поля — при сохранении сервер проверит заново:
    // конфликт с другим правилом (стоит у «Отделов», когда отделы выбраны) и «задайте вид, компанию или должность».
    const onField = (key) => (value) => {
        setForm((prev) => ({ ...prev, [key]: value?.target ? value.target.value : value }));
        setPending(null);
        setErrors((prev) => {
            const next = { ...prev, [key]: '' };
            if (form.department_ids.length) next.department_ids = '';
            if (key === 'company_id' || key === 'job_title') next.employment_type = '';
            return Object.keys(prev).some((name) => prev[name] && !next[name]) ? next : prev;
        });
    };

    const save = async (after = null) => {
        if (busy.current || !rule) return;
        busy.current = true;
        setSaving('save');
        setErrors({});
        const body = {
            department_ids: form.department_ids.map(Number),
            role_scope: form.role_scope,
            employment_type: form.employment_type || null,
            company_id: form.company_id ? Number(form.company_id) : null,
            job_title: form.job_title.trim(),
        };
        try {
            const { data: next } = rule.id
                ? await api.put(`/settings/rules/${rule.id}`, body)
                : await api.post('/settings/rules', body);
            applyData(next);
            toast?.(rule.id ? 'Правило сохранено' : 'Правило добавлено', 'success');
            onChanged?.();
            setPending(null);
            if (after) after();
            else if (narrow) select(null);
            else select(next.saved_id ?? rule.id ?? null);
        } catch (requestError) {
            setPending(null);
            const fields = requestError?.response?.data?.fields;
            setErrors(fields || {});
            // Ошибка у поля видна в форме сразу — тост повторял бы её.
            if (!fields) toast?.(errorText(requestError, 'Не сохранилось'), 'error');
        } finally {
            busy.current = false;
            setSaving('');
        }
    };

    const remove = async () => {
        if (!rule?.id) return;
        if (!confirmDelete) {
            setConfirmDelete(true);
            return;
        }
        if (busy.current) return;
        busy.current = true;
        setSaving('delete');
        try {
            const { data: next } = await api.del(`/settings/rules/${rule.id}`);
            applyData(next);
            toast?.('Правило удалено', 'success');
            onChanged?.();
            select(narrow ? null : (next.rules[0]?.id ?? null));
        } catch (requestError) {
            toast?.(errorText(requestError, 'Не удалилось'), 'error');
        } finally {
            busy.current = false;
            setSaving('');
        }
    };

    // «Отмена» — вернуть сохранённое; у нового правила (и на телефоне) — уйти с правила.
    const cancel = () => {
        if (!rule) return;
        if (!rule.id || narrow) {
            select(narrow ? null : (rulesRef.current[0]?.id ?? null));
            return;
        }
        setForm(formOf(rule));
        setErrors({});
        setConfirmDelete(false);
        setPending(null);
    };

    const footer = (
        <div className="flex flex-wrap items-center gap-2" data-hr-rule-footer>
            {pending ? (
                <>
                    <span className="text-[13.5px] font-medium text-slate-700">Изменения не сохранены</span>
                    <span className="grow" />
                    <button type="button" className={iosBtnGhost} onClick={() => setPending(null)} disabled={!!saving}>
                        Остаться
                    </button>
                    <button type="button" className={iosBtnSecondary} disabled={!!saving}
                            onClick={() => { const { run } = pending; setPending(null); run(); }}>
                        Не сохранять
                    </button>
                    <button type="button" className={iosBtnPrimary} onClick={() => save(pending.run)} disabled={!!saving}>
                        {saving === 'save' && <Loader2 size={15} className="animate-spin" />} Сохранить
                    </button>
                </>
            ) : (
                <>
                    {rule?.id && (
                        <button type="button" onClick={remove} disabled={!!saving}
                                className={`${iosBtnGhost} ${confirmDelete ? 'text-rose-600' : 'text-slate-500'}`}>
                            {saving === 'delete' ? <Loader2 size={15} className="animate-spin" /> : <Trash2 size={15} />}
                            {confirmDelete ? 'Удалить правило?' : 'Удалить'}
                        </button>
                    )}
                    <span className="grow" />
                    <button type="button" className={iosBtnSecondary} onClick={cancel} disabled={!!saving}>Отмена</button>
                    <button type="button" className={iosBtnPrimary} onClick={() => save()} disabled={!!saving}>
                        {saving === 'save' && <Loader2 size={15} className="animate-spin" />} Сохранить
                    </button>
                </>
            )}
        </div>
    );

    const ruleTitle = rule && (
        <div>
            <h2 className="flex flex-wrap items-center gap-2 text-[17px] font-semibold tracking-tight text-slate-900">
                {rule.id ? rule.departments.join(', ') : 'Новое правило'}
                {rule.id && <RoleChip>{rule.role_title}</RoleChip>}
            </h2>
            <p className="mt-0.5 text-[13px] text-slate-500">Что подставлять новым сотрудникам при оформлении</p>
        </div>
    );
    const editor = rule && (
        <section key={selectedId} className="flex min-w-0 flex-col gap-4 motion-safe:animate-fade-in-soft" data-hr-rule-editor>
            {narrow ? (
                <button type="button" onClick={() => attempt(() => select(null))}
                        className="-ml-1 flex w-fit items-center gap-0.5 text-[15px] font-medium text-blue-600 active:opacity-60">
                    <ChevronLeft size={20} /> Правила
                </button>
            ) : null}
            {ruleTitle}
            <RuleForm data={data} rule={rule} form={form} errors={errors} onField={onField} />
            {/* На компьютере кнопки прилипают к низу экрана: «Сохранить» видно, как бы ни прокрутили форму. */}
            <div className={`${iosCard} px-4 py-3 ${narrow ? '' : 'sticky bottom-4 z-10 shadow-[0_8px_24px_rgba(15,23,42,0.08)]'}`}>
                {footer}
            </div>
        </section>
    );
    const emptyPane = (
        <div className={`${iosCard} flex flex-col items-center gap-3 px-8 py-12 text-center`}>
            <span className="grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                <BookOpen size={22} />
            </span>
            <div className="text-[15px] font-semibold text-slate-800">
                {rules.length ? 'Выберите правило слева' : 'Правил пока нет'}
            </div>
            <p className="max-w-md text-[13px] leading-relaxed text-slate-500">{HOW_IT_WORKS}</p>
        </div>
    );
    const list = (
        <section className="flex min-w-0 flex-col gap-1.5" data-hr-settings-list>
            <div className={iosGroupLabel}>Правила оформления по умолчанию{data ? ` · ${rules.length}` : ''}</div>
            <div className={`${iosCard} flex flex-col gap-1 p-1.5`}>
                {error && <div className="px-3 py-2 text-[13px] text-rose-600">{error}</div>}
                {!data && !error && <Loading text="Загружаем правила…" />}
                {data && !rules.length && <Empty>Правил нет — новым сотрудникам ничего не подставляется.</Empty>}
                {selectedId === 'new' && !narrow && (
                    <div className="rounded-xl bg-blue-50 px-3 py-2.5 text-[14px] font-medium text-blue-800 ring-1 ring-blue-200/80">
                        Новое правило
                    </div>
                )}
                {rules.map((item) => (
                    <RuleItem key={item.id} rule={item} narrow={narrow} active={!narrow && item.id === selectedId}
                              onOpen={(id) => { if (id !== selectedId) attempt(() => select(id)); }} />
                ))}
            </div>
        </section>
    );

    // gap, а не space-y: окно журнала — ребёнок этого столбика, space-y дало бы ему отступ сверху (полоса над затемнением).
    return (
        <div className="flex flex-col gap-4" data-hr-settings>
            <div className="flex flex-wrap items-center justify-between gap-3">
                {/* «i» — в строке текста: на телефоне текст в две строки, и значок встаёт за последним словом. */}
                <div className="min-w-0 text-[13px] text-slate-500">
                    Кому что подставлять при оформлении: вид договора, компанию и должность
                    <InfoHint text={HOW_IT_WORKS} className="ml-1.5 align-middle" />
                </div>
                <div className="flex flex-wrap gap-2">
                    <button type="button" className={TOOLBAR_BTN} onClick={() => openJournal(true)}>
                        <History size={15} /> Журнал
                    </button>
                    <button type="button" className={TOOLBAR_BTN_PRIMARY} disabled={!data}
                            onClick={() => attempt(() => select('new'))}>
                        <Plus size={15} /> Добавить правило
                    </button>
                </div>
            </div>
            {narrow ? (rule ? editor : list) : (
                <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,400px)_minmax(0,1fr)]">
                    {list}
                    {rule ? editor : (data ? emptyPane : null)}
                </div>
            )}
            {keptJournal && (
                <HrJournalSheet key={journalToken} open={!!journal} api={api} section="settings" today={boot?.today}
                                onClose={closeJournal} />
            )}
        </div>
    );
}
