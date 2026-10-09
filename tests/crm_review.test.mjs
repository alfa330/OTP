import test from 'node:test';
import assert from 'node:assert/strict';

import {
  REVIEW_FILTER, REVIEW_PENDING, REVIEW_RESOLVED, REVIEW_SENT,
  isOverdue, isUnderReview, lockedReplyText, reviewToast, rowAlert, rowBadges, stateFilters,
  statusView,
} from '../src/components/crm/ticketList.js';
import { createdToast, deliveryWording } from '../src/components/crm/wizardRules.js';
import {
  REVIEW_NOTE_TAG, authorBadge, continuesRun, groupByDay, threadBubble,
} from '../src/components/crm/threadView.js';

/* Проверка супервайзером до группы: «Сотрудничество с Яндексом» (возврат задачи
 * #297). В группу такое обращение само не уходит — супервайзер решает: «Решено»
 * с итогом или «Отправить в группу».
 *
 * Здесь — слова и признаки интерфейса. Они не падают, если сломаются: оператор
 * просто прочитает «Отправлено» про обращение, которое никуда не ушло. */

const SENT = { label: 'Отправлено', tone: null };
const RESOLVED = { label: 'Решено', tone: 'green' };
const NORMAL = { label: 'Обычный', tone: null };

const ticket = (over = {}) => ({
  id: 110, status: 'open', delivery_status: 'pending', flags: [], review_state: REVIEW_PENDING,
  ...over,
});

const labels = (item, status = SENT) => rowBadges(item, { status, priority: NORMAL })
  .map((badge) => `${badge.label}:${badge.tone}`);

/* ─── Строка ленты ────────────────────────────────────────────────────────── */

test('обращение на проверке узнаётся по состоянию проверки, а не по статусу', () => {
  assert.equal(isUnderReview(ticket()), true);
  for (const state of [null, undefined, REVIEW_SENT, REVIEW_RESOLVED]) {
    assert.equal(isUnderReview(ticket({ review_state: state })), false, String(state));
  }
  assert.equal(isUnderReview(null), false);
});

test('«Ждёт проверки» горит только у того, чья это задача', () => {
  // review_mine считает сервер — тем же правилом, что счётчик и колокол.
  assert.deepEqual(labels(ticket({ review_mine: true })), ['Ждёт проверки:amber']);
  // Автору и всем остальным — спокойное «На проверке»: им тут делать нечего.
  assert.deepEqual(labels(ticket({ review_mine: false })), ['На проверке:slate']);
  assert.deepEqual(labels(ticket()), ['На проверке:slate']);
});

test('после решения строка снова читается как обычная', () => {
  // Отправлено супервайзером — штатное «Отправлено» бейджем не рисуется.
  assert.deepEqual(labels(ticket({ review_state: REVIEW_SENT, delivery_status: 'sent' })), []);
  assert.deepEqual(labels(ticket({ review_state: REVIEW_RESOLVED, status: 'resolved' }), RESOLVED),
    ['Решено:green']);
});

test('обращение на проверке не «не доставлено» и не «просрочено»', () => {
  // В группу оно не уходило — сбоя доставки нет; срока ответа группы тоже.
  assert.equal(rowAlert(ticket()), null);
  assert.equal(isOverdue(ticket({ due_at: null })), false);
});

test('сбой доставки после решения «в группу» важнее всего остального', () => {
  const failed = ticket({ review_state: REVIEW_SENT, delivery_status: 'failed' });
  assert.deepEqual(labels(failed), ['Не доставлено:red']);
});

/* ─── Шапка и подвал карточки ─────────────────────────────────────────────── */

test('в шапке карточки вместо «Отправлено» — «На проверке»', () => {
  assert.deepEqual(statusView(ticket(), SENT), { label: 'На проверке', tone: null });
  // Тон нейтральный: автору здесь делать нечего, красить нечего.
  assert.equal(statusView(ticket(), SENT).tone, null);
});

test('обычному обращению и решённому статус не подменяется', () => {
  assert.equal(statusView(ticket({ review_state: null }), SENT), SENT);
  assert.equal(statusView(ticket({ review_state: REVIEW_SENT }), SENT), SENT);
  assert.equal(statusView(ticket({ review_state: REVIEW_RESOLVED, status: 'resolved' }), RESOLVED),
    RESOLVED);
});

