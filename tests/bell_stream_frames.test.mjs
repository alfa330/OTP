import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { bellResumeHeaders, parseBellFrame } from '../src/components/notifications/bellStream.js';

// Поток колокола с 27.10.2026: возврат во вкладку продолжает поток с прежнего
// места, а новость дня спрашивается только на широковещательный тычок.

test('кадры нового сервера: процесс, курсор, продолжение, широковещательность', () => {
    assert.deepEqual(parseBellFrame(': connected 1761570000 abc123 42 1'),
        { kind: 'connected', epoch: 'abc123', seq: 42, resumed: true, broadcast: true });
    assert.equal(parseBellFrame(': connected 1761570000 abc123 42 0').resumed, false);
    assert.deepEqual(parseBellFrame('id: 43\nevent: reload\ndata: {"b":0}'),
        { kind: 'reload', epoch: null, seq: 43, resumed: false, broadcast: false });
    assert.equal(parseBellFrame('id: 44\nevent: reload\ndata: {"b":1}').broadcast, true);
    assert.deepEqual(parseBellFrame(': heartbeat 1761570025 44'),
        { kind: 'heartbeat', epoch: null, seq: 44, resumed: false, broadcast: true });
});

test('кадры прежнего сервера читаются как раньше: без продолжения, тычок — для всех', () => {
    const connected = parseBellFrame(': connected 1761570000');
    assert.equal(connected.kind, 'connected');
    assert.equal(connected.resumed, false, 'без процесса и курсора продолжать нечего');
    const reload = parseBellFrame('event: reload\ndata: {}');
    assert.equal(reload.kind, 'reload');
    assert.equal(reload.broadcast, true, 'новость спрашивается, как до 27.10.2026');
    assert.equal(parseBellFrame(': heartbeat 1761570025').seq, null);
    assert.equal(parseBellFrame('event: reload\ndata: не-json').broadcast, true);
});

test('продолжение — только с процессом и целым курсором', () => {
    assert.deepEqual(bellResumeHeaders({ epoch: 'abc', seq: 7 }), { 'Last-Event-ID': 'abc:7' });
    assert.deepEqual(bellResumeHeaders({ epoch: null, seq: 7 }), {});
    assert.deepEqual(bellResumeHeaders({ epoch: 'abc', seq: null }), {});
    assert.deepEqual(bellResumeHeaders(null), {});
    // Флаг «продолжен» без курсора — не продолжение.
    assert.equal(parseBellFrame(': connected 1 abc x 1').resumed, false);
});

const bell = readFileSync(new URL('../src/components/notifications/NotificationsBell.jsx', import.meta.url), 'utf8')
    .replace(/\r\n/g, '\n');

test('колокол: продолженный поток не перечитывает сводку и не дёргает новость', () => {
    const connected = bell.slice(bell.indexOf("} else if (frame.kind === 'connected') {"), bell.indexOf('if (poked && !pokeTimer) {'));
    assert.ok(connected.indexOf('if (frame.resumed) continue;') > 0);
    assert.ok(connected.indexOf('if (frame.resumed) continue;') < connected.indexOf('load();'),
        'проверка продолжения стоит до перечитки');
    assert.match(bell, /Accept: 'text\/event-stream', \.\.\.bellResumeHeaders\(resume\)/);
});

test('колокол: новость дня — только на широковещательный тычок, и он не теряется в склейке', () => {
    const start = bell.indexOf('if (poked && !pokeTimer) {');
    const poke = bell.slice(start, bell.indexOf('} catch (e) {', start));
    assert.equal((poke.match(/if \(news\) streamPokeRef\.current\?\.\(\);/g) || []).length, 2,
        'и в фоне, и после джиттера');
    assert.doesNotMatch(poke, /load\(\);\s*streamPokeRef\.current\?\.\(\);/, 'безусловного тычка больше нет');
    assert.match(bell, /pokeBroadcast \|\|= frame\.broadcast;/);
    assert.equal((poke.match(/pokeBroadcast = false;/g) || []).length, 2, 'признак сбрасывается вместе с тычком');
});

test('окно новости: возврат во вкладку — не чаще раза в 5 минут, первый запрос мимо гарда', () => {
    const modal = readFileSync(new URL('../src/components/news/NewsOfDayModal.jsx', import.meta.url), 'utf8')
        .replace(/\r\n/g, '\n');
    assert.match(modal, /const NEWS_WAKE_GAP_MS = 5 \* 60 \* 1000;/);
    const wake = modal.slice(modal.indexOf('const onWake = () => {'), modal.indexOf("window.addEventListener('focus', onWake);"));
    assert.match(wake, /performance\.now\(\) - lastLoadRef\.current < NEWS_WAKE_GAP_MS\) return;/);
    assert.match(modal, /useEffect\(\(\) => \{ load\(true\); \}, \[load\]\);/);
    assert.match(modal, /subscribeNewsPoke\(\(\) => load\(\)\)/, 'тычок колокола — с прежним гардом в 4 с');
});
