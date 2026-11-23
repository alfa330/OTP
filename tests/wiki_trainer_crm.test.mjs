import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { buildSync } from 'esbuild';

import {
    browse, currentBlock, currentStep, expectedTap, isFinished, restartBlock, speech, stageCount, startRun, takeHint,
    tap, uiAction, uiMisses,
} from '../src/components/wiki/trainers/runner.js';
import desk, {
    CAT_COMMISSION, CAT_WITHDRAW, ORDER_ID, PARKS, PARK_CITIES, SOURCES, childrenAt,
} from '../src/components/wiki/trainers/scenarioCrmTicket.js';
import call from '../src/components/wiki/trainers/scenarioOperatorCall.js';
import {
    CATEGORY_COUNT, LEAVES, categoryChildren, categoryNode, chainIds, leafAt, nodeAtPath, pathLabel,
    resolveChain, searchLeaves,
} from '../src/components/wiki/trainers/crmCatalog.js';
import {
    CRM_MENU, CRM_VIEWS, canSave, crmAccount, crmFormFields, crmMoney, crmResultFields, crmUrl, emptyForm,
    filterMenu, findCrmAccounts, formLeaf, lastSavedForm, normalizePhone, phoneHistory, sameCategoryTickets,
    withCategory,
} from '../src/components/wiki/trainers/crmModel.js';
import {
    YA_DEFAULT_PARK, YA_PARKS, YA_PARKS_CLOSED, YA_SAMPLE_APPEALS, YA_TOPICS, emptyYaForm, topicAt,
    topicInputs, topicLeaves, yaMissing, yaParkFor,
} from '../src/components/wiki/trainers/crmYandex.js';
import { CRM_PEOPLE, DEFAULT_CASE } from '../src/components/wiki/trainers/crmPeople.js';
import { prepareCase } from '../src/components/wiki/trainers/caseData.js';
import attestation, { examSummary } from '../src/components/wiki/trainers/scenarioAttestation.js';
import {
    defaultPeriod, gpsRows, gpsSummary, incomeSummary, orderCard, orderRows, statementRows, statementSummary, workRuleLog,
} from '../src/components/wiki/trainers/crmCard.js';
import { nearestShift, revealInFrame } from '../src/components/wiki/trainers/crmReveal.js';
import { resultExamItems, resultVerdict } from '../src/components/wiki/trainerResult.js';
import { TRAINERS } from '../src/components/wiki/trainers/registry.js';
import { PHONE_HIDDEN_TRAINERS } from '../src/trainers_embed/trainerList.js';

/* Рабочее место оператора — КУРС по ТЗ SZOV-8: три блока подряд (обращения,
 * учётные записи водителей, обращения в Яндекс), в каждом Барс ведёт по
 * шагам, а в конце — практика без подсветки с проверкой и объяснением
 * ошибок. Проверяем движок курса, сами правила проверки и то, что весь курс
 * проходится теми же действиями, какие шлют страницы учебной CRM.
 */

/* Страницы CRM — JSX: собираем их esbuild'ом, как тест экранов. */
async function loadCrm() {
    const dir = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
    mkdirSync(dir, { recursive: true });
    const outfile = join(dir, 'wiki-trainer-crm-pages.mjs');
    buildSync({
        stdin: {
            contents: [
                "export { DeskScreen, makeUi, viewPatch, crmTitle } from './screensCrm.jsx';",
                "export { accountSearchPatch, CODE_CONFIRM } from './crmDrivers.jsx';",
                "export { yaAppealFrom } from './crmYandexAppeals.jsx';",
            ].join('\n'),
            resolveDir: fileURLToPath(new URL('../src/components/wiki/trainers/', import.meta.url)),
            loader: 'jsx',
        },
        bundle: true, format: 'esm', platform: 'node', target: 'node18', outfile,
        packages: 'external', loader: { '.js': 'jsx', '.jsx': 'jsx', '.css': 'empty' },
        logLevel: 'silent',
    });
    return import(pathToFileURL(outfile));
}

const CRM = await loadCrm();

const TODAY = { year: 2026, month: 8, day: 23 };
const NOW = new Date(Date.UTC(2026, 7, 23, 6));

/** Попытка курса на первом шаге рабочего места. */
const atDesk = () => {
    const run = startRun(desk, { now: NOW });
    const next = tap(run, expectedTap(run)).run;
    assert.equal(currentStep(next).key, 'b1_phone');
    return next;
};

const world = () => atDesk().world;

/* Путь конечной категории без тикета и путь той, по которой у номера дела
   открыт тикет. Оба — из настоящего дерева. Форма хранит НОМЕРА узлов. */
const CONSULT = ['Водитель', 'Обычный водитель', 'Консультация', 'Консультация по Комиссии от Таксопарка'];
const UNFREEZE = ['Водитель', 'Обычный водитель', 'Запрос', 'Таксопарк', 'Финансовый отдел', 'Разморозка счета'];
const ids = (titles) => chainIds(nodeAtPath(titles));

/* ── Действия страниц CRM ─────────────────────────────────────────────────
   Те же patch и payload, что шлют crmRequests / crmDrivers / crmYandexAppeals,
   и тот же путь — через ui (screensCrm.makeUi) в runner.uiAction. Одно
   действие — один ui: страница каждый раз рисуется заново с новым миром. */
const via = (run, fn) => {
    let next = run;
    const ui = CRM.makeUi({
        world: run.world,
        onUi: (action) => { next = uiAction(next, action); },
        browse: (patch) => { next = browse(next, patch); },
        emit: null,
        target: null,
        misses: (id) => uiMisses(next, id),
    });
    fn(ui, run.world);
    return next;
};

const PHONE = '+7 701 555 01 42';
const op = {
    phone: (phone) => (run) => via(run, (ui, w) => ui.touch('lr_phone', { form: { ...w.form, phone, linked: '' } })),
    source: (source) => (run) => via(run, (ui, w) => ui.pick('lr_source', { form: { ...w.form, source } })),
    park: (park) => (run) => via(run, (ui, w) => ui.pick('lr_park_city', { form: { ...w.form, park, city: '', promotion: '' } })),
    city: (city) => (run) => via(run, (ui, w) => ui.pick('lr_park_city', { form: { ...w.form, city } })),
    leaf: (id) => (run) => via(run, (ui, w) => ui.pick('lr_leaf', { form: withCategory(w.form, chainIds(categoryNode(id))) })),
    comment: (text) => (run) => via(run, (ui, w) => ui.touch('lr_info', {
        form: { ...w.form, info: { ...(w.form.info || {}), Комментарий: text } },
    })),
    save: () => (run) => via(run, (ui, w) => {
        const count = (w.crmRequests || []).length;
        const saved = { ...w.form, id: 1829800 + count + 1, at: w.now, ticketNo: null };
        ui.save('lr_save', {
            crmRequests: [...(w.crmRequests || []), saved],
            form: emptyForm(w.case?.crm?.prefill),
            crmView: 'requests',
        });
    }),
    link: (ticket) => (run) => via(run, (ui, w) => ui.pick('lr_link', { form: { ...w.form, linked: ticket } })),
    listSearch: (search) => (run) => via(run, (ui) => ui.pick('lr_filter_apply', null, { filter: { search } })),
    rowShow: () => (run) => via(run, (ui, w) => {
        const row = w.crmRequests[w.crmRequests.length - 1];
        ui.go('request_show', { crmShown: { ...row, operator: 'Стажёр', fields: [] } }, 'lr_row_show', { id: row.id, own: true });
    }),
    crumbRequests: () => (run) => via(run, (ui) => ui.go('requests', {}, 'crumb_requests')),
    createRequest: () => (run) => via(run, (ui) => ui.go('request_new', {}, 'lr_create_btn')),
    menu: (key, view) => (run) => via(run, (ui) => ui.menu(key, view)),
    drvSearch: (query, id = 'drv_search') => (run) => via(run, (ui, w) => ui.pick(id, CRM.accountSearchPatch(w, query), { query })),
    /* «Подробнее» в строке учётки — как в crmDrivers.openCard. */
    drvOpen: (phone = null) => (run) => via(run, (ui, w) => {
        const [row] = findCrmAccounts(w, phone || w.case.contractor.phone);
        ui.go('account', {
            drvOpenId: row.accountId, drvOpenNumber: row.id, drvTab: 'general', drvModal: null, drvOrder: null,
            drvOpenedAt: w.now.slice(0, 16),
        }, 'drv_open', { accountId: row.accountId });
    }),
    refresh: () => (run) => via(run, (ui, w) => ui.act('drv_refresh', {
        drvRefreshed: { ...(w.drvRefreshed || {}), [w.drvOpenId]: w.now.slice(0, 16) },
    }, { accountId: w.drvOpenId })),
    order: (number) => (run) => via(run, (ui) => ui.touch('drv_order', { drvModal: 'order-card', drvOrder: number }, { order: number })),
    orderClose: () => (run) => via(run, (ui) => ui.touch('drv_order_close', { drvModal: null, drvOrder: null })),
    tab: (key) => (run) => via(run, (ui) => ui.pick(`drv_tab_${key}`, { drvTab: key }, { tab: key })),
    modal: (id, value) => (run) => via(run, (ui) => ui.touch(id, { drvModal: value })),
    crumbAccounts: () => (run) => via(run, (ui) => ui.go('accounts', { drvModal: null }, 'crumb_accounts')),
    /* «Код» в строке учётки найденного по телефону водителя. */
    code: (phone = null) => (run) => via(run, (ui, w) => {
        const [row] = findCrmAccounts(w, phone || w.case.contractor.phone);
        ui.act('drv_code', { drvCodeSent: [...(w.drvCodeSent || []), { accountId: row.accountId, at: w.now }] },
            { accountId: row.accountId });
    }),
    yaRow: (id) => (run) => via(run, (ui, w) => ui.go('ya_show', {
        yaOpen: id, yaRead: [...new Set([...(w.yaRead || []), id])],
    }, `ya_row_${id}`, { id })),
    yaBack: () => (run) => via(run, (ui) => ui.go('ya_list', {}, 'ya_back')),
    yaCreate: () => (run) => via(run, (ui) => ui.go('ya_new', { yaForm: emptyYaForm() }, 'ya_create_btn')),
    yaPark: (park) => (run) => via(run, (ui, w) => ui.pick('ya_park', {
        yaForm: { ...w.yaForm, park, topics: [], values: {}, files: [] },
    }, { park })),
    yaTopic: (level, title) => (run) => via(run, (ui, w) => {
        const topics = [...w.yaForm.topics.slice(0, level), title];
        ui.pick(`ya_topic_${level}`, { yaForm: { ...w.yaForm, topics, values: {}, files: [] } }, { level, topics });
    }),
    yaField: (name, value) => (run) => via(run, (ui, w) => ui.touch(`ya_field_${name}`, {
        yaForm: { ...w.yaForm, values: { ...w.yaForm.values, [name]: value } },
    }, { name, value })),
    yaFiles: (files) => (run) => via(run, (ui, w) => ui.touch('ya_files', { yaForm: { ...w.yaForm, files } }, { files })),
    yaSend: () => (run) => via(run, (ui, w) => {
        const appeal = CRM.yaAppealFrom(w, w.yaForm);
        ui.act('ya_send', {
            yaAppeals: [...(w.yaAppeals || []), appeal], yaForm: emptyYaForm(), yaOpen: appeal.id, crmView: 'ya_show',
        }, { form: w.yaForm });
    }),
};

