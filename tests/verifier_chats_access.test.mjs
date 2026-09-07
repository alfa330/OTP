import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    headedDepartmentId, isDepartmentHead, isSupervisorRole, normalizeRole,
} from '../src/utils/roles.js';

/**
 * Кто попадает в «Чаты Верификаторов», а кто — в «ИИ-оценку».
 *
 * Разделы жили на одном предикате, пока раздел с перепиской не открыли всем
 * глобальным админам. Потом «ИИ-оценка» расширилась с одного отдела продаж на
 * три (СЗоВ и Тез КЦ), и предикаты разошлись во ВТОРУЮ сторону: «Чаты
 * Верификаторов» остались разделом ОТДЕЛА ПРОДАЖ — это переписка Wazzup, у СЗоВ
 * своя в Chat2Desk, у Тез КЦ — раздел «Чаты ChatApp». Вывод одного предиката из
 * другого молча отдал бы переписку Верификаторов главе Тез КЦ и супервайзерам
 * СЗоВ/Тез.
 *
 * Разъехаться они могут молча: лишний допуск НИКАК не проявляется в интерфейсе
 * того, кто его получил. Поэтому проверяем не текст, а поведение — таблицей по
 * всем ролям сразу. Сами объявления достаём из src/App.jsx: файл монолитный и не
 * импортируется, а переписывать предикат в тест значит проверять копию вместо
 * кода.
 */
const source = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8');

/* Объявление `const NAME ...;` целиком: от имени до точки с запятой на нулевой
   глубине скобок. Границу считаем, а не ищем по отступу: тело предиката —
   многострочное, и `};` внутри вложенного блока оборвало бы срез. */
const declarationOf = (name) => {
    const at = source.indexOf(`const ${name} `);
    assert.ok(at >= 0, `объявление ${name} не найдено — проверь тест`);
    let depth = 0;
    for (let i = at; i < source.length; i += 1) {
        const ch = source[i];
        if (ch === '(' || ch === '[' || ch === '{') depth += 1;
        else if (ch === ')' || ch === ']' || ch === '}') depth -= 1;
        else if (ch === ';' && depth === 0) return source.slice(at, i + 1);
    }
    throw new Error(`не нашёл конец объявления ${name} — проверь тест`);
};

const NAMES = [
    'AI_QA_OP_DEPARTMENT_ID',
    // Порядок важен: тело склеивается в этом же порядке, а
    // AI_QA_HEAD_DEPARTMENT_CODES собирается из двух наборов выше.
    'AI_QA_SUBJECT_DEPARTMENT_CODES',
    'AI_QA_OBSERVER_DEPARTMENT_CODES',
    'VERIFIER_CHATS_HEAD_DEPARTMENT_CODES',
    'AI_QA_HEAD_DEPARTMENT_CODES',
    'AI_QA_EXTRA_ACCESS_USER_IDS',
    'normalizeDepartmentCode',
    'isOpSalesSupervisorForAiQa',
    'isAiQaSupervisor',
    'aiQaHeadDepartmentCodesOf',
    'isAiQaDepartmentHead',
    'MARKETING_OBSERVER_DEPARTMENT_CODE',
    'isMarketingObserver',
    'canAccessAiQaForUser',
    'canAccessVerifierChatsForUser',
];

const predicates = (overrides = {}) => {
    const body = NAMES.map((name) => overrides[name] ?? declarationOf(name)).join('\n');
    // Роли берём настоящие, из src/utils/roles.js: уровень 'admin' и признак
    // главы отдела — половина смысла этих предикатов.
    return new Function('deps', `
        const { headedDepartmentId, isDepartmentHead, isSupervisorRole, normalizeRole } = deps;
        ${body}
        return { canAccessAiQaForUser, canAccessVerifierChatsForUser };
    `)({ headedDepartmentId, isDepartmentHead, isSupervisorRole, normalizeRole });
};

const OP_DEPARTMENT_ID = 367;

