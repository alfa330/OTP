// Build the review gallery into a portable HTML file, including React, styles
// and the original SVG. It can be opened from disk without a server or internet.
import { build } from 'esbuild';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const target = resolve(root, 'outputs/icore-assistant-icons/index.html');
const result = await build({
    absWorkingDir: root,
    entryPoints: ['src/assistant_icon_preview/main.jsx'],
    bundle: true,
    write: false,
    outfile: 'gallery.js',
    format: 'iife',
    minify: true,
    legalComments: 'none',
    define: { 'process.env.NODE_ENV': '"production"' },
    loader: { '.svg': 'dataurl' },
});
const js = result.outputFiles.find(file => file.path.endsWith('.js')).text;
const css = result.outputFiles.find(file => file.path.endsWith('.css')).text;
const template = await readFile(resolve(root, 'assistant-icons.html'), 'utf8');
const html = template
    .replace('</head>', () => `<style>${css}</style></head>`)
    .replace('<script type="module" src="/src/assistant_icon_preview/main.jsx"></script>', () => `<script>${js.replaceAll('</script', '<\\/script')}</script>`);
await mkdir(dirname(target), { recursive: true });
await writeFile(target, html, 'utf8');
console.log(`Gallery: ${target} (${Math.round(Buffer.byteLength(html) / 1024)} KB)`);
