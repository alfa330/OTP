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
    API, BASE, HEADERS, HR_SPACE, React, activeTab, calls, firstFrame, loadSection, mount, pending,
    pingAnswer, read, sectionProps, sent, storage, withAccessTokenHeader, withAddress,
} from './wiki_entry_lab.mjs';
import { answeringSpaceName } from '../src/components/assistant/answeringSpace.js';

const {
    WikiView, AssistantOrb, AssistantPanel, useAssistantChat, memory,
} = await loadSection({ lab: true });

const pingKey = (user) => memory.memoryKey(BASE, user.id, 'ping');

/* Лежит ли в состоянии корня, прожившего последний кадр, объект ровно такого
   вида — те же ключи и значения. Что именно раздел передал дальше (карточку
   вопроса или разговор, пространство пробы), разметка не показывает: вкладки
   ленивые, а панель шарика в его кадрах не рисуется. */
const holdsLike = (shape) => globalThis.__wikiEntryLab.slots
    .filter((slot) => slot.kind === 'state').map((slot) => slot.value)
    .some((value) => value && typeof value === 'object'
        && Object.keys(value).length === Object.keys(shape).length
        && Object.entries(shape).every(([key, want]) => Object.is(value[key], want)));

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

test('отказ или сбой /ping при просьбе «Помощник»: над карточкой ошибки шарик виден', async () => {
    /* Просьба ждёт «Попробовать снова», но на экране карточка «Раздел
       недоступен», а не чат: гасить шарик здесь — гасить его не на «Помощнике». */
    const doors = [
        ['адрес', '?view=wiki&tab=assistant', {}],
        ['мини-чат', '', { bellFocus: { kind: 'assistant', id: 41, nonce: 1 } }],
    ];
    const answers = [['сбой', (call) => call.fail()], ['отказ', (call) => call.refuse(403)]];
    let id = 2110;
    for (const [door, address, over] of doors) {
        for (const [what, answer] of answers) {
            const tabs = tabReports();
            const mark = calls.length;
            id += 1;
            await withAddress(address, async () => {
                const section = mount(WikiView, sectionProps({ id }, { ...over, onTabShown: tabs.onTabShown }));
                await section.settle();
                answer(pending('/ping', mark));
                await section.settle();
                assert.match(section.html, /Раздел недоступен/);
            });
            assert.deepEqual(tabs.seen, ['assistant', ''], `${door}, ${what}: шарик погашен над карточкой ошибки`);
        }
    }
});

test('мини-чат в вике без «Помощника»: дверь ведёт в вику, по которой он отвечал', async () => {
    /* В «Кадрах» помощник выключен, и сервер отвечал шарику из «Таксопарков».
       Без переключения просьба погасла бы по набору вкладок «Кадров», и кнопка
       просто закрыла бы панель. */
    const user = { id: 2120 };
    const TAXI = pingAnswer().spaces[0];
    memory.remember(pingKey(user), pingAnswer({ spaces: [TAXI, HR_SPACE] }));
    storage.set('wiki:space', String(HR_SPACE.id));
    try {
        let consumed = 0;
        const tabs = tabReports();
        const spaces = [];
        const section = mount(WikiView, sectionProps(user, {
            bellFocus: { kind: 'assistant', id: 41, space_id: TAXI.id, nonce: 1 },
            onBellFocusConsumed: () => { consumed += 1; },
            onTabShown: tabs.onTabShown,
            onSpaceShown: (space) => spaces.push(space),
        }));
        await section.settle();
        assert.equal(consumed, 1);
        assert.equal(activeTab(section.html), 'Помощник');
        assert.equal(spaces.at(-1), TAXI.id, 'вика не переключилась');
        assert.deepEqual(tabs.seen, ['assistant'], 'шарик на кадр вставал над главной «Кадров»');
        assert.ok(holdsLike({ chatId: 41, nonce: 1 }), 'разговор из мини-чата не открылся');
    } finally {
        storage.delete('wiki:space');
    }
});

