import assert from 'node:assert/strict';
import test from 'node:test';

import {
    startRun, tap, toggle, takeHint, currentStep, expectedTap, progressPercent,
    stageCount, previousPeriod, almatyToday, speech, stepGoal, isFinished,
} from '../src/components/wiki/trainers/runner.js';
import taxiPro from '../src/components/wiki/trainers/scenarioTaxiPro.js';
import saparSite from '../src/components/wiki/trainers/scenarioSapar.js';
import { TRAINERS, findTrainer, TRAINER_CARDS } from '../src/components/wiki/trainers/registry.js';

/* Тренажёр — это правила («сначала закрой рекламу», «после eGov вернись на
 * портал», «пока не сохранил — не подписано»), и проверять их надо здесь, а не
 * прощёлкивая четырнадцать экранов руками после каждой правки текста реплики.
 */

/* Нажатия, у которых есть ВВОД. Правильное значение знает мир попытки, а не
   тест: коды eGov случайны на каждый заход, телефон приходит из легенды звонка.
   Держим таблицу здесь, а не в сценариях: подсказка «что ввести, чтобы прошло»
   в рабочем коде — это ответ к упражнению, лежащий рядом с упражнением. */
const INPUT = {
    submit_code: (run) => ({
        code: currentStep(run).key === 'code_docs' ? run.world.codes.docs : run.world.codes.auth,
    }),
    phone_done: (run) => ({ value: run.world.call.phone }),
    comment_done: () => ({
        value: 'Водитель спросил, за что таксопарк удержал комиссию — объяснил условия.',
    }),
};

/** Прогон сценария правильными нажатиями. Возвращает финальную попытку. */
const playThrough = (scenario, { random } = {}) => {
    let run = startRun(scenario, { random });
    let guard = 0;
    while (!isFinished(run) && guard < 60) {
        const id = expectedTap(run);
        const payload = INPUT[id] ? INPUT[id](run) : {};
        const result = tap(run, id, payload);
        assert.equal(result.ok, true,
            `шаг «${currentStep(run).key}»: правильное нажатие «${id}» не прошло — `
            + `${speech(result.run).text}`);
        run = result.run;
        guard += 1;
    }
    assert.ok(isFinished(run), 'сценарий не дошёл до финала');
    return run;
};

/* Тренажёры, которые проходятся одними нажатиями: учебный телефон и рабочее
   место оператора — весь мир помещается в экран. Фотоконтроль устроен иначе:
   там кадр зависит от того, где стоит человек и что у машины открыто, поэтому
   его проход живёт в wiki_trainer_photocontrol.test.mjs, где есть чем эти
   условия задать. */
const TAPPABLE = TRAINERS.filter((scenario) => scenario.stage !== 'world');

test('тренажёры проходятся правильными нажатиями до конца', () => {
    assert.ok(TAPPABLE.length >= 2, 'проходимых нажатиями сценариев стало меньше двух — проверь фильтр');
    for (const scenario of TAPPABLE) {
        const run = playThrough(scenario);
        assert.equal(run.errors, 0, `${scenario.key}: правильный путь не должен давать ошибок`);
        assert.equal(progressPercent(run), 100);
        assert.equal(currentStep(run).screen, 'result');
    }
});

/* Финальный экран один на все сценарии, а итог у каждого свой. Пока текст был
   зашит в экран, тренажёр смены провайдера заканчивался словами «Документы
   подписаны», хотя ничего не подписывал. Проверяем, что своя строка есть у
   всех — общая заглушка не должна всплыть снова. */
test('у каждого сценария свой текст итога', () => {
    for (const scenario of TRAINERS) {
        assert.ok(scenario.resultNote && scenario.resultNote.length > 12,
            `${scenario.key}: нет своего resultNote — финал покажет общую фразу`);
    }
    const notes = TRAINERS.map((s) => s.resultNote);
    assert.equal(new Set(notes).size, notes.length, 'два сценария заканчиваются одинаково');
});

