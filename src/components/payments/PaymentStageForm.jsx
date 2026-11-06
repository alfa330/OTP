import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import axios from 'axios';
import { AlertTriangle, Check, Loader2, Pencil, Search } from 'lucide-react';
import { iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosInput } from '../ui/ios';
import PaymentAssetsEditor, { assetIssues, assetPayload, assetsFromRequest } from './PaymentAssetsEditor';
import PaymentItemsEditor, { newItem } from './PaymentItemsEditor';
import PaymentOffersEditor, { newOffer, offerFromRow, offerPayload } from './PaymentOffersEditor';
import PaymentCardFields, { useSavedCards } from './PaymentCardFields';
import {
    ACCOUNTING_CATEGORY_OPTIONS, ALTERNATIVES_OPTIONS, ASSET_STATUS_META, CARD_RECIPIENT_LABELS, DOCS_STATUS_META, HINTS,
    OBJECT_TYPE_OPTIONS, PAYMENT_METHOD_OPTIONS, STOCK_OPTIONS, cardDigits, fileKindOptions, fmtDate, fmtMoney, helpdeskIssues,
    isBlankItem, isBlankOffer, issueKeys, issueSentence, issuesByKey, itemQuantity, itemsTotal, parseAmount, pricedByOffer,
    quantityText, recommendedOffer, stageAtFinal, stageTaken, todayISO,
} from './paymentsMeta';
import {
    AmountInput, CardNumber, Choice, Field, FileChips, FilePicker, FormDate, FormInput, FormSelect, FormText, INVALID_RING,
    IssueNav, NoticeBox, ReturnNotice, Row, TonePill, errorText, fromKeyboard, useIssueNav,
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
 * 2. ЧЕГО НЕ ХВАТАЕТ — КРАСНЫМ У САМОГО ПОЛЯ (просьба владельца 14.10.2026).
 *    Кнопка действия нажимается всегда; не хватает обязательного — действие не
 *    уходит, а пустые поля обводятся красным, под каждым написано, чего в нём нет,
 *    и окно подводит прокрутку к первому (кнопка — внизу формы). Таких полей
 *    несколько — в нижней панели окна «Не хватает: N» со стрелками ⌃ ⌄ (окно
 *    заявки даёт для них место — `issueSlot`). Сервер проверяет то же самое
 *    (workflow.missing_for_action).
 *
 * 3. КОММЕНТАРИЙ ОБЯЗАТЕЛЕН ТАМ, ГДЕ ЕГО ТРЕБУЕТ ТЗ: возврат на доработку и
 *    отклонение (п. 7), «Запросить информацию» — причина из списка (п. 8).
 *
 * Состояние формы живёт здесь и сбрасывается сменой этапа: карточка монтирует
 * компонент с key из номера заявки и вида подзадачи.
 *
 * 4. СТУПЕНЬ ЗА СТУПЕНЬЮ (просьба владельца 14.10.2026: «не нужен такой
 *    переключатель… сделай кнопками, и чтобы было постепенно», как в «Задачах»). Статус
 *    задачи меняет кнопка следующей ступени в нижней панели окна заявки
 *    («Взять на рассмотрение», «Взять в проверку», «Счёт проверен», «Начать оплату»…,
 *    paymentsMeta.STAGE_STEPS). Пока ступени не пройдены, форма показывает то, что нужно
 *    для проверки (реквизиты, сумму, файлы), а поля и итоговое действие («Согласовать»,
 *    «Оплачено», «Пополнено», «Поставить на учёт») — на последней ступени. «Запросить
 *    информацию» — с той ступени, на которой задачу взяли.
 */

const useAttachments = (data, kind) => useMemo(() => {
    const all = data?.attachments || [];
    return { all, own: all.filter((item) => item.subtask_kind === kind) };
}, [data, kind]);

/* Чего не хватает этапу — пунктами с ключом поля, как у формы заявки
   (paymentsMeta.purchaseIssues): по ключу поле обводится красным и к нему ведут
   стрелки. Правила — те же, что у сервера (workflow.missing_for_action). */
export const stageIssues = (kind, { request = {}, paid = {}, received = {}, assets = [], kinds = new Set(), queued = 0, entityId = null } = {}) => {
    const issues = [];
    const add = (key, text) => issues.push({ key, text });
    if (kind === 'entity_clarify') {
        if (!entityId) add('legal_entity_id', 'выберите компанию');
    } else if (kind === 'invoice_payment') {
        if (!paid.paid_on) add('paid_on', 'укажите дату оплаты');
        if (!(parseAmount(paid.paid_amount) > 0)) add('paid_amount', 'укажите фактическую сумму');
        if (!kinds.has('payment_order')) add('files', 'приложите платёжное поручение');
    } else if (kind === 'card_topup') {
        if (!paid.paid_on) add('paid_on', 'укажите дату перевода');
        if (!(parseAmount(paid.paid_amount) > 0)) add('paid_amount', 'укажите сумму');
        if (!kinds.has('transfer_proof')) add('files', 'приложите подтверждение перевода');
    } else if (kind === 'receipt_confirm') {
        if (!kinds.has('receipt')) add('files', 'приложите чек или другое подтверждение расхода');
    } else if (kind === 'receiving') {
        if (!received.received_on) add('received_on', 'укажите дату получения');
        if (request.object_type === 'goods' && !String(received.received_quantity || '').trim()) {
            add('received_quantity', 'укажите полученное количество');
        }
    } else if (kind === 'asset_registration') {
        issues.push(...assetIssues(assets));
        if (!kinds.has('handover_act')) add('files', 'приложите акт приёма-передачи');
    } else if (kind === 'closing_docs' && (request.closing_docs_status || 'none') === 'none') {
        // Скан выбран, но ещё не загружен (`queued`): закрыть мешает уже не ожидание, а кнопка «Загрузить».
        add('files', queued
            ? 'нажмите «Загрузить», чтобы приложить выбранный скан'
            : 'дождитесь скана или оригинала закрывающих документов — или приложите скан сами');
    }
    return issues;
};

/* Заявке инициатора не хватает данных (их правят в форме заявки, не здесь) — список
   красным: «Доработать заявку» откроет форму сразу на этих полях. */
const Missing = ({ items }) => (items.length ? (
    <div className="px-1 text-[12.5px] leading-relaxed text-rose-600" data-issue-text>Чтобы продолжить: {items.join('; ')}.</div>
) : null);

const Comment = ({ value, onChange, placeholder = 'Что важно знать следующему по маршруту', optional = true }) => (
    <Field label="Комментарий" optionalMark={optional}>
        <textarea className={`${iosInput} min-h-[56px] resize-y`} value={value} onChange={(event) => onChange(event.target.value)} maxLength={4000} placeholder={placeholder} />
    </Field>
);

/* Панель «вернуть / отклонить / запросить информацию»: причина обязательна. Нажали,
   а её нет, — пустое поле краснеет, как в форме этапа. */
const ReasonPanel = ({ mode, busy, reasons = [], onSubmit, onCancel }) => {
    const [reason, setReason] = useState(null);
    const [text, setText] = useState('');
    const [reveal, setReveal] = useState(false);
    const panelRef = useRef(null);
    const info = mode === 'request_info';
    const chosen = reasons.find((item) => item.code === reason) || null;
    const issues = [];
    if (info && !reason) issues.push({ key: 'reason', text: 'выберите причину' });
    if (info ? reason === 'other' && !text.trim() : !text.trim()) {
        issues.push({ key: 'text', text: { request_info: 'напишите, какая информация нужна', reject: 'укажите причину отклонения', return: 'напишите, что доработать' }[mode] });
    }
    const keys = issueKeys(issues);
    const nav = useIssueNav(panelRef, keys);
    const issueOf = (key) => (reveal ? issueSentence(issues.filter((item) => item.key === key).map((item) => item.text)) : '');
    const submit = (event) => {
        if (busy) return;
        if (issues.length) { setReveal(true); nav.first({ focus: fromKeyboard(event) }); return; }
        onSubmit({ reason, comment: text.trim() });
    };
    const copy = {
        return: { intro: 'Заявка вернётся инициатору. Напишите, что доработать, — он увидит это в заявке.', action: 'Вернуть', placeholder: 'Что доработать' },
        reject: { intro: 'Заявка будет закрыта как отклонённая. Укажите причину — её увидит инициатор.', action: 'Отклонить заявку', placeholder: 'Причина отклонения' },
        request_info: { intro: 'Задача вернётся инициатору с вашей причиной; после его ответа — снова к вам.', action: 'Запросить', placeholder: 'Что именно нужно (необязательно)' },
    }[mode];
    return (
        <div ref={panelRef} className="space-y-2 rounded-xl bg-slate-50 p-3 motion-safe:animate-reveal">
            <div className="text-[12.5px] text-slate-600">{copy.intro}</div>
            {info && (
                <Field as="div" anchor="reason" issue={issueOf('reason')}>
                    <FormSelect value={reason} onChange={setReason} options={reasons.map((item) => ({ value: item.code, label: item.label }))} placeholder="Выберите причину" ariaLabel="Причина запроса" />
                </Field>
            )}
            {info && chosen?.ask && <div className="px-1 text-[12.5px] text-slate-500">Инициатор увидит: «{chosen.ask}»</div>}
            <Field as="div" anchor="text" issue={issueOf('text')}>
                <FormText
                    className="min-h-[56px] bg-white"
                    value={text}
                    onChange={(event) => setText(event.target.value)}
                    placeholder={info && reason === 'other' ? 'Какая информация нужна' : copy.placeholder}
                    maxLength={4000}
                />
            </Field>
            <div className="flex gap-2">
                <button type="button" className={mode === 'reject' ? `${iosBtnPrimary} !bg-rose-600 hover:!bg-rose-700` : iosBtnPrimary} disabled={busy} onClick={submit}>
                    {copy.action}
                </button>
                <button type="button" className={iosBtnSecondary} onClick={onCancel}>Отмена</button>
            </div>
        </div>
    );
};

/* ── Helpdesk IT: проверка IT-закупа (просьба владельца 04.11.2026) ─────────────────────────
   После руководителя сюда приходит IT-закуп — заявка с категорией, у которой отметка «Helpdesk
   IT». Нужное для решения — под рукой сразу: что уже числится за сотрудником и что лежит на
   складе, и сразу же — выбор «Есть в наличии?» (06.11.2026, владелец: «в данном окне я не могу
   ничего выбрать» — ступени «Взять в работу» у этапа нет, «В работе» на доске карточка встаёт
   сама, по первому выбору). Строка склада выбирается нажатием — это и есть «Есть»:
     «Есть» — отметить вещь из найденного на складе (или описать в комментарии) и выдать:
              заявка закроется «Выдано из наличия», без закупа и оплаты (выбор владельца);
     «Нет»  — варианты поставщиков по тем же правилам, что у инициатора, и цены позиций по
              рекомендуемому: сумма позиций должна совпасть с его стоимостью — по ней раздел
              выберет утверждающего (тоже выбор владельца: цены ставит Helpdesk IT). С 06.11.2026
              здесь же товар или услуга, категория учёта и способ оплаты: инициатор IT-закупа
              пишет только что, сколько и пожелания.
   «Вернуть на доработку» — когда из заявки не понять, что именно нужно. */

const STOCK_SEARCH_MS = 300;
// Порядок полей сверху вниз — по нему стрелки «Не хватает» ходят от поля к полю.
const HELPDESK_FIELDS = ['in_stock', 'stock_ids', 'no_alternatives_reason', 'no_alternatives_comment', 'offers',
    'supplier_choice_reason', 'items', 'object_type', 'accounting_category', 'payment_method', 'card_recipient',
    'card_holder_user_id', 'card_holder_name', 'card_number', 'payment_purpose'];

// Позиции заявки — строками редактора, как в форме заявки: количество так, как его набирают.
const itemRows = (items) => {
    const rows = (items || []).map((item) => {
        const quantity = parseAmount(item.quantity);
        return {
            key: Math.random().toString(36).slice(2, 9), name: item.name || '',
            quantity: quantity ? String(quantity).replace('.', ',') : '1', unit: item.unit || '',
            unit_price: parseAmount(item.unit_price),
        };
    });
    return rows.length ? rows : [newItem()];
};

const HelpdeskStage = ({ data, subtask, dictionaries, users, meta, apiBaseUrl, headers, busy, permissions, onAct, onTake, issueSlot }) => {
    const request = data.request;
    const minSuppliers = Number(dictionaries?.settings?.min_suppliers) || 3;
    const [inStock, setInStock] = useState(null);
    const [draft, setDraft] = useState(() => {
        // Поставщики, уже лежащие в заявке (Helpdesk проверяет её второй раз), — с ними.
        const offers = (data.offers || []).map(offerFromRow);
        return {
            offers: offers.length ? offers : Array.from({ length: minSuppliers }, () => newOffer()),
            no_alternatives: Boolean(request.no_alternatives),
            no_alternatives_reason: request.no_alternatives_reason ?? null,
            no_alternatives_comment: request.no_alternatives_comment || '',
            supplier_choice_reason: request.supplier_choice_reason || '',
            items: itemRows(data.items),
            // Товар или услуга, категория учёта и способ оплаты — тоже его (06.11.2026); второй проход — с прежним выбором.
            object_type: request.object_type ?? null,
            accounting_category: request.accounting_category ?? null,
            payment_method: request.payment_method ?? null,
            card_recipient: request.card_recipient ?? null,
            card_holder_user_id: request.card_holder_user_id ?? null,
            card_holder_name: request.card_holder_name || '',
            card_id: null,
            card_number: '',
            payment_purpose: request.payment_purpose || '',
            comment: '',
        };
    });
    const set = (key, value) => setDraft((prev) => ({ ...prev, [key]: value }));
    // Что выдают из наличия: строки склада целиком — выбранное видно и после нового поиска.
    const [chosen, setChosen] = useState([]);
    const [mode, setMode] = useState(null);                 // 'return'
    const [reveal, setReveal] = useState(false);
    const formRef = useRef(null);

    // ── Склад: имущество «На складе», поиском ──
    const [query, setQuery] = useState('');
    const [stock, setStock] = useState({ items: [], total: 0, loading: true, error: '' });
    // Перечитать склад: выдача не прошла — вещь могли отдать по другой заявке.
    const [reload, setReload] = useState(0);
    useEffect(() => {
        let cancelled = false;
        setStock((prev) => ({ ...prev, loading: true, error: '' }));
        const timer = setTimeout(() => {
            // Номер заявки — склад открыт и тому, кому администратор передал эту проверку.
            const params = new URLSearchParams({ request: String(request.id) });
            if (query.trim()) params.set('q', query.trim());
            axios.get(`${apiBaseUrl}/api/payments/stock?${params}`, { headers: headers() })
                .then((response) => {
                    if (!cancelled) setStock({ items: response.data?.items || [], total: response.data?.total || 0, loading: false, error: '' });
                })
                .catch((err) => {
                    if (!cancelled) setStock({ items: [], total: 0, loading: false, error: errorText(err, 'Склад не загрузился') });
                });
        }, query ? STOCK_SEARCH_MS : 0);
        return () => { cancelled = true; clearTimeout(timer); };
    }, [apiBaseUrl, headers, query, request.id, reload]);

    const offersShown = draft.no_alternatives ? draft.offers.slice(0, 1) : draft.offers;
    const recommended = recommendedOffer(draft);
    // Номер карты, что уже лежит в заявке, годится, пока получатель прежний: для поставщика — тот же,
    // что рекомендуемый сейчас (как в форме заявки и на сервере, routes._helpdesk_payment).
    const [cardEditing, setCardEditing] = useState(false);
    const cardKept = Boolean(request.has_card && !cardEditing && draft.payment_method === 'card'
        && draft.card_recipient === (request.card_recipient ?? null)
        && (draft.card_recipient === 'employee'
            ? draft.card_holder_user_id === (request.card_holder_user_id ?? null)
            : (recommended?.counterparty_id ?? null) === (request.counterparty_id ?? null)));
    const cardOptions = useSavedCards({
        enabled: inStock === false && draft.payment_method === 'card', apiBaseUrl, headers,
        recipient: draft.card_recipient, userId: draft.card_holder_user_id, counterpartyId: recommended?.counterparty_id,
    });
    // Проверка видит то же, что на экране и уйдёт на сервер: при «альтернатив нет» — один вариант.
    const issues = helpdeskIssues({ ...draft, offers: offersShown, stock_ids: chosen.map((item) => item.id) },
        { minSuppliers, inStock, hasCard: cardKept });
    const byKey = issuesByKey(issues);
    const keys = issueKeys(issues, HELPDESK_FIELDS);
    const nav = useIssueNav(formRef, keys);
    const issueOf = (key) => (reveal && byKey[key] ? issueSentence(byKey[key].map((item) => item.text)) : '');
    const attempt = (run) => (event) => {
        if (busy) return;
        if (issues.length) { setMode(null); setReveal(true); nav.first({ focus: fromKeyboard(event) }); return; }
        run();
    };
    // Первый выбор — и карточка на доске Helpdesk IT встаёт в «В работе»: отдельной кнопки нет.
    const take = () => { if (subtask.status === 'new') onTake?.(); };
    const pick = (value) => {
        setInStock(value); setReveal(false); setMode(null);
        if (value === false) setChosen([]);
        take();
    };
    // Нажали строку склада — это и есть «Есть в наличии»: выбор переключается сам.
    const toggle = (item) => {
        const on = chosen.some((other) => other.id === item.id);
        setChosen((prev) => (on ? prev.filter((other) => other.id !== item.id) : [...prev, item]));
        if (!on && inStock !== true) { setInStock(true); setReveal(false); setMode(null); }
        if (!on) take();
    };

    const counterpartyOptions = useMemo(() => (dictionaries?.counterparties || [])
        .map((item) => ({ value: item.id, label: item.bin ? `${item.name} · БИН ${item.bin}` : item.name })), [dictionaries]);
    // Причины «альтернатив нет» — словарь сервера (п. 4.3), как в форме заявки.
    const reasonOptions = useMemo(() => (meta?.no_alternatives_reasons || [])
        .map((item) => ({ value: item.code, label: item.label })), [meta]);
    const savedFilesByOffer = useMemo(() => {
        const map = {};
        (data.attachments || []).forEach((item) => { if (item.offer_id) (map[item.offer_id] ||= []).push(item); });
        return map;
    }, [data]);

    const total = itemsTotal(draft.items.filter((item) => !isBlankItem(item)));
    const price = parseAmount(recommended?.amount);
    // Позиция одна, а сумма с поставщиком не сходится — цену можно поставить одной кнопкой.
    const priced = recommended && price > 0 && Math.round(total * 100) !== Math.round(price * 100)
        ? pricedByOffer(draft.items, price) : null;

    const passOffers = () => {
        const sent = offersShown.filter((offer) => !isBlankOffer(offer));
        onAct({
            action: 'it_offers',
            fields: {
                comment: draft.comment.trim(),
                items: draft.items.filter((item) => !isBlankItem(item)).map((item) => ({
                    name: item.name.trim(), quantity: itemQuantity(item), unit: item.unit || null,
                    unit_price: parseAmount(item.unit_price),
                })),
                offers: sent.map((offer, index) => ({
                    ...offerPayload(offer), is_recommended: draft.no_alternatives ? index === 0 : Boolean(offer.is_recommended),
                })),
                no_alternatives: Boolean(draft.no_alternatives),
                no_alternatives_reason: draft.no_alternatives ? draft.no_alternatives_reason : null,
                no_alternatives_comment: draft.no_alternatives ? draft.no_alternatives_comment.trim() : '',
                supplier_choice_reason: draft.no_alternatives ? '' : draft.supplier_choice_reason.trim(),
                // Товар или услуга, категория учёта и способ оплаты — выбор Helpdesk IT (владелец 06.11.2026).
                object_type: draft.object_type,
                accounting_category: draft.object_type === 'goods' ? draft.accounting_category : null,
                payment_method: draft.payment_method,
                ...(draft.payment_method === 'card' ? {
                    card_recipient: draft.card_recipient,
                    card_holder_user_id: draft.card_recipient === 'employee' ? draft.card_holder_user_id : null,
                    card_holder_name: draft.card_recipient === 'supplier' ? draft.card_holder_name.trim() : '',
                    payment_purpose: draft.payment_purpose.trim(),
                    ...(cardKept ? {} : (draft.card_id ? { card_id: draft.card_id } : { card_number: cardDigits(draft.card_number) })),
                } : {}),
            },
            // Коммерческие предложения — к своим вариантам: номер варианта среди отправленных.
            files: sent.map((offer, index) => (offer.file ? { file: offer.file, kind: 'offer', offerIndex: index } : null)).filter(Boolean),
        });
    };
    const issueStock = async () => {
        const ok = await onAct({
            action: 'it_stock', fields: { comment: draft.comment.trim(), stock_asset_ids: chosen.map((item) => item.id) }, files: [],
        });
        // Не вышло (вещь уже не на складе или выдана по другой заявке) — склад перечитан, выбор снят.
        if (ok === false) { setChosen([]); setReload((value) => value + 1); }
    };

    const navigator = reveal && issueSlot && keys.length > 1
        ? createPortal(<div className="order-first w-full"><IssueNav count={keys.length} onStep={nav.step} /></div>, issueSlot)
        : null;

    const assets = data.initiator_assets;
    const stockEmpty = stock.error || (query.trim() ? 'Ничего не нашлось — уточните поиск' : 'На складе сейчас ничего нет');

    return (
        <div ref={formRef} data-stage-form={subtask.kind} className="space-y-3 rounded-2xl bg-white p-3.5 ring-1 ring-blue-100">
            {permissions.acting_as_admin && (
                <div className="text-[12px] text-slate-500">
                    Вы действуете за исполнителя ({subtask.assignee_name || subtask.role_label}) как администратор раздела — это будет видно в истории.
                </div>
            )}
            {subtask.brief && <div className="text-[12.5px] text-slate-600">{subtask.brief}</div>}

            {/* Что уже числится за сотрудником — по реестру имущества. */}
            {Array.isArray(assets) && (
                <Field as="div" label="Имущество сотрудника" optionalMark={false} hint={HINTS.itAssets}>
                    {assets.length ? (
                        <div data-helpdesk-assets className="divide-y divide-slate-100 overflow-hidden rounded-xl ring-1 ring-slate-200/70">
                            {assets.map((asset) => (
                                <div key={asset.id} className="flex items-center gap-3 px-3 py-2">
                                    <div className="min-w-0 grow basis-0">
                                        <div className="truncate text-[13.5px] text-slate-900">{asset.name}</div>
                                        <div className="truncate text-[12px] text-slate-500">
                                            {[asset.inventory_number && `инв. ${asset.inventory_number}`, asset.category_name,
                                                asset.received_on && `с ${fmtDate(asset.received_on)}`].filter(Boolean).join(' · ')}
                                        </div>
                                    </div>
                                    {ASSET_STATUS_META[asset.status] && (
                                        <TonePill tone={ASSET_STATUS_META[asset.status].tone}>{ASSET_STATUS_META[asset.status].label}</TonePill>
                                    )}
                                </div>
                            ))}
                        </div>
                    ) : (
                        <div data-helpdesk-assets className="rounded-xl bg-slate-50 px-3 py-2 text-[13px] text-slate-500">
                            В реестре имущества за сотрудником ничего не числится.
                        </div>
                    )}
                </Field>
            )}

            {/* Склад: есть ли нужное в наличии. Строка выбирается нажатием — и это «Есть в наличии». */}
            <Field as="div" label="Склад" optionalMark={false} hint="Имущество со статусом «На складе». Ищите по названию, инвентарному или серийному номеру." anchor="stock_ids" issue={issueOf('stock_ids')}>
                <div className="relative">
                    <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
                    <input className={`${iosInput} !pl-8`} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Найти на складе" aria-label="Поиск по складу" maxLength={120} />
                </div>
                {chosen.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 px-0.5" data-stock-chosen>
                        {chosen.map((item) => (
                            <button key={item.id} type="button" onClick={() => toggle(item)} className="inline-flex max-w-full items-center gap-1.5 rounded-full bg-blue-50 px-2.5 py-1 text-[12.5px] text-blue-700 ring-1 ring-blue-200 transition active:scale-[0.97]" title="Убрать из выдачи">
                                <Check size={12} strokeWidth={3} className="shrink-0" />
                                <span className="truncate">{item.name}{item.inventory_number ? ` · ${item.inventory_number}` : ''}</span>
                            </button>
                        ))}
                    </div>
                )}
                <div data-stock-list className="divide-y divide-slate-100 overflow-hidden rounded-xl ring-1 ring-slate-200/70">
                    {stock.items.map((item) => {
                        const on = chosen.some((other) => other.id === item.id);
                        // Уже выдана по другой заявке — выбрать её нельзя (сервер такую выдачу тоже не примет).
                        const pickable = !item.issued_request_id;
                        const body = (
                            <>
                                <div className="min-w-0 grow basis-0">
                                    <div className={`truncate text-[13.5px] ${on ? 'font-medium text-blue-700' : 'text-slate-900'}`}>{item.name}</div>
                                    <div className="truncate text-[12px] text-slate-500">
                                        {[item.inventory_number && `инв. ${item.inventory_number}`, item.category_name, item.city, item.location].filter(Boolean).join(' · ')}
                                    </div>
                                    {/* Выдали по другой заявке, а по акту ещё не передали: карточка пока «На складе». */}
                                    {item.issued_request_id && (
                                        <div className="text-[12px] leading-snug text-amber-700" data-stock-issued={item.issued_request_id}>
                                            Уже выдан по заявке №{item.issued_request_id} — ещё не передан по акту
                                        </div>
                                    )}
                                </div>
                                {pickable && (on
                                    ? <Check size={15} strokeWidth={3} className="shrink-0 text-blue-600" aria-hidden="true" />
                                    : <span className="h-4 w-4 shrink-0 rounded-full ring-1 ring-slate-300" aria-hidden="true" />)}
                            </>
                        );
                        return pickable ? (
                            <button key={item.id} type="button" role="checkbox" aria-checked={on} data-stock-item={item.id} onClick={() => toggle(item)}
                                className={`flex w-full items-center gap-3 px-3 py-2 text-left transition active:scale-[0.99] ${on ? 'bg-blue-50/60' : 'hover:bg-slate-50'}`}>
                                {body}
                            </button>
                        ) : (
                            <div key={item.id} data-stock-item={item.id} className="flex items-center gap-3 px-3 py-2">{body}</div>
                        );
                    })}
                    {!stock.items.length && (
                        <div className={`px-3 py-2.5 text-[13px] ${stock.error ? 'text-rose-600' : 'text-slate-500'}`}>
                            {stock.loading ? 'Ищем на складе…' : stockEmpty}
                        </div>
                    )}
                </div>
                {stock.total > stock.items.length && (
                    <div className="px-1 text-[12px] text-slate-500">Показаны {stock.items.length} из {stock.total} — уточните поиск.</div>
                )}
            </Field>

            {/* Решение — сразу, без ступени «Взять в работу» (06.11.2026). */}
            <>
                <Field as="div" label="Есть в наличии?" required optionalMark={false} hint={HINTS.itStock} anchor="in_stock" issue={issueOf('in_stock')}>
                    <Choice value={inStock} options={STOCK_OPTIONS} onChange={pick} ariaLabel="Есть ли нужное в наличии" />
                </Field>

                {inStock === false && (
                    <>
                        <Field as="div" label="Альтернативные предложения" required optionalMark={false} hint={HINTS.alternatives}>
                            <Choice value={Boolean(draft.no_alternatives)} options={ALTERNATIVES_OPTIONS} onChange={(value) => set('no_alternatives', value)} ariaLabel="Альтернативные предложения" />
                        </Field>
                        {draft.no_alternatives && (
                            <div className="grid gap-3 sm:grid-cols-2">
                                <Field label="Причина отсутствия альтернатив" required anchor="no_alternatives_reason" issue={issueOf('no_alternatives_reason')}>
                                    <FormSelect value={draft.no_alternatives_reason} onChange={(value) => set('no_alternatives_reason', value)} options={reasonOptions} placeholder="Выберите причину" ariaLabel="Причина отсутствия альтернатив" />
                                </Field>
                                <Field label="Пояснение" required={draft.no_alternatives_reason === 'other'} anchor="no_alternatives_comment" issue={issueOf('no_alternatives_comment')}>
                                    <FormInput value={draft.no_alternatives_comment} onChange={(event) => set('no_alternatives_comment', event.target.value)} maxLength={2000} />
                                </Field>
                            </div>
                        )}
                        <Field as="div" label={draft.no_alternatives ? 'Поставщик' : 'Варианты поставщиков'} required optionalMark={false} hint={draft.no_alternatives ? null : { ...HINTS.offers, intro: `${HINTS.offers.intro} Нужно не меньше ${minSuppliers}.` }} anchor="offers" issue={issueOf('offers')} issueFirst>
                            <PaymentOffersEditor
                                offers={draft.offers}
                                onChange={(next) => set('offers', next)}
                                counterpartyOptions={counterpartyOptions}
                                savedFilesByOffer={savedFilesByOffer}
                                single={Boolean(draft.no_alternatives)}
                                issues={reveal ? byKey.offers || [] : []}
                            />
                        </Field>
                        {!draft.no_alternatives && (
                            <Field label="Обоснование выбора поставщика" required hint={HINTS.choiceReason} anchor="supplier_choice_reason" issue={issueOf('supplier_choice_reason')}>
                                <FormText className="min-h-[56px]" value={draft.supplier_choice_reason} onChange={(event) => set('supplier_choice_reason', event.target.value)} maxLength={2000} />
                            </Field>
                        )}
                        <Field as="div" label="Позиции и цены" required optionalMark={false} hint={HINTS.itItems} anchor="items" issue={issueOf('items')} issueFirst>
                            <PaymentItemsEditor items={draft.items} onChange={(items) => set('items', items)} issue={reveal ? byKey.items?.[0] || null : null} />
                            {priced && (
                                <button type="button" className={`${iosBtnGhost} -ml-1`} onClick={() => set('items', priced)} data-price-by-offer>
                                    Поставить цену поставщика · {fmtMoney(price)}
                                </button>
                            )}
                        </Field>
                        {/* Товар или услуга и категория учёта — Helpdesk IT ставит имущество на учёт, ему и решать (06.11.2026). */}
                        <div className="flex flex-wrap gap-x-6 gap-y-3">
                            <Field as="div" label="Тип объекта" required optionalMark={false} hint={HINTS.objectType} anchor="object_type" issue={issueOf('object_type')}>
                                <Choice value={draft.object_type} options={OBJECT_TYPE_OPTIONS} onChange={(value) => setDraft((prev) => ({ ...prev, object_type: value, accounting_category: value === 'goods' ? prev.accounting_category : null }))} ariaLabel="Тип объекта" />
                            </Field>
                            {draft.object_type === 'goods' && (
                                <Field as="div" label="Категория учёта" required optionalMark={false} hint={HINTS.accountingCategory} anchor="accounting_category" issue={issueOf('accounting_category')}>
                                    <Choice value={draft.accounting_category} options={ACCOUNTING_CATEGORY_OPTIONS} onChange={(value) => set('accounting_category', value)} ariaLabel="Категория учёта приобретения" />
                                </Field>
                            )}
                        </div>
                        <Field as="div" label="Как платим" required optionalMark={false} hint={HINTS.paymentMethod} anchor="payment_method" issue={issueOf('payment_method')}>
                            <Choice value={draft.payment_method} options={PAYMENT_METHOD_OPTIONS} onChange={(value) => set('payment_method', value)} ariaLabel="Способ оплаты" />
                        </Field>
                        {draft.payment_method === 'invoice' && (
                            <div className="px-1 text-[12.5px] text-slate-500">Счёт приложат после утверждения — раздел попросит его перед оплатой.</div>
                        )}
                        {draft.payment_method === 'card' && (
                            <PaymentCardFields
                                draft={draft}
                                onPatch={(patch) => setDraft((prev) => ({ ...prev, ...patch }))}
                                issueOf={issueOf}
                                users={users}
                                keptMask={cardKept ? request.card_mask : null}
                                onEditKept={() => setCardEditing(true)}
                                cardOptions={cardOptions}
                                digitsRange={meta?.card_digits}
                            />
                        )}
                    </>
                )}

                {inStock !== null && (
                    <Comment
                        value={draft.comment}
                        onChange={(value) => set('comment', value)}
                        // Выдают из наличия — без отмеченной вещи на складе словами о выдаче не обойтись.
                        optional={!inStock}
                        placeholder={inStock ? 'Что выдаёте и где сотрудник может забрать' : 'Что важно знать утверждающему (необязательно)'}
                    />
                )}
                <div className="flex flex-wrap items-center gap-2">
                    {inStock === true && (
                        <button type="button" className={iosBtnPrimary} disabled={busy} onClick={attempt(issueStock)}>
                            {busy && <Loader2 size={14} className="animate-spin" />} Выдать из наличия
                        </button>
                    )}
                    {inStock === false && (
                        <button type="button" className={iosBtnPrimary} disabled={busy} onClick={attempt(passOffers)}>
                            {busy && <Loader2 size={14} className="animate-spin" />} Передать на утверждение
                        </button>
                    )}
                    <button type="button" className={iosBtnSecondary} disabled={busy} onClick={() => { setMode((prev) => (prev === 'return' ? null : 'return')); setReveal(false); }}>
                        Вернуть на доработку
                    </button>
                </div>
                {mode && (
                    <ReasonPanel
                        mode={mode}
                        busy={busy}
                        onCancel={() => setMode(null)}
                        onSubmit={({ reason, comment }) => onAct({ action: mode, fields: { reason, comment }, files: [] })}
                    />
                )}
            </>
            {navigator}
        </div>
    );
};

