/*
 * Вход в «Вики» — что происходит ПОСЛЕ первого кадра: запросы, ответы, отказы.
 *
 * tests/wiki_entry_first_frame.test.mjs проверяет кадр до ответа сервера:
 * серверный рендер эффектов не запускает. Здесь КОРНЕВОЙ компонент (WikiView
 * либо WikiLibrary) проживает несколько кадров без браузера: его состояние
 * хранится между рендерами, эффекты выполняются, а на запросы отвечает сам
 * тест — ответом, отказом (сервер назвал код) или сбоем (ответа нет). Как это
 * устроено и где граница приёма — в tests/wiki_entry_lab.mjs.
 *
 * Запуск: node --test tests/wiki_entry_after_answer.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
    BASE, HEADER_HIDDEN, HOME_SHIMMER, HR_SPACE, PARK, READER, SKELETON, TABS_SKELETON,
    activeTab, calls, libraryProps, loadSection, mount, parksAnswer, pending, pingAnswer,
    sectionProps, sent, tabLabels, withAddress, withTimers,
} from './wiki_entry_lab.mjs';

/* Новая функция заголовков — новый загрузчик /ping: так тест повторяет запрос,
   не нажимая «Обновить» (обработчики нажатий стенду недоступны). */
const repeatPing = (section) => section.update({ withAccessTokenHeader: () => ({}) });

const { WikiView, WikiLibrary, memory } = await loadSection({ lab: true });

const mountSection = (user, over) => mount(WikiView, sectionProps(user, over));
const mountLibrary = (over) => mount(WikiLibrary, libraryProps(over));

const pingKey = (user) => memory.memoryKey(BASE, user.id, 'ping');
const parksKey = (userId, space) => memory.memoryKey(BASE, userId, 'parks', space);
const lists = (from) => sent(from).filter((url) => /^\/(articles|home|parks)(\?|$)/.test(url));

/* ── Раздел: первый вход ─────────────────────────────────────────────────── */

test('первый вход: до ответа уходят только /ping и /structure; ответ запомнен и ставит вкладки', async () => {
    const user = { id: 951 };
    const mark = calls.length;
    const section = mountSection(user);
    await section.settle();

    assert.deepEqual(sent(mark).sort(), ['/ping', '/structure'],
        'до ответа /ping ушло что-то ещё — запрос без прав и пространства');
    assert.match(section.html, SKELETON);
    assert.match(section.html, TABS_SKELETON);
    assert.match(section.html, HEADER_HIDDEN);

    const answer = pingAnswer();
    pending('/ping', mark).answer(answer);
    await section.settle();

    assert.equal(memory.recall(pingKey(user)), answer,
        'ответ /ping не запомнен — второй вход снова начнётся с заготовки');
    assert.equal(tabLabels(section.html).length, 11);
    assert.doesNotMatch(section.html, TABS_SKELETON, 'пустая подложка осталась рядом с настоящими вкладками');
    assert.doesNotMatch(section.html, HEADER_HIDDEN);
    // Каталог редактора — один раз и сразу с пространством.
    assert.deepEqual(sent(mark).filter((url) => url.startsWith('/catalog')), ['/catalog?space_id=1']);
});

test('первый вход: и отказ, и сбой /ping — карточка «Раздел недоступен» без заготовок', async () => {
    /* Ответа ещё не было, показывать нечего: что сервер отказал, что не
       ответил вовсе — человек видит одно и то же и может повторить. */
    const outcomes = [(call) => call.refuse(403), (call) => call.refuse(500), (call) => call.fail()];
    for (const [index, outcome] of outcomes.entries()) {
        const mark = calls.length;
        const section = mountSection({ id: 9520 + index });
        await section.settle();
        outcome(pending('/ping', mark));
        await section.settle();

        assert.match(section.html, /Раздел недоступен/);
        assert.match(section.html, /Попробовать снова/);
        assert.doesNotMatch(section.html, TABS_SKELETON, 'пустая подложка вкладок осталась под карточкой отказа');
        assert.doesNotMatch(section.html, SKELETON, 'заготовка «Загружаем вики» под карточкой отказа');
        assert.doesNotMatch(section.html, HEADER_HIDDEN,
            '«Обновить» в шапке невидима после отказа — выбраться из раздела нечем');
        assert.deepEqual(tabLabels(section.html), []);
    }
});

/* ── Раздел: второй вход ─────────────────────────────────────────────────── */

