import test from 'node:test';
import assert from 'node:assert/strict';

import {
  LIST_REFRESH_MS, LIST_REFRESH_WAIT_MS, LIST_STALE_ON_RETURN_MS,
  linkAction, noticeFor, officeSpaces, phoneCopiedText, phoneToCopy, pickOfficesSpace, refreshDue,
  retryDelayMs, telNumber, toastText,
} from '../src/offices_embed/officesPage.js';
import {
  QR_DEFAULT_TTL_SEC, accessStateFromError, formatCountdown, isApiUrl, qrSchedule, sessionKeyOf,
  splitQrCode, statusPollDelayMs, retryDelayMs as sharedRetryDelayMs,
} from '../src/embed/embedAccess.js';

/* Страница «Адреса офисов» для iCORE Phone (offices.html) и общее с тренажёрами
   (src/embed/embedAccess.js). Стережём то, что ломается молча:
     - не то пространство (гостевое, с выключенной вкладкой) — список офисов пуст
       или справочник чужой вики;
     - ссылка tel: уходит в WebView2, и вместо страницы — ошибка загрузки;
     - по нажатию на телефон копируется склейка двух номеров или номер с
       приросшим добавочным — а тост говорит «скопирован»;
     - список офисов не обновляется за смену, и закрытый офис числится открытым;
     - ответ сервера прочитан не так: «нужен QR» вместо отказа и наоборот;
     - QR истекает раньше, чем страница попросит новый (часы оператора врут). */

const API = 'https://otp-2-fos4.onrender.com';

