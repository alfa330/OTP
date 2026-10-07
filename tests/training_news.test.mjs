import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

/* Объявление Oktell за интервалом «Тренинг» (задача #382): чистая логика из
 * src/components/schedule/trainingNews.js.
 *
 * Модуль исполняется напрямую, без сборщика: в нём нет ни JSX, ни импортов.
 * Числа в примерах — из замера на проде 07.10.2026: статус АТС начинается за
 * секунду до показа объявления и кончается за секунду до подтверждения.
 */

const source = readFileSync(new URL('../src/components/schedule/trainingNews.js', import.meta.url), 'utf8');
const module = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

const {
    NEWS_TRAINING_REASON,
    normalizeNewsWindows, newsForSegment,
    buildNewsComment, formatNewsWindow, planTrainingSaves,
} = module;

const at = (hh, mm, ss = 0) => hh * 3600 + mm * 60 + ss;
const seg = (startSec, endSec) => ({ startSec, endSec });
const win = (newsId, title, startSec, endSec = null) => ({ newsId, title, startSec, endSec });
const ids = (matches) => matches.map((match) => match.newsId);

/* ── Разбор ответа сервера ──────────────────────────────────────────────── */

test('окна читаются из ответа сервера и встают по времени показа', () => {
    const windows = normalizeNewsWindows([
        { news_id: 2, title: 'Вторая', start_sec: 500, end_sec: 560 },
        { news_id: 1, title: ' Первая ', start_sec: 100, end_sec: 147 },
    ]);
    assert.deepEqual(windows, [
        { newsId: 1, title: 'Первая', startSec: 100, endSec: 147 },
        { newsId: 2, title: 'Вторая', startSec: 500, endSec: 560 },
    ]);
});

test('окно без названия или без времени показа выбрасывается', () => {
    assert.deepEqual(normalizeNewsWindows([
        { news_id: 1, title: '', start_sec: 100, end_sec: 147 },
        { news_id: 2, title: 'Без показа', start_sec: null, end_sec: 147 },
        { news_id: 3, title: 'Мусор', start_sec: 'abc' },
    ]), []);
    assert.deepEqual(normalizeNewsWindows(null), []);
    assert.deepEqual(normalizeNewsWindows(undefined), []);
});

test('неподтверждённое окно приходит без конца', () => {
    const [open] = normalizeNewsWindows([{ news_id: 1, title: 'Ждёт', start_sec: 100, end_sec: null }]);
    assert.equal(open.endSec, null);
    // Конец раньше начала — не отрицательная длительность, а «не подтверждено».
    const [broken] = normalizeNewsWindows([{ news_id: 1, title: 'Ждёт', start_sec: 100, end_sec: 40 }]);
    assert.equal(broken.endSec, null);
});

/* ── Какое объявление объясняет интервал ────────────────────────────────── */

test('интервал статуса получает объявление, окно которого с ним совпало', () => {
    // Статус 10:02:11–10:03:39, окно объявления на секунду позже с обеих сторон.
    const windows = [win(39, 'Новый тариф', at(10, 2, 12), at(10, 3, 40))];
    assert.deepEqual(ids(newsForSegment(seg(at(10, 2, 11), at(10, 3, 39)), windows)), [39]);
});

test('интервал без объявления остаётся без объявления', () => {
    const windows = [win(39, 'Новый тариф', at(10, 2, 12), at(10, 3, 40))];
    assert.deepEqual(newsForSegment(seg(at(14, 0, 0), at(14, 45, 0)), windows), []);
    assert.deepEqual(newsForSegment(seg(at(10, 2, 11), at(10, 3, 39)), []), []);
    assert.deepEqual(newsForSegment(seg(at(10, 2, 11), at(10, 3, 39)), null), []);
});