test('второй вход: равный ответ не меняет состояние, изменившийся — сужает вкладки', async () => {
    const user = { id: 953 };
    const remembered = pingAnswer();
    memory.remember(pingKey(user), remembered);

    let mark = calls.length;
    const same = mountSection(user);
    await same.settle();
    assert.equal(tabLabels(same.html).length, 11);
    assert.ok(same.holds(remembered));
    pending('/ping', mark).answer(JSON.parse(JSON.stringify(remembered)));
    await same.settle();
    assert.ok(same.holds(remembered),
        'равный ответ подменил объект состояния — вкладки и каталог пересчитаются и перезапросятся');
    assert.equal(sent(mark).filter((url) => url.startsWith('/catalog')).length, 1,
        'каталог запрошен повторно на равный ответ /ping');

    // Права сузили между входами: экран обязан поправиться свежим ответом.
    mark = calls.length;
    const narrowed = mountSection(user);
    await narrowed.settle();
    const fresh = pingAnswer(READER);
    pending('/ping', mark).answer(fresh);
    await narrowed.settle();
    assert.deepEqual(tabLabels(narrowed.html),
        ['Главная', 'Помощник', 'Новости', 'Обзор', 'Парки', 'Офисы', 'Города']);
    assert.ok(!narrowed.html.includes('Новая статья'));
    assert.deepEqual(memory.recall(pingKey(user)), fresh);
});

test('второй вход: ОТКАЗ сверки стирает память и убирает вкладки', async () => {
    const user = { id: 954 };
    memory.remember(pingKey(user), pingAnswer());
    const mark = calls.length;
    const section = mountSection(user);
    await section.settle();
    assert.equal(tabLabels(section.html).length, 11);

    pending('/ping', mark).refuse(403);
    await section.settle();
    assert.equal(memory.recall(pingKey(user)), null, 'прошлому ответу верят после отказа — доступ могли снять');
    assert.deepEqual(tabLabels(section.html), []);
    assert.match(section.html, /Раздел недоступен/);
    assert.doesNotMatch(section.html, TABS_SKELETON);
    assert.doesNotMatch(section.html, HEADER_HIDDEN);
});

test('второй вход: СБОЙ сверки раздел не трогает — права от него не изменились', async () => {
    /* Сеть моргнула, сервер перезапускается после выкладки. Раньше любой
       неудачный /ping стирал ответ: вкладки пропадали, а человека, стоявшего
       на «Журнале» или правившего «Структуру», выбрасывало с его вкладки. */
    for (const [index, outcome] of [(call) => call.refuse(502), (call) => call.fail()].entries()) {
        const user = { id: 9550 + index };
        const remembered = pingAnswer();
        memory.remember(pingKey(user), remembered);
        const toasts = [];
        const mark = calls.length;
        const section = mountSection(user, { showToast: (text, kind) => toasts.push([text, kind]) });
        await section.settle();

        outcome(pending('/ping', mark));
        await section.settle();
        assert.equal(tabLabels(section.html).length, 11, 'сбой сверки убрал вкладки');
        assert.ok(section.holds(remembered));
        assert.equal(memory.recall(pingKey(user)), remembered, 'сбой стёр память — следующий вход начнётся с заготовки');
        assert.doesNotMatch(section.html, /Раздел недоступен/);
        // Об ошибке сказано один раз и по-человечески — не кодом и не «Network Error».
        assert.equal(toasts.length, 1);
        assert.equal(toasts[0][1], 'error');
        assert.match(toasts[0][0], /Не удалось обновить вики/);
    }
});

test('содержимое вкладки при отказе уходит с экрана, при сбое — остаётся', async () => {
    /* Вкладке нужны пространство и права. После ОТКАЗА их нет: раньше её
       содержимое оставалось под карточкой «Раздел недоступен», перезапрашивалось
       без пространства и показывало вторую ошибку. При СБОЕ ответ на месте — и
       вкладка тоже. */
    await withAddress('?view=wiki&tab=overview', async () => {
        const refused = { id: 9561 };
        memory.remember(pingKey(refused), pingAnswer());
        let mark = calls.length;
        const gone = mountSection(refused);
        await gone.settle();
        assert.equal(activeTab(gone.html), 'Обзор');
        assert.ok(gone.html.includes('Ваш доступ'));
        pending('/ping', mark).refuse(403);
        await gone.settle();
        assert.match(gone.html, /Раздел недоступен/);
        assert.ok(!gone.html.includes('Ваш доступ'), 'содержимое вкладки осталось под карточкой отказа');

        const failed = { id: 9562 };
        memory.remember(pingKey(failed), pingAnswer());
        mark = calls.length;
        const kept = mountSection(failed);
        await kept.settle();
        pending('/ping', mark).fail();
        await kept.settle();
        assert.equal(activeTab(kept.html), 'Обзор', 'сбой сверки выбросил человека с вкладки');
        assert.ok(kept.html.includes('Ваш доступ'));
    });
});

