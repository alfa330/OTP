/*
 * Шарик помощника над «Вики» (решение владельца 29.10.2026): виден на всех
 * вкладках раздела, кроме «Помощника», и отвечает по той вике, что открыта.
 *
 * Правило живёт в двух местах, и каждое здесь проживает несколько кадров на
 * стенде входа (tests/wiki_entry_lab.mjs): раздел сообщает порталу вкладку и
 * пространство (WikiView: onTabShown, onSpaceShown), шарик по ним гаснет и
 * спрашивает сервер (AssistantOrb: isSuppressed, askSpace). Что портал передаёт
 * одно в другое — tests/test_assistant_orb.py.
 *
 * Запуск: node --test tests/assistant_orb_wiki_tab.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    API, BASE, HR_SPACE, activeTab, calls, loadSection, mount, pending, pingAnswer,
    read, sectionProps, sent, storage, withAccessTokenHeader, withAddress,
} from './wiki_entry_lab.mjs';

const { WikiView, AssistantOrb, memory } = await loadSection({ lab: true });

const pingKey = (user) => memory.memoryKey(BASE, user.id, 'ping');

/* Вкладки, которые раздел сообщил порталу, по порядку. Без повторов подряд:
   важна смена, а не то, сколько раз раздел перерисовался. */
const tabReports = () => {
    const seen = [];
    const onTabShown = (tab) => { if (seen.at(-1) !== tab) seen.push(tab); };
    return { seen, onTabShown };
};

const TEZ_SPACE = {
    id: 7, name: 'Тез КЦ', code: 'tez', icon: null, description: '', features: {}, guest_only: false,
};

/* ── Раздел: какую вкладку он сообщает ───────────────────────────────────── */

test('главная: раздел сообщает её порталу, и шарик над ней остаётся', async () => {
    const user = { id: 981 };
    memory.remember(pingKey(user), pingAnswer());
    const tabs = tabReports();
    const section = mount(WikiView, sectionProps(user, { onTabShown: tabs.onTabShown }));
    await section.settle();
    assert.equal(activeTab(section.html), 'Главная');
    assert.deepEqual(tabs.seen, ['library']);
});

test('первый вход: до ответа /ping шарик не гасится, ответ ставит главную', async () => {
    const tabs = tabReports();
    const mark = calls.length;
    const section = mount(WikiView, sectionProps({ id: 982 }, { onTabShown: tabs.onTabShown }));
    await section.settle();
    assert.deepEqual(tabs.seen, ['']);
    pending('/ping', mark).answer(pingAnswer());
    await section.settle();
    assert.deepEqual(tabs.seen, ['', 'library']);
});

test('вход прямо в «Помощник» по адресу: вкладка сообщается с первого кадра, а не с ответом /ping', async () => {
    /* Перезагрузка на «Помощнике»: ответа /ping в памяти страницы ещё нет, и
       сообщи раздел до него «главную» или «ничего» — шарик успел бы показаться
       над чатом и исчезнуть. */
    const tabs = tabReports();
    const mark = calls.length;
    await withAddress('?view=wiki&tab=assistant', async () => {
        const section = mount(WikiView, sectionProps({ id: 983 }, { onTabShown: tabs.onTabShown }));
        await section.settle();
        assert.deepEqual(tabs.seen, ['assistant']);
        pending('/ping', mark).answer(pingAnswer());
        await section.settle();
        assert.equal(activeTab(section.html), 'Помощник');
    });
    assert.deepEqual(tabs.seen, ['assistant'], 'шарик на кадр вставал над «Помощником»');
});

test('ссылка на «Помощник» в вике, где его нет: встаёт главная, и шарик появляется', async () => {
    const tabs = tabReports();
    const mark = calls.length;
    await withAddress('?view=wiki&tab=assistant', async () => {
        const section = mount(WikiView, sectionProps({ id: 984 }, { onTabShown: tabs.onTabShown }));
        await section.settle();
        pending('/ping', mark).answer(pingAnswer({ spaces: [HR_SPACE] }));
        await section.settle();
        assert.equal(activeTab(section.html), 'Главная');
    });
    assert.deepEqual(tabs.seen, ['assistant', 'library'], 'погашенный шарик остался над главной');
});

