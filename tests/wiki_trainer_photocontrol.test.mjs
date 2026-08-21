import assert from 'node:assert/strict';
import test from 'node:test';

import {
    startRun, tap, toggle, takeHint, currentStep, expectedTap, isFinished,
    progressPercent, speech, stageCount,
} from '../src/components/wiki/trainers/runner.js';
import photoControl from '../src/components/wiki/trainers/scenarioPhotoControl.js';

/* Фотоконтроль — это правила про КАДР («багажник снимают открытым», «кузов —
 * собранным», «через стекло салон не считается»), а не про порядок нажатий.
 * Проверять их прощёлкиванием трёхмерной сцены руками нельзя: там семь ракурсов
 * и три открывающиеся части, то есть десятки сочетаний. Здесь они проверяются
 * без React и без сцены — сцена присылает ровно то, что подставляет тест.
 */

/** Что экран пришлёт с затвором на каждом кадре, если человек всё сделал верно. */
const GOOD_SHOT = {
    shot_front: { view: 'front', framing: 'ok' },
    shot_left: { view: 'left', framing: 'ok' },
    shot_right: { view: 'right', framing: 'ok' },
    shot_rear: { view: 'rear', framing: 'ok' },
    shot_seats_front: { view: 'inside_front', framing: 'ok' },
    shot_seats_rear: { view: 'inside_rear', framing: 'ok' },
    shot_trunk: { view: 'trunk', framing: 'ok' },
};

/** Что надо открыть перед кадром. Открывание — не шаг: движок знает его как toggle. */
const OPEN_BEFORE = {
    shot_seats_front: 'doorFrontLeft',
    shot_seats_rear: 'doorRearLeft',
    shot_trunk: 'trunkOpen',
};

/** Честный проход: открыть что нужно, снять что просят. */
const playThrough = () => {
    let run = startRun(photoControl);
    let guard = 0;
    while (!isFinished(run) && guard < 40) {
        const step = currentStep(run);
        if (OPEN_BEFORE[step.key]) run = toggle(run, OPEN_BEFORE[step.key]);
        const result = tap(run, expectedTap(run), GOOD_SHOT[step.key] || {});
        assert.equal(result.ok, true,
            `кадр «${step.key}» не прошёл: ${speech(result.run).text}`);
        run = result.run;
        guard += 1;
    }
    assert.ok(isFinished(run), 'сценарий не дошёл до финала');
    return run;
};

test('семь кадров снимаются подряд и доводят тренажёр до конца', () => {
    const run = playThrough();
    assert.equal(run.errors, 0, 'правильный путь не должен давать ошибок');
    assert.equal(progressPercent(run), 100);
    assert.equal(currentStep(run).screen, 'result');
    assert.equal(Object.keys(run.world.shots).length, 7, 'в мире обязаны остаться все семь кадров');
});

test('пунктов инструкции ровно столько же, сколько строк в чек-листе', () => {
    // Разойдутся — и человек увидит «шаг 7 из 7» на середине списка.
    assert.equal(stageCount(photoControl), photoControl.checklist.length);
});

test('закрытый багажник объясняют багажником, а не ракурсом', () => {
    // Самая частая причина отказа: кадр сзади вместо кадра багажника.
    let run = startRun(photoControl);
    run = tap(run, 'begin').run;
    for (const key of ['shot_front', 'shot_left', 'shot_right', 'shot_rear',
        'shot_seats_front', 'shot_seats_rear']) {
        if (OPEN_BEFORE[key]) run = toggle(run, OPEN_BEFORE[key]);
        run = tap(run, 'shutter', GOOD_SHOT[key]).run;
    }
    assert.equal(currentStep(run).key, 'shot_trunk');

    const wrong = tap(run, 'shutter', { view: 'rear', framing: 'ok' });
    assert.equal(wrong.ok, false);
    assert.equal(currentStep(wrong.run).key, 'shot_trunk', 'шаг обязан остаться на месте');
    assert.match(speech(wrong.run).text, /багажник закрыт/i);
    assert.equal(speech(wrong.run).tone, 'error');

    // Открыли — и тот же кадр проходит.
    const opened = toggle(wrong.run, 'trunkOpen');
    const good = tap(opened, 'shutter', { view: 'trunk', framing: 'ok' });
    assert.equal(good.ok, true);
});

