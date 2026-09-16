/*
 * Список адресатов выдачи доступа к разделу вики.
 *
 * 16.09.2026 форма перестала спрашивать «тип субъекта» и стала сплошным
 * списком с галочками: владелец попросил «добавлять права сразу к группам
 * людей, и по их должности тоже». Всё, что раньше держал двойной селект,
 * теперь держит этот модуль — и ломается он молча: лишний адресат в списке
 * даёт 403 на заполненной форме, потерянный ключ выдаёт доступ не тому.
 *
 * Экран проверить нечем: это модалка с загрузкой по сети, и серверный рендер
 * до списка не доходит. Поэтому логика вынесена сюда (как sectionGrants.js).
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { buildRecipients, recipientKey, grantedKeys, peopleLabel }
    from '../src/components/wiki/accessRecipients.js';

const ROWS = [
    { key: '30|', label: 'Супервайзер', people: 4, min_role_level: 30,
      job_title: null, subject_type: 'department', subject_id: 1, locked: false },
    { key: '40|', label: 'Руководитель группы', people: 6, min_role_level: 40,
      job_title: null, subject_type: 'department', subject_id: 1, locked: true },
    { key: '10|Видеограф', label: 'Видеограф', people: 1, min_role_level: 10,
      job_title: 'Видеограф', subject_type: 'department', subject_id: 1, locked: false },
];

const CATALOG = {
    group: [{ id: 10, name: 'Кастек Гаухар группа Основа', people: 19 }],
    direction: [{ id: 70, name: 'Основа', people: 54 }],
    department: [{ id: 1, name: 'СЗоВ', people: 86 }],
    wiki_role: [{ id: 2, name: 'Редактор', people: null }],
    otp_role: [{ id: 'sv', name: 'Супервайзер' }],
};

const PEOPLE = [{ id: 5, name: 'Иванов А.', role: 'operator', department_name: 'СЗоВ' }];

const build = (extra = {}) => buildRecipients({
    rows: ROWS, catalog: CATALOG, people: PEOPLE, hasBranch: true, ...extra });

const byGroup = (items, groupLabel) => items.filter((i) => i.groupLabel === groupLabel);

test('в списке есть и группы, и должности ветки — ровно об этом была просьба', () => {
    const items = build();
    assert.equal(byGroup(items, 'Группы').length, 1);
    assert.equal(byGroup(items, 'Должности отдела').length, 3);
});

test('люди идут последними — их 174, а групп 12', () => {
    // Стой они посередине, до ролей и отделов пришлось бы прокручивать
    // полторы сотни строк. Поиск в списке есть, но порядок обязан работать и
    // без него.
    const order = [...new Set(build().map((i) => i.groupLabel))];
    assert.equal(order[0], 'Должности отдела');
    assert.equal(order[order.length - 1], 'Люди');
});

test('число людей стоит в подписи и склоняется', () => {
    const items = build();
    assert.match(byGroup(items, 'Группы')[0].label, /19 человек$/);
    assert.match(byGroup(items, 'Должности отдела')[0].label, /4 человека$/);
    assert.equal(peopleLabel(1), '1 человек');
    assert.equal(peopleLabel(22), '22 человека');
    assert.equal(peopleLabel(11), '11 человек');
    assert.equal(peopleLabel(174), '174 человека');
});

test('строка выше потолка показана, но заперта', () => {
    // Спрятанная строка читается как «такой должности не бывает»; запертая
    // объясняет, что выдача есть, но не отсюда.
    const locked = build().find((i) => i.label.startsWith('Руководитель группы'));
    assert.equal(locked.disabled, true);
    assert.match(locked.label, /выдаёт вышестоящий/);
});

test('должность едет в теле правила третьим измерением', () => {
    // Потеряв job_title, выдача «Видеографу» открыла бы раздел всему отделу.
    const row = build().find((i) => i.label.startsWith('Видеограф'));
    assert.deepEqual(row.body, {
        subject_type: 'department', subject_id: 1, subject_role: null,
        min_role_level: 10, job_title: 'Видеограф',
    });
});

test('у отдела и его главы разные ключи и разные подписи', () => {
    const departments = byGroup(build(), 'Отделы');
    assert.deepEqual(departments.map((i) => i.label), ['СЗоВ · 86 человек', 'Глава «СЗоВ»']);
    assert.notEqual(departments[0].key, departments[1].key);
});

test('раздающему с границей отдела роли не предлагаются вовсе', () => {
    // Сервер их отвергает (access.may_grant_to_subject), и строка, на которую
    // приходит отказ, читается как поломка, а не как правило.
    const items = build({ bounded: true });
    assert.equal(items.some((i) => i.body.subject_type === 'otp_role'), false);
    assert.equal(items.some((i) => i.body.subject_type === 'wiki_role'), false);
    // А группы, направления и люди на месте: их сервер присылает уже сужёнными.
    assert.equal(byGroup(items, 'Группы').length, 1);
    assert.equal(byGroup(items, 'Люди').length, 1);
});

test('вне ветки отдела должностей нет — их заменяют роли', () => {
    // Там правило писать не на что, кроме самой роли, и два списка одного и
    // того же человек выбирал бы дважды.
    const items = buildRecipients({ rows: ROWS, catalog: CATALOG, people: PEOPLE,
                                    hasBranch: false });
    assert.equal(byGroup(items, 'Должности отдела').length, 0);
    assert.equal(items.some((i) => i.body.subject_type === 'otp_role'), true);
});

test('ключ различает все четыре измерения правила', () => {
    const base = { subject_type: 'department', subject_id: 1,
                   min_role_level: 10, job_title: null };
    const keys = new Set([
        recipientKey(base),
        recipientKey({ ...base, min_role_level: 30 }),
        recipientKey({ ...base, job_title: 'Видеограф' }),
        recipientKey({ ...base, job_title: 'Таргетолог' }),
        recipientKey({ ...base, subject_type: 'department_head' }),
    ]);
    assert.equal(keys.size, 5, 'два разных адресата склеились в один ключ');
});

test('ключ не зависит от того, числом или строкой приехал id', () => {
    assert.equal(recipientKey({ subject_type: 'group', subject_id: '10' }),
                 recipientKey({ subject_type: 'group', subject_id: 10 }));
});

test('уже выданное узнаётся по тому же ключу, что строит список', () => {
    // Выдача поверх готового правила ЗАМЕНЯЕТ его целиком, и предупредить об
    // этом можно, только совпав ключами до последнего измерения.
    const items = build();
    const granted = grantedKeys([
        { subject_type: 'group', subject_id: 10, min_role_level: null, job_title: null },
        { subject_type: 'department', subject_id: 1, min_role_level: 10,
          job_title: 'Видеограф' },
    ]);
    const marked = items.filter((i) => granted.has(i.key)).map((i) => i.groupLabel);
    assert.deepEqual(marked.sort(), ['Группы', 'Должности отдела']);
});
