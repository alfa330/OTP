import assert from 'node:assert/strict';
import test from 'node:test';

import {
    browse, currentStep, expectedTap, isFinished, startRun, tap,
} from '../src/components/wiki/trainers/runner.js';
import desk, { PARKS, PARK_CITIES, SOURCES, childrenAt }
    from '../src/components/wiki/trainers/scenarioCrmTicket.js';
import { CATEGORY_TREE } from '../src/components/wiki/trainers/crmCatalog.js';
import { DEFAULT_CASE, FILTERS } from '../src/components/wiki/trainers/fleetData.js';
import { findContractors } from '../src/components/wiki/trainers/caseData.js';

/* Рабочее место оператора — свободная среда, а не урок: три системы в одном
 * окне, ни шагов, ни подсказок, ни ловушек. Проверяем то, что от такой среды
 * требуется: ошибиться в ней нельзя, справочники настоящие, человек выдуман,
 * а работа доезжает до статистики.
 */

const TODAY = { year: 2026, month: 8, day: 23 };

/** Довести попытку до рабочего стола. */
const atDesk = () => {
    const run = startRun(desk, { now: new Date(Date.UTC(2026, 7, 23, 6)) });
    const next = tap(run, expectedTap(run)).run;
    assert.equal(currentStep(next).key, 'desk');
    return next;
};

test('это свободная среда, а не урок', () => {
    assert.equal(desk.key, 'crm-ticket-create', 'ключ уехал в статьи, его нельзя менять');
    assert.equal(desk.stage, 'desktop');
    assert.equal(desk.mode, 'sandbox');
    assert.deepEqual(desk.traps, {});
    for (const step of desk.steps) {
        assert.deepEqual(step.traps || {}, {}, `у шага «${step.key}» остались ловушки`);
    }
});

test('в окне три вкладки, и открывается оно на CRM', () => {
    const world = atDesk().world;
    assert.equal(world.tab, 'crm');
    assert.equal(world.fleetView, 'contractors');
    assert.equal(world.oktLogged, false, 'в Oktell нужно войти самому');
    assert.equal(world.oktIn, false, 'вход в клиент не ставит в очередь');
    assert.equal(world.call.state, 'offline', 'в свободной среде звонка нет');
});

/* Данные экранов приезжают слепком: чтобы сменить водителя, правят JSON, а не
   код. Проверяем именно это — мир собран из слепка, а не из констант. */
test('мир собирается из слепка дела', () => {
    const world = atDesk().world;
    assert.equal(world.case.key, DEFAULT_CASE.key);
    assert.equal(world.case.contractors.length, DEFAULT_CASE.contractors.length);
    assert.ok(world.case.transactions[0].when, 'даты не подготовлены к показу');

    // Подмена слепка меняет водителя во всех вкладках.
    const other = startRun(desk, {
        now: new Date(Date.UTC(2026, 7, 23, 6)),
        caseData: {
            ...DEFAULT_CASE,
            contractor: { ...DEFAULT_CASE.contractor, last: 'Иванов', first: 'Пётр' },
            contractors: [{ ...DEFAULT_CASE.contractors[0], name: 'Иванов Пётр' }],
        },
    });
    assert.equal(other.world.case.contractor.last, 'Иванов');
    assert.equal(other.world.case.contractors[0].name, 'Иванов Пётр');
});

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
    assert.ok(PARK_CITIES['Регионы'].includes('Экибастуз'));
    form({ city: 'Экибастуз', cats: ['Пассажир', 'Двойная оплата'], comment: 'ок' });
    assert.equal(run.errors, 0, 'свободный выбор дал промах');
});

