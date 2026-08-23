/* Режим «Звонок»: стажёр принимает входящий от водителя.
 *
 * ОТДЕЛЬНЫЙ КЛЮЧ, а не переделка `crm-ticket-create`: тот уехал в текст
 * опубликованных статей и переименованию не подлежит, а жизнь попытки здесь
 * другая.
 *
 * Порядок смены:
 *   1. Вступление — ОДНА фраза: чья смена и что сейчас будет. Никакой
 *      предыстории водителя: всё, что рассказано заранее, стажёр уже не станет
 *      искать, а искать — и есть задача.
 *   2. Стажёр сам входит в Okapp и встаёт на линию.
 *   3. Через несколько секунд — входящий.
 *   4. Разговор.
 *   5. Постобработка: оформить обращение в CRM.
 *   6. «Завершить попытку».
 *
 * Правды о деле в интерфейсе нет — она разложена по трём системам, и водитель
 * её сам не расскажет. Вердикта «верно/неверно» среда не выносит: судит разбор.
 */

import { createDeskWorld } from './deskWorld.js';
import { talkMs } from './callMachine.js';
import { PARK_CITIES, PARKS, SOURCES, childrenAt } from './crmCatalog.js';

export { PARK_CITIES, PARKS, SOURCES, childrenAt };

const steps = [
    {
        key: 'intro', screen: 'intro', action: 'begin', stage: 0,
        msg: 'Твоя смена на линии водителей. Сейчас поступит звонок — прими его и '
            + 'разберись, с чем человек пришёл.',
        goal: 'Заступить на смену.',
        hint: 'Нажми «Начать урок».',
        traps: {},
    },
    {
        key: 'shift', screen: 'desk', action: 'finish_attempt', stage: 1,
        /* Единственная реплика на всю смену. Она НЕ рассказывает про водителя —
           только про порядок работы; иначе искать в кабинете станет незачем. */
        msg: 'Вкладка Oktell — войди в клиент и встань на линию, иначе звонки не '
            + 'придут. Дальше слушай, ищи в Диспетчерской и заводи обращение в CRM. '
            + 'Когда всё сделано — «Завершить попытку».',
        goal: 'Принять звонок и оформить обращение.',
        hint: 'Начни с вкладки Oktell: «Войти в call-центр».',
        traps: {},
    },
    {
        key: 'done', screen: 'result', action: 'finish', stage: 1,
        msg: 'Смена закончена.',
        goal: 'Готово.',
        hint: 'Готово.',
        traps: {},
    },
];

export default {
    key: 'operator-call',
    title: 'Звонок водителя',
    subtitle: 'Oktell · Диспетчерская · CRM',
    app: 'Рабочее место',
    stage: 'desktop',
    mode: 'call',
    description: 'Смена на линии: встать в call-центр, принять входящий от водителя, '
        + 'найти правду о его деле в Диспетчерской и оформить обращение в CRM. '
        + 'Подсказок нет — что случилось, выясняется разговором и кабинетом.',
    dataNote: 'Данные учебные: водитель, его телефон, номер В/У и суммы придуманы. '
        + 'Обращение никуда не отправляется, звонок не настоящий.',
    checklist: [
        'Oktell — войти в клиент и встать на линию',
        'Звонок — принять, выслушать, при необходимости перевести',
        'Диспетчерская — найти водителя по номеру и проверить, что с ним',
        'CRM — оформить обращение по итогам разговора',
    ],
    resultNote: 'Смена закончена: звонок принят, обращение оформлено.',

    createWorld: createDeskWorld,

    vars: (world) => ({
        phone: world.case.call.phone_pretty || world.case.call.phone,
        park: world.case.park.name,
    }),

    traps: {},
    steps,

    result: (world) => {
        const form = world.form || {};
        const call = world.call || {};
        const fields = [
            ['Звонок/Чат', form.source],
            ['Номер телефона', form.phone],
            ['Номер В/У', form.license],
            ['ID водителя', form.account],
            ['Таксопарк', form.park],
            ['Город', form.city],
            ['Категория', (form.cats || []).filter(Boolean).join(' / ')],
            ['Комментарий', form.comment],
        ].filter(([, value]) => value);

        return {
            title: 'Смена на линии',
            fields,
            /* Итог смены — не только карточка: разбор смотрит и на то, сняли ли
               трубку, сколько шёл разговор и не ушёл ли звонок коллеге. */
            call: {
                answered: Boolean(call.answeredAt),
                duration_ms: talkMs(call),
                transferred_to: call.transferredTo || null,
                reason: call.reason || null,
                saved: Boolean(world.saved),
            },
        };
    },

    recordOnFinishOnly: true,
};
