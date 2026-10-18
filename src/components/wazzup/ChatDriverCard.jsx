import React, { useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, Check, ChevronRight, Copy, ExternalLink, IdCard, Loader2, Pencil, X } from 'lucide-react';
import { IosHint } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import {
    amoStatus, changedFields, fmtCardTime, normalizeDriverId, pickSuggested, sameCardValues,
} from './driverCards';
import { loadDriverCardOptions, lookupDriver, useDriverInfo } from './useDriverCard';

/* Карточка водителя в чатах Верификаторов: ID водителя, таксопарк и город
 * привлечения (постановка владельца 16.10.2026), в amoCRM уходят эти три поля.
 *
 * Как устроено на экране (решение владельца 16.10.2026): кнопка «Карточка» —
 * в шапке чата, рядом с номером. Пустая карточка — по кнопке маленькое окно:
 * вставить ID, а таксопарк и город подставятся сами по данным CRM. Заполненная —
 * по кнопке справа выезжает панель: поля сделки, всё о водителе из CRM блоками
 * в духе iOS, история сохранений.
 *
 * Данные и сохранение — useDriverCard.js; списки «Таксопарк» и «Город» —
 * справочники самой amoCRM (loadDriverCardOptions); водитель — CRM yataxi
 * (lookupDriver, useDriverInfo; сервер — wazzup/driver_info.py), а его
 * профессия — кабинет Fleet (wazzup/driver_profession.py). */

const fieldLabel = 'mb-1 flex h-4 items-center gap-1.5 px-1 text-[11px] font-medium text-slate-500';
const idInput = 'h-9 w-full rounded-xl bg-slate-100 px-3 font-mono text-[13px] leading-5 text-slate-900 placeholder:font-sans placeholder:text-slate-400 transition-all focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/60 disabled:opacity-50';
const selectText = 'text-[13px] leading-5 text-slate-900';
const groupCard = 'divide-y divide-slate-100 overflow-hidden rounded-2xl bg-white ring-1 ring-slate-200/70 shadow-[0_1px_2px_rgba(15,23,42,0.04)]';
const actionRow = 'flex w-full items-center justify-between gap-2 px-3.5 py-2.5 text-left text-[13px] font-medium text-blue-600 transition hover:bg-slate-50';
const closeButton = 'grid h-8 w-8 shrink-0 place-items-center rounded-full bg-slate-100 text-slate-500 transition hover:bg-slate-200 hover:text-slate-700 active:scale-95';

/* live=false — экран без живых обновлений (реестр «Карточек»): «отправляется»
   без вращения, иначе спиннер обещал бы движение, которого тут не будет. */
export function AmoStatus({ card, onRetry, busy = false, live = true }) {
    const status = amoStatus(card);
    if (status.tone === 'ok') {
        const url = card.amo?.leadUrl;
        const body = <><Check size={13} aria-hidden="true" /> {status.label}</>;
        return url ? (
            <a href={url} target="_blank" rel="noopener noreferrer" title={status.title}
               className="inline-flex shrink-0 items-center gap-1 text-[12px] font-medium text-emerald-700 hover:underline">
                {body}
            </a>
        ) : <span title={status.title} className="inline-flex shrink-0 items-center gap-1 text-[12px] font-medium text-emerald-700">{body}</span>;
    }
    if (status.tone === 'bad') {
        return (
            <span className="inline-flex shrink-0 items-center gap-1.5 text-[12px]">
                <span title={status.title || undefined} className="inline-flex items-center gap-1 font-medium text-rose-600">
                    <AlertCircle size={13} aria-hidden="true" /> {status.label}
                </span>
                {status.retry && onRetry && (
                    <button type="button" onClick={onRetry} disabled={busy}
                        className="font-semibold text-blue-600 hover:underline disabled:opacity-50">
                        {busy ? 'Ставим…' : 'Повторить'}
                    </button>
                )}
            </span>
        );
    }
    if (status.tone === 'muted') {
        return <span title={status.title} className="shrink-0 text-[12px] text-slate-400">{status.label}</span>;
    }
    return (
        <span title={status.title || undefined} className="inline-flex shrink-0 items-center gap-1 text-[12px] text-slate-500">
            {live && <Loader2 size={12} className="animate-spin" aria-hidden="true" />} {status.label}
        </span>
    );
}

