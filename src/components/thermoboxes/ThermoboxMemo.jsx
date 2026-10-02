import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { ChevronDown, ChevronUp, Loader2, Pencil, Plus, Trash2 } from 'lucide-react';
import { IosModal, IosSegmented, iosBtnGhost, iosBtnPrimary, iosCard, iosGroupLabel, iosInput } from '../ui/ios';
import { MEMO_KIND_META, MemoIcon, TONES } from './thermoboxUi';
import {
    MEMO_ITEMS_MAX, MEMO_KINDS, MEMO_KIND_LABELS, MEMO_OWNER_LABELS, MEMO_TEXT_MAX, fmtStamp, memoKindOf, splitMemo,
} from './thermoboxMeta';

/*
 * «Памятка для сотрудников» — правила выдачи термокоробов рядом с таблицей
 * (постановка #363, блок 4). Редактирует только супервайзер.
 *
 * Пункт — карточка со значком, как строка памятки в листе (⛔ 💰 📊 ⚠️ ✅ ❓):
 * первая строка — суть жирным, остальное — пояснение, ниже — кто отвечает.
 * Значок у пункта свой (`kind`), его выбирает СВ; у пункта, сохранённого до
 * появления значков, он угадывается по тексту.
 */

const OWNER_OPTIONS = [
    { value: 'kc', label: MEMO_OWNER_LABELS.kc },
    { value: 'regions', label: MEMO_OWNER_LABELS.regions },
    { value: '', label: 'Никто' },
];

const errorOf = (requestError, fallback) => requestError?.response?.data?.error || fallback;

/* Поле пункта растёт под текст: пункт памятки — от строки до трёх, и поле
   фиксированной высоты прятало бы конец правила под край. */
const fitHeight = (element) => {
    if (!element) return;
    element.style.height = 'auto';
    element.style.height = `${element.scrollHeight}px`;
};

/* Значок пункта — шесть кнопок-переключателей, как выбор значка в «Напоминаниях» iOS. */
const KindPicker = ({ value, onChange }) => (
    <div className="flex items-center gap-1" role="radiogroup" aria-label="Значок правила">
        {MEMO_KINDS.map((kind) => {
            const meta = MEMO_KIND_META[kind];
            const Icon = meta.icon;
            const active = value === kind;
            return (
                <button
                    key={kind}
                    type="button"
                    role="radio"
                    aria-checked={active}
                    aria-label={MEMO_KIND_LABELS[kind]}
                    title={MEMO_KIND_LABELS[kind]}
                    onClick={() => onChange(kind)}
                    className={`grid h-7 w-7 place-items-center rounded-lg transition active:scale-95 ${
                        active ? `${TONES[meta.tone].tile} text-white shadow-sm` : 'bg-slate-100 text-slate-400 hover:text-slate-600'
                    }`}
                >
                    <Icon size={14} strokeWidth={2.3} />
                </button>
            );
        })}
    </div>
);

