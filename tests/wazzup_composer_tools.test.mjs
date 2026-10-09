import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url);
const React = require('react');
const { build } = require('esbuild');
const cache = join(process.cwd(), 'node_modules', '.cache', 'otp-tests');
mkdirSync(cache, { recursive: true });
const output = join(cache, 'ChatComposerToolsTemplatesTest.mjs');
await build({ entryPoints: [join(process.cwd(), 'src/components/wazzup/ChatComposerTools.jsx')],
    outfile: output, bundle: true, format: 'esm', platform: 'node', external: ['lucide-react'],
    plugins: [{ name: 'tools-fixture', setup(builder) {
        builder.onResolve({ filter: /^(react|axios)$|chatEmojiLoading$/ }, ({ path }) => ({ path, namespace: 'fixture' }));
        builder.onLoad({ filter: /.*/, namespace: 'fixture' }, ({ path }) => ({ loader: 'js', contents: path === 'react'
            ? `const h=()=>globalThis.__templateTools; export const useState=(...x)=>h().useState(...x);
                export const useRef=(...x)=>h().useRef(...x); export const useEffect=(...x)=>h().useEffect(...x);
                export default {Fragment:'fixture-fragment',createElement:(...x)=>h().createElement(...x)};`
            : path === 'axios' ? `export default Object.fromEntries(['get','post','patch','delete'].map(method=>[method,(...args)=>globalThis.__templateTools.request(method,...args)]));`
                : `export const loadChatEmojiPicker=async()=>({default:()=>null}); export const warmChatEmojiPicker=loadChatEmojiPicker; export const scheduleEmojiWarmup=()=>()=>{};` }));
    } }] });
const { default: Tools } = await import(pathToFileURL(output));
const tick = () => new Promise((resolve) => setImmediate(resolve));
const plain = { id: 'plain', source: 'wazzup', kind: 'text', supported: true, title: 'Быстрый ответ', text: 'Обычный текст', variables: [], channels: [] };
const waba = { id: 'waba', source: 'wazzup', kind: 'waba', supported: true, title: 'WABA приветствие', text: 'Шаблон', templateCode: '[[code]]', variables: [] };
const find = (tree, predicate) => {
    if (!tree || typeof tree !== 'object') return null;
    if (predicate(tree)) return tree;
    if (typeof tree.type === 'function' && tree.type.name === 'WhatsAppMark') return find(tree.type(tree.props), predicate);
    for (const child of React.Children.toArray(tree.props?.children)) { const match = find(child, predicate); if (match) return match; }
    return null;
};
const label = (tree, value) => find(tree, (node) => node.props?.['aria-label'] === value);
const button = (tree, value) => find(tree, (node) => node.type === 'button' && React.Children.toArray(node.props.children).includes(value));
const dialog = (tree) => find(tree, (node) => node.props?.role === 'dialog' && node.props?.['aria-label'] === 'Шаблоны сообщений');

function fixture(request) {
    const oldDocument = globalThis.document;
    const slots = [], listeners = new Map(), calls = [], choices = [];
    let index = 0, effects = [], focused = 0;
    const inside = {};
    const root = { contains: (target) => target === inside };
    const props = { apiBaseUrl: '/fixture', channelId: 'channel', locked: false, slash: null,
        headers: () => ({}), onChoose: (item) => choices.push(item), onEmoji() {} };
    globalThis.document = { addEventListener: (name, handler) => listeners.set(name, handler),
        removeEventListener: (name, handler) => { if (listeners.get(name) === handler) listeners.delete(name); } };
    const h = {
        createElement: React.createElement,
        request(method, ...args) { calls.push({ method, args }); return request?.(method, ...args) || Promise.resolve({ data: { items: [plain, waba] } }); },
        useState(initial) { const i = index++; slots[i] ??= { value: typeof initial === 'function' ? initial() : initial };
            return [slots[i].value, (value) => { slots[i].value = typeof value === 'function' ? value(slots[i].value) : value; }]; },
        useRef(initial) { const i = index++; return slots[i] ??= { current: initial }; },
        useEffect(fn, deps) { const i = index++, previous = slots[i]; if (previous && deps.every((v, k) => Object.is(v, previous.deps[k]))) return;
            slots[i] = { deps }; effects.push(() => { previous?.cleanup?.(); slots[i].cleanup = fn(); }); },
        render(next = {}) { Object.assign(props, next); index = 0; const tree = Tools(props);
            tree.ref.current = root;
            label(tree, 'Шаблоны сообщений').ref.current = { focus: () => { focused++; } };
            const pending = effects; effects = []; pending.forEach((fn) => fn()); return tree; },
        async open() { label(this.render(), 'Шаблоны сообщений').props.onClick(); this.render(); await tick(); return this.render(); },
        outside() { listeners.get('pointerdown')?.({ target: {} }); },
        inside() { listeners.get('pointerdown')?.({ target: inside }); },
        escape() { listeners.get('keydown')?.({ key: 'Escape', preventDefault() {}, stopPropagation() {} }); },
        restore() { slots.forEach((slot) => slot?.cleanup?.()); globalThis.document = oldDocument; delete globalThis.__templateTools; },
        calls, choices, get focused() { return focused; },
    };
    globalThis.__templateTools = h;
    return h;
}

