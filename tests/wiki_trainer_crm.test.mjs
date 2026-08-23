import assert from 'node:assert/strict';
import test from 'node:test';

import {
    browse, currentStep, expectedTap, isFinished, startRun, tap,
} from '../src/components/wiki/trainers/runner.js';
import desk, {
    CALL, DRIVER, PARKS, PARK_CITIES, SOURCES, childrenAt,
} from '../src/components/wiki/trainers/scenarioCrmTicket.js';
import { CATEGORY_TREE } from '../src/components/wiki/trainers/crmCatalog.js';
import { CONTRACTORS, DRIVER as FLEET_DRIVER, FILTERS, TRANSACTIONS }
    from '../src/components/wiki/trainers/fleetData.js';

/* Рабочее место оператора — свободная среда, а не урок: три системы в одном
 * окне, ни шагов, ни подсказок, ни ловушек. Проверяем ровно то, что от такой
 * среды требуется: по ней нельзя «ошибиться», данные в ней настоящие (кроме
 * человека, который выдуман), и работа человека доезжает до статистики.
 */

/** Довести попытку до рабочего стола (единственный шаг перед «Сохранить»). */
const atDesk = () => {
    const run = startRun(desk);
    const next = tap(run, expectedTap(run)).run;
    assert.equal(currentStep(next).key, 'desk');
    return next;
};

test('это свободная среда, а не урок', () => {
    assert.equal(desk.key, 'crm-ticket-create', 'ключ уехал в статьи, его нельзя менять');
    assert.equal(desk.stage, 'desktop');
    assert.equal(desk.mode, 'sandbox');
    assert.deepEqual(desk.traps, {}, 'в свободной среде ловушек быть не должно');
    for (const step of desk.steps) {
        assert.deepEqual(step.traps || {}, {}, `у шага «${step.key}» остались ловушки`);
    }
});

test('в окне три вкладки, и открывается оно на CRM', () => {
    const world = startRun(desk).world;
    assert.equal(world.tab, 'crm');
    assert.equal(world.fleetView, 'contractors');
    assert.equal(world.oktLogged, false, 'в Oktell нужно войти самому');
    assert.equal(world.oktIn, false, 'вход в клиент не ставит в очередь');
});

/* Ходить по средe — не ход движка. Иначе тренажёр наказывал бы за то, что
   человек открыл справочник, то есть отучал бы туда смотреть. */
test('переходы по системам не считаются промахами и не двигают шаг', () => {
    let run = atDesk();
    const before = run.errors;
    run = browse(run, { tab: 'fleet' });
    run = browse(run, { fleetView: 'card', fleetTab: 'transactions' });
    run = browse(run, { tab: 'oktell' });
    run = browse(run, { oktLogged: true, oktIn: true, oktStatus: 'Перезвон' });
    run = browse(run, { tab: 'crm' });

    assert.equal(run.errors, before, 'прогулка засчитана промахом');
    assert.equal(currentStep(run).key, 'desk', 'шаг сдвинулся от перехода');
    assert.equal(run.world.fleetTab, 'transactions', 'кабинет забыл, где мы были');
    assert.equal(run.world.oktStatus, 'Перезвон', 'Oktell забыл статус');
});

test('форма свободная: любая ветка проходится до конца', () => {
    let run = atDesk();
    const form = (patch) => { run = browse(run, { form: { ...run.world.form, ...patch } }); };

    form({ source: 'Whatsapp', phone: '77010000000', park: 'Регионы' });
    assert.ok(PARK_CITIES['Регионы'].includes('Экибастуз'),
        'города берутся из справочника своего парка');
    form({ city: 'Экибастуз' });

    // Ветка, не имеющая ничего общего с «правильной» — среда её принимает.
    form({ cats: ['Пассажир', 'Двойная оплата'], comment: 'ок' });
    assert.equal(run.errors, 0, 'свободный выбор дал промах');
});