test('вкладка, которой нет в наборе «по умолчанию», при отказе не рисуется без пространства', async () => {
    /* «Журнал», «Статьи», «Вопросы», «Аналитика» — у них нет ничего, кроме
       прав. Содержимое убрано, а сама вкладка помнится: после «Попробовать
       снова» человек возвращается туда, где был. */
    await withAddress('?view=wiki&tab=audit', async () => {
        const user = { id: 9571 };
        memory.remember(pingKey(user), pingAnswer());
        const mark = calls.length;
        const section = mountSection(user);
        await section.settle();
        assert.equal(activeTab(section.html), 'Журнал');
        assert.ok(section.html.includes('Журнал изменений'));

        pending('/ping', mark).refuse(403);
        await section.settle();
        assert.match(section.html, /Раздел недоступен/);
        assert.ok(!section.html.includes('Журнал изменений'), 'журнал остался под карточкой отказа — без пространства и прав');
    });
});

test('«Обзор» из адреса на втором входе стоит, пока идёт сверка /ping', async () => {
    /* Заглушки на время запроса больше нет — вкладка, убранная на loading,
       мигнула бы пустым местом на каждый вход и каждое «Обновить». */
    const user = { id: 958 };
    memory.remember(pingKey(user), pingAnswer());
    await withAddress('?view=wiki&tab=overview', async () => {
        const section = mountSection(user);
        await section.settle();
        assert.ok(section.html.includes('Ваш доступ'), '«Обзор» пуст, пока /ping не ответил повторно');
        assert.doesNotMatch(section.html, /h-\[120px\]/, 'заглушка 120 px встала на сверку /ping');
    });
});

test('ответ, пришедший уже в этом входе, сбой сверки тоже не стирает', async () => {
    /* Первый вход (памяти нет) → ответ → человек жмёт «Обновить», и сеть
       моргает. «Ответ есть» обязано быть правдой и без запомненного с прошлого
       раза. */
    const user = { id: 9591 };
    let mark = calls.length;
    const section = mountSection(user);
    await section.settle();
    pending('/ping', mark).answer(pingAnswer());
    await section.settle();
    assert.equal(tabLabels(section.html).length, 11);

    mark = calls.length;
    await repeatPing(section);
    pending('/ping', mark).fail();
    await section.settle();
    assert.equal(tabLabels(section.html).length, 11, 'сбой повторного запроса убрал вкладки');
    assert.doesNotMatch(section.html, /Раздел недоступен/);
});

test('повтор запроса после отказа не прячет кнопки шапки, которые уже были видны', async () => {
    const mark = calls.length;
    const section = mountSection({ id: 9592 });
    await section.settle();
    assert.match(section.html, HEADER_HIDDEN);
    pending('/ping', mark).refuse(403);
    await section.settle();
    assert.doesNotMatch(section.html, HEADER_HIDDEN);

    await repeatPing(section);   // «Попробовать снова»: запрос снова в пути
    assert.match(section.html, TABS_SKELETON);
    assert.doesNotMatch(section.html, HEADER_HIDDEN, 'поиск и «Обновить» исчезли на время повторного запроса');
});

/* ── Переходы в раздел извне ─────────────────────────────────────────────── */

test('колокол: переход к помощнику ждёт ответа /ping и срабатывает один раз', async () => {
    let consumed = 0;
    const mark = calls.length;
    const section = mountSection({ id: 971 }, {
        bellFocus: { id: 77, kind: 'assistant', nonce: 1 },
        onBellFocusConsumed: () => { consumed += 1; },
    });
    await section.settle();
    assert.equal(consumed, 0, 'переход сгорел до ответа — по набору вкладок «по умолчанию»');
    assert.doesNotMatch(section.html, SKELETON, 'до ответа стоит заготовка главной, а откроется помощник');
    assert.match(section.html, /h-\[120px\]/);

    pending('/ping', mark).answer(pingAnswer());
    await section.settle();
    assert.equal(consumed, 1);
    assert.equal(activeTab(section.html), 'Помощник');
});

