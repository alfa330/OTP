import test from 'node:test';
import assert from 'node:assert/strict';

import {
  chatBillingAverages,
  chatBillingHourLabel,
  chatBillingMinutes,
  formatChatBillingMinutes,
  formatChatBillingMinutesUnit,
  formatChatBillingRating,
} from '../src/components/resources/chatBillingMetrics.js';

// Intl в node может ставить неразрывный пробел — сравниваем по видимому тексту.
const plain = (value) => String(value).replace(/ /g, ' ');

test('время — только минуты с одним знаком, как в постановке: 2,5 мин, 7 мин, 12 мин', () => {
  assert.equal(formatChatBillingMinutes(150), '2,5');
  assert.equal(formatChatBillingMinutes(420), '7');
  assert.equal(formatChatBillingMinutes(720), '12');
  assert.equal(plain(formatChatBillingMinutesUnit(150)), '2,5 мин');
  assert.equal(formatChatBillingMinutes(null), '—');
  assert.equal(formatChatBillingMinutesUnit(undefined), '—');
});

test('округление «половина вверх» совпадает с выгрузкой в Excel', () => {
  // Те же значения проверяет tests/test_chat_billing_report.py (_chat_billing_minutes).
  const cases = [[135, 2.3], [150, 2.5], [420, 7], [720, 12], [3, 0.1], [2, 0], [1047, 17.5]];
  for (const [seconds, expected] of cases) {
    assert.equal(chatBillingMinutes(seconds), expected, String(seconds));
  }
});

test('у каждого среднего свой знаменатель', () => {
  const averages = chatBillingAverages({
    chats: 10, answered: 8, answered_sl: 5, first_reply_seconds: 1200,
    inner_reply_seconds: 900, inner_replied: 6, rating_sum: 13, rated: 3,
  });
  assert.equal(averages.firstReplySeconds, 150);
  assert.equal(averages.innerReplySeconds, 150);
  assert.equal(averages.sl, 0.5);
  assert.equal(formatChatBillingRating(averages.rating), '4,3');
  const empty = chatBillingAverages({ chats: 3, answered: 0 });
  assert.equal(empty.firstReplySeconds, null);
  assert.equal(empty.innerReplySeconds, null);
  assert.equal(empty.rating, null);
  assert.equal(formatChatBillingRating(empty.rating), '—');
  assert.equal(formatChatBillingRating(5), '5,0');
  // Как в ежедневном отчёте СЗоВ: один знак, половина вверх.
  assert.equal(formatChatBillingRating(4.25), '4,3');
});

test('час группировки — промежуток', () => {
  assert.equal(chatBillingHourLabel(9), '09:00–10:00');
  assert.equal(chatBillingHourLabel(23), '23:00–00:00');
  assert.equal(chatBillingHourLabel(0), '00:00–01:00');
});
