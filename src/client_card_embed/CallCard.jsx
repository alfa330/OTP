/* Карточка звонка: аккаунт → причина → комментарий → «Сохранить» (задача #335, п. 2–3).
 *
 * Порядок на экране — порядок работы оператора. Аккаунт: то, что уже привязано к номеру
 * (и что нашёл TEZ APP), — выбором; нет ни одного — одно поле «Вставьте ссылку на аккаунт».
 * Причины появляются после выбора аккаунта. Всё, что оператор вносит, сохраняется само
 * (черновик уходит на сервер с паузой 0,7 с), кнопка внизу только заканчивает карточку.
 *
 * Причины и комментарий держатся здесь, а не берутся из ответа сервера на каждое действие:
 * ответ на выбор аккаунта, пришедший посреди набора комментария, не должен стирать буквы.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
    Check, ChevronDown, ExternalLink, Link2, Loader2, Plus, Search, Send, Unlink, X,
} from 'lucide-react';

import CustomSelect from '../components/ui/CustomSelect';
import { IosBadge, IosHint, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel, iosInput } from '../components/ui/ios';
import RequestComposer from '../components/client_cards/RequestComposer';
import {
    REQUEST_TONES, accountTitle, canAddTopic, errorText, filterCategories, fmtDate, isTopicPicked,
    looksLikeAccountLink, pickedInCategory, saveLabel, toggleTopic, topicLabel, topicsPayload,
} from '../components/client_cards/cardRules';
import { KAZAKHSTAN_CITY_OPTIONS } from '../utils/kazakhstanCities';

const AUTOSAVE_MS = 700;
// С какого числа причин показывать поиск по ним: короткий список видно и так.
const SEARCH_FROM = 9;

const ORIGIN_HINTS = {
    tez: 'Найден в Тез по этому номеру',
    manual: 'Привязан по ссылке оператора',
};

function AccountRow({ account, picked, busy, onPick, onUnlink, onOpen }) {
    const meta = [account.city, account.status].filter(Boolean).join(' · ');
    const link = account.link;
    const origin = link
        ? (link.origin === 'manual'
            ? `${ORIGIN_HINTS.manual}${link.created_by_name ? `: ${link.created_by_name}` : ''}${link.created_at ? `, ${fmtDate(link.created_at)}` : ''}`
            : ORIGIN_HINTS.tez)
        : '';
    return (
        <div className={`flex items-center gap-2 rounded-xl px-2.5 py-2 transition ${picked ? 'bg-blue-50 ring-1 ring-blue-200/80' : 'hover:bg-slate-50'}`}>
            <button type="button" onClick={onPick} disabled={busy} aria-pressed={picked}
                    className="flex min-w-0 flex-1 items-center gap-2.5 text-left">
                <span className={`grid h-5 w-5 shrink-0 place-items-center rounded-full ring-1 transition ${
                    picked ? 'bg-blue-600 text-white ring-blue-600' : 'bg-white text-transparent ring-slate-300'}`}>
                    <Check size={12} strokeWidth={3} />
                </span>
                <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-1.5">
                        <IosBadge className="!px-2 !py-0.5">{account.kind_label}</IosBadge>
                        <span className="min-w-0 truncate text-[13.5px] font-medium text-slate-900">{accountTitle(account)}</span>
                    </span>
                    {meta && <span className="mt-0.5 block truncate text-[12px] text-slate-500">{meta}</span>}
                </span>
            </button>
            {origin && <IosHint text={origin} label="Откуда привязка" align="right" />}
            <button type="button" onClick={onOpen} aria-label="Открыть аккаунт в админке Тез" title="Открыть в админке Тез"
                    className="grid h-7 w-7 shrink-0 place-items-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-blue-600">
                <ExternalLink size={14} />
            </button>
            {link?.can_unlink && (
                <button type="button" onClick={onUnlink} disabled={busy} aria-label="Отвязать аккаунт от номера"
                        title="Отвязать от номера"
                        className="grid h-7 w-7 shrink-0 place-items-center rounded-lg text-slate-400 hover:bg-rose-50 hover:text-rose-600">
                    <Unlink size={14} />
                </button>
            )}
        </div>
    );
}

function TezNotice({ tez, accounts }) {
    if (!tez) return null;
    if (!tez.ok && tez.error) {
        return <p className="px-1 text-[12px] text-amber-700">{tez.error}. Показаны аккаунты, привязанные к номеру ранее.</p>;
    }
    const hint = tez.driver_hint;
    if (hint?.exists && !accounts.some((item) => item.kind === 'driver')) {
        return (
            <p className="px-1 text-[12px] text-slate-600">
                В Тез на этом номере есть аккаунт водителя
                {hint.registered_at ? ` (регистрация ${fmtDate(hint.registered_at)})` : ''} — найдите его в админке и вставьте ссылку.
            </p>
        );
    }
    return null;
}

export default function CallCard({
    api, bundle, boot, toast, onBundle, onCompleted, onOpenExternal, tezPending = false,
}) {
    const { card, accounts, account, requests } = bundle;
    const cardId = card.id;
    const limits = boot.limits || {};
    const topicsLimit = limits.topics || 5;

    const [topics, setTopics] = useState(card.topics || []);
    const [comment, setComment] = useState(card.comment || '');
    const [city, setCity] = useState(card.city || '');
    const [link, setLink] = useState('');
    const [linkOpen, setLinkOpen] = useState(false);
    const [busy, setBusy] = useState('');
    const [query, setQuery] = useState('');
    const [expanded, setExpanded] = useState(null);
    const [composer, setComposer] = useState(false);

    const draftRef = useRef({ topics, comment });
    draftRef.current = { topics, comment };
    const dirtyRef = useRef(false);
    const bundleRef = useRef(onBundle);
    bundleRef.current = onBundle;

    // Другая карточка на экране — черновик берём из неё.
    useEffect(() => {
        setTopics(card.topics || []);
        setComment(card.comment || '');
        setCity(card.city || '');
        setLink('');
        setLinkOpen(false);
        setQuery('');
        setExpanded(null);
        dirtyRef.current = false;
        // Только смена карточки: правки сервера в той же карточке черновик не перезаписывают.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [cardId]);
    // Город аккаунта мог прийти из Тез уже после открытия карточки.
    useEffect(() => { if (card.city) setCity(card.city); }, [card.city]);

    const flush = useCallback(async () => {
        if (!dirtyRef.current) return true;
        dirtyRef.current = false;
        const draft = draftRef.current;
        try {
            const { data } = await api.saveCard(cardId, { topics: topicsPayload(draft.topics), comment: draft.comment });
            bundleRef.current(data);
            return true;
        } catch (error) {
            dirtyRef.current = true;
            toast(errorText(error, 'Карточка не сохранилась'), 'error');
            return false;
        }
    }, [api, cardId, toast]);

    useEffect(() => {
        if (!dirtyRef.current) return undefined;
        const timer = setTimeout(flush, AUTOSAVE_MS);
        return () => clearTimeout(timer);
    }, [topics, comment, flush]);
    // Карточку сменили или окно закрыли — несохранённое дописываем.
    useEffect(() => () => { flush(); }, [flush]);

    const edit = (setter) => (value) => { dirtyRef.current = true; setter(value); };

    const run = async (name, work) => {
        if (busy) return null;
        setBusy(name);
        try {
            const { data } = await work();
            onBundle(data);
            return data;
        } catch (error) {
            toast(errorText(error), 'error');
            return null;
        } finally {
            setBusy('');
        }
    };

    const attach = async () => {
        const value = link.trim();
        if (!value) return;
        const data = await run('link', () => api.attachLink(cardId, value));
        if (data) {
            setLink('');
            setLinkOpen(false);
        }
    };

    const saveCity = async (value) => {
        setCity(value);
        if (!account || value === (card.city || '')) return;
        await run('city', () => api.saveCard(cardId, { city: value }));
    };

    const complete = async () => {
        if (busy) return;
        setBusy('complete');
        try {
            if (!(await flush())) return;
            const { data } = await api.completeCard(cardId);
            onBundle(data);
            onCompleted(data);
        } catch (error) {
            toast(errorText(error, 'Карточка не сохранилась'), 'error');
        } finally {
            setBusy('');
        }
    };

    const openComposer = async () => {
        // Причина уходит в сообщение группе из сохранённой карточки — сначала дописываем черновик.
        if (await flush()) setComposer(true);
    };

    const categories = useMemo(() => filterCategories(boot.categories, query), [boot.categories, query]);
    // Город, записанный раньше и не входящий в справочник, остаётся в списке — иначе он бы пропал с экрана.
    const cityOptions = useMemo(() => (
        city && !KAZAKHSTAN_CITY_OPTIONS.some((option) => option.value === city)
            ? [{ value: city, label: city }, ...KAZAKHSTAN_CITY_OPTIONS] : KAZAKHSTAN_CITY_OPTIONS
    ), [city]);
    const total = useMemo(() => (boot.categories || []).reduce(
        (sum, item) => sum + 1 + (item.children?.length || 0), 0), [boot.categories]);
    const missing = !account ? 'Выберите аккаунт или вставьте ссылку на него'
        : !topics.length ? 'Выберите причину обращения' : '';
    const cityKnownFromTez = Boolean(account?.known && account?.city);
    const showLinkField = !accounts.length || linkOpen;
    const done = card.status === 'done';

    return (
        <div className="space-y-4 pb-24">
            {/* ── Аккаунт ─────────────────────────────────────────────────────── */}
            <section className="space-y-1.5">
                <div className="flex items-center justify-between">
                    <div className={iosGroupLabel}>Аккаунт</div>
                    {tezPending && (
                        <span className="flex items-center gap-1.5 text-[11.5px] text-slate-400">
                            <Loader2 size={12} className="animate-spin" /> проверяем номер в Тез
                        </span>
                    )}
                </div>
                <div className={`${iosCard} space-y-1 p-2`}>
                    {accounts.map((item) => (
                        <AccountRow key={item.id} account={item} picked={item.id === card.account_id} busy={Boolean(busy)}
                                    onPick={() => run('account', () => api.chooseAccount(cardId, item.id === card.account_id ? null : item.id))}
                                    onUnlink={() => run('unlink', () => api.unlink(cardId, item.link.id))}
                                    onOpen={() => onOpenExternal(item.url)} />
                    ))}
                    {/* Выбранный аккаунт, который от номера уже отвязали, — всё равно показываем. */}
                    {account && !accounts.some((item) => item.id === account.id) && (
                        <AccountRow account={account} picked busy={Boolean(busy)}
                                    onPick={() => run('account', () => api.chooseAccount(cardId, null))}
                                    onUnlink={() => {}} onOpen={() => onOpenExternal(account.url)} />
                    )}
                    {showLinkField ? (
                        <div className={accounts.length ? 'border-t border-slate-100 pt-2' : ''}>
                            <div className="flex items-center gap-2 px-0.5">
                                <label className="relative block min-w-0 flex-1">
                                    <Link2 size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
                                    <input value={link} onChange={(event) => setLink(event.target.value)}
                                           onKeyDown={(event) => { if (event.key === 'Enter') attach(); }}
                                           placeholder="Вставьте ссылку на аккаунт" autoFocus={linkOpen}
                                           className={`${iosInput} pl-8`} aria-label="Ссылка на аккаунт в админке Тез" />
                                </label>
                                <button type="button" onClick={attach} disabled={Boolean(busy) || !link.trim()} className={iosBtnPrimary}>
                                    {busy === 'link' ? <Loader2 size={14} className="animate-spin" /> : 'Привязать'}
                                </button>
                                <IosHint align="right" label="Какая ссылка нужна"
                                         text={`Откройте водителя или клиента в админке Тез и скопируйте адрес страницы, например ${boot.link_example}. Аккаунт закрепится за этим номером и в следующий раз подтянется сам.`} />
                            </div>
                            {link.trim() && !looksLikeAccountLink(link) && (
                                <p className="mt-1 px-1 text-[11.5px] text-amber-700">Похоже, это не ссылка на аккаунт из админки Тез.</p>
                            )}
                        </div>
                    ) : (
                        <button type="button" onClick={() => setLinkOpen(true)}
                                className="flex w-full items-center gap-1.5 rounded-xl px-2.5 py-2 text-[12.5px] font-medium text-blue-600 hover:bg-blue-50">
                            <Plus size={14} /> Другой аккаунт
                        </button>
                    )}
                </div>
                <TezNotice tez={bundle.tez} accounts={accounts} />
                {account && !cityKnownFromTez && (
                    <div className="flex items-center gap-2 px-1 pt-1">
                        <span className="shrink-0 text-[12.5px] text-slate-500">Город</span>
                        <CustomSelect variant="ios" searchable value={city} onChange={saveCity} options={cityOptions}
                                      placeholder="Город аккаунта" searchPlaceholder="Найти город" ariaLabel="Город аккаунта"
                                      className="min-w-0 flex-1" disabled={Boolean(busy)} />
                        <IosHint align="right" label="Зачем город"
                                 text="Нужен для отчёта по городам. Указывается один раз — запомнится за аккаунтом." />
                    </div>
                )}
            </section>

            {/* ── Причина обращения — после выбора аккаунта ─────────────────── */}
            {account && (
                <section className="space-y-1.5">
                    <div className={iosGroupLabel}>Причина обращения</div>
                    <div className={`${iosCard} space-y-2 p-3`}>
                        {topics.length > 0 && (
                            <div className="flex flex-wrap gap-1.5">
                                {topics.map((topic) => (
                                    <span key={`${topic.category_id}:${topic.subcategory_id ?? 0}`}
                                          className="inline-flex max-w-full items-center gap-1 rounded-lg bg-slate-900 py-1 pl-2.5 pr-1 text-[12.5px] font-medium text-white">
                                        <span className="min-w-0 truncate">{topicLabel(topic)}</span>
                                        <button type="button" aria-label={`Убрать причину «${topicLabel(topic)}»`}
                                                onClick={() => edit(setTopics)(topics.filter((item) => item !== topic))}
                                                className="grid h-5 w-5 shrink-0 place-items-center rounded-md text-white/60 hover:bg-white/15 hover:text-white">
                                            <X size={12} />
                                        </button>
                                    </span>
                                ))}
                            </div>
                        )}
                        {total >= SEARCH_FROM && (
                            <label className="relative block">
                                <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
                                <input value={query} onChange={(event) => setQuery(event.target.value)}
                                       placeholder="Найти причину" className={`${iosInput} py-2 pl-8`} />
                            </label>
                        )}
                        {!boot.categories?.length && (
                            <p className="text-[12.5px] text-slate-500">
                                Причины ещё не заведены — их добавляет руководитель в разделе «Карточка клиента» → «Настройки».
                            </p>
                        )}
                        {boot.categories?.length > 0 && !categories.length && (
                            <p className="text-[12.5px] text-slate-500">Такой причины нет.</p>
                        )}
                        <ul className="space-y-0.5">
                            {categories.map((category) => {
                                const children = category.children || [];
                                const open = Boolean(query) || expanded === category.id;
                                const count = pickedInCategory(topics, category.id);
                                if (!children.length) {
                                    const picked = isTopicPicked(topics, category.id);
                                    return (
                                        <li key={category.id}>
                                            <button type="button" aria-pressed={picked}
                                                    disabled={!picked && !canAddTopic(topics, topicsLimit)}
                                                    onClick={() => edit(setTopics)(toggleTopic(topics, category, null, topicsLimit))}
                                                    className={`flex w-full items-center justify-between gap-2 rounded-xl px-2.5 py-2 text-left text-[13.5px] transition disabled:opacity-40 ${
                                                        picked ? 'bg-blue-50 font-medium text-blue-800' : 'text-slate-800 hover:bg-slate-50'}`}>
                                                <span className="min-w-0 truncate">{category.title}</span>
                                                {picked && <Check size={15} className="shrink-0 text-blue-600" />}
                                            </button>
                                        </li>
                                    );
                                }
                                return (
                                    <li key={category.id}>
                                        <button type="button" aria-expanded={open}
                                                onClick={() => setExpanded(open && !query ? null : category.id)}
                                                className="flex w-full items-center justify-between gap-2 rounded-xl px-2.5 py-2 text-left text-[13.5px] text-slate-800 hover:bg-slate-50">
                                            <span className="min-w-0 truncate">{category.title}</span>
                                            <span className="flex shrink-0 items-center gap-1.5">
                                                {count > 0 && <span className="rounded-full bg-blue-600 px-1.5 text-[11px] font-semibold tabular-nums text-white">{count}</span>}
                                                <ChevronDown size={15} className={`text-slate-400 transition-transform ${open ? 'rotate-180' : ''}`} />
                                            </span>
                                        </button>
                                        {open && (
                                            <div className="flex flex-wrap gap-1.5 px-2.5 pb-2 pt-1">
                                                {children.map((sub) => {
                                                    const picked = isTopicPicked(topics, category.id, sub.id);
                                                    return (
                                                        <button key={sub.id} type="button" aria-pressed={picked}
                                                                disabled={!picked && !canAddTopic(topics, topicsLimit)}
                                                                onClick={() => edit(setTopics)(toggleTopic(topics, category, sub, topicsLimit))}
                                                                className={`rounded-lg px-2.5 py-1.5 text-left text-[12.5px] transition active:scale-[0.98] disabled:opacity-40 ${
                                                                    picked ? 'bg-blue-600 font-medium text-white' : 'bg-slate-100 text-slate-700 hover:bg-slate-200'}`}>
                                                            {sub.title}
                                                        </button>
                                                    );
                                                })}
                                            </div>
                                        )}
                                    </li>
                                );
                            })}
                        </ul>
                    </div>
                </section>
            )}

            {account && (
                <section className="space-y-1.5">
                    <div className={iosGroupLabel}>Комментарий</div>
                    <textarea value={comment} onChange={(event) => edit(setComment)(event.target.value)}
                              rows={3} maxLength={limits.comment || 4000} placeholder="Что сказал человек и чем закончился разговор"
                              className={`${iosInput} resize-y bg-white ring-1 ring-slate-200/70`} />
                </section>
            )}

            {account && (
                <section className="space-y-1.5">
                    {requests.length > 0 && (
                        <ul className="space-y-1">
                            {requests.map((request) => (
                                <li key={request.id} className={`${iosCard} flex items-center justify-between gap-2 px-3 py-2 text-[12.5px]`}>
                                    <span className="min-w-0 truncate text-slate-700">Запрос №{request.id} · {request.destination_title}</span>
                                    <IosBadge tone={request.delivery_status !== 'sent' ? 'red' : (REQUEST_TONES[request.status] || 'slate')}>
                                        {request.delivery_status !== 'sent' ? 'Не доставлен' : request.status_label}
                                    </IosBadge>
                                </li>
                            ))}
                        </ul>
                    )}
                    <button type="button" onClick={openComposer} className={`${iosBtnSecondary} w-full`}>
                        <Send size={14} /> Запрос в другой отдел
                    </button>
                </section>
            )}

            {/* ── Низ окна: чего не хватает и главная кнопка ────────────────── */}
            <div className="fixed inset-x-0 bottom-0 z-20 border-t border-slate-200/70 bg-white/90 px-4 py-3 backdrop-blur">
                <div className="mx-auto flex w-full max-w-2xl items-center gap-3">
                    <span className="min-w-0 flex-1 text-[12px] text-slate-500">{missing}</span>
                    <button type="button" onClick={complete} disabled={Boolean(missing) || busy === 'complete'} className={iosBtnPrimary}>
                        {busy === 'complete' ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />}
                        {saveLabel(card)}
                    </button>
                </div>
            </div>

            <RequestComposer open={composer} onClose={() => setComposer(false)} api={api} card={{ ...card, topics }}
                             account={account} destinations={boot.destinations} limits={limits} toast={toast}
                             onCreated={() => api.cardBundle(cardId).then(({ data }) => onBundle(data)).catch(() => {})} />
            {done && <span className="sr-only">Карточка заполнена</span>}
        </div>
    );
}
