/*
 * «Почта» (#417): правила экрана без React — что форма считает «не хватает», как говорит о
 * сроке, как сверка проверяет строки и как настройки сворачивают маршруты туда-обратно.
 * Тексты пунктов — те же, что у сервера (mail/rules.py); подписи кодов сверяет
 * tests/test_mail_rules.py::FrontMirrorTests.
 *
 * Запуск: node --test tests/mail_meta.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {
    ACTION_LABELS, MENU_ACTIONS, PRIMARY_ORDER, SECONDARY_ACTIONS, actionLayout, almatyNow, blankForm, collapseRoutePairs,
    deadlineCell, deadlineText, deadlineTone, economyReady, formIssues, formToPayload, officeLabel, primaryAction,
    reconcileIssues, recipientDefaults, statusTone,
} from '../src/components/mail/mailMeta.js';

const CONTEXT = {
    me: { name: 'Алматинцева Айгуль', phone: '+77010000010' },
    default_sender_office_id: 101,
    legal_entities: [{ id: 7, name: 'ТОО' }],
};

test('новая форма: офис, отправитель и единственный плательщик подставлены', () => {
    const form = blankForm(CONTEXT);
    assert.equal(form.sender_office_id, 101);
    assert.equal(form.sender_name, 'Алматинцева Айгуль');
    assert.equal(form.payer_entity_id, 7);
    assert.equal(form.items.length, 1);
    assert.equal(blankForm({ ...CONTEXT, legal_entities: [{ id: 1 }, { id: 2 }] }).payer_entity_id, null);
});

test('черновик: «Иное» без описания не сохраняется, остальное — можно', () => {
    const form = blankForm(CONTEXT);
    form.items[0] = { ...form.items[0], doc_type: 'other', other_description: '  ' };
    assert.deepEqual(formIssues(form).map((issue) => issue.key), ['items.0.other_description']);
    form.items[0].other_description = 'Кепки';
    assert.deepEqual(formIssues(form), []);
});

test('«Готово к отправке» требует полноты — те же поля, что сервер', () => {
    const form = blankForm(CONTEXT);
    const keys = formIssues(form, { ready: true }).map((issue) => issue.key).sort();
    assert.deepEqual(keys, ['recipient_address', 'recipient_name', 'recipient_office_id', 'recipient_phone', 'weight_kg']);
    Object.assign(form, {
        recipient_office_id: 103, recipient_name: 'Б', recipient_phone: '+7', recipient_address: 'ул. 1', weight_kg: '0,4',
    });
    assert.deepEqual(formIssues(form, { ready: true }), []);
    form.recipient_office_id = 101;
    assert.equal(formIssues(form)[0].key, 'recipient_office_id');
});

test('в запрос уходят числа с точкой и без описания у не-«Иного»', () => {
    const form = blankForm(CONTEXT);
    form.weight_kg = '1 250,5';
    form.items[0] = { ...form.items[0], doc_type: 'contract', other_description: 'хвост', qty: '3' };
    const payload = formToPayload(form);
    assert.equal(payload.weight_kg, '1250.5');
    assert.equal(payload.items[0].other_description, null);
    assert.equal(payload.items[0].qty, '3');
});

test('адрес офиса получения подставляется, пока человек его не правил', () => {
    let form = recipientDefaults({ recipient_address: '' }, { address: 'ул. Ленина, 3' });
    assert.equal(form.recipient_address, 'ул. Ленина, 3');
    form = recipientDefaults(form, { address: 'пр. Абая, 1' });
    assert.equal(form.recipient_address, 'пр. Абая, 1');
    form = recipientDefaults({ ...form, recipient_address: 'склад, ворота 2' }, { address: 'ул. Ленина, 3' });
    assert.equal(form.recipient_address, 'склад, ворота 2');
});

test('срок словами и цветом', () => {
    assert.equal(deadlineText({ kind: 'overdue', days: -2 }, '2026-11-12'), 'просрочено на 2 рабочих дня');
    assert.equal(deadlineText({ kind: 'today', days: 0 }, '2026-11-13'), 'срок сегодня');
    assert.equal(deadlineText({ kind: 'soon', days: 1 }, '2026-11-16'), 'срок 16.11.2026 — следующий рабочий день');
    assert.equal(deadlineText({ kind: 'ok', days: 5 }, '2026-11-20'), 'до 20.11.2026 · ещё 5 рабочих дней');
    assert.equal(deadlineText(null, '2026-11-20'), 'до 20.11.2026');
    assert.equal(deadlineText(null, null), '');
    assert.equal(deadlineTone({ kind: 'soon' }), 'soon');
    assert.equal(statusTone({ status: 'in_transit', overdue: true }), 'overdue');
    assert.equal(statusTone({ status: 'in_transit' }), 'neutral');
    assert.equal(statusTone({ status: 'delivered' }), 'blocked');
});

test('колонка «Срок»: едет — срок, вручено — в срок или позже, до передачи — пусто', () => {
    assert.deepEqual(deadlineCell({ deadline: { kind: 'today', days: 0 }, due_on: '2026-11-13' }),
        { text: 'срок сегодня', tone: 'soon' });
    assert.deepEqual(deadlineCell({ deadline: null, delivered_on: '2026-11-10', due_on: '2026-11-11' }),
        { text: 'вручено 10.11.2026 · в срок', tone: 'normal' });
    assert.deepEqual(deadlineCell({ deadline: null, delivered_on: '2026-11-12', due_on: '2026-11-11' }),
        { text: 'вручено 12.11.2026 · позже срока', tone: 'soon' });
    assert.equal(deadlineCell({ deadline: null, delivered_on: null, due_on: null }), null);
});

test('сверка: каждая строка отмечена, расхождение — с количеством и комментарием', () => {
    const items = [{ id: 1, qty: 5 }, { id: 2, qty: 3 }];
    const lines = [{ id: 1, verdict: null }, { id: 2, verdict: 'ok' }];
    assert.deepEqual(reconcileIssues(lines, items, { needDate: true, deliveredOn: '', note: '' }).map((issue) => issue.key),
        ['delivered_on', 'lines.0.line_status']);
    lines[0] = { id: 1, verdict: 'bad', line_status: 'shortage', received_qty: '5' };
    assert.deepEqual(reconcileIssues(lines, items, { needDate: false, note: '' }).map((issue) => issue.key),
        ['lines.0.received_qty', 'note']);
    lines[0].received_qty = '4';
    assert.deepEqual(reconcileIssues(lines, items, { needDate: false, note: 'нет одного' }), []);
});

test('маршруты туда-обратно с одной зоной — одной строкой', () => {
    const rows = collapseRoutePairs([
        { from_city: 'Алматы', to_city: 'Павлодар', zone_code: '1' },
        { from_city: 'Павлодар', to_city: 'Алматы', zone_code: '1' },
        { from_city: 'Алматы', to_city: 'Шымкент', zone_code: '1' },
        { from_city: 'Шымкент', to_city: 'Алматы', zone_code: '3' },
    ]);
    assert.deepEqual(rows.map((row) => [row.from_city, row.to_city, row.both_ways]), [
        ['Алматы', 'Павлодар', true], ['Алматы', 'Шымкент', false], ['Шымкент', 'Алматы', false],
    ]);
});

test('у каждого шага сервера есть кнопка: главная, рядом или в меню «⋯»', () => {
    const placed = new Set([...PRIMARY_ORDER, ...SECONDARY_ACTIONS, ...MENU_ACTIONS]);
    Object.keys(ACTION_LABELS).forEach((code) => assert.ok(placed.has(code), `нет кнопки у шага ${code}`));
    // «Готово к отправке»: и заявка курьеру, и сразу передача — обе доступны.
    assert.deepEqual(actionLayout(['courier_request', 'hand_over', 'cancel']),
        { primary: 'hand_over', secondary: ['courier_request'], menu: ['cancel'] });
    assert.deepEqual(actionLayout(['in_transit', 'start_return']),
        { primary: null, secondary: ['in_transit'], menu: ['start_return'] });
});

test('эконом без заданных сроков — пункт «не хватает» у поля типа доставки', () => {
    const form = { ...blankForm(CONTEXT), recipient_office_id: 103, recipient_name: 'Б', recipient_phone: '+7',
        recipient_address: 'ул. 1', weight_kg: '1', delivery_type: 'economy' };
    assert.deepEqual(formIssues(form, { ready: true, economyAllowed: false }).map((issue) => issue.key), ['delivery_type']);
    assert.deepEqual(formIssues(form, { ready: true, economyAllowed: true }), []);
    assert.equal(economyReady([{ eco_max_days: null }]), false);
    assert.equal(economyReady([{ eco_max_days: 12 }]), true);
});

test('офис подписан без повтора города', () => {
    assert.equal(officeLabel({ city: 'Алматы', name: 'Центральный' }), 'Алматы · Центральный');
    assert.equal(officeLabel({ city: 'Павлодар', name: 'Павлодар' }), 'Павлодар');
    assert.equal(officeLabel({ city: 'Шымкент', name: 'Шымкент Центр' }), 'Шымкент Центр');
    assert.equal(officeLabel(null), '');
});

test('главный шаг', () => {
    assert.equal(primaryAction(['cancel', 'courier_request', 'hand_over']), 'hand_over');
    assert.equal(primaryAction(['in_transit', 'not_delivered']), null);
});

test('сейчас по Алматы — от UTC+5, а не от пояса браузера', () => {
    const now = almatyNow(new Date('2026-11-13T20:30:00Z'));
    assert.deepEqual(now, { date: '2026-11-14', time: '01:30' });
});
