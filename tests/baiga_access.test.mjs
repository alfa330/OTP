// «Списки Байги», лист «Доступ» (решение владельца 07.10.2026): уровни,
// список адресатов и подписи строк; с 08.10.2026 — и правка строк «открыт по
// умолчанию». Сами правила доступа и ручки сверяет tests/test_baiga.py; здесь —
// то, что живёт только во фронте.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    CIRCLE_LEVELS, KIND_LABEL, LEVELS, LEVEL_HINT, MAX_SUBJECTS, NAMED_SECTION, NONE_LEVEL, buildRecipients,
    circleBody, circleLevelHint, circleLevelOf, circleSections, circleSummary, circleToast, grantBody,
    grantMeta, grantTitle, grantToast, grantedLevels, levelBody, levelOf, replacingCount, replacingNote,
    roleTitle, slotOwner, slotTitle, slotValue, subjectKey,
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
    // Несохранённый выбор — и выдачи, и строки «открыт по умолчанию» — первым выходом не теряется.
    assert.ok(sheet.includes('        if (busy) return false;\n'
        + '        if (changed || (narrow && (circleOpen || draft))) { leave(); return false; }\n'
        + '        onClose?.();'));
    assert.ok(sheet.includes('    const changed = slot\n        ? slot.level !== slot.row.level\n'));
    // Назад — по одному уровню: со строки круга к «открыт по умолчанию», оттуда в список.
    assert.ok(sheet.includes('        if (slot) { setSlot(null); return; }\n        setCircleOpen(false);\n'));
    assert.ok(sheet.includes('onBack={circleOpen || draft || slot ? goBack : null}'));
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

/* Круг, как его отдаёт GET /api/baiga/access: строка — должность в отделе или
   человек, названный поимённо (baiga/routes.py: _circle). */
const circleRow = (slot, level, extra = {}) => {
    const [kind, code] = slot.split(':');
    return {
        slot, kind, level, qr: kind === 'staff', locked: false, code: kind === 'named' ? null : code,
        department: { marketing: 'Маркетинг', op: 'Отдел продаж', szov: 'СЗоВ' }[code] || null,
        person: null, updated_by_name: null, updated_at: null, ...extra,
    };
};

const CIRCLE = [
    { slot: 'super_admin', kind: 'super_admin', level: 'full', qr: false, locked: true, code: null,
      department: null, person: null, updated_by_name: null, updated_at: null },
    circleRow('head:marketing', 'full'),
    circleRow('staff:marketing', 'read'),
    circleRow('head:op', 'read'),
    circleRow('sv:op', 'read'),
    circleRow('staff:op', 'read'),
    circleRow('head:szov', 'read'),
    circleRow('sv:szov', 'read'),
    circleRow('staff:szov', 'read'),
    circleRow('named:540', 'full', { person: 'Аналитиков А. А.' }),
];

const edited = (changes) => CIRCLE.map((row) => (row.slot in changes ? { ...row, level: changes[row.slot] } : row));

// Строки секций без ссылки на исходную строку — она сверяется отдельно.
const plain = (sections) => sections.map((section) => ({
    ...section, rows: section.rows.map(({ row, ...rest }) => rest),
}));

test('уровни строки круга — «Нет» и те же три, как на сервере', () => {
    const none = /^LEVEL_NONE = '(\w+)'$/m.exec(ACCESS_PY);
    assert.ok(none && /^CIRCLE_LEVELS = \(LEVEL_NONE,\) \+ LEVELS$/m.test(ACCESS_PY), 'в baiga/access.py нет уровней круга');
    assert.equal(NONE_LEVEL.key, none[1]);
    assert.deepEqual(CIRCLE_LEVELS.map((level) => level.key), [none[1], ...LEVELS.map((level) => level.key)]);
    assert.deepEqual(CIRCLE_LEVELS.map((level) => level.label), ['Нет', 'Чтение', 'Выгрузка', 'Полный']);
    for (const level of CIRCLE_LEVELS) assert.equal(circleLevelOf(level.key), level);
    assert.equal(circleLevelOf('owner'), null);
    // «Нет» — только у круга: выдачу не «выдают на нет», её снимают.
    assert.equal(levelOf(NONE_LEVEL.key), null);
    assert.ok(!LEVELS.includes(NONE_LEVEL));
});

test('подсказка у строки круга: варианты сегментов, про QR — только рядовым', () => {
    const staff = circleLevelHint(CIRCLE[5]);
    assert.deepEqual(staff.options.map(([name]) => name), CIRCLE_LEVELS.map((level) => level.label));
    for (const [name, meaning] of staff.options) {
        assert.match(meaning, /^[а-яё]/, name);
        assert.doesNotMatch(meaning, /\.$/, name);
        assert.doesNotMatch(meaning, /\. [А-ЯЁ]/, name);
    }
    assert.match(staff.outro, /после QR-подтверждения/);
    // Главу, супервайзера и названного поимённо замок не спрашивает — и подсказка молчит.
    for (const row of [CIRCLE[1], CIRCLE[3], CIRCLE[4], CIRCLE[9]]) assert.equal(circleLevelHint(row).outro, '', row.slot);
    assert.equal(circleLevelHint().outro, '');
    const sheet = read('../src/components/baiga/BaigaAccessSheet.jsx');
    assert.ok(sheet.includes('{hint.outro && <div className="text-slate-500">{hint.outro}</div>}'));
    assert.ok(sheet.includes('levels={CIRCLE_LEVELS}'));
    assert.ok(sheet.includes('hint={circleLevelHint(slot.row)}'));
});

