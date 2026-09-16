import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
    AlertCircle, Check, Copy, FileSignature, Hash, Loader2, RotateCcw, X,
} from 'lucide-react';

import CustomSelect from '../ui/CustomSelect';
import { IosDateRangePicker } from '../ui/DateRangePicker';
import {
    APPLE_FONT, iosCard, iosBtnPrimary, iosBtnSecondary, iosBtnGhost,
    IosSegmented, IosBadge, IosPager,
} from '../ui/ios';
import {
    formatTime, formatDayFull, dayKeyOf, shiftDaysBack, todayISO, roleLabel,
} from '../driver_chats/journalMeta';
import { validateIin, iinErrorMessage, normalizeIin, formatIin, IIN_LENGTH } from './iin';
import {
    OUTCOME_ORDER, outcomeLabel, outcomeTone, departmentLabel, limitWarning, hostLabel,
} from './signLinkMeta';

/* Раздел «Ссылка на подписание» (просьба владельца 16.09.2026).
 *
 * Оператор СЗоВ или менеджер фронт-офиса вводит ИИН водителя и получает ссылку
 * на подписание документов через eGov Mobile. Саму ссылку генерирует СЕРВЕР —
 * у гостевого генератора Sapar; его адрес в этот файл не попадает вовсе, и
 * оператор до него не добирается. Каждый запрос ложится в журнал, журнал
 * видят админы и главы отделов раздела.
 *
 * Оформление — как в «Чатах водителей» (решение владельца 16.09.2026): поле
 * посреди экрана с кнопкой внутри, объяснение под ним тремя карточками, после
 * ответа поле уезжает наверх; журнал — карточка отбора, сводка, таблица по
 * дням, пейджер.
 *
 * ЧЕГО ЗДЕСЬ НАМЕРЕННО НЕТ:
 *
 * * Отправки ссылки водителю из портала. Канала «сообщение водителю по ИИН» у
 *   нас нет: Sapar телефона не отдаёт, рассылки Флита адресуются профилю в
 *   парке. Ссылку оператор передаёт сам — копирует или показывает QR с экрана.
 * * Истории запросов у оператора. Ссылка выдаётся один раз и в базе не
 *   хранится: понадобилась снова — генерируется заново, это дешевле, чем
 *   держать у каждого список ссылок на чужие документы.
 * * Проверки «по мере ввода» на сервере. ИИН проверяется у себя (iin.js —
 *   двойник серверного правила), к вендору уходит только верный.
 */

const EMPTY_JOURNAL_FILTERS = { outcome: 'all', userId: 'all', department: 'all', iin: '' };

const JOURNAL_PRESETS = [
    { label: 'Сегодня', range: () => ({ from: todayISO(), to: todayISO() }) },
    { label: '7 дней', range: () => ({ from: shiftDaysBack(todayISO(), 6), to: todayISO() }) },
    { label: '30 дней', range: () => ({ from: shiftDaysBack(todayISO(), 29), to: todayISO() }) },
];

/* Вид чипа дат и поля ИИН — ОДИН В ОДИН с ios-вариантом CustomSelect и с
   отбором журнала «Чатов водителей»: в одной строке отбора не должно стоять
   трёх разных предметов. `[&>span]:flex-1` у чипа обязателен: triggerClassName
   заменяет класс кнопки целиком, и без него подпись не растягивается. */
const FILTER_TRIGGER = 'flex w-full items-center gap-2 rounded-xl bg-white px-3 py-2 '
    + 'text-left text-[12.5px] font-medium text-slate-700 ring-1 ring-slate-200/70 '
    + 'shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition-all hover:bg-slate-50 '
    + 'active:scale-[0.99] focus:outline-none focus:ring-2 focus:ring-blue-500/60 '
    + '[&>span]:flex-1 [&>span]:text-left [&>span]:truncate';

const FILTER_INPUT = 'h-9 w-full rounded-xl bg-white pl-9 pr-8 text-[12.5px] '
    + 'font-medium tabular-nums text-slate-700 ring-1 ring-slate-200/70 '
    + 'shadow-[0_1px_2px_rgba(15,23,42,0.04)] outline-none transition-all '
    + 'placeholder:font-normal placeholder:text-slate-400 focus:ring-2 focus:ring-blue-500/60';

/* Сворачивание заголовка и объяснения при появлении ответа — та же кривая,
   что у поиска в «Чатах водителей». */
