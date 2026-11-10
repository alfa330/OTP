import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
    Building2, CalendarClock, ChevronRight, CircleCheck, FileDown, FileSpreadsheet, FileText, History, Lock, LockOpen,
    MessageSquareText, Receipt, Search, Table2, TriangleAlert, UserPen, UserPlus,
} from 'lucide-react';
import { IosModal, IosSegmented, iosCard } from '../ui/ios';
import { dateText, dayTitle, errorText, plural, timeOf } from './hrOffice';
import { Avatar, ChangeList, SkeletonRows, changesOf } from './HrChanges';

/*
 * Журнал раздела: кто, когда и что сделал. «Прием» — загрузки таблиц, оформление, статусы,
 * компании, шаблоны, скачанные документы; «Табель учета рабочего времени» — правки ячеек
 * «было → стало», комментарии, закрытие месяца; «АВР» — каждый выгруженный акт.
 *
 * Просьба владельца 10.11.2026: «в журнале сделай всё аккуратнее; загрузка файла — удобный
 * вид всех сотрудников, сейчас всё впритык; плавнее переключения». Поэтому записи — по дням,
 * со значком действия и временем справа; у оформления — «было → стало» по полям; загрузка
 * таблицы открывается отдельной страницей окна (как второй уровень «Настроек» iOS): сводка,
 * фильтр «изменено / без изменений / нет в iCORE», поиск и просторный список людей.
 * Обе страницы одного роста — окно не прыгает; страница въезжает сбоку, назад — обратно.
 * Значений ИИН, документа, адресов и сумм в журнале нет — только факт правки.
 */
const TITLES = {
    intake: 'Журнал «Приема»',
    timesheet: 'Журнал табеля',
    acts: 'Журнал АВР',
};

const ACTIONS = {
    import: { icon: FileSpreadsheet, tone: 'bg-emerald-50 text-emerald-600' },
    person: { icon: UserPen, tone: 'bg-blue-50 text-blue-600' },
    // ТЗ п. 2: данные для документов внесены при создании учётки (СВ, админ или HR).
    intake: { icon: UserPlus, tone: 'bg-emerald-50 text-emerald-600' },
    status: { icon: CalendarClock, tone: 'bg-amber-50 text-amber-600' },
    company: { icon: Building2, tone: 'bg-slate-100 text-slate-600' },
    template: { icon: FileText, tone: 'bg-violet-50 text-violet-600' },
    documents: { icon: FileDown, tone: 'bg-sky-50 text-sky-600' },
    cell: { icon: Table2, tone: 'bg-blue-50 text-blue-600' },
    note: { icon: MessageSquareText, tone: 'bg-slate-100 text-slate-600' },
    close: { icon: Lock, tone: 'bg-emerald-50 text-emerald-600' },
    reopen: { icon: LockOpen, tone: 'bg-amber-50 text-amber-600' },
    act: { icon: Receipt, tone: 'bg-emerald-50 text-emerald-600' },
};
const OTHER = { icon: History, tone: 'bg-slate-100 text-slate-500' };

const PEOPLE_GEN = ['сотрудника', 'сотрудников', 'сотрудников'];
// Обе страницы окна — одного роста на компьютере: окно не прыгает ни при поиске, ни при переходе.
const PAGE_HEIGHT = 'sm:h-[min(66vh,640px)]';

/* Что прокручивает список: на компьютере — сам список (у страницы постоянный рост), на телефоне —
   тело окна. Место в журнале запоминается и возвращается у него. */
function scrollHost(node) {
    for (let host = node; host && host !== document.body; host = host.parentElement) {
        const { overflowY } = getComputedStyle(host);
        if (/(auto|scroll)/.test(overflowY) && host.scrollHeight > host.clientHeight) return host;
    }
    return null;
}

function importLine(details) {
    const counts = details?.counts;
    const parts = [];
    if (String(details?.sheet || '').trim()) parts.push(`лист «${String(details.sheet).trim()}»`);
    if (counts) {
        parts.push(`изменено у ${counts.changed} ${plural(counts.changed, PEOPLE_GEN)}`);
        if (counts.same) parts.push(`без изменений ${counts.same}`);
        if (counts.missing) parts.push(`нет в iCORE ${counts.missing}`);
        if (counts.companies_created) parts.push(`новых компаний ${counts.companies_created}`);
    } else if (details?.people?.length) {
        parts.push(`записано ${details.people.length}`);
    }
    return parts.join(' · ');
}

function EntryIcon({ action }) {
    const { icon: Icon, tone } = ACTIONS[action] || OTHER;
    return (
        <span className={`mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-[10px] ${tone}`}>
            <Icon size={16} />
        </span>
    );
}

