import { readFileSync } from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  buildWazzupChatLink,
  findWazzupChatExact,
  formatWazzupChatTarget,
  matchWazzupChatsByPhone,
  normalizePhoneDigits,
  normalizeWazzupChannelId,
  normalizeWazzupChatId,
  parseWazzupChatTarget,
  pickWazzupChatByPhone,
  readWazzupChatTargetFromSearch,
  syncWazzupChatDeepLink,
} from '../src/components/wazzup/chatLink.js';

/* Портал живёт на GitHub Pages с базовым путём /OTP — ссылка обязана строиться
   поверх него, а не от корня домена. */
const PORTAL = 'https://alfa330.github.io/OTP?view=wazzup_chats';
const CHANNEL = '34403077-8ba8-4bb0-8c1e-576c0ce0db8e';
const PHONE = '77784237140';

const replaced = [];

const useLocation = (href) => {
  const url = new URL(href);
  globalThis.window = {
    location: {
      href: url.toString(),
      origin: url.origin,
      pathname: url.pathname,
      search: url.search,
    },
    history: {
      state: { key: 'portal' },
      replaceState: (state, title, next) => replaced.push({ state, next }),
    },
  };
  replaced.length = 0;
};

const chat = (channelId, chatId, extra = {}) => ({
  channelId, chatId, chatType: 'whatsapp', contactName: null, contactPhone: chatId, ...extra,
});

/* ── Ссылка ────────────────────────────────────────────────────────────────── */

test('ссылка на чат строится поверх текущего адреса портала', () => {
  useLocation(PORTAL);
  // '/' в значении параметра URLSearchParams кодирует — читается обратно верно.
  assert.equal(
    buildWazzupChatLink(chat(CHANNEL, PHONE)),
    `https://alfa330.github.io/OTP?view=wazzup_chats&chat=${CHANNEL}%2F${PHONE}`
  );
});

test('метки перезагрузки в ссылку не уезжают', () => {
  useLocation(`${PORTAL}&v=1786951163258&auth_reload=1786533936834`);
  assert.equal(
    buildWazzupChatLink(chat(CHANNEL, PHONE)),
    `https://alfa330.github.io/OTP?view=wazzup_chats&chat=${CHANNEL}%2F${PHONE}`
  );
});

/* Ссылку на чат отправляют в рабочую переписку, поэтому чужие метки из неё
   вычищаются: человек, зашедший в портал по ссылке бота
   ?view=crm_tickets&ticket_id=812, иначе унёс бы номер чужого обращения в
   ссылку на чат. App снимает task_id и article сам, а ticket_id — нигде. */
test('ссылку строим и из другого раздела: view переписывается, чужие метки снимаются', () => {
  useLocation('https://alfa330.github.io/OTP?view=tasks&task_id=166');
  assert.equal(
    buildWazzupChatLink(chat(CHANNEL, PHONE)),
    `https://alfa330.github.io/OTP?view=wazzup_chats&chat=${CHANNEL}%2F${PHONE}`
  );
  useLocation('https://alfa330.github.io/OTP?view=crm_tickets&ticket_id=812');
  assert.equal(
    buildWazzupChatLink(chat(CHANNEL, PHONE)),
    `https://alfa330.github.io/OTP?view=wazzup_chats&chat=${CHANNEL}%2F${PHONE}`
  );
  useLocation('https://alfa330.github.io/OTP?view=wiki&article=some-slug');
  assert.equal(
    buildWazzupChatLink(chat(CHANNEL, PHONE)),
    `https://alfa330.github.io/OTP?view=wazzup_chats&chat=${CHANNEL}%2F${PHONE}`
  );
});

test('битая пара ссылки не даёт — вместо неё пустая строка', () => {
  useLocation(PORTAL);
  assert.equal(buildWazzupChatLink({ channelId: CHANNEL, chatId: '' }), '');
  assert.equal(buildWazzupChatLink({ channelId: '', chatId: PHONE }), '');
  assert.equal(buildWazzupChatLink(null), '');
  assert.equal(formatWazzupChatTarget(chat(CHANNEL, PHONE)), `${CHANNEL}/${PHONE}`);
});

/* ── Разбор параметра ──────────────────────────────────────────────────────── */