test('outside pointer and Escape close templates; inside clicks and unchanged slash do not reopen them', async () => {
    const h = fixture();
    try {
        h.render({ slash: '' }); h.render(); await tick(); assert.ok(dialog(h.render()));
        h.inside(); assert.ok(dialog(h.render()));
        h.outside(); assert.equal(dialog(h.render()), null);
        h.render({ locked: true }); h.render({ locked: false }); assert.equal(dialog(h.render()), null);
        assert.ok(dialog(await h.open()));
        h.escape(); assert.equal(dialog(h.render()), null); assert.equal(h.focused, 1);
    } finally { h.restore(); }
});

test('ordinary Wazzup replies insert plain text, while only WABA templates display WhatsApp', async () => {
    const h = fixture();
    try {
        const tree = await h.open();
        const plainButton = find(tree, (node) => node.type === 'button' && node.props.title === plain.text);
        const wabaButton = find(tree, (node) => node.type === 'button' && node.props.title === waba.text);
        assert.equal(label(plainButton, 'Шаблон WhatsApp Business'), null);
        assert.ok(label(wabaButton, 'Шаблон WhatsApp Business'));
        plainButton.props.onClick();
        assert.deepEqual(h.choices, [{ text: plain.text, preview: '' }]);
        assert.equal(dialog(h.render()), null);
    } finally { h.restore(); }
});

test('template search matches only titles, ignoring case and surrounding query whitespace', async () => {
    const bodyOnly = { ...plain, id: 'body-only', title: 'Инструкция', text: 'Быстрый ответ внутри сообщения' };
    const items = [plain, bodyOnly, waba];
    const h = fixture(() => Promise.resolve({ data: { items } }));
    const option = (tree, item) => find(tree, (node) => node.type === 'button' && node.props.title === item.text);
    try {
        let tree = await h.open();
        assert.equal(label(tree, 'Найти шаблон').props.placeholder, 'Найти по названию…');
        for (const query of ['БЫСТРЫЙ', '  быстРЫЙ ответ  ', '\tОТВЕТ\n']) {
            label(tree, 'Найти шаблон').props.onChange({ target: { value: query } });
            tree = h.render();
            assert.ok(option(tree, plain));
            assert.equal(option(tree, bodyOnly), null, 'a body-only match must not enter search results');
            assert.equal(option(tree, waba), null);
        }
        label(tree, 'Найти шаблон').props.onChange({ target: { value: 'Обычный текст' } });
        tree = h.render();
        assert.ok(items.every((item) => option(tree, item) === null));
        for (const query of ['', '   ']) {
            label(tree, 'Найти шаблон').props.onChange({ target: { value: query } });
            tree = h.render();
            assert.ok(items.every((item) => option(tree, item)), 'clearing the search restores all templates');
        }
    } finally { h.restore(); }
});

test('slash title search keeps templates with the same name distinct and inserts the chosen one', async () => {
    const local = { ...plain, source: 'icore', text: 'Локальный ответ' };
    const h = fixture(() => Promise.resolve({ data: { items: [plain, local, waba] } }));
    try {
        h.render({ slash: ' ОТВЕТ ' }); h.render(); await tick();
        const tree = h.render();
        const sourceButton = find(tree, (node) => node.type === 'button' && node.props.title === plain.text);
        const localButton = find(tree, (node) => node.type === 'button' && node.props.title === local.text);
        assert.ok(sourceButton && localButton, 'matching names do not collapse distinct source/ID pairs');
        assert.equal(find(tree, (node) => node.type === 'button' && node.props.title === waba.text), null);
        localButton.props.onClick();
        assert.deepEqual(h.choices, [{ text: local.text, preview: '' }]);
    } finally { h.restore(); }
});

test('Enter stays inside template dialog and cannot implicitly submit the message form', async () => {
    const h = fixture();
    try {
        const handler = dialog(await h.open()).props.onKeyDown;
        for (const tagName of ['INPUT', 'TEXTAREA', 'BUTTON']) {
            let prevented = false, stopped = false;
            handler({ key: 'Enter', target: { tagName }, preventDefault() { prevented = true; }, stopPropagation() { stopped = true; } });
            assert.equal(stopped, true);
            assert.equal(prevented, tagName === 'INPUT');
        }
        assert.equal(h.choices.length, 0);
    } finally { h.restore(); }
});

