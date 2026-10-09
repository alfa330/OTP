import React, { useMemo, useState } from 'react';
import { AlertTriangle, Check, Loader2, Pencil } from 'lucide-react';
import { iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosInput, IosSegmented } from '../ui/ios';
import IosDatePicker from '../ui/DatePicker';
import PaymentAssetsEditor, { assetGaps, assetPayload, assetsFromRequest } from './PaymentAssetsEditor';
import {
    CARD_RECIPIENT_LABELS, DOCS_STATUS_META, HINTS, fileKindOptions, fmtDate, fmtMoney, parseAmount, quantityText,
    todayISO,
} from './paymentsMeta';
import {
    AmountInput, CardNumber, DATE_TRIGGER, Field, FileChips, FilePicker, FormSelect, NoticeBox, Row, TonePill,
} from './paymentsUi';

/*
 * Форма действия по текущей подзадаче заявки — у каждого этапа своя
 * (ТЗ «Закуп и оплата», пп. 5, 7–11, 14).
 *
 * Три правила, по которым она собрана.
 *
 * 1. НУЖНОЕ ДЛЯ РЕШЕНИЯ — ПОД РУКОЙ. Бухгалтеру — то, что он обязан проверить
 *    (п. 8: поставщик, БИН, реквизиты, компания-плательщик, сумма, НДС, номер и
 *    дата счёта, договор, история оплат) и предупреждение о дубле; финансовому
 *    отделу — получатель, карта, сумма и назначение. Это строки и кнопки-файлы,
 *    а не копия разделов карточки.
 *
 * 2. ЧТО ОСТАЛОСЬ СДЕЛАТЬ — СЛОВАМИ У КНОПКИ. Кнопка действия неактивна, пока
 *    не заполнено обязательное, и рядом написано, чего именно не хватает.
 *    Сервер проверяет то же самое (workflow.missing_for_action).
 *
 * 3. КОММЕНТАРИЙ ОБЯЗАТЕЛЕН ТАМ, ГДЕ ЕГО ТРЕБУЕТ ТЗ: возврат на доработку и
 *    отклонение (п. 7), «Запросить информацию» — причина из списка (п. 8).
 *
 * Состояние формы живёт здесь и сбрасывается сменой этапа: карточка монтирует
 * компонент с key из номера заявки и вида подзадачи.
 */

const LOCAL_STATUS_LABELS = { new: 'Новая', in_progress: 'В работе' };

const useAttachments = (data, kind) => useMemo(() => {
    const all = data?.attachments || [];
    return { all, own: all.filter((item) => item.subtask_kind === kind) };
}, [data, kind]);

const Missing = ({ items }) => (items.length ? (
    <div className="px-1 text-[12.5px] text-slate-500">Чтобы продолжить: {items.join('; ')}.</div>
) : null);

const Comment = ({ value, onChange, placeholder = 'Что важно знать следующему по маршруту' }) => (
    <Field label="Комментарий">
        <textarea className={`${iosInput} min-h-[56px] resize-y`} value={value} onChange={(event) => onChange(event.target.value)} maxLength={4000} placeholder={placeholder} />
    </Field>
);

