import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { boundedCanvasSize } from '../wazzup/chatAttachments';

/* PDF одной лентой страниц, как в «Просмотре» macOS.

   Рисуются только страницы рядом с видимой областью (IntersectionObserver
   с запасом в экран): холст страницы в два раза плотнее экрана весит
   десятки мегабайт, и сотня таких держала бы гигабайты. Ушедшая далеко
   страница отдаёт холст, место под неё остаётся — лента не прыгает.

   Поверх холста — текстовый слой PDF.js: текст выделяется мышью и копируется
   ⌘C, как в настоящем просмотрщике. */

const PAGE_GAP = 14;
const MAX_FIT_WIDTH = 960;

function PdfPage({ pdf, library, number, scale, base, rootRef }) {
    const holder = useRef(null);
    const canvasHost = useRef(null);
    const textHost = useRef(null);
    const [near, setNear] = useState(false);
    const [size, setSize] = useState(null);

    useEffect(() => {
        const element = holder.current;
        if (!element || typeof IntersectionObserver !== 'function') {
            setNear(true);
            return undefined;
        }
        const observer = new IntersectionObserver(([entry]) => setNear(entry.isIntersecting), {
            root: rootRef.current,
            rootMargin: '1200px 0px',
        });
        observer.observe(element);
        return () => observer.disconnect();
    }, [rootRef]);

    useEffect(() => {
        if (!near) return undefined;
        let cancelled = false;
        let render = null;
        let text = null;
        const canvas = document.createElement('canvas');
        (async () => {
            const page = await pdf.getPage(number);
            if (cancelled) return;
            const unit = page.getViewport({ scale: 1 });
            setSize((prev) => (prev && prev.width === unit.width && prev.height === unit.height
                ? prev : { width: unit.width, height: unit.height }));
            const view = page.getViewport({ scale });
            const pixels = boundedCanvasSize(view.width, view.height, Math.min(window.devicePixelRatio || 1, 2));
            canvas.width = pixels.width;
            canvas.height = pixels.height;
            render = page.render({
                canvasContext: canvas.getContext('2d'),
                viewport: view,
                transform: [pixels.scale, 0, 0, pixels.scale, 0, 0],
                background: '#fff',
            });
            await render.promise;
            if (cancelled || !canvasHost.current) return;
            // Прежний холст (другой масштаб) стоит до готовности нового — без
            // белой вспышки при «+» и «−» — и сразу отдаёт память.
            const old = canvasHost.current.firstChild;
            canvasHost.current.replaceChildren(canvas);
            if (old && old !== canvas) {
                old.width = 1;
                old.height = 1;
            }
            const layer = document.createElement('div');
            layer.className = 'textLayer wazzup-pdf-text';
            layer.style.setProperty('--scale-factor', view.scale);
            layer.style.setProperty('--total-scale-factor', view.scale * (view.userUnit || 1));
            const content = await page.getTextContent();
            if (cancelled) return;
            text = new library.TextLayer({ textContentSource: content, container: layer, viewport: view });
            await text.render();
            if (!cancelled && textHost.current) textHost.current.replaceChildren(layer);
        })().catch(() => {
            // Страница, которую не удалось нарисовать, остаётся белым листом —
            // остальные страницы читаются.
        });
        return () => {
            cancelled = true;
            render?.cancel();
            text?.cancel();
        };
    }, [near, scale, pdf, library, number]);

    useEffect(() => {
        if (near) return;
        const host = canvasHost.current;
        host?.querySelectorAll('canvas').forEach((item) => {
            item.width = 1;
            item.height = 1;
        });
        host?.replaceChildren();
        textHost.current?.replaceChildren();
    }, [near]);

    const own = size || base;
    return (
        <div
            ref={holder}
            className="tfv-pdf-page"
            data-page={number}
            style={{ width: Math.round(own.width * scale), height: Math.round(own.height * scale) }}
        >
            <div ref={canvasHost} className="tfv-pdf-canvas" />
            <div ref={textHost} className="tfv-pdf-text" />
        </div>
    );
}

export default function PdfPreview({ pdf, library, zoom = 1, onPageChange }) {
    const scroller = useRef(null);
    const [width, setWidth] = useState(0);
    const [base, setBase] = useState(null);

    useEffect(() => {
        const element = scroller.current;
        if (!element) return undefined;
        const read = () => setWidth(element.clientWidth);
        read();
        if (typeof ResizeObserver !== 'function') return undefined;
        const observer = new ResizeObserver(read);
        observer.observe(element);
        return () => observer.disconnect();
    }, []);

    useEffect(() => {
        let alive = true;
        pdf.getPage(1).then((page) => {
            if (!alive) return;
            const unit = page.getViewport({ scale: 1 });
            setBase({ width: unit.width, height: unit.height });
        }).catch(() => {});
        return () => { alive = false; };
    }, [pdf]);

    const fit = base && width ? Math.max(0.2, Math.min(width - 32, MAX_FIT_WIDTH) / base.width) : 0;
    const scale = fit * zoom;

    /* Масштаб сменился — остаёмся на том же месте документа, а не у его начала. */
    const position = useRef({ scale: 0, ratio: 0 });
    useLayoutEffect(() => {
        const element = scroller.current;
        if (!element || !scale) return;
        if (position.current.scale && position.current.scale !== scale) {
            element.scrollTop = position.current.ratio * element.scrollHeight;
        }
        position.current.scale = scale;
    }, [scale]);

    const frame = useRef(0);
    const reportPage = () => {
        const element = scroller.current;
        if (!element) return;
        position.current.ratio = element.scrollTop / Math.max(1, element.scrollHeight);
        if (frame.current) return;
        frame.current = requestAnimationFrame(() => {
            frame.current = 0;
            const line = element.scrollTop + element.clientHeight * 0.35;
            const pages = element.querySelectorAll('[data-page]');
            let current = 1;
            for (const page of pages) {
                if (page.offsetTop <= line) current = Number(page.dataset.page);
                else break;
            }
            onPageChange?.(current);
        });
    };
    useEffect(() => () => cancelAnimationFrame(frame.current), []);

    return (
        <div ref={scroller} className="tfv-scroll thin-scroll" onScroll={reportPage}>
            {scale > 0 && (
                <div className="tfv-pdf" style={{ gap: PAGE_GAP }}>
                    {Array.from({ length: pdf.numPages }, (_, index) => (
                        <PdfPage
                            key={index + 1}
                            pdf={pdf}
                            library={library}
                            number={index + 1}
                            scale={scale}
                            base={base}
                            rootRef={scroller}
                        />
                    ))}
                </div>
            )}
        </div>
    );
}

/** Весь текст документа — для «Скопировать». Страницы по очереди, чтобы не держать их все разом. */
export async function pdfDocumentText(pdf, pageText, signal) {
    const parts = [];
    for (let number = 1; number <= pdf.numPages; number += 1) {
        if (signal?.aborted) return '';
        // eslint-disable-next-line no-await-in-loop
        const page = await pdf.getPage(number);
        // eslint-disable-next-line no-await-in-loop
        const text = pageText(await page.getTextContent());
        if (text) parts.push(text);
        page.cleanup?.();
    }
    return parts.join('\n\n');
}