const jwt = (payload) => {
  const b64url = (obj) => Buffer.from(JSON.stringify(obj)).toString('base64')
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${b64url({ alg: 'HS256', typ: 'JWT' })}.${b64url(payload)}.sig`;
};

const httpError = (status, data) => ({ response: { status, data } });

// ── Пространство ────────────────────────────────────────────────────────────

const SPACES = [
  { id: 1, name: 'Гостевая', features: {}, guest_only: true },
  { id: 2, name: 'Тез', features: { offices: false } },
  { id: 3, name: 'Таксопарки', features: {} },
  { id: 4, name: 'Тез ОП', features: { offices: true, parks: false } },
];

test('справочник — только в своих пространствах с вкладкой «Офисы»', () => {
  assert.deepEqual(officeSpaces(SPACES).map((s) => s.id), [3, 4]);
  // Чего нет в features — то включено (spaceFeatures.js), пустой объект и null тоже.
  assert.deepEqual(officeSpaces([{ id: 7, features: null }]).map((s) => s.id), [7]);
  assert.deepEqual(officeSpaces(undefined), []);
  assert.deepEqual(officeSpaces([null, { name: 'без id' }]), []);
});

test('открывается первое подходящее пространство или запомненное', () => {
  assert.equal(pickOfficesSpace(SPACES).id, 3);
  assert.equal(pickOfficesSpace(SPACES, 4).id, 4);
  assert.equal(pickOfficesSpace(SPACES, '4').id, 4, 'из localStorage id приходит строкой');
  // Запомненное больше не подходит (гостевое, выключено, пропало) — первое.
  assert.equal(pickOfficesSpace(SPACES, 1).id, 3);
  assert.equal(pickOfficesSpace(SPACES, 2).id, 3);
  assert.equal(pickOfficesSpace(SPACES, 99).id, 3);
  assert.equal(pickOfficesSpace(SPACES, null).id, 3);
  assert.equal(pickOfficesSpace([SPACES[0], SPACES[1]]), null, 'справочника нет — null, а не чужая вики');
  assert.equal(pickOfficesSpace([]), null);
});

// ── Ссылки ──────────────────────────────────────────────────────────────────

test('tel: всегда копируется, а не открывается', () => {
  assert.equal(linkAction('tel:+77011234567', '', true), 'copy-tel');
  assert.equal(linkAction('TEL:87011234567', '', false), 'copy-tel');
  assert.equal(linkAction(' tel:+7701', '_blank', true), 'copy-tel');
});

test('в телефоне внешние ссылки отдаются телефону, в браузере — не трогаем', () => {
  const page = 'https://alfa330.github.io';
  assert.equal(linkAction('https://2gis.kz/almaty/firm/1', '_blank', true, page), 'external');
  assert.equal(linkAction('https://2gis.kz/almaty/firm/1', '_BLANK', true, page), 'external');
  // Чужой сайт без target — WebView2 ушёл бы со страницы офисов.
  assert.equal(linkAction('https://2gis.kz/almaty/firm/1', '', true, page), 'external');
  // Своя страница без target — обычный переход.
  assert.equal(linkAction('https://alfa330.github.io/OTP/offices.html', '', true, page), null);
  assert.equal(linkAction('https://2gis.kz/almaty/firm/1', '_blank', false, page), null);
  assert.equal(linkAction('mailto:a@b.kz', '_blank', true, page), null);
  assert.equal(linkAction('javascript:void(0)', '', true, page), null);
  assert.equal(linkAction('', '_blank', true, page), null);
  assert.equal(linkAction(undefined, undefined, true), null);
});

test('номер из tel: — цифры и плюс, %-коды раскрыты', () => {
  assert.equal(telNumber('tel:+77011234567'), '+77011234567');
  assert.equal(telNumber('tel:%2B77011234567'), '+77011234567');
  assert.equal(telNumber('tel:%2B7%20(701)%20123-45-67'), '+77011234567');
  assert.equal(telNumber('tel:8 701 123 45 67'), '87011234567');
  assert.equal(telNumber('tel:+7701123;ext=12'), '+7701123');
  assert.equal(telNumber('tel://+7701123'), '+7701123');
  assert.equal(telNumber('tel:%E0%A4%A+7701'), '7701', 'битая %-последовательность не роняет разбор');
  assert.equal(telNumber('tel:'), '');
  assert.equal(telNumber(null), '');
});

// ── Копирование телефона ────────────────────────────────────────────────────

/* Так портал строит ссылку из поля телефона (OfficeTable.jsx, CityCard.jsx,
   WikiPark.jsx): всё, кроме цифр и плюса, вычёркивается. */
const portalHref = (phone) => `tel:${phone.replace(/[^\d+]/g, '')}`;

test('два номера в поле: копируется первый, а не склейка из href', () => {
  const text = '+7 707 705 08 80, +7 727 333 22 11';
  // Сама беда: в href оба номера слиты, и telNumber послушно отдаёт 22 цифры.
  assert.equal(portalHref(text), 'tel:+77077050880+77273332211');
  assert.equal(telNumber(portalHref(text)), '+7707705088077273332211');

  const phone = phoneToCopy(portalHref(text), text);
  assert.deepEqual(phone, { value: '+77077050880', ext: '', more: 1 });
  assert.equal(phoneCopiedText(phone, true), 'Номер скопирован: +77077050880 (первый из 2)');
  assert.equal(phoneCopiedText(phone, false), 'Не удалось скопировать номер +77077050880 (первый из 2)');

  assert.equal(phoneToCopy(portalHref('+7 707 705 08 80 / 8 727 333 22 11'), '+7 707 705 08 80 / 8 727 333 22 11').value, '+77077050880');
  assert.equal(phoneToCopy('tel:x', '8 701 111 22 33; 8 702 222 33 44; 8 703 333 44 55').more, 2);
});

test('добавочный в буфер не идёт, но в тосте показан', () => {
  const text = '8 (7212) 50-60-70 доб. 12';
  assert.equal(telNumber(portalHref(text)), '8721250607012', 'в href добавочный прирос к номеру');

  const phone = phoneToCopy(portalHref(text), text);
  assert.deepEqual(phone, { value: '87212506070', ext: '12', more: 0 });
  assert.equal(phoneCopiedText(phone, true), 'Номер скопирован: 87212506070 (доб. 12)');

  // Один добавочный знак даёт в href правдоподобные 12 цифр — по href его не отличить.
  const short = '+7 707 705 08 80 доб. 5';
  assert.equal(telNumber(portalHref(short)), '+770770508805');
  assert.deepEqual(phoneToCopy(portalHref(short), short), { value: '+77077050880', ext: '5', more: 0 });

  // И номеров два, и добавочный у первого — в тосте оба пояснения.
  assert.equal(
    phoneCopiedText(phoneToCopy('tel:x', '8 (7212) 50-60-70 доб. 12, 8 701 111 22 33'), true),
    'Номер скопирован: 87212506070 (доб. 12, первый из 2)',
  );
});

test('обычный номер копируется как раньше', () => {
  assert.deepEqual(phoneToCopy('tel:+77273551234', '+7 (727) 355-12-34'), { value: '+77273551234', ext: '', more: 0 });
  assert.equal(phoneCopiedText({ value: '+77273551234', ext: '', more: 0 }, true), 'Номер скопирован: +77273551234');
  // Текст ссылки берётся из textContent: значок и пробелы по краям не мешают.
  assert.equal(phoneToCopy('tel:87011234567', '\n   8 701 123 45 67 \n').value, '87011234567');
});

test('подпись без цифр — номер из ссылки, если в ней ровно один номер', () => {
  assert.deepEqual(phoneToCopy('tel:+77011234567', 'Позвонить'), { value: '+77011234567', ext: '', more: 0 });
  assert.deepEqual(phoneToCopy('tel:%2B7%20(701)%20123-45-67', ''), { value: '+77011234567', ext: '', more: 0 });
  assert.deepEqual(phoneToCopy('tel:+7701123;ext=12', 'Офис'), { value: '+7701123', ext: '12', more: 0 });
  // Склейка в ссылке и ни одной цифры в подписи: копировать нечего, врать не будем.
  assert.equal(phoneToCopy('tel:+77077050880+77273332211', 'Позвонить'), null);
  assert.equal(phoneToCopy('tel:8721250607012', 'Позвонить'), null, '13 цифр — не один номер');
  assert.equal(phoneToCopy('tel:7701+7702', 'Позвонить'), null, 'плюс не в начале');
  assert.equal(phoneToCopy('tel:', ''), null);
  assert.equal(phoneToCopy(null, null), null);
  assert.equal(phoneCopiedText(null, true), '');
});

test('номер не читается — копируется то, что на экране, без выдумок', () => {
  // Три номера подряд без разделителя: ни одного не выделить.
  const text = '87077050880  87273332211 87011234567';
  const phone = phoneToCopy(portalHref(text), text);
  assert.deepEqual(phone, { value: '87077050880 87273332211 87011234567', ext: '', more: 0, raw: true });
  assert.equal(phoneCopiedText(phone, true), 'Скопировано: 87077050880 87273332211 87011234567');
  assert.equal(phoneCopiedText(phone, false), 'Не удалось скопировать: 87077050880 87273332211 87011234567');
});

// ── Тосты и фоновое обновление ──────────────────────────────────────────────

test('английские ошибки axios в тосте — по-русски', () => {
  assert.equal(toastText('Network Error'), 'Нет связи с порталом — проверьте подключение к интернету.');
  assert.match(toastText('Request failed with status code 502'), /^Портал не ответил \(ошибка 502\)/);
  assert.match(toastText('Request failed with status code 429'), /^Портал отклонил запрос \(код 429\)/);
  assert.match(toastText('timeout of 30000ms exceeded'), /не ответил вовремя/);
  for (const text of ['Network Error', 'Request failed with status code 502', 'timeout of 15000ms exceeded']) {
    assert.doesNotMatch(toastText(text), /[A-Za-z]{4,}/, `«${text}» остался английским`);
  }
  // Отменённый запрос — не событие для человека: тоста нет вовсе.
  assert.equal(toastText('canceled'), '');
  assert.equal(toastText('Request aborted'), '');
  // Свой текст сервера (он русский) и обычные сообщения проходят как есть.
  assert.equal(toastText('Офис не найден'), 'Офис не найден');
  assert.equal(toastText('Не удалось загрузить офисы'), 'Не удалось загрузить офисы');
  assert.equal(toastText(undefined), '');
});

test('список обновляется раз в пять минут, а при возврате во вкладку — раньше', () => {
  const now = Date.parse('2026-10-12T10:00:00Z');
  const base = { now, day: '2026-10-12', today: '2026-10-12' };
  assert.equal(refreshDue({ ...base, okAt: now - LIST_REFRESH_MS + 1000 }), false);
  assert.equal(refreshDue({ ...base, okAt: now - LIST_REFRESH_MS }), true);
  // Вернулись во вкладку: двух минут достаточно, чтобы не верить списку.
  assert.equal(refreshDue({ ...base, okAt: now - LIST_STALE_ON_RETURN_MS, returning: true }), true);
  assert.equal(refreshDue({ ...base, okAt: now - LIST_STALE_ON_RETURN_MS + 1000, returning: true }), false);
  assert.ok(LIST_STALE_ON_RETURN_MS < LIST_REFRESH_MS);
  assert.ok(LIST_REFRESH_MS <= 5 * 60 * 1000, 'статус «на сегодня» не старше пяти минут');
});

test('новые сутки — обновляем сразу, свежесть списка не спасает', () => {
  const now = Date.parse('2026-10-12T19:00:30Z'); // 00:00:30 по Алматы
  assert.equal(refreshDue({ now, okAt: now - 20000, day: '2026-10-12', today: '2026-10-13' }), true);
  assert.equal(refreshDue({ now, okAt: now - 20000, day: '2026-10-13', today: '2026-10-13' }), false);
});

test('пока прежняя просьба в пути, новую не шлём', () => {
  const now = Date.parse('2026-10-12T10:00:00Z');
  const stale = { now, okAt: now - 30 * 60 * 1000, day: '2026-10-12', today: '2026-10-12' };
  assert.equal(refreshDue({ ...stale, askedAt: now - 5000 }), false);
  assert.equal(refreshDue({ ...stale, askedAt: now - 5000, today: '2026-10-13' }), false, 'и смена суток подождёт ответа');
  // Ответа нет слишком долго — просим снова, а не ждём вечно.
  assert.equal(refreshDue({ ...stale, askedAt: now - LIST_REFRESH_WAIT_MS }), true);
  assert.equal(refreshDue({ ...stale, askedAt: 0 }), true);
});

test('пауза повтора одна на обе страницы', () => {
  assert.equal(retryDelayMs, sharedRetryDelayMs, 'офисы берут паузу из общего модуля, а не держат копию');
  assert.deepEqual([0, 1, 2, 3, 4, 5].map(sharedRetryDelayMs), [3000, 6000, 12000, 24000, 30000, 30000]);
});

// ── Ответ сервера о доступе ─────────────────────────────────────────────────

test('коды вики читаются по смыслу', () => {
  assert.deepEqual(
    accessStateFromError(httpError(403, { code: 'SENSITIVE_ACCESS_REQUIRED', error: 'Раздел «Вики» откроется после QR-подтверждения доступа' })),
    { state: 'locked', reason: 'qr', message: 'Раздел «Вики» откроется после QR-подтверждения доступа' },
  );
  assert.equal(accessStateFromError(httpError(403, { code: 'WIKI_DEPARTMENT_DISABLED', error: 'Раздел «Вики» не выдан вашему отделу' })).reason, 'department');
  assert.equal(accessStateFromError(httpError(403, { code: 'WIKI_DEPARTMENT_DISABLED' })).state, 'denied');
  // «Ни одного пространства» (403) — отказ; «укажите пространство» (400) и
  // «пространство не найдено» (404) — повод выбрать пространство заново.
  assert.deepEqual(accessStateFromError(httpError(403, { code: 'WIKI_SPACE_REQUIRED', error: 'Вам не выдано ни одного пространства вики' })),
    { state: 'denied', reason: 'no-space', message: 'Вам не выдано ни одного пространства вики' });
  assert.equal(accessStateFromError(httpError(400, { code: 'WIKI_SPACE_REQUIRED' })).state, 'respace');
  assert.equal(accessStateFromError(httpError(404, { code: 'WIKI_SPACE_NOT_FOUND' })).state, 'respace');
});

test('401, сеть, 5xx и прочие отказы', () => {
  assert.equal(accessStateFromError(httpError(401, { error: 'JWT authentication failed', code: 'SESSION_REVOKED' })).state, 'no-session');
  assert.deepEqual(accessStateFromError({ message: 'Network Error' }), { state: 'error', reason: 'network', message: '' });
  assert.deepEqual(accessStateFromError(undefined), { state: 'error', reason: 'network', message: '' });
  assert.equal(accessStateFromError(httpError(500, { error: 'Внутренняя ошибка раздела «Вики»' })).reason, 'server');
  assert.equal(accessStateFromError(httpError(502, '<html>Bad gateway</html>')).state, 'error');
  assert.equal(accessStateFromError(httpError(403, { code: 'WIKI_FORBIDDEN', error: 'Недостаточно прав' })).state, 'denied');
  assert.equal(accessStateFromError(httpError(404, { error: 'Пользователь не найден' })).state, 'denied');
  assert.equal(accessStateFromError(httpError(404, '<html>Not Found</html>')).state, 'error', 'голый 404 — повторить, а не отказ');
  assert.equal(accessStateFromError(httpError(429, {})).reason, 'http');
});

test('токен подставляется только в запросы к нашему API', () => {
  assert.equal(isApiUrl(`${API}/api/wiki/ping`, API), true);
  assert.equal(isApiUrl(API, API), true);
  assert.equal(isApiUrl('https://otp-2-fos4.onrender.com.evil.kz/api/wiki', API), false);
  assert.equal(isApiUrl('https://tile0.maps.2gis.com/tiles?x=1', API), false);
  assert.equal(isApiUrl('', API), false);
  assert.equal(isApiUrl(undefined, API), false);
  // Стенд (vite.stand.config.mjs) вырезает адрес API: пути становятся относительными.
  assert.equal(isApiUrl('/api/wiki/ping', ''), true);
  assert.equal(isApiUrl('/assets/x.js', ''), false);
});

test('сессия токена — по sid: обновлённый токен той же сессии — та же сессия', () => {
  const a1 = jwt({ sub: '42', sid: '6f1c', exp: 1_800_000_000 });
  const a2 = jwt({ sub: '42', sid: '6f1c', exp: 1_800_001_800 });
  const b = jwt({ sub: '42', sid: '9aa0', exp: 1_800_000_000 });
  assert.equal(sessionKeyOf(a1), 'sid:6f1c');
  assert.equal(sessionKeyOf(a1), sessionKeyOf(a2));
  assert.notEqual(sessionKeyOf(a1), sessionKeyOf(b));
  assert.equal(sessionKeyOf('opaque-token'), 'token:opaque-token', 'не JWT — ключом служит сам токен');
  assert.equal(sessionKeyOf(''), '');
  assert.equal(sessionKeyOf(null), '');
});

// ── QR ──────────────────────────────────────────────────────────────────────

test('новый QR просим за 30 с до конца срока; срок считаем от получения ответа', () => {
  const now = Date.parse('2026-10-12T10:00:00Z');
  const ok = qrSchedule('2026-10-12T10:05:00Z', now);
  assert.equal(ok.validUntilMs - now, 300_000);
  assert.equal(ok.refreshAtMs - now, 270_000);
  // Короткий срок с сервера: запас не больше четверти срока.
  const short = qrSchedule(new Date(now + 60_000).toISOString(), now);
  assert.equal(short.refreshAtMs - now, 45_000);
  // Совсем короткий — не чаще раза в 15 с.
  assert.equal(qrSchedule(new Date(now + 10_000).toISOString(), now).refreshAtMs - now, 15_000);
});

test('часы оператора врут — срок по умолчанию, а не мгновенный или вечный код', () => {
  const now = Date.parse('2026-10-12T10:00:00Z');
  const ttl = QR_DEFAULT_TTL_SEC * 1000;
  // Часы спешат на 10 минут: «истёк ещё до выдачи».
  assert.equal(qrSchedule('2026-10-12T09:55:00Z', now).validUntilMs - now, ttl);
  // Часы отстают на 10 минут: «действует ещё 15 минут».
  assert.equal(qrSchedule('2026-10-12T10:15:00Z', now).validUntilMs - now, ttl);
  // Срока нет или он не разбирается.
  assert.equal(qrSchedule(undefined, now).refreshAtMs - now, ttl - 30_000);
  assert.equal(qrSchedule('завтра', now).validUntilMs - now, ttl);
});

test('опрос статуса при отказах сети растягивается до потолка', () => {
  assert.equal(statusPollDelayMs(0), 2500);
  assert.equal(statusPollDelayMs(1), 5000);
  assert.equal(statusPollDelayMs(3), 20000);
  assert.equal(statusPollDelayMs(10), 30000);
  assert.equal(statusPollDelayMs(-1), 2500);
  assert.equal(retryDelayMs(0), 3000);
  assert.equal(retryDelayMs(2), 12000);
  assert.equal(retryDelayMs(9), 30000);
});

test('резервный код — приставкой и четвёрками, а копируется целиком', () => {
  const payload = 'OTPQ:ABCDEFGHIJKLMNOPQRSTUVWXYZ234567ABCDEFGHIJKLMNOPQRST';
  const { prefix, groups } = splitQrCode(payload);
  assert.equal(prefix, 'OTPQ:');
  assert.equal(groups.length, 13);
  assert.ok(groups.every((g) => g.length === 4));
  // Группы — только для глаз: склеенные обратно, они дают ровно исходную строку.
  assert.equal(prefix + groups.join(''), payload);
  assert.deepEqual(splitQrCode('ABCDEF'), { prefix: '', groups: ['ABCD', 'EF'] });
  assert.deepEqual(splitQrCode(''), { prefix: '', groups: [] });
  assert.deepEqual(splitQrCode(null), { prefix: '', groups: [] });
});

test('отсчёт срока кода', () => {
  assert.equal(formatCountdown(245_000), '4:05');
  assert.equal(formatCountdown(59_001), '1:00');
  assert.equal(formatCountdown(0), '0:00');
  assert.equal(formatCountdown(-5000), '0:00');
});

// ── Тексты состояний ────────────────────────────────────────────────────────

test('у каждого состояния — понятный текст и выход', () => {
  assert.match(noticeFor('connecting', { inPhone: true }).title, /iCORE Phone/);
  assert.equal(noticeFor('checking').kind, 'wait');
  // Телефон сам объясняет, почему токена нет, — его словами.
  const phoneNoSession = noticeFor('no-session', { inPhone: true, tokenError: 'Нет сессии iCORE — войдите в телефон заново' });
  assert.equal(phoneNoSession.title, 'Войдите в iCORE Phone');
  assert.equal(phoneNoSession.text, 'Нет сессии iCORE — войдите в телефон заново');
  assert.ok(phoneNoSession.action);
  // 401 на живой с виду токен — английский ответ сервера человеку не показываем.
  assert.doesNotMatch(noticeFor('no-session', { inPhone: true, reason: 'expired', message: 'JWT authentication failed' }).text, /JWT/);
  assert.equal(noticeFor('no-session', { inPhone: false }).title, 'Войдите в портал');
  assert.equal(noticeFor('denied', { reason: 'department' }).text, 'Раздел «Вики» не выдан вашему отделу.');
  assert.match(noticeFor('denied', { reason: 'no-offices' }).text, /нет справочника офисов/);
  assert.equal(noticeFor('denied', { reason: 'forbidden', message: 'Недостаточно прав' }).text, 'Недостаточно прав');
  assert.equal(noticeFor('error', { reason: 'network' }).title, 'Нет связи с порталом');
  assert.equal(noticeFor('error', { reason: 'server', message: 'Внутренняя ошибка раздела «Вики»' }).text, 'Внутренняя ошибка раздела «Вики»');
  assert.equal(noticeFor('ready'), null);
  assert.equal(noticeFor('locked'), null);
});
