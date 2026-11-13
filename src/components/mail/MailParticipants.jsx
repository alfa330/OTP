import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, Plus, Search, Trash2 } from 'lucide-react';
import { IosModal, iosBtnGhost, iosBtnPrimary, iosCard, iosInput } from '../ui/ios';
import { Empty, Loading, TOOLBAR_BTN_PRIMARY, useSheetState } from '../hr_office/hrUi';
import { Field, FormSelect } from '../payments/paymentsUi';
import { ROLE_LABELS, dateTimeText, errorText, officeLabel, officeOptions } from './mailMeta';

/*
 * «Участники» — сотрудники офисов, которые отправляют и принимают почту; каждый привязан к своему
 * офису (решения владельца 13.11.2026: вкладка вместо «офис определяется автоматически» и «нам нужны
 * именно сотрудники офисов, которые будут отправлять и принимать»). По этой строке раздел знает,
 * откуда человек отправляет и что ему входящее; кого здесь нет, тот «Почту» не видит.
 *
 * Ролей на выбор нет. Администратор раздела (постановщик ТЗ) помечен в списке и правит участников и
 * справочники; суперадмины видят всё и в списке не стоят. Сервер держит то же (access.clean_participant):
 * роль из запроса он не читает вовсе.
 */
const PANEL = 'rounded-2xl bg-white ring-1 ring-slate-200/70';
const ADMIN_HINT = 'Администратор раздела правит «Участников» и «Справочники». Офис ему нужен, только если он сам '
    + 'отправляет и принимает почту.';

function PersonPicker({ api, onPick }) {
    const [query, setQuery] = useState('');
    const [people, setPeople] = useState([]);
    const [loading, setLoading] = useState(false);
    const ticketRef = useRef(0);
    useEffect(() => {
        const text = query.trim();
        if (text.length < 2) {
            setPeople([]);
            return undefined;
        }
        const timer = window.setTimeout(() => {
            const ticket = ticketRef.current + 1;
            ticketRef.current = ticket;
            setLoading(true);
            api.get('/people', { q: text })
                .then(({ data }) => { if (ticketRef.current === ticket) setPeople(data.items || []); })
                .catch(() => { if (ticketRef.current === ticket) setPeople([]); })
                .finally(() => { if (ticketRef.current === ticket) setLoading(false); });
        }, 250);
        return () => window.clearTimeout(timer);
    }, [api, query]);
    return (
        <div className="space-y-2">
            <div className="relative">
                <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="ФИО или логин"
                       className={`${iosInput} pl-9`} />
                {loading && <Loader2 size={15} className="absolute right-3 top-1/2 -translate-y-1/2 animate-spin text-slate-400" />}
            </div>
            {people.length > 0 && (
                <div className={`${PANEL} max-h-64 divide-y divide-slate-100 overflow-y-auto`}>
                    {people.map((person) => (
                        <button key={person.id} type="button" onClick={() => onPick(person)}
                                className="flex w-full items-center gap-3 px-3.5 py-2.5 text-left transition hover:bg-slate-50">
                            <span className="min-w-0 flex-1">
                                <span className="block truncate text-[13.5px] text-slate-900">{person.name}</span>
                                <span className="block truncate text-[12px] text-slate-500">
                                    {[person.department, person.city].filter(Boolean).join(' · ')}
                                </span>
                            </span>
                            {person.role && <span className="text-[12px] text-slate-500">уже в участниках</span>}
                        </button>
                    ))}
                </div>
            )}
            {query.trim().length >= 2 && !loading && people.length === 0 && (
                <div className="px-1 text-[12.5px] text-slate-500">Никого не нашли</div>
            )}
        </div>
    );
}

