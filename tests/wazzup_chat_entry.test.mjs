import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
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