test('у каждого тренажёра в каталоге есть имя, шаги и чек-лист', () => {
    // Общая проверка на ВСЕ сценарии, включая фотоконтроль: пустая карточка в
    // каталоге или сценарий без шагов ломает вкладку «Тренажёры» целиком.
    for (const scenario of TRAINERS) {
        assert.ok(scenario.key && scenario.title, 'сценарий без ключа или названия');
        assert.ok(scenario.steps.length > 2, `${scenario.key}: шагов почти нет`);
        assert.ok((scenario.checklist || []).length > 0, `${scenario.key}: пустой чек-лист`);
        assert.equal(scenario.steps[scenario.steps.length - 1].screen, 'result',
            `${scenario.key}: последний шаг обязан быть финалом`);
        assert.equal(findTrainer(scenario.key), scenario, `${scenario.key}: не ищется по ключу`);
    }
});

test('прогресс растёт монотонно от 0 до 100', () => {
    let run = startRun(taxiPro);
    assert.equal(progressPercent(run), 0);
    let previous = 0;
    let guard = 0;
    while (!isFinished(run) && guard < 60) {
        run = tap(run, expectedTap(run)).run;
        const now = progressPercent(run);
        assert.ok(now > previous, 'шаг вперёд обязан двигать полосу прогресса');
        previous = now;
        guard += 1;
    }
    assert.equal(previous, 100);
});

test('неверное нажатие не двигает шаг, а объясняет', () => {
    // Реклама поверх кабинета — тот самый экран, где нажатие «уходит в баннер».
    let run = startRun(taxiPro);
    run = tap(run, 'begin').run;         // intro → главная
    run = tap(run, 'nav_docs').run;      // главная → документы
    run = tap(run, 'press_sign').run;    // документы → кабинет с рекламой
    assert.equal(currentStep(run).key, 'close_ad');

    const wrong = tap(run, 'sign_all');
    assert.equal(wrong.ok, false);
    assert.equal(currentStep(wrong.run).key, 'close_ad', 'шаг остался на месте');
    assert.equal(wrong.run.errors, 1);
    assert.match(speech(wrong.run).text, /закрой рекламу/i);
    assert.equal(speech(wrong.run).tone, 'error');

    // После правильного нажатия реплика возвращается к объяснению шага.
    const right = tap(wrong.run, 'close_ad');
    assert.equal(right.ok, true);
    assert.equal(currentStep(right.run).key, 'sign_all');
    assert.equal(speech(right.run).tone, 'idle');
    assert.equal(right.run.errors, 1, 'счётчик ошибок не обнуляется удачным шагом');
});

test('нажатие, которого автор сценария не предусмотрел, всё равно получает ответ', () => {
    const run = startRun(taxiPro);
    const result = tap(run, 'кнопка-которой-нет');
    assert.equal(result.ok, false);
    assert.ok(result.run.speech.text.length > 10, 'молчаливый отказ читается как поломка');
});

test('свернуть eGov вместо «Продолжить» — отдельное объяснение', () => {
    // Самая частая ошибка: подпись создана, но портал о ней не узнал.
    let run = startRun(taxiPro);
    for (const id of ['begin', 'nav_docs', 'press_sign', 'close_ad', 'sign_all', 'open_egov', 'approve']) {
        run = tap(run, id).run;
    }
    assert.equal(currentStep(run).key, 'continue');
    const wrong = tap(run, 'minimize');
    assert.equal(wrong.ok, false);
    assert.match(speech(wrong.run).text, /сверн/i);
});

test('статусы актов меняются только после обновления списка', () => {
    let run = startRun(taxiPro);
    for (const id of ['begin', 'nav_docs', 'press_sign', 'close_ad', 'sign_all', 'open_egov',
        'approve', 'continue']) {
        run = tap(run, id).run;
    }
    assert.equal(currentStep(run).key, 'check_status');
    assert.equal(run.world.refreshed, false, 'до обновления список обязан быть старым');
    run = tap(run, 'refresh').run;
    assert.equal(run.world.refreshed, true);
    assert.equal(currentStep(run).key, 'confirm_signed');
});

test('на сайте Сапар код документов отличается от кода входа', () => {
    const run = startRun(saparSite);
    assert.match(run.world.codes.auth, /^\d{4}$/);
    assert.match(run.world.codes.docs, /^\d{4}$/);
    assert.notEqual(run.world.codes.docs, run.world.codes.auth,
        'одинаковые коды убивают смысл шага «сессия новая — код новый»');
});

