/*
 * «Оплата счетов»: почта поставщиков и письма о смене реквизитов наших компаний.
 *
 * Просьба владельца 20.10.2026: «добавление эмайла контрагентов поставщиков и тд, автомат
 * рассылка по эмайлом при изменении реквизитов». Решения владельца: письмо — о реквизитах
 * НАШЕЙ компании, всем поставщикам с почтой, с «Не отправлять»; 27.10.2026 — «пока убери
 * автоотправку и сделай полу ручной»: «Отправить письмо» открывает почту компании с готовым
 * письмом, отправляет человек.
 *
 * Здесь — правила формы (те же, что у сервера: случаи общие с tests/test_payments_requisites_mail.py,
 * tests/data/payments_requisites_mail_cases.json), строки и метки писем, ссылка на почту и то, что
 * видно в окне справочника без браузера. Что значат запросы, проверяет
 * tests/test_payments_requisites_mail_postgres.py.
 *
 * Запуск: node --test tests/payments_requisites_mail.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

import {
    GMAIL_LIMIT, HINTS, MAILTO_LIMIT, MAX_SUPPLIER_EMAILS, REQUISITE_FIELDS, changedLine, composeLink, emailsIssue, emailsShort,
    isPhoneDevice, letterAddresses, lettersEmailIssue, mailNotice, mailingPill, mailingSummary, mailingWhen, openedToast, parseEmails,
    requisitesChanged,
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

test('когда: сегодня — время, в другой день — и число', () => {
    assert.equal(mailingWhen('2026-10-20T14:35:00', TODAY), 'в 14:35');
    assert.equal(mailingWhen('2026-10-21T00:05:00', TODAY), '21.10 в 00:05');
    assert.equal(mailingWhen('2026-10-20', TODAY), '');
    assert.equal(mailingWhen(null), '');
});

test('метка в строке компании — только у письма, которое ещё не отправили', () => {
    assert.equal(mailingPill(null), null);
    assert.deepEqual(mailingPill({ status: 'pending', send_after: '2026-10-20T14:35:00' }), { tone: 'blocked', label: 'письмо не отправлено' });
    for (const status of ['opened', 'done', 'cancelled', 'sending']) {
        assert.equal(mailingPill({ status, failed: 2 }), null, `${status}: метки нет — делать с письмом нечего`);
    }
});

test('письмо из истории одной строкой', () => {
    const at = { today: TODAY };
    assert.equal(mailingSummary({ status: 'pending', send_after: '2026-10-20T14:35:00', last_error: 'x' }, at), 'Ждёт отправки');
    assert.equal(mailingSummary({ status: 'opened', opened_at: '2026-10-20T14:35:00', counts: { total: 4, opened: 4 } }, at),
        'Открыто в почте в 14:35 · 4 адреса');
    assert.equal(mailingSummary({ status: 'opened', opened_at: '2026-10-19T09:05:00', counts: { total: 1, opened: 1 } }, at),
        'Открыто в почте 19.10 в 09:05 · 1 адрес');
    assert.equal(mailingSummary({ status: 'opened', opened_at: '2026-10-20T14:35:00' }, at), 'Открыто в почте в 14:35');
    assert.equal(mailingSummary({ status: 'opened' }, at), 'Открыто в почте');
    // Письма, которые до 27.10.2026 отправлял сам портал.
    assert.equal(mailingSummary({ status: 'done', counts: { total: 1, sent: 1, failed: 0 } }, at), 'Отправлено на 1 адрес');
    assert.equal(mailingSummary({ status: 'done', counts: { total: 3, sent: 3, failed: 0 } }, at), 'Отправлено на 3 адреса');
    assert.equal(mailingSummary({ status: 'done', counts: { total: 4, sent: 3, failed: 1 } }, at), 'Отправлено 3 из 4 адресов · не ушло: 1');
    assert.equal(mailingSummary({ status: 'sending', counts: { total: 2, sent: 1, failed: 1 } }, at), 'Отправлено 1 из 2 адресов · не ушло: 1',
        'недосланное при выкладке закрыто — как ушедшее');
    assert.equal(mailingSummary({ status: 'done', counts: { total: 4, sent: 4, failed: 0, removed: 1 } }, at),
        'Отправлено 4 из 5 адресов · адрес убран: 1', 'убранный адрес не прячется за «отправлено на все»');
    assert.equal(mailingSummary({ status: 'done', counts: { total: 3, sent: 3, superseded: 2 } }, at),
        'Отправлено на 3 адреса · ещё 2 адреса — новым письмом');
    assert.equal(mailingSummary({ status: 'done', counts: { total: 0 } }, at), 'Некому было отправить: ни у одного поставщика нет почты');
    assert.equal(mailingSummary({ status: 'cancelled', cancelled_by_name: 'Иванов Иван' }, at), 'Отменено · Иванов Иван');
});

test('строка перед сохранением: будет ли письмо, сколько адресов и у кого почты нет', () => {
    const recipients = { addresses: 6, suppliers: 5, without_email: 2, without_names: ['ИП Бетов', 'ТОО «Альфа»'] };
    assert.equal(mailNotice({ recipients }),
        'После сохранения будет готово письмо поставщикам о новых реквизитах — его отправляют кнопкой «Отправить письмо». '
        + 'Получат 6 адресов. Без почты — 2 поставщика: ИП Бетов, ТОО «Альфа».');
    assert.match(mailNotice({ recipients, waiting: true }), /^Письмо, которое ждёт отправки, будет уже с этими реквизитами\. Получат 6 адресов/);
    assert.doesNotMatch(mailNotice({ recipients }), /минут|уйдёт/, 'сроков больше нет: письмо отправляет человек');
    assert.match(mailNotice({ recipients: { addresses: 0, without_email: 3 } }), /ни у одного поставщика не указана почта — письмо некому отправить\.$/);
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

test('подсказки «i»: как отправить письмо и кто его получает', () => {
    assert.match(HINTS.supplierEmails.outro, /через запятую/);
    assert.doesNotMatch(HINTS.supplierEmails.outro, /само уходит/, 'письма портал больше не отправляет');
    const outro = HINTS.requisitesMail.outro;
    for (const part of [/«Отправить письмо»/, /«Скрытой копии»/, /Ctrl\+V, на Mac ⌘V/, /«Не отправлять»/, /портал не видит/]) assert.match(outro, part);
    assert.doesNotMatch(outro, /через 10 минут|принял почтовый сервер/);
    assert.doesNotMatch(outro, /кроме скрытых/, 'получают все поставщики с почтой — решение владельца');
    assert.match(HINTS.lettersEmail.outro, /Gmail откроется в браузере/);
    assert.match(HINTS.lettersEmail.outro, /Outlook/);
    assert.equal(HINTS.companyMailbox, undefined, 'пароля приложения и входа через Google больше нет');
    assert.match(HINTS.recipientsPick.outro, /пока письмо не открыто в почте/);
});

// ── «Отправить письмо» (27.10.2026) ───────────────────────────────────────────

// Публичная почта — только доменом: адресов на ней в репозитории нет (tests/test_no_personal_data_in_repo.py).
const GMAIL = ['buh', 'gmail.com'].join('@');
const SUBJECT = 'Изменение реквизитов ТОО «Наша»';

test('ссылка на письмо: Gmail — в браузере, в аккаунте компании; иначе — почтовой программой; текста в ссылке нет', () => {
    const bcc = ['a@x.kz', 'b+tag@y.kz'];
    const gmail = composeLink({ provider: 'gmail', from: GMAIL, bcc, subject: SUBJECT });
    assert.equal(gmail.href, `https://mail.google.com/mail/u/${GMAIL}/?to=${GMAIL}&bcc=a@x.kz,b%2Btag@y.kz&su=${encodeURIComponent(SUBJECT)}&tf=cm`);
    assert.deepEqual([gmail.web, gmail.tooLong], [true, false]);
    const program = composeLink({ provider: null, from: 'buh@company.kz', bcc, subject: SUBJECT });
    assert.equal(program.href, `mailto:buh@company.kz?bcc=a@x.kz,b%2Btag@y.kz&subject=${encodeURIComponent(SUBJECT)}`);
    assert.equal(program.web, false);
    for (const provider of ['yandex', 'mailru', 'microsoft']) {
        assert.match(composeLink({ provider, from: 'buh@company.kz', bcc, subject: SUBJECT }).href, /^mailto:/, provider);
    }
    assert.match(composeLink({ provider: 'gmail', from: GMAIL, bcc, subject: SUBJECT, phone: true }).href, /^mailto:/,
        'на телефоне Gmail-ссылка письмо не открывает — почта телефона');
    assert.match(composeLink({ provider: 'gmail', bcc, subject: SUBJECT }).href, /^https:\/\/mail\.google\.com\/mail\/u\/0\/\?bcc=/,
        'адреса компании нет — Gmail, в котором уже вошли');
    assert.match(composeLink({ bcc, subject: SUBJECT }).href, /^mailto:\?bcc=/);
    for (const link of [gmail, program]) assert.doesNotMatch(link.href, /body=/, 'текст идёт буфером: в ссылку он не влезает');
    assert.doesNotMatch(composeLink({ from: 'buh@company.kz', bcc, subject: SUBJECT, withBcc: false }).href, /bcc=/);
    assert.match(composeLink({ from: 'x&y@company.kz', bcc: ['a?b@x.kz'], subject: 'a&b' }).href, /^mailto:x%26y@company\.kz\?bcc=a%3Fb@x\.kz&subject=a%26b$/,
        'служебные знаки адреса не ломают ссылку');
    const many = (count) => Array.from({ length: count }, (_, index) => `buh${index}@supplier-${index}.kz`);
    assert.equal(composeLink({ from: 'buh@company.kz', bcc: many(40), subject: SUBJECT }).tooLong, false);
    assert.equal(composeLink({ from: 'buh@company.kz', bcc: many(100), subject: SUBJECT }).tooLong, true,
        `почтовая программа на Windows берёт ссылку до ~${MAILTO_LIMIT} знаков`);
    assert.equal(composeLink({ provider: 'gmail', from: GMAIL, bcc: many(100), subject: SUBJECT }).tooLong, false);
    assert.equal(composeLink({ provider: 'gmail', from: GMAIL, bcc: many(200), subject: SUBJECT }).tooLong, true, `Gmail — до ${GMAIL_LIMIT}`);
});

test('адреса письма: у ждущего — отмеченные поставщики, у открытого — те, что были в «Скрытой копии»', () => {
    const candidates = [
        { id: 1, emails: ['shared@group.kz'], send: true }, { id: 2, emails: ['y@y.kz', 'shared@group.kz'], send: false },
        { id: 3, emails: ['x@x.kz'], send: true },
    ];
    assert.deepEqual(letterAddresses({ status: 'pending', candidates }), ['shared@group.kz', 'x@x.kz'], 'общий адрес — один раз');
    assert.deepEqual(letterAddresses({ status: 'pending', candidates: candidates.map((item) => ({ ...item, send: false })) }), []);
    assert.deepEqual(letterAddresses({ status: 'opened', recipients: [{ email: 'b@x.kz', status: 'opened' }, { email: 'a@x.kz', status: 'opened' },
        { email: 'c@x.kz', status: 'removed' }] }), ['a@x.kz', 'b@x.kz']);
    assert.deepEqual(letterAddresses(null), []);
});

test('телефон: Gmail в мобильном браузере письмо не открывает', () => {
    assert.equal(isPhoneDevice('Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148'), true);
    assert.equal(isPhoneDevice('Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 Chrome/130 Mobile Safari/537.36'), true);
    assert.equal(isPhoneDevice('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36'), false);
    assert.equal(isPhoneDevice('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15'), false,
        'iPad представляется Mac’ом и открывает Gmail как компьютер');
    assert.equal(isPhoneDevice(''), false);
});

test('после «Отправить письмо» — где письмо и что сделать с буфером', () => {
    assert.equal(openedToast({ copied: true, web: true }), 'Письмо открыто в Gmail. Текст скопирован — вставьте его в письмо: Ctrl+V, на Mac ⌘V.');
    assert.equal(openedToast({ copied: true, web: false }), 'Письмо открыто в почте. Текст скопирован — вставьте его в письмо: Ctrl+V, на Mac ⌘V.');
    assert.equal(openedToast({ copied: true, web: false, phone: true }), 'Письмо открыто в почте. Текст скопирован — вставьте его в письмо.');
    assert.match(openedToast({ copied: false, web: false }), /Текст скопировать не вышло — скопируйте его из окна письма\.$/);
    assert.match(openedToast({ copied: true, web: false, addressesCopied: true }), /Адреса скопированы — вставьте их в «Скрытую копию», потом нажмите «Скопировать текст»\.$/);
});

test('«Почта для писем»: один адрес или пусто', () => {
    assert.equal(lettersEmailIssue(''), '');
    assert.equal(lettersEmailIssue(undefined), '');
    assert.equal(lettersEmailIssue(' Buh@Company.KZ '), '');
    assert.equal(lettersEmailIssue('a@x.kz, b@x.kz'), 'Укажите один адрес — ту почту, с которой пишете поставщикам');
    assert.equal(lettersEmailIssue('buh@company'), '«buh@company» — не похоже на адрес почты');
});

// ── Окно справочника ─────────────────────────────────────────────────────────

const LETTER_BODY = { subject: SUBJECT, text: 'Здравствуйте!', html: '<!doctype html><html><body><p>Здравствуйте!</p></body></html>',
    paste: '<div style="color:#0f172a"><p>Здравствуйте!</p></div>' };
const PENDING = { id: 1, status: 'pending', send_after: '2026-10-20T14:35:00', changed: ['ИИК'], created_by_name: 'Иванов Иван',
    recipients: [], counts: {}, latest: true, letter: LETTER_BODY,
    candidates: [{ id: 1, name: 'ТОО «Альфа»', is_active: true, emails: ['a@alfa.kz'], send: true }] };

test('карточка письма: ждёт — «Отправить письмо» и «Не отправлять», открыто — «Открыть в почте ещё раз» у последнего', async () => {
    const { MailingCard } = await bundle('src/components/payments/RequisitesMailings.jsx', 'mailings');
    const pending = renderToStaticMarkup(h(MailingCard, { item: PENDING, sender: { name: 'ТОО «Наша»', address: 'buh@company.kz', provider: null } }));
    assert.match(pending, /data-mailing-status="pending"/);
    assert.match(pending, />Ждёт отправки</);
    assert.match(pending, /Изменилось: ИИК · Иванов Иван/);
    assert.match(pending, />Не отправлять<\/button>/);
    const send = (pending.match(/<a [^>]*data-letter-send="open"[^>]*>/) || [''])[0];
    assert.match(send, new RegExp(`href="mailto:buh@company\\.kz\\?bcc=a@alfa\\.kz&amp;subject=${encodeURIComponent(SUBJECT)}"`));
    assert.doesNotMatch(send, /target=/, 'почтовая программа открывается без новой вкладки');
    assert.match(pending, /Отправить письмо<\/a>/);
    assert.ok(pending.indexOf('>Не отправлять<') < pending.indexOf('Отправить письмо</a>'), 'главное действие — справа');
    assert.match(pending, /data-fold="letter-1"/, 'письмо раскрывается по высоте, как разделы окна');
    assert.doesNotMatch(pending, /Здравствуйте!|<iframe/, 'письмо раскрывается по запросу');
    assert.doesNotMatch(pending, /Отправить ещё раз|Попробуем снова|ждёт почту/);
    const gmail = renderToStaticMarkup(h(MailingCard, { item: PENDING, sender: { name: 'ТОО «Наша»', address: GMAIL, provider: 'gmail' } }));
    const tab = (gmail.match(/<a [^>]*data-letter-send="open"[^>]*>/) || [''])[0];
    assert.match(tab, /href="https:\/\/mail\.google\.com\/mail\/u\//);
    assert.match(tab, /target="_blank" rel="noopener noreferrer"/, 'Gmail — в новой вкладке, портал остаётся открытым');

    const opened = (latest) => renderToStaticMarkup(h(MailingCard, {
        item: { id: 2, status: 'opened', opened_at: '2026-10-20T14:40:00', latest, letter: LETTER_BODY, excluded_names: ['ТОО «Икс»'],
            recipients: [{ email: 'a@alfa.kz', counterparty_names: ['ТОО «Альфа»'], status: 'opened' }], counts: { total: 1, opened: 1 } },
        sender: { name: 'ТОО «Наша»', address: 'buh@company.kz' },
    }));
    const latest = opened(true);
    assert.match(latest, /Открыто в почте 20\.10 в 14:40 · 1 адрес|Открыто в почте в 14:40 · 1 адрес/);
    assert.match(latest, /data-letter-send="again"[^>]*>.*Открыть в почте ещё раз<\/a>/s);
    assert.doesNotMatch(latest, />Не отправлять</, 'открытое письмо уже не отменить');
    assert.doesNotMatch(latest, /data-mailing-problem/, 'адреса открытого письма — не «проблемы»');
    assert.match(latest, /data-excluded-names="true">Не отправляли: ТОО «Икс»</);
    assert.doesNotMatch(opened(false), /Открыть в почте ещё раз/, 'у старого письма — нет: у поставщика оказались бы старые реквизиты');

    const legacy = renderToStaticMarkup(h(MailingCard, {
        item: { id: 3, status: 'done', changed: ['Банк'], latest: true, recipients: [
            { email: 'nobody@alfa.kz', counterparty_names: ['ТОО «Альфа»'], status: 'failed', last_error: 'Автоотправка выключена 27.10.2026 — на этот адрес письмо не ушло' },
            { email: 'b@beta.kz', counterparty_names: ['ТОО «Бета»'], status: 'sent' },
        ], counts: { total: 2, sent: 1, failed: 1 } },
    }));
    assert.match(legacy, /Отправлено 1 из 2 адресов · не ушло: 1/);
    assert.match(legacy, /nobody@alfa\.kz · ТОО «Альфа»<\/span><span class="text-slate-500"> — Автоотправка выключена/);
    assert.doesNotMatch(legacy, /b@beta\.kz/, 'ушедшие адреса не перечисляются');
    assert.doesNotMatch(legacy, /data-letter-send/, 'письмо, которое отправлял портал, вручную не переотправляется');
});

test('кнопка «Отправить письмо»: адресов не хватает ссылке — сначала адреса, текст отдельной кнопкой', async () => {
    const { SendLetterButton } = await bundle('src/components/payments/SupplierLetters.jsx', 'letters');
    const many = Array.from({ length: 100 }, (_, index) => ({ id: index + 1, name: `П${index}`, emails: [`buh${index}@supplier-${index}.kz`], send: true }));
    const html = renderToStaticMarkup(h(SendLetterButton, { item: { ...PENDING, candidates: many }, sender: { address: 'buh@company.kz' } }));
    assert.match(html, /data-letter-copy-text="true"[^>]*>.*Скопировать текст<\/button>/s);
    const link = (html.match(/<a [^>]*>/) || [''])[0];
    assert.match(link, /href="mailto:buh@company\.kz\?subject=/, 'ссылка — без адресов: они идут буфером');
    const usual = renderToStaticMarkup(h(SendLetterButton, { item: PENDING, sender: { address: 'buh@company.kz' } }));
    assert.doesNotMatch(usual, /Скопировать текст/, 'обычно лишней кнопки нет');
    assert.match(usual, /bg-blue-600/, 'у ждущего письма — главная кнопка');
    const busy = renderToStaticMarkup(h(SendLetterButton, { item: PENDING, sender: {}, busy: true }));
    assert.match(busy, /aria-disabled="true"/);
    assert.match(busy, /pointer-events-none/);
    const source_ = source('src/components/payments/SupplierLetters.jsx');
    // Щелчок: текст — в буфер с оформлением, письмо — «открыто в почте» с адресами из «Скрытой копии».
    assert.match(source_, /copyRich\(\{ html: item\.letter\?\.paste, text: item\.letter\?\.text \}\)/);
    assert.match(source_, /if \(pending\) onOpened\?\.\(bcc\);/);
    assert.match(source_, /onOpened=\{\(addresses\) => act\('open', \{ addresses \}\)\}/, 'окно письма отмечает открытие на сервере');
    assert.match(source_, /Письмо некому отправить: отметьте поставщиков в «Кому»/, 'кнопка не серая — говорит, чего не хватает');
    const ui = source('src/components/payments/paymentsUi.jsx');
    assert.match(ui, /event\.clipboardData\.setData\('text\/html', html\)/);
    assert.match(ui, /document\.addEventListener\('beforecopy', before, true\)/, 'Safari без выделения иначе не пускает copy');
    assert.match(ui, /new ClipboardItem\(\{/);
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
    const row = { id: 1, name: 'ТОО «Наша»', requisites_text: 'реквизиты', is_active: true };
    assert.match(renderToStaticMarkup(state.cell({ ...row, mailing: { status: 'pending' } })), /письмо не отправлено/);
    for (const status of ['opened', 'done']) {
        assert.doesNotMatch(renderToStaticMarkup(state.cell({ ...row, mailing: { status, failed: 1 } })), /письмо/, status);
    }

    const [name] = dictionaryColumns('counterparties');
    assert.equal(name.cell({ name: 'ТОО «Ромашка»', kind: 'too', bin: '123456789012', vat_payer: true, emails: ['buh@romashka.kz', 'info@romashka.kz'] }).sub,
        'ТОО · БИН 123456789012 · с НДС · buh@romashka.kz +1');
});

test('окно справочника: «Почта» у поставщика, «Почта для писем» и письма — у компании, письмо — сразу после сохранения', () => {
    const dict = source('src/components/payments/PaymentsDictionaries.jsx');
    assert.match(dict, /<Field label="Почта" hint=\{HINTS\.supplierEmails\} anchor="supplier-emails" issue=\{emailsTried \? \(emailsIssue\(draft\.emails\) \|\| emailsRefused\) : ''\}>/);
    assert.match(dict, /emails: \(row\.emails \|\| \[\]\)\.join\(', '\)/, 'в окне адреса через запятую');
    assert.match(dict, /placeholder: 'Имя, телефон' \}/, 'почта больше не пишется в «Контакт»');
    assert.match(dict, /<Field label="Почта для писем" hint=\{HINTS\.lettersEmail\} anchor="letters-email"\s+issue=\{lettersTried \? \(lettersEmailIssue\(draft\.letters_email\) \|\| lettersRefused\) : ''\}>/);
    assert.match(dict, /letters_email: row\.letters_email \|\| '',/);
    assert.match(dict, /\{editing && !isNew && canEdit && name === 'legal_entities' && \(\n\s+<RequisitesMailings /);
    assert.doesNotMatch(dict, /CompanyMailbox|mailboxControl|mailboxReady/, 'блока с паролем почты больше нет');
    const start = dict.indexOf('const save = async (event) => {', dict.indexOf('const savedText = '));
    const save = dict.slice(start, dict.indexOf('const remove = async () => {', start));
    // Неверный адрес не уходит на сервер: поле краснеет и окно подводит к нему.
    assert.ok(save.includes('emailsIssue(draft.emails)') && save.indexOf('emailsIssue(draft.emails)') < save.indexOf('axios.post'));
    assert.ok(save.indexOf('lettersEmailIssue(draft.letters_email)') !== -1 && save.indexOf('lettersEmailIssue(draft.letters_email)') < save.indexOf('axios.post'));
    assert.match(dict, /revealAnchor\(\s*document\.querySelector\('\[data-issue-anchor="supplier-emails"\]'\), \{ focus: fromKeyboard\(event\) \}\)/);
    // Отказ сервера в адресе — красным у поля, а не строкой сверху окна.
    assert.match(save, /err\?\.response\?\.data\?\.field === 'emails'/);
    assert.match(save, /err\?\.response\?\.data\?\.field === 'letters_email'/);
    // Новые реквизиты — следом окно письма с «Отправить письмо».
    assert.match(save, /toastRef\.current\?\.\(savedText\(mailing\), 'success'\)/);
    assert.match(save, /if \(name === 'legal_entities' && mailing\?\.status === 'pending' && mailing\.recipients\?\.addresses\) setLetterId\(mailing\.id\);/);
    assert.match(dict, /<LetterWindow apiBaseUrl=\{apiBaseUrl\} headers=\{headers\} mailingId=\{letterId\} onClose=\{\(\) => setLetterId\(null\)\}/);
    assert.match(dict, /'Сохранено\. Письмо поставщикам готово — отправьте его кнопкой «Отправить письмо»'/);
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
    assert.equal(letterMoment({ status: 'pending', send_after: 'a', started_at: 'b' }), 'a', 'ждёт — с какой правки готово');
    assert.equal(letterMoment({ status: 'opened', opened_at: 'o', updated_at: 'c' }), 'o', 'открыто — когда открыли в почте');
    assert.equal(letterMoment({ status: 'done', started_at: 'b', updated_at: 'c' }), 'b', 'ушло — когда ушло');
    assert.equal(letterMoment({ status: 'cancelled', cancelled_at: 'd', started_at: 'b' }), 'd');
    assert.deepEqual(recipientState({ status: 'opened', sent_at: '2026-10-20T16:43:10' }), { tone: 'neutral', label: 'в письме' });
    assert.deepEqual(recipientState({ status: 'sent', sent_at: '2026-10-20T16:43:10' }), { tone: 'done', label: 'ушло в 16:43' });
    assert.deepEqual(recipientState({ status: 'failed', next_attempt_at: '2026-10-20T16:48:00' }), { tone: 'overdue', label: 'не ушло' },
        'повторов больше нет');
    assert.deepEqual(recipientState({ status: 'removed' }), { tone: 'neutral', label: 'адрес убран' });
    assert.deepEqual(recipientState({ status: 'superseded' }), { tone: 'neutral', label: 'новым письмом' });
});

const LETTER = {
    id: 5, legal_entity_id: 2, legal_entity_name: 'ИП Иванов', status: 'opened', opened_at: '2026-10-20T16:43:00', latest: true,
    letter: LETTER_BODY,
    recipients: [
        { email: 'c@gamma.kz', counterparty_names: ['ТОО «Гамма»'], status: 'opened', sent_at: '2026-10-20T16:43:00' },
        { email: 'b@beta.kz', counterparty_names: ['ТОО «Бета»', 'ИП Бетов'], status: 'opened', sent_at: '2026-10-20T16:43:00' },
        { email: 'a@alfa.kz', counterparty_names: ['ТОО «Альфа»'], status: 'opened', sent_at: '2026-10-20T16:43:00' },
    ],
    counts: { total: 3, opened: 3 },
};

test('окно письма: шапка как в «Почте» и само письмо во фрейме без скриптов', async () => {
    const { LetterView } = await bundle('src/components/payments/SupplierLetters.jsx', 'letters');
    const html = renderToStaticMarkup(h(LetterView, { item: LETTER, sender: { name: 'ИП Иванов', address: 'buh@company.kz' } }));
    assert.match(html, /data-letter-view/);
    assert.match(html, /rounded-full bg-gradient-to-b from-slate-300 to-slate-400[^>]*>И<\/span>/, 'кружок с буквами компании');
    assert.match(html, />ИП Иванов<\/span>/);
    assert.match(html, />buh@company\.kz<\/div>/);
    assert.match(html, /Кому: <\/span>a@alfa\.kz, b@beta\.kz и ещё 1/, 'адресаты по алфавиту, как в «Почте»');
    assert.match(html, new RegExp(`>${SUBJECT}</div>`));
    assert.match(html, /20\.10\.2026 16:43/);
    const frame = (html.match(/<iframe[^>]*>/) || [''])[0];
    assert.match(frame, /sandbox="allow-same-origin"/, 'без allow-scripts: скрипты в письме не выполнятся');
    assert.doesNotMatch(frame, /allow-scripts/);
    assert.match(frame, /srcDoc="&lt;!doctype html&gt;/i);
    const bare = renderToStaticMarkup(h(LetterView, { item: { ...PENDING, legal_entity_name: 'ИП Иванов' }, sender: { name: 'ИП Иванов', address: null } }));
    assert.doesNotMatch(bare, /почта для писем не подключена/, 'без адреса строки отправителя просто нет');
    assert.match(bare, /Кому: <\/span>1 поставщик · 1 адрес/, 'ждущее письмо — кому по галочкам');
    const cancelled = renderToStaticMarkup(h(LetterView, { item: { ...LETTER, status: 'cancelled', recipients: [] }, sender: { name: 'ИП' } }));
    assert.match(cancelled, /Кому: <\/span>—/);
});

test('строка журнала: красным — только то, что не ушло; ряды не переносятся на телефоне', async () => {
    const { LetterRow } = await bundle('src/components/payments/SupplierLetters.jsx', 'letters');
    const opened = renderToStaticMarkup(h(LetterRow, { item: { ...LETTER, changed: ['ИИК', 'Банк'], updated_by_name: 'Иванов Иван' }, onOpen: () => {} }));
    assert.match(opened, /<span>Открыто в почте (в 16:43|20\.10 в 16:43) · 3 адреса<\/span> · Иванов Иван/);
    assert.match(opened, /Изменилось: ИИК, банк/);
    const legacy = renderToStaticMarkup(h(LetterRow, { item: { id: 5, status: 'done', legal_entity_name: 'ИП Иванов', started_at: '2026-10-18T16:43:00',
        changed: ['ИИК'], updated_by_name: 'Иванов Иван', counts: { total: 8, sent: 7, failed: 1 } }, onOpen: () => {} }));
    assert.match(legacy, /<span class="text-rose-700">Отправлено 7 из 8 адресов · не ушло: 1<\/span> · Иванов Иван/);
    assert.match(legacy, />18\.10<\/span>/);
    assert.match(legacy, /min-w-0 grow basis-0/, 'basis-0: оболочка телефона иначе уносит текст под кружок');
});

test('«Кому»: по адресу — поставщики и что с адресом', async () => {
    const { RecipientList } = await bundle('src/components/payments/SupplierLetters.jsx', 'letters');
    const html = renderToStaticMarkup(h(RecipientList, { item: LETTER }));
    assert.equal((html.match(/data-recipient="opened"/g) || []).length, 3);
    assert.match(html, /ТОО «Бета», ИП Бетов/);
    assert.equal((html.match(/в письме<\/span>/g) || []).length, 3);
    const legacy = renderToStaticMarkup(h(RecipientList, { item: { recipients: [
        { email: 'nobody@alfa.kz', counterparty_names: ['ТОО «Альфа»'], status: 'failed', last_error: 'Сервер не принял адрес: 550 No such user' },
        { email: 'b@beta.kz', counterparty_names: ['ТОО «Бета»'], status: 'sent', sent_at: '2026-10-20T16:43:05' }] } }));
    assert.match(legacy, /nobody@alfa\.kz<\/div><div[^>]*>ТОО «Альфа»<\/div><div[^>]*>Сервер не принял адрес: 550 No such user<\/div>/);
    assert.match(legacy, /не ушло<\/span>/);
    assert.match(legacy, /ушло в 16:43<\/span>/);
    assert.match(renderToStaticMarkup(h(RecipientList, { item: { status: 'cancelled', recipients: [] } })), /Письмо отменили — оно никому не ушло\./);
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

// ── «Почта поставщиков» ──────────────────────────────────────────────────────

test('«Почта поставщиков» и журнал: подписи словами', async () => {
    const { addressLetterState, excludedLine } = await import('../src/components/payments/paymentsMeta.js');
    assert.deepEqual(addressLetterState(null), { tone: 'neutral', label: 'писем не было' });
    assert.deepEqual(addressLetterState({ status: 'opened', sent_at: '2026-10-20T16:43:00' }, TODAY), { tone: 'done', label: 'в письме в 16:43' });
    assert.deepEqual(addressLetterState({ status: 'opened', sent_at: '2026-10-18T16:43:00' }, TODAY), { tone: 'done', label: 'в письме 18.10 в 16:43' });
    assert.deepEqual(addressLetterState({ status: 'sent', sent_at: '2026-10-20T16:43:00' }, TODAY), { tone: 'done', label: 'ушло в 16:43' });
    assert.deepEqual(addressLetterState({ status: 'failed', next_attempt_at: '2026-10-20T16:48:00' }, TODAY), { tone: 'overdue', label: 'не дошло' });
    assert.equal(excludedLine(['ТОО «Икс»', 'ТОО «Игрек»']), 'Не отправляли: ТОО «Икс», ТОО «Игрек»');
    assert.equal(excludedLine([]), '');
    const meta = source('src/components/payments/paymentsMeta.js');
    for (const gone of ['mailboxLine', 'serverLine', 'waitingMailboxLine', 'minutesWord', 'almatyNow']) {
        assert.doesNotMatch(meta, new RegExp(`export const ${gone}\\b`), `${gone}: автоотправки и подключения почты больше нет`);
    }
    assert.equal(existsSync(new URL('src/components/payments/CompanyMailbox.jsx', ROOT)), false);
});

test('«Почта поставщиков»: колонки, проверка строки, место в меню', async () => {
    const { dictionaryColumns, dictionarySections, emailRowIssues } = await bundle('src/components/payments/PaymentsDictionaries.jsx', 'dictionaries');
    const columns = dictionaryColumns('counterparty_emails');
    assert.deepEqual(columns.map((column) => column.key), ['email', 'party', 'letter']);
    const row = { id: 1, email: 'buh@supplier.kz', contact_name: 'Айгерим, бухгалтер', counterparty_name: 'ТОО «Поставщик»',
        counterparty_active: false, note: 'с 2025', last_letter: { status: 'opened', sent_at: '2026-10-18T16:43:00' } };
    assert.deepEqual(columns[0].cell(row), { main: 'buh@supplier.kz', strong: true, sub: 'Айгерим, бухгалтер' });
    assert.deepEqual(columns[1].cell(row), { main: 'ТОО «Поставщик»', sub: 'поставщик скрыт · с 2025' });
    assert.match(renderToStaticMarkup(columns[2].cell(row)), /в письме 18\.10 в 16:43/);
    assert.match(renderToStaticMarkup(columns[2].cell({ ...row, last_letter: null })), /писем не было/);
    assert.deepEqual(emailRowIssues({}), { counterparty_id: 'Выберите поставщика, чей это адрес', email: 'Укажите адрес почты' });
    assert.deepEqual(emailRowIssues({ counterparty_id: 7, email: ' Buh@Supplier.KZ ' }), {});
    assert.deepEqual(emailRowIssues({ counterparty_id: 7, email: 'a@x.kz, b@x.kz' }), { email: 'Один адрес — одна строка' });
    assert.deepEqual(emailRowIssues({ counterparty_id: 7, email: 'buh@supplier' }), { email: '«buh@supplier» — не похоже на адрес почты' });
    const section = dictionarySections({ is_admin: false }).find((item) => item.value === 'counterparty_emails');
    assert.equal(section.label, 'Почта поставщиков', 'справочник читает любой участник, как и поставщиков');
});

test('карточка ждущего письма: «Кому» с галочками; у открытого — кого не отправляли', async () => {
    const { MailingCard } = await bundle('src/components/payments/RequisitesMailings.jsx', 'mailings');
    const pending = renderToStaticMarkup(h(MailingCard, {
        item: { ...PENDING, candidates: CANDIDATES }, sender: {}, onCancel: () => {}, onOpen: () => {}, onPick: () => {},
    }));
    assert.match(pending, /data-mailing-recipients/);
    assert.match(pending, />Кому</);
    assert.match(pending, /2 из 3 поставщиков · 2 адреса/);
    const opened = renderToStaticMarkup(h(MailingCard, {
        item: { id: 2, status: 'opened', counts: { total: 1, opened: 1 }, recipients: [], excluded_names: ['ТОО «Игрек»'] }, sender: {},
    }));
    assert.match(opened, /data-excluded-names="true">Не отправляли: ТОО «Игрек»</);
    assert.doesNotMatch(opened, /data-mailing-recipients/, 'открытому письму галочки не нужны');
});