function ParticipantSheet({ open, onClose, api, context, existing, officesById, onSaved, toast, onExisting }) {
    const [person, setPerson] = useState(existing ? { id: existing.user_id, name: existing.name, department: existing.department,
        city: existing.city } : null);
    const [officeId, setOfficeId] = useState(existing?.office_ids?.[0] ?? null);
    const [tried, setTried] = useState(false);
    const [busy, setBusy] = useState('');
    const offices = context?.offices || [];
    const isAdmin = existing?.role === 'admin';
    // Офис, ушедший в архив, остаётся в списке, пока его не сменили: иначе он молча пропал бы.
    const options = useMemo(() => {
        const active = officeOptions(offices);
        if (!officeId || offices.some((office) => office.id === officeId)) return active;
        return [{ value: officeId, label: `${officeLabel(officesById[officeId]) || `Офис ${officeId}`} (в архиве)` }].concat(active);
    }, [offices, officeId, officesById]);

    const pickPerson = (picked) => {
        // Уже участник — открываем его строку: иначе «Сохранить» молча заменило бы его офис.
        if (picked.role && onExisting) {
            onExisting(picked.id);
            return;
        }
        setPerson(picked);
        // В городе сотрудника один офис — подставляем его (правило «Посылок»).
        const sameCity = offices.filter((office) => picked.city && office.city === picked.city);
        if (!officeId && sameCity.length === 1) setOfficeId(sameCity[0].id);
    };

    const problems = {};
    if (!person) problems.person = 'Выберите сотрудника';
    if (!isAdmin && !officeId) problems.office = 'Выберите офис';
    const issue = (key) => (tried ? problems[key] || '' : '');

    const save = async () => {
        setTried(true);
        if (Object.keys(problems).length) return;
        setBusy('save');
        try {
            const { data } = await api.put(`/participants/${person.id}`, { office_ids: officeId ? [officeId] : [] });
            toast?.(existing ? 'Участник обновлён' : `${person.name} — в участниках`, 'success');
            onSaved(data);
        } catch (requestError) {
            toast?.(errorText(requestError, 'Не сохранилось'), 'error');
        } finally {
            setBusy('');
        }
    };
    const remove = async () => {
        setBusy('remove');
        try {
            const { data } = await api.del(`/participants/${existing.user_id}`);
            toast?.(`${existing.name} больше не видит «Почту»`, 'success');
            onSaved(data);
        } catch (requestError) {
            toast?.(errorText(requestError, 'Не получилось'), 'error');
        } finally {
            setBusy('');
        }
    };

    const footer = (
        <div className="flex flex-wrap items-center gap-2">
            {existing && (
                <button type="button" className={`${iosBtnGhost} !text-rose-600 hover:!bg-rose-50`} onClick={remove} disabled={Boolean(busy)}>
                    {busy === 'remove' ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />} Убрать из участников
                </button>
            )}
            <div className="ml-auto flex gap-2">
                <button type="button" className={iosBtnGhost} onClick={onClose}>Отмена</button>
                <button type="button" className={iosBtnPrimary} onClick={save} disabled={Boolean(busy)}>
                    {busy === 'save' && <Loader2 size={15} className="animate-spin" />} Сохранить
                </button>
            </div>
        </div>
    );

    const subtitle = existing
        ? [existing.department, isAdmin ? 'администратор раздела' : ''].filter(Boolean).join(' · ')
        : 'Сотрудник офиса увидит «Почту» после сохранения';
    return (
        <IosModal open={open} onClose={onClose} title={existing ? existing.name : 'Новый участник'} maxWidth="max-w-lg"
                  footer={footer} subtitle={subtitle}>
            <div className="space-y-5">
                {!existing && (
                    <Field label="Сотрудник" required as="div" issue={issue('person')}>
                        {person ? (
                            <div className="flex items-center gap-3 rounded-xl bg-slate-100 px-3.5 py-2.5">
                                <span className="min-w-0 flex-1">
                                    <span className="block truncate text-[14px] text-slate-900">{person.name}</span>
                                    <span className="block truncate text-[12px] text-slate-500">
                                        {[person.department, person.city].filter(Boolean).join(' · ')}
                                    </span>
                                </span>
                                <button type="button" className={`${iosBtnGhost} !py-1`} onClick={() => setPerson(null)}>Сменить</button>
                            </div>
                        ) : <PersonPicker api={api} onPick={pickPerson} />}
                    </Field>
                )}
                <Field label="Офис" required={!isAdmin} as="div" issue={issue('office')}
                       hint={isAdmin ? ADMIN_HINT : 'Из этого офиса сотрудник отправляет, в него — получает и сверяет.'}>
                    <FormSelect value={officeId} options={options} searchable
                                placeholder={isAdmin ? 'Без своего офиса' : 'Выберите офис'}
                                onChange={(value) => setOfficeId(value ? Number(value) : null)} ariaLabel="Офис" />
                </Field>
            </div>
        </IosModal>
    );
}

