// «Списки Байги», лист «Доступ» (решение владельца 07.10.2026): уровни,
// список адресатов и подписи строк. Сами правила доступа и ручки сверяет
// tests/test_baiga.py; здесь — то, что живёт только во фронте.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    KIND_LABEL, LEVELS, LEVEL_HINT, MAX_SUBJECTS, buildRecipients, circleRows, circleSummary, grantBody,
    grantMeta, grantTitle, grantToast, grantedLevels, levelBody, levelOf, replacingCount, replacingNote,
    roleTitle, subjectKey,
} from '../src/components/baiga/baigaAccess.js';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const ACCESS_PY = read('../baiga/access.py');

const CATALOG = {
    department: [
        { id: 44, name: 'Бухгалтерия', detail: null, role: null, people: 1 },
        { id: 70, name: 'Тез КЦ', detail: null, role: null, people: 21 },
    ],
    group: [
        { id: 2, name: 'Тех поддержка', detail: 'Тез КЦ', role: null, people: 16 },
        { id: 5, name: 'Группа без отдела', detail: null, role: null, people: 0 },
    ],
    user: [
        { id: 31, name: 'Оператов О. О.', detail: 'Тез КЦ', role: 'operator', people: null },
        { id: 98, name: 'Кадрова К. К.', detail: 'HR', role: 'hr_manager', people: null },
        { id: 22, name: 'Корнев К. К.', detail: null, role: 'super_admin', people: null },
    ],
};

test('уровни — те же и в том же порядке, что на сервере', () => {
    const server = /^LEVELS = \(LEVEL_READ, LEVEL_EXPORT, LEVEL_FULL\)$/m.test(ACCESS_PY)
        && /^LEVEL_READ, LEVEL_EXPORT, LEVEL_FULL = '(\w+)', '(\w+)', '(\w+)'$/m.exec(ACCESS_PY);
    assert.ok(server, 'в baiga/access.py нет шкалы уровней');
    assert.deepEqual(LEVELS.map((level) => level.key), server.slice(1, 4));
    // У каждого уровня есть слово для сегмента, слово для строки и пояснение.
    for (const level of LEVELS) {
        assert.ok(level.label && level.summary && level.note, level.key);
        assert.equal(levelOf(level.key), level);
    }
    assert.equal(new Set(LEVELS.map((level) => level.label)).size, LEVELS.length);
    assert.equal(levelOf('owner'), null);
    assert.equal(levelOf(undefined), null);
});

test('подсказка «i» называет ровно варианты сегментов и говорит про QR', () => {
    assert.deepEqual(LEVEL_HINT.options.map(([name]) => name), LEVELS.map((level) => level.label));
    for (const [name, meaning] of LEVEL_HINT.options) {
        // «Чтение — ищет и смотрит…»: со строчной и без точки на конце.
        assert.match(meaning, /^[а-яё]/, name);
        assert.doesNotMatch(meaning, /\.$/, name);
        // Одно предложение: внутри строки подсказки нет второй заглавной фразы.
        assert.doesNotMatch(meaning, /\. [А-ЯЁ]/, name);
    }
    assert.match(LEVEL_HINT.intro, /включает предыдущий/);
    assert.match(LEVEL_HINT.outro, /после QR-подтверждения/);
    // Кого спрашивают о QR — те же должности, что на сервере (QR_ASKED_ROLES):
    // оператор, стажёр, бухгалтерия, маркетинг.
    for (const word of ['Операторам', 'стажёрам', 'бухгалтерии', 'маркетинга']) {
        assert.ok(LEVEL_HINT.outro.includes(word), word);
    }
    const sheet = read('../src/components/baiga/BaigaAccessSheet.jsx');
    // Обе подсказки листа — порталом: пузырь внутри тела окна обрезался бы.
    assert.equal((sheet.match(/<InfoHint/g) || []).length, 2);
    assert.ok(!sheet.includes('<IosHint'));
    // Пояснение уровня — только под «i»: той же фразы строкой под сегментами нет.
    assert.ok(!sheet.includes('.note}'));
});

test('потолок адресатов одной выдачи — как на сервере', () => {
    const server = /^MAX_GRANT_SUBJECTS = (\d+)$/m.exec(ACCESS_PY);
    assert.ok(server);
    assert.equal(MAX_SUBJECTS, Number(server[1]));
});

