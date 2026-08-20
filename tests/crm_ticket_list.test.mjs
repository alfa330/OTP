import test from 'node:test';
import assert from 'node:assert/strict';

import {
  isOverdue,
  markTicketSeen,
  mergeTicketsById,
  previewAuthor,
  previewText,
  queueMonogram,
  queueTile,
  rowAlert,
  rowBadges,
  unreadLabel,
  BADGE_LIMIT,
} from '../src/components/crm/ticketList.js';

/* Строка ленты обращений. Проверяется здесь, а не глазами в разделе, по той же
 * причине, что и wizardRules: раньше это была разметка, и любая ошибка в ней
 * находилась только на проде. */

test('монограмма берёт по букве из двух слов', () => {
  assert.equal(queueMonogram('iTaxi Sapar'), 'iS');
  assert.equal(queueMonogram('Яндекс Доставка'), 'ЯД');
});

test('монограмма одного слова — две первые буквы, регистр не меняется', () => {
  assert.equal(queueMonogram('Посылки'), 'По');
  assert.equal(queueMonogram('iTaxi'), 'iT');
});

test('очередь без названия не роняет строку', () => {
  assert.equal(queueMonogram(''), '—');
  assert.equal(queueMonogram(null), '—');
  assert.equal(queueMonogram('   '), '—');
});

test('цвет плитки постоянен для очереди и не зависит от названия', () => {
  assert.equal(queueTile(7), queueTile(7));
  assert.equal(typeof queueTile(undefined), 'string');
  assert.equal(typeof queueTile('нечисло'), 'string');
});

test('своя реплика подписывается «Вы», чужая — именем без фамилии', () => {
  assert.equal(previewAuthor({ direction: 'out', author_name: 'Иван Петров' }), 'Вы');
  assert.equal(previewAuthor({ direction: 'in', author_name: 'Асхат Нурланов' }), 'Асхат');
  assert.equal(previewAuthor({ direction: 'in', author_name: '@nick' }), 'nick');
});

test('у заметки и у безымянного входящего подписи нет', () => {
  assert.equal(previewAuthor({ direction: 'note', author_name: 'Бот' }), null);
  assert.equal(previewAuthor({ direction: 'in', author_name: '' }), null);
  assert.equal(previewAuthor(null), null);
});

test('превью схлопывает переносы и обрезает длинное', () => {
  assert.equal(previewText({ body: 'первая\nвторая   строка' }), 'первая вторая строка');
  const long = 'я'.repeat(200);
  const short = previewText({ body: long }, 20);
  assert.equal(short.length, 20);
  assert.ok(short.endsWith('…'));
});

test('вложение без текста показывается словом, а не пустотой', () => {
  assert.equal(previewText({ body: '', attachment: { kind: 'photo' } }), 'Фото');
  assert.equal(previewText({ body: null, attachment: { kind: 'document', name: 'акт.pdf' } }),
               'акт.pdf');
  // Неизвестный вид вложения тоже не должен давать пустую строку.
  assert.equal(previewText({ attachment: { kind: 'sticker' } }), 'Файл');
});

test('нет последней реплики — нет превью', () => {
  assert.equal(previewText(null), '');
  assert.equal(previewText({ body: '   ' }), '');
});

test('пузырёк непрочитанного: пусто на нуле, «99+» на сотне', () => {
  assert.equal(unreadLabel(0), '');
  assert.equal(unreadLabel(undefined), '');
  assert.equal(unreadLabel(-3), '');
  assert.equal(unreadLabel(1), '1');
  assert.equal(unreadLabel(99), '99');
  assert.equal(unreadLabel(100), '99+');
});

test('просрочка считается только у незакрытых', () => {
  const now = Date.parse('2026-08-20T12:00:00');
  const overdue = { due_at: '2026-08-20T10:00:00', status: 'answered' };
  assert.equal(isOverdue(overdue, now), true);
  assert.equal(isOverdue({ ...overdue, status: 'resolved' }, now), false);
  assert.equal(isOverdue({ ...overdue, status: 'cancelled' }, now), false);
  assert.equal(isOverdue({ due_at: null, status: 'open' }, now), false);
});

test('недоставленное важнее просроченного', () => {
  const now = Date.parse('2026-08-20T12:00:00');
  const ticket = { due_at: '2026-08-20T10:00:00', status: 'open', delivery_status: 'failed' };
  assert.equal(rowAlert(ticket, now), 'failed');
  assert.equal(rowAlert({ ...ticket, delivery_status: 'sent' }, now), 'overdue');
  assert.equal(rowAlert({ status: 'open', delivery_status: 'sent' }, now), null);
});