const COMMISSION_TEXT = 'Водитель Нурланов Асхат (ВУ AN000000) не согласен с комиссией сервиса 526,67 ₸ и НДС '
    + '84,27 ₸ за заказ 11837902. Просим проверить расчёт комиссии.';
const NO_ORDERS_TEXT = 'Водитель Нурланов Асхат (ВУ AN000000) второй день на линии, но ему не поступают заказы: '
    + 'рейтинг в норме, фотоконтроль пройден. Просим проверить распределение заказов.';

/* Правильный путь курса: что делает человек на каждом шаге. Практика — тот же
   путь, только целиком своими руками, без подсветки. */
const PATH = {
    intro: [(run) => tap(run, 'begin').run],
    b1_phone: [op.phone(PHONE)],
    b1_park: [op.park('Jana такси')],
    b1_city: [op.city('Алматы')],
    b1_category: [op.leaf(CAT_COMMISSION)],
    b1_comment: [op.comment('Спросил про комиссию за заказ 11837902 — объяснил комиссию сервиса и НДС.')],
    b1_save: [op.save()],
    b1_list_search: [op.listSearch('77015550142')],
    b1_row_show: [op.rowShow()],
    b1_practice: [
        op.crumbRequests(), op.createRequest(), op.source('Whatsapp'), op.phone('87015550142'), op.park('Jana такси'),
        op.city('Алматы'), op.leaf(CAT_WITHDRAW), op.comment('Спросил, как вывести деньги на карту, — объяснил вывод в Яндекс Про.'),
        op.save(),
    ],
    b2_menu: [op.menu('accounts', 'accounts')],
    b2_search: [op.drvSearch('77015550142')],
    b2_open: [op.drvOpen()],
    b2_refresh: [op.refresh()],
    b2_statement: [op.tab('statement')],
    b2_order: [op.order(ORDER_ID)],
    b2_order_close: [op.orderClose()],
    b2_general: [op.tab('general')],
    b2_car: [op.modal('drv_car_more', 'car-details')],
    b2_car_back: [op.modal('drv_car_back', null)],
    b2_workrule: [op.modal('drv_workrule', 'work-rule')],
    b2_workrule_close: [op.modal('drv_workrule_close', null)],
    b2_back_list: [op.crumbAccounts()],
    b2_history: [op.drvSearch('77015550142', 'drv_history')],
    b2_code: [op.code()],
    b2_practice_search: [op.drvSearch('000 XXX 02')],
    b2_practice_open: [op.drvOpen()],
    b2_practice_statement: [op.tab('statement')],
    b3_menu: [op.menu('ya_list', 'ya_list')],
    b3_unread: [op.yaRow(14806)],
    b3_back: [op.yaBack()],
    b3_new: [op.yaCreate()],
    b3_park: [op.yaPark('Jana (Алматы)')],
    b3_topic: [op.yaTopic(0, 'Вопрос по заказам и бонусам')],
    b3_subtopic: [op.yaTopic(1, 'Вопрос про комиссию')],
    b3_order: [op.yaField('order_id', ORDER_ID)],
    b3_message: [op.yaField('message', COMMISSION_TEXT)],
    b3_files: [op.yaFiles(['ведомость.png'])],
    b3_send: [op.yaSend()],
    b3_practice: [
        op.yaBack(), op.yaCreate(), op.yaPark('Jana (Алматы)'), op.yaTopic(0, 'Вопросы об исполнителе'),
        op.yaTopic(1, 'Не поступают заказы'), op.yaField('driver_license', 'AN000000'),
        op.yaField('message', NO_ORDERS_TEXT), op.yaSend(),
    ],
};

/** Довести попытку до шага key правильным путём. */
const until = (key) => {
    let run = startRun(desk, { now: NOW });
    for (let guard = 0; guard < 80 && currentStep(run).key !== key; guard += 1) {
        for (const action of PATH[currentStep(run).key]) run = action(run);
    }
    assert.equal(currentStep(run).key, key);
    return run;
};

/** Цель на экране — как её считает проигрыватель (TrainerPlayer). */
const targetOf = (run) => {
    const step = currentStep(run);
    return step.hideTarget && run.hintedIndex !== run.index ? null : expectedTap(run);
};

const renderDesk = (run) => renderToStaticMarkup(React.createElement(CRM.DeskScreen, {
    world: run.world,
    tap() {},
    browse() {},
    onUi() {},
    misses: () => false,
    emit: null,
    act() {},
    target: targetOf(run),
}));

test('курс из трёх блоков по ТЗ: обращения, учётные записи, обращения в Яндекс', () => {
    assert.equal(desk.key, 'crm-ticket-create', 'ключ уехал в статьи, его нельзя менять');
    assert.equal(desk.stage, 'desktop');
    assert.equal(desk.mode, undefined, 'курс — урок, а не свободная среда');
    assert.deepEqual(desk.blocks.map((block) => block.title),
        ['Работа с обращениями', 'Учётные записи водителей', 'Обращения в Яндекс']);
    const total = desk.blocks.reduce((sum, block) => sum + block.checklist.length, 0);
    assert.equal(total, stageCount(desk), 'пункты блоков и пункты шагов разошлись');
    assert.deepEqual(desk.checklist, desk.blocks.flatMap((block) => block.checklist));
    assert.equal(desk.hintLabel, 'Спросить Барса');

    let previous = 0;
    for (const step of desk.steps) {
        assert.ok(step.stage >= previous, `пункты идут не подряд у «${step.key}»`);
        previous = step.stage;
        assert.ok(step.msg && step.goal && step.hint, `у шага «${step.key}» нет реплики, цели или подсказки`);
        assert.ok(PATH[step.key] || step.screen === 'result', `у шага «${step.key}» нет правильного пути в тесте`);
    }
    /* Практика — в каждом блоке и в его конце: подсветки нет, дорогу человек
       ищет сам, но проверка шага есть. */
    desk.blocks.forEach((block, index) => {
        const last = desk.steps.filter((step) => step.screen === 'desk' && currentBlockOf(step.stage) === index);
        const practice = last.filter((step) => step.free && step.hideTarget);
        assert.ok(practice.length >= 1, `в блоке «${block.title}» нет практики`);
        assert.equal(last[last.length - 1].free, true, `блок «${block.title}» заканчивается не практикой`);
        assert.ok(practice.some((step) => step.check), `практика блока «${block.title}» ничего не проверяет`);
    });
});

