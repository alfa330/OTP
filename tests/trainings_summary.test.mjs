import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

/* Сводка раздела «Тренинги»: чистая логика из src/components/trainings/constants.js.
 *
 * Модуль исполняется здесь напрямую, без сборщика: в нём нет ни JSX, ни импортов —
 * ровно для того, чтобы арифметику раздела можно было проверить тестом, а не
 * глазами по скриншоту. Раздел до этой работы не был покрыт ничем.
 */

const source = readFileSync(new URL('../src/components/trainings/constants.js', import.meta.url), 'utf8');
const module = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

const {
    FAMILY_BASE, FAMILY_CORPORATE,
    durationMinutes, formatDuration, timeToMinutes,
    plural, pluralPeople, pluralSessions,
    formatMonth, formatDayShort, formatDayLong,
    buildTopicSummaries, sortTopicSummaries, remainingCount, coveragePercent,
    buildGroupBuckets, NO_GROUP_KEY,
    tileTone, initials,
} = module;

const session = (over = {}) => ({
    id: over.id ?? 1,
    operator_id: over.operator_id ?? 100,
    operator_name: over.operator_name ?? 'Оператор',
    operator_status: over.operator_status ?? 'working',
    date: over.date ?? '2026-08-05',
    start_time: over.start_time ?? '10:00',
    end_time: over.end_time ?? '10:30',
    reason: over.reason ?? 'Обратная связь',
    topic_id: over.topic_id ?? null,
    count_in_hours: over.count_in_hours ?? true,
    group_id: over.group_id ?? 11,
    group_name: over.group_name ?? 'Группа А',
    ...over,
});

/* ── Длительность ───────────────────────────────────────────────────────── */

test('длительность считается по началу и окончанию', () => {
    assert.equal(durationMinutes({ start_time: '10:00', end_time: '10:30' }), 30);
    assert.equal(durationMinutes({ start_time: '09:15', end_time: '11:00' }), 105);
});

test('занятие через полночь считается как переход на следующие сутки', () => {
    // Та же арифметика, что в _training_intervals_overlap на сервере: иначе
    // раздел и часы показывали бы разные числа по одной записи.
    assert.equal(durationMinutes({ start_time: '23:30', end_time: '00:30' }), 60);
});

test('битое или пустое время даёт нуль, а не NaN', () => {
    assert.equal(durationMinutes({ start_time: '', end_time: '10:00' }), 0);
    assert.equal(durationMinutes({ start_time: 'ерунда', end_time: '10:00' }), 0);
    assert.equal(durationMinutes(null), 0);
    assert.equal(timeToMinutes(null), null);
});

test('длительность выводится по-русски', () => {
    assert.equal(formatDuration(0), '—');
    assert.equal(formatDuration(45), '45 мин');
    assert.equal(formatDuration(60), '1 ч');
    assert.equal(formatDuration(105), '1 ч 45 мин');
});

/* ── Числительные ───────────────────────────────────────────────────────── */

test('форма числительного согласуется с числом', () => {
    assert.equal(pluralPeople(1), 'сотрудник');
    assert.equal(pluralPeople(2), 'сотрудника');
    assert.equal(pluralPeople(5), 'сотрудников');
    assert.equal(pluralPeople(11), 'сотрудников');
    assert.equal(pluralPeople(21), 'сотрудник');
    assert.equal(pluralPeople(0), 'сотрудников');
    assert.equal(pluralSessions(1), 'занятие');
    assert.equal(pluralSessions(3), 'занятия');
    assert.equal(pluralSessions(14), 'занятий');
    assert.equal(plural(112, 'a', 'b', 'c'), 'c');
});

/* ── Даты ───────────────────────────────────────────────────────────────── */

test('месяц и день выводятся по-русски', () => {
    assert.equal(formatMonth('2026-08'), 'Август 2026');
    assert.equal(formatDayShort('2026-08-05'), '05.08');
    assert.equal(formatDayLong('2026-08-05'), '5 августа 2026');
});

test('мусор в дате не роняет форматтеры', () => {
    assert.equal(formatMonth(''), '');
    assert.equal(formatDayShort(null), '');
    assert.equal(formatDayLong('чепуха'), 'чепуха');
});

/* ── Сводка по темам ───────────────────────────────────────────────────── */