const PEOPLE = [
    // [кто, пользователь, чаты верификаторов, ИИ-оценка]
    ['супер-админ', { id: 1, role: 'super_admin' }, true, true],
    // Глобальному админу «ИИ-оценку» открыли вместе с селектором отдела: ради
    // него селектор и сделан — он единственный, кто видит все три отдела.
    ['глобальный админ', { id: 2, role: 'admin' }, true, true],
    // Глава Тез КЦ: разборы своего отдела теперь его, а переписка Верификаторов
    // отдела продаж — нет.
    ['админ, назначенный главой ТЭЗ', {
        id: 3, role: 'admin', headed_department_id: 560, headed_department_codes: ['tez'],
    }, false, true],
    ['глава СЗоВ', {
        id: 4, role: 'admin', headed_department_id: 501, headed_department_codes: ['szov'],
    }, true, true],
    ['глава маркетинга', {
        id: 5, role: 'sv', headed_department_id: 888, headed_department_code: 'marketing',
    }, true, true],
    ['глава отдела продаж (по id отдела)', {
        id: 6, role: 'admin', headed_department_id: OP_DEPARTMENT_ID,
    }, true, true],
    ['СВ отдела продаж', { id: 7, role: 'sv', department_id: OP_DEPARTMENT_ID }, true, true],
    // СВ СЗоВ и Тез КЦ: разборы своего отдела — да, переписка Верификаторов — нет.
    ['СВ СЗоВ', { id: 14, role: 'sv', department_code: 'szov' }, false, true],
    ['СВ Тез КЦ', { id: 15, role: 'sv', department_code: 'tez' }, false, true],
    ['СВ чужого отдела', { id: 8, role: 'sv', department_id: 900 }, false, false],
    ['тренер', { id: 9, role: 'trainer' }, false, false],
    ['оператор', { id: 10, role: 'operator' }, false, false],
    ['оператор из whitelist ИИ-оценки', { id: 183, role: 'operator' }, true, true],
    ['бухгалтер', { id: 11, role: 'accounting_manager' }, false, false],
    // ЕДИНСТВЕННЫЙ, у кого разборы есть, а переписки нет: рядовой сотрудник
    // «Маркетинга». Разборы звонков ему выдал владелец (04.09.2026), чаты
    // Верификаторов в его перечень разделов не входят.
    ['рядовой маркетолог', {
        id: 12, role: 'marketing_manager', department_code: 'marketing',
    }, false, true],
    ['маркетолог, переведённый в ОП', {
        id: 13, role: 'marketing_manager', department_code: 'op',
    }, false, false],
    ['никто (нет сессии)', null, false, false],
];

test('чаты верификаторов открыты глобальным админам, ИИ-оценка — нет', () => {
    const { canAccessAiQaForUser, canAccessVerifierChatsForUser } = predicates();
    for (const [who, user, chats, aiQa] of PEOPLE) {
        assert.equal(canAccessVerifierChatsForUser(user), chats, `чаты: ${who}`);
        assert.equal(canAccessAiQaForUser(user), aiQa, `ИИ-оценка: ${who}`);
    }
});