test('«Сохранить» заканчивает попытку и отдаёт итог', () => {
    let run = atDesk();
    assert.equal(desk.result(run.world), null, 'итог отдан до сохранения');

    run = browse(run, {
        form: {
            ...run.world.form,
            source: 'Звонок', phone: '77015550142', park: 'Jana такси', city: 'Алматы',
            cats: ['Водитель', 'Обычный водитель', 'Консультация'],
            comment: 'Объяснил удержание комиссии парка.',
        },
    });

    const done = tap(run, 'save');
    assert.equal(done.ok, true);
    assert.equal(done.run.world.saved, true);

    const result = desk.result(done.run.world);
    const fields = Object.fromEntries(result.fields);
    assert.equal(fields['Категория'], 'Водитель / Обычный водитель / Консультация');
    /* Правильного ответа у свободной среды нет, поэтому и вердикта быть не
       должно: судит наставник, а не тренажёр. */
    assert.equal(result.correct, undefined, 'в свободной среде появился вердикт');
});

test('в статистику попадают только завершённые попытки', () => {
    assert.equal(desk.recordOnFinishOnly, true);
});

test('справочники CRM полные', () => {
    assert.equal(PARKS.length, 19);
    assert.equal(CATEGORY_TREE.length, 6);
    assert.deepEqual(SOURCES.slice(0, 2), ['Стажер', 'Звонок']);

    const count = (nodes) => nodes.reduce(
        (sum, node) => sum + 1 + (node[1] ? count(node[1]) : 0), 0);
    assert.equal(count(CATEGORY_TREE), 387, 'дерево категорий поредело');

    assert.equal(childrenAt(['Водитель', 'Обычный водитель', 'Консультация']).length, 31);
    assert.deepEqual(childrenAt(['нет такой']), [], 'неизвестный путь не должен падать');

    const pairs = Object.values(PARK_CITIES).reduce((sum, list) => sum + list.length, 0);
    assert.equal(pairs, 95);
});

/* Список кабинета — не шесть строк с пометкой «это он»: героя ничем не
   помечаем, а рядом стоят двойники, чтобы стажёр убедился, что открыл ТОГО. */
test('в списке есть двойники, и герой ничем не помечен', () => {
    const people = DEFAULT_CASE.contractors;
    assert.ok(people.length >= 20, 'список короче двадцати строк');
    assert.ok(people.every((p) => p.me === undefined), 'герой помечен флагом me');
    assert.ok(people.every((p) => /^[0-9a-f]{32}$/.test(p.id)), 'id не 32 hex');

    const byPhone = findContractors(people, DEFAULT_CASE.call.phone);
    assert.equal(byPhone.items.length, 1, 'номер из звонка обязан давать одну строку');
    assert.equal(byPhone.items[0].id, DEFAULT_CASE.contractor.id);

    const bySurname = findContractors(people, 'Нурланов');
    assert.equal(bySurname.items.length, 3, 'двойников по фамилии должно быть двое');
});

test('в ведомости различимы комиссии сервиса и таксопарка', () => {
    const park = DEFAULT_CASE.transactions.filter((t) => t.park);
    const service = DEFAULT_CASE.transactions.filter(
        (t) => !t.park && t.category.includes('Комиссия сервиса'));
    assert.ok(park.length >= 2 && service.length >= 2);
    assert.ok(park.every((t) => t.category.includes('партнёра')));
});

test('в кабинете есть все оси фильтрации', () => {
    assert.ok(FILTERS.length >= 15);
    const names = FILTERS.map(([axis]) => axis);
    for (const axis of ['Статус', 'Статус на линии', 'Профессия', 'Категории', 'Провайдер ЭДО']) {
        assert.ok(names.includes(axis), `нет оси «${axis}»`);
    }
});

/* Слепок не должен содержать разгадки: любое поле вида «правильно/ожидается/
   подсказка» читается из devtools за пять секунд, и тренажёр перестаёт быть
   тренажёром. */
test('в слепке нет ответа и подсказок', () => {
    const text = JSON.stringify(DEFAULT_CASE);
    for (const banned of ['answer', 'expected', 'correct', 'hint', 'checks', 'solution']) {
        assert.ok(!new RegExp(`"${banned}"`).test(text),
            `в слепке есть поле «${banned}» — это ответ, он остаётся на сервере`);
    }
});