test('колокол: вкладки в окончательном наборе нет — просьба гаснет и не выстрелит позже', async () => {
    /* Непогашенная, она дождалась бы своего набора — и перебросила бы человека
       в чат помощника в тот момент, когда он просто сменил пространство в шапке. Гасим
       по СВЕЖЕМУ ответу, а не по запомненному: в нём вкладка могла появиться. */
    const user = { id: 972 };
    memory.remember(pingKey(user), pingAnswer({ spaces: [HR_SPACE] }));
    let consumed = 0;
    const mark = calls.length;
    const section = mountSection(user, {
        bellFocus: { id: 77, kind: 'assistant', nonce: 1 },
        onBellFocusConsumed: () => { consumed += 1; },
    });
    await section.settle();
    assert.equal(consumed, 0, 'просьба погашена по запомненному ответу, не дождавшись свежего');

    pending('/ping', mark).answer(pingAnswer({ spaces: [HR_SPACE] }));
    await section.settle();
    assert.equal(consumed, 1, 'неисполнимая просьба осталась висеть');
    assert.equal(activeTab(section.html), 'Главная');
    assert.ok(!tabLabels(section.html).includes('Помощник'));
});

test('шарик помощника: с запомненным ответом «Офисы» открываются, не дожидаясь свежего /ping', async () => {
    /* Раньше переход ждал конца запроса — и полсекунды человеку показывали
       главную со всеми её запросами, прежде чем открыть то, за чем он шёл. */
    const user = { id: 973 };
    memory.remember(pingKey(user), pingAnswer());
    let consumed = 0;
    const section = mountSection(user, {
        directoryFocus: { tab: 'offices', kind: 'office', id: 3, space_id: 1 },
        onDirectoryFocusConsumed: () => { consumed += 1; },
    });
    await section.settle();   // /ping так и не ответил
    assert.equal(consumed, 1);
    assert.equal(activeTab(section.html), 'Офисы');
});

test('шарик помощника: без ответа ждёт /ping; отказ просьбу гасит, а не откладывает', async () => {
    const focus = { tab: 'offices', kind: 'office', id: 3, space_id: 1 };
    let consumed = 0;
    let mark = calls.length;
    const opened = mountSection({ id: 974 }, {
        directoryFocus: focus, onDirectoryFocusConsumed: () => { consumed += 1; },
    });
    await opened.settle();
    assert.equal(consumed, 0, 'просьба сгорела до ответа /ping');
    pending('/ping', mark).answer(pingAnswer());
    await opened.settle();
    assert.equal(consumed, 1);
    assert.equal(activeTab(opened.html), 'Офисы');

    consumed = 0;
    mark = calls.length;
    const refused = mountSection({ id: 975 }, {
        directoryFocus: focus, onDirectoryFocusConsumed: () => { consumed += 1; },
    });
    await refused.settle();
    pending('/ping', mark).refuse(403);
    await refused.settle();
    assert.equal(consumed, 1, 'просьба пережила отказ и выстрелит при следующем входе');
    assert.match(refused.html, /Раздел недоступен/);
});

test('шарик помощника: просьба, которую не сняли снаружи, исполняется один раз', async () => {
    /* Исполненное помнит сам раздел. Иначе неснятая просьба открывала бы
       «Офисы» заново на каждое изменение набора вкладок — и навсегда оставила
       бы витрину «с которой уходят», то есть пустой. */
    const user = { id: 976 };
    memory.remember(pingKey(user), pingAnswer());
    memory.remember(parksKey(user.id, 1), parksAnswer(1, []));
    const focus = { tab: 'offices', kind: 'office', id: 3, space_id: 12 };   // вики 12 у человека нет
    const mark = calls.length;
    const section = mountSection(user, { directoryFocus: focus });           // onDirectoryFocusConsumed не передан
    await section.settle();
    assert.equal(activeTab(section.html), 'Главная');
    assert.ok(section.html.includes('Все статьи вики'), 'витрина осталась пустой из-за неснятой просьбы');

    pending('/ping', mark).answer(pingAnswer({ counters: { spaces: 1, sections: 5 } }));
    await section.settle();
    assert.equal(activeTab(section.html), 'Главная');
});