function Entry({ item, onOpen }) {
    const isImport = item.action === 'import' && item.details && (item.details.people || item.details.counts);
    const time = <span className="shrink-0 pt-0.5 text-[12px] tabular-nums text-slate-400">{timeOf(item.created_at)}</span>;
    if (isImport) {
        return (
            <button type="button" onClick={() => onOpen(item)}
                    className="flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-slate-50 active:bg-slate-100 focus:outline-none focus-visible:bg-slate-50">
                <EntryIcon action={item.action} />
                <span className="min-w-0 flex-1">
                    <span className="block text-[13.5px] font-medium leading-snug text-slate-900">
                        Загружена таблица «{item.details.file || 'без имени'}»
                    </span>
                    <span className="mt-0.5 block text-[12.5px] text-slate-500">{importLine(item.details)}</span>
                    <span className="mt-1 block text-[12px] text-slate-400">{item.actor_name || '—'}</span>
                </span>
                {time}
                <ChevronRight size={16} className="mt-0.5 shrink-0 text-slate-300" />
            </button>
        );
    }
    const changes = item.action === 'person' || item.action === 'intake' ? changesOf(item.details) : [];
    return (
        <div className="flex items-start gap-3 px-4 py-3">
            <EntryIcon action={item.action} />
            <div className="min-w-0 flex-1">
                {item.user_name && <div className="text-[13.5px] font-medium leading-snug text-slate-900">{item.user_name}</div>}
                {item.action === 'intake' && (
                    <div className="mt-0.5 text-[13px] text-slate-600">Учётка создана — внесены данные для документов</div>
                )}
                {changes.length > 0 ? (
                    <div className="mt-1.5 rounded-xl bg-slate-50 px-3 py-1.5 ring-1 ring-slate-100">
                        <ChangeList changes={changes} />
                    </div>
                ) : (
                    <div className={`text-[13.5px] leading-snug ${item.user_name ? 'mt-0.5 text-slate-600' : 'text-slate-900'}`}>{item.summary}</div>
                )}
                <div className="mt-1 text-[12px] text-slate-400">{item.actor_name || '—'}</div>
            </div>
            {time}
        </div>
    );
}

function Stat({ value, label, tone = 'text-slate-900' }) {
    return (
        <div className="rounded-xl bg-slate-50 px-3 py-2 ring-1 ring-slate-100">
            <div className={`text-[17px] font-semibold tabular-nums ${tone}`}>{value}</div>
            <div className="text-[12px] text-slate-500">{label}</div>
        </div>
    );
}

function PersonLine({ name, children, muted = false, trailing = null }) {
    return (
        <div className="flex items-start gap-3 px-4 py-3">
            <Avatar name={name} className={muted ? '!bg-slate-50 !text-slate-400' : ''} />
            <div className="min-w-0 flex-1 pt-0.5">
                <div className={`text-[13.5px] font-medium ${muted ? 'text-slate-600' : 'text-slate-900'}`}>{name}</div>
                {children}
            </div>
            {trailing}
        </div>
    );
}

