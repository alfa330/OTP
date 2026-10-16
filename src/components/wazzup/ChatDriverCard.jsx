import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { AlertCircle, Check, History, IdCard, Loader2 } from 'lucide-react';
import { IosHint, IosModal } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { amoStatus, changedFields, fmtCardTime, normalizeDriverId, sameCardValues } from './driverCards';
import { loadDriverCardOptions } from './useDriverCard';

/* Карточка водителя сверху у чата: ID водителя, таксопарк и город привлечения
 * (постановка владельца 16.10.2026). Свёрнута — одна строка значений и итог
 * записи в сделку amoCRM; «Изменить» раскрывает форму тут же, над перепиской;
 * «История» — все сохранения карточки этого чата.
 *
 * Данные и сохранение — useDriverCard.js; чем заполнять поле «Таксопарк» и
 * «Город» — справочники самой amoCRM (loadDriverCardOptions). */

const pill = 'inline-flex shrink-0 items-center gap-1.5 rounded-full bg-slate-100 px-3 py-1.5 text-[12px] font-semibold text-slate-600 transition hover:bg-slate-200 active:scale-[0.97]';
// Подписи одной высоты — с «i» у ID и без неё у списков.
const fieldLabel = 'mb-1 flex h-4 items-center gap-1 px-1 text-[11px] font-medium text-slate-500';
// Поле ID — в виде кнопки списка рядом (CustomSelect variant="ios"), а не
// серой заливкой iosInput: в одной строке формы два вида полей — шум.
const idInput = 'h-9 w-full rounded-xl bg-white px-3 font-mono text-[13px] leading-5 text-slate-900 ring-1 shadow-[0_1px_2px_rgba(15,23,42,0.04)] placeholder:font-sans placeholder:text-slate-400 transition-all focus:outline-none focus:ring-2 focus:ring-blue-500/60 disabled:opacity-50';
const selectText = 'text-[13px] leading-5 text-slate-900';

function Value({ label, value, mono = false, strong = false }) {
    return (
        <div className="flex min-w-0 items-baseline gap-1.5">
            <dt className="shrink-0 text-slate-400">{label}</dt>
            <dd className={`min-w-0 truncate ${mono ? 'font-mono text-[12px]' : ''} ${
                value ? (strong ? 'font-semibold text-slate-900' : 'text-slate-800') : 'text-slate-400'}`}
                title={value || undefined}>
                {value || '—'}
            </dd>
        </div>
    );
}

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

function CardEditor({ card, apiBaseUrl, headers, onSave, onCancel }) {
    const [driver, setDriver] = useState(card?.driverId || '');
    const [parkId, setParkId] = useState(card?.park?.id ?? null);
    const [cityId, setCityId] = useState(card?.city?.id ?? null);
    const [options, setOptions] = useState(null);
    const [optionsError, setOptionsError] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const [fieldError, setFieldError] = useState('');
    const inputRef = useRef(null);
    const mounted = useRef(true);
    // Человек уже правил форму — пришедшую версию карточки поверх не подставляем.
    const touched = useRef(false);

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
    const edit = (apply) => (value) => { touched.current = true; apply(value); setError(''); };

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

    return (
        <form onSubmit={submit} onKeyDown={onKeyDown}
            className="grid w-full gap-2 sm:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)_minmax(0,1fr)_auto] sm:items-end"
            aria-label="Карточка водителя">
            <label className="block min-w-0">
                <span className={fieldLabel}>
                    ID водителя
                    <IosHint label="Что вставить в ID" text="ID профиля водителя в Яндекс Fleet — 32 символа. Можно вставить ссылку на профиль целиком: ID возьмём из неё." />
                </span>
                <input ref={inputRef} value={driver} disabled={busy} spellCheck={false} autoComplete="off"
                    onChange={(event) => { touched.current = true; setDriver(event.target.value); setFieldError(''); setError(''); }}
                    onBlur={() => { if (parsed.value && parsed.value !== driver) setDriver(parsed.value); }}
                    placeholder="32 символа или ссылка из Fleet"
                    aria-invalid={Boolean(idError) || undefined}
                    className={`${idInput} ${idError ? 'ring-2 ring-rose-300' : 'ring-slate-200/70'}`} />
            </label>
            <div className="min-w-0">
                <span className={fieldLabel}>Таксопарк привлечения</span>
                <CustomSelect variant="ios" searchable value={parkId} onChange={edit(setParkId)}
                    options={parkOptions} disabled={busy || !options} invalid={Boolean(parkMissing)}
                    placeholder={options ? 'Выберите' : 'Загрузка…'} searchPlaceholder="Найти парк…"
                    ariaLabel="Таксопарк привлечения" textClassName={selectText} />
            </div>
            <div className="min-w-0">
                <span className={fieldLabel}>Город привлечения</span>
                <CustomSelect variant="ios" searchable value={cityId} onChange={edit(setCityId)}
                    options={cityOptions} disabled={busy || !options} invalid={Boolean(cityMissing)}
                    placeholder={options ? 'Выберите' : 'Загрузка…'} searchPlaceholder="Найти город…"
                    ariaLabel="Город привлечения" textClassName={selectText} />
            </div>
            <div className="flex items-center gap-1.5">
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
            {(idError || parkMissing || cityMissing || error || optionsError) && (
                <p role="alert" className="px-1 text-xs text-rose-600 sm:col-span-4">
                    {idError || parkMissing || cityMissing || error || optionsError}
                </p>
            )}
        </form>
    );
}

