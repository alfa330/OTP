import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Plus, RefreshCw, Repeat, Search, X } from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import { IosModal, IosSegmented, iosBtnPrimary, iosBtnSecondary, iosCard } from '../ui/ios';
import {
    POOL_STAGES, STAGE_TONES, SUBSTAGE_LABELS, SUBSTAGE_TONES, codeOf, daysSince, errorOf, initials, shortTime,
} from './hiringMeta';
import { FIELD, Label, Loading, Pill, TOOLBAR_BTN, TOOLBAR_BTN_PRIMARY } from './hiringUi';

/*
 * Вкладка «Воронка» (ТЗ 4): канбан из десяти колонок ТЗ 4.1 в его порядке. Карточка идёт
 * слева направо; «Отказ» и «Отсев» — завершающие. Перетаскивание открывает карточку с уже
 * выбранным переходом — у каждого перехода свои обязательные поля (дата визита, группа,
 * причина), и бросать карточку «вслепую» нельзя.
 *
 * Рекрутер видит пул и свои карточки; у удалённого рекрутера в пуле нет телефонов (сервер их
 * не присылает). «Взять в работу» — прямо с карточки в пуле, без открытия.
 */

const COLUMN_WIDTH = 'w-[272px] min-w-[272px]';

const CardTile = ({ card, meta, canTake, onOpen, onTake, draggable, onDragStart }) => {
    const idle = card.last_touch_at || card.taken_at;
    const idleDays = idle && !POOL_STAGES.includes(card.stage) && !card.is_closed ? daysSince(idle) : null;
    const overdue = card.next_contact_at && !card.is_closed && daysSince(card.next_contact_at) > 0;
    const line2 = [card.vacancy_name || card.vacancy_text, card.city].filter(Boolean).join(' · ');
    return (
        <article
            draggable={draggable}
            onDragStart={draggable ? (event) => onDragStart(event, card) : undefined}
            className={`${iosCard} group cursor-pointer p-3 transition hover:ring-slate-300 ${draggable ? 'active:cursor-grabbing' : ''}`}
            onClick={() => onOpen(card.id)}
            role="button"
            tabIndex={0}
            onKeyDown={(event) => { if (event.key === 'Enter') onOpen(card.id); }}
        >
            <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                    <div className="truncate text-[13.5px] font-semibold text-slate-900">
                        {card.full_name || 'Без имени'}
                    </div>
                    {line2 && <div className="truncate text-[12px] text-slate-500">{line2}</div>}
                </div>
                {card.recruiter_name && (
                    <span title={card.recruiter_name}
                          className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-slate-100 text-[11px] font-semibold text-slate-600">
                        {initials(card.recruiter_name)}
                    </span>
                )}
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
                {card.substage && <Pill tone={SUBSTAGE_TONES[card.substage]}>{SUBSTAGE_LABELS[card.substage]}</Pill>}
                {card.source_name && <Pill>{card.source_name}</Pill>}
                {card.repeat_of_card_id && <Pill tone="blue" title="Повторное обращение"><Repeat size={11} /> повторно</Pill>}
                {card.no_answer_suggest && <Pill tone="amber">3 недозвона</Pill>}
                {overdue && <Pill tone="red">просрочено</Pill>}
            </div>
            <div className="mt-2 flex items-center justify-between text-[11.5px] tabular-nums text-slate-400">
                <span>{card.candidate_no ? `№${card.candidate_no}` : shortTime(card.received_at)}</span>
                <span>
                    {card.next_contact_at && !card.is_closed ? `контакт ${shortTime(card.next_contact_at)}`
                        : idleDays !== null && idleDays >= 7 ? `без касаний ${idleDays} дн` : card.candidate_no ? shortTime(card.received_at) : ''}
                </span>
            </div>
            {canTake && POOL_STAGES.includes(card.stage) && (
                <button type="button" onClick={(event) => { event.stopPropagation(); onTake(card); }}
                        className="mt-2.5 inline-flex h-8 w-full items-center justify-center rounded-xl bg-blue-600 text-[12.5px] font-semibold text-white transition hover:bg-blue-700 active:scale-[0.98]">
                    Взять в работу
                </button>
            )}
        </article>
    );
};

