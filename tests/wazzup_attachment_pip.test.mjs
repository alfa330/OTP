import test from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const cache = join(process.cwd(), 'node_modules/.cache/otp-tests');
mkdirSync(cache, { recursive: true });
const output = join(cache, 'wazzup-attachment-pip.mjs');
await build({ entryPoints: ['src/components/wazzup/useAttachmentPip.js'], outfile: output,
    bundle: true, format: 'esm', platform: 'node', plugins: [{ name: 'pip-fixture', setup(builder) {
        builder.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'fixture' }));
        builder.onLoad({ filter: /.*/, namespace: 'fixture' }, () => ({ contents: `
            const h=()=>globalThis.__pipHarness;
            export const useState=(...v)=>h().useState(...v);
            export const useRef=(...v)=>h().useRef(...v);
            export const useEffect=(...v)=>h().useEffect(...v);
            export const useCallback=(...v)=>h().useCallback(...v);
        ` }));
    } }] });
const { default: useAttachmentPip } = await import(pathToFileURL(output));

const deferred = () => {
    let resolve, reject;
    const promise = new Promise((a, b) => { resolve = a; reject = b; });
    return { promise, resolve, reject };
};
const node = (tagName, textContent = '', href = '') => ({
    tagName, textContent, href, parentNode: null,
    get nextSibling() { return this.parentNode?.childNodes[this.parentNode.childNodes.indexOf(this) + 1] ?? null; },
    remove() { if (!this.parentNode) return;
        this.parentNode.childNodes.splice(this.parentNode.childNodes.indexOf(this), 1); this.parentNode = null; },
    cloneNode() { return node(this.tagName, this.textContent, this.href); },
    isEqualNode(other) { return this.tagName === other.tagName && this.textContent === other.textContent && this.href === other.href; },
});
const dom = () => {
    const attrs = new Map();
    return { head: { childNodes: [],
            get children() { return this.childNodes.filter((child) => child.tagName); },
            appendChild(child) { child.remove(); child.parentNode = this; this.childNodes.push(child); },
            insertBefore(child, next) { child.remove(); child.parentNode = this;
                this.childNodes.splice(this.childNodes.indexOf(next), 0, child); },
        },
        createComment(text) { return node(null, text); },
        body: { className: '', style: {} },
        documentElement: {
            setAttribute(name, value) { attrs.set(name, value); },
            getAttribute(name) { return attrs.get(name) ?? null; },
            hasAttribute(name) { return attrs.has(name); },
            removeAttribute(name) { attrs.delete(name); },
        },
        querySelectorAll() { return []; },
    };
};
const makeWindow = () => {
    const events = new Map();
    return { document: dom(), closed: false, closeCalls: 0, focusCalls: 0, events,
        addEventListener(type, handler) { events.set(type, handler); },
        removeEventListener(type, handler) { if (events.get(type) === handler) events.delete(type); },
        emit(type) { events.get(type)?.(); },
        focus() { this.focusCalls += 1; },
        close() { this.closeCalls += 1; this.closed = true; this.emit('pagehide'); },
    };
};

function fixture({ supported = true, occupied = null, onReturn, request } = {}) {
    const saved = Object.fromEntries(['window', 'document', 'MutationObserver'].map((key) => [key, globalThis[key]]));
    const doc = dom(), target = makeWindow(), observers = [], calls = [], slots = [];
    doc.documentElement.setAttribute('data-otp-theme', 'dark');
    doc.body.className = 'font-sans';
    doc.styles = [node('LINK', '', 'https://example.invalid/assets/app.css')];
    doc.querySelectorAll = () => doc.styles;
    globalThis.document = doc;
    globalThis.window = { documentPictureInPicture: supported ? {
        window: occupied,
        requestWindow(options) { calls.push(options); return request ? request(options) : Promise.resolve(target); },
    } : undefined };
    globalThis.MutationObserver = class {
        constructor(callback) { this.callback = callback; this.targets = []; this.disconnected = false; observers.push(this); }
        observe(node, options) { this.targets.push({ node, options }); }
        disconnect() { this.disconnected = true; }
    };
    let cursor = 0, effects = [], unmounted = false, lateWrites = 0;
    const h = {
        target, doc, calls, observers, onReturn,
        useState(initial) { const i = cursor++; slots[i] ??= { value: initial };
            return [slots[i].value, (value) => { if (unmounted) lateWrites += 1;
                slots[i].value = typeof value === 'function' ? value(slots[i].value) : value; }]; },
        useRef(initial) { const i = cursor++; slots[i] ??= { current: initial }; return slots[i]; },
        useCallback(fn, deps) { const i = cursor++, old = slots[i];
            if (!old || deps.some((value, index) => !Object.is(value, old.deps[index]))) slots[i] = { value: fn, deps };
            return slots[i].value; },
        useEffect(effect, deps) { const i = cursor++, old = slots[i];
            if (old && deps.every((value, index) => Object.is(value, old.deps[index]))) return;
            slots[i] = { deps, effect }; effects.push(() => { old?.cleanup?.(); slots[i].cleanup = effect(); }); },
        render() { globalThis.__pipHarness = h; cursor = 0;
            const value = useAttachmentPip({ onReturn: h.onReturn });
            const pending = effects; effects = []; pending.forEach((fn) => fn());
            return value; },
        restartEffects() { slots.forEach((slot) => slot.cleanup?.());
            slots.forEach((slot) => { if (slot.effect) slot.cleanup = slot.effect(); }); },
        unmount() { if (!unmounted) { unmounted = true; slots.forEach((slot) => slot.cleanup?.()); } },
        restore() { h.unmount(); delete globalThis.__pipHarness;
            Object.entries(saved).forEach(([key, value]) => { if (value === undefined) delete globalThis[key]; else globalThis[key] = value; }); },
        get lateWrites() { return lateWrites; },
        get themeObserver() { return observers.find((observer) => observer.targets.some(({ node: target }) => target === doc.documentElement)); },
        get styleObserver() { return observers.find((observer) => observer.targets.some(({ node: target }) => target === doc.head)); },
    };
    return h;
}