test('статья по ссылке открывается, не дожидаясь /ping, и списков не запрашивает', async () => {
    let consumed = 0;
    const mark = calls.length;
    const library = mountLibrary({
        ready: false, spaceId: null, userId: 977,
        initialSlug: 'pravila', onInitialSlugConsumed: () => { consumed += 1; },
    });
    await library.settle();
    assert.equal(consumed, 1);
    assert.doesNotMatch(library.html, SKELETON, 'вместо статьи стоит заготовка витрины');
    assert.deepEqual(lists(mark), []);
});

/* ── Витрина: запросы ────────────────────────────────────────────────────── */

const HOME_LISTS = ['/articles?space_id=1', '/home?space_id=1', '/parks?space_id=1'];

test('витрина без пространства не запрашивает ничего, с ответом — всё по разу и с пространством', async () => {
    /* Запрос без пространства возвращает статьи ВСЕХ вики человека, а с ответом
       /ping тот же список запрашивался заново — это и были лишние запросы
       каждого входа. */
    const mark = calls.length;
    const library = mountLibrary({ ready: false, spaceId: null, userId: 957 });
    await library.settle();
    assert.deepEqual(sent(mark), [], 'витрина ушла за данными, не зная пространства');

    await library.update({ ready: true, spaceId: 1 });
    assert.deepEqual(sent(mark).sort(), HOME_LISTS);
});

test('пространство известно заранее — витрина уходит за данными одновременно с /ping', async () => {
    /* Пространство, выбранное в прошлый раз, лежит в localStorage: ждать ответа
       /ping, чтобы спросить его статьи, незачем — раньше первый вход шёл в два круга
       подряд (права, затем данные), и витрина вставала на треть секунды позже
       вкладок. Рисовать при этом нечего, пока нет прав. */
    const mark = calls.length;
    const library = mountLibrary({ ready: false, userId: 9572 });
    await library.settle();
    assert.deepEqual(sent(mark).sort(), HOME_LISTS, 'запросы витрины ждут ответа /ping');
    assert.match(library.html, SKELETON, 'витрина нарисована до ответа о правах');

    // Ответы пришли раньше прав — лежат и ждут.
    pending('/parks', mark).answer({ items: [PARK], can_manage: false });
    pending('/home', mark).answer({ favorites: [], recent: [] });
    await library.settle();
    assert.match(library.html, SKELETON);

    // Права пришли, пространство подтвердилось: витрина встаёт сразу, целиком и без новых запросов.
    await library.update({ ready: true });
    assert.deepEqual(sent(mark).sort(), HOME_LISTS, 'подтверждённое пространство запрошено повторно');
    assert.doesNotMatch(library.html, SKELETON);
    assert.ok(library.html.includes('aria-label="Тест Такси"'));
    assert.ok(library.html.includes('Все статьи вики'));
    assert.doesNotMatch(library.html, HOME_SHIMMER, 'подборки пришли до прав, но не показаны');
});

test('догадка о пространстве не подтвердилась — данные запрошены заново, прежние ответы отброшены', async () => {
    const mark = calls.length;
    const library = mountLibrary({ ready: false, spaceId: 5, userId: 9573 });
    await library.settle();
    const early = { parks: pending('/parks', mark), home: pending('/home', mark) };

    await library.update({ ready: true, spaceId: 1 });   // пространство 5 убрали в архив — открылось первое
    assert.deepEqual(sent(mark).filter((url) => url.includes('space_id=1')).sort(), HOME_LISTS);

    const staleHome = { favorites: [], recent: [], marker: 'пространство 5' };
    early.parks.answer({ items: [{ ...PARK, name: 'Парк чужой вики' }], can_manage: false });
    early.home.answer(staleHome);
    await library.settle();
    assert.ok(!library.html.includes('Парк чужой вики'), 'рельс пространства из догадки встал в открытом');
    assert.ok(!library.holds(staleHome), 'подборки пространства из догадки легли в открытое');
});