/* Номер блока по пункту — как runner.blockOfStage, но без попытки. */
function currentBlockOf(stage) {
    let last = 0;
    for (let i = 0; i < desk.blocks.length; i += 1) {
        last += desk.blocks[i].checklist.length;
        if (stage <= last) return i;
    }
    return -1;
}

test('весь курс проходится действиями CRM — без промахов, с подсветкой цели на каждом шаге', () => {
    let run = startRun(desk, { now: NOW });
    let checked = 0;
    for (let guard = 0; guard < 80 && !isFinished(run); guard += 1) {
        const step = currentStep(run);
        if (step.screen === 'desk') {
            const html = renderDesk(run);
            if (step.hideTarget) {
                assert.doesNotMatch(html, /is-target/, `практика «${step.key}» подсвечивает цель до подсказки`);
            } else {
                assert.match(html, /is-target/, `шаг «${step.key}»: цели «${expectedTap(run)}» нет на экране`);
            }
        }
        for (const action of PATH[step.key]) run = action(run);
        assert.equal(run.errors, 0, `шаг «${step.key}»: правильный путь дал промах — ${speech(run).text}`);
        assert.notEqual(currentStep(run).key, step.key, `шаг «${step.key}» не засчитан — ${speech(run).text}`);
        checked += 1;
    }
    assert.ok(isFinished(run), 'курс не дошёл до финала');
    assert.equal(checked, desk.steps.length - 1);

    /* Итог курса — сделанное своими руками: два обращения и два обращения в Яндекс. */
    const fields = Object.fromEntries(desk.result(run.world).fields);
    assert.equal(fields['Звонок/Чат · №1'], 'Звонок');
    assert.equal(fields['Звонок/Чат · №2'], 'Whatsapp');
    assert.match(fields['Категория · №2'], /Консультация по выводу денег с аккаунта$/);
    assert.equal(fields['Обращение в Яндекс · №1'], 'Вопрос по заказам и бонусам › Вопрос про комиссию · Jana (Алматы)');
    assert.equal(fields['Обращение в Яндекс · №2'], 'Вопросы об исполнителе › Не поступают заказы · Jana (Алматы)');
});

test('кнопка, поле и выбор: что считается ходом, а что промахом', () => {
    let run = until('b1_park');

    /* Чужая кнопка в уроке — промах с объяснением, экран не меняется. */
    const wrong = uiAction(run, { kind: 'act', id: 'lr_save', patch: { crmView: 'requests' } });
    assert.equal(wrong.errors, 1);
    assert.equal(wrong.world.crmView, 'request_new', 'промах сменил страницу');
    assert.equal(speech(wrong).tone, 'error');

    /* Поле и вкладка — не промах, даже когда урок ждёт другого. */
    const typed = uiAction(run, { kind: 'touch', id: 'lr_license', patch: { form: { ...run.world.form, license: 'A' } } });
    assert.equal(typed.errors, 0);
    assert.equal(typed.world.form.license, 'A', 'набранное пропало');
    assert.equal(currentStep(typed).key, 'b1_park');

    /* Неверный выбор там, где его ждут, — объяснение; выбранное остаётся на экране. */
    const picked = op.park('Регионы')(run);
    assert.equal(picked.errors, 1);
    assert.equal(picked.world.form.park, 'Регионы');
    assert.equal(currentStep(picked).key, 'b1_park');
    assert.match(speech(picked).text, /Jana такси/);
    run = op.park('Jana такси')(picked);
    assert.equal(currentStep(run).key, 'b1_city');

    /* Набор номера по буквам — не промахи: шаг засчитывается, когда номер целиком. */
    let phone = startRun(desk, { now: NOW });
    phone = tap(phone, 'begin').run;
    for (const partial of ['+7', '+7 701', '+7 701 555']) phone = op.phone(partial)(phone);
    assert.equal(phone.errors, 0);
    assert.equal(currentStep(phone).key, 'b1_phone');
    phone = op.phone(PHONE)(phone);
    assert.equal(currentStep(phone).key, 'b1_park');
});

test('не та вкладка карточки — объяснение, но вкладка открывается', () => {
    const run = until('b2_statement');
    const income = op.tab('income')(run);
    assert.equal(income.world.drvTab, 'income', 'вкладка не открылась');
    assert.equal(income.errors, 1);
    assert.match(speech(income).text, /Ведомост/);
    assert.equal(currentStep(income).key, 'b2_statement');
});

test('практика: цели не видно до «Спросить Барса», ошибки разбираются словами', () => {
    let run = until('b1_practice');
    assert.equal(targetOf(run), null, 'практика подсвечивает цель');
    assert.equal(targetOf(takeHint(run)), 'lr_save', 'подсказка не показала цель');

    /* По дороге человек ходит где хочет — это не промахи. */
    run = op.crumbRequests()(run);
    run = op.createRequest()(run);
    assert.equal(run.errors, 0);
    assert.equal(run.world.crmView, 'request_new');

    /* Источник «Звонок» вместо «Whatsapp» — промах с объяснением. */
    for (const action of [op.phone(PHONE), op.park('Jana такси'), op.city('Алматы'), op.leaf(CAT_WITHDRAW),
        op.comment('Спросил, как вывести деньги на карту, — объяснил.')]) run = action(run);
    let saved = op.save()(run);
    assert.equal(saved.errors, 1);
    assert.match(speech(saved).text, /WhatsApp|Whatsapp/);
    assert.equal(currentStep(saved).key, 'b1_practice', 'неверное обращение засчитано');
    assert.equal((saved.world.crmRequests || []).length, 1, 'неверное обращение сохранилось');

    /* «Ошибка при выводе» вместо консультации — объяснение про категорию. */
    run = op.source('Whatsapp')(run);
    run = op.leaf(181)(run);
    run = op.comment('Спросил, как вывести деньги на карту, — объяснил.')(run);
    saved = op.save()(run);
    assert.match(speech(saved).text, /КАК вывести/);
});

test('обращение в Яндекс: парк, тема и текст проверяются словами', () => {
    let run = until('b3_park');
    const wrongPark = op.yaPark('Jana Taxi (Актобе)')(run);
    assert.equal(wrongPark.errors, 1);
    assert.match(speech(wrongPark).text, /Jana \(Алматы\)/);

    run = until('b3_send');
    const vague = op.yaSend()(op.yaField('message', 'Комиссия большая')(run));
    assert.equal(currentStep(vague).key, 'b3_send', 'отправилось с коротким текстом');
    assert.match(speech(vague).text, /кто водитель, что случилось/);

    const noAsk = op.yaSend()(op.yaField('message', 'Водитель Нурланов Асхат (ВУ AN000000) не согласен с комиссией '
        + 'сервиса за заказ 11837902, считает её завышенной.')(run));
    assert.match(speech(noAsk).text, /чего мы просим/);
});

test('«Повторить блок» — блок с начала, на чистом мире, промахи остаются', () => {
    let run = until('b2_statement');
    run = op.tab('income')(run); // промах
    assert.equal(currentBlock(run), 1);
    const again = restartBlock(run, { now: NOW });
    assert.equal(currentStep(again).key, 'b2_menu');
    assert.equal(again.errors, 1, 'повтор блока стёр промахи попытки');
    assert.equal(again.world.drvSearched, null, 'мир не чистый');
    assert.equal(again.world.crmView, 'request_new');
});

test('«Код» спрашивает подтверждение, только если нажатие не промах', () => {
    const open = until('b2_open');
    assert.equal(uiMisses(open, 'drv_code'), true, 'на шаге «Подробнее» «Код» — ловушка');
    assert.equal(uiMisses(open, 'drv_open'), false);
    const code = until('b2_code');
    assert.equal(uiMisses(code, 'drv_code'), false);
    const practice = until('b2_practice_search');
    assert.equal(uiMisses(practice, 'drv_reset'), false, 'на практике чужая кнопка без ловушки — не промах');
    assert.match(CRM.CODE_CONFIRM, /^Отправить водителю НОВЫЙ код подтверждения по SMS\?/);
});