test('«Открыть в разделе «Вики»» из мини-чата без разговора открывает «Помощник»', async () => {
    /* Шарик приходит дверью колокола, но номера разговора у него может не быть:
       человек открыл мини-чат и сразу развернул его. Раньше такая просьба
       молча гасла — колокол без номера считался «просьбы нет». */
    const user = { id: 985 };
    memory.remember(pingKey(user), pingAnswer());
    let consumed = 0;
    const tabs = tabReports();
    const section = mount(WikiView, sectionProps(user, {
        bellFocus: { kind: 'assistant', id: null, nonce: 1 },
        onBellFocusConsumed: () => { consumed += 1; },
        onTabShown: tabs.onTabShown,
    }));
    await section.settle();
    assert.equal(consumed, 1);
    assert.equal(activeTab(section.html), 'Помощник');
    assert.deepEqual(tabs.seen, ['assistant'], 'шарик на кадр вставал над главной перед «Помощником»');
});

test('мини-чат до ответа /ping: «Помощник» сообщается заранее и открывается с ответом', async () => {
    let consumed = 0;
    const tabs = tabReports();
    const mark = calls.length;
    const section = mount(WikiView, sectionProps({ id: 986 }, {
        bellFocus: { kind: 'assistant', id: 41, nonce: 1 },
        onBellFocusConsumed: () => { consumed += 1; },
        onTabShown: tabs.onTabShown,
    }));
    await section.settle();
    assert.equal(consumed, 0);
    assert.deepEqual(tabs.seen, ['assistant']);
    pending('/ping', mark).answer(pingAnswer());
    await section.settle();
    assert.equal(consumed, 1);
    assert.equal(activeTab(section.html), 'Помощник');
    assert.deepEqual(tabs.seen, ['assistant']);
});

test('вопрос из колокола без номера по-прежнему никуда не ведёт', async () => {
    const user = { id: 987 };
    memory.remember(pingKey(user), pingAnswer());
    const tabs = tabReports();
    const section = mount(WikiView, sectionProps(user, {
        bellFocus: { kind: 'questions', id: null, nonce: 1 },
        onTabShown: tabs.onTabShown,
    }));
    await section.settle();
    assert.equal(activeTab(section.html), 'Главная');
    assert.deepEqual(tabs.seen, ['library']);
});

test('уход из раздела: вкладки больше нет — шарик не остаётся погашенным', async () => {
    const user = { id: 988 };
    memory.remember(pingKey(user), pingAnswer());
    const tabs = tabReports();
    await withAddress('?view=wiki&tab=assistant', async () => {
        const section = mount(WikiView, sectionProps(user, { onTabShown: tabs.onTabShown }));
        await section.settle();
        assert.equal(activeTab(section.html), 'Помощник');
        section.unmount();
    });
    assert.deepEqual(tabs.seen, ['assistant', '']);
});

test('вкладка уходит порталу до отрисовки кадра, а не после', () => {
    /* Стенд выполняет useEffect и useLayoutEffect одинаково, поэтому вид хука
       держим текстом. Обычный эффект сработал бы ПОСЛЕ отрисовки: кадр со
       вставшим «Помощником» и ещё висящим шариком доходил бы до экрана. */
    const view = read('src/components/wiki/WikiView.jsx');
    assert.match(view, /useLayoutEffect\(\(\) => \{ reportTab\(shownTab\); \}, \[shownTab, reportTab\]\);/);
    assert.match(view, /useLayoutEffect\(\(\) => \(\) => reportTab\(''\), \[reportTab\]\);/);
});

test('раздел сообщает порталу открытую вику — по ней шарик и отвечает', async () => {
    const user = { id: 989 };
    memory.remember(pingKey(user), pingAnswer({ spaces: [pingAnswer().spaces[0], TEZ_SPACE] }));
    storage.set('wiki:space', '7');
    try {
        const spaces = [];
        const section = mount(WikiView, sectionProps(user, { onSpaceShown: (id) => spaces.push(id) }));
        await section.settle();
        assert.equal(spaces.at(-1), 7);
        // Запомненной вики больше нет (архив, закрыли от отдела) — первая доступная.
        storage.set('wiki:space', '99');
        const fallback = [];
        const other = mount(WikiView, sectionProps(user, { onSpaceShown: (id) => fallback.push(id) }));
        await other.settle();
        assert.deepEqual(fallback, [1]);
    } finally {
        storage.delete('wiki:space');
    }
});

/* ── Шарик: где он виден и по какой вике спрашивает ──────────────────────── */

const ORB = /aria-label="Открыть помощника"/;
const ORB_READY = { perimeter: { articles_for_ai: 12 }, index: { chunks: 340 }, space_id: 1 };

const orbProps = (over = {}) => ({
    user: { id: 991 }, view: 'wiki', wikiTab: 'library',
    apiBaseUrl: API, withAccessTokenHeader, showToast: () => {},
    ...over,
});

/* Шарик меряет окно и слушает его события. Своего window у стенда нет: адрес
   раздел читает внутри withAddress, а здесь окно — на время одной проверки. */
