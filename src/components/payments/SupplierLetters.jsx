import React, { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { Check, Copy, Loader2, Mail, Search } from 'lucide-react';
import { IosModal, IosSegmented, iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosInput } from '../ui/ios';
import {
    DICTIONARY_PAGE_SIZE, HINTS, LETTER_FORMS, changedLine, composeLink, excludedLine, fmtDateTime, isPhoneDevice, letterAddresses,
    letterDay, letterInitials, letterMoment, letterTime, mailingSummary, openedToast, recipientState, recipientsChosen,
    recipientsLine, toggleRecipient, allRecipients,
} from './paymentsMeta';
import { Bone, FieldHint, GroupDivider, Pager, TonePill, copyRich, errorText } from './paymentsUi';

/*
 * «Письма поставщикам» — письма о смене реквизитов наших компаний внутри раздела
 * (просьба владельца 20.10.2026: «всё же должно быть внутри раздела Оплата счетов…
 * аккуратно и удобно в стиле ios/macos»). Устройство писем — payments/requisites_mail.py.
 *
 * Как «Почта» на Mac: список по дням — кружок с буквами компании, название, время,
 * что изменилось и что с письмом; письмо открывается окном с шапкой «от кого, кому,
 * тема» и самим письмом в том виде, в каком его получит поставщик (HTML письма — в
 * изолированном фрейме без скриптов), рядом — кому оно. Оттуда же «Не отправлять» и
 * «Отправить письмо»: с 27.10.2026 письмо отправляет человек из почты компании
 * (владелец: «пока убери автоотправку и сделай полу ручной») — SendLetterButton.
 *
 * Видит администратор раздела — тот, кто правит «Реквизиты компаний» (то же правило
 * у ручек: routes_directory._mailing_guard).
 */

const FRAME_START = 560;

/* Письмо как его видит поставщик. Фрейм без скриптов (sandbox без allow-scripts):
   HTML собирает сервер, но и чужое в нём не выполнилось бы. Рост — по содержимому. */
export const LetterFrame = ({ html, onHeight }) => {
    const ref = useRef(null);
    const [height, setHeight] = useState(FRAME_START);
    const heightRef = useRef(FRAME_START);
    const measure = useCallback(() => {
        const next = ref.current?.contentDocument?.documentElement?.scrollHeight;
        if (!next || Math.abs(next - heightRef.current) < 2) return;
        heightRef.current = next;
        setHeight(next);
        onHeight?.(next);
    }, [onHeight]);
    useEffect(() => {
        const frame = ref.current;
        if (!frame || typeof ResizeObserver !== 'function') return undefined;
        // Окно сузили (телефон, край экрана) — письмо переносится и меняет рост.
        const observer = new ResizeObserver(() => measure());
        observer.observe(frame);
        return () => observer.disconnect();
    }, [measure]);
    return (
        <iframe
            ref={ref}
            title="Письмо поставщику"
            srcDoc={html}
            sandbox="allow-same-origin"
            referrerPolicy="no-referrer"
            onLoad={measure}
            style={{ height }}
            className="block w-full border-0 bg-[#f5f5f7]"
            data-letter-frame
        />
    );
};

const toLine = (item) => {
    // Кому письмо адресовано — по алфавиту, как в «Почте»; убранные и отданные новому письму — не адресаты.
    const list = (item?.recipients || []).filter((recipient) => recipient.status !== 'removed' && recipient.status !== 'superseded')
        .slice().sort((a, b) => a.email.localeCompare(b.email));
    // Ждущее письмо: адресов ещё нет — кому оно уйдёт по галочкам.
    if (!list.length) return item?.candidates ? recipientsLine(item.candidates) : '—';
    const shown = list.slice(0, 2).map((recipient) => recipient.email).join(', ');
    return list.length > 2 ? `${shown} и ещё ${list.length - 2}` : shown;
};

/* Шапка письма, как в «Почте»: кружок отправителя, от кого, когда, кому, тема — и само письмо.
   `bare` — без своей карточки (внутри карточки письма в окне компании). */
export const LetterView = ({ item, sender, bare = false, onHeight }) => {
    const name = sender?.name || item?.legal_entity_name || '';
    return (
        <div className={bare ? '' : `${iosCard} overflow-hidden`} data-letter-view>
            <div className="flex gap-3 px-4 py-3.5">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-gradient-to-b from-slate-300 to-slate-400 text-[14px] font-semibold text-white" aria-hidden="true">
                    {letterInitials(name)}
                </span>
                {/* basis-0: оболочка телефона переносит ряды с gap — без него текст уезжал бы под кружок. */}
                <div className="min-w-0 grow basis-0">
                    <div className="flex items-baseline justify-between gap-3">
                        <span className="min-w-0 truncate text-[14px] font-semibold text-slate-900">{name}</span>
                        <span className="shrink-0 text-[12px] tabular-nums text-slate-500">{fmtDateTime(letterMoment(item))}</span>
                    </div>
                    {sender?.address && <div className="truncate text-[12.5px] text-slate-500">{sender.address}</div>}
                    <div className="mt-1 truncate text-[12.5px] text-slate-600"><span className="text-slate-400">Кому: </span>{toLine(item)}</div>
                    <div className="mt-1.5 text-[14px] font-medium leading-snug text-slate-900">{item?.letter?.subject}</div>
                </div>
            </div>
            {item?.letter?.html && <div className="border-t border-slate-100"><LetterFrame html={item.letter.html} onHeight={onHeight} /></div>}
        </div>
    );
};

/* «Отправить письмо» (27.10.2026). Настоящая ссылка <a>: почтовую программу или вкладку
   Gmail открывает сам браузер — без всплывающих окон, которые он мог бы заблокировать.
   Тем же щелчком текст письма ложится в буфер с оформлением, а ждущее письмо
   отмечается «открыто в почте» (`onOpened` получает адреса, вставленные в «Скрытую
   копию»). У открытого письма — «Открыть в почте ещё раз»: то же письмо тем же адресам,
   отметка не меняется. Адреса не влезают в ссылку — почта открывается без них, в буфер
   идут адреса, а текст — отдельной кнопкой «Скопировать текст». */
export const SendLetterButton = ({ item, sender, busy = false, onOpened, showToast, primary = true, className = '' }) => {
    const [copied, setCopied] = useState(false);
    const phone = isPhoneDevice();
    const pending = item?.status === 'pending';
    const bcc = letterAddresses(item);
    const full = composeLink({ provider: sender?.provider, from: sender?.address, bcc, subject: item?.letter?.subject, phone });
    const link = full.tooLong ? composeLink({ provider: sender?.provider, from: sender?.address, subject: item?.letter?.subject, phone, withBcc: false }) : full;
    const open = (event) => {
        if (busy) { event.preventDefault(); return; }
        if (!bcc.length) {
            // Чего не хватает — словами, а не серой кнопкой: кому письмо, выбирают в «Кому».
            event.preventDefault();
            showToast?.('Письмо некому отправить: отметьте поставщиков в «Кому»', 'error');
            return;
        }
        const copying = full.tooLong
            ? copyRich({ text: bcc.join(', ') })
            : copyRich({ html: item.letter?.paste, text: item.letter?.text });
        copying.then((done) => showToast?.(
            openedToast({ copied: done, web: link.web, addressesCopied: full.tooLong && done, phone }), done ? 'success' : 'error'));
        if (pending) onOpened?.(bcc);
    };
    const copyText = () => copyRich({ html: item.letter?.paste, text: item.letter?.text }).then((done) => {
        if (done) {
            setCopied(true);
            setTimeout(() => setCopied(false), 1600);
        } else showToast?.('Текст скопировать не вышло — скопируйте его из окна письма', 'error');
    });
    return (
        <>
            {full.tooLong && (
                <button type="button" onClick={copyText} className={`${iosBtnGhost} ${copied ? '!text-emerald-600' : ''}`} data-letter-copy-text>
                    {copied ? <Check size={13} /> : <Copy size={13} />} {copied ? 'Скопировано' : 'Скопировать текст'}
                </button>
            )}
            <a
                href={link.href}
                target={link.web ? '_blank' : undefined}
                rel={link.web ? 'noopener noreferrer' : undefined}
                onClick={open}
                aria-disabled={busy || undefined}
                className={`${pending && primary ? iosBtnPrimary : iosBtnSecondary} shrink-0 no-underline ${busy ? 'pointer-events-none opacity-60' : ''} ${className}`}
                data-letter-send={pending ? 'open' : 'again'}
            >
                {busy ? <Loader2 size={14} className="animate-spin" /> : <Mail size={14} aria-hidden="true" />}
                {pending ? 'Отправить письмо' : 'Открыть в почте ещё раз'}
            </a>
        </>
    );
};

/* Кому письмо — по адресу, с поставщиками; у писем, которые отправлял портал, — ушло или нет и почему. */
export const RecipientList = ({ item }) => {
    const list = item?.recipients || [];
    if (!list.length) {
        return (
            <div className={`${iosCard} px-4 py-3.5 text-[13px] leading-relaxed text-slate-600`} data-recipient-list="empty">
                {item?.status === 'cancelled' ? 'Письмо отменили — оно никому не ушло.' : 'Адресов у письма нет.'}
            </div>
        );
    }
    return (
        <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`} data-recipient-list="ready">
            {list.map((recipient) => {
                const state = recipientState(recipient);
                const problem = recipient.status === 'failed' && recipient.last_error;
                return (
                    <div key={recipient.email} className="flex items-start gap-3 px-4 py-2.5" data-recipient={recipient.status}>
                        <div className="min-w-0 grow basis-0">
                            <div className="truncate text-[13.5px] text-slate-900">{recipient.email}</div>
                            {recipient.counterparty_names?.length > 0 && (
                                <div className="truncate text-[12px] text-slate-500">{recipient.counterparty_names.join(', ')}</div>
                            )}
                            {problem && <div className="mt-0.5 break-words text-[12px] leading-snug text-rose-700">{recipient.last_error}</div>}
                        </div>
                        <TonePill tone={state.tone} className="mt-0.5 shrink-0">{state.label}</TonePill>
                    </div>
                );
            })}
        </div>
    );
};

const PICKER_SEARCH_FROM = 9;

/* Галочки «Кому» у ждущего письма (решение владельца 21.10.2026): по умолчанию отмечены
   все поставщики с почтой; снятому письмо не уйдёт. `onChange` получает поставщиков,
   которым не отправлять, — так их и хранит сервер (excluded_counterparty_ids). Пока
   запрос идёт, галочки не нажимаются: ответы не должны лечь не в том порядке. */
export const RecipientPicker = ({ candidates, busy = false, onChange }) => {
    const [query, setQuery] = useState('');
    const list = candidates || [];
    if (!list.length) {
        return (
            <div className={`${iosCard} px-4 py-3.5 text-[13px] leading-relaxed text-slate-600`} data-recipient-picker="empty">
                Ни у одного поставщика нет почты — письмо некому отправить. Адреса ведутся в «Почте поставщиков».
            </div>
        );
    }
    const allOn = list.every((item) => item.send);
    const needle = query.trim().toLowerCase();
    const shown = needle
        ? list.filter((item) => `${item.name} ${(item.emails || []).join(' ')}`.toLowerCase().includes(needle))
        : list;
    const toggle = (item) => onChange?.(toggleRecipient(list, item.id));
    return (
        <div className="space-y-2" data-recipient-picker="ready">
            <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-1">
                <span className="flex items-center gap-1.5 text-[12.5px] text-slate-600">
                    <span data-recipient-summary>{recipientsLine(list)}</span>
                    <FieldHint hint={HINTS.recipientsPick} />
                </span>
                <button type="button" disabled={busy} onClick={() => onChange?.(allRecipients(list, !allOn))}
                    className="rounded-md px-1.5 py-0.5 text-[12.5px] font-medium text-blue-600 transition hover:bg-blue-50 disabled:opacity-50">
                    {allOn ? 'Снять всех' : 'Отметить всех'}
                </button>
            </div>
            {list.length >= PICKER_SEARCH_FROM && (
                <label className="relative block">
                    <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
                    <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Найти поставщика или адрес"
                        aria-label="Найти поставщика" className={`${iosInput} !py-2 pl-9 text-[13px]`} />
                </label>
            )}
            <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`} role="group" aria-label="Кому отправить письмо">
                {shown.map((item) => (
                    <button key={item.id} type="button" role="checkbox" aria-checked={item.send} disabled={busy} onClick={() => toggle(item)}
                        className="flex w-full items-start gap-3 px-4 py-2.5 text-left transition hover:bg-[#f6f6f4] active:scale-[0.995] disabled:cursor-progress"
                        data-candidate={item.id} data-candidate-send={item.send ? 'yes' : 'no'}>
                        <span className={`mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full border-2 transition ${item.send ? 'border-[#2563eb] bg-[#2563eb] text-white' : 'border-slate-300 bg-white'}`} aria-hidden="true">
                            {item.send && <Check size={12} strokeWidth={3} />}
                        </span>
                        {/* basis-0: оболочка телефона переносит ряды с gap — без него текст уезжал бы под галочку. */}
                        <span className="min-w-0 grow basis-0">
                            <span className="flex items-center gap-2">
                                <span className={`min-w-0 truncate text-[13.5px] ${item.send ? 'text-slate-900' : 'text-slate-500'}`}>{item.name}</span>
                                {!item.is_active && <span className="shrink-0 rounded-full bg-slate-200 px-2 py-0.5 text-[11px] text-slate-600">скрыт</span>}
                            </span>
                            <span className="block truncate text-[12px] text-slate-500">{(item.emails || []).join(', ')}</span>
                        </span>
                    </button>
                ))}
                {!shown.length && <div className="px-4 py-3 text-[12.5px] text-slate-500">Никого не нашли</div>}
            </div>
        </div>
    );
};

