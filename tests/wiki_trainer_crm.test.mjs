import assert from 'node:assert/strict';
import test from 'node:test';

import {
    currentStep, expectedTap, isFinished, speech, startRun, stepGoal, tap,
} from '../src/components/wiki/trainers/runner.js';
import crm, {
    ANSWER, CALL, CATS, CITIES, PARKS, SOURCES, activeField, optionId,
} from '../src/components/wiki/trainers/scenarioCrmTicket.js';

/* Тренажёр создания обращения проверяем отдельно от остальных: он единственный
 * с вводом текста и единственный, где «правильный ответ» — это ветка категорий,
 * а не кнопка. Всё, что ниже, — правила, из-за которых тренажёр вообще сделан;
 * сломать их правкой реплики легко, а заметить глазами на одиннадцати шагах —
 * нет.
 */

const COMMENT = 'Водитель спросил, за что таксопарк удержал комиссию — объяснил условия.';

const INPUT = {
    phone_done: { value: CALL.phone },
    comment_done: { value: COMMENT },
};

/** Довести попытку до шага с указанным ключом, идя только верным путём. */
const upTo = (key) => {
    let run = startRun(crm);
    let guard = 0;
    while (currentStep(run).key !== key && guard < 40) {
        const id = expectedTap(run);
        const result = tap(run, id, INPUT[id] || {});
        assert.equal(result.ok, true,
            `не дошли до «${key}»: шаг «${currentStep(run).key}» отверг «${id}» — `
            + speech(result.run).text);
        run = result.run;
        guard += 1;
    }
    assert.equal(currentStep(run).key, key, `шаг «${key}» не найден`);
    return run;
};

const play = () => {
    let run = startRun(crm);
    let guard = 0;
    while (!isFinished(run) && guard < 40) {
        const id = expectedTap(run);
        run = tap(run, id, INPUT[id] || {}).run;
        guard += 1;
    }
    return run;
};

test('тренажёр живёт на рабочем месте оператора, а не в телефоне', () => {
    assert.equal(crm.key, 'crm-ticket-create');
    assert.equal(crm.stage, 'desktop');
});

test('верный путь заполняет форму целиком и сохраняет обращение', () => {
    const run = play();
    assert.equal(run.errors, 0);
    assert.equal(run.world.saved, true, 'обращение не сохранено');
    assert.deepEqual(run.world.form.cats, ANSWER.cats);
    assert.equal(run.world.form.source, 'Звонок');
    assert.equal(run.world.form.phone, CALL.phone);
    assert.equal(run.world.form.park, CALL.park);
    assert.equal(run.world.form.city, CALL.city);
    assert.equal(run.world.form.comment, COMMENT);
});

/* Главный урок формы: города не существует, пока не выбран таксопарк. В движке
   это выражено порядком шагов, а на экране — тем, что поля просто нет. */
test('город идёт строго после таксопарка', () => {
    const beforePark = upTo('park');
    assert.equal(beforePark.world.form.park, '', 'парк выбран раньше времени');
    assert.equal(activeField(beforePark.world.form), 'park',
        'до выбора парка подсвечиваться должен парк, а не город');

    const afterPark = upTo('city');
    assert.equal(afterPark.world.form.park, CALL.park);
    assert.equal(activeField(afterPark.world.form), 'city');
    assert.ok(CITIES[CALL.park].includes(CALL.city), 'город легенды не из списка своего парка');
});

/* Самая частая ошибка на линии: две «комиссии» стоят в списке рядом. Ловушка
   обязана назвать обе стороны, иначе объяснение не помогает выбрать. */
test('комиссия Яндекса вместо комиссии таксопарка объясняется, а не просто отвергается', () => {
    const run = upTo('cat4');
    const wrong = optionId('c4', CATS.level4.indexOf('Консультация по Комиссии от Яндекс'));
    const result = tap(run, wrong);

    assert.equal(result.ok, false, 'соседний пункт зачли как верный');
    assert.equal(result.run.errors, 1);
    const said = speech(result.run).text;
    assert.match(said, /Яндекс/, 'в объяснении не сказано, чья это комиссия');
    assert.match(said, /Таксопарка/, 'в объяснении не назван верный пункт');
    // Шаг остался на месте: ошибка объясняет, но не пропускает вперёд.
    assert.equal(currentStep(result.run).key, 'cat4');
});

test('верная категория четвёртого уровня закрывает шаг', () => {
    const run = upTo('cat4');
    const right = optionId('c4', CATS.level4.indexOf(ANSWER.cats[3]));
    const result = tap(run, right);
    assert.equal(result.ok, true, speech(result.run).text);
    assert.equal(result.run.world.form.cats[3], 'Консультация по Комиссии от Таксопарка');
});