test('«Сохранить» заканчивает попытку и отдаёт итог', () => {
    let run = atDesk();
    assert.equal(desk.result(run.world), null, 'итог отдан до сохранения');

    run = browse(run, {
        form: {
            ...run.world.form,
            source: 'Звонок', phone: CALL.phone, park: CALL.park, city: CALL.city,
            cats: ['Водитель', 'Обычный водитель', 'Консультация'],
            comment: 'Объяснил удержание комиссии парка.',
        },
    });

    const done = tap(run, 'save');
    assert.equal(done.ok, true);
    assert.ok(isFinished(done.run) || currentStep(done.run).key === 'done');
    assert.equal(done.run.world.saved, true);

    const result = desk.result(done.run.world);
    const fields = Object.fromEntries(result.fields);
    assert.equal(fields['Звонок/Чат'], 'Звонок');
    assert.equal(fields['Таксопарк'], CALL.park);
    assert.equal(fields['Категория'], 'Водитель / Обычный водитель / Консультация');
    /* Правильного ответа у свободной среды нет, поэтому и вердикта быть не
       должно: судит наставник, а не тренажёр. */
    assert.equal(result.correct, undefined, 'в свободной среде появился вердикт');
});

test('в статистику попадают только завершённые попытки', () => {
    assert.equal(desk.recordOnFinishOnly, true);
});

/* Справочники — настоящие, снятые с рабочей CRM. Если кто-то «сократит» их,
   тренажёр начнёт учить искать в списке, которого на смене нет. */
test('справочники CRM полные', () => {
    assert.equal(PARKS.length, 19);
    assert.equal(CATEGORY_TREE.length, 6, 'корневых категорий стало не 6');
    assert.deepEqual(SOURCES.slice(0, 2), ['Стажер', 'Звонок']);

    const count = (nodes) => nodes.reduce(
        (sum, node) => sum + 1 + (node[1] ? count(node[1]) : 0), 0);
    assert.equal(count(CATEGORY_TREE), 387, 'дерево категорий поредело');

    assert.equal(childrenAt(['Водитель']).length, 2);
    assert.equal(childrenAt(['Водитель', 'Обычный водитель', 'Консультация']).length, 31);
    assert.deepEqual(childrenAt(['Тестовый звонок/Чат']), [], 'у листа не должно быть детей');
    assert.deepEqual(childrenAt(['нет такой']), [], 'неизвестный путь не должен падать');

    // Пары «парк — город» тоже настоящие: у каждого парка свой набор.
    const pairs = Object.values(PARK_CITIES).reduce((sum, list) => sum + list.length, 0);
    assert.equal(pairs, 95);
});

/* Водитель придуман и должен быть ОДИН на все три системы: иначе человек
   пересобирает в голове «кто это» при каждом переходе между вкладками. */
test('водитель выдуман и одинаков в CRM и Диспетчерской', () => {
    assert.equal(FLEET_DRIVER.full, DRIVER.full);
    assert.equal(FLEET_DRIVER.phone, DRIVER.phone);
    assert.equal(CALL.phone, DRIVER.phone);
    assert.equal(CONTRACTORS[0].name, DRIVER.full, 'в списке кабинета не тот человек');

    // Признаки выдуманности: учебный номер и «никакой» номер ВУ.
    assert.match(DRIVER.phone, /^7701555/, 'телефон перестал быть учебным');
    assert.match(DRIVER.license, /0{4,}/, 'номер ВУ выглядит настоящим');
});

/* Ведомость — то место, ради которого кабинет вообще открыт: в ней рядом видно,
   что удержал сервис и что удержал парк. */
test('в ведомости различимы комиссии сервиса и таксопарка', () => {
    const park = TRANSACTIONS.filter((t) => t.park);
    const service = TRANSACTIONS.filter((t) => !t.park && t.category.includes('Комиссия сервиса'));
    assert.ok(park.length >= 2, 'удержаний парка почти нет');
    assert.ok(service.length >= 2, 'комиссий сервиса почти нет');
    assert.ok(park.every((t) => t.category.includes('партнёра')),
        'строка парка не названа комиссией партнёра');
});

test('в кабинете есть все оси фильтрации', () => {
    assert.ok(FILTERS.length >= 15, 'осей фильтрации стало меньше пятнадцати');
    const names = FILTERS.map(([axis]) => axis);
    for (const axis of ['Статус', 'Статус на линии', 'Профессия', 'Тип сотрудничества',
        'Категории', 'Провайдер ЭДО']) {
        assert.ok(names.includes(axis), `нет оси «${axis}»`);
    }
});
