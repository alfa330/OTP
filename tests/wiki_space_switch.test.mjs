/*
 * Переключатель пространств: как строка узнаётся в списке и что стоит в шапке.
 *
 * Правила отбора и значка живут в отдельном модуле (spaceIdentity.js) именно
 * ради этого теста: ошибка здесь молчаливая — человек не находит свою вику в
 * списке и решает, что доступ отобрали, а сборка при этом зелёная.
 *
 * Сам компонент отрисовывается через react-dom/server: браузерного окружения в
 * проекте нет (ни jsdom, ни puppeteer), а серверный рендер закрывает главное —
 * что стоит в кнопке шапки и при каких условиях она вообще кнопка. Раскрытое
 * меню сюда не попадает: оно живёт в портале (createPortal → document.body),
 * а document на сервере отсутствует.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

import {
    SPACE_SEARCH_THRESHOLD, filterSpaces, matchesSpaceQuery, spaceIcon, spaceMonogram,
} from '../src/components/wiki/spaceIdentity.js';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { transformSync } = require('esbuild');
const { readFileSync, writeFileSync, mkdirSync } = require('node:fs');
const { join } = require('node:path');

// ── Значок ──────────────────────────────────────────────────────────────────

test('значком считается только emoji, а не имя иконки из старой схемы', () => {
    // Колонка wiki_spaces.icon — VARCHAR(64) из первой версии структуры, где
    // предполагались имена вроде 'book'. Отрисованное как текст, такое имя
    // выглядит в списке мусором в квадратике.
    assert.equal(spaceIcon({ icon: '🚕' }), '🚕');
    assert.equal(spaceIcon({ icon: 'book' }), '');
    assert.equal(spaceIcon({ icon: 'парк' }), '');
    assert.equal(spaceIcon({ icon: '   ' }), '');
    assert.equal(spaceIcon({}), '');
    assert.equal(spaceIcon(null), '');
});

test('монограмма: два слова — две буквы, одно слово — одна', () => {
    // Две первые буквы одного слова («ТА» от «Таксопарки») читаются как чужая
    // аббревиатура, поэтому односложное имя сокращается до одной буквы.
    assert.equal(spaceMonogram({ name: 'Тез КЦ' }), 'ТК');
    assert.equal(spaceMonogram({ name: 'Таксопарки' }), 'Т');
    assert.equal(spaceMonogram({ name: 'Отдел — продаж' }), 'ОП');
    assert.equal(spaceMonogram({ name: '' }), '#');
    assert.equal(spaceMonogram(undefined), '#');
});

// ── Поиск ───────────────────────────────────────────────────────────────────

test('ищем и по названию, и по коду, и по описанию', () => {
    // Код виден в адресах и выгрузках: человек, пришедший из письма, набирает
    // именно его.
    const space = { id: 1, name: 'Тез КЦ', code: 'tez', description: 'Контакт-центр' };
    assert.equal(matchesSpaceQuery(space, 'тез'), true);
    assert.equal(matchesSpaceQuery(space, 'ТЕЗ'), true);
    assert.equal(matchesSpaceQuery(space, ' tez '), true);
    assert.equal(matchesSpaceQuery(space, 'контакт'), true);
    assert.equal(matchesSpaceQuery(space, 'igroup'), false);
});

test('пустой запрос ничего не отсекает', () => {
    // Поле поиска пусто ровно в момент открытия меню — список обязан быть целым.
    const spaces = [{ id: 1, name: 'A' }, { id: 2, name: 'B' }];
    assert.deepEqual(filterSpaces(spaces, ''), spaces);
    assert.deepEqual(filterSpaces(spaces, '   '), spaces);
    assert.deepEqual(filterSpaces(spaces, undefined), spaces);
});

test('отбор сохраняет порядок сервера', () => {
    // Порядок задаёт wiki_spaces.position, и переставлять его на клиенте нельзя:
    // список в меню обязан совпадать с тем, что видно в конструкторе.
    const spaces = [
        { id: 3, name: 'Тез ОП' }, { id: 1, name: 'Тез КЦ' }, { id: 2, name: 'iGroup' },
    ];
    assert.deepEqual(filterSpaces(spaces, 'тез').map((s) => s.id), [3, 1]);
});

test('порог поиска — восемь: до него список читается глазами', () => {
    assert.equal(SPACE_SEARCH_THRESHOLD, 8);
});

// ── Кнопка в шапке ──────────────────────────────────────────────────────────

async function loadSwitch() {
    const source = new URL('../src/components/wiki/WikiSpaceSwitch.jsx', import.meta.url);
    const { code } = transformSync(readFileSync(source, 'utf8'),
                                   { loader: 'jsx', format: 'esm', target: 'node18' });
    /* Подменяем соседей, чтобы не тащить цепочку зависимостей UI-кита и axios:
       проверяем разметку кнопки, а не сеть. Кавычки после esbuild двойные. */
    const patched = code
        .replace(/import\s*\{[^}]*\}\s*from\s*["']\.\.\/ui\/ios["'];?/,
                 'const APPLE_FONT = "system-ui";')
        .replace(/import\s+axios\s+from\s*["']axios["'];?/, 'const axios = { get() {}, patch() {} };')
        .replace(/import\s*\{([^}]*)\}\s*from\s*["']lucide-react["'];?/,
                 (_all, names) => names.split(',').map((raw) => {
                     const name = raw.trim();
                     return `const ${name} = () => null;`;
                 }).join('\n'))
        .replace(/from\s*["']\.\/spaceIdentity["']/, 'from "../../../src/components/wiki/spaceIdentity.js"');
    /* Собранный модуль кладём ВНУТРЬ проекта: из системной временной папки
       `import 'react'` не разрешается — node ищет node_modules вверх от файла. */
    const dir = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
    mkdirSync(dir, { recursive: true });
    const file = join(dir, 'WikiSpaceSwitch.mjs');
    writeFileSync(file, patched, 'utf8');
    return (await import(`file://${file.replace(/\\/g, '/')}?t=${Date.now()}`)).default;
}

const WikiSpaceSwitch = await loadSwitch();

const render = (props) => renderToStaticMarkup(React.createElement(WikiSpaceSwitch, props));

test('в кнопке — имя выбранного пространства и его значок', () => {
    const html = render({
        spaces: [{ id: 1, name: 'Таксопарки', icon: '🚕' }, { id: 2, name: 'Тез КЦ' }],
        value: 2,
    });
    assert.ok(html.includes('Тез КЦ'), 'в кнопке должно стоять выбранное пространство');
    assert.ok(!html.includes('Таксопарки'), 'закрытое меню не должно рисовать список');
    assert.ok(html.includes('ТК'), 'без emoji плитка показывает монограмму');
});

test('пространство одно и настраивать нечем — это подпись, а не кнопка', () => {
    // Кнопка, которая ничего не делает, врёт о своих намерениях: выбирать не из
    // чего, а «+» и шестерёнка супер-админа этому человеку не полагаются.
    const html = render({ spaces: [{ id: 1, name: 'iGroup' }], value: 1 });
    assert.ok(html.includes('disabled'), 'единственное пространство без прав — не кнопка');
});

test('пространство одно, но права есть — дверь к созданию остаётся', () => {
    // Первое пространство заводят из этого же меню; закрыв его при списке из
    // одной строки, супер-админ остался бы без единственного входа.
    const html = render({ spaces: [{ id: 1, name: 'iGroup' }], value: 1, canManage: true });
    assert.ok(!html.includes('disabled'), 'супер-админу кнопка нужна открытой');
});

test('нет ни одного пространства — шапка не рисует пустую плитку', () => {
    assert.equal(render({ spaces: [], value: null }), '');
});
