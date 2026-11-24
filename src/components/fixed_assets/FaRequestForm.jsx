import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Check, Loader2, Plus, Send, X } from 'lucide-react';
import { IosModal, IosToggle, iosBtnPrimary, iosBtnSecondary } from '../ui/ios';
import {
    CHECK_BY_LABELS, KIND_LABELS, PHOTO_EXTRA, PHOTO_SLOTS, blankRequest, errorFields, errorText, hasAssets, isMoving,
    photoGapText, photoGaps, placeText, requestIssues, requestPayload, requestToForm, shortName,
} from './fixedAssetsMeta';
import {
    ErrorNote, FIELD, FIELD_INVALID, Field, Footnote, FormSelect, Group, GroupTitle, PersonSelect, PhotoSlot, SURFACE,
    SearchField, Thumb, useDebounced,
} from './fixedAssetsUi';

/*
 * Форма запроса: перемещение, списание, передача при увольнении, «Новое ОС».
 *
 * К каждому ОС — два обязательных снимка, у каждого своё место: «ОС целиком» и «Инвентарный
 * номер» (ТЗ, правило «Фото при любом запросе»). Без них «Отправить» запрос не отправляет, а
 * называет причину — кнопка при этом не гаснет: погасшая кнопка ничего не объясняет.
 * Недозаполненный запрос можно сохранить черновиком и вернуться к нему позже.
 */
const LEVEL_FORWARD = 'motion-safe:animate-push-in';
const LEVEL_BACK = 'motion-safe:animate-pop-in';
const MAX_EXTRA = 4;

const ASSET_TITLES = { transfer: 'Что передаём', writeoff: 'Что списываем', dismissal: 'ОС сотрудника' };
const COMMENT_FIELDS = {
    transfer: { label: 'Причина', required: false, placeholder: 'Например: сотрудник переехал в другой кабинет' },
    writeoff: { label: 'Причина списания', required: true, placeholder: 'Что с ОС и почему его нельзя использовать' },
    dismissal: { label: 'Комментарий', required: false, placeholder: '' },
    need: { label: 'Зачем', required: true, placeholder: 'Для какой работы нужно ОС' },
};

const itemToAsset = (item) => ({
    id: item.asset_id, name: item.name, inventory_number: item.inventory_number, check_by: item.check_by,
    mol_name: item.before_mol_name, department_name: item.before_department_name, location: item.before_location,
    on_stock: item.before_on_stock, cover_url: item.photos?.[0]?.thumb_url || null,
});

const photosFromCard = (card) => {
    const result = {};
    (card?.items || []).forEach((item) => {
        const own = { extra: [] };
        (item.photos || []).forEach((photo) => {
            if (photo.kind === PHOTO_EXTRA) own.extra.push(photo);
            else own[photo.kind] = photo;
        });
        result[item.asset_id] = own;
    });
    return result;
};

