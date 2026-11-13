import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  NO_ACCOUNT_LABEL, accountTitle, almatyToday, autoAccountId, branchIds, canAddTopic, cardAccountText, countReasons,
  errorText, eventIsForCall, filterCategories, flattenReasons, fmtClock,
  fmtDate, fmtSeconds, fmtShare, fmtSize, fmtTime, fmtWhen, isTopicPicked, looksLikeAccountLink, parseCallMessage,
  periodPreset, pickedInBranch, quoteOf, reasonLevelTitle, reportReasonRows, saveLabel, senderColor, toggleTopic,
  topicLabel, topicsPayload,
  uploadsProblem, wrapupLeft,
} from '../src/components/client_cards/cardRules.js';

/* «Карточка клиента» (задача #335): правила экранов — страницы карточки в iCORE Phone
   (src/client_card_embed) и раздела портала (src/components/client_cards). Серверные
   правила — tests/test_client_cards.py. */

const source = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');

// ─── Время: всегда по Алматы, какой бы пояс ни стоял на компьютере ───────────

test('время звонка показывается по Алматы', () => {
  // 09:05 UTC = 14:05 в Алматы.
  assert.equal(fmtTime('2026-11-12T14:05:00+05:00'), '14:05');
  assert.equal(fmtTime('2026-11-12T09:05:00Z'), '14:05');
  assert.equal(fmtDate('2026-11-12T23:30:00+05:00'), '12.11.2026');
  // 20:30 UTC 11-го — это уже 12-е в Алматы.
  assert.equal(fmtDate('2026-11-11T20:30:00Z'), '12.11.2026');
  assert.equal(fmtTime(''), '');
  assert.equal(fmtDate('не дата'), '');
});

test('«когда» — коротко для сегодня и вчера, с датой для остального', () => {
  const now = new Date('2026-11-12T10:00:00+05:00');
  assert.equal(fmtWhen('2026-11-12T09:15:00+05:00', now), '09:15');
  assert.equal(fmtWhen('2026-11-11T22:40:00+05:00', now), 'вчера 22:40');
  assert.equal(fmtWhen('2026-11-03T08:00:00+05:00', now), '03.11 08:00');
  assert.equal(fmtWhen('2025-12-31T23:59:00+05:00', now), '31.12.2025 23:59');
  assert.equal(fmtWhen(null, now), '');
  assert.equal(almatyToday(new Date('2026-11-11T20:30:00Z')), '2026-11-12');
});

test('отсчёт, длительность, доля и размер файла', () => {
  assert.equal(fmtClock(0), '0:00');
  assert.equal(fmtClock(65.9), '1:05');
  assert.equal(fmtClock(3725), '1:02:05');
  assert.equal(fmtClock(-5), '0:00');
  assert.equal(fmtSeconds(null), '—');
  assert.equal(fmtSeconds(45.4), '45 с');
  assert.equal(fmtSeconds(65), '1 мин 05 с');
  assert.equal(fmtShare(3, 8), '37,5 %');
  assert.equal(fmtShare(0, 8), '0,0 %');
  assert.equal(fmtShare(3, 0), '—');
  assert.equal(fmtSize(900), '1 КБ');
  assert.equal(fmtSize(860 * 1024), '860 КБ');
  assert.equal(fmtSize(13 * 1024 * 1024), '13,0 МБ');
});

// ─── Причины ─────────────────────────────────────────────────────────────────

const ORDERS = { id: 1, title: 'Заказы', children: [{ id: 11, title: 'Не приходят заказы' }, { id: 12, title: 'Отмена заказа' }] };
const MONEY = { id: 2, title: 'Выплаты', children: [] };
// Дерево из просьбы владельца: основная причина → подтема 1-го уровня → подтема 2-го уровня.
const FINISH = { id: 311, title: 'Не может завершить заказ', children: [] };
const ABORT = { id: 312, title: 'Не может отменить заказ', children: [] };
const ORDER_PROBLEM = { id: 31, title: 'Проблема с заказом', children: [FINISH, ABORT] };
const PAY = { id: 32, title: 'Вопрос по оплате', children: [] };
const ASK = { id: 3, title: 'Запрос', children: [ORDER_PROBLEM, PAY] };

