/* Аттестация оператора: рабочее место — пять ситуаций с линии, всё в CRM.
 *
 * Курс учит (crm-ticket-create), аттестация проверяет. Подсказок нет, цель не
 * подсвечена, помощник только читает ситуацию. Человек сам находит водителя,
 * смотрит его карточку и делает то, что нужно: обращение, новый код по SMS
 * или обращение в поддержку Яндекса.
 *
 * Попытка на каждую ситуацию ОДНА, как на смене: «Сохранить» или «Отправить»
 * засчитывает ответ — верный или с причиной ошибки (runner, step.exam), — и
 * аттестация идёт дальше. Разбор каждой ситуации человек и наставник видят в
 * конце; в учёт прохождений уходит то же самое (result → exam).
 *
 * Ответ в каждой ситуации виден ТОЛЬКО в CRM, а водитель о главном не скажет:
 * однофамильцы и номер, отличный на одну цифру; «нет заказов», когда баланс
 * ниже лимита; уже открытый тикет; самозанятый вместо физлица.
 *
 * Данные водителей — crmPeople.js (вымышлены). Никуда ничего не уходит.
 */

import { createDeskWorld } from './deskWorld.js';
import { caseByPhone, crmFormLine } from './crmModel.js';
import { yaParkFor } from './crmYandex.js';
import { compact, expectFor, requestProblem, yaFormProblem, yaTextProblem } from './crmChecks.js';

/* Ситуации — по телефону водителя: так их и узнаёт оператор. */
const PHONES = {
    withdraw: '77015550143',
    balance: '77080000044',
    code: '77025550101',
    frozen: '77070000033',
    orders: '77050000011',
};

const person = (world, key) => caseByPhone(world, PHONES[key]);

/* Кнопки, которые меняют данные водителя: в аттестации нажатие — ошибка. */
const DANGER = {
    drv_smz: 'Перевод в самозанятые меняет договор водителя — без его заявки так нельзя. Засчитано как ошибка.',
    drv_individual: 'Перерегистрация в физлицо меняет договор водителя — без заявки так нельзя. Засчитано как ошибка.',
    drv_limit: 'Лимит по счёту водителя без заявки не меняют. Засчитано как ошибка.',
    drv_car: 'Редактор автомобиля сразу правит данные в Яндексе — без заявки так нельзя. Засчитано как ошибка.',
    drv_code: 'Новый код сбрасывает пароль водителя — его отправляют, только когда водитель не может войти. Засчитано как ошибка.',
};
const dangerExcept = (id) => Object.fromEntries(Object.entries(DANGER).filter(([key]) => key !== id));

const NO_HINT = 'Это аттестация — подсказок нет. Найди водителя в «Учетных записях водителей» и смотри его карточку.';

const situation = (key, stage, action, title, msg, check, traps = DANGER) => ({
    key, screen: 'desk', action, stage, free: true, hideTarget: true, exam: true,
    title, msg, goal: title, hint: NO_HINT, check, traps,
});

