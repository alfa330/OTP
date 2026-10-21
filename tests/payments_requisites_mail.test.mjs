/*
 * «Оплата счетов»: почта поставщиков и письма о смене реквизитов наших компаний.
 *
 * Просьба владельца 20.10.2026: «добавление эмайла контрагентов поставщиков и тд, автомат
 * рассылка по эмайлом при изменении реквизитов». Решения владельца: письмо — о реквизитах
 * НАШЕЙ компании, всем поставщикам с почтой, через 10 минут после сохранения с «Не отправлять».
 *
 * Здесь — правила формы (те же, что у сервера: случаи общие с tests/test_payments_requisites_mail.py,
 * tests/data/payments_requisites_mail_cases.json), строки и метки писем и то, что видно в окне
 * справочника без браузера. Очередь и отправку проверяет tests/test_payments_requisites_mail_postgres.py.
 *
 * Запуск: node --test tests/payments_requisites_mail.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

import {
    HINTS, MAX_SUPPLIER_EMAILS, REQUISITE_FIELDS, changedLine, emailsIssue, emailsShort, mailNotice, mailingPill,
    mailingSummary, mailingWhen, minutesWord, parseEmails, requisitesChanged,
} from '../src/components/payments/paymentsMeta.js';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const esbuild = require('esbuild');

const ROOT = new URL('../', import.meta.url);
const source = (path) => readFileSync(new URL(path, ROOT), 'utf8');
const CASES = JSON.parse(source('tests/data/payments_requisites_mail_cases.json'));

// Сборка — внутрь node_modules: из системного temp внешние react и lucide-react не резолвятся.
const OUT_DIR = join(new URL('../node_modules/.cache/otp-payments-requisites-mail-test/', import.meta.url)
    .pathname.replace(/^\/([A-Za-z]:)/, '$1'));
mkdirSync(OUT_DIR, { recursive: true });
const bundles = new Map();
async function bundle(entry, name) {
    if (bundles.has(name)) return bundles.get(name);
    const stubPath = join(OUT_DIR, 'axios-stub.mjs');
    writeFileSync(stubPath, 'const never = () => new Promise(() => {});\nexport default { get: never, post: never, patch: never, delete: never };\n');
    const result = await esbuild.build({
        entryPoints: [new URL(entry, ROOT).pathname.replace(/^\/([A-Za-z]:)/, '$1')],
        bundle: true, write: false, format: 'esm', platform: 'node', target: 'node18', jsx: 'transform',
        loader: { '.jsx': 'jsx', '.js': 'jsx', '.css': 'empty' },
        external: ['react', 'react-dom', 'lucide-react'],
        alias: { axios: stubPath },
        logLevel: 'silent',
    });
    const outPath = join(OUT_DIR, `${name}.mjs`);
    writeFileSync(outPath, result.outputFiles[0].text);
    const loaded = await import(pathToFileURL(outPath).href + `?t=${Date.now()}`);
    bundles.set(name, loaded);
    return loaded;
}
const h = React.createElement;
const TODAY = '2026-10-20';
const NOW = '2026-10-20T14:00:00';

// ── Почта поставщика ──────────────────────────────────────────────────────────

test('адреса разбираются так же, как на сервере', () => {
    for (const item of CASES.emails) {
        const parsed = parseEmails(item.input);
        assert.deepEqual(parsed.emails, item.emails, item.input);
        assert.equal(parsed.bad, item.bad ?? '', item.input);
        assert.equal(emailsIssue(item.input), item.bad === null ? '' : `«${item.bad}» — не похоже на адрес почты`, item.input);
    }
    assert.equal(parseEmails(CASES.too_many).tooMany, true);
    assert.equal(emailsIssue(CASES.too_many), `Не больше ${MAX_SUPPLIER_EMAILS} адресов у одного поставщика`);
    assert.equal(parseEmails('a@x.kz, b@x.kz, c@x.kz, d@x.kz, e@x.kz').tooMany, false, 'пять — можно');
    assert.deepEqual(parseEmails(null).emails, []);
});

test('почта в строке поставщика — первый адрес и «+N»', () => {
    assert.equal(emailsShort([]), '');
    assert.equal(emailsShort(undefined), '');
    assert.equal(emailsShort(['buh@romashka.kz']), 'buh@romashka.kz');
    assert.equal(emailsShort(['buh@romashka.kz', 'info@romashka.kz', 'ceo@romashka.kz']), 'buh@romashka.kz +2');
});

// ── Что считается сменой реквизитов ──────────────────────────────────────────

test('смена реквизитов — те же поля и то же сравнение, что у сервера', () => {
    assert.deepEqual(REQUISITE_FIELDS, ['name', 'bin', 'legal_address', 'iik', 'bank_name', 'bik', 'kbe', 'vat_payer', 'requisites']);
    for (const item of CASES.requisites) {
        assert.deepEqual(requisitesChanged(item.before, item.after), item.changed, item.about);
    }
});

// ── Строки и метки писем ──────────────────────────────────────────────────────

test('когда уйдёт письмо: сегодня — время, в другой день — и число', () => {
    assert.equal(mailingWhen('2026-10-20T14:35:00', TODAY), 'в 14:35');
    assert.equal(mailingWhen('2026-10-21T00:05:00', TODAY), '21.10 в 00:05');
    assert.equal(mailingWhen('2026-10-20', TODAY), '');
    assert.equal(mailingWhen(null), '');
});

test('метка в строке компании — только когда письмо ждёт внимания', () => {
    const at = { today: TODAY, now: NOW };
    assert.equal(mailingPill(null), null);
    assert.deepEqual(mailingPill({ status: 'pending', send_after: '2026-10-20T14:35:00' }, at), { tone: 'current', label: 'письмо в 14:35' });
    assert.deepEqual(mailingPill({ status: 'pending', send_after: '2026-10-20T13:35:00' }, at), { tone: 'current', label: 'письмо уходит' },
        'срок прошёл — не время в прошлом');
    assert.deepEqual(mailingPill({ status: 'pending', send_after: '2026-10-20T14:35:00' }, { ...at, ready: false }), { tone: 'blocked', label: 'письмо ждёт почту' });
    assert.deepEqual(mailingPill({ status: 'pending', send_after: '2026-10-20T13:35:00', last_error: 'Почта не приняла логин' }, at), { tone: 'blocked', label: 'письмо не уходит' });
    assert.deepEqual(mailingPill({ status: 'sending' }, at), { tone: 'current', label: 'письмо уходит' });
    assert.deepEqual(mailingPill({ status: 'sending' }, { ...at, ready: false }), { tone: 'blocked', label: 'письмо не уходит' });
    assert.deepEqual(mailingPill({ status: 'sending', last_error: 'Почта не приняла логин' }, at), { tone: 'blocked', label: 'письмо не уходит' });
    assert.deepEqual(mailingPill({ status: 'done', failed: 2 }, at), { tone: 'overdue', label: 'письмо не ушло: 2' });
    assert.equal(mailingPill({ status: 'done', failed: 0, sent: 5 }, at), null, 'ушло на все адреса — метки нет');
});

test('письмо из истории одной строкой', () => {
    const at = { today: TODAY, now: NOW };
    assert.equal(mailingSummary({ status: 'pending', send_after: '2026-10-20T14:35:00' }, at), 'Уйдёт в 14:35');
    assert.equal(mailingSummary({ status: 'pending', send_after: '2026-10-20T13:35:00' }, at), 'Уходит');
    assert.equal(mailingSummary({ status: 'pending', last_error: 'x' }, at), 'Не уходит');
    assert.equal(mailingSummary({ status: 'pending' }, { ...at, ready: false }), 'Ждёт подключения почты');
    assert.equal(mailingSummary({ status: 'sending', counts: { total: 5, sent: 2 } }, at), 'Отправляется: 2 из 5 адресов');
    assert.equal(mailingSummary({ status: 'sending', counts: { total: 1, sent: 0 } }, at), 'Отправляется: 0 из 1 адреса', 'после «из» — родительный');
    assert.equal(mailingSummary({ status: 'sending', counts: { total: 21, sent: 3 } }, at), 'Отправляется: 3 из 21 адреса');
    assert.equal(mailingSummary({ status: 'sending', last_error: 'x', counts: { total: 2, sent: 1 } }, at), 'Не уходит: 1 из 2 адресов');
    assert.equal(mailingSummary({ status: 'done', counts: { total: 1, sent: 1, failed: 0 } }, at), 'Отправлено на 1 адрес');
    assert.equal(mailingSummary({ status: 'done', counts: { total: 3, sent: 3, failed: 0 } }, at), 'Отправлено на 3 адреса');
    assert.equal(mailingSummary({ status: 'done', counts: { total: 4, sent: 3, failed: 1 } }, at), 'Отправлено 3 из 4 адресов · не ушло: 1');
    assert.equal(mailingSummary({ status: 'done', counts: { total: 4, sent: 4, failed: 0, removed: 1 } }, at),
        'Отправлено 4 из 5 адресов · адрес убран: 1', 'убранный адрес не прячется за «отправлено на все»');
    assert.equal(mailingSummary({ status: 'done', counts: { total: 3, sent: 3, superseded: 2 } }, at),
        'Отправлено на 3 адреса · ещё 2 адреса — новым письмом');
    assert.equal(mailingSummary({ status: 'done', counts: { total: 0 } }, at), 'Некому было отправить: ни у одного поставщика нет почты');
    assert.equal(mailingSummary({ status: 'cancelled', cancelled_by_name: 'Иванов Иван' }, at), 'Отменено · Иванов Иван');
});

test('строка перед сохранением: уйдёт ли письмо, когда, сколько адресов и у кого почты нет', () => {
    const recipients = { addresses: 6, suppliers: 5, without_email: 2, without_names: ['ИП Бетов', 'ТОО «Альфа»'] };
    assert.equal(mailNotice({ recipients }),
        'После сохранения поставщикам уйдёт письмо о новых реквизитах — через 10 минут. Получат 6 адресов. '
        + 'Без почты — 2 поставщика: ИП Бетов, ТОО «Альфа».');
    assert.match(mailNotice({ recipients, waiting: true }), /^Письмо, которое ждёт отправки, уйдёт уже с этими реквизитами — через 10 минут/);
    assert.match(mailNotice({ recipients, delayMinutes: 1 }), /— через 1 минуту\. /);
    assert.deepEqual([1, 2, 5, 21].map(minutesWord), ['1 минуту', '2 минуты', '5 минут', '21 минуту']);
    assert.match(mailNotice({ recipients: { addresses: 0, without_email: 3 } }), /ни у одного поставщика не указана почта — письмо некому отправить\.$/);
    assert.match(mailNotice({ recipients, ready: false }),
        /^После сохранения письмо о новых реквизитах уйдёт поставщикам, когда у компании заработает почта для писем: сейчас с неё отправлять нельзя\./,
        'без почты строка не обещает «через 10 минут»');
    assert.doesNotMatch(mailNotice({ recipients, ready: false }), /через 10 минут/);
    assert.equal(mailNotice({ recipients, reverting: true }), 'Реквизиты вернутся к тем, что знают поставщики, — письма не будет.');
    assert.equal(mailNotice({ recipients, reverting: true, waiting: true }), 'Реквизиты вернутся к тем, что знают поставщики, — ждущее письмо не уйдёт.');
    const many = { addresses: 6, without_email: 21, without_names: ['А', 'Б', 'В', 'Г', 'Д'] };
    assert.match(mailNotice({ recipients: many }), /Без почты — 21 поставщик: А, Б, В и ещё 18\.$/, 'названий не больше трёх');
    assert.match(mailNotice({ recipients: { addresses: 1, without_email: 2, without_names: ['А', 'Б'] } }), /Без почты — 2 поставщика: А, Б\.$/);
});

test('что изменилось: сокращения заглавными, остальное строчными', () => {
    assert.equal(changedLine(['Наименование', 'ИИК', 'Банк', 'НДС']), 'наименование, ИИК, банк, НДС');
    assert.equal(changedLine([]), '');
});

test('подсказки «i» у поля почты и у писем: что значит «отправлено» и кто получает', () => {
    assert.match(HINTS.supplierEmails.outro, /через запятую/);
    assert.match(HINTS.requisitesMail.outro, /через 10 минут/);
    assert.match(HINTS.requisitesMail.outro, /«Не отправлять»/);
    assert.match(HINTS.requisitesMail.outro, /принял почтовый сервер/);
    assert.doesNotMatch(HINTS.requisitesMail.outro, /кроме скрытых/, 'получают все поставщики с почтой — решение владельца');
});

// ── Окно справочника ─────────────────────────────────────────────────────────

const FUTURE = '2099-01-01T10:00:00';

test('карточка письма: ждёт — «Не отправлять», не ушло — кому и почему, повтор — только у последнего', async () => {
    const { MailingCard } = await bundle('src/components/payments/RequisitesMailings.jsx', 'mailings');
    const pending = renderToStaticMarkup(h(MailingCard, {
        item: { id: 1, status: 'pending', send_after: FUTURE, changed: ['ИИК'], created_by_name: 'Иванов Иван',
            recipients: [], counts: {}, latest: true, letter: { subject: 'Изменение реквизитов ТОО «Наша»', text: 'Здравствуйте!' } },
        ready: true,
    }));
    assert.match(pending, /data-mailing-status="pending"/);
    assert.match(pending, />Не отправлять<\/button>/);
    assert.match(pending, /Уйдёт 01\.01 в 10:00/);
    assert.match(pending, /Изменилось: ИИК · Иванов Иван/);
    assert.match(pending, /data-fold="letter-1"/, 'письмо раскрывается по высоте, как разделы окна');
    assert.match(pending, />Письмо<\/span>/);
    assert.doesNotMatch(pending, /Здравствуйте!|<iframe/, 'письмо раскрывается по запросу');
    assert.doesNotMatch(pending, /Отправить ещё раз/);

    const done = (latest) => renderToStaticMarkup(h(MailingCard, {
        item: { id: 2, status: 'done', changed: ['Банк'], latest, recipients: [
            { email: 'nobody@alfa.kz', counterparty_names: ['ТОО «Альфа»'], status: 'failed', next_attempt_at: null, last_error: 'Сервер не принял адрес: 550 No such user' },
            { email: 'old@gamma.kz', counterparty_names: ['ТОО «Гамма»'], status: 'removed', last_error: 'Сервер не принял адрес: 550' },
            { email: 'b@beta.kz', counterparty_names: ['ТОО «Бета»'], status: 'sent' },
        ], counts: { total: 2, sent: 1, failed: 1, removed: 1 } },
        ready: true,
    }));
    const latest = done(true);
    assert.match(latest, /Отправлено 1 из 3 адресов · не ушло: 1 · адрес убран: 1/);
    assert.match(latest, /nobody@alfa\.kz · ТОО «Альфа»<\/span><span class="text-slate-500"> — Сервер не принял адрес: 550 No such user/);
    assert.match(latest, /old@gamma\.kz · ТОО «Гамма»<\/span><span class="text-slate-500"> — адрес убран из карточки поставщика — письмо по нему не ушло/);
    assert.doesNotMatch(latest, /b@beta\.kz/, 'ушедшие адреса не перечисляются');
    assert.match(latest, /Отправить ещё раз<\/button>/);
    assert.doesNotMatch(done(false), /Отправить ещё раз/, 'у старого письма повтора нет: у поставщика оказались бы старые реквизиты');

    const stuck = renderToStaticMarkup(h(MailingCard, {
        item: { id: 3, status: 'sending', last_error: 'Почта не приняла логин или пароль приложения', recipients: [], counts: { total: 2, sent: 0 } },
        ready: true, pauseMinutes: 10,
    }));
    assert.match(stuck, /Не уходит: 0 из 2 адресов/);
    assert.match(stuck, /Почта не приняла логин или пароль приложения\. Попробуем снова через 10 минут\./);
    const waitingStuck = renderToStaticMarkup(h(MailingCard, {
        item: { id: 4, status: 'pending', send_after: FUTURE, last_error: 'Нет связи с почтовым сервером', recipients: [], counts: {} },
        ready: true,
    }));
    assert.match(waitingStuck, /Нет связи с почтовым сервером\. Попробуем снова/, 'и у ждущего письма видно, почему не уходит');
});

test('блок писем: заготовка в рост, пока история едет; без писем и правки — ничего', async () => {
    const { default: RequisitesMailings } = await bundle('src/components/payments/RequisitesMailings.jsx', 'mailings');
    const props = (entity) => ({ apiBaseUrl: '', headers: () => ({}), entity, draft: entity, showToast: () => {} });
    const loading = renderToStaticMarkup(h(RequisitesMailings, props({ id: 3, mailings_count: 5 })));
    assert.match(loading, /data-requisites-mailings="loading"/);
    assert.equal((loading.match(/animate-pulse/g) || []).length, 9, 'три заготовки карточек по три строки');
    assert.equal(renderToStaticMarkup(h(RequisitesMailings, props({ id: 3, mailings_count: 0 }))), '', 'писем не было — ничего');
    assert.equal(renderToStaticMarkup(h(RequisitesMailings, props({ id: null }))), '');
});

test('компании: метка письма в строке; колонка шире, только пока метка есть', async () => {
    const { dictionaryColumns, dictionaryTableMin } = await bundle('src/components/payments/PaymentsDictionaries.jsx', 'dictionaries');
    const plain = dictionaryColumns('legal_entities');
    const wide = dictionaryColumns('legal_entities', { mailWide: true });
    assert.equal(dictionaryTableMin(plain), 720, 'без меток — прежняя ширина таблицы');
    assert.ok(dictionaryTableMin(wide) <= 820);
    const state = wide.find((column) => column.key === 'state');
    const row = { id: 1, name: 'ТОО «Наша»', requisites_text: 'реквизиты', is_active: true, mailbox: { address: 'buh@company.kz', problem: null } };
    assert.match(renderToStaticMarkup(state.cell({ ...row, mailing: { status: 'done', failed: 1 } })), /письмо не ушло: 1/);
    assert.doesNotMatch(renderToStaticMarkup(state.cell({ ...row, mailing: { status: 'done', failed: 0 } })), /письмо/);
    // Почта — у каждой компании своя: нет её или она не пускает — письмо этой компании ждёт.
    assert.match(renderToStaticMarkup(state.cell({ ...row, mailbox: null, mailing: { status: 'pending', send_after: FUTURE } })), /письмо ждёт почту/);
    assert.match(renderToStaticMarkup(state.cell({ ...row, mailbox: { address: 'buh@company.kz', problem: 'не пускает' },
        mailing: { status: 'pending', send_after: FUTURE } })), /письмо ждёт почту/);
    assert.match(renderToStaticMarkup(state.cell({ ...row, mailing: { status: 'pending', send_after: FUTURE } })), /письмо (в|\d)/);

    const [name] = dictionaryColumns('counterparties');
    assert.equal(name.cell({ name: 'ТОО «Ромашка»', kind: 'too', bin: '123456789012', vat_payer: true, emails: ['buh@romashka.kz', 'info@romashka.kz'] }).sub,
        'ТОО · БИН 123456789012 · с НДС · buh@romashka.kz +1');
});

test('окно справочника: поле «Почта» у поставщика, письма — только у компании и только тому, кто правит', () => {
    const dict = source('src/components/payments/PaymentsDictionaries.jsx');
    assert.match(dict, /<Field label="Почта" hint=\{HINTS\.supplierEmails\} anchor="supplier-emails" issue=\{emailsTried \? \(emailsIssue\(draft\.emails\) \|\| emailsRefused\) : ''\}>/);
    assert.match(dict, /emails: \(row\.emails \|\| \[\]\)\.join\(', '\)/, 'в окне адреса через запятую');
    assert.match(dict, /placeholder: 'Имя, телефон' \}/, 'почта больше не пишется в «Контакт»');
    assert.match(dict, /\{editing && !isNew && canEdit && name === 'legal_entities' && \(\n\s+<RequisitesMailings /);
    assert.match(dict, /toastRef\.current\?\.\(savedText\(response\.data\?\.mailing\), 'success'\)/);
    // Неверный адрес не уходит на сервер: поле краснеет и окно подводит к нему, с клавиатуры — с фокусом.
    const start = dict.indexOf('const save = async (event) => {', dict.indexOf('const savedText = '));
    const save = dict.slice(start, dict.indexOf('const remove = async () => {', start));
    assert.ok(save.includes('emailsIssue(draft.emails)') && save.indexOf('emailsIssue(draft.emails)') < save.indexOf('axios.post'));
    assert.match(dict, /revealAnchor\(\s*document\.querySelector\('\[data-issue-anchor="supplier-emails"\]'\), \{ focus: fromKeyboard\(event\) \}\)/);
    // Отказ сервера в адресе — красным у поля, а не строкой сверху окна.
    assert.match(save, /err\?\.response\?\.data\?\.field === 'emails'/);
});

// ── «Письма поставщикам»: журнал и окно письма ────────────────────────────────

test('журнал писем: день, время, буквы компании и что с адресом', async () => {
    const { letterDay, letterTime, letterInitials, letterMoment, recipientState } = await import('../src/components/payments/paymentsMeta.js');
    assert.equal(letterDay('2026-10-20T16:43:00', TODAY), 'Сегодня');
    assert.equal(letterDay('2026-10-19T09:00:00', TODAY), 'Вчера');
    assert.equal(letterDay('2026-03-03T09:00:00', TODAY), '3 марта');
    assert.equal(letterDay('2025-12-31T09:00:00', TODAY), '31 декабря 2025');
    assert.equal(letterDay('2026-01-01T00:00:00', '2026-01-02'), 'Вчера');
    assert.equal(letterTime('2026-10-20T16:43:00', TODAY), '16:43');
    assert.equal(letterTime('2026-10-18T16:43:00', TODAY), '18.10');
    assert.deepEqual(['ИП Иванов', 'ТОО «Альфа Кор»', 'ТОО «Бизнес-центр Северный»', ''].map(letterInitials), ['И', 'АК', 'БС', '·']);
    assert.equal(letterMoment({ status: 'pending', send_after: 'a', started_at: 'b' }), 'a', 'ждёт — когда уйдёт');
    assert.equal(letterMoment({ status: 'done', started_at: 'b', updated_at: 'c' }), 'b', 'ушло — когда ушло');
    assert.equal(letterMoment({ status: 'cancelled', cancelled_at: 'd', started_at: 'b' }), 'd');
    assert.deepEqual(recipientState({ status: 'sent', sent_at: '2026-10-20T16:43:10' }), { tone: 'done', label: 'ушло в 16:43' });
    assert.deepEqual(recipientState({ status: 'failed', next_attempt_at: null }), { tone: 'overdue', label: 'не ушло' });
    assert.deepEqual(recipientState({ status: 'failed', next_attempt_at: '2026-10-20T16:48:00' }, { today: TODAY }), { tone: 'blocked', label: 'повтор в 16:48' });
    assert.deepEqual(recipientState({ status: 'queued' }), { tone: 'current', label: 'ждёт' });
    assert.deepEqual(recipientState({ status: 'removed' }), { tone: 'neutral', label: 'адрес убран' });
    assert.deepEqual(recipientState({ status: 'superseded' }), { tone: 'neutral', label: 'новым письмом' });
});

const LETTER = {
    id: 5, legal_entity_id: 2, legal_entity_name: 'ИП Иванов', status: 'done', started_at: '2026-10-20T16:43:00',
    letter: { subject: 'Изменение реквизитов ИП Иванов', text: 'Здравствуйте!', html: '<!doctype html><html><body><p>Здравствуйте!</p></body></html>' },
    recipients: [
        { email: 'nobody@alfa.kz', counterparty_names: ['ТОО «Альфа»'], status: 'failed', next_attempt_at: null, last_error: 'Сервер не принял адрес: 550 No such user' },
        { email: 'b@beta.kz', counterparty_names: ['ТОО «Бета»', 'ИП Бетов'], status: 'sent', sent_at: '2026-10-20T16:43:05' },
        { email: 'c@gamma.kz', counterparty_names: ['ТОО «Гамма»'], status: 'sent', sent_at: '2026-10-20T16:43:06' },
    ],
};

test('окно письма: шапка как в «Почте» и само письмо во фрейме без скриптов', async () => {
    const { LetterView } = await bundle('src/components/payments/SupplierLetters.jsx', 'letters');
    const html = renderToStaticMarkup(h(LetterView, { item: LETTER, sender: { name: 'ИП Иванов', address: 'buh@company.kz' } }));
    assert.match(html, /data-letter-view/);
    assert.match(html, /rounded-full bg-gradient-to-b from-slate-300 to-slate-400[^>]*>И<\/span>/, 'кружок с буквами компании');
    assert.match(html, />ИП Иванов<\/span>/);
    assert.match(html, />buh@company\.kz<\/div>/);
    assert.match(html, /Кому: <\/span>b@beta\.kz, c@gamma\.kz и ещё 1/, 'адресаты по алфавиту, как в «Почте»');
    assert.match(html, />Изменение реквизитов ИП Иванов<\/div>/);
    assert.match(html, /20\.10\.2026 16:43/);
    const frame = (html.match(/<iframe[^>]*>/) || [''])[0];
    assert.match(frame, /sandbox="allow-same-origin"/, 'без allow-scripts: скрипты в письме не выполнятся');
    assert.doesNotMatch(frame, /allow-scripts/);
    assert.match(frame, /srcDoc="&lt;!doctype html&gt;/i);
    const unplugged = renderToStaticMarkup(h(LetterView, { item: { ...LETTER, recipients: [] }, sender: { name: 'ИП Иванов', address: null }, recipients: { addresses: 7 } }));
    assert.match(unplugged, />почта для писем не подключена<\/div>/);
    assert.match(unplugged, /Кому: <\/span>поставщикам с почтой — 7 адресов/);
});

test('строка журнала: красным — только то, что не ушло; ряды не переносятся на телефоне', async () => {
    const { LetterRow } = await bundle('src/components/payments/SupplierLetters.jsx', 'letters');
    const row = renderToStaticMarkup(h(LetterRow, { item: { id: 5, status: 'done', legal_entity_name: 'ИП Иванов', started_at: '2026-10-18T16:43:00',
        changed: ['ИИК', 'Банк'], updated_by_name: 'Иванов Иван', counts: { total: 8, sent: 7, failed: 1 } }, onOpen: () => {} }));
    assert.match(row, /<span class="text-rose-700">Отправлено 7 из 8 адресов · не ушло: 1<\/span> · Иванов Иван/);
    assert.match(row, />18\.10<\/span>/);
    assert.match(row, /Изменилось: ИИК, банк/);
    assert.match(row, /min-w-0 grow basis-0/, 'basis-0: оболочка телефона иначе уносит текст под кружок');
    const { LetterView } = await bundle('src/components/payments/SupplierLetters.jsx', 'letters');
    assert.match(renderToStaticMarkup(h(LetterView, { item: LETTER, sender: { name: 'ИП', address: 'a@b.kz' } })), /min-w-0 grow basis-0/);
});

test('«Кому»: по адресу — поставщики, ушло или нет и почему', async () => {
    const { RecipientList } = await bundle('src/components/payments/SupplierLetters.jsx', 'letters');
    const html = renderToStaticMarkup(h(RecipientList, { item: LETTER }));
    assert.equal((html.match(/data-recipient="/g) || []).length, 3);
    assert.match(html, /nobody@alfa\.kz<\/div><div[^>]*>ТОО «Альфа»<\/div><div[^>]*>Сервер не принял адрес: 550 No such user<\/div>/);
    assert.match(html, /не ушло<\/span>/);
    assert.match(html, /ТОО «Бета», ИП Бетов/);
    assert.match(html, /ушло в 16:43<\/span>/);
    const waiting = renderToStaticMarkup(h(RecipientList, { item: { ...LETTER, recipients: [] }, recipients: { addresses: 7, without_email: 20 } }));
    assert.match(waiting, /Адреса соберутся в момент отправки/);
    assert.match(waiting, /Сейчас это 7 адресов; без почты — 20 поставщиков\./);
});

test('экран «Письма поставщикам»: только администратору, заготовка с первого кадра', async () => {
    const { dictionarySections } = await bundle('src/components/payments/PaymentsDictionaries.jsx', 'dictionaries');
    assert.ok(!dictionarySections({}).some((item) => item.value === 'mailings'), 'не администратору журнала нет');
    const section = dictionarySections({ is_admin: true }).find((item) => item.value === 'mailings');
    assert.equal(section.label, 'Письма поставщикам');
    assert.equal(section.group, 'pay');
    const { default: SupplierLetters, LetterWindow } = await bundle('src/components/payments/SupplierLetters.jsx', 'letters');
    const screen = renderToStaticMarkup(h(SupplierLetters, { apiBaseUrl: '', headers: () => ({}), showToast: () => {} }));
    assert.match(screen, /data-supplier-letters/);
    assert.equal((screen.match(/animate-pulse/g) || []).length, 9, 'три строки-заготовки');
    assert.equal(renderToStaticMarkup(h(LetterWindow, { apiBaseUrl: '', headers: () => ({}), mailingId: null, onClose: () => {} })), '',
        'окно закрыто — ничего не рисуется');
    const dict = source('src/components/payments/PaymentsDictionaries.jsx');
    assert.match(dict, /\{name === 'mailings' && <SupplierLetters apiBaseUrl=\{apiBaseUrl\} headers=\{headers\} showToast=\{showToast\} \/>\}/);
    assert.match(dict, /const OWN_SCREENS = \['settings', 'templates', 'mailings'\];/);
});

// ── 21.10.2026: почта у каждой компании, галочки «Кому», «Почта поставщиков» ──

test('почта компании, «Почта поставщиков» и журнал: подписи словами', async () => {
    const { addressLetterState, excludedLine, mailboxLine, serverLine, waitingMailboxLine } = await import('../src/components/payments/paymentsMeta.js');
    assert.equal(mailboxLine({ label: 'Gmail', checked_at: '2026-10-20T11:40:00' }, TODAY), 'Gmail · проверено в 11:40');
    assert.equal(mailboxLine({ host: 'smtp.hoster.kz', port: 587, checked_at: '2026-10-18T09:05:00' }, TODAY),
        'smtp.hoster.kz:587 · проверено 18.10 в 09:05');
    assert.equal(mailboxLine({ label: 'Яндекс' }, TODAY), 'Яндекс');
    assert.equal(mailboxLine(null), '');
    assert.equal(serverLine({ label: 'Яндекс', host: 'smtp.yandex.ru' }), 'Яндекс · smtp.yandex.ru');
    assert.equal(serverLine(null), '');
    assert.deepEqual(addressLetterState(null), { tone: 'neutral', label: 'писем не было' });
    assert.deepEqual(addressLetterState({ status: 'sent', sent_at: '2026-10-20T16:43:00' }, TODAY), { tone: 'done', label: 'ушло в 16:43' });
    assert.deepEqual(addressLetterState({ status: 'sent', sent_at: '2026-10-18T16:43:00' }, TODAY), { tone: 'done', label: 'ушло 18.10 в 16:43' });
    assert.deepEqual(addressLetterState({ status: 'failed' }, TODAY), { tone: 'overdue', label: 'не дошло' });
    assert.deepEqual(addressLetterState({ status: 'failed', next_attempt_at: '2026-10-20T16:48:00' }, TODAY), { tone: 'blocked', label: 'повтор в 16:48' });
    assert.deepEqual(addressLetterState({ status: 'queued' }, TODAY), { tone: 'current', label: 'письмо уходит' });
    assert.equal(excludedLine(['ТОО «Икс»', 'ТОО «Игрек»']), 'Не отправляли: ТОО «Икс», ТОО «Игрек»');
    assert.equal(excludedLine([]), '');
    assert.equal(waitingMailboxLine([]), '');
    assert.equal(waitingMailboxLine([{ name: 'ТОО «Гамма»', connected: false }, { name: 'ТОО «Альфа»', connected: true }]),
        'Письма ждут почту: у ТОО «Гамма» она не подключена. Почта ТОО «Альфа» не пускает — введите пароль заново. '
        + 'Почту подключают в «Реквизитах компаний», в окне компании — блок «Почта для писем».');
});

const CANDIDATES = [
    { id: 1, name: 'ТОО «Зет»', is_active: true, emails: ['shared@group.kz'], send: true },
    { id: 2, name: 'ТОО «Игрек»', is_active: true, emails: ['y@y.kz', 'shared@group.kz'], send: false },
    { id: 3, name: 'ТОО «Икс»', is_active: false, emails: ['x@x.kz'], send: true },
];

test('«Кому»: сколько поставщиков отмечено и сколько адресов получат', async () => {
    const { recipientsChosen, recipientsLine } = await import('../src/components/payments/paymentsMeta.js');
    assert.deepEqual(recipientsChosen(CANDIDATES), { chosen: 2, total: 3, addresses: 2 }, 'общий адрес — один раз');
    assert.equal(recipientsLine(CANDIDATES), '2 из 3 поставщиков · 2 адреса');
    assert.equal(recipientsLine(CANDIDATES.map((item) => ({ ...item, send: true }))), 'все 3 поставщика · 3 адреса');
    assert.equal(recipientsLine([{ ...CANDIDATES[0] }]), '1 поставщик · 1 адрес');
    assert.equal(recipientsLine(CANDIDATES.map((item) => ({ ...item, send: false }))), 'никому — письмо не уйдёт');
    assert.equal(recipientsLine([]), 'ни у одного поставщика нет почты');
    assert.equal(recipientsLine([{ ...CANDIDATES[0], send: true }, { ...CANDIDATES[1], send: false }, ...Array.from({ length: 19 }, (_, index) => (
        { id: 10 + index, name: `П${index}`, emails: [`p${index}@x.kz`], send: false }))]), '1 из 21 поставщика · 1 адрес');
    const { allRecipients, toggleRecipient } = await import('../src/components/payments/paymentsMeta.js');
    assert.deepEqual(toggleRecipient(CANDIDATES, 1), [2, 1], 'сняли отмеченного — его не отправлять');
    assert.deepEqual(toggleRecipient(CANDIDATES, 2), [], 'отметили снятого — отправлять всем');
    assert.deepEqual(toggleRecipient(CANDIDATES, 99), [2], 'нет такого — ничего не меняется');
    assert.deepEqual(allRecipients(CANDIDATES, true), []);
    assert.deepEqual(allRecipients(CANDIDATES, false), [1, 2, 3]);
    assert.match(mailNotice({ recipients: { addresses: 0, without_email: 0, excluded: 3 } }),
        /Но все поставщики с почтой сняты в «Кому» — письмо некому отправить\.$/);
});

test('галочки «Кому» в разметке: отмечен — синий кружок с галочкой, снят — пустой', async () => {
    const { RecipientPicker } = await bundle('src/components/payments/SupplierLetters.jsx', 'letters');
    const html = renderToStaticMarkup(h(RecipientPicker, { candidates: CANDIDATES, onChange: () => {} }));
    assert.equal((html.match(/role="checkbox" aria-checked="true"/g) || []).length, 2);
    assert.equal((html.match(/role="checkbox" aria-checked="false"/g) || []).length, 1);
    assert.match(html, /aria-checked="true"[^>]*data-candidate="1" data-candidate-send="yes"/);
    assert.match(html, /border-\[#2563eb\] bg-\[#2563eb\] text-white/, 'отмеченное — синим, как выбор на сайте, а не чёрным');
    assert.match(html, />скрыт<\/span>/, 'скрытый поставщик — тоже в списке (решение 20.10: «никого не пропустим»)');
    assert.match(html, />y@y\.kz, shared@group\.kz<\/span>/);
    assert.match(html, />Отметить всех<\/button>/);
    assert.match(html, /data-recipient-summary="true">2 из 3 поставщиков · 2 адреса</);
    assert.doesNotMatch(html, /Найти поставщика/, 'поиск — только у длинного списка');
    const all = renderToStaticMarkup(h(RecipientPicker, { candidates: CANDIDATES.map((item) => ({ ...item, send: true })) }));
    assert.match(all, />Снять всех<\/button>/);
    const many = Array.from({ length: 9 }, (_, index) => ({ id: index + 1, name: `ТОО «П${index}»`, is_active: true, emails: [`p${index}@x.kz`], send: true }));
    assert.match(renderToStaticMarkup(h(RecipientPicker, { candidates: many })), /placeholder="Найти поставщика или адрес"/);
    assert.match(renderToStaticMarkup(h(RecipientPicker, { candidates: [] })), /Ни у одного поставщика нет почты/);
    assert.match(renderToStaticMarkup(h(RecipientPicker, { candidates: CANDIDATES, busy: true })), /disabled=""/, 'пока запрос идёт — не нажимаются');
});

test('блок «Почта для писем»: подключена, не пускает, не подключена', async () => {
    const { default: CompanyMailbox } = await bundle('src/components/payments/CompanyMailbox.jsx', 'mailbox');
    const props = (mailbox) => ({ apiBaseUrl: '', headers: () => ({}), entity: { id: 3, name: 'ТОО «Наша»', mailbox }, showToast: () => {} });
    const box = { address: 'buh@company.kz', label: 'Gmail', host: 'smtp.gmail.com', port: 465, manual: false, checked_at: '2026-10-20T11:40:00', problem: null };
    const ready = renderToStaticMarkup(h(CompanyMailbox, props(box)));
    assert.match(ready, /data-company-mailbox="ready"/);
    assert.match(ready, /data-mailbox-address="true">buh@company\.kz</);
    assert.match(ready, />подключена</);
    for (const label of ['Отключить', 'Изменить', 'Проверить']) assert.match(ready, new RegExp(`> ?${label}</button>`));
    assert.doesNotMatch(ready, /type="password"/, 'у подключённой почты пароль не спрашивается и не показывается');
    const broken = renderToStaticMarkup(h(CompanyMailbox, props({ ...box, problem: 'Почта не приняла логин или пароль приложения (535)' })));
    assert.match(broken, /data-company-mailbox="problem"/);
    assert.match(broken, />не пускает</);
    assert.match(broken, /Почта не приняла логин или пароль приложения \(535\)\. Письма компании ждут — введите пароль заново\./);
    assert.match(broken, />Ввести пароль<\/button>/);
    const empty = renderToStaticMarkup(h(CompanyMailbox, props(null)));
    assert.match(empty, /data-company-mailbox="edit"/);
    assert.match(empty, /Пока почта не подключена, письма ждут\./);
    assert.match(empty, /type="password" name="mailbox-app-password" autoComplete="new-password"/i, 'браузер не подставит сюда пароль от портала');
    assert.match(empty, /data-mailbox-connect="true">Подключить</);
    assert.match(empty, /Проверим: отправим пробное письмо на этот адрес\./);
    assert.doesNotMatch(empty, />Отмена</, 'нечего отменять — почты ещё нет');
    assert.equal(renderToStaticMarkup(h(CompanyMailbox, { ...props(null), entity: { id: null } })), '');
    const source_ = source('src/components/payments/CompanyMailbox.jsx');
    assert.match(source_, /target="_blank" rel="noopener noreferrer"/, 'ссылка на пароль приложения — в новой вкладке');
});

test('карточка ждущего письма: «Кому» с галочками; у ушедшего — кого не отправляли', async () => {
    const { MailingCard } = await bundle('src/components/payments/RequisitesMailings.jsx', 'mailings');
    const pending = renderToStaticMarkup(h(MailingCard, {
        item: { id: 1, status: 'pending', send_after: FUTURE, changed: ['ИИК'], counts: {}, recipients: [], candidates: CANDIDATES },
        ready: true, onCancel: () => {}, onRetry: () => {}, onPick: () => {},
    }));
    assert.match(pending, /data-mailing-recipients/);
    assert.match(pending, />Кому</);
    assert.match(pending, /2 из 3 поставщиков · 2 адреса/);
    const done = renderToStaticMarkup(h(MailingCard, {
        item: { id: 2, status: 'done', counts: { total: 1, sent: 1 }, recipients: [], excluded_names: ['ТОО «Игрек»'] },
        ready: true, onCancel: () => {}, onRetry: () => {},
    }));
    assert.match(done, /data-excluded-names="true">Не отправляли: ТОО «Игрек»</);
    assert.doesNotMatch(done, /data-mailing-recipients/, 'ушедшему письму галочки не нужны');
});

test('«Почта поставщиков»: колонки, проверка строки, место в меню', async () => {
    const { dictionaryColumns, dictionarySections, emailRowIssues } = await bundle('src/components/payments/PaymentsDictionaries.jsx', 'dictionaries');
    const columns = dictionaryColumns('counterparty_emails');
    assert.deepEqual(columns.map((column) => column.key), ['email', 'party', 'letter']);
    const row = { id: 1, email: 'buh@supplier.kz', contact_name: 'Айгерим, бухгалтер', counterparty_name: 'ТОО «Поставщик»',
        counterparty_active: false, note: 'с 2025', last_letter: { status: 'sent', sent_at: '2026-10-18T16:43:00' } };
    assert.deepEqual(columns[0].cell(row), { main: 'buh@supplier.kz', strong: true, sub: 'Айгерим, бухгалтер' });
    assert.deepEqual(columns[1].cell(row), { main: 'ТОО «Поставщик»', sub: 'поставщик скрыт · с 2025' });
    assert.match(renderToStaticMarkup(columns[2].cell(row)), /ушло 18\.10 в 16:43/);
    assert.match(renderToStaticMarkup(columns[2].cell({ ...row, last_letter: null })), /писем не было/);
    assert.deepEqual(emailRowIssues({}), { counterparty_id: 'Выберите поставщика, чей это адрес', email: 'Укажите адрес почты' });
    assert.deepEqual(emailRowIssues({ counterparty_id: 7, email: ' Buh@Supplier.KZ ' }), {});
    assert.deepEqual(emailRowIssues({ counterparty_id: 7, email: 'a@x.kz, b@x.kz' }), { email: 'Один адрес — одна строка' });
    assert.deepEqual(emailRowIssues({ counterparty_id: 7, email: 'buh@supplier' }), { email: '«buh@supplier» — не похоже на адрес почты' });
    const section = dictionarySections({ is_admin: false }).find((item) => item.value === 'counterparty_emails');
    assert.equal(section.label, 'Почта поставщиков', 'справочник читает любой участник, как и поставщиков');
    const dict = source('src/components/payments/PaymentsDictionaries.jsx');
    // «Сохранить» с набранной, но не подключённой почтой компании подключает её раньше записи реквизитов.
    const start = dict.indexOf('const save = async (event) => {', dict.indexOf('const savedText = '));
    const save = dict.slice(start, dict.indexOf('const remove = async () => {', start));
    assert.ok(save.indexOf('mailboxControl.current?.dirty()') !== -1 && save.indexOf('mailboxControl.current?.dirty()') < save.indexOf('axios.post'));
    assert.match(dict, /<CompanyMailbox key=\{editing\.id\}/);
    assert.ok(dict.indexOf('<CompanyMailbox ') < dict.indexOf('<RequisitesMailings '), 'почта — над письмами');
});

test('«Войти через Google»: кнопка, когда вход настроен; у подключённой — «вход через Google» и «Войти заново»', async () => {
    const { default: CompanyMailbox } = await bundle('src/components/payments/CompanyMailbox.jsx', 'mailbox');
    const { mailboxLine } = await import('../src/components/payments/paymentsMeta.js');
    const props = (entity) => ({ apiBaseUrl: 'https://api.example', headers: () => ({}), entity: { id: 3, name: 'ТОО «Наша»', ...entity }, showToast: () => {} });
    const on = renderToStaticMarkup(h(CompanyMailbox, props({ mailbox: null, google_mail: true })));
    assert.match(on, /data-mailbox-google-start="true"[^>]*>.*Войти через Google<\/button>/s);
    assert.match(on, /<svg viewBox="0 0 48 48"/, 'значок Google по правилам оформления кнопки');
    assert.match(on, /или по паролю приложения/);
    assert.ok(on.indexOf('Войти через Google') < on.indexOf('Адрес почты'), 'Google — первым, пароль — запасной путь');
    const off = renderToStaticMarkup(h(CompanyMailbox, props({ mailbox: null, google_mail: false })));
    assert.doesNotMatch(off, /Войти через Google/, 'вход не настроен на сервере — кнопки нет');
    const google = { address: 'buh@company.kz', label: 'Gmail', method: 'google', host: 'gmail.googleapis.com', port: 443,
        checked_at: '2026-10-21T20:05:00', problem: null };
    const ready = renderToStaticMarkup(h(CompanyMailbox, props({ mailbox: google, google_mail: true })));
    assert.match(ready, /Gmail · вход через Google · проверено/);
    assert.match(ready, /> ?Войти заново<\/button>/);
    assert.doesNotMatch(ready, />Изменить</, 'у входа через Google пароля нет — менять нечего');
    const broken = renderToStaticMarkup(h(CompanyMailbox, props({ mailbox: { ...google, problem: 'Google отозвал доступ к почте' }, google_mail: true })));
    assert.match(broken, /Google отозвал доступ к почте\. Письма компании ждут — войдите через Google заново\./);
    assert.equal(mailboxLine({ method: 'google', label: 'Gmail' }), 'Gmail · вход через Google');
    const source_ = source('src/components/payments/CompanyMailbox.jsx');
    assert.match(source_, /event\.origin !== origin/, 'итог принимается только от страницы API');
    assert.match(source_, /window\.location\.assign\(url\)/, 'окошки запрещены — Google в этой же вкладке');
});