test('точная форма «канал/чат» разбирается', () => {
  assert.deepEqual(parseWazzupChatTarget(`${CHANNEL}/${PHONE}`), {
    channelId: CHANNEL, chatId: PHONE, phone: '',
  });
  // Пробелы по краям приходят из копипаста в мессенджере.
  assert.deepEqual(parseWazzupChatTarget(`  ${CHANNEL}/${PHONE}  `).chatId, PHONE);
});

test('своя же ссылка читается обратно параметром', () => {
  useLocation(PORTAL);
  const link = buildWazzupChatLink(chat(CHANNEL, PHONE));
  assert.deepEqual(readWazzupChatTargetFromSearch(new URL(link).search), {
    channelId: CHANNEL, chatId: PHONE, phone: '',
  });
  // Рукописная ссылка с «живым» слешем разбирается так же.
  assert.deepEqual(
    readWazzupChatTargetFromSearch(`?view=wazzup_chats&chat=${CHANNEL}/${PHONE}`),
    { channelId: CHANNEL, chatId: PHONE, phone: '' }
  );
});

test('chatId со слешем внутри не обрезаем до первого сегмента', () => {
  /* chat_id — непрозрачный TEXT из вебхука, и обрезка дала бы валидную с виду
     пару, ведущую в ЧУЖОЙ чат. Такое значение остаётся без перехода. */
  assert.equal(parseWazzupChatTarget(`${CHANNEL}/group/${PHONE}`), null);
  assert.equal(normalizeWazzupChatId('group/77784237140'), '');
});

test('форма «только номер» разбирается и нормализуется', () => {
  assert.deepEqual(parseWazzupChatTarget(PHONE), { channelId: '', chatId: '', phone: PHONE });
  assert.deepEqual(parseWazzupChatTarget('8 778 423 71 40').phone, PHONE);
  assert.deepEqual(parseWazzupChatTarget('+7 (778) 423-71-40').phone, PHONE);
});

test('мусор в параметре перехода не даёт', () => {
  for (const bad of ['../api/admin/users', 'a%b', 'imya-kontakta', '', '   ', null, undefined, '7778']) {
    assert.equal(parseWazzupChatTarget(bad), null, String(bad));
  }
  assert.equal(readWazzupChatTargetFromSearch('?view=wazzup_chats'), null);
  assert.equal(readWazzupChatTargetFromSearch(''), null);
});

test('служебные символы не пускаем ни в канал, ни в чат', () => {
  assert.equal(normalizeWazzupChannelId(CHANNEL), CHANNEL);
  assert.equal(normalizeWazzupChannelId('  34403077  '), '34403077');
  assert.equal(normalizeWazzupChannelId('34403077%2f'), '');
  assert.equal(normalizeWazzupChannelId('a'.repeat(65)), '');
  assert.equal(normalizeWazzupChatId(PHONE), PHONE);
  assert.equal(normalizeWazzupChatId('user.name@s.whatsapp.net'), 'user.name@s.whatsapp.net');
  // '%' на сервере работает шаблоном ILIKE — в значение его не пускаем.
  assert.equal(normalizeWazzupChatId('7778%'), '');
  assert.equal(normalizeWazzupChatId('7778 4237140'), '');
});

test('номер вне разумной длины номером не считаем', () => {
  assert.equal(normalizePhoneDigits('777842371'), '');
  assert.equal(normalizePhoneDigits('7778423714012345'), '');
  assert.equal(normalizePhoneDigits('7784237140'), '7784237140');
});

/* ── Поиск строки чата ─────────────────────────────────────────────────────── */

test('точную пару находим равенством, а не подстрокой', () => {
  /* Поиск на сервере подстрочный (ILIKE '%q%'), и в ответе рядом с нашим чатом
     лежит сосед '777842371400'. items[0] открыл бы чужую переписку. */
  const items = [chat(CHANNEL, '777842371400'), chat(CHANNEL, PHONE)];
  assert.equal(findWazzupChatExact(items, { channelId: CHANNEL, chatId: PHONE }).chatId, PHONE);
  assert.equal(findWazzupChatExact(items, { channelId: 'other-channel', chatId: PHONE }), null);
  assert.equal(findWazzupChatExact(items, { channelId: CHANNEL, chatId: '' }), null);
  assert.equal(findWazzupChatExact([], { channelId: CHANNEL, chatId: PHONE }), null);
});