test('виды адресатов — ровно те, что принимает сервер', () => {
    const server = /^SUBJECT_USER, SUBJECT_GROUP, SUBJECT_DEPARTMENT = '(\w+)', '(\w+)', '(\w+)'$/m.exec(ACCESS_PY);
    assert.ok(server);
    assert.deepEqual(Object.keys(KIND_LABEL).sort(), server.slice(1, 4).sort());
    const sent = new Set(buildRecipients(CATALOG).map((item) => item.body.type));
    assert.deepEqual([...sent].sort(), server.slice(1, 4).sort());
});

test('список адресатов сплошной: группы, отделы, люди — последними', () => {
    const list = buildRecipients(CATALOG);
    assert.deepEqual(list.map((item) => item.groupLabel),
        ['Группы', 'Группы', 'Отделы', 'Отделы', 'Люди', 'Люди', 'Люди']);
    assert.deepEqual(list.map((item) => item.label), [
        'Тех поддержка · Тез КЦ · 16 человек',
        'Группа без отдела · 0 человек',
        'Бухгалтерия · 1 человек',
        'Тез КЦ · 21 человек',
        'Оператов О. О. · оператор · Тез КЦ',
        'Кадрова К. К. · HR-менеджер · HR',
        'Корнев К. К. · супер-админ',
    ]);
    // Ключ различает вид: группа №2 и человек №2 — разные адресаты.
    assert.equal(new Set(list.map((item) => item.key)).size, list.length);
    assert.equal(subjectKey('group', 2), 'group:2');
    assert.notEqual(subjectKey('group', 2), subjectKey('user', 2));
    assert.equal(subjectKey('user', '31'), subjectKey('user', 31));
    // На сервер уходит вид и число, а не подпись.
    assert.deepEqual(list[0].body, { type: 'group', id: 2 });
    assert.deepEqual(list[4].body, { type: 'user', id: 31 });
});

test('пустой и неполный справочник не роняют список', () => {
    assert.deepEqual(buildRecipients(), []);
    assert.deepEqual(buildRecipients({}), []);
    assert.deepEqual(buildRecipients({ user: [{ id: 1, name: 'Один О.' }] }).map((item) => item.label), ['Один О.']);
});

test('должность в подписи человека — словарь портала, включая бэк-офис', () => {
    assert.equal(roleTitle('sv'), 'супервайзер');
    assert.equal(roleTitle(' Supervisor '), 'супервайзер');
    assert.equal(roleTitle('trainee'), 'стажёр');
    assert.equal(roleTitle('accounting_manager'), 'менеджер бухгалтерии');
    assert.equal(roleTitle('marketing_manager'), 'менеджер маркетинга');
    assert.equal(roleTitle('hr_manager'), 'HR-менеджер');
    assert.equal(roleTitle('незнакомая'), '');
    assert.equal(roleTitle(null), '');
});

test('строка выдачи: кто это, чей и сколько людей', () => {
    const group = { subject_type: 'group', subject_id: 2, label: 'Тех поддержка', detail: 'Тез КЦ', people: 16, active: true };
    assert.equal(grantTitle(group), 'Тех поддержка');
    assert.equal(grantMeta(group), 'Группа · Тез КЦ · 16 человек');
    const department = { subject_type: 'department', subject_id: 44, label: 'Бухгалтерия', detail: null, people: 1, active: true };
    assert.equal(grantMeta(department), 'Отдел · 1 человек');
    // У человека вместо слова «Сотрудник» — должность: она и отличает тёзок.
    const person = { subject_type: 'user', subject_id: 31, label: 'Оператов О. О.', detail: 'Тез КЦ', role: 'operator', people: null, active: true };
    assert.equal(grantMeta(person), 'оператор · Тез КЦ');
    assert.equal(grantMeta({ ...person, role: 'незнакомая', detail: null }), 'Сотрудник');
});

test('выдача, которая никому ничего не открывает, сказана словом', () => {
    assert.equal(grantMeta({ subject_type: 'user', label: 'Бывший Б. Б.', role: 'operator', detail: 'Тез КЦ', active: false }),
        'оператор · Тез КЦ · уволен');
    assert.equal(grantMeta({ subject_type: 'group', label: 'Старая', detail: 'СЗоВ', people: 0, active: false }),
        'Группа · СЗоВ · 0 человек · в архиве');
    assert.equal(grantMeta({ subject_type: 'department', label: 'Закрытый', people: 0, active: false }),
        'Отдел · 0 человек · отдел закрыт');
    // Адресата удалили вовсе: строка остаётся, чтобы выдачу можно было снять.
    const gone = { subject_type: 'group', subject_id: 9, label: null, active: false };
    assert.equal(grantTitle(gone), 'Адресат удалён');
    assert.equal(grantMeta(gone), 'Группа');
    assert.equal(grantTitle(null), 'Адресат удалён');
    assert.equal(grantMeta(null), '');
});

