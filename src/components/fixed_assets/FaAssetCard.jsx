import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowRightLeft, ChevronRight, Loader2, PackageX, Pencil } from 'lucide-react';
import { IosModal, iosBtnPrimary, iosBtnSecondary } from '../ui/ios';
import { Loading } from '../hr_office/hrUi';
import useIsMobileShell from '../common/useIsMobileShell';
import {
    KIND_LABELS, PHOTO_LABELS, STATUS_SHORT, STATUS_TONE, assetState, errorFields, errorText, fmtDate, fmtMoney, placeText,
} from './fixedAssetsMeta';
import {
    ErrorNote, FIELD, FIELD_INVALID, Field, Footnote, FormDate, FormSelect, Group, GroupRow, LEAD_ROW, PersonRow, PhotoStrip,
    SHEET_BODY, SheetTabs, StatusWord, Thumb, usePhotoViewer,
} from './fixedAssetsUi';
import FaActBlock from './FaActBlock';
import FaTimeline from './FaTimeline';
import { BarcodeField } from './FaScanner';

/*
 * Карточка ОС. Сверху — что это: снимок, название, номер и состояние. Остальное разложено по
 * вкладкам, чтобы экран не был стеной строк: «Сведения» (за кем и где), «Фото», «Акты» (блок
 * акта из ТЗ у каждой операции — приёма, перемещения, списания) и «История».
 *
 * МОЛ, место и статус здесь не правятся — их меняет только закрытый запрос («нет обхода»).
 * Опечатку в описании бухгалтерия исправляет, пока не закрыт акт приёма, — с причиной и
 * строкой в истории. Штрихкод наклейки бухгалтерия привязывает в любой момент, пока ОС на учёте:
 * наклейки клеят и на ОС, принятые раньше. Сотрудник видит своё ОС без кнопок и ту часть истории,
 * где участвует сам.
 */
const LEVEL_FORWARD = 'motion-safe:animate-push-in';
const LEVEL_BACK = 'motion-safe:animate-pop-in';

