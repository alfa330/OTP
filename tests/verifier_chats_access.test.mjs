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
 * глобальным админам: саму переписку они читают, а разборы ИИ им не нужны —
 * там оценки операторов чужих отделов и кнопки переоценки. Предикатов стало
 * два, и разъехаться они могут молча: лишний допуск НИКАК не проявляется в
 * интерфейсе того, кто его получил.
 *
 * Поэтому проверяем не текст, а поведение — таблицей по всем ролям сразу. Сами
 * объявления достаём из src/App.jsx: файл монолитный и не импортируется, а
 * переписывать предикат в тест значит проверять копию вместо кода.
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
    'AI_QA_HEAD_DEPARTMENT_CODES',
    'AI_QA_EXTRA_ACCESS_USER_IDS',
    'normalizeDepartmentCode',
    'isOpSalesSupervisorForAiQa',
    'aiQaHeadDepartmentCodesOf',
    'isAiQaDepartmentHead',
    'canAccessAiQaForUser',
    'MARKETING_OBSERVER_DEPARTMENT_CODE',
    'isMarketingObserver',
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
    ['глобальный админ', { id: 2, role: 'admin' }, true, false],
    ['админ, назначенный главой ТЭЗ', {
        id: 3, role: 'admin', headed_department_id: 777, headed_department_codes: ['tez'],
    }, false, false],
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

test('доступ к чатам — надмножество доступа к ИИ-оценке, кроме маркетинга', () => {
    /* Обратное отношение было бы дефектом молчаливым: человек с разборами, но
       без переписки, упирался бы в 403 внутри раздела.

       У рядового маркетолога исключение, и оно проверено по коду, а не принято
       на веру: экран «ИИ-оценки» (src/components/call_qa/CallQaView.jsx) ходит
       ТОЛЬКО в /api/ai-qa/* и ни одной ручки /api/wazzup/* не зовёт — значит
       закрытые чаты Верификаторов его не ломают. Появится в CallQaView запрос к
       /api/wazzup/* — исключение придётся снимать, и упадёт этот тест. */
    const { canAccessAiQaForUser, canAccessVerifierChatsForUser } = predicates();
    for (const [who, user] of PEOPLE) {
        if (who === 'рядовой маркетолог') continue;
        if (canAccessAiQaForUser(user)) {
            assert.ok(canAccessVerifierChatsForUser(user), `у «${who}» есть разборы, но нет чатов`);
        }
    }
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
    const tampered = predicates({
        canAccessAiQaForUser:
            'const canAccessAiQaForUser = (userLike) => '
            + "normalizeRole(userLike?.role) === 'admin' && !isDepartmentHead(userLike);",
    });
    assert.equal(tampered.canAccessAiQaForUser({ id: 2, role: 'admin' }), true,
        'подделка обязана открывать ИИ-оценку админу — иначе тест ничего не сторожит');
});