test('режим «Звонок»: CRM свободная — нажатия в ней не промахи и не ходы', () => {
    let run = startRun(call, { now: NOW });
    run = tap(run, 'begin').run;
    assert.equal(currentStep(run).key, 'shift');
    for (const action of [op.menu('accounts', 'accounts'), op.drvSearch('77015550142'), op.drvOpen(),
        op.tab('statement'), (r) => uiAction(r, { kind: 'act', id: 'browser_back' })]) run = action(run);
    assert.equal(run.errors, 0);
    assert.equal(currentStep(run).key, 'shift');
    assert.equal(run.world.drvTab, 'statement', 'CRM в смене не работает');
});

test('в окне одна вкладка — CRM, и открывается она на «Создать обращение»', () => {
    const w = world();
    assert.equal(w.tab, 'crm');
    assert.equal(w.crmView, 'request_new');
    assert.equal(w.form.source, 'Звонок', 'CRM открывает форму на «Звонок»');
    assert.equal(w.onLine, false, 'в курсе звонков нет');
    assert.equal(w.call.state, 'offline');
    assert.equal(CRM.crmTitle(w), 'Создать обращение - ITaxi');
    for (const gone of ['fleetView', 'oktLogged', 'oktIn']) assert.equal(w[gone], undefined, `осталось поле ${gone}`);

    const html = renderDesk(atDesk());
    assert.doesNotMatch(html, /Диспетчерская<\/span>|Okapp|Oktell/, 'в окне остались вкладки Диспетчерской или Oktell');
    assert.equal((html.match(/wt-win__tab-name/g) || []).length, 1, 'в окне не одна вкладка');
});

test('карточка водителя в CRM — как в рабочей: телефон слитно, условия с комиссией парка', () => {
    const account = crmAccount(prepareCase(DEFAULT_CASE, TODAY));
    assert.equal(account.phoneCrm, '+77015550142');
    assert.equal(account.termsCrm, 'А-5 На карту (парк 4)');
    assert.equal(account.limit, '-600 ₸', 'минус в CRM — дефис');
    assert.equal(account.balanceCrm, '-619 ₸', 'деньги в карточке — целыми тенге');
    assert.match(account.lastTransaction, /^\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}$/);
    assert.equal(crmMoney(''), 'Нет данных');
    assert.equal(crmUrl({ crmView: 'stub', crmStub: 'limit' }), 'backend.yataxi.kz/admin/driver-accounts/50417/limit');
    assert.equal(crmUrl({ crmView: 'request_show', crmShown: { id: 1829801 } }), 'backend.yataxi.kz/admin/list-requests/1829801');
});

test('мир собирается из слепков: герой и остальные водители учебной CRM', () => {
    const w = world();
    assert.equal(w.case.key, DEFAULT_CASE.key);
    assert.equal(w.crmCases.length, 1 + CRM_PEOPLE.length);
    assert.equal(w.crmCases[0], w.case, 'герой — первым, у него учётка 50417');
    assert.ok(w.crmCases.every((item) => item.transactions.every((t) => t.when)), 'даты не подготовлены к показу');

    const other = startRun(desk, {
        now: new Date(Date.UTC(2026, 7, 23, 6)),
        caseData: { ...DEFAULT_CASE, contractor: { ...DEFAULT_CASE.contractor, last: 'Иванов', first: 'Пётр' } },
    });
    assert.equal(other.world.case.contractor.last, 'Иванов');
    assert.equal(findCrmAccounts(other.world, DEFAULT_CASE.contractor.phone)[0].name, 'Иванов Пётр');
});

test('без конечной категории «Сохранить» не нажимается — как в CRM', () => {
    assert.equal(canSave(emptyForm()), false);
    assert.equal(canSave({ ...emptyForm(), cats: ids(['Водитель', 'Обычный водитель']) }), false);
    assert.equal(canSave({ ...emptyForm(), cats: ids(CONSULT) }), true);
    assert.equal(canSave({ ...emptyForm(), cats: [...ids(CONSULT), 999999] }), false, 'хвост неизвестного узла');
});

test('итог — СОХРАНЁННЫЕ обращения, а не очищенная после сохранения форма', () => {
    const saved = { ...emptyForm(), phone: '77015550142', cats: ids(CONSULT), info: { Комментарий: 'первое' } };
    const w = { form: emptyForm(), crmRequests: [saved] };
    assert.equal(lastSavedForm(w), saved);
    assert.equal(lastSavedForm({ form: saved }), saved, 'без сохранённых — форма на экране');
    const fields = Object.fromEntries(call.result({ ...w, call: {} }).fields);
    assert.equal(fields['Комментарий'], 'первое', 'режим смены теряет итог обращения');

    /* Два обращения одного звонка («Дублировать обращение») — в итоге оба. */
    const second = { ...saved, cats: ids(UNFREEZE), info: { Комментарий: 'второе' } };
    const both = Object.fromEntries(crmResultFields({ form: emptyForm(), crmRequests: [saved, second] }));
    assert.equal(both['Комментарий · №1'], 'первое');
    assert.equal(both['Комментарий · №2'], 'второе');
    assert.equal(both['Категория · №2'], UNFREEZE.join(' › '));
});

test('справочники CRM полные и сняты с рабочей формы', () => {
    assert.equal(PARKS.length, 23);
    assert.equal(CATEGORY_COUNT, 407, 'дерево категорий поредело');
    assert.equal(LEAVES.length, 341);
    assert.deepEqual(childrenAt(), ['Посторонний звонок/Чат', 'Тестовый звонок/Чат', 'Сотрудничество',
        'Пассажир', 'Звонок от Яндекса', 'Водитель']);
    assert.deepEqual(SOURCES.slice(0, 2), ['Стажер', 'Звонок']);
    assert.equal(childrenAt(['Водитель', 'Обычный водитель', 'Консультация']).length, 31);
    assert.deepEqual(childrenAt(['нет такой']), [], 'неизвестный путь не должен падать');

    const pairs = Object.values(PARK_CITIES).reduce((sum, list) => sum + list.length, 0);
    assert.equal(pairs, 102);
    for (const node of categoryChildren(0)) assert.equal(node.parent, 0);
});

test('конечная категория решает, что будет в форме', () => {
    const coop = leafAt(['Сотрудничество']);
    assert.ok(coop && coop.ticket && coop.file, '«Сотрудничество» — конечная, с тикетом и файлами');
    assert.deepEqual(coop.fields.map((f) => f.title),
        ['Название компании', 'Номер для обратного звонка', 'Предлагаемая услуга', 'Комментарий']);
    assert.deepEqual(coop.executors, [], 'у «Сотрудничества» отдел не назначен');

    const unfreeze = leafAt(UNFREEZE);
    assert.ok(unfreeze.ticket);
    assert.deepEqual(unfreeze.executors, ['Модератор', 'Аналитик']);
    assert.deepEqual(unfreeze.schedules, { 'Финансовый отдел': '9:00-18:00 (без выходных)' });

    assert.equal(leafAt(['Водитель', 'Обычный водитель']), null, 'неконечная категория — не лист');
    assert.ok(LEAVES.some((leaf) => leaf.promotion), 'нет ни одной категории с акцией');
    assert.equal(pathLabel(nodeAtPath(CONSULT)), CONSULT.join(' › '));
});

test('поиск категории — подстрока во всём пути, как в CRM', () => {
    const hits = searchLeaves('выплат смз');
    assert.ok(hits.length >= 1);
    for (const leaf of hits) assert.ok(pathLabel(leaf).toLowerCase().includes('выплат смз'));
    assert.equal(searchLeaves('ВЫПЛАТ').length, searchLeaves('выплат').length, 'регистр важен');
    assert.equal(searchLeaves('смз выплат').length, 0, 'слова переставлены — CRM такого не найдёт');
    assert.equal(searchLeaves('').length, LEAVES.length);
    assert.ok(searchLeaves('').every((leaf) => leaf.leaf), 'в поиске оказалась неконечная категория');
});