const COLLAPSE = 'overflow-hidden transition-all duration-500 ease-[cubic-bezier(0.16,1,0.3,1)] motion-reduce:transition-none';

const STEPS = [
    { title: 'Введите ИИН', text: 'Двенадцать цифр из удостоверения водителя. Опечатку поле заметит само — по контрольной цифре.' },
    { title: 'Получите ссылку', text: 'Сервер сформирует ссылку на подписание документов через eGov Mobile. Если подписывать нечего — так и скажет.' },
    { title: 'Передайте водителю', text: 'Скопируйте ссылку в сообщение или дайте отсканировать QR-код с экрана. Открывается она на телефоне водителя.' },
];

const SignLinksView = ({ apiBaseUrl, withAccessTokenHeader, showToast }) => {
    const headers = useCallback(
        () => (withAccessTokenHeader ? withAccessTokenHeader() : {}),
        [withAccessTokenHeader],
    );

    /* showToast приходит новой функцией на каждый рендер App — известная ловушка
       портала. Держим её в ref, чтобы она не попала в зависимости эффектов. */
    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);
    const toast = useCallback((message, kind) => {
        if (toastRef.current) toastRef.current(message, kind);
    }, []);

    const [context, setContext] = useState(null);
    const [contextError, setContextError] = useState('');
    const [tab, setTab] = useState('generate');

    // ── Контекст раздела: права и дневной остаток ───────────────────────────
    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const response = await fetch(`${apiBaseUrl}/api/sign_links/ping`, {
                    headers: headers(), credentials: 'include',
                });
                const data = await response.json().catch(() => ({}));
                if (cancelled) return;
                if (!response.ok) {
                    setContextError(data.error || 'Раздел недоступен');
                    return;
                }
                setContext(data);
            } catch {
                if (!cancelled) setContextError('Не удалось открыть раздел');
            }
        })();
        return () => { cancelled = true; };
    }, [apiBaseUrl, headers]);

    const canViewJournal = Boolean(context?.capabilities?.can_view_journal);
    const schemaReady = context ? context.schema_ready !== false : true;

    // ── Запрос ссылки ────────────────────────────────────────────────────────
    const [iinInput, setIinInput] = useState('');
    const [touched, setTouched] = useState(false);
    const [busy, setBusy] = useState(false);
    const [result, setResult] = useState(null);
    const [requestError, setRequestError] = useState('');
    const inputRef = useRef(null);

    useEffect(() => { inputRef.current?.focus(); }, []);

    const check = useMemo(() => validateIin(iinInput), [iinInput]);
    const digits = normalizeIin(iinInput);
    /* В поле попадают ТОЛЬКО цифры и не больше двенадцати (замечание владельца
       16.09.2026: «в поле можно внести даже буквы»). Вставка «900101 300 007»
       с пробелами при этом работает — лишнее отбрасывается на входе. */
    const acceptIin = useCallback((raw) => {
        const next = String(raw ?? '').replace(/\D/g, '').slice(0, IIN_LENGTH);
        setIinInput(next);
        if (!next) setTouched(false);
    }, []);
    /* Под полем — одна строка. Пока набирают, серый счётчик «7 из 12 цифр»;
       когда цифр двенадцать и они не сходятся — розовая ошибка сразу; если
       нажали кнопку с недобором — розовая «должно быть 12 цифр». */
    const incomplete = digits.length > 0 && digits.length < IIN_LENGTH;
    const fieldError = check.error && check.error !== 'empty'
        && (touched || digits.length >= IIN_LENGTH)
        ? iinErrorMessage(check.error)
        : '';
    const fieldHint = !fieldError && incomplete ? `${digits.length} из ${IIN_LENGTH} цифр` : '';

    const submit = useCallback(async () => {
        setTouched(true);
        if (check.error) {
            inputRef.current?.focus();
            return;
        }
        setBusy(true);
        setRequestError('');
        try {
            const response = await fetch(`${apiBaseUrl}/api/sign_links/generate`, {
                method: 'POST',
                headers: { ...headers(), 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ iin: check.iin }),
            });
            const data = await response.json().catch(() => ({}));
            // Остаток на сегодня сервер присылает с каждым ответом — обновляем,
            // не спрашивая /ping второй раз.
            if (data.limits) {
                setContext((prev) => (prev ? { ...prev, limits: data.limits } : prev));
            }
            if (!response.ok) {
                if (data.code === 'SIGN_LINK_UNAVAILABLE') {
                    setResult({ outcome: 'unavailable', iin: check.iin, message: data.error });
                    return;
                }
                setRequestError(data.error || 'Не удалось получить ссылку');
                return;
            }
            setResult(data);
        } catch {
            setRequestError('Сеть недоступна. Попробуйте ещё раз');
        } finally {
            setBusy(false);
        }
    }, [apiBaseUrl, check, headers]);

    const reset = useCallback(() => {
        setResult(null);
        setRequestError('');
        setIinInput('');
        setTouched(false);
        // Фокус — после перерисовки, когда поле снова стало большим.
        setTimeout(() => inputRef.current?.focus(), 0);
    }, []);

    const compact = Boolean(result);
    const warning = limitWarning(context?.limits);

    return (
        <div className="space-y-4" style={{ fontFamily: APPLE_FONT }}>
            {canViewJournal && (
                <div className="flex justify-center">
                    <IosSegmented
                        value={tab}
                        onChange={setTab}
                        ariaLabel="Разделы"
                        options={[
                            { value: 'generate', label: 'Ссылка' },
                            { value: 'journal', label: 'Журнал' },
                        ]}
                    />
                </div>
            )}

            {contextError && (
                <div className={`${iosCard} mx-auto flex max-w-[640px] items-start gap-2.5 px-4 py-3 text-sm text-rose-600`}>
                    <AlertCircle size={16} className="mt-0.5 shrink-0" />
                    <span>{contextError}</span>
                </div>
            )}

            {!contextError && !schemaReady && (
                <div className={`${iosCard} mx-auto flex max-w-[640px] items-start gap-2.5 px-4 py-3 text-sm text-amber-700`}>
                    <AlertCircle size={16} className="mt-0.5 shrink-0" />
                    <span>Раздел ещё разворачивается на сервере — журнал заработает после перезапуска.</span>
                </div>
            )}

            {tab === 'journal' && canViewJournal ? (
                <JournalPanel
                    apiBaseUrl={apiBaseUrl}
                    headers={headers}
                    scope={context?.capabilities?.journal_scope ?? null}
                    departments={context?.departments || []}
                />
            ) : (
                <div className={`transition-[padding] duration-500 ease-[cubic-bezier(0.16,1,0.3,1)] motion-reduce:transition-none ${
                    compact ? 'pt-0' : 'pt-[3vh] sm:pt-[6vh]'}`}>
                    <GenerateStage
                        compact={compact}
                        value={iinInput}
                        onChange={acceptIin}
                        onSubmit={submit}
                        busy={busy}
                        disabled={Boolean(contextError)}
                        inputRef={inputRef}
                        fieldError={fieldError}
                        fieldHint={fieldHint}
                        complete={digits.length === IIN_LENGTH}
                        warning={warning}
                    />

                    {requestError && (
                        <div className={`${iosCard} mx-auto mt-4 flex max-w-[640px] items-start gap-2.5 px-4 py-3 text-sm text-rose-600`}>
                            <AlertCircle size={16} className="mt-0.5 shrink-0" />
                            <span>{requestError}</span>
                        </div>
                    )}

                    {result && (
                        <ResultCard result={result} onReset={reset} toast={toast} />
                    )}
                </div>
            )}
        </div>
    );
};