function ImportDetails({ item }) {
    const details = item.details || {};
    const counts = details.counts || null;
    const tabs = useMemo(() => [
        { key: 'changed', label: 'Изменено', people: details.people || [] },
        { key: 'same', label: 'Без изменений', people: details.same || [] },
        { key: 'conflicts', label: 'Не записано', people: details.conflicts || [] },
        { key: 'skipped', label: 'Пропущены', people: details.skipped || [] },
        { key: 'missing', label: 'Нет в iCORE', people: (details.missing || []).map((name) => ({ name })) },
        { key: 'unresolved', label: 'Тёзки', people: (details.unresolved || []).map((name) => ({ name })) },
        { key: 'duplicates', label: 'Повторы', people: details.duplicates || [] },
    ].filter((tab) => tab.key === 'changed' || tab.people.length > 0), [details]);
    const [tab, setTab] = useState('changed');
    const [query, setQuery] = useState('');
    const needle = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const current = tabs.find((entry) => entry.key === tab) || tabs[0];
    const people = current.people.filter((person) => needle.every((part) => String(person.name || '').toLowerCase().includes(part)));

    return (
        <div className={`flex flex-col gap-3 ${PAGE_HEIGHT}`}>
            <div className={`${iosCard} shrink-0 p-4`}>
                <div className="flex items-center gap-3">
                    <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-emerald-50 text-emerald-600">
                        <FileSpreadsheet size={20} />
                    </span>
                    <div className="min-w-0 flex-1">
                        <div className="truncate text-[14px] font-medium text-slate-900">{details.file || 'Таблица без имени'}</div>
                        <div className="text-[12.5px] text-slate-500">
                            {[details.sheet && `лист «${String(details.sheet).trim()}»`,
                                details.mode === 'group' && details.project && `проект ${details.project}`].filter(Boolean).join(' · ') || '—'}
                        </div>
                    </div>
                </div>
                {counts && (
                    <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
                        <Stat value={counts.changed} label="с изменениями" tone="text-blue-600" />
                        <Stat value={counts.fields} label={plural(counts.fields, ['поле записано', 'поля записано', 'полей записано'])} />
                        <Stat value={counts.same} label="без изменений" />
                        {/* Тёзки в iCORE есть — их не смешиваем с ненайденными: у них своя вкладка. */}
                        <Stat value={counts.missing} label="нет в iCORE" />
                    </div>
                )}
                {!counts && (
                    <div className="mt-3 text-[12.5px] text-slate-500">
                        Загрузка до сверки: тогда сохранялось только то, что записано, — без прежних значений.
                    </div>
                )}
                {details.companies_created?.length > 0 && (
                    <div className="mt-3 text-[12.5px] text-slate-600">
                        Заведены в справочнике: <span className="font-medium">{details.companies_created.join(', ')}</span>
                    </div>
                )}
            </div>

            <div className="flex shrink-0 flex-wrap items-center gap-2">
                {tabs.length > 1 && (
                    <IosSegmented slide value={current.key} onChange={setTab} ariaLabel="Кого показать"
                                  options={tabs.map((entry) => ({ value: entry.key, label: `${entry.label} · ${entry.people.length}` }))} />
                )}
                <div className="relative ml-auto min-w-[200px] flex-1 sm:max-w-[240px]">
                    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Поиск по ФИО"
                           className="h-9 w-full rounded-xl border-0 bg-white pl-9 pr-3 text-[13.5px] text-slate-900 ring-1 ring-slate-200/70 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/70" />
                </div>
            </div>

            <div className={`${iosCard} thin-scroll min-h-0 flex-1 overflow-y-auto`}>
                <div key={current.key} className="divide-y divide-slate-100 motion-safe:animate-fade-in-soft">
                    {people.length === 0 && (
                        <div className="flex min-h-[160px] items-center justify-center px-6 text-center text-[13.5px] text-slate-500">
                            {needle.length ? 'Никого не нашли' : 'Здесь никого'}
                        </div>
                    )}
                    {people.map((person, index) => {
                        const key = `${person.user_id || person.name}-${index}`;
                        if (current.key === 'changed') {
                            return (
                                <PersonLine key={key} name={person.name}>
                                    <ChangeList changes={changesOf(person)} className="mt-1" />
                                </PersonLine>
                            );
                        }
                        if (current.key === 'same') {
                            return <PersonLine key={key} name={person.name}
                                               trailing={<CircleCheck size={17} className="mt-2 shrink-0 text-emerald-500" aria-label="совпадает" />} />;
                        }
                        if (current.key === 'conflicts') {
                            return (
                                <PersonLine key={key} name={person.name}>
                                    <div className="mt-0.5 flex items-center gap-1.5 text-[12.5px] text-amber-700">
                                        <TriangleAlert size={13} /> поменялось во время сверки: {(person.fields || []).join(', ').toLowerCase()}
                                    </div>
                                </PersonLine>
                            );
                        }
                        return (
                            <PersonLine key={key} name={person.name} muted>
                                <div className="text-[12.5px] text-slate-400">
                                    {current.key === 'skipped' ? 'сняли галочку при сверке'
                                        : current.key === 'unresolved' ? 'тёзки — не выбрали, кто это'
                                            : current.key === 'duplicates' ? 'несколько строк таблицы на одного — не записан'
                                                : 'в iCORE не нашёлся'}
                                </div>
                            </PersonLine>
                        );
                    })}
                </div>
            </div>
        </div>
    );
}