test('история номера: с десяти цифр и только у номера из дела', () => {
    const w = world();
    assert.deepEqual(phoneHistory(w, '7701555'), { requests: [], open_tickets: [] }, 'история раньше десяти цифр');
    for (const phone of ['+7 701 555 01 42', '87015550142', '7015550142']) {
        const history = phoneHistory(w, phone);
        assert.equal(history.requests.length, 1, `номер ${phone} не узнан`);
        assert.equal(history.open_tickets.length, 1);
        assert.match(history.open_tickets[0].when, /^\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}$/);
    }
    assert.deepEqual(phoneHistory(w, '+7 700 000 00 00').requests, [], 'чужой номер получил историю дела');
    assert.equal(normalizePhone('8 701 555 01 42'), '77015550142');

    /* Сохранённое в попытке попадает в историю номера сразу, свежее сверху,
       а его тикет — в открытые со статусом «Новый»: второе обращение того же
       звонка в той же категории получает «Присоединить к тикету». */
    const saved = { ...emptyForm(), phone: '87015550142', cats: ids(UNFREEZE), id: 1790401,
        at: '20.11.2026 12:00:00', ticketNo: 41301 };
    const withOwn = { ...w, crmRequests: [saved] };
    const history = phoneHistory(withOwn, '+7 701 555 01 42');
    assert.equal(history.requests[0].id, 1790401);
    assert.equal(history.requests[0].when, '20.11.2026 12:00');
    assert.deepEqual(history.open_tickets.map((t) => [t.id, t.status]), [[41301, 'Новый'], [41207, 'В работе']]);
    const next = { ...emptyForm(), phone: '+7 701 555 01 42', cats: ids(UNFREEZE) };
    assert.deepEqual(sameCategoryTickets(withOwn, next).map((t) => t.id), [41301, 41207]);
    const linked = { ...saved, ticketNo: 41207, linked: 41207 };
    assert.deepEqual(phoneHistory({ ...w, crmRequests: [linked] }, saved.phone).open_tickets.map((t) => t.id), [41207],
        'присоединённое обращение завело второй тикет');
    const stranger = { ...saved, phone: '+7 700 000 00 09' };
    assert.equal(phoneHistory({ ...w, crmRequests: [stranger] }, '+7 700 000 00 09').requests.length, 1,
        'у чужого номера своё сохранённое тоже видно');
});

test('присоединиться к тикету предлагают только в той же категории', () => {
    const w = world();
    const form = { ...emptyForm(), phone: '+7 701 555 01 42', cats: ids(UNFREEZE) };
    assert.equal(sameCategoryTickets(w, form).length, 1);
    assert.equal(sameCategoryTickets(w, { ...form, cats: ids(CONSULT) }).length, 0);
    assert.equal(sameCategoryTickets(w, { ...form, phone: '+7 700 000 00 00' }).length, 0);

    const fields = Object.fromEntries(crmFormFields({ ...form, linked: 41207 }));
    assert.equal(fields['Тикет'], 'присоединено к №41207');
    assert.equal(Object.fromEntries(crmFormFields(form))['Формировать тикет'], 'да');
    assert.equal(Object.fromEntries(crmFormFields({ ...form, ticket: false }))['Формировать тикет'], 'нет');
});

test('история и тикет дела сдвигаются вместе с датами дела', () => {
    const shifted = prepareCase(DEFAULT_CASE, { year: 2026, month: 11, day: 20 });
    assert.equal(shifted.crm.history.open_tickets[0].when.slice(0, 10), '16.11.2026',
        'тикет за четыре дня до «сегодня» дела уехал не туда');
    assert.equal(prepareCase({ ...DEFAULT_CASE, crm: undefined }, TODAY).crm.history, null);
});

test('учётка водителя находится только по точному совпадению', () => {
    const w = world();
    const c = w.case;
    for (const query of [c.contractor.id, c.contractor.id.toUpperCase(), '+7 701 555 01 42', '87015550142',
        '7015550142', 'an000000', 'AN000000', '000 XXX 02', '000xxx02']) {
        assert.equal(findCrmAccounts(w, query).length, 1, `не нашлось по «${query}»`);
    }
    for (const query of ['7015550', 'Нурланов', 'Асхат', 'AN0000', '000 XXX', '', '   ']) {
        assert.equal(findCrmAccounts(w, query).length, 0, `нашлось по обрывку «${query}»`);
    }
    const [account] = findCrmAccounts(w, '+7 701 555 01 42');
    assert.equal(account.name, 'Нурланов Асхат');
    assert.equal(account.employment, 'Физическое лицо', 'парковый сотрудник в CRM — «Физическое лицо»');
    assert.equal(account.employmentShort, 'Физлицо');
});

test('меню CRM — восемь групп в рабочем порядке, и у каждого экрана есть пункт', () => {
    assert.deepEqual(CRM_MENU.map((g) => g.title), ['Водители', 'Акции', 'Поддержка', 'Купоны', 'Рассылки',
        'Контент', 'Тикетная система', 'ЭДО']);
    const items = new Set(CRM_MENU.flatMap((g) => g.items.map(([key]) => key)));
    for (const [view, item] of Object.entries(CRM_VIEWS)) assert.ok(items.has(item), `у экрана ${view} нет пункта`);
    const views = CRM_MENU.flatMap((g) => g.items).filter(([, , view]) => view).map(([, title]) => title);
    assert.deepEqual(views, ['Учетные записи водителей', 'Обращения в Яндекс', 'Обращения']);

    const found = filterMenu('яндекс');
    assert.deepEqual(found.map((g) => g.title), ['Поддержка']);
    assert.deepEqual(found[0].items.map(([, title]) => title), ['Обращения в Яндекс']);
    assert.equal(filterMenu('эдо')[0].items.length, 6, 'совпала группа — показываются все её пункты');
    assert.deepEqual(filterMenu('нет такого раздела'), []);
});

/* «Поиск раздела» ищет как рабочая CRM: окончания отрезаются, ё = е, другая
   раскладка, ключевые слова и путь страницы. */
test('поиск раздела — по правилам рабочей CRM', () => {
    const titles = (query) => filterMenu(query).flatMap((g) => g.items.map(([, title]) => title));
    assert.deepEqual(titles('обращение'), ['Обращения в Яндекс', 'Обращения'], 'окончание не отрезано');
    assert.ok(titles('водителя').includes('Учетные записи водителей'));
    assert.ok(titles('водителя').includes('ЭДО водителей'));
    assert.deepEqual(titles('учётные'), ['Учетные записи водителей'], '«ё» не приравнена к «е»');
    assert.deepEqual(titles('j,hfotybz'), titles('обращения'), 'другая раскладка не ищется');
    assert.ok(titles('тикеты').includes('Обращения в Яндекс'), 'ключевые слова пункта не ищутся');
    assert.ok(titles('диспетчерская').includes('Обращения в Яндекс'));
    assert.deepEqual(titles('team members'), ['Моя команда'], 'путь страницы не ищется');
    /* «кц» — ключевое слово двух пунктов; и подстрока «акции»: рабочий поиск
       тоже покажет группу «Акции» — подстрока есть подстрока. */
    assert.ok(titles('кц').includes('Офферы по номеру'));
    assert.ok(titles('кц').includes('Проверка ИИН (Сапар)'));
});

test('адрес заглушки — путь рабочей страницы', () => {
    const url = (key) => crmUrl({ crmView: 'stub', crmStub: key });
    assert.equal(url('team'), 'backend.yataxi.kz/admin/driver-accounts/team-members');
    assert.equal(url('registered'), 'backend.yataxi.kz/admin/registered-drivers');
    assert.equal(url('unblock_ip'), 'backend.yataxi.kz/admin/withdraw-blocked-drivers');
    assert.equal(url('proposed_offers'), 'backend.yataxi.kz/admin/proposed-offers/lookup');
    assert.equal(url('sapar_iins'), 'backend.yataxi.kz/admin/sapar-iins/check');
    assert.equal(url('provider_dashboard'), 'backend.yataxi.kz/admin/driver-edo/provider-switch/dashboard');
    for (const group of CRM_MENU) {
        for (const [key, , , path] of group.items) assert.ok(path, `у пункта ${key} нет пути`);
    }
});

/* У родителя бывают одноимённые листья с разными отделами: «Регистрация ›
   Перерегистрация» — 41 (Отдел продаж) и 442 (Отдел обработки запросов).
   Форма хранит номера узлов, поэтому выбор второго не открывает первый. */
test('одноимённые листья одного родителя не путаются', () => {
    const [first, second] = [categoryNode(41), categoryNode(442)];
    assert.equal(first.title, second.title);
    assert.equal(first.parent, second.parent);
    assert.equal(formLeaf({ cats: chainIds(second) }).id, 442);
    assert.deepEqual(formLeaf({ cats: chainIds(second) }).executors, ['Отдел обработки запросов']);
    assert.equal(formLeaf({ cats: chainIds(first) }).id, 41);
    /* Старые слепки с названиями понимаются — по первому совпадению. */
    assert.equal(resolveChain(CONSULT).length, CONSULT.length);
    assert.equal(formLeaf({ cats: CONSULT }).id, nodeAtPath(CONSULT).id);
});