test('ответы за пространство из догадки, пришедшие ДО ответа /ping, в другом не показываются ни на кадр', async () => {
    /* Самый неудобный порядок: догадка не подтвердилась, а её данные уже
       лежат в состоянии. Загрузчики за настоящее пространство — эффекты, они отработают
       после рендера; на этот рендер чужие полки, оглавление и рельс обязаны
       считаться недогруженными. */
    const guessed = [{ id: 1, slug: 'a', title: 'Статья вики из догадки', section_ids: [], status: 'published' }];
    const answerGuess = (from) => {
        pending('/parks', from).answer({ items: [{ ...PARK, name: 'Парк чужой вики' }], can_manage: false });
        pending('/home', from).answer({ favorites: guessed, recent: [], popular: [] });
        pending('/articles', from).answer({ items: guessed, total_visible: 1 });
    };

    // В настоящем пространстве рельс есть: первый кадр ждёт ЕГО парки, а не ставит чужие.
    let mark = calls.length;
    const railed = mountLibrary({ ready: false, spaceId: 5, userId: 9575 });
    await railed.settle();
    answerGuess(mark);
    await railed.settle();
    await railed.update({ ready: true, spaceId: 1 });
    assert.ok(!railed.html.includes('Парк чужой вики'), 'первый кадр встал с рельсом пространства из догадки');
    assert.match(railed.html, SKELETON);

    // В настоящем пространстве рельса нет: витрина встаёт сразу — с заглушками, а не с чужими данными.
    mark = calls.length;
    const plain = mount(WikiLibrary, libraryProps({
        ready: false, spaceId: 5, userId: 9576, structure: { spaces: [], sections: [] },
    }));
    await plain.settle();
    answerGuess(mark);
    await plain.settle();
    const settling = plain.update({ ready: true, spaceId: 12, features: { parks: false, library_park_rail: false } });
    // Кадр сразу после ответа /ping, до того как отработали новые загрузчики.
    assert.ok(plain.html.includes('Все статьи вики'));
    assert.ok(!plain.html.includes('Статья вики из догадки'), 'полки или оглавление пространства из догадки встали на кадр');
    assert.match(plain.html, HOME_SHIMMER);
    await settling;
    assert.ok(!plain.html.includes('Статья вики из догадки'));
});

test('витрина, с которой сейчас уйдут, за данными не ходит', async () => {
    /* Переход из колокола или шарика: права и пространство известны, но экран
       через кадр сменится — оглавление, подборки и парки ради него не нужны. */
    const mark = calls.length;
    const library = mountLibrary({ leaving: true, userId: 9571 });
    await library.settle();
    assert.deepEqual(sent(mark), []);
    // То же до ответа /ping: пространство известно, но открывать будут не главную.
    const early = mountLibrary({ ready: false, leaving: true, userId: 9574 });
    await early.settle();
    assert.deepEqual(sent(mark), []);

    // Просьба исполнена, человек вернулся на главную — теперь можно.
    await library.update({ leaving: false });
    assert.deepEqual(sent(mark).sort(), HOME_LISTS);
});

test('рельс: ответ о парках снимает заготовку и запоминается за пространством', async () => {
    const mark = calls.length;
    const library = mountLibrary({ userId: 958 });
    await library.settle();
    assert.match(library.html, SKELETON);

    pending('/parks', mark).answer({ items: [PARK, { ...PARK, id: 6, name: 'Архивный', status: 'archived' }],
                                     can_manage: false });
    await library.settle();
    assert.ok(library.html.includes('aria-label="Тест Такси"'));
    assert.ok(!library.html.includes('Архивный'));
    assert.ok(library.html.includes('Все статьи вики'));
    const kept = memory.recall(parksKey(958, 1));
    assert.equal(kept?.space, 1);
    assert.deepEqual(kept.items.map((park) => park.name), ['Тест Такси']);
    assert.ok(Date.now() - kept.at < 5000, 'у запомненного ответа нет времени — логотипы из него встанут и через сутки');
    assert.equal(memory.recall(parksKey(958, 9)), null);
});

test('рельс: молчащий запрос о парках держит заготовку не дольше полусекунды после ответа /ping', () => withTimers(async (timers) => {
    /* Рельс — справочник под рукой: застрявший запрос о нём не должен держать
       под заготовкой поиск и оглавление, которые уже пришли. */
    const early = mountLibrary({ ready: false, userId: 9591 });
    await early.settle();
    assert.equal(timers.filter((timer) => !timer.cleared && timer.ms >= 300).length, 0,
        'ожидание рельса пошло до ответа /ping — истечёт раньше, чем придут права');

    const library = mountLibrary({ userId: 959 });
    await library.settle();
    assert.match(library.html, SKELETON);

    const wait = timers.filter((timer) => !timer.cleared && timer.ms >= 300);
    assert.equal(wait.length, 1, 'у ожидания рельса нет таймера — застрявший /parks запер бы витрину');
    assert.ok(wait[0].ms <= 600, `ожидание рельса ${wait[0].ms} мс — дольше полусекунды`);
    wait[0].fn();
    await library.settle();
    assert.doesNotMatch(library.html, SKELETON, 'таймер сработал, а витрина всё ещё под заготовкой');
    assert.ok(library.html.includes('Все статьи вики'));
}));

