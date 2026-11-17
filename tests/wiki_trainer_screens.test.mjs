/* Экраны тренажёров против их сценариев.
 *
 * Движок (runner) проверяется отдельно; здесь — разметка. Карта экранов обязана
 * покрывать каждый шаг каждого тренажёра, а в уроке «Такси.Про» на экране каждого
 * шага должна быть подсвечена ровно кнопка этого шага и стоять все кнопки, про
 * которые говорят ловушки шага. Перевёрнутая вкладка, переименованный крестик
 * окна или экран, выпавший из карты, раньше ломали урок молча: тесты движка
 * зелёные, а человек стоит перед телефоном без кнопки, которую велит нажать барс.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { buildSync } from 'esbuild';

import { TRAINERS } from '../src/components/wiki/trainers/registry.js';
import taxiPro from '../src/components/wiki/trainers/scenarioTaxiPro.js';
import {
    currentStep, expectedTap, startRun, tap, toggle,
} from '../src/components/wiki/trainers/runner.js';

async function loadScreens() {
    const dir = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
    mkdirSync(dir, { recursive: true });
    const outfile = join(dir, 'wiki-trainer-screen-map.mjs');
    buildSync({
        entryPoints: [fileURLToPath(new URL('../src/components/wiki/trainers/screenMap.js', import.meta.url))],
        bundle: true, format: 'esm', platform: 'node', target: 'node18', outfile,
        packages: 'external', loader: { '.js': 'jsx', '.jsx': 'jsx', '.css': 'empty' },
        logLevel: 'silent',
    });
    return import(pathToFileURL(outfile));
}

const { SCREENS } = await loadScreens();

/* Фотоконтроль рисует съёмку не экраном телефона, а сценой с машиной (ветка
   проигрывателя для pc_list и pc_camera), поэтому этих экранов в карте нет. */
const OUTSIDE_MAP = { 'photo-control-car': new Set(['pc_list', 'pc_camera']) };

const NOW = new Date(Date.UTC(2026, 10, 17, 6));

const renderStep = (run, target) => {
    const step = currentStep(run);
    const Screen = SCREENS[run.scenario.key][step.screen];
    return renderToStaticMarkup(React.createElement(Screen, {
        scenario: run.scenario,
        world: run.world,
        tap() {},
        toggle() {},
        browse() {},
        target,
        purpose: 'docs',
        period: run.world.period?.label,
        onRestart() {},
    }));
};

/** Коды кнопок, подсвеченных как цель шага (Tap: data-tap, затем class). */
const highlighted = (html) => [...html.matchAll(/data-tap="([^"]+)" class="[^"]*\bis-target\b/g)]
    .map((m) => m[1]);

test('карта экранов покрывает каждый шаг каждого тренажёра и не держит лишних экранов', () => {
    for (const scenario of TRAINERS) {
        const map = SCREENS[scenario.key];
        assert.ok(map, `${scenario.key}: тренажёра нет в карте экранов`);
        const outside = OUTSIDE_MAP[scenario.key] || new Set();
        const used = new Set(scenario.steps.map((step) => step.screen));
        for (const step of scenario.steps) {
            if (outside.has(step.screen)) continue;
            assert.ok(map[step.screen], `${scenario.key}/${step.key}: экран «${step.screen}» не нарисован`);
        }
        for (const key of Object.keys(map)) {
            assert.ok(used.has(key), `${scenario.key}: экран «${key}» в карте, но его не показывает ни один шаг`);
        }
    }
});

test('Такси.Про: на экране каждого шага подсвечена ровно кнопка этого шага', () => {
    let run = startRun(taxiPro, { now: NOW });
    let checked = 0;
    for (let guard = 0; guard < 40 && currentStep(run).screen !== 'result'; guard += 1) {
        const step = currentStep(run);
        const expected = expectedTap(run);
        const html = renderStep(run, expected);
        assert.deepEqual(highlighted(html), [expected],
            `${step.key}: подсвечено ${JSON.stringify(highlighted(html))}, а шаг ждёт «${expected}»`);
        run = tap(run, expected).run;
        checked += 1;
    }
    assert.equal(checked, taxiPro.steps.length - 1, 'урок обязан дойти до итога');
});

test('Такси.Про: ловушки шага — про кнопки, которые на его экране есть', () => {
    let run = startRun(taxiPro, { now: NOW });
    for (let guard = 0; guard < 40 && currentStep(run).screen !== 'result'; guard += 1) {
        const step = currentStep(run);
        const html = renderStep(run, expectedTap(run));
        for (const id of Object.keys(step.traps || {})) {
            assert.ok(html.includes(`data-tap="${id}"`),
                `${step.key}: ловушка «${id}» объясняет кнопку, которой на экране нет`);
        }
        run = tap(run, expectedTap(run)).run;
    }
});

test('Такси.Про: вкладки «Через ЭЦП» и «Через eGov» рисуют своё содержимое', () => {
    let run = startRun(taxiPro, { now: NOW });
    run = tap(run, 'begin').run;
    run = tap(run, 'nav_docs').run;
    const ecp = renderStep(run, expectedTap(run));
    assert.match(ecp, /Документы на подпись/);
    assert.match(ecp, /data-tap="ecp_sign"/);
    assert.doesNotMatch(ecp, /data-tap="press_sign"/, 'на «Через ЭЦП» актов с «Подписать» нет');

    run = tap(run, 'tab_egov').run;
    const egov = renderStep(run, expectedTap(run));
    assert.match(egov, /Акт выполненных работ/);
    assert.doesNotMatch(egov, /data-tap="ecp_sign"/);
    assert.match(egov, /data-tap="tab_egov" class="[^"]*\bis-active\b/, 'активна вкладка «Через eGov»');
});

test('Такси.Про: после «Продолжить» в окне — кабинет с «Вы успешно подписали», без кнопки подписания', () => {
    let run = startRun(taxiPro, { now: NOW });
    for (const id of ['begin', 'nav_docs', 'tab_egov', 'press_sign', 'close_ad', 'sign_all',
        'open_egov', 'approve', 'continue']) {
        run = tap(run, id).run;
    }
    const html = renderStep(run, expectedTap(run));
    assert.match(html, /Вы успешно подписали АВР и закрыли ЭДО/);
    assert.match(html, /Нет документов на подписание/);
    assert.doesNotMatch(html, /data-tap="sign_all"|data-tap="open_egov"|data-tap="save"/);
});

test('Такси.Про: раскрытый список пакета показывает документы месяца, свёрнутый — нет', () => {
    let run = startRun(taxiPro, { now: NOW });
    for (const id of ['begin', 'nav_docs', 'tab_egov', 'press_sign', 'close_ad', 'sign_all']) {
        run = tap(run, id).run;
    }
    assert.doesNotMatch(renderStep(run, expectedTap(run)), /Агентский договор/);
    const opened = toggle(run, 'docsExpanded');
    assert.match(renderStep(opened, expectedTap(opened)), /Агентский договор/);
});