test('«открыт по умолчанию» — секциями по отделам: должность, уровень, замок', () => {
    const sections = circleSections(CIRCLE);
    assert.deepEqual(plain(sections), [
        { key: 'always', title: '', rows: [
            { slot: 'super_admin', title: 'Супер-админы', value: 'Полный', muted: false, locked: true },
        ] },
        { key: 'department:marketing', title: 'Маркетинг', rows: [
            { slot: 'head:marketing', title: 'Глава отдела', value: 'Полный', muted: false, locked: false },
            // В «Маркетинге» рядовые — не операторы.
            { slot: 'staff:marketing', title: 'Сотрудники', value: 'Чтение · после QR', muted: false, locked: false },
        ] },
        { key: 'department:op', title: 'Отдел продаж', rows: [
            { slot: 'head:op', title: 'Глава отдела', value: 'Чтение', muted: false, locked: false },
            { slot: 'sv:op', title: 'Супервайзеры', value: 'Чтение', muted: false, locked: false },
            // «После QR» — справа, у уровня: там строка не обрезается.
            { slot: 'staff:op', title: 'Операторы', value: 'Чтение · после QR', muted: false, locked: false },
        ] },
        { key: 'department:szov', title: 'СЗоВ', rows: [
            { slot: 'head:szov', title: 'Глава отдела', value: 'Чтение', muted: false, locked: false },
            { slot: 'sv:szov', title: 'Супервайзеры', value: 'Чтение', muted: false, locked: false },
            { slot: 'staff:szov', title: 'Операторы', value: 'Чтение · после QR', muted: false, locked: false },
        ] },
        { key: 'named', title: NAMED_SECTION, rows: [
            { slot: 'named:540', title: 'Аналитиков А. А.', value: 'Полный', muted: false, locked: false },
        ] },
    ]);
    // Справа — то же слово, что в сегментах экрана строки.
    const words = new Set(CIRCLE_LEVELS.map((level) => level.label));
    for (const row of sections.flatMap((section) => section.rows)) {
        assert.ok(words.has(row.value.replace(' · после QR', '')), row.slot);
    }
    // Экрану правки строка отдаётся целиком, той же ссылкой: в ней имя строки и её уровень.
    assert.equal(sections[2].rows[2].row, CIRCLE[5]);
    assert.equal(circleSummary(CIRCLE), 'супер-админы, Маркетинг, Отдел продаж, СЗоВ');
});

test('правленая строка: уровень — словом сегмента, закрытая — «Нет» без QR', () => {
    const rows = Object.fromEntries(circleSections(edited({
        'staff:op': 'none', 'sv:op': 'export', 'staff:szov': 'full', 'head:marketing': 'read', 'named:540': 'none',
    })).flatMap((section) => section.rows).map((row) => [row.slot, row]));
    assert.deepEqual([rows['staff:op'].value, rows['staff:op'].muted], ['Нет', true]);
    assert.deepEqual([rows['sv:op'].value, rows['sv:op'].muted], ['Выгрузка', false]);
    assert.deepEqual([rows['staff:szov'].value, rows['staff:szov'].muted], ['Полный · после QR', false]);
    assert.deepEqual([rows['head:marketing'].value, rows['head:marketing'].muted], ['Чтение', false]);
    // Закрытая строка остаётся в списке: её открывают обратно там же.
    assert.deepEqual([rows['named:540'].value, rows['named:540'].muted], ['Нет', true]);
    // Незнакомое слово вместо уровня — закрыта, а не «открыта неизвестно как».
    assert.equal(slotValue({ ...CIRCLE[5], level: 'owner' }), 'Нет');
    assert.equal(slotValue({ ...CIRCLE[5], level: undefined }), 'Нет');
    assert.equal(slotValue(), 'Нет');
});

test('сводка в списке называет отделы, где хоть одна строка открыта', () => {
    assert.equal(circleSummary(edited({ 'staff:op': 'none' })), 'супер-админы, Маркетинг, Отдел продаж, СЗоВ');
    assert.equal(circleSummary(edited({ 'head:op': 'none', 'sv:op': 'none', 'staff:op': 'none' })),
        'супер-админы, Маркетинг, СЗоВ');
    const all = Object.fromEntries(CIRCLE.filter((row) => !row.locked).map((row) => [row.slot, 'none']));
    assert.equal(circleSummary(edited(all)), 'супер-админы');
    assert.equal(circleSummary(), 'супер-админы');
});