test('рельс: ОТКАЗ о парках — это ответ «рельса нет», его запоминаем', async () => {
    /* Гостю и тому, кому справочник не выдан, сервер отвечает 404 каждый раз.
       Без запомненного «нет» такой человек на каждом входе и каждом возврате
       на главную ждал бы под заготовкой запроса, исход которого известен. */
    const mark = calls.length;
    const library = mountLibrary({ userId: 960 });
    await library.settle();
    pending('/parks', mark).refuse(404);
    await library.settle();

    assert.doesNotMatch(library.html, SKELETON);
    const kept = memory.recall(parksKey(960, 1));
    assert.deepEqual(kept?.items, [], 'отказ не запомнен — следующий вход снова начнётся с заготовки');
    // Следующий вход: витрина стоит с первого кадра, запрос уходит тихо.
    assert.doesNotMatch(mountLibrary({ userId: 960 }).html, SKELETON);
});

test('рельс: СБОЙ запроса о парках не стирает то, что о них знали', async () => {
    const mark = calls.length;
    memory.remember(parksKey(961, 1), parksAnswer(1, [PARK]));
    const library = mountLibrary({ userId: 961 });
    await library.settle();
    assert.ok(library.html.includes('aria-label="Тест Такси"'));

    pending('/parks', mark).fail();
    await library.settle();
    assert.ok(library.html.includes('aria-label="Тест Такси"'), 'сбой убрал с экрана рельс, который стоял');
    assert.deepEqual(memory.recall(parksKey(961, 1))?.items.map((park) => park.name), ['Тест Такси'],
        'сбой запомнен как «парков нет» — следующий вход покажет витрину без рельса');

    // Ничего не знали — сбой не держит витрину под заготовкой и в память не пишет.
    const blankMark = calls.length;
    const blank = mountLibrary({ userId: 962 });
    await blank.settle();
    pending('/parks', blankMark).refuse(502);
    await blank.settle();
    assert.doesNotMatch(blank.html, SKELETON);
    assert.equal(memory.recall(parksKey(962, 1)), null);
});

test('рельс: поздний ответ за прежнее пространство не встаёт в нынешнем', async () => {
    const mark = calls.length;
    const library = mountLibrary({ userId: 963, spaceId: 1 });
    await library.settle();
    const first = pending('/parks', mark);
    await library.update({ spaceId: 9 });
    assert.equal(sent(mark).filter((url) => url.startsWith('/parks')).length, 2);

    first.answer({ items: [PARK], can_manage: true });
    await library.settle();
    assert.ok(!library.html.includes('Тест Такси'), 'рельс прежнего пространства встал в нынешнем');
});

test('рельс: ответ, опоздавший к смене вики, не встаёт и там, где о парках ничего не узнали', async () => {
    /* Витрина стоит, вику сменили, запрос о парках новой вики сорвался — и тут
       доезжает ответ за прежнюю. «Прежний рельс остаётся» — это про то, что
       уже на экране, а не про ответ, который пришёл после ухода. */
    memory.remember(parksKey(9631, 1), parksAnswer(1, []));
    let mark = calls.length;
    const library = mountLibrary({ userId: 9631, spaceId: 1 });
    await library.settle();
    assert.ok(library.html.includes('Все статьи вики'));
    const late = pending('/parks', mark);

    mark = calls.length;
    await library.update({ spaceId: 9 });
    pending('/parks', mark).fail();
    await library.settle();
    late.answer({ items: [{ ...PARK, name: 'Парк прежней вики' }], can_manage: false });
    await library.settle();
    assert.ok(!library.html.includes('Парк прежней вики'), 'опоздавший ответ поставил рельс прежней вики');
});