export default function MailParticipants({ api, context, toast, onChanged }) {
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [query, setQuery] = useState('');
    // Окно — тем же хуком, что карточка и форма раздела: уход доигрывает, повторное открытие не гасит.
    const [sheet, keptSheet, openSheet, closeSheet, swapSheet, token] = useSheetState(null);

    const load = useCallback(() => api.get('/participants')
        .then(({ data: payload }) => { setData(payload); setError(''); })
        .catch((requestError) => setError(errorText(requestError, 'Список не загрузился'))), [api]);
    useEffect(() => { load(); }, [load]);

    const officesById = useMemo(() => {
        const map = {};
        (context?.offices || []).forEach((office) => { map[office.id] = office; });
        (data?.offices || []).forEach((office) => { map[office.id] = map[office.id] || office; });
        return map;
    }, [context, data]);

    const visible = useMemo(() => {
        const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
        return (data?.participants || []).filter((person) => {
            const haystack = `${person.name} ${person.department || ''} ${person.office_ids
                .map((id) => officeLabel(officesById[id])).join(' ')}`.toLowerCase();
            return words.every((word) => haystack.includes(word));
        });
    }, [data, query, officesById]);

    const open = (person) => openSheet(person || 'new');
    const saved = (payload) => {
        setData(payload);
        closeSheet();
        onChanged?.();
    };
    const openExisting = (userId) => {
        const row = (data?.participants || []).find((person) => person.user_id === userId);
        if (row) swapSheet(row);
    };
    // Кроме сотрудника офиса в списке бывает только администратор раздела — его и помечаем.
    const roleNote = (person) => (person.role === 'office' ? '' : (person.role === 'admin'
        ? 'администратор раздела' : (ROLE_LABELS[person.role] || '').toLowerCase()));

    return (
        <div className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2">
                <div className="relative w-full sm:w-auto sm:min-w-[220px] sm:max-w-sm sm:flex-1">
                    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Поиск по ФИО и офису"
                           className="h-9 w-full rounded-xl border-0 bg-white pl-9 pr-3 text-[13.5px] text-slate-900 ring-1 ring-slate-200/70 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/70" />
                </div>
                <button type="button" className={`${TOOLBAR_BTN_PRIMARY} ml-auto`} onClick={() => open(null)}>
                    <Plus size={15} /> Участник
                </button>
            </div>
            {error && <div className={`${iosCard} p-4 text-[13.5px] text-rose-600`}>{error}</div>}
            {!data && !error && <Loading text="Загружаем участников…" />}
            {data && (
                <div className={`${iosCard} overflow-hidden`}>
                    <div className="hidden grid-cols-[minmax(0,1.3fr)_minmax(0,1.3fr)_minmax(0,1fr)] gap-3 border-b border-slate-100 px-4 py-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400 sm:grid">
                        <span>Сотрудник</span><span>Офис</span><span>Изменил</span>
                    </div>
                    <div className="divide-y divide-slate-100">
                        {visible.length === 0 ? (
                            <Empty>{data.participants.length ? 'Под поиск никто не попал' : 'Участников пока нет — добавьте сотрудников офисов'}</Empty>
                        ) : visible.map((person) => (
                            <button key={person.user_id} type="button" onClick={() => open(person)} data-mail-participant={person.user_id}
                                    className="block w-full px-4 py-3 text-left transition hover:bg-slate-50/80 sm:grid sm:grid-cols-[minmax(0,1.3fr)_minmax(0,1.3fr)_minmax(0,1fr)] sm:items-center sm:gap-3">
                                <span className="block min-w-0">
                                    <span className={`block truncate text-[13.5px] font-medium ${person.fired ? 'text-slate-400 line-through' : 'text-slate-900'}`}>
                                        {person.name}
                                    </span>
                                    <span className="block truncate text-[12px] text-slate-500">
                                        {[person.department, roleNote(person)].filter(Boolean).join(' · ') || '—'}
                                    </span>
                                </span>
                                <span className="block min-w-0 truncate text-[13px] text-slate-700">
                                    {person.office_ids.length
                                        ? person.office_ids.map((id) => officeLabel(officesById[id]) || `офис ${id}`).join(', ')
                                        : '—'}
                                </span>
                                <span className="block text-[12px] text-slate-500">
                                    {[person.changed_by, dateTimeText(person.changed_at).slice(0, 10)].filter(Boolean).join(' · ')}
                                </span>
                            </button>
                        ))}
                    </div>
                </div>
            )}
            {keptSheet && (
                <ParticipantSheet key={token} open={Boolean(sheet)} onClose={closeSheet} api={api} context={context} toast={toast}
                                  existing={keptSheet === 'new' ? null : keptSheet} officesById={officesById} onSaved={saved}
                                  onExisting={openExisting} />
            )}
        </div>
    );
}
