import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { build } from 'esbuild';
import { mkdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const view = readFileSync('src/components/wazzup/WazzupChatsView.jsx', 'utf8');
const loading = view.slice(view.indexOf('let attachmentViewerModule;'), view.indexOf('/* Чаты Wazzup'));
const media = view.slice(view.indexOf('function MediaContent('), view.indexOf('/* Исходящие'));
const cache = join(process.cwd(), 'node_modules/.cache/otp-tests');
mkdirSync(cache, { recursive: true });
const outfile = join(cache, 'wazzup-attachment-opening.mjs');
await build({ stdin: { contents: `
    import React from 'react';
    import { FileText, Loader2 } from 'lucide-react';
    import { attachmentName, attachmentPreviewKind } from './chatAttachments';
    const useState=()=>[false,()=>{}];
    const lazy=(loader)=>loader;
    const IosModal=({children,...props})=><section>{children}</section>;
    const ChatMessageImage=()=>null, ChatMessageVideo=()=>null, ChatAudioPlayer=()=>null;
    const MEDIA_LABELS={}, MEDIA_ICONS={};
    ${loading}
    ${media}
    export { loadAttachmentViewer, warmAttachmentViewer, ChatAttachmentViewer, AttachmentViewerFallback, MediaContent };
`, resolveDir: join(process.cwd(), 'src/components/wazzup'), loader: 'jsx' }, outfile, bundle: true,
    platform: 'node', format: 'esm', external: ['react', 'lucide-react'], plugins: [{ name: 'lazy-fixtures', setup(builder) {
        builder.onResolve({ filter: /^\.\/(ChatAttachmentViewer|pdfRuntime)$/ }, ({ path }) => ({ path, namespace: 'lazy-fixture' }));
        builder.onLoad({ filter: /.*/, namespace: 'lazy-fixture' }, ({ path }) => ({ contents:
            `globalThis.__attachmentModuleLoads.push(${JSON.stringify(path)}); export default function Fixture(){return null;}` }));
    } }] });
const module = await import(pathToFileURL(outfile));

test('intent prepares only the viewer/PDF code, shares the pending viewer import with React.lazy, and does not open files', async () => {
    globalThis.__attachmentModuleLoads = [];
    const requests = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (...args) => { requests.push(args); throw new Error('No media requests before opening'); };
    try {
        assert.equal(module.ChatAttachmentViewer, module.loadAttachmentViewer);
        const messages = [
            { type: 'image', contentUri: 'https://example.invalid/photo.jpg' },
            { type: 'video', contentUri: 'https://example.invalid/movie.mp4' },
            { type: 'document', contentUri: 'https://example.invalid/doc.pdf' },
            { type: 'document', contentUri: 'https://example.invalid/table.xlsx' },
        ];
        const opened = [];
        for (const msg of messages) {
            const tree = module.MediaContent({ msg, onAttachment: (value) => opened.push(value) });
            assert.equal(tree.props.className, 'contents');
            tree.props.onPointerEnter();
            tree.props.onFocus();
            assert.deepEqual(opened, []);
            const control = tree.props.children;
            (control.props.onOpen || control.props.onClick)();
            assert.equal(opened.pop(), msg);
        }
        const first = module.loadAttachmentViewer();
        assert.equal(module.loadAttachmentViewer(), first);
        await first;
        await new Promise((resolve) => setImmediate(resolve));
        assert.deepEqual(globalThis.__attachmentModuleLoads.sort(), ['./ChatAttachmentViewer', './pdfRuntime']);
        assert.deepEqual(requests, []);
    } finally { globalThis.fetch = originalFetch; delete globalThis.__attachmentModuleLoads; }
});

test('the lazy viewer fallback immediately displays the already used image URL in the full viewer frame', () => {
    let closed = false;
    const image = { type: 'image', contentUri: 'https://example.invalid/photo.jpg', fileName: 'Фото.jpg' };
    const tree = module.AttachmentViewerFallback({ message: image, onClose: () => { closed = true; } });
    assert.equal(tree.props.maxWidth, 'max-w-6xl');
    assert.equal(tree.props.children.props.style.height, 'min(78vh, 900px)');
    const markup = renderToStaticMarkup(tree);
    assert.match(markup, /src="https:\/\/example.invalid\/photo.jpg"/);
    assert.match(markup, /alt="Вложение из сообщения"/);
    assert.doesNotMatch(markup, /Открываем просмотр|animate-spin/);
    tree.props.onClose();
    assert.equal(closed, true);
    const pdf = renderToStaticMarkup(module.AttachmentViewerFallback({ message: {
        type: 'document', contentUri: 'https://example.invalid/doc.pdf',
    } }));
    assert.match(pdf, /Открываем просмотр/);
    assert.doesNotMatch(pdf, /<img/);
});
