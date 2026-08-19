import test from 'node:test';
import assert from 'node:assert/strict';

import {
  AUTHOR_TONES, attachmentKind, authorKey, authorTone, indexByTgId,
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