const LetterSkeleton = () => (
    <div className={`${iosCard} overflow-hidden`} aria-hidden="true">
        <div className="flex gap-3 px-4 py-3.5">
            <span className="h-10 w-10 shrink-0 animate-pulse rounded-full bg-slate-200/70 motion-reduce:animate-none" />
            <div className="min-w-0 grow basis-0 space-y-2 pt-1">
                <Bone className="h-3 w-40" />
                <Bone className="h-2.5 w-56 max-w-full" />
                <Bone className="h-2.5 w-64 max-w-full" />
            </div>
        </div>
        <div className="border-t border-slate-100 bg-[#f5f5f7]" style={{ height: FRAME_START }} />
    </div>
);

/* Окно письма: «Письмо» и «Кому», внизу — что с письмом можно сделать. Открывается и
   из журнала, и само после сохранения новых реквизитов компании (PaymentsDictionaries). */
export const LetterWindow = ({ apiBaseUrl, headers, mailingId, onClose, showToast, onChanged }) => {
    const [reply, setReply] = useState(null);
    const [tab, setTab] = useState('letter');
    const [busy, setBusy] = useState(false);
    const [picking, setPicking] = useState(false);
    const [confirm, setConfirm] = useState(false);
    const [frameHeight, setFrameHeight] = useState(0);
    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);
    const ticketRef = useRef(0);

    const load = useCallback(async ({ quiet = false } = {}) => {
        if (!mailingId) return;
        const ticket = ticketRef.current + 1;
        ticketRef.current = ticket;
        try {
            const response = await axios.get(`${apiBaseUrl}/api/payments/mailings/${mailingId}`, { headers: headers() });
            if (ticketRef.current === ticket) setReply(response.data || null);
        } catch (err) {
            if (ticketRef.current === ticket && !quiet) toastRef.current?.(errorText(err, 'Не удалось открыть письмо'), 'error');
        }
    }, [apiBaseUrl, headers, mailingId]);

    useEffect(() => {
        setReply(null);
        setTab('letter');
        setConfirm(false);
        setFrameHeight(0);
        if (mailingId) load();
        return () => { ticketRef.current += 1; };
    }, [mailingId, load]);

    const item = reply?.item;
    const base = item ? `${apiBaseUrl}/api/payments/dictionaries/legal_entities/${item.legal_entity_id}/mailings/${item.id}` : '';

    const act = async (action, body, done) => {
        if (!item) return;
        setBusy(true);
        try {
            const response = await axios.post(`${base}/${action}`, body, { headers: headers() });
            if (done) toastRef.current?.(done(response.data || {}), 'success');
            onChanged?.();
        } catch (err) {
            toastRef.current?.(errorText(err, 'Не получилось'), 'error');
        } finally {
            setBusy(false);
            setConfirm(false);
            load({ quiet: true });
        }
    };

    /* Галочки «Кому»: видно сразу, запрос — следом; не вышло — тост и то, что на деле. */
    const pick = async (excluded) => {
        if (!item) return;
        ticketRef.current += 1;
        setReply((prev) => (prev?.item ? {
            ...prev,
            item: { ...prev.item, candidates: (prev.item.candidates || []).map((candidate) => ({ ...candidate, send: !excluded.includes(candidate.id) })) },
        } : prev));
        setPicking(true);
        try {
            await axios.post(`${base}/recipients`, { excluded }, { headers: headers() });
            onChanged?.();
        } catch (err) {
            toastRef.current?.(errorText(err, 'Не удалось изменить, кому уходит письмо'), 'error');
        } finally {
            setPicking(false);
            load({ quiet: true });
        }
    };

    const pending = item?.status === 'pending';
    const again = item?.status === 'opened' && item?.latest;
    const count = pending && item?.candidates
        ? recipientsChosen(item.candidates).chosen
        : (item?.recipients || []).filter((recipient) => recipient.status !== 'superseded').length;
    return (
        <IosModal
            open={Boolean(mailingId)}
            onClose={onClose}
            title={item?.legal_entity_name || 'Письмо поставщикам'}
            subtitle={item ? mailingSummary(item) : ''}
            maxWidth="max-w-3xl"
            sizeKey={`${item ? 'ready' : 'loading'}:${tab}:${frameHeight}`}
            footer={(
                <>
                    {pending && (confirm ? (
                        <div className="mr-auto flex items-center gap-2 text-[12.5px] text-slate-600">
                            Не отправлять письмо?
                            <button type="button" className={`${iosBtnSecondary} !text-rose-700`} disabled={busy} onClick={() => act('cancel', {}, () => 'Письмо не уйдёт')}>
                                {busy && <Loader2 size={13} className="animate-spin" />} Да
                            </button>
                            <button type="button" className={iosBtnSecondary} disabled={busy} onClick={() => setConfirm(false)}>Нет</button>
                        </div>
                    ) : (
                        <button type="button" className={`${iosBtnSecondary} mr-auto`} disabled={busy} onClick={() => setConfirm(true)}>Не отправлять</button>
                    ))}
                    {again && (
                        <SendLetterButton item={item} sender={reply?.sender} className="mr-auto" showToast={(text, kind) => toastRef.current?.(text, kind)} />
                    )}
                    <button type="button" className={pending ? iosBtnSecondary : iosBtnPrimary} onClick={onClose}>{pending ? 'Закрыть' : 'Готово'}</button>
                    {pending && !confirm && (
                        <SendLetterButton item={item} sender={reply?.sender} busy={busy} showToast={(text, kind) => toastRef.current?.(text, kind)}
                            onOpened={(addresses) => act('open', { addresses })} />
                    )}
                </>
            )}
        >
            <div className="space-y-3" data-letter-window={item ? 'ready' : 'loading'}>
                <IosSegmented
                    value={tab}
                    onChange={setTab}
                    slide
                    ariaLabel="Письмо или кому"
                    options={[{ value: 'letter', label: 'Письмо' }, { value: 'recipients', label: 'Кому', count }]}
                />
                {!item && <LetterSkeleton />}
                {item && tab === 'letter' && (
                    <LetterView item={item} sender={reply?.sender} onHeight={setFrameHeight} />
                )}
                {item && tab === 'recipients' && (pending && item.candidates
                    ? <RecipientPicker candidates={item.candidates} busy={picking} onChange={pick} />
                    : (
                        <>
                            <RecipientList item={item} />
                            {excludedLine(item.excluded_names) && (
                                <div className="px-1 text-[12.5px] leading-snug text-slate-500" data-excluded-names>{excludedLine(item.excluded_names)}</div>
                            )}
                        </>
                    ))}
            </div>
        </IosModal>
    );
};

