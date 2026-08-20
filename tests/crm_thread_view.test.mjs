import test from 'node:test';
import assert from 'node:assert/strict';

import {
  AUTHOR_TONES, attachmentKind, authorBadge, authorInitials, authorKey, authorTone,
  continuesRun, dayKey, dayLabel, groupByDay, indexByTgId,
  messageSnippet, quoteOf,
} from '../src/components/crm/threadView.js';

/* В нить обращения падает ВСЯ ветка обсуждения из группы: сотрудники отвечают
 * и боту, и друг другу. Эти правила отвечают на «кто кому» и «кто говорит». */

const msg = (over = {}) => ({ id: 1, direction: 'in', body: 'текст', ...over });

test('цвет имени держится за id из Telegram, а не за имя', () => {
  const before = msg({ telegram_user_id: 77, author_name: 'Гаухар' });
  const after = msg({ telegram_user_id: 77, author_name: 'Gaukhar K.' });
  assert.equal(authorTone(before), authorTone(after), 'смена имени перекрасила автора');
  assert.equal(authorKey(before), 'tg:77');
});

test('разные люди получают разные цвета', () => {
  const tones = new Set([11, 12, 13, 14].map((id) => authorTone(msg({ telegram_user_id: id }))));
  assert.ok(tones.size >= 3, 'слишком мало различий: ' + tones.size);
});

test('цвет всегда из палитры и всегда есть', () => {
  for (const m of [msg(), msg({ author_name: null }), msg({ author_user_id: 2 })]) {
    assert.ok(AUTHOR_TONES.includes(authorTone(m)));
  }
  assert.ok(AUTHOR_TONES.includes(authorTone(null)));
});

test('цитата берёт сообщение, на которое ответили', () => {
  const root = msg({ id: 1, tg_message_id: 100, body: 'Документы не поступили', direction: 'out' });
  const answer = msg({ id: 2, tg_message_id: 101, reply_to_tg_message_id: 100,
                       author_name: 'Гаухар' });
  const quote = quoteOf(answer, indexByTgId([root, answer]));
  assert.equal(quote.id, 1);
  assert.equal(quote.text, 'Документы не поступили');
  assert.equal(quote.author, 'Оператор');
  assert.equal(quote.missing, false);
});

test('обычное сообщение цитаты не имеет', () => {
  assert.equal(quoteOf(msg({ tg_message_id: 5 }), indexByTgId([])), null);
});

test('недоступную цель показываем честно, а не прячем', () => {
  const quote = quoteOf(msg({ reply_to_tg_message_id: 999 }), indexByTgId([]));
  assert.equal(quote.missing, true);
  assert.equal(quote.text, 'Сообщение недоступно');
});

test('сообщение не цитирует само себя', () => {
  const self = msg({ id: 3, tg_message_id: 7, reply_to_tg_message_id: 7 });
  assert.equal(quoteOf(self, indexByTgId([self])), null);
});

test('в цитате вложение без текста называется, а не пустует', () => {
  assert.equal(messageSnippet(msg({ body: '', attachment: { kind: 'photo' } })), 'Фото');
  assert.equal(messageSnippet(msg({ body: '', attachment: { kind: 'document', name: 'акт.pdf' } })),
               'акт.pdf');
  assert.equal(messageSnippet(msg({ body: '' })), 'Без текста');
});

test('длинная цитата обрезается и не тащит переносы строк', () => {
  const long = messageSnippet(msg({ body: 'первая строка\nвторая ' + 'я'.repeat(200) }), 40);
  assert.equal(long.length, 40);
  assert.ok(long.endsWith('…'));
  assert.ok(!long.includes('\n'));
});

test('вид вложения определяется и по типу, и по имени файла', () => {
  assert.equal(attachmentKind({ kind: 'photo' }), 'image');
  assert.equal(attachmentKind({ mime: 'image/png' }), 'image');
  assert.equal(attachmentKind({ name: 'screen.JPG' }), 'image');
  assert.equal(attachmentKind({ mime: 'video/mp4' }), 'video');
  assert.equal(attachmentKind({ name: 'запись.ogg' }), 'audio');
  assert.equal(attachmentKind({ name: 'акт.pdf' }), 'file');
  assert.equal(attachmentKind(null), null);
});

