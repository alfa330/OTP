/*
 * Табло СЗоВ «Чат», каталог показателей виджета: «Занят» Syntony у чатников — «Закрытие чатов»
 * (решение владельца 27.11.2026). У Syntony свой показатель «Закрытие чатов» (счётчик
 * operators_on_holiday), а «Занят» остаётся только у Chat2Desk: у Syntony отдельного «Занят» нет,
 * и сохранённый в виджете показатель иначе стоял бы прочерком. Модуль собирается esbuild'ом и
 * исполняется настоящим node — проверяется поведение, а не строчки исходника.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url);
const esbuild = require('esbuild');

const ROOT = new URL('../', import.meta.url);
// Сборка кладётся ВНУТРЬ node_modules: из системного temp внешний react не резолвится.
const OUT_DIR = join(new URL('../node_modules/.cache/otp-szov-chat-closing-test/', import.meta.url)
    .pathname.replace(/^\/([A-Za-z]:)/, '$1'));
mkdirSync(OUT_DIR, { recursive: true });

async function bundle() {
    const result = await esbuild.build({
        entryPoints: [new URL('src/components/monitoring/szovWallboardShared.js', ROOT).pathname
            .replace(/^\/([A-Za-z]:)/, '$1')],
        bundle: true,
        write: false,
        format: 'esm',
        platform: 'node',
        target: 'node18',
        loader: { '.js': 'jsx' },
        external: ['react', 'react-dom'],
        logLevel: 'silent',
    });
    const outPath = join(OUT_DIR, 'szov-wallboard-shared.mjs');
    writeFileSync(outPath, result.outputFiles[0].text);
    return import(pathToFileURL(outPath).href + `?t=${Date.now()}`);
}

const {
    CHAT_WALLBOARD_METRIC_MAP, WALLBOARD_DIRECTIONS, WIDGET_METRICS_VERSION, chatMetricAvailable,
    readWallboardMetric, restoreWidgetMetrics,
} = await bundle();

const SYNTONY = { source: 'syntony', statuses: true, now: { operators_busy: null, operators_on_holiday: 3 } };
const SYNTONY_NO_STATUSES = { source: 'syntony', statuses: false, now: { operators_on_holiday: null } };
const CHAT2DESK = { now: { operators_busy: 2, operators_on_holiday: 1 } };

test('«Закрытие чатов» — показатель Syntony со статусами, «Занят» — только Chat2Desk', () => {
    const closing = CHAT_WALLBOARD_METRIC_MAP.chat_closing;
    const busy = CHAT_WALLBOARD_METRIC_MAP.chat_busy;
    assert.equal(closing.label, 'Закрытие чатов');
    assert.equal(chatMetricAvailable(closing, SYNTONY), true);
    assert.equal(chatMetricAvailable(closing, SYNTONY_NO_STATUSES), false);
    assert.equal(chatMetricAvailable(closing, CHAT2DESK), false);
    assert.equal(chatMetricAvailable(busy, SYNTONY), false);
    assert.equal(chatMetricAvailable(busy, CHAT2DESK), true);
});

test('«Закрытие чатов» считает людей на закрытии чатов из снимка', () => {
    const closing = CHAT_WALLBOARD_METRIC_MAP.chat_closing;
    assert.equal(readWallboardMetric(closing, SYNTONY).value, '3');
    assert.equal(readWallboardMetric(CHAT_WALLBOARD_METRIC_MAP.chat_busy, CHAT2DESK).value, '2');
});

test('набор виджета до 27.11: к «Занят» добавляется «Закрытие чатов», снятое потом не возвращается', () => {
    const chat = WALLBOARD_DIRECTIONS.chat;
    // Сохранён до правила (версии нет): «Закрытие чатов» встаёт рядом с «Занят».
    assert.deepEqual(restoreWidgetMetrics({ metrics: ['chat_online', 'chat_busy', 'chat_first'] }, chat),
        ['chat_online', 'chat_busy', 'chat_closing', 'chat_first']);
    // Без «Занят» — набор как был.
    assert.deepEqual(restoreWidgetMetrics({ metrics: ['chat_online', 'chat_first'] }, chat),
        ['chat_online', 'chat_first']);
    // Человек после правила сам снял «Закрытие чатов» — набор записан с версией и не меняется.
    assert.deepEqual(restoreWidgetMetrics({ metrics: ['chat_online', 'chat_busy'], version: WIDGET_METRICS_VERSION }, chat),
        ['chat_online', 'chat_busy']);
    // У линии своих переименований нет: набор только чистится от незнакомых ключей.
    assert.deepEqual(restoreWidgetMetrics({ metrics: ['queue', 'chat_busy', 'нет такого'] }, WALLBOARD_DIRECTIONS.osnova),
        ['queue']);
    assert.deepEqual(restoreWidgetMetrics(null, chat), []);
});