async function withBrowser(run) {
    const quiet = { addEventListener() {}, removeEventListener() {} };
    globalThis.window = {
        ...quiet, innerWidth: 1440, innerHeight: 900, localStorage: globalThis.localStorage,
    };
    globalThis.document = { ...quiet, hidden: false, visibilityState: 'visible' };
    try { return await run(); } finally {
        delete globalThis.window;
        delete globalThis.document;
    }
}

/** Шарик, которому сервер подтвердил, что отвечать есть на чём. */
async function readyOrb(over = {}) {
    const mark = calls.length;
    const orb = mount(AssistantOrb, orbProps(over));
    await orb.settle();
    pending('/ai/status', mark).answer(ORB_READY);
    await orb.settle();
    return { orb, mark };
}

test('шарик: в вике виден на всех вкладках, кроме «Помощника»', () => withBrowser(async () => {
    const { orb } = await readyOrb();
    assert.match(orb.html, ORB, 'над главной вики шарика нет — он снова гаснет во всём разделе');
    await orb.update({ wikiTab: 'assistant' });
    assert.equal(orb.html, '', 'шарик висит над «Помощником» — вторым входом в тот же чат');
    for (const tab of ['offices', 'cities', 'news', 'catalog', 'questions', '']) {
        await orb.update({ wikiTab: tab });
        assert.match(orb.html, ORB, `нет шарика на вкладке «${tab}»`);
    }
}));

test('шарик: вкладка «Помощник» решает только в вике, чужие экраны гасят по-прежнему', () => withBrowser(async () => {
    const { orb } = await readyOrb({ user: { id: 992 } });
    await orb.update({ view: 'tasks', wikiTab: 'assistant' });
    assert.match(orb.html, ORB, 'устаревшая вкладка вики погасила шарик в другом разделе');
    for (const view of ['lms', 'call_evaluation']) {
        await orb.update({ view, wikiTab: '' });
        assert.equal(orb.html, '', `шарик поверх чужого экрана «${view}»`);
    }
}));

test('шарик спрашивает открытую вику: другая — новая проба, та же — без второго запроса', () => withBrowser(async () => {
    storage.set('wiki:space', '1');
    try {
        const { orb, mark } = await readyOrb({ user: { id: 993 } });
        assert.deepEqual(sent(mark), ['/ai/status?space_id=1']);
        // Вошли в ту же вику, что была при загрузке портала.
        await orb.update({ wikiSpaceId: 1 });
        assert.deepEqual(sent(mark), ['/ai/status?space_id=1'], 'вход в ту же вику повторил пробу');
        // Переключили вику в шапке раздела.
        await orb.update({ wikiSpaceId: 7 });
        assert.deepEqual(sent(mark), ['/ai/status?space_id=1', '/ai/status?space_id=7']);
        pending('/ai/status', mark).answer({ ...ORB_READY, space_id: 7 });
        await orb.settle();
        assert.match(orb.html, ORB);
        // Ушли из раздела — вика остаётся последней выбранной, новой пробы нет.
        await orb.update({ view: 'tasks', wikiTab: '' });
        assert.equal(sent(mark).length, 2);
    } finally {
        storage.delete('wiki:space');
    }
}));

test('шарик: сбой повторной пробы его не гасит, отказ сервера — гасит', () => withBrowser(async () => {
    const { orb, mark } = await readyOrb({ user: { id: 994 } });
    await orb.update({ wikiSpaceId: 7 });
    pending('/ai/status', mark).fail();
    await orb.settle();
    assert.match(orb.html, ORB, 'сеть моргнула при смене вики — и шарик пропал до перезагрузки');
    await orb.update({ wikiSpaceId: 8 });
    pending('/ai/status', mark).refuse(503);
    await orb.settle();
    assert.match(orb.html, ORB, '5xx — сбой, а не ответ «помощника нет»');
    await orb.update({ wikiSpaceId: 9 });
    pending('/ai/status', mark).refuse(403);
    await orb.settle();
    assert.equal(orb.html, '', 'отказ — это ответ сервера, и шарик обязан ему поверить');
}));

test('шарик: первая проба, не дойдя, его не показывает', () => withBrowser(async () => {
    /* «Прежний ответ остаётся» — только если он был. Без ответа шарик не знает,
       есть ли помощнику на чём отвечать, и кнопка, которая по построению не
       работает, хуже отсутствующей. */
    const mark = calls.length;
    const orb = mount(AssistantOrb, orbProps({ user: { id: 995 } }));
    await orb.settle();
    pending('/ai/status', mark).fail();
    await orb.settle();
    assert.equal(orb.html, '');
}));