/* Поле ИИН посреди экрана с кнопкой внутри — как поиск в «Чатах водителей».
   Заголовок над полем и объяснение под ним сворачиваются по высоте, а не
   исчезают: сворачивание читается как «поле переехало наверх», исчезновение —
   как «страница перескочила». */
const GenerateStage = ({ compact, value, onChange, onSubmit, busy, disabled, inputRef,
                         fieldError, fieldHint, complete, warning }) => {
    const [focused, setFocused] = useState(false);
    // Кнопка оживает только на двенадцати цифрах: с недобором ей нечего слать.
    const canSubmit = !busy && !disabled && complete;

    return (
        <div>
            <div aria-hidden={compact} className={`${COLLAPSE} ${
                compact ? 'max-h-0 -translate-y-2 opacity-0' : 'max-h-24 translate-y-0 opacity-100'}`}>
                <h2 className="text-center text-[22px] font-semibold tracking-tight text-slate-900">
                    Ссылка на подписание
                </h2>
            </div>

            <div
                className={`mx-auto w-full transition-[max-width] duration-300 ease-out motion-reduce:transition-none ${
                    compact
                        ? (focused ? 'max-w-[560px]' : 'max-w-[480px]')
                        : (focused ? 'max-w-[680px]' : 'max-w-[600px]')} ${compact ? 'mt-0' : 'mt-3'}`}
            >
                <div
                    className={`relative flex items-center rounded-2xl bg-white transition-all duration-300 ease-out motion-reduce:transition-none ${
                        fieldError
                            ? 'ring-2 ring-rose-400/70 shadow-[0_1px_2px_rgba(15,23,42,0.04)]'
                            : focused
                                ? 'shadow-[0_12px_34px_-14px_rgba(15,23,42,0.35)] ring-2 ring-blue-500/70'
                                : 'shadow-[0_1px_2px_rgba(15,23,42,0.04)] ring-1 ring-slate-200/70'} ${
                        compact ? (focused ? 'h-12' : 'h-11') : (focused ? 'h-[58px]' : 'h-[52px]')}`}
                >
                    <Hash
                        size={17}
                        className={`pointer-events-none absolute left-4 transition-colors ${
                            fieldError ? 'text-rose-400' : focused ? 'text-blue-500' : 'text-slate-400'}`}
                    />
                    <input
                        ref={inputRef}
                        value={value}
                        onChange={(event) => onChange(event.target.value)}
                        onKeyDown={(event) => { if (event.key === 'Enter' && canSubmit) onSubmit(); }}
                        onFocus={() => setFocused(true)}
                        onBlur={() => setFocused(false)}
                        inputMode="numeric"
                        pattern="[0-9]*"
                        autoComplete="off"
                        maxLength={IIN_LENGTH}
                        placeholder="ИИН водителя"
                        aria-label="ИИН водителя"
                        aria-invalid={Boolean(fieldError)}
                        className={`h-full w-full rounded-2xl bg-transparent pl-11 pr-[164px] tabular-nums tracking-[0.04em] text-slate-900 outline-none placeholder:tracking-normal placeholder:text-slate-400 ${
                            compact ? 'text-[14.5px]' : 'text-[15.5px]'}`}
                    />
                    <button
                        type="button"
                        onClick={onSubmit}
                        disabled={!canSubmit}
                        className={`${iosBtnPrimary} absolute right-1.5 top-1/2 h-9 -translate-y-1/2 px-3.5 py-0 disabled:opacity-40`}
                    >
                        {busy ? <Loader2 size={15} className="animate-spin" /> : <FileSignature size={15} />}
                        {busy ? 'Запрашиваем…' : 'Получить ссылку'}
                    </button>
                </div>

                {/* Под полем — одна строка: ошибка ИИН, счётчик цифр во время
                    набора или предупреждение о пределе. На пустом поле в обычный
                    день здесь пусто, и строка не занимает места. */}
                {(fieldError || fieldHint || warning) && (
                    <div className={`mt-2 text-center text-[12px] tabular-nums ${
                        fieldError ? 'text-rose-600' : fieldHint ? 'text-slate-400' : 'text-amber-600'}`}>
                        {fieldError || fieldHint || warning}
                    </div>
                )}
            </div>

            <div aria-hidden={compact} className={`${COLLAPSE} ${
                compact ? 'max-h-0 -translate-y-2 opacity-0' : 'max-h-[420px] translate-y-0 opacity-100'}`}>
                <ol className="mx-auto mt-6 grid max-w-3xl gap-2 text-left sm:grid-cols-3">
                    {STEPS.map((step, index) => (
                        <li key={step.title} className="rounded-2xl bg-slate-500/[0.045] px-3.5 py-3">
                            <div className="flex items-center gap-2">
                                <span className="grid h-[18px] w-[18px] place-items-center rounded-full bg-slate-900/85 text-[10.5px] font-semibold text-white">
                                    {index + 1}
                                </span>
                                <span className="text-[13px] font-semibold text-slate-800">{step.title}</span>
                            </div>
                            <p className="mt-1.5 text-[12.5px] leading-snug text-slate-500">{step.text}</p>
                        </li>
                    ))}
                </ol>
            </div>
        </div>
    );
};