const steps = [
    {
        key: 'intro', screen: 'intro', action: 'begin', stage: 0,
        msg: 'Аттестация: пять ситуаций с линии, всё — в CRM. Подсказок нет, на каждую ситуацию одна попытка: '
            + '«Сохранить» или «Отправить» засчитывает ответ. Разбор — в конце.',
        goal: 'Начни аттестацию.',
        hint: 'Нажми «Начать аттестацию».',
        traps: {},
    },
    situation('withdraw', 1, 'lr_save', 'Заведи обращение по звонку.',
        'Ситуация 1 из 5. Звонок с номера +7 701 555 01 43: «Как вывести деньги с аккаунта на карту?» '
            + 'Ты объяснил порядок вывода. Заведи обращение по звонку.',
        (world, payload) => requestProblem(payload.form || world.form, expectFor(person(world, 'withdraw'), {
            source: 'Звонок',
            leaf: 32,
            leafProblem: (node) => (/ошибк/i.test(node.title)
                ? 'Ошибки при выводе не было — водитель спросил, КАК вывести. Это «Консультация по выводу денег с аккаунта».'
                : null),
            keyword: /(вывод|вывест|карт)/,
            keywordHint: 'Из комментария не понять, о чём был вопрос: упомяни вывод денег.',
        }))),
    situation('balance', 2, 'lr_save', 'Найди причину и заведи обращение по чату.',
        'Ситуация 2 из 5. Пишет в WhatsApp Ким Виктор, +7 708 000 00 44: «Второй час на линии — ни одного '
            + 'заказа. Что случилось?» Найди причину в CRM, объясни водителю и заведи обращение по чату.',
        (world, payload) => requestProblem(payload.form || world.form, expectFor(person(world, 'balance'), {
            source: 'Whatsapp',
            leaf: 31,
            leafProblem: (node) => (/не поступают заказы/i.test(node.title)
                ? 'Баланс водителя −684 ₸ ниже лимита −50 ₸: заказы не приходят из-за баланса, Яндекс тут ни при чём. '
                    + 'Это «Консультация по пополнению баланса таксометра».'
                : null),
            keyword: /(баланс|лимит|пополн)/,
            keywordHint: 'Из комментария не понять причины: напиши, что баланс ниже лимита и его нужно пополнить.',
        }))),
    situation('code', 3, 'drv_code', 'Отправь водителю новый код.',
        'Ситуация 3 из 5. Звонит Нурланов Ерлан, +7 702 555 01 01: «Не приходит SMS-код, не могу войти в '
            + 'Яндекс Про». Отправь ему новый код.',
        (world, payload) => {
            const right = person(world, 'code');
            if (payload.accountId === right.contractor.id) return null;
            const other = (world.crmCases || []).find((item) => item.contractor.id === payload.accountId);
            return other
                ? `Код ушёл не тому водителю: ${other.contractor.last} ${other.contractor.first} — однофамилец. `
                    + 'Звонил Нурланов Ерлан, +7 702 555 01 01: учётку ищут по номеру звонка.'
                : 'Код ушёл не тому водителю. Учётку ищут по номеру звонка: +7 702 555 01 01.';
        },
        dangerExcept('drv_code')),
    situation('code_request', 3, 'lr_save', 'Заведи обращение по этому звонку.',
        'Код отправлен. Теперь заведи обращение по этому звонку (Нурланов Ерлан, +7 702 555 01 01).',
        (world, payload) => requestProblem(payload.form || world.form, expectFor(person(world, 'code'), {
            source: 'Звонок',
            leaf: 366,
        }))),
    situation('frozen', 4, 'lr_save', 'Заведи обращение по звонку.',
        'Ситуация 4 из 5. Звонит Жумабеков Ерлан, +7 707 000 00 33: «Счёт заморожен — когда разморозят? '
            + 'Деньги не могу вывести». Заведи обращение.',
        (world, payload) => requestProblem(payload.form || world.form, expectFor(person(world, 'frozen'), {
            source: 'Звонок',
            leaf: 95,
            linked: 41255,
        }))),
    situation('orders', 5, 'ya_send', 'Напиши в поддержку Яндекса.',
        'Ситуация 5 из 5. Звонит Сериков Данияр, +7 705 000 00 11: «Вчера весь день был на линии — ни одного '
            + 'заказа. Рейтинг 4,95, фотоконтроль пройден». Проверь его учётку и напиши в поддержку Яндекса.',
        (world, payload) => {
            const item = person(world, 'orders');
            return yaFormProblem(payload.form || world.yaForm, {
                park: yaParkFor(item.park.name, item.park.city),
                topics: ['Вопросы об исполнителе', 'Не поступают заказы'],
                topicHint: 'Заказы не приходят водителю — это вопрос об исполнителе: тема «Вопросы об исполнителе».',
                subtopicHint: 'Подтема — «Не поступают заказы».',
                fields: (values) => {
                    if (compact(values.driver_license) !== compact(item.contractor.license)) {
                        return `«Номер в/у» — номер ВУ этого водителя: ${item.contractor.license} (в его учётке CRM).`;
                    }
                    return yaTextProblem(values.message, {
                        about: /заказ/,
                        aboutHint: 'В тексте не сказано, что случилось: водителю не поступают заказы.',
                    });
                },
            });
        }),
    {
        key: 'done', screen: 'result', action: 'finish', stage: 5,
        msg: 'Аттестация закончена. Разбор каждой ситуации — на экране.',
        goal: 'Готово.',
        hint: 'Готово.',
        traps: {},
    },
];