export default function HrJournalSheet({ open = true, api, section, today, onClose }) {
    const [items, setItems] = useState(null);
    const [total, setTotal] = useState(0);
    const [query, setQuery] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const [page, setPage] = useState(null);
    const listRef = useRef(null);
    const savedScroll = useRef(0);
    const direction = useRef('');

    /* Номер запроса: ответ на прежний поиск, пришедший позже нового, не перетирает его, а «Показать
       ещё» по прежнему запросу не дописывается к списку нового. */
    const ticket = useRef(0);
    const load = useCallback((offset = 0, append = false) => {
        const mine = append ? ticket.current : ticket.current + 1;
        ticket.current = mine;
        setBusy(true);
        return api.get('/journal', { section, q: query.trim() || undefined, offset })
            .then(({ data }) => {
                if (mine !== ticket.current) return;
                setItems((prev) => (append ? [...(prev || []), ...(data.items || [])] : (data.items || [])));
                setTotal(data.total || 0);
                setError('');
            })
            .catch((requestError) => { if (mine === ticket.current) setError(errorText(requestError, 'Журнал не загрузился')); })
            .finally(() => { if (mine === ticket.current) setBusy(false); });
    }, [api, section, query]);

    useEffect(() => {
        const timer = window.setTimeout(() => load(0, false), items ? 250 : 0);
        return () => window.clearTimeout(timer);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [load]);

    const groups = useMemo(() => {
        const out = [];
        (items || []).forEach((item) => {
            const day = String(item.created_at || '').slice(0, 10);
            const last = out[out.length - 1];
            if (last && last.day === day) last.items.push(item);
            else out.push({ day, items: [item] });
        });
        return out;
    }, [items]);

    const openImport = (item) => {
        savedScroll.current = scrollHost(listRef.current)?.scrollTop || 0;
        direction.current = 'push';
        setPage(item);
    };
    const back = () => {
        direction.current = 'pop';
        setPage(null);
    };
    // Назад — к тому же месту списка, а не в начало.
    useLayoutEffect(() => {
        const host = !page ? scrollHost(listRef.current) : null;
        if (host) host.scrollTop = savedScroll.current;
    }, [page]);

    const more = items && items.length < total;
    const motion = direction.current === 'push' ? 'motion-safe:animate-push-in'
        : direction.current === 'pop' ? 'motion-safe:animate-pop-in' : '';

    return (
        <IosModal open={open} onClose={onClose} onBack={page ? back : null} maxWidth="max-w-3xl"
                  title={page ? 'Загрузка таблицы' : (TITLES[section] || 'Журнал')}
                  subtitle={page
                      ? [`${dateText(page.created_at)} в ${timeOf(page.created_at)}`, page.actor_name].filter(Boolean).join(' · ')
                      : (items ? `Записей: ${total}` : 'Загружаем…')}>
            {page ? (
                <div key={`import-${page.id}`} className={motion}>
                    <ImportDetails item={page} />
                </div>
            ) : (
                <div key="list" className={`flex flex-col gap-3 ${PAGE_HEIGHT} ${motion}`}>
                    <div className="relative shrink-0">
                        <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="ФИО или текст записи"
                               className="h-9 w-full rounded-xl border-0 bg-white pl-9 pr-3 text-[13.5px] text-slate-900 ring-1 ring-slate-200/70 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/70" />
                    </div>
                    <div ref={listRef} className={`thin-scroll min-h-0 flex-1 overflow-y-auto transition-opacity duration-200 ${busy && items ? 'opacity-60' : ''}`}>
                        {error && <div className={`${iosCard} px-4 py-3 text-[13px] text-rose-600`}>{error}</div>}
                        {!items && !error && (
                            <div className={`${iosCard} overflow-hidden`}><SkeletonRows rows={7} label="Загружаем журнал" /></div>
                        )}
                        {items && items.length === 0 && !error && (
                            <div className={`${iosCard} flex min-h-[220px] flex-col items-center justify-center gap-2 px-6 text-center`}>
                                <History size={26} className="text-slate-300" />
                                <div className="text-[13.5px] text-slate-500">{query.trim() ? 'Ничего не нашлось' : 'Пока ничего не происходило'}</div>
                            </div>
                        )}
                        {items && items.length > 0 && (
                            <div className="space-y-4 pb-1 motion-safe:animate-fade-in-soft">
                                {groups.map((group) => (
                                    <section key={group.day}>
                                        <h4 className="sticky top-0 z-10 mb-1.5 bg-slate-50/95 px-1 py-1 text-[12px] font-semibold uppercase tracking-wide text-slate-500 backdrop-blur-sm">
                                            {dayTitle(group.day, today)}
                                        </h4>
                                        <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                                            {group.items.map((item) => <Entry key={item.id} item={item} onOpen={openImport} />)}
                                        </div>
                                    </section>
                                ))}
                                {more && (
                                    <button type="button" onClick={() => load(items.length, true)} disabled={busy}
                                            className="w-full rounded-2xl py-2.5 text-[13px] font-medium text-blue-600 transition hover:bg-blue-50/70 disabled:text-slate-400">
                                        Показать ещё {Math.min(50, total - items.length)}
                                    </button>
                                )}
                            </div>
                        )}
                    </div>
                </div>
            )}
        </IosModal>
    );
}
