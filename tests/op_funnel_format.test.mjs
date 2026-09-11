import test from 'node:test';
import assert from 'node:assert/strict';

import {
    BUCKET_ORDER,
    DASH,
    DATE_PRESETS,
    DIRECTIONS,
    METRIC_LABELS,
    anomalyText,
    formatDelta,
    formatHours,
    formatNumber,
    formatPercent,
    formatSeconds,
    funnelStepLabel,
    heatAlpha,
    isLoadDirection,
    isoDay,
    presetRange,
    reasonBucketLabel,
    shortDay,
    toneForRate,
} from '../src/components/op_funnel/funnelFormat.js';

/* Форматы раздела «Воронка ОП».
 *
 * Ради чего тест: сервер отдаёт проценты долями и `null` вместо нуля, и оба
 * соглашения ломаются молча. Доля, показанная как есть, превращает 50,8 % в
 * «0,5», а `null`, сложенный с нулём, превращает «не считали» в «ноль дозвонов»
 * — и то и другое человек принимает за факт.
 *
 * Неразрывные пробелы записаны escape-последовательностями ( ), а не
 * вставлены символом: в чужом редакторе они неотличимы от обычного пробела, и
 * упавший тест невозможно было бы прочитать.
 *
 * Ловушка репозитория: файлы лежат с CRLF. Ни одна проверка здесь не завязана
 * на перевод строки — на этом уже горели в tests/mjs-тестах.
 */

// ── проценты ─────────────────────────────────────────────────────────────────

test('доля превращается в проценты с одним знаком', () => {
    assert.equal(formatPercent(0.5083), '50,8 %');
    assert.equal(formatPercent(0.377), '37,7 %');
    assert.equal(formatPercent(1), '100,0 %');
    // Перевыполнение больше 100 % — обычное дело, обрезать его нельзя.
    assert.equal(formatPercent(1.246), '124,6 %');
});

test('ноль процентов — это не «нет данных»', () => {
    assert.equal(formatPercent(0), '0,0 %');
    assert.equal(formatPercent(null), DASH);
    assert.equal(formatPercent(undefined), DASH);
    assert.equal(formatPercent(''), DASH);
    assert.equal(formatPercent('мусор'), DASH);
});

test('число знаков после запятой задаётся вызывающим', () => {
    assert.equal(formatPercent(0.5083, 0), '51 %');
    assert.equal(formatPercent(0.5083, 2), '50,83 %');
});

// ── числа ────────────────────────────────────────────────────────────────────

test('разряды режутся неразрывным пробелом', () => {
    assert.equal(formatNumber(5787), '5 787');
    assert.equal(formatNumber(30000), '30 000');
    assert.equal(formatNumber(1234567), '1 234 567');
    assert.equal(formatNumber(999), '999');
});

test('ноль показывается нулём, отсутствие — прочерком', () => {
    assert.equal(formatNumber(0), '0');
    assert.equal(formatNumber(null), DASH);
    assert.equal(formatNumber(undefined), DASH);
    assert.equal(formatNumber(Number.NaN), DASH);
});

test('минус настоящий, а «минус нуля» не бывает', () => {
    assert.equal(formatNumber(-12), '−12');
    // Округление −0,4 до целых даёт ноль: знак у него только сбивает с толку.
    assert.equal(formatNumber(-0.4), '0');
});

test('часы округляются до десятых, целые — без «,0»', () => {
    assert.equal(formatHours(8.5), '8,5 ч');
    assert.equal(formatHours(212.44), '212,4 ч');
    assert.equal(formatHours(8), '8 ч');
    assert.equal(formatHours(0), '0 ч');
    assert.equal(formatHours(null), DASH);
});

test('секунды читаются как время, а не как число', () => {
    assert.equal(formatSeconds(125), '2:05');
    assert.equal(formatSeconds(59), '0:59');
    assert.equal(formatSeconds(3725), '1:02:05');
    assert.equal(formatSeconds(0), '0:00');
    assert.equal(formatSeconds(null), DASH);
    assert.equal(formatSeconds(-5), DASH);
});

// ── дельты ───────────────────────────────────────────────────────────────────

test('дельта процентов подписана пунктами, дельта штук — нет', () => {
    // Без «п.п.» одно и то же «+3,8» читается и как пункты дозвона, и как лиды.
    assert.equal(formatDelta(0.038, 'percent', 'up').text, '+3,8 п.п.');
    assert.equal(formatDelta(-0.038, 'percent', 'down').text, '−3,8 п.п.');
    assert.equal(formatDelta(12, 'count', 'up').text, '+12');
    assert.equal(formatDelta(-12, 'count', 'down').text, '−12');
    assert.equal(formatDelta(-1200, 'count', 'down').text, '−1 200');
});