/* issueSlot — место в нижней панели окна заявки для «Не хватает: N ⌃ ⌄»: кнопка этапа —
   внизу формы, а после нажатия человек уже наверху, у первого пустого поля. */
const PaymentStageForm = ({
    data, subtask, dictionaries, users, meta, apiBaseUrl, headers, busy, showToast, permissions,
    onAct, onTake, onEdit, onReply, onAttach, loadCard, issueSlot = null,
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
    const [entityId, setEntityId] = useState(null);
    // Нажали кнопку этапа, а чего-то не хватало: с этого момента пустые поля красные.
    const [reveal, setReveal] = useState(false);
    const formRef = useRef(null);

    const kinds = new Set([...own.map((item) => item.kind), ...files.map((entry) => entry.kind)]);
    const issues = stageIssues(kind, { request, paid, received, assets, kinds, queued: files.length, entityId });
    const byKey = issuesByKey(issues);
    const keys = issueKeys(issues);
    const nav = useIssueNav(formRef, keys);
    const issueOf = (key) => (reveal && byKey[key] ? issueSentence(byKey[key].map((item) => item.text)) : '');
    /* Кнопка этапа: не хватает обязательного — действие не уходит, поля краснеют, и окно
       подводит к первому из них (нажали с клавиатуры — фокус в это поле). Панель «вернуть /
       запросить информацию» при этом закрывается: человек выбрал действие этапа. И наоборот —
       открыли панель, красные поля этапа гаснут: тому, кто запрашивает информацию, «Приложите
       платёжное поручение» как обязательное не нужно (разбор 14.10.2026). */
    const attempt = (run) => (event) => {
        if (busy) return;
        if (issues.length) { setMode(null); setReveal(true); nav.first({ focus: fromKeyboard(event) }); return; }
        run();
    };
    const toggleMode = (next) => { setMode((prev) => (prev === next ? null : next)); setReveal(false); };
    const act = (action, fields = {}, withFiles = true) => onAct({
        action, fields: { comment: comment.trim(), ...fields }, files: withFiles ? files : [],
    });
    const invoiceFiles = all.filter((item) => item.kind === 'invoice');
    const closingFiles = all.filter((item) => (meta?.closing_doc_kinds || ['act', 'waybill', 'receipt']).includes(item.kind));
    const counterparty = (dictionaries?.counterparties || []).find((item) => item.id === request.counterparty_id) || null;
    const chips = (label, list) => (
        <FileChips label={label} attachments={list} apiBaseUrl={apiBaseUrl} headers={headers} showToast={showToast} />
    );

    // Ступень задачи: на последней — поля и итоговое действие, после первой — «Запросить информацию».
    const final = stageAtFinal(kind, subtask.status);
    const taken = stageTaken(kind, subtask.status);

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

    const reasonPanel = mode ? (
        <ReasonPanel
            mode={mode}
            busy={busy}
            reasons={data.clarify_reasons || []}
            onCancel={() => setMode(null)}
            onSubmit={({ reason, comment: text }) => onAct({ action: mode, fields: { reason, comment: text }, files: [] })}
        />
    ) : null;

    /* В нижней панели окна — своей строкой над кнопками: в ряду с переключателем статуса
       (у бухгалтерии в нём четыре статуса) и «К заявке» / «Готово» стрелкам места нет —
       «Готово» уезжал бы отдельной строкой. */
    const navigator = reveal && issueSlot && keys.length > 1
        ? createPortal(<div className="order-first w-full"><IssueNav count={keys.length} onStep={nav.step} /></div>, issueSlot)
        : null;

    const wrap = (children) => (
        <div ref={formRef} data-stage-form={kind} className="space-y-3 rounded-2xl bg-white p-3.5 ring-1 ring-blue-100">
            {head}
            {children}
            {navigator}
        </div>
    );

    // ── Инициатор: черновик, доработка, ответ на запрос ──
    if (kind === 'initiation') {
        const problems = data.submit_problems || [];
        const asked = request.clarify_reason;
        return wrap(
            <>
                <ReturnNotice request={request} subtask={subtask} />
                {problems.length > 0 && <Missing items={problems} />}
                {problems.length === 0 && (
                    <Comment value={comment} onChange={setComment} placeholder="Ответ тому, кто запросил (необязательно)" />
                )}
                <div className="flex flex-wrap items-center gap-2">
                    <button type="button" className={problems.length ? iosBtnPrimary : iosBtnSecondary} disabled={busy} onClick={() => onEdit?.({ reveal: problems.length > 0 })}>
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

    // ── Helpdesk IT: IT-закуп после руководителя (просьба владельца 04.11.2026) ──
    if (kind === 'it_review') {
        return (
            <HelpdeskStage data={data} subtask={subtask} dictionaries={dictionaries} users={users} meta={meta} apiBaseUrl={apiBaseUrl}
                headers={headers} busy={busy} permissions={permissions} onAct={onAct} onTake={onTake} issueSlot={issueSlot} />
        );
    }

    // ── Бухгалтерия: компания, которой не знал инициатор (просьба владельца 15.10.2026) ──
    // Под рукой — то, по чему решают, чья это покупка: подразделение, инициатор, проект и
    // филиал. Что закупают и у кого — в сводке над страницами.
    if (kind === 'entity_clarify') {
        const entityOptions = (dictionaries?.legal_entities || []).map((item) => ({ value: item.id, label: item.name }));
        return wrap(
            <>
                <div className="rounded-xl bg-slate-50 px-3 py-1.5">
                    <Row label="Подразделение">{request.department_name}</Row>
                    <Row label="Инициатор">{request.initiator_name}</Row>
                    <Row label="Проект">{request.project_name}</Row>
                    <Row label="Филиал / регион">{request.branch}</Row>
                </div>
                <Field label="Компания" required optionalMark={false} hint={HINTS.entityClarify} anchor="legal_entity_id" issue={issueOf('legal_entity_id')}>
                    <FormSelect
                        value={entityId}
                        onChange={(value) => setEntityId(value || null)}
                        options={entityOptions}
                        placeholder={entityOptions.length ? 'Выберите компанию' : 'Справочник компаний пуст'}
                        disabled={!entityOptions.length}
                        searchable={entityOptions.length > 8}
                        ariaLabel="Компания"
                    />
                </Field>
                <Comment value={comment} onChange={setComment} placeholder="Комментарий (необязательно)" />
                <button type="button" className={iosBtnPrimary} disabled={busy} onClick={attempt(() => act('set_entity', { legal_entity_id: entityId }, false))}>
                    {busy && <Loader2 size={14} className="animate-spin" />} Указать компанию
                </button>
            </>,
        );
    }

    // ── Согласование (п. 7) ──
    if (kind === 'manager_approval' || kind === 'approval') {
        const offerFiles = all.filter((item) => item.kind === 'offer' || item.kind === 'supplier_registry');
        return wrap(
            <>
                {chips('Коммерческие предложения:', offerFiles)}
                {/* Решение — когда заявку взяли на рассмотрение (кнопка внизу окна). */}
                {final && (
                    <>
                        <Comment value={comment} onChange={setComment} placeholder="Комментарий к согласованию (необязательно)" />
                        <div className="flex flex-wrap items-center gap-2">
                            <button type="button" className={iosBtnPrimary} disabled={busy} onClick={() => act('approve', {}, false)}>
                                {busy ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />} Согласовать
                            </button>
                            <button type="button" className={iosBtnSecondary} disabled={busy} onClick={() => toggleMode('return')}>
                                Вернуть на доработку
                            </button>
                            <button type="button" className={`${iosBtnGhost} !text-rose-600 hover:!bg-rose-50`} disabled={busy} onClick={() => toggleMode('reject')}>
                                Отклонить
                            </button>
                        </div>
                        {reasonPanel}
                    </>
                )}
            </>,
        );
    }

    // ── Бухгалтерия: оплата счёта (п. 8) ──
    if (kind === 'invoice_payment') {
        const amount = parseAmount(request.amount);
        const approved = parseAmount(request.amount_approved);
        const check = data.contract_check || {};
        const lastPayments = (data.history || []).filter((row) => (row.matched || []).includes('counterparty')).slice(0, 3);
        return wrap(
            <>
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
                {/* Поля оплаты и «Оплачено» — на ступени «На оплате»; до неё — проверка счёта. */}
                {final && (
                    <>
                        <div className="grid gap-3 sm:grid-cols-2">
                            <Field label="Дата оплаты" required optionalMark={false} anchor="paid_on" issue={issueOf('paid_on')}>
                                <FormDate value={paid.paid_on} onChange={(value) => setPaid((prev) => ({ ...prev, paid_on: value || '' }))} ariaLabel="Дата оплаты" />
                            </Field>
                            <Field label="Фактическая сумма, ₸" required optionalMark={false} hint="Сколько ушло поставщику. Подставлена сумма заявки — поправьте, если оплатили другую." anchor="paid_amount" issue={issueOf('paid_amount')}>
                                <AmountInput value={paid.paid_amount} onChange={(value) => setPaid((prev) => ({ ...prev, paid_amount: value }))} ariaLabel="Фактическая сумма" />
                            </Field>
                        </div>
                        <Field as="div" label="Платёжное поручение" required optionalMark={false} anchor="files" issue={issueOf('files')}>
                            <FilePicker files={files} onChange={setFiles} kinds={fileKindOptions('invoice_payment')} label="Приложить платёжное поручение" />
                        </Field>
                        <Comment value={comment} onChange={setComment} placeholder="Комментарий к оплате (необязательно)" />
                    </>
                )}
                {taken && (
                    <div className="flex flex-wrap items-center gap-2">
                        {final && (
                            <button type="button" className={iosBtnPrimary} disabled={busy} onClick={attempt(() => act('pay', paid))}>
                                {busy && <Loader2 size={14} className="animate-spin" />} Оплачено
                            </button>
                        )}
                        <button type="button" className={iosBtnSecondary} disabled={busy} onClick={() => toggleMode('request_info')}>
                            Запросить информацию
                        </button>
                    </div>
                )}
                {reasonPanel}
            </>,
        );
    }

    // ── Финансовый отдел: пополнение карты (п. 9) ──
    if (kind === 'card_topup') {
        return wrap(
            <>
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
                {/* Поля перевода и «Пополнено» — на ступени «Готово к пополнению». */}
                {final && (
                    <>
                        <div className="grid gap-3 sm:grid-cols-2">
                            <Field label="Дата перевода" required optionalMark={false} anchor="paid_on" issue={issueOf('paid_on')}>
                                <FormDate value={paid.paid_on} onChange={(value) => setPaid((prev) => ({ ...prev, paid_on: value || '' }))} ariaLabel="Дата перевода" />
                            </Field>
                            <Field label="Сумма перевода, ₸" required optionalMark={false} anchor="paid_amount" issue={issueOf('paid_amount')}>
                                <AmountInput value={paid.paid_amount} onChange={(value) => setPaid((prev) => ({ ...prev, paid_amount: value }))} ariaLabel="Сумма перевода" />
                            </Field>
                        </div>
                        <Field as="div" label="Подтверждение перевода" required optionalMark={false} anchor="files" issue={issueOf('files')}>
                            <FilePicker files={files} onChange={setFiles} kinds={fileKindOptions('card_topup')} label="Приложить подтверждение" />
                        </Field>
                        <Comment value={comment} onChange={setComment} placeholder="Комментарий к переводу (необязательно)" />
                    </>
                )}
                {taken && (
                    <div className="flex flex-wrap items-center gap-2">
                        {final && (
                            <button type="button" className={iosBtnPrimary} disabled={busy} onClick={attempt(() => act('top_up', paid))}>
                                {busy && <Loader2 size={14} className="animate-spin" />} Пополнено
                            </button>
                        )}
                        <button type="button" className={iosBtnSecondary} disabled={busy} onClick={() => toggleMode('request_info')}>
                            Запросить информацию
                        </button>
                    </div>
                )}
                {reasonPanel}
            </>,
        );
    }

    // ── Инициатор: чек (п. 5.3) ──
    if (kind === 'receipt_confirm') {
        return wrap(
            <>
                <div className="text-[12.5px] text-slate-600">
                    На карту переведено {fmtMoney(request.paid_amount ?? request.amount)}{request.paid_on ? ` · ${fmtDate(request.paid_on)}` : ''}.
                </div>
                <Field as="div" label="Чек" required optionalMark={false} anchor="files" issue={issueOf('files')}>
                    <FilePicker files={files} onChange={setFiles} kinds={fileKindOptions('receipt_confirm')} label="Приложить чек" />
                </Field>
                <Comment value={comment} onChange={setComment} placeholder="Комментарий (необязательно)" />
                <button type="button" className={iosBtnPrimary} disabled={busy} onClick={attempt(() => act('provide'))}>
                    {busy && <Loader2 size={14} className="animate-spin" />} Отправить чек
                </button>
            </>,
        );
    }

    // ── Инициатор: получение (п. 10.1) ──
    if (kind === 'receiving') {
        const goods = request.object_type === 'goods';
        return wrap(
            <>
                <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="Дата получения" required optionalMark={false} anchor="received_on" issue={issueOf('received_on')}>
                        <FormDate value={received.received_on} onChange={(value) => setReceived((prev) => ({ ...prev, received_on: value || '' }))} ariaLabel="Дата получения" />
                    </Field>
                    {goods && (
                        <Field label="Количество получено" required optionalMark={false} hint={HINTS.received} anchor="received_quantity" issue={issueOf('received_quantity')}>
                            <FormInput value={received.received_quantity} onChange={(event) => setReceived((prev) => ({ ...prev, received_quantity: event.target.value }))} maxLength={200} />
                        </Field>
                    )}
                </div>
                <Field as="div" label="Закрывающие документы" hint="Накладная, акт или чек — если уже на руках. Можно приложить и позже: бухгалтерия будет ждать.">
                    <FilePicker files={files} onChange={setFiles} kinds={fileKindOptions('receiving')} label="Приложить документ" />
                </Field>
                <Comment value={comment} onChange={setComment} placeholder="Комментарий (необязательно)" />
                <button type="button" className={iosBtnPrimary} disabled={busy} onClick={attempt(() => act('confirm', received))}>
                    {busy && <Loader2 size={14} className="animate-spin" />} Подтвердить получение
                </button>
            </>,
        );
    }

    // ── Ответственный за учёт имущества (п. 10.2, п. 11) ──
    if (kind === 'asset_registration') {
        // Карточки имущества — когда задачу взяли в работу (кнопка внизу окна).
        if (!final) return wrap(null);
        return wrap(
            <>
                <PaymentAssetsEditor assets={assets} onChange={setAssets} users={users} dictionaries={dictionaries} issueOf={issueOf} />
                <Field as="div" label="Акт приёма-передачи" required optionalMark={false} hint="Подписанный акт передачи имущества ответственному сотруднику." anchor="files" issue={issueOf('files')}>
                    <FilePicker files={files} onChange={setFiles} kinds={fileKindOptions('asset_registration')} label="Приложить акт" />
                </Field>
                <Comment value={comment} onChange={setComment} placeholder="Комментарий (необязательно)" />
                <button type="button" className={iosBtnPrimary} disabled={busy} onClick={attempt(() => act('register', { assets: assets.map(assetPayload) }))}>
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
                <Field as="div" label="Добавить скан" hint="Если скан или оригинал пришёл к вам, а не через инициатора, — приложите его сами." anchor="files" issue={issueOf('files')}>
                    {/* Скан выбран, но не загружен — красной рамкой обведена «Загрузить», а не выбор файла. */}
                    <FilePicker files={files} onChange={setFiles} kinds={fileKindOptions('closing_docs')} label="Приложить документ" invalid={files.length > 0 ? false : undefined} />
                    {files.length > 0 && (
                        <button type="button" className={`${iosBtnSecondary} mt-2${issueOf('files') ? ` ${INVALID_RING}` : ''}`} disabled={busy} onClick={async () => { if (await onAttach(files)) setFiles([]); }}>
                            {busy && <Loader2 size={14} className="animate-spin" />} Загрузить
                        </button>
                    )}
                </Field>
                <Comment value={comment} onChange={setComment} placeholder="Комментарий (необязательно)" />
                <div className="flex flex-wrap items-center gap-2">
                    {status !== 'original' && (
                        <button type="button" className={iosBtnSecondary} disabled={busy} onClick={() => act('docs_original', {}, false)}>
                            Оригинал получен
                        </button>
                    )}
                    <button type="button" className={iosBtnPrimary} disabled={busy} onClick={attempt(() => act('docs_close', {}, false))}>
                        {busy && <Loader2 size={14} className="animate-spin" />} Закрыть документы
                    </button>
                </div>
            </>,
        );
    }

    return null;
};

export default PaymentStageForm;