test('unsupported browsers keep the normal viewer and explain how to open PiP', async () => {
    const h = fixture({ supported: false });
    try {
        const view = h.render();
        assert.equal(view.supported, false);
        assert.equal(await view.open(), null);
        assert.match(h.render().error, /Chrome или Edge/);
        assert.equal(h.calls.length, 0);
    } finally { h.restore(); }
});

test('another widget retains its PiP window', async () => {
    const other = makeWindow(), h = fixture({ occupied: other });
    try {
        assert.equal(await h.render().open(), null);
        assert.equal(h.calls.length, 0);
        assert.equal(other.closeCalls, 0);
        assert.match(h.render().error, /занято другим виджетом/);
    } finally { h.restore(); }
});

test('opening copies styles and theme before exposing the window, and returning moves the host first', async () => {
    let returned = 0;
    const h = fixture({ onReturn() { returned += 1; assert.equal(h.target.closed, false); } });
    try {
        const opening = h.render().open();
        assert.deepEqual(h.calls, [{ width: 960, height: 720 }], 'request happens before yielding user activation');
        assert.equal(h.render().opening, true);
        assert.equal(await opening, h.target);
        const view = h.render();
        assert.equal(view.pipWindow, h.target);
        assert.equal(view.opening, false);
        assert.equal(h.target.document.title, 'Вложения — Чаты ОП');
        assert.equal(h.target.document.head.children[0].href, 'https://example.invalid/assets/app.css');
        assert.equal(h.target.document.documentElement.getAttribute('data-otp-theme'), 'dark');
        assert.equal(h.target.document.body.className, 'font-sans');
        assert.equal(h.target.document.body.style.overflow, 'hidden');
        view.returnToChat();
        assert.equal(returned, 1);
        assert.equal(h.target.closeCalls, 1);
        assert.equal(h.target.events.size, 0);
        assert.equal(h.observers[0].disconnected, true);
        assert.equal(h.render().pipWindow, null);
        h.render().returnToChat();
        assert.equal(returned, 1);
    } finally { h.restore(); }
});

test('the browser close button returns the viewer without closing an unrelated window', async () => {
    let returned = 0;
    const h = fixture({ onReturn() { returned += 1; } });
    try {
        await h.render().open();
        const other = makeWindow();
        window.documentPictureInPicture.window = other;
        h.target.closed = true;
        h.target.emit('pagehide');
        assert.equal(h.render().pipWindow, null);
        assert.equal(returned, 1);
        assert.equal(h.target.closeCalls, 0);
        assert.equal(other.closeCalls, 0);
        assert.equal(h.observers[0].disconnected, true);
    } finally { h.restore(); }
});

test('double clicks share an opening request and focus the already opened own window', async () => {
    const pending = deferred(), h = fixture({ request: () => pending.promise });
    try {
        const first = h.render().open();
        assert.equal(await h.render().open(), null);
        assert.equal(h.calls.length, 1);
        pending.resolve(h.target);
        await first;
        window.documentPictureInPicture.window = h.target;
        assert.equal(await h.render().open(), h.target);
        assert.equal(h.calls.length, 1);
        assert.equal(h.target.focusCalls, 1);
    } finally { h.restore(); }
});

test('a rejected request leaves the viewer in place and permits retry', async () => {
    let fail = true;
    const h = fixture({ request() { return fail ? Promise.reject(new Error('NotAllowedError')) : Promise.resolve(h.target); } });
    try {
        assert.equal(await h.render().open(), null);
        assert.match(h.render().error, /Не удалось открыть/);
        assert.equal(h.render().opening, false);
        assert.equal(h.render().pipWindow, null);
        fail = false;
        assert.equal(await h.render().open(), h.target);
        assert.equal(h.render().error, '');
    } finally { h.restore(); }
});

