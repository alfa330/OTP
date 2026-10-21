import React, { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { AtSign, ExternalLink, Eye, EyeOff, Loader2 } from 'lucide-react';
import { iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard } from '../ui/ios';
import { HINTS, mailboxLine, parseEmails, serverLine } from './paymentsMeta';
import { ErrorBox, Field, FieldHint, FormInput, TonePill, errorText, fromKeyboard, revealAnchor } from './paymentsUi';

/*
 * «Почта для писем» — в окне компании («Реквизиты компаний»). Решение владельца
 * 21.10.2026 (ответ на вопрос с вариантами): у каждой компании своя почта; адрес и
 * пароль приложения, сервер подставляется сам по адресу; «Подключить» сначала шлёт
 * пробное письмо на этот же адрес и только потом сохраняет; пароль хранится
 * зашифрованным и больше не показывается; письма компании без почты ждут.
 * Устройство — payments/mailbox.py.
 *
 * Как «Почта» на Mac при добавлении ящика: адрес → какая это почта (Gmail, Яндекс,
 * Mail.ru, свой домен по MX); сервер спрашиваем, только если не узнали. Отказ почты —
 * красным у того поля, которое править (пароль, адрес, сервер), со ссылкой, где взять
 * пароль приложения.
 *
 * `controlRef` — окну компании: есть ли недоподключённая почта и «подключить» её
 * перед «Сохранить», чтобы набранный пароль не пропал с закрытием окна.
 *
 * «Войти через Google» (владелец 21.10.2026: «без каких-либо лишних действий»): выбрали
 * аккаунт, разрешили отправку — почта подключена, пароль не нужен (payments/google_mail.py).
 * Google открывается отдельным окошком поверх портала; ответ приходит сообщением от
 * страницы API (postMessage), а если окошко не открылось или его закрыли — блок просто
 * перечитывает почту компании.
 */

const EMPTY = { address: '', password: '', host: '', port: '465' };
const GOOGLE_NOTE = 'otp-payments-mailbox';

/* Значок Google — как требуют правила оформления кнопки «Войти через Google». */
const GoogleMark = () => (
    <svg viewBox="0 0 48 48" width="18" height="18" aria-hidden="true">
        <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
        <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
        <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
        <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
);

const apiOrigin = (apiBaseUrl) => {
    try {
        return new URL(apiBaseUrl, typeof window === 'undefined' ? 'http://localhost' : window.location.href).origin;
    } catch {
        return '';
    }
};
const DETECT_DELAY_MS = 500;
const ANCHORS = ['address', 'password', 'host', 'port'];

const oneAddress = (value) => {
    const { emails, bad } = parseEmails(value);
    if (bad) return { error: `«${bad.slice(0, 80)}» — не похоже на адрес почты` };
    if (emails.length !== 1) return { error: emails.length ? 'Укажите один адрес почты' : 'Укажите адрес почты' };
    return { address: emails[0] };
};

const Label = () => (
    <span className="flex min-h-5 items-center gap-1.5 px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
        <AtSign size={13} aria-hidden="true" /> Почта для писем
        <FieldHint hint={HINTS.companyMailbox} />
    </span>
);

const CompanyMailbox = ({ apiBaseUrl, headers, entity, showToast, onChanged, controlRef }) => {
    const entityId = entity?.id;
    const base = `${apiBaseUrl}/api/payments/dictionaries/legal_entities/${entityId}/mailbox`;
    const [box, setBox] = useState(entity?.mailbox || null);
    const [editing, setEditing] = useState(!entity?.mailbox);
    const [form, setForm] = useState(EMPTY);
    const [manual, setManual] = useState(false);
    const [found, setFound] = useState(null);       // { address, server, guess } — какая это почта
    const [detecting, setDetecting] = useState(false);
    const [issues, setIssues] = useState({});
    const [busy, setBusy] = useState(null);         // 'connect' | 'check' | 'remove'
    const [confirmRemove, setConfirmRemove] = useState(false);
    const [showPassword, setShowPassword] = useState(false);
    const [googleOn, setGoogleOn] = useState(Boolean(entity?.google_mail));
    const popupRef = useRef(null);
    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);
    const ticketRef = useRef(0);
    const detectRef = useRef({ ticket: 0, address: '' });
    // Поля сервера открыло само определение (почту не узнали), а не человек: исправили
    // адрес на узнаваемый — поля уходят сами.
    const autoManualRef = useRef(false);

    // Окно открылось по строке списка — сразу видно то, что в ней; свежее подтягивается тихо.
    const refresh = useCallback(async () => {
        if (!entityId) return;
        const ticket = ticketRef.current + 1;
        ticketRef.current = ticket;
        try {
            const response = await axios.get(base, { headers: headers() });
            if (ticketRef.current !== ticket) return;
            setBox(response.data?.mailbox || null);
            if (response.data?.google) setGoogleOn(Boolean(response.data.google.enabled));
        } catch {
            // Блок уже показал то, что пришло со списком.
        }
    }, [base, entityId, headers]);
    useEffect(() => {
        refresh();
        return () => { ticketRef.current += 1; detectRef.current.ticket += 1; };
    }, [refresh]);

    const detect = useCallback(async (value) => {
        const { address } = oneAddress(value);
        if (!address || address === detectRef.current.address) return;
        const ticket = detectRef.current.ticket + 1;
        detectRef.current = { ticket, address };
        setDetecting(true);
        try {
            const response = await axios.post(`${base}/detect`, { address }, { headers: headers() });
            if (detectRef.current.ticket !== ticket) return;
            const reply = response.data || null;
            setFound(reply);
            if (reply && !reply.server) {
                // Почту по адресу не узнали — нужен сервер; имя подсказываем.
                setManual((prev) => {
                    if (!prev) autoManualRef.current = true;
                    return true;
                });
                setForm((prev) => (prev.host ? prev : { ...prev, host: reply.guess || '' }));
            } else if (reply?.server && autoManualRef.current) {
                autoManualRef.current = false;
                setManual(false);
                setForm((prev) => ({ ...prev, host: '', port: '465' }));
            }
        } catch {
            if (detectRef.current.ticket === ticket) detectRef.current.address = '';
        } finally {
            if (detectRef.current.ticket === ticket) setDetecting(false);
        }
    }, [base, headers]);

    // Пока вводят адрес — узнаём почту чуть погодя, без запроса на каждую букву.
    useEffect(() => {
        if (!editing || !form.address) return undefined;
        const timer = setTimeout(() => detect(form.address), DETECT_DELAY_MS);
        return () => clearTimeout(timer);
    }, [detect, editing, form.address]);

    const set = (key, value) => {
        setForm((prev) => ({ ...prev, [key]: value }));
        setIssues((prev) => (prev[key] || prev.general ? { ...prev, [key]: '', general: '' } : prev));
    };

    const startEdit = () => {
        setForm({ ...EMPTY, address: box?.address || '', host: box?.manual ? box.host : '', port: String(box?.port || 465) });
        setManual(Boolean(box?.manual));
        autoManualRef.current = false;
        setFound(box && !box.manual && box.label
            ? { address: box.address, server: { label: box.label, host: box.host, port: box.port, help_url: box.help_url } }
            : null);
        detectRef.current = { ticket: detectRef.current.ticket + 1, address: box?.address || '' };
        setIssues({});
        setShowPassword(false);
        setEditing(true);
    };

    const cancelEdit = () => {
        setEditing(false);
        setIssues({});
        setForm(EMPTY);
    };

    const keepPassword = Boolean(box) && oneAddress(form.address).address === box.address;
    const dirty = editing && Boolean(form.password || (form.address.trim() && form.address.trim() !== (box?.address || ''))
        || (manual && box?.manual && (form.host.trim() !== box.host || String(form.port) !== String(box.port))));

    const reveal = (field, event) => requestAnimationFrame(() => revealAnchor(
        document.querySelector(`[data-issue-anchor="mailbox-${field}"]`), { focus: fromKeyboard(event) }));

    const connect = useCallback(async (event) => {
        const problems = {};
        const { address, error } = oneAddress(form.address);
        if (error) problems.address = error;
        if (!form.password.trim() && !keepPassword) problems.password = 'Введите пароль приложения';
        if (manual && !form.host.trim()) problems.host = 'Укажите сервер исходящей почты (SMTP)';
        const first = ANCHORS.find((key) => problems[key]);
        if (first) {
            setIssues(problems);
            reveal(first, event);
            return false;
        }
        setBusy('connect');
        setIssues({});
        try {
            const response = await axios.post(base, {
                address, password: form.password, host: manual ? form.host.trim() : '', port: manual ? form.port : '',
            }, { headers: headers() });
            ticketRef.current += 1;
            setBox(response.data?.mailbox || null);
            setEditing(false);
            setForm(EMPTY);
            setConfirmRemove(false);
            toastRef.current?.(`Почта подключена: пробное письмо ушло на ${address}`, 'success');
            onChanged?.();
            return true;
        } catch (err) {
            const data = err?.response?.data || {};
            if (ANCHORS.includes(data.field)) {
                setIssues({ [data.field]: data.error, helpUrl: data.help_url || '' });
                if (data.field === 'host' || data.field === 'port') setManual(true);
                reveal(data.field, event);
            } else {
                setIssues({ general: errorText(err, 'Не удалось подключить почту') });
            }
            return false;
        } finally {
            setBusy(null);
        }
    }, [base, form, headers, keepPassword, manual, onChanged]);

    // Окну компании: «Сохранить» с набранной, но не подключённой почтой подключает и её.
    useEffect(() => {
        if (!controlRef) return undefined;
        controlRef.current = { dirty: () => dirty, connect };
        return () => { controlRef.current = null; };
    }, [connect, controlRef, dirty]);

    const check = async () => {
        setBusy('check');
        try {
            const response = await axios.post(`${base}/check`, {}, { headers: headers() });
            ticketRef.current += 1;
            setBox(response.data?.mailbox || null);
            toastRef.current?.(`Пробное письмо ушло на ${box?.address} — почта работает`, 'success');
        } catch (err) {
            toastRef.current?.(errorText(err, 'Почта не прошла проверку'), 'error');
            refresh();
        } finally {
            setBusy(null);
            onChanged?.();
        }
    };

    /* Ответ Google: страница API сообщает итог окну портала. */
    useEffect(() => {
        if (busy !== 'google') return undefined;
        const origin = apiOrigin(apiBaseUrl);
        const onMessage = (event) => {
            if (event.origin !== origin || event.data?.type !== GOOGLE_NOTE || event.data.entity !== entityId) return;
            setBusy(null);
            ticketRef.current += 1;
            refresh();
            onChanged?.();
            if (event.data.ok) {
                setEditing(false);
                setForm(EMPTY);
                toastRef.current?.('Почта подключена через Google — пробное письмо во входящих', 'success');
            } else {
                toastRef.current?.(`${event.data.title}: ${event.data.text}`, 'error');
            }
        };
        // Окошко закрыли или Google открылся вкладкой (телефон) — почту просто перечитываем.
        const onBack = () => { if (document.visibilityState === 'visible') refresh(); };
        const timer = setInterval(() => {
            if (popupRef.current && popupRef.current.closed) {
                popupRef.current = null;
                setBusy(null);
                refresh();
                onChanged?.();
            }
        }, 800);
        window.addEventListener('message', onMessage);
        document.addEventListener('visibilitychange', onBack);
        return () => {
            clearInterval(timer);
            window.removeEventListener('message', onMessage);
            document.removeEventListener('visibilitychange', onBack);
        };
    }, [apiBaseUrl, busy, entityId, onChanged, refresh]);

    // Почта подключилась (ответ Google или перечитали) — форма больше не нужна.
    useEffect(() => {
        if (box && busy === null && editing && box.method === 'google' && !form.address && !form.password) setEditing(false);
    }, [box, busy, editing, form.address, form.password]);

    const startGoogle = async () => {
        setBusy('google');
        setIssues({});
        try {
            const response = await axios.post(`${base}/google`, {
                address: oneAddress(form.address).address || box?.address || '',
                return_to: typeof window === 'undefined' ? '' : window.location.href,
            }, { headers: headers() });
            const url = response.data?.url;
            if (!url) throw new Error('no url');
            const popup = window.open(url, 'otp-google-mailbox', 'popup=yes,width=520,height=680');
            if (popup) {
                popupRef.current = popup;
                popup.focus?.();
            } else {
                // Окошки запрещены (телефон, настройки браузера) — Google в этой же вкладке.
                window.location.assign(url);
            }
        } catch (err) {
            setBusy(null);
            toastRef.current?.(errorText(err, 'Не удалось открыть вход через Google'), 'error');
        }
    };

    const remove = async () => {
        setBusy('remove');
        try {
            await axios.delete(base, { headers: headers() });
            ticketRef.current += 1;
            setBox(null);
            setConfirmRemove(false);
            setForm(EMPTY);
            setFound(null);
            setManual(false);
            setEditing(true);
            toastRef.current?.('Почта отключена — письма компании будут ждать новую', 'success');
            onChanged?.();
        } catch (err) {
            toastRef.current?.(errorText(err, 'Не удалось отключить почту'), 'error');
        } finally {
            setBusy(null);
        }
    };

    if (!entityId) return null;

    if (!editing && box) {
        const problem = box.problem;
        return (
            <section className="space-y-2" data-company-mailbox={problem ? 'problem' : 'ready'}>
                <Label />
                <div className={`${iosCard} overflow-hidden`}>
                    <div className="flex items-start gap-3 px-3.5 py-3">
                        <span className={`mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-full text-white ${problem ? 'bg-gradient-to-b from-rose-400 to-rose-500' : 'bg-gradient-to-b from-sky-400 to-blue-500'}`} aria-hidden="true">
                            <AtSign size={16} />
                        </span>
                        {/* basis-0: оболочка телефона переносит ряды с gap — без него текст уезжал бы под кружок. */}
                        <div className="min-w-0 grow basis-0">
                            <div className="flex items-center justify-between gap-2">
                                <span className="min-w-0 truncate text-[14px] font-semibold text-slate-900" data-mailbox-address>{box.address}</span>
                                <TonePill tone={problem ? 'overdue' : 'done'} className="shrink-0">{problem ? 'не пускает' : 'подключена'}</TonePill>
                            </div>
                            <div className="truncate text-[12.5px] text-slate-500">{mailboxLine(box)}</div>
                            {problem && (
                                <div className="mt-1.5 break-words text-[12.5px] leading-snug text-rose-700" data-mailbox-problem>
                                    {problem}. {box.method === 'google' ? 'Письма компании ждут — войдите через Google заново.' : 'Письма компании ждут — введите пароль заново.'}
                                </div>
                            )}
                        </div>
                    </div>
                    <div className="flex flex-wrap items-center justify-end gap-2 border-t border-slate-100 px-3 py-2.5">
                        {confirmRemove ? (
                            <>
                                <span className="mr-auto text-[12.5px] leading-snug text-slate-600">Отключить почту? Письма компании будут ждать новую.</span>
                                <button type="button" className={`${iosBtnSecondary} !text-rose-700`} disabled={Boolean(busy)} onClick={remove}>
                                    {busy === 'remove' && <Loader2 size={13} className="animate-spin" />} Отключить
                                </button>
                                <button type="button" className={iosBtnSecondary} disabled={Boolean(busy)} onClick={() => setConfirmRemove(false)}>Нет</button>
                            </>
                        ) : (
                            <>
                                <button type="button" className={`${iosBtnGhost} mr-auto !text-rose-600`} disabled={Boolean(busy)} onClick={() => setConfirmRemove(true)}>Отключить</button>
                                {box.method === 'google' ? (
                                    <button type="button" className={iosBtnSecondary} disabled={Boolean(busy)} onClick={startGoogle}>
                                        {busy === 'google' && <Loader2 size={13} className="animate-spin" />} Войти заново
                                    </button>
                                ) : (
                                    <button type="button" className={iosBtnSecondary} disabled={Boolean(busy)} onClick={startEdit}>{problem ? 'Ввести пароль' : 'Изменить'}</button>
                                )}
                                <button type="button" className={iosBtnSecondary} disabled={Boolean(busy)} onClick={check}>
                                    {busy === 'check' && <Loader2 size={13} className="animate-spin" />} Проверить
                                </button>
                            </>
                        )}
                    </div>
                </div>
            </section>
        );
    }

    const server = found?.server && found.address === oneAddress(form.address).address ? found.server : null;
    const unknown = manual && found && !found.server && found.address === oneAddress(form.address).address;
    const serverNote = detecting || (server && !manual) || unknown || (manual && server);
    const helpUrl = issues.helpUrl || (!manual && server?.help_url) || '';
    return (
        <section className="space-y-2" data-company-mailbox="edit">
            <Label />
            <div className={`${iosCard} space-y-3 px-3.5 py-3.5`}>
                {!box && (
                    <p className="px-1 text-[12.5px] leading-snug text-slate-600">
                        Письма поставщикам о смене реквизитов этой компании уходят с её почты. Пока почта не подключена, письма ждут.
                    </p>
                )}
                {googleOn && (
                    <div className="space-y-3" data-mailbox-google>
                        <button type="button" onClick={startGoogle} disabled={Boolean(busy)} data-mailbox-google-start
                            className="flex w-full items-center justify-center gap-2.5 rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-[14px] font-medium text-slate-800 shadow-sm transition hover:bg-slate-50 active:scale-[0.99] disabled:opacity-60">
                            {busy === 'google' ? <Loader2 size={16} className="animate-spin" /> : <GoogleMark />}
                            {busy === 'google' ? 'Ждём ответа Google…' : 'Войти через Google'}
                        </button>
                        <p className="-mt-1 px-1 text-[12px] leading-snug text-slate-500">
                            Для Gmail: выберите аккаунт и разрешите отправку писем — пароль не нужен.
                        </p>
                        <div className="flex items-center gap-3 px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400" aria-hidden="true">
                            <span className="h-px grow basis-0 bg-slate-200" />или по паролю приложения<span className="h-px grow basis-0 bg-slate-200" />
                        </div>
                    </div>
                )}
                <Field label="Адрес почты" required optionalMark={false} anchor="mailbox-address" issue={issues.address}>
                    <FormInput type="email" inputMode="email" name="mailbox-address" autoComplete="off" spellCheck={false}
                        placeholder="buh@company.kz" value={form.address} onChange={(event) => set('address', event.target.value)}
                        onBlur={() => detect(form.address)} />
                </Field>
                {/* Какая это почта — строкой под адресом; пока сказать нечего, строки нет. */}
                {serverNote && (
                    <div className="-mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 px-1 text-[12.5px] leading-snug text-slate-500" data-mailbox-server>
                        {detecting && <><Loader2 size={12} className="animate-spin" /> Определяю почту…</>}
                        {!detecting && server && !manual && (
                            <>
                                <span>{serverLine(server)}</span>
                                <button type="button" className="font-medium text-blue-600 hover:underline" onClick={() => { autoManualRef.current = false; setManual(true); set('host', server.host); set('port', String(server.port)); }}>
                                    Другой сервер
                                </button>
                            </>
                        )}
                        {!detecting && unknown && (
                            <span>Почту по адресу не узнали — укажите сервер исходящей почты: он есть в настройках почты или у хостинга.</span>
                        )}
                        {!detecting && manual && server && (
                            <button type="button" className="font-medium text-blue-600 hover:underline" onClick={() => setManual(false)}>
                                Сервер по адресу: {server.label}
                            </button>
                        )}
                    </div>
                )}
                {manual && (
                    <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_120px]">
                        <Field label="Сервер исходящей почты (SMTP)" required optionalMark={false} anchor="mailbox-host" issue={issues.host}>
                            <FormInput name="mailbox-host" autoComplete="off" spellCheck={false} placeholder="smtp.company.kz"
                                value={form.host} onChange={(event) => set('host', event.target.value)} />
                        </Field>
                        <Field label="Порт" optionalMark={false} anchor="mailbox-port" issue={issues.port} hint="465 — SSL, 587 — STARTTLS.">
                            <FormInput name="mailbox-port" inputMode="numeric" autoComplete="off" value={form.port}
                                onChange={(event) => set('port', event.target.value.replace(/\D/g, '').slice(0, 5))} />
                        </Field>
                    </div>
                )}
                <Field label="Пароль приложения" required={!keepPassword} optionalMark={false} anchor="mailbox-password"
                    issue={issues.password} hint={server?.password_hint || HINTS.companyMailbox}>
                    <div className="relative">
                        <FormInput type={showPassword ? 'text' : 'password'} name="mailbox-app-password" autoComplete="new-password"
                            spellCheck={false} className="pr-11" value={form.password}
                            placeholder={keepPassword ? 'Прежний — новый, если меняли' : ''}
                            onChange={(event) => set('password', event.target.value)} />
                        <button type="button" aria-label={showPassword ? 'Скрыть пароль' : 'Показать пароль'}
                            onClick={() => setShowPassword((value) => !value)}
                            className="absolute inset-y-0 right-1 my-auto grid h-8 w-8 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-200/60 hover:text-slate-600">
                            {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                        </button>
                    </div>
                </Field>
                {helpUrl && (
                    <a href={helpUrl} target="_blank" rel="noopener noreferrer" className="-mt-1.5 inline-flex items-center gap-1 px-1 text-[12.5px] font-medium text-blue-600 hover:underline" data-mailbox-help>
                        Как получить пароль приложения <ExternalLink size={12} aria-hidden="true" />
                    </a>
                )}
                <ErrorBox text={issues.general} />
                <div className="flex flex-wrap items-center justify-end gap-2 pt-0.5">
                    <span className="mr-auto min-w-0 grow basis-0 px-1 text-[12px] leading-snug text-slate-500">Проверим: отправим пробное письмо на этот адрес.</span>
                    {box && <button type="button" className={iosBtnSecondary} disabled={Boolean(busy)} onClick={cancelEdit}>Отмена</button>}
                    <button type="button" className={iosBtnPrimary} disabled={Boolean(busy)} onClick={connect} data-mailbox-connect>
                        {busy === 'connect' ? <><Loader2 size={14} className="animate-spin" /> Проверяю…</> : 'Подключить'}
                    </button>
                </div>
            </div>
        </section>
    );
};

export default CompanyMailbox;