/* Ответ сервера — одна карточка на четыре исхода. Цвет только у двух: зелёный
   у выданной ссылки, розовый у отказа и сбоя; «документов нет» — обычный ответ
   водителю, его не красим. */
const ResultCard = ({ result, onReset, toast }) => {
    const outcome = result.outcome;
    const iin = formatIin(result.iin);

    if (outcome === 'link') {
        return <LinkCard link={result.link} iin={iin} onReset={onReset} toast={toast} />;
    }

    const failed = outcome === 'rejected' || outcome === 'unavailable';
    const title = outcome === 'no_documents'
        ? 'Документов на подписание нет'
        : outcome === 'rejected'
            ? 'Сервис подписания отказал'
            : 'Сервис подписания не отвечает';
    const body = result.message
        || (outcome === 'unavailable' ? 'Попробуйте ещё раз через минуту.' : '');

    return (
        <div className={`${iosCard} mx-auto mt-4 max-w-[640px] animate-card-open px-6 py-7 text-center`}>
            <div className={`mx-auto mb-3 grid h-11 w-11 place-items-center rounded-2xl ${
                failed ? 'bg-rose-50 text-rose-500 ring-1 ring-rose-100' : 'bg-slate-100 text-slate-400'}`}>
                {failed ? <AlertCircle size={22} /> : <FileSignature size={22} />}
            </div>
            <div className={`text-[15px] font-semibold ${failed ? 'text-rose-600' : 'text-slate-800'}`}>
                {title}
            </div>
            {body && <div className="mt-1 text-[13px] text-slate-500">{body}</div>}
            <div className="mt-1 text-[12px] tabular-nums text-slate-400">ИИН {iin}</div>
            <button type="button" onClick={onReset} className={`${iosBtnSecondary} mt-5`}>
                <RotateCcw size={15} />
                Новый запрос
            </button>
        </div>
    );
};

