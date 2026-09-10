import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    EMPTY_FILTERS, NO_GROUP, countActiveFilters, hasActiveFilters, filtersToParams,
    filtersKey, clampScore, activeFilterChips, pullParamsFromFilters, operatorsMatching,
} from '../src/components/call_qa/filters.js';

/**
 * Фильтры раздела «ИИ-оценка».
 *
 * Проверяем чистый модуль, а не отрисовку: правила отбора нужны в трёх местах
 * сразу — панель, запросы обоих списков и подбор «оценить из отбора», — и
 * разъехаться они могут молча. Разъезд виден не как ошибка, а как «на вкладке
 * „Очередь ревью“ фильтр работает, а на „Звонках“ почему-то нет».
 */

/* Читаем с нормализацией переводов строк: на Windows core.autocrlf=true отдаёт
   файлы с CRLF, и проверки подстрок краснели бы от переводов строк. */
const readLf = (name) => readFileSync(new URL(`../${name}`, import.meta.url), 'utf8')
    .split('\r\n').join('\n');

test('пустой отбор не отправляет ни одного параметра', () => {
    assert.deepEqual(filtersToParams(EMPTY_FILTERS), {});
    assert.equal(countActiveFilters(EMPTY_FILTERS), 0);
    assert.equal(hasActiveFilters(EMPTY_FILTERS), false);
    // Незаданный отбор вообще не должен ломать вызывающего.
    assert.deepEqual(filtersToParams(null), {});
    assert.equal(countActiveFilters(undefined), 0);
});

test('пустая строка — это «фильтра нет», а не значение', () => {
    // Сервер отличает «нет фильтра» от значения: отправленный пустой q дал бы
    // поиск по пустой строке вместо полного списка.
    const params = filtersToParams({ ...EMPTY_FILTERS, q: '', operator_id: null, score_min: '' });
    assert.deepEqual(params, {});
});

test('период считается ОДНИМ фильтром, а не двумя', () => {
    // Иначе на кнопке стояло бы «2» за один выбранный диапазон.
    assert.equal(countActiveFilters({ ...EMPTY_FILTERS, date_from: '2026-05-01', date_to: '2026-05-31' }), 1);
    assert.equal(countActiveFilters({ ...EMPTY_FILTERS, date_from: '2026-05-01' }), 1);
});

test('балл ИИ тоже считается одним фильтром', () => {
    assert.equal(countActiveFilters({ ...EMPTY_FILTERS, score_min: '0', score_max: '60' }), 1);
});

test('счётчик складывает разные оси', () => {
    const filters = {
        ...EMPTY_FILTERS, date_from: '2026-05-01', direction_id: 7, group_id: 3,
        operator_id: 42, reviewed: 'no', score_max: '60', q: 'иванов',
    };
    assert.equal(countActiveFilters(filters), 7);
});

test('в параметры уходит ровно отобранное', () => {
    const params = filtersToParams({
        ...EMPTY_FILTERS, date_from: '2026-05-01', date_to: '2026-05-31',
        operator_id: 42, reviewed: 'yes', q: '  иванов  ',
    });
    assert.deepEqual(params, {
        date_from: '2026-05-01', date_to: '2026-05-31',
        operator_id: 42, reviewed: 'yes', q: 'иванов',
    });
});

test('«без группы» доезжает до сервера как значение, а не теряется', () => {
    // Корзина — отдельный фильтр: у части звонков из АТС учётной записи нет
    // вовсе, и без неё такие строки пропадали бы из любого разреза по группам.
    const params = filtersToParams({ ...EMPTY_FILTERS, group_id: NO_GROUP });
    assert.equal(params.group_id, 'none');
    assert.equal(countActiveFilters({ ...EMPTY_FILTERS, group_id: NO_GROUP }), 1);
});

test('ключ отбора сравнивается по значению, а не по ссылке', () => {
    // Объект фильтров пересоздаётся на каждом рендере; положи его в зависимости
    // эффекта — получишь бесконечный перезапрос списка.
    const a = { ...EMPTY_FILTERS, operator_id: 42 };
    const b = { ...EMPTY_FILTERS, operator_id: 42 };
    assert.notEqual(a, b);
    assert.equal(filtersKey(a), filtersKey(b));
    assert.notEqual(filtersKey(a), filtersKey({ ...EMPTY_FILTERS, operator_id: 43 }));
});

test('ключ не меняется от полей, которые никуда не уходят', () => {
    assert.equal(filtersKey(EMPTY_FILTERS), filtersKey({ ...EMPTY_FILTERS, q: '' }));
});

test('балл зажимается в 0…100 и целые', () => {
    assert.equal(clampScore('120'), '100');
    assert.equal(clampScore('-5'), '0');
    assert.equal(clampScore('72.6'), '73');
    assert.equal(clampScore(''), '');
    assert.equal(clampScore('  '), '');
    assert.equal(clampScore('abc'), '');
});

test('чипы называют отобранное словами, а не идентификаторами', () => {
    const chips = activeFilterChips({
        ...EMPTY_FILTERS, direction_id: 7, group_id: 3, operator_id: 42,
    }, {
        directions: [{ id: 7, name: 'ТП линия' }],
        groups: [{ id: 3, name: 'Группа 1' }],
        operators: [{ id: 42, name: 'Иванов И.' }],
    });
    assert.deepEqual(chips.map((chip) => chip.label), ['ТП линия', 'Группа 1', 'Иванов И.']);
});