export default function FaRequestForm({ open, kind, card = null, preset = null, api, context, people, toast, onClose, onSaved }) {
    const editing = Boolean(card?.request?.id);
    const [form, setForm] = useState(() => (card ? requestToForm(card) : blankRequest(kind, preset || {})));
    const [assets, setAssets] = useState(() => (card ? (card.items || []).map(itemToAsset) : []));
    const [photos, setPhotos] = useState(() => photosFromCard(card));
    const [removed, setRemoved] = useState([]);
    const [skipped, setSkipped] = useState(0);        // ОС сотрудника, занятые другими запросами
    const [level, setLevel] = useState(null);          // 'pick'
    const [motion, setMotion] = useState('');
    const [pool, setPool] = useState(null);
    const [search, setSearch] = useState('');
    const query = useDebounced(search.trim(), 250);
    const [issues, setIssues] = useState({});
    const [photoIssue, setPhotoIssue] = useState('');
    const [tried, setTried] = useState(false);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState('');
    const bottomRef = useRef(null);

    /* Поле заполнили — его красная рамка и подпись «чего не хватает» уходят сразу. */
    const clearIssues = (keys) => setIssues((prev) => {
        if (!keys.some((key) => prev[key])) return prev;
        const next = { ...prev };
        keys.forEach((key) => { delete next[key]; });
        return next;
    });
    const set = (patch) => { setForm((prev) => ({ ...prev, ...patch })); clearIssues(Object.keys(patch)); };
    const withAssets = hasAssets(kind);
    const moving = isMoving(kind);

    /* ОС, с которым форму открыли из карточки («Переместить», «Списать»). */
    const presetIds = preset?.asset_ids;
    useEffect(() => {
        if (card || !presetIds?.length) return;
        Promise.all(presetIds.map((id) => api.get(`/assets/${id}`).then((response) => response.data.asset)))
            .then((list) => { setAssets(list); set({ asset_ids: list.map((item) => item.id) }); })
            .catch((requestError) => setError(errorText(requestError, 'ОС не загрузилось')));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    /* Увольнение: в передачу входят ВСЕ свободные ОС сотрудника — их подставляет сервер. */
    const leaverId = kind === 'dismissal' ? form.from_user_id : null;
    useEffect(() => {
        if (kind !== 'dismissal' || !leaverId || card) return undefined;
        let alive = true;
        api.get('/dismissals/check', { user_id: leaverId }).then(({ data }) => {
            if (!alive) return;
            const free = (data.assets || []).filter((asset) => !asset.open_request);
            setSkipped((data.assets || []).length - free.length);
            setAssets(free);
            set({ asset_ids: free.map((asset) => asset.id) });
        }).catch((requestError) => alive && setError(errorText(requestError, 'ОС сотрудника не загрузились')));
        return () => { alive = false; };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [leaverId]);

    /* Список для выбора ОС: только свободные и только те, что человек вправе включить. */
    useEffect(() => {
        if (level !== 'pick') return undefined;
        let alive = true;
        api.get('/assets', { pick: kind, ...(query ? { q: query } : {}) })
            .then(({ data }) => alive && setPool(data.items || []))
            .catch((requestError) => alive && setError(errorText(requestError, 'Список ОС не загрузился')));
        return () => { alive = false; };
    }, [api, kind, level, query]);

    const holder = assets[0] || null;
    const toggleAsset = (asset) => {
        setAssets((prev) => {
            const next = prev.some((item) => item.id === asset.id) ? prev.filter((item) => item.id !== asset.id) : [...prev, asset];
            set({ asset_ids: next.map((item) => item.id) });
            return next;
        });
    };
    const dropAsset = (assetId) => {
        const own = photos[assetId] || {};
        const ids = [own.photo_whole?.id, own.photo_number?.id, ...(own.extra || []).map((photo) => photo.id)].filter(Boolean);
        if (ids.length) setRemoved((prev) => [...prev, ...ids]);
        setPhotos((prev) => { const next = { ...prev }; delete next[assetId]; return next; });
        setAssets((prev) => {
            const next = prev.filter((item) => item.id !== assetId);
            set({ asset_ids: next.map((item) => item.id) });
            return next;
        });
    };

    const setSlot = (assetId, slot, value) => {
        setPhotos((prev) => ({ ...prev, [assetId]: { ...(prev[assetId] || {}), [slot]: value } }));
        setPhotoIssue('');
    };
    const clearSlot = (assetId, slot) => {
        const current = photos[assetId]?.[slot];
        if (current?.id) setRemoved((prev) => [...prev, current.id]);
        setSlot(assetId, slot, null);
    };
    const addExtra = (assetId, value) => setPhotos((prev) => {
        const own = prev[assetId] || {};
        return { ...prev, [assetId]: { ...own, extra: [...(own.extra || []), value] } };
    });
    const replaceExtra = (assetId, index, value) => {
        const current = photos[assetId]?.extra?.[index];
        if (current?.id) setRemoved((prev) => [...prev, current.id]);
        setPhotos((prev) => {
            const own = prev[assetId] || {};
            return { ...prev, [assetId]: { ...own, extra: (own.extra || []).map((photo, at) => (at === index ? value : photo)) } };
        });
    };
    const dropExtra = (assetId, index) => {
        const current = photos[assetId]?.extra?.[index];
        if (current?.id) setRemoved((prev) => [...prev, current.id]);
        setPhotos((prev) => {
            const own = prev[assetId] || {};
            return { ...prev, [assetId]: { ...own, extra: (own.extra || []).filter((_, at) => at !== index) } };
        });
    };

    const categoryOptions = useMemo(() => [{ value: '', label: 'Не выбрана' },
        ...(context?.categories || []).map((item) => ({ value: item.id, label: item.name }))], [context]);
    const checkGroups = useMemo(() => [...new Set(assets.map((asset) => asset.check_by).filter(Boolean))], [assets]);

    const save = async (submit) => {
        setError('');
        if (submit) {
            // «Отправить» не срабатывает, пока чего-то не хватает, — и говорит, чего именно.
            const fieldIssues = requestIssues(form, assets);
            const gaps = withAssets ? photoGaps(assets, photos) : [];
            setTried(true);
            setIssues(fieldIssues);
            setPhotoIssue(photoGapText(gaps));
            if (Object.keys(fieldIssues).length || gaps.length) {
                requestAnimationFrame(() => bottomRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }));
                return;
            }
        } else if (kind === 'dismissal' && !form.from_user_id) {
            setIssues({ from_user_id: 'Выберите сотрудника' });
            return;
        }
        const files = [];
        const meta = [];
        assets.forEach((asset) => {
            const own = photos[asset.id] || {};
            PHOTO_SLOTS.forEach((slot) => {
                if (own[slot.kind]?.file) { files.push(own[slot.kind].file); meta.push({ asset_id: asset.id, kind: slot.kind }); }
            });
            (own.extra || []).forEach((photo) => {
                if (photo.file) { files.push(photo.file); meta.push({ asset_id: asset.id, kind: PHOTO_EXTRA }); }
            });
        });
        const body = { ...requestPayload(form), photos: meta, remove_file_ids: removed, submit };
        setBusy(submit ? 'submit' : 'draft');
        try {
            const response = await api.post(editing ? `/requests/${card.request.id}/save` : '/requests', body, files);
            toast(submit ? 'Запрос отправлен' : 'Черновик сохранён', 'success');
            onSaved(response.data);
        } catch (requestError) {
            setIssues(errorFields(requestError));
            setError(errorText(requestError, 'Запрос не сохранён'));
            requestAnimationFrame(() => bottomRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }));
        } finally {
            setBusy('');
        }
    };

    const openPicker = () => { setSearch(''); setPool(null); setMotion(LEVEL_FORWARD); setLevel('pick'); };
    const closePicker = () => { setMotion(LEVEL_BACK); setLevel(null); };
    const visiblePool = useMemo(() => (pool || []).filter((asset) => !holder || asset.mol_user_id === holder.mol_user_id
        || assets.some((item) => item.id === asset.id) || holder.mol_user_id === undefined), [pool, holder, assets]);
    const hiddenByHolder = (pool || []).length - visiblePool.length;

    const comment = COMMENT_FIELDS[kind];
    const footer = level === 'pick' ? (
        <button type="button" className={iosBtnPrimary} onClick={closePicker}>Готово{assets.length ? ` · ${assets.length}` : ''}</button>
    ) : (
        <>
            <button type="button" className={iosBtnSecondary} onClick={() => save(false)} disabled={Boolean(busy)}>
                {busy === 'draft' && <Loader2 size={14} className="animate-spin" />} В черновик
            </button>
            <button type="button" className={iosBtnPrimary} onClick={() => save(true)} disabled={Boolean(busy)}>
                {busy === 'submit' ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />} Отправить
            </button>
        </>
    );

    const assetBlock = (asset) => {
        const own = photos[asset.id] || {};
        const extras = own.extra || [];
        return (
            <div key={asset.id} className={`${SURFACE} p-3.5`}>
                <div className="flex items-center gap-3">
                    <Thumb url={asset.cover_url} size={44} />
                    <div className="min-w-0 flex-1">
                        <div className="truncate text-[14.5px] font-medium text-slate-900">{asset.name}</div>
                        <div className="truncate text-[12.5px] tabular-nums text-slate-500">
                            {[asset.inventory_number, shortName(asset.mol_name)].filter(Boolean).join(' · ')}
                        </div>
                    </div>
                    {kind !== 'dismissal' && (
                        <button type="button" onClick={() => dropAsset(asset.id)} aria-label={`Убрать из запроса: ${asset.name}`}
                                className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-600">
                            <X size={16} />
                        </button>
                    )}
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                    {PHOTO_SLOTS.map((slot) => (
                        <PhotoSlot key={slot.kind} label={slot.label} value={own[slot.kind] || null}
                                   invalid={tried && !own[slot.kind]}
                                   onPick={(value) => setSlot(asset.id, slot.kind, value)}
                                   onClear={() => clearSlot(asset.id, slot.kind)} />
                    ))}
                    {extras.map((photo, index) => (
                        // eslint-disable-next-line react/no-array-index-key
                        <PhotoSlot key={`extra-${index}`} label="Ещё фото" optional value={photo}
                                   onPick={(value) => replaceExtra(asset.id, index, value)} onClear={() => dropExtra(asset.id, index)} />
                    ))}
                    {extras.length < MAX_EXTRA && (
                        <PhotoSlot label="Ещё фото" optional value={null} onPick={(value) => addExtra(asset.id, value)} />
                    )}
                </div>
            </div>
        );
    };

    return (
        <IosModal
            open={open}
            onClose={onClose}
            onBack={level ? closePicker : null}
            title={level === 'pick' ? 'Выбор ОС' : `${KIND_LABELS[kind]}${editing ? ` № ${card.request.id}` : ''}`}
            subtitle={level === 'pick' ? '' : (editing ? 'Черновик' : 'Новый запрос')}
            maxWidth="max-w-2xl"
            footer={footer}
            sizeKey={level || 'form'}
        >
            {level === 'pick' ? (
                <div key="pick" className={`space-y-3 ${motion}`}>
                    <SearchField autoFocus value={search} onChange={setSearch} placeholder="Название, номер, сотрудник" ariaLabel="Поиск ОС" />
                    {holder && hiddenByHolder > 0 && (
                        <Footnote>В одном запросе — ОС одного МОЛ: {shortName(holder.mol_name)}.</Footnote>
                    )}
                    {!pool && <div className="flex items-center justify-center gap-2 py-10 text-[13.5px] text-slate-500"><Loader2 size={15} className="animate-spin" /> Загружаем…</div>}
                    {pool && !visiblePool.length && (
                        <div className="py-10 text-center text-[13.5px] text-slate-500">{query ? 'Ничего не нашлось' : 'Свободных ОС нет'}</div>
                    )}
                    {visiblePool.length > 0 && (
                        <Group>
                            {visiblePool.map((asset) => {
                                const chosen = assets.some((item) => item.id === asset.id);
                                return (
                                    <button key={asset.id} type="button" onClick={() => toggleAsset(asset)} aria-pressed={chosen}
                                            className="flex w-full items-center gap-3 px-4 py-2.5 text-left transition first:rounded-t-2xl last:rounded-b-2xl hover:bg-slate-50">
                                        <Thumb url={asset.cover_url} size={40} />
                                        <span className="min-w-0 flex-1">
                                            <span className="block truncate text-[14.5px] font-medium text-slate-900">{asset.name}</span>
                                            <span className="block truncate text-[12.5px] text-slate-500">
                                                {[asset.inventory_number, shortName(asset.mol_name), placeText(asset)].filter(Boolean).join(' · ')}
                                            </span>
                                        </span>
                                        <span className={`grid h-[22px] w-[22px] shrink-0 place-items-center rounded-full ${chosen ? 'bg-blue-600 text-white' : 'border-2 border-slate-300'}`}>
                                            {chosen && <Check size={13} strokeWidth={3} />}
                                        </span>
                                    </button>
                                );
                            })}
                        </Group>
                    )}
                </div>
            ) : (
                <div key="form" className={`space-y-4 ${motion}`}>
                    {kind === 'dismissal' && (
                        <Field label="Кто увольняется" required error={issues.from_user_id}>
                            <PersonSelect people={people || []} value={form.from_user_id} invalid={Boolean(issues.from_user_id)}
                                          onChange={(value) => { setAssets([]); setPhotos({}); set({ from_user_id: value, asset_ids: [] }); }}
                                          ariaLabel="Сотрудник, который увольняется" />
                        </Field>
                    )}

                    {withAssets && (kind !== 'dismissal' || form.from_user_id) && (
                        <section>
                            <GroupTitle>{ASSET_TITLES[kind]}{assets.length ? ` · ${assets.length}` : ''}</GroupTitle>
                            <div className="space-y-2">
                                {assets.map(assetBlock)}
                                {kind !== 'dismissal' && (
                                    <button type="button" onClick={openPicker}
                                            className={`flex h-12 w-full items-center justify-center gap-2 rounded-2xl text-[14px] font-semibold transition active:scale-[0.99] ${
                                                issues.asset_ids ? 'bg-rose-50 text-rose-600 ring-1 ring-inset ring-rose-200' : 'bg-slate-100 text-slate-700 hover:bg-slate-200'}`}>
                                        <Plus size={16} /> {assets.length ? 'Добавить ОС' : 'Выбрать ОС'}
                                    </button>
                                )}
                                {kind === 'dismissal' && !assets.length && (
                                    <div className="py-6 text-center text-[13.5px] text-slate-500">За сотрудником нет ОС для передачи</div>
                                )}
                            </div>
                            {issues.asset_ids && <div className="mt-1 px-1 text-[12px] text-rose-600">{issues.asset_ids}</div>}
                            {kind === 'dismissal' && skipped > 0 && (
                                <Footnote className="mt-1.5">Ещё {skipped} ОС уже в других запросах.</Footnote>
                            )}
                            {kind === 'writeoff' && checkGroups.length === 1 && (
                                <Footnote className="mt-1.5">Перед подписанием ОС проверит {CHECK_BY_LABELS[checkGroups[0]].toLowerCase()}.</Footnote>
                            )}
                            {kind === 'writeoff' && checkGroups.length > 1 && (
                                <div className="mt-1.5 px-1 text-[12.5px] text-rose-600">Технику и мебель списывают разными запросами: у них разные проверяющие.</div>
                            )}
                        </section>
                    )}

                    {moving && (
                        <section className="space-y-3">
                            <Field label={kind === 'dismissal' ? 'Кому передать' : 'Кому'} required error={issues.to_user_id}>
                                <PersonSelect people={people || []} value={form.to_user_id} invalid={Boolean(issues.to_user_id)}
                                              exclude={kind === 'dismissal' && form.from_user_id ? [form.from_user_id] : []}
                                              onChange={(value) => set({ to_user_id: value })} ariaLabel="Кому передаётся ОС" />
                            </Field>
                            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
                                <Field label="Новое место">
                                    <input className={FIELD} value={form.to_location} maxLength={300}
                                           placeholder={holder?.location ? `Прежнее: ${holder.location}` : 'Город, офис, кабинет'}
                                           onChange={(event) => set({ to_location: event.target.value })} />
                                </Field>
                                <label className="flex h-[41px] items-center gap-2.5 rounded-xl bg-slate-100 px-3.5 text-[14px] text-slate-700">
                                    <IosToggle checked={form.to_stock} onChange={(value) => set({ to_stock: value })} />
                                    На склад
                                </label>
                            </div>
                        </section>
                    )}

                    {kind === 'need' && (
                        <section className="space-y-3">
                            <Field label="Что нужно" required error={issues.need_title}>
                                <input className={`${FIELD} ${issues.need_title ? FIELD_INVALID : ''}`} value={form.need_title} maxLength={300}
                                       placeholder="Например: ноутбук 15″" onChange={(event) => set({ need_title: event.target.value })} />
                            </Field>
                            <div className="grid gap-3 sm:grid-cols-2">
                                <Field label="Кому" required error={issues.need_for_user_id}>
                                    <PersonSelect people={people || []} value={form.need_for_user_id} invalid={Boolean(issues.need_for_user_id)}
                                                  onChange={(value) => set({ need_for_user_id: value })} ariaLabel="Кому нужно ОС" />
                                </Field>
                                <Field label="Категория">
                                    <FormSelect value={form.need_category_id || ''} onChange={(value) => set({ need_category_id: value || null })}
                                                options={categoryOptions} ariaLabel="Категория" />
                                </Field>
                            </div>
                        </section>
                    )}

                    <Field label={comment.label} required={comment.required} error={issues.comment}>
                        <textarea className={`${FIELD} min-h-[76px] resize-y ${issues.comment ? FIELD_INVALID : ''}`} value={form.comment} maxLength={2000}
                                  placeholder={comment.placeholder} onChange={(event) => set({ comment: event.target.value })} />
                    </Field>

                    <div ref={bottomRef} className="space-y-2">
                        <ErrorNote text={photoIssue} />
                        <ErrorNote text={error} />
                    </div>
                </div>
            )}
        </IosModal>
    );
}