/* Панель «вернуть / отклонить / запросить информацию»: причина обязательна. */
const ReasonPanel = ({ mode, busy, reasons = [], onSubmit, onCancel }) => {
    const [reason, setReason] = useState(null);
    const [text, setText] = useState('');
    const info = mode === 'request_info';
    const chosen = reasons.find((item) => item.code === reason) || null;
    const ready = info ? Boolean(reason) && (reason !== 'other' || text.trim()) : Boolean(text.trim());
    const copy = {
        return: { intro: 'Заявка вернётся инициатору. Напишите, что доработать, — он увидит это в заявке.', action: 'Вернуть', placeholder: 'Что доработать' },
        reject: { intro: 'Заявка будет закрыта как отклонённая. Укажите причину — её увидит инициатор.', action: 'Отклонить заявку', placeholder: 'Причина отклонения' },
        request_info: { intro: 'Задача вернётся инициатору с вашей причиной; после его ответа — снова к вам.', action: 'Запросить', placeholder: 'Что именно нужно (необязательно)' },
    }[mode];
    return (
        <div className="space-y-2 rounded-xl bg-slate-50 p-3 motion-safe:animate-reveal">
            <div className="text-[12.5px] text-slate-600">{copy.intro}</div>
            {info && (
                <FormSelect value={reason} onChange={setReason} options={reasons.map((item) => ({ value: item.code, label: item.label }))} placeholder="Выберите причину" ariaLabel="Причина запроса" />
            )}
            {info && chosen?.ask && <div className="px-1 text-[12.5px] text-slate-500">Инициатор увидит: «{chosen.ask}»</div>}
            <textarea
                className={`${iosInput} min-h-[56px] resize-y bg-white`}
                value={text}
                onChange={(event) => setText(event.target.value)}
                placeholder={info && reason === 'other' ? 'Какая информация нужна' : copy.placeholder}
                maxLength={4000}
            />
            <div className="flex gap-2">
                <button type="button" className={mode === 'reject' ? `${iosBtnPrimary} !bg-rose-600 hover:!bg-rose-700` : iosBtnPrimary} disabled={busy || !ready} onClick={() => onSubmit({ reason, comment: text.trim() })}>
                    {copy.action}
                </button>
                <button type="button" className={iosBtnSecondary} onClick={onCancel}>Отмена</button>
            </div>
        </div>
    );
};

