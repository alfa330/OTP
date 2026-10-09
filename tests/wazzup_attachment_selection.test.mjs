import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { build } from 'esbuild';
import { readFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

// Run the actual workspace handlers, effects and viewer props with deterministic
// hook storage, without mounting the unrelated chat API and realtime clients.
const source = readFileSync('src/components/wazzup/WazzupChatsView.jsx', 'utf8').replace(/\r\n/g, '\n');
const between = (start, end, from = 0) => {
    const first = source.indexOf(start, from), last = source.indexOf(end, first);
    assert.ok(first >= 0 && last > first, `workspace fragment: ${start}`);
    return source.slice(first, last);
};
const handlers = between('    const selectedKey = pilotChatKey(account, selected);', '    const chooseReply');
const reset = between('    useEffect(() => {\n        setReplySelection(null);', '    const searchDebounce');
const guard = between('    const attachmentAllowed = ', '    const notesEnabled');
const viewerStart = source.indexOf('{attachmentAllowed &&');
const viewer = source.slice(viewerStart, source.indexOf('</Suspense>}', viewerStart) + '</Suspense>}'.length);
const cache = join(process.cwd(), 'node_modules/.cache/otp-tests');
mkdirSync(cache, { recursive: true });
const output = join(cache, 'wazzup-attachment-selection.mjs');
await build({ stdin: { contents: `
    import React from 'react';
    import { pilotChatKey } from './chatPilot';
    import { attachmentPreviewKind } from './chatAttachments';
    import { buildAttachmentGroup } from './chatAttachmentGroups';
    const useState=(...args)=>globalThis.__selectionHarness.useState(...args);
    const useRef=(...args)=>globalThis.__selectionHarness.useRef(...args);
    const useEffect=(...args)=>globalThis.__selectionHarness.useEffect(...args);
    const useCallback=(...args)=>globalThis.__selectionHarness.useCallback(...args);
    const ChatAttachmentViewer='attachment-viewer', Suspense='suspense', IosModal='modal', Loader2='loader';
    const warmAttachmentViewer=()=>{};
    const AttachmentViewerFallback='attachment-fallback';
    export default function Selection({selected, account, user, apiBaseUrl, pilot}) {
        const [attachmentSelection, setAttachmentSelection] = useState(null);
        const setReplySelection=()=>{}, setNoteComposerKey=()=>{};
        const quoteHighlight=useRef({});
        const headers=()=>({});
        ${handlers}
        ${reset}
        ${guard}
        return { selection:attachmentSelection, open:openAttachment, select:selectAttachment,
            detach:setAttachmentDetached, thread:attachmentThread, viewer:<>${viewer}</> };
    }
`, loader: 'jsx', resolveDir: join(process.cwd(), 'src/components/wazzup') }, outfile: output,
    bundle: true, platform: 'node', format: 'esm', external: ['react'] });
const { default: Selection } = await import(pathToFileURL(output));

const chatA = { channelId: 'channel-a', chatId: 'chat-a', contactName: 'First' };
const chatB = { channelId: 'channel-b', chatId: 'chat-b', contactName: 'Second' };
const media = (id, type = 'image') => ({ messageId: id, type, contentUri: `https://example.invalid/${id}`,
    dt: '2026-10-09T10:00:00Z', isEcho: false, channelId: chatA.channelId, chatId: chatA.chatId });
const first = media('first'), next = media('next');
const findViewer = (node) => {
    if (!node || typeof node !== 'object') return null;
    if (node.type === 'attachment-viewer') return node;
    for (const child of React.Children.toArray(node.props?.children)) {
        const found = findViewer(child); if (found) return found;
    }
    return null;
};

function fixture() {
    const slots = [];
    let cursor = 0, effects = [], dirty;
    const props = { selected: chatA, account: 'op', user: { id: 1 }, apiBaseUrl: 'https://example.invalid',
        pilot: { enabled: true, capability: { excludedChannelIds: [] } } };
    const h = {
        useState(initial) { const index = cursor++; slots[index] ??= { value: initial };
            return [slots[index].value, (input) => { const previous = slots[index].value;
                const value = typeof input === 'function' ? input(previous) : input;
                if (!Object.is(previous, value)) dirty = true;
                slots[index].value = value;
            }]; },
        useRef(initial) { const index = cursor++; slots[index] ??= { current: initial }; return slots[index]; },
        useCallback(fn, deps) { const index = cursor++, previous = slots[index];
            if (!previous || deps.some((value, i) => !Object.is(value, previous.deps[i]))) slots[index] = { value: fn, deps };
            return slots[index].value; },
        useEffect(effect, deps) { const index = cursor++, previous = slots[index];
            if (previous && deps.every((value, i) => Object.is(value, previous.deps[i]))) return;
            slots[index] = { deps }; effects.push(() => { previous?.cleanup?.(); slots[index].cleanup = effect(); }); },
        render(update = {}) { Object.assign(props, update); globalThis.__selectionHarness = h;
            let result, count = 0;
            do {
                assert.ok(count++ < 10, 'effects must settle'); cursor = 0; dirty = false;
                result = Selection(props); result.thread.current = [first, next];
                const pending = effects; effects = []; pending.forEach((effect) => effect());
            } while (dirty);
            return { ...result, viewer: findViewer(result.viewer) };
        },
        restore() { slots.forEach((slot) => slot?.cleanup?.()); delete globalThis.__selectionHarness; },
    };
    return h;
}

test('ordinary attachment closes when the selected conversation changes', () => {
    const h = fixture();
    try {
        h.render().open(first);
        assert.ok(h.render().viewer);
        assert.equal(h.render({ selected: chatB }).viewer, null);
        assert.equal(h.render().selection, null);
    } finally { h.restore(); }
});

test('detached viewer keeps its chat, key and navigation snapshot, and can return over another chat', () => {
    const h = fixture();
    try {
        h.render().open(first); let view = h.render();
        const originalKey = view.viewer.key;
        view.viewer.props.onDetachedChange(true);
        view = h.render({ selected: chatB });
        assert.equal(view.viewer.key, originalKey);
        assert.deepEqual(view.viewer.props.chat, chatA);
        assert.notEqual(view.viewer.props.chat, chatA, 'selected metadata is copied when opening');
        view.viewer.props.onSelect({ ...next, contentUri: 'https://example.invalid/unrelated' });
        view = h.render();
        assert.equal(view.viewer.props.message, next, 'navigation uses the stored item, not caller metadata');
        view.viewer.props.onSelect(media('outside-group'));
        assert.equal(h.render().viewer.props.message, next);
        view.viewer.props.onDetachedChange(false);
        view = h.render();
        assert.ok(view.viewer, 'return to modal is allowed over the current conversation');
        assert.deepEqual(view.viewer.props.chat, chatA);
        assert.equal(h.render({ selected: chatA }).viewer, null, 'the next chat change closes the returned modal');
    } finally { h.restore(); }
});

test('a new attachment replaces the viewer and callbacks from a different chat cannot modify it', () => {
    const h = fixture();
    try {
        h.render().open(first); let view = h.render(); view.detach(true);
        view = h.render(); view.open(next); view = h.render();
        assert.equal(view.selection.detached, true, 'another attachment in this chat replaces PiP contents');
        assert.equal(view.viewer.props.message.messageId, next.messageId);
        const previousViewer = view.viewer;
        view = h.render({ selected: chatB }); view.open(media('new-chat'));
        view = h.render();
        assert.notEqual(view.viewer.key, previousViewer.key);
        assert.equal(view.selection.detached, false);
        previousViewer.props.onDetachedChange(true);
        previousViewer.props.onSelect(first);
        view = h.render();
        assert.equal(view.selection.detached, false);
        assert.equal(view.viewer.props.message.messageId, 'new-chat');
    } finally { h.restore(); }
});

test('account, owner, API and capability changes discard detached snapshots permanently', () => {
    for (const update of [{ account: 'potok' }, { user: { id: 2 } }, { user: null },
        { apiBaseUrl: 'https://other.invalid' }, { pilot: { enabled: false } },
        { pilot: { enabled: true, capability: { excludedChannelIds: [chatA.channelId] } } }]) {
        const h = fixture();
        try {
            h.render().open(first); h.render().detach(true);
            h.render({ selected: chatB });
            assert.equal(h.render(update).viewer, null);
            assert.equal(h.render().selection, null);
        } finally { h.restore(); }
    }
});

test('excluded-channel checks use the original chat and direct video mode needs no pilot capability', () => {
    const h = fixture();
    try {
        h.render().open(first); h.render().detach(true);
        assert.ok(h.render({ selected: chatB, pilot: { enabled: true,
            capability: { excludedChannelIds: [chatB.channelId] } } }).viewer);
        h.render().open(media('video', 'video'), true); h.render().detach(true);
        const view = h.render({ selected: chatA, pilot: { enabled: false } });
        assert.ok(view.viewer);
        assert.equal(view.viewer.props.message.type, 'video');
        assert.equal(view.viewer.props.chat.chatId, chatB.chatId);
        assert.equal(h.render({ account: 'potok' }).viewer, null);
    } finally { h.restore(); }
});