test('уровень сменится только у тех, кому выдан ДРУГОЙ', () => {
    const granted = grantedLevels([
        { subject_type: 'group', subject_id: 2, level: 'read' },
        { subject_type: 'user', subject_id: '31', level: 'full' },
    ]);
    assert.deepEqual([...granted].sort(), [['group:2', 'read'], ['user:31', 'full']]);
    const picked = ['group:2', 'user:2', 'user:31', 'department:44'];
    // Тот же уровень сервер не трогает — и предупреждать о смене нечего.
    assert.equal(replacingCount(picked, granted, 'read'), 1);
    assert.equal(replacingCount(picked, granted, 'full'), 1);
    assert.equal(replacingCount(picked, granted, 'export'), 2);
    assert.equal(replacingCount(['group:2'], granted, 'read'), 0);
    assert.equal(replacingCount([], granted, 'full'), 0);
    assert.equal(replacingCount(['group:2']), 0);
    assert.equal(grantedLevels().size, 0);
});

test('лист не теряет выбор и не залипает: сохранение, отказ, жест «назад»', () => {
    const sheet = read('../src/components/baiga/BaigaAccessSheet.jsx');
    // Пока идёт сохранение, с экрана не уходят: ответ закрыл бы уже другой экран.
    assert.ok(sheet.includes('const goBack = () => { if (!busy) leave(); };'));
    assert.ok(sheet.includes('<button type="button" className={iosBtnSecondary} disabled={busy} onClick={goBack}>'));
    assert.ok(sheet.includes('        if (busy) return;\n        setBusy(true);'));
    // Жест «назад» на телефоне: false — «остаюсь», иначе следующий жест прошёл бы мимо листа.
    assert.ok(sheet.includes('        if (busy) return false;\n'
        + '        if ((draft && changed) || (narrow && (circleOpen || draft))) { leave(); return false; }\n'
        + '        onClose?.();'));
    // Выдачу успели снять — в список; и список, и справочник перечитываются.
    assert.ok(sheet.includes('                if (status === 404) leave();\n'));
    assert.ok(sheet.includes('                if (status === 404 || status === 422) load();'));
    // Из отмеченных уходят те, кого в перечитанном справочнике больше нет.
    assert.ok(sheet.includes('const kept = current.subjects.filter((key) => recipientByKey.has(key));'));
    // Предупреждение считается по уровню черновика, а не по одному совпадению адресата.
    assert.ok(sheet.includes('replacingCount(draft.subjects, granted, draft.level)'));
    // Кому раздел уже выдан — видно в самом списке адресатов, словом уровня справа.
    assert.ok(sheet.includes('meta: levelOf(granted.get(item.key))?.label,'));
    assert.equal(levelOf(new Map().get('group:2')), null);
});

test('предупреждение о смене уровня — без числительного в падеже', () => {
    assert.equal(replacingNote(0), '');
    assert.equal(replacingNote(1), 'Одному из выбранных раздел уже выдан — его уровень сменится.');
    for (const count of [2, 5, 21, 50]) {
        assert.equal(replacingNote(count), `Раздел уже выдан ${count} из выбранных — их уровень сменится.`);
    }
});

test('на сервер уходит ровно то, что он ждёт', () => {
    const byKey = new Map(buildRecipients(CATALOG).map((item) => [item.key, item]));
    // POST /api/baiga/access/grants — { subjects: [{ type, id }], level } (baiga/routes.py).
    assert.deepEqual(grantBody(['group:2', 'user:31'], byKey, 'export'), {
        subjects: [{ type: 'group', id: 2 }, { type: 'user', id: 31 }], level: 'export',
    });
    // Ключ, которого в справочнике уже нет, в запрос не попадает.
    assert.deepEqual(grantBody(['group:2', 'user:999'], byKey, 'read'), {
        subjects: [{ type: 'group', id: 2 }], level: 'read',
    });
    assert.deepEqual(grantBody(), { subjects: [], level: '' });
    // PATCH /api/baiga/access/grants/<id> — { level }.
    assert.deepEqual(levelBody('full'), { level: 'full' });
    const routes = read('../baiga/routes.py');
    assert.ok(routes.includes("raw = data.get('subjects')"));
    assert.ok(routes.includes("(item or {}).get('type')"));
    assert.ok(routes.includes("item.get('id')"));
    assert.ok(routes.includes("data.get('level')"));
    // Лист шлёт именно эти тела: уровень — из черновика, а не из открытой выдачи.
    const sheet = read('../src/components/baiga/BaigaAccessSheet.jsx');
    assert.ok(sheet.includes('grantBody(draft.subjects, recipientByKey, draft.level), { headers: headers() }), grantToast);'));
    assert.ok(sheet.includes('levelBody(draft.level), { headers: headers() }), \'Доступ изменён\');'));
});