test('очередь объявлений подряд не приписывает соседу чужое объявление', () => {
    // Второе окно открывается через секунду после подтверждения первого.
    const first = win(1, 'Первое', at(10, 0, 5), at(10, 0, 52));
    const second = win(2, 'Второе', at(10, 0, 54), at(10, 2, 10));
    const windows = [first, second];
    assert.deepEqual(ids(newsForSegment(seg(at(10, 0, 4), at(10, 0, 51)), windows)), [1]);
    assert.deepEqual(ids(newsForSegment(seg(at(10, 0, 53), at(10, 2, 9)), windows)), [2]);
});

test('склеенный интервал несёт все объявления, которые в него попали', () => {
    const windows = [
        win(1, 'Первое', at(10, 0, 5), at(10, 0, 52)),
        win(2, 'Второе', at(10, 0, 52), at(10, 2, 10)),
    ];
    assert.deepEqual(ids(newsForSegment(seg(at(10, 0, 4), at(10, 2, 9)), windows)), [1, 2]);
});

test('окно, открытое вечером и подтверждённое утром, объясняет оба интервала', () => {
    // Секунды считаются от полуночи УТРЕННЕГО дня: вечерний показ — отрицательный.
    const overnight = win(7, 'График на праздники', at(18, 59, 10) - 86400, at(9, 1, 0));
    assert.deepEqual(ids(newsForSegment(seg(at(9, 0, 10), at(9, 0, 59)), [overnight])), [7]);
    // И тот же показ глазами вечернего дня: подтверждение ушло за 24 часа.
    const sameFromEvening = win(7, 'График на праздники', at(18, 59, 10), at(9, 1, 0) + 86400);
    assert.deepEqual(ids(newsForSegment(seg(at(18, 59, 9), at(19, 0, 0)), [sameFromEvening])), [7]);
});

test('неподтверждённое объявление объясняет только интервал, в котором его показали', () => {
    const pending = win(5, 'Не дочитал', at(18, 50, 0), null);
    assert.deepEqual(ids(newsForSegment(seg(at(18, 49, 59), at(19, 0, 0)), [pending])), [5]);
    // Снятое без подтверждения объявление не подписывает завтрашние тренинги.
    assert.deepEqual(newsForSegment(seg(at(19, 30, 0), at(20, 0, 0)), [pending]), []);
    assert.deepEqual(newsForSegment(seg(at(9, 0, 0) + 86400, at(9, 30, 0) + 86400), [pending]), []);
});

test('короткое объявление внутри долгого тренинга показывается при нём', () => {
    // Человек уже был в «Тренинге», когда пришло объявление: супервайзер должен
    // увидеть и то, и другое — интервал час, объявление минута.
    const windows = [win(9, 'Минутное', at(10, 30, 0), at(10, 31, 0))];
    assert.deepEqual(ids(newsForSegment(seg(at(10, 0, 0), at(11, 0, 0)), windows)), [9]);
});

test('интервал без секунд сверяется по минутам', () => {
    const windows = [win(39, 'Новый тариф', at(10, 2, 12), at(10, 3, 40))];
    assert.deepEqual(ids(newsForSegment({ startMin: 602, endMin: 604 }, windows)), [39]);
    assert.deepEqual(newsForSegment({ startMin: 604, endMin: 602 }, windows), []);
    assert.deepEqual(newsForSegment({}, windows), []);
});

test('одно объявление не повторяется у интервала дважды', () => {
    const twice = [
        win(1, 'Первое', at(10, 0, 5), at(10, 0, 52)),
        win(1, 'Первое', at(10, 0, 5), at(10, 0, 52)),
    ];
    assert.equal(newsForSegment(seg(at(10, 0, 4), at(10, 0, 51)), twice).length, 1);
});

/* ── Что уходит в «Тренинги» ────────────────────────────────────────────── */

test('тема записи — существующая тема раздела «Тренинги»', () => {
    assert.equal(NEWS_TRAINING_REASON, 'Тренинг по продукту');
});

test('комментарий записи называет объявление', () => {
    assert.equal(buildNewsComment([win(1, 'Новый тариф', 0, 10)]), 'Новость в Oktell: «Новый тариф»');
    assert.equal(
        buildNewsComment([win(1, 'Первое', 0, 10), win(2, 'Второе', 20, 30), win(3, 'Первое', 40, 50)]),
        'Новость в Oktell: «Первое»; «Второе»',
    );
    assert.equal(buildNewsComment([]), '');
    assert.equal(buildNewsComment(null), '');
});