test('цвет дельты берётся из направления сервера, а не из знака', () => {
    // У отказов рост — это плохо, и сервер присылает direction: 'down'.
    assert.equal(formatDelta(120, 'count', 'down').tone, 'red');
    assert.equal(formatDelta(-120, 'count', 'up').tone, 'green');
    // Направления нет — цвета нет: выдумывать смысл роста нельзя.
    assert.equal(formatDelta(120, 'count').tone, '');
});

test('незначимая дельта не красится', () => {
    // Полпункта туда-сюда — обычное колебание, а не новость.
    assert.equal(formatDelta(0.001, 'percent', 'up').tone, '');
    assert.equal(formatDelta(0.02, 'percent', 'up').tone, 'green');
    assert.equal(formatDelta(0, 'count', 'flat').tone, '');
    assert.equal(formatDelta(0, 'count', 'flat').text, '0');
});

test('нет предыдущего периода — нет и дельты', () => {
    assert.deepEqual(formatDelta(null, 'percent', 'up'), { text: DASH, tone: '' });
    assert.deepEqual(formatDelta(undefined, 'count', ''), { text: DASH, tone: '' });
});

test('часы и секунды в дельте несут свою единицу', () => {
    assert.equal(formatDelta(2.5, 'hours', 'up').text, '+2,5 ч');
    assert.equal(formatDelta(-14, 'seconds', 'up').text, '−14 с');
});

// ── периоды ──────────────────────────────────────────────────────────────────

test('пресеты периодов перечислены и подписаны по-русски', () => {
    assert.deepEqual(DATE_PRESETS.map((item) => item.key),
        ['today', 'yesterday', 'week7', 'month', 'prev_month']);
    assert.deepEqual(DATE_PRESETS.map((item) => item.label),
        ['Сегодня', 'Вчера', '7 дней', 'Этот месяц', 'Прошлый месяц']);
});

test('семь дней — это шесть дней назад и сегодня', () => {
    assert.deepEqual(presetRange('week7', '2026-09-11'),
        { from: '2026-09-05', to: '2026-09-11' });
    assert.deepEqual(presetRange('today', '2026-09-11'),
        { from: '2026-09-11', to: '2026-09-11' });
    assert.deepEqual(presetRange('yesterday', '2026-09-01'),
        { from: '2026-08-31', to: '2026-08-31' });
});

test('«этот месяц» заканчивается сегодня, а не последним числом', () => {
    // Будущих суток в данных нет, и они занижали бы средние и выполнение плана.
    assert.deepEqual(presetRange('month', '2026-09-11'),
        { from: '2026-09-01', to: '2026-09-11' });
});

test('прошлый месяц переходит через год целиком', () => {
    assert.deepEqual(presetRange('prev_month', '2026-01-15'),
        { from: '2025-12-01', to: '2025-12-31' });
    // Февраль високосного года — ровно 29 дней, а не 28 и не 30.
    assert.deepEqual(presetRange('prev_month', '2024-03-10'),
        { from: '2024-02-01', to: '2024-02-29' });
});

test('день не уезжает в соседние сутки из-за пояса', () => {
    // Алматы +5: перевод через toISOString() делал бы «сегодня» вчерашним для
    // всего, что открыто до пяти утра. Дата собирается из местных частей.
    const morning = new Date(2026, 8, 11, 2, 30);
    assert.equal(isoDay(morning), '2026-09-11');
    assert.deepEqual(presetRange('today', morning),
        { from: '2026-09-11', to: '2026-09-11' });
    const evening = new Date(2026, 8, 11, 23, 45);
    assert.equal(isoDay(evening), '2026-09-11');
});

test('незнакомый пресет не подменяется чужим периодом', () => {
    // Молча показать не тот отрезок хуже, чем не показать ничего: человек
    // примет цифры за выбранные им.
    assert.equal(presetRange('квартал', '2026-09-11'), null);
    assert.equal(presetRange('', '2026-09-11'), null);
});

test('короткая дата — день и месяц, без года', () => {
    assert.equal(shortDay('2026-09-03'), '03.09');
    assert.equal(shortDay('2026-09-03 14:22:01'), '03.09');
    assert.equal(shortDay(null), DASH);
});

// ── пороги и тепловая карта ──────────────────────────────────────────────────

test('тон выполнения плана берётся по границам направления', () => {
    // Границы приходят из /targets числами процентов: green_from = 100.
    assert.equal(toneForRate(1.05, 100, 80), 'green');
    assert.equal(toneForRate(1, 100, 80), 'green');
    assert.equal(toneForRate(0.92, 100, 80), 'amber');
    assert.equal(toneForRate(0.8, 100, 80), 'amber');
    assert.equal(toneForRate(0.69, 100, 80), 'red');
});

test('границы понимаются и долями, и процентами', () => {
    // На одном экране порог приходит как 80, на другом как 0.8 — разойтись им
    // нельзя, иначе «0.92 >= 80» никогда не сработает и зелёного не будет.
    assert.equal(toneForRate(0.92, 1, 0.8), 'amber');
    assert.equal(toneForRate(1.4, 1, 0.8), 'green');
});

