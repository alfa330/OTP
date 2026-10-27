import React, { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { Loader2, Mail } from 'lucide-react';
import { iosBtnSecondary, iosCard } from '../ui/ios';
import {
    HINTS, changedLine, excludedLine, mailNotice, mailingSummary, recipientsChosen, recipientsLine, requisitesChanged,
} from './paymentsMeta';
import { Bone, FieldHint, Fold, NoticeBox, errorText } from './paymentsUi';
import { LetterView, RecipientPicker, SendLetterButton } from './SupplierLetters';

/*
 * Письма поставщикам о смене реквизитов компании — в окне записи «Реквизиты
 * компаний» (просьба владельца 20.10.2026; с 27.10.2026 письмо отправляет человек из
 * почты компании: «пока убери автоотправку и сделай полу ручной»). Устройство и
 * решения — payments/requisites_mail.py.
 *
 * Что здесь видно и когда:
 *   — человек правит реквизиты: голубая строка — будет ли письмо, сколько адресов его
 *     получат и у кого почты нет; вернул реквизиты к тем, что знают поставщики, —
 *     строка говорит, что письма не будет. Правка не реквизитов (примечание,
 *     «скрыта») строки не вызывает;
 *   — письмо ждёт отправки: «Отправить письмо» (открывает почту компании с готовым
 *     письмом), «Не отправлять» и «Кому» — галочки по поставщикам с почтой;
 *   — письма, открытые в почте: когда и кому, у последнего — «Открыть в почте ещё
 *     раз», и сам текст письма.
 * Ни писем, ни правки реквизитов — блока нет вовсе. Письма у компании есть —
 * окно открывается сразу с заготовкой блока в его рост: ответ сервера не
 * раздвигает окно рывком.
 *
 * Блок видит тот, кто правит справочник (сервер отдаёт письма только ему).
 */

const SHOWN = 3;

const recipientLine = (recipient) => [
    recipient.email,
    recipient.counterparty_names?.length ? recipient.counterparty_names.join(', ') : '',
].filter(Boolean).join(' · ');

const problemText = (recipient) => (recipient.status === 'removed'
    ? 'адрес убран из карточки поставщика — письмо по нему не ушло'
    : recipient.last_error);

const Label = () => (
    <span className="flex min-h-5 items-center gap-1.5 px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
        <Mail size={13} aria-hidden="true" /> Письма поставщикам
        <FieldHint hint={HINTS.requisitesMail} />
    </span>
);

/* Заготовка карточки письма — того же роста: две строки и строка «Текст письма». */
const MailingSkeleton = () => (
    <div className={`${iosCard} overflow-hidden`} aria-hidden="true">
        <div className="space-y-2.5 px-3.5 py-3.5">
            <Bone className="h-3 w-44" />
            <Bone className="h-2.5 w-64 max-w-full" />
        </div>
        <div className="flex h-11 items-center border-t border-slate-100 px-4"><Bone className="h-2.5 w-24" /></div>
    </div>
);

export const MailingCard = ({ item, busy, picking = false, sender, showToast, onCancel, onOpen, onPick }) => {
    const [confirm, setConfirm] = useState(false);
    const [showLetter, setShowLetter] = useState(false);
    const [showRecipients, setShowRecipients] = useState(false);
    const [frameHeight, setFrameHeight] = useState(0);
    // Что не ушло — у писем, которые до 27.10.2026 отправлял сам портал.
    const problems = (item.recipients || []).filter((recipient) => recipient.status === 'failed' || recipient.status === 'removed');
    const failed = problems.filter((recipient) => recipient.status === 'failed');
    const who = item.status === 'cancelled' ? '' : (item.updated_by_name || item.created_by_name || '');
    const about = [item.changed?.length ? `Изменилось: ${changedLine(item.changed)}` : '', who].filter(Boolean).join(' · ');
    const pending = item.status === 'pending';
    const again = item.status === 'opened' && item.latest;
    useEffect(() => { if (!pending) setConfirm(false); }, [pending]);
    return (
        <div className={`${iosCard} overflow-hidden`} data-mailing={item.id} data-mailing-status={item.status}>
            <div className="space-y-2 px-3.5 py-3">
                <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2">
                    <div className="min-w-0 grow basis-60">
                        <div className={`text-[13.5px] font-medium ${failed.length ? 'text-rose-700' : 'text-slate-900'}`}>{mailingSummary(item)}</div>
                        {about && <div className="text-[12px] text-slate-500">{about}</div>}
                    </div>
                    {pending && (confirm ? (
                        <div className="flex shrink-0 items-center gap-2 text-[12.5px] text-slate-600">
                            Не отправлять письмо?
                            <button type="button" className={`${iosBtnSecondary} !text-rose-700`} disabled={busy} onClick={() => onCancel(item, () => setConfirm(false))}>
                                {busy && <Loader2 size={13} className="animate-spin" />} Да
                            </button>
                            <button type="button" className={iosBtnSecondary} disabled={busy} onClick={() => setConfirm(false)}>Нет</button>
                        </div>
                    ) : (
                        <div className="flex shrink-0 flex-wrap items-center gap-2">
                            <button type="button" className={iosBtnSecondary} disabled={busy} onClick={() => setConfirm(true)}>Не отправлять</button>
                            <SendLetterButton item={item} sender={sender} busy={busy} showToast={showToast} onOpened={(addresses) => onOpen(item, addresses)} />
                        </div>
                    ))}
                    {again && <SendLetterButton item={item} sender={sender} showToast={showToast} />}
                </div>
                {problems.length > 0 && (
                    <ul className="space-y-1 text-[12.5px] leading-snug">
                        {problems.map((recipient) => (
                            <li key={recipient.email} className="break-words" data-mailing-problem={recipient.status}>
                                <span className={recipient.status === 'removed' ? 'text-slate-500' : 'text-slate-900'}>{recipientLine(recipient)}</span>
                                {problemText(recipient) && <span className="text-slate-500"> — {problemText(recipient)}</span>}
                            </li>
                        ))}
                    </ul>
                )}
                {excludedLine(item.excluded_names) && (
                    <div className="break-words text-[12.5px] leading-snug text-slate-500" data-excluded-names>{excludedLine(item.excluded_names)}</div>
                )}
            </div>
            {pending && item.candidates && (
                <div className="border-t border-slate-100" data-mailing-recipients>
                    {/* Кому уйдёт письмо — галочки, пока оно ждёт. */}
                    <Fold id={`recipients-${item.id}`} title="Кому" summary={recipientsLine(item.candidates)} open={showRecipients}
                        onToggle={() => setShowRecipients((value) => !value)} sizeKey={item.candidates.length}>
                        <div className="bg-slate-50/60 p-2">
                            <RecipientPicker candidates={item.candidates} busy={picking} onChange={(excluded) => onPick?.(item, excluded)} />
                        </div>
                    </Fold>
                </div>
            )}
            {(item.letter?.html || item.letter?.text) && (
                <div className="border-t border-slate-100">
                    {/* Письмо — то же, что видит поставщик (как в «Письмах поставщикам»), раскрывается по высоте. */}
                    <Fold id={`letter-${item.id}`} title="Письмо" summary={item.letter.subject} open={showLetter}
                        onToggle={() => setShowLetter((value) => !value)} sizeKey={frameHeight}>
                        <div data-mailing-letter>
                            {item.letter.html
                                ? <LetterView item={{ ...item, legal_entity_name: sender?.name }} sender={sender} bare onHeight={setFrameHeight} />
                                : <div className="whitespace-pre-wrap break-words px-4 py-3 text-[12.5px] leading-relaxed text-slate-700">{item.letter.text}</div>}
                        </div>
                    </Fold>
                </div>
            )}
        </div>
    );
};

const RequisitesMailings = ({ apiBaseUrl, headers, entity, draft, showToast, onChanged }) => {
    const [data, setData] = useState(null);
    const [busy, setBusy] = useState(false);
    const [picking, setPicking] = useState(false);
    const [older, setOlder] = useState(false);
    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);
    const toast = useCallback((text, kind) => toastRef.current?.(text, kind), []);
    // Билет запроса: поздний ответ прежнего чтения не ложится поверх свежего.
    const ticketRef = useRef(0);
    const entityId = entity?.id;
    const base = `${apiBaseUrl}/api/payments/dictionaries/legal_entities/${entityId}/mailings`;

    const reload = useCallback(async ({ quiet = false } = {}) => {
        if (!entityId) return;
        const ticket = ticketRef.current + 1;
        ticketRef.current = ticket;
        try {
            const response = await axios.get(base, { headers: headers() });
            if (ticketRef.current === ticket) setData(response.data || null);
        } catch (err) {
            if (ticketRef.current === ticket && !quiet) toastRef.current?.(errorText(err, 'Не удалось загрузить письма поставщикам'), 'error');
        }
    }, [base, entityId, headers]);

    useEffect(() => {
        reload();
        return () => { ticketRef.current += 1; };
    }, [reload]);

    const act = useCallback(async (item, action, body, done, after) => {
        setBusy(true);
        ticketRef.current += 1;
        try {
            const response = await axios.post(`${base}/${item.id}/${action}`, body, { headers: headers() });
            setData(response.data || null);
            if (done) toastRef.current?.(done(response.data || {}), 'success');
            onChanged?.();
        } catch (err) {
            toastRef.current?.(errorText(err, 'Не получилось'), 'error');
            // Письмо могли открыть или отменить в другом окне: показать, что есть на деле.
            reload({ quiet: true });
        } finally {
            setBusy(false);
            after?.();
        }
    }, [base, headers, onChanged, reload]);

    /* Галочки «Кому»: видно сразу, ответ сервера — следом (он же и есть правда). */
    const pick = useCallback(async (item, excluded) => {
        ticketRef.current += 1;
        setData((prev) => (prev ? {
            ...prev,
            items: (prev.items || []).map((entry) => (entry.id === item.id ? {
                ...entry, candidates: (entry.candidates || []).map((candidate) => ({ ...candidate, send: !excluded.includes(candidate.id) })),
            } : entry)),
        } : prev));
        setPicking(true);
        try {
            const response = await axios.post(`${base}/${item.id}/recipients`, { excluded }, { headers: headers() });
            setData(response.data || null);
        } catch (err) {
            toastRef.current?.(errorText(err, 'Не удалось изменить, кому уходит письмо'), 'error');
            reload({ quiet: true });
        } finally {
            setPicking(false);
        }
    }, [base, headers, reload]);

    if (!entityId) return null;
    if (!data) {
        // Письма есть — заготовка в их рост, чтобы ответ сервера не раздвигал окно.
        const count = Math.min(entity?.mailings_count || 0, SHOWN);
        if (!count) return null;
        return (
            <section className="space-y-2" data-requisites-mailings="loading">
                <Label />
                {Array.from({ length: count }, (_, index) => <MailingSkeleton key={index} />)}
            </section>
        );
    }
    const items = data.items || [];
    const changed = requisitesChanged(entity, draft);
    const waiting = items.find((item) => item.status === 'pending');
    if (!changed.length && !items.length) return null;

    const reverting = changed.length > 0 && data.known && !requisitesChanged(data.known, draft).length;
    // Письмо уже ждёт — получат те, кто отмечен в его «Кому», а не все поставщики с почтой.
    const chosen = waiting?.candidates
        ? { ...data.mail?.recipients, addresses: recipientsChosen(waiting.candidates).addresses,
            excluded: waiting.candidates.filter((candidate) => !candidate.send).length }
        : data.mail?.recipients;
    const recent = items.slice(0, SHOWN);
    const earlier = items.slice(SHOWN);
    const card = (item) => (
        <MailingCard
            key={item.id}
            item={item}
            busy={busy}
            picking={picking}
            sender={data.sender}
            showToast={toast}
            onPick={pick}
            onCancel={(target, after) => act(target, 'cancel', {}, () => 'Письмо не уйдёт', after)}
            onOpen={(target, addresses) => act(target, 'open', { addresses })}
        />
    );
    return (
        <section className="space-y-2" data-requisites-mailings="ready">
            <Label />
            {changed.length > 0 && (
                <NoticeBox tone="blue" text="">
                    <span data-mailing-notice={reverting ? 'reverting' : 'letter'}>
                        {mailNotice({ recipients: chosen, waiting: Boolean(waiting), reverting })}
                    </span>
                </NoticeBox>
            )}
            {recent.map(card)}
            {earlier.length > 0 && (
                <div className={`${iosCard} overflow-hidden`}>
                    <Fold
                        id="earlier-mailings"
                        title="Ранние письма"
                        summary={data.total > items.length ? `${earlier.length} из ${data.total - SHOWN}` : String(earlier.length)}
                        open={older}
                        onToggle={() => setOlder((value) => !value)}
                    >
                        <div className="space-y-2 bg-slate-50/60 p-2">{earlier.map(card)}</div>
                    </Fold>
                </div>
            )}
        </section>
    );
};

export default RequisitesMailings;