test('смена категории сбрасывает поля и тикет, акцию — только если поле «Акция» исчезает', () => {
    const promoLeaves = LEAVES.filter((leaf) => leaf.promotion);
    const [a, b] = promoLeaves;
    const base = { ...emptyForm(), cats: chainIds(a), promotion: 'Бонус за первые заказы',
        info: { Комментарий: 'x' }, linked: 41207 };
    const moved = withCategory(base, chainIds(b));
    assert.equal(moved.promotion, 'Бонус за первые заказы', 'из листа с акцией в лист с акцией — акция осталась');
    assert.deepEqual(moved.info, {});
    assert.equal(moved.linked, '');
    assert.equal(withCategory(base, ids(CONSULT)).promotion, '', 'у консультации поля «Акция» нет');
    assert.equal(withCategory(base, chainIds(a).slice(0, 2)).promotion, '', 'неконечный узел — акции нет');
});

test('темы обращения в Яндекс — дерево Диспетчерской целиком', () => {
    let simple = 0;
    let nested = 0;
    const walk = (nodes) => nodes.forEach((node) => {
        if (node.type === 'nested') { nested += 1; walk(node.topics); } else simple += 1;
    });
    walk(YA_TOPICS);
    assert.equal(YA_TOPICS.length, 10);
    assert.equal(simple, 62);
    assert.equal(nested, 21);
    assert.equal(YA_PARKS.length, 90);
    assert.deepEqual(YA_PARKS_CLOSED, ['iTaxi (Жанаозен) — нет доступа к обращениям']);
    for (const appeal of YA_SAMPLE_APPEALS) assert.ok(topicAt(appeal.topic), `тема «${appeal.topic}» не из дерева`);
});

test('«Отправить» в Яндекс — только с конечной темой и полями со звёздочкой', () => {
    const form = emptyYaForm();
    assert.equal(form.park, '2донгелек (Алматы)', 'в рабочей форме парк выбран всегда — первый');
    assert.equal(YA_DEFAULT_PARK, '2донгелек (Алматы)');
    assert.deepEqual(yaMissing({ ...form, park: '' }), ['Таксопарк']);
    const parked = { ...form, park: 'Jana (Алматы)' };
    assert.deepEqual(yaMissing(parked), ['Тема']);
    assert.deepEqual(yaMissing({ ...parked, topics: ['Вопросы об исполнителе'] }), ['Тема'], 'раздел — не конечная тема');

    const hiring = { ...parked, topics: ['Вопросы об исполнителе', 'Платный найм'] };
    assert.deepEqual(yaMissing(hiring), ['Номер в/у', 'Текст обращения', 'Файлы']);
    assert.deepEqual(yaMissing({ ...hiring, values: { driver_license: 'X', message: 'y' }, files: ['a.png'] }), []);

    const period = { ...parked, topics: ['Вопрос по заказам и бонусам', 'Вопрос про бонус', 'Бонус водителю за период'],
        values: { driver_license: 'X', message: 'y', period: ['2026-11-01', ''] } };
    assert.deepEqual(yaMissing(period), ['Период, за который имеются вопросы по начислению бонусов'],
        'период засчитан с одной датой');
    assert.deepEqual(yaMissing({ ...period, values: { ...period.values, period: ['2026-11-01', '2026-11-07'] } }), []);

    const kinds = topicInputs(topicAt(['Вопросы об исполнителе', 'Не поступают заказы'])).map((i) => i.kind);
    assert.deepEqual(kinds, ['label', 'text', 'textarea', 'file']);
});

test('из карточки водителя форма Яндекса открывается на его парке', () => {
    assert.equal(yaParkFor('Jana такси', 'Алматы'), 'Jana (Алматы)');
    assert.equal(yaParkFor('Jana такси', 'Актобе'), 'Jana Taxi (Актобе)');
    assert.equal(yaParkFor('Такси 24', 'Астана'), 'Такси 24 (Астана)');
    assert.equal(yaParkFor('Департамент', 'Алматы'), 'ДепТакси (Алматы)');
    assert.equal(yaParkFor('Аманат', 'Актобе'), 'Amanat (Актобе)');
    assert.equal(yaParkFor('Аманат', 'Караганда'), 'Аманат (Караганда)');
    assert.equal(yaParkFor('Тенге Такси', 'Астана'), 'Tenge (Астана)');
    assert.equal(yaParkFor('iTaxi', 'Жанаозен'), '', 'парк без доступа к обращениям подставлен');
    assert.equal(yaParkFor('Регионы', 'Экибастуз'), '', 'кабинета нет — пусто');
    assert.equal(yaParkFor('', 'Алматы'), '');
    assert.equal(emptyYaForm(yaParkFor('Регионы', 'Экибастуз')).park, YA_DEFAULT_PARK, 'без кабинета — парк по умолчанию');
});

test('фильтр «Подтема» — конечные темы раздела любой глубины', () => {
    assert.deepEqual(topicLeaves('Вопрос по заказам и бонусам'), ['Бонус водителю за период', 'Бонус за конкретный заказ',
        'Вопрос по оплате заказа', 'Вопрос про комиссию', 'Жалоба на пользователя', 'Забытые вещи',
        'Помощь с завершением заказа по тарифам Доставка/Курьер/Грузовой']);
    assert.ok(!topicLeaves('Вопрос по заказам и бонусам').includes('Вопрос про бонус'), 'в подтемах раздел, а не тема');
    assert.deepEqual(topicLeaves('Моментальные выплаты в Яндекс Про'), [], 'у конечной темы первого уровня подтем нет');
});

/* Однофамильцы и номер, отличный на одну цифру: найти водителя можно только
   по ТОЧНОМУ номеру из звонка — «первая строка» здесь была бы не тем. */
test('в учебной CRM есть однофамилец и почти такой же номер — находится только точный', () => {
    const w = world();
    const surnames = w.crmCases.filter((item) => item.contractor.last === 'Нурланов');
    assert.equal(surnames.length, 2, 'однофамилец пропал');
    assert.equal(findCrmAccounts(w, '+7 701 555 01 42')[0].name, 'Нурланов Асхат');
    assert.equal(findCrmAccounts(w, '+7 701 555 01 43')[0].name, 'Серикова Динара', 'номер на одну цифру — другой водитель');
    assert.equal(findCrmAccounts(w, 'Нурланов').length, 0, 'по фамилии CRM не ищет');
    for (const item of w.crmCases) {
        const person = item.contractor;
        for (const query of [person.id, person.phone, person.license, item.car?.plate].filter(Boolean)) {
            const found = findCrmAccounts(w, query);
            assert.equal(found.length, 1, `«${query}» дал ${found.length} учёток`);
            assert.equal(found[0].accountId, person.id, `«${query}» нашёл не того`);
        }
    }
});

test('в ведомости различимы комиссии сервиса и таксопарка', () => {
    const park = DEFAULT_CASE.transactions.filter((t) => t.park);
    const service = DEFAULT_CASE.transactions.filter(
        (t) => !t.park && t.category.includes('Комиссия сервиса'));
    assert.ok(park.length >= 2 && service.length >= 2);
    assert.ok(park.every((t) => t.category.includes('партнёра')));
});

/* Слепок не должен содержать разгадки: любое поле вида «правильно/ожидается/
   подсказка» читается из devtools за пять секунд, и тренажёр перестаёт быть
   тренажёром. */
test('в слепке нет ответа и подсказок', () => {
    const text = JSON.stringify([DEFAULT_CASE, ...CRM_PEOPLE]);
    for (const banned of ['answer', 'expected', 'correct', 'hint', 'checks', 'solution']) {
        assert.ok(!new RegExp(`"${banned}"`).test(text),
            `в слепке есть поле «${banned}» — это ответ, он остаётся на сервере`);
    }
});


/* ── Учебные данные CRM ────────────────────────────────────────────────── */

/* Баланс в ведомости — остаток ПОСЛЕ операции, строки от свежих к старым:
   balance[i] = balance[i+1] + amount[i]. Сломанная колонка учит вранью. */
test('ведомость каждого водителя сходится: баланс — остаток после операции', () => {
    for (const item of [DEFAULT_CASE, ...CRM_PEOPLE]) {
        const rows = item.transactions;
        if (!rows.length) continue;
        assert.equal(rows[0].balance, item.contractor.balance, `${item.key}: текущий баланс не равен последней строке`);
        for (let i = 0; i < rows.length - 1; i += 1) {
            const expected = Math.round((rows[i + 1].balance + rows[i].amount) * 100) / 100;
            assert.equal(rows[i].balance, expected, `${item.key}: строка ${i + 1} не сходится`);
        }
        for (const row of rows.filter((t) => t.order)) {
            assert.ok(item.orders.some((o) => o.number === row.order), `${item.key}: операция по заказу ${row.order} без заказа`);
        }
    }
});