const Column = ({ column, meta, caps, onOpen, onTake, canDrag, onDragStart, onDrop }) => {
    const [over, setOver] = useState(false);
    const tone = STAGE_TONES[column.stage];
    return (
        <section
            className={`${COLUMN_WIDTH} flex max-h-[calc(100vh-220px)] flex-col rounded-2xl p-2 transition ${over ? 'bg-blue-50 ring-2 ring-blue-300' : 'bg-slate-100/80'}`}
            onDragOver={(event) => { event.preventDefault(); setOver(true); }}
            onDragLeave={() => setOver(false)}
            onDrop={(event) => { event.preventDefault(); setOver(false); onDrop(event, column.stage); }}
        >
            <header className="flex items-center justify-between px-2 pb-2 pt-1">
                <span className={`truncate text-[12.5px] font-semibold ${tone === 'red' ? 'text-rose-700' : tone === 'green' ? 'text-emerald-700' : 'text-slate-700'}`}>
                    {column.label}
                </span>
                <span className="ml-2 shrink-0 rounded-full bg-white px-2 py-0.5 text-[11.5px] font-semibold tabular-nums text-slate-500 ring-1 ring-slate-200/70">
                    {column.total}
                </span>
            </header>
            <div className="thin-scroll flex flex-col gap-2 overflow-y-auto px-0.5 pb-1">
                {column.cards.map((card) => (
                    <CardTile key={card.id} card={card} meta={meta} canTake={caps.take} onOpen={onOpen} onTake={onTake}
                              draggable={canDrag(card)} onDragStart={onDragStart} />
                ))}
                {column.cards.length === 0 && <div className="py-6 text-center text-[12px] text-slate-400">Пусто</div>}
                {column.total > column.cards.length && (
                    <div className="py-2 text-center text-[11.5px] text-slate-400">
                        Показаны {column.cards.length} из {column.total} — уточните отбор
                    </div>
                )}
            </div>
        </section>
    );
};