test('постоянный генератор всё равно даёт два разных кода', () => {
    // random() из теста возвращает одно и то же — сценарий обязан развести коды
    // сам, иначе на детерминированном прогоне шаг «новая сессия» вырождается.
    const run = startRun(saparSite, { random: () => 0.4242 });
    assert.notEqual(run.world.codes.docs, run.world.codes.auth);
});

test('код от входа на шаге документов получает свою реплику', () => {
    let run = startRun(saparSite);
    const path = ['begin', 'open_chrome', 'focus_address', 'go_sapar', 'login_egov'];
    for (const id of path) run = tap(run, id).run;
    assert.equal(currentStep(run).key, 'code_auth');

    // Неверный код: шаг остаётся, ошибка считается.
    const bad = tap(run, 'submit_code', { code: '0000' === run.world.codes.auth ? '1111' : '0000' });
    assert.equal(bad.ok, false);
    assert.equal(currentStep(bad.run).key, 'code_auth');
    assert.equal(bad.run.errors, 1);

    run = tap(run, 'submit_code', { code: run.world.codes.auth }).run;
    for (const id of ['approve', 'continue', 'open_documents', 'sign_all', 'open_egov']) {
        run = tap(run, id).run;
    }
    assert.equal(currentStep(run).key, 'code_docs');

    const reused = tap(run, 'submit_code', { code: run.world.codes.auth });
    assert.equal(reused.ok, false);
    assert.match(speech(reused.run).text, /код от входа/i);
    // Реплика подставляет НУЖНЫЙ код, а не оставляет шаблон.
    assert.ok(speech(reused.run).text.includes(run.world.codes.docs));
    assert.ok(!speech(reused.run).text.includes('{codeDocs}'));
});

test('«Сохранить» обязательно: до него документы не подписаны', () => {
    let run = startRun(saparSite);
    const path = ['begin', 'open_chrome', 'focus_address', 'go_sapar', 'login_egov'];
    for (const id of path) run = tap(run, id).run;
    run = tap(run, 'submit_code', { code: run.world.codes.auth }).run;
    for (const id of ['approve', 'continue', 'open_documents', 'sign_all', 'open_egov']) {
        run = tap(run, id).run;
    }
    run = tap(run, 'submit_code', { code: run.world.codes.docs }).run;
    run = tap(run, 'approve').run;
    run = tap(run, 'continue').run;

    assert.equal(currentStep(run).key, 'save');
    assert.equal(run.world.saved, false);
    // Обновление страницы до сохранения — ловушка с отдельным объяснением.
    const wrong = tap(run, 'refresh');
    assert.equal(wrong.ok, false);
    assert.match(speech(wrong.run).text, /потеряется/i);

    run = tap(run, 'save').run;
    assert.equal(run.world.saved, true);
    assert.equal(currentStep(run).key, 'check_status');
});

test('раскрыть список — не шаг и не ошибка', () => {
    let run = startRun(taxiPro);
    for (const id of ['begin', 'nav_docs', 'press_sign', 'close_ad', 'sign_all']) {
        run = tap(run, id).run;
    }
    const before = currentStep(run).key;
    const opened = toggle(run, 'docsExpanded');
    assert.equal(opened.world.docsExpanded, true);
    assert.equal(currentStep(opened).key, before);
    assert.equal(opened.errors, 0);
    assert.equal(toggle(opened, 'docsExpanded').world.docsExpanded, false);
});

test('подсказка считается и подменяет реплику', () => {
    const run = startRun(saparSite);
    const hinted = takeHint(run);
    assert.equal(hinted.hints, 1);
    assert.equal(speech(hinted).tone, 'hint');
    assert.equal(speech(hinted).text, currentStep(run).hint);
});

test('в подсказке с кодом стоит сам код, а не шаблон', () => {
    let run = startRun(saparSite);
    for (const id of ['begin', 'open_chrome', 'focus_address', 'go_sapar', 'login_egov']) {
        run = tap(run, id).run;
    }
    const hinted = takeHint(run);
    assert.ok(speech(hinted).text.includes(run.world.codes.auth));
    assert.ok(!speech(hinted).text.includes('{codeAuth}'));
});

