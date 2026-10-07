export function createPdfResourceFactory(assetUrls, fetchResource = (...args) => fetch(...args)) {
    // Asset-map lookup keeps document-supplied filenames from becoming URLs.
    return class LocalPdfResources {
        async fetch({ kind, filename }) {
            const directory = { cMapUrl: 'cmaps', standardFontDataUrl: 'standard_fonts', wasmUrl: 'wasm' }[kind];
            const url = assetUrls.get(`${directory}/${filename}`);
            if (!url) throw new Error('Ресурс PDF недоступен');
            const response = await fetchResource(url);
            if (!response.ok) throw new Error('Не удалось загрузить ресурс PDF');
            return new Uint8Array(await response.arrayBuffer());
        }
    };
}