function CardHistory({ open, onClose, items, chat, onRetry, retrying }) {
    // IosModal сам Escape не ловит — как и просмотр вложений раздела, окно закрывается им здесь.
    const close = useRef(onClose);
    close.current = onClose;
    useEffect(() => {
        if (!open) return undefined;
        const onKey = (event) => {
            if (event.key !== 'Escape') return;
            event.stopPropagation();
            close.current?.();
        };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [open]);
    return (
        <IosModal open={open} onClose={onClose} title="История карточки"
            subtitle={chat?.contactName || chat?.contactPhone || chat?.chatId} maxWidth="max-w-md">
            <ol className="space-y-2" data-testid="wazzup-driver-card-history">
                {items.map((card, index) => {
                    const changed = changedFields(card, items[index + 1]);
                    return (
                        <li key={card.id} className="rounded-2xl bg-white px-3.5 py-2.5 ring-1 ring-slate-200/70">
                            <div className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-0.5 text-[12px] text-slate-500">
                                <span>{fmtCardTime(card.createdAt)} · {card.authorName || 'Сотрудник'}</span>
                                <AmoStatus card={card} onRetry={index === 0 && onRetry ? () => onRetry(card.id) : null}
                                    busy={retrying} />
                            </div>
                            <dl className="mt-1 grid gap-0.5 text-[13px]">
                                <Value label="ID" value={card.driverId} mono strong={changed.has('driverId')} />
                                <Value label="Таксопарк" value={card.park?.name} strong={changed.has('park')} />
                                <Value label="Город" value={card.city?.name} strong={changed.has('city')} />
                            </dl>
                        </li>
                    );
                })}
            </ol>
            {items.length >= 50 && <p className="mt-2 px-1 text-[11px] text-slate-400">Показаны последние 50 сохранений</p>}
        </IosModal>
    );
}

export default function ChatDriverCard({ chat, card: driverCard, apiBaseUrl, headers, showToast }) {
    const { current, items, canEdit, loading, error, save, retry, refresh } = driverCard;
    const [editing, setEditing] = useState(false);
    const [historyOpen, setHistoryOpen] = useState(false);
    const [retrying, setRetrying] = useState(false);

    const onSave = async (values) => {
        await save(values);
        setEditing(false);
    };
    const onRetry = async (cardId) => {
        setRetrying(true);
        try { await retry(cardId); } catch (failure) { showToast?.(failure.message, 'error'); } finally { setRetrying(false); }
    };

    // Сервер ещё без карточек (портал выкатился раньше) — полосы нет вовсе.
    if (driverCard.unavailable) return null;

    /* Окно истории — в body: у полосы карточки backdrop-filter, а он делает её
       опорным блоком для position: fixed, и окно вставало бы внутри полосы.
       На сервере (тесты отрисовки) документа нет — окна тоже. */
    const history = typeof document === 'undefined' ? null : createPortal(
        <CardHistory open={historyOpen} onClose={() => setHistoryOpen(false)} items={items} chat={chat}
            onRetry={canEdit ? onRetry : null} retrying={retrying} />, document.body);

    return (
        <section aria-label="Карточка водителя" data-testid="wazzup-driver-card"
            className="shrink-0 border-b border-slate-200/70 bg-white/85 px-4 py-2 backdrop-blur-xl">
            {editing && canEdit ? (
                <CardEditor card={current} apiBaseUrl={apiBaseUrl} headers={headers}
                    onSave={onSave} onCancel={() => setEditing(false)} />
            ) : (
                <div className="flex w-full flex-wrap items-center gap-x-3 gap-y-1.5">
                    <IdCard size={15} className="shrink-0 text-slate-400" aria-hidden="true" />
                    {current ? (
                        <dl className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-4 gap-y-0.5 text-[12.5px]">
                            <Value label="ID" value={current.driverId} mono />
                            <Value label="Таксопарк" value={current.park?.name} />
                            <Value label="Город" value={current.city?.name} />
                        </dl>
                    ) : (
                        <span className="min-w-0 flex-1 text-[12.5px] text-slate-500">
                            {loading ? 'Загрузка карточки…' : error
                                ? <>{error} <button type="button" onClick={() => refresh()} className="font-semibold text-blue-600 hover:underline">Повторить</button></>
                                : 'Карточка водителя не заполнена'}
                        </span>
                    )}
                    {current && <AmoStatus card={current} onRetry={canEdit ? () => onRetry(current.id) : null} busy={retrying} />}
                    {items.length > 0 && (
                        <button type="button" onClick={() => setHistoryOpen(true)} className={pill}
                            aria-label="История карточки" title="Все сохранения карточки">
                            <History size={12} aria-hidden="true" /> История
                        </button>
                    )}
                    {/* До ответа сервера неизвестно, есть ли у чата карточка: пустая
                        форма поверх заполненной дала бы перезаписать её, не видя. */}
                    {canEdit && !(loading && !current) && (
                        <button type="button" onClick={() => setEditing(true)} className={pill}>
                            {current ? 'Изменить' : 'Заполнить'}
                        </button>
                    )}
                </div>
            )}
            {history}
        </section>
    );
}
