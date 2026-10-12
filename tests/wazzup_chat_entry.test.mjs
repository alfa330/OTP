import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import React from 'react';
import { build } from 'esbuild';
import { focusOnChatEntry } from '../src/components/wazzup/chatEntryFocus.js';
import { pilotDraftStorageKey } from '../src/components/wazzup/chatPilot.js';

// Вход в чат «Чатов ОП»: фокус сразу в поле сообщения (chatEntryFocus.js).

const MOUSE = '(hover: hover) and (pointer: fine)';
// Ровно те селекторы, что спрашивает правило; незнакомый — ошибка теста, а не «не совпало».
const matchesOne = (node, selector) => {
    if (['input', 'textarea', 'select', 'button', 'div', 'body'].includes(selector)) return node.tag === selector;
    if (selector === '[contenteditable]:not([contenteditable="false"])') {
        return node.attrs.contenteditable !== undefined && node.attrs.contenteditable !== 'false';
    }
    if (selector === '[role="dialog"]') return node.attrs.role === 'dialog';
    throw new Error(`селектор вне фикстуры: ${selector}`);
};
function page({ mouse = true, matchMedia = true } = {}) {
    const doc = { activeElement: null, defaultView: matchMedia
        ? { matchMedia: (query) => ({ matches: mouse && query === MOUSE }) } : {} };
    const element = (tag, attrs = {}, parent = null) => ({ tag, attrs, parent, ownerDocument: doc,
        matches(selector) { return selector.split(',').some((part) => matchesOne(this, part.trim())); },
        closest(selector) {
            for (let current = this; current; current = current.parent) if (current.matches(selector)) return current;
            return null;
        } });
    doc.body = element('body');
    doc.activeElement = doc.body;
    const field = Object.assign(element('textarea', {}, doc.body), {
        value: 'Добрый день', focused: [], selection: null,
        focus(options) { this.focused.push(options); doc.activeElement = this; },
        setSelectionRange(start, end) { this.selection = [start, end]; },
    });
    return { doc, field, element };
}

test('с мышью вход в чат ставит фокус в поле без прокрутки и курсор в конец черновика', () => {
    const { doc, field, element } = page();
    assert.equal(focusOnChatEntry(field), true);
    assert.deepEqual(field.focused, [{ preventScroll: true }]);
    assert.deepEqual(field.selection, [11, 11]);
    assert.equal(doc.activeElement, field);
    // Нажатая строка списка (кнопка) и «ничего в фокусе» фокус не держат.
    for (const active of [element('button', {}, doc.body), null, element('div', { contenteditable: 'false' }, doc.body)]) {
        const fresh = page();
        fresh.doc.activeElement = active && Object.assign(active, { ownerDocument: fresh.doc });
        assert.equal(focusOnChatEntry(fresh.field), true, active?.tag || 'null');
    }
});

test('без мыши фокус не ставится: экранная клавиатура закрыла бы переписку', () => {
    for (const options of [{ mouse: false }, { matchMedia: false }]) {
        const { doc, field } = page(options);
        assert.equal(focusOnChatEntry(field), false);
        assert.deepEqual(field.focused, []);
        assert.equal(field.selection, null);
        assert.equal(doc.activeElement, doc.body);
    }
});

test('не отбирает фокус у другого поля и у открытого окна', () => {
    const typing = [['input'], ['textarea'], ['select'], ['div', { contenteditable: 'true' }], ['div', { contenteditable: '' }]];
    for (const [tag, attrs] of typing) {
        const { doc, field, element } = page();
        doc.activeElement = element(tag, attrs, doc.body);
        assert.equal(focusOnChatEntry(field), false, `${tag} ${JSON.stringify(attrs || {})}`);
        assert.deepEqual(field.focused, []);
    }
    const { doc, field, element } = page();
    const viewer = element('div', { role: 'dialog' }, doc.body);
    doc.activeElement = element('button', {}, element('div', {}, viewer));
    assert.equal(focusOnChatEntry(field), false);
    assert.deepEqual(field.focused, []);
});

test('без поля или документа — тихо ничего', () => {
    assert.equal(focusOnChatEntry(null), false);
    assert.equal(focusOnChatEntry({ focus() { throw new Error('нет документа'); } }), false);
    assert.equal(focusOnChatEntry({ ownerDocument: page().doc }), false);
});