export const LetterRow = ({ item, onOpen }) => {
    const failed = item.counts?.failed > 0;
    return (
        <button
            type="button"
            onClick={() => onOpen(item.id)}
            data-letter-row={item.id}
            data-letter-status={item.status}
            className="flex w-full items-start gap-3 rounded-xl border border-slate-200 bg-white px-3.5 py-3 text-left shadow-[0_1px_2px_rgba(15,23,42,0.06)] transition hover:brightness-[0.98] active:scale-[0.995]"
        >
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-gradient-to-b from-slate-300 to-slate-400 text-[13px] font-semibold text-white" aria-hidden="true">
                {letterInitials(item.legal_entity_name)}
            </span>
            <span className="min-w-0 grow basis-0">
                <span className="flex items-baseline justify-between gap-3">
                    <span className="min-w-0 truncate text-[13.5px] font-semibold text-slate-900">{item.legal_entity_name}</span>
                    <span className="shrink-0 text-[12px] tabular-nums text-slate-500">{letterTime(letterMoment(item))}</span>
                </span>
                <span className="block truncate text-[13px] text-slate-700">
                    {item.changed?.length ? `Изменилось: ${changedLine(item.changed)}` : item.subject}
                </span>
                <span className="block truncate text-[12px] text-slate-500">
                    {/* Красным — только то, что не ушло; автор правки — серым. */}
                    <span className={failed ? 'text-rose-700' : undefined}>{mailingSummary(item)}</span>
                    {(item.updated_by_name || item.created_by_name) && ` · ${item.updated_by_name || item.created_by_name}`}
                </span>
            </span>
        </button>
    );
};