export default function FaAssetCard({ open, assetId, api, context, toast, onClose, onChanged, onOpenRequest, onRequest }) {
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [tab, setTab] = useState('info');
    const [expanded, setExpanded] = useState(null);     // запрос, чей акт раскрыт во вкладке «Акты»
    const [mode, setMode] = useState(null);             // 'correct' | 'barcode'
    const [motion, setMotion] = useState('motion-safe:animate-fade-in-soft');
    const [draft, setDraft] = useState({});
    const [issues, setIssues] = useState({});
    const [busy, setBusy] = useState(false);
    const mobile = useIsMobileShell();

    const load = useCallback(() => api.get(`/assets/${assetId}`)
        .then((response) => { setData(response.data); setError(''); })
        .catch((requestError) => setError(errorText(requestError, 'Карточка не загрузилась'))), [api, assetId]);
    useEffect(() => { load(); }, [load]);

    const asset = data?.asset;
    const level = data?.level;
    const photos = useMemo(() => (data?.photos || []).map((photo) => ({ ...photo, label: PHOTO_LABELS[photo.kind] })), [data]);
    const operations = data?.operations || [];
    const { open: openPhoto, viewer } = usePhotoViewer(photos);
    const state = assetState(asset);
    const seesCost = Boolean(context.capabilities?.sees_cost);
    const categoryOptions = useMemo(() => (context.categories || []).map((item) => ({ value: item.id, label: item.name })), [context.categories]);

    const tabs = [
        { value: 'info', label: 'Сведения' },
        photos.length > 0 && { value: 'photos', label: 'Фото', count: photos.length },
        operations.length > 0 && { value: 'acts', label: 'Акты', count: operations.length },
        level && level !== 'brief' && { value: 'history', label: 'История' },
    ].filter(Boolean);
    const shownTab = tabs.some((item) => item.value === tab) ? tab : 'info';
    const openAct = expanded ?? operations[0]?.request.id ?? null;

    const startCorrect = () => {
        setDraft({
            name: asset.name || '', category_id: asset.category_id, inventory_number: asset.inventory_number || '',
            serial_number: asset.serial_number || '', cost: asset.cost ?? '', acquired_on: String(asset.acquired_on || '').slice(0, 10),
            note: asset.note || '', reason: '',
        });
        setIssues({});
        setError('');
        setMotion(LEVEL_FORWARD);
        setMode('correct');
    };
    const startBarcode = () => {
        setDraft({ barcode: asset.barcode || '' });
        setIssues({});
        setError('');
        setMotion(LEVEL_FORWARD);
        setMode('barcode');
    };
    const leave = () => { setMotion(LEVEL_BACK); setMode(null); setError(''); };
    const set = (key, value) => {
        setDraft((prev) => ({ ...prev, [key]: value }));
        setIssues((prev) => (prev[key] ? { ...prev, [key]: '' } : prev));
    };

    const saveCorrection = async () => {
        const missing = {};
        if (!String(draft.name).trim()) missing.name = 'Назовите ОС';
        if (!String(draft.inventory_number).trim()) missing.inventory_number = 'Нужен инвентарный номер';
        if (!String(draft.reason).trim()) missing.reason = 'Напишите, что исправляете и почему';
        setIssues(missing);
        if (Object.keys(missing).length) return;
        setBusy(true);
        setError('');
        try {
            const body = {
                name: draft.name, category_id: draft.category_id, inventory_number: draft.inventory_number,
                serial_number: draft.serial_number, acquired_on: draft.acquired_on || null, note: draft.note,
                reason: draft.reason,
            };
            if (seesCost) body.cost = draft.cost === '' ? null : draft.cost;
            await api.patch(`/assets/${assetId}`, body);
            toast('Карточка исправлена', 'success');
            await load();
            leave();
            onChanged();
        } catch (requestError) {
            setIssues(errorFields(requestError));
            setError(errorText(requestError, 'Не удалось сохранить'));
        } finally {
            setBusy(false);
        }
    };

    const removing = mode === 'barcode' && !String(draft.barcode || '').trim() && Boolean(asset?.barcode);
    const saveBarcode = async () => {
        setBusy(true);
        setError('');
        try {
            await api.post(`/assets/${assetId}/barcode`, { barcode: String(draft.barcode || '').trim() || null });
            toast(removing ? 'Штрихкод снят' : 'Штрихкод привязан', 'success');
            await load();
            leave();
            onChanged();
        } catch (requestError) {
            if (requestError?.response?.data?.code === 'FA_UNCHANGED') {
                leave();
            } else {
                setIssues(errorFields(requestError));
                setError(errorText(requestError, 'Не удалось сохранить'));
            }
        } finally {
            setBusy(false);
        }
    };

    const canRequest = data?.can_request || [];
    let footer = null;
    if (mode === 'correct' || mode === 'barcode') {
        footer = (
            <>
                <button type="button" className={iosBtnSecondary} onClick={leave} disabled={busy}>Отмена</button>
                <button type="button" className={iosBtnPrimary} onClick={mode === 'correct' ? saveCorrection : saveBarcode} disabled={busy}>
                    {busy && <Loader2 size={14} className="animate-spin" />} {removing ? 'Снять штрихкод' : 'Сохранить'}
                </button>
            </>
        );
    } else if (asset && (canRequest.length > 0 || data?.can_correct)) {
        footer = (
            <>
                {data?.can_correct && (
                    <button type="button" className={`${iosBtnSecondary} mr-auto`} onClick={startCorrect}><Pencil size={14} /> Исправить</button>
                )}
                {canRequest.includes('writeoff') && (
                    <button type="button" className={iosBtnSecondary} onClick={() => onRequest('writeoff', { asset_ids: [asset.id] })}>
                        <PackageX size={14} /> Списать
                    </button>
                )}
                {canRequest.includes('transfer') && (
                    <button type="button" className={iosBtnPrimary} onClick={() => onRequest('transfer', { asset_ids: [asset.id] })}>
                        <ArrowRightLeft size={14} /> Переместить
                    </button>
                )}
            </>
        );
    }

    const stateLine = () => {
        if (asset.status !== 'written_off' && asset.open_request) {
            const text = `${KIND_LABELS[asset.open_request.kind]} № ${asset.open_request.id} · ${STATUS_SHORT[asset.open_request.status]}`;
            // IT видит только «что, где и за кем»: чужой запрос ему не откроется — без ссылки.
            if (level === 'brief') return <StatusWord tone="amber">{text}</StatusWord>;
            return (
                <button type="button" onClick={() => onOpenRequest(asset.open_request.id)}
                        className="inline-flex max-w-full items-center gap-0.5 text-left text-[13px] font-semibold text-amber-700 hover:underline">
                    <span className="truncate">{text}</span><ChevronRight size={15} className="shrink-0" />
                </button>
            );
        }
        return <StatusWord tone={state.tone}>{state.label}</StatusWord>;
    };

    return (
        <>
            <IosModal
                open={open}
                onClose={onClose}
                onBack={mode ? leave : null}
                title={({ correct: 'Исправление карточки', barcode: 'Штрихкод наклейки' })[mode] || 'Основное средство'}
                maxWidth="max-w-2xl"
                footer={footer}
                sizeKey={asset ? (mode || `view-${shownTab}`) : 'loading'}
            >
                <div className="space-y-4">
                    <ErrorNote text={error} />
                    {!data && !error && <Loading text="Загружаем карточку…" />}

                    {asset && !mode && (
                        <div key="view" className={`space-y-4 ${motion}`}>
                            <div className="grid items-start gap-3.5" style={LEAD_ROW}>
                                <button type="button" disabled={!photos.length} onClick={() => photos[0] && openPhoto(photos[0].id)}
                                        className="transition active:scale-[0.97] disabled:cursor-default" aria-label="Открыть фото">
                                    <Thumb url={photos[0]?.thumb_url || asset.cover_url} size={76} className={`!rounded-[18px] ${asset.status === 'written_off' ? 'grayscale' : ''}`} />
                                </button>
                                <div className="min-w-0 pt-0.5">
                                    <div className="text-[19px] font-bold leading-snug tracking-tight text-slate-900">{asset.name}</div>
                                    <div className="mt-0.5 truncate text-[13px] tabular-nums text-slate-500">
                                        {[asset.inventory_number, asset.category_name].filter(Boolean).join(' · ')}
                                    </div>
                                    <div className="mt-1.5">{stateLine()}</div>
                                </div>
                            </div>

                            <SheetTabs value={shownTab} onChange={setTab} options={tabs} ariaLabel="Раздел карточки ОС" />

                            <div className={SHEET_BODY}>
                            {shownTab === 'info' && (
                                <div className="space-y-3">
                                    <Group>
                                        <PersonRow label="МОЛ" name={asset.mol_name} note={asset.department_name} />
                                        <GroupRow label="Место">{placeText(asset)}</GroupRow>
                                    </Group>
                                    <Group>
                                        {(asset.barcode || data.can_set_barcode) && (
                                            <div className="flex min-h-[44px] items-center justify-between gap-4 px-4 py-2">
                                                <span className="shrink-0 text-[14px] text-slate-500">Штрихкод</span>
                                                <span className="flex min-w-0 items-center gap-3">
                                                    {asset.barcode && <span className="truncate text-[14px] tabular-nums text-slate-900">{asset.barcode}</span>}
                                                    {data.can_set_barcode && (
                                                        <button type="button" onClick={startBarcode}
                                                                className="shrink-0 rounded-lg text-[14px] font-semibold text-blue-600 hover:underline">
                                                            {asset.barcode ? 'Изменить' : 'Привязать'}
                                                        </button>
                                                    )}
                                                </span>
                                            </div>
                                        )}
                                        <GroupRow label="Серийный номер">{asset.serial_number}</GroupRow>
                                        <GroupRow label="Принято к учёту">{fmtDate(asset.acquired_on)}</GroupRow>
                                        {level === 'full' && <GroupRow label="Стоимость">{fmtMoney(asset.cost)}</GroupRow>}
                                        {asset.written_off_at && <GroupRow label="Списано">{fmtDate(asset.written_off_at)}</GroupRow>}
                                        <GroupRow label="Примечание" stacked>{asset.note}</GroupRow>
                                    </Group>
                                </div>
                            )}

                            {shownTab === 'photos' && <PhotoStrip grid photos={photos} onOpen={openPhoto} labels={PHOTO_LABELS} />}

                            {shownTab === 'acts' && (
                                <div className="space-y-3">
                                    {operations.map((operation) => {
                                        const request = operation.request;
                                        const shown = openAct === request.id;
                                        return (
                                            <section key={request.id}>
                                                <button type="button" aria-expanded={shown} onClick={() => setExpanded(shown ? 0 : request.id)}
                                                        className="flex w-full items-center gap-1.5 rounded-xl px-1 py-1.5 text-left transition hover:bg-slate-100">
                                                    <ChevronRight size={16} className={`shrink-0 text-slate-400 transition-transform ${shown ? 'rotate-90' : ''}`} />
                                                    <span className="min-w-0 flex-1 truncate text-[14.5px] font-semibold text-slate-900">
                                                        {KIND_LABELS[request.kind]} № {request.id}
                                                    </span>
                                                    <StatusWord tone={STATUS_TONE[request.status]} className="shrink-0">{STATUS_SHORT[request.status]}</StatusWord>
                                                </button>
                                                {shown && (
                                                    <div className="mt-1.5 space-y-1.5">
                                                        {/* До шага «На подписании» акта ещё нет — показывать пустой блок незачем. */}
                                                        {operation.act?.act_date && (
                                                            <FaActBlock act={operation.act} requestId={request.id} api={api} toast={toast}
                                                                        canDownload={request.status !== 'rejected'}
                                                                        rejected={request.status === 'rejected'} />
                                                        )}
                                                        {request.status === 'rejected' && operation.reject_reason && (
                                                            <Footnote>Причина отказа: {operation.reject_reason}</Footnote>
                                                        )}
                                                        <button type="button" onClick={() => onOpenRequest(request.id)}
                                                                className="rounded-lg px-1 py-1 text-[13px] font-semibold text-blue-600 hover:underline">
                                                            Открыть запрос
                                                        </button>
                                                    </div>
                                                )}
                                            </section>
                                        );
                                    })}
                                </div>
                            )}

                            {shownTab === 'history' && <FaTimeline events={data.events || []} onOpenRequest={onOpenRequest} />}
                            </div>
                        </div>
                    )}

                    {asset && mode === 'barcode' && (
                        <div key="barcode" className={`space-y-3 ${motion}`}>
                            <div className="truncate text-[14px] text-slate-500">
                                {[asset.name, asset.inventory_number].filter(Boolean).join(' · ')}
                            </div>
                            <Field label="Штрихкод наклейки" error={issues.barcode}>
                                <BarcodeField value={draft.barcode} onChange={(value) => set('barcode', value)}
                                              invalid={Boolean(issues.barcode)} autoFocus={!mobile} />
                            </Field>
                            <Footnote>Один штрихкод — одно ОС. Чтобы снять штрихкод, очистите поле.</Footnote>
                        </div>
                    )}

                    {asset && mode === 'correct' && (
                        <div key="correct" className={`space-y-3 ${motion}`}>
                            <div className="grid gap-3 sm:grid-cols-2">
                                <Field label="Наименование" required error={issues.name}>
                                    <input className={`${FIELD} ${issues.name ? FIELD_INVALID : ''}`} value={draft.name} maxLength={300}
                                           onChange={(event) => set('name', event.target.value)} />
                                </Field>
                                <Field label="Категория" required>
                                    <FormSelect value={draft.category_id} onChange={(value) => set('category_id', value)} options={categoryOptions} ariaLabel="Категория" />
                                </Field>
                            </div>
                            <div className="grid gap-3 sm:grid-cols-2">
                                <Field label="Инвентарный номер" required error={issues.inventory_number}>
                                    <input className={`${FIELD} ${issues.inventory_number ? FIELD_INVALID : ''}`} value={draft.inventory_number} maxLength={64}
                                           onChange={(event) => set('inventory_number', event.target.value)} />
                                </Field>
                                <Field label="Серийный номер">
                                    <input className={FIELD} value={draft.serial_number} maxLength={120} onChange={(event) => set('serial_number', event.target.value)} />
                                </Field>
                            </div>
                            <div className="grid gap-3 sm:grid-cols-2">
                                <Field label="Дата принятия к учёту">
                                    <FormDate value={draft.acquired_on} onChange={(value) => set('acquired_on', value || '')} ariaLabel="Дата принятия к учёту" />
                                </Field>
                                {seesCost && (
                                    <Field label="Стоимость, ₸">
                                        <input className={`${FIELD} tabular-nums`} inputMode="decimal" value={draft.cost}
                                               onChange={(event) => set('cost', event.target.value.replace(/[^\d.,\s]/g, ''))} />
                                    </Field>
                                )}
                            </div>
                            <Field label="Примечание">
                                <input className={FIELD} value={draft.note} maxLength={2000} onChange={(event) => set('note', event.target.value)} />
                            </Field>
                            <Field label="Что исправлено и почему" required error={issues.reason}>
                                <input className={`${FIELD} ${issues.reason ? FIELD_INVALID : ''}`} value={draft.reason} maxLength={500}
                                       onChange={(event) => set('reason', event.target.value)} placeholder="Например: опечатка в серийном номере" />
                            </Field>
                            <Footnote>МОЛ, место и статус меняют только запросы.</Footnote>
                        </div>
                    )}
                </div>
            </IosModal>
            {viewer}
        </>
    );
}