/* Справочник amoCRM → варианты списка. Значение карточки, которого в
   справочнике уже нет (переименовали или убрали в amoCRM), остаётся видно —
   с пометкой: сохранить его нельзя, amoCRM такого значения уже не примет. */
export const MISSING_SUFFIX = ' — нет в amoCRM';
export function selectOptions(list, current) {
    const items = (list || []).map((item) => ({ value: item.id, label: item.name }));
    return current && !items.some((item) => item.value === current.id)
        ? [{ value: current.id, label: `${current.name}${list ? MISSING_SUFFIX : ''}` }, ...items] : items;
}

/* Выбранное значение, которого нет в справочнике amoCRM, — подсказка, что выбрать. */
export function missingChoice(list, value, label) {
    if (!list || value == null || list.some((item) => item.id === value)) return '';
    return `${label} больше нет в списке amoCRM — выберите другой`;
}

/* Escape закрывает форму — но не тот, что закрывает раскрытый список: поиск
   списка живёт в портале (событие React всё равно всплывает до формы), а у
   кнопки раскрытого списка aria-expanded. Тот Escape — дело CustomSelect. */
export const cancelsForm = (event) => event.key === 'Escape'
    && Boolean(event.currentTarget?.contains?.(event.target))
    && event.target?.getAttribute?.('aria-expanded') !== 'true';

const toneStyle = { ok: 'bg-emerald-50 text-emerald-700', bad: 'bg-rose-50 text-rose-600', muted: 'bg-slate-100 text-slate-600' };

export function StatusPill({ status }) {
    if (!status?.label) return null;
    return (
        <span className={`inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[11.5px] font-medium ${toneStyle[status.tone] || toneStyle.muted}`}>
            {status.label}
        </span>
    );
}

/* Что нашли по вставленному ID — под полем, до выбора полей. */
export function LookupNote({ lookup, onRetry }) {
    if (!lookup.id) return null;
    const note = 'rounded-xl px-3 py-2 text-[12px]';
    if (lookup.loading) {
        return (
            <div className={`${note} flex items-center gap-1.5 bg-slate-50 text-slate-500`}>
                <Loader2 size={12} className="animate-spin" aria-hidden="true" /> Ищем водителя в CRM…
            </div>
        );
    }
    const data = lookup.data;
    if (!data || data.state === 'unavailable') {
        return (
            <div className={`${note} bg-slate-50 text-slate-500`}>
                {data?.error || lookup.error || 'CRM не ответила'} — таксопарк и город выберите сами.{' '}
                <button type="button" onClick={onRetry} className="font-semibold text-blue-600 hover:underline">Повторить</button>
            </div>
        );
    }
    if (data.state === 'not_found') {
        return <div className={`${note} bg-amber-50 text-amber-700`}>В CRM нет водителя с таким ID — проверьте ID или выберите таксопарк и город сами.</div>;
    }
    const info = data.info || {};
    const hint = data.suggestion || {};
    const misses = [
        !hint.park && `таксопарка «${hint.crmPark || 'без названия'}» нет в списке amoCRM`,
        !hint.city && (hint.crmCity ? `города «${hint.crmCity}» нет в списке amoCRM` : 'город парка CRM не знает'),
    ].filter(Boolean);
    const facts = [info.profession?.name, info.phone, info.car, hint.crmPark].filter(Boolean).join(' · ');
    return (
        <div className={`${note} bg-slate-50 ring-1 ring-slate-200/70`} data-testid="wazzup-driver-lookup">
            <div className="flex min-w-0 items-center gap-2">
                <span className="min-w-0 truncate text-[13px] font-semibold text-slate-900" title={info.name || undefined}>
                    {info.name || 'Водитель найден'}
                </span>
                <StatusPill status={info.status} />
            </div>
            {facts && <div className="mt-0.5 truncate text-slate-500" title={facts}>{facts}</div>}
            {misses.length > 0 && <div className="mt-1 text-amber-700">{misses.join(', ')} — выберите сами</div>}
        </div>
    );
}

const FromCrm = () => (
    <span className="rounded-full bg-emerald-50 px-1.5 text-[10px] font-semibold leading-4 text-emerald-700">из CRM</span>
);

/* Форма карточки: ID, а таксопарк и город — сами по данным CRM. Поля списков
   появляются, когда есть что в них показать: вставлен ID или карточка уже
   заполнена, — до этого форма — одно поле ID (окно в шапке не растёт зря). */
