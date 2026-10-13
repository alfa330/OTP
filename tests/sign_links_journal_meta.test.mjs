// Журнал «Ссылки на подписание»: запросы внешнего сервиса (вход по ключу).
//
// Сервис пишется в тот же журнал, что и сотрудники, но сотрудником не является:
// у его строки нет ни имени человека, ни роли, ни отдела. Здесь сторожится, что
// строка «кто» читается из записи сервера и что отбор «Источник» называет те
// же значения, что понимает сервер (sign_links/queries.py).
//
// Запуск: node --test tests/sign_links_journal_meta.test.mjs

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    API_ROLE, SOURCE_LABELS, SOURCE_ORDER, apiClient, isApiRow,
} from '../src/components/sign_links/signLinkMeta.js';

const queries = readFileSync(new URL('../sign_links/queries.py', import.meta.url), 'utf8');

test('строку сервиса отличает роль, а не пустой сотрудник', () => {
    assert.equal(isApiRow({ user_role: 'api', user_id: null }), true);
    // У строки уволенного сотрудника user_id тоже пуст — но это человек.
    assert.equal(isApiRow({ user_role: 'operator', user_id: null }), false);
    assert.equal(isApiRow({}), false);
    assert.equal(isApiRow(null), false);
});

test('запись сервиса расходится на имя и отпечаток ключа', () => {
    assert.deepEqual(apiClient({ user_name: 'Внешний сервис (ключ 6f899aa2)' }),
        { name: 'Внешний сервис', note: 'ключ 6f899aa2' });
});

test('формат записи — тот же, что пишет сервер', () => {
    const format = /API_CLIENT_NAME = '([^']+)'/.exec(queries);
    assert.ok(format, 'в sign_links/queries.py нет API_CLIENT_NAME');
    const written = format[1].replace('%s', '0a1b2c3d');
    const who = apiClient({ user_name: written });
    assert.equal(who.name, SOURCE_LABELS.api);
    assert.equal(who.note, 'ключ 0a1b2c3d');
});

test('запись другого вида показывается как есть, без потери слов', () => {
    assert.deepEqual(apiClient({ user_name: 'Партнёрский бот' }),
        { name: 'Партнёрский бот', note: 'вход по ключу' });
    assert.deepEqual(apiClient({ user_name: '' }),
        { name: SOURCE_LABELS.api, note: 'вход по ключу' });
    assert.deepEqual(apiClient(null), { name: SOURCE_LABELS.api, note: 'вход по ключу' });
});

test('отбор «Источник» называет значения, которые понимает сервер', () => {
    assert.deepEqual(SOURCE_ORDER, ['all', 'staff', 'api']);
    for (const code of SOURCE_ORDER) {
        assert.match(SOURCE_LABELS[code], /[А-Яа-яЁё]/, code);
    }
    // «Все» параметром не уходит; остальные два — ровно queries.SOURCES.
    assert.ok(queries.includes("SOURCE_STAFF = 'staff'"));
    assert.ok(queries.includes("SOURCE_API = 'api'"));
    assert.ok(queries.includes(`API_ROLE = '${API_ROLE}'`));
});
