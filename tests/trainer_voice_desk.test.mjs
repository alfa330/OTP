import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { buildSync } from 'esbuild';

/* Смена в среде Windows раздела «Тренажёр»: сценарий смены и выбор водителя. */

const loadDesk = async () => {
    const dir = fileURLToPath(new URL('../node_modules/.cache/voice-desk-test/', import.meta.url));
    mkdirSync(dir, { recursive: true });
    const outfile = join(dir, `desk-${process.pid}.mjs`);
    buildSync({
        stdin: {
            contents: "export { DESK_SCENARIO, pickDriver, launchFailure } from './VoiceDesk.jsx';",
            resolveDir: fileURLToPath(new URL('../src/components/trainer/', import.meta.url)),
            loader: 'jsx',
        },
        bundle: true, format: 'esm', platform: 'node', target: 'node18', outfile,
        packages: 'external', loader: { '.js': 'jsx', '.jsx': 'jsx', '.css': 'empty' },
        define: { 'import.meta.env': '{}' },
        logLevel: 'silent',
    });
    return import(pathToFileURL(outfile));
};

const desk = await loadDesk();

test('смена открывается сразу рабочим столом и не уходит с него на карточку итога', () => {
    const steps = desk.DESK_SCENARIO.steps;
    assert.equal(steps[0].screen, 'desk', 'первым шагом — вступительная карточка, а не рабочий стол');
    assert.ok(steps.every((step) => step.screen === 'desk'), 'после попытки рабочий стол сменяется карточкой');
    assert.equal(desk.DESK_SCENARIO.mode, 'call');
    assert.equal(desk.DESK_SCENARIO.stage, 'desktop');
    assert.equal(steps[0].action, 'finish_attempt');
});

test('случайный водитель — не тот же, что звонил только что', () => {
    const scenarios = [{ key: 'a' }, { key: 'b' }, { key: 'c' }];
    for (let i = 0; i < 30; i += 1) {
        const picked = desk.pickDriver(scenarios, 'b', () => i / 30);
        assert.notEqual(picked, 'b');
        assert.ok(['a', 'c'].includes(picked));
    }
    assert.equal(desk.pickDriver([{ key: 'only' }], 'only'), 'only', 'один водитель — звонит он');
    assert.equal(desk.pickDriver([], null), null);
});

test('дело водителя не пришло: без смены — окно с «Повторить» тем же выбором, во время смены — уведомление', () => {
    const first = desk.launchFailure(false, 'random', new Error('HTTP 404'));
    assert.deepEqual(Object.keys(first), ['failed'], 'первый звонок не открылся — а окна нет, экран пуст');
    assert.equal(first.failed.choice, 'random', '«Повторить» должен звать того же водителя (или снова случайного)');
    assert.match(first.failed.text, /HTTP 404/);

    const during = desk.launchFailure(true, 'cancel_calm', new Error('сеть'));
    assert.deepEqual(Object.keys(during), ['problem'], 'идущая смена закрылась бы окном ошибки');
    assert.match(during.problem, /Не удалось открыть дело водителя: сеть/);
});