const CIRCLE = [
    { key: 'super_admin', level: 'full', qr: false, departments: [], people: [] },
    { key: 'head', level: 'full', qr: false, departments: ['Маркетинг'], people: [] },
    { key: 'named', level: 'full', qr: false, departments: [], people: ['Аналитиков А. А.'] },
    { key: 'lead', level: 'read', qr: false, departments: ['Отдел продаж', 'СЗоВ'], people: [] },
    { key: 'staff', level: 'read', qr: true, departments: ['Маркетинг', 'Отдел продаж', 'СЗоВ'], people: [] },
];

test('кому раздел открыт по умолчанию — строками, с уровнем и замком', () => {
    assert.deepEqual(circleRows(CIRCLE), [
        { key: 'super_admin', title: 'Супер-админы', meta: '', value: 'Полный доступ' },
        { key: 'head', title: 'Глава отдела «Маркетинг»', meta: '', value: 'Полный доступ' },
        { key: 'named', title: 'Аналитиков А. А.', meta: 'поимённо', value: 'Полный доступ' },
        { key: 'lead', title: 'Главы и супервайзеры', meta: 'Отдел продаж, СЗоВ', value: 'Чтение' },
        // «После QR» — справа, у уровня: слева длинные названия отделов его обрезали.
        { key: 'staff', title: 'Операторы и сотрудники', meta: 'Маркетинг, Отдел продаж, СЗоВ', value: 'Чтение · после QR' },
    ]);
    assert.equal(circleSummary(CIRCLE), 'супер-админы, Маркетинг, Отдел продаж, СЗоВ');
});

test('строки круга — те же ключи, что отдаёт сервер', () => {
    const body = ACCESS_PY.split('def circle():')[1].split('\ndef ')[0];
    const server = [...body.matchAll(/\{'key': '(\w+)'/g)].map((match) => match[1]);
    assert.deepEqual(server, CIRCLE.map((row) => row.key));
    // Каждый ключ сервера листу знаком: незнакомая строка молча пропала бы.
    assert.equal(circleRows(server.map((key) => ({
        key, level: 'read', qr: false, departments: ['Отдел'], people: ['Человек Ч.'],
    }))).length, server.length);
});

test('круг без имён и с незнакомой строкой не ломает лист', () => {
    // Названного поимённо уволили — имён нет, и строки нет.
    assert.deepEqual(circleRows([{ key: 'named', level: 'full', qr: false, people: [] }]), []);
    assert.deepEqual(circleRows([{ key: 'новое правило', level: 'read', qr: false }]), []);
    assert.deepEqual(circleRows(), []);
    assert.equal(circleSummary(), 'супер-админы');
});

test('тост говорит, что произошло, по ответу сервера', () => {
    // Новая выдача: пункт меню у человека появится после обновления страницы
    // (флаг профиля читается при её загрузке) — раздающий должен это знать.
    const reload = 'Раздел появится в меню после обновления страницы.';
    assert.equal(grantToast({ granted: 1, changed: 0 }), `Доступ выдан. ${reload}`);
    assert.equal(grantToast({ granted: 3, changed: 0 }), `Доступ выдан: 3. ${reload}`);
    assert.equal(grantToast({ granted: 2, changed: 1 }), `Доступ выдан: 2, изменён: 1. ${reload}`);
    // Смена уровня перезагрузки не требует: права раздел спрашивает сам.
    assert.equal(grantToast({ granted: 0, changed: 1 }), 'Доступ изменён');
    assert.equal(grantToast({ granted: 0, changed: 2 }), 'Доступ изменён: 2');
    assert.equal(grantToast({ granted: 0, changed: 0 }), 'У выбранных этот доступ уже есть');
    assert.equal(grantToast(), 'У выбранных этот доступ уже есть');
});
