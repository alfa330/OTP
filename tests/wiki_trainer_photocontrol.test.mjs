import assert from 'node:assert/strict';
import test from 'node:test';

import {
    startRun, tap, toggle, takeHint, currentStep, expectedTap, isFinished,
    progressPercent, speech, stageCount,
} from '../src/components/wiki/trainers/runner.js';
import photoControl from '../src/components/wiki/trainers/scenarioPhotoControl.js';
import { SHOTS, slotTap } from '../src/components/wiki/trainers/photoShots.js';

/* Фотоконтроль — это правила про КАДР («багажник снимают открытым», «кузов —
 * собранным», «через стекло салон не считается») и про КОМПЛЕКТ (семь кадров,
 * отправляются целиком). Проверять их прощёлкиванием трёхмерной сцены руками
 * нельзя: там семь ракурсов и три открывающиеся части, то есть десятки
 * сочетаний. Здесь они проверяются без React и без сцены — сцена присылает
 * ровно то, что подставляет тест.
 */

/** Кадр по ключу шага: шагов на кадр два, съёмочный называется shot_<ключ>. */
const shotOfStep = (step) => SHOTS.find((item) => step.key === `shot_${item.key}`) || null;

/** Привести машину в состояние, которого требует кадр: нужное открыть,
 *  лишнее закрыть. Открывание — не шаг: движок знает его как toggle. */
const prepare = (run, shot) => {
    let next = run;
    ['doorFrontLeft', 'doorRearLeft', 'trunkOpen'].forEach((key) => {
        const want = shot.open === key;
        if (Boolean(next.world[key]) !== want) next = toggle(next, key);
    });
    return next;
};

/** Что экран пришлёт с затвором, если человек всё сделал верно. */
const goodShot = (shot) => ({
    view: shot.view,
    framing: 'ok',
    wide: true,
    thumb: `data:image/jpeg;base64,${shot.key}`,
});

