import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Check, Loader2, Search, Send } from 'lucide-react';
import { IosModal, IosPager, iosBtnPrimary, iosBtnSecondary, iosInput } from '../ui/ios';
import { TILES_PAGE_SIZE, errorOf, filterAccounts, groupAccounts, pageOf } from './twofaMeta';
import { GroupLabel, SearchField } from './twofaUi';

/*
 * Окна «Доступов» «Кодов 2FA»: найти сотрудника и выбрать, к каким аккаунтам ему можно
 * запрашивать коды (слова владельца: «настройки доступов, каким сотрудникам можно
 * отправлять»).
 *
 * Набор аккаунтов человека сохраняется целиком одним PUT: снятое закрывает и его ждущие
 * запросы к этим аккаунтам (twofa/service.py: set_person_grants).
 */

const SEARCH_DELAY_MS = 250;

export const PeoplePicker = ({ open, onClose, api, onPick }) => {
    const [query, setQuery] = useState('');
    const [people, setPeople] = useState(null);
    const [problem, setProblem] = useState('');
    const inputRef = useRef(null);
    const seqRef = useRef(0);

    useEffect(() => {
        if (!open) { setQuery(''); setPeople(null); setProblem(''); return undefined; }
        const seq = ++seqRef.current;
        const timer = setTimeout(async () => {
            try {
                const { data } = await api.get('/people', { q: query });
                if (seq === seqRef.current) { setPeople(data.people || []); setProblem(''); }
            } catch (requestError) {
                if (seq === seqRef.current) setProblem(errorOf(requestError, 'Не удалось найти сотрудников'));
            }
        }, query ? SEARCH_DELAY_MS : 0);
        return () => clearTimeout(timer);
    }, [open, query, api]);

    useEffect(() => {
        if (open) setTimeout(() => inputRef.current?.focus(), 60);
    }, [open]);

    return (
        <IosModal open={open} onClose={onClose} title="Выдать доступ" subtitle="Кому можно запрашивать коды" maxWidth="max-w-lg">
            <div className="space-y-3">
                <div className="relative">
                    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input ref={inputRef} className={`${iosInput} pl-9`} value={query} placeholder="Имя или логин"
                           onChange={(event) => setQuery(event.target.value)} />
                </div>
                {problem && <p className="px-1 text-[12.5px] text-rose-600">{problem}</p>}
                {people === null && !problem && (
                    <div className="flex items-center justify-center gap-2 py-8 text-[13px] text-slate-500">
                        <Loader2 size={15} className="animate-spin" /> Ищем…
                    </div>
                )}
                {people && people.length === 0 && (
                    <p className="py-8 text-center text-[13px] text-slate-500">Никого не нашли</p>
                )}
                {people && people.length > 0 && (
                    <ul className="divide-y divide-slate-100 overflow-hidden rounded-2xl bg-white ring-1 ring-slate-200/70">
                        {people.map((person) => (
                            <li key={person.id}>
                                <button type="button" onClick={() => onPick(person)}
                                        className="flex w-full items-center gap-3 px-3.5 py-2.5 text-left transition hover:bg-slate-50">
                                    <span className="min-w-0 grow basis-0">
                                        <span className="block truncate text-[13.5px] font-semibold text-slate-800">{person.name}</span>
                                        <span className="block truncate text-[12px] text-slate-500">{person.department || 'Без отдела'}</span>
                                    </span>
                                    {!person.telegram_linked && (
                                        <span className="shrink-0 text-[11.5px] text-rose-600">Telegram не привязан</span>
                                    )}
                                </button>
                            </li>
                        ))}
                    </ul>
                )}
            </div>
        </IosModal>
    );
};

/* Плитка-галочка аккаунта — та же в доступах сотрудника и в составе раздела. */
export const AccountTile = ({ account, checked, onToggle }) => (
    <button type="button" onClick={onToggle} aria-pressed={checked}
            className={`flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left ring-1 transition ${
                checked ? 'bg-blue-50 ring-blue-200' : 'bg-white ring-slate-200/70 hover:bg-slate-50'
            }`}>
        <span className={`grid h-5 w-5 shrink-0 place-items-center rounded-md ring-1 transition ${
            checked ? 'bg-blue-600 text-white ring-blue-600' : 'bg-white ring-slate-300'
        }`}>
            {checked && <Check size={13} strokeWidth={3} />}
        </span>
        <span className="min-w-0 grow basis-0">
            <span className={`block truncate text-[13.5px] font-semibold ${checked ? 'text-blue-800' : 'text-slate-800'}`}>{account.title}</span>
            {account.service && <span className="block truncate text-[12px] text-slate-500">{account.service}</span>}
        </span>
    </button>
);

/* Плитки по разделам: заголовки — когда разделов больше одного (иначе это шум) или когда
   важно, откуда аккаунт (labelled — состав раздела: отмеченный из другого раздела переедет).
   Длинный список — с поиском и страницами по TILES_PAGE_SIZE; у подписи раздела — сколько в
   нём отмечено: отмеченное на другой странице не теряется из виду. */