test('номер телефона: чужой не проходит, свой — в любом формате', () => {
    const run = upTo('phone');

    const empty = tap(run, 'phone_done', { value: '' });
    assert.equal(empty.ok, false);
    assert.match(speech(empty.run).text, /пуст/i);

    const alien = tap(run, 'phone_done', { value: '77771234567' });
    assert.equal(alien.ok, false, 'чужой номер зачли');
    assert.match(speech(alien.run).text, new RegExp(CALL.phone),
        'в объяснении не показан нужный номер');

    // Оператор набирает привычно, со скобками и плюсом. Придираться к формату
    // там, где CRM его сама вычистит, значит учить не тому.
    const pretty = tap(run, 'phone_done', { value: '+7 (701) 555 01 42' });
    assert.equal(pretty.ok, true, speech(pretty.run).text);
    assert.equal(pretty.run.world.form.phone, CALL.phone, 'номер сохранён не в чистом виде');
});

test('комментарий из двух слов не принимается', () => {
    const run = upTo('comment');

    assert.equal(tap(run, 'comment_done', { value: '' }).ok, false);
    const short = tap(run, 'comment_done', { value: 'звонил водитель' });
    assert.equal(short.ok, false, 'отписка принята как комментарий');
    assert.match(speech(short.run).text, /коротко/i);

    assert.equal(tap(run, 'comment_done', { value: COMMENT }).ok, true);
});

/* «Сохранить» — цель последнего шага и одновременно общая ловушка. Нажатый
   раньше времени, он обязан объяснить, а не молча сохранить полупустую форму. */
test('сохранить раньше времени нельзя', () => {
    for (const key of ['source', 'park', 'cat1', 'comment']) {
        const run = upTo(key);
        const result = tap(run, 'save');
        assert.equal(result.ok, false, `на шаге «${key}» сохранение прошло`);
        assert.equal(result.run.world.saved, false, 'форма сохранилась досрочно');
        assert.match(speech(result.run).text, /Рано сохранять/);
    }
});

test('кнопки окна браузера и разделы CRM объясняются, а не молчат', () => {
    const run = upTo('cat1');
    for (const id of ['win_close', 'tab_close', 'browser_reload', 'browser_back',
        'nav_tickets', 'nav_drivers', 'dup_check', 'field_date']) {
        const result = tap(run, id);
        assert.equal(result.ok, false, `«${id}» зачли как верное нажатие`);
        const said = speech(result.run).text;
        assert.ok(said.length > 25,
            `у «${id}» нет внятного объяснения, только общая отговорка: ${said}`);
        assert.ok(!/посмотри на подсвеченную кнопку/.test(said),
            `«${id}» отвечает общей заглушкой вместо разбора`);
    }
});

test('подсказки и цели шагов подставляют данные звонка, а не шаблон', () => {
    let run = startRun(crm);
    let guard = 0;
    while (!isFinished(run) && guard < 40) {
        assert.ok(!/\{\w+\}/.test(stepGoal(run)),
            `в цели шага «${currentStep(run).key}» остался шаблон`);
        assert.ok(!/\{\w+\}/.test(speech(run).text),
            `в реплике шага «${currentStep(run).key}» остался шаблон`);
        const id = expectedTap(run);
        run = tap(run, id, INPUT[id] || {}).run;
        guard += 1;
    }
});

/* Списки сняты с рабочей CRM. Если кто-то «поправит» их на глаз, тренажёр
   начнёт учить искать то, чего в системе нет. */
test('списки формы совпадают с рабочей CRM', () => {
    assert.equal(PARKS.length, 19, 'таксопарков стало не 19');
    assert.ok(PARKS.includes(CALL.park));
    assert.deepEqual(SOURCES, [
        'Стажер', 'Звонок', 'Whatsapp', 'Электронная почта', 'Телеграм', 'Инстаграм',
    ]);
    assert.equal(CATS.level1.length, 6, 'корневых категорий стало не 6');
    assert.equal(CATS.level4.length, 31, 'в «Консультации» стало не 31 пункт');
    for (const [level, list] of Object.entries(CATS)) {
        assert.equal(new Set(list).size, list.length, `в ${level} есть повторы`);
    }
    // Ответы обязаны существовать в своих списках: опечатка здесь сделала бы
    // шаг непроходимым, а тренажёр — тупиком.
    assert.ok(SOURCES.includes(ANSWER.source));
    assert.ok(CATS.level1.includes(ANSWER.cats[0]));
    assert.ok(CATS.level2.includes(ANSWER.cats[1]));
    assert.ok(CATS.level3.includes(ANSWER.cats[2]));
    assert.ok(CATS.level4.includes(ANSWER.cats[3]));
});

test('порядок полей формы ведёт от источника к сохранению', () => {
    const form = {
        source: '', phone: '', park: '', city: '', cats: [], comment: '',
    };
    assert.equal(activeField(form), 'source');
    assert.equal(activeField({ ...form, source: 'Звонок' }), 'phone');
    assert.equal(activeField({ ...form, source: 'Звонок', phone: CALL.phone }), 'park');

    const filled = {
        source: 'Звонок', phone: CALL.phone, park: CALL.park, city: CALL.city,
        cats: ANSWER.cats, comment: COMMENT,
    };
    assert.equal(activeField(filled), 'save');
    assert.equal(activeField({ ...filled, comment: '' }), 'comment');
    assert.equal(activeField({ ...filled, cats: ANSWER.cats.slice(0, 2) }), 'cat3');
});