// Настоящее поле ввода: эффект берёт именно поле сообщения, и только при входе в чат.
const cache = join(process.cwd(), 'node_modules/.cache/otp-tests');
mkdirSync(cache, { recursive: true });
const output = join(cache, 'wazzup-chat-entry-composer.mjs');
await build({
    entryPoints: ['src/components/wazzup/ChatPilotComposer.jsx'], outfile: output,
    bundle: true, format: 'esm', platform: 'node', external: ['lucide-react'],
    plugins: [{ name: 'chat-entry-fixture', setup(builder) {
        builder.onResolve({ filter: /^(react|axios)$/ }, ({ path }) => ({ path, namespace: 'chat-entry' }));
        builder.onLoad({ filter: /.*/, namespace: 'chat-entry' }, ({ path }) => ({ loader: 'js', contents: path === 'react'
            ? `const h=()=>globalThis.__chatEntry;
                export const useState=(...x)=>h().useState(...x),useRef=(...x)=>h().useRef(...x),useEffect=(...x)=>h().useEffect(...x);
                export const useSyncExternalStore=(subscribe,get)=>get(),memo=(component)=>component,lazy=()=>()=>null,Suspense=({children})=>children;
                export default {createElement:(...x)=>h().createElement(...x)};`
            : 'export default {post:()=>Promise.reject(new Error("сеть в тесте не нужна"))};' }));
    } }],
});
const { default: Composer } = await import(pathToFileURL(output));
const find = (node, check) => {
    if (!node || typeof node !== 'object') return null;
    if (check(node)) return node;
    for (const child of React.Children.toArray(node.props?.children)) { const match = find(child, check); if (match) return match; }
    return null;
};

function composer({ mouse = true } = {}) {
    const previous = globalThis.sessionStorage;
    const store = new Map();
    globalThis.sessionStorage = { getItem: (key) => store.get(key) ?? null, setItem: (key, value) => store.set(key, String(value)),
        removeItem: (key) => store.delete(key) };
    const props = { apiBaseUrl: '/fixture', headers: () => ({}), chat: { channelId: 'channel-a', chatId: 'chat-a' }, onSend: () => true };
    let slots = [], cursor = 0, effects = [], previousKey = null;
    const h = {
        fields: [], store,
        createElement: React.createElement,
        useState(initial) { const i = cursor++; slots[i] ??= { value: typeof initial === 'function' ? initial() : initial };
            return [slots[i].value, (value) => { slots[i].value = typeof value === 'function' ? value(slots[i].value) : value; }]; },
        useRef(initial) { const i = cursor++; return slots[i] ??= { current: initial }; },
        useEffect(effect, deps) { const i = cursor++, old = slots[i];
            if (old && deps?.every((value, k) => Object.is(value, old.deps?.[k]))) return;
            slots[i] = { deps }; effects.push(() => { old?.cleanup?.(); slots[i].cleanup = effect(); }); },
        render(next = {}) {
            Object.assign(props, next);
            const wrapper = Composer(props);
            if (previousKey !== null && wrapper.key !== previousKey) { h.unmount(); slots = []; }
            previousKey = wrapper.key; cursor = 0;
            const tree = wrapper.type(wrapper.props);
            // Как коммит React: ref поля сообщения получает узел до эффектов.
            const textarea = find(tree, (node) => node.type === 'textarea');
            const ref = textarea.ref ?? textarea.props.ref;
            if (!ref.current) {
                const { field } = page({ mouse });
                ref.current = Object.assign(field, { id: textarea.props.id, style: {}, scrollHeight: 40 });
                h.fields.push(field);
            }
            ref.current.value = textarea.props.value;
            const pending = effects; effects = []; pending.forEach((effect) => effect());
            return tree;
        },
        unmount() { slots.forEach((slot) => slot?.cleanup?.()); },
        close() { h.unmount(); delete globalThis.__chatEntry;
            if (previous === undefined) delete globalThis.sessionStorage; else globalThis.sessionStorage = previous; },
    };
    globalThis.__chatEntry = h;
    return h;
}

