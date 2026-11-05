/*
 * «Забыл пароль?» на экране входа (05.11.2026): окно с двумя контактами в
 * Telegram — Руслан и Шерзад восстанавливают доступ вручную.
 *
 * Что сторожим:
 * - ссылки ведут ровно на те два ника, что дал владелец, и подставляют в поле
 *   сообщения текст с логином из формы (иначе помогающий переспрашивает);
 * - окно — настоящий компонент поверх IosModal: заголовок, ссылки в новой
 *   вкладке без opener, фото с запасной буквой;
 * - кнопка на экране входа открывает окно, а окно смонтировано всегда и
 *   закрывается через open (снятое условием окно не уходит анимацией).
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { buildSync } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import {
    PASSWORD_HELP_CONTACTS,
    passwordHelpDraft,
    telegramChatUrl,
    telegramUserpicUrl,
} from '../src/components/common/passwordHelp.js';

const cache = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
mkdirSync(cache, { recursive: true });
const outfile = join(cache, 'ForgotPasswordWindow.cjs');
buildSync({
    entryPoints: ['src/components/common/ForgotPasswordWindow.jsx'], outfile,
    bundle: true, platform: 'node', format: 'cjs', packages: 'external', logLevel: 'silent',
});
const require = createRequire(import.meta.url);
const ForgotPasswordWindow = require(outfile).default;

const render = (props) => renderToStaticMarkup(
    React.createElement(ForgotPasswordWindow, { onClose: () => {}, ...props }));

/* react-dom/server экранирует & в атрибутах. */
const attr = (value) => value.replaceAll('&', '&amp;');

test('контакты — ровно те два ника, что дал владелец', () => {
    assert.deepEqual(
        PASSWORD_HELP_CONTACTS.map(({ username, name }) => [username, name]),
        [['nalsuur', 'Руслан'], ['sherzAdh', 'Шерзад']],
    );
});

test('текст сообщения: логин из формы, без него — заготовка «ФИО:»', () => {
    assert.equal(passwordHelpDraft('ivanov'), 'Здравствуйте! Не помню пароль от iCORE. Логин: ivanov');
    assert.equal(passwordHelpDraft('  ivan   petrov \n'), 'Здравствуйте! Не помню пароль от iCORE. Логин: ivan petrov');
    for (const empty of ['', '   ', undefined, null]) {
        assert.equal(passwordHelpDraft(empty), 'Здравствуйте! Не помню пароль от iCORE. ФИО: ');
    }
    const long = passwordHelpDraft('x'.repeat(500));
    assert.ok(long.endsWith(`Логин: ${'x'.repeat(64)}`), 'логин обрезан до 64 знаков');
});

test('ссылка на чат кодирует текст, без текста — голый адрес', () => {
    assert.equal(telegramChatUrl('nalsuur'), 'https://t.me/nalsuur');
    assert.equal(
        telegramChatUrl('sherzAdh', 'Логин: a&b c'),
        'https://t.me/sherzAdh?text=%D0%9B%D0%BE%D0%B3%D0%B8%D0%BD%3A%20a%26b%20c',
    );
    assert.equal(telegramUserpicUrl('nalsuur'), 'https://t.me/i/userpic/160/nalsuur.jpg');
});

test('открытое окно: заголовок, обе ссылки с логином, новая вкладка без opener', () => {
    const html = render({ open: true, login: 'ivanov' });
    assert.match(html, /Забыли пароль\?/);
    assert.match(html, /Напишите в Telegram Руслану или Шерзаду/);
    const draft = passwordHelpDraft('ivanov');
    for (const contact of PASSWORD_HELP_CONTACTS) {
        const href = attr(telegramChatUrl(contact.username, draft));
        const link = html.match(new RegExp(`<a [^>]*href="${href.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}"[^>]*>`));
        assert.ok(link, `ссылка на ${contact.username} с текстом сообщения`);
        assert.match(link[0], /target="_blank"/);
        assert.match(link[0], /rel="noopener noreferrer"/);
        assert.match(link[0], new RegExp(`aria-label="Написать ${contact.dative} в Telegram"`));
        assert.match(html, new RegExp(`>@${contact.username}<`));
        // Фото — из Telegram, без адреса портала в Referer; под ним буква имени.
        assert.match(html, new RegExp(`<img[^>]*src="${telegramUserpicUrl(contact.username).replace(/[.?]/g, '\\$&')}"[^>]*referrerPolicy="no-referrer"|<img[^>]*referrerPolicy="no-referrer"[^>]*src="${telegramUserpicUrl(contact.username).replace(/[.?]/g, '\\$&')}"`, 'i'));
        assert.match(html, new RegExp(`>${contact.name.charAt(0)}<`));
    }
});

test('закрытое окно ничего не рисует', () => {
    assert.equal(render({ open: false, login: 'ivanov' }), '');
});

test('кнопка «Забыл пароль?» открывает окно, окно смонтировано всегда', () => {
    const app = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8');
    assert.doesNotMatch(app, /Forgot password clicked/);
    const button = app.match(/<button[^>]*?onClick=\{\(\) => ([^}]*)\}[^>]*>\s*Забыл пароль\?\s*<\/button>/);
    assert.ok(button, 'кнопка «Забыл пароль?» найдена');
    assert.equal(button[1].trim(), 'setForgotPasswordOpen(true)');
    assert.match(app, /<ForgotPasswordWindow\s+open=\{forgotPasswordOpen\}\s+onClose=\{\(\) => setForgotPasswordOpen\(false\)\}\s+login=\{login\}\s*\/>/);
    assert.doesNotMatch(app, /forgotPasswordOpen\s*&&\s*\(?\s*<ForgotPasswordWindow/);
});