test('время окна пишется с секундами, как у интервала статуса', () => {
    assert.equal(formatNewsWindow(win(1, 'А', at(10, 2, 12), at(10, 3, 40))), '10:02:12 — 10:03:40');
    assert.equal(formatNewsWindow(win(1, 'А', at(18, 59, 10) - 86400, at(9, 1, 0))), 'вчера 18:59:10 — 09:01:00');
    assert.equal(formatNewsWindow(win(1, 'А', at(18, 59, 10), at(9, 1, 0) + 86400)), '18:59:10 — завтра 09:01:00');
    assert.equal(formatNewsWindow(win(1, 'А', at(18, 50, 0), null)), 'с 18:50:00, не подтверждена');
    assert.equal(formatNewsWindow(win(1, 'А', at(9, 0, 0) - 3 * 86400, at(9, 1, 0))), '3 дн. назад 09:00:00 — 09:01:00');
    assert.equal(formatNewsWindow(null), '');
});

/* ── Записи при подтверждении ───────────────────────────────────────────── */

const ranges = (saves) => saves.map((save) => `${save.startTime}-${save.endTime}`);
const titlesOf = (saves) => saves.map((save) => save.news.map((match) => match.title));

test('запись накрывает интервал целиком: начало вниз, конец вверх', () => {
    // 10:00:40–10:00:58. Округление к ближайшей минуте дало бы 10:01–10:02 —
    // запись мимо интервала, и он навсегда оставался бы «ожидает».
    const [save] = planTrainingSaves([seg(at(10, 0, 40), at(10, 0, 58))]);
    assert.equal(save.startTime, '10:00');
    assert.equal(save.endTime, '10:01');
    assert.deepEqual(save.news, []);
});

test('запись получает объявление своего интервала', () => {
    const windows = [win(39, 'Новый тариф', at(10, 2, 12), at(10, 3, 40))];
    const saves = planTrainingSaves([seg(at(10, 2, 11), at(10, 3, 39))], { windows });
    assert.deepEqual(ranges(saves), ['10:02-10:04']);
    assert.deepEqual(titlesOf(saves), [['Новый тариф']]);
});

test('объявления подряд ложатся соседними записями, каждая со своим названием', () => {
    const windows = [
        win(1, 'Первое', at(10, 0, 5), at(10, 0, 52)),
        win(2, 'Второе', at(10, 0, 54), at(10, 1, 20)),
    ];
    const saves = planTrainingSaves([
        seg(at(10, 0, 53), at(10, 1, 19)),
        seg(at(10, 0, 4), at(10, 0, 51)),
    ], { windows });
    // Вторая начинается там, где кончилась первая: наезда сервер не примет.
    assert.deepEqual(ranges(saves), ['10:00-10:01', '10:01-10:02']);
    assert.deepEqual(titlesOf(saves), [['Первое'], ['Второе']]);
});

test('интервалы одной минуты сливаются в одну запись с обоими объявлениями', () => {
    const windows = [
        win(2, 'Второе', at(10, 1, 5), at(10, 1, 20)),
        win(3, 'Третье', at(10, 1, 23), at(10, 1, 41)),
    ];
    const saves = planTrainingSaves([
        seg(at(10, 1, 4), at(10, 1, 19)),
        seg(at(10, 1, 22), at(10, 1, 40)),
    ], { windows });
    assert.deepEqual(ranges(saves), ['10:01-10:02']);
    assert.deepEqual(titlesOf(saves), [['Второе', 'Третье']]);
});