test('нет данных — нет цвета', () => {
    assert.equal(toneForRate(null, 100, 80), '');
    assert.equal(toneForRate(undefined), '');
    // Ноль — это измеренный провал, его красим.
    assert.equal(toneForRate(0, 100, 80), 'red');
});

test('заливка тепловой карты растёт с выполнением и не зашкаливает', () => {
    assert.equal(heatAlpha(null), 0);
    assert.equal(heatAlpha(undefined), 0);
    const low = heatAlpha(0);
    const mid = heatAlpha(0.8);
    const high = heatAlpha(1.2);
    assert.ok(low > 0, 'ноль плана всё равно закрашен — иначе он неотличим от пустого дня');
    assert.ok(low < mid && mid < high, 'насыщенность обязана расти вместе с выполнением');
    assert.ok(heatAlpha(5) <= 1, 'альфа не может выйти за единицу');
    assert.equal(heatAlpha(5), heatAlpha(2), 'выше потолка заливка одинаковая');
});

// ── подписи ──────────────────────────────────────────────────────────────────

test('шаги воронки подписаны по-русски', () => {
    assert.equal(funnelStepLabel('handled'), 'Обработано');
    assert.equal(funnelStepLabel('reached'), 'Дозвон');
    assert.equal(funnelStepLabel('agreed'), 'Согласия');
    assert.equal(funnelStepLabel('succeeded'), 'Успешно');
    // Новое поле сервера видно как есть, а не пропадает с экрана.
    assert.equal(funnelStepLabel('new_field'), 'new_field');
});

test('корзины причин названы так, как их читает руководитель', () => {
    assert.deepEqual(BUCKET_ORDER, ['nedozvon', 'otkaz', 'netsel', 'moved']);
    assert.equal(reasonBucketLabel('nedozvon'), 'Недозвон');
    assert.equal(reasonBucketLabel('otkaz'), 'Отказы');
    assert.equal(reasonBucketLabel('netsel'), 'Нецелевые');
    // «Увели» без пояснения читается как потеря, а лид просто ушёл в другой
    // процесс и в отказы не попадает (metrics.BUCKET_MOVED).
    assert.equal(reasonBucketLabel('moved'), 'Увели в другой процесс');
});

test('направления совпадают с кодами моделей расчёта на сервере', () => {
    assert.deepEqual(DIRECTIONS.map((item) => item.code),
        ['op_osnova', 'op_potok', 'op_yandex_reg', 'op_verificator']);
    DIRECTIONS.forEach((item) => {
        assert.ok(item.title && item.short && item.hint, `у ${item.code} нет подписи`);
    });
    // У Верификатора обзвона нет: воронка переходов ему не рисуется.
    assert.equal(isLoadDirection('op_verificator'), true);
    assert.equal(isLoadDirection('op_potok'), false);
});

test('нормы подписаны с единицей измерения', () => {
    ['reached_per_hour', 'agreed_per_hour', 'plan_per_fte', 'chats_per_hour',
        'reply_seconds', 'quality', 'weight_reply', 'weight_chats', 'weight_quality',
        'green_from', 'amber_from'].forEach((metric) => {
        assert.ok(METRIC_LABELS[metric], `норма ${metric} осталась без подписи`);
    });
    assert.equal(METRIC_LABELS.reached_per_hour, 'Норма дозвонов в час');
    // «120» в поле времени ответа без единицы правили как минуты.
    assert.match(METRIC_LABELS.reply_seconds, /сек/);
});

// ── аномалии ─────────────────────────────────────────────────────────────────

test('аномалия объясняется фразой с числами, а не кодом', () => {
    const zero = anomalyText({
        kind: 'zero_hours_with_leads', work_day: '2026-09-03', detail: { handled: 41 },
    });
    assert.match(zero, /03\.09/);
    assert.match(zero, /41 лид/);

    const jump = anomalyText({
        kind: 'volume_jump', work_day: '2026-09-04', detail: { was: 10, became: 40 },
    });
    assert.match(jump, /с 10 до 40/);

    const streak = anomalyText({
        kind: 'below_target_streak', work_day: '2026-09-05', detail: { days: 2, rate: 0.68 },
    });
    assert.match(streak, /2 дня подряд/);
    assert.match(streak, /68,0 %/);
});

test('имя оператора в фразу не входит', () => {
    // Панель показывает его отдельной строкой: одно число или имя дважды на
    // одном экране — это шум, а шум владелец считает браком.
    const text = anomalyText({
        kind: 'zero_hours_with_leads', work_day: '2026-09-03',
        name: 'Кузембекова Аяулым', detail: { handled: 41 },
    });
    assert.ok(!text.includes('Кузембекова'));
});

test('незнакомый флаг не роняет панель', () => {
    assert.match(anomalyText({ kind: 'что-то новое', work_day: '2026-09-03' }), /03\.09/);
    assert.equal(typeof anomalyText(null), 'string');
});
