import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Check, Download, FileUp, Loader2, PackagePlus, Pencil, Send, Trash2 } from 'lucide-react';
import { IosModal, iosBtnGhost, iosBtnPrimary, iosBtnSecondary } from '../ui/ios';
import { Loading } from '../hr_office/hrUi';
import {
    CHECK_BY_LABELS, KIND_LABELS, PHOTO_LABELS, STATUS_TONE, errorFields, errorText, fmtDate, fmtDateTime, photoGapText,
    saveBlob, shortName, statusLabel, todayIso,
} from './fixedAssetsMeta';
import {
    ErrorNote, FIELD, FIELD_INVALID, Field, Footnote, FormDate, Group, GroupRow, GroupTitle, IconCircle, KIND_LOOK, PdfPicker,
    PersonRow, PersonSelect, PhotoStrip, SHEET_BODY, SheetTabs, StatusWord, Stepper, Thumb, usePhotoViewer,
} from './fixedAssetsUi';
import FaActBlock from './FaActBlock';
import FaTimeline from './FaTimeline';

/*
 * Карточка запроса. Сверху — вид, номер, статус и путь по шагам; остальное по вкладкам:
 * «Запрос» (кто, кому, зачем), «ОС» (что именно, с фотографиями), «Акт» (блок из ТЗ и кто
 * подписывает) и «История». Что делать дальше, говорит главная кнопка внизу — пояснений на
 * экране нет.
 *
 * Кнопки — по списку действий с сервера (fixed_assets/access.py: allowed_actions): экран не
 * решает, кому что можно. У сотрудника, чьё ОС в запросе, кнопок нет — он только смотрит.
 * Шаг с вводом (скан акта, оригинал, отказ) — второй уровень внутри окна.
 *
 * step — шаг, ради которого карточку открыли с доски (карточку бросили в колонку): окно сразу
 * встаёт на этот шаг. Только если сервер его разрешает — пока карточку несли, запрос мог уйти
 * дальше, и тогда открывается просто карточка.
 */
const LEVEL_FORWARD = 'motion-safe:animate-push-in';
const LEVEL_BACK = 'motion-safe:animate-pop-in';
const DANGER_BTN = `${iosBtnGhost} !text-rose-600 hover:!bg-rose-50`;
const MODE_TITLES = {
    check: 'Проверка перед списанием', scan: 'Скан подписанного акта', receive: 'Оригинал получен',
    reject: 'Отклонить запрос', remove: 'Удалить черновик',
};

const placeLine = (department, location, onStock) => [department, onStock ? 'склад' : '', location].filter(Boolean).join(' · ');