test('поле сообщения получает фокус при входе в чат — один раз на чат', () => {
    const h = composer();
    try {
        h.store.set(pilotDraftStorageKey({ channelId: 'channel-a', chatId: 'chat-a' }), JSON.stringify({ text: 'Здравствуйте, Алия' }));
        h.render();
        assert.equal(h.fields.length, 1);
        assert.equal(h.fields[0].id, 'wazzup-pilot-message');
        assert.deepEqual(h.fields[0].focused, [{ preventScroll: true }]);
        assert.deepEqual(h.fields[0].selection, [18, 18]);
        // Перерисовка и правка текста в том же чате фокус заново не ставят — даже
        // когда человек уже ушёл из поля.
        h.render();
        const doc = h.fields[0].ownerDocument;
        doc.activeElement = doc.body;
        find(h.render(), (node) => node.type === 'textarea').props.onChange({ target: { value: 'Здравствуйте!' } });
        h.render();
        assert.deepEqual(h.fields[0].focused, [{ preventScroll: true }]);
        // Другой чат — новое поле (ключ чата) и снова фокус, курсор в конце его черновика.
        h.render({ chat: { channelId: 'channel-a', chatId: 'chat-b' } });
        assert.equal(h.fields.length, 2);
        assert.deepEqual(h.fields[1].focused, [{ preventScroll: true }]);
        assert.deepEqual(h.fields[1].selection, [0, 0]);
    } finally { h.close(); }
});

test('на сенсорном экране поле сообщения при входе в чат фокус не берёт', () => {
    const h = composer({ mouse: false });
    try {
        h.render();
        h.render({ chat: { channelId: 'channel-a', chatId: 'chat-b' } });
        assert.equal(h.fields.length, 2);
        assert.deepEqual(h.fields.map((field) => field.focused), [[], []]);
    } finally { h.close(); }
});

// Лента при входе в чат (useThreadEnd.js): настоящий хук; лента и ResizeObserver — подделки,
// которые ведут себя как браузер: прокрутка зажата в [0, scrollHeight − clientHeight],
// события scroll и наблюдения размеров тест присылает сам — тогда, когда их прислал бы браузер.
const threadEndOutput = join(cache, 'wazzup-thread-end.mjs');
await build({ entryPoints: ['src/components/wazzup/useThreadEnd.js'], outfile: threadEndOutput,
    bundle: true, format: 'esm', platform: 'node', plugins: [{ name: 'thread-end-react', setup(builder) {
        builder.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'thread-end' }));
        builder.onLoad({ filter: /.*/, namespace: 'thread-end' }, () => ({ loader: 'js', contents: `
            const h=()=>globalThis.__threadEnd;
            export const useRef=(...v)=>h().useRef(...v);
            export const useLayoutEffect=(...v)=>h().useLayoutEffect(...v);` }));
    } }] });
const { default: useThreadEnd } = await import(pathToFileURL(threadEndOutput));

function threadBox({ content = 600, client = 600, padding = 24 } = {}) {
    const box = {
        content: { part: 'сообщения' }, contentHeight: content, clientHeight: client, padding, top: 0,
        listeners: new Set(),
        get scrollHeight() { return this.contentHeight + this.padding; },
        get scrollTop() { return this.top; },
        set scrollTop(value) { this.top = Math.max(0, Math.min(value, this.scrollHeight - this.clientHeight)); },
        get firstElementChild() { return this.content; },
        addEventListener(name, listener, options) {
            assert.equal(name, 'scroll'); assert.equal(options?.passive, true); this.listeners.add(listener);
        },
        removeEventListener(name, listener) { if (name === 'scroll') this.listeners.delete(listener); },
        // Высота поменялась: браузер зажимает прокрутку, но сам её не двигает.
        resize({ content = this.contentHeight, client = this.clientHeight } = {}) {
            this.contentHeight = content; this.clientHeight = client; this.scrollTop = this.top;
        },
        scroll() { this.listeners.forEach((listener) => listener()); },
        gap() { return this.scrollHeight - this.top - this.clientHeight; },
    };
    return box;
}