const PaymentStageForm = ({
    data, subtask, dictionaries, users, meta, apiBaseUrl, headers, busy, showToast, permissions,
    onAct, onMove, onEdit, onReply, onAttach, loadCard,
}) => {
    const request = data.request;
    const kind = subtask.kind;
    const { all, own } = useAttachments(data, kind);
    const [comment, setComment] = useState('');
    const [files, setFiles] = useState([]);
    const [mode, setMode] = useState(null);                 // 'return' | 'reject' | 'request_info'
    const [paid, setPaid] = useState({ paid_on: todayISO(), paid_amount: parseAmount(request.amount) });
    const [received, setReceived] = useState({
        received_on: todayISO(), received_quantity: request.object_type === 'goods' ? quantityText(data.items) : '',
    });
    const [assets, setAssets] = useState(() => assetsFromRequest(request, data.items));

    const kinds = new Set([...own.map((item) => item.kind), ...files.map((entry) => entry.kind)]);
    const act = (action, fields = {}, withFiles = true) => onAct({
        action, fields: { comment: comment.trim(), ...fields }, files: withFiles ? files : [],
    });
    const board = (meta?.boards || []).find((item) => item.code === (meta?.subtasks || []).find((s) => s.kind === kind)?.board);
    const statuses = ((meta?.subtasks || []).find((item) => item.kind === kind)?.statuses || [])
        .map((value) => ({ value, label: board?.columns.find((column) => column.key === value)?.label || LOCAL_STATUS_LABELS[value] || value }));
    const invoiceFiles = all.filter((item) => item.kind === 'invoice');
    const closingFiles = all.filter((item) => (meta?.closing_doc_kinds || ['act', 'waybill', 'receipt']).includes(item.kind));
    const counterparty = (dictionaries?.counterparties || []).find((item) => item.id === request.counterparty_id) || null;
    const chips = (label, list) => (
        <FileChips label={label} attachments={list} apiBaseUrl={apiBaseUrl} headers={headers} showToast={showToast} />
    );

    const head = (
        <>
            {permissions.acting_as_admin && (
                <div className="text-[12px] text-slate-500">
                    Вы действуете за исполнителя ({subtask.assignee_name || subtask.role_label}) как администратор раздела — это будет видно в истории.
                </div>
            )}
            {subtask.brief && <div className="text-[12.5px] text-slate-600">{subtask.brief}</div>}
        </>
    );

    const statusSwitch = statuses.length > 1 ? (
        <div className="overflow-x-auto">
            <IosSegmented value={subtask.status} options={statuses} onChange={(value) => value !== subtask.status && onMove(value)} ariaLabel="Статус задачи" className="min-w-max" />
        </div>
    ) : null;

    const reasonPanel = mode ? (
        <ReasonPanel
            mode={mode}
            busy={busy}
            reasons={data.clarify_reasons || []}
            onCancel={() => setMode(null)}
            onSubmit={({ reason, comment: text }) => onAct({ action: mode, fields: { reason, comment: text }, files: [] })}
        />
    ) : null;

    const wrap = (children) => (
        <div data-stage-form={kind} className="space-y-3 rounded-2xl bg-white p-3.5 ring-1 ring-blue-100">
            {head}
            {children}
        </div>
    );

    // ── Инициатор: черновик, доработка, ответ на запрос ──
    if (kind === 'initiation') {
        const problems = data.submit_problems || [];
        const asked = request.clarify_reason;
        return wrap(
            <>
                {asked && (
                    <NoticeBox text="">
                        <span className="font-medium">
                            {asked === 'rework' ? 'Вернули на доработку' : (subtask.clarify_label || 'Требуется уточнение')}
                            {request.clarify_by_name ? ` — ${request.clarify_by_name}` : ''}.
                        </span>
                        {request.clarify_comment ? ` ${request.clarify_comment}` : ''}
                    </NoticeBox>
                )}
                {problems.length > 0 && <Missing items={problems} />}
                {problems.length === 0 && (
                    <Comment value={comment} onChange={setComment} placeholder="Ответ тому, кто запросил (необязательно)" />
                )}
                <div className="flex flex-wrap items-center gap-2">
                    <button type="button" className={problems.length ? iosBtnPrimary : iosBtnSecondary} disabled={busy} onClick={onEdit}>
                        <Pencil size={14} /> {asked ? 'Доработать заявку' : 'Открыть заявку'}
                    </button>
                    {problems.length === 0 && (
                        <button type="button" className={iosBtnPrimary} disabled={busy} onClick={() => onReply({ comment: comment.trim(), files: [] })}>
                            {busy && <Loader2 size={14} className="animate-spin" />} {asked ? 'Отправить ответ' : 'Отправить на согласование'}
                        </button>
                    )}
                </div>
            </>,
        );
    }

    // ── Согласование (п. 7) ──
    if (kind === 'manager_approval' || kind === 'approval') {
        const offerFiles = all.filter((item) => item.kind === 'offer' || item.kind === 'supplier_registry');
        return wrap(
            <>
                {statusSwitch}
                {chips('Коммерческие предложения:', offerFiles)}
                <Comment value={comment} onChange={setComment} placeholder="Комментарий к согласованию (необязательно)" />
                <div className="flex flex-wrap items-center gap-2">
                    <button type="button" className={iosBtnPrimary} disabled={busy} onClick={() => act('approve', {}, false)}>
                        {busy ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />} Согласовать
                    </button>
                    <button type="button" className={iosBtnSecondary} disabled={busy} onClick={() => setMode(mode === 'return' ? null : 'return')}>
                        Вернуть на доработку
                    </button>
                    <button type="button" className={`${iosBtnGhost} !text-rose-600 hover:!bg-rose-50`} disabled={busy} onClick={() => setMode(mode === 'reject' ? null : 'reject')}>
                        Отклонить
                    </button>
                </div>
                {reasonPanel}
            </>,
        );
    }

    // ── Бухгалтерия: оплата счёта (п. 8) ──
    if (kind === 'invoice_payment') {
        const amount = parseAmount(request.amount);
        const approved = parseAmount(request.amount_approved);
        const check = data.contract_check || {};
        const lastPayments = (data.history || []).filter((row) => (row.matched || []).includes('counterparty')).slice(0, 3);
        const missing = [];
        if (!paid.paid_on) missing.push('укажите дату оплаты');
        if (!(parseAmount(paid.paid_amount) > 0)) missing.push('укажите фактическую сумму');
        if (!kinds.has('payment_order')) missing.push('приложите платёжное поручение');
        return wrap(
            <>
                {statusSwitch}
                {(data.duplicates || []).length > 0 && (
                    <NoticeBox text="">
                        <span className="inline-flex items-center gap-1.5 font-medium"><AlertTriangle size={14} /> Возможный дубль.</span>
                        {' '}Тот же поставщик, номер счёта, сумма и дата:
                        {data.duplicates.map((item) => (
                            <div key={item.id}>
                                заявка №{item.id} «{item.expense_name}»{item.paid_on ? ` — оплачена ${fmtDate(item.paid_on)}, ${fmtMoney(item.paid_amount ?? item.amount)}` : ` — ${item.stage_label || 'в работе'}`}
                            </div>
                        ))}
                    </NoticeBox>
                )}
                <div className="rounded-xl bg-slate-50 px-3 py-1.5">
                    <Row label="Поставщик">{request.counterparty_name || '—'}</Row>
                    <Row label="БИН">{request.counterparty_bin || <span className="text-amber-700">не указан в справочнике</span>}</Row>
                    <Row label="Банковские реквизиты">
                        {data.counterparty_account?.text || counterparty?.requisites || <span className="text-amber-700">не указаны</span>}
                    </Row>
                    <Row label="Компания-плательщик">{[request.legal_entity_name, data.legal_entity?.bin && `БИН ${data.legal_entity.bin}`].filter(Boolean).join(' · ') || '—'}</Row>
                    <Row label="Сумма">
                        {fmtMoney(amount)}
                        {approved > 0 && approved !== amount && <span className="ml-2 text-amber-700">согласовано {fmtMoney(approved)}</span>}
                    </Row>
                    <Row label="НДС">{request.counterparty_vat === null || request.counterparty_vat === undefined ? '—' : (request.counterparty_vat ? 'поставщик с НДС' : 'поставщик без НДС')}</Row>
                    <Row label="Счёт">
                        {request.invoice_number ? `№${request.invoice_number}` : '—'}{request.invoice_date ? ` от ${fmtDate(request.invoice_date)}` : ''}
                    </Row>
                    <Row label="Договор">
                        {request.contract_number ? `№${request.contract_number}` : 'без договора'}
                        {check.applies && !check.ok && <div className="text-amber-700">{check.reason}</div>}
                    </Row>
                    <Row label="Прошлые оплаты">
                        {lastPayments.length ? lastPayments.map((row) => (
                            <div key={row.request_id}>№{row.request_id} · {fmtDate(row.paid_on)} · {fmtMoney(row.paid_amount ?? row.amount)}</div>
                        )) : 'этому поставщику в разделе ещё не платили'}
                    </Row>
                </div>
                {chips('Счёт:', invoiceFiles)}
                <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="Дата оплаты" required optionalMark={false}>
                        <IosDatePicker value={paid.paid_on} onChange={(value) => setPaid((prev) => ({ ...prev, paid_on: value || '' }))} allowEmpty placeholder="Дата" triggerClassName={DATE_TRIGGER} ariaLabel="Дата оплаты" />
                    </Field>
                    <Field label="Фактическая сумма, ₸" required optionalMark={false} hint="Сколько ушло поставщику. Подставлена сумма заявки — поправьте, если оплатили другую.">
                        <AmountInput value={paid.paid_amount} onChange={(value) => setPaid((prev) => ({ ...prev, paid_amount: value }))} ariaLabel="Фактическая сумма" />
                    </Field>
                </div>
                <Field as="div" label="Платёжное поручение" required optionalMark={false}>
                    <FilePicker files={files} onChange={setFiles} kinds={fileKindOptions('invoice_payment')} label="Приложить платёжное поручение" />
                </Field>
                <Comment value={comment} onChange={setComment} placeholder="Комментарий к оплате (необязательно)" />
                <Missing items={missing} />
                <div className="flex flex-wrap items-center gap-2">
                    <button type="button" className={iosBtnPrimary} disabled={busy || missing.length > 0} onClick={() => act('pay', paid)}>
                        {busy && <Loader2 size={14} className="animate-spin" />} Оплачено
                    </button>
                    <button type="button" className={iosBtnSecondary} disabled={busy} onClick={() => setMode(mode ? null : 'request_info')}>
                        Запросить информацию
                    </button>
                </div>
                {reasonPanel}
            </>,
        );
    }

    // ── Финансовый отдел: пополнение карты (п. 9) ──
    if (kind === 'card_topup') {
        const missing = [];
        if (!paid.paid_on) missing.push('укажите дату перевода');
        if (!(parseAmount(paid.paid_amount) > 0)) missing.push('укажите сумму');
        if (!kinds.has('transfer_proof')) missing.push('приложите подтверждение перевода');
        return wrap(
            <>
                {statusSwitch}
                <div className="rounded-xl bg-slate-50 px-3 py-1.5">
                    <Row label="Получатель">{[CARD_RECIPIENT_LABELS[request.card_recipient], request.card_holder_name].filter(Boolean).join(' · ')}</Row>
                    <Row label="Номер карты"><CardNumber mask={request.card_mask} canReveal={permissions.can_view_card} load={loadCard} showToast={showToast} /></Row>
                    <Row label="Сумма">{fmtMoney(request.amount)}</Row>
                    <Row label="Назначение">{request.payment_purpose}</Row>
                    <Row label="Компания">{request.legal_entity_name}</Row>
                    <Row label="Подразделение">{request.department_name}</Row>
                    <Row label="Согласовал">{request.approved_by_name}</Row>
                    <Row label="Срок">{request.due_on ? fmtDate(request.due_on) : null}</Row>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="Дата перевода" required optionalMark={false}>
                        <IosDatePicker value={paid.paid_on} onChange={(value) => setPaid((prev) => ({ ...prev, paid_on: value || '' }))} allowEmpty placeholder="Дата" triggerClassName={DATE_TRIGGER} ariaLabel="Дата перевода" />
                    </Field>
                    <Field label="Сумма перевода, ₸" required optionalMark={false}>
                        <AmountInput value={paid.paid_amount} onChange={(value) => setPaid((prev) => ({ ...prev, paid_amount: value }))} ariaLabel="Сумма перевода" />
                    </Field>
                </div>
                <Field as="div" label="Подтверждение перевода" required optionalMark={false}>
                    <FilePicker files={files} onChange={setFiles} kinds={fileKindOptions('card_topup')} label="Приложить подтверждение" />
                </Field>
                <Comment value={comment} onChange={setComment} placeholder="Комментарий к переводу (необязательно)" />
                <Missing items={missing} />
                <div className="flex flex-wrap items-center gap-2">
                    <button type="button" className={iosBtnPrimary} disabled={busy || missing.length > 0} onClick={() => act('top_up', paid)}>
                        {busy && <Loader2 size={14} className="animate-spin" />} Пополнено
                    </button>
                    <button type="button" className={iosBtnSecondary} disabled={busy} onClick={() => setMode(mode ? null : 'request_info')}>
                        Запросить информацию
                    </button>
                </div>
                {reasonPanel}
            </>,
        );
    }

    // ── Инициатор: чек (п. 5.3) ──
    if (kind === 'receipt_confirm') {
        const missing = kinds.has('receipt') ? [] : ['приложите чек или другое подтверждение расхода'];
        return wrap(
            <>
                <div className="text-[12.5px] text-slate-600">
                    На карту переведено {fmtMoney(request.paid_amount ?? request.amount)}{request.paid_on ? ` · ${fmtDate(request.paid_on)}` : ''}.
                </div>
                <Field as="div" label="Чек" required optionalMark={false}>
                    <FilePicker files={files} onChange={setFiles} kinds={fileKindOptions('receipt_confirm')} label="Приложить чек" />
                </Field>
                <Comment value={comment} onChange={setComment} placeholder="Комментарий (необязательно)" />
                <Missing items={missing} />
                <button type="button" className={iosBtnPrimary} disabled={busy || missing.length > 0} onClick={() => act('provide')}>
                    {busy && <Loader2 size={14} className="animate-spin" />} Отправить чек
                </button>
            </>,
        );
    }

    // ── Инициатор: получение (п. 10.1) ──
    if (kind === 'receiving') {
        const goods = request.object_type === 'goods';
        const missing = [];
        if (!received.received_on) missing.push('укажите дату получения');
        if (goods && !received.received_quantity.trim()) missing.push('укажите полученное количество');
        return wrap(
            <>
                <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="Дата получения" required optionalMark={false}>
                        <IosDatePicker value={received.received_on} onChange={(value) => setReceived((prev) => ({ ...prev, received_on: value || '' }))} allowEmpty placeholder="Дата" triggerClassName={DATE_TRIGGER} ariaLabel="Дата получения" />
                    </Field>
                    {goods && (
                        <Field label="Количество получено" required optionalMark={false} hint={HINTS.received}>
                            <input className={iosInput} value={received.received_quantity} onChange={(event) => setReceived((prev) => ({ ...prev, received_quantity: event.target.value }))} maxLength={200} />
                        </Field>
                    )}
                </div>
                <Field as="div" label="Закрывающие документы" hint="Накладная, акт или чек — если уже на руках. Можно приложить и позже: бухгалтерия будет ждать.">
                    <FilePicker files={files} onChange={setFiles} kinds={fileKindOptions('receiving')} label="Приложить документ" />
                </Field>
                <Comment value={comment} onChange={setComment} placeholder="Комментарий (необязательно)" />
                <Missing items={missing} />
                <button type="button" className={iosBtnPrimary} disabled={busy || missing.length > 0} onClick={() => act('confirm', received)}>
                    {busy && <Loader2 size={14} className="animate-spin" />} Подтвердить получение
                </button>
            </>,
        );
    }

    // ── Ответственный за учёт имущества (п. 10.2, п. 11) ──
    if (kind === 'asset_registration') {
        const missing = [];
        assets.forEach((asset, index) => {
            const gaps = assetGaps(asset);
            if (gaps.length) missing.push(`имущество${assets.length > 1 ? ` ${index + 1}` : ''}: ${gaps.join(', ')}`);
        });
        const numbers = assets.map((asset) => asset.inventory_number.trim().toLowerCase()).filter(Boolean);
        if (new Set(numbers).size !== numbers.length) missing.push('инвентарные номера не должны повторяться');
        if (!kinds.has('handover_act')) missing.push('приложите акт приёма-передачи');
        return wrap(
            <>
                {statusSwitch}
                <PaymentAssetsEditor assets={assets} onChange={setAssets} users={users} dictionaries={dictionaries} />
                <Field as="div" label="Акт приёма-передачи" required optionalMark={false} hint="Подписанный акт передачи имущества ответственному сотруднику.">
                    <FilePicker files={files} onChange={setFiles} kinds={fileKindOptions('asset_registration')} label="Приложить акт" />
                </Field>
                <Comment value={comment} onChange={setComment} placeholder="Комментарий (необязательно)" />
                <Missing items={missing} />
                <button type="button" className={iosBtnPrimary} disabled={busy || missing.length > 0} onClick={() => act('register', { assets: assets.map(assetPayload) })}>
                    {busy && <Loader2 size={14} className="animate-spin" />} Поставить на учёт
                </button>
            </>,
        );
    }

    // ── Бухгалтерия: закрывающие документы (п. 14) ──
    if (kind === 'closing_docs') {
        const status = request.closing_docs_status || 'none';
        const docsMeta = DOCS_STATUS_META[status] || DOCS_STATUS_META.none;
        const received_ = status !== 'none';
        return wrap(
            <>
                <div className="flex flex-wrap items-center gap-2">
                    <TonePill tone={docsMeta.tone}>{docsMeta.label}</TonePill>
                    {!received_ && <span className="text-[12.5px] text-slate-500">Инициатору отправлено напоминание приложить документы.</span>}
                </div>
                {chips('Документы:', closingFiles)}
                <Field as="div" label="Добавить скан" hint="Если скан или оригинал пришёл к вам, а не через инициатора, — приложите его сами.">
                    <FilePicker files={files} onChange={setFiles} kinds={fileKindOptions('closing_docs')} label="Приложить документ" />
                    {files.length > 0 && (
                        <button type="button" className={`${iosBtnSecondary} mt-2`} disabled={busy} onClick={async () => { if (await onAttach(files)) setFiles([]); }}>
                            {busy && <Loader2 size={14} className="animate-spin" />} Загрузить
                        </button>
                    )}
                </Field>
                <Comment value={comment} onChange={setComment} placeholder="Комментарий (необязательно)" />
                {!received_ && <Missing items={['дождитесь скана или оригинала закрывающих документов']} />}
                <div className="flex flex-wrap items-center gap-2">
                    {status !== 'original' && (
                        <button type="button" className={iosBtnSecondary} disabled={busy} onClick={() => act('docs_original', {}, false)}>
                            Оригинал получен
                        </button>
                    )}
                    <button type="button" className={iosBtnPrimary} disabled={busy || !received_} onClick={() => act('docs_close', {}, false)}>
                        {busy && <Loader2 size={14} className="animate-spin" />} Закрыть документы
                    </button>
                </div>
            </>,
        );
    }

    return null;
};

export default PaymentStageForm;