/* Отклик вручную (ТЗ 5.2): ФИО, телефон, город, вакансия, источник обязателен. */
const AddResponseModal = ({ open, onClose, api, toast, boot, onCreated }) => {
    const empty = { full_name: '', phone: '', whatsapp: '', city: '', vacancy_id: '', source_id: '', referred_by: '', comment: '' };
    const [form, setForm] = useState(empty);
    const [saving, setSaving] = useState(false);
    useEffect(() => { if (open) setForm(empty); }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
    const sources = (boot.dictionaries?.sources || []).filter((s) => s.kind === 'responses' && s.is_active !== false);
    const vacancies = (boot.dictionaries?.vacancies || []).filter((v) => v.is_active !== false);
    const set = (key) => (value) => setForm((prev) => ({ ...prev, [key]: value?.target ? value.target.value : value }));
    const submit = async () => {
        if (!form.phone.trim()) return toast('Укажите телефон', 'error');
        if (!form.source_id) return toast('Укажите источник отклика', 'error');
        setSaving(true);
        try {
            const { data } = await api.post('/cards', {
                full_name: form.full_name, phone: form.phone, whatsapp: form.whatsapp || null, city: form.city || null,
                vacancy_id: form.vacancy_id ? Number(form.vacancy_id) : null, source_id: Number(form.source_id),
                referred_by: form.referred_by || null, comment: form.comment || null,
            });
            toast(data.outcome === 'repeat' ? 'По этому телефону уже есть открытая карточка — отклик дописан в неё' : 'Отклик добавлен в пул', 'success');
            onCreated(data.card);
            onClose();
        } catch (error) {
            toast(errorOf(error, 'Не удалось добавить отклик'), 'error');
        } finally {
            setSaving(false);
        }
        return undefined;
    };
    const isReferral = sources.find((s) => String(s.id) === String(form.source_id))?.code === 'referral';
    return (
        <IosModal open={open} onClose={onClose} title="Добавить отклик" subtitle="Кандидат откликнулся сам — телефон и источник обязательны"
                  footer={(
                      <div className="flex justify-end gap-2">
                          <button type="button" className={iosBtnSecondary} onClick={onClose}>Отмена</button>
                          <button type="button" className={iosBtnPrimary} onClick={submit} disabled={saving}>Добавить</button>
                      </div>
                  )}>
            <div className="grid gap-3 sm:grid-cols-2">
                <div className="sm:col-span-2">
                    <Label required>Телефон</Label>
                    <input className={FIELD} value={form.phone} onChange={set('phone')} placeholder="+7 705 123 45 67" inputMode="tel" />
                </div>
                <div className="sm:col-span-2">
                    <Label>ФИО</Label>
                    <input className={FIELD} value={form.full_name} onChange={set('full_name')} />
                </div>
                <div>
                    <Label required hint="Откуда пришёл отклик: площадка, реклама, знакомый. По источнику считается эффективность и мотивация.">Источник</Label>
                    <CustomSelect variant="ios" value={form.source_id} onChange={set('source_id')} placeholder="Выберите источник"
                                  options={sources.map((s) => ({ value: String(s.id), label: s.name }))} invalid={!form.source_id} />
                </div>
                <div>
                    <Label>Вакансия</Label>
                    <CustomSelect variant="ios" value={form.vacancy_id} onChange={set('vacancy_id')} placeholder="Не указана"
                                  options={[{ value: '', label: 'Не указана' }, ...vacancies.map((v) => ({ value: String(v.id), label: v.name }))]} />
                </div>
                <div>
                    <Label>Город</Label>
                    <input className={FIELD} value={form.city} onChange={set('city')} />
                </div>
                <div>
                    <Label hint="Если WhatsApp на другом номере.">WhatsApp</Label>
                    <input className={FIELD} value={form.whatsapp} onChange={set('whatsapp')} placeholder="как телефон" inputMode="tel" />
                </div>
                {isReferral && (
                    <div className="sm:col-span-2">
                        <Label hint="Сотрудник или кандидат, который привёл человека по «Приведи друга».">Кто привёл</Label>
                        <input className={FIELD} value={form.referred_by} onChange={set('referred_by')} />
                    </div>
                )}
                <div className="sm:col-span-2">
                    <Label>Комментарий</Label>
                    <textarea className={`${FIELD} min-h-[72px]`} value={form.comment} onChange={set('comment')} />
                </div>
            </div>
        </IosModal>
    );
};

const BoardTab = ({ api, toast, boot, caps, onOpenCard, version, onChanged }) => {
    // «Мои и пул» — отбор рекрутера; тренер и руководитель отдела видят свой круг целиком (сервер
    // отбор mine у них и не применяет), руководитель рекрутинга — всё с переключателем.
    const [scope, setScope] = useState(caps.lead || !caps.recruiter ? 'all' : 'mine');
    const [query, setQuery] = useState('');
    const [filters, setFilters] = useState({ vacancy_id: '', city: '', source_id: '', recruiter_id: '' });
    const [columns, setColumns] = useState(null);
    const [loading, setLoading] = useState(true);
    const [adding, setAdding] = useState(false);
    const timer = useRef(null);
    const [debounced, setDebounced] = useState('');

    useEffect(() => {
        clearTimeout(timer.current);
        timer.current = setTimeout(() => setDebounced(query.trim()), 350);
        return () => clearTimeout(timer.current);
    }, [query]);

    const load = useCallback(() => {
        setLoading(true);
        return api.get('/board', {
            mine: scope === 'mine' ? '1' : undefined, q: debounced || undefined,
            vacancy_id: filters.vacancy_id || undefined, city: filters.city || undefined,
            source_id: filters.source_id || undefined, recruiter_id: filters.recruiter_id || undefined,
        })
            .then(({ data }) => setColumns(data.columns))
            .catch((error) => toast(errorOf(error, 'Не удалось загрузить доску'), 'error'))
            .finally(() => setLoading(false));
    }, [api, scope, debounced, filters, toast]);

    useEffect(() => { load(); }, [load, version]);

    const dictionaries = boot.dictionaries || {};
    const options = useMemo(() => ({
        vacancies: [{ value: '', label: 'Все вакансии' }, ...(dictionaries.vacancies || []).map((v) => ({ value: String(v.id), label: v.name }))],
        cities: [{ value: '', label: 'Все города' }, ...(dictionaries.cities || []).map((c) => ({ value: c, label: c }))],
        sources: [{ value: '', label: 'Все источники' }, ...(dictionaries.sources || []).map((s) => ({ value: String(s.id), label: s.name }))],
        recruiters: [{ value: '', label: 'Все рекрутеры' }, ...(dictionaries.recruiters || []).map((r) => ({ value: String(r.id), label: r.name }))],
    }), [dictionaries]);

    const take = async (card) => {
        try {
            await api.post(`/cards/${card.id}/take`);
            toast(`${card.full_name || 'Карточка'} — в работе у вас`, 'success');
            onChanged();
        } catch (error) {
            toast(codeOf(error) === 'HIRING_ALREADY_TAKEN' ? 'Уже в работе' : errorOf(error, 'Не удалось взять в работу'), 'error');
            load();
        }
    };

    const canDrag = (card) => {
        if (card.is_closed && !caps.lead) return false;
        if (POOL_STAGES.includes(card.stage)) return Boolean(caps.take);
        return caps.lead || Number(card.recruiter_id) === Number(boot.me?.id)
            || (card.group_id && (caps.trainer_group_ids || []).includes(Number(card.group_id)))
            || (card.department_id && (caps.headed_ids || []).includes(Number(card.department_id)));
    };
    const onDragStart = (event, card) => {
        event.dataTransfer.setData('text/plain', String(card.id));
        event.dataTransfer.effectAllowed = 'move';
    };
    const onDrop = (event, stage) => {
        const id = Number(event.dataTransfer.getData('text/plain'));
        if (!id) return;
        const card = (columns || []).flatMap((column) => column.cards).find((item) => item.id === id);
        if (!card || card.stage === stage) return;
        // Из пула — только «Взять в работу» (атомарное закрепление); других целей у карточки пула нет.
        if (POOL_STAGES.includes(card.stage)) {
            if (stage === 'in_work' && caps.take) take(card);
            else toast('Из пула карточка берётся только в работу', 'error');
            return;
        }
        if (!(card.targets || []).includes(stage)) {
            toast(`Из «${(columns || []).find((c) => c.stage === card.stage)?.label}» в «${(columns || []).find((c) => c.stage === stage)?.label}» карточка не переходит`, 'error');
            return;
        }
        onOpenCard(id, { move: stage });
    };

    const activeFilters = Object.values(filters).filter(Boolean).length;
    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
                {caps.lead && (
                    <IosSegmented value={scope} onChange={setScope} ariaLabel="Чьи карточки"
                                  options={[{ value: 'mine', label: 'Мои и пул' }, { value: 'all', label: 'Все' }]} />
                )}
                <label className="relative">
                    <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Имя, телефон, город, №"
                           className="h-9 w-56 rounded-xl bg-white pl-8 pr-8 text-[13px] text-slate-900 ring-1 ring-slate-200/70 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/70" />
                    {query && (
                        <button type="button" aria-label="Очистить" onClick={() => setQuery('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"><X size={14} /></button>
                    )}
                </label>
                <CustomSelect variant="ios" value={filters.vacancy_id} onChange={(v) => setFilters((f) => ({ ...f, vacancy_id: v }))} options={options.vacancies} className="w-44" ariaLabel="Вакансия" />
                <CustomSelect variant="ios" value={filters.city} onChange={(v) => setFilters((f) => ({ ...f, city: v }))} options={options.cities} className="w-40" ariaLabel="Город" searchable />
                <CustomSelect variant="ios" value={filters.source_id} onChange={(v) => setFilters((f) => ({ ...f, source_id: v }))} options={options.sources} className="w-44" ariaLabel="Источник" />
                {caps.lead && (
                    <CustomSelect variant="ios" value={filters.recruiter_id} onChange={(v) => setFilters((f) => ({ ...f, recruiter_id: v }))} options={options.recruiters} className="w-44" ariaLabel="Рекрутер" searchable />
                )}
                {activeFilters > 0 && (
                    <button type="button" className={TOOLBAR_BTN} onClick={() => setFilters({ vacancy_id: '', city: '', source_id: '', recruiter_id: '' })}>
                        <X size={14} /> Сбросить
                    </button>
                )}
                <div className="ml-auto flex items-center gap-2">
                    <button type="button" className={TOOLBAR_BTN} onClick={load} disabled={loading} aria-label="Обновить">
                        <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
                    </button>
                    {caps.add_response && (
                        <button type="button" className={TOOLBAR_BTN_PRIMARY} onClick={() => setAdding(true)}>
                            <Plus size={14} /> Добавить отклик
                        </button>
                    )}
                </div>
            </div>

            {!columns ? <Loading text="Загружаем доску…" /> : (
                <div className="-mx-3 overflow-x-auto px-3 pb-2 sm:-mx-5 sm:px-5">
                    <div className="flex items-start gap-3">
                        {columns.map((column) => (
                            <Column key={column.stage} column={column} meta={boot.meta} caps={caps} onOpen={onOpenCard} onTake={take}
                                    canDrag={canDrag} onDragStart={onDragStart} onDrop={onDrop} />
                        ))}
                    </div>
                </div>
            )}

            <AddResponseModal open={adding} onClose={() => setAdding(false)} api={api} toast={toast} boot={boot}
                              onCreated={() => onChanged()} />
        </div>
    );
};

export default BoardTab;