function threadEnd({ resizeObserver = true } = {}) {
    const observers = [];
    class FakeResizeObserver {
        constructor(callback) { this.callback = callback; this.targets = new Set(); observers.push(this); }
        observe(target) { this.targets.add(target); }
        disconnect() { this.targets.clear(); }
    }
    const previous = globalThis.ResizeObserver;
    if (resizeObserver) globalThis.ResizeObserver = FakeResizeObserver; else delete globalThis.ResizeObserver;
    const boxRef = { current: null };
    const slots = [];
    let cursor = 0, effects = [];
    const h = {
        boxRef,
        useRef(initial) { const i = cursor++; return slots[i] ??= { current: initial }; },
        useLayoutEffect(effect, deps) {
            const i = cursor++, old = slots[i];
            if (old && deps.every((value, k) => Object.is(value, old.deps[k]))) return;
            slots[i] = { deps }; effects.push(() => { old?.cleanup?.(); slots[i].cleanup = effect(); });
        },
        render(props, box = boxRef.current) {
            boxRef.current = box;
            globalThis.__threadEnd = h; cursor = 0;
            useThreadEnd(boxRef, props);
            const pending = effects; effects = []; pending.forEach((effect) => effect());
        },
        // Наблюдение размера: только тем наблюдателям, что следят за этим узлом.
        observed(target) { observers.forEach((observer) => { if (observer.targets.has(target)) observer.callback([{ target }]); }); },
        watching: () => observers.filter((observer) => observer.targets.size).map((observer) => [...observer.targets]),
        close() {
            slots.forEach((slot) => slot?.cleanup?.()); delete globalThis.__threadEnd;
            if (previous === undefined) delete globalThis.ResizeObserver; else globalThis.ResizeObserver = previous;
        },
    };
    return h;
}

// Открыть чат: лента грузится (спиннер), потом React вставляет сообщения.
const openThread = (h, box, chatKey, content) => {
    h.render({ chatKey, loaded: false }, box);
    box.resize({ content });
    h.render({ chatKey, loaded: true }, box);
};

test('вход в чат: лента в конце сразу, как React вставил сообщения, — без кадра', () => {
    const h = threadEnd();
    try {
        const box = threadBox();
        h.render({ chatKey: 'A', loaded: false }, box);
        assert.equal(box.top, 0);
        box.resize({ content: 5000 });
        h.render({ chatKey: 'A', loaded: true });
        assert.equal(box.gap(), 0);
        assert.equal(box.top, 5024 - 600);
        // Ответ без перезагрузки ленты (сверка, правки) — сам по себе прокрутку не ставит.
        box.scrollTop = 1000;
        h.render({ chatKey: 'A', loaded: true });
        assert.equal(box.top, 1000);
    } finally { h.close(); }
});

test('пока человек внизу, поздние заметка, кнопка и поле ввода не уводят его от последних сообщений', () => {
    const h = threadEnd();
    try {
        const box = threadBox();
        openThread(h, box, 'A', 5000);
        // Заметка пришла позже ленты — выросла сама лента.
        box.resize({ content: 5120 }); h.observed(box.content);
        assert.equal(box.gap(), 0);
        // Поле ввода появилось позже — окно ленты стало ниже.
        box.resize({ client: 520 }); h.observed(box);
        assert.equal(box.gap(), 0);
        // «Ответ не нужен»: событие scroll пришло раньше наблюдения размера — догоняем сразу.
        box.resize({ content: 5160 }); box.scroll();
        assert.equal(box.gap(), 0);
        // Кнопку убрали — браузер зажал прокрутку; человек всё ещё внизу, и от нового низа
        // считается следующий рост (событие scroll снова раньше наблюдения).
        box.resize({ content: 5120 }); box.scroll();
        box.resize({ content: 5300 }); box.scroll();
        assert.equal(box.gap(), 0);
        box.resize({ content: 5420 }); h.observed(box.content);
        assert.equal(box.gap(), 0);
    } finally { h.close(); }
});

test('прокрутка вверх снимает прилипание к низу, возврат вниз — возвращает', () => {
    const h = threadEnd();
    try {
        const box = threadBox();
        openThread(h, box, 'A', 5000);
        // Сдвиг в пару пикселей — всё ещё внизу.
        box.scrollTop = box.top - 2; box.scroll();
        box.resize({ content: 5060 }); h.observed(box.content);
        assert.equal(box.gap(), 0);
        // Ушёл читать выше: новая высота его не двигает.
        box.scrollTop = box.top - 30; box.scroll();
        const reading = box.top;
        box.resize({ content: 5200 }); h.observed(box.content);
        box.resize({ client: 520 }); h.observed(box);
        assert.equal(box.top, reading);
        box.resize({ content: 5260 }); box.scroll();
        assert.equal(box.top, reading);
        // Вернулся вниз сам — снова держимся у последних.
        box.scrollTop = box.scrollHeight; box.scroll();
        box.resize({ content: 5400 }); h.observed(box.content);
        assert.equal(box.gap(), 0);
    } finally { h.close(); }
});