test('returning during an unresolved request closes its late window without moving the viewer', async () => {
    const pending = deferred(); let returned = 0;
    const h = fixture({ request: () => pending.promise, onReturn() { returned += 1; } });
    try {
        const opening = h.render().open();
        h.render().returnToChat();
        assert.equal(h.render().opening, false);
        pending.resolve(h.target);
        assert.equal(await opening, null);
        assert.equal(h.target.closeCalls, 1);
        assert.equal(h.render().pipWindow, null);
        assert.equal(returned, 0);
    } finally { h.restore(); }
});

test('unmount during a pending open disposes the late window without React updates', async () => {
    const pending = deferred(), h = fixture({ request: () => pending.promise });
    try {
        const opening = h.render().open();
        h.unmount();
        pending.resolve(h.target);
        await opening;
        assert.equal(h.target.closeCalls, 1);
        assert.equal(h.lateWrites, 0);
    } finally { h.restore(); }
});

test('unmount restores the portal host and releases listeners and its own window', async () => {
    let returned = 0;
    const h = fixture({ onReturn() { returned += 1; assert.equal(h.target.closed, false); } });
    try {
        await h.render().open();
        h.unmount();
        assert.equal(returned, 1);
        assert.equal(h.target.closeCalls, 1);
        assert.equal(h.target.events.size, 0);
        assert.equal(h.observers[0].disconnected, true);
        assert.equal(h.lateWrites, 0);
    } finally { h.restore(); }
});

test('React StrictMode effect replay cancels an old request and can open a fresh window', async () => {
    const old = deferred(), fresh = makeWindow(); let calls = 0;
    const h = fixture({ request() { return ++calls === 1 ? old.promise : Promise.resolve(fresh); } });
    try {
        const first = h.render().open();
        h.restartEffects();
        assert.equal(await h.render().open(), fresh);
        old.resolve(h.target);
        assert.equal(await first, null);
        assert.equal(h.target.closeCalls, 1);
        assert.equal(h.render().pipWindow, fresh);
        assert.equal(fresh.closeCalls, 0);
    } finally { h.restore(); }
});

test('theme updates follow the main document and clearing theme or body classes is mirrored', async () => {
    const h = fixture();
    try {
        await h.render().open();
        h.doc.documentElement.setAttribute('data-otp-theme', 'light');
        h.doc.body.className = 'another-font';
        h.themeObserver.callback();
        assert.equal(h.target.document.documentElement.getAttribute('data-otp-theme'), 'light');
        assert.equal(h.target.document.body.className, 'another-font');
        h.doc.documentElement.removeAttribute('data-otp-theme');
        h.doc.body.className = '';
        h.themeObserver.callback();
        assert.equal(h.target.document.documentElement.getAttribute('data-otp-theme'), null);
        assert.equal(h.target.document.body.className, '');
    } finally { h.restore(); }
});

test('lazy PDF styles and dev stylesheet changes reach PiP while preserving unrelated head nodes', async () => {
    const h = fixture();
    try {
        const foreign = node('STYLE', '.pip-only { color: red; }');
        h.target.document.head.appendChild(foreign);
        await h.render().open();
        const head = h.target.document.head;
        const initialLink = head.children[1];
        const pdf = node('LINK', '', 'https://example.invalid/assets/pdf-lazy.css');
        const vite = node('STYLE', '.pdf-page { width: 500px; }');
        h.doc.styles.push(pdf, vite);
        h.styleObserver.callback();
        assert.deepEqual(head.children.map((child) => child.href || child.textContent), [
            foreign.textContent, initialLink.href, pdf.href, vite.textContent,
        ]);
        assert.equal(head.children[1], initialLink, 'unchanged stylesheet links retain their DOM node');
        const previousViteClone = head.children[3];
        vite.textContent = '.pdf-page { width: 600px; }';
        h.styleObserver.callback();
        assert.equal(head.children[3].textContent, vite.textContent);
        assert.notEqual(head.children[3], previousViteClone);
        assert.equal(head.children[1], initialLink);
        h.doc.styles.splice(1, 1);
        h.styleObserver.callback();
        assert.equal(head.children.length, 3);
        assert.equal(head.children[0], foreign);
        assert.equal(head.children.some((child) => child.href === pdf.href), false);
        h.render().returnToChat();
        assert.equal(h.styleObserver.disconnected, true);
        assert.equal(h.themeObserver.disconnected, true);
        assert.deepEqual(head.childNodes, [foreign], 'cleanup removes only clones and its own boundary');
        h.styleObserver.callback();
        assert.deepEqual(head.childNodes, [foreign], 'a queued callback cannot append styles after closing');
    } finally { h.restore(); }
});