export const AccountTiles = ({ accounts, groups, isChecked, onToggle, labelled = false }) => {
    const [query, setQuery] = useState('');
    const [pages, setPages] = useState({});
    const matched = filterAccounts(accounts, query);
    const sections = groupAccounts(matched, groups);
    const block = (key, list) => {
        const view = pageOf(list, pages[key], TILES_PAGE_SIZE);
        return (
            <>
                <div key={view.page} className="space-y-2 motion-safe:animate-fade-in-soft">
                    {view.items.map((account) => (
                        <AccountTile key={account.id} account={account} checked={isChecked(account.id)} onToggle={() => onToggle(account.id)} />
                    ))}
                </div>
                {view.pageCount > 1 && (
                    <div className="mt-1.5">
                        <IosPager page={view.page} pageCount={view.pageCount} total={view.total} from={view.from} to={view.to}
                                  onPage={(next) => setPages((prev) => ({ ...prev, [key]: next }))} unit="аккаунты" />
                    </div>
                )}
            </>
        );
    };
    let body;
    if (!matched.length) {
        body = <p className="px-1 py-3 text-[13px] text-slate-500">Аккаунтов с «{query.trim()}» нет</p>;
    } else if (sections.length < 2 && !labelled) {
        body = block('all', matched);
    } else {
        body = (
            <div className="space-y-4">
                {sections.map((section) => {
                    const marked = section.accounts.filter((account) => isChecked(account.id)).length;
                    return (
                        <div key={section.id ?? 'none'}>
                            <GroupLabel right={(
                                <span className="text-[11px] tabular-nums text-slate-400">отмечено {marked} из {section.accounts.length}</span>
                            )}>
                                {section.title}
                            </GroupLabel>
                            {block(section.id ?? 'none', section.accounts)}
                        </div>
                    );
                })}
            </div>
        );
    }
    return (
        <div className="space-y-3">
            {(accounts || []).length > TILES_PAGE_SIZE && (
                <SearchField value={query} placeholder="Найти аккаунт" onChange={(value) => { setQuery(value); setPages({}); }} />
            )}
            {body}
        </div>
    );
};

export const PersonSheet = ({ person, accounts, groups, onClose, api, onSaved, botLink }) => {
    const initial = useMemo(() => new Set(person?.account_ids || []), [person]);
    const [picked, setPicked] = useState(initial);
    const [busy, setBusy] = useState(false);
    const [problem, setProblem] = useState('');

    useEffect(() => { setPicked(new Set(person?.account_ids || [])); setProblem(''); setBusy(false); }, [person]);

    if (!person) return <IosModal open={false} onClose={onClose} title="" />;

    const changed = picked.size !== initial.size || [...picked].some((id) => !initial.has(id));
    const toggle = (id) => setPicked((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id); else next.add(id);
        return next;
    });

    const save = async (ids) => {
        setBusy(true);
        try {
            const { data } = await api.put(`/people/${person.id}/grants`, { account_ids: ids });
            onSaved?.(person, data);
            onClose();
        } catch (requestError) {
            setProblem(errorOf(requestError, 'Не сохранилось'));
            setBusy(false);
        }
    };

    const hadAny = initial.size > 0;

    return (
        <IosModal
            open={Boolean(person)}
            onClose={onClose}
            title={person.name}
            subtitle={person.department || null}
            maxWidth="max-w-lg"
            footer={(
                <div className="flex w-full items-center justify-between gap-2">
                    {hadAny ? (
                        <button type="button" disabled={busy} onClick={() => save([])}
                                className="rounded-xl px-3 py-2 text-[13px] font-semibold text-rose-600 transition hover:bg-rose-50 disabled:opacity-50">
                            Снять все доступы
                        </button>
                    ) : <span />}
                    <div className="flex items-center gap-2">
                        <button type="button" className={iosBtnSecondary} onClick={onClose}>Отмена</button>
                        <button type="button" className={iosBtnPrimary} disabled={busy || !changed} onClick={() => save([...picked])}>
                            {busy && <Loader2 size={15} className="animate-spin" />}
                            Сохранить
                        </button>
                    </div>
                </div>
            )}
        >
            <div className="space-y-3">
                {!person.telegram_linked && (
                    <div className="flex items-start gap-2 rounded-xl bg-rose-50 px-3 py-2.5 text-[12.5px] text-rose-800 ring-1 ring-rose-200">
                        <Send size={14} className="mt-0.5 shrink-0" />
                        <span>
                            Telegram не привязан — коды до сотрудника не дойдут, пока он не войдёт в бота портала своим логином и паролем
                            {botLink && <> (<a href={botLink} target="_blank" rel="noopener noreferrer" className="font-semibold underline">открыть бота</a>)</>}.
                        </span>
                    </div>
                )}
                {accounts.length === 0 ? (
                    <p className="py-6 text-center text-[13px] text-slate-500">Сначала добавьте аккаунт во вкладке «Аккаунты»</p>
                ) : (
                    <AccountTiles accounts={accounts} groups={groups} isChecked={(id) => picked.has(id)} onToggle={toggle} />
                )}
                {problem && <p className="px-1 text-[12.5px] text-rose-600">{problem}</p>}
            </div>
        </IosModal>
    );
};