const LinkCard = ({ link, iin, onReset, toast }) => {
    const [qr, setQr] = useState('');
    const [copied, setCopied] = useState(false);

    /* Код рисуем сами, у себя: та же библиотека и те же параметры, что у QR
       доступа в App.jsx. Грузится по требованию — открывает эту карточку не
       каждый, а в общий бандл она весит полсотни килобайт. */
    useEffect(() => {
        let cancelled = false;
        setQr('');
        if (!link) return undefined;
        import('qrcode')
            .then((mod) => (mod.default || mod).toDataURL(link, {
                errorCorrectionLevel: 'M',
                margin: 2,
                width: 512,
            }))
            .then((url) => { if (!cancelled) setQr(url); })
            .catch(() => { if (!cancelled) setQr(''); });
        return () => { cancelled = true; };
    }, [link]);

    const copy = useCallback(async () => {
        try {
            if (navigator.clipboard?.writeText) {
                await navigator.clipboard.writeText(link);
            } else {
                // Старые WebView без clipboard API: временное поле + execCommand.
                const area = document.createElement('textarea');
                area.value = link;
                area.setAttribute('readonly', '');
                area.style.position = 'fixed';
                area.style.opacity = '0';
                document.body.appendChild(area);
                area.select();
                document.execCommand('copy');
                area.remove();
            }
            setCopied(true);
            toast('Ссылка скопирована', 'success');
            setTimeout(() => setCopied(false), 2000);
        } catch {
            toast('Не удалось скопировать — выделите ссылку и скопируйте вручную', 'error');
        }
    }, [link, toast]);

    return (
        <div className={`${iosCard} mx-auto mt-4 max-w-[640px] animate-card-open p-5 sm:p-6`}>
            {/* items-center, а не items-start: мобильная оболочка складывает
                `flex.items-start` с растягивающимся блоком в колонку — это
                правило для каркасов экранов (mobile-shell.css), и шапка
                карточки под него попадать не должна. */}
            <div className="flex items-center gap-3">
                <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-emerald-50 text-emerald-600 ring-1 ring-emerald-100">
                    <Check size={18} />
                </div>
                <div className="min-w-0 flex-1">
                    <div className="text-[15px] font-semibold text-slate-900">Ссылка готова</div>
                    <div className="mt-0.5 text-[12.5px] tabular-nums text-slate-500">ИИН {iin}</div>
                </div>
                <button type="button" onClick={onReset} className={`${iosBtnGhost} -mr-2 shrink-0`}>
                    <RotateCcw size={14} />
                    Новый запрос
                </button>
            </div>

            <div className="mt-4 grid gap-4 sm:grid-cols-[168px_minmax(0,1fr)] sm:items-start">
                {/* QR — для водителя, который стоит рядом: он сканирует код с экрана
                    менеджера и открывает ссылку у себя. Белая подложка с кантом —
                    чтобы код читался и на тёмной теме. */}
                <div className="mx-auto grid h-[168px] w-[168px] place-items-center rounded-2xl bg-white ring-1 ring-slate-200/70">
                    {qr ? (
                        <img src={qr} alt="QR-код со ссылкой на подписание" className="h-[160px] w-[160px] rounded-xl" />
                    ) : (
                        <Loader2 size={18} className="animate-spin text-slate-300" />
                    )}
                </div>

                <div className="min-w-0 space-y-3">
                    <div className="rounded-xl bg-slate-100 px-3.5 py-2.5">
                        <div className="break-all font-mono text-[12.5px] leading-snug text-slate-800" data-testid="sign-link">
                            {link}
                        </div>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                        <button type="button" onClick={copy} className={iosBtnPrimary}>
                            {copied ? <Check size={15} /> : <Copy size={15} />}
                            {copied ? 'Скопировано' : 'Скопировать ссылку'}
                        </button>
                    </div>
                    <p className="text-[12.5px] leading-snug text-slate-500">
                        Отправьте ссылку водителю или дайте отсканировать код с экрана.
                        Открывается на телефоне водителя в eGov Mobile.
                    </p>
                </div>
            </div>
        </div>
    );
};

