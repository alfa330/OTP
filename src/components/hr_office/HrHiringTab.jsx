import React, { useMemo, useState } from 'react';
import { Building2, Download, FileSpreadsheet, FileText, History, Loader2, MoreHorizontal, Search } from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import { IosMenu, IosPager, IosSegmented, iosCard } from '../ui/ios';
import HrPersonSheet from './HrPersonSheet';
import HrCompaniesSheet from './HrCompaniesSheet';
import HrTemplatesSheet from './HrTemplatesSheet';
import HrImportSheet from './HrImportSheet';
import HrJournalSheet from './HrJournalSheet';
import { EMPLOYMENT_SHORT, blobErrorText, contractLine, dateText, initials } from './hrOffice';
import { Empty, Loading, PAGE_SIZE, TOOLBAR_BTN, pagerProps, useNarrowScreen, useServerPage, useSheetState } from './hrUi';

/*
 * «Прием» (ТЗ п. 1–4): кадровик видит всех — по 50 на странице — кто ждёт оформления и чего не хватает
 * для документов. Учётку заводят как раньше («Учет сотрудников», у СВ — свой раздел) и там же
 * вводят данные для документов (п. 2); здесь — вид оформления и компания (по умолчанию п. 3:
 * ОП и СЗоВ — ГПХ, ИП Eazy Go Rent; операторы TEZ — ИП GaBa), договор и готовые документы. Таблицы HR («Список по компаниям»,
 * табель) загружаются кнопкой «Загрузить таблицу» и заполняют проект, должность,
 * вид оформления и компанию у всех найденных разом.
 */
/* «Ждут оформления» — договора ещё нет: кадровику выбрать вид и компанию (ТЗ п. 2), умолчание
   п. 3 видно прямо в строке. «Не хватает данных» — договор есть, но документы выйдут с пропусками. */
const VIEW_OPTIONS = [
    { value: 'all', label: 'Все' },
    { value: 'new', label: 'Ждут оформления' },
    { value: 'data', label: 'Не хватает данных' },
];

function PersonRow({ person, onOpen, onDownload, downloading }) {
    const contract = contractLine(person);
    const hiring = person.default_hiring;
    return (
        <div className="flex items-center gap-1 pr-2 transition hover:bg-slate-50">
            <button type="button" onClick={() => onOpen(person.user_id)}
                    className="flex min-w-0 flex-1 items-center gap-3 px-4 py-3 text-left focus:outline-none focus-visible:bg-slate-50">
                <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-slate-100 text-[12.5px] font-semibold text-slate-600">
                    {initials(person.name)}
                </span>
                <span className="min-w-0 flex-1">
                    <span className="block truncate text-[14px] font-medium text-slate-900">{person.name}</span>
                    <span className="block truncate text-[12.5px] text-slate-500">
                        {[person.job_title, person.department, person.project,
                            !person.has_contract && person.hire_date && `с ${dateText(person.hire_date)}`]
                            .filter(Boolean).join(' · ') || 'отдел не указан'}
                    </span>
                    {/* На телефоне правой колонки нет — оформление и чего не хватает строкой под именем. */}
                    <span className="mt-0.5 block truncate text-[12px] sm:hidden">
                        {person.has_contract ? (
                            <>
                                {contract && <span className="text-slate-600">{contract} · </span>}
                                {person.missing.length > 0
                                    ? <span className="text-amber-700">нет: {person.missing.join(', ')}</span>
                                    : <span className="text-emerald-700">документы готовы</span>}
                            </>
                        ) : <span className="font-medium text-blue-700">Ждёт оформления</span>}
                    </span>
                </span>
                <span className="hidden min-w-0 max-w-[45%] text-right sm:block">
                    {person.has_contract ? (
                        <>
                            <span className="block truncate text-[13px] text-slate-700">{contract}</span>
                            {person.missing.length > 0
                                ? <span className="block truncate text-[12px] text-amber-700">нет: {person.missing.join(', ')}</span>
                                : <span className="block text-[12px] text-emerald-700">документы готовы</span>}
                        </>
                    ) : (
                        <>
                            <span className="block text-[13px] font-medium text-blue-700">Ждёт оформления</span>
                            <span className="block truncate text-[12px] text-slate-500">
                                {hiring
                                    ? `по умолчанию: ${EMPLOYMENT_SHORT[hiring.employment_type] || ''} · ${hiring.company}`
                                    : (contract ? `в карточке: ${contract}` : 'вид и компания не выбраны')}
                            </span>
                        </>
                    )}
                </span>
            </button>
            {/* ТЗ п. 2: документы — одной кнопкой из строки, у кого договор уже оформлен. */}
            {person.has_contract && (
                <button type="button" onClick={() => onDownload(person)} disabled={!!downloading}
                        title="Скачать документы приема" aria-label={`Скачать документы: ${person.name}`}
                        className="grid h-9 w-9 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 disabled:opacity-40">
                    {downloading === person.user_id ? <Loader2 size={16} className="animate-spin" /> : <Download size={16} />}
                </button>
            )}
        </div>
    );
}