test('сохранённый тренинг — стена: запись поджимается к его краю', () => {
    // Первое объявление уже подтверждено записью 10:00–10:01.
    const busy = [{ startMin: 600, endMin: 601 }];
    const saves = planTrainingSaves([seg(at(10, 0, 53), at(10, 1, 19))], { busy });
    assert.deepEqual(ranges(saves), ['10:01-10:02']);
    // И с другой стороны: тренинг стоит сразу после интервала.
    const after = planTrainingSaves([seg(at(10, 0, 5), at(10, 1, 10))], { busy: [{ startMin: 601, endMin: 630 }] });
    assert.deepEqual(ranges(after), ['10:00-10:01']);
});

test('интервал, середина которого уже под сохранённым тренингом, второй записи не получает', () => {
    const busy = [{ startMin: 600, endMin: 602 }];
    assert.deepEqual(planTrainingSaves([seg(at(10, 0, 30), at(10, 1, 10))], { busy }), []);
});

test('последняя минута суток режется до 23:59', () => {
    const saves = planTrainingSaves([seg(at(23, 50, 10), at(23, 59, 59))]);
    assert.deepEqual(ranges(saves), ['23:50-23:59']);
    // Интервал целиком в последней минуте записать нечем — и он не ломает остальные.
    const mixed = planTrainingSaves([seg(at(23, 59, 10), at(23, 59, 50)), seg(at(9, 0, 5), at(9, 0, 40))]);
    assert.deepEqual(ranges(mixed), ['09:00-09:01']);
    // Сосед, упёршийся в срезанный конец суток, пустой записи не оставляет.
    const windows = [win(4, 'Под занавес', at(23, 58, 31), at(23, 59, 58))];
    const tail = planTrainingSaves([
        seg(at(23, 57, 0), at(23, 58, 20)),
        seg(at(23, 58, 30), at(23, 59, 59)),
    ], { windows });
    assert.deepEqual(ranges(tail), ['23:57-23:59']);
    assert.deepEqual(titlesOf(tail), [['Под занавес']]);
});

test('пустой и кривой ввод записей не даёт', () => {
    assert.deepEqual(planTrainingSaves([]), []);
    assert.deepEqual(planTrainingSaves(null), []);
    assert.deepEqual(planTrainingSaves([{}, seg(100, 50), { startMin: 5, endMin: 5 }]), []);
});

test('на любом наборе интервалов записи не наезжают ни друг на друга, ни на стены', () => {
    // Детерминированный «случайный» перебор: соседние короткие интервалы — ровно
    // тот случай, который даёт очередь объявлений.
    let seed = 382;
    const next = (limit) => {
        seed = (seed * 1103515245 + 12345) % 2147483648;
        return seed % limit;
    };
    for (let round = 0; round < 400; round += 1) {
        const segments = [];
        let cursor = at(8, 0, 0) + next(600);
        const count = 1 + next(7);
        for (let i = 0; i < count; i += 1) {
            const start = cursor + next(90);
            const end = start + 1 + next(200);
            segments.push(seg(start, end));
            cursor = end + 1;
        }
        const busy = [];
        if (next(3) === 0) {
            const wallStart = Math.floor(segments[0].startSec / 60) + next(6);
            busy.push({ startMin: wallStart, endMin: wallStart + 1 + next(3) });
        }
        const saves = planTrainingSaves(segments, { busy });

        saves.forEach((save, index) => {
            assert.ok(save.endMin > save.startMin, `пустая запись в раунде ${round}`);
            if (index > 0) {
                assert.ok(save.startMin >= saves[index - 1].endMin, `записи наехали в раунде ${round}`);
            }
            busy.forEach((wall) => {
                const crosses = save.startMin < wall.endMin && wall.startMin < save.endMin;
                assert.ok(!crosses, `запись наехала на сохранённый тренинг в раунде ${round}`);
            });
        });

        // Главное: середина каждого интервала накрыта записью или стеной —
        // иначе интервал остался бы «ожидает» после подтверждения.
        segments.forEach((segment) => {
            const middle = (segment.startSec + segment.endSec) / 120;
            const covered = [...saves, ...busy].some((item) => item.startMin <= middle && middle < item.endMin);
            assert.ok(covered, `середина интервала не накрыта в раунде ${round}`);
        });
    }
});