test('по номеру находим чат и через chatId, и через contactPhone', () => {
  const byPhone = chat(CHANNEL, 'abcdef', { contactPhone: '+7 778 423-71-40' });
  assert.equal(pickWazzupChatByPhone([chat(CHANNEL, '777842371400'), byPhone], PHONE), byPhone);
  assert.equal(pickWazzupChatByPhone([chat(CHANNEL, PHONE)], '8 778 423 71 40').chatId, PHONE);
  assert.equal(pickWazzupChatByPhone([chat(CHANNEL, '777842371400')], PHONE), null);
  assert.equal(pickWazzupChatByPhone([], PHONE), null);
});

test('номер без кода страны — приблизительное совпадение по последним 10 цифрам', () => {
  const items = [chat(CHANNEL, PHONE)];
  assert.equal(pickWazzupChatByPhone(items, '7784237140').chatId, PHONE);
});

test('один номер в двух каналах — выбор за человеком, а не за нами', () => {
  const items = [chat(CHANNEL, PHONE), chat('11111111-2222-3333-4444-555555555555', PHONE)];
  assert.equal(matchWazzupChatsByPhone(items, PHONE).length, 2);
  // Точные совпадения гасят приблизительные: сосед по хвосту в тир не попадает.
  const mixed = [chat(CHANNEL, PHONE), chat(CHANNEL, '7787784237140')];
  assert.equal(matchWazzupChatsByPhone(mixed, PHONE).length, 1);
});

/* ── Адресная строка ───────────────────────────────────────────────────────── */

test('открытый чат попадает в адрес, закрытый — уходит из него', () => {
  useLocation(`${PORTAL}&v=1`);
  syncWazzupChatDeepLink(chat(CHANNEL, PHONE));
  assert.equal(replaced.at(-1).next, `/OTP?view=wazzup_chats&chat=${CHANNEL}%2F${PHONE}`);
  // Состояние истории переносим как есть: роутер хранит в нём свой ключ.
  assert.deepEqual(replaced.at(-1).state, { key: 'portal' });

  useLocation(`${PORTAL}&chat=${CHANNEL}%2F${PHONE}`);
  syncWazzupChatDeepLink(null);
  assert.equal(replaced.at(-1).next, '/OTP?view=wazzup_chats');
});

test('хэш адреса переживает и открытие, и закрытие чата', () => {
  useLocation(`${PORTAL}#top`);
  syncWazzupChatDeepLink(chat(CHANNEL, PHONE));
  assert.equal(replaced.at(-1).next, `/OTP?view=wazzup_chats&chat=${CHANNEL}%2F${PHONE}#top`);
});

test('ссылка-по-номеру после разрешения переписывается в точную форму', () => {
  useLocation(`${PORTAL}&chat=${PHONE}`);
  syncWazzupChatDeepLink(chat(CHANNEL, PHONE));
  assert.equal(replaced.at(-1).next, `/OTP?view=wazzup_chats&chat=${CHANNEL}%2F${PHONE}`);
});

/* ── Гигиена адреса в App.jsx ──────────────────────────────────────────────────
   Метку ставит и снимает сам раздел, но ухода в ДРУГОЙ раздел он не видит:
   снимать её обязан App, там же где снимаются task_id и article. Забыть этот
   шаг — значит унести чужой chat= в каждую скопированную ссылку и в каждый
   Ctrl-клик по пункту меню. Проверить это в браузере нечем, поэтому сторожим
   исходник. Файлы в репозитории с CRLF — перевод строки ищем вместе с \r. */

test('App.jsx снимает метку чата при уходе из раздела — в обоих местах', () => {
  const source = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8');
  const guards = source.match(
    /if\s*\(nextView\s*!==\s*'wazzup_chats'\)\s*\{\r?\n\s*url\.searchParams\.delete\(WAZZUP_CHAT_QUERY_PARAM\);/g
  );
  assert.equal(guards?.length, 2, 'нужны buildAppViewUrl и syncAppViewWithUrl');
});