test('дробная прокрутка при масштабе экрана не снимает прилипание', () => {
    const h = threadEnd();
    try {
        const box = threadBox({ padding: 24.67 });
        openThread(h, box, 'A', 5000);
        assert.equal(box.top, 4424.67);
        // Браузер округлил прокрутку до пикселя экрана, и тут же лента подросла снизу.
        box.top = 4424.5; box.resize({ content: 5020 }); box.scroll();
        assert.equal(box.gap(), 0);
    } finally { h.close(); }
});

test('новый чат и «Обновить» — снова с последних сообщений; подписки — на одну ленту', () => {
    const h = threadEnd();
    try {
        const box = threadBox();
        openThread(h, box, 'A', 5000);
        box.scrollTop = 200; box.scroll();
        openThread(h, box, 'B', 3000);
        assert.equal(box.gap(), 0);
        assert.equal(box.listeners.size, 1);
        assert.deepEqual(h.watching(), [[box, box.content]]);
        box.scrollTop = 100; box.scroll();
        openThread(h, box, 'B', 3200);
        assert.equal(box.gap(), 0);
        // Смена ключа без перезагрузки ленты прокрутку не ставит — даже вернувшись к прежнему чату.
        box.scrollTop = 100; box.scroll();
        h.render({ chatKey: 'C', loaded: true });
        h.render({ chatKey: 'B', loaded: true });
        assert.equal(box.top, 100);
    } finally { h.close(); }
});

test('чат закрыт — подписки сняты; новая лента — новые подписки; без ResizeObserver не падает', () => {
    const h = threadEnd();
    try {
        const box = threadBox();
        openThread(h, box, 'A', 5000);
        h.render({ chatKey: '', loaded: false }, null);
        assert.equal(box.listeners.size, 0);
        assert.deepEqual(h.watching(), []);
        h.render({ chatKey: '', loaded: true }, null);
        const next = threadBox();
        openThread(h, next, 'B', 4000);
        assert.equal(next.gap(), 0);
        assert.equal(next.listeners.size, 1);
        assert.deepEqual(h.watching(), [[next, next.content]]);
    } finally { h.close(); }
    const bare = threadEnd({ resizeObserver: false });
    try {
        const box = threadBox();
        openThread(bare, box, 'A', 5000);
        assert.equal(box.gap(), 0);
        box.resize({ content: 5100 }); box.scroll();
        assert.equal(box.gap(), 0);
    } finally { bare.close(); }
});

test('раздел: лента подключена к useThreadEnd, загрузка переписки не крутит её по кадру', () => {
    const view = readFileSync('src/components/wazzup/WazzupChatsView.jsx', 'utf8').replace(/\r\n/g, '\n');
    assert.match(view, /\n {4}useThreadEnd\(threadBox, \{ chatKey: selectedKey, loaded: thread !== null \}\);\n/);
    assert.match(view, /<div ref=\{threadBox\} className="[^"]*overflow-y-auto[^"]*">\n\s*<div className="mx-auto w-full/);
    const start = view.indexOf('    const loadThread = ');
    const loadThread = view.slice(start, view.indexOf('    const refreshPilotThread = ', start));
    // Кадр остался только у «Более ранних»: там позиция считается от прежней высоты.
    assert.equal(loadThread.split('requestAnimationFrame(').length - 1, 1);
    assert.match(loadThread, /if \(before\) \{[\s\S]*requestAnimationFrame\([\s\S]*\} else \{\n[^\n]*\n\s*setThread\(r\.data\.items \|\| \[\]\);\n\s*\}/);
});

test('шапка чата: у оператора счётчика «вх. · исх.» нет, у остальных — прежний', () => {
    const view = readFileSync('src/components/wazzup/WazzupChatsView.jsx', 'utf8').replace(/\r\n/g, '\n');
    assert.match(view, /\{!operator && selected\.messagesCount != null && \(\n\s*<IosBadge tone="slate">\n\s*\{selected\.inboundCount \?\? 0\} вх\. · \{selected\.outboundCount \?\? 0\} исх\.\n/);
    // Других мест со счётчиком в разделе нет.
    assert.equal(view.split('} вх. · {').length - 1, 1);
});