test('три разных «писать нельзя» объясняются тремя разными фразами', () => {
  const waiting = lockedReplyText(ticket());
  const reviewed = lockedReplyText(ticket({ review_state: REVIEW_RESOLVED, status: 'resolved' }));
  const closed = lockedReplyText(ticket({ review_state: null, status: 'resolved' }));
  assert.match(waiting, /Ждёт проверки супервайзером/);
  assert.equal(reviewed, 'В группу это обращение не уходило');
  assert.equal(closed, 'Обращение закрыто');
  assert.equal(new Set([waiting, reviewed, closed]).size, 3);
});

test('подвал решённого супервайзером не повторяет плашку над собой', () => {
  // «Решено супервайзером · дата» уже стоит под итогом — подвал добавляет
  // только то, чего на экране ещё нет.
  const reviewed = lockedReplyText(ticket({ review_state: REVIEW_RESOLVED, status: 'resolved' }));
  assert.doesNotMatch(reviewed, /Решено/);
});

test('отменённое и прочее закрытое — прежними словами', () => {
  assert.equal(lockedReplyText(ticket({ review_state: null, status: 'cancelled' })),
    'Обращение закрыто');
  assert.equal(lockedReplyText(ticket({ review_state: null, status: 'open' })),
    'Писать в это обращение нельзя');
  assert.equal(lockedReplyText(null), 'Писать в это обращение нельзя');
});

/* ─── Сегмент «На проверку» ───────────────────────────────────────────────── */

const BASE = [
  { key: 'active', label: 'В работе', statuses: 'open,in_progress,answered' },
  { key: 'all', label: 'Все', statuses: '' },
];

test('сегмента нет, пока проверять нечего', () => {
  // У оператора и у супервайзера без задач фильтр остаётся прежним.
  assert.equal(stateFilters(BASE, { reviewCount: 0, selected: 'active' }), BASE);
  assert.equal(stateFilters(BASE), BASE);
  assert.equal(stateFilters(BASE, { reviewCount: undefined, selected: 'all' }), BASE);
});

test('сегмент появляется с первой задачей и встаёт в конец', () => {
  const filters = stateFilters(BASE, { reviewCount: 2, selected: 'active' });
  // Остальные сегменты не сдвигаются: появление не дёргает то, на что смотрят.
  assert.deepEqual(filters.slice(0, BASE.length), BASE);
  assert.deepEqual(filters[BASE.length],
    { key: REVIEW_FILTER, label: 'На проверку', statuses: '', count: 2 });
});

test('выбранный сегмент не исчезает из-под руки', () => {
  // Последнее обращение решено, а человек ещё в этом фильтре.
  const filters = stateFilters(BASE, { reviewCount: 0, selected: REVIEW_FILTER });
  assert.equal(filters.length, BASE.length + 1);
  assert.equal(filters[BASE.length].count, 0);
});

test('число на сегменте — целое и неотрицательное', () => {
  for (const [raw, expected] of [['3', 3], [2.9, 2], [-1, 0], [null, 0], ['x', 0]]) {
    const filters = stateFilters(BASE, { reviewCount: raw, selected: REVIEW_FILTER });
    assert.equal(filters[BASE.length].count, expected, String(raw));
  }
});

test('сам список сегментов функция не портит', () => {
  const copy = JSON.parse(JSON.stringify(BASE));
  stateFilters(BASE, { reviewCount: 5 });
  assert.deepEqual(BASE, copy);
});

/* ─── Сообщения после решения ─────────────────────────────────────────────── */

test('«Решено» — про закрытие, «в группу» — про доставку', () => {
  assert.deepEqual(reviewToast('resolve', { delivered: null }),
    { tone: 'success', text: 'Решено — обращение закрыто' });
  assert.deepEqual(reviewToast('send', { delivered: true }),
    { tone: 'success', text: 'Обращение отправлено в группу' });
});

test('решение принято, а Telegram отказал — ошибка говорит ровно про доставку', () => {
  const toast = reviewToast('send', { delivered: false, delivery_error: 'бота нет в группе' });
  assert.equal(toast.tone, 'error');
  assert.equal(toast.text, 'В группу не ушло: бота нет в группе');
  assert.equal(reviewToast('send', { delivered: false }).text, 'В группу не ушло: ошибка Telegram');
  assert.equal(reviewToast('send', null).tone, 'error');
});