test('кузов с распахнутой дверью не принимают', () => {
    let run = startRun(photoControl);
    run = tap(run, 'begin').run;
    run = toggle(run, 'doorFrontLeft');

    const wrong = tap(run, 'shutter', { view: 'front', framing: 'ok' });
    assert.equal(wrong.ok, false);
    assert.match(speech(wrong.run).text, /закрой всё|кузов снимают собранным/i);

    // Закрыли — кадр тот же, теперь принят.
    const closed = toggle(wrong.run, 'doorFrontLeft');
    assert.equal(tap(closed, 'shutter', { view: 'front', framing: 'ok' }).ok, true);
});

test('не тот ракурс — барс называет, что сейчас в кадре', () => {
    // Без названия текущего вида замечание читается как «где-то не так»,
    // и человек крутит машину наугад.
    let run = startRun(photoControl);
    run = tap(run, 'begin').run;
    const wrong = tap(run, 'shutter', { view: 'left', framing: 'ok' });
    assert.equal(wrong.ok, false);
    assert.match(speech(wrong.run).text, /левый борт/i, 'реплика обязана назвать текущий вид');
    assert.match(speech(wrong.run).text, /перёд/i, 'и требуемый тоже');
});

test('дистанция объясняется отдельно от ракурса', () => {
    let run = startRun(photoControl);
    run = tap(run, 'begin').run;

    const close = tap(run, 'shutter', { view: 'front', framing: 'close' });
    assert.equal(close.ok, false);
    assert.match(speech(close.run).text, /три-четыре метра|слишком близко/i);

    const far = tap(run, 'shutter', { view: 'front', framing: 'far' });
    assert.equal(far.ok, false);
    assert.match(speech(far.run).text, /слишком далеко/i);
});

test('фото из галереи отклоняется на любом кадре', () => {
    // Ловушка общая для сценария: подсунуть старый снимок пытаются не на первом
    // кадре, а на том, который лень переснимать.
    let run = startRun(photoControl);
    run = tap(run, 'begin').run;
    for (const id of ['gallery', 'switch_camera', 'skip', 'close_camera']) {
        const wrong = tap(run, id);
        assert.equal(wrong.ok, false, `«${id}» обязан быть ошибкой`);
        assert.ok(speech(wrong.run).text.length > 20, `«${id}»: молчаливый отказ читается как поломка`);
    }
    assert.match(speech(tap(run, 'gallery').run).text, /галере/i);
});

test('открыть дверь — не шаг и не ошибка', () => {
    // Любопытство не наказывается: человек открывает багажник, смотрит и
    // закрывает обратно, шаг при этом стоит на месте.
    let run = startRun(photoControl);
    run = tap(run, 'begin').run;
    const before = currentStep(run).key;

    run = toggle(run, 'trunkOpen');
    assert.equal(run.world.trunkOpen, true);
    assert.equal(currentStep(run).key, before, 'открывание не двигает шаг');
    assert.equal(run.errors, 0, 'открывание не ошибка');

    run = toggle(run, 'trunkOpen');
    assert.equal(run.world.trunkOpen, false, 'та же кнопка закрывает обратно');
});

test('у каждого кадра есть подсказка, и она считается', () => {
    let run = startRun(photoControl);
    run = tap(run, 'begin').run;
    const hinted = takeHint(run);
    assert.equal(hinted.hints, 1);
    assert.equal(speech(hinted).tone, 'hint');
    assert.ok(speech(hinted).text.length > 10);

    for (const step of photoControl.steps) {
        assert.ok(step.hint && step.hint.length > 10, `шаг «${step.key}» остался без подсказки`);
        assert.ok(step.goal && step.goal.length > 5, `шаг «${step.key}» остался без цели`);
    }
});

test('учебная машина в текстах совпадает с моделью в сцене', () => {
    // Подпись «Volkswagen Vento» рядом с другой машиной в объективе — первое,
    // на чём тренажёр теряет доверие.
    const run = startRun(photoControl);
    assert.equal(run.world.car.model, 'Volkswagen Vento');
    const front = photoControl.steps.find((s) => s.key === 'shot_front');
    assert.match(front.msg, /Volkswagen Vento/);
    const rear = photoControl.steps.find((s) => s.key === 'shot_rear');
    assert.match(rear.msg, new RegExp(run.world.car.plate.replace(/ /g, ' ')));
});
