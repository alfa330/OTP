import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import { attachmentName, attachmentPreviewKind, boundedCanvasSize, pdfPageText } from '../src/components/wazzup/chatAttachments.js';
import { createPdfResourceFactory } from '../src/components/wazzup/pdfResources.js';

test('attachment viewer chooses PDF/images safely and leaves other documents alone', () => {
    const message = { type: 'document', contentUri: 'https://store.wazzup24.com/opaque/?filename=%D0%94%D0%BE%D0%B3%D0%BE%D0%B2%D0%BE%D1%80.PDF' };
    assert.equal(attachmentName(message), 'Договор.PDF');
    assert.equal(attachmentPreviewKind(message), 'pdf');
    assert.equal(attachmentPreviewKind({ ...message, isDeleted: true }), null);
    assert.equal(attachmentPreviewKind({ ...message, contentUri: 'https://store.wazzup24.com/file.docx' }), null);
    assert.equal(attachmentPreviewKind({ ...message, contentUri: 'https://store.wazzup24.com/opaque/' }), 'document');
    assert.equal(attachmentPreviewKind({ ...message, type: 'image', contentUri: 'https://store.wazzup24.com/photo' }), 'image');
    assert.equal(attachmentName({ fileName: '../../filename.pdf' }), '.._.._filename.pdf');
    assert.equal(attachmentName({ contentUri: 'invalid' }), '');
});

test('PDF text extraction preserves layout lines and adjacent split-word fragments', () => {
    const item = (str, x, y, width, hasEOL = false) => ({ str, width, height: 12, transform: [12,0,0,12,x,y], hasEOL });
    assert.equal(pdfPageText({ items: [item('Hello',0,40,25), item('world',31,40,25,true),
        item('2500',0,20,25), item('тенге',31,20,25)] }), 'Hello world\n2500 тенге');
    assert.equal(pdfPageText({ items: [item('Docu',0,40,20), item('ment',20,40,20), item('Next',0,20,20)] }), 'Document\nNext');
    assert.equal(pdfPageText({ items: [] }), '');
});

test('display and OCR rasters stay inside pixel and side-length budgets', () => {
    for (const [width, height] of [[612,792],[10_000,60_000],[1,100_000],[50_000,2]]) {
        const display = boundedCanvasSize(width,height,2);
        assert.ok(display.width * display.height <= 8_000_000);
        assert.ok(Math.max(display.width,display.height) <= 8192);
        const ocr = boundedCanvasSize(width,height,2.5,1920*1920,1920);
        assert.ok(Math.max(ocr.width,ocr.height) <= 1920);
        assert.ok(ocr.width * ocr.height <= 1920*1920);
    }
    assert.throws(() => boundedCanvasSize(0,100), /размер/);
    assert.throws(() => boundedCanvasSize(Infinity,100), /размер/);
});

const pdfRoot = fileURLToPath(new URL('../node_modules/pdfjs-dist/', import.meta.url));
const resourceMap = new Map();
for (const directory of ['standard_fonts','cmaps','wasm']) {
    for (const filename of await readdir(join(pdfRoot,directory))) {
        resourceMap.set(`${directory}/${filename}`, join(pdfRoot,directory,filename));
    }
}
const requestedResources = [];
const ResourceFactory = createPdfResourceFactory(resourceMap, async (path) => {
    requestedResources.push(path);
    const bytes = await readFile(path);
    return { ok: true, arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength) };
});

test('PDF.js binary factory serves real font/CMap/WASM bytes and rejects unknown filenames', async () => {
    const resources = new ResourceFactory({});
    for (const [kind, filename] of [['standardFontDataUrl','LiberationSans-Regular.ttf'],
        ['cMapUrl','Adobe-Japan1-UCS2.bcmap'],['wasmUrl','openjpeg.wasm']]) {
        const bytes = await resources.fetch({kind,filename});
        assert.ok(bytes instanceof Uint8Array && bytes.byteLength > 100);
    }
    const count = requestedResources.length;
    await assert.rejects(resources.fetch({kind:'wasmUrl',filename:'../../private'}));
    await assert.rejects(resources.fetch({kind:'unknown',filename:'openjpeg.wasm'}));
    assert.equal(requestedResources.length,count);
});

function syntheticPdf() {
    const content = 'BT /F1 18 Tf 30 150 Td (Hello iCORE 2500) Tj 0 -28 Td (Second line) Tj ET';
    const objects = [
        '<< /Type /Catalog /Pages 2 0 R >>',
        '<< /Type /Pages /Count 1 /Kids [3 0 R] >>',
        '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        `<< /Length ${content.length} >>\nstream\n${content}\nendstream`,
    ];
    let output = '%PDF-1.4\n';
    const offsets = [0];
    objects.forEach((object,index) => { offsets.push(output.length); output += `${index+1} 0 obj\n${object}\nendobj\n`; });
    const xref = output.length;
    output += `xref\n0 ${objects.length+1}\n0000000000 65535 f \n`;
    for (const offset of offsets.slice(1)) output += `${String(offset).padStart(10,'0')} 00000 n \n`;
    output += `trailer\n<< /Size ${objects.length+1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
    return new Uint8Array(Buffer.from(output));
}

test('installed PDF.js parses and renders a real synthetic PDF with the production resource factory', async () => {
    const canvas = await import('@napi-rs/canvas');
    const previousMatrix = globalThis.DOMMatrix;
    globalThis.DOMMatrix = canvas.DOMMatrix;
    let task;
    try {
        const { getDocument } = await import('pdfjs-dist/legacy/build/pdf.mjs');
        requestedResources.length = 0;
        task = getDocument({data:syntheticPdf(), BinaryDataFactory:ResourceFactory, useWorkerFetch:false,
            isEvalSupported:false, useSystemFonts:false, disableFontFace:true, verbosity:0});
        const pdf = await task.promise;
        assert.equal(pdf.numPages,1);
        const page = await pdf.getPage(1);
        assert.equal(pdfPageText(await page.getTextContent()),'Hello iCORE 2500\nSecond line');
        const viewport = page.getViewport({scale:2});
        const target = canvas.createCanvas(viewport.width,viewport.height);
        await page.render({canvasContext:target.getContext('2d'),viewport}).promise;
        const pixels = target.getContext('2d').getImageData(0,0,target.width,target.height).data;
        let dark = 0;
        for (let offset = 0; offset < pixels.length; offset += 4) if (pixels[offset] < 160) dark += 1;
        assert.ok(dark > 300, 'Rendered PDF must contain visible glyphs, not a blank canvas');
        assert.ok(requestedResources.some((path) => path.endsWith('LiberationSans-Regular.ttf')),
            'Production resource factory must participate in the real PDF render');
    } finally { await task?.destroy(); globalThis.DOMMatrix = previousMatrix; }
});