test('мини-чат: вика, которой у человека нет, никуда не уводит', async () => {
    const user = { id: 2121 };
    memory.remember(pingKey(user), pingAnswer());
    const spaces = [];
    const section = mount(WikiView, sectionProps(user, {
        bellFocus: { kind: 'assistant', id: 41, space_id: 99, nonce: 1 },
        onSpaceShown: (space) => spaces.push(space),
    }));
    await section.settle();
    assert.equal(activeTab(section.html), 'Помощник');
    assert.deepEqual(spaces, [1]);
});

test('мини-чат в вике без «Помощника», пока /ping обновляется: шарик над главной не гаснет', async () => {
    /* Запомненный набор вкладок «Помощника» не знает, свежий ответ ещё в пути.
       Просьба ждёт его, а на экране главная — шарик на ней обязан остаться. */
    const user = { id: 2122 };
    memory.remember(pingKey(user), pingAnswer({ spaces: [HR_SPACE] }));
    let consumed = 0;
    const tabs = tabReports();
    const mark = calls.length;
    const section = mount(WikiView, sectionProps(user, {
        bellFocus: { kind: 'assistant', id: 41, nonce: 1 },
        onBellFocusConsumed: () => { consumed += 1; },
        onTabShown: tabs.onTabShown,
    }));
    await section.settle();
    assert.equal(activeTab(section.html), 'Главная');
    assert.deepEqual(tabs.seen, ['library'], 'шарик погас над главной ради вкладки, которой нет');
    pending('/ping', mark).answer(pingAnswer({ spaces: [HR_SPACE] }));
    await section.settle();
    assert.equal(consumed, 1);
    assert.deepEqual(tabs.seen, ['library']);
});

test('колокол: вопрос открывается карточкой, разговор — разговором', async () => {
    /* Ветки перестроены под дверь шарика без номера: перепутай их — и
       супервайзер получил бы чат вместо карточки, оператор — карточку вместо
       ответа. Вкладка при этом встаёт верная, поэтому смотрим, что передано. */
    const user = { id: 2123 };
    memory.remember(pingKey(user), pingAnswer());
    const question = mount(WikiView, sectionProps(user, { bellFocus: { kind: 'questions', id: 5, nonce: 3 } }));
    await question.settle();
    assert.equal(activeTab(question.html), 'Вопросы');
    assert.ok(holdsLike({ id: 5, nonce: 3 }), 'вопрос из колокола открылся без карточки');
    assert.ok(!holdsLike({ chatId: 5, nonce: 3 }), 'вопрос ушёл разговором помощника');
    const answer = mount(WikiView, sectionProps(user, { bellFocus: { kind: 'assistant', id: 7, nonce: 4 } }));
    await answer.settle();
    assert.equal(activeTab(answer.html), 'Помощник');
    assert.ok(holdsLike({ chatId: 7, nonce: 4 }), 'разговор из колокола не открылся');
    assert.ok(!holdsLike({ id: 7, nonce: 4 }), 'разговор ушёл карточкой вопроса');
});

test('исполненная просьба колокола и шарика не повторяется', () => {
    /* Стенду не сменить вику в шапке (нажатия ему недоступны), поэтому
       сторож — текстом: без него смена вики возвращала бы человека в ту,
       куда его однажды позвал шарик. */
    const view = read('src/components/wiki/WikiView.jsx');
    assert.match(view, /const target = bellTabOf\(bellFocus\);\s*if \(!target \|\| bellDone === bellFocus\) return;\s*if \(bellSpace\) \{\s*setSpaceId\(bellSpace\);\s*return;\s*\}/);
});