/* Склейка страниц — из-за неё и завёлся модуль: порядок «непрочитанное сверху»
 * сдвигается от прочтения, и догрузка через OFFSET перестала быть безобидной. */

test('догрузка не плодит дубли', () => {
  const merged = mergeTicketsById([{ id: 1 }, { id: 2 }, { id: 3 }],
                                  [{ id: 3 }, { id: 4 }, { id: 5 }]);
  assert.deepEqual(merged.map((t) => t.id), [1, 2, 3, 4, 5]);
});

test('у совпавшей строки берётся свежая версия', () => {
  const merged = mergeTicketsById([{ id: 1, unread_count: 3 }],
                                  [{ id: 1, unread_count: 5 }, { id: 2 }]);
  assert.deepEqual(merged, [{ id: 1, unread_count: 5 }, { id: 2 }]);
});

test('склейка выдерживает пустоту с любой стороны', () => {
  assert.deepEqual(mergeTicketsById(null, [{ id: 1 }]), [{ id: 1 }]);
  assert.deepEqual(mergeTicketsById([{ id: 1 }], null), [{ id: 1 }]);
  assert.deepEqual(mergeTicketsById(undefined, undefined), []);
});

test('гашение непрочитанного правит только свою строку', () => {
  const tickets = [{ id: 1, unread: true, unread_count: 4 }, { id: 2, unread: true, unread_count: 1 }];
  const next = markTicketSeen(tickets, 1);
  assert.deepEqual(next[0], { id: 1, unread: false, unread_kind: null, unread_count: 0 });
  assert.equal(next[1], tickets[1]);
});

test('гасить нечего — массив тот же самый (лента не перерисовывается)', () => {
  const tickets = [{ id: 1, unread: false, unread_count: 0 }];
  assert.equal(markTicketSeen(tickets, 1), tickets);
  assert.equal(markTicketSeen(tickets, 777), tickets);
});

/* Бейджи в строке. Их число — не вкусовщина: третий переносит строку, и ряды в
 * ленте становятся рваными (замерено: 79 / 98 / 79 / 98 px). */

const meta = (status, priority) => ({ status, priority });
const TONE = { label: 'Есть ответ', tone: 'blue' };
const NEUTRAL = { label: 'Отправлено', tone: null };
const NORMAL = { label: 'Обычный', tone: null };
const NOW = Date.parse('2026-08-20T12:00:00');
const labels = (ticket, m) => rowBadges(ticket, m, NOW).map((b) => b.label);

test('бейджей никогда больше двух', () => {
  const ticket = {
    status: 'in_progress', due_at: '2026-08-18T15:00:00',
    delivery_status: 'sent', flags: ['mass_outage'],
  };
  assert.equal(BADGE_LIMIT, 2);
  assert.deepEqual(labels(ticket, meta({ label: 'В работе', tone: 'amber' },
                                       { label: 'Критический', tone: 'red' })),
                   ['Просрочено', 'Критический']);
});

test('статус не дублирует пузырёк непрочитанного', () => {
  const ticket = { status: 'answered', delivery_status: 'sent', flags: [] };
  assert.deepEqual(labels({ ...ticket, unread: true }, meta(TONE, NORMAL)), []);
  assert.deepEqual(labels(ticket, meta(TONE, NORMAL)), ['Есть ответ']);
});

test('недоставленное вытесняет статус, но не приоритет', () => {
  const ticket = { status: 'open', delivery_status: 'failed', flags: [] };
  assert.deepEqual(labels(ticket, meta(TONE, { label: 'Высокий', tone: 'amber' })),
                   ['Не доставлено', 'Высокий']);
});

test('нейтральный статус и обычный приоритет бейджей не рождают', () => {
  assert.deepEqual(labels({ status: 'open', delivery_status: 'sent', flags: [] },
                          meta(NEUTRAL, NORMAL)), []);
  assert.deepEqual(rowBadges(null), []);
});

test('массовый сбой показывается, когда есть место', () => {
  const ticket = { status: 'answered', delivery_status: 'sent', flags: ['mass_outage'], unread: true };
  assert.deepEqual(labels(ticket, meta(TONE, NORMAL)), ['Массовый сбой']);
});
