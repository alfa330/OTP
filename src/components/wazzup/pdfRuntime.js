import { getDocument as loadDocument, GlobalWorkerOptions, TextLayer } from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import { createPdfResourceFactory } from './pdfResources';
import './pdfTextLayer.css';

// Both code and worker are bundled locally; the document and credentials never
// go to a public PDF viewer or CDN.
GlobalWorkerOptions.workerSrc = workerUrl;

const assets = import.meta.glob([
    '../../../node_modules/pdfjs-dist/cmaps/*.bcmap',
    '../../../node_modules/pdfjs-dist/standard_fonts/*.{pfb,ttf}',
    '../../../node_modules/pdfjs-dist/wasm/*.{wasm,js}',
], { query: '?url', import: 'default', eager: true });
const assetUrls = new Map(Object.entries(assets).map(([path, url]) => {
    const relative = path.split('/pdfjs-dist/')[1];
    return [relative, url];
}));

// PDF.js normally constructs filenames under a public directory. Vite hashes
// bundled files, so resolve its known resources through the generated URL map.
const LocalPdfResources = createPdfResourceFactory(assetUrls);

export const getDocument = (options) => loadDocument({ ...options,
    BinaryDataFactory: LocalPdfResources, useWorkerFetch: false });
export { TextLayer };