const MemoEditor = ({ open, memo, onClose, apiBaseUrl, headers, onSaved, showToast }) => {
    const [title, setTitle] = useState('');
    const [items, setItems] = useState([]);
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        if (!open) return;
        setTitle(memo?.title || '');
        setItems((memo?.items || []).map((item, index) => ({
            key: `m${index}`, text: item.text || '', owner: item.owner || '', kind: memoKindOf(item),
        })));
    }, [open, memo]);

    const update = (key, patch) => setItems((list) => list.map((item) => (item.key === key ? { ...item, ...patch } : item)));
    const remove = (key) => setItems((list) => list.filter((item) => item.key !== key));
    const move = (index, delta) => setItems((list) => {
        const next = [...list];
        const target = index + delta;
        if (target < 0 || target >= next.length) return list;
        [next[index], next[target]] = [next[target], next[index]];
        return next;
    });
    const add = () => setItems((list) => [...list, { key: `n${Date.now()}`, text: '', owner: 'kc', kind: 'data' }]);

    const filled = items.filter((item) => item.text.trim());
    const tooLong = items.some((item) => item.text.trim().length > MEMO_TEXT_MAX);
    const valid = !tooLong && filled.length <= MEMO_ITEMS_MAX;

    const save = async () => {
        if (!valid || saving) return;
        setSaving(true);
        try {
            const response = await axios.put(`${apiBaseUrl}/api/thermoboxes/memo`, {
                title,
                items: filled.map((item) => ({ text: item.text, owner: item.owner || null, kind: item.kind })),
            }, { headers: headers() });
            onSaved?.(response.data?.memo);
            showToast?.('Памятка сохранена', 'success');
            onClose();
        } catch (requestError) {
            showToast?.(errorOf(requestError, 'Не удалось сохранить памятку'), 'error');
        } finally {
            setSaving(false);
        }
    };

    const ghostDisabled = 'disabled:pointer-events-none disabled:opacity-30';

    return (
        <IosModal
            open={open}
            onClose={onClose}
            title="Памятка для сотрудников"
            subtitle={memo?.updated_at
                ? `Изменена ${fmtStamp(memo.updated_at)}${memo.updated_by_name ? ` · ${memo.updated_by_name}` : ''}`
                : null}
            maxWidth="max-w-2xl"
            footer={(
                <button type="button" className={iosBtnPrimary} onClick={save} disabled={!valid || saving}>
                    {saving && <Loader2 size={15} className="animate-spin" />}
                    Сохранить
                </button>
            )}
        >
            <div className="space-y-4">
                <label className="block space-y-1.5">
                    <span className={iosGroupLabel}>Подзаголовок</span>
                    <input className={iosInput} value={title} maxLength={200} onChange={(event) => setTitle(event.target.value)}
                           placeholder="Термопакеты и термокороба — правила выдачи, депозита и сверки данных" />
                </label>

                <section className="space-y-1.5">
                    <div className={iosGroupLabel}>Пункты</div>
                    <p className="px-1 text-[12px] text-slate-500">Первая строка пункта — суть, она пишется жирным; со второй строки — пояснение.</p>
                    <ol className="space-y-2">
                        {items.map((item, index) => (
                            <li key={item.key} className={`${iosCard} space-y-2 p-3`}>
                                <div className="flex items-start gap-2.5">
                                    <MemoIcon kind={item.kind} />
                                    <textarea
                                        ref={fitHeight}
                                        className={`${iosInput} min-h-[44px] grow basis-0 resize-none overflow-hidden leading-snug`}
                                        value={item.text}
                                        rows={1}
                                        onChange={(event) => { update(item.key, { text: event.target.value }); fitHeight(event.target); }}
                                        placeholder="Текст правила"
                                        aria-label={`Пункт ${index + 1}`}
                                    />
                                </div>
                                <div className="flex flex-wrap items-center justify-between gap-2 pl-[46px]">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <KindPicker value={item.kind} onChange={(kind) => update(item.key, { kind })} />
                                        <IosSegmented
                                            size="xs"
                                            value={item.owner}
                                            onChange={(owner) => update(item.key, { owner })}
                                            options={OWNER_OPTIONS}
                                            ariaLabel="Кто отвечает"
                                        />
                                    </div>
                                    <div className="flex items-center gap-0.5">
                                        <button type="button" className={`${iosBtnGhost} ${ghostDisabled}`} onClick={() => move(index, -1)}
                                                disabled={index === 0} aria-label="Выше">
                                            <ChevronUp size={16} />
                                        </button>
                                        <button type="button" className={`${iosBtnGhost} ${ghostDisabled}`} onClick={() => move(index, 1)}
                                                disabled={index === items.length - 1} aria-label="Ниже">
                                            <ChevronDown size={16} />
                                        </button>
                                        <button type="button" className={`${iosBtnGhost} hover:!text-rose-600`} onClick={() => remove(item.key)}
                                                aria-label="Удалить пункт">
                                            <Trash2 size={15} />
                                        </button>
                                    </div>
                                </div>
                                {item.text.trim().length > MEMO_TEXT_MAX && (
                                    <p className="pl-[46px] text-[12px] text-rose-600">Не длиннее {MEMO_TEXT_MAX} знаков</p>
                                )}
                            </li>
                        ))}
                    </ol>
                    {items.length < MEMO_ITEMS_MAX && (
                        <button type="button" className={`${iosBtnGhost} !text-blue-600`} onClick={add}>
                            <Plus size={15} /> Добавить пункт
                        </button>
                    )}
                </section>
            </div>
        </IosModal>
    );
};

const ThermoboxMemo = ({ memo, canEdit, apiBaseUrl, headers, onSaved, showToast }) => {
    const [editing, setEditing] = useState(false);
    const items = memo?.items || [];

    if (!items.length && !canEdit) return null;

    return (
        <aside className={`${iosCard} p-4`}>
            <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 grow basis-0">
                    <h2 className="text-[16px] font-bold text-slate-900">Памятка для сотрудников</h2>
                    {memo?.title && <p className="mt-0.5 text-[12.5px] text-slate-500">{memo.title}</p>}
                </div>
                {canEdit && (
                    <button type="button" className={`${iosBtnGhost} -mr-2 -mt-1 shrink-0`} onClick={() => setEditing(true)}
                            aria-label="Изменить памятку">
                        <Pencil size={14} /> <span className="hidden sm:inline">Изменить</span>
                    </button>
                )}
            </div>

            {items.length ? (
                /* Под таблицей — в две колонки (ширина есть), справа от неё на
                   широком экране — в одну. */
                <ol className="mt-3 grid gap-2 lg:grid-cols-2 2xl:grid-cols-1">
                    {items.map((item, index) => {
                        const { head, rest } = splitMemo(item.text);
                        return (
                            <li key={index} className="flex gap-3 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-200/60">
                                <MemoIcon item={item} />
                                <div className="min-w-0 grow basis-0">
                                    <p className="text-[13.5px] font-semibold leading-snug text-slate-900">{head}</p>
                                    {rest && <p className="mt-0.5 text-[12.5px] leading-snug text-slate-600">{rest}</p>}
                                    {item.owner && MEMO_OWNER_LABELS[item.owner] && (
                                        <p className="mt-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                                            Отвечает: {MEMO_OWNER_LABELS[item.owner]}
                                        </p>
                                    )}
                                </div>
                            </li>
                        );
                    })}
                </ol>
            ) : (
                <p className="mt-3 text-[13px] text-slate-500">Памятка пока пустая — добавьте правила выдачи.</p>
            )}

            {canEdit && (
                <MemoEditor
                    open={editing}
                    memo={memo}
                    onClose={() => setEditing(false)}
                    apiBaseUrl={apiBaseUrl}
                    headers={headers}
                    onSaved={onSaved}
                    showToast={showToast}
                />
            )}
        </aside>
    );
};

export default ThermoboxMemo;