test('рельс: смена вики не убирает с экрана уже стоящую витрину', async () => {
    /* Заготовка — только до первого кадра. Смена вики в шапке — не вход: на
       время запроса о парках новой вики обложка с поиском остаётся на месте. */
    memory.remember(parksKey(964, 1), parksAnswer(1, [PARK]));
    const library = mountLibrary({ userId: 964, spaceId: 1 });
    await library.settle();
    assert.ok(library.html.includes('Все статьи вики'));

    await library.update({ spaceId: 9, features: { parks: false, library_park_rail: false } });
    await library.update({ spaceId: 12, features: null });   // из пространства без парков — в третье
    assert.ok(library.html.includes('Все статьи вики'), 'витрина подменена заготовкой при смене вики');
    assert.doesNotMatch(library.html, SKELETON);
    assert.ok(!library.html.includes('Тест Такси'), 'в третьем пространстве встал рельс первого');
});

test('рельс: при смене вики давние логотипы из памяти тоже не рисуются', async () => {
    const logo = 'https://storage.test/logo.png';
    memory.remember(parksKey(968, 1), parksAnswer(1, [PARK]));
    memory.remember(parksKey(968, 9), parksAnswer(9, [{ ...PARK, id: 6, name: 'Тез Парк', logo_url: logo }],
        { at: Date.now() - 26 * 60 * 1000 }));
    const library = mountLibrary({ userId: 968, spaceId: 1 });
    await library.settle();

    await library.update({ spaceId: 9 });
    assert.ok(library.html.includes('aria-label="Тез Парк"'));
    assert.ok(!library.html.includes('storage.test'), 'логотип с истёкшей подписью встал из памяти при смене вики');
});

test('подборки: поздний ответ /home за прежнее пространство не ложится в состояние', async () => {
    const mark = calls.length;
    const library = mountLibrary({ userId: 965, spaceId: 1 });
    await library.settle();
    const first = pending('/home', mark);
    await library.update({ spaceId: 9 });
    const second = pending('/home', mark);
    assert.notEqual(first, second);

    const mineData = { favorites: [], recent: [], marker: 'нынешняя' };
    const staleData = { favorites: [], recent: [], marker: 'прежняя' };
    second.answer(mineData);
    await library.settle();
    assert.ok(library.holds(mineData));
    first.answer(staleData);
    await library.settle();
    assert.ok(!library.holds(staleData), 'подборки прежнего пространства легли поверх нынешних');
    assert.ok(library.holds(mineData));
});

/* ── Витрина: чего ждут оглавление и подборки ────────────────────────────── */

const NO_RAIL = { parks: false, library_park_rail: false };

test('оглавление ждёт дерево разделов: список статей без него не показывается', async () => {
    const mark = calls.length;
    const library = mountLibrary({ userId: 966, features: NO_RAIL, structurePending: true });
    await library.settle();
    pending('/articles', mark).answer({
        items: [{ id: 1, slug: 'a', title: 'Статья без дерева', section_ids: [7], status: 'published' }],
        total_visible: 1,
    });
    await library.settle();
    assert.ok(library.html.includes('Загружаем…'), 'оглавление нарисовано по одному списку, без дерева');
    assert.ok(!library.html.includes('Без раздела') && !library.html.includes('Статья без дерева'),
        'статьи встали в «Без раздела» и разъедутся по разделам, когда придёт дерево');

    await library.update({ structurePending: false, structure: { spaces: [], sections: [] } });
    assert.ok(!library.html.includes('Загружаем…'));
});

test('подборки редактора ждут каталог: полки не встают раньше плиток-счётчиков', async () => {
    const mark = calls.length;
    const library = mountLibrary({ userId: 967, features: NO_RAIL, canCreate: true, catalogPending: true });
    await library.settle();
    pending('/home', mark).answer({ favorites: [], recent: [] });
    await library.settle();
    assert.match(library.html, HOME_SHIMMER,
        'полки показаны до каталога — плитки встанут над ними и сдвинут их вниз');
    assert.ok(!library.html.includes('Пусто. Звёздочка'));

    await library.update({ catalogPending: false, catalog: { totals: {}, sections_total: 0 } });
    assert.doesNotMatch(library.html, HOME_SHIMMER);
});

test('на узких экранах оглавление встаёт вместе с подборками, а не раньше них', async () => {
    const mark = calls.length;
    const library = mountLibrary({ userId: 969, features: NO_RAIL });
    await library.settle();
    assert.match(library.html, /<div class="hidden lg:contents"><aside class="wiki-m-index/);

    pending('/home', mark).answer({ favorites: [], recent: [] });
    await library.settle();
    assert.match(library.html, /<div class="contents"><aside class="wiki-m-index/,
        'подборки пришли, а оглавление на телефоне так и не показано');
});