test('до ответа /ping раздел не сообщает «вики нет»', async () => {
    /* Иначе на каждом первом входе шарик откатывался бы к вике из памяти
       браузера и спрашивал сервер лишний раз. */
    const spaces = [];
    const mark = calls.length;
    const section = mount(WikiView, sectionProps({ id: 2124 }, { onSpaceShown: (space) => spaces.push(space) }));
    await section.settle();
    assert.deepEqual(spaces, []);
    pending('/ping', mark).answer(pingAnswer());
    await section.settle();
    assert.deepEqual(spaces, [1]);
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
    pending('/ai/status', mark).refuse(500);
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

test('шарик: проба не дошла — он остаётся, но спрашивает уже открытую вику', () => withBrowser(async () => {
    /* С прежней шарик отвечал бы над статьями новой до перезагрузки: пробу
       повторяет только следующая смена вики. Пространство сервер проверит и
       так — каждый запрос чата идёт через effective_space. */
    const { orb, mark } = await readyOrb({ user: { id: 2201 } });
    await orb.update({ wikiSpaceId: 7 });
    pending('/ai/status', mark).fail();
    await orb.settle();
    assert.match(orb.html, ORB);
    assert.ok(holdsLike({ ok: true, spaceId: 7 }), 'мини-чат остался на прежней вике');
}));

const ORB_EMPTY = { perimeter: { articles_for_ai: 0 }, index: { chunks: 0 }, space_id: 7 };

test('шарик: поздний ответ по вике, из которой ушли, не перетирает ответ по открытой', () => withBrowser(async () => {
    /* Проба теперь уходит на каждую смену вики, и при быстром переключении
       ответы приходят не по порядку. */
    for (const [what, late] of [['пустой периметр', (call) => call.answer(ORB_EMPTY)],
                                ['отказ', (call) => call.refuse(403)]]) {
        const { orb, mark } = await readyOrb({ user: { id: what === 'отказ' ? 2203 : 2202 } });
        await orb.update({ wikiSpaceId: 7 });
        const stale = pending('/ai/status', mark);
        await orb.update({ wikiSpaceId: 8 });
        const fresh = pending('/ai/status', mark);
        assert.notEqual(stale, fresh);
        fresh.answer({ ...ORB_READY, space_id: 8 });
        await orb.settle();
        late(stale);
        await orb.settle();
        assert.match(orb.html, ORB, `${what} по прежней вике погасил шарик над открытой`);
        assert.ok(holdsLike({ ok: true, spaceId: 8 }), `${what} по прежней вике подменил пространство`);
    }
}));

test('шарик: без сообщённой вкладки он в вике виден', () => withBrowser(async () => {
    /* Вторая точка монтирования без пропса не должна возвращать «шарика нет
       во всей вике». */
    const props = orbProps({ user: { id: 2204 } });
    delete props.wikiTab;
    const mark = calls.length;
    const orb = mount(AssistantOrb, props);
    await orb.settle();
    pending('/ai/status', mark).answer(ORB_READY);
    await orb.settle();
    assert.match(orb.html, ORB);
}));

/* ── Мини-чат: подпись и дверь в раздел ──────────────────────────────────── */

test('подпись мини-чата: имя вики — когда вик несколько или сервер подставил другую', () => {
    const TAXI = { id: 1, name: 'Таксопарки' };
    const TEZ = { id: 9, name: 'Тез КЦ' };
    // Одна вика с помощником, спрашивали её же — имя было бы лишним словом.
    assert.equal(answeringSpaceName({ space_id: 1, spaces: [TAXI] }, 1), null);
    assert.equal(answeringSpaceName({ space_id: 1, spaces: [TAXI] }, undefined), null);
    // Открыта вика без помощника, сервер ответил из другой — без имени никак.
    assert.equal(answeringSpaceName({ space_id: 1, spaces: [TAXI] }, 12), 'Таксопарки');
    // Вик с помощником несколько — имя всегда.
    assert.equal(answeringSpaceName({ space_id: 9, spaces: [TAXI, TEZ] }, 9), 'Тез КЦ');
    // Статуса ещё нет, помощника нет нигде.
    assert.equal(answeringSpaceName(null, 1), null);
    assert.equal(answeringSpaceName({ space_id: null, spaces: [] }, 1), null);
});

const panelChat = (over = {}) => ({
    status: { space_id: 1 }, statusError: null, perimeter: { articles_for_ai: 49 },
    indexReady: true, noAccess: false, canAsk: true, suggestions: [],
    chats: [], chatsLoading: false, activeId: null, messages: [], draft: '', busy: false, error: '',
    escalating: null, canEscalate: false,
    setDraft() {}, ask() {}, startNewChat() {}, openChat() {}, sendFeedback() {},
    removeChat() {}, escalate() {}, loadStatus() {}, loadChats() {},
    ...over,
});
const panelHtml = (over = {}) => firstFrame(AssistantPanel, {
    appearance: 'aurora', onAppearanceChange() {}, onOpenArticle() {}, onOpenFullAssistant() {},
    onClose() {}, showToast() {}, ...over,
});
const doorButton = (html) => html.match(/<button[^>]*aria-label="Открыть помощника в разделе «Вики»"[^>]*>/)?.[0] || '';

test('мини-чат: пока ответ пишется, дверь в раздел закрыта и объясняет почему', () => {
    /* Вопрос с ответом сервер сохраняет одним махом в конце. Открой вкладку
       раньше — она покажет разговор без них, а ответ придёт в погашенный шарик. */
    const waiting = doorButton(panelHtml({ chat: panelChat({ busy: true, activeId: 41 }) }));
    assert.match(waiting, /\sdisabled=""/);
    assert.match(waiting, /title="Дождитесь ответа — разговор откроется вместе с ним"/);
    const idle = doorButton(panelHtml({ chat: panelChat({ activeId: 41 }) }));
    assert.ok(idle, 'двери в раздел нет');
    assert.doesNotMatch(idle, /\sdisabled=""/);
});

test('мини-чат: подпись называет вику, из которой ответ', () => {
    assert.match(panelHtml({ chat: panelChat(), spaceName: 'Таксопарки' }), /Таксопарки · доступно статей: 49/);
    assert.match(panelHtml({ chat: panelChat() }), />доступно статей: 49</);
});

test('мини-чат: статус прежней вики, опоздав, не ложится поверх нынешнего', async () => {
    /* Шарик идёт за викой из шапки раздела, и при быстрой смене ответы
       приходят не по порядку: подпись назвала бы не ту вику, подсказки и
       периметр были бы от неё же, а вопросы уходили бы уже в нынешнюю. */
    const ChatStatus = (props) => {
        const chat = useAssistantChat(props);
        return React.createElement('i', null, String(chat.status?.space_id ?? ''));
    };
    const mark = calls.length;
    const chat = mount(ChatStatus, { base: BASE, headers: HEADERS, spaceId: 7, enabled: true, withSuggestions: true });
    await chat.settle();
    await chat.update({ spaceId: 9 });
    const statuses = calls.slice(mark).filter((call) => call.url === `${BASE}/ai/status`);
    assert.deepEqual(statuses.map((call) => call.params.space_id), [7, 9]);
    statuses[1].answer({ space_id: 9, spaces: [], perimeter: { articles_for_ai: 3 } });
    await chat.settle();
    statuses[0].answer({ space_id: 7, spaces: [], perimeter: { articles_for_ai: 5 } });
    await chat.settle();
    assert.equal(chat.html, '<i>9</i>', 'статус вики, из которой ушли, перетёр нынешний');
    // И отказ по прежней вике нынешний статус не стирает.
    await chat.update({ spaceId: 11 });
    await chat.update({ spaceId: 12 });
    const more = calls.slice(mark).filter((call) => call.url === `${BASE}/ai/status`).slice(2);
    more[1].answer({ space_id: 12, spaces: [], perimeter: { articles_for_ai: 1 } });
    await chat.settle();
    more[0].refuse(403);
    await chat.settle();
    assert.equal(chat.html, '<i>12</i>');
});