test('причина выбирается и снимается повторным нажатием', () => {
  let topics = toggleTopic([], [ORDERS, ORDERS.children[0]]);
  assert.deepEqual(topics, [{ category_id: 1, subcategory_id: 11, title: 'Заказы', sub_title: 'Не приходят заказы',
    path: ['Заказы', 'Не приходят заказы'] }]);
  assert.equal(topicLabel(topics[0]), 'Заказы → Не приходят заказы');
  topics = toggleTopic(topics, [MONEY]);
  assert.equal(topicLabel(topics[1]), 'Выплаты');
  assert.equal(isTopicPicked(topics, [ORDERS, ORDERS.children[0]]), true);
  assert.equal(isTopicPicked(topics, [ORDERS, ORDERS.children[1]]), false);
  assert.equal(isTopicPicked(topics, [MONEY]), true);
  assert.equal(pickedInBranch(topics, ORDERS), 1);
  assert.deepEqual(topicsPayload(topics), [
    { category_id: 1, subcategory_id: 11 }, { category_id: 2, subcategory_id: null }]);
  topics = toggleTopic(topics, [ORDERS, ORDERS.children[0]]);
  assert.deepEqual(topics.map(topicLabel), ['Выплаты']);
  // Исходный список не меняется — React сравнивает по ссылке.
  const frozen = Object.freeze([]);
  assert.notEqual(toggleTopic(frozen, [MONEY]), frozen);
});

test('причина третьего уровня: серверу — основная причина и выбранная подтема, на экране — весь путь', () => {
  let topics = toggleTopic([], [ASK, ORDER_PROBLEM, FINISH]);
  assert.deepEqual(topics, [{ category_id: 3, subcategory_id: 311, title: 'Запрос',
    sub_title: 'Проблема с заказом → Не может завершить заказ',
    path: ['Запрос', 'Проблема с заказом', 'Не может завершить заказ'] }]);
  assert.equal(topicLabel(topics[0]), 'Запрос → Проблема с заказом → Не может завершить заказ');
  assert.deepEqual(topicsPayload(topics), [{ category_id: 3, subcategory_id: 311 }]);
  topics = toggleTopic(topics, [ASK, PAY]);
  // Счётчик свёрнутой ветки — по всем её уровням.
  assert.equal(pickedInBranch(topics, ASK), 2);
  assert.equal(pickedInBranch(topics, ORDER_PROBLEM), 1);
  assert.equal(pickedInBranch(topics, ABORT), 0);
  assert.equal(isTopicPicked(topics, [ASK, ORDER_PROBLEM, FINISH]), true);
  assert.equal(isTopicPicked(topics, [ASK, ORDER_PROBLEM, ABORT]), false);
  assert.deepEqual(toggleTopic(topics, [ASK, ORDER_PROBLEM, FINISH]).map(topicLabel), ['Запрос → Вопрос по оплате']);
  assert.deepEqual(branchIds(ASK), [3, 31, 311, 312, 32]);
  // Причина без поля path читается по title и sub_title.
  assert.equal(topicLabel({ title: 'Запрос', sub_title: 'Проблема с заказом → Не может завершить заказ' }),
    'Запрос → Проблема с заказом → Не может завершить заказ');
  assert.equal(topicLabel(null), '');
});

test('сверх предела причина не добавляется, а снять выбранную можно', () => {
  const full = [1, 2].map((id) => ({ category_id: id, subcategory_id: null, title: `К${id}`, sub_title: null }));
  assert.equal(canAddTopic(full, 2), false);
  assert.equal(toggleTopic(full, [{ id: 3, title: 'К3' }], 2), full);
  assert.equal(toggleTopic(full, [{ id: 2, title: 'К2' }], 2).length, 1);
  assert.equal(canAddTopic([], 2), true);
});

test('поиск по причинам: причина со всей веткой или только подходящие подтемы — на любой глубине', () => {
  const all = [ORDERS, MONEY, ASK];
  assert.equal(filterCategories(all, ''), all);
  assert.deepEqual(filterCategories(all, 'ЗАКАЗЫ').map((item) => item.title), ['Заказы']);
  assert.equal(filterCategories(all, 'заказы')[0].children.length, 2);
  const byChild = filterCategories(all, 'отмена');
  assert.deepEqual(byChild.map((item) => [item.title, item.children.map((sub) => sub.title)]), [['Заказы', ['Отмена заказа']]]);
  // Нашлась подтема 2-го уровня — остаётся только её ветка: Запрос → Проблема с заказом → она.
  const deep = filterCategories(all, 'завершить');
  assert.deepEqual(deep.map((item) => item.title), ['Запрос']);
  assert.deepEqual(deep[0].children.map((item) => [item.title, item.children.map((leaf) => leaf.title)]),
    [['Проблема с заказом', ['Не может завершить заказ']]]);
  // Нашлась середина ветки — она со всеми своими подтемами.
  assert.equal(filterCategories(all, 'проблема')[0].children[0].children.length, 2);
  assert.deepEqual(filterCategories(all, 'такого нет'), []);
  assert.deepEqual(filterCategories(null, 'x'), []);
  assert.equal(countReasons(all), 9);
  assert.equal(countReasons(null), 0);
});