test('комиссия парка — процент условий работы водителя', () => {
    for (const item of [DEFAULT_CASE, ...CRM_PEOPLE]) {
        for (const order of item.orders.filter((o) => o.status === 'complete')) {
            const park = item.transactions.filter((t) => t.order === order.number && t.park);
            for (const row of park) {
                const expected = Math.round(order.price * item.contractor.park_commission) / 100;
                assert.equal(-row.amount, expected, `${item.key}: комиссия парка за ${order.number} не ${item.contractor.park_commission} %`);
            }
        }
    }
});

test('вкладки карточки: период, ведомость, карточка заказа, GPS, заработок', () => {
    const w = world();
    const hero = w.case;
    const period = defaultPeriod(w.nowMs);
    const rows = statementRows(hero, period);
    assert.deepEqual(rows.map((r) => r.category), ['Комиссия сервиса за заказ', 'Комиссия сервиса, НДС',
        'Комиссия партнёра за заказ'], 'по умолчанию — вчера и сегодня: только вчерашний заказ');
    const summary = statementSummary(rows);
    assert.equal(summary.after, -618.95);
    assert.equal(summary.change, -738.54);
    const wide = statementRows(hero, { ...period, from: '2026-01-01' });
    assert.equal(wide.length, hero.transactions.length, 'широкий период показывает всё');
    assert.equal(statementRows(hero, period, { withoutCash: true }).length, 3);

    assert.equal(orderRows(hero, period).length, 1);
    const card = orderCard(hero, ORDER_ID);
    assert.equal(card.money.driver_income.replace(/\s/g, ' '), '2 451,46 ₸', 'доход водителя — цена минус удержания');
    assert.equal(card.money.payment_method, 'Наличные');
    assert.equal(orderCard(hero, 'нет такого'), null);

    const gps = gpsRows(hero, period);
    assert.ok(gps.some((row) => row.order && row.order.number === ORDER_ID), 'событие «На заказе» без ссылки на заказ');
    assert.equal(gpsSummary(gps)[1][1], 15.9, 'пробег на заказе');
    assert.deepEqual(gpsRows(hero, period, true).map((row) => row.type), ['logout', 'login']);

    const income = incomeSummary(hero, period);
    assert.equal(income.completed, 1);
    assert.equal(income.price, 3190);
    assert.equal(income.total, Math.round((3190 - 738.54) * 100) / 100, 'итог — наличные минус комиссии');
    assert.deepEqual(workRuleLog(hero).map((row) => row.to), ['А-5 На карту'], 'смена условий пропала из журнала');
});

test('у каждого учебного водителя своя ситуация видна в его карточке', () => {
    const w = world();
    const byPhone = (phone) => w.crmCases.find((item) => item.contractor.phone === phone);
    const kim = crmAccount(byPhone('77080000044'));
    assert.ok(kim.balanceValue < kim.limitValue, 'у Кима баланс не ниже лимита');
    assert.equal(crmAccount(byPhone('77025550101')).employmentShort, 'СМЗ', 'Ерлан не самозанятый');
    assert.equal(byPhone('77070000033').crm.history.open_tickets[0].id, 41255);
    assert.equal(byPhone('77020000022').photo_days[0].ok, false, 'у Абдуллаева фотоконтроль пройден');
    assert.equal(crmAccount(byPhone('77000000055')).workStatus, 'Уволен');
    const daniyar = byPhone('77050000011');
    assert.equal(orderRows(daniyar, defaultPeriod(w.nowMs)).length, 0, 'у Данияра есть вчерашние заказы');
    assert.ok(gpsRows(daniyar, defaultPeriod(w.nowMs)).some((row) => row.type === 'free' && row.sec > 8 * 3600));
});

/* ── Аттестация ─────────────────────────────────────────────────────────── */

const EXAM_PATH = {
    intro: [(run) => tap(run, 'begin').run],
    withdraw: [op.phone('+7 701 555 01 43'), op.park('Регионы'), op.city('Экибастуз'), op.leaf(32),
        op.comment('Спросила, как вывести деньги на карту, — объяснил вывод.'), op.save()],
    balance: [op.createRequest(), op.source('Whatsapp'), op.phone('+7 708 000 00 44'), op.park('Тенге Такси'),
        op.city('Астана'), op.leaf(31), op.comment('Баланс ниже лимита — объяснил, как пополнить баланс.'), op.save()],
    code: [op.menu('accounts', 'accounts'), op.drvSearch('77025550101'), op.code('77025550101')],
    code_request: [op.createRequest(), op.phone('77025550101'), op.park('Jana такси'), op.city('Алматы'), op.leaf(366),
        op.comment('Не приходит SMS-код — отправили новый код.'), op.save()],
    frozen: [op.createRequest(), op.phone('77070000033'), op.park('Jana такси'), op.city('Алматы'), op.leaf(95),
        op.link(41255), op.comment('Счёт заморожен — присоединил к открытому тикету.'), op.save()],
    orders: [op.menu('ya_list', 'ya_list'), op.yaCreate(), op.yaPark('Такси 24 (Астана)'),
        op.yaTopic(0, 'Вопросы об исполнителе'), op.yaTopic(1, 'Не поступают заказы'), op.yaField('driver_license', 'BQ100011'),
        op.yaField('message', 'Водитель Сериков Данияр (ВУ BQ100011) вчера весь день был на линии, но ему не поступают '
            + 'заказы: рейтинг 4,95, фотоконтроль пройден. Просим проверить распределение заказов.'), op.yaSend()],
};

const playExam = (path) => {
    let run = startRun(attestation, { now: NOW });
    for (let guard = 0; guard < 20 && !isFinished(run); guard += 1) {
        const key = currentStep(run).key;
        for (const action of path[key]) run = action(run);
        assert.notEqual(currentStep(run).key, key, `аттестация застряла на «${key}» — ${speech(run).text}`);
    }
    return run;
};

test('аттестация: пять ситуаций без подсказок, верный путь — 5 из 5', () => {
    assert.equal(attestation.key, 'crm-attestation');
    assert.equal(attestation.noHints, true);
    assert.ok(TRAINERS.includes(attestation), 'аттестации нет в списке тренажёров');
    assert.ok(PHONE_HIDDEN_TRAINERS.has('crm-attestation'), 'аттестация показана в телефоне');
    for (const step of attestation.steps.filter((item) => item.screen === 'desk')) {
        assert.ok(step.exam && step.free && step.hideTarget && step.check, `ситуация «${step.key}» — не экзамен`);
    }
    const run = playExam(EXAM_PATH);
    assert.equal(run.errors, 0, 'верный путь дал ошибки');
    const summary = examSummary(run.world);
    assert.equal(summary.correct, 5);
    assert.ok(summary.items.every((item) => item.ok));
    const result = attestation.result(run.world);
    assert.equal(result.exam.correct, 5);
    assert.match(result.title, /5 из 5/);
});

test('аттестация: ошибка не останавливает экзамен, а попадает в разбор', () => {
    const wrong = {
        ...EXAM_PATH,
        // Ветка «Самозанятый» вместо физлица.
        withdraw: [op.phone('+7 701 555 01 43'), op.park('Регионы'), op.city('Экибастуз'), op.leaf(234),
            op.comment('Спросила, как вывести деньги на карту.'), op.save()],
        // «Нет заказов» без взгляда в карточку: баланс ниже лимита.
        balance: [op.createRequest(), op.source('Whatsapp'), op.phone('+7 708 000 00 44'), op.park('Тенге Такси'),
            op.city('Астана'), op.leaf(60), op.comment('Водитель жалуется: нет заказов, передали в Яндекс.'), op.save()],
        // Код однофамильцу.
        code: [op.menu('accounts', 'accounts'), op.drvSearch('77015550142'), op.code('77015550142')],
        // Второй тикет вместо присоединения к открытому.
        frozen: [op.createRequest(), op.phone('77070000033'), op.park('Jana такси'), op.city('Алматы'), op.leaf(95),
            op.comment('Счёт заморожен — завели тикет в финотдел.'), op.save()],
        // Обращение от чужого кабинета: парк по умолчанию.
        orders: [op.menu('ya_list', 'ya_list'), op.yaCreate(), op.yaTopic(0, 'Вопросы об исполнителе'),
            op.yaTopic(1, 'Не поступают заказы'), op.yaField('driver_license', 'BQ100011'),
            op.yaField('message', 'Водитель Сериков Данияр (ВУ BQ100011) вчера весь день был на линии без заказов. '
                + 'Просим проверить распределение заказов.'), op.yaSend()],
    };
    const run = playExam(wrong);
    const summary = examSummary(run.world);
    assert.equal(summary.correct, 0);
    assert.equal(run.errors, 5, 'каждый неверный ответ — одна ошибка');
    const problems = Object.fromEntries(summary.items.map((item) => [item.key, item.problems.join(' ')]));
    assert.match(problems.withdraw, /Обычный водитель/);
    assert.match(problems.balance, /ниже лимита/);
    assert.match(problems.code, /однофамилец/);
    assert.match(problems.frozen, /41255/);
    assert.match(problems.orders, /Такси 24 \(Астана\)/);
    assert.equal(run.world.crmRequests.length, 4, 'неверные обращения тоже сохранены — как на смене');

    /* Итог для учёта: разбор целиком, обращения — строкой на каждое. Сервер
       хранит итог до 4000 знаков вместе с лентой действий (до 60 кодов), и
       балл не должен вылетать оттуда из-за подробностей. */
    const result = attestation.result(run.world);
    assert.equal(result.exam.correct, 0);
    assert.deepEqual(result.fields.filter(([label]) => /^Обращение №/.test(label)).map(([label]) => label),
        ['Обращение №1', 'Обращение №2', 'Обращение №3', 'Обращение №4']);
    assert.match(result.fields[0][1], /Регионы · Экибастуз/);
    const withEvents = { ...result, events_total: 60, events: Array(60).fill('crm.driver_search') };
    assert.ok(JSON.stringify(withEvents).length < 3500, `итог аттестации раздулся: ${JSON.stringify(withEvents).length}`);
});