/* ─── Кружок с инициалами и разбивка по дням ─────────────────────────────── */

test('инициалы берутся из двух слов, «@ник» даёт одну букву', () => {
  assert.equal(authorInitials('Асхат Нурланов'), 'АН');
  assert.equal(authorInitials('Гаухар'), 'Г');
  assert.equal(authorInitials('@nick'), 'N');
  assert.equal(authorInitials('ivan.petrov'), 'IP');
  assert.equal(authorInitials(''), '?');
  assert.equal(authorInitials(null), '?');
});

test('кружок и имя одного сотрудника одного цвета', () => {
  const message = msg({ telegram_user_id: 91, author_name: 'Асхат Нурланов' });
  const badge = authorBadge(message);
  assert.equal(badge.tone, authorTone(message));
  // Фон соответствует тому же индексу палитры, что и цвет имени.
  assert.equal(AUTHOR_TONES.indexOf(badge.tone) >= 0, true);
  assert.equal(badge.initials, 'АН');
});

test('день считается по календарю, а не по срезу строки', () => {
  assert.equal(dayKey('2026-08-20T23:30:00'), '2026-08-20');
  assert.notEqual(dayKey('2026-08-20T23:30:00'), dayKey('2026-08-21T01:00:00'));
  assert.equal(dayKey(null), '');
  assert.equal(dayKey('не дата'), '');
});

test('подпись дня: сегодня, вчера, дата, дата с годом', () => {
  const now = new Date('2026-08-20T12:00:00');
  assert.equal(dayLabel('2026-08-20T09:00:00', now), 'Сегодня');
  assert.equal(dayLabel('2026-08-19T23:59:00', now), 'Вчера');
  assert.equal(dayLabel('2026-08-12T09:00:00', now), '12 августа');
  assert.equal(dayLabel('2025-08-12T09:00:00', now), '12 августа 2025');
  assert.equal(dayLabel(null, now), '');
});

test('первое число месяца: «вчера» — это прошлый месяц', () => {
  const now = new Date('2026-09-01T10:00:00');
  assert.equal(dayLabel('2026-08-31T20:00:00', now), 'Вчера');
});

test('нить делится на дни одним проходом', () => {
  const now = new Date('2026-08-20T12:00:00');
  const groups = groupByDay([
    msg({ id: 1, created_at: '2026-08-19T10:00:00' }),
    msg({ id: 2, created_at: '2026-08-20T09:00:00' }),
    msg({ id: 3, created_at: '2026-08-20T11:00:00' }),
  ], now);
  assert.deepEqual(groups.map((g) => [g.label, g.items.length]),
                   [['Вчера', 1], ['Сегодня', 2]]);
});

test('пустая нить не рождает пустых дней', () => {
  assert.deepEqual(groupByDay([]), []);
  assert.deepEqual(groupByDay(null), []);
  assert.deepEqual(groupByDay([null, undefined]), []);
});

test('серия — это тот же автор, та же сторона и тот же день', () => {
  const first = msg({ id: 1, telegram_user_id: 5, created_at: '2026-08-20T10:00:00' });
  const same = msg({ id: 2, telegram_user_id: 5, created_at: '2026-08-20T10:01:00' });
  const other = msg({ id: 3, telegram_user_id: 6, created_at: '2026-08-20T10:02:00' });
  const nextDay = msg({ id: 4, telegram_user_id: 5, created_at: '2026-08-21T10:00:00' });
  const outgoing = msg({ id: 5, direction: 'out', telegram_user_id: 5, created_at: '2026-08-20T10:03:00' });
  assert.equal(continuesRun(first, same), true);
  assert.equal(continuesRun(first, other), false);
  assert.equal(continuesRun(first, nextDay), false);
  assert.equal(continuesRun(first, outgoing), false);
  // У самого первого сообщения нити предшественника нет.
  assert.equal(continuesRun(undefined, first), false);
});