test('разборы без переписки Верификаторов раздел не ломают', () => {
    /* Отношения «надмножество» между разделами БОЛЬШЕ НЕТ: у «ИИ-оценки» три
       отдела, а «Чаты Верификаторов» — раздел отдела продаж. Людей с разборами
       и без этой переписки теперь много (глава и СВ СЗоВ и Тез КЦ, рядовой
       маркетолог), и это нормально, а не дефект.

       Проверять надо другое — что закрытая переписка Верификаторов не ломает сам
       экран «ИИ-оценки». Это проверено по коду: CallQaView ходит ТОЛЬКО в
       /api/ai-qa/* и ни одной ручки /api/wazzup/* не зовёт. Появится там запрос
       к /api/wazzup/* — раздел начнёт отдавать 403 всем, кроме продаж, и этот
       тест упадёт. */
    const view = readFileSync(
        new URL('../src/components/call_qa/CallQaView.jsx', import.meta.url), 'utf8');
    assert.ok(!view.includes('/api/wazzup/'),
        'экран «ИИ-оценки» зовёт ручку Верификаторов — закрытая переписка его сломает');
    for (const name of ['ChatQueue', 'EvaluationsList', 'QaDashboard', 'QueueList',
                        'CriteriaClassification', 'AdjudicationsRag', 'CallReviewCard']) {
        const child = readFileSync(
            new URL(`../src/components/call_qa/${name}.jsx`, import.meta.url), 'utf8');
        assert.ok(!child.includes('/api/wazzup/'),
            `${name} зовёт ручку Верификаторов — закрытая переписка сломает раздел`);
    }
});

test('«Чаты Верификаторов» не выводятся из аудитории «ИИ-оценки»', () => {
    /* Ровно этот вывод и отдал бы переписку отдела продаж главе Тез КЦ и
       супервайзерам СЗоВ/Тез, когда раздел оценки расширили на три отдела. */
    const declaration = declarationOf('canAccessVerifierChatsForUser');
    assert.ok(!declaration.includes('canAccessAiQaForUser('),
        'периметр чатов снова выведен из «ИИ-оценки» — он поедет за ней');
});

test('страж ловит потерю вычета наблюдателя «Маркетинга»', () => {
    // Без подделки «зелено» ничего не значит: таблица прошла бы и на предикате,
    // который отдаёт маркетологу переписку вместе с разборами.
    const tampered = predicates({
        canAccessVerifierChatsForUser:
            'const canAccessVerifierChatsForUser = (userLike) => canAccessAiQaForUser(userLike)'
            + " || (normalizeRole(userLike?.role) === 'admin' && !isDepartmentHead(userLike));",
    });
    const marketer = PEOPLE.find(([who]) => who === 'рядовой маркетолог')[1];
    assert.equal(tampered.canAccessVerifierChatsForUser(marketer), true,
        'подделка обязана открывать чаты маркетологу — иначе тест ничего не сторожит');
});

test('страж ловит потерю проверки «не глава отдела»', () => {
    // Без подделки «зелено» ничего не значит: таблица выше прошла бы и на
    // предикате, который пускает в раздел главу любого отдела.
    const tampered = predicates({
        canAccessVerifierChatsForUser:
            'const canAccessVerifierChatsForUser = (userLike) => canAccessAiQaForUser(userLike)'
            + " || normalizeRole(userLike?.role) === 'admin';",
    });
    const head = PEOPLE.find(([who]) => who === 'админ, назначенный главой ТЭЗ')[1];
    assert.equal(tampered.canAccessVerifierChatsForUser(head), true,
        'подделка обязана открывать раздел главе ТЭЗ — иначе тест ничего не сторожит');
});

test('страж ловит склейку разделов обратно в один предикат', () => {
    // Подделка возвращает вывод одного предиката из другого — тот самый, из-за
    // которого переписка Верификаторов уехала бы главе Тез КЦ.
    const tampered = predicates({
        canAccessVerifierChatsForUser:
            'const canAccessVerifierChatsForUser = (userLike) => '
            + 'canAccessAiQaForUser(userLike);',
    });
    const tezHead = PEOPLE.find(([who]) => who === 'админ, назначенный главой ТЭЗ')[1];
    assert.equal(tampered.canAccessVerifierChatsForUser(tezHead), true,
        'подделка обязана открывать чаты главе ТЭЗ — иначе тест ничего не сторожит');
    const szovSupervisor = PEOPLE.find(([who]) => who === 'СВ СЗоВ')[1];
    assert.equal(tampered.canAccessVerifierChatsForUser(szovSupervisor), true,
        'подделка обязана открывать чаты СВ СЗоВ — иначе тест ничего не сторожит');
});