export function CardForm({ card, chat, apiBaseUrl, headers, onSave, onCancel, onDraft }) {
    const [driver, setDriver] = useState(card?.driverId || '');
    const [parkId, setParkId] = useState(card?.park?.id ?? null);
    const [cityId, setCityId] = useState(card?.city?.id ?? null);
    const [options, setOptions] = useState(null);
    const [optionsError, setOptionsError] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const [fieldError, setFieldError] = useState('');
    const [lookup, setLookup] = useState({ id: null, loading: false, data: null, error: '', attempt: 0 });
    const [auto, setAuto] = useState({ park: false, city: false });
    const inputRef = useRef(null);
    const mounted = useRef(true);
    // Человек уже правил форму — пришедшую версию карточки поверх не подставляем.
    const touched = useRef(false);
    /* Подстановка из CRM. ID вставили в этой форме — парк и город берутся из CRM,
       даже если были выбраны; форма открыта с прежним ID — заполняются только
       пустые. Выбранное руками после вставки ID не перебивается. */
    const idEdited = useRef(false);
    const manual = useRef({ park: false, city: false });
    const values = useRef({ parkId, cityId });
    values.current = { parkId, cityId };

    useEffect(() => {
        mounted.current = true;
        inputRef.current?.focus();
        loadDriverCardOptions({ apiBaseUrl, headers }).then((data) => {
            if (mounted.current) setOptions(data);
        }).catch((failure) => { if (mounted.current) setOptionsError(failure.message); });
        return () => { mounted.current = false; };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiBaseUrl]);

    /* Пока форма открыта, может прийти другая версия: сохранил коллега, или
       живой кадр принёс нашу же версию после «ответ не получен». Нетронутая
       форма берёт её значения; совпавшая с черновиком — снимает «сохраните ещё раз». */
    const cardId = card?.id ?? null;
    useEffect(() => {
        if (!touched.current) {
            setDriver(card?.driverId || '');
            setParkId(card?.park?.id ?? null);
            setCityId(card?.city?.id ?? null);
        }
        setError('');
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [cardId]);

    const parsed = normalizeDriverId(driver);
    const clearsId = Boolean(card?.driverId) && !driver.trim();
    const draft = { driverId: parsed.value, parkId, cityId };
    const empty = !parsed.value && parkId == null && cityId == null;
    const unchanged = sameCardValues(card, draft);
    const idError = fieldError || (driver.trim() && parsed.error)
        || (clearsId ? 'ID уже заполнен — его можно изменить, но не очистить' : '');
    const parkMissing = missingChoice(options?.parks, parkId, 'Этого таксопарка');
    const cityMissing = missingChoice(options?.cities, cityId, 'Этого города');
    const blocked = busy || Boolean(idError) || Boolean(parkMissing) || Boolean(cityMissing)
        || empty || unchanged || !options;
    const edit = (apply, field) => (value) => {
        touched.current = true;
        manual.current = { ...manual.current, [field]: true };
        setAuto((old) => ({ ...old, [field]: false }));
        apply(value);
        setError('');
    };

    const lookupId = parsed.value;
    useEffect(() => {
        if (!lookupId) {
            setLookup((old) => ({ ...old, id: null, loading: false, data: null, error: '' }));
            return undefined;
        }
        const controller = new AbortController();
        setLookup((old) => ({ ...old, id: lookupId, loading: true, data: null, error: '' }));
        // Пауза — на набор руками: вставка ссылки целиком ждёт её же, а не каждой буквы.
        const timer = setTimeout(() => {
            lookupDriver({ apiBaseUrl, headers, chat, driverId: lookupId, signal: controller.signal }).then((data) => {
                if (controller.signal.aborted || !mounted.current) return;
                setLookup((old) => ({ ...old, id: lookupId, loading: false, data, error: '' }));
                const hint = data.state === 'found' ? data.suggestion : null;
                if (!hint) return;
                const pick = pickSuggested({ hint, fresh: idEdited.current, manual: manual.current,
                    parkId: values.current.parkId, cityId: values.current.cityId });
                if (pick.park !== undefined) setParkId(pick.park);
                if (pick.city !== undefined) setCityId(pick.city);
                if (pick.park !== undefined || pick.city !== undefined) {
                    touched.current = true;
                    setAuto({ park: pick.park !== undefined, city: pick.city !== undefined });
                }
            }).catch((failure) => {
                if (controller.signal.aborted || !mounted.current) return;
                setLookup((old) => ({ ...old, id: lookupId, loading: false, data: null, error: failure.message }));
            });
        }, 300);
        return () => { clearTimeout(timer); controller.abort(); };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [lookupId, lookup.attempt, apiBaseUrl]);

    const submit = async (event) => {
        event?.preventDefault();
        if (blocked) return;
        setBusy(true);
        setError('');
        try {
            await onSave(draft);
        } catch (failure) {
            if (!mounted.current) return;
            if (failure.field === 'driverId') setFieldError(failure.message);
            else setError(failure.message);
        } finally {
            if (mounted.current) setBusy(false);
        }
    };

    /* Варианты — постоянными массивами: CustomSelect пересчитывает подсветку
       эффектом на каждый новый массив, и с вариантами «на лету» каждая буква
       в поле ID ставила бы ему состояние — длинная ссылка из Fleet (150+
       нажатий подряд) доводила React до «Maximum update depth exceeded». */
    const parkOptions = useMemo(() => selectOptions(options?.parks, card?.park),
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [options, card?.park?.id, card?.park?.name]);
    const cityOptions = useMemo(() => selectOptions(options?.cities, card?.city),
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [options, card?.city?.id, card?.city?.name]);

    const onKeyDown = (event) => {
        if (!busy && cancelsForm(event)) onCancel();
    };
    const showFields = Boolean(lookup.id) || card?.park != null || card?.city != null;
    const problem = idError || parkMissing || cityMissing || error || optionsError;

    return (
        <form onSubmit={submit} onKeyDown={onKeyDown} className="space-y-2.5" aria-label="Карточка водителя">
            <label className="block">
                <span className={fieldLabel}>
                    ID водителя
                    <IosHint label="Что вставить в ID" text="ID профиля водителя в Яндекс Fleet — 32 символа. Можно вставить ссылку на профиль целиком: ID возьмём из неё, таксопарк и город — из CRM." />
                </span>
                <input ref={inputRef} value={driver} disabled={busy} spellCheck={false} autoComplete="off"
                    onChange={(event) => {
                        touched.current = true;
                        idEdited.current = true;
                        manual.current = { park: false, city: false };
                        setDriver(event.target.value);
                        onDraft?.(event.target.value);
                        setFieldError('');
                        setError('');
                    }}
                    onBlur={() => { if (parsed.value && parsed.value !== driver) setDriver(parsed.value); }}
                    placeholder="32 символа или ссылка из Fleet"
                    aria-invalid={Boolean(idError) || undefined}
                    className={`${idInput} ${idError ? 'ring-2 ring-rose-300' : ''}`} />
            </label>
            {!idError && (
                <LookupNote lookup={lookup}
                    onRetry={() => setLookup((old) => ({ ...old, attempt: old.attempt + 1 }))} />
            )}
            {showFields && (
                <div className="space-y-2">
                    <div className="min-w-0">
                        <span className={fieldLabel}>Таксопарк привлечения {auto.park && <FromCrm />}</span>
                        <CustomSelect variant="ios" searchable value={parkId} onChange={edit(setParkId, 'park')}
                            options={parkOptions} disabled={busy || !options} invalid={Boolean(parkMissing)}
                            placeholder={options ? 'Выберите' : 'Загрузка…'} searchPlaceholder="Найти парк…"
                            ariaLabel="Таксопарк привлечения" textClassName={selectText} />
                    </div>
                    <div className="min-w-0">
                        <span className={fieldLabel}>Город привлечения {auto.city && <FromCrm />}</span>
                        <CustomSelect variant="ios" searchable value={cityId} onChange={edit(setCityId, 'city')}
                            options={cityOptions} disabled={busy || !options} invalid={Boolean(cityMissing)}
                            placeholder={options ? 'Выберите' : 'Загрузка…'} searchPlaceholder="Найти город…"
                            ariaLabel="Город привлечения" textClassName={selectText} />
                    </div>
                </div>
            )}
            {problem && <p role="alert" className="px-1 text-xs text-rose-600">{problem}</p>}
            <div className="flex items-center justify-end gap-2 pt-0.5">
                <div className="flex shrink-0 items-center gap-1.5">
                    <button type="button" onClick={onCancel} disabled={busy}
                        className="inline-flex h-9 items-center rounded-xl px-3 text-[13px] font-medium text-slate-500 transition hover:bg-slate-100 disabled:opacity-50">
                        Отмена
                    </button>
                    <button type="submit" disabled={blocked}
                        className="inline-flex h-9 items-center gap-1.5 rounded-xl bg-blue-600 px-3.5 text-[13px] font-semibold text-white shadow-sm transition hover:bg-blue-700 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50">
                        {busy ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                        {busy ? 'Сохраняем…' : 'Сохранить'}
                    </button>
                </div>
            </div>
        </form>
    );
}

/* ── Кнопка в шапке чата и окно для ID ─────────────────────────────────── */

/* Значок итога записи в amoCRM рядом с подписью кнопки. */
function ButtonMark({ card }) {
    if (!card) return null;
    const { tone } = amoStatus(card);
    if (tone === 'ok') return <Check size={13} className="text-emerald-700" aria-hidden="true" />;
    if (tone === 'bad') return <AlertCircle size={13} className="text-rose-600" aria-hidden="true" />;
    if (tone === 'wait') return <Loader2 size={12} className="animate-spin text-slate-400" aria-hidden="true" />;
    return null;
}

export function DriverCardButton({ card: driverCard, chat, apiBaseUrl, headers, panelOpen, onTogglePanel }) {
    const { current, canEdit, loading, unavailable, save } = driverCard;
    const [formOpen, setFormOpen] = useState(false);
    const root = useRef(null);
    // Набранное в окне не теряется от случайного щелчка мимо: закрывает его тогда
    // только «Отмена», крестик или Escape.
    const draft = useRef('');

    useEffect(() => {
        if (!formOpen) return undefined;
        draft.current = '';
        const onDown = (event) => {
            if (root.current?.contains(event.target) || draft.current.trim()) return;
            setFormOpen(false);
        };
        document.addEventListener('mousedown', onDown);
        return () => document.removeEventListener('mousedown', onDown);
    }, [formOpen]);
    // Карточку успел сохранить коллега — окно пустой карточки больше не нужно.
    const currentId = current?.id ?? null;
    useEffect(() => { if (currentId) setFormOpen(false); }, [currentId]);

    // Сервер ещё без карточек (сайт выкатился раньше) — кнопки нет вовсе; пустую
    // карточку тому, кто не заполняет, показывать нечем.
    if (unavailable || (!current && !loading && !canEdit)) return null;
    const waiting = loading && !current;
    const status = current ? amoStatus(current) : null;
    const active = current ? panelOpen : formOpen;
    return (
        <div ref={root} className="relative">
            <button type="button" disabled={waiting}
                onClick={() => (current ? onTogglePanel() : setFormOpen((value) => !value))}
                aria-expanded={active} aria-haspopup="dialog" data-testid="wazzup-driver-card-button"
                title={current ? `Карточка водителя · ${status.label}` : 'Заполнить карточку водителя'}
                className={`inline-flex h-9 items-center justify-center gap-1.5 rounded-full px-3 text-xs font-semibold transition active:scale-[0.97] disabled:opacity-60 ${
                    active ? 'bg-blue-50 text-blue-600 ring-1 ring-blue-100' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>
                {waiting ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <IdCard size={16} aria-hidden="true" />}
                Карточка
                <ButtonMark card={current} />
            </button>
            {formOpen && !current && (
                <div role="dialog" aria-label="Карточка водителя"
                    className="absolute left-0 top-full z-40 mt-2 w-[340px] max-w-[calc(100vw-24px)] rounded-2xl bg-white p-3.5 text-left shadow-[0_14px_40px_rgba(15,23,42,0.16)] ring-1 ring-slate-200/80 motion-safe:animate-popover-in">
                    <div className="mb-2.5 flex items-center justify-between gap-2">
                        <span className="text-[14px] font-semibold text-slate-900">Карточка водителя</span>
                        <button type="button" onClick={() => setFormOpen(false)} aria-label="Закрыть" className={closeButton}>
                            <X size={14} aria-hidden="true" />
                        </button>
                    </div>
                    <CardForm card={null} chat={chat} apiBaseUrl={apiBaseUrl} headers={headers}
                        onDraft={(value) => { draft.current = value; }}
                        onSave={async (values) => { await save(values); setFormOpen(false); }}
                        onCancel={() => setFormOpen(false)} />
                </div>
            )}
        </div>
    );
}

/* ── Панель справа ─────────────────────────────────────────────────────── */

export const initials = (name) => String(name || '').trim().split(/\s+/).filter(Boolean).slice(0, 2)
    .map((word) => word[0].toUpperCase()).join('') || '?';

function Group({ title, note, children }) {
    return (
        <section>
            {title && <div className="mb-1.5 px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">{title}</div>}
            <div className={groupCard}>{children}</div>
            {note && <div className="mt-1.5 px-1 text-[11px] text-slate-400">{note}</div>}
        </section>
    );
}

function Row({ label, children, mono = false }) {
    return (
        <div className="flex items-baseline justify-between gap-3 px-3.5 py-2.5 text-[13px]">
            <span className="shrink-0 text-slate-500">{label}</span>
            <span className={`min-w-0 break-words text-right text-slate-900${mono ? ' font-mono text-[12.5px]' : ''}`}>{children}</span>
        </div>
    );
}

function CopyButton({ value }) {
    const [copied, setCopied] = useState(false);
    useEffect(() => {
        if (!copied) return undefined;
        const timer = setTimeout(() => setCopied(false), 1500);
        return () => clearTimeout(timer);
    }, [copied]);
    return (
        <button type="button" title={copied ? 'Скопировано' : 'Скопировать'} aria-label="Скопировать ID"
            onClick={() => { navigator.clipboard?.writeText?.(value)?.then(() => setCopied(true)).catch(() => {}); }}
            className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-700">
            {copied ? <Check size={14} className="text-emerald-700" aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
        </button>
    );
}

/* Строки CRM, которые уже стоят в шапке панели, в блоках не повторяются. */
const IN_HEADER = new Set(['name', 'phone', 'status']);

function CrmRow({ row }) {
    if (row.chips?.length) {
        return (
            <div className="px-3.5 py-2.5 text-[13px]">
                <div className="text-slate-500">{row.label}</div>
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {row.chips.map((chip) => (
                        <span key={chip} className="rounded-full bg-slate-100 px-2 py-0.5 text-[11.5px] font-medium text-slate-700">{chip}</span>
                    ))}
                </div>
            </div>
        );
    }
    if (row.tone) return <Row label={row.label}><StatusPill status={{ label: row.value, tone: row.tone }} /></Row>;
    return <Row label={row.label} mono={row.mono}>{row.value}</Row>;
}

function CrmSections({ data }) {
    const info = data.info || {};
    return (info.sections || []).map((section) => {
        const rows = (section.rows || []).filter((row) => !IN_HEADER.has(row.key));
        if (!rows.length && !section.stats?.length) return null;
        return (
            <Group key={section.title} title={section.title}>
                {section.stats?.length > 0 && (
                    <div className="grid divide-x divide-slate-100" style={{ gridTemplateColumns: `repeat(${section.stats.length}, minmax(0, 1fr))` }}>
                        {section.stats.map((stat) => (
                            <div key={stat.label} className="px-1.5 py-2.5 text-center">
                                <div className="text-[16px] font-semibold tabular-nums text-slate-900">{stat.value}</div>
                                <div className="mt-0.5 text-[11px] text-slate-500">{stat.label}</div>
                            </div>
                        ))}
                    </div>
                )}
                {rows.map((row, index) => <CrmRow key={`${row.key}:${index}`} row={row} />)}
            </Group>
        );
    });
}

/* Всё о водителе из CRM: блоками, как «Настройки» в iOS. */
export function DriverCrm({ driver }) {
    const { data, error, hidden, loading, reload } = driver;
    if (hidden) return null;
    const message = (text, retry = false) => (
        <Group title="Данные CRM">
            <div className="px-3.5 py-3 text-[13px] text-slate-500">
                {text}{retry && <>{' '}<button type="button" onClick={reload} className="font-semibold text-blue-600 hover:underline">Повторить</button></>}
            </div>
        </Group>
    );
    if (data?.state === 'found' && data.info) {
        return (
            <>
                <CrmSections data={data} />
                {data.fetchedAt && (
                    <p className="px-1 text-[11px] text-slate-400">
                        {data.stale ? 'CRM сейчас не отвечает — данные на ' : 'Данные CRM на '}{fmtCardTime(data.fetchedAt)}
                    </p>
                )}
            </>
        );
    }
    if (data?.state === 'not_found') return message('В CRM нет водителя с этим ID');
    if (loading) {
        return (
            <Group title="Данные CRM">
                <div className="flex items-center gap-2 px-3.5 py-3 text-[13px] text-slate-500">
                    <Loader2 size={14} className="animate-spin" aria-hidden="true" /> Загружаем данные водителя…
                </div>
            </Group>
        );
    }
    return message(data?.error || error || 'Данные водителя из CRM не загрузились', true);
}

function HistoryGroup({ items, onRetry, retrying }) {
    const [all, setAll] = useState(false);
    if (items.length < 2) return null;
    const shown = all ? items : items.slice(0, 3);
    return (
        <Group title={`История · ${items.length}`}>
            {shown.map((card, index) => {
                const changed = changedFields(card, items[index + 1]);
                const strong = (field) => (changed.has(field) ? 'font-semibold text-slate-900' : 'text-slate-700');
                return (
                    <div key={card.id} className="px-3.5 py-2.5" data-testid="wazzup-driver-card-version">
                        <div className="flex items-baseline justify-between gap-2 text-[11.5px] text-slate-500">
                            <span className="min-w-0 truncate">{fmtCardTime(card.createdAt)} · {card.authorName || 'Сотрудник'}</span>
                            <AmoStatus card={card} onRetry={index === 0 && onRetry ? () => onRetry(card.id) : null} busy={retrying} />
                        </div>
                        <div className="mt-1 space-y-0.5 text-[12.5px]">
                            {card.driverId && <div className={`break-all font-mono text-[12px] ${strong('driverId')}`}>{card.driverId}</div>}
                            <div className="text-slate-500">
                                <span className={strong('park')}>{card.park?.name || '—'}</span>
                                {' · '}
                                <span className={strong('city')}>{card.city?.name || '—'}</span>
                            </div>
                        </div>
                    </div>
                );
            })}
            {items.length > 3 && (
                <button type="button" onClick={() => setAll((value) => !value)} className={actionRow}>
                    {all ? 'Свернуть' : `Показать все (${items.length})`}
                    <ChevronRight size={14} className={`text-slate-300 transition-transform ${all ? '-rotate-90' : 'rotate-90'}`} aria-hidden="true" />
                </button>
            )}
        </Group>
    );
}

export function DriverCardPanel({ card: driverCard, chat, apiBaseUrl, headers, showToast, onClose }) {
    const { current, items, canEdit, loading, error, save, retry, refresh, unavailable } = driverCard;
    const driver = useDriverInfo({ chat, driverId: unavailable ? null : current?.driverId, apiBaseUrl, headers });
    const [editing, setEditing] = useState(false);
    const [retrying, setRetrying] = useState(false);
    const crm = driver.data?.state === 'found' ? driver.data.info : null;
    const title = crm?.name || chat?.contactName || chat?.contactPhone || chat?.chatId || 'Карточка водителя';
    const profession = crm?.profession?.name || '';
    if (unavailable) return null;

    const onRetry = async (cardId) => {
        setRetrying(true);
        try { await retry(cardId); } catch (failure) { showToast?.(failure.message, 'error'); } finally { setRetrying(false); }
    };
    const onKeyDown = (event) => {
        if (event.key === 'Escape' && !editing && event.currentTarget.contains(event.target)) onClose();
    };

    let card;
    if (editing && canEdit) {
        card = (
            <Group title="Карточка">
                <div className="p-3.5">
                    <CardForm card={current} chat={chat} apiBaseUrl={apiBaseUrl} headers={headers}
                        onSave={async (values) => { await save(values); setEditing(false); }}
                        onCancel={() => setEditing(false)} />
                </div>
            </Group>
        );
    } else if (current) {
        const first = items[items.length - 1];
        card = (
            <Group title="Карточка" note="ID, таксопарк и город уходят в сделку amoCRM">
                {current.driverId && (
                    <div className="px-3.5 py-2.5">
                        <div className="text-[13px] text-slate-500">ID водителя</div>
                        <div className="mt-0.5 flex items-center justify-between gap-2">
                            <span className="min-w-0 break-all font-mono text-[12.5px] text-slate-900">{current.driverId}</span>
                            <CopyButton value={current.driverId} />
                        </div>
                    </div>
                )}
                <Row label="Таксопарк">{current.park?.name || <span className="text-slate-400">—</span>}</Row>
                <Row label="Город">{current.city?.name || <span className="text-slate-400">—</span>}</Row>
                <Row label="amoCRM"><AmoStatus card={current} onRetry={canEdit ? () => onRetry(current.id) : null} busy={retrying} /></Row>
                {first && (
                    <Row label="Заполнил(а)">
                        <span className="text-slate-600">{first.authorName || 'Сотрудник'} · {fmtCardTime(first.createdAt)}</span>
                    </Row>
                )}
                {crm?.fleetUrl && (
                    <a href={crm.fleetUrl} target="_blank" rel="noopener noreferrer" className={actionRow}>
                        Открыть во Fleet <ExternalLink size={14} className="text-slate-300" aria-hidden="true" />
                    </a>
                )}
                {canEdit && (
                    <button type="button" onClick={() => setEditing(true)} className={actionRow}>
                        Изменить <Pencil size={14} className="text-slate-300" aria-hidden="true" />
                    </button>
                )}
            </Group>
        );
    } else if (loading) {
        card = (
            <Group title="Карточка">
                <div className="flex items-center gap-2 px-3.5 py-3 text-[13px] text-slate-500">
                    <Loader2 size={14} className="animate-spin" aria-hidden="true" /> Загрузка карточки…
                </div>
            </Group>
        );
    } else if (error) {
        card = (
            <Group title="Карточка">
                <div className="px-3.5 py-3 text-[13px] text-slate-500">
                    {error}{' '}
                    <button type="button" onClick={() => refresh()} className="font-semibold text-blue-600 hover:underline">Повторить</button>
                </div>
            </Group>
        );
    } else {
        card = (
            <Group title="Карточка">
                <div className="px-3.5 py-3 text-[13px] text-slate-500">Карточка водителя не заполнена</div>
                {canEdit && (
                    <button type="button" onClick={() => setEditing(true)} className={actionRow}>
                        Заполнить <ChevronRight size={14} className="text-slate-300" aria-hidden="true" />
                    </button>
                )}
            </Group>
        );
    }

    return (
        <aside aria-label="Карточка водителя" data-testid="wazzup-driver-panel" onKeyDown={onKeyDown}
            className="absolute inset-y-0 right-0 z-30 flex w-full flex-col border-l border-slate-200/70 bg-slate-50 shadow-[-12px_0_32px_rgba(15,23,42,0.10)] motion-safe:animate-push-in sm:w-[360px] xl:static xl:z-auto xl:shrink-0 xl:shadow-none">
            <div className="flex items-start gap-3 border-b border-slate-200/70 bg-white/80 px-4 pb-3 pt-3.5">
                <div className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-slate-200 text-[14px] font-semibold text-slate-600" aria-hidden="true">
                    {initials(title)}
                </div>
                <div className="min-w-0 flex-1">
                    <div className="truncate text-[15.5px] font-semibold text-slate-900" title={title}>{title}</div>
                    {/* Профессия из кабинета Fleet (просьба владельца 18.10.2026) — своей
                        строкой, как должность в «Контактах»: длинная («Водитель на грузовом
                        авто») не рвёт строку телефона и не уносит метку статуса вниз. */}
                    {profession && (
                        <div className="mt-0.5 truncate text-[12.5px] text-slate-500" title={profession}>{profession}</div>
                    )}
                    <div className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px] text-slate-500">
                        {crm?.phone ? <span>{crm.phone}</span> : <span>Карточка водителя</span>}
                        <StatusPill status={crm?.status} />
                    </div>
                </div>
                <button type="button" onClick={onClose} aria-label="Закрыть карточку" className={closeButton}>
                    <X size={15} aria-hidden="true" />
                </button>
            </div>
            <div className="thin-scroll min-h-0 flex-1 space-y-4 overflow-y-auto px-3.5 pb-6 pt-3.5">
                {card}
                {current?.driverId && !editing && <DriverCrm driver={driver} />}
                {!editing && <HistoryGroup items={items} onRetry={canEdit ? onRetry : null} retrying={retrying} />}
            </div>
        </aside>
    );
}