export default function FaRequestCard({ open, requestId, api, people, needPeople, toast, onClose, onChanged, onEdit, onOpenAsset, onAccept,
    step = null }) {
    const [card, setCard] = useState(null);
    const [error, setError] = useState('');
    const [tab, setTab] = useState('main');
    const [mode, setMode] = useState(null);
    const [motion, setMotion] = useState('motion-safe:animate-fade-in-soft');
    const [draft, setDraft] = useState({});
    const [issues, setIssues] = useState({});
    const [busy, setBusy] = useState('');

    const load = useCallback(() => api.get(`/requests/${requestId}`)
        .then((response) => { setCard(response.data); setError(''); })
        .catch((requestError) => setError(errorText(requestError, 'Запрос не загрузился'))), [api, requestId]);
    useEffect(() => { load(); }, [load]);

    const request = card?.request;
    const actions = card?.actions || [];
    const items = card?.items || [];
    const allPhotos = useMemo(() => items.flatMap((item) => (item.photos || []).map((photo) => ({
        ...photo, label: `${item.name} — ${PHOTO_LABELS[photo.kind] || 'фото'}`,
    }))), [items]);
    const { open: openPhoto, viewer } = usePhotoViewer(allPhotos);

    const enter = (next, initial = {}) => {
        if (next === 'receive') needPeople();
        setDraft(initial);
        setIssues({});
        setError('');
        setMotion(LEVEL_FORWARD);
        setMode(next);
    };
    const leave = () => { setMotion(LEVEL_BACK); setMode(null); setError(''); };
    const set = (key, value) => {
        setDraft((prev) => ({ ...prev, [key]: value }));
        setIssues((prev) => (prev[key] ? { ...prev, [key]: '' } : prev));
    };

    /* Шаг запроса: ответ сервера — свежая карточка; списки раздела перечитываются. */
    const act = async (name, work, done, fallback) => {
        setBusy(name);
        setError('');
        try {
            const response = await work();
            if (response?.data?.request) setCard(response.data);
            if (done) toast(done, 'success');
            setMotion(LEVEL_BACK);
            setMode(null);
            onChanged();
            return response;
        } catch (requestError) {
            setIssues(errorFields(requestError));
            setError(errorText(requestError, fallback));
            // Шаг уже сделал кто-то другой — показываем, что стало с запросом.
            if (requestError?.response?.status === 409) load();
            return null;
        } finally {
            setBusy('');
        }
    };

    const submit = () => act('submit', () => api.post(`/requests/${requestId}/submit`), 'Запрос отправлен', 'Запрос не отправлен');
    const confirmCheck = () => act('check', () => api.post(`/requests/${requestId}/check`, { comment: String(draft.comment || '').trim() }),
        'Проверено — запрос ушёл на подписание', 'Не удалось отметить проверку');
    const attachScan = () => {
        const missing = {};
        if (!draft.file) missing.file = 'Приложите скан акта';
        if (!draft.signed_on) missing.signed_on = 'Укажите дату подписания';
        setIssues(missing);
        if (Object.keys(missing).length) return null;
        return act('scan', () => api.post(`/requests/${requestId}/scan`, { signed_on: draft.signed_on }, [draft.file]),
            request.status === 'original' ? 'Скан заменён' : 'Скан приложен — бухгалтерия ждёт оригинал', 'Скан не приложен');
    };
    const receiveOriginal = () => {
        const missing = {};
        if (!draft.handed_by_user_id) missing.handed_by_user_id = 'Выберите, кто передал оригинал';
        if (!draft.handed_on) missing.handed_on = 'Укажите дату';
        setIssues(missing);
        if (Object.keys(missing).length) return null;
        return act('receive', () => api.post(`/requests/${requestId}/original`, {
            handed_by_user_id: draft.handed_by_user_id, handed_on: draft.handed_on,
        }), 'Оригинал получен — запрос закрыт', 'Не удалось закрыть запрос');
    };
    const reject = () => {
        if (String(draft.reason || '').trim().length < 3) { setIssues({ reason: 'Напишите причину' }); return null; }
        return act('reject', () => api.post(`/requests/${requestId}/reject`, { reason: draft.reason.trim() }),
            'Запрос отклонён', 'Не удалось отклонить');
    };
    const remove = async () => {
        setBusy('remove');
        try {
            await api.del(`/requests/${requestId}`);
            toast('Черновик удалён', 'success');
            onChanged();
            onClose();
        } catch (requestError) {
            setError(errorText(requestError, 'Не удалось удалить'));
        } finally {
            setBusy('');
        }
    };
    const downloadAct = async () => {
        setBusy('act');
        try {
            saveBlob(await api.blob(`/requests/${requestId}/act`), `Акт № ${requestId}.docx`);
        } catch (requestError) {
            toast(errorText(requestError, 'Акт не собрался'), 'error');
        } finally {
            setBusy('');
        }
    };

    const receiveDraft = () => ({ handed_by_user_id: request.scan_by || request.from_user_id || request.to_user_id || null,
        handed_on: todayIso() });
    const startStep = {
        check: () => enter('check', { comment: '' }),
        scan: () => enter('scan', { file: null, signed_on: todayIso() }),
        receive: () => enter('receive', receiveDraft()),
        reject: () => enter('reject', { reason: '' }),
        submit: () => submit(),
        accept: () => onAccept(card),
    };
    const stepTaken = useRef(false);
    useEffect(() => {
        if (!step || stepTaken.current || !card) return;
        stepTaken.current = true;
        if ((card.actions || []).includes(step)) startStep[step]?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [card, step]);

    const spinner = (name) => (busy === name ? <Loader2 size={14} className="animate-spin" /> : null);
    const gapText = card?.photo_gaps?.length ? photoGapText(card.photo_gaps.map((gap) => ({ asset: gap, missing: gap.missing }))) : '';
    const missing = Object.values(card?.missing || {});
    const moving = request && (request.kind === 'transfer' || request.kind === 'dismissal');
    const closesText = !request ? ''
        : moving ? 'Запрос закроется, ОС перейдёт к новому МОЛ.'
            : request.kind === 'writeoff' ? 'Запрос закроется, ОС получит статус «Списано».'
                : 'Запрос закроется.';
    const hasAct = Boolean(request?.act?.act_date) && request.status !== 'draft';
    const tabs = request ? [
        { value: 'main', label: 'Запрос' },
        items.length > 0 && { value: 'items', label: 'ОС', count: items.length },
        hasAct && { value: 'act', label: 'Акт' },
        (card.events || []).length > 0 && { value: 'history', label: 'История' },
    ].filter(Boolean) : [];
    const shownTab = tabs.some((item) => item.value === tab) ? tab : 'main';
    // Поимённые подписанты; строка «Бухгалтер» без фамилии остаётся только на бумаге.
    const signers = (request?.signers || []).filter((signer) => signer.name);

    let footer = null;
    const cancel = <button type="button" className={iosBtnSecondary} onClick={leave} disabled={Boolean(busy)}>Отмена</button>;
    if (mode === 'check') {
        footer = <>{cancel}<button type="button" className={iosBtnPrimary} onClick={confirmCheck} disabled={Boolean(busy)}>{spinner('check')} Проверено</button></>;
    } else if (mode === 'scan') {
        footer = (
            <>
                {cancel}
                <button type="button" className={iosBtnPrimary} onClick={attachScan} disabled={Boolean(busy)}>
                    {spinner('scan')} {request?.status === 'original' ? 'Заменить скан' : 'Приложить скан'}
                </button>
            </>
        );
    } else if (mode === 'receive') {
        footer = <>{cancel}<button type="button" className={iosBtnPrimary} onClick={receiveOriginal} disabled={Boolean(busy)}>{spinner('receive')} Оригинал получен</button></>;
    } else if (mode === 'reject') {
        footer = <>{cancel}<button type="button" className={`${iosBtnPrimary} !bg-rose-600 hover:!bg-rose-700`} onClick={reject} disabled={Boolean(busy)}>{spinner('reject')} Отклонить</button></>;
    } else if (mode === 'remove') {
        footer = <>{cancel}<button type="button" className={`${iosBtnPrimary} !bg-rose-600 hover:!bg-rose-700`} onClick={remove} disabled={Boolean(busy)}>{spinner('remove')} Удалить</button></>;
    } else if (request && actions.some((code) => code !== 'act')) {
        footer = (
            <>
                <div className="mr-auto flex flex-wrap items-center gap-1">
                    {actions.includes('reject') && <button type="button" className={DANGER_BTN} onClick={() => enter('reject', { reason: '' })}>Отклонить</button>}
                    {actions.includes('delete') && <button type="button" className={DANGER_BTN} onClick={() => enter('remove')}><Trash2 size={14} /> Удалить</button>}
                </div>
                {actions.includes('edit') && <button type="button" className={iosBtnSecondary} onClick={() => onEdit(card)}><Pencil size={14} /> Изменить</button>}
                {actions.includes('act') && actions.includes('scan') && (
                    <button type="button" className={iosBtnSecondary} onClick={downloadAct} disabled={busy === 'act'}>
                        {busy === 'act' ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />} Скачать акт
                    </button>
                )}
                {actions.includes('submit') && <button type="button" className={iosBtnPrimary} onClick={submit} disabled={Boolean(busy)}>{spinner('submit') || <Send size={14} />} Отправить</button>}
                {actions.includes('check') && <button type="button" className={iosBtnPrimary} onClick={() => enter('check', { comment: '' })}><Check size={14} /> Проверено</button>}
                {actions.includes('accept') && <button type="button" className={iosBtnPrimary} onClick={() => onAccept(card)}><PackagePlus size={14} /> Принять в учёт</button>}
                {actions.includes('scan') && <button type="button" className={iosBtnPrimary} onClick={() => enter('scan', { file: null, signed_on: todayIso() })}><FileUp size={14} /> Приложить скан</button>}
                {actions.includes('receive') && (
                    <button type="button" className={iosBtnPrimary} onClick={startStep.receive}>
                        <Check size={14} /> Оригинал получен
                    </button>
                )}
            </>
        );
    }

    const [KindIcon, kindTone] = KIND_LOOK[request?.kind] || KIND_LOOK.transfer;
    const sender = request ? [request.from_name || items[0]?.before_mol_name, request.from_department_name || items[0]?.before_department_name] : [];

    return (
        <>
            <IosModal
                open={open}
                onClose={onClose}
                onBack={mode ? leave : null}
                title={mode ? MODE_TITLES[mode] : 'Запрос'}
                subtitle={mode && request ? `${KIND_LABELS[request.kind]} № ${request.id}` : ''}
                maxWidth="max-w-2xl"
                footer={footer}
                sizeKey={request ? (mode || `view-${shownTab}`) : 'loading'}
            >
                <div className="space-y-4">
                    <ErrorNote text={error} />
                    {!card && !error && <Loading text="Загружаем запрос…" />}

                    {request && !mode && (
                        <div key="view" className={`space-y-4 ${motion}`}>
                            <div className="flex items-center gap-3.5">
                                <IconCircle icon={KindIcon} tone={kindTone} size={46} />
                                <div className="min-w-0 flex-1">
                                    <div className="text-[19px] font-bold leading-snug tracking-tight text-slate-900">
                                        {KIND_LABELS[request.kind]} <span className="font-semibold text-slate-400">№ {request.id}</span>
                                    </div>
                                    <StatusWord tone={STATUS_TONE[request.status]}>{statusLabel(request)}</StatusWord>
                                </div>
                            </div>

                            <Stepper steps={request.track || []} />

                            <SheetTabs value={shownTab} onChange={setTab} options={tabs} ariaLabel="Раздел карточки запроса" />

                            <div className={SHEET_BODY}>
                            {shownTab === 'main' && (
                                <div className="space-y-3">
                                    {request.status === 'rejected' && (
                                        <Group>
                                            <GroupRow label="Причина отказа" stacked>{request.reject_reason}</GroupRow>
                                            <GroupRow label="Отклонил">{`${shortName(request.rejected_by_name)}, ${fmtDateTime(request.rejected_at)}`}</GroupRow>
                                        </Group>
                                    )}
                                    {/* Отказ сервера уже сказал то же самое сверху — второй раз не пишем. */}
                                    {request.status === 'draft' && !error && (missing.length > 0 || gapText) && (
                                        <ErrorNote text={[...missing, gapText].filter(Boolean).join(' ')} />
                                    )}

                                    <Group>
                                        {moving && (
                                            <>
                                                <PersonRow label="Сдаёт" name={sender[0]} note={sender[1]} />
                                                <PersonRow label="Принимает" name={request.to_name} note={request.to_department_name} />
                                                <GroupRow label="Новое место">{placeLine('', request.to_location, request.to_stock)}</GroupRow>
                                                <GroupRow label="Причина" stacked>{request.comment}</GroupRow>
                                            </>
                                        )}
                                        {request.kind === 'writeoff' && (
                                            <>
                                                <PersonRow label="МОЛ" name={sender[0]} note={sender[1]} />
                                                <GroupRow label="Причина списания" stacked>{request.comment}</GroupRow>
                                                {request.checked_by_name
                                                    ? <GroupRow label="Проверил">{`${shortName(request.checked_by_name)}, ${fmtDate(request.checked_at)}`}</GroupRow>
                                                    : (request.status === 'review' && <GroupRow label="Проверяет">{request.review_by === 'accounting' ? 'Бухгалтерия' : CHECK_BY_LABELS[request.review_by]}</GroupRow>)}
                                                <GroupRow label="Заключение" stacked>{request.check_comment}</GroupRow>
                                            </>
                                        )}
                                        {request.kind === 'intake' && (
                                            <>
                                                <PersonRow label="МОЛ" name={request.to_name} note={request.to_department_name} />
                                                <GroupRow label="Место">{placeLine('', request.to_location, request.to_stock)}</GroupRow>
                                            </>
                                        )}
                                        {request.kind === 'need' && (
                                            <>
                                                <GroupRow label="Что нужно" stacked>{request.need_title}</GroupRow>
                                                <PersonRow label="Кому" name={request.need_for_name} />
                                                <GroupRow label="Категория">{request.need_category_name}</GroupRow>
                                                <GroupRow label="Зачем" stacked>{request.comment}</GroupRow>
                                            </>
                                        )}
                                    </Group>

                                    <Group>
                                        <GroupRow label="Автор">{request.created_by_name}</GroupRow>
                                        <GroupRow label="Отправлен">{request.submitted_at ? fmtDateTime(request.submitted_at) : ''}</GroupRow>
                                        <GroupRow label="Закрыт">{request.closed_at ? fmtDateTime(request.closed_at) : ''}</GroupRow>
                                    </Group>
                                </div>
                            )}

                            {shownTab === 'items' && (
                                <div className="space-y-3">
                                    {items.map((item) => (
                                        <Group key={item.asset_id}>
                                            <button type="button" onClick={() => onOpenAsset(item.asset_id)}
                                                    className="flex w-full items-center gap-3 rounded-t-2xl px-4 py-3 text-left transition hover:bg-slate-50">
                                                <Thumb url={item.photos?.[0]?.thumb_url} size={44} />
                                                <span className="min-w-0 flex-1">
                                                    <span className="block truncate text-[14.5px] font-medium text-slate-900">{item.name}</span>
                                                    <span className="block truncate text-[12.5px] tabular-nums text-slate-500">
                                                        {[item.inventory_number, request.kind !== 'intake' && placeLine('', item.before_location, item.before_on_stock)].filter(Boolean).join(' · ')}
                                                    </span>
                                                </span>
                                            </button>
                                            {(item.photos || []).length > 0 && (
                                                <div className="px-4 py-3">
                                                    <PhotoStrip photos={item.photos} onOpen={openPhoto} size={72} labels={PHOTO_LABELS} />
                                                </div>
                                            )}
                                        </Group>
                                    ))}
                                </div>
                            )}

                            {shownTab === 'act' && hasAct && (
                                <div className="space-y-4">
                                    <FaActBlock act={request.act} requestId={request.id} api={api} toast={toast}
                                                canDownload={actions.includes('act')} rejected={request.status === 'rejected'}
                                                onReplace={actions.includes('rescan')
                                                    ? () => enter('scan', { file: null, signed_on: String(request.signed_on || todayIso()).slice(0, 10) })
                                                    : null} />
                                    {signers.length > 0 && (
                                        <section>
                                            <GroupTitle>Подписывают на бумаге</GroupTitle>
                                            <Group>
                                                {signers.map((signer) => (
                                                    <GroupRow key={`${signer.role}-${signer.name}`} label={signer.role}>{signer.name}</GroupRow>
                                                ))}
                                            </Group>
                                        </section>
                                    )}
                                </div>
                            )}

                            {shownTab === 'history' && <FaTimeline events={card.events} requestRef={false} />}
                            </div>
                        </div>
                    )}

                    {request && mode === 'check' && (
                        <div key="check" className={`space-y-3 ${motion}`}>
                            <Field label="Заключение">
                                <textarea className={`${FIELD} min-h-[96px] resize-y`} value={draft.comment || ''} maxLength={2000} autoFocus
                                          onChange={(event) => set('comment', event.target.value)} placeholder="Что с ОС — попадёт в акт на списание" />
                            </Field>
                            <Footnote>Запрос уйдёт на подписание акта.</Footnote>
                        </div>
                    )}

                    {request && mode === 'scan' && (
                        <div key="scan" className={`space-y-3 ${motion}`}>
                            <Field label="Скан акта с подписями" required error={issues.file}>
                                <PdfPicker file={draft.file} onChange={(file) => set('file', file)} invalid={Boolean(issues.file)} />
                            </Field>
                            <Field label="Дата подписания" required error={issues.signed_on}>
                                <FormDate value={draft.signed_on || ''} onChange={(value) => set('signed_on', value || '')} max={todayIso()}
                                          invalid={Boolean(issues.signed_on)} ariaLabel="Дата подписания" />
                            </Field>
                            <Footnote>
                                {request.status === 'original' ? 'Прежний скан останется в истории.' : 'Бумажный оригинал передайте в бухгалтерию.'}
                            </Footnote>
                        </div>
                    )}

                    {request && mode === 'receive' && (
                        <div key="receive" className={`space-y-3 ${motion}`}>
                            <Field label="Кто передал оригинал" required error={issues.handed_by_user_id}>
                                {people ? (
                                    <PersonSelect people={people} value={draft.handed_by_user_id} onChange={(value) => set('handed_by_user_id', value)}
                                                  invalid={Boolean(issues.handed_by_user_id)} ariaLabel="Кто передал оригинал" />
                                ) : <div className="px-1 text-[13px] text-slate-500">Загружаем сотрудников…</div>}
                            </Field>
                            <Field label="Дата передачи в бухгалтерию" required error={issues.handed_on}>
                                <FormDate value={draft.handed_on || ''} onChange={(value) => set('handed_on', value || '')} max={todayIso()}
                                          invalid={Boolean(issues.handed_on)} ariaLabel="Дата передачи в бухгалтерию" />
                            </Field>
                            <Footnote>{closesText}</Footnote>
                        </div>
                    )}

                    {request && mode === 'reject' && (
                        <div key="reject" className={`space-y-3 ${motion}`}>
                            <Field label="Причина" required error={issues.reason}>
                                <textarea className={`${FIELD} min-h-[96px] resize-y ${issues.reason ? FIELD_INVALID : ''}`} value={draft.reason || ''} maxLength={2000}
                                          autoFocus onChange={(event) => set('reason', event.target.value)} />
                            </Field>
                            <Footnote>ОС останется у прежнего МОЛ. Отклонённый запрос не возобновляется.</Footnote>
                        </div>
                    )}

                    {request && mode === 'remove' && (
                        <div key="remove" className={motion}>
                            <Footnote>Черновик и его фото будут удалены.</Footnote>
                        </div>
                    )}
                </div>
            </IosModal>
            {viewer}
        </>
    );
}