test('дерево причин списком для фильтра журнала — с полным путём', () => {
  assert.deepEqual(flattenReasons([ASK, MONEY]).map((item) => [item.id, item.label, item.depth]), [
    [3, 'Запрос', 1],
    [31, 'Запрос → Проблема с заказом', 2],
    [311, 'Запрос → Проблема с заказом → Не может завершить заказ', 3],
    [312, 'Запрос → Проблема с заказом → Не может отменить заказ', 3],
    [32, 'Запрос → Вопрос по оплате', 2],
    [2, 'Выплаты', 1]]);
  assert.deepEqual(flattenReasons(undefined), []);
  assert.deepEqual([1, 2, 3].map(reasonLevelTitle), ['Основная причина', 'Подтема 1-го уровня', 'Подтема 2-го уровня']);
});

test('отчёт по причинам: ветка раскрывается уровень за уровнем, «без подтемы» — своей строкой', () => {
  const report = [{ id: 3, title: 'Запрос', count: 9, own: 0, children: [
    { id: 31, title: 'Проблема с заказом', count: 7, own: 2, children: [
      { id: 311, title: 'Не может завершить заказ', count: 4, own: 4, children: [] },
      { id: 312, title: 'Не может отменить заказ', count: 1, own: 1, children: [] }] },
    { id: 32, title: 'Вопрос по оплате', count: 3, own: 3, children: [] }] },
  { id: 2, title: 'Выплаты', count: 1, own: 1, children: [] }];
  const rows = (open) => reportReasonRows(report, (id) => open.includes(id)).map((row) => [row.title, row.count, row.depth]);
  assert.deepEqual(rows([]), [['Запрос', 9, 0], ['Выплаты', 1, 0]]);
  assert.deepEqual(rows([3]), [['Запрос', 9, 0], ['Проблема с заказом', 7, 1], ['Вопрос по оплате', 3, 1], ['Выплаты', 1, 0]]);
  assert.deepEqual(rows([3, 31]), [['Запрос', 9, 0], ['Проблема с заказом', 7, 1], ['Не может завершить заказ', 4, 2],
    ['Не может отменить заказ', 1, 2], ['без подтемы', 2, 2], ['Вопрос по оплате', 3, 1], ['Выплаты', 1, 0]]);
  // Раскрыта только подтема без раскрытой причины над ней — её строк не видно.
  assert.deepEqual(rows([31]), rows([]));
  const [root, , leaf] = reportReasonRows(report, () => true);
  assert.equal(root.hasChildren && root.open, true);
  assert.equal(leaf.hasChildren, false);
  // Причина без подтем не раскрывается, а её own строкой не дублируется.
  assert.deepEqual(reportReasonRows([report[1]], () => true).map((row) => row.title), ['Выплаты']);
  assert.deepEqual(reportReasonRows(null, () => true), []);
});

// ─── Аккаунт ─────────────────────────────────────────────────────────────────

test('подпись аккаунта и подсказка о ссылке', () => {
  assert.equal(accountTitle({ full_name: ' Иванов Иван ', tez_id: 12345 }), 'Иванов Иван · ID 12345');
  assert.equal(accountTitle({ full_name: null, tez_id: 67890 }), 'ID 67890');
  assert.equal(accountTitle(null), '');
  // Два вида ссылок, названные владельцем 12.11.2026.
  assert.equal(looksLikeAccountLink('https://admin.tezapp.org/admin/drivers/12345'), true);
  assert.equal(looksLikeAccountLink('https://admin.tezapp.org/admin/clients/67890'), true);
  assert.equal(looksLikeAccountLink(' admin.tezapp.org/admin/drivers/12345/edit?tab=1 '), true);
  assert.equal(looksLikeAccountLink('https://admin.tezapp.org/admin/orders/5'), false);
  assert.equal(looksLikeAccountLink('https://example.com/admin/drivers/5'), false);
  assert.equal(looksLikeAccountLink('12345'), false);
});