test('базовые темы собираются по причине', () => {
    const items = buildTopicSummaries({
        trainings: [
            session({ id: 1, reason: 'Собрание', operator_id: 1 }),
            session({ id: 2, reason: 'Собрание', operator_id: 2 }),
            session({ id: 3, reason: 'Мониторинг', operator_id: 1 }),
        ],
        topics: [],
        archivedReasons: [],
    });
    const meeting = items.find((item) => item.title === 'Собрание');
    assert.equal(meeting.family, FAMILY_BASE);
    assert.equal(meeting.monthSessions, 2);
    assert.equal(meeting.monthOperators, 2);
    assert.equal(meeting.monthMinutes, 60);
});

test('один сотрудник с двумя занятиями по теме считается один раз', () => {
    const [item] = buildTopicSummaries({
        trainings: [
            session({ id: 1, reason: 'Собрание', operator_id: 7 }),
            session({ id: 2, reason: 'Собрание', operator_id: 7, start_time: '14:00', end_time: '14:30' }),
        ],
        topics: [],
        archivedReasons: [],
    });
    assert.equal(item.monthSessions, 2);
    assert.equal(item.monthOperators, 1);
});

test('архивная базовая тема помечена', () => {
    const [item] = buildTopicSummaries({
        trainings: [session({ reason: 'Тех. сбой' })],
        topics: [],
        archivedReasons: ['Тех. сбой'],
    });
    assert.equal(item.isArchivedReason, true);
});

test('корпоративная тема попадает в список даже без занятий в месяце', () => {
    // У темы с нулевым охватом главное действие — «провести пачке»; спрятать её
    // значило бы спрятать саму работу.
    const items = buildTopicSummaries({
        trainings: [],
        topics: [{
            id: 3, title: 'Скрипт по возвратам', kind: 'info', department_id: 367,
            covered_count: 0, audience_count: 57, session_count: 0, is_archived: false,
        }],
        archivedReasons: [],
    });
    assert.equal(items.length, 1);
    assert.equal(items[0].family, FAMILY_CORPORATE);
    assert.equal(items[0].monthSessions, 0);
    assert.equal(items[0].audienceCount, 57);
});

test('охват берётся с сервера за всё время, а занятия — за месяц', () => {
    // Раскатка идёт неделями и месяц не заканчивает, поэтому это два разных
    // числа, и путать их нельзя.
    const [item] = buildTopicSummaries({
        trainings: [session({ id: 1, topic_id: 1, reason: 'Новые правила', count_in_hours: false })],
        topics: [{
            id: 1, title: 'Новые правила', kind: 'info',
            covered_count: 47, audience_count: 68, session_count: 9, is_archived: false,
        }],
        archivedReasons: [],
    });
    assert.equal(item.monthSessions, 1);
    assert.equal(item.coveredCount, 47);
    assert.equal(item.totalSessions, 9);
    assert.equal(remainingCount(item), 21);
    assert.equal(coveragePercent(item), 69);
});

test('корпоративное занятие с неизвестной темой не теряется', () => {
    // Тема чужого отдела в справочник не приедет — занятие обязано остаться
    // видимым под своим названием, а не исчезнуть из месяца.
    const items = buildTopicSummaries({
        trainings: [session({ topic_id: 99, reason: 'Тема другого отдела', count_in_hours: false })],
        topics: [],
        archivedReasons: [],
    });
    assert.equal(items.length, 1);
    assert.equal(items[0].title, 'Тема другого отдела');
    assert.equal(items[0].family, FAMILY_CORPORATE);
    assert.equal(items[0].monthSessions, 1);
});

test('охват не превышает 100 % даже если провели лишним людям', () => {
    const item = { family: FAMILY_CORPORATE, coveredCount: 70, audienceCount: 68 };
    assert.equal(coveragePercent(item), 100);
    assert.equal(remainingCount(item), 0);
});

test('у темы без аудитории охвата нет, а не ноль процентов', () => {
    assert.equal(coveragePercent({ family: FAMILY_CORPORATE, coveredCount: 0, audienceCount: 0 }), null);
});

test('у базовой темы охвата нет вовсе', () => {
    assert.equal(remainingCount({ family: FAMILY_BASE, coveredCount: 0, audienceCount: 68 }), 0);
});

test('архивная корпоративная тема не требует довести охват', () => {
    assert.equal(remainingCount({
        family: FAMILY_CORPORATE, isArchivedTopic: true, coveredCount: 1, audienceCount: 68,
    }), 0);
});

test('флаги «всё в часах» и «ничего в часах» не врут на смеси', () => {
    const [mixed] = buildTopicSummaries({
        trainings: [
            session({ id: 1, reason: 'Собрание', count_in_hours: true }),
            session({ id: 2, reason: 'Собрание', count_in_hours: false }),
        ],
        topics: [],
        archivedReasons: [],
    });
    assert.equal(mixed.allCounted, false);
    assert.equal(mixed.noneCounted, false);
});

