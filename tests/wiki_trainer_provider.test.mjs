import assert from 'node:assert/strict';
import test from 'node:test';

import {
    startRun, tap, currentStep, expectedTap, isFinished, speech, stageCount, takeHint,
} from '../src/components/wiki/trainers/runner.js';
import yandexPro from '../src/components/wiki/trainers/scenarioYandexPro.js';

/* Тренажёр «Смена провайдера ЭДО на Sapar в Яндекс Про».
 *
 * Проверяем не разметку, а правила, из-за которых он и написан: смена считается
 * только после «Подтвердить», список провайдеров открывается строкой активного
 * провайдера, а похожий пункт «Закрывающие документы» — не тот. Прощёлкивать
 * это руками после каждой правки реплики нельзя: экранов восемь, а ошибок в
 * них — по три на каждом.
 */

const PATH = ['begin', 'nav_profile', 'yp_legal', 'yp_edo', 'yp_active_provider',
    'pick_sapar', 'change_provider', 'confirm', 'finish'];

const play = (upTo, options) => {
    let run = startRun(yandexPro, options);
    for (const id of PATH.slice(0, upTo)) {
        const result = tap(run, id);
        assert.equal(result.ok, true, `нажатие «${id}» не прошло: ${speech(result.run).text}`);
        run = result.run;
    }
    return run;
};

test('полный путь доходит до финала без ошибок', () => {
    const run = play(PATH.length);
    assert.ok(isFinished(run));
    assert.equal(run.errors, 0);
    assert.equal(currentStep(run).screen, 'result');
    assert.equal(stageCount(yandexPro), 8);
    assert.equal(yandexPro.checklist.length, 8, 'чек-лист обязан совпадать с числом шагов');
});

test('провайдер меняется только после «Подтвердить», а не в шторке', () => {
    // Ровно то место, где смену бросают: шторка нажата, а согласие — нет.
    let run = play(6);
    assert.equal(currentStep(run).key, 'change_provider');
    assert.equal(run.world.activeProvider, 'Бумажный документооборот');

    run = tap(run, 'change_provider').run;
    assert.equal(currentStep(run).key, 'confirm');
    assert.equal(run.world.activeProvider, 'Бумажный документооборот',
        'после шторки провайдер меняться не должен — иначе урок врёт про главный шаг');
    assert.equal(run.world.switched, false);

    const back = tap(run, 'yp_consent_back');
    assert.equal(back.ok, false);
    assert.match(speech(back.run).text, /не сменится|остаётся прежним/i);

    run = tap(run, 'confirm').run;
    assert.equal(run.world.activeProvider, 'Sapar');
    assert.equal(run.world.switched, true);
    assert.equal(currentStep(run).key, 'check');
});

test('«Закрывающие документы» — не тот пункт, и объяснение об этом', () => {
    const run = play(3);
    assert.equal(currentStep(run).key, 'open_edo');
    const wrong = tap(run, 'yp_closing_docs');
    assert.equal(wrong.ok, false);
    assert.equal(currentStep(wrong.run).key, 'open_edo', 'шаг остался на месте');
    assert.match(speech(wrong.run).text, /акты|документы за месяц/i);
    assert.match(speech(wrong.run).text, /электронном документооборот/i);
});

test('другой провайдер в списке — объяснение про парк, а не «не туда»', () => {
    const run = play(5);
    assert.equal(currentStep(run).key, 'pick_sapar');
    for (const id of ['pick_cnt', 'pick_payda', 'pick_partners', 'pick_vezunchik']) {
        const wrong = tap(run, id);
        assert.equal(wrong.ok, false, `${id}: должен быть отказ`);
        assert.match(speech(wrong.run).text, /Sapar/,
            `${id}: в объяснении обязан быть назван нужный провайдер`);
    }
    // Бумажный — отдельный случай: приложение отвечает своей шторкой.
    const paper = tap(run, 'pick_paper');
    assert.equal(paper.ok, false);
    assert.match(speech(paper.run).text, /не такой удобный|бумажный/i);
});

test('«Тарифы и условия» и крестик шторку не заменяют', () => {
    const run = play(6);
    const terms = tap(run, 'yp_terms');
    assert.equal(terms.ok, false);
    assert.match(speech(terms.run).text, /не запустит|стоимость|тариф/i);

    const closed = tap(run, 'yp_close_sheet');
    assert.equal(closed.ok, false);
    assert.match(speech(closed.run).text, /бумажный|начинать/i);
    assert.equal(closed.run.world.activeProvider, 'Бумажный документооборот');
});

test('кнопка из сообщения Про не обругана, а объяснена', () => {
    // Она действительно ведёт к выбору провайдера: врать про неё нельзя, но
    // урок идёт полным путём — это и должно быть сказано.
    const run = play(1);
    assert.equal(currentStep(run).key, 'read_news');
    const shortcut = tap(run, 'yp_choose_provider');
    assert.equal(shortcut.ok, false);
    assert.match(speech(shortcut.run).text, /тоже открывает/i);
    assert.match(speech(shortcut.run).text, /полный путь/i);
});

test('оговорка про даты подставляется по числу месяца', () => {
    // 3 августа: приложение может ответить «Менять провайдера можно с 6 числа».
    const early = play(6, { now: new Date(Date.UTC(2026, 7, 3, 6, 0)) });
    assert.match(speech(early).text, /с 6 числа месяца/);
    assert.match(speech(early).text, /Сегодня 3-е/);

    // 20 августа: ограничение остаётся, но как предупреждение на будущее.
    const later = play(6, { now: new Date(Date.UTC(2026, 7, 20, 6, 0)) });
    assert.match(speech(later).text, /23:30/);
    assert.ok(!speech(later).text.includes('Сегодня 20-е'));
});

test('месяц в репликах — текущий, а следующий назван отдельно', () => {
    const run = play(6, { now: new Date(Date.UTC(2026, 7, 20, 6, 0)) });
    // «подписать со следующего месяца» — это сентябрь, и он должен быть в реплике.
    assert.match(speech(run).text, /сентябре/);
    const checked = tap(tap(run, 'change_provider').run, 'confirm').run;
    assert.match(speech(checked).text, /августе/, 'проверочный шаг говорит про текущий месяц');
    assert.equal(checked.world.monthIn, 'августе');
    assert.equal(checked.world.nextMonthIn, 'сентябре');
});

test('декабрь переходит в январь, а не в «месяц 13»', () => {
    const run = startRun(yandexPro, { now: new Date(Date.UTC(2026, 11, 15, 6, 0)) });
    assert.equal(run.world.monthIn, 'декабре');
    assert.equal(run.world.nextMonthIn, 'январе');
});

test('подсказки есть на каждом шаге и не пустые', () => {
    let run = startRun(yandexPro);
    let guard = 0;
    while (guard < 20) {
        const hinted = takeHint(run);
        assert.ok(speech(hinted).text.length > 8, `${currentStep(run).key}: пустая подсказка`);
        assert.equal(speech(hinted).tone, 'hint');
        if (isFinished(run)) break;
        run = tap(run, expectedTap(run)).run;
        guard += 1;
    }
});