test('аттестация: кнопки, которые меняют данные водителя, — ошибка без перехода', () => {
    let run = startRun(attestation, { now: NOW });
    run = tap(run, 'begin').run;
    run = op.menu('accounts', 'accounts')(run);
    run = op.drvSearch('77015550143')(run);
    assert.equal(run.errors, 0, 'поиск на аттестации — не ошибка');
    const smz = uiAction(run, { kind: 'act', id: 'drv_smz', patch: { crmView: 'stub', crmStub: 'smz' } });
    assert.equal(smz.errors, 1);
    assert.equal(smz.world.crmView, 'accounts', 'страница перевода в СМЗ открылась');
    assert.match(speech(smz).text, /ошибка/);
    assert.equal(currentStep(smz).key, 'withdraw');
});


/* Макет документа CRM для прокрутки к цели: прямоугольники заданы руками,
   прокрутки пишутся в журнал. Окно-«фрейм» 1240×937, шапка CRM — 55 px. */
const frameDoc = () => {
    const calls = [];
    const node = (name, rect, style = {}, size = {}) => ({
        name,
        style: { overflowX: 'visible', overflowY: 'visible', position: 'static', ...style },
        parentElement: null,
        clientTop: 0,
        clientLeft: 0,
        scrollHeight: 0,
        clientHeight: 0,
        scrollWidth: 0,
        clientWidth: 0,
        ...size,
        getBoundingClientRect: () => rect,
        getClientRects: () => [rect],
        scrollBy: ({ top, left }) => calls.push([name, top, left]),
    });
    const rect = (top, bottom, left = 300, right = 400) => ({ top, bottom, left, right });
    const body = node('body', rect(0, 2000, 0, 1240));
    const header = node('header', rect(0, 55, 0, 1240), { position: 'fixed' });
    const win = {
        innerHeight: 937,
        innerWidth: 1240,
        getComputedStyle: (item) => item.style,
        scrollBy: ({ top, left }) => calls.push(['window', top, left]),
    };
    const doc = {
        defaultView: win, body, documentElement: { name: 'html' },
        querySelector: (sel) => (sel === '.app-header' ? header : null),
    };
    const chain = (...items) => {
        items.forEach((item, index) => { item.parentElement = items[index + 1] || body; });
        return items[0];
    };
    return { calls, node, rect, doc, chain };
};

test('прокрутка к цели — только внутри CRM: окно заказа, шапка, таблица вбок', () => {
    assert.equal(nearestShift(10, 20, 0, 100), 0, 'видно — не крутим');
    assert.equal(nearestShift(-30, -10, 0, 100), -30, 'выше края — к верху');
    assert.equal(nearestShift(90, 130, 0, 100), 30, 'ниже края — к низу');
    assert.equal(nearestShift(50, 300, 0, 100), 50, 'выше окна — началом к краю, не концом');

    /* Крестик окна «Заказ …» над краем прокручиваемого слоя окна: крутится
       слой, и только он — закреплённый слой прокруткой страницы не двигается,
       а наружу фрейма прокрутка не уходит вовсе (там нет даже вызова). */
    {
        const { calls, node, rect, doc, chain } = frameDoc();
        const overlay = node('overlay', rect(0, 937, 0, 1240), { overflowY: 'auto', position: 'fixed' },
            { scrollHeight: 1115, clientHeight: 937 });
        const target = chain(node('close', rect(-120, -90)), node('modal', rect(-140, 970, 100, 1140)), overlay);
        revealInFrame(doc, target, 'auto');
        assert.deepEqual(calls, [['overlay', -120, 0]]);
    }

    /* Цель под закреплённой шапкой CRM — не видна: страница крутится до низа
       шапки. Цель ниже окна — до низа окна. */
    {
        const { calls, node, rect, doc, chain } = frameDoc();
        revealInFrame(doc, chain(node('link', rect(30, 60))), 'auto');
        assert.deepEqual(calls, [['window', -25, 0]]);
    }
    {
        const { calls, node, rect, doc, chain } = frameDoc();
        revealInFrame(doc, chain(node('save', rect(1000, 1030))), 'auto');
        assert.deepEqual(calls, [['window', 93, 0]]);
    }

    /* Таблица учёток шире экрана: сначала вбок крутится она сама; страница
       видит цель уже там, куда таблица её привезёт, и вбок не едет. */
    {
        const { calls, node, rect, doc, chain } = frameDoc();
        const table = node('table', rect(200, 700, 200, 1200), { overflowX: 'auto' },
            { scrollWidth: 2400, clientWidth: 1000, scrollHeight: 500, clientHeight: 500 });
        revealInFrame(doc, chain(node('code', rect(400, 430, 1300, 1400)), table), 'auto');
        assert.deepEqual(calls, [['table', 0, 200]]);
    }

    /* Цели нет на экране (скрыта) — ничего не крутим. */
    {
        const { calls, node, rect, doc, chain } = frameDoc();
        const hidden = chain(node('hidden', rect(0, 0)));
        hidden.getClientRects = () => [];
        revealInFrame(doc, hidden, 'auto');
        revealInFrame(doc, null, 'auto');
        assert.deepEqual(calls, []);
    }
});

test('статистика: метка итога — балл аттестации, а у курса без вердикта метки нет', () => {
    assert.deepEqual(resultVerdict({ exam: { total: 5, correct: 5 } }), { label: '5 из 5', tone: 'green' });
    assert.deepEqual(resultVerdict({ exam: { total: 5, correct: 3 } }), { label: '3 из 5', tone: 'amber' });
    assert.deepEqual(resultVerdict({ exam: { total: 5, correct: 0 } }), { label: '0 из 5', tone: 'red' });
    assert.deepEqual(resultVerdict({ exam: { total: 5, correct: 9 } }), { label: '5 из 5', tone: 'green' });
    // Бросил на середине: балл не окончательный — серым, а не красным.
    assert.deepEqual(resultVerdict({ exam: { total: 5, correct: 0, items: [{ ok: false, answered: true }, { answered: false }] } }),
        { label: '0 из 5', tone: 'slate' });
    assert.deepEqual(resultVerdict({ title: 'Обращение', correct: true }), { label: 'верно', tone: 'green' });
    assert.deepEqual(resultVerdict({ title: 'Обращение', correct: false }), { label: 'неверно', tone: 'red' });
    /* Курс и свободная среда: правильного ответа нет — и метки нет, а не
       красное «неверно», как было. */
    for (const result of [null, 'x', {}, { title: 'Курс «Рабочее место оператора»', fields: [['А', 'Б']] },
        { exam: { total: 0 } }, { exam: 'x' }, { correct: 1 }]) {
        assert.equal(resultVerdict(result), null, JSON.stringify(result));
    }

    /* Итог настоящей попытки: курс — без метки, аттестация — балл и разбор. */
    let course = startRun(desk, { now: NOW });
    for (const step of desk.steps) for (const action of PATH[step.key] || []) course = action(course);
    assert.ok(isFinished(course));
    assert.equal(resultVerdict(desk.result(course.world)), null);
    const exam = attestation.result(playExam(EXAM_PATH).world);
    assert.deepEqual(resultVerdict(exam), { label: '5 из 5', tone: 'green' });
    const items = resultExamItems(exam);
    assert.equal(items.length, 5);
    assert.ok(items.every((item) => item.ok && item.answered && !item.problems.length));
    assert.deepEqual(resultExamItems({ exam: { items: [null, { key: 'code', ok: false, answered: false }] } }),
        [{ title: 'code', ok: false, answered: false, problems: [] }]);
});
