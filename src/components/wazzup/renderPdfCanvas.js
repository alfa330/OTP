/** Render in the document where PDF.js registered its embedded FontFace objects. */
export function renderPdfCanvas(page, { canvas, ...options }, fontDocument = document) {
    if (canvas.ownerDocument === fontDocument) {
        return page.render({ ...options, canvasContext: canvas.getContext('2d') });
    }

    // FontFaceSet belongs to a document, whereas stylesheet cloning does not
    // transfer its programmatically loaded fonts to Picture-in-Picture. Keep
    // PDF.js drawing with its own fonts and copy the completed pixels instead.
    const source = fontDocument.createElement('canvas');
    source.width = canvas.width;
    source.height = canvas.height;
    let released = false;
    const release = () => {
        if (released) return;
        released = true;
        source.width = 1;
        source.height = 1;
    };
    let task;
    try {
        task = page.render({ ...options, canvasContext: source.getContext('2d') });
    } catch (error) {
        release();
        throw error;
    }
    let cancelled = false;
    let settled = false;
    const promise = task.promise.then((result) => {
        if (!cancelled) canvas.getContext('2d').drawImage(source, 0, 0);
        return result;
    }).finally(() => {
        settled = true;
        release();
    });
    return {
        promise,
        cancel(...args) {
            if (cancelled || settled) return;
            cancelled = true;
            // PDF.js ends its drawing synchronously during cancel; releasing
            // the bitmap first would resize a canvas it is still using.
            try { task.cancel(...args); } finally { release(); }
        },
    };
}