test('единственный привязанный аккаунт подставляется сам, несколько — выбирает оператор', () => {
  const open = { status: 'open', account_id: null };
  assert.equal(autoAccountId(open, [{ id: 7 }]), 7);
  assert.equal(autoAccountId(open, [{ id: 7 }, { id: 8 }]), null);
  assert.equal(autoAccountId(open, []), null);
  assert.equal(autoAccountId({ status: 'open', account_id: 9 }, [{ id: 7 }]), null);
  assert.equal(autoAccountId({ status: 'done', account_id: null }, [{ id: 7 }]), null);
  assert.equal(autoAccountId(null, [{ id: 7 }]), null);
});

test('«Аккаунта нет»: отметку оператора не перебивает найденный позже аккаунт, в списках она видна', () => {
  // Оператор сказал «аккаунта нет» — запоздавший ответ TEZ APP с единственным аккаунтом его не подставит.
  assert.equal(autoAccountId({ status: 'open', account_id: null, no_account: true }, [{ id: 7 }]), null);
  assert.equal(autoAccountId({ status: 'open', account_id: null, no_account: false }, [{ id: 7 }]), 7);
  assert.equal(NO_ACCOUNT_LABEL, 'Аккаунта нет');
  const account = { kind_label: 'Водитель', full_name: 'Иванов Иван', tez_id: 12345 };
  assert.equal(cardAccountText({ account }), 'Водитель · Иванов Иван · ID 12345');
  assert.equal(cardAccountText({ account: null, no_account: true }, 'Аккаунт не указан'), 'Аккаунта нет');
  assert.equal(cardAccountText({ account: null, no_account: false }, 'Аккаунт не указан'), 'Аккаунт не указан');
  assert.equal(cardAccountText(null), '');
  // Аккаунт есть — он и показан, даже если отметка почему-то осталась.
  assert.equal(cardAccountText({ account, no_account: true }), 'Водитель · Иванов Иван · ID 12345');
});

