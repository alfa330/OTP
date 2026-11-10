/*
 * «Удаленный КЦ» → «Аналитика»: строка проверки подписания с отсчётом до следующего обновления
 * (владелец, 10.11.2026: «отображать ближайшее обновление… обратный отсчёт»; сама проверка с этого дня идёт
 * по кругу без перерыва, а не раз в 3 часа). Правила строки — src/components/dial_list/signCycle.js (без React),
 * круг и его оценку ведёт сервер (dial_list/sign_check.py).
 *
 * Что защищаем:
 *   • отсчёт идёт по часам сервера, а не компьютера;
 *   • в строке — когда обновлено и сколько до следующего обновления; оценка вышла — «идёт обновление…», а не
 *     отсчёт в минус;
 *   • старая база и сервер без Sapar — без отсчёта и без своих перезапросов;
 *   • числа на вкладке обновляются сами в конце круга, но не чаще раза в 5 секунд и не реже раза в 5 минут;
 *     после неудачного своего запроса — пауза, а не стук каждые секунды;
 *   • экран берёт всё это из signCycle.js, а «раз в 3 часа» в подсказках раздела больше нет.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
    RELOAD_MAX_MS, RELOAD_MIN_MS, RELOAD_UNKNOWN_MS, clockOffset, fmtCountdown, msLeft, reloadDelay, signCycleLine,
} from '../src/components/dial_list/signCycle.js';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8').split('\r\n').join('\n');

const NOW = Date.parse('2026-11-10T14:07:30Z');
const fmtWhen = (iso) => `в ${iso.slice(11, 16)}`;
const line = (check, nowMs = NOW, offsetMs = 0) => signCycleLine(check, { nowMs, offsetMs, fmtWhen });
const cycle = (extra = {}) => ({
    active: true, configured: true, updating: true,
    server_now: '2026-11-10T14:07:30+00:00',
    updated_at: '2026-11-10T14:03:30+00:00',
    last_checked_at: '2026-11-10T14:06:00+00:00',
    next_update_at: '2026-11-10T14:15:12+00:00',
    ...extra,
});

test('часы сервера: на сколько они впереди часов компьютера', () => {
    assert.equal(clockOffset(cycle(), NOW), 0);
    assert.equal(clockOffset(cycle(), NOW - 90000), 90000);      // компьютер отстаёт на полторы минуты
    assert.equal(clockOffset(cycle(), NOW + 4000), -4000);       // спешит на 4 секунды
    assert.equal(clockOffset({}, NOW), 0);
    assert.equal(clockOffset(undefined, NOW), 0);
    assert.equal(clockOffset({ server_now: 'не время' }, NOW), 0);
});

test('отсчёт — по часам сервера', () => {
    assert.equal(msLeft(cycle(), 0, NOW), 462000);
    // Часы компьютера отстают на полторы минуты: без поправки отсчёт показал бы лишние 90 секунд.
    assert.equal(msLeft(cycle(), 90000, NOW - 90000), 462000);
    assert.equal(msLeft(cycle({ next_update_at: null }), 0, NOW), null);
    assert.equal(msLeft(undefined, 0, NOW), null);
});

test('вид отсчёта', () => {
    assert.equal(fmtCountdown(462), '07:42');
    assert.equal(fmtCountdown(59), '00:59');
    assert.equal(fmtCountdown(60), '01:00');
    assert.equal(fmtCountdown(3909), '1:05:09');
    assert.equal(fmtCountdown(0), '00:00');
    assert.equal(fmtCountdown(-5), '00:00');
    assert.equal(fmtCountdown(undefined), '00:00');
});

test('строка: когда обновлено и сколько до следующего обновления', () => {
    assert.deepEqual(line(cycle()), { text: 'обновлено в 14:03', countdown: '07:42' });
    // Секунда на исходе считается целой: «00:01», пока она не кончилась.
    assert.deepEqual(line(cycle(), Date.parse('2026-11-10T14:15:11.400Z')), { text: 'обновлено в 14:03', countdown: '00:01' });
    // Сервер только что перезапустился и полного круга ещё не было — берём время последней записи.
    assert.deepEqual(line(cycle({ updated_at: null })), { text: 'обновлено в 14:06', countdown: '07:42' });
});

test('оценка вышла, а новых чисел ещё нет — «идёт обновление», а не отсчёт в минус', () => {
    const late = Date.parse('2026-11-10T14:15:12Z');
    assert.deepEqual(line(cycle(), late), { text: 'обновлено в 14:03 · идёт обновление…', countdown: '' });
    assert.deepEqual(line(cycle(), late + 40000), { text: 'обновлено в 14:03 · идёт обновление…', countdown: '' });
    // Оценки нет вовсе (первые секунды круга после перезапуска сервера), но круг идёт.
    assert.deepEqual(line(cycle({ next_update_at: null })), { text: 'обновлено в 14:03 · идёт обновление…', countdown: '' });
    assert.deepEqual(line(cycle({ next_update_at: null, updating: false })), { text: 'обновлено в 14:03', countdown: '' });
});

test('без отсчёта: старая база, сервер без Sapar, база без единой проверки', () => {
    assert.deepEqual(line(cycle({ active: false })), { text: 'эта база больше не проверяется', countdown: '' });
    assert.deepEqual(line(cycle({ configured: false })), { text: 'проверено в 14:06', countdown: '' });
    assert.deepEqual(line(cycle({ configured: false, last_checked_at: null })), { text: '', countdown: '' });
    assert.deepEqual(line(cycle({ updated_at: null, last_checked_at: null })),
        { text: 'первая проверка — в ближайшие минуты', countdown: '' });
    assert.deepEqual(line(undefined), { text: '', countdown: '' });
});

test('числа обновляются сами в конце круга — не слишком часто и не слишком редко', () => {
    assert.equal(RELOAD_MIN_MS, 5000);
    assert.equal(RELOAD_MAX_MS, 300000);
    // До конца круга 100 секунд — спросим через 102: две секунды на запись последних ответов.
    assert.equal(reloadDelay(cycle({ next_update_at: '2026-11-10T14:09:10+00:00' }), 0, NOW), 102000);
    // До конца 7 минут 42 секунды — всё равно заглянем через 5 минут: статусы меняются и по ходу круга.
    assert.equal(reloadDelay(cycle(), 0, NOW), 300000);
    // Оценка уже вышла (вкладка долго была не на экране) — через 5 секунд, не мгновенно и не в минус.
    assert.equal(reloadDelay(cycle({ next_update_at: '2026-11-10T13:00:00+00:00' }), 0, NOW), 5000);
    assert.equal(reloadDelay(cycle({ next_update_at: '2026-11-10T14:07:31+00:00' }), 0, NOW), 5000);
    // Оценки нет — спросим через 20 секунд.
    assert.equal(reloadDelay(cycle({ next_update_at: null }), 0, NOW), RELOAD_UNKNOWN_MS);
    // Часы компьютера отстают — считаем по серверным.
    assert.equal(reloadDelay(cycle({ next_update_at: '2026-11-10T14:09:10+00:00' }), 90000, NOW - 90000), 102000);
    // Прошлый свой запрос не удался (сеть, вышла сессия) — пауза 5 минут, а не стук каждые 5 секунд.
    assert.equal(reloadDelay(cycle({ next_update_at: '2026-11-10T13:00:00+00:00' }), 0, NOW, true), 300000);
});

test('старая база и сервер без Sapar сами ничего не перезапрашивают', () => {
    assert.equal(reloadDelay(cycle({ active: false }), 0, NOW), null);
    assert.equal(reloadDelay(cycle({ configured: false }), 0, NOW), null);
    assert.equal(reloadDelay(undefined, 0, NOW), null);
    assert.equal(reloadDelay(cycle({ active: false }), 0, NOW, true), null);
});

test('экран: строка с отсчётом и свой перезапрос — из signCycle.js', () => {
    const source = read('../src/components/dial_list/DialListAnalytics.jsx');
    assert.match(source, /import \{ clockOffset, reloadDelay, signCycleLine \} from '\.\/signCycle';/);
    const lineSource = source.slice(source.indexOf('const SignCheckLine'), source.indexOf('/* Документы:'));
    assert.match(lineSource, /signCycleLine\(check, \{ nowMs, offsetMs, fmtWhen \}\)/);
    // Отсчёт тикает раз в секунду и стоит отдельным неразрывным куском (вместе с «i»): в тесной шапке
    // режется не он, а начало строки; на телефоне он уходит на свою строку.
    assert.match(lineSource, /setInterval\(\(\) => setNowMs\(Date\.now\(\)\), 1000\)/);
    assert.match(lineSource, /\{line\.countdown \? \(\s*<span className="flex shrink-0 basis-full items-center gap-1\.5 whitespace-nowrap sm:basis-auto">/);
    assert.match(lineSource, /следующее через <span className="font-medium tabular-nums text-slate-700">\{line\.countdown\}<\/span>\s*<\/span>\s*\{hint\}\s*<\/span>\s*\) : hint\}/);
    const view = source.slice(source.indexOf('const DialListAnalytics'));
    // Кнопки слева не сжимаются: в тесной шапке «Отчёт в Telegram» наезжал на строку справа.
    assert.match(view, /<div className="flex items-center gap-2 sm:shrink-0">\s*<CustomSelect/);
    assert.match(view, /<SignCheckLine check=\{check\} offsetMs=\{clock\} \/>/);
    assert.match(view, /setClock\(clockOffset\(body\?\.sign_check, Date\.now\(\)\)\)/);
    assert.match(view, /const delay = reloadDelay\(check, clock, Date\.now\(\), reloads\.failed\);/);
    assert.match(view, /if \(delay == null \|\| !visible \|\| loading\) return undefined;/);
    assert.match(view, /setTimeout\(\(\) => load\(\{ silent: true \}\), delay\)/);
    // Свой перезапрос — без затемнения вкладки и без текста ошибки; устаревший ответ не затирает свежий.
    assert.match(view, /if \(!silent\) \{\s*setLoading\(true\);\s*setError\(''\);\s*\}/);
    assert.match(view, /if \(mine !== requestRef\.current\) return;/);
    // Кнопка «Обновить» зовёт загрузку без события в аргументе.
    assert.match(view, /onClick=\{\(\) => load\(\)\}/);
});

test('подсказки раздела больше не обещают «раз в 3 часа»', () => {
    const files = [
        '../src/components/dial_list/DialListAnalytics.jsx',
        '../src/components/dial_list/DialListJournal.jsx',
        '../src/components/dial_list/DialListInformerPanel.jsx',
        '../src/components/sip/DialListSettings.jsx',
    ];
    for (const file of files) {
        const text = read(file);
        assert.doesNotMatch(text, /[Рр]аз в (3|три) часа сервер|проверяется по ИИН в Sapar раз в 3 часа|проверка раз в три часа|по нему раз в 3 часа/, file);
    }
    const analytics = read(files[0]);
    const hint = analytics.slice(analytics.indexOf('const RULES_HINT'), analytics.indexOf('const Notice'));
    assert.match(hint, /Сервер по кругу спрашивает Sapar по ИИН о каждом неподписавшем водителе базы/);
    // Раз в 3 часа остались только те, у кого срок подписания истёк, — об этом сказано прямо.
    assert.match(hint, /Тех, у кого срок '\s*\+ 'подписания истёк, сервер спрашивает раз в 3 часа\./);
});