/* ─── Мастер: куда уйдёт обращение ────────────────────────────────────────── */

test('последний экран мастера не обещает группу обращению на проверку', () => {
  const review = deliveryWording(true);
  for (const text of Object.values(review)) {
    assert.doesNotMatch(text, /в групп/i, text);
  }
  assert.equal(review.submit, 'Отправить на проверку');
  assert.match(review.subtitle, /супервайзер/);
});

test('обычному обращению слова мастера прежние', () => {
  assert.deepEqual(deliveryWording(false), {
    subtitle: 'Так обращение увидят в группе',
    label: 'Сообщение в группу',
    submit: 'Подтвердить и отправить',
  });
  assert.deepEqual(deliveryWording(undefined), deliveryWording(false));
});

test('оба набора слов называют одни и те же места экрана', () => {
  assert.deepEqual(Object.keys(deliveryWording(true)).sort(),
    Object.keys(deliveryWording(false)).sort());
});

test('обращение на проверке после создания — не «не ушло в Telegram»', () => {
  // delivered у него false и ошибки нет: в группу оно и не должно было уйти.
  const toast = createdToast({ item: { id: 110 }, delivered: false, delivery_error: null,
                               review: true });
  assert.deepEqual(toast,
    { tone: 'success', text: 'Обращение №110 отправлено на проверку супервайзеру' });
});

test('обычное обращение после создания — прежними словами', () => {
  assert.deepEqual(
    createdToast({ item: { id: 7, tg_chat_title: 'Сотрудничество', queue_title: 'Очередь' },
                   delivered: true, review: false }),
    { tone: 'success', text: 'Обращение №7 отправлено в «Сотрудничество»' });
  assert.equal(
    createdToast({ item: { id: 7, queue_title: 'Очередь' }, delivered: true }).text,
    'Обращение №7 отправлено в «Очередь»');
  assert.deepEqual(
    createdToast({ item: { id: 7 }, delivered: false, delivery_error: 'бота нет в группе' }),
    { tone: 'error', text: 'Обращение №7 сохранено, но не ушло в Telegram: бота нет в группе' });
});

/* ─── Итог супервайзера в переписке ───────────────────────────────────────── */

const note = (over = {}) => ({
  id: 9, direction: 'note', body: 'Передала контакты директору', author_user_id: 55,
  author_name: 'Проверова Асель', created_at: '2026-10-07T11:30:00', ...over,
});

test('итог супервайзера стоит в переписке как ответ — с меткой, чей он', () => {
  const bubble = threadBubble(note());
  assert.equal(bubble.message.direction, 'in');
  assert.equal(bubble.message.body, 'Передала контакты директору');
  assert.equal(bubble.message.author_name, 'Проверова Асель');
  assert.deepEqual(bubble.tag, REVIEW_NOTE_TAG);
  assert.equal(REVIEW_NOTE_TAG.label, 'Итог проверки');
  // Жёлтое в разделе — то, что требует действия; итог — наоборот, точка.
  assert.notEqual(REVIEW_NOTE_TAG.tone, 'amber');
});

test('у итога есть кружок с инициалами проверяющего', () => {
  assert.equal(authorBadge(threadBubble(note()).message).initials, 'ПА');
});

test('остальные реплики проходят как были', () => {
  for (const direction of ['in', 'out']) {
    const message = { id: 1, direction, body: 'текст' };
    const bubble = threadBubble(message);
    assert.equal(bubble.message, message, 'лишняя копия перерисовывала бы пузырь');
    assert.equal(bubble.tag, null);
  }
  assert.deepEqual(threadBubble(null), { message: null, tag: null });
});

test('исходное сообщение не меняется', () => {
  const original = note();
  threadBubble(original);
  assert.equal(original.direction, 'note');
});

test('итог не склеивается в серию с файлом оператора', () => {
  // До решения в нити может лежать только файл, приложенный к обращению.
  const file = { id: 3, direction: 'out', author_user_id: 158, author_name: 'Оператор',
                 created_at: '2026-10-07T11:07:00', attachment: { kind: 'document', name: 'КП.pdf' } };
  assert.equal(continuesRun(file, note()), false);
  const days = groupByDay([file, note()], new Date('2026-10-07T12:00:00'));
  assert.equal(days.length, 1);
  assert.equal(days[0].items.length, 2);
});