test('карточка звонка: строка «Аккаунта нет» ведёт к причинам так же, как выбранный аккаунт', () => {
  const card = readFileSync(new URL('../src/client_card_embed/CallCard.jsx', import.meta.url), 'utf8').replace(/\r\n/g, '\n');
  assert.match(card, /function NoAccountRow\(/);
  assert.match(card, /const noAccount = Boolean\(card\.no_account\) && !account;/);
  assert.match(card, /const personKnown = Boolean\(account\) \|\| noAccount;/);
  // Отметка ставится своей ручкой, снимается обычным «снять выбор аккаунта».
  assert.match(card, /noAccount \? api\.chooseAccount\(cardId, null\) : api\.noAccount\(cardId\)/);
  // Причины, комментарий и запрос в отдел открываются по personKnown, а не только по аккаунту.
  assert.equal((card.match(/\{personKnown && \(/g) || []).length, 3);
  assert.doesNotMatch(card, /\{account && \(\n\s*<section/);
  // Город — только у аккаунта: у звонившего без аккаунта его нет.
  assert.match(card, /\{account && !cityKnownFromTez && \(/);
  const api = readFileSync(new URL('../src/components/client_cards/cardsApi.js', import.meta.url), 'utf8');
  assert.match(api, /noAccount: \(cardId\) => axios\.post\(url\(`\/cards\/\$\{cardId\}\/account`\), \{ none: true \}, config\(\)\)/);
});

// ─── Постобработка и кнопка ──────────────────────────────────────────────────

test('отсчёт постобработки идёт от момента ответа сервера, а не по часам компьютера', () => {
  const card = { state: 'wrapup', wrapup_left_sec: 60 };
  assert.equal(wrapupLeft(card, 1_000_000, 1_000_000), 60);
  assert.equal(wrapupLeft(card, 1_000_000, 1_020_400), 40);
  assert.equal(wrapupLeft(card, 1_000_000, 1_090_000), 0);
  assert.equal(wrapupLeft({ state: 'talking', wrapup_left_sec: null }, 0, 0), null);
  assert.equal(wrapupLeft({ state: 'overdue', wrapup_left_sec: null }, 0, 0), null);
  assert.equal(wrapupLeft(null, 0, 0), null);
  assert.equal(saveLabel({ status: 'open' }), 'Сохранить карточку');
  assert.equal(saveLabel({ status: 'done' }), 'Сохранить изменения');
});

// ─── Мост с телефоном ────────────────────────────────────────────────────────

test('сообщения телефона о звонке', () => {
  assert.deepEqual(parseCallMessage({ type: 'call:start', call_key: 'abc', phone: '+77012345678', direction: 'out' }),
    { kind: 'start', callKey: 'abc', phone: '+77012345678', direction: 'out' });
  // Строкой (WebView2 отдаёт и так) и без направления — входящий.
  assert.deepEqual(parseCallMessage('{"type":"call:start","phone":"77012345678"}'),
    { kind: 'start', callKey: '', phone: '77012345678', direction: 'in' });
  assert.deepEqual(parseCallMessage({ type: 'call:answered', call_key: 'abc' }), { kind: 'answered', callKey: 'abc' });
  assert.deepEqual(parseCallMessage({ type: 'call:ended', call_key: 'abc' }), { kind: 'ended', callKey: 'abc' });
  // Начало звонка без номера карточку не открывает; чужие сообщения — не про звонок.
  assert.equal(parseCallMessage({ type: 'call:start', call_key: 'abc' }), null);
  assert.equal(parseCallMessage({ type: 'auth:token', token: 'x' }), null);
  assert.equal(parseCallMessage('не json'), null);
  assert.equal(parseCallMessage(null), null);
});

test('событие относится к карточке на экране по идентификатору звонка', () => {
  assert.equal(eventIsForCall({ callKey: 'abc' }, 'abc'), true);
  assert.equal(eventIsForCall({ callKey: 'abc' }, 'other'), false);
  // Телефон не прислал идентификатор или карточку открыли руками — событие текущей карточке.
  assert.equal(eventIsForCall({ callKey: '' }, 'abc'), true);
  assert.equal(eventIsForCall({ callKey: 'abc' }, null), true);
});

// ─── Запросы ─────────────────────────────────────────────────────────────────

test('цитата ответа ищется в загруженной нити', () => {
  const messages = [
    { id: 1, tg_message_id: 100, body: 'Запрос', author_name: 'Анна' },
    { id: 2, tg_message_id: 101, body: 'Уточните дату', author_name: 'Айгуль', reply_to_tg_message_id: 100 },
    { id: 3, tg_message_id: 102, body: '10.11', author_name: 'Анна', reply_to_tg_message_id: 101 },
    { id: 4, tg_message_id: 103, body: '', file: { name: 'выписка.pdf' }, author_name: 'Айгуль' },
    { id: 5, tg_message_id: 104, body: 'Спасибо', reply_to_tg_message_id: 103 },
    { id: 6, tg_message_id: 105, body: 'Потерянный ответ', reply_to_tg_message_id: 999 },
  ];
  // Реплай на сам запрос цитатой не показываем: вся нить — о нём.
  assert.equal(quoteOf(messages[1], messages), null);
  assert.deepEqual(quoteOf(messages[2], messages), { author: 'Айгуль', text: 'Уточните дату' });
  assert.deepEqual(quoteOf(messages[4], messages), { author: 'Айгуль', text: 'выписка.pdf' });
  assert.equal(quoteOf(messages[5], messages), null);
  assert.equal(quoteOf(messages[0], messages), null);
});

test('цвет имени держится на id отправителя, а не на имени', () => {
  const first = senderColor({ tg_from_id: 77, author_name: 'Айгуль' });
  assert.equal(senderColor({ tg_from_id: 77, author_name: 'Айгуль Финансы' }), first);
  assert.match(first, /^text-[a-z]+-600$/);
  assert.match(senderColor({}), /^text-[a-z]+-600$/);
});

test('файлы проверяются до отправки по пределам сервера', () => {
  const limits = { request_files: 2, request_file_bytes: 1024 };
  assert.equal(uploadsProblem([{ name: 'a.png', size: 10 }], limits), null);
  assert.equal(uploadsProblem([{ name: 'a', size: 1 }, { name: 'b', size: 1 }, { name: 'c', size: 1 }], limits),
    'Не больше 2 файлов');
  assert.match(uploadsProblem([{ name: 'видео.mp4', size: 4096 }], limits), /Файл «видео\.mp4» больше/);
  assert.equal(uploadsProblem([], null), null);
});

// ─── Период и ошибки ─────────────────────────────────────────────────────────

test('готовые периоды отчёта', () => {
  assert.deepEqual(periodPreset('today', '2026-11-12'), { from: '2026-11-12', to: '2026-11-12' });
  assert.deepEqual(periodPreset('yesterday', '2026-11-01'), { from: '2026-10-31', to: '2026-10-31' });
  assert.deepEqual(periodPreset('week', '2026-11-12'), { from: '2026-11-06', to: '2026-11-12' });
  assert.deepEqual(periodPreset('month', '2026-11-12'), { from: '2026-11-01', to: '2026-11-12' });
  assert.deepEqual(periodPreset('prev_month', '2026-03-31'), { from: '2026-02-01', to: '2026-02-28' });
  assert.deepEqual(periodPreset('prev_month', '2026-01-15'), { from: '2025-12-01', to: '2025-12-31' });
});

test('ошибка сервера — его словами, обрыв связи — по-русски', () => {
  assert.equal(errorText({ response: { data: { error: 'Выберите аккаунт' } } }), 'Выберите аккаунт');
  assert.equal(errorText({ message: 'Network Error' }), 'Нет связи с порталом');
  assert.equal(errorText({ response: { data: '<html>' } }, 'Не открылось'), 'Не открылось');
  assert.equal(errorText({ response: { data: { error: '  ' } } }), 'Не получилось — попробуйте ещё раз');
});

// ─── Раздел подключён ────────────────────────────────────────────────────────

test('страница карточки собирается отдельной точкой входа и держит адрес API дословно', () => {
  const vite = source('../vite.config.mjs');
  assert.match(vite, /clientCard: resolve\(rootDir, 'client_card\.html'\)/);
  assert.match(source('../client_card.html'), /src="\/src\/client_card_embed\/main\.jsx"/);
  // Адрес API — той же строкой, что у офисов и тренажёров: её переписывает конфиг стенда.
  const line = "const API_BASE_URL = import.meta.env?.VITE_API_BASE_URL || 'https://api.icorehub.kz';";
  assert.ok(source('../src/client_card_embed/ClientCardEmbed.jsx').includes(line));
  assert.ok(source('../src/offices_embed/OfficesEmbed.jsx').includes(line));
});

test('раздел подключён в портал: меню, охрана доступа, замок QR и колокол', () => {
  const app = source('../src/App.jsx');
  assert.match(app, /const canAccessClientCardsForUser = \(userLike\) => userLike\?\.client_cards_access === true;/);
  assert.match(app, /requestedViewFromUrl !== 'client_cards' \|\| canAccessClientCardsSection/);
  assert.match(app, /if \(view === 'client_cards' && !canAccessClientCardsSection\) \{/);
  assert.match(app, /if \(view === 'client_cards' && canAccessClientCardsSection\) return;/);
  assert.match(app, /handleSidebarViewNavigation\(e, 'client_cards'\)/);
  // У рядового раздел закрыт до QR — двойник замка в client_cards/routes.py.
  assert.match(app, /view === 'client_cards' && canAccessClientCardsSection && \(syntonyLocked \? \(\s*<SensitiveSectionGate/);
  assert.match(app, /nextView === 'client_cards' && Number\(target\)/);
  // Метки ссылки раздела стираются из адреса других разделов — в обоих местах сборки адреса.
  assert.equal(app.split("url.searchParams.delete('cc_request');").length - 1, 2);
  // Метка запроса своя: request носит «Оплата счетов», и портал стирает её у чужих разделов.
  const view = source('../src/components/client_cards/ClientCardsView.jsx');
  assert.match(view, /searchParams\.get\('cc_request'\)/);
  assert.match(source('../client_cards/telegram.py'), /view=client_cards&cc_request=%d/);
  assert.match(source('../src/components/notifications/NotificationsBell.jsx'), /^\s{4}client_cards: \{ label: 'Запросы в отделы'/m);
});

test('системных выпадашек и полей даты в разделе нет', () => {
  // Правило портала: <select> и input[type=date] рисует ОС — в интерфейсе iOS/macOS это чужая деталь.
  for (const file of ['ClientCardsView', 'JournalTab', 'ReportTab', 'SettingsTab', 'RequestsPanel', 'RequestComposer', 'CardDetail']) {
    const code = source(`../src/components/client_cards/${file}.jsx`);
    assert.doesNotMatch(code, /<select\b/, file);
    assert.doesNotMatch(code, /type="date"/, file);
    assert.doesNotMatch(code, /<datalist\b/, file);
  }
  for (const file of ['ClientCardEmbed', 'CallCard', 'HistoryTab']) {
    const code = source(`../src/client_card_embed/${file}.jsx`);
    assert.doesNotMatch(code, /<select\b/, file);
    assert.doesNotMatch(code, /<datalist\b/, file);
  }
});