test('виды строк круга — те же, что отдаёт сервер, и каждая подписана', () => {
    const server = /^SLOT_HEAD, SLOT_SV, SLOT_STAFF, SLOT_NAMED = '(\w+)', '(\w+)', '(\w+)', '(\w+)'$/m.exec(ACCESS_PY);
    assert.ok(server, 'в baiga/access.py нет видов строк круга');
    const kinds = ['super_admin', ...server.slice(1, 5)];
    assert.deepEqual([...new Set(CIRCLE.map((row) => row.kind))].sort(), [...kinds].sort());
    // Незнакомая строка молча пропала бы — у каждого вида сервера есть подпись.
    for (const kind of kinds) {
        assert.ok(slotTitle({ kind, code: 'op', person: 'Человек Ч.' }), kind);
    }
    assert.ok(ACCESS_PY.includes("rows = [{'slot': 'super_admin', 'kind': 'super_admin'"));
    // Чья строка — подзаголовок её экрана.
    assert.equal(slotOwner(CIRCLE[5]), 'Отдел продаж');
    assert.equal(slotOwner(CIRCLE[9]), NAMED_SECTION);
    assert.equal(slotOwner(), '');
    assert.equal(slotTitle(), '');
});

test('круг без имён и с незнакомой строкой не ломает лист', () => {
    // Названного поимённо уволили — имени нет, и строки нет (и пустой секции тоже).
    assert.deepEqual(circleSections([circleRow('named:540', 'full')]), []);
    assert.deepEqual(circleSections([{ slot: 'trainer:op', kind: 'trainer', level: 'read', code: 'op', department: 'Отдел продаж' }]), []);
    assert.deepEqual(circleSections(), []);
    // Отдел без названия в справочнике подписан кодом — так его отдаёт сервер; секция не пропадает.
    assert.equal(circleSections([circleRow('head:op', 'read', { department: 'op' })])[0].title, 'op');
});

test('правка строки круга: тело запроса и разводка в листе', () => {
    // PATCH /api/baiga/access/circle — { slot, level } (baiga/routes.py).
    assert.deepEqual(circleBody('staff:op', 'none'), { slot: 'staff:op', level: 'none' });
    const routes = read('../baiga/routes.py');
    assert.ok(routes.includes("@baiga_route('/access/circle', methods=('PATCH',), need='access')"));
    assert.ok(routes.includes("slot = str(data.get('slot') or '').strip()"));
    assert.ok(routes.includes("level = str(data.get('level') or '').strip().lower()"));
    assert.ok(routes.includes('return jsonify({"circle": circle})'));
    const sheet = read('../src/components/baiga/BaigaAccessSheet.jsx');
    // Уходит имя строки и уровень из черновика, а ответ кладётся на место круга.
    assert.ok(sheet.includes('            run(axios.patch(`${apiBaseUrl}/api/baiga/access/circle`,\n'
        + '                circleBody(slot.row.slot, slot.level), { headers: headers() }),\n'
        + "            circleToast(slot.row.level, slot.level), 'circle');"));
    assert.ok(sheet.includes('setData((prev) => ({ ...(prev || {}), [key]: response.data?.[key] || [] }));'));
    // Черновик строки начинается с её уровня: «Сохранить» гаснет, пока он тот же.
    assert.ok(sheet.includes('setSlot({ row, level: row.level });'));
    assert.ok(sheet.includes('onOpen={() => openSlot(row.row)}'));
    // Строка супер-админов — без нажатия: это не кнопка.
    assert.ok(sheet.includes('{section.rows.map((row) => (row.locked ? ('));
    // Кто и когда правил строку — как «Выдано» у выдачи.
    assert.ok(sheet.includes("{['Изменено', slot.row.updated_by_name, fmtStamp(slot.row.updated_at)]"));
    // Раздающий правит и свою строку — права раздела под листом перечитываются.
    assert.ok(sheet.includes('                onChanged?.();'));
    assert.ok(read('../src/components/baiga/BaigaView.jsx').includes('onChanged={refreshScreen}'));
});

test('тост правки строки: открыли, закрыли или сменили уровень', () => {
    const reload = 'Раздел появится в меню после обновления страницы.';
    // Открыли закрытую строку — пункт меню у людей появится после обновления страницы.
    assert.equal(circleToast('none', 'read'), `Доступ открыт. ${reload}`);
    assert.equal(circleToast('none', 'full'), `Доступ открыт. ${reload}`);
    assert.equal(circleToast('read', 'none'), 'Доступ закрыт');
    assert.equal(circleToast('full', 'none'), 'Доступ закрыт');
    // Смена уровня открытой строки перезагрузки не требует.
    assert.equal(circleToast('read', 'export'), 'Доступ изменён');
    assert.equal(circleToast('full', 'read'), 'Доступ изменён');
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