/* Ситуация засчитана, когда верны все её ответы (у «кода» их два). */
const SITUATIONS = [
    ['withdraw', 'Вопрос о выводе денег', ['withdraw']],
    ['balance', '«Нет заказов» в чате', ['balance']],
    ['code', 'Не приходит SMS-код', ['code', 'code_request']],
    ['frozen', 'Заморожен счёт', ['frozen']],
    ['orders', 'Весь день без заказов', ['orders']],
];

/** Итог аттестации: ситуации с вердиктом и причиной ошибки. */
export const examSummary = (world) => {
    const answers = world?.exam || {};
    const items = SITUATIONS.map(([key, title, parts]) => {
        const problems = parts.map((part) => answers[part]).filter((answer) => answer && !answer.ok)
            .map((answer) => answer.problem);
        const answered = parts.every((part) => answers[part]);
        return { key, title, ok: answered && !problems.length, answered, problems };
    });
    return { total: items.length, correct: items.filter((item) => item.ok).length, items };
};

export default {
    key: 'crm-attestation',
    title: 'Аттестация: рабочее место оператора',
    subtitle: 'Обращения · Учётные записи водителей · Обращения в Яндекс',
    app: 'Рабочее место',
    stage: 'desktop',
    mode: 'exam',
    noHints: true,
    beginLabel: 'Начать аттестацию',
    description: 'Пять ситуаций с линии без подсказок: найти водителя в CRM, разобраться по его карточке и '
        + 'сделать нужное — обращение, новый код по SMS или обращение в поддержку Яндекса. На каждую '
        + 'ситуацию одна попытка, разбор — в конце.',
    dataNote: 'Всё в учебной среде: водители, телефоны и суммы придуманы. Обращения, коды и обращения в '
        + 'Яндекс никуда не уходят и данные водителей не меняют.',
    checklist: SITUATIONS.map(([, title]) => title),
    resultNote: 'Аттестация закончена.',

    createWorld: createDeskWorld,

    vars: () => ({}),

    traps: {
        tab_close: 'Вкладку CRM не закрывают: на смене она открыта всегда.',
        browser_back: '«Назад» браузера уводит со страницы, и несохранённое пропадёт. Ходи по меню и ссылкам CRM.',
        browser_reload: 'Обновление страницы сотрёт несохранённую форму.',
    },
    steps,

    examSummary,

    /* В учёт прохождений — разбор и сделанное: обращения (строкой на каждое),
       обращения в Яндекс и коды. Разбор целиком, обращения — коротко: итог
       хранится до ~4000 знаков, и балл не должен вылетать из-за лишних
       обращений (сервер ужимает с хвоста «что завёл», а не разбор). */
    result: (world) => {
        const summary = examSummary(world);
        const fields = (world.crmRequests || []).map((form, index) => [`Обращение №${index + 1}`, crmFormLine(form)]);
        (world.yaAppeals || []).forEach((appeal, index) => {
            fields.push([`Обращение в Яндекс · №${index + 1}`, `${appeal.topic.join(' › ')} · ${appeal.park}`]);
        });
        (world.drvCodeSent || []).forEach((sent, index) => {
            const item = (world.crmCases || []).find((candidate) => candidate.contractor.id === sent.accountId);
            fields.push([`Код по SMS · №${index + 1}`, item ? `${item.contractor.last} ${item.contractor.first}` : String(sent.accountId)]);
        });
        return {
            title: `Аттестация: верно ${summary.correct} из ${summary.total}`,
            fields,
            exam: summary,
        };
    },
};