test('период документов — предыдущий календарный месяц', () => {
    assert.equal(previousPeriod({ year: 2026, month: 8, day: 20 }).short, 'июль 2026');
    assert.equal(previousPeriod({ year: 2026, month: 8, day: 20 }).label, 'за Июль 2026');
    // Январь уходит в декабрь прошлого года — на этом ломаются самодельные -1.
    const january = previousPeriod({ year: 2026, month: 1, day: 9 });
    assert.equal(january.short, 'декабрь 2025');
    assert.equal(january.iso, '2025-12-31');
    // Февраль невисокосного года — 28 дней, високосного — 29.
    assert.equal(previousPeriod({ year: 2026, month: 3, day: 5 }).iso, '2026-02-28');
    assert.equal(previousPeriod({ year: 2024, month: 3, day: 5 }).iso, '2024-02-29');
});

test('дата берётся по Алматы, а не по UTC', () => {
    // 01:30 первого августа в Алматы — это ещё 20:30 31 июля по UTC. Наивный
    // toISOString() показал бы июль и увёл период документов на месяц назад.
    const midnightAlmaty = new Date(Date.UTC(2026, 6, 31, 20, 30));
    assert.deepEqual(almatyToday(midnightAlmaty), { year: 2026, month: 8, day: 1 });
});

test('реплики и цели шагов заполнены во всех сценариях', () => {
    for (const scenario of TRAINERS) {
        let run = startRun(scenario);
        let guard = 0;
        while (guard < 60) {
            const step = currentStep(run);
            assert.ok(step.msg && step.msg.length > 10, `${scenario.key}/${step.key}: нет реплики`);
            assert.ok(step.hint && step.hint.length > 5, `${scenario.key}/${step.key}: нет подсказки`);
            assert.ok(stepGoal(run).length > 3, `${scenario.key}/${step.key}: нет цели`);
            // Шаблоны обязаны раскрываться: {period} в реплике читателю не нужен.
            assert.ok(!speech(run).text.includes('{'), `${scenario.key}/${step.key}: шаблон не раскрыт`);
            if (isFinished(run)) break;
            const id = expectedTap(run);
            const payload = id === 'submit_code'
                ? { code: step.key === 'code_docs' ? run.world.codes.docs : run.world.codes.auth }
                : {};
            run = tap(run, id, payload).run;
            guard += 1;
        }
    }
});

test('ключи сценариев уникальны и совпадают со списком витрины', () => {
    // Ключ уезжает в текст статьи (data-wiki-trainer) — совпадение двух
    // сценариев на одном ключе означало бы, что кнопка открывает не то.
    const keys = TRAINERS.map((s) => s.key);
    assert.equal(new Set(keys).size, keys.length);
    for (const key of keys) assert.ok(findTrainer(key));
    assert.equal(findTrainer('нет-такого'), null);
    assert.deepEqual(TRAINER_CARDS.map((c) => c.key), keys);
    /* Витрина рисует рядом с карточкой телефон и решает по stage, уместен ли он:
       у тренажёра за компьютером телефона быть не должно. Без поля в карточке
       она молча вернулась бы к телефону для всех. */
    for (const card of TRAINER_CARDS) {
        assert.ok(card.stage, `${card.key}: карточка витрины без stage`);
    }
    assert.equal(TRAINER_CARDS.find((c) => c.key === 'crm-ticket-create').stage, 'desktop');
    // Число шагов инструкции показывается человеку — оно не должно быть нулём.
    assert.equal(stageCount(taxiPro), 8);
    assert.equal(stageCount(saparSite), 6);
});

test('у каждого шага есть экран, и все экраны сценария различимы', () => {
    for (const scenario of TRAINERS) {
        for (const step of scenario.steps) {
            assert.ok(step.screen, `${scenario.key}/${step.key}: не указан экран`);
            assert.ok(step.action, `${scenario.key}/${step.key}: не указано нажатие`);
        }
    }
});