test('чип снимает СВОЙ фильтр и не трогает соседние', () => {
    const filters = { ...EMPTY_FILTERS, direction_id: 7, operator_id: 42, q: 'иванов' };
    const chips = activeFilterChips(filters, {});
    const operatorChip = chips.find((chip) => chip.key === 'operator');
    assert.equal(operatorChip.next.operator_id, null);
    assert.equal(operatorChip.next.direction_id, 7);
    assert.equal(operatorChip.next.q, 'иванов');
});

test('чип не дублирует имя фильтра, когда значение с него и начинается', () => {
    // В портале группы называются «Группа Тестбаевой», и выходило «Группа
    // Группа Тестбаевой». Имя опускаем — значение говорит само за себя.
    const [dup] = activeFilterChips({ ...EMPTY_FILTERS, group_id: 3 },
        { groups: [{ id: 3, name: 'Группа Тестбаевой' }] });
    assert.equal(dup.name, '');
    assert.equal(dup.label, 'Группа Тестбаевой');
    // А когда не дублирует — имя остаётся: без него «Ночная смена» не читается.
    const [plain] = activeFilterChips({ ...EMPTY_FILTERS, group_id: 5 },
        { groups: [{ id: 5, name: 'Ночная смена' }] });
    assert.equal(plain.name, 'Группа');
});

test('чип «без группы» подписан словами', () => {
    const [chip] = activeFilterChips({ ...EMPTY_FILTERS, group_id: NO_GROUP }, {});
    assert.equal(chip.label, 'без группы');
});

test('чип периода читается и когда задана одна граница', () => {
    const [only] = activeFilterChips({ ...EMPTY_FILTERS, date_from: '2026-05-01' }, {});
    assert.equal(only.label, 'с 01.05');
    const [both] = activeFilterChips({ ...EMPTY_FILTERS, date_from: '2026-05-01', date_to: '2026-05-31' }, {});
    assert.equal(both.label, '01.05 — 31.05');
});

test('в АТС уходят сотрудник, период и то, что сужает круг людей', () => {
    // Балл и наличие оценки человека к ещё НЕ подтянутому звонку отношения не
    // имеют вовсе, а поиск по имени заменяет селектор сотрудника.
    const params = pullParamsFromFilters({
        ...EMPTY_FILTERS, operator_id: 42, date_from: '2026-05-01', date_to: '2026-05-07',
        direction_id: 7, group_id: 3, score_min: '10', reviewed: 'no', q: 'иванов',
    });
    assert.deepEqual(params, { operator_id: 42, date_from: '2026-05-01', date_to: '2026-05-07',
                               direction_id: 7, group_id: 3 });
});

test('список сотрудников сужается уже выбранными направлением и группой', () => {
    const people = [
        { id: 1, name: 'А', direction_id: 7, group_id: 3 },
        { id: 2, name: 'Б', direction_id: 7, group_id: null },
        { id: 3, name: 'В', direction_id: 9, group_id: 3 },
    ];
    assert.deepEqual(operatorsMatching(people, { ...EMPTY_FILTERS, direction_id: 7 })
        .map((person) => person.id), [1, 2]);
    assert.deepEqual(operatorsMatching(people, { ...EMPTY_FILTERS, group_id: 3 })
        .map((person) => person.id), [1, 3]);
    assert.deepEqual(operatorsMatching(people, { ...EMPTY_FILTERS, group_id: NO_GROUP })
        .map((person) => person.id), [2]);
});

test('фронт и бэкенд знают ОДИН набор фильтров', () => {
    // Ось, которую фронт отправляет, а сервер не разбирает, просто не работает —
    // и виновата в этом на глаз панель, а не пропущенное имя в списке.
    const api = readLf('call_qa/api.py');
    const declared = api.slice(api.indexOf('def normalise_list_filters'),
                              api.indexOf('def _like_pattern'));
    for (const key of Object.keys(EMPTY_FILTERS)) {
        assert.ok(declared.includes(`'${key}'`), `сервер не разбирает фильтр ${key}`);
    }
    // И ручка обязана прочитать их из query-строки — иначе разбор не позовут.
    const routes = readLf('bot_schedule2.py');
    const reader = routes.slice(routes.indexOf('def _ai_qa_list_filters'),
                               routes.indexOf('def api_ai_qa_filter_options'));
    for (const key of Object.keys(EMPTY_FILTERS)) {
        assert.ok(reader.includes(`'${key}'`), `ручка не читает фильтр ${key} из запроса`);
    }
});

test('оба списка и оба подбора получают отбор, а не только один из них', () => {
    // Фильтр, доехавший до списка, но не до кнопки подбора, выглядит как
    // «кнопка игнорирует настройку рядом с собой».
    const view = readLf('src/components/call_qa/CallQaView.jsx');
    assert.ok(view.includes('...filtersToParams(filters)'), 'очередь ревью без отбора');
    assert.ok(view.includes('<QaFilters'), 'панель не подключена');

    const list = readLf('src/components/call_qa/EvaluationsList.jsx');
    // Список, подбор случайного и подтяжка из АТС — три места.
    assert.equal((list.match(/filtersToParams\(filters\)/g) || []).length, 2);
    assert.ok(list.includes('pullParamsFromFilters(filters)'), 'подтяжка из АТС без отбора');

    const chats = readLf('src/components/call_qa/ChatQueue.jsx');
    assert.ok(chats.includes('...filtersToParams(filters)'), 'подбор переписки без отбора');
});