/** Честный проход: открыть плитку, встать как надо, снять. */
const playThrough = () => {
    let run = startRun(photoControl);
    let guard = 0;
    while (!isFinished(run) && guard < 60) {
        const step = currentStep(run);
        const shot = shotOfStep(step);
        if (shot) run = prepare(run, shot);
        const result = tap(run, expectedTap(run), shot ? goodShot(shot) : {});
        assert.equal(result.ok, true,
            `шаг «${step.key}» не прошёл: ${speech(result.run).text}`);
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

test('запрашиваются ровно те кадры и в том же порядке, что в приложении', () => {
    /* Состав и порядок сняты с экрана «Фотоконтроль машины» дословно. Разойдутся
       — и человек, выучивший тренажёр, будет искать в приложении плитки не там
       и не под теми названиями. */
    assert.deepEqual(SHOTS.map((shot) => shot.title), [
        'Машина слева',
        'Машина спереди',
        'Машина справа',
        'Машина сзади',
        'Открытый багажник',
        'Задний ряд сидений',
        'Передний ряд сидений',
    ]);
    // Чек-лист урока повторяет тот же список и добавляет отправку.
    assert.deepEqual(photoControl.checklist.slice(0, 7), SHOTS.map((shot) => shot.title));
    assert.match(photoControl.checklist[7], /отправ/i);
});

test('кузов снимают горизонтально, салон — вертикально', () => {
    // Это единственная просьба приложения повернуть телефон, и на ней сыпется
    // часть кадров: салон, снятый горизонтально, обрезает сиденья.
    const hold = Object.fromEntries(SHOTS.map((shot) => [shot.key, shot.hold]));
    assert.deepEqual(hold, {
        left: 'landscape',
        front: 'landscape',
        right: 'landscape',
        rear: 'landscape',
        trunk: 'landscape',
        seats_rear: 'portrait',
        seats_front: 'portrait',
    });
});

test('между кадрами тренажёр возвращает на экран списка', () => {
    /* Так устроено приложение: после каждого снимка водитель видит сетку и то,
       что в ней уже заполнено. Тренажёр, который вёл из камеры прямо в камеру,
       эту половину фотоконтроля прятал. */
    const screens = photoControl.steps.map((step) => step.screen);
    assert.equal(screens[0], 'intro');
    SHOTS.forEach((shot, index) => {
        assert.equal(screens[1 + index * 2], 'pc_list', `перед кадром ${shot.key} нет списка`);
        assert.equal(screens[2 + index * 2], 'pc_camera', `кадр ${shot.key} снимается не в камере`);
    });
    assert.equal(screens[screens.length - 2], 'pc_list', 'отправляют с экрана списка');
    assert.equal(screens[screens.length - 1], 'result');
});

test('пунктов инструкции ровно столько же, сколько строк в чек-листе', () => {
    // Разойдутся — и человек увидит «шаг 8 из 8» на середине списка.
    assert.equal(stageCount(photoControl), photoControl.checklist.length);
});

test('снимок кладётся в плитку миниатюрой, а не отметкой', () => {
    // В приложении плитка показывает сделанное фото; тренажёр обязан класть
    // туда кадр из объектива, иначе список врёт о том, что отправлено.
    const run = playThrough();
    SHOTS.forEach((shot) => {
        assert.equal(typeof run.world.shots[shot.key], 'string',
            `кадр «${shot.title}» остался без миниатюры`);
        assert.match(run.world.shots[shot.key], /^data:image\/jpeg/);
    });
});

test('«Далее» с пустыми плитками не отправляет, а объясняет', () => {
    // Кнопка в приложении жёлтая и активная даже с пустым списком: про
    // неполный комплект водитель узнаёт по нажатию.
    let run = startRun(photoControl);
    run = tap(run, 'begin').run;

    const early = tap(run, 'next');
    assert.equal(early.ok, false);
    assert.match(speech(early.run).text, /7|семь|плит/i);
    assert.equal(currentStep(early.run).key, 'open_left', 'шаг обязан остаться на месте');
});

test('не та плитка — тренажёр называет нужную и не наказывает молчанием', () => {
    let run = startRun(photoControl);
    run = tap(run, 'begin').run;

    const wrong = tap(run, slotTap('seats_front'));
    assert.equal(wrong.ok, false);
    assert.match(speech(wrong.run).text, /Машина слева/,
        'реплика обязана назвать плитку, которую ждут');
    // И честно сказать, что очередь — правило урока, а не приложения.
    assert.match(speech(wrong.run).text, /в любом порядке/i);
});

test('закрытый багажник объясняют багажником, а не ракурсом', () => {
    // Самая частая причина отказа: кадр сзади вместо кадра багажника.
    let run = startRun(photoControl);
    run = tap(run, 'begin').run;
    for (const shot of SHOTS.slice(0, 4)) {
        run = tap(run, slotTap(shot.key)).run;
        run = prepare(run, shot);
        run = tap(run, 'shutter', goodShot(shot)).run;
    }
    run = tap(run, slotTap('trunk')).run;
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
    run = tap(run, slotTap('left')).run;
    run = toggle(run, 'doorFrontLeft');

    const wrong = tap(run, 'shutter', { view: 'left', framing: 'ok' });
    assert.equal(wrong.ok, false);
    assert.match(speech(wrong.run).text, /закрой всё|кузов снимают собранным/i);

    // Закрыли — кадр тот же, теперь принят.
    const closed = toggle(wrong.run, 'doorFrontLeft');
    assert.equal(tap(closed, 'shutter', { view: 'left', framing: 'ok' }).ok, true);
});

test('не тот ракурс — барс называет, что сейчас в кадре', () => {
    // Без названия текущего вида замечание читается как «где-то не так»,
    // и человек крутит машину наугад.
    let run = startRun(photoControl);
    run = tap(run, 'begin').run;
    run = tap(run, slotTap('left')).run;
    const wrong = tap(run, 'shutter', { view: 'front', framing: 'ok' });
    assert.equal(wrong.ok, false);
    assert.match(speech(wrong.run).text, /перёд/i, 'реплика обязана назвать текущий вид');
    assert.match(speech(wrong.run).text, /левый борт/i, 'и требуемый тоже');
    // И подсказать силуэтом — тем самым, что нарисован на экране.
    assert.match(speech(wrong.run).text, /Машина слева/);
});

test('дистанция объясняется отдельно от ракурса', () => {
    let run = startRun(photoControl);
    run = tap(run, 'begin').run;
    run = tap(run, slotTap('left')).run;

    const close = tap(run, 'shutter', { view: 'left', framing: 'close' });
    assert.equal(close.ok, false);
    assert.match(speech(close.run).text, /три-четыре метра|слишком близко/i);

    const far = tap(run, 'shutter', { view: 'left', framing: 'far' });
    assert.equal(far.ok, false);
    assert.match(speech(far.run).text, /слишком далеко/i);
});

test('галерея, фронталка и крестик объясняются на любом кадре', () => {
    // Ловушки общие для сценария: подсунуть старый снимок пытаются не на первом
    // кадре, а на том, который лень переснимать.
    let run = startRun(photoControl);
    run = tap(run, 'begin').run;
    run = tap(run, slotTap('left')).run;
    for (const id of ['gallery', 'switch_camera', 'skip', 'close_camera', 'back']) {
        const wrong = tap(run, id);
        assert.equal(wrong.ok, false, `«${id}» обязан быть ошибкой`);
        assert.ok(speech(wrong.run).text.length > 20, `«${id}»: молчаливый отказ читается как поломка`);
    }
    assert.match(speech(tap(run, 'gallery').run).text, /галере/i);
    assert.match(speech(tap(run, 'close_camera').run).text, /пуст|без снимка/i);
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

test('ширик включён с самого начала — им фотоконтроль и снимают', () => {
    // Кнопка 0,5x переключает объектив и меняет то, что влезает в кадр; с 1x
    // машина в силуэт с трёх метров не помещается.
    const run = startRun(photoControl);
    assert.equal(run.world.wide, true);
    assert.equal(run.world.flash, false);
    const switched = toggle(run, 'wide');
    assert.equal(switched.world.wide, false, 'зум — переключатель, а не ошибка');
    assert.equal(switched.errors, 0);
});

test('у каждого шага есть подсказка и цель, и подсказка считается', () => {
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