/* ── Журнал ─────────────────────────────────────────────────────────────── */

const JournalPanel = ({ apiBaseUrl, headers, scope, departments }) => {
    const [filters, setFilters] = useState(() => ({
        ...EMPTY_JOURNAL_FILTERS,
        from: shiftDaysBack(todayISO(), 6),
        to: todayISO(),
    }));
    /* Черновик ИИН живёт отдельно от отбора: набранное ещё не запрос.
       Применяется по Enter и по уходу из поля. */
    const [iinDraft, setIinDraft] = useState('');
    const [data, setData] = useState({ items: [], total: 0, summary: {}, people: [] });
    const [page, setPage] = useState(1);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    // Фильтр по отделу — только у того, кто видит оба: у главы граница уже
    // стоит на сервере, и выпадашка из одного пункта была бы шумом.
    const showDepartments = scope === null && departments.length > 1;

    const params = useMemo(() => {
        const search = new URLSearchParams();
        if (filters.from) search.set('date_from', filters.from);
        if (filters.to) search.set('date_to', filters.to);
        if (filters.outcome !== 'all') search.set('outcomes', filters.outcome);
        if (filters.userId !== 'all') search.set('user_id', String(filters.userId));
        if (filters.department !== 'all') search.set('department', filters.department);
        if (filters.iin.trim()) search.set('iin', filters.iin.trim());
        return search;
    }, [filters]);

    useEffect(() => { setPage(1); }, [params]);

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        const search = new URLSearchParams(params);
        search.set('page', String(page));
        (async () => {
            try {
                const response = await fetch(
                    `${apiBaseUrl}/api/sign_links/journal?${search.toString()}`,
                    { headers: headers(), credentials: 'include' });
                const payload = await response.json().catch(() => ({}));
                if (cancelled) return;
                if (!response.ok) {
                    setError(payload.error || 'Не удалось загрузить журнал');
                    return;
                }
                setError('');
                setData(payload);
            } catch {
                if (!cancelled) setError('Сеть недоступна');
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, [apiBaseUrl, headers, params, page]);

    const applyIin = useCallback(() => {
        const next = normalizeIin(iinDraft);
        setFilters((prev) => (prev.iin === next ? prev : { ...prev, iin: next }));
    }, [iinDraft]);

    const resetFilters = useCallback(() => {
        setIinDraft('');
        setFilters((prev) => ({ ...prev, ...EMPTY_JOURNAL_FILTERS }));
    }, []);

    const summary = data.summary || {};
    const pageSize = data.page_size || 50;
    const total = data.total || 0;
    const pageCount = Math.max(1, Math.ceil(total / pageSize));

    const outcomeOptions = useMemo(() => ([
        { value: 'all', label: 'Все исходы' },
        ...OUTCOME_ORDER.map((code) => ({ value: code, label: outcomeLabel(code) })),
    ]), []);

    const peopleOptions = useMemo(() => ([
        { value: 'all', label: 'Все сотрудники' },
        ...(data.people || []).map((person) => ({
            value: String(person.user_id),
            label: person.name || `№${person.user_id}`,
        })),
    ]), [data.people]);

    const departmentOptions = useMemo(() => ([
        { value: 'all', label: 'Все отделы' },
        ...departments.map((code) => ({ value: code, label: departmentLabel(code) })),
    ]), [departments]);

    /* Строки, разложенные по дням. Группируем последовательно: сервер уже
       отдал их по убыванию времени, и группа меняется ровно там, где день. */
    const groups = useMemo(() => {
        const out = [];
        (data.items || []).forEach((item) => {
            const key = dayKeyOf(item.created_at);
            const last = out[out.length - 1];
            if (last && last.key === key) last.items.push(item);
            else out.push({ key, label: formatDayFull(item.created_at), items: [item] });
        });
        return out;
    }, [data.items]);

    const filtered = filters.outcome !== 'all' || filters.userId !== 'all'
        || filters.department !== 'all' || Boolean(filters.iin.trim());

    return (
        <div className="space-y-3">
            {/* ── Отбор ─────────────────────────────────────────────────── */}
            <div className={`${iosCard} flex flex-wrap items-center gap-2 px-3 py-2.5`}>
                <div className="w-full sm:w-[188px]">
                    <IosDateRangePicker
                        from={filters.from}
                        to={filters.to}
                        max={todayISO()}
                        presets={JOURNAL_PRESETS}
                        triggerClassName={FILTER_TRIGGER}
                        onChange={(next) => setFilters((prev) => ({
                            ...prev,
                            from: next.from || next.to || prev.from,
                            to: next.to || next.from || prev.to,
                        }))}
                    />
                </div>
                <CustomSelect
                    variant="ios"
                    value={filters.outcome}
                    options={outcomeOptions}
                    ariaLabel="Результат"
                    className="w-full sm:w-[196px]"
                    onChange={(value) => setFilters((prev) => ({ ...prev, outcome: value }))}
                />
                <CustomSelect
                    variant="ios"
                    value={filters.userId}
                    options={peopleOptions}
                    ariaLabel="Сотрудник"
                    searchable={peopleOptions.length > 8}
                    className="w-full sm:w-[200px]"
                    onChange={(value) => setFilters((prev) => ({ ...prev, userId: value }))}
                />
                {showDepartments && (
                    <CustomSelect
                        variant="ios"
                        value={filters.department}
                        options={departmentOptions}
                        ariaLabel="Отдел"
                        className="w-full sm:w-[160px]"
                        onChange={(value) => setFilters((prev) => ({ ...prev, department: value }))}
                    />
                )}
                <div className="relative w-full sm:w-[186px]">
                    <Hash size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                        value={iinDraft}
                        onChange={(event) => setIinDraft(event.target.value)}
                        onKeyDown={(event) => { if (event.key === 'Enter') applyIin(); }}
                        onBlur={applyIin}
                        inputMode="numeric"
                        placeholder="ИИН водителя"
                        aria-label="ИИН водителя"
                        className={FILTER_INPUT}
                    />
                    {Boolean(iinDraft) && (
                        <button
                            type="button"
                            aria-label="Очистить ИИН"
                            onClick={() => { setIinDraft(''); setFilters((prev) => ({ ...prev, iin: '' })); }}
                            className="absolute right-2 top-1/2 grid h-5 w-5 -translate-y-1/2 place-items-center rounded-full text-slate-400 transition hover:bg-slate-200 hover:text-slate-600"
                        >
                            <X size={12} />
                        </button>
                    )}
                </div>
                {filtered && (
                    <button type="button" onClick={resetFilters} className={iosBtnGhost}>
                        Сбросить
                    </button>
                )}
            </div>

            {/* ── Сводка по всей выборке, а не по странице ───────────────── */}
            <div className={`${iosCard} grid grid-cols-2 divide-x divide-y divide-slate-100 sm:grid-cols-4 sm:divide-y-0`}>
                <Stat label="Запросов" value={summary.requests} />
                <Stat label="Ссылок выдано" value={summary.links} />
                <Stat label="Без документов" value={summary.no_documents} />
                <Stat label="Отказов и сбоев" value={summary.failures} />
            </div>

            {error && (
                <div className={`${iosCard} flex items-start gap-2.5 px-4 py-3 text-sm text-rose-600`}>
                    <AlertCircle size={16} className="mt-0.5 shrink-0" /><span>{error}</span>
                </div>
            )}

            <div className={`${iosCard} overflow-hidden`}>
                {loading ? (
                    <div className="flex items-center justify-center gap-2 py-12 text-sm text-slate-400">
                        <Loader2 size={15} className="animate-spin" /> Загрузка журнала…
                    </div>
                ) : !data.items?.length ? (
                    <div className="px-6 py-12 text-center">
                        <div className="text-sm font-medium text-slate-700">
                            За выбранный период запросов не было
                        </div>
                        <div className="mt-1 text-[13px] text-slate-500">
                            {filtered
                                ? 'Попробуйте расширить период или снять фильтры.'
                                : 'Ссылки в эти дни не запрашивали.'}
                        </div>
                        {filtered && (
                            <button type="button" onClick={resetFilters} className={`${iosBtnSecondary} mt-4`}>
                                Снять фильтры
                            </button>
                        )}
                    </div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full min-w-[720px] text-left text-[13px]">
                            <thead>
                                <tr className="border-b border-slate-200/70 text-[11px] uppercase tracking-wide text-slate-400">
                                    <th className="px-4 py-2.5 font-medium">Время</th>
                                    <th className="px-4 py-2.5 font-medium">Сотрудник</th>
                                    <th className="px-4 py-2.5 font-medium">ИИН</th>
                                    <th className="px-4 py-2.5 font-medium">Результат</th>
                                    <th className="px-4 py-2.5 font-medium">Подробности</th>
                                </tr>
                            </thead>
                            <tbody>
                                {groups.map((group) => (
                                    <React.Fragment key={group.key}>
                                        <tr>
                                            <th
                                                colSpan={5}
                                                scope="colgroup"
                                                className="bg-slate-500/[0.04] px-4 py-1.5 text-left text-[11.5px] font-semibold text-slate-500"
                                            >
                                                {group.label}
                                            </th>
                                        </tr>
                                        {group.items.map((item) => (
                                            <JournalRow key={item.id} item={item} showDepartment={scope === null} />
                                        ))}
                                    </React.Fragment>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            {pageCount > 1 && (
                <IosPager
                    page={page}
                    pageCount={pageCount}
                    total={total}
                    from={(page - 1) * pageSize + 1}
                    to={Math.min(total, page * pageSize)}
                    onPage={setPage}
                    unit="записи"
                />
            )}
        </div>
    );
};

/* Подробности строки — что именно ответил сервис. У выданной ссылки это домен
   (самой ссылки в журнале нет), у опечатки — какая именно, у отказа — слова
   вендора. Техническая причина сбоя лежит в title: админу она нужна раз в
   год, а в строке — шум. */
const details = (item) => {
    if (item.outcome === 'link') {
        return item.link_host ? `ссылка на ${hostLabel(item.link_host)}` : 'ссылка выдана';
    }
    if (item.outcome === 'invalid') return iinErrorMessage(item.error_text);
    if (item.outcome === 'limit') return '';
    // У сбоя без слов вендора подробности нет: повторять бейдж словами —
    // шум, а техническая причина лежит в title ячейки.
    return item.vendor_message || '';
};

const JournalRow = ({ item, showDepartment }) => (
    <tr className="border-b border-slate-100 last:border-0">
        <td className="whitespace-nowrap px-4 py-2.5 tabular-nums text-slate-500">
            {formatTime(item.created_at)}
        </td>
        <td className="px-4 py-2.5">
            <div className="font-medium text-slate-800">{item.user_name || '—'}</div>
            <div className="text-[11.5px] text-slate-400">
                {roleLabel(item.user_role)}
                {showDepartment && item.department_code ? ` · ${departmentLabel(item.department_code)}` : ''}
            </div>
        </td>
        <td className="whitespace-nowrap px-4 py-2.5 tabular-nums text-slate-600">
            {formatIin(item.iin)}
        </td>
        <td className="px-4 py-2.5">
            {item.outcome === 'no_documents' ? (
                <span className="text-slate-600">{outcomeLabel(item.outcome)}</span>
            ) : (
                <IosBadge tone={outcomeTone(item.outcome)}>{outcomeLabel(item.outcome)}</IosBadge>
            )}
        </td>
        <td
            className="max-w-[320px] px-4 py-2.5 text-[12.5px] text-slate-600"
            title={item.error_text && item.outcome !== 'invalid' ? item.error_text : undefined}
        >
            {details(item)}
        </td>
    </tr>
);

const Stat = ({ label, value }) => (
    <div className="px-4 py-3">
        <div className="text-[19px] font-semibold leading-tight tabular-nums text-slate-900">
            {value ?? 0}
        </div>
        <div className="mt-0.5 text-[11.5px] leading-tight text-slate-500">{label}</div>
    </div>
);

export default SignLinksView;