test('темы с незакрытым охватом идут первыми', () => {
    const sorted = sortTopicSummaries(buildTopicSummaries({
        trainings: [
            session({ id: 1, reason: 'Собрание' }),
            session({ id: 2, reason: 'Собрание' }),
            session({ id: 3, reason: 'Собрание' }),
        ],
        topics: [{
            id: 1, title: 'Раскатка', kind: 'info',
            covered_count: 1, audience_count: 68, session_count: 1, is_archived: false,
        }],
        archivedReasons: [],
    }));
    assert.equal(sorted[0].title, 'Раскатка', 'незакрытая раскатка обязана быть выше');
});

/* ── Группировка по группам ─────────────────────────────────────────────── */

test('занятия раскладываются по группе из самой записи', () => {
    const buckets = buildGroupBuckets([
        session({ id: 1, group_id: 11, group_name: 'Группа А', operator_id: 1, operator_name: 'Аня' }),
        session({ id: 2, group_id: 11, group_name: 'Группа А', operator_id: 2, operator_name: 'Борис' }),
        session({ id: 3, group_id: 12, group_name: 'Группа Б', operator_id: 3, operator_name: 'Вера' }),
    ]);
    assert.equal(buckets.length, 2);
    const a = buckets.find((b) => b.name === 'Группа А');
    assert.equal(a.trainings.length, 2);
    assert.equal(a.people.length, 2);
    assert.equal(a.minutes, 60);
});

test('занятия без группы попадают в отдельную корзину, а не исчезают', () => {
    // На проде 87 занятий с июня не накрыты членством ни на одну дату
    // (зачисление задним числом) — прятать их нельзя.
    const buckets = buildGroupBuckets([
        session({ id: 1, group_id: 11, group_name: 'Группа А' }),
        session({ id: 2, group_id: null, group_name: null, operator_id: 9 }),
    ]);
    const orphan = buckets.find((b) => b.key === NO_GROUP_KEY);
    assert.ok(orphan, 'корзина «Без группы» обязана появиться');
    assert.equal(orphan.name, 'Без группы');
    assert.equal(orphan.trainings.length, 1);
});

test('«Без группы» всегда последней: это остаток, а не группа', () => {
    const buckets = buildGroupBuckets([
        session({ id: 1, group_id: null, group_name: null }),
        session({ id: 2, group_id: 11, group_name: 'Яблоко' }),
        session({ id: 3, group_id: 12, group_name: 'Абрикос' }),
    ]);
    assert.equal(buckets[buckets.length - 1].key, NO_GROUP_KEY);
    assert.equal(buckets[0].name, 'Абрикос', 'остальные группы — по алфавиту');
});

test('люди внутри группы отсортированы по имени и несут свои занятия', () => {
    const [bucket] = buildGroupBuckets([
        session({ id: 1, operator_id: 2, operator_name: 'Ярослав' }),
        session({ id: 2, operator_id: 1, operator_name: 'Айгерим' }),
        session({ id: 3, operator_id: 1, operator_name: 'Айгерим', start_time: '14:00', end_time: '15:00' }),
    ]);
    assert.deepEqual(bucket.people.map((p) => p.name), ['Айгерим', 'Ярослав']);
    assert.equal(bucket.people[0].trainings.length, 2);
    assert.equal(bucket.people[0].minutes, 90);
});

test('уволенный сотрудник сохраняет свой статус в группировке', () => {
    const [bucket] = buildGroupBuckets([
        session({ operator_id: 5, operator_name: 'Ушедший', operator_status: 'fired' }),
    ]);
    assert.equal(bucket.people[0].status, 'fired');
});

/* ── Плитка-монограмма ─────────────────────────────────────────────────── */

test('цвет плитки стабилен для одной строки и не зависит от порядка', () => {
    // Прошлая версия раздела выдавала цвет «золотым углом» по счётчику, и он
    // перетасовывался на каждом рендере — запомнить его было нельзя.
    assert.equal(tileTone('Тренинг по продукту'), tileTone('Тренинг по продукту'));
    assert.notEqual(tileTone(''), undefined);
});

test('монограмма берёт по одной букве от двух первых слов', () => {
    assert.equal(initials('Тренинг по продукту'), 'ТП');
    assert.equal(initials('Мониторинг'), 'МО');
    assert.equal(initials('   '), '—');
    assert.equal(initials(null), '—');
});
