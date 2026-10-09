import test from 'node:test';
import assert from 'node:assert/strict';
import { renderPdfCanvas } from '../src/components/wazzup/renderPdfCanvas.js';

const deferred = () => {
    let resolve, reject;
    const promise = new Promise((accept, fail) => { resolve = accept; reject = fail; });
    return { promise, resolve, reject };
};

function fixture() {
    const created = [], copies = [];
    const fontDocument = {
        createElement(kind) {
            assert.equal(kind, 'canvas');
            const source = { ownerDocument: fontDocument, width: 300, height: 150,
                getContext(type) { assert.equal(type, '2d'); return sourceContext; } };
            const sourceContext = { canvas: source };
            created.push(source);
            return source;
        },
    };
    const pipDocument = {};
    const canvas = { ownerDocument: pipDocument, width: 1200, height: 1600,
        getContext(type) { assert.equal(type, '2d'); return context; } };
    const context = { canvas,
        drawImage(source, x, y) { copies.push({ source, x, y, width: source.width, height: source.height }); },
    };
    const options = { canvas, viewport: { width: 600, height: 800 },
        transform: [2, 0, 0, 2, 0, 0], background: '#fff' };
    return { fontDocument, canvas, context, created, copies, options };
}

test('the main document keeps the native render task without another canvas or pixel copy', async () => {
    const h = fixture();
    h.canvas.ownerDocument = h.fontDocument;
    const native = { promise: Promise.resolve('ready'), cancel() {} };
    let received;
    const task = renderPdfCanvas({ render(options) { received = options; return native; } }, h.options, h.fontDocument);
    assert.equal(task, native);
    assert.deepEqual(received, { viewport: h.options.viewport, transform: h.options.transform,
        background: '#fff', canvasContext: h.context });
    assert.equal(await task.promise, 'ready');
    assert.equal(h.created.length, 0);
    assert.equal(h.copies.length, 0);
});

test('PiP rendering uses the font document at the exact display resolution and copies only completed pixels', async () => {
    const h = fixture(), pending = deferred();
    let received;
    const task = renderPdfCanvas({ render(options) {
        received = options;
        return { promise: pending.promise, cancel() {} };
    } }, h.options, h.fontDocument);
    assert.equal(h.created.length, 1);
    const source = h.created[0];
    assert.equal(received.canvasContext.canvas, source);
    assert.equal(source.ownerDocument, h.fontDocument);
    assert.equal(source.width, 1200);
    assert.equal(source.height, 1600);
    assert.equal(received.viewport, h.options.viewport);
    assert.equal(received.transform, h.options.transform);
    assert.equal(received.background, '#fff');
    assert.equal('canvas' in received, false);
    assert.equal(h.copies.length, 0);
    pending.resolve('complete');
    assert.equal(await task.promise, 'complete');
    assert.deepEqual(h.copies, [{ source, x: 0, y: 0, width: 1200, height: 1600 }]);
    assert.equal(source.width, 1);
    assert.equal(source.height, 1);
    assert.equal(h.canvas.width, 1200);
    assert.equal(h.canvas.height, 1600);
});

test('cancellation ends native rendering before freeing pixels and a late result never overwrites the new page', async () => {
    const h = fixture(), pending = deferred();
    let cancelled = 0;
    const task = renderPdfCanvas({ render() { return { promise: pending.promise, cancel(delay) {
        cancelled += 1;
        assert.equal(delay, 50);
        assert.equal(h.created[0].width, 1200, 'PDF.js still needs its bitmap in cancel');
        assert.equal(h.created[0].height, 1600);
    } }; } }, h.options, h.fontDocument);
    task.cancel(50);
    task.cancel(50);
    assert.equal(cancelled, 1);
    assert.equal(h.created[0].width, 1);
    assert.equal(h.created[0].height, 1);
    pending.resolve();
    await task.promise;
    assert.equal(h.copies.length, 0);
});

test('native cancellation rejection is preserved and cleanup does not copy a partial page', async () => {
    const h = fixture(), pending = deferred();
    const failure = new Error('Rendering cancelled');
    failure.name = 'RenderingCancelledException';
    const task = renderPdfCanvas({ render() { return { promise: pending.promise, cancel() {
        pending.reject(failure);
    } }; } }, h.options, h.fontDocument);
    task.cancel();
    await assert.rejects(task.promise, (error) => error === failure);
    assert.equal(h.created[0].width, 1);
    assert.equal(h.created[0].height, 1);
    assert.equal(h.copies.length, 0);
});

test('synchronous PDF rendering failure frees the scratch canvas and propagates the same error', () => {
    const h = fixture(), failure = new Error('Cannot render this page');
    assert.throws(() => renderPdfCanvas({ render() { throw failure; } }, h.options, h.fontDocument),
        (error) => error === failure);
    assert.equal(h.created[0].width, 1);
    assert.equal(h.created[0].height, 1);
    assert.equal(h.copies.length, 0);
});

test('asynchronous PDF rendering failure frees the scratch canvas without displaying incomplete pixels', async () => {
    const h = fixture(), pending = deferred(), failure = new Error('Broken font');
    const task = renderPdfCanvas({ render() { return { promise: pending.promise, cancel() {} }; } }, h.options, h.fontDocument);
    pending.reject(failure);
    await assert.rejects(task.promise, (error) => error === failure);
    assert.equal(h.created[0].width, 1);
    assert.equal(h.created[0].height, 1);
    assert.equal(h.copies.length, 0);
});

test('a display copy failure also frees the scratch bitmap', async () => {
    const h = fixture(), failure = new Error('Display context lost');
    h.context.drawImage = () => { throw failure; };
    const task = renderPdfCanvas({ render() { return { promise: Promise.resolve(), cancel() {} }; } }, h.options, h.fontDocument);
    await assert.rejects(task.promise, (error) => error === failure);
    assert.equal(h.created[0].width, 1);
    assert.equal(h.created[0].height, 1);
});

test('cleanup after completion does not cancel a finished native render', async () => {
    const h = fixture();
    let cancelled = 0;
    const task = renderPdfCanvas({ render() { return { promise: Promise.resolve(), cancel() { cancelled += 1; } }; } },
        h.options, h.fontDocument);
    await task.promise;
    task.cancel();
    assert.equal(cancelled, 0);
    assert.equal(h.copies.length, 1);
});