/* Экран «Письма поставщикам» в «Справочниках». */
const SupplierLetters = ({ apiBaseUrl, headers, showToast }) => {
    const [reply, setReply] = useState(null);
    const [page, setPage] = useState(1);
    // С первого кадра — заготовка списка, а не пустое место до ответа сервера.
    const [loading, setLoading] = useState(true);
    const [openId, setOpenId] = useState(null);
    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);
    const ticketRef = useRef(0);

    const load = useCallback(async ({ quiet = false } = {}) => {
        const ticket = ticketRef.current + 1;
        ticketRef.current = ticket;
        if (!quiet) setLoading(true);
        try {
            const response = await axios.get(`${apiBaseUrl}/api/payments/mailings`, {
                headers: headers(), params: { limit: DICTIONARY_PAGE_SIZE, offset: (page - 1) * DICTIONARY_PAGE_SIZE },
            });
            if (ticketRef.current === ticket) setReply(response.data || null);
        } catch (err) {
            if (ticketRef.current === ticket && !quiet) toastRef.current?.(errorText(err, 'Не удалось загрузить письма'), 'error');
        } finally {
            if (ticketRef.current === ticket) setLoading(false);
        }
    }, [apiBaseUrl, headers, page]);

    useEffect(() => {
        load();
        return () => { ticketRef.current += 1; };
    }, [load]);

    const items = reply?.items || [];
    const groups = [];
    items.forEach((item) => {
        const day = letterDay(letterMoment(item));
        if (!groups.length || groups[groups.length - 1].day !== day) groups.push({ day, items: [] });
        groups[groups.length - 1].items.push(item);
    });

    return (
        <div className="space-y-2" data-supplier-letters>
            {!reply && loading && (
                <div className="space-y-1.5" aria-hidden="true">
                    {[0, 1, 2].map((index) => (
                        <div key={index} className="flex gap-3 rounded-xl border border-slate-200 bg-white px-3.5 py-3">
                            <span className="h-9 w-9 shrink-0 animate-pulse rounded-full bg-slate-200/70 motion-reduce:animate-none" />
                            <span className="min-w-0 grow basis-0 space-y-2 pt-1"><Bone className="h-3 w-40" /><Bone className="h-2.5 w-64 max-w-full" /></span>
                        </div>
                    ))}
                </div>
            )}
            {reply && !items.length && (
                <div className={`${iosCard} px-4 py-10 text-center`} data-supplier-letters-empty>
                    <div className="text-[13.5px] font-medium text-slate-700">Писем пока не было</div>
                    <div className="mt-1 text-[12.5px] text-slate-500">Письмо готовится само, когда меняются реквизиты компании в «Реквизитах компаний».</div>
                </div>
            )}
            {groups.map((group) => (
                <div key={group.day} className="space-y-1.5">
                    <GroupDivider>{group.day}</GroupDivider>
                    {group.items.map((item) => <LetterRow key={item.id} item={item} onOpen={setOpenId} />)}
                </div>
            ))}
            <Pager page={page} pageSize={DICTIONARY_PAGE_SIZE} total={reply?.total || 0} loading={loading} forms={LETTER_FORMS} onPage={setPage} />
            <LetterWindow
                apiBaseUrl={apiBaseUrl}
                headers={headers}
                mailingId={openId}
                onClose={() => setOpenId(null)}
                showToast={showToast}
                onChanged={() => load({ quiet: true })}
            />
        </div>
    );
};

export default SupplierLetters;