export default function HrHiringTab({ api, boot, toast, reloadBoot }) {
    const [query, setQuery] = useState('');
    const [project, setProject] = useState('');
    const [view, setView] = useState('all');
    // Окна держатся в дереве, пока доигрывают уход (hrUi.useSheetState): закрываются плавно.
    const [openId, keptId, openPerson, closePerson, , personToken] = useSheetState(null);
    const [sheet, keptSheet, openSheet, closeSheet, swapSheet, sheetToken] = useSheetState('');

    // Список страницами с сервера (как «Учет сотрудников»): поиск, проект и вкладка — отбор на сервере.
    const list = useServerPage(api, '/people', { q: query, project, view });
    const people = list.data?.people || null;
    const total = list.data?.total || 0;
    const counts = list.data?.counts || {};
    const load = list.reload;
    const error = list.data ? '' : list.error;

    const projectOptions = useMemo(() => [{ value: '', label: 'Все проекты' }]
        .concat((boot.projects || []).map((item) => ({ value: item, label: item }))), [boot.projects]);

    const [downloading, setDownloading] = useState(null);
    const downloadDocuments = async (person) => {
        setDownloading(person.user_id);
        try {
            await api.download('get', `/people/${person.user_id}/documents`, { fallback: `Документы ${person.name}.zip` });
        } catch (requestError) {
            toast?.(await blobErrorText(requestError, 'Документы не собрались'), 'error');
        } finally {
            setDownloading(null);
        }
    };
    const narrow = useNarrowScreen();
    /* «Список по компаниям» — Excel как «Приложение 4» HR, по тому же отбору, что на экране. */
    const [exporting, setExporting] = useState(false);
    const exportList = async () => {
        setExporting(true);
        try {
            const { offset, limit, ...params } = list.query;
            await api.download('get', '/people/export', { params, fallback: 'Список по компаниям.xlsx' });
        } catch (requestError) {
            toast?.(await blobErrorText(requestError, 'Список не собрался'), 'error');
        } finally {
            setExporting(false);
        }
    };

    /* Действия раздела: на компьютере — кнопками в ряд, на телефоне — одним меню «Действия». */
    const actions = [
        { key: 'export', label: 'Список по компаниям', icon: Download, onSelect: exportList, disabled: exporting || !total,
          title: 'Excel как «Список по компаниям» HR — по отбору на экране' },
        { key: 'import', label: 'Загрузить таблицу', icon: FileSpreadsheet, onSelect: () => openSheet('import') },
        { key: 'companies', label: 'Компании', icon: Building2, onSelect: () => openSheet('companies') },
        { key: 'templates', label: 'Шаблоны', icon: FileText, onSelect: () => openSheet('templates') },
        { key: 'journal', label: 'Журнал', icon: History, onSelect: () => openSheet('journal') },
    ];

    return (
        /* Столбик — flex/gap, а не space-y: окна ниже — его дети, и space-y дал бы им margin-top —
           затемнение начиналось бы на 12 px ниже верха экрана, над ним белая полоса. */
        <div className="flex flex-col gap-3">
            {/* На телефоне: поиск во всю ширину, под ним проект и «Действия» одним меню, ниже — вкладки
                отбора во всю ширину. С 640 px — как на компьютере: всё в ряд, кнопки справа. */}
            <div className="flex flex-wrap items-center gap-2">
                <div className="relative order-1 w-full sm:order-none sm:w-auto sm:min-w-48 sm:flex-1 sm:max-w-sm">
                    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Поиск по ФИО"
                           className="h-10 w-full rounded-xl border-0 bg-white pl-9 pr-3 text-[14px] text-slate-900 ring-1 ring-slate-200/70 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/70 sm:h-9 sm:text-[13.5px]" />
                </div>
                <div className="order-2 min-w-0 grow basis-0 sm:order-none sm:grow-0 sm:basis-auto">
                    <CustomSelect variant="ios" value={project} onChange={setProject} options={projectOptions}
                                  className="w-full sm:w-40" ariaLabel="Проект" />
                </div>
                <div className="order-3 sm:hidden">
                    <IosMenu label="Действия «Приема»" align="right" items={actions.filter((item) => !item.disabled)}
                             trigger={{ icon: <MoreHorizontal size={16} />, text: 'Действия', className: TOOLBAR_BTN }} />
                </div>
                <div className="order-4 w-full sm:order-none sm:w-auto">
                    <IosSegmented slide stretch={narrow} value={view} onChange={setView} ariaLabel="Кого показать"
                                  options={VIEW_OPTIONS.map((item) => {
                                      const count = item.value === 'all' ? 0 : counts[item.value] || 0;
                                      return count ? { ...item, label: `${item.label} · ${count}` } : item;
                                  })} />
                </div>
                <div className="ml-auto hidden flex-wrap gap-2 sm:flex">
                    {actions.map(({ key, label, icon: Icon, onSelect, disabled, title }) => (
                        <button key={key} type="button" className={TOOLBAR_BTN} onClick={onSelect} disabled={disabled} title={title}>
                            {key === 'export' && exporting ? <Loader2 size={15} className="animate-spin" /> : <Icon size={15} />} {label}
                        </button>
                    ))}
                </div>
            </div>

            {error && <div className={`${iosCard} p-4 text-[13.5px] text-rose-600`}>{error}</div>}
            {!people && !error && <Loading text="Загружаем сотрудников…" />}
            {people && (
                /* Смена отбора («Все» / «Не оформлены», проект) — список проявляется заново, а не подменяется за кадр;
                   пока идёт следующая страница, прежняя чуть гаснет. */
                <div key={`${view}|${project}`}
                     className={`${iosCard} overflow-hidden transition-opacity duration-200 motion-safe:animate-fade-in-soft ${list.loading || list.searching ? 'opacity-60' : ''}`}>
                    <div className="divide-y divide-slate-100">
                        {people.length === 0
                            ? <Empty>{counts.all ? 'Под отбор никто не попал' : 'Сотрудников нет'}</Empty>
                            : people.map((person) => (
                                <PersonRow key={person.user_id} person={person} onOpen={openPerson}
                                           onDownload={downloadDocuments} downloading={downloading} />
                            ))}
                    </div>
                    {total > PAGE_SIZE && (
                        <div className="border-t border-slate-100 px-4 py-2.5">
                            <IosPager {...pagerProps(list.page, total)} onPage={list.setPage} unit="сотрудники" />
                        </div>
                    )}
                </div>
            )}

            {keptId && (
                <HrPersonSheet key={personToken} open={openId === keptId} api={api} boot={boot} toast={toast} userId={keptId}
                               onClose={closePerson} onSaved={load}
                               onOpenCompanies={() => openSheet('companies')} />
            )}
            {keptSheet === 'companies' && (
                <HrCompaniesSheet key={sheetToken} open={sheet === 'companies'} api={api} toast={toast} onClose={closeSheet}
                                  onChanged={() => { reloadBoot(); load(); }} />
            )}
            {keptSheet === 'templates' && (
                <HrTemplatesSheet key={sheetToken} open={sheet === 'templates'} api={api} toast={toast} onClose={closeSheet} />
            )}
            {keptSheet === 'journal' && (
                <HrJournalSheet key={sheetToken} open={sheet === 'journal'} api={api} section="intake" today={boot.today} onClose={closeSheet} />
            )}
            {keptSheet === 'import' && (
                <HrImportSheet key={sheetToken} open={sheet === 'import'} api={api} boot={boot} toast={toast} onClose={closeSheet}
                               onApplied={() => { reloadBoot(); load(); }} onOpenJournal={() => swapSheet('journal')} />
            )}
        </div>
    );
}
