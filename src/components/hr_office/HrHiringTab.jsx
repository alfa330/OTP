import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Building2, Download, FileSpreadsheet, FileText, History, Loader2, Search } from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import { IosSegmented, iosCard } from '../ui/ios';
import HrPersonSheet from './HrPersonSheet';
import HrCompaniesSheet from './HrCompaniesSheet';
import HrTemplatesSheet from './HrTemplatesSheet';
import HrImportSheet from './HrImportSheet';
import HrJournalSheet from './HrJournalSheet';
import { EMPLOYMENT_SHORT, blobErrorText, contractLine, dateText, errorText, initials } from './hrOffice';
import { Empty, Loading, TOOLBAR_BTN, useSheetState } from './hrUi';

/*
 * «Прием» (ТЗ п. 1–4): кадровик видит всех и сразу — кто ждёт оформления и чего не хватает
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
    const [people, setPeople] = useState(null);
    const [error, setError] = useState('');
    const [query, setQuery] = useState('');
    const [project, setProject] = useState('');
    const [view, setView] = useState('all');
    // Окна держатся в дереве, пока доигрывают уход (hrUi.useSheetState): закрываются плавно.
    const [openId, keptId, openPerson, closePerson, , personToken] = useSheetState(null);
    const [sheet, keptSheet, openSheet, closeSheet, swapSheet, sheetToken] = useSheetState('');

    const load = useCallback(() => {
        api.get('/people')
            .then(({ data }) => { setPeople(data.people || []); setError(''); })
            .catch((requestError) => setError(errorText(requestError, 'Список не загрузился')));
    }, [api]);
    useEffect(() => { load(); }, [load]);

    const projectOptions = useMemo(() => [{ value: '', label: 'Все проекты' }]
        .concat((boot.projects || []).map((item) => ({ value: item, label: item }))), [boot.projects]);

    const visible = useMemo(() => {
        const needle = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
        const list = (people || []).filter((person) => {
            if (project && person.project !== project) return false;
            if (view === 'new' && person.has_contract) return false;
            if (view === 'data' && (!person.has_contract || person.missing.length === 0)) return false;
            if (needle.length) {
                const hay = person.name.toLowerCase();
                if (!needle.every((part) => hay.includes(part))) return false;
            }
            return true;
        });
        // Ждущие оформления — сначала недавно принятые: их и оформляют первыми.
        return view === 'new' ? [...list].sort((a, b) => String(b.hire_date || '').localeCompare(String(a.hire_date || ''))) : list;
    }, [people, query, project, view]);
    const waiting = (people || []).filter((person) => !person.has_contract).length;
    const lacking = (people || []).filter((person) => person.has_contract && person.missing.length > 0).length;
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

    return (
        /* Столбик — flex/gap, а не space-y: окна ниже — его дети, и space-y дал бы им margin-top —
           затемнение начиналось бы на 12 px ниже верха экрана, над ним белая полоса. */
        <div className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2">
                <div className="relative min-w-[220px] flex-1 sm:max-w-sm">
                    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Поиск по ФИО"
                           className="h-9 w-full rounded-xl border-0 bg-white pl-9 pr-3 text-[13.5px] text-slate-900 ring-1 ring-slate-200/70 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/70" />
                </div>
                <CustomSelect variant="ios" value={project} onChange={setProject} options={projectOptions}
                              className="w-44" ariaLabel="Проект" />
                <IosSegmented slide value={view} onChange={setView} ariaLabel="Кого показать"
                              options={VIEW_OPTIONS.map((item) => {
                                  const count = item.value === 'new' ? waiting : item.value === 'data' ? lacking : 0;
                                  return count ? { ...item, label: `${item.label} · ${count}` } : item;
                              })} />
                <div className="ml-auto flex flex-wrap gap-2">
                    <button type="button" className={TOOLBAR_BTN} onClick={() => openSheet('import')}>
                        <FileSpreadsheet size={15} /> Загрузить таблицу
                    </button>
                    <button type="button" className={TOOLBAR_BTN} onClick={() => openSheet('companies')}>
                        <Building2 size={15} /> Компании
                    </button>
                    <button type="button" className={TOOLBAR_BTN} onClick={() => openSheet('templates')}>
                        <FileText size={15} /> Шаблоны
                    </button>
                    <button type="button" className={TOOLBAR_BTN} onClick={() => openSheet('journal')}>
                        <History size={15} /> Журнал
                    </button>
                </div>
            </div>

            {error && <div className={`${iosCard} p-4 text-[13.5px] text-rose-600`}>{error}</div>}
            {!people && !error && <Loading text="Загружаем сотрудников…" />}
            {people && (
                /* Смена отбора («Все» / «Не оформлены», проект) — список проявляется заново, а не подменяется за кадр. */
                <div key={`${view}|${project}`} className={`${iosCard} divide-y divide-slate-100 overflow-hidden motion-safe:animate-fade-in-soft`}>
                    {visible.length === 0
                        ? <Empty>{people.length ? 'Под отбор никто не попал' : 'Сотрудников нет'}</Empty>
                        : visible.map((person) => (
                            <PersonRow key={person.user_id} person={person} onOpen={openPerson}
                                       onDownload={downloadDocuments} downloading={downloading} />
                        ))}
                </div>
            )}
            {people && visible.length > 0 && (
                <div className="px-1 text-[12px] text-slate-400">Показано {visible.length} из {people.length}</div>
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