test('outside dismissal and Escape preserve an unsaved template draft on reopen', async () => {
    const h = fixture();
    try {
        button(await h.open(), '+ iCORE').props.onClick();
        label(h.render(), 'Название шаблона').props.onChange({ target: { value: 'Незавершённый шаблон' } });
        label(h.render(), 'Текст шаблона').props.onChange({ target: { value: 'Первая строка\nПродолжение ответа' } });
        for (const dismiss of [() => h.outside(), () => h.escape()]) {
            dismiss(); assert.equal(dialog(h.render()), null);
            const tree = await h.open();
            assert.equal(label(tree, 'Название шаблона').props.value, 'Незавершённый шаблон');
            assert.equal(label(tree, 'Текст шаблона').props.value, 'Первая строка\nПродолжение ответа');
        }
        assert.equal(h.calls.some(({ method }) => method !== 'get'), false, 'hiding never saves the draft');
        button(h.render(), 'Отмена').props.onClick();
        h.outside(); h.render();
        assert.equal(label(await h.open(), 'Название шаблона'), null, 'explicit cancellation discards editing');
    } finally { h.restore(); }
});

test('WABA variable draft survives dismissal and clears after successful insertion', async () => {
    const item = { ...waba, variables: ['bodyVar1'], text: 'Здравствуйте, {{1}}!', templateCode: '[[code]][[bodyVar1]]' };
    const h = fixture(() => Promise.resolve({ data: { items: [item] } }));
    try {
        find(await h.open(), (node) => node.type === 'button' && node.props.title === item.text).props.onClick();
        label(h.render(), 'Переменная 1').props.onChange({ target: { value: 'Алия' } });
        h.outside(); h.render();
        assert.equal(label(await h.open(), 'Переменная 1').props.value, 'Алия');
        button(h.render(), 'Вставить').props.onClick(); h.render();
        assert.deepEqual(h.choices, [{ text: '[[code]][[Алия]]', preview: 'Здравствуйте, Алия!' }]);
        const tree = await h.open();
        assert.equal(label(tree, 'Переменная 1'), null);
        find(tree, (node) => node.type === 'button' && node.props.title === item.text).props.onClick();
        assert.equal(label(h.render(), 'Переменная 1').props.value, '');
    } finally { h.restore(); }
});

test('pending template save blocks dismissals and duplicate same-tick writes', async () => {
    let finish;
    const h = fixture((method) => method === 'post' ? new Promise((resolve) => { finish = resolve; }) : null);
    try {
        button(await h.open(), '+ iCORE').props.onClick();
        label(h.render(), 'Название шаблона').props.onChange({ target: { value: 'Новый' } });
        label(h.render(), 'Текст шаблона').props.onChange({ target: { value: 'Ответ' } });
        const save = button(h.render(), 'Сохранить').props.onClick;
        const pending = save(); await save();
        h.outside(); h.escape(); assert.ok(dialog(h.render()));
        assert.equal(h.calls.filter(({ method }) => method === 'post').length, 1);
        assert.equal(label(h.render(), 'Закрыть').props.disabled, true);
        finish({ data: { item: { ...plain, source: 'icore', id: 'new' } } }); await pending;
        h.outside(); assert.equal(dialog(h.render()), null);
    } finally { h.restore(); }
});

test('upstream failures stay visible without the availability hint', async () => {
    const warning = 'Не удалось загрузить шаблоны Wazzup. Попробуйте позднее.';
    const h = fixture(() => Promise.resolve({ data: { items: [], sourceWarnings: [warning] } }));
    try {
        const tree = await h.open();
        assert.ok(find(tree, (node) => node.props?.role === 'status' && node.props.children === warning));
        assert.equal(find(tree, (node) => node.type === 'summary'), null);
    } finally { h.restore(); }
});

test('pending deletion cannot be dismissed and releases the panel after failure', async () => {
    let fail;
    const local = { ...plain, source: 'icore', title: 'Общий ответ' };
    const h = fixture((method) => method === 'delete' ? new Promise((resolve, reject) => { fail = reject; })
        : Promise.resolve({ data: { items: [local] } }));
    try {
        label(await h.open(), 'Удалить Общий ответ').props.onClick();
        const remove = button(h.render(), 'Удалить').props.onClick;
        const pending = remove(); await remove();
        h.outside(); h.escape(); assert.ok(dialog(h.render()));
        assert.equal(h.calls.filter(({ method }) => method === 'delete').length, 1);
        fail({ response: { data: { error: 'Не удалось удалить шаблон' } } }); await pending;
        assert.ok(find(h.render(), (node) => node.props?.role === 'alert' && node.props.children === 'Не удалось удалить шаблон'));
        h.escape(); assert.equal(dialog(h.render()), null);
    } finally { h.restore(); }
});
